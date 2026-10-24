//go:build linux

package session

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestCapturedClosureExpiryPriority(t *testing.T) {
	now := time.Now()
	for _, phase := range []StartShellExitPhase{StartShellExitInputBarrier, StartShellExitOperationStart, StartShellExitPrestartDelivery} {
		for _, finalDue := range []bool{false, true} {
			t.Run(string(phase)+"/final-due-"+map[bool]string{false: "false", true: "true"}[finalDue], func(t *testing.T) {
				final := now.Add(time.Second)
				if finalDue {
					final = now.Add(-time.Second)
				}
				drain, stop := context.WithDeadline(context.Background(), final)
				defer stop()
				code, err := capturedClosureCode(phase)
				if err != nil {
					t.Fatal(err)
				}
				original := errors.New("private closure still missing")
				captured := now.Add(-2 * time.Second)
				err = capturedClosureFailure(now, captured, drain, code, original)
				var failed *StartError
				if !errors.As(err, &failed) || failed.Code != code || failed.Deadline != captured ||
					!errors.Is(err, original) || !errors.Is(err, context.DeadlineExceeded) {
					t.Fatal("captured expiry lost priority, identity or original cause", err)
				}
			})
		}
	}
}

func TestCapturedClosureCompletionAndDrainHaveSeparateBounds(t *testing.T) {
	now := time.Now()
	captured := now.Add(time.Second)
	drain, stop := context.WithDeadline(context.Background(), now.Add(2*time.Second))
	defer stop()
	if err := capturedClosureFailure(now, captured, drain, "operation-start-timeout", nil); err != nil {
		t.Fatal("closure before its original expiry failed", err)
	}
	// Once closure has passed, its cancelled local context does not own drain.
	closure, endClosure := context.WithDeadline(drain, captured)
	endClosure()
	if closure.Err() == nil || drain.Err() != nil {
		t.Fatal("retiring closure cancelled independent drain", closure.Err(), drain.Err())
	}
	if got, _ := drain.Deadline(); !got.Equal(now.Add(2 * time.Second)) {
		t.Fatal("retiring closure reset final drain", got)
	}
	original := errors.New("non-timeout private failure")
	if got := capturedClosureFailure(now, captured, drain, "operation-start-timeout", original); got != original {
		t.Fatal("unexpired closure changed its failure", got)
	}
	// At the exact boundary a ready completion cannot outrank captured expiry.
	var failed *StartError
	if err := capturedClosureFailure(captured, captured, drain, "operation-start-timeout", nil); !errors.As(err, &failed) {
		t.Fatal("same-turn completed receipt bypassed expiry", err)
	}
}

func TestCapturedClosureRejectsUnknownPhase(t *testing.T) {
	if _, err := capturedClosureCode(StartShellExitPhase("sender-control-write")); err == nil {
		t.Fatal("sender control-write became an Envoy closure epoch")
	}
}

func TestCapturedClosureCancelledGateWaitAfterInputReset(t *testing.T) {
	f := completionFixture(t)
	channels := f.c.w.channels
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	cancel() // The accepted shell exit interrupted this still-unstarted I/O.
	if err := channels.ResumeInput(""); err != nil {
		t.Fatal(err)
	}
	if err := channels.WaitGateInput(ctx, "operation-1", 1); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled shell-end wait became an ownership failure", err)
	}
	if channels.Err() != nil {
		t.Fatal("expected joined cancellation failed the session", channels.Err())
	}
	live, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := channels.WaitGateInput(live, "operation-1", 1); err == nil {
		t.Fatal("live invalid ownership was accepted")
	}
}

