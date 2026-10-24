//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type terminalWaitHold struct {
	observed, publish chan struct{}
	once              sync.Once
}

var nativeTerminalHeldWait *terminalWaitHold

// The native runner's retained Go overlay calls this only after the real
// command.Wait returned. It delays publication, never replacing Wait or status.
func nativeTerminalWaitPublication() {
	if h := nativeTerminalHeldWait; h != nil {
		close(h.observed)
		<-h.publish
	}
}

func (h *terminalWaitHold) release() {
	h.once.Do(func() { close(h.publish) })
}

func TestNativeTerminalClosure(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_TERMINAL") != "1" {
		t.Skip("requires actual qualified Bash and Awsh")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	cases := []string{"shutdown", "idle-exit", "shutdown-cross-exit", "active-zero", "active-nonzero", "active-signal", "active-descendant", "fault-nonzero-awsh", "fault-signalled-awsh", "fault-adopted-live", "fault-duplicate", "fault-idle-result", "output-order"}
	for _, form := range []string{"closed", "shell-exit"} {
		for _, variant := range []string{"premature-eof", "trailing-frame", "reset", "signal", "nonzero-reap", "missing-eof", "missing-reap"} {
			cases = append(cases, "matrix-"+form+"-"+variant)
		}
	}
	if only := os.Getenv("OMEGAFLOW_TERMINAL_CASE"); only != "" {
		cases = []string{only}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			ctx, end := context.WithTimeout(context.Background(), 25*time.Second)
			defer end()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeTerminalFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_TERMINAL_CHILD="+scenario, "GORACE=atexit_sleep_ms=0")
			out, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(out), "NATIVE TERMINAL "+scenario+" PROVED") {
				t.Fatalf("actual terminal case %s: %v\n%s", scenario, err, out)
			}
			t.Logf("%s", out)
		})
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("shared harness subreaper changed", before, after, err)
	}
}

func TestNativeTerminalFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_TERMINAL_CHILD")
	if scenario == "" {
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	if strings.HasSuffix(scenario, "-missing-reap") {
		nativeTerminalHeldWait = &terminalWaitHold{observed: make(chan struct{}), publish: make(chan struct{})}
		defer nativeTerminalHeldWait.release()
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	e := &evidenceTranscript{n: n, sink: &nativeSplitSink{wake: make(chan struct{}, 1)}, base: n.through, offset: n.through, stream: "pty", ctx: ctx}
	collected := make(chan error, 1)
	n.terminal.SetReadDeadline(time.Now().Add(20 * time.Second))
	go func() { _, err := io.Copy(e.sink, n.terminal); collected <- err }()
	joined := false
	defer func() {
		if !joined {
			n.terminal.SetReadDeadline(time.Now())
			select {
			case <-collected:
			case <-time.After(time.Second):
				t.Error("terminal collector did not join")
			}
		}
	}()
	if strings.HasPrefix(scenario, "matrix-") {
		nativeTerminalMatrix(t, ctx, n, owner, scenario)
		t.Logf("NATIVE TERMINAL %s PROVED: actual actors, fatal original closure bound and separately accounted harness cleanup", scenario)
		return
	}
	if scenario == "output-order" {
		nativeTerminalOutputOrder(t, ctx, n, owner, e, collected)
		joined = true
		t.Log("NATIVE TERMINAL output-order PROVED: terminal write EOF before public closed and final owner release")
		return
	}
	if strings.HasPrefix(scenario, "fault-") {
		nativeTerminalFailure(t, ctx, n, owner, e, scenario)
		t.Logf("NATIVE TERMINAL %s PROVED: trusted terminal-byte injection, actual Awsh failure/reap and actual disposal", scenario)
		return
	}
	wantReason := "requested-terminal-end"
	if err := owner.reapAdoptedShell(ctx); err == nil || !strings.Contains(err.Error(), "original Awsh reap") {
		t.Fatal("live Awsh lost its selected-shell wait ownership", err)
	}
	if err := owner.ProveIdle(ctx); err != nil {
		t.Fatal("rejected wait changed live idle topology", err)
	}
	var status int64
	var descendant *processRef
	var pidfile string
	if strings.HasPrefix(scenario, "active-") {
		source := "builtin read -r release; builtin printf 'FINAL-é\\n'; builtin exit 0"
		if scenario == "active-nonzero" {
			status, source = 23, strings.Replace(source, "exit 0", "exit 23", 1)
		} else if scenario == "active-signal" {
			status, source = 137, strings.Replace(source, "builtin exit 0", "builtin kill -KILL \"$$\"", 1)
		} else if scenario == "active-descendant" {
			status = 23
			pidfile = filepath.Join(t.TempDir(), "child")
			source = "nohup sleep 60 >/dev/null 2>&1 & builtin printf '%s' \"$!\" >'" + pidfile + "'; " + strings.Replace(source, "exit 0", "exit 23", 1)
			defer func() {
				if descendant != nil {
					descendant.close()
				}
			}()
			// Store only the path here. The actual identity is pinned after
			// operation_started and before releasing the live Bash read.
		}
		req := startRequest()
		req.OperationID, req.Source = "terminal", source
		req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
		n.request(t, &req)
		command := receiveInput(t, n.c.w.channels).Message.(*protocol.Execute)
		out, err := n.c.Start(ctx, *command)
		if err != nil || !out.Started {
			t.Fatal("actual terminal start", out, err)
		}
		if _, ok := e.event(t).(*protocol.OperationStarted); !ok {
			t.Fatal("missing actual operation_started")
		}
		if scenario == "active-descendant" {
			var raw []byte
			for len(raw) == 0 {
				raw, _ = os.ReadFile(pidfile)
				select {
				case <-ctx.Done():
					t.Fatal("missing actual authored child", ctx.Err())
				case <-time.After(time.Millisecond):
				}
			}
			pid, err := strconv.Atoi(string(raw))
			if err != nil {
				t.Fatal(err)
			}
			descendant, _, err = pinProcess(ctx, pid)
			if err != nil {
				t.Fatal(err)
			}
		}
		done := make(chan error, 1)
		go func() { done <- n.c.completeOperation(ctx, owner) }()
		if _, err := n.terminal.Write([]byte("release\n")); err != nil {
			t.Fatal(err)
		}
		select {
		case err = <-done:
		case <-ctx.Done():
			t.Fatal("actual terminal operation did not join", ctx.Err())
		}
		if err != nil {
			t.Fatal("actual shell-end completion", err)
		}
		completed, ok := e.event(t).(*protocol.OperationCompleted)
		if !ok || completed.Status != status || completed.ShellEnded == nil || !*completed.ShellEnded {
			t.Fatal("selected shell status became Awsh status", completed)
		}
		if n.c.exited == nil || !n.c.exitedEOF || n.c.exited.Status != status {
			t.Fatal("missing actual terminal frame or EOF", n.c.exited)
		}
		if !bytes.Contains(e.sink.bytes(), []byte("FINAL-é\r\n")) {
			t.Fatal("lost final authored output")
		}
		wantReason = "shell_ended"
	} else if scenario == "idle-exit" {
		if err := syscall.Kill(int(n.c.w.ShellPID), syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		n.request(t, &protocol.Shutdown{Header: protocol.Header{Schema: protocol.Schema, Type: "shutdown", Seq: n.seq}, Reason: "crossed-request"})
		if err := n.c.idleShellExit(ctx, owner); err != nil {
			t.Fatal("actual idle shell-exit drain", err)
		}
		if n.c.exited == nil || n.c.exited.Status != 137 || !n.c.exitedEOF {
			t.Fatal("idle shell exit lost actual status/EOF", n.c.exited)
		}
		wantReason = "shell_ended"
	} else {
		if scenario == "shutdown-cross-exit" {
			if err := syscall.Kill(int(n.c.w.ShellPID), syscall.SIGKILL); err != nil {
				t.Fatal(err)
			}
			// The real Awsh writes shell_exit, closes control and exits.
			// Envoy has not accepted the buffered private fact. Accepting
			// shutdown first must retain the controller's public reason.
			select {
			case <-n.c.w.reaped:
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
		} else if scenario != "shutdown" {
			t.Fatal("unknown native terminal case", scenario)
		}
		n.request(t, &protocol.Shutdown{Header: protocol.Header{Schema: protocol.Schema, Type: "shutdown", Seq: n.seq}, Reason: wantReason})
		request, ok := receiveInput(t, n.c.w.channels).Message.(*protocol.Shutdown)
		if !ok {
			t.Fatal("missing actual public shutdown")
		}
		if err := n.c.shutdownIdle(request.Reason, owner); err != nil {
			t.Fatal("actual requested shutdown closure", err)
		}
	}
	if n.c.w.waitErr != nil {
		t.Fatal("nonzero Awsh became orderly shell end", n.c.w.waitErr)
	}
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("terminal result preceded actual cleanup/reap", err)
	}
	if descendant != nil {
		if _, err := descendant.observe(ctx); !processGone(err) {
			t.Fatal("authored child survived terminal closure", err)
		}
	}
	for _, kind := range []string{"draining", "closed"} {
		m := e.event(t)
		switch v := m.(type) {
		case *protocol.Draining:
			if kind != "draining" || v.Reason != wantReason || v.OutputThrough != e.offset {
				t.Fatal("wrong native drain reason/frontier", m)
			}
		case *protocol.Closed:
			if kind != "closed" || v.Reason != wantReason || v.OutputThrough != e.offset {
				t.Fatal("wrong native closed order/frontier", m)
			}
		default:
			t.Fatal("unexpected native terminal event", m)
		}
	}
	select {
	case err := <-collected:
		joined = true
		if err != nil {
			t.Fatal("terminal EOF after closed was not clean", err)
		}
	case <-ctx.Done():
		t.Fatal("missing actual terminal EOF", ctx.Err())
	}
	if e.offset != e.base+int64(len(e.sink.bytes())) {
		t.Fatal("final mark did not cover exact terminal bytes")
	}
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := readLine(n.reader); !errors.Is(err, io.EOF) {
		t.Fatal("event followed public closed", err)
	}
	if _, err := os.Stat(sessionRuntimeDir); !os.IsNotExist(err) {
		t.Fatal("private runtime path survived terminal closure", err)
	}
	t.Logf("NATIVE TERMINAL %s PROVED: actual Awsh reap zero, requested/shell_ended winner and exact output frontier", scenario)
}

// Hold only final resource disposal, after the real private EOF/reap and tree
// cleanup. This prevents a later whole-socket Close from masking an omitted
// earlier CloseWrite. No transport, process, result or cleanup peer is replaced.
func nativeTerminalOutputOrder(t *testing.T, ctx context.Context, n *nativeOrdinary, owner *processCensus, e *evidenceTranscript, collected <-chan error) {
	t.Helper()
	n.c.w.closeMu.Lock()
	done := make(chan error, 1)
	go func() { done <- n.c.shutdownIdle("ordered-end", owner) }()
	defer func() {
		n.c.w.closeMu.Unlock()
		select {
		case err := <-done:
			if err != nil {
				t.Error("actual terminal owner did not close", err)
			}
		case <-ctx.Done():
			t.Error("terminal owner did not join", ctx.Err())
		}
	}()
	for _, kind := range []string{"draining", "closed"} {
		m := e.event(t)
		switch v := m.(type) {
		case *protocol.Draining:
			if kind != "draining" || v.Reason != "ordered-end" || v.OutputThrough != e.offset {
				t.Fatal("wrong terminal drain order", m)
			}
		case *protocol.Closed:
			if kind != "closed" || v.Reason != "ordered-end" || v.OutputThrough != e.offset {
				t.Fatal("wrong terminal close order", m)
			}
		default:
			t.Fatal("unexpected terminal event", m)
		}
	}
	select {
	case err := <-collected:
		if err != nil {
			t.Fatal("terminal EOF was not clean", err)
		}
	case <-time.After(time.Second):
		t.Fatal("terminal EOF did not precede final owner release")
	}
	if err := owner.proveEnded(ctx); err != nil || e.offset != e.base+int64(len(e.sink.bytes())) {
		t.Fatal("closed preceded actual cleanup or covering output", err)
	}
}

// Faults keep the real Awsh process and its Wait owner. The trusted harness
// injects terminal bytes while Awsh is stopped, then causes a real supervisor
// failure. These frames are negative test input, never positive shell evidence.
func nativeTerminalFailure(t *testing.T, ctx context.Context, n *nativeOrdinary, owner *processCensus, e *evidenceTranscript, scenario string) {
	t.Helper()
	adoptedLive := scenario == "fault-adopted-live"
	if adoptedLive {
		ready := filepath.Join(t.TempDir(), "ready")
		req := startRequest()
		req.OperationID, req.Source = "terminal", "builtin trap '' HUP; builtin printf ready >'"+ready+"'; while :; do :; done"
		req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
		n.request(t, &req)
		command := receiveInput(t, n.c.w.channels).Message.(*protocol.Execute)
		out, err := n.c.Start(ctx, *command)
		if err != nil || !out.Started {
			t.Fatal("actual adopted-live loop start", out, err)
		}
		if _, ok := e.event(t).(*protocol.OperationStarted); !ok {
			t.Fatal("missing actual operation_started for adopted-live loop")
		}
		for {
			if raw, readErr := os.ReadFile(ready); readErr == nil && string(raw) == "ready" {
				break
			}
			select {
			case <-ctx.Done():
				t.Fatal("authored HUP-ignore readiness was not observed", ctx.Err())
			case <-time.After(time.Millisecond):
			}
		}
	}
	if !adoptedLive {
		if err := syscall.Kill(owner.awsh, syscall.SIGSTOP); err != nil {
			t.Fatal(err)
		}
		defer syscall.Kill(owner.awsh, syscall.SIGCONT)
		for {
			stat, err := os.ReadFile("/proc/" + strconv.Itoa(owner.awsh) + "/stat")
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(stat), ") T") || strings.Contains(string(stat), ") t") {
				break
			}
			select {
			case <-ctx.Done():
				t.Fatal("actual Awsh did not stop", ctx.Err())
			case <-time.After(time.Millisecond):
			}
		}
		writer, err := os.OpenFile("/proc/"+strconv.Itoa(owner.awsh)+"/fd/4", os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		frame, err := protocol.EncodePrivate(protocol.PrivateShellExit{Status: 23, CWD: n.c.w.CWD}, protocol.AwshToEnvoy)
		if err != nil {
			t.Fatal(err)
		}
		if scenario == "fault-duplicate" {
			frame = append(frame, frame...)
		} else if scenario == "fault-idle-result" {
			frame, err = protocol.EncodePrivate(protocol.PrivateClosed{Reason: "shutdown", CWD: n.c.w.CWD}, protocol.AwshToEnvoy)
			if err != nil {
				t.Fatal(err)
			}
		}
		for _, b := range frame {
			if _, err := writer.Write([]byte{b}); err != nil {
				t.Fatal(err)
			}
		}
		writer.Close()
	}
	if !adoptedLive && scenario == "fault-nonzero-awsh" {
		if _, err := n.c.w.Control.Write([]byte("invalid-v1\x00bad\x00")); err != nil {
			t.Fatal(err)
		}
		if err := syscall.Kill(owner.awsh, syscall.SIGCONT); err != nil {
			t.Fatal(err)
		}
	} else if err := syscall.Kill(owner.awsh, syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	select {
	case <-n.c.w.reaped:
	case <-ctx.Done():
		t.Fatal("actual failed Awsh was not reaped", ctx.Err())
	}
	var observed *exec.ExitError
	if !errors.As(n.c.w.waitErr, &observed) {
		t.Fatal("fault did not produce actual failed Awsh reap", n.c.w.waitErr)
	}
	if scenario == "fault-nonzero-awsh" && observed.ExitCode() != 2 {
		t.Fatal("malformed control did not produce normal nonzero Awsh exit", observed)
	}
	if (scenario == "fault-signalled-awsh" || adoptedLive) && !observed.Sys().(syscall.WaitStatus).Signaled() {
		t.Fatal("signalled Awsh was represented as normal exit", observed)
	}
	var cause error
	if scenario == "fault-idle-result" {
		cause = n.c.idleShellExit(ctx, owner)
	} else if scenario == "fault-signalled-awsh" {
		cause = n.c.idleShellExit(ctx, owner)
	} else if adoptedLive {
		cause = fmt.Errorf("terminal Awsh reap: %w", n.c.w.waitErr)
	} else {
		cause = n.c.shutdownIdle("requested-terminal-end", owner)
	}
	if cause == nil {
		t.Fatal("invalid terminal closure became successful")
	}
	if scenario == "fault-idle-result" {
		if !strings.Contains(cause.Error(), "invalid idle shell_exit") || n.c.w.channels.Err() == nil {
			t.Fatal("unsolicited closed left idle session usable", cause)
		}
	} else if scenario == "fault-duplicate" {
		if !strings.Contains(cause.Error(), "private EOF required") {
			t.Fatal("duplicate terminal was not rejected before reap", cause)
		}
	} else if !errors.Is(cause, n.c.w.waitErr) || !strings.Contains(cause.Error(), "terminal Awsh reap") {
		t.Fatal("nonzero/signalled Awsh reap cause was lost", cause)
	}
	// Exercise the product's original five-second final-drain epoch. The
	// fixture context remains longer so only the disposal bound can decide it.
	disposeCtx, disposeEnd := context.WithTimeout(ctx, 5*time.Second)
	defer disposeEnd()
	_ = n.c.disposeFailure(disposeCtx, owner, cause)
	if !n.c.failureDisposed {
		t.Fatal("actual failed terminal actors not disposed", n.c.failure)
	}
	if err := owner.proveEnded(disposeCtx); err != nil {
		t.Fatal(err)
	}
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	for {
		line, err := readLine(n.reader)
		if err != nil {
			if !errors.Is(err, io.EOF) {
				t.Fatal(err)
			}
			break
		}
		m, err := protocol.DecodeEnvoy(line)
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := m.(*protocol.Closed); ok {
			t.Fatal("failed terminal emitted successful public closed")
		}
	}
}

func nativeTerminalMatrix(t *testing.T, ctx context.Context, n *nativeOrdinary, owner *processCensus, scenario string) {
	t.Helper()
	form := "closed"
	if strings.HasPrefix(scenario, "matrix-shell-exit-") {
		form = "shell-exit"
	}
	variant := strings.TrimPrefix(scenario, "matrix-"+form+"-")
	done := make(chan error, 1)
	awshPin, shellPin := owner.refs[owner.awsh], owner.refs[owner.shell]
	if variant == "missing-reap" {
		h := nativeTerminalHeldWait
		if h == nil {
			t.Fatal("missing actual Wait publication observation")
		}
		if form == "shell-exit" {
			if err := shellPin.signal(ctx, syscall.SIGKILL); err != nil {
				t.Fatal(err)
			}
			select {
			case <-h.observed:
			case <-ctx.Done():
				t.Fatal("actual Awsh Wait did not return", ctx.Err())
			}
		}
		go func() { done <- n.c.shutdownIdle("matrix-end", owner) }()
	} else {
		if err := awshPin.signal(ctx, syscall.SIGSTOP); err != nil {
			t.Fatal(err)
		}
		defer awshPin.signal(ctx, syscall.SIGCONT)
		for {
			stat, err := os.ReadFile("/proc/" + strconv.Itoa(owner.awsh) + "/stat")
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(stat), ") T") || strings.Contains(string(stat), ") t") {
				break
			}
			select {
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			case <-time.After(time.Millisecond):
			}
		}
		var terminal protocol.PrivateMessage = protocol.PrivateClosed{Reason: "shutdown", Status: 23, CWD: n.c.w.CWD}
		if form == "shell-exit" {
			terminal = protocol.PrivateShellExit{Status: 23, CWD: n.c.w.CWD}
		}
		frame, err := protocol.EncodePrivate(terminal, protocol.AwshToEnvoy)
		if err != nil {
			t.Fatal(err)
		}
		if variant == "premature-eof" {
			frame = frame[:len(frame)-1]
		} else if variant == "trailing-frame" {
			frame = append(frame, frame...)
		}
		writer, err := os.OpenFile("/proc/"+strconv.Itoa(owner.awsh)+"/fd/4", os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, b := range frame {
			if _, err := writer.Write([]byte{b}); err != nil {
				t.Fatal(err)
			}
		}
		writer.Close()
		if variant == "nonzero-reap" {
			if _, err := n.c.w.Control.Write([]byte("invalid-v1\x00bad\x00")); err != nil {
				t.Fatal(err)
			}
		}
		go func() { done <- n.c.shutdownIdle("matrix-end", owner) }()
		if variant != "premature-eof" {
			for {
				if _, err := n.c.w.Control.Stat(); errors.Is(err, os.ErrClosed) {
					break
				}
				select {
				case <-ctx.Done():
					t.Fatal("terminal frame was not accepted", ctx.Err())
				case <-time.After(time.Millisecond):
				}
			}
		}
		switch variant {
		case "missing-eof": // Keep the actual stopped Awsh and result writer.
		case "nonzero-reap":
			if err := awshPin.signal(ctx, syscall.SIGCONT); err != nil {
				t.Fatal(err)
			}
		default:
			if variant == "reset" {
				// A private descriptor pipe has no TCP reset. Revoke its actual
				// read owner after acceptance to inject a non-EOF I/O failure.
				if err := n.c.w.Result.Close(); err != nil {
					t.Fatal(err)
				}
			}
			if err := awshPin.signal(ctx, syscall.SIGKILL); err != nil {
				t.Fatal(err)
			}
		}
	}
	var cause error
	select {
	case cause = <-done:
	case <-ctx.Done():
		t.Fatal("original final-drain did not finish", ctx.Err())
	}
	if variant == "missing-reap" {
		h := nativeTerminalHeldWait
		select {
		case <-h.observed:
			if n.c.w.waitErr != nil {
				t.Fatal("missing-reap case did not use a real zero-status Awsh Wait", n.c.w.waitErr)
			}
		default:
			t.Fatal("missing-reap test did not observe actual kernel Wait")
		}
		h.release()
	}
	if cause == nil {
		t.Fatal("invalid terminal matrix became orderly")
	}
	switch variant {
	case "missing-eof", "missing-reap":
		if !errors.Is(cause, context.DeadlineExceeded) {
			t.Fatal("withheld terminal proof did not retain original expiry", cause)
		}
	case "premature-eof":
		if !errors.Is(cause, io.ErrUnexpectedEOF) {
			t.Fatal("partial terminal accepted as between-frame EOF", cause)
		}
	case "trailing-frame", "reset":
		if !strings.Contains(cause.Error(), "private EOF required") {
			t.Fatal("terminal closure error did not reject trailing/interrupted I/O", cause)
		}
	case "signal", "nonzero-reap":
		var actual *exec.ExitError
		if !errors.As(n.c.w.waitErr, &actual) || !errors.Is(cause, n.c.w.waitErr) {
			t.Fatal("actual failed Awsh reap was not retained", cause)
		}
		if variant == "signal" && !actual.Sys().(syscall.WaitStatus).Signaled() {
			t.Fatal("expected actual signalled Awsh", actual)
		}
		if variant == "nonzero-reap" && actual.ExitCode() != 2 {
			t.Fatal("expected actual normal nonzero Awsh", actual)
		}
	}
	// This cleanup is test resource accounting after a proved fatal outcome.
	// It cannot change that outcome, reset the product's expired drain, or turn
	// a withheld publication into accepted timely evidence.
	_ = n.c.disposeFailure(ctx, owner, cause)
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("post-failure harness did not account for actual actors", err)
	}
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	for {
		line, err := readLine(n.reader)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		m, err := protocol.DecodeEnvoy(line)
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := m.(*protocol.Closed); ok {
			t.Fatal("fatal terminal matrix emitted public closed")
		}
	}
}
