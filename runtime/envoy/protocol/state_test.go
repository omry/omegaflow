package protocol

import "testing"

type sessionDriver struct {
	t             *testing.T
	state         *SessionState
	controllerSeq uint64
	envoySeq      uint64
}

func newSessionDriver(t *testing.T) *sessionDriver {
	t.Helper()
	driver := &sessionDriver{t: t, state: NewSessionState()}
	driver.controller(Hello{SessionID: "9f3c7a1e5b2d48c6a0e4f18d73b9c25a"})
	driver.envoy(Ready{EnvoyPID: 2, ShellPID: 3, CWD: "/w", Columns: 80, Rows: 24})
	return driver
}

func (driver *sessionDriver) controller(message any) {
	driver.t.Helper()
	if err := driver.tryController(message); err != nil {
		driver.t.Fatalf("controller %T: %v", message, err)
	}
}

func (driver *sessionDriver) tryController(message any) error {
	driver.controllerSeq++
	stamped := withSequence(message, driver.controllerSeq)
	err := driver.state.AcceptController(stamped)
	if err != nil {
		driver.controllerSeq--
	}
	return err
}

func (driver *sessionDriver) envoy(message any) {
	driver.t.Helper()
	if err := driver.tryEnvoy(message); err != nil {
		driver.t.Fatalf("envoy %T: %v", message, err)
	}
}

func (driver *sessionDriver) tryEnvoy(message any) error {
	driver.envoySeq++
	stamped := withSequence(message, driver.envoySeq)
	err := driver.state.AcceptEnvoy(stamped)
	if err != nil {
		driver.envoySeq--
	}
	return err
}

func (driver *sessionDriver) mark(offset, elapsed uint64) {
	driver.t.Helper()
	driver.envoy(OutputMark{Offset: offset, Stream: "pty", ElapsedUS: elapsed})
}

func withSequence(message any, sequence uint64) any {
	switch value := message.(type) {
	case Hello:
		value.Seq = sequence
		return value
	case Execute:
		value.Seq = sequence
		return value
	case Continue:
		value.Seq = sequence
		return value
	case Cancel:
		value.Seq = sequence
		return value
	case Finalize:
		value.Seq = sequence
		return value
	case Resize:
		value.Seq = sequence
		return value
	case Shutdown:
		value.Seq = sequence
		return value
	case Ready:
		value.Seq = sequence
		return value
	case OperationStarted:
		value.Seq = sequence
		return value
	case OperationReady:
		value.Seq = sequence
		return value
	case OperationContinued:
		value.Seq = sequence
		return value
	case OperationGateInterrupted:
		value.Seq = sequence
		return value
	case OutputMark:
		value.Seq = sequence
		return value
	case OperationCompleted:
		value.Seq = sequence
		return value
	case OperationCancelled:
		value.Seq = sequence
		return value
	case OperationFinalized:
		value.Seq = sequence
		return value
	case OperationFailed:
		value.Seq = sequence
		return value
	case ResizeApplied:
		value.Seq = sequence
		return value
	case Diagnostic:
		value.Seq = sequence
		return value
	case Draining:
		value.Seq = sequence
		return value
	case Closed:
		value.Seq = sequence
		return value
	default:
		return message
	}
}

func realtimeExecute(operationID string, inputThrough uint64) Execute {
	return Execute{
		OperationID:     operationID,
		Source:          "true",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationShared},
		Inspections:     []InspectionSpec{},
		InputThrough:    inputThrough,
	}
}

func (driver *sessionDriver) startRealtime(operationID string, elapsed uint64) {
	driver.t.Helper()
	offset := driver.state.OutputThrough()
	driver.controller(realtimeExecute(operationID, 0))
	driver.mark(offset, elapsed)
	driver.envoy(OperationStarted{OperationID: operationID, OutputStart: offset})
}

func TestSessionIDMismatchFailsHandshake(t *testing.T) {
	state, err := NewSessionStateForSession("9f3c7a1e5b2d48c6a0e4f18d73b9c25a")
	if err != nil {
		t.Fatalf("state: %v", err)
	}
	err = state.AcceptController(Hello{Seq: 1, SessionID: "00000000000000000000000000000000"})
	mustCode(t, err, "session-mismatch")
	if err := state.AcceptController(Hello{Seq: 1, SessionID: "9f3c7a1e5b2d48c6a0e4f18d73b9c25a"}); err != nil {
		t.Fatalf("matching hello: %v", err)
	}
}

