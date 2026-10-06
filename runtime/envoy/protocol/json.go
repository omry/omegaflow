package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"strconv"
	"strings"
	"unicode/utf8"
)

// DecodeController decodes exactly one LF-terminated controller request.
// It checks scalar bounds but does not advance a session sequence or barrier.
func DecodeController(frame []byte) (Message, error) { return decode(frame, true) }

// DecodeEnvoy decodes exactly one LF-terminated Envoy event.
func DecodeEnvoy(frame []byte) (Message, error) { return decode(frame, false) }

// EncodeController validates the complete compact frame before returning it.
func EncodeController(m Message) ([]byte, error) { return encode(m, true) }

// EncodeEnvoy validates the complete compact frame before returning it.
func EncodeEnvoy(m Message) ([]byte, error) { return encode(m, false) }

func encode(m Message, controller bool) ([]byte, error) {
	switch m.(type) {
	case Hello, *Hello, Execute, *Execute, Continue, *Continue, Cancel, *Cancel, Finalize, *Finalize, Resize, *Resize, Shutdown, *Shutdown,
		Ready, *Ready, OperationStarted, *OperationStarted, OperationReady, *OperationReady, OperationContinued, *OperationContinued,
		OperationGateInterrupted, *OperationGateInterrupted, OutputMark, *OutputMark, OperationCompleted, *OperationCompleted,
		OperationCancelled, *OperationCancelled, OperationFinalized, *OperationFinalized, OperationFailed, *OperationFailed,
		ResizeApplied, *ResizeApplied, Diagnostic, *Diagnostic, Draining, *Draining, Closed, *Closed:
	default:
		return nil, fmt.Errorf("unsupported message model")
	}
	if m == nil || !validStrings(reflect.ValueOf(m)) {
		return nil, fmt.Errorf("invalid message strings")
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(m); err != nil {
		return nil, err
	}
	parsed, err := decode(buf.Bytes(), controller)
	if err != nil {
		return nil, err
	}
	if parsed.wireType() != m.wireType() {
		return nil, fmt.Errorf("message type does not match model")
	}
	return buf.Bytes(), nil
}

func validStrings(v reflect.Value) bool {
	switch v.Kind() {
	case reflect.Pointer:
		return v.IsNil() || validStrings(v.Elem())
	case reflect.String:
		return utf8.ValidString(v.String())
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			if !validStrings(v.Field(i)) {
				return false
			}
		}
	case reflect.Slice:
		for i := 0; i < v.Len(); i++ {
			if !validStrings(v.Index(i)) {
				return false
			}
		}
	}
	return true
}

func decode(frame []byte, controller bool) (Message, error) {
	if err := checkFrame(frame); err != nil {
		return nil, err
	}
	var h Header
	if err := json.Unmarshal(frame[:len(frame)-1], &h); err != nil {
		return nil, err
	}
	if h.Schema != Schema || h.Seq < 1 {
		return nil, fmt.Errorf("invalid schema or sequence")
	}
	var m Message
	if controller {
		switch h.Type {
		case "hello":
			m = &Hello{}
		case "execute":
			m = &Execute{}
		case "continue":
			m = &Continue{}
		case "cancel":
			m = &Cancel{}
		case "finalize":
			m = &Finalize{}
		case "resize":
			m = &Resize{}
		case "shutdown":
			m = &Shutdown{}
		}
	} else {
		switch h.Type {
		case "ready":
			m = &Ready{}
		case "operation_started":
			m = &OperationStarted{}
		case "operation_ready":
			m = &OperationReady{}
		case "operation_continued":
			m = &OperationContinued{}
		case "operation_gate_interrupted":
			m = &OperationGateInterrupted{}
		case "output_mark":
			m = &OutputMark{}
		case "operation_completed":
			m = &OperationCompleted{}
		case "operation_cancelled":
			m = &OperationCancelled{}
		case "operation_finalized":
			m = &OperationFinalized{}
		case "operation_failed":
			m = &OperationFailed{}
		case "resize_applied":
			m = &ResizeApplied{}
		case "diagnostic":
			m = &Diagnostic{}
		case "draining":
			m = &Draining{}
		case "closed":
			m = &Closed{}
		}
	}
	if m == nil {
		return nil, fmt.Errorf("unknown message type or wrong direction")
	}
	required, optional := modelFields(reflect.TypeOf(m).Elem())
	if err := exactObject(frame[:len(frame)-1], m, required, optional); err != nil {
		return nil, err
	}
	if err := validate(m); err != nil {
		return nil, err
	}
	return m, nil
}

