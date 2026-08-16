//go:build linux

package envoy

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/internal/protocoltest"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type testSession struct {
	t         *testing.T
	terminal  *net.TCPConn
	telemetry *net.TCPConn
	reader    *bufio.Reader
	client    *protocoltest.Client
	raw       []byte
	done      <-chan error
}

func startTestSession(t *testing.T) *testSession {
	t.Helper()
	awshPath, err := filepath.Abs(filepath.Join("..", "..", "..", "..", "docs", "future", "prototype", "awsh", "awsh"))
	if err != nil {
		t.Fatal(err)
	}
	config := DefaultConfig()
	config.TerminalListen = "127.0.0.1:0"
	config.TelemetryListen = "127.0.0.1:0"
	config.AwshPath = awshPath
	config.ConnectTimeout = 2 * time.Second
	config.HandshakeTimeout = 2 * time.Second
	config.WriteTimeout = 2 * time.Second
	config.CancelGrace = 2 * time.Second
	config.DrainTimeout = 2 * time.Second
	addresses := make(chan Addresses, 1)
	config.OnListening = func(value Addresses) { addresses <- value }
	done := make(chan error, 1)
	go func() { done <- Run(context.Background(), config) }()
	var coordinates Addresses
	select {
	case coordinates = <-addresses:
	case err := <-done:
		t.Fatalf("Envoy failed before listening: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("Envoy did not bind listeners")
	}
	terminal := dialTCP(t, coordinates.Terminal)
	telemetry := dialTCP(t, coordinates.Telemetry)
	session := &testSession{
		t: t, terminal: terminal, telemetry: telemetry, reader: bufio.NewReader(telemetry),
		client: protocoltest.NewClient(), done: done,
	}
	session.send(session.client.Hello("test-session"))
	first := session.event()
	if _, ok := first.(protocol.Ready); !ok {
		t.Fatalf("first Envoy event was not ready: %#v", first)
	}
	t.Cleanup(func() {
		_ = terminal.Close()
		_ = telemetry.Close()
	})
	return session
}

func dialTCP(t *testing.T, address string) *net.TCPConn {
	t.Helper()
	connection, err := net.DialTimeout("tcp", address, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	tcp, ok := connection.(*net.TCPConn)
	if !ok {
		t.Fatal("connection is not TCP")
	}
	return tcp
}

func TestAcceptTCPUsesSharedDeadline(t *testing.T) {
	terminalListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer terminalListener.Close()
	telemetryListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer telemetryListener.Close()

	const timeout = 600 * time.Millisecond
	deadline := time.Now().Add(timeout)
	dialDone := make(chan error, 1)
	go func() {
		time.Sleep(timeout / 2)
		connection, dialErr := net.DialTimeout("tcp", terminalListener.Addr().String(), timeout)
		if connection != nil {
			_ = connection.Close()
		}
		dialDone <- dialErr
	}()

	started := time.Now()
	terminal, err := acceptTCP(terminalListener, deadline)
	if err != nil {
		t.Fatal(err)
	}
	_ = terminal.Close()
	if err := <-dialDone; err != nil {
		t.Fatal(err)
	}
	if _, err := acceptTCP(telemetryListener, deadline); err == nil {
		t.Fatal("telemetry accept succeeded without a connection")
	} else if timeoutErr, ok := err.(net.Error); !ok || !timeoutErr.Timeout() {
		t.Fatalf("telemetry accept did not reach the shared deadline: %v", err)
	}
	if elapsed := time.Since(started); elapsed > timeout+300*time.Millisecond {
		t.Fatalf("shared connect deadline took %s, want at most %s", elapsed, timeout+300*time.Millisecond)
	}
}

func TestEnvoyDiagnosticPreservesRejectedEventSequence(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	controller := dialTCP(t, listener.Addr().String())
	defer controller.Close()
	envoyConnection, err := listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	telemetry, ok := envoyConnection.(*net.TCPConn)
	if !ok {
		t.Fatal("accepted connection is not TCP")
	}
	defer telemetry.Close()

	state := protocol.NewSessionState()
	if err := state.AcceptController(protocol.Hello{Seq: 1, SessionID: "sequence-test"}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(protocol.Ready{Seq: 1, EnvoyPID: 41, ShellPID: 42, CWD: "/work", Columns: 80, Rows: 24}); err != nil {
		t.Fatal(err)
	}
	session := &session{state: state, telemetry: telemetry, nextSeq: 2, config: DefaultConfig()}
	if err := session.emit(protocol.OperationCompleted{Seq: session.takeSeq(), OperationID: "op", CWD: "/work"}); err == nil {
		t.Fatal("out-of-state event was accepted")
	}
	if session.nextSeq != 2 {
		t.Fatalf("rejected event consumed sequence: next is %d", session.nextSeq)
	}

	session.bestEffortDiagnostic(fail(FailureProtocol, "envoy-state", errors.New("rejected event")))
	if err := controller.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	frame, err := bufio.NewReader(controller).ReadBytes('\n')
	if err != nil {
		t.Fatal(err)
	}
	message, err := protocol.DecodeEnvoy(frame)
	if err != nil {
		t.Fatal(err)
	}
	diagnostic, ok := message.(protocol.Diagnostic)
	if !ok || diagnostic.Seq != 2 || diagnostic.Code != "envoy-state" {
		t.Fatalf("unexpected diagnostic after rejected event: %#v", message)
	}
}

func (session *testSession) send(frame []byte, err error) {
	session.t.Helper()
	if err != nil {
		session.t.Fatal(err)
	}
	if err := writeAllWithDeadline(session.telemetry, frame, 2*time.Second); err != nil {
		session.t.Fatal(err)
	}
}

func (session *testSession) event() any {
	session.t.Helper()
	if err := session.telemetry.SetReadDeadline(time.Now().Add(4 * time.Second)); err != nil {
		session.t.Fatal(err)
	}
	frame, err := session.reader.ReadBytes('\n')
	if err != nil {
		session.t.Fatalf("read telemetry: %v", err)
	}
	messages, err := session.client.Accept(frame)
	if err != nil {
		session.t.Fatalf("accept telemetry %q: %v", frame, err)
	}
	if len(messages) != 1 {
		session.t.Fatalf("want one telemetry message, got %d", len(messages))
	}
	return messages[0]
}

func (session *testSession) execute(operationID, source string) (protocol.OperationStarted, protocol.OperationCompleted) {
	session.t.Helper()
	session.send(session.client.Execute(operationID, source))
	started, ok := session.event().(protocol.OperationStarted)
	if !ok {
		session.t.Fatal("execute did not emit operation_started")
	}
	completed, ok := session.event().(protocol.OperationCompleted)
	if !ok {
		session.t.Fatal("execute did not emit operation_completed")
	}
	return started, completed
}

func (session *testSession) outputThrough(offset uint64) string {
	session.t.Helper()
	for uint64(len(session.raw)) < offset {
		if err := session.terminal.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			session.t.Fatal(err)
		}
		buffer := make([]byte, int(offset)-len(session.raw))
		count, err := session.terminal.Read(buffer)
		if count > 0 {
			session.raw = append(session.raw, buffer[:count]...)
		}
		if err != nil {
			session.t.Fatalf("read terminal through %d: %v", offset, err)
		}
	}
	return string(session.raw)
}

func (session *testSession) outputUntil(fragment string) string {
	session.t.Helper()
	for !strings.Contains(string(session.raw), fragment) {
		if err := session.terminal.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			session.t.Fatal(err)
		}
		buffer := make([]byte, 4096)
		count, err := session.terminal.Read(buffer)
		if count > 0 {
			session.raw = append(session.raw, buffer[:count]...)
		}
		if err != nil {
			session.t.Fatalf("read terminal until %q: %v", fragment, err)
		}
	}
	return string(session.raw)
}

func (session *testSession) shutdown() {
	session.t.Helper()
	session.send(session.client.Shutdown("test-complete"))
	if _, ok := session.event().(protocol.Draining); !ok {
		session.t.Fatal("shutdown did not emit draining")
	}
	closed, ok := session.event().(protocol.Closed)
	if !ok {
		session.t.Fatal("shutdown did not emit closed")
	}
	session.outputThrough(closed.OutputThrough)
	if err := session.client.Finish(); err != nil {
		session.t.Fatal(err)
	}
	if err := session.terminal.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		session.t.Fatal(err)
	}
	buffer := make([]byte, 1)
	if count, err := session.terminal.Read(buffer); count != 0 || !errors.Is(err, io.EOF) {
		session.t.Fatalf("want terminal EOF, got count=%d err=%v", count, err)
	}
	select {
	case err := <-session.done:
		if err != nil {
			session.t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		session.t.Fatal("Envoy did not exit after shutdown")
	}
}

func TestEnvoyStreamsOutputAndPreservesPersistentBashState(t *testing.T) {
	session := startTestSession(t)
	_, first := session.execute("state-1", "cd /tmp; export OMEGAFLOW_TEST_VALUE=persistent; printf 'first'")
	if first.Status != 0 || first.CWD != "/tmp" {
		t.Fatalf("unexpected first result: %#v", first)
	}
	_, second := session.execute("state-2", "printf '|%s|' \"$OMEGAFLOW_TEST_VALUE\"")
	output := session.outputThrough(second.OutputThrough)
	if !strings.Contains(output, "first|persistent|") {
		t.Fatalf("persistent output missing from %q", output)
	}
	if second.OutputStart != first.OutputThrough {
		t.Fatalf("output ranges are not contiguous: first=%d second=%d", first.OutputThrough, second.OutputStart)
	}
	session.shutdown()
}

func TestEnvoyCompletionBarrierCoversAllEarlierOutput(t *testing.T) {
	session := startTestSession(t)
	started, completed := session.execute("bulk-output", "head -c 131072 /dev/zero | tr '\\0' x")
	if completed.OutputThrough-started.OutputStart != 131072 {
		t.Fatalf("unexpected output range: start=%d through=%d", started.OutputStart, completed.OutputThrough)
	}
	output := session.outputThrough(completed.OutputThrough)
	if len(output) != 131072 || strings.Trim(output, "x") != "" {
		t.Fatalf("terminal output was not retained exactly: length=%d", len(output))
	}
	session.shutdown()
}

func TestOutputBarrierWaitsForInFlightBytesAfterPTYClose(t *testing.T) {
	if outputDrained(0, syscall.EIO, true) {
		t.Fatal("PTY close must not overtake bytes already being written to the terminal channel")
	}
	if !outputDrained(0, syscall.EIO, false) {
		t.Fatal("closed PTY with no in-flight bytes should be drained")
	}
}

func TestEnvoySupportsInteractiveInputGatesAndResize(t *testing.T) {
	session := startTestSession(t)

	session.send(session.client.Execute("interactive", "IFS= read -r value; printf 'read:%s' \"$value\""))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("interactive operation did not start")
	}
	if _, err := session.terminal.Write([]byte("hello\n")); err != nil {
		t.Fatal(err)
	}
	interactive, ok := session.event().(protocol.OperationCompleted)
	if !ok {
		t.Fatal("interactive operation did not complete")
	}
	if output := session.outputThrough(interactive.OutputThrough); !strings.Contains(output, "read:hello") {
		t.Fatalf("interactive output missing from %q", output)
	}

	session.send(session.client.Execute("gate", "printf before; awsh_gate gate-1 || return $?; printf after"))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("gated operation did not start")
	}
	ready, ok := session.event().(protocol.OperationReady)
	if !ok || ready.GateID != "gate-1" {
		t.Fatalf("unexpected gate event: %#v", ready)
	}
	session.send(session.client.Continue("gate", "gate-1"))
	if _, ok := session.event().(protocol.OperationContinued); !ok {
		t.Fatal("gate did not continue")
	}
	gated, ok := session.event().(protocol.OperationCompleted)
	if !ok {
		t.Fatal("gated operation did not complete")
	}
	if output := session.outputThrough(gated.OutputThrough); !strings.Contains(output, "beforeafter") {
		t.Fatalf("gated output missing from %q", output)
	}

	session.send(session.client.Resize(101, 41))
	resize, ok := session.event().(protocol.ResizeApplied)
	if !ok || resize.Columns != 101 || resize.Rows != 41 {
		t.Fatalf("unexpected resize event: %#v", resize)
	}
	_, resized := session.execute("size", "stty size")
	if output := session.outputThrough(resized.OutputThrough); !strings.Contains(output, "41 101") {
		t.Fatalf("resized dimensions missing from %q", output)
	}
	session.shutdown()
}

