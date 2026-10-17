//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"runtime"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func dispatchRequest(t *testing.T, f *startFixture, m protocol.Message) {
	t.Helper()
	var h *protocol.Header
	switch v := m.(type) {
	case *protocol.Cancel:
		h = &v.Header
		h.Type = "cancel"
	case *protocol.Finalize:
		h = &v.Header
		h.Type = "finalize"
	case *protocol.Continue:
		h = &v.Header
		h.Type = "continue"
	case *protocol.Resize:
		h = &v.Header
		h.Type = "resize"
	default:
		t.Fatal("unsupported dispatch request")
	}
	h.Schema, h.Seq = protocol.Schema, f.seq
	f.seq++
	b, err := protocol.EncodeController(m)
	if err != nil {
		t.Fatal(err)
	}
	f.telemetry.SetWriteDeadline(time.Now().Add(time.Second))
	if err = helper.WriteAll(f.telemetry, b); err != nil {
		t.Fatal(err)
	}
}
func nextDispatch(t *testing.T, f *startFixture) protocol.Message {
	t.Helper()
	for {
		m := nextRelayFrame(t, f.relayFixture)
		if _, mark := m.(*protocol.OutputMark); !mark {
			return m
		}
	}
}
func controlledFixture(t *testing.T, f *startFixture, ctx context.Context, cleanup ReturnCleanup, peers *controlPeers) <-chan error {
	t.Helper()
	done := make(chan error, 1)
	go func() { done <- f.c.completeControlled(ctx, cleanup, peers) }()
	return done
}
func heldCensus(entered chan struct{}) censusPeer {
	return censusPeer{prove: func(ctx context.Context, _ int64) error { close(entered); <-ctx.Done(); return ctx.Err() }, members: func(context.Context, int64) ([]foregroundMember, error) {
		return nil, errors.New("blocked proof must never sample")
	}}
}
func awaitProof(t *testing.T, entered <-chan struct{}) {
	t.Helper()
	select {
	case <-entered:
	case <-relayTestContext(t).Done():
		t.Fatal("lifecycle was not accepted while I/O was pending")
	}
}
func waitWatermark(t *testing.T, f *startFixture, want int64) {
	t.Helper()
	ctx := relayTestContext(t)
	g := f.c.w.channels.inputState()
	for {
		g.mu.Lock()
		seen := g.watermark == want
		g.mu.Unlock()
		if seen {
			return
		}
		select {
		case <-ctx.Done():
			t.Fatal("gate input wait not entered")
		default:
			runtime.Gosched()
		}
	}
}
func cleanReturn(context.Context, string, int64) error { return nil }

func TestDispatchGateContinueAndInputForwarding(t *testing.T) {
	f := completionFixture(t)
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, nil)
	f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "first"})
	if m, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok || m.GateID != "first" {
		t.Fatal("missing committed readiness", m)
	}
	dispatchRequest(t, f, &protocol.Continue{OperationID: "operation-1", GateID: "first", InputThrough: 4})
	waitWatermark(t, f, 4)
	f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	b := make([]byte, 1)
	if n, err := f.control.Read(b); n != 0 || err == nil {
		t.Fatal("gate released before watermark", n, err)
	}
	f.control.SetReadDeadline(time.Time{})
	f.terminal.Write([]byte("data"))
	f.slave.SetReadDeadline(time.Now().Add(time.Second))
	data := make([]byte, 4)
	if _, err := io.ReadFull(f.slave, data); err != nil || string(data) != "data" {
		t.Fatal("gated input lost", string(data), err)
	}
	if m, ok := f.readPrivate(t).(*protocol.PrivateContinue); !ok || m.GateID != "first" {
		t.Fatal("missing exact private continue", m)
	}
	f.private(t, protocol.PrivateGateContinued{OperationID: "operation-1", GateID: "first"})
	if _, ok := nextDispatch(t, f).(*protocol.OperationContinued); !ok {
		t.Fatal("missing continued event")
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 7, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if m := completedEvent(t, f); m.Status != 7 {
		t.Fatal(m)
	}
}

