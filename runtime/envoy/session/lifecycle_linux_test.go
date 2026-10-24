//go:build linux

package session

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func lifecycleRequest(kind, id, reason string) protocol.Message {
	h := protocol.Header{Schema: protocol.Schema, Type: kind, Seq: 1}
	if kind == "cancel" {
		return &protocol.Cancel{Header: h, OperationID: id, Reason: reason}
	}
	return &protocol.Finalize{Header: h, OperationID: id, Reason: reason}
}

func releasedLifecycle(t *testing.T) *operationLifecycle {
	t.Helper()
	l := newOperationLifecycle("operation")
	if err := l.startReleased(context.Background(), StartOutcome{Started: true}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(l.close)
	return l
}

func TestLifecycleStartReleaseAndOriginalGrace(t *testing.T) {
	for _, kind := range []string{"cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			l := newOperationLifecycle("operation")
			t.Cleanup(l.close)
			request := lifecycleRequest(kind, "operation", "first")
			if err := l.accept(context.Background(), request); err == nil || l.grace != nil || l.signalNeeded() {
				t.Fatal("unreleased start acquired a lifecycle signal or timer", err)
			}
			before := time.Now()
			out := StartOutcome{Started: true}
			if kind == "cancel" {
				out.QueuedCancel = request.(*protocol.Cancel)
			}
			if err := l.startReleased(context.Background(), out); err != nil {
				t.Fatal(err)
			}
			if kind == "finalize" {
				if l.grace != nil || l.signalNeeded() {
					t.Fatal("start without a queued request created a grace")
				}
				if err := l.accept(context.Background(), request); err != nil {
					t.Fatal(err)
				}
			}
			deadline, ok := l.grace.Deadline()
			if !ok || deadline.Before(before.Add(5*time.Second)) || deadline.After(time.Now().Add(5*time.Second)) || !l.signalNeeded() {
				t.Fatal("grace did not start at lifecycle selection", deadline, ok)
			}
			if kind == "finalize" {
				if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "crossed")); err != nil {
					t.Fatal(err)
				}
			}
			again, _ := l.grace.Deadline()
			if !again.Equal(deadline) || l.kind != "cancel" || (kind == "finalize" && l.reason != "crossed") {
				t.Fatal("crossed request reset the epoch or lost cancellation", again, l.kind, l.reason)
			}
			if err := l.startReleased(context.Background(), out); err == nil {
				t.Fatal("accepted a second start release")
			}
		})
	}
	for _, out := range []StartOutcome{{}, {Started: true, ShellExit: &protocol.PrivateShellExit{}}} {
		l := newOperationLifecycle("operation")
		if err := l.startReleased(context.Background(), out); err == nil || l.grace != nil || l.signalNeeded() {
			t.Fatal("rejected/pre-start/shell-ended outcome acquired lifecycle ownership")
		}
	}
}

func TestLifecycleReturnAndCleanupCrossings(t *testing.T) {
	for _, first := range []string{"", "cancel", "finalize"} {
		for _, later := range []string{"", "cancel", "finalize"} {
			t.Run(first+"-return-"+later, func(t *testing.T) {
				l := releasedLifecycle(t)
				if first != "" {
					if err := l.accept(context.Background(), lifecycleRequest(first, "operation", "initial")); err != nil {
						t.Fatal(err)
					}
				}
				original := l.grace
				ack, err := l.observeReturn(&protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: 123})
				if err != nil || !ack || l.signalNeeded() {
					t.Fatal("return did not stop signalling and authorize input_closed after cleanup", ack, err)
				}
				if later != "" {
					err = l.accept(context.Background(), lifecycleRequest(later, "operation", "during-cleanup"))
					if (err != nil) != (first == "cancel" && later == "cancel") {
						t.Fatal("wrong request disposition during cleanup", err)
					}
				}
				if l.grace != original || l.signalNeeded() {
					t.Fatal("return cleanup created/reset grace or signalled idle Bash")
				}
				kind, reason := l.outcome()
				want, wantReason := "completed", ""
				if first == "finalize" {
					want, wantReason = "finalized", "initial"
				}
				if first == "cancel" {
					want, wantReason = "cancelled", "initial"
				}
				if later == "cancel" && first != "cancel" {
					want, wantReason = "cancelled", "during-cleanup"
				}
				if kind != want || reason != wantReason {
					t.Fatal("wrong selected outcome", kind, reason, want, wantReason)
				}
				if timeout, err := l.selectTimeout(time.Now().Add(time.Hour)); timeout || err != nil {
					t.Fatal("return cleanup replaced by grace timeout", timeout, err)
				}
			})
		}
	}
}

