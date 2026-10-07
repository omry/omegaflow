package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const privatePrefix = "awsh-v1"
const helperPrefix = "awsh-helper-v1"

var envName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func privateModel(kind string, direction PrivateDirection) PrivateMessage {
	if direction == EnvoyToAwsh {
		switch kind {
		case "execute":
			return &PrivateExecute{}
		case "continue":
			return &PrivateContinue{}
		case "start_release":
			return &PrivateStartRelease{}
		case "started_ack":
			return &PrivateStartedAck{}
		case "input_closed":
			return &PrivateInputClosed{}
		case "shutdown":
			return &PrivateShutdown{}
		}
	} else if direction == AwshToEnvoy {
		switch kind {
		case "ready":
			return &PrivateReady{}
		case "submit":
			return &PrivateSubmit{}
		case "start_prepared":
			return &PrivateStartPrepared{}
		case "started":
			return &PrivateStarted{}
		case "start_released":
			return &PrivateStartReleased{}
		case "gate_ready":
			return &PrivateGateReady{}
		case "gate_continued":
			return &PrivateGateContinued{}
		case "gate_interrupted":
			return &PrivateGateInterrupted{}
		case "input_close":
			return &PrivateInputClose{}
		case "completed":
			return &PrivateCompleted{}
		case "rejected":
			return &PrivateRejected{}
		case "shell_exit":
			return &PrivateShellExit{}
		case "protocol_error":
			return &PrivateProtocolError{}
		case "closed":
			return &PrivateClosed{}
		}
	}
	return nil
}

func helperModel(kind string, direction HelperDirection, phase HelperPhase) HelperMessage {
	if phase < HelperStartup || phase > HelperGate {
		return nil
	}
	if direction == HelperToAwsh {
		switch kind {
		case "prompt_state":
			if phase == HelperStartup || phase == HelperCompletion {
				return &HelperPromptState{}
			}
		case "prompt_ready":
			if phase == HelperStartup {
				return &HelperStartupReady{}
			}
			if phase == HelperCompletion {
				return &HelperCompletionReady{}
			}
		case "source":
			if phase == HelperSource {
				return &HelperSourceRequest{}
			}
		case "start_prepared":
			if phase == HelperStartPrepared {
				return &HelperPreparedRequest{}
			}
		case "gate":
			if phase == HelperGate {
				return &HelperGateRequest{}
			}
		}
	} else if direction == AwshToHelper {
		if kind == "accepted" {
			if phase == HelperGate {
				return &HelperGateAccepted{}
			}
			if phase != HelperSource {
				return &HelperAccepted{}
			}
		} else if kind == "source" && phase == HelperSource {
			return &HelperSourceReply{}
		}
	}
	return nil
}

// DecodePrivate validates exactly one descriptor frame, including its final NUL.
func DecodePrivate(frame []byte, direction PrivateDirection) (PrivateMessage, error) {
	fields, err := splitFields(frame, privatePrefix)
	if err != nil {
		return nil, err
	}
	m := privateModel(fields[1], direction)
	if m == nil {
		return nil, fmt.Errorf("unknown private type or wrong direction")
	}
	if err := decodeFields(fields[2:], m); err != nil {
		return nil, err
	}
	return m, validateWireModel(m)
}

// EncodePrivate validates the model and direction before producing canonical bytes.
func EncodePrivate(m PrivateMessage, direction PrivateDirection) ([]byte, error) {
	switch m.(type) {
	case PrivateExecute, *PrivateExecute, PrivateContinue, *PrivateContinue, PrivateStartRelease, *PrivateStartRelease, PrivateStartedAck, *PrivateStartedAck, PrivateInputClosed, *PrivateInputClosed, PrivateShutdown, *PrivateShutdown,
		PrivateReady, *PrivateReady, PrivateSubmit, *PrivateSubmit, PrivateStartPrepared, *PrivateStartPrepared, PrivateStarted, *PrivateStarted, PrivateStartReleased, *PrivateStartReleased,
		PrivateGateReady, *PrivateGateReady, PrivateGateContinued, *PrivateGateContinued, PrivateGateInterrupted, *PrivateGateInterrupted, PrivateInputClose, *PrivateInputClose, PrivateCompleted, *PrivateCompleted,
		PrivateRejected, *PrivateRejected, PrivateShellExit, *PrivateShellExit, PrivateProtocolError, *PrivateProtocolError, PrivateClosed, *PrivateClosed:
	default:
		return nil, fmt.Errorf("unsupported private model")
	}
	if nilModel(m) {
		return nil, fmt.Errorf("nil private model")
	}
	frame, err := encodeFields(privatePrefix, m.privateType(), m)
	if err != nil {
		return nil, err
	}
	_, err = DecodePrivate(frame, direction)
	if err != nil {
		return nil, err
	}
	return frame, nil
}

