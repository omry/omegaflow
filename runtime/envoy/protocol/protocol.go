// Package protocol defines the bounded OmegaFlow Envoy v1 wire contracts:
// the controller/Envoy JSON Lines telemetry channel, the private NUL-framed
// Envoy-to-external-awsh descriptor protocol, the submission capsule bounds,
// workload inspection models, digests, and fail-closed session lifecycle
// validation. Decoders and lifecycle state are deliberately unsynchronized
// because one session owner serializes their use; a rejected frame or
// transition is a terminal session failure.
package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	TelemetrySchema        = "omegaflow-envoy-telemetry-v1"
	MaxTelemetryFrameBytes = 1_048_576

	// MaxOperationSourceBytes is the inclusive operation-source maximum.
	// Source is 1 through 491,520 UTF-8 bytes so that the doubled-source
	// submission capsule and its private submit envelope stay inside the
	// private frame limit.
	MaxOperationSourceBytes = 491_520

	MaxDiagnosticBytes = 4_096
	MaxReasonBytes     = 256
	MaxCWDBytes        = 4_096
	MaxSequence        = uint64(1)<<63 - 1
	MaxOutputOffset    = uint64(1)<<63 - 1
	MaxElapsedUS       = uint64(1)<<63 - 1
	MaxInputThrough    = uint64(1)<<63 - 1
	MaxPID             = 1<<31 - 1
	MinColumns         = 1
	MaxColumns         = 1_000
	MinRows            = 1
	MaxRows            = 1_000

	MaxInspectionsPerOperation = 64
	MaxInspectionPathBytes     = 4_096
	MaxOutputMarksPerSession   = 1_000_000
)

