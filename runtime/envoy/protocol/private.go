package protocol

// PrivateDirection identifies the sender on the inherited descriptor channel.
type PrivateDirection uint8

const (
	EnvoyToAwsh PrivateDirection = iota + 1
	AwshToEnvoy
)

// HelperDirection identifies the sender on a single helper exchange.
type HelperDirection uint8

const (
	HelperToAwsh HelperDirection = iota + 1
	AwshToHelper
)

// HelperPhase selects the wire form, not actor ordering or readiness.
type HelperPhase uint8

const (
	HelperStartup HelperPhase = iota + 1
	HelperCompletion
	HelperSource
	HelperStartPrepared
	HelperGate
)

// PrivateMessage and HelperMessage are sealed, stateless wire models.
type PrivateMessage interface{ privateType() string }
type HelperMessage interface{ helperType() string }

// Fields are declared in their normative NUL wire order.
type PrivateExecute struct {
	OperationID    string       `wire:"operation_id"`
	ExecutionShape string       `wire:"execution_shape"`
	Timing         string       `wire:"timing"`
	Publication    string       `wire:"publication"`
	Observation    string       `wire:"observation"`
	Inspections    []Inspection `wire:"inspections"`
	StdoutFIFO     string       `wire:"stdout_fifo"`
	StderrFIFO     string       `wire:"stderr_fifo"`
	Source         string       `wire:"source"`
}

type PrivateContinue struct {
	OperationID string `wire:"operation_id"`
	GateID      string `wire:"gate_id"`
}

type PrivateStartRelease struct {
	OperationID string `wire:"operation_id"`
}
type PrivateStartedAck struct {
	OperationID string `wire:"operation_id"`
}
type PrivateInputClosed struct {
	OperationID string `wire:"operation_id"`
}
type PrivateShutdown struct{}

type PrivateReady struct {
	AwshPID  int64  `wire:"pid"`
	ShellPID int64  `wire:"pid"`
	CWD      string `wire:"cwd"`
}

type PrivateSubmit struct {
	OperationID string `wire:"operation_id"`
}
type PrivateStartPrepared struct {
	OperationID string `wire:"operation_id"`
}
type PrivateStarted struct {
	OperationID string `wire:"operation_id"`
}
type PrivateStartReleased struct {
	OperationID string `wire:"operation_id"`
}
type PrivateGateReady PrivateContinue
type PrivateGateContinued PrivateContinue
type PrivateGateInterrupted PrivateContinue

type PrivateInputClose struct {
	OperationID         string `wire:"operation_id"`
	CompletionHelperPID int64  `wire:"pid"`
}

// ResolvedInspection carries a plan only; filesystem results belong to public events.
type ResolvedInspection struct {
	InspectionID string `json:"inspection_id"`
	Kind         string `json:"kind"`
	ResolvedPath string `json:"resolved_path"`
	ProducerID   string `json:"producer_id,omitempty"`
	OutputID     string `json:"output_id,omitempty"`
}

type PrivateCompleted struct {
	OperationID string               `wire:"operation_id"`
	Status      int64                `wire:"status"`
	PhysicalCWD string               `wire:"cwd"`
	Inspections []ResolvedInspection `wire:"resolved_inspections"`
}

type PrivateRejected struct {
	OperationID string `wire:"operation_id"`
	Code        string `wire:"rejection_code"`
	Message     string `wire:"message"`
}

type PrivateShellExit struct {
	ActiveOperationID string `wire:"active_operation_id"`
	Status            int64  `wire:"status"`
	CWD               string `wire:"cwd"`
}

type PrivateProtocolError struct {
	Code    string `wire:"code"`
	Message string `wire:"message"`
}

type PrivateClosed struct {
	Reason string `wire:"shutdown"`
	Status int64  `wire:"status"`
	CWD    string `wire:"cwd"`
}

// PromptState is shared by startup prompt_state and completion state reports.
// LogicalCWD is only lexically checked here; the actor verifies directory identity.
type PromptState struct {
	Status      int64             `wire:"status"`
	HistExpand  string            `wire:"histexpand"`
	EditingMode string            `wire:"editing_mode"`
	PhysicalCWD string            `wire:"cwd"`
	LogicalCWD  string            `wire:"logical_cwd"`
	ExportedEnv map[string]string `wire:"environment"`
}

type HelperPromptState struct{ PromptState }
type HelperStartupReady struct{}
type HelperCompletionReady struct{ PromptState }
type HelperSourceRequest struct{}
type HelperPreparedRequest struct{}
type HelperGateRequest struct {
	GateID string `wire:"gate_id"`
}
type HelperAccepted struct{}
type HelperGateAccepted struct {
	GateID string `wire:"gate_id"`
}
type HelperSourceReply struct {
	OperationID    string `wire:"operation_id"`
	Status         int64  `wire:"status"`
	HistExpand     string `wire:"histexpand"`
	EditingMode    string `wire:"editing_mode"`
	ExecutionShape string `wire:"execution_shape"`
	StdoutFIFO     string `wire:"stdout_fifo"`
	StderrFIFO     string `wire:"stderr_fifo"`
	Source         string `wire:"source"`
}

func (PrivateExecute) privateType() string         { return "execute" }
func (PrivateContinue) privateType() string        { return "continue" }
func (PrivateStartRelease) privateType() string    { return "start_release" }
func (PrivateStartedAck) privateType() string      { return "started_ack" }
func (PrivateInputClosed) privateType() string     { return "input_closed" }
func (PrivateShutdown) privateType() string        { return "shutdown" }
func (PrivateReady) privateType() string           { return "ready" }
func (PrivateSubmit) privateType() string          { return "submit" }
func (PrivateStartPrepared) privateType() string   { return "start_prepared" }
func (PrivateStarted) privateType() string         { return "started" }
func (PrivateStartReleased) privateType() string   { return "start_released" }
func (PrivateGateReady) privateType() string       { return "gate_ready" }
func (PrivateGateContinued) privateType() string   { return "gate_continued" }
func (PrivateGateInterrupted) privateType() string { return "gate_interrupted" }
func (PrivateInputClose) privateType() string      { return "input_close" }
func (PrivateCompleted) privateType() string       { return "completed" }
func (PrivateRejected) privateType() string        { return "rejected" }
func (PrivateShellExit) privateType() string       { return "shell_exit" }
func (PrivateProtocolError) privateType() string   { return "protocol_error" }
func (PrivateClosed) privateType() string          { return "closed" }
func (HelperPromptState) helperType() string       { return "prompt_state" }
func (HelperStartupReady) helperType() string      { return "prompt_ready" }
func (HelperCompletionReady) helperType() string   { return "prompt_ready" }
func (HelperSourceRequest) helperType() string     { return "source" }
func (HelperPreparedRequest) helperType() string   { return "start_prepared" }
func (HelperGateRequest) helperType() string       { return "gate" }
func (HelperAccepted) helperType() string          { return "accepted" }
func (HelperGateAccepted) helperType() string      { return "accepted" }
func (HelperSourceReply) helperType() string       { return "source" }