func TestDispatchCancelSuppressesGateAndUnsentContinue(t *testing.T) {
	for _, where := range []string{"before-ready", "during-input-barrier", "after-continue"} {
		t.Run(where, func(t *testing.T) {
			f := completionFixture(t)
			entered := make(chan struct{})
			p := &controlPeers{sessionID: 123, census: heldCensus(entered)}
			done := controlledFixture(t, f, relayTestContext(t), cleanReturn, p)
			if where != "before-ready" {
				f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "gate"})
				if _, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok {
					t.Fatal("missing ready")
				}
				through := int64(10)
				if where == "after-continue" {
					through = 0
				}
				dispatchRequest(t, f, &protocol.Continue{OperationID: "operation-1", GateID: "gate", InputThrough: through})
				if where == "after-continue" {
					if _, ok := f.readPrivate(t).(*protocol.PrivateContinue); !ok {
						t.Fatal("missing private continue")
					}
				} else {
					waitWatermark(t, f, through)
				}
			}
			dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "stop"})
			awaitProof(t, entered)
			if where == "before-ready" {
				f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "gate"})
			}
			f.private(t, protocol.PrivateGateInterrupted{OperationID: "operation-1", GateID: "gate"})
			f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
			finishPrivateReturn(t, f, 130, "/tmp")
			if err := finishCompletion(t, done); err != nil {
				t.Fatal(err)
			}
			m, ok := nextDispatch(t, f).(*protocol.OperationCancelled)
			if !ok || m.Status == nil || *m.Status != 130 || m.Reason != "stop" {
				t.Fatal("lifecycle lost to crossed gate", m)
			}
		})
	}
}

func TestDispatchCleanupCrossings(t *testing.T) {
	for _, kind := range []string{"cancel", "finalize", "finalize-then-cancel"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			entered, release := make(chan struct{}), make(chan struct{})
			done := controlledFixture(t, f, relayTestContext(t), func(ctx context.Context, _ string, _ int64) error {
				close(entered)
				select {
				case <-release:
					return nil
				case <-ctx.Done():
					return ctx.Err()
				}
			}, nil)
			f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
			awaitProof(t, entered)
			if kind != "cancel" {
				dispatchRequest(t, f, &protocol.Finalize{OperationID: "operation-1", Reason: "end"})
			}
			if kind != "finalize" {
				dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "stop"})
			}
			// A later FIFO control request and its acknowledgement prove the earlier
			// lifecycle was processed while cleanup remained blocked, without a sleep.
			dispatchRequest(t, f, &protocol.Resize{Columns: 91, Rows: 31})
			if _, ok := nextDispatch(t, f).(*protocol.ResizeApplied); !ok {
				t.Fatal("cleanup did not service resize")
			}
			close(release)
			finishPrivateReturn(t, f, 9, "/tmp")
			if err := finishCompletion(t, done); err != nil {
				t.Fatal(err)
			}
			m := nextDispatch(t, f)
			if kind == "finalize" {
				if _, ok := m.(*protocol.OperationCompleted); !ok {
					t.Fatal("post-return finalize overrode completion", m)
				}
			} else {
				v, ok := m.(*protocol.OperationCancelled)
				if !ok || v.Status == nil || *v.Status != 9 {
					t.Fatal("cleanup cancel did not win", m)
				}
			}
		})
	}
}

func TestDispatchControlRemainsResponsiveWithBlockedTerminalInput(t *testing.T) {
	f := completionFixture(t)
	entered := make(chan struct{})
	done := controlledFixture(t, f, relayTestContext(t), func(ctx context.Context, _ string, _ int64) error {
		select {
		case <-f.r.inputWriter:
			f.r.inputWriter <- struct{}{}
			return ctx.Err()
		default:
			return errors.New("cleanup raced input writer")
		}
	}, &controlPeers{sessionID: 123, census: heldCensus(entered)})
	if _, err := f.terminal.Write(bytes.Repeat([]byte("x"), 65536)); err != nil {
		t.Fatal(err)
	}
	waitKernelQueue(t, f.slave, nil, syscall.TIOCINQ)
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "blocked-input"})
	awaitProof(t, entered)
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 130, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if _, ok := nextDispatch(t, f).(*protocol.OperationCancelled); !ok {
		t.Fatal("input blocked cancellation")
	}
}

