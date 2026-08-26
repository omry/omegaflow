package protocol

import "fmt"

// Phase is the validated lifecycle phase of one controller/Envoy session,
// modeled from the Envoy's serialized acceptance order.
type Phase string

const (
	PhaseInitial      Phase = "initial"
	PhaseHelloSent    Phase = "hello-sent"
	PhaseIdle         Phase = "idle"
	PhaseStarting     Phase = "starting"
	PhaseRunning      Phase = "running"
	PhaseGated        Phase = "gated"
	PhaseContinuing   Phase = "continuing"
	PhaseCancelling   Phase = "cancelling"
	PhaseFinalizing   Phase = "finalizing"
	PhaseShutdownSent Phase = "shutdown-sent"
	PhaseDraining     Phase = "draining"
	PhaseClosed       Phase = "closed"
)

type terminalSize struct {
	columns int
	rows    int
}

// SessionState validates both directions of the v1 lifecycle from the
// Envoy's serialized acceptance order, including the crossing families the
// contract accepts and discards. Its zero value is an initial session.
// Callers must serialize access. A rejected message is a fatal protocol
// result; reusing the state after rejection is unsupported.
type SessionState struct {
	phase Phase

	// expectedSessionID, when set, is the Envoy's trusted --session-id
	// value: hello must match it exactly before Bash is created.
	expectedSessionID string

	controllerSeq uint64
	envoySeq      uint64

	// Terminal-input barrier watermark: never decreases across a session.
	inputWatermark uint64

	operationID      string
	policy           ExecutionPolicy
	inspections      []InspectionSpec
	operationStarted bool
	outputStart      uint64
	hasStart         bool
	outputEnd        uint64

	currentGate      string
	usedGateIDs      map[string]bool
	interruptedGates map[string]bool

	cancelPending   bool
	cancelReason    string
	finalizePending bool
	finalizeReason  string

	pendingResize  *terminalSize
	shutdownReason string

	// lastTerminalOp is the most recently completed operation: a cancel or
	// finalize naming it is accepted and discarded until the next execute
	// supersedes it.
	lastTerminalOp string

	// mustDrain is set by any terminal event carrying shell_ended: no
	// later operation starts and the Envoy-initiated shell_ended drain is
	// the only forward path.
	mustDrain    bool
	drainReason  string
	drainStarted bool

	// Output-mark stream state.
	markCount      uint64
	lastMarkOffset uint64
	lastElapsedUS  uint64
	currentStream  string
	prevEnvoyMark  bool
}

// NewSessionState returns an initial session validator that accepts any
// well-formed handshake session_id.
func NewSessionState() *SessionState {
	return &SessionState{
		phase:            PhaseInitial,
		usedGateIDs:      make(map[string]bool),
		interruptedGates: make(map[string]bool),
		currentStream:    "pty",
	}
}

// NewSessionStateForSession returns an initial session validator that
// requires hello.session_id to equal the Envoy's trusted --session-id value.
func NewSessionStateForSession(sessionID string) (*SessionState, error) {
	if err := validateSessionID(sessionID); err != nil {
		return nil, err
	}
	state := NewSessionState()
	state.expectedSessionID = sessionID
	return state, nil
}

// Phase returns the current lifecycle phase.
func (state *SessionState) Phase() Phase {
	if state.phase == "" {
		return PhaseInitial
	}
	return state.phase
}

// OutputThrough returns the largest accepted exclusive output offset.
func (state *SessionState) OutputThrough() uint64 { return state.outputEnd }

// CurrentStream returns the mark stream currently attributing raw output.
// Before any output source has selected a stream it is pty — including for
// the documented zero value; this initialization does not emit a mark at
// ready.
func (state *SessionState) CurrentStream() string {
	if state.currentStream == "" {
		return "pty"
	}
	return state.currentStream
}

