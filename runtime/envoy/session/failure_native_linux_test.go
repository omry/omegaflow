//go:build linux

package session

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type failureFDReport struct {
	PID, Parent int
	FDs         map[string]string
}

func failureDescriptors(pid int) (map[string]string, error) {
	root := "/proc/" + strconv.Itoa(pid) + "/fd"
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	result := make(map[string]string)
	for _, entry := range entries {
		target, err := os.Readlink(filepath.Join(root, entry.Name()))
		if os.IsNotExist(err) {
			continue
		} // The observer's own ReadDir is closed.
		if err != nil {
			return nil, err
		}
		result[entry.Name()] = target
	}
	return result, nil
}

// A normal exec descendant reports literal descriptor identities. It does not
// replace Awsh, its helper, or any process cleanup implementation.
func TestFailureDescriptorObserver(t *testing.T) {
	path := os.Getenv("OMEGAFLOW_FAILURE_FD_REPORT")
	if path == "" {
		return
	}
	fds, err := failureDescriptors(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(failureFDReport{os.Getpid(), os.Getppid(), fds})
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestNativeFailureIsolation(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_FAILURE") != "1" {
		t.Skip("requires actual qualified Bash and Awsh")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	cases := []string{"isolation", "public-malformed", "public-partial-eof", "terminal-eof", "telemetry-eof", "telemetry-reset", "private-malformed", "private-unknown-code", "control-loss", "result-loss", "split-public-malformed"}
	if only := os.Getenv("OMEGAFLOW_FAILURE_CASE"); only != "" {
		cases = []string{only}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			ctx, end := context.WithTimeout(context.Background(), 25*time.Second)
			defer end()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeFailureFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_FAILURE_CHILD="+scenario, "GORACE=atexit_sleep_ms=0")
			out, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(out), "NATIVE FAILURE "+scenario+" PROVED") {
				t.Fatalf("actual failure case %s: %v\n%s", scenario, err, out)
			}
			t.Logf("%s", out)
		})
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("shared harness subreaper state changed", before, after, err)
	}
}

func TestNativeFailureFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_FAILURE_CHILD")
	if scenario == "" {
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	// Keep additional real listening sockets live across the Awsh exec. They
	// exercise inheritance even though the selected-pair listeners have closed.
	if scenario == "isolation" {
		held := listeners(t)
		defer held.Close()
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	if scenario == "isolation" {
		n.peer = func(job context.Context, _ string, pid int64) error { return owner.Cleanup(job, pid) }
		awshFDs, err := failureDescriptors(n.c.w.command.Process.Pid)
		if err != nil {
			t.Fatal(err)
		}
		for _, fd := range []string{"0", "1", "2"} {
			if awshFDs[fd] != "/dev/null" {
				t.Fatal("Awsh inherited bootstrap stream", awshFDs)
			}
		}
		envoyFDs, err := failureDescriptors(os.Getpid())
		if err != nil {
			t.Fatal(err)
		}
		for _, target := range envoyFDs {
			if !strings.HasPrefix(target, "socket:[") {
				continue
			}
			for fd, awshTarget := range awshFDs {
				if target == awshTarget {
					t.Fatalf("Awsh inherited Envoy socket at fd %s", fd)
				}
			}
		}
		for fd, target := range envoyFDs {
			if !strings.HasPrefix(target, "socket:[") && target != awshFDs["3"] && target != awshFDs["4"] {
				continue
			}
			number, _ := strconv.Atoi(fd)
			flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(number), syscall.F_GETFD, 0)
			if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
				t.Fatal("Envoy channel lacks CLOEXEC", fd, target, errno)
			}
		}
		root := t.TempDir()
		for _, nested := range []bool{false, true} {
			path := filepath.Join(root, strconv.FormatBool(nested)+".json")
			source := "OMEGAFLOW_FAILURE_FD_REPORT='" + path + "' /omegaflow-runtime/bin/envoy -test.run=^TestFailureDescriptorObserver$"
			if nested {
				source = "/bin/bash -c \"" + source + "\""
			}
			n.execute(t, "fd-"+strconv.FormatBool(nested), source, "PASS\n", "/", 0)
			b, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var report failureFDReport
			if err = json.Unmarshal(b, &report); err != nil {
				t.Fatal(err)
			}
			for _, pid := range []int{os.Getpid(), n.c.w.command.Process.Pid} {
				fds, err := failureDescriptors(pid)
				if err != nil {
					t.Fatal(err)
				}
				for fd, target := range fds {
					if strings.HasPrefix(target, "socket:[") || (pid == n.c.w.command.Process.Pid && (fd == "3" || fd == "4")) {
						for childFD, childTarget := range report.FDs {
							if target == childTarget {
								t.Fatalf("inherited Envoy/Awsh channel %s at child fd %s", target, childFD)
							}
						}
					}
				}
			}
			for _, fd := range []string{"0", "1", "2"} {
				if !strings.HasPrefix(report.FDs[fd], "/dev/pts/") {
					t.Fatal("descendant bootstrap stream is not its PTY", report)
				}
			}
			t.Logf("descriptor observer nested=%v pid=%d parent=%d fds=%v", nested, report.PID, report.Parent, report.FDs)
		}
		if err := n.c.ShutdownIdle("test-complete"); err != nil {
			t.Fatal(err)
		}
		fmt.Println("NATIVE FAILURE " + scenario + " PROVED")
		return
	}
	// A real ordinary descendant is retained across the injected transport
	// failure. Its reap must come from the concrete census, not a fixture peer.
	source := "/usr/bin/python3 -c 'import os,signal,time; os.fork() and os._exit(0); os.setsid(); signal.signal(signal.SIGHUP,signal.SIG_IGN); time.sleep(300)' & builtin read -r token"
	if scenario == "split-public-malformed" {
		n.c.setup = NewSplitSetup()
		request := splitRequest()
		request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
		request.OperationID = "failure"
		request.Source = source
		n.request(t, &request)
		event := receiveInput(t, n.c.w.channels)
		actual, ok := event.Message.(*protocol.Execute)
		if !ok {
			t.Fatal(event)
		}
		if out, err := n.c.Start(ctx, *actual); err != nil || !out.Started {
			t.Fatal("actual split start", out, err)
		}
		for {
			if _, ok := n.frame(t).(*protocol.OperationStarted); ok {
				break
			}
		}
	} else {
		n.begin(t, ctx, "failure", source)
	}
	for {
		all, err := owner.Census(ctx)
		if err != nil {
			t.Fatal(err)
		}
		found := false
		for pid, p := range all {
			if pid != owner.awsh && pid != owner.shell && p.Live && p.Session == pid {
				found = true
			}
		}
		if found {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("actual ordinary descendant never appeared")
		case <-time.After(time.Millisecond):
		}
	}
	done := make(chan error, 1)
	go func() { done <- n.c.completeOperation(ctx, owner) }()
	switch scenario {
	case "public-malformed", "split-public-malformed":
		_, err = io.WriteString(n.telemetry, "{\"schema\":false}\n")
	case "public-partial-eof":
		_, err = io.WriteString(n.telemetry, "{\"type\":")
		if err == nil {
			err = n.telemetry.CloseWrite()
		}
	case "terminal-eof":
		err = n.terminal.CloseWrite()
	case "telemetry-eof":
		err = n.telemetry.CloseWrite()
	case "telemetry-reset":
		err = n.telemetry.SetLinger(0)
		if err == nil {
			err = n.telemetry.Close()
		}
	case "private-malformed", "private-unknown-code":
		var file *os.File
		file, err = os.OpenFile("/proc/"+strconv.Itoa(owner.awsh)+"/fd/4", os.O_WRONLY, 0)
		if err == nil {
			payload := "wrong-v1\x00bogus\x00"
			if scenario == "private-unknown-code" {
				payload = "awsh-v1\x00protocol_error\x00future-code\x00bounded unknown report\x00"
			}
			for _, b := range []byte(payload) {
				if _, err = file.Write([]byte{b}); err != nil {
					break
				}
			}
			err = errors.Join(err, file.Close())
		}
	case "control-loss":
		err = n.c.w.Control.Close()
	case "result-loss":
		err = n.c.w.Result.Close()
	default:
		t.Fatal(scenario)
	}
	if err != nil {
		t.Fatal("fault injection", err)
	}
	var cause error
	select {
	case cause = <-done:
	case <-ctx.Done():
		t.Fatal("failed operation owner did not join", ctx.Err())
	}
	if cause == nil || n.c.w.channels.Err() == nil {
		t.Fatal("channel failure became success", cause)
	}
	if scenario == "private-unknown-code" && !strings.Contains(cause.Error(), "future-code: bounded unknown report") {
		t.Fatal("unknown code or bounded message was lost", cause)
	}
	if scenario == "public-partial-eof" && !errors.Is(cause, io.ErrUnexpectedEOF) {
		t.Fatal("partial frame lost stable EOF class", cause)
	}
	if (scenario == "terminal-eof" || scenario == "telemetry-eof") && !errors.Is(cause, io.EOF) {
		t.Fatal("channel EOF class was lost", cause)
	}
	t.Logf("original failure: %v", cause)
	// No terminal operation result may follow failed transport. A local reset
	// has already discarded its read endpoint; the other cases retain the bytes.
	if scenario != "telemetry-reset" {
		n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
		for {
			frame, err := readLine(n.reader)
			if err != nil {
				break
			}
			var message struct {
				Type string `json:"type"`
			}
			if json.Unmarshal(frame, &message) != nil {
				t.Fatal("invalid emitted public frame", string(frame))
			}
			if strings.HasPrefix(message.Type, "operation_") || message.Type == "closed" {
				t.Fatal("failed session emitted successful terminal evidence", string(frame))
			}
		}
	}
	bound, stop := context.WithTimeout(context.Background(), finalDrainBudget)
	defer stop()
	if err := n.c.disposeFailure(bound, owner, cause); !errors.Is(err, cause) {
		t.Fatal("lost original cause", cause, err)
	}
	if !n.c.failureDisposed {
		t.Fatal("failed actors not fully disposed", n.c.failure)
	}
	if split := n.c.splitResources(); split != nil && split.operation != nil {
		t.Fatal("failed split resources remain owned")
	}
	if err := owner.proveEnded(bound); err != nil {
		t.Fatal("controlled descendants remain", err)
	}
	select {
	case <-n.c.w.reaped:
	default:
		t.Fatal("Awsh kill substituted for reap")
	}
	if _, err := os.Stat(sessionRuntimeDir); !os.IsNotExist(err) {
		t.Fatal("private session state retained after successful disposal", err)
	}
	first := n.c.failure
	if again := n.c.disposeFailure(bound, owner, errors.New("later failure")); again != first {
		t.Fatal("repeat disposal changed original failure", first, again)
	}
	if out, err := n.c.Start(ctx, startRequest()); err == nil || out.Started {
		t.Fatal("failed session admitted next operation", out, err)
	}
	// Signal observation is a local actor fact, never a synthetic shell result.
	if n.c.w.command.ProcessState == nil {
		t.Fatal("missing actual reap state")
	}
	if status, ok := n.c.w.command.ProcessState.Sys().(syscall.WaitStatus); ok {
		t.Logf("actual Awsh reap=%v", status)
	}
	fmt.Println("NATIVE FAILURE " + scenario + " PROVED")
}