func splitFields(frame []byte, prefix string) ([]string, error) {
	if len(frame) == 0 || len(frame) > MaxFrameBytes || frame[len(frame)-1] != 0 || !utf8.Valid(frame) {
		return nil, fmt.Errorf("invalid NUL frame")
	}
	fields := strings.Split(string(frame[:len(frame)-1]), "\x00")
	if len(fields) < 2 || fields[0] != prefix {
		return nil, fmt.Errorf("invalid wire prefix")
	}
	return fields, nil
}

func nilModel(m any) bool {
	return m == nil || (reflect.ValueOf(m).Kind() == reflect.Pointer && reflect.ValueOf(m).IsNil())
}

func modelValue(m any) reflect.Value {
	v := reflect.ValueOf(m)
	if v.Kind() == reflect.Pointer {
		v = v.Elem()
	}
	return v
}

// Embedded PromptState is flattened, preserving the declared field order.
func wireFields(v reflect.Value) []reflect.Value {
	var fields []reflect.Value
	for i := 0; i < v.NumField(); i++ {
		if v.Type().Field(i).Anonymous {
			fields = append(fields, wireFields(v.Field(i))...)
		} else {
			fields = append(fields, v.Field(i))
		}
	}
	return fields
}

func wireNames(t reflect.Type) []string {
	var names []string
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if f.Anonymous {
			names = append(names, wireNames(f.Type)...)
		} else {
			names = append(names, f.Tag.Get("wire"))
		}
	}
	return names
}

func canonicalJSON(value any) ([]byte, error) {
	var b bytes.Buffer
	e := json.NewEncoder(&b)
	e.SetEscapeHTML(false)
	if err := e.Encode(value); err != nil {
		return nil, err
	}
	data := literalLineSeparators(b.Bytes())
	return data[:len(data)-1], nil
}

func encodeFields(prefix, kind string, m any) ([]byte, error) {
	fields := []string{prefix, kind}
	for _, v := range wireFields(modelValue(m)) {
		var s string
		switch v.Kind() {
		case reflect.String:
			s = v.String()
		case reflect.Int64:
			s = strconv.FormatInt(v.Int(), 10)
		default:
			if err := validateNested(v); err != nil {
				return nil, err
			}
			data, err := canonicalJSON(v.Interface())
			if err != nil {
				return nil, err
			}
			s = string(data)
		}
		if !utf8.ValidString(s) || strings.ContainsRune(s, 0) {
			return nil, fmt.Errorf("unrepresentable wire field")
		}
		fields = append(fields, s)
	}
	frame := []byte(strings.Join(fields, "\x00") + "\x00")
	if len(frame) > MaxFrameBytes {
		return nil, fmt.Errorf("wire frame too large")
	}
	return frame, nil
}

func decodeFields(fields []string, m any) error {
	v := modelValue(m)
	values, names := wireFields(v), wireNames(v.Type())
	if len(fields) != len(values) {
		return fmt.Errorf("invalid wire arity")
	}
	for i, f := range values {
		s := fields[i]
		switch f.Kind() {
		case reflect.String:
			if !validWireString(names[i], s) {
				return fmt.Errorf("invalid %s", names[i])
			}
			f.SetString(s)
		case reflect.Int64:
			n, err := strconv.ParseInt(s, 10, 64)
			if err != nil || strconv.FormatInt(n, 10) != s || (names[i] == "pid" && (n < 1 || n > 1<<31-1)) || (names[i] == "status" && !validInteger("status", n)) {
				return fmt.Errorf("invalid decimal %s", names[i])
			}
			f.SetInt(n)
		default:
			// Reuse strict JSON validation inside an object envelope: inspection
			// fields are arrays, while the public frame validator requires an object.
			if err := checkFrame([]byte("{\"value\":" + s + "}\n")); err != nil {
				return err
			}
			if err := json.Unmarshal([]byte(s), f.Addr().Interface()); err != nil {
				return err
			}
			if err := validateNested(f); err != nil {
				return err
			}
			canonical, err := canonicalJSON(f.Interface())
			if err != nil || string(canonical) != s {
				return fmt.Errorf("noncanonical nested JSON")
			}
		}
	}
	return nil
}