func modelFields(t reflect.Type) (required, optional []string) {
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if f.Anonymous {
			r, o := modelFields(f.Type)
			required = append(required, r...)
			optional = append(optional, o...)
			continue
		}
		tag := strings.Split(f.Tag.Get("json"), ",")
		if len(tag) > 1 && tag[1] == "omitempty" {
			optional = append(optional, tag[0])
		} else {
			required = append(required, tag[0])
		}
	}
	return
}

func exactObject(data []byte, out any, required, optional []string) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	allowed := make(map[string]bool, len(required)+len(optional))
	for _, name := range required {
		if _, ok := fields[name]; !ok {
			return fmt.Errorf("missing %s", name)
		}
		allowed[name] = true
	}
	for _, name := range optional {
		allowed[name] = true
	}
	for name := range fields {
		if !allowed[name] {
			return fmt.Errorf("unknown %s", name)
		}
	}
	return json.Unmarshal(data, out)
}

// Go's JSON decoder replaces unpaired escaped surrogates. The wire contract
// requires valid Unicode, so reject them before decoding or measuring strings.
func checkEscapes(data []byte) error {
	for i := 0; i < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		i++
		if i >= len(data) {
			return fmt.Errorf("truncated escape")
		}
		if data[i] != 'u' {
			continue
		}
		if i+4 >= len(data) {
			return fmt.Errorf("truncated Unicode escape")
		}
		n, err := strconv.ParseUint(string(data[i+1:i+5]), 16, 16)
		if err != nil {
			return err
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return fmt.Errorf("unpaired low surrogate")
		}
		if n < 0xd800 || n > 0xdbff {
			continue
		}
		if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
			return fmt.Errorf("unpaired high surrogate")
		}
		low, err := strconv.ParseUint(string(data[i+3:i+7]), 16, 16)
		if err != nil || low < 0xdc00 || low > 0xdfff {
			return fmt.Errorf("invalid surrogate pair")
		}
		i += 6
	}
	return nil
}

func checkFrame(frame []byte) error {
	if len(frame) < 3 || len(frame) > MaxFrameBytes || frame[len(frame)-1] != '\n' || bytes.HasSuffix(frame, []byte("\r\n")) {
		return fmt.Errorf("invalid frame size or terminator")
	}
	data := frame[:len(frame)-1]
	if bytes.ContainsAny(data, "\n\x00") || !utf8.Valid(data) {
		return fmt.Errorf("invalid frame bytes")
	}
	if err := checkEscapes(data); err != nil {
		return err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	// A nonrecursive stack checks decoded duplicate keys at every object depth.
	type object struct {
		keys map[string]bool
		key  bool
	}
	var stack []object
	first := true
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			if first || len(stack) != 0 {
				return fmt.Errorf("incomplete JSON")
			}
			return nil
		}
		if err != nil {
			return err
		}
		if first {
			if tok != json.Delim('{') {
				return fmt.Errorf("frame must be an object")
			}
			first = false
		} else if len(stack) == 0 {
			return fmt.Errorf("trailing JSON")
		}
		if tok == nil {
			return fmt.Errorf("null is not a public field value")
		}
		if len(stack) > 0 && stack[len(stack)-1].keys != nil {
			top := &stack[len(stack)-1]
			if top.key && tok != json.Delim('}') {
				key, ok := tok.(string)
				if !ok || top.keys[key] {
					return fmt.Errorf("duplicate or invalid object key")
				}
				top.keys[key] = true
				top.key = false
				continue
			}
			top.key = true
		}
		if delim, ok := tok.(json.Delim); ok {
			switch delim {
			case '{':
				stack = append(stack, object{keys: map[string]bool{}, key: true})
			case '[':
				stack = append(stack, object{})
			case '}', ']':
				stack = stack[:len(stack)-1]
			}
		}
	}
}