func TestDispatchRejectsMalformedGateTransitions(t *testing.T) {
	for _, kind := range []string{"wrong-operation", "duplicate-ready", "wrong-outcome", "continued-without-continue", "duplicate-continue", "unknown-continue", "unresolved-return"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			done := controlledFixture(t, f, relayTestContext(t), cleanReturn, nil)
			op := "operation-1"
			if kind == "wrong-operation" {
				op = "other"
			}
			f.private(t, protocol.PrivateGateReady{OperationID: op, GateID: "gate"})
			if kind != "wrong-operation" {
				if _, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok {
					t.Fatal("missing ready")
				}
				switch kind {
				case "duplicate-ready":
					f.private(t, protocol.PrivateGateReady{OperationID: op, GateID: "gate"})
				case "wrong-outcome":
					f.private(t, protocol.PrivateGateInterrupted{OperationID: op, GateID: "other"})
				case "continued-without-continue":
					f.private(t, protocol.PrivateGateContinued{OperationID: op, GateID: "gate"})
				case "unresolved-return":
					f.private(t, protocol.PrivateInputClose{OperationID: op, CompletionHelperPID: 77})
				case "unknown-continue":
					dispatchRequest(t, f, &protocol.Continue{OperationID: op, GateID: "other"})
				case "duplicate-continue":
					dispatchRequest(t, f, &protocol.Continue{OperationID: op, GateID: "gate", InputThrough: 100})
					waitWatermark(t, f, 100)
					dispatchRequest(t, f, &protocol.Continue{OperationID: op, GateID: "gate", InputThrough: 100})
				}
			}
			if err := finishCompletion(t, done); err == nil || f.c.w.channels.Err() == nil {
				t.Fatal("invalid transition survived", kind, err)
			}
		})
	}
}

func TestDispatchMissingForegroundPeerFailsWithoutResult(t *testing.T) {
	f := completionFixture(t)
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, nil)
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "stop"})
	if err := finishCompletion(t, done); err == nil {
		t.Fatal("missing census authorized lifecycle")
	}
	m := nextDispatch(t, f)
	if _, ok := m.(*protocol.Diagnostic); !ok {
		t.Fatal("failure emitted terminal result", fmt.Sprintf("%T", m))
	}
}

func TestDispatchShellEndAndTimeoutRequireCleanupAndEOF(t *testing.T) {
	for _, kind := range []string{"shell-end", "unresolved-gate", "cancel-timeout", "finalize-timeout", "cleanup-failure"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			ctx, end := context.WithTimeout(context.Background(), 9*time.Second)
			defer end()
			proofEntered := make(chan struct{})
			cleanupEntered := make(chan struct{})
			release := make(chan struct{})
			peers := &controlPeers{sessionID: 123, census: heldCensus(proofEntered)}
			peers.endShell = func(job context.Context, id string, timeout bool) error {
				close(cleanupEntered)
				if id != "operation-1" || timeout != (kind == "cancel-timeout" || kind == "finalize-timeout") {
					return errors.New("wrong cleanup selection")
				}
				if kind == "cleanup-failure" {
					return errors.New("injected cleanup failure")
				}
				select {
				case <-release:
				case <-job.Done():
					return job.Err()
				}
				if timeout {
					// This return was not accepted when the grace expired. It cannot reopen
					// ordinary completion or authorize input_closed on the timeout branch.
					f.private(t, protocol.PrivateInputClose{OperationID: id, CompletionHelperPID: 77})
					f.private(t, protocol.PrivateShellExit{ActiveOperationID: id, Status: 137, CWD: "/tmp"})
				}
				f.result.Close()
				f.slave.Close()
				return nil
			}
			done := controlledFixture(t, f, ctx, cleanReturn, peers)
			if kind == "unresolved-gate" {
				f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "held"})
				if _, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok {
					t.Fatal("missing ready")
				}
			}
			if kind == "cancel-timeout" {
				dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "stop"})
			} else if kind == "finalize-timeout" {
				dispatchRequest(t, f, &protocol.Finalize{OperationID: "operation-1", Reason: "end"})
			} else {
				f.private(t, protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 7, CWD: "/tmp"})
			}
			select {
			case <-cleanupEntered:
			case <-ctx.Done():
				t.Fatal("cleanup not entered", ctx.Err())
			}
			if kind == "cleanup-failure" {
				if err := finishCompletion(t, done); err == nil {
					t.Fatal("cleanup failure produced success")
				}
				if _, ok := nextDispatch(t, f).(*protocol.Diagnostic); !ok {
					t.Fatal("terminal result escaped failed cleanup")
				}
				return
			}
			// Successful terminal results must not precede cleanup ownership release.
			select {
			case m := <-f.frames:
				if _, mark := m.(*protocol.OutputMark); !mark {
					t.Fatal("terminal result before cleanup", m)
				}
			default:
			}
			close(release)
			if err := finishCompletion(t, done); err != nil {
				t.Fatal(err)
			}
			m := nextDispatch(t, f)
			if kind == "shell-end" {
				v, ok := m.(*protocol.OperationCompleted)
				if !ok || v.ShellEnded == nil || !*v.ShellEnded || v.Status != 7 {
					t.Fatal("wrong observed shell-end winner", m)
				}
			} else {
				v, ok := m.(*protocol.OperationFailed)
				code := kind
				if kind == "unresolved-gate" {
					code = "shell-ended-unresolved"
				}
				if !ok || v.Code != code || v.ShellEnded == nil || !*v.ShellEnded {
					t.Fatal("late shell return replaced timeout", m)
				}
			}
			if m, ok := nextDispatch(t, f).(*protocol.Draining); !ok || m.Reason != "shell_ended" {
				t.Fatal("missing shell-ended drain", m)
			}
			if m, ok := nextDispatch(t, f).(*protocol.Closed); !ok || m.Reason != "shell_ended" {
				t.Fatal("missing closed", m)
			}
			f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
			b := make([]byte, 1)
			if n, err := f.control.Read(b); n != 0 || err == nil {
				t.Fatal("timeout acknowledged crossed input_close", n, err)
			}
		})
	}
}

