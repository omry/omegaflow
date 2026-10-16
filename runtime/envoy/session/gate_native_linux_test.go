//go:build linux

package session

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// These tests assemble B4.3's gate primitives on the existing native owners.
// B4.4 owns the integrated public/lifecycle dispatch; no test peer substitutes
// for the real fixed helper, supervisor, selected Bash or terminal relay here.
func nativeGateFact(t *testing.T, n *nativeOrdinary, ctx context.Context, kind, id string) protocol.PrivateMessage {
	t.Helper()
	var result privateResult
	select {
	case result = <-n.c.private.fatal:
	case result = <-n.c.private.events:
	case <-ctx.Done():
		t.Fatal("gate fact timed out", ctx.Err())
	}
	if result.err != nil {
		t.Fatal("native gate transport", result.err)
	}
	var op, gate, actual string
	switch m := result.message.(type) {
	case *protocol.PrivateGateReady:
		actual, op, gate = "ready", m.OperationID, m.GateID
	case *protocol.PrivateGateContinued:
		actual, op, gate = "continued", m.OperationID, m.GateID
	case *protocol.PrivateGateInterrupted:
		actual, op, gate = "interrupted", m.OperationID, m.GateID
	default:
		t.Fatal("unexpected native gate fact", result.message)
	}
	if actual != kind || op != "gate-operation" || gate != id {
		t.Fatal("wrong gate identity/outcome", result.message)
	}
	return result.message
}

func nativeGateEvent(t *testing.T, n *nativeOrdinary, ctx context.Context, fact protocol.PrivateMessage) string {
	t.Helper()
	if err := n.c.r.PublishGate(ctx, fact); err != nil {
		t.Fatal(err)
	}
	for {
		m := n.frame(t)
		var through int64
		switch event := m.(type) {
		case *protocol.OutputMark:
			continue
		case *protocol.OperationReady:
			through = event.OutputThrough
		case *protocol.OperationContinued:
			through = event.OutputThrough
		case *protocol.OperationGateInterrupted:
			through = event.OutputThrough
		default:
			t.Fatal("unexpected public gate event", m)
		}
		if n.markOffset < through {
			t.Fatal("native gate lacks covering mark")
		}
		return n.bytesThrough(t, through)
	}
}

func nativeGateContinue(t *testing.T, n *nativeOrdinary, ctx context.Context, id, input string) {
	t.Helper()
	request := &protocol.Continue{Header: protocol.Header{Schema: protocol.Schema, Type: "continue", Seq: n.seq}, OperationID: "gate-operation", GateID: id, InputThrough: n.inputThrough + int64(len(input))}
	// Admit the complete public request while the requested terminal bytes have
	// not arrived. No private continue may be sent before the watermark wait.
	n.request(t, request)
	e := receiveInput(t, n.c.w.channels)
	parsed, ok := e.Message.(*protocol.Continue)
	if !ok || parsed.InputThrough != request.InputThrough {
		t.Fatal("missing public continue", e)
	}
	done := make(chan error, 1)
	go func() { done <- n.c.w.channels.WaitGateInput(ctx, parsed.OperationID, parsed.InputThrough) }()
	if input != "" {
		select {
		case err := <-done:
			t.Fatal("gate watermark released before terminal input", err)
		default:
		}
		if _, err := n.terminal.Write([]byte(input)); err != nil {
			t.Fatal(err)
		}
		n.inputThrough += int64(len(input))
		forwardGateTestInput(t, ctx, n.c.r, parsed.OperationID, input)
	}
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if err := SendPrivate(ctx, n.c.w.Control, protocol.PrivateContinue{OperationID: parsed.OperationID, GateID: parsed.GateID}); err != nil {
		t.Fatal(err)
	}
}

func nativeGateComplete(t *testing.T, n *nativeOrdinary, ctx context.Context) string {
	t.Helper()
	if err := n.c.Complete(ctx, n.cleanup(t, "gate-operation")); err != nil {
		t.Fatal("native gate return", err)
	}
	for {
		switch m := n.frame(t).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationCompleted:
			if m.OperationID != "gate-operation" || n.markOffset < m.OutputThrough {
				t.Fatal("wrong gate completion", m)
			}
			return n.bytesThrough(t, m.OutputThrough)
		default:
			t.Fatal("unexpected post-gate public result", m)
		}
	}
}