func validWireString(name, value string) bool {
	switch name {
	case "active_operation_id":
		return value == "" || validString("operation_id", value)
	case "stdout_fifo", "stderr_fifo":
		return value == "" || bounded(value, MaxPathBytes, true)
	case "logical_cwd":
		return value == "" || bounded(value, MaxPathBytes, true)
	case "shutdown":
		return value == "shutdown"
	case "rejection_code":
		return oneOf(value, "source-syntax", "source-policy")
	case "histexpand":
		return oneOf(value, "on", "off")
	case "editing_mode":
		return oneOf(value, "emacs", "vi")
	default:
		return validString(name, value)
	}
}

func validateNested(v reflect.Value) error {
	if v.IsNil() {
		return fmt.Errorf("null nested value")
	}
	if v.Kind() == reflect.Map {
		for _, key := range v.MapKeys() {
			value := v.MapIndex(key).String()
			if !envName.MatchString(key.String()) || !utf8.ValidString(value) || strings.ContainsRune(value, 0) {
				return fmt.Errorf("invalid exported environment")
			}
		}
		return nil
	}
	if !validStrings(v) || v.Len() > MaxInspections {
		return fmt.Errorf("invalid inspections")
	}
	seen := map[string]bool{}
	for i := 0; i < v.Len(); i++ {
		entry := v.Index(i)
		if err := validateFields(entry); err != nil {
			return err
		}
		id := entry.FieldByName("InspectionID").String()
		if seen[id] {
			return fmt.Errorf("duplicate inspection id")
		}
		seen[id] = true
		kind, producer, output := entry.FieldByName("Kind").String(), entry.FieldByName("ProducerID").String(), entry.FieldByName("OutputID").String()
		if (kind == "file_exists" && (producer != "" || output != "")) || (kind == "produces" && (!identifier.MatchString(producer) || !identifier.MatchString(output))) {
			return fmt.Errorf("invalid inspection identifiers")
		}
	}
	return nil
}

func validateWireModel(m any) error {
	var shape, id, stdout, stderr string
	switch v := m.(type) {
	case *PrivateExecute:
		request := &Execute{Header: Header{Schema: Schema, Type: "execute", Seq: 1}, OperationID: v.OperationID, ExecutionShape: v.ExecutionShape, Timing: v.Timing, Publication: v.Publication, Observation: v.Observation, Source: v.Source, Inspections: v.Inspections}
		if err := validate(request); err != nil {
			return err
		}
		shape, id, stdout, stderr = v.ExecutionShape, v.OperationID, v.StdoutFIFO, v.StderrFIFO
	case *HelperSourceReply:
		shape, id, stdout, stderr = v.ExecutionShape, v.OperationID, v.StdoutFIFO, v.StderrFIFO
	default:
		return nil
	}
	if shape == "pty" && stdout == "" && stderr == "" {
		return nil
	}
	root := "/run/omegaflow/session/split/" + id
	if shape == "split" && stdout == root+".stdout" && stderr == root+".stderr" {
		return nil
	}
	return fmt.Errorf("invalid split FIFO paths")
}

func (s *ResolvedInspection) UnmarshalJSON(data []byte) error {
	type plain ResolvedInspection
	var kind struct {
		Kind string `json:"kind"`
	}
	if err := json.Unmarshal(data, &kind); err != nil {
		return err
	}
	required := []string{"inspection_id", "kind", "resolved_path"}
	if kind.Kind == "produces" {
		required = append(required, "producer_id", "output_id")
	} else if kind.Kind != "file_exists" {
		return fmt.Errorf("unknown inspection kind")
	}
	return exactObject(data, (*plain)(s), required, nil)
}
