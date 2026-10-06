package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"
	"testing"
)

// Declarative wire inputs: these do not qualify Bash or prove runtime behavior.
func readFixture(t *testing.T, name string, controller bool) [][]byte {
	t.Helper()
	data, err := os.ReadFile("../../../tests/fixtures/envoy-protocol-v1/" + name)
	if err != nil {
		t.Fatal(err)
	}
	if len(data) == 0 || data[len(data)-1] != '\n' {
		t.Fatalf("fixture %s is not LF terminated", name)
	}
	lines := bytes.Split(data, []byte{'\n'})
	lines = lines[:len(lines)-1]
	for i := range lines {
		lines[i] = append(append([]byte(nil), lines[i]...), '\n')
		var h Header
		if err := json.Unmarshal(bytes.TrimSuffix(lines[i], []byte{'\n'}), &h); err != nil {
			t.Fatalf("fixture %s line %d: %v", name, i+1, err)
		}
		if controller != controllerKind(h.Type) {
			t.Fatalf("fixture %s line %d has wrong wire direction for %q", name, i+1, h.Type)
		}
		if controller {
			if _, err := DecodeController(lines[i]); err != nil {
				t.Fatalf("fixture %s line %d is not a controller frame: %v", name, i+1, err)
			}
		} else if _, err := DecodeEnvoy(lines[i]); err != nil {
			t.Fatalf("fixture %s line %d is not an Envoy frame: %v", name, i+1, err)
		}
	}
	return lines
}

func corpus(t *testing.T) [][]byte {
	t.Helper()
	controller := readFixture(t, "controller.jsonl", true)
	envoy := readFixture(t, "envoy.jsonl", false)
	if len(controller) < 7 || len(envoy) < 14 {
		t.Fatalf("canonical fixture corpus is missing nominal frames: controller=%d envoy=%d", len(controller), len(envoy))
	}
	lines := make([][]byte, 0, len(controller)+len(envoy))
	lines = append(lines, controller[:7]...)
	lines = append(lines, envoy[:14]...)
	lines = append(lines, controller[7:]...)
	lines = append(lines, envoy[14:]...)
	return lines
}
func controllerKind(s string) bool {
	return oneOf(s, "hello", "execute", "continue", "cancel", "finalize", "resize", "shutdown")
}
func parse(data []byte) (Message, error) {
	var h Header
	_ = json.Unmarshal(bytes.TrimSuffix(data, []byte{'\n'}), &h)
	if controllerKind(h.Type) {
		return DecodeController(data)
	}
	return DecodeEnvoy(data)
}
func frame(t *testing.T, f map[string]any) []byte {
	t.Helper()
	b, e := json.Marshal(f)
	if e != nil {
		t.Fatal(e)
	}
	return append(b, '\n')
}
func object(t *testing.T, b []byte) map[string]any {
	t.Helper()
	var f map[string]any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if e := d.Decode(&f); e != nil {
		t.Fatal(e)
	}
	return f
}
func reject(t *testing.T, b []byte) {
	t.Helper()
	if _, e := parse(b); e == nil {
		t.Fatalf("accepted invalid frame %q", b[:min(len(b), 300)])
	}
}
func accept(t *testing.T, b []byte) Message {
	t.Helper()
	m, e := parse(b)
	if e != nil {
		t.Fatalf("rejected frame: %v", e)
	}
	return m
}