func TestEnvoyCancelsForegroundProcessAndKeepsShell(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Execute("slow", "sleep 30; printf should-not-run"))
	started, ok := session.event().(protocol.OperationStarted)
	if !ok {
		t.Fatal("slow operation did not start")
	}
	session.send(session.client.Cancel("slow", "test-cancel"))
	cancelEvent := session.event()
	cancelled, ok := cancelEvent.(protocol.OperationCancelled)
	if !ok {
		t.Fatalf("cancel did not emit operation_cancelled: %#v", cancelEvent)
	}
	if cancelled.Status != 130 || cancelled.Reason != "test-cancel" || cancelled.OutputStart != started.OutputStart {
		t.Fatalf("unexpected cancellation: %#v", cancelled)
	}
	_, after := session.execute("after-cancel", "printf alive")
	if output := session.outputThrough(after.OutputThrough); !strings.Contains(output, "alive") || strings.Contains(output, "should-not-run") {
		t.Fatalf("unexpected post-cancel output: %q", output)
	}
	session.shutdown()
}

func TestEnvoyCancelsOperationWaitingAtGate(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Execute("gated-cancel", "printf before; awsh_gate stop || return $?; printf after"))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("gated cancellation operation did not start")
	}
	if _, ok := session.event().(protocol.OperationReady); !ok {
		t.Fatal("gated cancellation operation did not become ready")
	}
	session.send(session.client.Cancel("gated-cancel", "cancel-at-gate"))
	cancelled, ok := session.event().(protocol.OperationCancelled)
	if !ok || cancelled.Status != 130 || cancelled.Reason != "cancel-at-gate" {
		t.Fatalf("unexpected gated cancellation: %#v", cancelled)
	}
	output := session.outputThrough(cancelled.OutputThrough)
	if !strings.Contains(output, "before") || strings.Contains(output, "after") {
		t.Fatalf("unexpected gated cancellation output: %q", output)
	}
	_, after := session.execute("after-gate-cancel", "printf alive-after-gate")
	if output := session.outputThrough(after.OutputThrough); !strings.Contains(output, "alive-after-gate") {
		t.Fatalf("shell did not survive gated cancellation: %q", output)
	}
	session.shutdown()
}

