// Package protocol defines the bounded OmegaFlow Envoy v1 wire contracts.
package protocol

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	TelemetrySchema         = "omegaflow-envoy-telemetry-v1"
	AwshSchema              = "awsh-v1"
	MaxTelemetryFrameBytes  = 1_048_576
	MaxAwshFrameBytes       = 1_048_576
	MaxOperationSourceBytes = 786_432
	MaxDiagnosticBytes      = 4_096
	MaxReasonBytes          = 256
	MaxCWDBytes             = 4_096
	MaxSequence             = 1<<63 - 1
	MaxOutputOffset         = 1<<63 - 1
	MaxLogicalStreamBytes   = 8 * 1_024 * 1_024
	MaxLogicalChunkBytes    = 192 * 1_024
	MaxPID                  = 1<<31 - 1
	MinColumns              = 1
	MaxColumns              = 1_000
	MinRows                 = 1
	MaxRows                 = 1_000
)

var (
	identifierPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	codePattern       = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
)

var telemetryFields = map[string]struct {
	required map[string]bool
	optional map[string]bool
}{
	"hello":               fieldSet("seq", "session_id"),
	"execute":             fieldSet("seq", "operation_id", "source", "execution_shape", "timing", "publication", "observation"),
	"continue":            fieldSet("seq", "operation_id", "gate_id"),
	"cancel":              fieldSet("seq", "operation_id", "reason"),
	"finalize":            fieldSet("seq", "operation_id", "reason"),
	"resize":              fieldSet("seq", "columns", "rows"),
	"shutdown":            fieldSet("seq", "reason"),
	"ready":               fieldSet("seq", "envoy_pid", "shell_pid", "cwd", "columns", "rows"),
	"operation_started":   fieldSet("seq", "operation_id", "output_start"),
	"operation_ready":     fieldSet("seq", "operation_id", "gate_id", "output_through"),
	"operation_continued": fieldSet("seq", "operation_id", "gate_id", "output_through"),
	"operation_output":    fieldSet("seq", "operation_id", "stream", "data_base64"),
	"operation_completed": fieldSet("seq", "operation_id", "status", "cwd", "output_start", "output_through"),
	"operation_cancelled": fieldSet("seq", "operation_id", "status", "cwd", "reason", "output_start", "output_through"),
	"operation_finalized": fieldSet("seq", "operation_id", "cwd", "reason", "output_start", "output_through"),
	"operation_failed":    fieldSet("seq", "operation_id", "code", "message", "cwd", "output_start", "output_through"),
	"resize_applied":      fieldSet("seq", "columns", "rows"),
	"diagnostic":          fieldSetOptional([]string{"seq", "severity", "code", "message"}, "operation_id"),
	"draining":            fieldSet("seq", "reason", "output_through"),
	"closed":              fieldSet("seq", "reason", "output_through"),
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
// or separate logical stdout and stderr pipes.
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

// ObservationMode selects whether operation output may be attributed to the
// operation without other supervised writers sharing its boundary.
type ObservationMode string

const (
	ObservationShared    ObservationMode = "shared"
	ObservationExclusive ObservationMode = "exclusive"
)

// ExecutionPolicy is compiled by the controller before execute. It contains
// no authored replacement text or presentation delay values.
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
}
type Continue struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	GateID      string `json:"gate_id"`
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
type OperationOutput struct {
	Seq         uint64 `json:"seq"`
	OperationID string `json:"operation_id"`
	Stream      string `json:"stream"`
	DataBase64  string `json:"data_base64"`
}
type OperationCompleted struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	Status        int    `json:"status"`
	CWD           string `json:"cwd"`
	OutputStart   uint64 `json:"output_start"`
	OutputThrough uint64 `json:"output_through"`
}
type OperationCancelled struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	Status        int    `json:"status"`
	CWD           string `json:"cwd"`
	Reason        string `json:"reason"`
	OutputStart   uint64 `json:"output_start"`
	OutputThrough uint64 `json:"output_through"`
}
type OperationFinalized struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	CWD           string `json:"cwd"`
	Reason        string `json:"reason"`
	OutputStart   uint64 `json:"output_start"`
	OutputThrough uint64 `json:"output_through"`
}
type OperationFailed struct {
	Seq           uint64 `json:"seq"`
	OperationID   string `json:"operation_id"`
	Code          string `json:"code"`
	Message       string `json:"message"`
	CWD           string `json:"cwd"`
	OutputStart   uint64 `json:"output_start"`
	OutputThrough uint64 `json:"output_through"`
}
type ResizeApplied struct {
	Seq     uint64 `json:"seq"`
	Columns int    `json:"columns"`
	Rows    int    `json:"rows"`
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

type AwshExecute struct {
	OperationID    string
	ExecutionShape ExecutionShape
	Observation    ObservationMode
	Source         string
}
type AwshContinue struct {
	OperationID string
	GateID      string
}
type AwshCancel struct {
	OperationID string
	Reason      string
}
type AwshFinalize struct {
	OperationID string
	Reason      string
}
type AwshShutdown struct{}
type AwshReady struct {
	ShellPID int
	CWD      string
}
type AwshStarted struct{ OperationID string }
type AwshGateReady struct {
	OperationID string
	GateID      string
}
type AwshGateContinued struct {
	OperationID string
	GateID      string
}
type AwshCompleted struct {
	OperationID string
	Status      int
	CWD         string
}
type AwshProtocolError struct {
	Code    string
	Message string
}
type AwshClosed struct {
	Reason string
	CWD    string
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
	case "operation_output":
		var wire struct {
			telemetryHeader
			OperationOutput
		}
		err = decodeExact(body, &wire)
		message = wire.OperationOutput
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

func (decoder *StreamDecoder) Finish() error {
	if len(decoder.buffer) != 0 {
		return protocolError("early-close", "telemetry closed mid-frame")
	}
	return nil
}

// AwshStreamDecoder incrementally decodes fixed-arity NUL-delimited frames.
type AwshStreamDecoder struct {
	buffer            []byte
	fields            [][]byte
	frameBytes        int
	result            bool
	shutdownRequested bool
}

func NewAwshRequestStreamDecoder() *AwshStreamDecoder {
	return &AwshStreamDecoder{}
}

func NewAwshResultStreamDecoder() *AwshStreamDecoder {
	return &AwshStreamDecoder{result: true}
}

func (decoder *AwshStreamDecoder) Feed(data []byte) ([]any, error) {
	var messages []any
	for len(data) != 0 {
		delimiter := bytes.IndexByte(data, 0)
		remaining := MaxAwshFrameBytes - decoder.frameBytes - len(decoder.buffer)
		if delimiter < 0 {
			if len(data) >= remaining {
				return nil, protocolError("frame-too-large", "unterminated awsh frame")
			}
			decoder.buffer = append(decoder.buffer, data...)
			return messages, nil
		}
		if delimiter+1 > remaining {
			return nil, protocolError("frame-too-large", "awsh frame exceeds limit")
		}
		field := make([]byte, 0, len(decoder.buffer)+delimiter)
		field = append(field, decoder.buffer...)
		field = append(field, data[:delimiter]...)
		decoder.buffer = nil
		data = data[delimiter+1:]
		decoder.fields = append(decoder.fields, field)
		decoder.frameBytes += len(field) + 1
		if len(decoder.fields) < 2 {
			continue
		}
		expected, err := decoder.expectedFields()
		if err != nil {
			return nil, err
		}
		if len(decoder.fields) != expected {
			continue
		}
		frame := bytes.Join(decoder.fields, []byte{0})
		frame = append(frame, 0)
		var message any
		if decoder.result {
			message, err = DecodeAwshResult(frame)
		} else {
			message, err = DecodeAwshRequest(frame)
		}
		if err != nil {
			return nil, err
		}
		if _, shutdown := message.(AwshShutdown); shutdown {
			decoder.shutdownRequested = true
		}
		messages = append(messages, message)
		decoder.fields = nil
		decoder.frameBytes = 0
	}
	return messages, nil
}

func (decoder *AwshStreamDecoder) expectedFields() (int, error) {
	if !utf8.Valid(decoder.fields[0]) || !utf8.Valid(decoder.fields[1]) {
		return 0, protocolError("invalid-utf8", "awsh header is not UTF-8")
	}
	if string(decoder.fields[0]) != AwshSchema {
		return 0, protocolError("unsupported-schema", "unsupported awsh schema")
	}
	messageType := string(decoder.fields[1])
	if decoder.result {
		switch messageType {
		case "ready", "gate_ready", "gate_continued", "protocol_error", "closed":
			return 4, nil
		case "started":
			return 3, nil
		case "completed":
			return 5, nil
		default:
			return 0, protocolError("unsupported-message", "unsupported awsh result")
		}
	}
	switch messageType {
	case "continue", "cancel", "finalize":
		return 4, nil
	case "execute":
		return 6, nil
	case "shutdown":
		return 2, nil
	default:
		return 0, protocolError("unsupported-message", "unsupported awsh request")
	}
}

// MarkShutdownRequested records the Envoy's shutdown request for a result
// stream. Callers must invoke it when the corresponding request is sent.
func (decoder *AwshStreamDecoder) MarkShutdownRequested() error {
	if !decoder.result {
		return protocolError("out-of-state", "shutdown context belongs to an awsh result stream")
	}
	decoder.shutdownRequested = true
	return nil
}

func (decoder *AwshStreamDecoder) Finish() error {
	if len(decoder.buffer) != 0 || len(decoder.fields) != 0 {
		return protocolError("early-close", "awsh stream closed mid-frame")
	}
	if !decoder.shutdownRequested {
		return protocolError("early-close", "awsh stream closed before shutdown")
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
	case OperationOutput:
		messageType = "operation_output"
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

func encodeTelemetry(messageType string, message any) ([]byte, error) {
	fields, err := json.Marshal(message)
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
	switch value := message.(type) {
	case Hello:
		return firstError(validateSeq(value.Seq), validateID("session_id", value.SessionID))
	case Execute:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateText("source", value.Source, MaxOperationSourceBytes), validateExecutionPolicy(value.ExecutionPolicy))
	case Continue:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID))
	case Cancel:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateText("reason", value.Reason, MaxReasonBytes))
	case Finalize:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateText("reason", value.Reason, MaxReasonBytes))
	case Resize:
		return firstError(validateSeq(value.Seq), validateSize(value.Columns, value.Rows))
	case Shutdown:
		return firstError(validateSeq(value.Seq), validateText("reason", value.Reason, MaxReasonBytes))
	case Ready:
		return firstError(validateSeq(value.Seq), validatePID(value.EnvoyPID), validatePID(value.ShellPID), validateCWD(value.CWD), validateSize(value.Columns, value.Rows))
	case OperationStarted:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateRange(value.OutputStart, value.OutputStart))
	case OperationReady:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID), validateRange(0, value.OutputThrough))
	case OperationContinued:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID), validateRange(0, value.OutputThrough))
	case OperationOutput:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateLogicalOutput(value.Stream, value.DataBase64))
	case OperationCompleted:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateStatus(value.Status), validateCWD(value.CWD), validateRange(value.OutputStart, value.OutputThrough))
	case OperationCancelled:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateStatus(value.Status), validateCWD(value.CWD), validateText("reason", value.Reason, MaxReasonBytes), validateRange(value.OutputStart, value.OutputThrough))
	case OperationFinalized:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateCWD(value.CWD), validateText("reason", value.Reason, MaxReasonBytes), validateRange(value.OutputStart, value.OutputThrough))
	case OperationFailed:
		return firstError(validateSeq(value.Seq), validateID("operation_id", value.OperationID), validateCode(value.Code), validateText("message", value.Message, MaxDiagnosticBytes), validateCWD(value.CWD), validateRange(value.OutputStart, value.OutputThrough))
	case ResizeApplied:
		return firstError(validateSeq(value.Seq), validateSize(value.Columns, value.Rows))
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
			return protocolError("invalid-field", "realtime publication requires pty execution and real output")
		}
	} else if value.ExecutionShape != ExecutionSplit {
		return protocolError("invalid-field", "presentation timing requires split execution")
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

func validateLogicalOutput(stream, encoded string) error {
	if stream != "stdout" && stream != "stderr" {
		return protocolError("invalid-field", "stream must be stdout or stderr")
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(decoded) == 0 || len(decoded) > MaxLogicalChunkBytes || base64.StdEncoding.EncodeToString(decoded) != encoded {
		return protocolError("invalid-field", "data_base64 is not canonical bounded base64")
	}
	return nil
}

func logicalOutputBytes(encoded string) int {
	decoded, _ := base64.StdEncoding.Strict().DecodeString(encoded)
	return len(decoded)
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

func EncodeAwshRequest(message any) ([]byte, error) {
	switch value := message.(type) {
	case AwshExecute:
		if err := firstError(validateID("operation_id", value.OperationID), validateText("source", value.Source, MaxOperationSourceBytes), validateExecutionShape(value.ExecutionShape), validateObservation(value.Observation)); err != nil {
			return nil, err
		}
		return encodeAwsh("execute", value.OperationID, string(value.ExecutionShape), string(value.Observation), value.Source)
	case AwshContinue:
		if err := firstError(validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID)); err != nil {
			return nil, err
		}
		return encodeAwsh("continue", value.OperationID, value.GateID)
	case AwshCancel:
		if err := firstError(validateID("operation_id", value.OperationID), validateText("reason", value.Reason, MaxReasonBytes)); err != nil {
			return nil, err
		}
		return encodeAwsh("cancel", value.OperationID, value.Reason)
	case AwshFinalize:
		if err := firstError(validateID("operation_id", value.OperationID), validateText("reason", value.Reason, MaxReasonBytes)); err != nil {
			return nil, err
		}
		return encodeAwsh("finalize", value.OperationID, value.Reason)
	case AwshShutdown:
		return encodeAwsh("shutdown")
	default:
		return nil, fmt.Errorf("unsupported awsh request %T", message)
	}
}

func EncodeAwshResult(message any) ([]byte, error) {
	switch value := message.(type) {
	case AwshReady:
		if err := firstError(validatePID(value.ShellPID), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("ready", strconv.Itoa(value.ShellPID), value.CWD)
	case AwshStarted:
		if err := validateID("operation_id", value.OperationID); err != nil {
			return nil, err
		}
		return encodeAwsh("started", value.OperationID)
	case AwshGateReady:
		if err := firstError(validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID)); err != nil {
			return nil, err
		}
		return encodeAwsh("gate_ready", value.OperationID, value.GateID)
	case AwshGateContinued:
		if err := firstError(validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID)); err != nil {
			return nil, err
		}
		return encodeAwsh("gate_continued", value.OperationID, value.GateID)
	case AwshCompleted:
		if err := firstError(validateID("operation_id", value.OperationID), validateStatus(value.Status), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("completed", value.OperationID, strconv.Itoa(value.Status), value.CWD)
	case AwshProtocolError:
		if err := firstError(validateCode(value.Code), validateText("message", value.Message, MaxDiagnosticBytes)); err != nil {
			return nil, err
		}
		return encodeAwsh("protocol_error", value.Code, value.Message)
	case AwshClosed:
		if err := firstError(validateText("reason", value.Reason, MaxReasonBytes), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("closed", value.Reason, value.CWD)
	default:
		return nil, fmt.Errorf("unsupported awsh result %T", message)
	}
}

func encodeAwsh(messageType string, fields ...string) ([]byte, error) {
	all := append([]string{AwshSchema, messageType}, fields...)
	frame := []byte(strings.Join(all, "\x00") + "\x00")
	if len(frame) > MaxAwshFrameBytes {
		return nil, protocolError("frame-too-large", "awsh frame exceeds limit")
	}
	return frame, nil
}

func DecodeAwshRequest(frame []byte) (any, error) {
	fields, err := awshFields(frame)
	if err != nil {
		return nil, err
	}
	switch fields[1] {
	case "execute":
		if err := requireAwshFields(fields, 6); err != nil {
			return nil, err
		}
		value := AwshExecute{OperationID: fields[2], ExecutionShape: ExecutionShape(fields[3]), Observation: ObservationMode(fields[4]), Source: fields[5]}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "continue":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshContinue{OperationID: fields[2], GateID: fields[3]}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "cancel":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshCancel{OperationID: fields[2], Reason: fields[3]}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "finalize":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshFinalize{OperationID: fields[2], Reason: fields[3]}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "shutdown":
		if err := requireAwshFields(fields, 2); err != nil {
			return nil, err
		}
		return AwshShutdown{}, nil
	default:
		return nil, protocolError("unsupported-message", "unsupported awsh request")
	}
}

func DecodeAwshResult(frame []byte) (any, error) {
	fields, err := awshFields(frame)
	if err != nil {
		return nil, err
	}
	switch fields[1] {
	case "ready":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		pid, err := strconv.Atoi(fields[2])
		if err != nil {
			return nil, protocolError("invalid-field", "shell_pid must be an integer")
		}
		value := AwshReady{ShellPID: pid, CWD: fields[3]}
		_, err = EncodeAwshResult(value)
		return value, err
	case "started":
		if err := requireAwshFields(fields, 3); err != nil {
			return nil, err
		}
		value := AwshStarted{OperationID: fields[2]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "gate_ready", "gate_continued":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		if fields[1] == "gate_ready" {
			value := AwshGateReady{OperationID: fields[2], GateID: fields[3]}
			_, err := EncodeAwshResult(value)
			return value, err
		}
		value := AwshGateContinued{OperationID: fields[2], GateID: fields[3]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "completed":
		if err := requireAwshFields(fields, 5); err != nil {
			return nil, err
		}
		status, err := strconv.Atoi(fields[3])
		if err != nil {
			return nil, protocolError("invalid-field", "status must be an integer")
		}
		value := AwshCompleted{OperationID: fields[2], Status: status, CWD: fields[4]}
		_, err = EncodeAwshResult(value)
		return value, err
	case "protocol_error":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshProtocolError{Code: fields[2], Message: fields[3]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "closed":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshClosed{Reason: fields[2], CWD: fields[3]}
		_, err := EncodeAwshResult(value)
		return value, err
	default:
		return nil, protocolError("unsupported-message", "unsupported awsh result")
	}
}

func awshFields(frame []byte) ([]string, error) {
	if len(frame) > MaxAwshFrameBytes {
		return nil, protocolError("frame-too-large", "awsh frame exceeds limit")
	}
	if len(frame) == 0 || frame[len(frame)-1] != 0 {
		return nil, protocolError("early-close", "awsh frame is not terminated")
	}
	raw := bytes.Split(frame[:len(frame)-1], []byte{0})
	fields := make([]string, len(raw))
	for index, field := range raw {
		if !utf8.Valid(field) {
			return nil, protocolError("invalid-utf8", "awsh frame is not UTF-8")
		}
		fields[index] = string(field)
	}
	if len(fields) < 2 || fields[0] != AwshSchema {
		return nil, protocolError("unsupported-schema", "unsupported awsh schema")
	}
	return fields, nil
}

func requireAwshFields(fields []string, expected int) error {
	if len(fields) != expected {
		return protocolError("invalid-field-count", "invalid awsh field count")
	}
	return nil
}
