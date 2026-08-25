package protocol

import (
	"encoding/base64"
	"testing"
)

func requireCode(t *testing.T, code string, err error) {
	t.Helper()
	protocolErr, ok := err.(*Error)
	if !ok || protocolErr.Code != code {
		t.Fatalf("want %s, got %v", code, err)
	}
}

func readyState(t *testing.T) *SessionState {
	t.Helper()
	state := NewSessionState()
	if err := state.AcceptController(Hello{Seq: 1, SessionID: "session-1"}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(Ready{Seq: 1, EnvoyPID: 41, ShellPID: 42, CWD: "/work", Columns: 80, Rows: 24}); err != nil {
		t.Fatal(err)
	}
	return state
}

func testExecute(seq uint64, operationID, source string) Execute {
	return Execute{Seq: seq, OperationID: operationID, Source: source, ExecutionPolicy: testExecutionPolicy()}
}

func testPTYExecute(seq uint64, operationID, source string) Execute {
	return Execute{
		Seq: seq, OperationID: operationID, Source: source,
		ExecutionPolicy: ExecutionPolicy{
			ExecutionShape: ExecutionPTY,
			Timing:         TimingRealtime,
			Publication:    PublicationReal,
			Observation:    ObservationExclusive,
		},
	}
}

func TestSessionStateGateAndOrderedShutdown(t *testing.T) {
	state := readyState(t)
	steps := []struct {
		controller bool
		message    any
	}{
		{true, Resize{Seq: 2, Columns: 100, Rows: 30}},
		{false, ResizeApplied{Seq: 2, Columns: 100, Rows: 30}},
		{true, testExecute(3, "op-1", "printf ok")},
		{false, OperationStarted{Seq: 3, OperationID: "op-1", OutputStart: 0}},
		{false, OperationReady{Seq: 4, OperationID: "op-1", GateID: "gate-1", OutputThrough: 3}},
		{true, Continue{Seq: 4, OperationID: "op-1", GateID: "gate-1"}},
		{false, OperationContinued{Seq: 5, OperationID: "op-1", GateID: "gate-1", OutputThrough: 3}},
		{false, OperationCompleted{Seq: 6, OperationID: "op-1", Status: 0, CWD: "/work", OutputStart: 0, OutputThrough: 6}},
		{true, Shutdown{Seq: 5, Reason: "capture-complete"}},
		{false, Draining{Seq: 7, Reason: "capture-complete", OutputThrough: 6}},
		{false, Closed{Seq: 8, Reason: "shutdown", OutputThrough: 6}},
	}
	for _, step := range steps {
		var err error
		if step.controller {
			err = state.AcceptController(step.message)
		} else {
			err = state.AcceptEnvoy(step.message)
		}
		if err != nil {
			t.Fatalf("%T: %v", step.message, err)
		}
	}
	if state.Phase() != PhaseClosed || state.OutputThrough() != 6 {
		t.Fatalf("unexpected final state: %s at %d", state.Phase(), state.OutputThrough())
	}
}

func TestSessionStateCancellationAndFailure(t *testing.T) {
	state := readyState(t)
	if err := state.AcceptController(testExecute(2, "op-1", "sleep 30")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "op-1", OutputStart: 0}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptController(Cancel{Seq: 3, OperationID: "op-1", Reason: "deadline"}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "cancellation-reason-mismatch", state.AcceptEnvoy(OperationCancelled{Seq: 3, OperationID: "op-1", Status: 130, CWD: "/work", Reason: "other", OutputStart: 0, OutputThrough: 1}))
	if err := state.AcceptEnvoy(OperationCancelled{Seq: 3, OperationID: "op-1", Status: 130, CWD: "/work", Reason: "deadline", OutputStart: 0, OutputThrough: 1}); err != nil {
		t.Fatal(err)
	}
	if state.Phase() != PhaseIdle || state.OutputThrough() != 1 {
		t.Fatalf("unexpected cancellation state: %s at %d", state.Phase(), state.OutputThrough())
	}

	state = readyState(t)
	if err := state.AcceptController(testExecute(2, "op-1", "true")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationFailed{Seq: 2, OperationID: "op-1", Code: "shell-exited", Message: "failed", CWD: "/work", OutputStart: 4, OutputThrough: 5}); err != nil {
		t.Fatal(err)
	}
	if state.Phase() != PhaseIdle || state.OutputThrough() != 5 {
		t.Fatalf("unexpected failure state: %s at %d", state.Phase(), state.OutputThrough())
	}
}

func TestSessionStateRejectsInvalidTransitions(t *testing.T) {
	state := NewSessionState()
	requireCode(t, "out-of-state", state.AcceptController(testExecute(1, "op-1", "true")))

	state = readyState(t)
	if err := state.AcceptController(Resize{Seq: 2, Columns: 100, Rows: 30}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "out-of-state", state.AcceptController(Resize{Seq: 3, Columns: 120, Rows: 40}))

	state = readyState(t)
	if err := state.AcceptController(testExecute(2, "op-1", "true")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "op-1", OutputStart: 5}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationReady{Seq: 3, OperationID: "op-1", GateID: "gate-1", OutputThrough: 8}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptController(Continue{Seq: 3, OperationID: "op-1", GateID: "gate-1"}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "invalid-output-order", state.AcceptEnvoy(OperationContinued{Seq: 4, OperationID: "op-1", GateID: "gate-1", OutputThrough: 7}))
	if err := state.AcceptEnvoy(OperationContinued{Seq: 4, OperationID: "op-1", GateID: "gate-1", OutputThrough: 8}); err != nil {
		t.Fatal(err)
	}
}

func TestSessionStateRejectsReusedGateAndBadShutdownReason(t *testing.T) {
	state := readyState(t)
	if err := state.AcceptController(testExecute(2, "op-1", "true")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "op-1", OutputStart: 0}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationReady{Seq: 3, OperationID: "op-1", GateID: "gate-1", OutputThrough: 0}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptController(Continue{Seq: 3, OperationID: "op-1", GateID: "gate-1"}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationContinued{Seq: 4, OperationID: "op-1", GateID: "gate-1", OutputThrough: 0}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "reused-gate", state.AcceptEnvoy(OperationReady{Seq: 5, OperationID: "op-1", GateID: "gate-1", OutputThrough: 0}))

	state = readyState(t)
	if err := state.AcceptController(Shutdown{Seq: 2, Reason: "capture-complete"}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "shutdown-reason-mismatch", state.AcceptEnvoy(Draining{Seq: 2, Reason: "other", OutputThrough: 0}))
}

func TestSessionStateRejectsWrongSequence(t *testing.T) {
	state := NewSessionState()
	requireCode(t, "invalid-sequence", state.AcceptController(Hello{Seq: 2, SessionID: "session-1"}))
}

func TestSessionStateLogicalStreamsAndPlannedFinalization(t *testing.T) {
	state := readyState(t)
	if err := state.AcceptController(testExecute(2, "server", "serve_forever")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "server", OutputStart: 0}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationOutput{Seq: 3, OperationID: "server", Stream: "stdout", DataBase64: "b3V0Cg=="}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptController(Finalize{Seq: 3, OperationID: "server", Reason: "recording-end"}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationOutput{Seq: 4, OperationID: "server", Stream: "stderr", DataBase64: "ZXJyCg=="}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "finalization-reason-mismatch", state.AcceptEnvoy(OperationFinalized{
		Seq: 5, OperationID: "server", CWD: "/work", Reason: "other",
		OutputStart: 0, OutputThrough: 8,
	}))
	if err := state.AcceptEnvoy(OperationFinalized{
		Seq: 5, OperationID: "server", CWD: "/work", Reason: "recording-end",
		OutputStart: 0, OutputThrough: 8,
	}); err != nil {
		t.Fatal(err)
	}
	if state.Phase() != PhaseIdle || state.OutputThrough() != 8 {
		t.Fatalf("unexpected finalization state: %s at %d", state.Phase(), state.OutputThrough())
	}
}

func TestSessionStateRejectsLogicalEvidenceBeyondOutputBarrier(t *testing.T) {
	state := readyState(t)
	if err := state.AcceptController(testExecute(2, "server", "printf out")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "server", OutputStart: 10}); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationOutput{Seq: 3, OperationID: "server", Stream: "stdout", DataBase64: "b3V0Cg=="}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "invalid-output-order", state.AcceptEnvoy(OperationReady{
		Seq: 4, OperationID: "server", GateID: "gate-1", OutputThrough: 13,
	}))
}