func TestGateInterruptionAndCrossings(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(Execute{
		OperationID:     "op-1",
		Source:          "./serve",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationSuppress, Observation: ObservationExclusive},
		Inspections:     []InspectionSpec{},
		InputThrough:    0,
	})
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	driver.mark(5, 20)
	driver.envoy(OperationReady{OperationID: "op-1", GateID: "gate-1", OutputThrough: 5})

	// The interruption reopens the running operation.
	driver.mark(5, 30)
	driver.envoy(OperationGateInterrupted{OperationID: "op-1", GateID: "gate-1", OutputThrough: 5})
	if driver.state.Phase() != PhaseRunning {
		t.Fatalf("expected running, got %s", driver.state.Phase())
	}

	// A continue that crossed the interruption is accepted and discarded.
	driver.controller(Continue{OperationID: "op-1", GateID: "gate-1", InputThrough: 3})
	if driver.state.Phase() != PhaseRunning {
		t.Fatalf("crossed continue must not change state, got %s", driver.state.Phase())
	}

	// Gate IDs cannot be reused within an operation.
	driver.mark(5, 40)
	err := driver.tryEnvoy(OperationReady{OperationID: "op-1", GateID: "gate-1", OutputThrough: 5})
	mustCode(t, err, "reused-gate")

	// A crossed cancel remains live against the resumed operation: the
	// interruption is a self-transition while cancelling.
	driver.envoy(OperationReady{OperationID: "op-1", GateID: "gate-2", OutputThrough: 5})
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})
	if driver.state.Phase() != PhaseCancelling {
		t.Fatalf("expected cancelling, got %s", driver.state.Phase())
	}
	driver.mark(5, 50)
	driver.envoy(OperationGateInterrupted{OperationID: "op-1", GateID: "gate-2", OutputThrough: 5})
	if driver.state.Phase() != PhaseCancelling {
		t.Fatalf("interruption while cancelling is a self-transition, got %s", driver.state.Phase())
	}
	driver.mark(5, 60)
	driver.envoy(OperationCancelled{OperationID: "op-1", Status: intPointer(130), CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 5})
	if driver.state.Phase() != PhaseIdle {
		t.Fatalf("expected idle, got %s", driver.state.Phase())
	}

	// A cancel naming the completed operation is accepted and discarded
	// while it is still the most recent operation.
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})
	if driver.state.Phase() != PhaseIdle {
		t.Fatalf("crossed cancel must be discarded, got %s", driver.state.Phase())
	}

	// A crossed finalize is likewise preserved through a winning gate
	// interruption: the interruption is a self-transition while
	// finalizing and the finalization request stays live.
	driver.controller(Execute{
		OperationID:     "op-2",
		Source:          "./serve",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationSuppress, Observation: ObservationExclusive},
		Inspections:     []InspectionSpec{},
		InputThrough:    3,
	})
	driver.mark(5, 70)
	driver.envoy(OperationStarted{OperationID: "op-2", OutputStart: 5})
	driver.mark(5, 80)
	driver.envoy(OperationReady{OperationID: "op-2", GateID: "gate-1", OutputThrough: 5})
	driver.controller(Finalize{OperationID: "op-2", Reason: "recording-end"})
	driver.mark(5, 90)
	driver.envoy(OperationGateInterrupted{OperationID: "op-2", GateID: "gate-1", OutputThrough: 5})
	if driver.state.Phase() != PhaseFinalizing {
		t.Fatalf("interruption while finalizing is a self-transition, got %s", driver.state.Phase())
	}
	driver.mark(5, 100)
	driver.envoy(OperationFinalized{OperationID: "op-2", CWD: "/w", Reason: "recording-end", OutputStart: 5, OutputThrough: 5, InspectionResults: []InspectionResult{}})
}