func TestDispatchInterruptedGateConsumesOneLateContinue(t *testing.T) {
	f := completionFixture(t)
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, nil)
	f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "gate"})
	if _, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok {
		t.Fatal("ready missing")
	}
	f.private(t, protocol.PrivateGateInterrupted{OperationID: "operation-1", GateID: "gate"})
	if _, ok := nextDispatch(t, f).(*protocol.OperationGateInterrupted); !ok {
		t.Fatal("interrupted missing")
	}
	dispatchRequest(t, f, &protocol.Continue{OperationID: "operation-1", GateID: "gate"})
	dispatchRequest(t, f, &protocol.Resize{Columns: 90, Rows: 30})
	if _, ok := nextDispatch(t, f).(*protocol.ResizeApplied); !ok {
		t.Fatal("crossed continue not consumed")
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 0, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	completedEvent(t, f)
}

func TestDispatchCommittedGatePredecessorSurvivesLaterCancel(t *testing.T) {
	f := completionFixture(t)
	entered := make(chan struct{})
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, &controlPeers{sessionID: 123, census: heldCensus(entered)})
	// Block only delivery, then observe the existing relay's guarded phase to
	// prove that readiness was accepted before the later public cancellation.
	f.c.w.channels.writeMu.Lock()
	locked := true
	defer func() {
		if locked {
			f.c.w.channels.writeMu.Unlock()
		}
	}()
	f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "committed"})
	ctx := relayTestContext(t)
	for {
		f.r.phaseMu.Lock()
		started := f.r.phase != nil
		f.r.phaseMu.Unlock()
		if started {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("gate publication did not enter output boundary")
		default:
			runtime.Gosched()
		}
	}
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "later"})
	awaitProof(t, entered)
	f.c.w.channels.writeMu.Unlock()
	locked = false
	if m, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok || m.GateID != "committed" {
		t.Fatal("later lifecycle suppressed committed predecessor", m)
	}
	f.private(t, protocol.PrivateGateInterrupted{OperationID: "operation-1", GateID: "committed"})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 130, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if m, ok := nextDispatch(t, f).(*protocol.OperationCancelled); !ok || m.Reason != "later" {
		t.Fatal("crossed predecessor replaced lifecycle", m)
	}
}