func TestEnvoyClosesCancelRaceWhenOperationEntersGate(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Execute("gate-race", "awsh_gate immediate || return $?; sleep 30; printf after"))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("gate-race operation did not start")
	}
	// Cancel without waiting for operation_ready. The shell may already be in
	// the gate even though its event has not crossed the telemetry channel.
	session.send(session.client.Cancel("gate-race", "race-cancel"))
	cancelled, ok := session.event().(protocol.OperationCancelled)
	if !ok || cancelled.Status != 130 || cancelled.Reason != "race-cancel" {
		t.Fatalf("unexpected gate-race cancellation: %#v", cancelled)
	}
	if output := session.outputThrough(cancelled.OutputThrough); strings.Contains(output, "after") {
		t.Fatalf("gate-race operation continued: %q", output)
	}
	session.shutdown()
}

func TestEnvoyClosesCancelRaceWhileGateContinues(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Execute("continue-race", "awsh_gate pause || return $?; sleep 30; printf after"))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("continue-race operation did not start")
	}
	if _, ok := session.event().(protocol.OperationReady); !ok {
		t.Fatal("continue-race operation did not reach its gate")
	}
	session.send(session.client.Continue("continue-race", "pause"))
	session.send(session.client.Cancel("continue-race", "continue-cancel"))
	cancelEvent := session.event()
	cancelled, ok := cancelEvent.(protocol.OperationCancelled)
	if !ok || cancelled.Status != 130 || cancelled.Reason != "continue-cancel" {
		t.Fatalf("unexpected continue-race cancellation: %#v", cancelEvent)
	}
	if output := session.outputThrough(cancelled.OutputThrough); strings.Contains(output, "after") {
		t.Fatalf("continue-race operation continued: %q", output)
	}
	session.shutdown()
}