func TestFinalizeRules(t *testing.T) {
	driver := newSessionDriver(t)

	// An operation still in Starting is never finalized.
	driver.controller(realtimeExecute("op-1", 0))
	err := driver.tryController(Finalize{OperationID: "op-1", Reason: "recording-end"})
	mustCode(t, err, "out-of-state")

	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	driver.controller(Finalize{OperationID: "op-1", Reason: "recording-end"})
	driver.mark(4, 20)
	driver.envoy(OperationFinalized{OperationID: "op-1", CWD: "/w", Reason: "recording-end", OutputStart: 0, OutputThrough: 4, InspectionResults: []InspectionResult{}})
	if driver.state.Phase() != PhaseIdle {
		t.Fatalf("expected idle, got %s", driver.state.Phase())
	}
}

func TestPreStartRules(t *testing.T) {
	driver := newSessionDriver(t)

	// A pre-start cancellation reports an empty range and no status.
	driver.controller(realtimeExecute("op-1", 0))
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})
	driver.mark(0, 10)
	err := driver.tryEnvoy(OperationCancelled{OperationID: "op-1", Status: intPointer(130), CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0})
	mustCode(t, err, "invalid-field")
	driver.envoy(OperationCancelled{OperationID: "op-1", CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0})

	// A started cancellation carries the returned status.
	driver.startRealtime("op-2", 20)
	driver.controller(Cancel{OperationID: "op-2", Reason: "deadline"})
	driver.mark(0, 30)
	err = driver.tryEnvoy(OperationCancelled{OperationID: "op-2", CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0})
	mustCode(t, err, "invalid-field")
	driver.envoy(OperationCancelled{OperationID: "op-2", Status: intPointer(130), CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0})

	// A pre-start failure carries one of the pre-start codes and an empty
	// range; a started operation cannot use them.
	driver.controller(realtimeExecute("op-3", 0))
	driver.mark(0, 40)
	err = driver.tryEnvoy(OperationFailed{OperationID: "op-3", Code: "cancel-timeout", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0, ShellEnded: boolPointer(true)})
	mustCode(t, err, "out-of-state")
	driver.envoy(OperationFailed{OperationID: "op-3", Code: "input-barrier-timeout", Message: "the watermark was not reached", CWD: "/w", OutputStart: 0, OutputThrough: 0})
}

func TestShellEndedDrainDiscardsRequests(t *testing.T) {
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)
	driver.mark(0, 20)
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 7, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}, ShellEnded: boolPointer(true)})

	// Requests in flight when the shell ends are accepted and discarded;
	// no later operation starts.
	driver.controller(realtimeExecute("op-2", 0))
	driver.controller(Resize{Columns: 90, Rows: 30})
	driver.controller(Shutdown{Reason: "recording-complete"})
	if driver.state.Phase() != PhaseIdle {
		t.Fatalf("discarded requests must not change state, got %s", driver.state.Phase())
	}

	driver.mark(0, 30)
	driver.envoy(Draining{Reason: "shell_ended", OutputThrough: 0})
	driver.mark(0, 40)
	driver.envoy(Closed{Reason: "shell_ended", OutputThrough: 0})
	if driver.state.Phase() != PhaseClosed {
		t.Fatalf("expected closed, got %s", driver.state.Phase())
	}
}

func TestShellEndedGateFailsUnresolved(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(Execute{
		OperationID:     "op-1",
		Source:          "./serve",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationSuppress, Observation: ObservationExclusive},
		Inspections:     []InspectionSpec{},
		InputThrough:    0,
	})
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	driver.mark(2, 20)
	driver.envoy(OperationReady{OperationID: "op-1", GateID: "gate-1", OutputThrough: 2})

	// An unresolved gate cannot complete: the ended shell can no longer
	// resolve it, so the operation fails as unevaluable.
	driver.mark(2, 30)
	err := driver.tryEnvoy(OperationCompleted{OperationID: "op-1", Status: 7, CWD: "/w", OutputStart: 0, OutputThrough: 2, InspectionResults: []InspectionResult{}, ShellEnded: boolPointer(true)})
	mustCode(t, err, "out-of-state")
	driver.envoy(OperationFailed{OperationID: "op-1", Code: "shell-ended-unresolved", Message: "an authored gate was left unevaluable", CWD: "/w", OutputStart: 0, OutputThrough: 2, ShellEnded: boolPointer(true)})
}