func TestGoldenPublicCorpus(t *testing.T) {
	seen := map[string]bool{}
	for i, b := range corpus(t) {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			m := accept(t, b)
			seen[m.wireType()] = true
			var out []byte
			var e error
			if controllerKind(m.wireType()) {
				out, e = EncodeController(m)
				if _, err := DecodeEnvoy(b); err == nil {
					t.Fatal("wrong direction accepted")
				}
			} else {
				out, e = EncodeEnvoy(m)
				if _, err := DecodeController(b); err == nil {
					t.Fatal("wrong direction accepted")
				}
			}
			if e != nil {
				t.Fatal(e)
			}
			if !bytes.Equal(out, b) {
				t.Fatalf("golden bytes or order changed:\n%s\n%s", out, b)
			}
		})
	}
	if len(seen) != 21 {
		t.Fatalf("covered %d types", len(seen))
	}
}
func TestExactPublicSchemas(t *testing.T) {
	optional := map[string][]string{"operation_completed": {"shell_ended"}, "operation_failed": {"shell_ended"}, "operation_cancelled": {"status"}, "diagnostic": {"operation_id"}}
	for _, b := range corpus(t) {
		original := object(t, b)
		kind := original["type"].(string)
		t.Run(kind, func(t *testing.T) {
			f := object(t, b)
			f["unknown"] = 1
			reject(t, frame(t, f))
			for name := range original {
				t.Run(name, func(t *testing.T) {
					f := object(t, b)
					delete(f, name)
					if oneOf(name, optional[kind]...) {
						accept(t, frame(t, f))
					} else {
						reject(t, frame(t, f))
					}
					for _, bad := range []any{nil, map[string]any{}, false} {
						f = object(t, b)
						f[name] = bad
						reject(t, frame(t, f))
					}
					f = object(t, b)
					f[strings.ToUpper(name)] = f[name]
					delete(f, name)
					reject(t, frame(t, f))
				})
			}
			for _, name := range optional[kind] {
				f := object(t, b)
				f[name] = nil
				reject(t, frame(t, f))
			}
		})
	}
}
func TestPublicScalarBounds(t *testing.T) {
	for _, b := range corpus(t) {
		original := object(t, b)
		for name, value := range original {
			var invalid, valid []any
			switch value.(type) {
			case json.Number:
				invalid = []any{-1, true, "1", json.Number("1.5"), json.Number("1e0"), json.Number("9223372036854775808")}
				valid = []any{int64(0), int64(math.MaxInt64)}
				switch name {
				case "seq":
					invalid = append(invalid, 0)
					valid = []any{int64(1), int64(math.MaxInt64)}
				case "envoy_pid", "shell_pid":
					invalid = append(invalid, 0, int64(1<<31))
					valid = []any{int64(1), int64(1<<31 - 1)}
				case "columns", "rows":
					invalid = append(invalid, 0, 1001)
					valid = []any{1, 1000}
				case "status":
					invalid = append(invalid, 256)
					valid = []any{0, 255}
				case "elapsed_us":
					if original["type"] == "ready" {
						invalid = append(invalid, 1)
						valid = []any{0}
					}
				case "output_through":
					if original["type"] == "ready" {
						invalid = append(invalid, 4097)
						valid = []any{0, 4096}
					} else if _, ok := original["output_start"]; ok {
						valid = nil
					}
				case "output_start":
					if _, ok := original["output_through"]; ok {
						valid = nil
					}
				}
			case string:
				invalid = []any{0, true, "", "a\x00b"}
				switch name {
				case "code":
					if original["type"] == "operation_failed" {
						invalid = append(invalid, "unknown")
					}
				case "schema", "type":
					invalid = append(invalid, "unsupported")
				case "session_id":
					invalid = append(invalid, strings.Repeat("a", 31), strings.Repeat("a", 33), strings.Repeat("A", 32), strings.Repeat("g", 32))
					valid = []any{strings.Repeat("0", 32), strings.Repeat("f", 32)}
				case "operation_id", "gate_id":
					invalid = append(invalid, "-a", "with space", strings.Repeat("a", 65), "é")
					valid = []any{"0", strings.Repeat("A", 64), "a._-9"}
				case "reason", "message", "cwd", "source":
					limit := map[string]int{"reason": MaxReasonBytes, "message": MaxMessageBytes, "cwd": MaxPathBytes, "source": MaxSourceBytes}[name]
					invalid = append(invalid, strings.Repeat("x", limit+1), strings.Repeat("é", limit/2)+"x")
					prefix := ""
					if name == "cwd" {
						prefix = "/"
						invalid = append(invalid, "relative")
					}
					valid = []any{prefix + strings.Repeat("x", limit-len(prefix)), prefix + strings.Repeat("é", (limit-len(prefix))/2)}
					if name == "source" {
						invalid = append(invalid, "echo __OMEGAFLOW_AWSH_RESERVED")
						valid = append(valid, "__OMEGAFLOW_AWS")
					}
				default:
					invalid = append(invalid, "unknown")
				}
			case bool:
				invalid = []any{false, 1, "true"}
				valid = []any{true}
			default:
				continue
			}
			t.Run(fmt.Sprint(original["type"], "/", name), func(t *testing.T) {
				for _, bad := range invalid {
					f := object(t, b)
					f[name] = bad
					reject(t, frame(t, f))
				}
				for _, good := range valid {
					f := object(t, b)
					f[name] = good
					accept(t, frame(t, f))
				}
			})
		}
	}
}
func TestPublicPoliciesAndEnums(t *testing.T) {
	execute := corpus(t)[1]
	for _, shape := range []string{"pty", "split"} {
		for _, timing := range []string{"realtime", "presentation"} {
			for _, publication := range []string{"real", "suppress", "replace"} {
				for _, observation := range []string{"shared", "exclusive"} {
					f := object(t, execute)
					f["execution_shape"] = shape
					f["timing"] = timing
					f["publication"] = publication
					f["observation"] = observation
					valid := (timing != "realtime" || (shape == "pty" && publication == "real")) && (timing != "presentation" || (shape == "split" && observation == "exclusive")) && (publication == "real" || observation == "exclusive")
					if valid {
						accept(t, frame(t, f))
					} else {
						reject(t, frame(t, f))
					}
				}
			}
		}
	}
	for _, stream := range []string{"pty", "stdout", "stderr"} {
		f := object(t, corpus(t)[12])
		f["stream"] = stream
		accept(t, frame(t, f))
	}
	for _, severity := range []string{"info", "warning", "error", "fatal"} {
		f := object(t, corpus(t)[18])
		f["severity"] = severity
		accept(t, frame(t, f))
	}
	for _, code := range []string{"inspection-resolution", "inspection-missing", "inspection-type", "inspection-limit", "inspection-unstable", "inspection-read", "input-barrier-timeout", "source-syntax", "source-policy", "cancel-timeout", "finalize-timeout", "shell-ended-unresolved"} {
		f := object(t, corpus(t)[16])
		f["code"] = code
		accept(t, frame(t, f))
	}
	for _, code := range []string{"future-code", "operation-cleanup", "shell-launch-output", "resize-failed", "inspection-cancel-timeout"} {
		f := object(t, corpus(t)[16])
		f["code"] = code
		reject(t, frame(t, f))
		f = object(t, corpus(t)[18])
		f["code"] = code
		accept(t, frame(t, f))
	}
	for _, code := range []string{"A", "-a", "a_b", strings.Repeat("a", 65)} {
		f := object(t, corpus(t)[18])
		f["code"] = code
		reject(t, frame(t, f))
	}
	f := object(t, corpus(t)[18])
	f["code"] = strings.Repeat("a", 64)
	accept(t, frame(t, f))
	for _, i := range []int{13, 14, 15, 16} {
		f := object(t, corpus(t)[i])
		if i == 14 {
			f["status"] = 0 // A non-empty cancellation range requires a started operation.
		} else if i == 16 {
			f["code"] = "cancel-timeout" // Use a failure that may follow operation_started.
		}
		f["output_start"] = 1
		f["output_through"] = 0
		reject(t, frame(t, f))
		f["output_through"] = int64(math.MaxInt64)
		accept(t, frame(t, f))
	}
	for _, i := range []int{13, 15} {
		f := object(t, corpus(t)[i])
		f["inspection_results"] = nil
		reject(t, frame(t, f))
	}
	for _, i := range []int{14, 16} {
		f := object(t, corpus(t)[i])
		f["inspection_results"] = []any{}
		reject(t, frame(t, f))
	}
	f = object(t, corpus(t)[15])
	f["status"] = 0
	reject(t, frame(t, f))
}