func TestEnvoyCancellationTimeoutEmitsOperationFailure(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Execute("ignore-int", "trap '' INT; printf armed; sleep 30"))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("ignore-int operation did not start")
	}
	session.outputUntil("armed")
	session.send(session.client.Cancel("ignore-int", "must-stop"))
	failed, ok := session.event().(protocol.OperationFailed)
	if !ok || failed.Code != "cancellation-timeout" || failed.OperationID != "ignore-int" {
		t.Fatalf("unexpected cancellation failure: %#v", failed)
	}
	diagnostic, ok := session.event().(protocol.Diagnostic)
	if !ok || diagnostic.Code != "grace-expired" {
		t.Fatalf("unexpected cancellation diagnostic: %#v", diagnostic)
	}
	select {
	case err := <-session.done:
		failure, ok := err.(*Failure)
		if !ok || failure.Class != FailureCancel || failure.Code != "grace-expired" {
			t.Fatalf("unexpected cancellation timeout result: %v", err)
		}
	case <-time.After(4 * time.Second):
		t.Fatal("Envoy did not finish cancellation escalation")
	}
}

func TestEnvoyFailsWhenTerminalClosesDuringShutdown(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Shutdown("terminal-disconnected"))
	if err := session.terminal.Close(); err != nil {
		t.Fatal(err)
	}

	select {
	case err := <-session.done:
		failure, ok := err.(*Failure)
		if !ok || (failure.Class != FailureConnection && failure.Class != FailureDrain) {
			t.Fatalf("unexpected terminal-close result: %v", err)
		}
	case <-time.After(4 * time.Second):
		t.Fatal("Envoy did not fail after terminal closure during shutdown")
	}
}