var (
	identifierPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	codePattern       = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
	sessionIDPattern  = regexp.MustCompile(`^[0-9a-f]{32}$`)
	sha256Pattern     = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// OperationFailedCodes is the closed v1 operation_failed code set. Adding a
// code is a schema change under the versioning rule. Diagnostic codes remain
// an open, shape-bounded set.
var OperationFailedCodes = map[string]bool{
	"inspection-resolution":  true,
	"inspection-missing":     true,
	"inspection-type":        true,
	"inspection-limit":       true,
	"inspection-unstable":    true,
	"inspection-read":        true,
	"source-invalid":         true,
	"source-unsupported":     true,
	"input-barrier-timeout":  true,
	"cancel-timeout":         true,
	"finalize-timeout":       true,
	"shell-ended-unresolved": true,
}

var telemetryFields = map[string]struct {
	required map[string]bool
	optional map[string]bool
}{
	"hello":                      fieldSet("seq", "session_id"),
	"execute":                    fieldSet("seq", "operation_id", "source", "execution_shape", "timing", "publication", "observation", "inspections", "input_through"),
	"continue":                   fieldSet("seq", "operation_id", "gate_id", "input_through"),
	"cancel":                     fieldSet("seq", "operation_id", "reason"),
	"finalize":                   fieldSet("seq", "operation_id", "reason"),
	"resize":                     fieldSet("seq", "columns", "rows"),
	"shutdown":                   fieldSet("seq", "reason"),
	"ready":                      fieldSet("seq", "envoy_pid", "shell_pid", "cwd", "columns", "rows", "elapsed_us"),
	"operation_started":          fieldSet("seq", "operation_id", "output_start"),
	"operation_ready":            fieldSet("seq", "operation_id", "gate_id", "output_through"),
	"operation_continued":        fieldSet("seq", "operation_id", "gate_id", "output_through"),
	"operation_gate_interrupted": fieldSet("seq", "operation_id", "gate_id", "output_through"),
	"output_mark":                fieldSet("seq", "offset", "stream", "elapsed_us"),
	"operation_completed":        fieldSetOptional([]string{"seq", "operation_id", "status", "cwd", "output_start", "output_through", "inspection_results"}, "shell_ended"),
	"operation_cancelled":        fieldSetOptional([]string{"seq", "operation_id", "cwd", "reason", "output_start", "output_through"}, "status"),
	"operation_finalized":        fieldSet("seq", "operation_id", "cwd", "reason", "output_start", "output_through", "inspection_results"),
	"operation_failed":           fieldSetOptional([]string{"seq", "operation_id", "code", "message", "cwd", "output_start", "output_through"}, "shell_ended"),
	"resize_applied":             fieldSet("seq", "columns", "rows", "elapsed_us", "output_through"),
	"diagnostic":                 fieldSetOptional([]string{"seq", "severity", "code", "message"}, "operation_id"),
	"draining":                   fieldSet("seq", "reason", "output_through"),
	"closed":                     fieldSet("seq", "reason", "output_through"),
}

func fieldSet(names ...string) struct {
	required map[string]bool
	optional map[string]bool
} {
	return fieldSetOptional(names)
}

func fieldSetOptional(required []string, optional ...string) struct {
	required map[string]bool
	optional map[string]bool
} {
	result := struct {
		required map[string]bool
		optional map[string]bool
	}{required: map[string]bool{"schema": true, "type": true}, optional: make(map[string]bool)}
	for _, name := range required {
		result.required[name] = true
	}
	for _, name := range optional {
		result.optional[name] = true
	}
	return result
}

// Error reports a stable fail-closed protocol code.
type Error struct {
	Code    string
	Message string
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

func protocolError(code, message string) error {
	return &Error{Code: code, Message: message}
}

type Hello struct {
	Seq       uint64 `json:"seq"`
	SessionID string `json:"session_id"`
}

// ExecutionShape selects whether an operation owns the terminal slave streams
// or separate Envoy-owned stdout and stderr pipes.
type ExecutionShape string

const (
	ExecutionPTY   ExecutionShape = "pty"
	ExecutionSplit ExecutionShape = "split"
)

// PublicationTiming selects the controller's compiled output schedule.
type PublicationTiming string

const (
	TimingRealtime     PublicationTiming = "realtime"
	TimingPresentation PublicationTiming = "presentation"
)

// PublicationMode selects how observed output enters the presentation cast.
type PublicationMode string

const (
	PublicationReal     PublicationMode = "real"
	PublicationSuppress PublicationMode = "suppress"
	PublicationReplace  PublicationMode = "replace"
)

// ObservationMode selects whether operation output evidence requires an
// exclusive operation boundary.
type ObservationMode string

const (
	ObservationShared    ObservationMode = "shared"
	ObservationExclusive ObservationMode = "exclusive"
)

// ExecutionPolicy is compiled by the controller before execute. Authored
// replacement text and presentation delays stay controller-private.
type ExecutionPolicy struct {
	ExecutionShape ExecutionShape    `json:"execution_shape"`
	Timing         PublicationTiming `json:"timing"`
	Publication    PublicationMode   `json:"publication"`
	Observation    ObservationMode   `json:"observation"`
}

type Execute struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	Source      string `json:"source"`
	ExecutionPolicy
	Inspections []InspectionSpec `json:"inspections"`
	// InputThrough is the terminal-input barrier: the running count of
	// terminal bytes the controller has written since the session began.
	InputThrough uint64 `json:"input_through"`
}
type Continue struct {
	Seq          uint64 `json:"seq"`
	OperationID  string `json:"operation_id"`
	GateID       string `json:"gate_id"`
	InputThrough uint64 `json:"input_through"`
}
type Cancel struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	Reason      string `json:"reason"`
}
type Finalize struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	Reason      string `json:"reason"`
}
type Resize struct {
	Seq     uint64 `json:"seq"`
	Columns int    `json:"columns"`
	Rows    int    `json:"rows"`
}
type Shutdown struct {
	Seq    uint64 `json:"seq"`
	Reason string `json:"reason"`
}

type Ready struct {
	Seq      uint64 `json:"seq"`
	EnvoyPID int    `json:"envoy_pid"`
	ShellPID int    `json:"shell_pid"`
	CWD      string `json:"cwd"`
	Columns  int    `json:"columns"`
	Rows     int    `json:"rows"`
	// ElapsedUS is 0: ready is the session epoch, stamped the instant it
	// establishes the Envoy's monotonic microsecond clock.
	ElapsedUS uint64 `json:"elapsed_us"`
}
type OperationStarted struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	OutputStart uint64 `json:"output_start"`
}
type OperationReady struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	GateID        string `json:"gate_id"`
	OutputThrough uint64 `json:"output_through"`
}
type OperationContinued struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	GateID        string `json:"gate_id"`
	OutputThrough uint64 `json:"output_through"`
}

// OperationGateInterrupted is the typed result of terminal Ctrl-C reaching a
// waiting gate helper. It reopens the running operation; it is never an
// implicit continue and never lifecycle cancellation.
type OperationGateInterrupted struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	GateID        string `json:"gate_id"`
	OutputThrough uint64 `json:"output_through"`
}

