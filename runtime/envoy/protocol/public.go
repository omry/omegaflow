// Package protocol implements the stateless public Envoy v1 wire contract.
// Session ordering, live Bash policy checks and actor behavior belong to callers.
package protocol

const (
	Schema          = "omegaflow-envoy-telemetry-v1"
	MaxFrameBytes   = 1_048_576
	MaxSourceBytes  = 786_432
	MaxPathBytes    = 4_096
	MaxMessageBytes = 4_096
	MaxReasonBytes  = 256
	MaxInspections  = 64
)

// Header is the first three fields of every public frame. Type must match
// the concrete message; Schema must be Schema and Seq must be positive.
type Header struct {
	Schema string `json:"schema"`
	Type   string `json:"type"`
	Seq    int64  `json:"seq"`
}

// Message is a typed public request or event. Optional values use pointers so
// omission remains distinct from false, zero and an empty string.
type Message interface{ wireType() string }

type Hello struct {
	Header
	SessionID string `json:"session_id"`
}
type Execute struct {
	Header
	OperationID    string       `json:"operation_id"`
	Source         string       `json:"source"`
	ExecutionShape string       `json:"execution_shape"`
	Timing         string       `json:"timing"`
	Publication    string       `json:"publication"`
	Observation    string       `json:"observation"`
	Inspections    []Inspection `json:"inspections"`
	InputThrough   int64        `json:"input_through"`
}
type Continue struct {
	Header
	OperationID  string `json:"operation_id"`
	GateID       string `json:"gate_id"`
	InputThrough int64  `json:"input_through"`
}
type Cancel struct {
	Header
	OperationID string `json:"operation_id"`
	Reason      string `json:"reason"`
}
type Finalize struct {
	Header
	OperationID string `json:"operation_id"`
	Reason      string `json:"reason"`
}
type Resize struct {
	Header
	Columns int64 `json:"columns"`
	Rows    int64 `json:"rows"`
}
type Shutdown struct {
	Header
	Reason string `json:"reason"`
}
type Ready struct {
	Header
	EnvoyPID      int64  `json:"envoy_pid"`
	ShellPID      int64  `json:"shell_pid"`
	Cwd           string `json:"cwd"`
	Columns       int64  `json:"columns"`
	Rows          int64  `json:"rows"`
	ElapsedUS     int64  `json:"elapsed_us"`
	OutputThrough int64  `json:"output_through"`
}
type OperationStarted struct {
	Header
	OperationID string `json:"operation_id"`
	OutputStart int64  `json:"output_start"`
}
type OperationReady struct {
	Header
	OperationID   string `json:"operation_id"`
	GateID        string `json:"gate_id"`
	OutputThrough int64  `json:"output_through"`
}
type OperationContinued struct {
	Header
	OperationID   string `json:"operation_id"`
	GateID        string `json:"gate_id"`
	OutputThrough int64  `json:"output_through"`
}
type OperationGateInterrupted struct {
	Header
	OperationID   string `json:"operation_id"`
	GateID        string `json:"gate_id"`
	OutputThrough int64  `json:"output_through"`
}
type OutputMark struct {
	Header
	Offset    int64  `json:"offset"`
	Stream    string `json:"stream"`
	ElapsedUS int64  `json:"elapsed_us"`
}
type OperationCompleted struct {
	Header
	OperationID       string             `json:"operation_id"`
	Status            int64              `json:"status"`
	Cwd               string             `json:"cwd"`
	OutputStart       int64              `json:"output_start"`
	OutputThrough     int64              `json:"output_through"`
	InspectionResults []InspectionResult `json:"inspection_results"`
	ShellEnded        *bool              `json:"shell_ended,omitempty"`
}
type OperationCancelled struct {
	Header
	OperationID   string `json:"operation_id"`
	Cwd           string `json:"cwd"`
	OutputStart   int64  `json:"output_start"`
	OutputThrough int64  `json:"output_through"`
	Reason        string `json:"reason"`
	Status        *int64 `json:"status,omitempty"`
}
type OperationFinalized struct {
	Header
	OperationID       string             `json:"operation_id"`
	Cwd               string             `json:"cwd"`
	OutputStart       int64              `json:"output_start"`
	OutputThrough     int64              `json:"output_through"`
	Reason            string             `json:"reason"`
	InspectionResults []InspectionResult `json:"inspection_results"`
}
type OperationFailed struct {
	Header
	OperationID   string `json:"operation_id"`
	OutputStart   int64  `json:"output_start"`
	OutputThrough int64  `json:"output_through"`
	Code          string `json:"code"`
	Message       string `json:"message"`
	Cwd           string `json:"cwd"`
	ShellEnded    *bool  `json:"shell_ended,omitempty"`
}
type ResizeApplied struct {
	Header
	Columns       int64 `json:"columns"`
	Rows          int64 `json:"rows"`
	ElapsedUS     int64 `json:"elapsed_us"`
	OutputThrough int64 `json:"output_through"`
}
type Diagnostic struct {
	Header
	Severity    string  `json:"severity"`
	Code        string  `json:"code"`
	Message     string  `json:"message"`
	OperationID *string `json:"operation_id,omitempty"`
}
type Draining struct {
	Header
	Reason        string `json:"reason"`
	OutputThrough int64  `json:"output_through"`
}
type Closed struct {
	Header
	Reason        string `json:"reason"`
	OutputThrough int64  `json:"output_through"`
}

func (Hello) wireType() string                    { return "hello" }
func (Execute) wireType() string                  { return "execute" }
func (Continue) wireType() string                 { return "continue" }
func (Cancel) wireType() string                   { return "cancel" }
func (Finalize) wireType() string                 { return "finalize" }
func (Resize) wireType() string                   { return "resize" }
func (Shutdown) wireType() string                 { return "shutdown" }
func (Ready) wireType() string                    { return "ready" }
func (OperationStarted) wireType() string         { return "operation_started" }
func (OperationReady) wireType() string           { return "operation_ready" }
func (OperationContinued) wireType() string       { return "operation_continued" }
func (OperationGateInterrupted) wireType() string { return "operation_gate_interrupted" }
func (OutputMark) wireType() string               { return "output_mark" }
func (OperationCompleted) wireType() string       { return "operation_completed" }
func (OperationCancelled) wireType() string       { return "operation_cancelled" }
func (OperationFinalized) wireType() string       { return "operation_finalized" }
func (OperationFailed) wireType() string          { return "operation_failed" }
func (ResizeApplied) wireType() string            { return "resize_applied" }
func (Diagnostic) wireType() string               { return "diagnostic" }
func (Draining) wireType() string                 { return "draining" }
func (Closed) wireType() string                   { return "closed" }

// Inspection is a configured path request. ProducerID and OutputID are used
// only by produces; file_exists omits both fields.
type Inspection struct {
	InspectionID string `json:"inspection_id"`
	Kind         string `json:"kind"`
	Path         string `json:"path"`
	ProducerID   string `json:"producer_id,omitempty"`
	OutputID     string `json:"output_id,omitempty"`
}

// InspectionResult carries bounded wire evidence, not a filesystem probe.
// Only produced directories carry DigestAlgorithm, always directory-v2.
type InspectionResult struct {
	InspectionID    string `json:"inspection_id"`
	Kind            string `json:"kind"`
	ResolvedPath    string `json:"resolved_path"`
	PathKind        string `json:"path_kind"`
	ProducerID      string `json:"producer_id,omitempty"`
	OutputID        string `json:"output_id,omitempty"`
	SHA256          string `json:"sha256,omitempty"`
	DigestAlgorithm string `json:"digest_algorithm,omitempty"`
}