func TestInputWatermarkNeverDecreases(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(realtimeExecute("op-1", 10))
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	driver.mark(0, 20)
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}})

	err := driver.tryController(realtimeExecute("op-2", 4))
	mustCode(t, err, "invalid-field")
	driver.controller(realtimeExecute("op-2", 10))
}

func TestBoundaryMarkAndOrderRules(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(realtimeExecute("op-1", 0))

	// A range event without its covering mark immediately before fails.
	err := driver.tryEnvoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	mustCode(t, err, "missing-boundary-mark")

	// The covering mark must name the exact boundary offset.
	driver.mark(0, 10)
	err = driver.tryEnvoy(OperationStarted{OperationID: "op-1", OutputStart: 5})
	mustCode(t, err, "missing-boundary-mark")

	driver.mark(5, 20)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 5})

	// Marks never regress in offset or elapsed_us.
	err = driver.tryEnvoy(OutputMark{Offset: 3, Stream: "pty", ElapsedUS: 30})
	mustCode(t, err, "invalid-output-order")
	err = driver.tryEnvoy(OutputMark{Offset: 9, Stream: "pty", ElapsedUS: 4})
	mustCode(t, err, "invalid-output-order")

	// A terminal result repeats the operation's original start.
	driver.mark(9, 40)
	err = driver.tryEnvoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 6, OutputThrough: 9, InspectionResults: []InspectionResult{}})
	mustCode(t, err, "invalid-output-range")
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 5, OutputThrough: 9, InspectionResults: []InspectionResult{}})
}

func TestResizeRules(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(Resize{Columns: 100, Rows: 30})

	// Only one resize may be outstanding, and shutdown waits for it.
	err := driver.tryController(Resize{Columns: 90, Rows: 30})
	mustCode(t, err, "out-of-state")
	err = driver.tryController(Shutdown{Reason: "end"})
	mustCode(t, err, "out-of-state")

	// The acknowledgement repeats the exact dimensions.
	driver.mark(0, 10)
	err = driver.tryEnvoy(ResizeApplied{Columns: 90, Rows: 30, ElapsedUS: 20, OutputThrough: 0})
	mustCode(t, err, "out-of-state")
	driver.envoy(ResizeApplied{Columns: 100, Rows: 30, ElapsedUS: 20, OutputThrough: 0})

	// A requested shutdown drains under its own reason.
	driver.controller(Shutdown{Reason: "recording-complete"})
	driver.mark(0, 30)
	driver.envoy(Draining{Reason: "recording-complete", OutputThrough: 0})
	driver.mark(0, 40)
	driver.envoy(Closed{Reason: "recording-complete", OutputThrough: 0})
}

func TestDrainingResolvesOutstandingResize(t *testing.T) {
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)
	driver.controller(Resize{Columns: 100, Rows: 30})
	driver.mark(0, 20)
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 7, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}, ShellEnded: boolPointer(true)})
	driver.mark(0, 30)
	driver.envoy(Draining{Reason: "shell_ended", OutputThrough: 0})
	// The outstanding resize was resolved without a resize_applied event.
	driver.mark(0, 40)
	driver.envoy(Closed{Reason: "shell_ended", OutputThrough: 0})
}

func TestInspectionResultsMatchRequestOrder(t *testing.T) {
	driver := newSessionDriver(t)
	specs := []InspectionSpec{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "out.txt"},
		{InspectionID: "inspection-2", Kind: InspectionProduces, Path: "build", ProducerID: "build-step", OutputID: "bundle"},
	}
	driver.controller(Execute{
		OperationID:     "op-1",
		Source:          "make",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReal, Observation: ObservationExclusive},
		Inspections:     specs,
		InputThrough:    0,
	})
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})

	driver.mark(6, 20)
	incomplete := []InspectionResult{{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/w/out.txt", PathKind: PathKindFile}}
	err := driver.tryEnvoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 6, InspectionResults: incomplete})
	mustCode(t, err, "inspection-mismatch")

	complete := append(incomplete, InspectionResult{
		InspectionID:    "inspection-2",
		Kind:            InspectionProduces,
		ResolvedPath:    "/w/build",
		PathKind:        PathKindDirectory,
		ProducerID:      "build-step",
		OutputID:        "bundle",
		SHA256:          FileDigest([]byte("x")),
		DigestAlgorithm: DigestDirectoryV2,
	})
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 6, InspectionResults: complete})
}