func TestNativeGateContinueAndNestedLaunchPath(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_GATE") != "1" {
		t.Skip("explicit genuinely qualified native payload required")
	}
	n := nativeOrdinaryFor(t)
	ctx, cancel := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
	defer cancel()
	n.begin(t, ctx, "gate-operation", "PATH=/hostile; builtin printf 'BEFORE_GATE\\n'; awsh gate first; read -r answer; builtin printf 'INPUT=%s\\n' \"$answer\"; (awsh gate nested); builtin printf 'AFTER_GATES\\n'")
	var output strings.Builder
	for _, id := range []string{"first", "nested"} {
		output.WriteString(nativeGateEvent(t, n, ctx, nativeGateFact(t, n, ctx, "ready", id)))
		input := ""
		if id == "first" {
			input = "retained-input\n"
		}
		nativeGateContinue(t, n, ctx, id, input)
		output.WriteString(nativeGateEvent(t, n, ctx, nativeGateFact(t, n, ctx, "continued", id)))
	}
	output.WriteString(nativeGateComplete(t, n, ctx))
	for _, marker := range []string{"BEFORE_GATE", "INPUT=retained-input", "AFTER_GATES"} {
		if !strings.Contains(output.String(), marker) {
			t.Fatal("native gate lost workload output", marker, output.String())
		}
	}
	if strings.Contains(output.String(), "__OMEGAFLOW_AWSH_") {
		t.Fatal("canonical frame redisplayed")
	}
	// Distinct operations can reuse their own gate namespace after completion.
	n.execute(t, "after-gate", "builtin printf 'NEXT_OPERATION\\n'", "NEXT_OPERATION\n", "/", 0)
	if err := n.c.ShutdownIdle("recording-end"); err != nil {
		t.Fatal(err)
	}
}

func TestNativeGateTerminalInterruptAndLateContinue(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_GATE") != "1" {
		t.Skip("explicit genuinely qualified native payload required")
	}
	n := nativeOrdinaryFor(t)
	ctx, cancel := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
	defer cancel()
	n.begin(t, ctx, "gate-operation", "awsh gate interrupted; builtin printf 'RETURN_AFTER_GATE\\n'")
	nativeGateEvent(t, n, ctx, nativeGateFact(t, n, ctx, "ready", "interrupted"))
	if _, err := n.terminal.Write([]byte{3}); err != nil {
		t.Fatal(err)
	}
	n.inputThrough++
	e := receiveInput(t, n.c.w.channels)
	if len(e.Input) != 1 || e.Input[0] != 3 {
		t.Fatal("terminal Ctrl-C was not forwarded", e)
	}
	if err := n.c.r.ForwardInput(ctx, e); err != nil {
		t.Fatal(err)
	}
	nativeGateEvent(t, n, ctx, nativeGateFact(t, n, ctx, "interrupted", "interrupted"))
	if err := SendPrivate(ctx, n.c.w.Control, protocol.PrivateContinue{OperationID: "gate-operation", GateID: "interrupted"}); err != nil {
		t.Fatal(err)
	}
	nativeGateComplete(t, n, ctx)
	// Completion arriving instead of a second gate outcome proves the late
	// continue neither released another helper nor displaced input_closed.
	n.execute(t, "after-interrupt", "builtin printf 'SHELL_SURVIVED\\n'", "SHELL_SURVIVED\n", "/", 0)
	if err := n.c.ShutdownIdle("recording-end"); err != nil {
		t.Fatal(fmt.Errorf("shutdown after interrupted gate: %w", err))
	}
}

func TestNativeGateRejectsReusedIdentityAndPrematureContinue(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_GATE") != "1" {
		t.Skip("explicit genuinely qualified native payload required")
	}
	for _, premature := range []bool{false, true} {
		t.Run(fmt.Sprint("premature-", premature), func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			ctx, cancel := context.WithTimeout(n.c.w.channels.ctx, 5*time.Second)
			defer cancel()
			source := "awsh gate used; awsh gate used"
			if premature {
				// Withhold this read's input, so the helper cannot connect
				// before the premature control, regardless of scheduling.
				source = "builtin read -r gate_permit; awsh gate used"
			}
			n.begin(t, ctx, "gate-operation", source)
			if premature {
				if err := SendPrivate(ctx, n.c.w.Control, protocol.PrivateContinue{OperationID: "gate-operation", GateID: "used"}); err != nil {
					t.Fatal(err)
				}
			} else {
				nativeGateEvent(t, n, ctx, nativeGateFact(t, n, ctx, "ready", "used"))
				nativeGateContinue(t, n, ctx, "used", "")
				nativeGateEvent(t, n, ctx, nativeGateFact(t, n, ctx, "continued", "used"))
			}
			select {
			case r := <-n.c.private.events:
				if r.err != io.EOF || r.message != nil {
					t.Fatal("invalid gate traffic produced a normal result", r.message, r.err)
				}
			case <-ctx.Done():
				t.Fatal("invalid gate traffic did not close private results")
			}
		})
	}
}