func TestDispatchCancelCrossesFinalizeWithoutNewGrace(t *testing.T) {
	f := completionFixture(t)
	entered := make(chan struct{})
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, &controlPeers{sessionID: 123, census: heldCensus(entered)})
	dispatchRequest(t, f, &protocol.Finalize{OperationID: "operation-1", Reason: "end"})
	awaitProof(t, entered)
	original := f.c.running.life.grace
	deadline, _ := original.Deadline()
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "user"})
	dispatchRequest(t, f, &protocol.Resize{Columns: 93, Rows: 33})
	if _, ok := nextDispatch(t, f).(*protocol.ResizeApplied); !ok {
		t.Fatal("crossed control was not processed")
	}
	if f.c.running.life.grace != original {
		t.Fatal("cancel replaced original finalize grace")
	}
	if got, _ := original.Deadline(); got != deadline {
		t.Fatal("cancel reset grace")
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 130, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if m, ok := nextDispatch(t, f).(*protocol.OperationCancelled); !ok || m.Reason != "user" {
		t.Fatal("cancel did not supersede finalization", m)
	}
}

func TestDispatchNewExecuteSupersedesCrossedLifecycleAllowance(t *testing.T) {
	f := completionFixture(t)
	done := completeFixture(t, f, relayTestContext(t), cleanReturn)
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 0, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	completedEvent(t, f)
	next := startRequest()
	next.OperationID = "operation-2"
	started := f.start(t, relayTestContext(t), next)
	if m, ok := f.readPrivate(t).(*protocol.PrivateExecute); !ok || m.OperationID != next.OperationID {
		t.Fatal("second execute was not accepted", m)
	}
	f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "stale"})
	result := finishStart(t, started)
	if result.err == nil || !strings.Contains(result.err.Error(), "unexpected public request during start") {
		t.Fatal("stale lifecycle allowance survived next execute", result.err)
	}
}

func TestDispatchGateBarrierExpiryRetainsFatalCause(t *testing.T) {
	f := completionFixture(t)
	ctx, end := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer end()
	deadline, _ := ctx.Deadline()
	done := controlledFixture(t, f, ctx, cleanReturn, nil)
	f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "waiting"})
	if _, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok {
		t.Fatal("ready missing")
	}
	dispatchRequest(t, f, &protocol.Continue{OperationID: "operation-1", GateID: "waiting", InputThrough: 10})
	waitWatermark(t, f, 10)
	err := finishCompletion(t, done)
	var fatal *StartError
	if !errors.As(err, &fatal) || fatal.Code != "input-barrier-timeout" || fatal.Deadline != deadline {
		t.Fatal("gate barrier lost original failure classification", err)
	}
	f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || err == nil {
		t.Fatal("expired barrier released gate", n, err)
	}
	for {
		select {
		case m, open := <-f.frames:
			if !open {
				return
			}
			switch m.(type) {
			case *protocol.OutputMark, *protocol.Diagnostic:
			default:
				t.Fatal("fatal barrier published an operation result", m)
			}
		default:
			return
		}
	}
}

func TestDispatchCleanupFailureRetainsCleanupEpoch(t *testing.T) {
	for _, kind := range []string{"ordinary", "shell-end", "queued-timeout"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			ctx, end := context.WithTimeout(context.Background(), 15*time.Second)
			defer end()
			seen := make(chan time.Time, 1)
			fail := func(job context.Context) error {
				deadline, _ := job.Deadline()
				seen <- deadline
				return errors.New("injected cleanup failure")
			}
			peers := &controlPeers{endShell: func(job context.Context, id string, timeout bool) error {
				if id != "operation-1" || timeout != (kind == "queued-timeout") {
					return errors.New("wrong shell-end selection")
				}
				return fail(job)
			}}
			if kind == "queued-timeout" {
				f.c.active.cancel = lifecycleRequest("cancel", "operation-1", "stop").(*protocol.Cancel)
				f.c.active.releasedAt = time.Now().Add(-lifecycleGrace - time.Second)
			}
			done := controlledFixture(t, f, ctx, func(job context.Context, _ string, _ int64) error {
				return fail(job)
			}, peers)
			switch kind {
			case "ordinary":
				f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
			case "shell-end":
				f.private(t, protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 7, CWD: "/tmp"})
			}
			err := finishCompletion(t, done)
			var fatal *StartError
			if !errors.As(err, &fatal) || fatal.Code != "operation-cleanup" {
				t.Fatal("missing cleanup failure", err)
			}
			select {
			case deadline := <-seen:
				caller, _ := ctx.Deadline()
				if fatal.Deadline != deadline || !deadline.Before(caller) {
					t.Fatal("fatal failure replaced the original cleanup epoch", fatal.Deadline, deadline, caller)
				}
			default:
				t.Fatal("cleanup owner did not run")
			}
		})
	}
}