// AcceptController validates and applies one controller request in the
// Envoy's acceptance order. A request the contract accepts and discards —
// one that crossed its operation's terminal result, an interrupted gate's
// continue, or any request after an Envoy-initiated drain began — returns
// nil without changing lifecycle state.
func (state *SessionState) AcceptController(message any) (err error) {
	if err = validateTelemetry(message); err != nil {
		return err
	}
	sequence := telemetrySequence(message)
	counter, err := state.checkSequence("controller", sequence)
	if err != nil {
		return err
	}
	defer func() {
		if err == nil {
			*counter = sequence
		}
	}()

	switch value := message.(type) {
	case Hello:
		if err := state.requirePhase(PhaseInitial, message); err != nil {
			return err
		}
		if state.expectedSessionID != "" && value.SessionID != state.expectedSessionID {
			return protocolError("session-mismatch", "hello session_id does not match the trusted launch value")
		}
		state.phase = PhaseHelloSent
	case Resize:
		if state.drainDiscardsRequests() {
			return nil
		}
		if !phaseIn(state.Phase(), PhaseIdle, PhaseStarting, PhaseRunning, PhaseGated, PhaseContinuing) || state.pendingResize != nil {
			return state.outOfState(message)
		}
		state.pendingResize = &terminalSize{columns: value.Columns, rows: value.Rows}
	case Execute:
		if state.drainDiscardsRequests() {
			return nil
		}
		if err := state.requirePhase(PhaseIdle, message); err != nil {
			return err
		}
		if err := state.advanceWatermark(value.InputThrough); err != nil {
			return err
		}
		state.phase = PhaseStarting
		state.operationID = value.OperationID
		state.policy = value.ExecutionPolicy
		state.inspections = value.Inspections
		state.operationStarted = false
		state.hasStart = false
		state.currentGate = ""
		state.usedGateIDs = make(map[string]bool)
		state.interruptedGates = make(map[string]bool)
		state.cancelPending = false
		state.finalizePending = false
		state.lastTerminalOp = ""
	case Continue:
		if state.drainDiscardsRequests() {
			return nil
		}
		if value.OperationID == state.operationID && state.interruptedGates[value.GateID] {
			// A continue that crossed operation_gate_interrupted is
			// satisfied by that event and is discarded here.
			return nil
		}
		if err := state.requirePhase(PhaseGated, message); err != nil {
			return err
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if value.GateID != state.currentGate {
			return state.outOfState(message)
		}
		if err := state.advanceWatermark(value.InputThrough); err != nil {
			return err
		}
		state.phase = PhaseContinuing
	case Cancel:
		if state.drainDiscardsRequests() {
			return nil
		}
		if value.OperationID == state.lastTerminalOp && state.lastTerminalOp != "" {
			return nil
		}
		if !phaseIn(state.Phase(), PhaseStarting, PhaseRunning, PhaseGated, PhaseContinuing, PhaseFinalizing) {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		state.cancelPending = true
		state.cancelReason = value.Reason
		state.phase = PhaseCancelling
	case Finalize:
		if state.drainDiscardsRequests() {
			return nil
		}
		if value.OperationID == state.lastTerminalOp && state.lastTerminalOp != "" {
			return nil
		}
		// An operation still held at the terminal-input barrier has not
		// started and is never finalized; a recording ending there
		// cancels it instead.
		if !phaseIn(state.Phase(), PhaseRunning, PhaseGated, PhaseContinuing) {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		state.finalizePending = true
		state.finalizeReason = value.Reason
		state.phase = PhaseFinalizing
	case Shutdown:
		if state.drainDiscardsRequests() {
			return nil
		}
		if err := state.requirePhase(PhaseIdle, message); err != nil {
			return err
		}
		if state.pendingResize != nil {
			return state.outOfState(message)
		}
		state.shutdownReason = value.Reason
		state.phase = PhaseShutdownSent
	default:
		return protocolError("unsupported-message", fmt.Sprintf("unsupported controller model %T", message))
	}
	return nil
}

// AcceptEnvoy validates and applies one Envoy event.
func (state *SessionState) AcceptEnvoy(message any) (err error) {
	if err = validateTelemetry(message); err != nil {
		return err
	}
	sequence := telemetrySequence(message)
	counter, err := state.checkSequence("envoy", sequence)
	if err != nil {
		return err
	}
	wasMark := false
	defer func() {
		if err == nil {
			*counter = sequence
			state.prevEnvoyMark = wasMark
		}
	}()

	switch value := message.(type) {
	case Ready:
		if err := state.requirePhase(PhaseHelloSent, message); err != nil {
			return err
		}
		state.phase = PhaseIdle
	case OutputMark:
		if phaseIn(state.Phase(), PhaseInitial, PhaseHelloSent, PhaseClosed) {
			return state.outOfState(message)
		}
		if state.markCount == MaxOutputMarksPerSession {
			return protocolError("mark-budget-exhausted", "the session output-mark budget is exhausted")
		}
		if value.Offset < state.lastMarkOffset {
			return protocolError("invalid-output-order", "mark offset regressed")
		}
		if value.ElapsedUS < state.lastElapsedUS {
			return protocolError("invalid-output-order", "mark elapsed_us regressed")
		}
		// An offset-advancing mark attributes the new bytes to the
		// current stream. Outside an open split operation every byte is
		// read from the PTY master, so only pty attribution is legal;
		// equal-offset marks attribute nothing and may reselect or
		// repeat any stream.
		splitOpen := state.operationStarted && state.policy.ExecutionShape == ExecutionSplit
		if value.Offset > state.lastMarkOffset && !splitOpen && state.CurrentStream() != "pty" {
			return protocolError("invalid-mark-stream", "non-pty attribution is legal only while a split operation is open")
		}
		state.markCount++
		state.lastMarkOffset = value.Offset
		state.lastElapsedUS = value.ElapsedUS
		state.currentStream = value.Stream
		wasMark = true
	case ResizeApplied:
		if state.pendingResize == nil || *state.pendingResize != (terminalSize{columns: value.Columns, rows: value.Rows}) {
			return state.outOfState(message)
		}
		if value.ElapsedUS < state.lastElapsedUS {
			return protocolError("invalid-output-order", "resize elapsed_us regressed")
		}
		if err := state.closeBarrier(value.OutputThrough); err != nil {
			return err
		}
		state.lastElapsedUS = value.ElapsedUS
		state.pendingResize = nil
	case OperationStarted:
		// A cancel accepted after the start commit point is queued
		// through the non-interruptible submission transaction: the
		// crossed operation_started is accepted without leaving
		// Cancelling, and the later terminal result resolves the
		// request.
		crossedCancel := state.Phase() == PhaseCancelling && state.cancelPending && !state.operationStarted
		if state.Phase() != PhaseStarting && !crossedCancel {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if value.OutputStart < state.outputEnd {
			return protocolError("invalid-output-order", "operation start precedes accepted output")
		}
		if err := state.requireBoundaryMark(value.OutputStart); err != nil {
			return err
		}
		state.outputStart = value.OutputStart
		state.hasStart = true
		state.operationStarted = true
		state.outputEnd = value.OutputStart
		if !crossedCancel {
			state.phase = PhaseRunning
		}
	case OperationReady:
		if err := state.requirePhase(PhaseRunning, message); err != nil {
			return err
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if state.usedGateIDs[value.GateID] {
			return protocolError("reused-gate", "gate id was already used")
		}
		if err := state.closeBarrier(value.OutputThrough); err != nil {
			return err
		}
		state.usedGateIDs[value.GateID] = true
		state.currentGate = value.GateID
		state.phase = PhaseGated
	case OperationContinued:
		if err := state.requirePhase(PhaseContinuing, message); err != nil {
			return err
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if value.GateID != state.currentGate {
			return state.outOfState(message)
		}
		if err := state.closeBarrier(value.OutputThrough); err != nil {
			return err
		}
		state.currentGate = ""
		state.phase = PhaseRunning
	case OperationGateInterrupted:
		// Terminal Ctrl-C reaching a waiting gate helper. From Gated it
		// reopens the running operation; from Continuing it resolves a
		// crossed continue; while a lifecycle request is live it is a
		// legal self-transition and that request stays live.
		if !phaseIn(state.Phase(), PhaseGated, PhaseContinuing, PhaseCancelling, PhaseFinalizing) {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if state.currentGate == "" || value.GateID != state.currentGate {
			return state.outOfState(message)
		}
		if err := state.closeBarrier(value.OutputThrough); err != nil {
			return err
		}
		state.interruptedGates[value.GateID] = true
		state.currentGate = ""
		if phaseIn(state.Phase(), PhaseGated, PhaseContinuing) {
			state.phase = PhaseRunning
		}
	case OperationCompleted:
		return state.acceptCompleted(value)
	case OperationCancelled:
		return state.acceptCancelled(value)
	case OperationFinalized:
		return state.acceptFinalized(value)
	case OperationFailed:
		return state.acceptFailed(value)
	case Diagnostic:
		if phaseIn(state.Phase(), PhaseInitial, PhaseClosed) {
			return state.outOfState(message)
		}
		if value.OperationID != nil && *value.OperationID != state.operationID && *value.OperationID != state.lastTerminalOp {
			return protocolError("wrong-operation", "diagnostic names an unknown operation")
		}
	case Draining:
		return state.acceptDraining(value)
	case Closed:
		if err := state.requirePhase(PhaseDraining, message); err != nil {
			return err
		}
		if value.Reason != state.drainReason {
			return protocolError("shutdown-reason-mismatch", "closed reason does not match the drain reason")
		}
		if err := state.closeBarrier(value.OutputThrough); err != nil {
			return err
		}
		state.phase = PhaseClosed
	default:
		return protocolError("unsupported-message", fmt.Sprintf("unsupported Envoy model %T", message))
	}
	return nil
}

func (state *SessionState) acceptCompleted(value OperationCompleted) error {
	shellEnded := value.ShellEnded != nil
	switch state.Phase() {
	case PhaseRunning:
	case PhaseCancelling, PhaseFinalizing:
		// The observed result wins: a completion the Envoy committed
		// against a crossed cancel or finalize is authoritative, keeps
		// its real exit status, and discards the crossed request.
	default:
		return state.outOfState(value)
	}
	if !state.operationStarted {
		return state.outOfState(value)
	}
	if state.currentGate != "" {
		return protocolError("out-of-state", "an unresolved gate cannot complete; it fails as unevaluable")
	}
	if shellEnded && len(state.inspections) > 0 {
		return protocolError("out-of-state", "declared inspections cannot be evaluated after the shell ended")
	}
	if err := ValidateInspectionResultsAgainstSpecs(state.inspections, value.InspectionResults); err != nil {
		return err
	}
	return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough, shellEnded)
}

func (state *SessionState) acceptCancelled(value OperationCancelled) error {
	if err := state.requirePhase(PhaseCancelling, value); err != nil {
		return err
	}
	if value.Reason != state.cancelReason || !state.cancelPending {
		return protocolError("cancellation-reason-mismatch", "cancellation reason does not match request")
	}
	if state.operationStarted {
		if value.Status == nil {
			return protocolError("invalid-field", "a started cancellation carries the returned status")
		}
	} else {
		// True pre-start cancellation: an empty range at the current
		// offset and no status, because no shell ran.
		if value.Status != nil {
			return protocolError("invalid-field", "a pre-start cancellation carries no status")
		}
		if value.OutputStart != value.OutputThrough {
			return protocolError("invalid-output-range", "a pre-start cancellation reports an empty range")
		}
		if value.OutputStart < state.outputEnd {
			return protocolError("invalid-output-order", "a pre-start range precedes accepted output")
		}
		state.outputStart = value.OutputStart
		state.hasStart = true
	}
	return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough, false)
}

func (state *SessionState) acceptFinalized(value OperationFinalized) error {
	// Finalization commits from Finalizing, or from Cancelling when the
	// inspection worker's result was accepted before the crossed cancel.
	if !phaseIn(state.Phase(), PhaseFinalizing, PhaseCancelling) {
		return state.outOfState(value)
	}
	if !state.finalizePending || value.Reason != state.finalizeReason {
		return protocolError("finalization-reason-mismatch", "finalization reason does not match request")
	}
	if !state.operationStarted {
		return state.outOfState(value)
	}
	if err := ValidateInspectionResultsAgainstSpecs(state.inspections, value.InspectionResults); err != nil {
		return err
	}
	return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough, false)
}

func (state *SessionState) acceptFailed(value OperationFailed) error {
	shellEnded := value.ShellEnded != nil
	phase := state.Phase()
	switch value.Code {
	case "source-invalid", "source-unsupported", "input-barrier-timeout":
		if phase != PhaseStarting && !(phase == PhaseCancelling && !state.operationStarted) {
			return state.outOfState(value)
		}
	case "cancel-timeout":
		if phase != PhaseCancelling || !shellEnded {
			return state.outOfState(value)
		}
	case "finalize-timeout":
		if phase != PhaseFinalizing || !shellEnded {
			return state.outOfState(value)
		}
	case "shell-ended-unresolved":
		if !shellEnded || !phaseIn(phase, PhaseRunning, PhaseGated, PhaseContinuing, PhaseCancelling, PhaseFinalizing) {
			return state.outOfState(value)
		}
		// A shell end fails the operation only when an authored
		// requirement became unevaluable: declared inspections or an
		// unresolved gate. Otherwise the reaped status completes it.
		if len(state.inspections) == 0 && state.currentGate == "" {
			return protocolError("out-of-state", "a shell end with nothing unevaluable completes with its reaped status")
		}
	default:
		// The six inspection codes resolve after the adapter result,
		// while the operation's public phase is still running or a
		// lifecycle request is live.
		if !phaseIn(phase, PhaseRunning, PhaseCancelling, PhaseFinalizing) {
			return state.outOfState(value)
		}
	}
	if !state.operationStarted {
		// Only the pre-start codes may close an unstarted operation;
		// every other code requires an active adapter operation.
		switch value.Code {
		case "source-invalid", "source-unsupported", "input-barrier-timeout":
		default:
			return protocolError("out-of-state", "this failure code requires a started operation")
		}
		// A pre-start failure has no snapshot: both offsets equal the
		// offset observed at the failure, an empty range that never
		// regresses.
		if value.OutputStart != value.OutputThrough {
			return protocolError("invalid-output-range", "a pre-start failure reports an empty range")
		}
		if value.OutputStart < state.outputEnd {
			return protocolError("invalid-output-order", "a pre-start range precedes accepted output")
		}
		state.outputStart = value.OutputStart
		state.hasStart = true
	}
	return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough, shellEnded)
}

func (state *SessionState) acceptDraining(value Draining) error {
	switch state.Phase() {
	case PhaseShutdownSent:
		if value.Reason != state.shutdownReason && value.Reason != "shell_ended" {
			return protocolError("shutdown-reason-mismatch", "draining reason does not match request")
		}
	case PhaseIdle:
		// Envoy-initiated drain: after a shell_ended terminal result, or
		// a workload killed between operations.
		if value.Reason != "shell_ended" {
			return state.outOfState(value)
		}
	case PhaseStarting, PhaseCancelling:
		// Draining supersedes a crossed unstarted execute and its
		// deadline-derived cancel; the planned beat fails as unrunnable
		// without a terminal operation result.
		if value.Reason != "shell_ended" || state.operationStarted {
			return state.outOfState(value)
		}
		state.clearOperation()
	default:
		return state.outOfState(value)
	}
	if err := state.closeBarrier(value.OutputThrough); err != nil {
		return err
	}
	// Receipt of draining resolves a controller's outstanding resize; the
	// discarded request produces no resize event.
	state.pendingResize = nil
	state.drainReason = value.Reason
	state.drainStarted = true
	state.phase = PhaseDraining
	return nil
}

func telemetrySequence(message any) uint64 {
	switch value := message.(type) {
	case Hello:
		return value.Seq
	case Execute:
		return value.Seq
	case Continue:
		return value.Seq
	case Cancel:
		return value.Seq
	case Finalize:
		return value.Seq
	case Resize:
		return value.Seq
	case Shutdown:
		return value.Seq
	case Ready:
		return value.Seq
	case OperationStarted:
		return value.Seq
	case OperationReady:
		return value.Seq
	case OperationContinued:
		return value.Seq
	case OperationGateInterrupted:
		return value.Seq
	case OutputMark:
		return value.Seq
	case OperationCompleted:
		return value.Seq
	case OperationCancelled:
		return value.Seq
	case OperationFinalized:
		return value.Seq
	case OperationFailed:
		return value.Seq
	case ResizeApplied:
		return value.Seq
	case Diagnostic:
		return value.Seq
	case Draining:
		return value.Seq
	case Closed:
		return value.Seq
	default:
		return 0
	}
}

func (state *SessionState) checkSequence(direction string, sequence uint64) (*uint64, error) {
	counter := &state.envoySeq
	if direction == "controller" {
		counter = &state.controllerSeq
	}
	if *counter == MaxSequence || sequence != *counter+1 {
		return nil, protocolError("invalid-sequence", fmt.Sprintf("%s sequence is not consecutive", direction))
	}
	return counter, nil
}

func (state *SessionState) advanceWatermark(value uint64) error {
	if value < state.inputWatermark {
		return protocolError("invalid-field", "input_through regressed below the previous watermark")
	}
	state.inputWatermark = value
	return nil
}

// drainDiscardsRequests reports whether a controller request must be
// accepted and discarded because an Envoy-initiated shell_ended drain has
// begun or is required: any request already in flight when the shell ends is
// discarded exactly like one that crossed its own terminal result.
func (state *SessionState) drainDiscardsRequests() bool {
	// The crossing exception covers only the Envoy-initiated shell_ended
	// drain; requests during a controller-requested shutdown drain remain
	// out of state.
	if state.Phase() == PhaseDraining && state.drainReason == "shell_ended" {
		return true
	}
	return state.mustDrain
}

// closeBarrier validates one output_through boundary: the covering mark was
// emitted immediately before the event, and offsets never regress.
func (state *SessionState) closeBarrier(offset uint64) error {
	if err := state.requireBoundaryMark(offset); err != nil {
		return err
	}
	if offset < state.outputEnd || (state.hasStart && offset < state.outputStart) {
		return protocolError("invalid-output-order", "output offset regressed")
	}
	state.outputEnd = offset
	return nil
}

func (state *SessionState) requireBoundaryMark(offset uint64) error {
	if !state.prevEnvoyMark || state.lastMarkOffset != offset {
		return protocolError("missing-boundary-mark", "a covering output mark must immediately precede a range boundary")
	}
	return nil
}

func (state *SessionState) finishOperation(operationID string, start, through uint64, shellEnded bool) error {
	if err := state.requireOperation(operationID); err != nil {
		return err
	}
	if !state.hasStart || start != state.outputStart {
		return protocolError("invalid-output-range", "a terminal result must repeat the operation's original start")
	}
	if err := state.closeBarrier(through); err != nil {
		return err
	}
	state.lastTerminalOp = state.operationID
	state.clearOperation()
	if shellEnded {
		// No prompt is synthesized and no later operation starts; the
		// Envoy-initiated shell_ended drain follows.
		state.mustDrain = true
	}
	state.phase = PhaseIdle
	return nil
}

func (state *SessionState) clearOperation() {
	state.operationID = ""
	state.policy = ExecutionPolicy{}
	state.inspections = nil
	state.operationStarted = false
	state.currentGate = ""
	state.outputStart = 0
	state.hasStart = false
	state.usedGateIDs = make(map[string]bool)
	state.interruptedGates = make(map[string]bool)
	state.cancelPending = false
	state.finalizePending = false
	state.cancelReason = ""
	state.finalizeReason = ""
}

func (state *SessionState) requirePhase(expected Phase, message any) error {
	if state.Phase() != expected {
		return state.outOfState(message)
	}
	return nil
}

func (state *SessionState) requireOperation(operationID string) error {
	if state.operationID != operationID || state.operationID == "" {
		return protocolError("wrong-operation", "operation id does not match")
	}
	return nil
}

func (state *SessionState) outOfState(message any) error {
	return protocolError("out-of-state", fmt.Sprintf("%T is invalid while %s", message, state.Phase()))
}

func phaseIn(phase Phase, allowed ...Phase) bool {
	for _, candidate := range allowed {
		if phase == candidate {
			return true
		}
	}
	return false
}
