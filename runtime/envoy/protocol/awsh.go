package protocol

import (
	"bytes"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	AwshSchema        = "awsh-v1"
	MaxAwshFrameBytes = 1_048_576

	// BracketedPasteBegin and BracketedPasteEnd are the exact six-byte
	// Readline bracketed-paste framing sequences. The terminator bytes are
	// reserved: source containing them is rejected as source-invalid before
	// arming or constructing a submission, whatever Bash lexical context
	// they appear in.
	BracketedPasteBegin = "\x1b[200~"
	BracketedPasteEnd   = "\x1b[201~"

	// InputStateFunctionName is the immutable readonly Bash condition that
	// restores history state and returns the captured status. Its exact
	// name is a forbidden alias key.
	InputStateFunctionName = "__awsh_restore_input_state"

	// MaxCapsuleOverheadBytes bounds every generated Bash-capsule byte
	// other than the two source copies.
	MaxCapsuleOverheadBytes = 32_768

	// MaxTerminalSubmissionBytes is the doubled-source capsule maximum:
	// the 491,520-byte source maximum twice, plus the bounded generated
	// frame, begin/end sequences, and LF.
	MaxTerminalSubmissionBytes = 1_015_808

	// MaxSubmitFrameBytes bounds the complete private submit frame: with a
	// 64-byte operation ID and every NUL separator it stays below the
	// private-frame limit.
	MaxSubmitFrameBytes = 1_015_889

	MaxPrivateFIFOPathBytes = 4_096
)

// DispositionKind is the closed private cancel/finalize request-kind set.
const (
	DispositionCancel   = "cancel"
	DispositionFinalize = "finalize"
)

// Disposition phases are the closed private acknowledgement set for one
// accepted cancel or finalize request.
const (
	PhaseDisarmed           = "disarmed"
	PhaseSignal             = "signal"
	PhaseGateCancelled      = "gate-cancelled"
	PhaseSettled            = "settled"
	PhaseAlreadyInterrupted = "already-interrupted"
)

// Private closed reasons: controller-authored shutdown reasons remain public
// Envoy state and are never copied into this field.
const (
	ClosedReasonShutdown   = "shutdown"
	ClosedReasonShellEnded = "shell_ended"
)

var fifoPathPattern = regexp.MustCompile(`^/[A-Za-z0-9._/-]+$`)

