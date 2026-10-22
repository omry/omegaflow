// Package helper supplies the shared bounded frame-write primitive.
package helper

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sync"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const controlWriteBudget = 5 * time.Second

type DeadlineWriter interface {
	Write([]byte) (int, error)
	SetWriteDeadline(time.Time) error
}

// WriteControl writes one already encoded control frame. Its five-second epoch
// begins here and is capped by the caller's original phase deadline. Positive
// short writes retain that deadline; any incomplete delivery is an error.
// The caller must close its session on error, including after partial delivery.
func WriteControl(ctx context.Context, w DeadlineWriter, frame []byte) error {
	return writeControl(ctx, w, frame, false)
}

// WriteTerminalControl preserves a complete terminal delivery when Envoy's
// subsequent control EOF cancels the supervisor. Partial writes, deadlines,
// invalid control and signals remain failures. No other frame gets this rule.
func WriteTerminalControl(ctx context.Context, w DeadlineWriter, frame []byte) error {
	m, err := protocol.DecodePrivate(frame, protocol.AwshToEnvoy)
	if err != nil {
		return err
	}
	switch m.(type) {
	case *protocol.PrivateShellExit, *protocol.PrivateClosed:
	default:
		return fmt.Errorf("terminal control requires shell_exit or closed")
	}
	return writeControl(ctx, w, frame, true)
}

func writeControl(ctx context.Context, w DeadlineWriter, frame []byte, terminal bool) error {
	if len(frame) == 0 || len(frame) > protocol.MaxFrameBytes {
		return fmt.Errorf("invalid control frame size")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline := time.Now().Add(controlWriteBudget)
	if phase, ok := ctx.Deadline(); ok && phase.Before(deadline) {
		deadline = phase
	}
	if err := w.SetWriteDeadline(deadline); err != nil {
		return err
	}
	// Cancellation interrupts a blocked syscall without creating another epoch.
	var mu sync.Mutex
	finished := false
	stop := context.AfterFunc(ctx, func() {
		mu.Lock()
		defer mu.Unlock()
		if !finished {
			_ = w.SetWriteDeadline(time.Now())
		}
	})
	err := WriteAll(w, frame)
	mu.Lock()
	finished = true
	stop()
	mu.Unlock()
	result := WithContextCause(ctx, err)
	if terminal && err == nil && errors.Is(result, context.Canceled) && context.Cause(ctx) == io.EOF {
		return nil
	}
	return result
}

// WithContextCause retains both an interrupted socket error and its phase cause.
func WithContextCause(ctx context.Context, err error) error {
	cause := ctx.Err()
	if cause == nil {
		// A socket deadline can fire before the context timer is scheduled.
		if phase, ok := ctx.Deadline(); ok && !time.Now().Before(phase) {
			cause = context.DeadlineExceeded
		}
	}
	if cause != nil {
		return errors.Join(err, cause)
	}
	return err
}
