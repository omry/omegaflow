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
	"os/signal"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
	"unsafe"

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
		{"helper-ended-before-reap", errProcessEnded, true, true, false},
		{"helper-ended-after-accepted-reap", errProcessEnded, false, true, true},
		{"untyped-process-ended-after-reap", errors.New("process already ended"), false, true, false},
		{"helper-ended-with-substantive-failure", errors.Join(errProcessEnded, substantive), false, true, false},
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

// A real kernel-exited child can precede the sole reaper's publication. The
// join must not mistake that unpublished state for a live shell or reap twice.
func TestShellExitJoinsUnpublishedReap(t *testing.T) {
	for _, kind := range []string{"transport", "selected-child-identity", "helper-process-ended"} {
		t.Run(kind, func(t *testing.T) { testShellExitJoinsUnpublishedReap(t, kind) })
	}
}

func testShellExitJoinsUnpublishedReap(t *testing.T, kind string) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer command.Process.Release()
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	var start sync.Once
	begin := func() { start.Do(child.startReaper) }
	defer func() { begin(); child.close() }()
	ctx, end := context.WithTimeout(context.Background(), 3*time.Second)
	defer end()
	operation, cancel := context.WithCancel(ctx)
	defer cancel()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	s := &Session{child: child, ShellPID: child.PID, result: writer, State: protocol.PromptState{PhysicalCWD: "/"},
		active: &startRecord{ctx: operation, request: protocol.PrivateExecute{OperationID: "reap-join"}}}
	if err := command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	// Observe actual exit without consuming it. Only startReaper may Wait4.
	var info [16]uint64
	for {
		_, _, errno := syscall.Syscall6(syscall.SYS_WAITID, 1, uintptr(child.PID), uintptr(unsafe.Pointer(&info[0])), syscall.WEXITED|syscall.WNOWAIT, 0, 0)
		if errno == syscall.EINTR {
			continue
		}
		if errno != 0 {
			t.Fatal(errno)
		}
		break
	}
	var problem error = fmt.Errorf("helper transport: %w", io.EOF)
	if kind == "selected-child-identity" {
		problem = s.verifyShell()
		if problem != errSelectedShellEnded {
			t.Fatal("kernel-exited selected child became an identity mismatch", problem)
		}
	} else if kind == "helper-process-ended" {
		problem = errProcessEnded
	}
	entered, done := make(chan struct{}), make(chan error, 1)
	go func() {
		close(entered)
		s.startMu.Lock()
		defer s.startMu.Unlock()
		done <- s.failStart(problem)
	}()
	<-entered
	select {
	case err := <-done:
		t.Fatal("kernel exit was classified before the existing reaper joined", err)
	case <-time.After(25 * time.Millisecond):
	}
	accepted := make(chan error, 1)
	reapResult := make(chan shellReap, 1)
	go func() {
		r := <-child.Reaped
		accepted <- s.AcceptShellExit()
		cancel() // the actual supervisor cancels only after accepting reap
		reapResult <- r
	}()
	begin()
	select {
	case err := <-done:
		if err != problem {
			t.Fatal("original job error changed", err)
		}
	case <-ctx.Done():
		t.Fatal("failure classification did not join the actual reaper", ctx.Err())
	}
	if err := <-accepted; err != nil {
		t.Fatal("supervisor did not accept actual reap", err)
	}
	reaped := <-reapResult
	if reaped.Err != nil || !reaped.Status.Signaled() || reaped.Status.Signal() != syscall.SIGKILL || !s.ShellReaped() || s.shellFailure != nil {
		t.Fatal("lost actual reap or recorded a false transport failure", reaped, s.shellFailure)
	}
	cancel()
	if err := s.ReportShellExit(ctx, 137, problem); err != nil {
		t.Fatal("concurrent actual exit became fatal", err)
	}
	message, err := protocol.ReadPrivate(bufio.NewReader(reader), protocol.AwshToEnvoy)
	exit, ok := message.(*protocol.PrivateShellExit)
	if err != nil || !ok || exit.Status != 137 || exit.ActiveOperationID != "reap-join" {
		t.Fatal("missing actual-reap shell_exit", message, err)
	}
}