func TestLifecycleTimeoutAndLateFacts(t *testing.T) {
	for _, kind := range []string{"cancel", "finalize"} {
		for _, first := range []string{"input_close", "shell_exit", "timeout"} {
			t.Run(kind+"-"+first, func(t *testing.T) {
				l := releasedLifecycle(t)
				if err := l.accept(context.Background(), lifecycleRequest(kind, "operation", "selected")); err != nil {
					t.Fatal(err)
				}
				deadline, _ := l.grace.Deadline()
				if timed, err := l.selectTimeout(deadline.Add(-time.Nanosecond)); timed || err != nil {
					t.Fatal("early timeout", timed, err)
				}
				input := &protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: 123}
				exit := &protocol.PrivateShellExit{ActiveOperationID: "operation", Status: 130, CWD: "/"}
				if first == "input_close" {
					if ack, err := l.observeReturn(input); !ack || err != nil {
						t.Fatal(ack, err)
					}
				}
				if first == "shell_exit" {
					if _, err := l.observeReturn(exit); err != nil {
						t.Fatal(err)
					}
				}
				timed, err := l.selectTimeout(deadline)
				if err != nil || timed != (first == "timeout") {
					t.Fatal("wrong serialized winner", timed, err)
				}
				if first == "timeout" {
					if ack, err := l.observeReturn(input); ack || err != nil {
						t.Fatal("late input_close was acknowledged or not consumed", ack, err)
					}
					if _, err := l.observeReturn(exit); err != nil {
						t.Fatal("late shell_exit not retained as reap evidence", err)
					}
					if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "late")); err != nil {
						t.Fatal(err)
					}
				}
				outcome, _ := l.outcome()
				want := map[string]string{"input_close": map[string]string{"cancel": "cancelled", "finalize": "finalized"}[kind], "shell_exit": "shell-ended", "timeout": kind + "-timeout"}[first]
				if outcome != want || l.signalNeeded() {
					t.Fatal("winner replaced or signal still permitted", outcome, want)
				}
			})
		}
	}
}

func TestLifecycleRejectsInvalidAndCommittedFacts(t *testing.T) {
	l := releasedLifecycle(t)
	for _, request := range []protocol.Message{
		lifecycleRequest("cancel", "other", "reason"),
		lifecycleRequest("finalize", "operation", ""),
		&protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: 1}, Columns: 80, Rows: 24},
	} {
		if err := l.accept(context.Background(), request); err == nil || l.grace != nil {
			t.Fatal("invalid request changed lifecycle", request, err)
		}
	}
	for _, fact := range []protocol.PrivateMessage{
		&protocol.PrivateInputClose{OperationID: "other", CompletionHelperPID: 123},
		&protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: 0},
		&protocol.PrivateShellExit{ActiveOperationID: "other", Status: 0, CWD: "/"},
		&protocol.PrivateCompleted{OperationID: "operation", Status: 0, PhysicalCWD: "/", Inspections: []protocol.ResolvedInspection{}},
	} {
		if _, err := l.observeReturn(fact); err == nil {
			t.Fatal("invalid or out-of-state fact accepted", fact)
		}
	}
	l.committed = true
	if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "late")); err != nil || l.grace != nil || l.signalNeeded() {
		t.Fatal("late request replaced committed result", err)
	}
	if _, err := l.observeReturn(&protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: 123}); err == nil {
		t.Fatal("return after committed result accepted")
	}
}

func TestLifecycleSessionCancellationIsNotGraceTimeout(t *testing.T) {
	l := releasedLifecycle(t)
	ctx, cancel := context.WithCancel(context.Background())
	if err := l.accept(ctx, lifecycleRequest("cancel", "operation", "cancel")); err != nil {
		t.Fatal(err)
	}
	cancel()
	deadline, _ := l.grace.Deadline()
	if timed, err := l.selectTimeout(deadline); timed || !errors.Is(err, context.Canceled) || l.timeout != "" {
		t.Fatal("session cancellation relabelled as operation timeout", timed, err)
	}
}