// OutputMark attributes retained raw output to a logical stream and to sender
// time. It is session-scoped and carries no operation identity; a mark
// attributes every byte from its offset until the next mark's offset.
type OutputMark struct {
	Seq       uint64 `json:"seq"`
	Offset    uint64 `json:"offset"`
	Stream    string `json:"stream"`
	ElapsedUS uint64 `json:"elapsed_us"`
}
type OperationCompleted struct {
	Seq               uint64             `json:"seq"`
	OperationID       string             `json:"operation_id"`
	Status            int                `json:"status"`
	CWD               string             `json:"cwd"`
	OutputStart       uint64             `json:"output_start"`
	OutputThrough     uint64             `json:"output_through"`
	InspectionResults []InspectionResult `json:"inspection_results"`
	// ShellEnded is present, boolean true, only when the operation's shell
	// did not survive it. It is never present and false.
	ShellEnded *bool `json:"shell_ended,omitempty"`
}
type OperationCancelled struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	// Status is absent only for an operation cancelled before it started.
	Status        *int   `json:"status,omitempty"`
	CWD           string `json:"cwd"`
	Reason        string `json:"reason"`
	OutputStart   uint64 `json:"output_start"`
	OutputThrough uint64 `json:"output_through"`
}
type OperationFinalized struct {
	Seq               uint64             `json:"seq"`
	OperationID       string             `json:"operation_id"`
	CWD               string             `json:"cwd"`
	Reason            string             `json:"reason"`
	OutputStart       uint64             `json:"output_start"`
	OutputThrough     uint64             `json:"output_through"`
	InspectionResults []InspectionResult `json:"inspection_results"`
}
type OperationFailed struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	Code          string `json:"code"`
	Message       string `json:"message"`
	CWD           string `json:"cwd"`
	OutputStart   uint64 `json:"output_start"`
	OutputThrough uint64 `json:"output_through"`
	ShellEnded    *bool  `json:"shell_ended,omitempty"`
}
type ResizeApplied struct {
	Seq           uint64 `json:"seq"`
	Columns       int    `json:"columns"`
	Rows          int    `json:"rows"`
	ElapsedUS     uint64 `json:"elapsed_us"`
	OutputThrough uint64 `json:"output_through"`
}
type Diagnostic struct {
	Seq         uint64  `json:"seq"`
	Severity    string  `json:"severity"`
	Code        string  `json:"code"`
	Message     string  `json:"message"`
	OperationID *string `json:"operation_id,omitempty"`
}
type Draining struct {
	Seq           uint64 `json:"seq"`
	Reason        string `json:"reason"`
	OutputThrough uint64 `json:"output_through"`
}
type Closed struct {
	Seq           uint64 `json:"seq"`
	Reason        string `json:"reason"`
	OutputThrough uint64 `json:"output_through"`
}

type telemetryHeader struct {
	Schema string `json:"schema"`
	Type   string `json:"type"`
}

