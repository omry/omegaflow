//go:build linux

package launch

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

var errSelectedShellEnded = errors.New("selected shell ended")

// Only the accepted reap's cancellation may explain interrupted helper I/O.
// A malformed helper request or failed proof remains a substantive failure.
// The caller holds startMu, including while preserving the first failure.
func (s *Session) shellExitInterruption(err error) bool {
	if err == nil || err == errSelectedShellEnded {
		return true
	}
	if !s.shellEnding.Load() {
		return false
	}
	var ctx context.Context
	if s.completion != nil {
		ctx = s.completion.ctx
	} else if s.active != nil {
		ctx = s.active.ctx
	}
	if ctx == nil || ctx.Err() == nil {
		return false
	}
	return cancelledShellIO(err, ctx.Err())
}

// WithContextCause can join a transport error and cancellation, including a
// cancellation alone. Every cause must be an interruption; one matching cause
// cannot hide another substantive error.
func cancelledShellIO(err, cause error) bool {
	if err == cause || err == net.ErrClosed || err == os.ErrClosed || err == io.EOF || err == io.ErrUnexpectedEOF {
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
	message := protocol.PrivateShellExit{Status: status, CWD: s.State.PhysicalCWD}
	if s.active != nil {
		message.ActiveOperationID = s.active.request.OperationID
	}
	frame, err := protocol.EncodePrivate(message, protocol.AwshToEnvoy)
	if err != nil {
		return err
	}
	return helper.WriteControl(ctx, s.result, frame)
}