func TestMarkBudgetIsSessionScoped(t *testing.T) {
	driver := newSessionDriver(t)
	driver.state.markCount = MaxOutputMarksPerSession
	err := driver.tryEnvoy(OutputMark{Offset: 0, Stream: "pty", ElapsedUS: 1})
	mustCode(t, err, "mark-budget-exhausted")
}

func TestCrossedCancelAcrossStartCommitPoint(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(realtimeExecute("op-1", 0))
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})

	// A cancel queued through the non-interruptible submission
	// transaction accepts the crossed operation_started without leaving
	// Cancelling; the later terminal result resolves the request.
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	if driver.state.Phase() != PhaseCancelling {
		t.Fatalf("the crossed start must not leave cancelling, got %s", driver.state.Phase())
	}
	driver.mark(3, 20)
	driver.envoy(OperationCancelled{OperationID: "op-1", Status: intPointer(130), CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 3})
	if driver.state.Phase() != PhaseIdle {
		t.Fatalf("expected idle, got %s", driver.state.Phase())
	}
}

func TestCompletionWinsCrossedCancel(t *testing.T) {
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})

	// A completion the Envoy committed against the crossed cancel is
	// authoritative, keeps its real exit status, and discards the
	// request.
	driver.mark(4, 20)
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 4, InspectionResults: []InspectionResult{}})
	if driver.state.Phase() != PhaseIdle {
		t.Fatalf("expected idle, got %s", driver.state.Phase())
	}
}

func TestFinalizationEvidenceRules(t *testing.T) {
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)
	driver.controller(Finalize{OperationID: "op-1", Reason: "recording-end"})

	// The finalization result repeats the exact requested reason.
	driver.mark(0, 20)
	err := driver.tryEnvoy(OperationFinalized{OperationID: "op-1", CWD: "/w", Reason: "other", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}})
	mustCode(t, err, "finalization-reason-mismatch")
	driver.envoy(OperationFinalized{OperationID: "op-1", CWD: "/w", Reason: "recording-end", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}})
	if err := driver.state.AcceptEnvoy(OperationFinalized{}); err == nil {
		t.Fatal("finalized without a live request must fail")
	}
}

func TestShellEndedDrainSupersedesUnstartedExecute(t *testing.T) {
	// The shell dies while an execute waits at the input barrier: the
	// Envoy-initiated drain supersedes the crossed unstarted execute and
	// its deadline-derived cancel without a terminal operation result.
	driver := newSessionDriver(t)
	driver.controller(realtimeExecute("op-1", 0))
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})
	driver.mark(0, 10)
	driver.envoy(Draining{Reason: "shell_ended", OutputThrough: 0})
	if driver.state.Phase() != PhaseDraining {
		t.Fatalf("expected draining, got %s", driver.state.Phase())
	}
	if driver.state.CurrentStream() != "pty" {
		t.Fatalf("boundary-only marks keep the initial pty stream, got %s", driver.state.CurrentStream())
	}
	driver.mark(0, 20)
	driver.envoy(Closed{Reason: "shell_ended", OutputThrough: 0})

	// A shell death observed first also resolves a crossed shutdown.
	crossed := newSessionDriver(t)
	crossed.controller(Shutdown{Reason: "recording-complete"})
	crossed.mark(0, 10)
	crossed.envoy(Draining{Reason: "shell_ended", OutputThrough: 0})
	if crossed.state.Phase() != PhaseDraining {
		t.Fatalf("expected draining, got %s", crossed.state.Phase())
	}
}

