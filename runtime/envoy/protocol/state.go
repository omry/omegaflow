package protocol

import "fmt"

// Phase is the validated lifecycle phase of one controller/Envoy session.
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

// SessionState validates both directions of the v1 lifecycle. Its zero value
// is an initial session. Callers must serialize access. A rejected message is
// a fatal protocol result; reusing the state after rejection is unsupported.
type SessionState struct {
	phase Phase

	controllerSeq uint64
	envoySeq      uint64

	operationID    string
	executionShape ExecutionShape
	gateID         string
	outputStart    uint64
	hasStart       bool
	outputEnd      uint64

	pendingResize  *terminalSize
	usedGateIDs    map[string]bool
	cancelReason   string
	finalizeReason string
	shutdownReason string
	stdoutBytes    int
	stderrBytes    int
}

// NewSessionState returns an initial session validator.
func NewSessionState() *SessionState {
	return &SessionState{phase: PhaseInitial, usedGateIDs: make(map[string]bool)}
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

// AcceptController validates and applies one controller request.
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
		state.phase = PhaseHelloSent
	case Resize:
		if !phaseIn(state.Phase(), PhaseIdle, PhaseStarting, PhaseRunning, PhaseGated) || state.pendingResize != nil {
			return state.outOfState(message)
		}
		state.pendingResize = &terminalSize{columns: value.Columns, rows: value.Rows}
	case Execute:
		if err := state.requirePhase(PhaseIdle, message); err != nil {
			return err
		}
		state.phase = PhaseStarting
		state.operationID = value.OperationID
		state.executionShape = value.ExecutionShape
		state.gateID = ""
		state.usedGateIDs = make(map[string]bool)
		state.cancelReason = ""
		state.finalizeReason = ""
		state.stdoutBytes = 0
		state.stderrBytes = 0
	case Continue:
		if err := state.requirePhase(PhaseGated, message); err != nil {
			return err
		}
		if err := state.requireOperation(value.OperationID); err != nil || value.GateID != state.gateID {
			if err != nil {
				return err
			}
			return state.outOfState(message)
		}
		state.phase = PhaseContinuing
	case Cancel:
		if !phaseIn(state.Phase(), PhaseRunning, PhaseGated, PhaseContinuing) {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		state.cancelReason = value.Reason
		state.phase = PhaseCancelling
	case Finalize:
		if !phaseIn(state.Phase(), PhaseRunning, PhaseGated, PhaseContinuing) {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		state.finalizeReason = value.Reason
		state.phase = PhaseFinalizing
	case Shutdown:
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
	defer func() {
		if err == nil {
			*counter = sequence
		}
	}()

	switch value := message.(type) {
	case Ready:
		if err := state.requirePhase(PhaseHelloSent, message); err != nil {
			return err
		}
		state.phase = PhaseIdle
	case ResizeApplied:
		if state.pendingResize == nil || *state.pendingResize != (terminalSize{columns: value.Columns, rows: value.Rows}) {
			return state.outOfState(message)
		}
		state.pendingResize = nil
	case OperationStarted:
		if err := state.requirePhase(PhaseStarting, message); err != nil {
			return err
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if value.OutputStart < state.outputEnd {
			return protocolError("invalid-output-order", "operation start precedes accepted output")
		}
		state.outputStart = value.OutputStart
		state.hasStart = true
		state.phase = PhaseRunning
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
		if err := state.advanceOutput(value.OutputThrough); err != nil {
			return err
		}
		state.usedGateIDs[value.GateID] = true
		state.gateID = value.GateID
		state.phase = PhaseGated
	case OperationContinued:
		if err := state.requirePhase(PhaseContinuing, message); err != nil {
			return err
		}
		if err := state.requireOperation(value.OperationID); err != nil || value.GateID != state.gateID {
			if err != nil {
				return err
			}
			return state.outOfState(message)
		}
		if err := state.advanceOutput(value.OutputThrough); err != nil {
			return err
		}
		state.gateID = ""
		state.phase = PhaseRunning
	case OperationOutput:
		if !phaseIn(state.Phase(), PhaseRunning, PhaseGated, PhaseContinuing, PhaseCancelling, PhaseFinalizing) {
			return state.outOfState(message)
		}
		if err := state.requireOperation(value.OperationID); err != nil {
			return err
		}
		if state.executionShape != ExecutionSplit {
			return protocolError("out-of-state", "logical stream evidence requires split execution")
		}
		count := logicalOutputBytes(value.DataBase64)
		if value.Stream == "stdout" {
			state.stdoutBytes += count
			if state.stdoutBytes > MaxLogicalStreamBytes {
				return protocolError("logical-output-too-large", "stdout evidence exceeds limit")
			}
		} else {
			state.stderrBytes += count
			if state.stderrBytes > MaxLogicalStreamBytes {
				return protocolError("logical-output-too-large", "stderr evidence exceeds limit")
			}
		}
	case OperationCompleted:
		if err := state.requirePhase(PhaseRunning, message); err != nil {
			return err
		}
		return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough)
	case OperationCancelled:
		if err := state.requirePhase(PhaseCancelling, message); err != nil {
			return err
		}
		if value.Reason != state.cancelReason {
			return protocolError("cancellation-reason-mismatch", "cancellation reason does not match request")
		}
		return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough)
	case OperationFinalized:
		if err := state.requirePhase(PhaseFinalizing, message); err != nil {
			return err
		}
		if value.Reason != state.finalizeReason {
			return protocolError("finalization-reason-mismatch", "finalization reason does not match request")
		}
		return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough)
	case OperationFailed:
		if !phaseIn(state.Phase(), PhaseStarting, PhaseRunning, PhaseGated, PhaseContinuing, PhaseCancelling, PhaseFinalizing) {
			return state.outOfState(message)
		}
		if state.Phase() == PhaseStarting {
			if value.OutputStart < state.outputEnd {
				return protocolError("invalid-output-order", "operation start precedes accepted output")
			}
			state.outputStart = value.OutputStart
			state.hasStart = true
		}
		return state.finishOperation(value.OperationID, value.OutputStart, value.OutputThrough)
	case Diagnostic:
		if phaseIn(state.Phase(), PhaseInitial, PhaseClosed) {
			return state.outOfState(message)
		}
		if value.OperationID != nil {
			return state.requireOperation(*value.OperationID)
		}
	case Draining:
		if err := state.requirePhase(PhaseShutdownSent, message); err != nil {
			return err
		}
		if value.Reason != state.shutdownReason {
			return protocolError("shutdown-reason-mismatch", "draining reason does not match request")
		}
		if err := state.advanceOutput(value.OutputThrough); err != nil {
			return err
		}
		state.phase = PhaseDraining
	case Closed:
		if err := state.requirePhase(PhaseDraining, message); err != nil {
			return err
		}
		if err := state.advanceOutput(value.OutputThrough); err != nil {
			return err
		}
		state.phase = PhaseClosed
	default:
		return protocolError("unsupported-message", fmt.Sprintf("unsupported Envoy model %T", message))
	}
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
	case OperationOutput:
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

func (state *SessionState) finishOperation(operationID string, start, through uint64) error {
	if err := state.requireOperation(operationID); err != nil {
		return err
	}
	if !state.hasStart || start != state.outputStart {
		return state.outOfState(operationID)
	}
	if err := state.advanceOutput(through); err != nil {
		return err
	}
	state.operationID = ""
	state.executionShape = ""
	state.gateID = ""
	state.outputStart = 0
	state.hasStart = false
	state.usedGateIDs = make(map[string]bool)
	state.cancelReason = ""
	state.finalizeReason = ""
	state.stdoutBytes = 0
	state.stderrBytes = 0
	state.phase = PhaseIdle
	return nil
}

func (state *SessionState) advanceOutput(offset uint64) error {
	if offset < state.outputEnd || (state.hasStart && offset < state.outputStart) {
		return protocolError("invalid-output-order", "output offset regressed")
	}
	if state.hasStart && state.executionShape == ExecutionSplit {
		logicalBytes := uint64(state.stdoutBytes + state.stderrBytes)
		if logicalBytes > offset-state.outputStart {
			return protocolError("invalid-output-order", "logical output exceeds output barrier")
		}
	}
	state.outputEnd = offset
	return nil
}

func (state *SessionState) requirePhase(expected Phase, message any) error {
	if state.Phase() != expected {
		return state.outOfState(message)
	}
	return nil
}

func (state *SessionState) requireOperation(operationID string) error {
	if state.operationID != operationID {
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