// DecodeController decodes one complete controller-to-Envoy JSONL frame.
func DecodeController(frame []byte) (any, error) {
	body, messageType, err := telemetryBody(frame)
	if err != nil {
		return nil, err
	}
	switch messageType {
	case "hello":
		var wire struct {
			telemetryHeader
			Hello
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Hello, validateTelemetry(wire.Hello)
	case "execute":
		var wire struct {
			telemetryHeader
			Execute
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Execute, validateTelemetry(wire.Execute)
	case "continue":
		var wire struct {
			telemetryHeader
			Continue
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Continue, validateTelemetry(wire.Continue)
	case "cancel":
		var wire struct {
			telemetryHeader
			Cancel
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Cancel, validateTelemetry(wire.Cancel)
	case "finalize":
		var wire struct {
			telemetryHeader
			Finalize
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Finalize, validateTelemetry(wire.Finalize)
	case "resize":
		var wire struct {
			telemetryHeader
			Resize
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Resize, validateTelemetry(wire.Resize)
	case "shutdown":
		var wire struct {
			telemetryHeader
			Shutdown
		}
		if err := decodeExact(body, &wire); err != nil {
			return nil, err
		}
		return wire.Shutdown, validateTelemetry(wire.Shutdown)
	default:
		return nil, protocolError("unsupported-message", "unsupported controller message")
	}
}

// DecodeEnvoy decodes one complete Envoy-to-controller JSONL frame.
func DecodeEnvoy(frame []byte) (any, error) {
	body, messageType, err := telemetryBody(frame)
	if err != nil {
		return nil, err
	}
	var message any
	switch messageType {
	case "ready":
		var wire struct {
			telemetryHeader
			Ready
		}
		err = decodeExact(body, &wire)
		message = wire.Ready
	case "operation_started":
		var wire struct {
			telemetryHeader
			OperationStarted
		}
		err = decodeExact(body, &wire)
		message = wire.OperationStarted
	case "operation_ready":
		var wire struct {
			telemetryHeader
			OperationReady
		}
		err = decodeExact(body, &wire)
		message = wire.OperationReady
	case "operation_continued":
		var wire struct {
			telemetryHeader
			OperationContinued
		}
		err = decodeExact(body, &wire)
		message = wire.OperationContinued
	case "operation_gate_interrupted":
		var wire struct {
			telemetryHeader
			OperationGateInterrupted
		}
		err = decodeExact(body, &wire)
		message = wire.OperationGateInterrupted
	case "output_mark":
		var wire struct {
			telemetryHeader
			OutputMark
		}
		err = decodeExact(body, &wire)
		message = wire.OutputMark
	case "operation_completed":
		var wire struct {
			telemetryHeader
			OperationCompleted
		}
		err = decodeExact(body, &wire)
		message = wire.OperationCompleted
	case "operation_cancelled":
		var wire struct {
			telemetryHeader
			OperationCancelled
		}
		err = decodeExact(body, &wire)
		message = wire.OperationCancelled
	case "operation_finalized":
		var wire struct {
			telemetryHeader
			OperationFinalized
		}
		err = decodeExact(body, &wire)
		message = wire.OperationFinalized
	case "operation_failed":
		var wire struct {
			telemetryHeader
			OperationFailed
		}
		err = decodeExact(body, &wire)
		message = wire.OperationFailed
	case "resize_applied":
		var wire struct {
			telemetryHeader
			ResizeApplied
		}
		err = decodeExact(body, &wire)
		message = wire.ResizeApplied
	case "diagnostic":
		var wire struct {
			telemetryHeader
			Diagnostic
		}
		err = decodeExact(body, &wire)
		message = wire.Diagnostic
	case "draining":
		var wire struct {
			telemetryHeader
			Draining
		}
		err = decodeExact(body, &wire)
		message = wire.Draining
	case "closed":
		var wire struct {
			telemetryHeader
			Closed
		}
		err = decodeExact(body, &wire)
		message = wire.Closed
	default:
		return nil, protocolError("unsupported-message", "unsupported Envoy message")
	}
	if err != nil {
		return nil, err
	}
	return message, validateTelemetry(message)
}

func telemetryBody(frame []byte) ([]byte, string, error) {
	body, err := validateJSONLFrame(frame)
	if err != nil {
		return nil, "", err
	}
	fields, err := objectFields(body)
	if err != nil {
		return nil, "", err
	}
	if err := validateUnicodeEscapes(body); err != nil {
		return nil, "", err
	}
	var header telemetryHeader
	if raw, ok := fields["schema"]; ok {
		if isJSONNull(raw) {
			return nil, "", protocolError("invalid-field", "schema must not be null")
		}
		if err := json.Unmarshal(raw, &header.Schema); err != nil {
			return nil, "", protocolError("invalid-field", "schema must be a string")
		}
	}
	if raw, ok := fields["type"]; ok {
		if isJSONNull(raw) {
			return nil, "", protocolError("invalid-field", "type must not be null")
		}
		if err := json.Unmarshal(raw, &header.Type); err != nil {
			return nil, "", protocolError("invalid-field", "type must be a string")
		}
	}
	if header.Schema != TelemetrySchema {
		return nil, "", protocolError("unsupported-schema", "unsupported telemetry schema")
	}
	if header.Type == "" {
		return nil, "", protocolError("missing-field", "type")
	}
	if expected, ok := telemetryFields[header.Type]; ok {
		for name := range expected.required {
			if _, present := fields[name]; !present {
				return nil, "", protocolError("missing-field", name)
			}
		}
		for name := range fields {
			if !expected.required[name] && !expected.optional[name] {
				return nil, "", protocolError("unknown-field", name)
			}
		}
		for name, raw := range fields {
			if (expected.required[name] || expected.optional[name]) && isJSONNull(raw) {
				return nil, "", protocolError("invalid-field", name+" must not be null")
			}
		}
	}
	return body, header.Type, nil
}

func validateUnicodeEscapes(body []byte) error {
	inString := false
	for index := 0; index < len(body); {
		switch body[index] {
		case '"':
			inString = !inString
			index++
		case '\\':
			if !inString || index+1 >= len(body) {
				index++
				continue
			}
			if body[index+1] != 'u' {
				index += 2
				continue
			}
			unit, ok := parseUnicodeEscape(body, index)
			if !ok {
				return protocolError("invalid-field", "invalid Unicode escape")
			}
			switch {
			case unit >= 0xD800 && unit <= 0xDBFF:
				lowIndex := index + 6
				low, paired := parseUnicodeEscape(body, lowIndex)
				if !paired || low < 0xDC00 || low > 0xDFFF {
					return protocolError("invalid-field", "unpaired UTF-16 surrogate escape")
				}
				index += 12
			case unit >= 0xDC00 && unit <= 0xDFFF:
				return protocolError("invalid-field", "unpaired UTF-16 surrogate escape")
			default:
				index += 6
			}
		default:
			index++
		}
	}
	return nil
}

func parseUnicodeEscape(body []byte, index int) (uint16, bool) {
	if index+6 > len(body) || body[index] != '\\' || body[index+1] != 'u' {
		return 0, false
	}
	value, err := strconv.ParseUint(string(body[index+2:index+6]), 16, 16)
	return uint16(value), err == nil
}

func isJSONNull(raw json.RawMessage) bool {
	return bytes.Equal(bytes.TrimSpace(raw), []byte("null"))
}

func validateJSONLFrame(frame []byte) ([]byte, error) {
	if len(frame) > MaxTelemetryFrameBytes {
		return nil, protocolError("frame-too-large", "telemetry frame exceeds limit")
	}
	if len(frame) < 2 || frame[len(frame)-1] != '\n' || bytes.Count(frame, []byte{'\n'}) != 1 {
		return nil, protocolError("invalid-framing", "frame must end with one LF")
	}
	body := frame[:len(frame)-1]
	if bytes.ContainsAny(body, "\r\x00") {
		return nil, protocolError("invalid-framing", "frame contains an unsafe byte")
	}
	if !utf8.Valid(body) {
		return nil, protocolError("invalid-utf8", "telemetry is not UTF-8")
	}
	return body, nil
}

func objectFields(body []byte) (map[string]json.RawMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return nil, protocolError("invalid-json", "telemetry must be an object")
	}
	fields := make(map[string]json.RawMessage)
	for decoder.More() {
		keyToken, err := decoder.Token()
		if err != nil {
			return nil, protocolError("invalid-json", "invalid object key")
		}
		key, ok := keyToken.(string)
		if !ok {
			return nil, protocolError("invalid-json", "object key is not a string")
		}
		if _, exists := fields[key]; exists {
			return nil, protocolError("duplicate-field", "duplicate field "+key)
		}
		var raw json.RawMessage
		if err := decoder.Decode(&raw); err != nil {
			return nil, protocolError("invalid-json", "invalid field value")
		}
		fields[key] = raw
	}
	if _, err := decoder.Token(); err != nil {
		return nil, protocolError("invalid-json", "unterminated object")
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, protocolError("invalid-json", "trailing JSON value")
	}
	return fields, nil
}

func decodeExact(body []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		if protocolErr, ok := err.(*Error); ok {
			return protocolErr
		}
		if strings.Contains(err.Error(), "unknown field") {
			return protocolError("unknown-field", err.Error())
		}
		return protocolError("invalid-field", err.Error())
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return protocolError("invalid-json", "trailing JSON value")
	}
	return nil
}

// StreamDecoder incrementally decodes one direction of bounded JSONL frames.
type StreamDecoder struct {
	buffer []byte
	decode func([]byte) (any, error)
}

func NewControllerStreamDecoder() *StreamDecoder {
	return &StreamDecoder{decode: DecodeController}
}

func NewEnvoyStreamDecoder() *StreamDecoder {
	return &StreamDecoder{decode: DecodeEnvoy}
}

func (decoder *StreamDecoder) Feed(data []byte) ([]any, error) {
	var messages []any
	for len(data) != 0 {
		newline := bytes.IndexByte(data, '\n')
		if newline < 0 {
			if len(data) >= MaxTelemetryFrameBytes-len(decoder.buffer) {
				return nil, protocolError("frame-too-large", "unterminated frame")
			}
			decoder.buffer = append(decoder.buffer, data...)
			return messages, nil
		}
		chunk := data[:newline+1]
		data = data[newline+1:]
		if len(chunk) > MaxTelemetryFrameBytes-len(decoder.buffer) {
			return nil, protocolError("frame-too-large", "telemetry frame exceeds limit")
		}
		frame := make([]byte, 0, len(decoder.buffer)+len(chunk))
		frame = append(frame, decoder.buffer...)
		frame = append(frame, chunk...)
		decoder.buffer = nil
		message, err := decoder.decode(frame)
		if err != nil {
			return nil, err
		}
		messages = append(messages, message)
	}
	return messages, nil
}

// Finish reports whether the stream closed between complete frames. A
// telemetry EOF between complete frames is still not session success until a
// valid closed event was accepted; that rule belongs to SessionState.
func (decoder *StreamDecoder) Finish() error {
	if len(decoder.buffer) != 0 {
		return protocolError("early-close", "telemetry closed mid-frame")
	}
	return nil
}

// EncodeController returns the canonical JSONL representation of a request.
func EncodeController(message any) ([]byte, error) {
	messageType := ""
	switch message.(type) {
	case Hello:
		messageType = "hello"
	case Execute:
		messageType = "execute"
	case Continue:
		messageType = "continue"
	case Cancel:
		messageType = "cancel"
	case Finalize:
		messageType = "finalize"
	case Resize:
		messageType = "resize"
	case Shutdown:
		messageType = "shutdown"
	default:
		return nil, fmt.Errorf("unsupported controller model %T", message)
	}
	if err := validateTelemetry(message); err != nil {
		return nil, err
	}
	return encodeTelemetry(messageType, message)
}

// EncodeEnvoy returns the canonical JSONL representation of an event.
func EncodeEnvoy(message any) ([]byte, error) {
	messageType := ""
	switch message.(type) {
	case Ready:
		messageType = "ready"
	case OperationStarted:
		messageType = "operation_started"
	case OperationReady:
		messageType = "operation_ready"
	case OperationContinued:
		messageType = "operation_continued"
	case OperationGateInterrupted:
		messageType = "operation_gate_interrupted"
	case OutputMark:
		messageType = "output_mark"
	case OperationCompleted:
		messageType = "operation_completed"
	case OperationCancelled:
		messageType = "operation_cancelled"
	case OperationFinalized:
		messageType = "operation_finalized"
	case OperationFailed:
		messageType = "operation_failed"
	case ResizeApplied:
		messageType = "resize_applied"
	case Diagnostic:
		messageType = "diagnostic"
	case Draining:
		messageType = "draining"
	case Closed:
		messageType = "closed"
	default:
		return nil, fmt.Errorf("unsupported Envoy model %T", message)
	}
	if err := validateTelemetry(message); err != nil {
		return nil, err
	}
	return encodeTelemetry(messageType, message)
}

// marshalCanonical produces the canonical compact JSON encoding: HTML
// escaping is disabled so bytes such as &, <, and > — common in shell
// source — are emitted literally and stay identical across implementations.
func marshalCanonical(value any) ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buffer.Bytes(), "\n"), nil
}

func encodeTelemetry(messageType string, message any) ([]byte, error) {
	fields, err := marshalCanonical(message)
	if err != nil {
		return nil, err
	}
	prefix := fmt.Sprintf(`{"schema":%q,"type":%q,`, TelemetrySchema, messageType)
	frame := append([]byte(prefix), fields[1:]...)
	frame = append(frame, '\n')
	if len(frame) > MaxTelemetryFrameBytes {
		return nil, protocolError("frame-too-large", "telemetry frame exceeds limit")
	}
	return frame, nil
}

func validateTelemetry(message any) error {
	validateSeq := func(seq uint64) error {
		if seq < 1 || seq > MaxSequence {
			return protocolError("invalid-field", "seq is out of range")
		}
		return nil
	}
	validateRange := func(start, through uint64) error {
		if start > MaxOutputOffset || through > MaxOutputOffset || through < start {
			return protocolError("invalid-output-range", "invalid output range")
		}
		return nil
	}
	validateElapsed := func(elapsed uint64) error {
		if elapsed > MaxElapsedUS {
			return protocolError("invalid-field", "elapsed_us is out of range")
		}
		return nil
	}
	validateShellEnded := func(value *bool) error {
		if value != nil && !*value {
			return protocolError("invalid-field", "shell_ended is present only as true")
		}
		return nil
	}
	switch value := message.(type) {
	case Hello:
		return firstError(validateSeq(value.Seq), validateSessionID(value.SessionID))
	case Execute:
		if value.Inspections == nil {
			return protocolError("invalid-field", "inspections must be an array, including when empty")
		}
		return firstError(
			validateSeq(value.Seq),
			validateID("operation_id", value.OperationID),
			validateText("source", value.Source, MaxOperationSourceBytes),
			validateExecutionPolicy(value.ExecutionPolicy),
			validateInspectionSpecs(value.Inspections, value.Observation),
			validateInputThrough(value.InputThrough),
		)
	case Continue:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID), validateInputThrough(value.InputThrough))
	case Cancel:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateText("reason", value.Reason, MaxReasonBytes))
	case Finalize:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateText("reason", value.Reason, MaxReasonBytes))
	case Resize:
		return firstError(validateSeq(value.Seq), validateSize(value.Columns, value.Rows))
	case Shutdown:
		return firstError(validateSeq(value.Seq), validateText("reason", value.Reason, MaxReasonBytes))
	case Ready:
		if value.ElapsedUS != 0 {
			return protocolError("invalid-field", "ready carries elapsed_us 0")
		}
		return firstError(validateSeq(value.Seq), validatePID(value.EnvoyPID), validatePID(value.ShellPID), validateCWD(value.CWD), validateSize(value.Columns, value.Rows))
	case OperationStarted:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateRange(value.OutputStart, value.OutputStart))
	case OperationReady:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID), validateRange(0, value.OutputThrough))
	case OperationContinued:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID), validateRange(0, value.OutputThrough))
	case OperationGateInterrupted:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID), validateRange(0, value.OutputThrough))
	case OutputMark:
		if value.Stream != "pty" && value.Stream != "stdout" && value.Stream != "stderr" {
			return protocolError("invalid-field", "stream must be pty, stdout, or stderr")
		}
		return firstError(validateSeq(value.Seq), validateRange(0, value.Offset), validateElapsed(value.ElapsedUS))
	case OperationCompleted:
		if value.InspectionResults == nil {
			return protocolError("invalid-field", "inspection_results must be an array, including when empty")
		}
		return firstError(
			validateSeq(value.Seq),
			validateID("operation_id", value.OperationID),
			validateStatus(value.Status),
			validateCWD(value.CWD),
			validateRange(value.OutputStart, value.OutputThrough),
			validateInspectionResults(value.InspectionResults),
			validateShellEnded(value.ShellEnded),
		)
	case OperationCancelled:
		err := firstError(
			validateSeq(value.Seq),
			validateID("operation_id", value.OperationID),
			validateCWD(value.CWD),
			validateText("reason", value.Reason, MaxReasonBytes),
			validateRange(value.OutputStart, value.OutputThrough),
		)
		if err == nil && value.Status != nil {
			err = validateStatus(*value.Status)
		}
		return err
	case OperationFinalized:
		if value.InspectionResults == nil {
			return protocolError("invalid-field", "inspection_results must be an array, including when empty")
		}
		return firstError(
			validateSeq(value.Seq),
			validateID("operation_id", value.OperationID),
			validateCWD(value.CWD),
			validateText("reason", value.Reason, MaxReasonBytes),
			validateRange(value.OutputStart, value.OutputThrough),
			validateInspectionResults(value.InspectionResults),
		)
	case OperationFailed:
		if !OperationFailedCodes[value.Code] {
			return protocolError("invalid-field", "operation_failed code is outside the closed v1 set")
		}
		return firstError(
			validateSeq(value.Seq),
			validateID("operation_id", value.OperationID),
			validateCode(value.Code),
			validateText("message", value.Message, MaxDiagnosticBytes),
			validateCWD(value.CWD),
			validateRange(value.OutputStart, value.OutputThrough),
			validateShellEnded(value.ShellEnded),
		)
	case ResizeApplied:
		return firstError(validateSeq(value.Seq), validateSize(value.Columns, value.Rows), validateElapsed(value.ElapsedUS), validateRange(0, value.OutputThrough))
	case Diagnostic:
		err := firstError(validateSeq(value.Seq), validateSeverity(value.Severity), validateCode(value.Code), validateText("message", value.Message, MaxDiagnosticBytes))
		if err == nil && value.OperationID != nil {
			err = validateID("operation_id", *value.OperationID)
		}
		return err
	case Draining:
		return firstError(validateSeq(value.Seq), validateText("reason", value.Reason, MaxReasonBytes), validateRange(0, value.OutputThrough))
	case Closed:
		return firstError(validateSeq(value.Seq), validateText("reason", value.Reason, MaxReasonBytes), validateRange(0, value.OutputThrough))
	default:
		return fmt.Errorf("unsupported telemetry model %T", message)
	}
}

