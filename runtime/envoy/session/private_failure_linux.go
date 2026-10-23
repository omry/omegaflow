//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// privateFailure is evidence owned by the existing serialized fact consumer.
// EOF and Wait describe fatal disposal; neither supplies a terminal result.
type privateFailure struct {
	phase    string
	deadline time.Time
	cause    error
	eof      bool
	reaped   bool
	waitErr  error
	nonzero  bool
	readErr  error
}

func (f *privateFailure) Error() string { return f.phase + ": " + f.cause.Error() }
func (f *privateFailure) Unwrap() error { return f.cause }

// Joined I/O interruption is not a newly observed private protocol fault.
// Every cause must be cooperative; a mixed framing failure remains fatal.
func privateIOCancelled(ctx context.Context, err error) bool {
	if ctx.Err() == nil {
		return false
	}
	if joined, ok := err.(interface{ Unwrap() []error }); ok {
		for _, cause := range joined.Unwrap() {
			if !privateIOCancelled(ctx, cause) {
				return false
			}
		}
		return true
	}
	if cause := errors.Unwrap(err); cause != nil {
		return privateIOCancelled(ctx, cause)
	}
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrClosed) || errors.Is(err, os.ErrDeadlineExceeded)
}

func privateCause(result privateResult) error {
	if report, ok := result.message.(*protocol.PrivateProtocolError); ok {
		return errors.Join(fmt.Errorf("private protocol_error %s: %s", report.Code, report.Message), result.err)
	}
	return result.err
}

func newPrivateFailure(result privateResult, phase string, deadline time.Time) *privateFailure {
	f := &privateFailure{phase: phase, deadline: deadline, cause: privateCause(result), eof: result.err == io.EOF}
	if report, ok := result.message.(*protocol.PrivateProtocolError); ok {
		f.nonzero = report.Code == "shell-launch"
		if result.err != io.EOF {
			f.readErr = result.err
		}
	}
	if result.drainErr == io.EOF {
		f.eof, f.readErr = true, nil
	} else if result.drainErr != nil {
		f.readErr = result.drainErr
	}
	return f
}

// Only named Envoy owners can retain an epoch. A caller or sender deadline
// alone cannot prevent the existing final-drain fallback.
func (c *Coordinator) privateEpoch(ctx context.Context) (string, time.Time) {
	if !c.exitedDeadline.IsZero() && !c.exitedEOF {
		return string(c.exitedPhase), c.exitedDeadline
	}
	if d := c.running; d != nil {
		if !d.continueDeadline.IsZero() && d.gate != nil && d.gate.request != nil &&
			c.w.channels.InputThrough() < d.gate.request.InputThrough {
			return "terminal-input-barrier", d.continueDeadline
		}
		if i := d.inspection; i != nil && i.worker != nil && i.stop != nil {
			deadline, _ := i.stop.Deadline()
			return "inspection-cancellation", deadline
		}
		if d.cleanup != nil {
			deadline, _ := d.cleanup.Deadline()
			return "operation-cleanup", deadline
		}
		if d.life.grace != nil && d.life.inputClose == nil && !d.ending {
			deadline, _ := d.life.grace.Deadline()
			return "grace", deadline
		}
	} else if c.active != nil && !c.active.started {
		deadline, _ := ctx.Deadline()
		return string(c.active.shellExitPhase), deadline
	}
	return "", time.Time{}
}

// collect uses the original reader and original Wait publication. Closing the
// control pipe requests fatal disposal; it is not proof of either fact.
func (w *Workload) collectPrivateFailure(f *privateFailure, next func(context.Context) (protocol.PrivateMessage, error)) {
	ctx, end := context.WithDeadline(context.Background(), f.deadline)
	defer end()
	if err := w.Control.Close(); err != nil && !errors.Is(err, os.ErrClosed) {
		f.cause = errors.Join(f.cause, err)
	}
	if f.readErr != nil {
		f.cause = errors.Join(f.cause, fmt.Errorf("missing private EOF: %w", f.readErr))
	}
	for !f.eof && f.readErr == nil {
		_, err := next(ctx)
		if err == io.EOF {
			f.eof = true
		} else if err != nil {
			f.cause = errors.Join(f.cause, fmt.Errorf("missing private EOF: %w", err))
			break
		}
	}
	select {
	case <-w.reaped:
		f.reaped, f.waitErr = true, w.waitErr
	default:
		if w.reaped != nil {
			select {
			case <-w.reaped:
				f.reaped, f.waitErr = true, w.waitErr
			case <-ctx.Done():
			}
		}
	}
	if !f.reaped {
		f.cause = errors.Join(f.cause, fmt.Errorf("unreaped Awsh under original %s epoch", f.phase))
	} else if f.waitErr != nil {
		f.cause = errors.Join(f.cause, fmt.Errorf("fatal Awsh reap: %w", f.waitErr))
	} else if f.nonzero {
		f.cause = errors.Join(f.cause, fmt.Errorf("shell-launch requires nonzero Awsh reap"))
	}
	if !time.Now().Before(f.deadline) {
		f.cause = errors.Join(f.cause, context.DeadlineExceeded)
	}
}

func (c *Coordinator) failPrivate(ctx context.Context, interrupt context.CancelFunc, result privateResult, phase string, deadline time.Time) error {
	if c.privateFailure == nil {
		if deadline.IsZero() {
			phase, deadline = "final-drain", time.Now().Add(finalDrainBudget)
		}
		f := newPrivateFailure(result, phase, deadline)
		c.privateFailure = f
		if interrupt != nil {
			interrupt()
		}
		c.resize = nil
		if c.running != nil {
			c.running.stopInspection()
		}
		c.w.collectPrivateFailure(f, c.readPrivateResult)
	}
	f := c.privateFailure
	return &startFactError{&StartError{Code: "operation-cleanup", Deadline: f.deadline, Cause: f}}
}

// readPrivateResult is used only by the current owner, including fatal drain.
// It consumes no public controls and cannot select an operation result.
func (c *Coordinator) readPrivateResult(ctx context.Context) (protocol.PrivateMessage, error) {
	select {
	case result := <-c.private.fatal:
		return result.message, result.err
	default:
	}
	select {
	case result := <-c.private.fatal:
		return result.message, result.err
	case result := <-c.private.events:
		return result.message, result.err
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-c.private.ctx.Done():
		return nil, c.private.ctx.Err()
	}
}

func (c *Coordinator) readyPrivateFailure(ctx context.Context, phase string, deadline time.Time) error {
	select {
	case result := <-c.private.fatal:
		if _, report := result.message.(*protocol.PrivateProtocolError); !report && privateIOCancelled(ctx, result.err) {
			return &startFactError{result.err}
		}
		return c.failPrivate(ctx, nil, result, phase, deadline)
	default:
		return nil
	}
}
