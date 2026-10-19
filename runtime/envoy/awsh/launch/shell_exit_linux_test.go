//go:build linux

package launch

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Use the actual selectedChild wait owner and a real child, rather than setting
// ended or synthesizing a reap. This is a focused failure-order test; the native
// lifecycle suite separately proves the full qualified Bash/Awsh integration.
func TestShellExitPreservesJobFailure(t *testing.T) {
	substantive := errors.New("invalid helper state")
	for _, tc := range []struct {
		name   string
		err    error
		before bool
		claim  bool
		clean  bool
	}{
		{"failure-before-reap", substantive, true, true, false},
		{"EOF-before-reap", io.EOF, true, true, false},
		{"failure-after-accepted-reap", substantive, false, true, false},
		{"failure-with-context-cause", errors.Join(substantive, context.Canceled), false, true, false},
		{"failure-with-reap-cause", errors.Join(substantive, errSelectedShellEnded), false, true, false},
		{"failure-with-closed-transport", errors.Join(substantive, net.ErrClosed), false, true, false},
		{"failure-with-wrapped-closed-transport", fmt.Errorf("joined job: %w", errors.Join(substantive, net.ErrClosed)), false, true, false},
		{"unclaimed-worker-failure", substantive, false, false, false},
		{"selected-child-ended", errSelectedShellEnded, false, true, true},
		{"cancelled-job", context.Canceled, false, true, true},
		{"joined-cancellation-only", errors.Join(context.Canceled), false, true, true},
		{"joined-transport-cancellation", errors.Join(net.ErrClosed, context.Canceled), false, true, true},
		{"interrupted-helper-close", fmt.Errorf("read helper: %w", net.ErrClosed), false, true, true},
		{"interrupted-helper-EOF", fmt.Errorf("read helper: %w", io.EOF), false, true, true},
		{"interrupted-helper-partial-frame", fmt.Errorf("read helper: %w", io.ErrUnexpectedEOF), false, true, true},
		{"interrupted-socket-deadline", &net.OpError{Op: "read", Net: "unix", Err: os.ErrDeadlineExceeded}, false, true, true},
		{"clean-reap", nil, false, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			command := exec.Command("/bin/sleep", "60")
			if err := command.Start(); err != nil {
				t.Fatal(err)
			}
			defer command.Process.Release()
			child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
			child.startReaper()
			defer child.close()
			reader, writer, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer reader.Close()
			defer writer.Close()
			operation, cancel := context.WithCancel(context.Background())
			defer cancel()
			s := &Session{child: child, result: writer, State: protocol.PromptState{PhysicalCWD: "/"},
				active: &startRecord{ctx: operation, request: protocol.PrivateExecute{OperationID: "op"}}}
			claim := func() {
				s.startMu.Lock()
				defer s.startMu.Unlock()
				if err := s.failStart(tc.err); err != tc.err {
					t.Fatal("failure cause changed", err)
				}
			}
			if tc.before {
				claim()
			}
			if err := command.Process.Kill(); err != nil {
				t.Fatal(err)
			}
			select {
			case reap := <-child.Reaped:
				if reap.Err != nil || !reap.Status.Signaled() || reap.Status.Signal() != syscall.SIGKILL {
					t.Fatal("missing actual child reap", reap)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("selected-child wait did not join")
			}
			if err := s.AcceptShellExit(); err != nil {
				t.Fatal(err)
			}
			cancel()
			if tc.claim && !tc.before {
				claim()
			}
			ctx, end := context.WithTimeout(context.Background(), time.Second)
			defer end()
			err = s.ReportShellExit(ctx, 137, tc.err)
			if !tc.clean {
				if err != tc.err {
					t.Fatal("substantive job failure became clean exit or lost its cause", err)
				}
				writer.Close()
				data, readErr := io.ReadAll(reader)
				if readErr != nil || len(data) != 0 {
					t.Fatal("fatal job emitted a shell_exit", string(data), readErr)
				}
				return
			}
			if err != nil {
				t.Fatal("accepted reap interruption became fatal", err)
			}
			message, err := protocol.ReadPrivate(bufio.NewReader(reader), protocol.AwshToEnvoy)
			exit, ok := message.(*protocol.PrivateShellExit)
			if err != nil || !ok || exit.Status != 137 || exit.ActiveOperationID != "op" || exit.CWD != "/" {
				t.Fatal("wrong actual terminal fact", message, err)
			}
		})
	}
}