func TestLifecycleEarlierParentDeadlineIsSessionFailure(t *testing.T) {
	for _, kind := range []string{"cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			l := releasedLifecycle(t)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
			defer cancel()
			if err := l.accept(ctx, lifecycleRequest(kind, "operation", "reason")); err != nil {
				t.Fatal(err)
			}
			deadline, ok := l.grace.Deadline()
			if !ok || time.Until(deadline) < lifecycleGrace-time.Second {
				t.Fatal("parent deadline replaced lifecycle grace", deadline)
			}
			if kind == "finalize" {
				if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "crossed")); err != nil {
					t.Fatal(err)
				}
				again, _ := l.grace.Deadline()
				if !again.Equal(deadline) || l.kind != "cancel" || l.reason != "crossed" {
					t.Fatal("crossed cancellation changed lifecycle epoch", again, l.kind, l.reason)
				}
			}
			<-ctx.Done()
			selected, err := l.selectTimeout(time.Now())
			if selected || !errors.Is(err, context.DeadlineExceeded) || l.timeout != "" {
				t.Fatalf("parent deadline became lifecycle timeout: selected=%v err=%v timeout=%q", selected, err, l.timeout)
			}
		})
	}
}

func TestLifecycleRejectsFinalizeDuringSelectedLifecycle(t *testing.T) {
	for _, first := range []string{"cancel", "finalize"} {
		t.Run(first, func(t *testing.T) {
			l := releasedLifecycle(t)
			if err := l.accept(context.Background(), lifecycleRequest(first, "operation", "original")); err != nil {
				t.Fatal(err)
			}
			original := l.grace
			deadline, _ := original.Deadline()
			next := lifecycleRequest("finalize", "operation", "replacement").(*protocol.Finalize)
			next.Seq = 2
			if err := l.accept(context.Background(), next); err == nil {
				t.Fatal("out-of-state finalize silently accepted")
			}
			again, _ := l.grace.Deadline()
			if l.grace != original || !again.Equal(deadline) || l.kind != first || l.reason != "original" || l.attempted {
				t.Fatal("rejected finalize changed original lifecycle selection")
			}
		})
	}
}

func TestForegroundProofValidation(t *testing.T) {
	valid := foregroundMember{PID: 43, PGID: 42, SessionID: 41, Controlled: true}
	for _, tc := range []struct {
		name                string
		sid, observed, pgid int64
		members             []foregroundMember
		bad                 bool
	}{
		{"controlled", 41, 41, 42, []foregroundMember{valid}, false},
		{"empty-return-crossing", 41, 41, 42, nil, false},
		{"missing-session", 0, 0, 42, nil, true},
		{"wrong-terminal-session", 41, 40, 42, nil, true},
		{"zero-group", 41, 41, 0, nil, true},
		{"negative-group", 41, 41, -1, nil, true},
		{"wrong-member-session", 41, 41, 42, []foregroundMember{{PID: 43, PGID: 42, SessionID: 40, Controlled: true}}, true},
		{"unknown-member", 41, 41, 42, []foregroundMember{{PID: 43, PGID: 42, SessionID: 41}}, true},
		{"wrong-member-group", 41, 41, 42, []foregroundMember{{PID: 43, PGID: 44, SessionID: 41, Controlled: true}}, true},
		{"invalid-member", 41, 41, 42, []foregroundMember{{PID: 0, PGID: 42, SessionID: 41, Controlled: true}}, true},
		{"over-bound-member", 41, 41, 42, []foregroundMember{{PID: 2147483648, PGID: 42, SessionID: 41, Controlled: true}}, true},
		{"mixed-unexpected", 41, 41, 42, []foregroundMember{valid, {PID: 44, PGID: 42, SessionID: 41}}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := validateForeground(tc.sid, tc.observed, tc.pgid, tc.members); (err != nil) != tc.bad {
				t.Fatal("wrong foreground proof decision", err)
			}
		})
	}
}

func TestQueuedLifecycleUsesOriginalStartReleaseEpoch(t *testing.T) {
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	released := time.Now().Add(-lifecycleGrace - time.Second)
	l := newOperationLifecycle("operation")
	defer l.close()
	request := lifecycleRequest("cancel", "operation", "queued").(*protocol.Cancel)
	if err := l.startReleased(ctx, StartOutcome{Started: true, QueuedCancel: request, ReleasedAt: released}); err != nil {
		t.Fatal(err)
	}
	deadline, _ := l.grace.Deadline()
	if deadline != released.Add(lifecycleGrace) {
		t.Fatal("queued request reset grace to completion-owner entry", deadline, released)
	}
	if selected, err := l.selectTimeout(time.Now()); err != nil || !selected {
		t.Fatal("expired queued request received a fresh allowance", selected, err)
	}
}