func TestShellEndWithNothingUnevaluableCompletes(t *testing.T) {
	// A running operation with no declared inspections and no unresolved
	// gate keeps its reaped status: the shell end completes it, and
	// shell-ended-unresolved is rejected so real result evidence cannot
	// be discarded as a failure.
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)
	driver.mark(0, 20)
	err := driver.tryEnvoy(OperationFailed{OperationID: "op-1", Code: "shell-ended-unresolved", Message: "nothing was unevaluable", CWD: "/w", OutputStart: 0, OutputThrough: 0, ShellEnded: boolPointer(true)})
	mustCode(t, err, "out-of-state")
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 7, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}, ShellEnded: boolPointer(true)})
}

func TestMarkStreamMatchesExecutionShape(t *testing.T) {
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)

	// Selecting a split-only stream at the same offset attributes
	// nothing and is legal; advancing the offset under that attribution
	// during a PTY operation is not.
	driver.envoy(OutputMark{Offset: 0, Stream: "stdout", ElapsedUS: 20})
	err := driver.tryEnvoy(OutputMark{Offset: 9, Stream: "pty", ElapsedUS: 30})
	mustCode(t, err, "invalid-mark-stream")

	// Reselecting pty at the same offset makes the advance legal again.
	driver.envoy(OutputMark{Offset: 0, Stream: "pty", ElapsedUS: 40})
	driver.envoy(OutputMark{Offset: 9, Stream: "pty", ElapsedUS: 50})
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 9, InspectionResults: []InspectionResult{}})
}

func TestActiveOnlyFailureCodesRequireAStartedOperation(t *testing.T) {
	// A cancel moves an unstarted operation to Cancelling; codes that
	// require an active adapter operation must not fabricate a pre-start
	// empty range there.
	driver := newSessionDriver(t)
	driver.controller(Execute{
		OperationID:     "op-1",
		Source:          "make",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReal, Observation: ObservationExclusive},
		Inspections:     []InspectionSpec{{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "out.txt"}},
		InputThrough:    0,
	})
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})
	driver.mark(0, 10)
	err := driver.tryEnvoy(OperationFailed{OperationID: "op-1", Code: "inspection-read", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0})
	mustCode(t, err, "out-of-state")
	err = driver.tryEnvoy(OperationFailed{OperationID: "op-1", Code: "cancel-timeout", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0, ShellEnded: boolPointer(true)})
	mustCode(t, err, "out-of-state")
	err = driver.tryEnvoy(OperationFailed{OperationID: "op-1", Code: "shell-ended-unresolved", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0, ShellEnded: boolPointer(true)})
	mustCode(t, err, "out-of-state")
	// The true pre-start outcome remains available.
	driver.envoy(OperationCancelled{OperationID: "op-1", CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0})
}

func TestZeroValueSessionStateKeepsImplicitPTYStream(t *testing.T) {
	var state SessionState
	if state.CurrentStream() != "pty" {
		t.Fatalf("zero-value current stream must be pty, got %q", state.CurrentStream())
	}
	if err := state.AcceptController(Hello{Seq: 1, SessionID: "9f3c7a1e5b2d48c6a0e4f18d73b9c25a"}); err != nil {
		t.Fatalf("hello: %v", err)
	}
	if err := state.AcceptEnvoy(Ready{Seq: 1, EnvoyPID: 2, ShellPID: 3, CWD: "/w", Columns: 80, Rows: 24}); err != nil {
		t.Fatalf("ready: %v", err)
	}
	if err := state.AcceptEnvoy(OutputMark{Seq: 2, Offset: 0, Stream: "pty", ElapsedUS: 5}); err != nil {
		t.Fatalf("first mark: %v", err)
	}
	// Prompt output before the first operation legitimately advances
	// from offset zero under the implicit pty stream.
	if err := state.AcceptEnvoy(OutputMark{Seq: 3, Offset: 6, Stream: "pty", ElapsedUS: 9}); err != nil {
		t.Fatalf("advancing mark: %v", err)
	}
}

func TestRequestedDrainRejectsLateRequests(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(Shutdown{Reason: "recording-complete"})
	driver.mark(0, 10)
	driver.envoy(Draining{Reason: "recording-complete", OutputThrough: 0})

	// After a controller-requested shutdown drain the crossing exception
	// does not apply: later requests are out of state, not discarded.
	err := driver.tryController(realtimeExecute("op-1", 0))
	mustCode(t, err, "out-of-state")
	err = driver.tryController(Resize{Columns: 90, Rows: 30})
	mustCode(t, err, "out-of-state")
	err = driver.tryController(Shutdown{Reason: "again"})
	mustCode(t, err, "out-of-state")
}