func TestJoinShellReapDoesNotWaitForLiveChild(t *testing.T) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer command.Process.Release()
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	child.startReaper()
	defer child.close()
	ctx, end := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer end()
	if exited, err := (&Session{child: child}).JoinShellReap(ctx); err != nil || exited {
		t.Fatal("live child did not return immediately", exited, err)
	}
}

// The typed exited-child result must not forgive a live identity violation.
func TestShellExitKeepsLiveIdentityFailure(t *testing.T) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer command.Process.Release()
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	child.startReaper()
	defer child.close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s := &Session{child: child, ShellPID: child.PID, active: &startRecord{ctx: ctx}}
	// This real child inherits our process group. It is not a valid selected shell.
	err := s.verifyShell()
	if err == nil || err == errSelectedShellEnded {
		t.Fatal("live identity mismatch was accepted", err)
	}
	if got := s.failStart(err); got != err || s.shellFailure != err {
		t.Fatal("live identity failure was not preserved", got, s.shellFailure)
	}
}

// This subprocess is a real Go helper with ignored INT and actual kernel
// threads. Exiting a locked goroutine removes its OS thread and /proc entry.
func TestCompletionHelperThreadFixture(t *testing.T) {
	mode := os.Getenv("OMEGAFLOW_HELPER_THREAD_FIXTURE")
	if mode == "" {
		return
	}
	signal.Ignore(syscall.SIGINT)
	if mode == "child" {
		command := exec.Command("/bin/sleep", "3")
		if err := command.Start(); err != nil {
			t.Fatal(err)
		}
		defer func() { _ = command.Process.Kill(); _ = command.Wait() }()
	} else {
		stop, done := make(chan struct{}), make(chan struct{})
		defer func() { close(stop); <-done }()
		go func() {
			defer close(done)
			for {
				var threads sync.WaitGroup
				for i := 0; i < 8; i++ {
					threads.Add(1)
					go func() { runtime.LockOSThread(); defer threads.Done() }()
				}
				threads.Wait()
				select {
				case <-stop:
					return
				case <-time.After(200 * time.Microsecond):
				}
			}
		}()
	}
	fmt.Fprintln(os.Stdout, os.Getpid())
	_, _ = io.Copy(io.Discard, os.Stdin)
}

func TestCompletionHelperThreadChurn(t *testing.T) {
	for _, mode := range []string{"churn", "child"} {
		t.Run(mode, func(t *testing.T) {
			// Fixture lifetime includes scan work and joined race-detector shutdown.
			fixtureCtx, stopFixture := context.WithTimeout(context.Background(), 10*time.Second)
			defer stopFixture()
			ctx, cancel := context.WithTimeout(fixtureCtx, 5*time.Second)
			defer cancel()
			command := exec.CommandContext(fixtureCtx, os.Args[0], "-test.run=^TestCompletionHelperThreadFixture$")
			command.Env = append(os.Environ(), "OMEGAFLOW_HELPER_THREAD_FIXTURE="+mode)
			input, err := command.StdinPipe()
			if err != nil {
				t.Fatal(err)
			}
			output, err := command.StdoutPipe()
			if err != nil {
				t.Fatal(err)
			}
			if err = command.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() {
				_ = input.Close()
				if err := command.Wait(); err != nil {
					t.Error("helper fixture did not join", err)
				}
			}()
			var pid int
			if _, err := fmt.Fscan(bufio.NewReader(output), &pid); err != nil || pid != command.Process.Pid {
				t.Fatal("real helper did not start", pid, err)
			}
			if mode == "child" {
				if err := completionHelperState(ctx, pid); err == nil || !strings.Contains(err.Error(), "completion helper has child") {
					t.Fatal("actual helper child was not rejected by its child list", err)
				}
				return
			}
			for i := 0; i < 500; i++ {
				if err := completionHelperState(ctx, pid); err != nil {
					t.Fatal("real empty helper thread exit became a child failure", err)
				}
			}
			expired, end := context.WithCancel(ctx)
			end()
			if err := completionHelperState(expired, pid); err != context.Canceled {
				t.Fatal("helper scan lost the existing caller epoch", err)
			}
			// Completing the scan must not kill its fixture before the joined exit.
			cancel()
		})
	}
}