func validateExecutionPolicy(value ExecutionPolicy) error {
	if err := firstError(validateExecutionShape(value.ExecutionShape), validateObservation(value.Observation)); err != nil {
		return err
	}

	switch value.Timing {
	case TimingRealtime, TimingPresentation:
	default:
		return protocolError("invalid-field", "invalid timing")
	}
	switch value.Publication {
	case PublicationReal, PublicationSuppress, PublicationReplace:
	default:
		return protocolError("invalid-field", "invalid publication")
	}
	if value.Timing == TimingRealtime {
		if value.ExecutionShape != ExecutionPTY || value.Publication != PublicationReal {
			return protocolError("invalid-field", "realtime timing requires pty execution and real publication")
		}
	} else if value.ExecutionShape != ExecutionSplit || value.Observation != ObservationExclusive {
		return protocolError("invalid-field", "presentation timing requires split execution and exclusive observation")
	}
	if value.Publication != PublicationReal && value.Observation != ObservationExclusive {
		return protocolError("invalid-field", "suppressed and replaced output require exclusive observation")
	}
	return nil
}

func validateExecutionShape(value ExecutionShape) error {
	switch value {
	case ExecutionPTY, ExecutionSplit:
	default:
		return protocolError("invalid-field", "invalid execution_shape")
	}
	return nil
}