func TestCapturedClosureOnlyOutstandingContinueCanOwnEpoch(t *testing.T) {
	for _, kind := range []string{"satisfied-watermark", "closed-gate"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			ctx, end := context.WithTimeout(context.Background(), 3*time.Second)
			defer end()
			life := newOperationLifecycle("operation-1")
			if err := life.startReleased(ctx, StartOutcome{Started: true, ReleasedAt: time.Now()}); err != nil {
				t.Fatal(err)
			}
			d := &operationDispatch{ctx: ctx, life: life, continueDeadline: time.Now().Add(time.Second),
				gate: &gateRecord{id: "gate", request: &protocol.Continue{OperationID: "operation-1", GateID: "gate", InputThrough: 1}}}
			f.c.running = d
			defer func() {
				d.close()
				if d.endCleanup != nil {
					d.endCleanup()
				}
			}()
			if kind == "satisfied-watermark" {
				if _, err := f.terminal.Write([]byte("x")); err != nil {
					t.Fatal(err)
				}
				event := receiveInput(t, f.c.w.channels)
				if len(event.Input) != 1 || f.c.w.channels.InputThrough() != 1 {
					t.Fatal("missing actual received watermark", event)
				}
			} else {
				d.gate = nil
			}
			// This focused owner test supplies a typed return fact. The native
			// matrix separately requires the actual supervisor and kernel reap.
			terminal := &protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 23, CWD: "/tmp"}
			if _, _, err := f.c.privateFact(ctx, nil, privateResult{message: terminal}); err != nil {
				t.Fatal(err)
			}
			if !f.c.exitedDeadline.IsZero() {
				t.Fatal("ended or satisfied continue barrier replaced current cleanup epoch", kind, f.c.exitedDeadline)
			}
		})
	}
}

func TestC036ContinueGateCallerPreservesOwnerAndErrors(t *testing.T) {
	terminal := &protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 23, CWD: "/tmp"}
	substantive := errors.New("cleanup failed")
	for _, test := range []struct {
		name       string
		kind       string
		err        error
		want       error
		wantNil    bool
		wantMarker bool
	}{
		{name: "expected-cancel-without-cancel", err: context.Canceled, want: errLifecycleEnd, wantMarker: true},
		{name: "expected-cancel-after-cancel", kind: "cancel", err: context.Canceled, want: errLifecycleEnd, wantMarker: true},
		{name: "substantive-join-without-cancel", err: errors.Join(context.Canceled, substantive), want: substantive},
		{name: "substantive-join-after-cancel", kind: "cancel", err: errors.Join(context.Canceled, substantive), want: substantive},
		{name: "nil-worker-result", err: nil, wantNil: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := completionFixture(t)
			deadline := time.Now().Add(time.Second)
			g := &gateRecord{id: "gate"}
			d := &operationDispatch{life: &operationLifecycle{kind: test.kind, shellExit: terminal}, gate: g, continueDeadline: deadline}
			got := d.continueGateError(f.c, g, test.err)
			if test.wantNil {
				if got != nil {
					t.Fatalf("nil worker result became error: %v", got)
				}
			} else if !errors.Is(got, test.want) {
				t.Fatalf("unexpected continuation outcome: %v", got)
			}
			if test.wantMarker && !errors.Is(got, errLifecycleEnd) {
				t.Fatalf("expected lifecycle end marker: %v", got)
			}
			if !test.wantMarker && errors.Is(got, errLifecycleEnd) {
				t.Fatal("substantive joined error became lifecycle end")
			}
			if !d.continueDeadline.Equal(deadline) {
				t.Fatal("accepted shell-end changed the captured continuation deadline", d.continueDeadline, deadline)
			}
		})
	}
}