type AwshExecute struct {
	OperationID     string
	ExecutionShape  ExecutionShape
	Observation     ObservationMode
	InspectionsJSON string
	StdoutFIFO      string
	StderrFIFO      string
	Source          string
}
type AwshContinue struct {
	OperationID string
	GateID      string
}
type AwshGateInterruptAck struct {
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
type AwshStartedAck struct{ OperationID string }
type AwshResizePrepare struct {
	Columns int
	Rows    int
}
type AwshResizeApply struct {
	Columns int
	Rows    int
}
type AwshShutdown struct{}

type AwshReady struct {
	AwshPID  int
	ShellPID int
	CWD      string
}
type AwshSubmit struct {
	OperationID        string
	TerminalSubmission string
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

// AwshGateInterrupt is a proposal, not a completed gate decision: Awsh keeps
// the helper blocked until exactly one of gate_interrupt_ack, continue,
// cancel, or finalize commits the waiting gate's outcome.
type AwshGateInterrupt struct {
	OperationID string
	GateID      string
}
type AwshDisposition struct {
	OperationID string
	RequestKind string
	Phase       string
}
type AwshCompleted struct {
	OperationID             string
	Status                  int
	CWD                     string
	ResolvedInspectionsJSON string
}
type AwshRejected struct {
	OperationID string
	Code        string
	Message     string
	CWD         string
}

// AwshShellExit reports a reaped selected shell explicitly; private-channel
// EOF is never a substitute. The operation ID is empty for an idle shell
// exit.
type AwshShellExit struct {
	OperationID string
	Status      int
	CWD         string
}
type AwshResizeReady struct {
	Columns int
	Rows    int
}
type AwshResized struct {
	Columns int
	Rows    int
}
type AwshProtocolError struct {
	Code    string
	Message string
}
type AwshClosed struct {
	Reason string
	Status int
	CWD    string
}

// EncodeAwshRequest returns the canonical NUL-framed Envoy-to-awsh request.
func EncodeAwshRequest(message any) ([]byte, error) {
	switch value := message.(type) {
	case AwshExecute:
		if err := validateAwshExecute(value); err != nil {
			return nil, err
		}
		return encodeAwsh("execute", value.OperationID, string(value.ExecutionShape), string(value.Observation), value.InspectionsJSON, value.StdoutFIFO, value.StderrFIFO, value.Source)
	case AwshContinue:
		if err := firstError(validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID)); err != nil {
			return nil, err
		}
		return encodeAwsh("continue", value.OperationID, value.GateID)
	case AwshGateInterruptAck:
		if err := firstError(validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID)); err != nil {
			return nil, err
		}
		return encodeAwsh("gate_interrupt_ack", value.OperationID, value.GateID)
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
	case AwshStartedAck:
		if err := validateID("operation_id", value.OperationID); err != nil {
			return nil, err
		}
		return encodeAwsh("started_ack", value.OperationID)
	case AwshResizePrepare:
		if err := validateSize(value.Columns, value.Rows); err != nil {
			return nil, err
		}
		return encodeAwsh("resize_prepare", strconv.Itoa(value.Columns), strconv.Itoa(value.Rows))
	case AwshResizeApply:
		if err := validateSize(value.Columns, value.Rows); err != nil {
			return nil, err
		}
		return encodeAwsh("resize_apply", strconv.Itoa(value.Columns), strconv.Itoa(value.Rows))
	case AwshShutdown:
		return encodeAwsh("shutdown")
	default:
		return nil, fmt.Errorf("unsupported awsh request %T", message)
	}
}

func validateAwshExecute(value AwshExecute) error {
	if err := firstError(
		validateID("operation_id", value.OperationID),
		validateExecutionShape(value.ExecutionShape),
		validateObservation(value.Observation),
		validateText("source", value.Source, MaxOperationSourceBytes),
	); err != nil {
		return err
	}
	specs, err := DecodeInspectionSpecs([]byte(value.InspectionsJSON))
	if err != nil {
		return err
	}
	if len(specs) > 0 && value.Observation != ObservationExclusive {
		return protocolError("invalid-field", "an operation with inspections requires exclusive observation")
	}
	if value.ExecutionShape == ExecutionPTY {
		if value.StdoutFIFO != "" || value.StderrFIFO != "" {
			return protocolError("invalid-field", "pty execution carries empty FIFO fields")
		}
		return nil
	}
	return firstError(validateFIFOPath("stdout_fifo", value.StdoutFIFO), validateFIFOPath("stderr_fifo", value.StderrFIFO))
}

func validateFIFOPath(field, value string) error {
	if len(value) == 0 || len(value) > MaxPrivateFIFOPathBytes || !fifoPathPattern.MatchString(value) {
		return protocolError("invalid-field", field+" must be one bounded absolute private path")
	}
	return nil
}

