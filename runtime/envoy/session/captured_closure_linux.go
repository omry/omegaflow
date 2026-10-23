//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// completeStartShellExit consumes only the matching pre-write Start handoff.
// Start's local context has ended; its absolute deadline remains authoritative.
// Final drain starts once here and never replaces the captured closure bound.
func (c *Coordinator) completeStartShellExit(ctx context.Context, census *processCensus, out StartOutcome) (err error) {
	if err = c.idleProcessOwner(census); err != nil {
		return err
	}
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("coordinator is already in use")
	}
	defer c.busy.Store(false)
	if ctx == nil || ctx.Err() != nil || c.active != nil || c.pending != nil ||
		out.Started || out.ShellExit == nil || out.ShellExit != c.exited ||
		out.ShellExit.ActiveOperationID != "" || out.ShellExitDeadline.IsZero() ||
		out.ShellExitDeadline != c.exitedDeadline || out.ShellExitPhase != c.exitedPhase ||
		out.ShellExitEOF != c.exitedEOF {
		return fmt.Errorf("captured closure requires the exact unstarted shell-exit handoff")
	}
	code, err := capturedClosureCode(out.ShellExitPhase)
	if err != nil {
		return err
	}
	drain, endDrain := context.WithTimeout(ctx, finalDrainBudget)
	defer endDrain()
	closure, endClosure := context.WithDeadline(drain, out.ShellExitDeadline)
	defer endClosure()
	defer func() {
		if c.privateFailure != nil {
			var end context.CancelFunc
			drain, end = context.WithDeadline(context.WithoutCancel(drain), c.privateFailure.deadline)
			defer end()
		}
		if err != nil {
			c.w.channels.fail(err)
		}
		err = errors.Join(err, c.private.close(drain), c.w.Close(drain))
		c.w.channels.Close()
	}()
	if err = c.r.Close(closure); err == nil {
		next := c.nextIdleResult
		if out.ShellExitEOF {
			// The same private owner may already have consumed EOF during
			// original-epoch split rollback. Do not wait for a second EOF.
			next = func(context.Context) (protocol.PrivateMessage, error) { return nil, io.EOF }
		}
		err = c.w.finishTerminalClosure(closure, out.ShellExit, next, census)
	}
	// Recheck the actual absolute epochs after closure. A ready receipt is
	// not success when its governing deadline is already due in this turn.
	if failure := capturedClosureFailure(time.Now(), out.ShellExitDeadline, drain, code, err); failure != nil {
		return failure
	}
	c.exitedEOF = true
	endClosure() // Closure and rollback completed. Only final drain remains.
	if err = c.w.finishTerminalDrain(drain, "shell_ended"); err != nil {
		deadline, _ := drain.Deadline()
		return &StartError{Code: "final-drain", Deadline: deadline, Cause: errors.Join(err, drain.Err())}
	}
	return nil
}

func capturedClosureCode(phase StartShellExitPhase) (string, error) {
	switch phase {
	case StartShellExitInputBarrier:
		return "input-barrier-timeout", nil
	case StartShellExitOperationStart:
		return "operation-start-timeout", nil
	case StartShellExitPrestartDelivery:
		return "prestart-output-timeout", nil
	default:
		return "", fmt.Errorf("unknown captured shell-exit phase %q", phase)
	}
}

// Compare the captured deadline first, regardless of which Done channel a
// select observed. This also handles both epochs expiring in one owner turn.
func capturedClosureFailure(now, captured time.Time, drain context.Context, code string, cause error) error {
	if !now.Before(captured) {
		return &StartError{Code: code, Deadline: captured, Cause: errors.Join(cause, context.DeadlineExceeded)}
	}
	if cause != nil {
		return cause
	}
	return drain.Err()
}
