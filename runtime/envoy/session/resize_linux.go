//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"syscall"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Resize closes the already-admitted output prefix under the existing relay
// owner. It does not drain a continuously writing source to EAGAIN. False with
// no error means the existing drain superseded an unapplied request.
func (r *Relay) Resize(ctx context.Context, columns, rows int64) (applied bool, err error) {
	if columns < 1 || columns > 1000 || rows < 1 || rows > 1000 {
		return false, fmt.Errorf("resize dimensions outside protocol limits")
	}
	if ctx.Err() != nil {
		return false, ctx.Err()
	}
	select {
	case <-r.closing:
		return false, nil
	default:
	}
	_, err = r.boundary(ctx, false, func(offset int64) error {
		select {
		case <-r.closing:
			return context.Canceled
		default:
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		size := [4]uint16{uint16(rows), uint16(columns), 0, 0}
		if cause := ptyIOCTL(r.w.PTY, syscall.TIOCSWINSZ, unsafe.Pointer(&size)); cause != nil {
			deadline, _ := ctx.Deadline()
			fatal := &StartError{Code: "resize-failed", Deadline: deadline, Cause: cause}
			// Publish best effort before boundary's failure path closes sockets.
			_ = r.w.channels.sendEvent(ctx, "diagnostic", func(h protocol.Header) protocol.Message {
				return protocol.Diagnostic{Header: h, Severity: "fatal", Code: fatal.Code, Message: "Terminal resize failed"}
			})
			return fatal
		}
		if err := r.w.channels.sendEvent(ctx, "resize_applied", func(h protocol.Header) protocol.Message {
			return protocol.ResizeApplied{Header: h, Columns: columns, Rows: rows,
				ElapsedUS: time.Since(r.w.ReadyAt).Microseconds(), OutputThrough: offset}
		}); err != nil {
			return err
		}
		applied = true
		return nil
	})
	if r.closeInterrupted(err) {
		return applied, nil // A committed acknowledgement precedes the drain.
	}
	return applied, err
}

// applyPendingResize reuses the current work/fact loop, so a worker waiting
// for the relay cannot prevent private failure or publication selection.
func (c *Coordinator) applyPendingResize(ctx context.Context) error {
	request := c.resize
	c.resize = nil
	c.resizing = true
	defer func() { c.resizing = false }()
	var applied bool
	err := c.work(ctx, false, func(job context.Context) error {
		var err error
		applied, err = c.r.Resize(job, request.Columns, request.Rows)
		return err
	})
	// An expired earlier phase can yield before the output boundary is owned.
	// Carry the accepted request into its existing next phase; attempted frame
	// failures already close Channels and can never enter this retry path.
	if err != nil && !applied && c.w.channels.Err() == nil && c.privateFailure == nil &&
		(errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)) {
		c.resize = request
	}
	return err
}