func TestCrossedContinueStillAdvancesWatermark(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(Execute{
		OperationID:     "op-1",
		Source:          "./serve",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationSuppress, Observation: ObservationExclusive},
		Inspections:     []InspectionSpec{},
		InputThrough:    0,
	})
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	driver.mark(0, 20)
	driver.envoy(OperationReady{OperationID: "op-1", GateID: "gate-1", OutputThrough: 0})
	driver.mark(0, 30)
	driver.envoy(OperationGateInterrupted{OperationID: "op-1", GateID: "gate-1", OutputThrough: 0})

	// The discarded crossed continue communicated 25 written bytes; the
	// watermark never decreases below a communicated count.
	driver.controller(Continue{OperationID: "op-1", GateID: "gate-1", InputThrough: 25})
	driver.mark(0, 40)
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}})
	err := driver.tryController(realtimeExecute("op-2", 10))
	mustCode(t, err, "invalid-field")
	driver.controller(realtimeExecute("op-2", 25))
}

func TestPreStartResultsRepeatLastReportedCWD(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(realtimeExecute("op-1", 0))
	driver.controller(Cancel{OperationID: "op-1", Reason: "deadline"})
	driver.mark(0, 10)
	// ready reported /w; a pre-start result reporting another cwd fails.
	err := driver.tryEnvoy(OperationCancelled{OperationID: "op-1", CWD: "/elsewhere", Reason: "deadline", OutputStart: 0, OutputThrough: 0})
	mustCode(t, err, "invalid-field")
	driver.envoy(OperationCancelled{OperationID: "op-1", CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0})

	// A completed result moves the reported cwd, and the next pre-start
	// failure must repeat the new value.
	driver.startRealtime("op-2", 20)
	driver.mark(0, 30)
	driver.envoy(OperationCompleted{OperationID: "op-2", Status: 0, CWD: "/w/sub", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}})
	driver.controller(realtimeExecute("op-3", 25))
	driver.mark(0, 40)
	err = driver.tryEnvoy(OperationFailed{OperationID: "op-3", Code: "source-unsupported", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0})
	mustCode(t, err, "invalid-field")
	driver.envoy(OperationFailed{OperationID: "op-3", Code: "source-unsupported", Message: "m", CWD: "/w/sub", OutputStart: 0, OutputThrough: 0})
}

func TestInspectionFailuresReportASurvivingShell(t *testing.T) {
	driver := newSessionDriver(t)
	driver.controller(Execute{
		OperationID:     "op-1",
		Source:          "make",
		ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReal, Observation: ObservationExclusive},
		Inspections:     []InspectionSpec{{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "out.txt"}},
		InputThrough:    0,
	})
	driver.mark(0, 10)
	driver.envoy(OperationStarted{OperationID: "op-1", OutputStart: 0})
	driver.mark(0, 20)
	// A shell exit with declared inspections is shell-ended-unresolved,
	// never an inspection code carrying shell_ended.
	err := driver.tryEnvoy(OperationFailed{OperationID: "op-1", Code: "inspection-read", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0, ShellEnded: boolPointer(true)})
	mustCode(t, err, "invalid-field")
	driver.envoy(OperationFailed{OperationID: "op-1", Code: "shell-ended-unresolved", Message: "declared inspections became unevaluable", CWD: "/w", OutputStart: 0, OutputThrough: 0, ShellEnded: boolPointer(true)})
}

func TestInspectionCodesRequireDeclaredInspections(t *testing.T) {
	driver := newSessionDriver(t)
	driver.startRealtime("op-1", 10)
	driver.mark(0, 20)
	err := driver.tryEnvoy(OperationFailed{OperationID: "op-1", Code: "inspection-read", Message: "m", CWD: "/w", OutputStart: 0, OutputThrough: 0})
	mustCode(t, err, "out-of-state")
	driver.envoy(OperationCompleted{OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}})
}