func TestShellExitLatestValidatedCWD(t *testing.T) {
	for _, tc := range []struct {
		name       string
		completion *completionRecord
		want       string
	}{
		{"no-completion", nil, "/previous"},
		{"allocated-before-validation", &completionRecord{}, "/previous"},
		{"validated-before-commit", &completionRecord{state: protocol.PromptState{PhysicalCWD: "/latest"}}, "/latest"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			command := exec.Command("/bin/sleep", "60")
			if err := command.Start(); err != nil {
				t.Fatal(err)
			}
			defer command.Process.Release()
			child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1),
				Reaped: make(chan shellReap, 1), done: make(chan struct{})}
			child.startReaper()
			defer child.close()
			reader, writer, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer reader.Close()
			defer writer.Close()
			s := &Session{child: child, result: writer, State: protocol.PromptState{PhysicalCWD: "/previous"},
				completion: tc.completion, active: &startRecord{request: protocol.PrivateExecute{OperationID: "op"}}}
			if err := command.Process.Kill(); err != nil {
				t.Fatal(err)
			}
			select {
			case reap := <-child.Reaped:
				if reap.Err != nil || !reap.Status.Signaled() || reap.Status.Signal() != syscall.SIGKILL {
					t.Fatal("missing actual child reap", reap)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("selected-child wait did not join")
			}
			if err := s.AcceptShellExit(); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			if err := s.ReportShellExit(ctx, 137, nil); err != nil {
				t.Fatal(err)
			}
			message, err := protocol.ReadPrivate(bufio.NewReader(reader), protocol.AwshToEnvoy)
			exit, ok := message.(*protocol.PrivateShellExit)
			if err != nil || !ok || exit.CWD != tc.want || exit.Status != 137 || exit.ActiveOperationID != "op" {
				t.Fatal("wrong latest validated shell_exit CWD", message, err)
			}
		})
	}
}

func TestReviewReapBeforeCompletionEntry(t *testing.T) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	child.startReaper()
	defer child.close()
	if err := command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case reap := <-child.Reaped:
		if reap.Err != nil || !reap.Status.Signaled() || reap.Status.Signal() != syscall.SIGKILL {
			t.Fatal("missing actual selected-child reap", reap)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("selected-child wait did not join")
	}

	operation, cancel := context.WithTimeout(context.Background(), time.Second)
	s := &Session{child: child, ShellPID: child.PID, parentControlled: true,
		active: &startRecord{ctx: operation, phase: startReleased, request: protocol.PrivateExecute{OperationID: "op"}}}
	if err := s.AcceptShellExit(); err != nil {
		cancel()
		t.Fatal(err)
	}
	cancel() // The supervisor cancels the job after accepting the real reap.
	if _, err := s.completionEntry(operation); err != nil {
		t.Fatal("accepted reap cancellation became a generic completion-entry failure", err)
	}
	if s.shellFailure != nil {
		t.Fatal("completion entry recorded accepted reap cancellation as a job failure", s.shellFailure)
	}

	withoutReap, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	invalid := &Session{parentControlled: true,
		active: &startRecord{ctx: withoutReap, phase: startReleased, request: protocol.PrivateExecute{OperationID: "op"}}}
	stop()
	if _, err := invalid.completionEntry(withoutReap); err == nil {
		t.Fatal("cancellation without accepted reap entered completion")
	}
}

func TestReviewFullReapBeforeCompletionEntry(t *testing.T) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer command.Process.Release()
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	child.startReaper()
	defer child.close()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: t.TempDir() + "/helper.sock", Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	operation, cancel := context.WithCancel(context.Background())
	defer cancel()
	s := &Session{child: child, result: writer, listener: listener, parentControlled: true,
		State:  protocol.PromptState{PhysicalCWD: "/"},
		active: &startRecord{ctx: operation, phase: startReleased, request: protocol.PrivateExecute{OperationID: "op"}}}
	if err := command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case reap := <-child.Reaped:
		if reap.Err != nil || !reap.Status.Signaled() || reap.Status.Signal() != syscall.SIGKILL {
			t.Fatal("missing actual selected-child reap", reap)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("selected-child wait did not join")
	}
	if err := s.AcceptShellExit(); err != nil {
		t.Fatal(err)
	}
	cancel() // The supervisor cancels the job immediately after accepting the real reap.
	jobErr := func() error {
		_, err := s.BeginOperationEvent(operation)
		return err
	}()
	if jobErr == nil || s.shellFailure != nil {
		t.Fatalf("full completion entry did not preserve the accepted reap interruption: err=%v shellFailure=%v", jobErr, s.shellFailure)
	}
	reportCtx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := s.ReportShellExit(reportCtx, 137, jobErr); err != nil {
		t.Fatalf("accepted reap still became fatal after full completion entry: %v", err)
	}
	message, err := protocol.ReadPrivate(bufio.NewReader(reader), protocol.AwshToEnvoy)
	exit, ok := message.(*protocol.PrivateShellExit)
	if err != nil || !ok || exit.Status != 137 || exit.ActiveOperationID != "op" || exit.CWD != "/" {
		t.Fatalf("wrong full-path shell_exit frame: %v %v", message, err)
	}
}

func TestShellExitCompletionEntry(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	s := &Session{parentControlled: true,
		active: &startRecord{ctx: ctx, phase: startReleased, request: protocol.PrivateExecute{OperationID: "op"}}}
	if _, err := s.completionEntry(ctx); err == nil || err.Error() != "completion outside released operation or cleanup epoch" {
		t.Fatal("unaccepted cancellation changed completion-entry validation", err)
	}
}

func TestShellExitInterruptionPreservesRegistrationCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	s := &Session{active: &startRecord{ctx: ctx}}
	s.shellEnding.Store(true)

	if err := fmt.Errorf("completion interrupted: %w", ctx.Err()); !s.shellExitInterruption(err) {
		t.Fatalf("accepted reap cancellation lost at registration boundary: %v", err)
	}
	if err := fmt.Errorf("completion interrupted: %w", errors.New("invalid helper state")); s.shellExitInterruption(err) {
		t.Fatalf("substantive registration failure was classified as cancellation: %v", err)
	}
}