func TestDispatchEmptyForegroundResamplingIsBounded(t *testing.T) {
	p := lifecyclePTYFor(t)
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	l := releasedLifecycle(t)
	if err := l.accept(ctx, lifecycleRequest("cancel", "operation", "stop")); err != nil {
		t.Fatal(err)
	}
	deadline, _ := l.grace.Deadline()
	calls := 0
	peer := p.census()
	peer.members = func(context.Context, int64) ([]foregroundMember, error) {
		calls++
		return nil, nil
	}
	d := &operationDispatch{ctx: ctx, life: l, peers: &controlPeers{sessionID: p.pid, census: peer}}
	defer d.close()
	c := &Coordinator{w: &Workload{PTY: p.master}}
	start := time.Now()
	for time.Since(start) < 50*time.Millisecond {
		if err := d.step(c); err != nil {
			t.Fatal(err)
		}
		select {
		case <-d.proof.done:
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	if calls > 20 {
		t.Fatal("empty foreground caused a tight census loop", calls)
	}
	if got, _ := l.grace.Deadline(); got != deadline || l.attempted {
		t.Fatal("empty resampling reset the grace or signalled", got, deadline, l.attempted)
	}
	if _, err := l.observeReturn(&protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: p.pid}); err != nil {
		t.Fatal(err)
	}
	if err := d.step(c); err != nil || d.proof != nil || l.signalNeeded() {
		t.Fatal("return did not stop foreground sampling", err)
	}
	p.silent(t)
}

func TestDispatchReadyProofFailureWinsBeforeTimerSelection(t *testing.T) {
	for _, kind := range []string{"invalid-proof", "expired-proof", "live-proof-after-expiry"} {
		t.Run(kind, func(t *testing.T) {
			l := newOperationLifecycle("operation")
			defer l.close()
			if err := l.startReleased(context.Background(), StartOutcome{Started: true, ReleasedAt: time.Now().Add(-lifecycleGrace - time.Second), QueuedCancel: lifecycleRequest("cancel", "operation", "stop").(*protocol.Cancel)}); err != nil {
				t.Fatal(err)
			}
			p := &signalProof{done: make(chan struct{}), cancel: func() {}}
			fatal := &StartError{Code: "lifecycle-signal", Cause: errors.New("unclassified live foreground member")}
			switch kind {
			case "invalid-proof":
				p.err = fatal
			case "expired-proof":
				p.err = context.DeadlineExceeded
			case "live-proof-after-expiry":
				p.live = true
			}
			close(p.done)
			d := &operationDispatch{ctx: context.Background(), life: l, proof: p}
			defer func() {
				if d.endCleanup != nil {
					d.endCleanup()
				}
			}()
			err := d.step(&Coordinator{w: &Workload{}})
			if kind == "invalid-proof" {
				if err != fatal || l.timeout != "" {
					t.Fatal("ready proof error was masked by timer", err, l.timeout)
				}
			} else {
				if !errors.Is(err, errLifecycleEnd) || l.timeout != "cancel-timeout" || l.attempted {
					t.Fatal("expired proof signalled or fabricated failure", err, l.timeout, l.attempted)
				}
			}
		})
	}
}

func TestDispatchRejectsThirdResizeWhileOneRequestIsPending(t *testing.T) {
	f := completionFixture(t)
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, nil)
	f.c.w.channels.writeMu.Lock()
	locked := true
	defer func() {
		if locked {
			f.c.w.channels.writeMu.Unlock()
		}
	}()
	dispatchRequest(t, f, &protocol.Resize{Columns: 91, Rows: 31})
	ctx := relayTestContext(t)
	for {
		f.r.phaseMu.Lock()
		entered := f.r.phase != nil
		f.r.phaseMu.Unlock()
		if entered {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("first resize did not enter output boundary")
		default:
			runtime.Gosched()
		}
	}
	dispatchRequest(t, f, &protocol.Resize{Columns: 92, Rows: 32})
	dispatchRequest(t, f, &protocol.Resize{Columns: 93, Rows: 33})
	// A correct rejection cancels/joins the first unattempted boundary before
	// the fatal diagnostic acquires telemetry. Keep the write blocked until then.
	for {
		f.r.phaseMu.Lock()
		joined := f.r.phase == nil
		f.r.phaseMu.Unlock()
		if joined {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("resize pending slot exceeded its bound")
		default:
			runtime.Gosched()
		}
	}
	f.c.w.channels.writeMu.Unlock()
	locked = false
	if err := finishCompletion(t, done); err == nil || !strings.Contains(err.Error(), "multiple outstanding resizes") {
		t.Fatal("wrong duplicate-resize disposition", err)
	}
}

