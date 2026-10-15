//go:build linux

package supervise

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// This exercises the actual manifested Awsh binary, not a test replacement of
// its dispatcher or checker. Only the Envoy peer and workload are deterministic.
func TestNativePrivateOperation(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_OPERATION") != "1" {
		t.Skip("native genuinely qualified target only")
	}
	if os.Geteuid() == 0 {
		t.Fatal("workload must be non-root")
	}
	for _, outcome := range []string{"ordinary", "syntax-reject", "wrong-start", "wrong-ack", "early-input-closed", "duplicate-ack", "wrong-input-closed", "eof-running", "malformed-running", "term-running", "eof-completion", "term-completion", "helper-death", "helper-death-cancel"} {
		t.Run(outcome, func(t *testing.T) { nativeOperation(t, outcome) })
	}
}

func nativeOperation(t *testing.T, outcome string) {
	if err := os.Mkdir("/run/omegaflow/session", 0700); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll("/run/omegaflow/session") })
	master, slave := nativePTY(t)
	cr, cw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer cr.Close()
	defer cw.Close()
	rr, rw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer rr.Close()
	defer rw.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/omegaflow-runtime/bin/awsh", "supervise", "--control-fd=3", "--result-fd=4", "--pty-slave-fd=5", "--session-runtime-dir=/run/omegaflow/session")
	coverage := os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_COVERAGE") == "1"
	if coverage {
		// Supplemental Go statement coverage of the same production functions.
		// The normal native run above still proves the actual manifested entry.
		cmd = exec.CommandContext(ctx, os.Args[0], "-test.run=^TestControlledDispatchCoverageChild$", "-test.coverprofile=/evidence/"+outcome+"-"+strconv.FormatInt(time.Now().UnixNano(), 10)+".cover")
		cmd.Env = append(os.Environ(), "OMEGAFLOW_DISPATCH_COVERAGE_CHILD=1")
	}
	cmd.ExtraFiles = []*os.File{cr, rw, slave}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	wait := make(chan error, 1)
	go func() { wait <- cmd.Wait() }()
	waited := false
	defer func() {
		cancel()
		if !waited {
			cmd.Process.Kill()
			<-wait
		}
		// Test Envoy teardown peer only: later lifecycle slices own production
		// descendant cleanup. Failed helpers may remain in their fail-stop mode.
		entries, _ := os.ReadDir("/proc")
		for _, entry := range entries {
			pid, err := strconv.Atoi(entry.Name())
			if err != nil {
				continue
			}
			data, err := os.ReadFile("/proc/" + entry.Name() + "/stat")
			if err != nil {
				continue
			}
			fields := strings.Fields(string(data)[strings.LastIndex(string(data), ")")+1:])
			if len(fields) > 3 && fields[3] == strconv.Itoa(cmd.Process.Pid) {
				syscall.Kill(pid, syscall.SIGKILL)
			}
		}
	}()
	cr.Close()
	rw.Close()
	slave.Close()
	deadline, _ := ctx.Deadline()
	rr.SetReadDeadline(deadline)
	master.SetReadDeadline(deadline)
	reader := bufio.NewReader(rr)
	read := func() protocol.PrivateMessage {
		t.Helper()
		m, err := protocol.ReadPrivate(reader, protocol.AwshToEnvoy)
		if err != nil {
			t.Fatal("private result", err)
		}
		return m
	}
	send := func(m protocol.PrivateMessage) {
		t.Helper()
		b, err := protocol.EncodePrivate(m, protocol.EnvoyToAwsh)
		if err != nil {
			t.Fatal(err)
		}
		if err := helper.WriteControl(ctx, cw, b); err != nil {
			t.Fatal(err)
		}
	}
	ready, ok := read().(*protocol.PrivateReady)
	if !ok {
		t.Fatal("missing actual ready")
	}
	fatal := func() {
		t.Helper()
		select {
		case err := <-wait:
			waited = true
			exit, ok := err.(*exec.ExitError)
			code := 2
			if coverage {
				code = 1 // Go test reports Run's expected fatal return as failure
			}
			if !ok || exit.ExitCode() != code {
				t.Fatal("invalid boundary did not return through fatal cleanup", err)
			}
		case <-ctx.Done():
			t.Fatal("cancelled dispatcher did not join its I/O job")
		}
		if _, err := os.Stat("/proc/" + strconv.FormatInt(ready.ShellPID, 10)); !os.IsNotExist(err) {
			t.Fatal("failed operation did not reap selected shell", err)
		}
	}
	if outcome == "syntax-reject" {
		send(protocol.PrivateExecute{OperationID: "rejected", ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "exclusive", Inspections: []protocol.Inspection{}, Source: "if then"})
		if m, ok := read().(*protocol.PrivateRejected); !ok || m.OperationID != "rejected" || m.Code != "source-syntax" {
			t.Fatal("source rejection was not recoverable", m)
		}
	}
	secondSource := "test \"$?\" = 1 && test \"$persist\" = kept && test \"$PWD\" = /tmp"
	next, err := protocol.EncodePrivate(protocol.PrivateExecute{OperationID: "second", ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "exclusive", Inspections: []protocol.Inspection{}, Source: secondSource}, protocol.EnvoyToAwsh)
	if err != nil {
		t.Fatal(err)
	}
	request := func(id, source string, status int64, cwd string) bool {
		t.Helper()
		if id == "first" {
			send(protocol.PrivateExecute{OperationID: id, ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "exclusive", Inspections: []protocol.Inspection{}, Source: source})
		}
		if m, ok := read().(*protocol.PrivateSubmit); !ok || m.OperationID != id {
			t.Fatal("missing submit", m)
		}
		if _, err := master.Write([]byte{0x18, 0x02}); err != nil {
			t.Fatal(err)
		}
		if m, ok := read().(*protocol.PrivateStartPrepared); !ok || m.OperationID != id {
			t.Fatal("missing prepared", m)
		}
		if outcome == "wrong-start" || outcome == "early-input-closed" {
			if outcome == "wrong-start" {
				send(protocol.PrivateStartRelease{OperationID: "wrong"})
			} else {
				send(protocol.PrivateInputClosed{OperationID: id})
			}
			fatal()
			return false
		}
		send(protocol.PrivateStartRelease{OperationID: id})
		if m, ok := read().(*protocol.PrivateStarted); !ok || m.OperationID != id {
			t.Fatal("missing started", m)
		}
		if outcome == "wrong-ack" {
			send(protocol.PrivateStartedAck{OperationID: "wrong"})
			fatal()
			return false
		}
		send(protocol.PrivateStartedAck{OperationID: id})
		if outcome == "duplicate-ack" {
			send(protocol.PrivateStartedAck{OperationID: id})
			fatal()
			return false
		}
		if m, ok := read().(*protocol.PrivateStartReleased); !ok || m.OperationID != id {
			t.Fatal("missing released", m)
		}
		if outcome == "eof-running" || outcome == "malformed-running" || outcome == "term-running" {
			if outcome == "eof-running" {
				cw.Close()
			} else if outcome == "term-running" {
				cmd.Process.Signal(syscall.SIGTERM)
			} else {
				cw.Write([]byte("awsh-v1\x00unexpected\x00"))
			}
			fatal()
			return false
		}
		close, ok := read().(*protocol.PrivateInputClose)
		if !ok || close.OperationID != id || close.CompletionHelperPID <= 0 {
			t.Fatal("missing completion helper hold", close)
		}
		if _, err := os.Stat("/proc/" + strconv.FormatInt(close.CompletionHelperPID, 10)); err != nil {
			t.Fatal("completion helper was not retained", err)
		}
		// Withhold input_closed under a deliberate peer delay. No result may
		// arrive and the first helper must remain alive throughout that window.
		rr.SetReadDeadline(time.Now().Add(30 * time.Millisecond))
		if m, err := protocol.ReadPrivate(reader, protocol.AwshToEnvoy); err == nil || !os.IsTimeout(err) {
			t.Fatal("completion escaped matching input_closed hold", m, err)
		}
		rr.SetReadDeadline(deadline)
		if _, err := os.Stat("/proc/" + strconv.FormatInt(close.CompletionHelperPID, 10)); err != nil {
			t.Fatal("completion helper escaped during peer delay", err)
		}
		if outcome == "wrong-input-closed" || outcome == "eof-completion" || outcome == "term-completion" || outcome == "helper-death" || outcome == "helper-death-cancel" {
			if outcome == "eof-completion" {
				cw.Close()
			} else if outcome == "term-completion" {
				cmd.Process.Signal(syscall.SIGTERM)
			} else if outcome == "helper-death" || outcome == "helper-death-cancel" {
				if err := syscall.Kill(int(close.CompletionHelperPID), syscall.SIGKILL); err != nil {
					t.Fatal(err)
				}
				if outcome == "helper-death-cancel" {
					// An early helper exit may leave Bash in fail-stop. The
					// Envoy peer cancels that wait through its control owner.
					cw.Close()
					fatal()
					return false
				}
				// A zombie is still an owned process. Require full reap before
				// probing the missing-helper acknowledgement boundary.
				tick := time.NewTicker(time.Millisecond)
				defer tick.Stop()
				for {
					_, err := os.Stat("/proc/" + strconv.FormatInt(close.CompletionHelperPID, 10))
					if os.IsNotExist(err) {
						break
					}
					select {
					case <-ctx.Done():
						t.Fatal("test helper death not observed")
					case <-tick.C:
					}
				}
				send(protocol.PrivateInputClosed{OperationID: id})
			} else {
				send(protocol.PrivateInputClosed{OperationID: "wrong"})
			}
			fatal()
			return false
		}
		send(protocol.PrivateInputClosed{OperationID: id})
		completed, ok := read().(*protocol.PrivateCompleted)
		if !ok || completed.OperationID != id || completed.Status != status || completed.PhysicalCWD != cwd {
			t.Fatal("wrong completion", completed)
		}
		if id == "first" {
			// Reply immediately, before helper/PTY inspection or a deliberate
			// peer delay. Pre-encoding removes unrelated controller work from
			// the race with the preceding Session method's final cleanup.
			if err := helper.WriteAll(cw, next); err != nil {
				t.Fatal(err)
			}
		}
		if _, err := os.Stat("/proc/" + strconv.FormatInt(close.CompletionHelperPID, 10)); !os.IsNotExist(err) {
			t.Fatal("completion helper not reaped", err)
		}
		master.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
		b := make([]byte, 4096)
		// accept-line emits its line ending, as in the existing native adapter
		// proofs. Neither authored source nor canonical frame bytes may appear.
		if n, err := master.Read(b); n != 2 || string(b[:n]) != "\r\n" || err != nil {
			t.Fatalf("unexpected PTY bytes for silent command: %q %v", b[:n], err)
		}
		if n, err := master.Read(b); n != 0 || !os.IsTimeout(err) {
			t.Fatalf("source/frame or diagnostic leaked into PTY: %q %v", b[:n], err)
		}
		master.SetReadDeadline(deadline)
		return true
	}
	source := "persist=kept; cd /tmp; false"
	if outcome == "eof-running" || outcome == "malformed-running" || outcome == "term-running" {
		source = "while :; do :; done" // real shell work; no descendant fixture
	}
	if !request("first", source, 1, "/tmp") {
		return
	}
	request("second", secondSource, 0, "/tmp")
	send(protocol.PrivateShutdown{})
	if m, ok := read().(*protocol.PrivateClosed); !ok || m.CWD != "/tmp" {
		t.Fatal("missing orderly shutdown", m)
	}
	select {
	case err := <-wait:
		waited = true
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(fmt.Errorf("Awsh did not join: %w", ctx.Err()))
	}
}

func TestControlledDispatchCoverageChild(t *testing.T) {
	if os.Getenv("OMEGAFLOW_DISPATCH_COVERAGE_CHILD") != "1" {
		t.Skip("supplemental native component coverage only")
	}
	if err := Run([]string{"supervise", "--control-fd=3", "--result-fd=4", "--pty-slave-fd=5", "--session-runtime-dir=/run/omegaflow/session"}); err != nil {
		t.Error(err)
	}
}