func TestStamp01InterruptedGateCannotLendEpochToLaterGate(t *testing.T) {
	f := completionFixture(t)
	ctx, end := context.WithTimeout(context.Background(), 12*time.Second)
	defer end()
	life := newOperationLifecycle("operation-1")
	if err := life.startReleased(ctx, StartOutcome{Started: true, ReleasedAt: time.Now()}); err != nil {
		t.Fatal(err)
	}
	first := &gateRecord{id: "first", request: &protocol.Continue{OperationID: "operation-1", GateID: "first", InputThrough: 1}}
	d := &operationDispatch{ctx: ctx, life: life, gate: first, gates: map[string]*gateRecord{"first": first}, gateEvents: make(map[protocol.PrivateMessage]bool)}
	f.c.running = d
	f.c.phase = awaitInputClose
	defer func() {
		d.close()
		if d.endCleanup != nil {
			d.endCleanup()
		}
		f.c.running = nil
	}()
	done := make(chan error, 1)
	go func() { done <- d.continueGate(f.c, ctx) }()
	input := f.c.w.channels.inputState()
	for {
		input.mu.Lock()
		watermark, wake := input.watermark, input.wake
		input.mu.Unlock()
		if watermark == 1 {
			break
		}
		select {
		case <-wake:
		case <-time.After(time.Millisecond):
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	firstDeadline := d.continueDeadline
	if firstDeadline.IsZero() {
		t.Fatal("probe did not exercise interrupted original barrier")
	}
	f.private(t, protocol.PrivateGateInterrupted{OperationID: "operation-1", GateID: "first"})
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if d.gate != nil {
		t.Fatal("interrupted original gate remained live", d.gate)
	}
	if _, err := f.c.result(ctx); err != nil {
		t.Fatal(err)
	}
	f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "second"})
	if _, err := f.c.result(ctx); err != nil {
		t.Fatal(err)
	}
	if err := f.c.publicRequest(ctx, false, nil, &protocol.Continue{OperationID: "operation-1", GateID: "second", InputThrough: 2}); err != nil {
		t.Fatal(err)
	}
	// A ready terminal fact wins before the second continue wait is dispatched.
	terminal := &protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 23, CWD: "/tmp"}
	if _, _, err := f.c.privateFact(ctx, nil, privateResult{message: terminal}); err != nil {
		t.Fatal(err)
	}
	if !d.continueDeadline.IsZero() || !f.c.exitedDeadline.IsZero() {
		t.Fatal("new gate shell exit captured a retired continuation deadline", d.continueDeadline, f.c.exitedDeadline, firstDeadline)
	}
}

func TestStamp01CancelFirstLiveGateRetainsClosureEpoch(t *testing.T) {
	f := completionFixture(t)
	ctx, end := context.WithTimeout(context.Background(), 12*time.Second)
	defer end()
	life := newOperationLifecycle("operation-1")
	if err := life.startReleased(ctx, StartOutcome{Started: true, ReleasedAt: time.Now()}); err != nil {
		t.Fatal(err)
	}
	// Keep the existing foreground proof pending until the shell-exit fact.
	// This peer supplies no positive sample or signal authorization.
	first := &gateRecord{id: "live", request: &protocol.Continue{OperationID: "operation-1", GateID: "live", InputThrough: 1}}
	d := &operationDispatch{ctx: ctx, life: life, peers: &controlPeers{sessionID: 1, census: heldCensus(make(chan struct{}))}, gate: first, gates: map[string]*gateRecord{"live": first}, gateEvents: make(map[protocol.PrivateMessage]bool)}
	f.c.running = d
	f.c.phase = awaitInputClose
	defer func() {
		d.close()
		if d.endCleanup != nil {
			d.endCleanup()
		}
		f.c.running = nil
	}()
	done := make(chan error, 1)
	go func() { done <- d.continueGate(f.c, ctx) }()
	input := f.c.w.channels.inputState()
	for {
		input.mu.Lock()
		watermark := input.watermark
		input.mu.Unlock()
		if watermark == 1 {
			break
		}
		select {
		case <-time.After(time.Millisecond):
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	original := d.continueDeadline
	if original.IsZero() {
		t.Fatal("missing original live deadline")
	}
	f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "cancel-before-terminal"})
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if d.gate != first || d.life.kind != "cancel" || f.c.w.channels.InputThrough() >= 1 {
		t.Fatal("probe did not retain unresolved live gate")
	}
	terminal := &protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 23, CWD: "/tmp"}
	if _, _, err := f.c.privateFact(ctx, nil, privateResult{message: terminal}); err != nil {
		t.Fatal(err)
	}
	if f.c.exitedDeadline != original || f.c.exitedPhase != StartShellExitInputBarrier {
		t.Fatal("cancel-first shell exit lost live gate original closure epoch", f.c.exitedDeadline, original, f.c.exitedPhase)
	}
}