func TestDispatchAcknowledgedResizeQueuesNextUntilIOJoin(t *testing.T) {
	f := completionFixture(t)
	<-f.r.barrier
	defer func() { f.r.barrier <- struct{}{} }()
	// Model the exact boundary after the first complete acknowledgement and
	// acceptance of the next resize, but before the first writer has joined.
	f.c.resizing = true
	request := &protocol.Resize{Columns: 94, Rows: 34}
	f.c.resize = request
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	workDone := make(chan struct{})
	result := make(chan error, 1)
	go func() { _, err := f.c.wait(ctx, false, nil, workDone, false); result <- err }()
	if err := f.c.selectPublication(ctx); err != nil {
		t.Fatal("owner blocked on next resize before joining prior I/O", err)
	}
	close(workDone)
	select {
	case err := <-result:
		if err != nil || f.c.resize != request {
			t.Fatal("next resize ran before prior I/O joined", err)
		}
	case <-ctx.Done():
		t.Fatal("prior I/O completion was lost", ctx.Err())
	}
	f.c.resizing = false
}

func TestDispatchQueuedResizeKeepsLifecycleResponsiveAndAppliesInOrder(t *testing.T) {
	f := completionFixture(t)
	entered := make(chan struct{})
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, &controlPeers{sessionID: 123, census: heldCensus(entered)})
	f.c.w.channels.writeMu.Lock()
	locked := true
	defer func() {
		if locked {
			f.c.w.channels.writeMu.Unlock()
		}
	}()
	dispatchRequest(t, f, &protocol.Resize{Columns: 91, Rows: 31})
	ctx := relayTestContext(t)
	for {
		f.r.phaseMu.Lock()
		active := f.r.phase != nil
		f.r.phaseMu.Unlock()
		if active {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("first resize did not enter its boundary")
		default:
			runtime.Gosched()
		}
	}
	// A decoded request may wait in the bounded pending slot. It is not
	// selected or applied until the current acknowledgement writer joins.
	dispatchRequest(t, f, &protocol.Resize{Columns: 92, Rows: 32})
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "queued-resize"})
	awaitProof(t, entered) // The earlier FIFO resize did not block cancellation.
	f.c.w.channels.writeMu.Unlock()
	locked = false
	for _, columns := range []int64{91, 92} {
		m, ok := nextDispatch(t, f).(*protocol.ResizeApplied)
		if !ok || m.Columns != columns {
			t.Fatal("resize acknowledgement order changed", m)
		}
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 130, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if m, ok := nextDispatch(t, f).(*protocol.OperationCancelled); !ok || m.Reason != "queued-resize" {
		t.Fatal("pending resize lost lifecycle selection", m)
	}
}

func TestDispatchQueuedStartCancelUsesReleasedOperationAndOriginalGrace(t *testing.T) {
	f := startFixtureFor(t, nil)
	started := f.start(t, relayTestContext(t), startRequest())
	f.throughStarted(t)
	f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "queued"})
	ctx := relayTestContext(t)
	for {
		f.c.active.attemptMu.Lock()
		queued := f.c.active.cancel != nil
		f.c.active.attemptMu.Unlock()
		if queued {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("cancel was not retained before start_released")
		default:
			runtime.Gosched()
		}
	}
	f.private(t, protocol.PrivateStartReleased{OperationID: "operation-1"})
	out := finishStart(t, started)
	if out.err != nil || out.out.QueuedCancel == nil || out.out.ReleasedAt.IsZero() {
		t.Fatal("start release lost queued control/epoch", out)
	}
	entered := make(chan struct{})
	done := controlledFixture(t, f, relayTestContext(t), cleanReturn, &controlPeers{sessionID: 123, census: heldCensus(entered)})
	awaitProof(t, entered)
	deadline, _ := f.c.running.life.grace.Deadline()
	if deadline != out.out.ReleasedAt.Add(lifecycleGrace) {
		t.Fatal("completion owner reset queued grace", deadline, out.out.ReleasedAt)
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 130, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if m, ok := nextDispatch(t, f).(*protocol.OperationCancelled); !ok || m.Reason != "queued" || m.Status == nil || *m.Status != 130 {
		t.Fatal("queued cancel did not select started cancellation", m)
	}
}