func TestSessionStateRejectsLogicalStreamsForPTYOperations(t *testing.T) {
	state := readyState(t)
	if err := state.AcceptController(testPTYExecute(2, "interactive", "bash")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "interactive", OutputStart: 0}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "out-of-state", state.AcceptEnvoy(OperationOutput{
		Seq: 3, OperationID: "interactive", Stream: "stdout", DataBase64: "b3V0Cg==",
	}))
}

func TestSessionStateBoundsAccumulatedLogicalStreams(t *testing.T) {
	state := readyState(t)
	if err := state.AcceptController(testExecute(2, "bounded", "produce_output")); err != nil {
		t.Fatal(err)
	}
	if err := state.AcceptEnvoy(OperationStarted{Seq: 2, OperationID: "bounded", OutputStart: 0}); err != nil {
		t.Fatal(err)
	}
	chunk := base64.StdEncoding.EncodeToString(make([]byte, MaxLogicalChunkBytes))
	for seq := uint64(3); seq < 45; seq++ {
		if err := state.AcceptEnvoy(OperationOutput{Seq: seq, OperationID: "bounded", Stream: "stdout", DataBase64: chunk}); err != nil {
			t.Fatalf("chunk %d: %v", seq-2, err)
		}
	}
	requireCode(t, "logical-output-too-large", state.AcceptEnvoy(OperationOutput{
		Seq: 45, OperationID: "bounded", Stream: "stdout", DataBase64: chunk,
	}))
}