// EncodeAwshResult returns the canonical NUL-framed awsh-to-Envoy result.
func EncodeAwshResult(message any) ([]byte, error) {
	switch value := message.(type) {
	case AwshReady:
		if err := firstError(validatePID(value.AwshPID), validatePID(value.ShellPID), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("ready", strconv.Itoa(value.AwshPID), strconv.Itoa(value.ShellPID), value.CWD)
	case AwshSubmit:
		if err := validateID("operation_id", value.OperationID); err != nil {
			return nil, err
		}
		if err := ValidateTerminalSubmission([]byte(value.TerminalSubmission)); err != nil {
			return nil, err
		}
		frame, err := encodeAwsh("submit", value.OperationID, value.TerminalSubmission)
		if err != nil {
			return nil, err
		}
		if len(frame) > MaxSubmitFrameBytes {
			return nil, protocolError("frame-too-large", "submit frame exceeds its envelope limit")
		}
		return frame, nil
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
	case AwshGateInterrupt:
		if err := firstError(validateID("operation_id", value.OperationID), validateID("gate_id", value.GateID)); err != nil {
			return nil, err
		}
		return encodeAwsh("gate_interrupt", value.OperationID, value.GateID)
	case AwshDisposition:
		if err := validateID("operation_id", value.OperationID); err != nil {
			return nil, err
		}
		if value.RequestKind != DispositionCancel && value.RequestKind != DispositionFinalize {
			return nil, protocolError("invalid-field", "disposition request kind must be cancel or finalize")
		}
		switch value.Phase {
		case PhaseDisarmed, PhaseSignal, PhaseGateCancelled, PhaseSettled, PhaseAlreadyInterrupted:
		default:
			return nil, protocolError("invalid-field", "disposition phase is outside the closed set")
		}
		return encodeAwsh("disposition", value.OperationID, value.RequestKind, value.Phase)
	case AwshCompleted:
		if err := firstError(validateID("operation_id", value.OperationID), validateStatus(value.Status), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		if _, err := DecodeResolvedInspections([]byte(value.ResolvedInspectionsJSON)); err != nil {
			return nil, err
		}
		return encodeAwsh("completed", value.OperationID, strconv.Itoa(value.Status), value.CWD, value.ResolvedInspectionsJSON)
	case AwshRejected:
		if value.Code != "source-invalid" && value.Code != "source-unsupported" {
			return nil, protocolError("invalid-field", "rejection code must be source-invalid or source-unsupported")
		}
		if err := firstError(validateID("operation_id", value.OperationID), validateText("message", value.Message, MaxDiagnosticBytes), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("rejected", value.OperationID, value.Code, value.Message, value.CWD)
	case AwshShellExit:
		if value.OperationID != "" {
			if err := validateID("operation_id", value.OperationID); err != nil {
				return nil, err
			}
		}
		if err := firstError(validateStatus(value.Status), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("shell_exit", value.OperationID, strconv.Itoa(value.Status), value.CWD)
	case AwshResizeReady:
		if err := validateSize(value.Columns, value.Rows); err != nil {
			return nil, err
		}
		return encodeAwsh("resize_ready", strconv.Itoa(value.Columns), strconv.Itoa(value.Rows))
	case AwshResized:
		if err := validateSize(value.Columns, value.Rows); err != nil {
			return nil, err
		}
		return encodeAwsh("resized", strconv.Itoa(value.Columns), strconv.Itoa(value.Rows))
	case AwshProtocolError:
		if err := firstError(validateCode(value.Code), validateText("message", value.Message, MaxDiagnosticBytes)); err != nil {
			return nil, err
		}
		return encodeAwsh("protocol_error", value.Code, value.Message)
	case AwshClosed:
		if value.Reason != ClosedReasonShutdown && value.Reason != ClosedReasonShellEnded {
			return nil, protocolError("invalid-field", "private closed reason must be shutdown or shell_ended")
		}
		if err := firstError(validateStatus(value.Status), validateCWD(value.CWD)); err != nil {
			return nil, err
		}
		return encodeAwsh("closed", value.Reason, strconv.Itoa(value.Status), value.CWD)
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

// DecodeAwshRequest decodes one complete Envoy-to-awsh request frame.
func DecodeAwshRequest(frame []byte) (any, error) {
	fields, err := awshFields(frame)
	if err != nil {
		return nil, err
	}
	switch fields[1] {
	case "execute":
		if err := requireAwshFields(fields, 9); err != nil {
			return nil, err
		}
		value := AwshExecute{
			OperationID:     fields[2],
			ExecutionShape:  ExecutionShape(fields[3]),
			Observation:     ObservationMode(fields[4]),
			InspectionsJSON: fields[5],
			StdoutFIFO:      fields[6],
			StderrFIFO:      fields[7],
			Source:          fields[8],
		}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "continue":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshContinue{OperationID: fields[2], GateID: fields[3]}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "gate_interrupt_ack":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshGateInterruptAck{OperationID: fields[2], GateID: fields[3]}
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
	case "started_ack":
		if err := requireAwshFields(fields, 3); err != nil {
			return nil, err
		}
		value := AwshStartedAck{OperationID: fields[2]}
		_, err := EncodeAwshRequest(value)
		return value, err
	case "resize_prepare", "resize_apply":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		columns, rows, err := awshSize(fields[2], fields[3])
		if err != nil {
			return nil, err
		}
		if fields[1] == "resize_prepare" {
			return AwshResizePrepare{Columns: columns, Rows: rows}, nil
		}
		return AwshResizeApply{Columns: columns, Rows: rows}, nil
	case "shutdown":
		if err := requireAwshFields(fields, 2); err != nil {
			return nil, err
		}
		return AwshShutdown{}, nil
	default:
		return nil, protocolError("unsupported-message", "unsupported awsh request")
	}
}

// DecodeAwshResult decodes one complete awsh-to-Envoy result frame.
func DecodeAwshResult(frame []byte) (any, error) {
	fields, err := awshFields(frame)
	if err != nil {
		return nil, err
	}
	switch fields[1] {
	case "ready":
		if err := requireAwshFields(fields, 5); err != nil {
			return nil, err
		}
		awshPID, err := awshInteger("awsh_pid", fields[2])
		if err != nil {
			return nil, err
		}
		shellPID, err := awshInteger("shell_pid", fields[3])
		if err != nil {
			return nil, err
		}
		value := AwshReady{AwshPID: awshPID, ShellPID: shellPID, CWD: fields[4]}
		_, err = EncodeAwshResult(value)
		return value, err
	case "submit":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshSubmit{OperationID: fields[2], TerminalSubmission: fields[3]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "started":
		if err := requireAwshFields(fields, 3); err != nil {
			return nil, err
		}
		value := AwshStarted{OperationID: fields[2]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "gate_ready", "gate_continued", "gate_interrupt":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		switch fields[1] {
		case "gate_ready":
			value := AwshGateReady{OperationID: fields[2], GateID: fields[3]}
			_, err := EncodeAwshResult(value)
			return value, err
		case "gate_continued":
			value := AwshGateContinued{OperationID: fields[2], GateID: fields[3]}
			_, err := EncodeAwshResult(value)
			return value, err
		default:
			value := AwshGateInterrupt{OperationID: fields[2], GateID: fields[3]}
			_, err := EncodeAwshResult(value)
			return value, err
		}
	case "disposition":
		if err := requireAwshFields(fields, 5); err != nil {
			return nil, err
		}
		value := AwshDisposition{OperationID: fields[2], RequestKind: fields[3], Phase: fields[4]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "completed":
		if err := requireAwshFields(fields, 6); err != nil {
			return nil, err
		}
		status, err := awshInteger("status", fields[3])
		if err != nil {
			return nil, err
		}
		value := AwshCompleted{OperationID: fields[2], Status: status, CWD: fields[4], ResolvedInspectionsJSON: fields[5]}
		_, err = EncodeAwshResult(value)
		return value, err
	case "rejected":
		if err := requireAwshFields(fields, 6); err != nil {
			return nil, err
		}
		value := AwshRejected{OperationID: fields[2], Code: fields[3], Message: fields[4], CWD: fields[5]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "shell_exit":
		if err := requireAwshFields(fields, 5); err != nil {
			return nil, err
		}
		status, err := awshInteger("status", fields[3])
		if err != nil {
			return nil, err
		}
		value := AwshShellExit{OperationID: fields[2], Status: status, CWD: fields[4]}
		_, err = EncodeAwshResult(value)
		return value, err
	case "resize_ready", "resized":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		columns, rows, err := awshSize(fields[2], fields[3])
		if err != nil {
			return nil, err
		}
		if fields[1] == "resize_ready" {
			return AwshResizeReady{Columns: columns, Rows: rows}, nil
		}
		return AwshResized{Columns: columns, Rows: rows}, nil
	case "protocol_error":
		if err := requireAwshFields(fields, 4); err != nil {
			return nil, err
		}
		value := AwshProtocolError{Code: fields[2], Message: fields[3]}
		_, err := EncodeAwshResult(value)
		return value, err
	case "closed":
		if err := requireAwshFields(fields, 5); err != nil {
			return nil, err
		}
		status, err := awshInteger("status", fields[3])
		if err != nil {
			return nil, err
		}
		value := AwshClosed{Reason: fields[2], Status: status, CWD: fields[4]}
		_, err = EncodeAwshResult(value)
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

func awshInteger(field, value string) (int, error) {
	parsed, err := strconv.Atoi(value)
	if err != nil || strconv.Itoa(parsed) != value {
		return 0, protocolError("invalid-field", field+" must be one canonical decimal integer")
	}
	return parsed, nil
}

func awshSize(columnsField, rowsField string) (int, int, error) {
	columns, err := awshInteger("columns", columnsField)
	if err != nil {
		return 0, 0, err
	}
	rows, err := awshInteger("rows", rowsField)
	if err != nil {
		return 0, 0, err
	}
	if err := validateSize(columns, rows); err != nil {
		return 0, 0, err
	}
	return columns, rows, nil
}

// AwshStreamDecoder incrementally decodes fixed-arity NUL-delimited frames
// for one private descriptor direction.
type AwshStreamDecoder struct {
	buffer     []byte
	fields     [][]byte
	frameBytes int
	result     bool
	closed     bool
	shutdown   bool
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
		// closed and shutdown are terminal on their descriptors: no
		// later private frame, or partial byte, is ever accepted.
		if (decoder.result && decoder.closed) || (!decoder.result && decoder.shutdown) {
			return nil, protocolError("out-of-state", "no private frame is accepted after a terminal message")
		}
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
		if _, isShutdown := message.(AwshShutdown); isShutdown {
			decoder.shutdown = true
		}
		if _, isClosed := message.(AwshClosed); isClosed {
			decoder.closed = true
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
		case "started":
			return 3, nil
		case "submit", "gate_ready", "gate_continued", "gate_interrupt", "resize_ready", "resized", "protocol_error":
			return 4, nil
		case "ready", "disposition", "shell_exit", "closed":
			return 5, nil
		case "completed", "rejected":
			return 6, nil
		default:
			return 0, protocolError("unsupported-message", "unsupported awsh result")
		}
	}
	switch messageType {
	case "shutdown":
		return 2, nil
	case "started_ack":
		return 3, nil
	case "continue", "gate_interrupt_ack", "cancel", "finalize", "resize_prepare", "resize_apply":
		return 4, nil
	case "execute":
		return 9, nil
	default:
		return 0, protocolError("unsupported-message", "unsupported awsh request")
	}
}

// Finish reports whether this direction may close where it did. EOF on the
// result descriptor before a valid closed result is an Awsh-supervisor
// failure, never evidence that Bash exited; EOF on the request descriptor
// before a decoded shutdown request is the same failure from Awsh's side.
func (decoder *AwshStreamDecoder) Finish() error {
	if len(decoder.buffer) != 0 || len(decoder.fields) != 0 {
		return protocolError("early-close", "awsh stream closed mid-frame")
	}
	if decoder.result && !decoder.closed {
		return protocolError("early-close", "awsh result stream closed before a valid closed result")
	}
	if !decoder.result && !decoder.shutdown {
		return protocolError("early-close", "awsh request stream closed before shutdown")
	}
	return nil
}

// BuildTerminalSubmission constructs the exact v1 submission capsule:
// bracketed-paste begin, one NUL-free UTF-8 conditional frame with two
// byte-identical source copies and identical redirections, the bracketed-
// paste terminator, and one LF. Both FIFO paths are empty for a PTY
// operation and both are present for split execution.
func BuildTerminalSubmission(source, histexpand string, status int, stdoutFIFO, stderrFIFO string) ([]byte, error) {
	if err := validateText("source", source, MaxOperationSourceBytes); err != nil {
		return nil, err
	}
	if strings.Contains(source, BracketedPasteEnd) {
		return nil, protocolError("source-invalid", "source contains the reserved bracketed-paste terminator")
	}
	if histexpand != "on" && histexpand != "off" {
		return nil, protocolError("adapter-framing", "histexpand must be on or off")
	}
	if err := validateStatus(status); err != nil {
		return nil, protocolError("adapter-framing", "status must be 0 through 255")
	}
	redirections := ""
	if (stdoutFIFO == "") != (stderrFIFO == "") {
		return nil, protocolError("adapter-framing", "split execution requires both FIFO paths")
	}
	if stdoutFIFO != "" {
		if err := firstError(validateFIFOPath("stdout_fifo", stdoutFIFO), validateFIFOPath("stderr_fifo", stderrFIFO)); err != nil {
			return nil, protocolError("adapter-framing", "FIFO paths must be bounded absolute private paths")
		}
		redirections = " >" + stdoutFIFO + " 2>" + stderrFIFO
	}
	branch := "    { " + source + "\n    }" + redirections + "\n"
	frame := "if " + InputStateFunctionName + " " + histexpand + " " + strconv.Itoa(status) + "; then\n" +
		branch +
		"else\n" +
		branch +
		"fi"
	submission := BracketedPasteBegin + frame + BracketedPasteEnd + "\n"
	if len(submission)-2*len(source) > MaxCapsuleOverheadBytes {
		return nil, protocolError("adapter-framing", "generated capsule bytes exceed their bound")
	}
	if len(submission) > MaxTerminalSubmissionBytes {
		return nil, protocolError("adapter-framing", "terminal submission exceeds its bound")
	}
	return []byte(submission), nil
}

// ValidateTerminalSubmission checks the paste framing, bounds, and byte
// safety of one complete terminal submission. Envoy writes these bytes; it
// does not reparse the authored source inside them.
func ValidateTerminalSubmission(submission []byte) error {
	if len(submission) > MaxTerminalSubmissionBytes {
		return protocolError("adapter-framing", "terminal submission exceeds its bound")
	}
	if !utf8.Valid(submission) || bytes.IndexByte(submission, 0) >= 0 {
		return protocolError("adapter-framing", "terminal submission must be NUL-free UTF-8")
	}
	if !bytes.HasPrefix(submission, []byte(BracketedPasteBegin)) {
		return protocolError("adapter-framing", "terminal submission must begin with bracketed-paste begin")
	}
	if !bytes.HasSuffix(submission, []byte(BracketedPasteEnd+"\n")) {
		return protocolError("adapter-framing", "terminal submission must end with the paste terminator and one LF")
	}
	interior := submission[len(BracketedPasteBegin) : len(submission)-len(BracketedPasteEnd)-1]
	if bytes.Contains(interior, []byte(BracketedPasteEnd)) {
		return protocolError("adapter-framing", "the paste terminator may appear only as framing")
	}
	if !bytes.HasPrefix(interior, []byte("if "+InputStateFunctionName+" ")) || !bytes.HasSuffix(interior, []byte("\nfi")) {
		return protocolError("adapter-framing", "terminal submission must carry the generated conditional frame")
	}
	return nil
}