func validateObservation(value ObservationMode) error {
	switch value {
	case ObservationShared, ObservationExclusive:
	default:
		return protocolError("invalid-field", "invalid observation")
	}
	return nil
}

func validateInputThrough(value uint64) error {
	if value > MaxInputThrough {
		return protocolError("invalid-field", "input_through is out of range")
	}
	return nil
}

func validateSessionID(value string) error {
	if !sessionIDPattern.MatchString(value) {
		return protocolError("invalid-field", "session_id must be 128-bit lowercase hexadecimal")
	}
	return nil
}

func validateID(field, value string) error {
	if !identifierPattern.MatchString(value) {
		return protocolError("invalid-field", field+" is not identifier-like")
	}
	return nil
}
func validateCode(value string) error {
	if !codePattern.MatchString(value) {
		return protocolError("invalid-field", "code is not a protocol code")
	}
	return nil
}
func validateText(field, value string, maximum int) error {
	if value == "" || !utf8.ValidString(value) || strings.ContainsRune(value, 0) || len([]byte(value)) > maximum {
		return protocolError("invalid-field", field+" is empty, unsafe, or too large")
	}
	return nil
}
func validateCWD(value string) error {
	if err := validateText("cwd", value, MaxCWDBytes); err != nil {
		return err
	}
	if !strings.HasPrefix(value, "/") {
		return protocolError("invalid-field", "cwd must be absolute")
	}
	return nil
}
func validatePID(value int) error {
	if value < 1 || value > MaxPID {
		return protocolError("invalid-field", "pid is out of range")
	}
	return nil
}
func validateStatus(value int) error {
	if value < 0 || value > 255 {
		return protocolError("invalid-field", "status is out of range")
	}
	return nil
}
func validateSize(columns, rows int) error {
	if columns < MinColumns || columns > MaxColumns || rows < MinRows || rows > MaxRows {
		return protocolError("invalid-field", "terminal size is out of range")
	}
	return nil
}
func validateSeverity(value string) error {
	switch value {
	case "info", "warning", "error", "fatal":
		return nil
	default:
		return protocolError("invalid-field", "invalid severity")
	}
}
func firstError(errors ...error) error {
	for _, err := range errors {
		if err != nil {
			return err
		}
	}
	return nil
}
