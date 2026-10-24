//go:build linux

package launch

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"syscall"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

var errSelectedShellEnded = errors.New("selected shell ended")
var errProcessEnded = errors.New("process already ended")

// Only proven selected-child exit or its cancellation may explain interrupted
// helper transport. A malformed request or failed proof remains substantive.
// The caller holds startMu, including while preserving the first failure.
func (s *Session) shellExitInterruption(err error) bool {
	if err == nil {
		return true
	}
	var ctx context.Context
	if s.completion != nil {
		ctx = s.completion.ctx
	} else if s.active != nil {
		ctx = s.active.ctx
	} else {
		ctx = s.startContext
	}
	if ctx == nil {
		return false
	}
	if !s.shellEnding.Load() {
		// A helper can lose its parent before the independent reaper publishes
		// ended. Join actual kernel exit before recording that I/O as failure.
		if err != errSelectedShellEnded && !cancelledShellIO(err, nil) {
			return false
		}
		exited, reapErr := s.JoinShellReap(ctx)
		if reapErr != nil || !exited {
			// The supervisor can accept reap and cancel this job while its
			// join is waking. Preserve that already accepted cause.
			if !s.shellEnding.Load() {
				return false
			}
		} else if s.AcceptShellExit() != nil {
			return false
		}
	}
	return err == errSelectedShellEnded || cancelledShellIO(err, ctx.Err())
}

// WithContextCause can join a transport error and cancellation, including a
// cancellation alone. Every cause must be an interruption; one matching cause
// cannot hide another substantive error.
func cancelledShellIO(err, cause error) bool {
	if err == cause || err == errProcessEnded || err == net.ErrClosed || err == os.ErrClosed || err == io.EOF || err == io.ErrUnexpectedEOF {
		return true
	}
	if joined, ok := err.(interface{ Unwrap() []error }); ok {
		children := joined.Unwrap()
		for _, child := range children {
			if !cancelledShellIO(child, cause) {
				return false
			}
		}
		return len(children) != 0
	}
	if child := errors.Unwrap(err); child != nil {
		return cancelledShellIO(child, cause)
	}
	transport, ok := err.(net.Error)
	return ok && transport.Timeout()
}

// ShellReaped observes the existing direct-child wait owner. It cannot infer
// an exit from private EOF, a helper error or a recycled numeric PID.
func (s *Session) ShellReaped() bool {
	if s.child == nil {
		return false
	}
	s.child.mu.Lock()
	defer s.child.mu.Unlock()
	return s.child.ended
}

// JoinShellReap observes exit without reaping or consuming Reaped. The same
// selected-child mutex protects this observation and the sole Wait4 owner.
// A live shell returns immediately; an exited shell joins that owner's done
// publication under the caller's existing context, with no new phase timer.
func (s *Session) JoinShellReap(ctx context.Context) (bool, error) {
	if s.child == nil {
		return false, nil
	}
	if ctx == nil {
		return false, fmt.Errorf("selected-shell reap requires a context")
	}
	c := s.child
	c.mu.Lock()
	exited := c.ended
	var probeErr error
	if !exited {
		var info [16]uint64
		for {
			if probeErr = ctx.Err(); probeErr != nil {
				break
			}
			_, _, errno := syscall.Syscall6(syscall.SYS_WAITID, 1, uintptr(c.PID),
				uintptr(unsafe.Pointer(&info[0])), syscall.WEXITED|syscall.WNOWAIT|syscall.WNOHANG, 0, 0)
			if errno == syscall.EINTR {
				continue
			}
			if errno != 0 {
				probeErr = errno
			}
			exited = *(*int32)(unsafe.Pointer(&info[0])) != 0
			break
		}
	}
	c.mu.Unlock()
	if probeErr != nil || !exited {
		return false, probeErr
	}
	select {
	case <-c.done:
		return true, c.reap.Err
	case <-ctx.Done():
		return false, ctx.Err()
	}
}

// AcceptShellExit preserves the result pipe after actual selected-child reap.
// The supervisor next cancels and joins its existing job. Its context callback
// interrupts helper I/O only after that cancellation is visible to the job.
func (s *Session) AcceptShellExit() error {
	if !s.ShellReaped() {
		return fmt.Errorf("shell-exit interruption requires selected-child reap")
	}
	s.shellEnding.Store(true)
	return nil
}

// ReportShellExit is a shell-local fact only. Envoy retains lifecycle outcome
// selection, descendant cleanup, deadline enforcement and public publication.
// The caller has joined the operation job and remains the sole result writer.
func (s *Session) ReportShellExit(ctx context.Context, status int64, jobErr error) error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if !s.shellEnding.Load() || !s.ShellReaped() {
		return fmt.Errorf("shell_exit requires accepted selected-child reap")
	}
	if s.shellFailure != nil {
		return s.shellFailure
	}
	if !s.shellExitInterruption(jobErr) {
		return jobErr
	}
	cwd := s.State.PhysicalCWD
	// Completion state is populated only after live-state validation. It can
	// precede FinishCompletion's commit while Envoy cleans up descendants.
	if s.completion != nil && s.completion.state.PhysicalCWD != "" {
		cwd = s.completion.state.PhysicalCWD
	}
	message := protocol.PrivateShellExit{Status: status, CWD: cwd}
	if s.active != nil {
		message.ActiveOperationID = s.active.request.OperationID
	}
	frame, err := protocol.EncodePrivate(message, protocol.AwshToEnvoy)
	if err != nil {
		return err
	}
	return helper.WriteTerminalControl(ctx, s.result, frame)
}
