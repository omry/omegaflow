package protocol

import (
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"strings"
	"unicode/utf8"
)

var (
	identifier = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	code       = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
	session    = regexp.MustCompile(`^[0-9a-f]{32}$`)
	digest     = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

func oneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

func bounded(value string, maximum int, absolute bool) bool {
	return len(value) > 0 && len(value) <= maximum && utf8.ValidString(value) && !strings.ContainsRune(value, 0) && (!absolute || strings.HasPrefix(value, "/"))
}

func validString(name, value string) bool {
	switch name {
	case "schema":
		return value == Schema
	case "type":
		return true // Direction and model identity are checked by the codec.
	case "session_id":
		return session.MatchString(value)
	case "operation_id", "gate_id", "inspection_id", "producer_id", "output_id":
		return identifier.MatchString(value)
	case "source":
		return bounded(value, MaxSourceBytes, false) && !strings.Contains(value, "__OMEGAFLOW_AWSH_")
	case "cwd", "resolved_path":
		return bounded(value, MaxPathBytes, true)
	case "path":
		return bounded(value, MaxPathBytes, false)
	case "reason":
		return bounded(value, MaxReasonBytes, false)
	case "message":
		return bounded(value, MaxMessageBytes, false)
	case "code":
		return code.MatchString(value)
	case "sha256":
		return digest.MatchString(value)
	case "execution_shape":
		return oneOf(value, "pty", "split")
	case "timing":
		return oneOf(value, "realtime", "presentation")
	case "publication":
		return oneOf(value, "real", "suppress", "replace")
	case "observation":
		return oneOf(value, "shared", "exclusive")
	case "stream":
		return oneOf(value, "pty", "stdout", "stderr")
	case "severity":
		return oneOf(value, "info", "warning", "error", "fatal")
	case "kind":
		return oneOf(value, "file_exists", "produces")
	case "path_kind":
		return oneOf(value, "file", "directory", "other")
	case "digest_algorithm":
		return value == "directory-v2"
	}
	return false
}

func validInteger(name string, n int64) bool {
	switch name {
	case "seq":
		return n >= 1
	case "envoy_pid", "shell_pid":
		return n >= 1 && n <= 1<<31-1
	case "columns", "rows":
		return n >= 1 && n <= 1000
	case "status":
		return n >= 0 && n <= 255
	default:
		return n >= 0 // int64 retains the exact declared upper bound.
	}
}

func validateFields(v reflect.Value) error {
	for i := 0; i < v.NumField(); i++ {
		field, value := v.Type().Field(i), v.Field(i)
		if field.Anonymous {
			if err := validateFields(value); err != nil {
				return err
			}
			continue
		}
		tag := strings.Split(field.Tag.Get("json"), ",")
		name := tag[0]
		if value.Kind() == reflect.Pointer {
			if value.IsNil() {
				continue
			}
			value = value.Elem()
		} else if len(tag) > 1 && tag[1] == "omitempty" && value.IsZero() {
			continue
		}
		valid := true
		switch value.Kind() {
		case reflect.String:
			valid = validString(name, value.String())
		case reflect.Int64:
			valid = validInteger(name, value.Int())
		case reflect.Bool:
			valid = value.Bool() // shell_ended is present only when true.
		case reflect.Slice:
			valid = !value.IsNil() && value.Len() <= MaxInspections
			seen := map[string]bool{}
			for j := 0; j < value.Len(); j++ {
				entry := value.Index(j)
				if err := validateFields(entry); err != nil {
					return err
				}
				id := entry.FieldByName("InspectionID").String()
				if seen[id] {
					return fmt.Errorf("duplicate inspection id")
				}
				seen[id] = true
			}
		}
		if !valid {
			return fmt.Errorf("invalid %s", name)
		}
	}
	return nil
}

func validate(m Message) error {
	v := reflect.ValueOf(m).Elem()
	if err := validateFields(v); err != nil {
		return err
	}
	start, end := v.FieldByName("OutputStart"), v.FieldByName("OutputThrough")
	if start.IsValid() && end.IsValid() && start.Int() > end.Int() {
		return fmt.Errorf("reversed output range")
	}
	switch value := m.(type) {
	case *Ready:
		if value.ElapsedUS != 0 || value.OutputThrough > 4096 {
			return fmt.Errorf("invalid readiness boundary")
		}
	case *Execute:
		if (value.Timing == "realtime" && (value.ExecutionShape != "pty" || value.Publication != "real")) || (value.Timing == "presentation" && (value.ExecutionShape != "split" || value.Observation != "exclusive")) || (value.Publication != "real" && value.Observation != "exclusive") || (len(value.Inspections) > 0 && value.Observation != "exclusive") {
			return fmt.Errorf("invalid execution policy")
		}
	case *OperationFailed:
		if !oneOf(value.Code, "inspection-resolution", "inspection-missing", "inspection-type", "inspection-limit", "inspection-unstable", "inspection-read", "input-barrier-timeout", "source-syntax", "source-policy", "cancel-timeout", "finalize-timeout", "shell-ended-unresolved") {
			return fmt.Errorf("unknown operation failure code")
		}
	}
	return nil
}

func (s *Inspection) UnmarshalJSON(data []byte) error {
	type plain Inspection
	var kind struct {
		Kind string `json:"kind"`
	}
	if err := json.Unmarshal(data, &kind); err != nil {
		return err
	}
	required := []string{"inspection_id", "kind", "path"}
	if kind.Kind == "produces" {
		required = append(required, "producer_id", "output_id")
	} else if kind.Kind != "file_exists" {
		return fmt.Errorf("unknown inspection kind")
	}
	if err := exactObject(data, (*plain)(s), required, nil); err != nil {
		return err
	}
	if kind.Kind == "produces" && (!identifier.MatchString(s.ProducerID) || !identifier.MatchString(s.OutputID)) {
		return fmt.Errorf("invalid produced identifiers")
	}
	return nil
}

func (s *InspectionResult) UnmarshalJSON(data []byte) error {
	type plain InspectionResult
	var kind struct {
		Kind     string `json:"kind"`
		PathKind string `json:"path_kind"`
	}
	if err := json.Unmarshal(data, &kind); err != nil {
		return err
	}
	required := []string{"inspection_id", "kind", "resolved_path", "path_kind"}
	if kind.Kind == "produces" {
		if kind.PathKind != "file" && kind.PathKind != "directory" {
			return fmt.Errorf("invalid produced path kind")
		}
		required = append(required, "producer_id", "output_id", "sha256")
		if kind.PathKind == "directory" {
			required = append(required, "digest_algorithm")
		}
	} else if kind.Kind != "file_exists" {
		return fmt.Errorf("unknown inspection result kind")
	}
	if err := exactObject(data, (*plain)(s), required, nil); err != nil {
		return err
	}
	if kind.Kind == "produces" && (!identifier.MatchString(s.ProducerID) || !identifier.MatchString(s.OutputID) || !digest.MatchString(s.SHA256) || (kind.PathKind == "directory" && s.DigestAlgorithm != "directory-v2")) {
		return fmt.Errorf("invalid produced digest evidence")
	}
	return nil
}