func TestDispatchEndingContinueRetainsIdentityAndSingleAdmission(t *testing.T) {
	for _, kind := range []string{"wrong-operation", "unknown-gate", "crossed", "duplicate"} {
		t.Run(kind, func(t *testing.T) {
			g := &gateRecord{id: "known", interrupted: true}
			d := &operationDispatch{ending: true, life: newOperationLifecycle("operation-1"), gates: map[string]*gateRecord{"known": g}}
			c := &Coordinator{running: d}
			request := &protocol.Continue{OperationID: "operation-1", GateID: "known"}
			if kind == "wrong-operation" {
				request.OperationID = "unrelated"
			}
			if kind == "unknown-gate" {
				request.GateID = "unknown"
			}
			if kind == "duplicate" {
				g.request = request
			}
			err := c.publicRequest(context.Background(), false, nil, request)
			if (err == nil) != (kind == "crossed") || g.sent {
				t.Fatal("wrong ending continue admission or private release", err, g.sent)
			}
		})
	}
}

func TestDispatchEndingUnknownControlFailsDuringMandatoryCleanup(t *testing.T) {
	for _, kind := range []string{"continue", "cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			entered, joined := make(chan struct{}), make(chan struct{})
			done := controlledFixture(t, f, relayTestContext(t), cleanReturn, &controlPeers{
				endShell: func(ctx context.Context, _ string, _ bool) error {
					defer close(joined)
					close(entered)
					<-ctx.Done()
					return ctx.Err()
				},
			})
			f.private(t, protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 0, CWD: "/tmp"})
			awaitProof(t, entered)
			var request protocol.Message
			want := "unexpected public request during shell end"
			switch kind {
			case "continue":
				request = &protocol.Continue{OperationID: "unrelated", GateID: "unknown"}
				want = "continue names another operation"
			case "cancel":
				request = &protocol.Cancel{OperationID: "unrelated", Reason: "stop"}
			case "finalize":
				request = &protocol.Finalize{OperationID: "unrelated", Reason: "stop"}
			}
			dispatchRequest(t, f, request)
			if err := finishCompletion(t, done); err == nil || !strings.Contains(err.Error(), want) {
				t.Fatal("unknown control survived ending cleanup", err)
			}
			select {
			case <-joined:
			default:
				t.Fatal("failed ending cleanup was not joined")
			}
			if _, ok := nextDispatch(t, f).(*protocol.Diagnostic); !ok {
				t.Fatal("invalid ending traffic published an operation result")
			}
		})
	}
}

func TestDispatchEndingPermittedCrossingsRetainSelectedResult(t *testing.T) {
	d := &operationDispatch{ending: true, life: newOperationLifecycle("operation-1")}
	d.life.timeout = "cancel-timeout"
	c := &Coordinator{running: d}
	next := startRequest()
	next.OperationID = "operation-2"
	for _, request := range []protocol.Message{&protocol.Cancel{OperationID: "operation-1", Reason: "late"}, &protocol.Finalize{OperationID: "operation-1", Reason: "late"}, &next, &protocol.Cancel{OperationID: "operation-2", Reason: "crossed"}, &protocol.Resize{Columns: 90, Rows: 30}, &protocol.Shutdown{Reason: "end"}} {
		if err := c.publicRequest(context.Background(), false, nil, request); err != nil {
			t.Fatal("permitted crossing rejected", request, err)
		}
	}
	if d.life.timeout != "cancel-timeout" || d.life.kind != "" || c.resize != nil {
		t.Fatal("ending crossing replaced selected result or applied resize")
	}
	if err := c.publicRequest(context.Background(), false, nil, &next); err == nil {
		t.Fatal("second crossed execute accepted")
	}
	for _, request := range []protocol.Message{&protocol.Cancel{OperationID: "unknown", Reason: "bad"}, &protocol.Finalize{OperationID: "operation-2", Reason: "bad"}} {
		if err := c.publicRequest(context.Background(), false, nil, request); err == nil {
			t.Fatal("unrelated lifecycle accepted", request)
		}
	}
}