func TestEnvoyCleansDetachedBackgroundProcessGroupMember(t *testing.T) {
	session := startTestSession(t)
	_, completed := session.execute(
		"background",
		"nohup sleep 30 >/dev/null 2>&1 & printf 'background-pid=%s broker-pid=%s\\n' \"$!\" \"$awsh_control_pid\"",
	)
	output := session.outputThrough(completed.OutputThrough)
	match := regexp.MustCompile(`background-pid=([0-9]+) broker-pid=([0-9]+)`).FindStringSubmatch(output)
	if len(match) != 3 {
		t.Fatalf("background and broker pids missing from %q", output)
	}
	pid, err := strconv.Atoi(match[1])
	if err != nil {
		t.Fatal(err)
	}
	brokerPID, err := strconv.Atoi(match[2])
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = syscall.Kill(pid, syscall.SIGKILL)
		_ = syscall.Kill(brokerPID, syscall.SIGKILL)
	})

	session.shutdown()
	if err := syscall.Kill(pid, 0); !errors.Is(err, syscall.ESRCH) {
		t.Fatalf("background process %d survived structured shutdown: %v", pid, err)
	}
	if err := syscall.Kill(brokerPID, 0); !errors.Is(err, syscall.ESRCH) {
		t.Fatalf("control broker %d was not reaped during structured shutdown: %v", brokerPID, err)
	}
}

func TestEnvoyDoesNotExposeControlDescriptorsOrSocketsToChildren(t *testing.T) {
	session := startTestSession(t)
	source := `if [ -e /proc/self/fd/20 ] || [ -e /proc/self/fd/21 ]; then printf private-fd-leaked; fi
for target in /proc/self/fd/*; do readlink "$target" 2>/dev/null || :; done`
	_, completed := session.execute("fds", source)
	output := session.outputThrough(completed.OutputThrough)
	if strings.Contains(output, "private-fd-leaked") || strings.Contains(output, "socket:[") {
		t.Fatalf("private Envoy descriptor leaked: %q", output)
	}
	session.shutdown()
}

func TestEnvoyClassifiesShellExitAndRetainsDiagnostic(t *testing.T) {
	session := startTestSession(t)
	session.send(session.client.Execute("exit-shell", "exit 7"))
	if _, ok := session.event().(protocol.OperationStarted); !ok {
		t.Fatal("shell-exit operation did not start")
	}
	diagnostic, ok := session.event().(protocol.Diagnostic)
	if !ok || diagnostic.Severity != "fatal" || (diagnostic.Code != "shell-result-closed" && diagnostic.Code != "shell-exited") {
		t.Fatalf("unexpected shell-exit diagnostic: %#v", diagnostic)
	}
	select {
	case err := <-session.done:
		failure, ok := err.(*Failure)
		if !ok || failure.Class != FailureShell || failure.Code != diagnostic.Code {
			t.Fatalf("unexpected shell-exit failure: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Envoy did not report shell exit")
	}
}

func TestEnvoyFailsClosedOnMalformedTelemetry(t *testing.T) {
	awshPath, err := filepath.Abs(filepath.Join("..", "..", "..", "..", "docs", "future", "prototype", "awsh", "awsh"))
	if err != nil {
		t.Fatal(err)
	}
	config := DefaultConfig()
	config.TerminalListen = "127.0.0.1:0"
	config.TelemetryListen = "127.0.0.1:0"
	config.AwshPath = awshPath
	config.ConnectTimeout = time.Second
	config.HandshakeTimeout = time.Second
	addresses := make(chan Addresses, 1)
	config.OnListening = func(value Addresses) { addresses <- value }
	done := make(chan error, 1)
	go func() { done <- Run(context.Background(), config) }()
	var coordinates Addresses
	select {
	case coordinates = <-addresses:
	case err := <-done:
		t.Fatalf("Envoy failed before listening: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("Envoy did not bind listeners")
	}
	terminal := dialTCP(t, coordinates.Terminal)
	defer terminal.Close()
	telemetry := dialTCP(t, coordinates.Telemetry)
	defer telemetry.Close()
	if _, err := fmt.Fprint(telemetry, "not-json\n"); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		failure, ok := err.(*Failure)
		if !ok || failure.Class != FailureHandshake || failure.Code != "hello-read" {
			t.Fatalf("unexpected failure: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Envoy did not reject malformed telemetry")
	}
}

func TestEnvoyFailsClosedOnTerminalTrafficBeforeReady(t *testing.T) {
	awshPath, err := filepath.Abs(filepath.Join("..", "..", "..", "..", "docs", "future", "prototype", "awsh", "awsh"))
	if err != nil {
		t.Fatal(err)
	}
	wrapper := filepath.Join(t.TempDir(), "delayed-awsh")
	wrapperSource := fmt.Sprintf("#!/bin/sh\nsleep 0.2\nexec %q \"$@\"\n", awshPath)
	if err := os.WriteFile(wrapper, []byte(wrapperSource), 0o700); err != nil {
		t.Fatal(err)
	}
	config := DefaultConfig()
	config.TerminalListen = "127.0.0.1:0"
	config.TelemetryListen = "127.0.0.1:0"
	config.AwshPath = wrapper
	config.ConnectTimeout = time.Second
	config.HandshakeTimeout = time.Second
	addresses := make(chan Addresses, 1)
	config.OnListening = func(value Addresses) { addresses <- value }
	done := make(chan error, 1)
	go func() { done <- Run(context.Background(), config) }()
	coordinates := <-addresses
	terminal := dialTCP(t, coordinates.Terminal)
	defer terminal.Close()
	telemetry := dialTCP(t, coordinates.Telemetry)
	defer telemetry.Close()
	client := protocoltest.NewClient()
	hello, err := client.Hello("early-terminal")
	if err != nil {
		t.Fatal(err)
	}
	if err := writeAllWithDeadline(telemetry, hello, time.Second); err != nil {
		t.Fatal(err)
	}
	if _, err := terminal.Write([]byte("too-early")); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		failure, ok := err.(*Failure)
		if !ok || failure.Class != FailureHandshake || failure.Code != "traffic-before-ready" {
			t.Fatalf("unexpected early-terminal failure: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Envoy accepted terminal traffic before ready")
	}
}

func TestEnvoyUsesFixedBashOutsideDelegatedPATH(t *testing.T) {
	directory := t.TempDir()
	fakeBash := filepath.Join(directory, "bash")
	if err := os.WriteFile(fakeBash, []byte("#!/bin/sh\nprintf path-bash-was-used\nexit 99\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", directory)
	session := startTestSession(t)
	_, completed := session.execute("fixed-bash", "printf fixed-bash")
	output := session.outputThrough(completed.OutputThrough)
	if !strings.Contains(output, "fixed-bash") || strings.Contains(output, "path-bash-was-used") {
		t.Fatalf("unexpected Bash selection output: %q", output)
	}
	session.shutdown()
}

func TestControlledEnvironmentRemovesBashControlVariables(t *testing.T) {
	environment := controlledEnvironment([]string{
		"PATH=/bin", "AWSH_BASH=/tmp/bash", "BASH_ENV=/tmp/hook", "BASH_FUNC_bad%%=() { :; }", "APP=value",
	})
	joined := strings.Join(environment, "\n")
	if joined != "PATH=/bin\nAPP=value" {
		t.Fatalf("unexpected controlled environment: %q", joined)
	}
}

func TestExitCodeClassMapping(t *testing.T) {
	want := map[FailureClass]int{
		FailureConnection: 10,
		FailureHandshake:  11,
		FailureProtocol:   12,
		FailurePTY:        13,
		FailureAwsh:       14,
		FailureShell:      15,
		FailureResize:     16,
		FailureCancel:     17,
		FailureDrain:      18,
		FailureCleanup:    19,
	}
	for class, expected := range want {
		if actual := ExitCode(fail(class, "test", errors.New("test"))); actual != expected {
			t.Errorf("ExitCode(%q) = %d, want %d", class, actual, expected)
		}
	}
	if actual := ExitCode(errors.New("untyped")); actual != 1 {
		t.Fatalf("ExitCode(untyped) = %d, want 1", actual)
	}
}
