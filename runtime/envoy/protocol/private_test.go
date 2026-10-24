package protocol

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
)

// These declarative fixtures prove wire bytes only, not live Bash or socket behavior.
type nulFixture struct {
	ID        string   `json:"id"`
	Direction string   `json:"direction"`
	Phase     string   `json:"phase"`
	Fields    []string `json:"fields"`
}

func nulFixtures(t testing.TB, name string) []nulFixture {
	t.Helper()
	data, err := os.ReadFile("../../../tests/fixtures/envoy-protocol-v1/" + name + ".jsonl")
	if err != nil {
		t.Fatal(err)
	}
	var out []nulFixture
	for _, row := range bytes.Split(bytes.TrimSuffix(data, []byte{'\n'}), []byte{'\n'}) {
		var f nulFixture
		if err := json.Unmarshal(row, &f); err != nil {
			t.Fatal(err)
		}
		out = append(out, f)
	}
	return out
}

func nulBytes(fields []string) []byte { return []byte(strings.Join(fields, "\x00") + "\x00") }
func helperBytes(fields []string) []byte {
	payload := nulBytes(fields)
	frame := make([]byte, 4, len(payload)+4)
	binary.BigEndian.PutUint32(frame, uint32(len(payload)))
	return append(frame, payload...)
}
func privateDirection(f nulFixture) PrivateDirection {
	if f.Direction == "envoy" {
		return EnvoyToAwsh
	}
	return AwshToEnvoy
}
func helperDirection(f nulFixture) HelperDirection {
	if f.Direction == "helper" {
		return HelperToAwsh
	}
	return AwshToHelper
}
func helperPhase(f nulFixture) HelperPhase {
	return map[string]HelperPhase{"startup": HelperStartup, "completion": HelperCompletion, "source": HelperSource, "prepared": HelperStartPrepared, "gate": HelperGate}[f.Phase]
}

func TestPrivateGoldenForms(t *testing.T) {
	seen := map[string]bool{}
	for _, f := range nulFixtures(t, "private") {
		t.Run(f.ID, func(t *testing.T) {
			data, d := nulBytes(f.Fields), privateDirection(f)
			m, err := DecodePrivate(data, d)
			if err != nil {
				t.Fatal(err)
			}
			seen[m.privateType()] = true
			encoded, err := EncodePrivate(m, d)
			if err != nil || !bytes.Equal(encoded, data) {
				t.Fatalf("round trip: %q %v", encoded, err)
			}
			if _, err := DecodePrivate(data, 3-d); err == nil {
				t.Fatal("wrong direction accepted")
			}
			if _, err := EncodePrivate(m, 3-d); err == nil {
				t.Fatal("wrong encode direction accepted")
			}
			for split := 0; split <= len(data); split++ {
				r := bufio.NewReader(ioFragments(data, split))
				parsed, err := ReadPrivate(r, d)
				if err != nil || !reflect.DeepEqual(parsed, m) {
					t.Fatalf("split %d: %v", split, err)
				}
			}
		})
	}
	if len(seen) != 20 {
		t.Fatalf("missing private forms: %v", seen)
	}
}

func TestHelperGoldenForms(t *testing.T) {
	seen := map[reflect.Type]bool{}
	for _, f := range nulFixtures(t, "helper") {
		t.Run(f.ID, func(t *testing.T) {
			data, d, p := helperBytes(f.Fields), helperDirection(f), helperPhase(f)
			m, err := DecodeHelper(data, d, p)
			if err != nil {
				t.Fatal(err)
			}
			seen[reflect.TypeOf(m)] = true
			encoded, err := EncodeHelper(m, d, p)
			if err != nil || !bytes.Equal(encoded, data) {
				t.Fatalf("round trip: %q %v", encoded, err)
			}
			if _, err := DecodeHelper(data, 3-d, p); err == nil {
				t.Fatal("wrong direction accepted")
			}
			if _, err := EncodeHelper(m, 3-d, p); err == nil {
				t.Fatal("wrong encode direction accepted")
			}
			for split := 0; split <= len(data); split++ {
				parsed, err := ReadHelper(ioFragments(data, split), d, p)
				if err != nil || !reflect.DeepEqual(parsed, m) {
					t.Fatalf("split %d: %v", split, err)
				}
			}
		})
	}
	if len(seen) != 9 {
		t.Fatalf("missing helper forms: %v", seen)
	}
}

func TestPrivateAndHelperMalformedFields(t *testing.T) {
	tests := []struct {
		name   string
		fields []string
		index  int
		bad    []string
	}{
		{"id", []string{privatePrefix, "continue", "op", "gate"}, 2, []string{"", "_id", "a b", strings.Repeat("a", 65)}},
		{"gate", []string{privatePrefix, "gate_ready", "op", "gate"}, 3, []string{"", "!", strings.Repeat("g", 65)}},
		{"pid", []string{privatePrefix, "ready", "1", "2", "/work"}, 2, []string{"0", "-1", "+1", "01", "1.0", "2147483648", "9223372036854775808", ""}},
		{"status", []string{privatePrefix, "shell_exit", "", "0", "/work"}, 3, []string{"-1", "+0", "00", "256", "0.0", "1e0", "", " 1"}},
		{"cwd", []string{privatePrefix, "shell_exit", "", "0", "/work"}, 4, []string{"", "relative", strings.Repeat("/", 4097)}},
		{"code", []string{privatePrefix, "protocol_error", "shell-launch", "failure"}, 2, []string{"", "Upper", "_bad", "a_b", strings.Repeat("a", 65)}},
		{"message", []string{privatePrefix, "protocol_error", "shell-launch", "failure"}, 3, []string{"", strings.Repeat("m", 4097)}},
		{"closed-reason", []string{privatePrefix, "closed", "shutdown", "0", "/work"}, 2, []string{"", "shell_ended", "reason"}},
		{"rejection-code", []string{privatePrefix, "rejected", "op", "source-policy", "failure"}, 3, []string{"", "fatal", "source-checker"}},
		{"histexpand", []string{helperPrefix, "prompt_state", "0", "off", "emacs", "/work", "", "{}"}, 3, []string{"", "ON", "true"}},
		{"editing-mode", []string{helperPrefix, "prompt_state", "0", "off", "emacs", "/work", "", "{}"}, 4, []string{"", "emacs-standard", "none"}},
		{"logical-cwd", []string{helperPrefix, "prompt_state", "0", "off", "emacs", "/work", "", "{}"}, 6, []string{"relative", strings.Repeat("/", 4097)}},
	}
	for _, test := range tests {
		for i, bad := range test.bad {
			t.Run(fmt.Sprintf("%s/%d", test.name, i), func(t *testing.T) {
				f := append([]string(nil), test.fields...)
				f[test.index] = bad
				var err error
				if f[0] == helperPrefix {
					_, err = DecodeHelper(helperBytes(f), HelperToAwsh, HelperStartup)
				} else {
					d := AwshToEnvoy
					if f[1] == "continue" {
						d = EnvoyToAwsh
					}
					_, err = DecodePrivate(nulBytes(f), d)
				}
				if err == nil {
					t.Fatalf("accepted malformed %s", test.name)
				}
			})
		}
	}
	// Uniform framing errors for every model, including no-payload forms.
	for _, name := range []string{"private", "helper"} {
		for _, f := range nulFixtures(t, name) {
			t.Run(name+"/"+f.ID, func(t *testing.T) {
				mutations := [][]string{append(append([]string(nil), f.Fields...), "extra"), append([]string(nil), f.Fields[:len(f.Fields)-1]...)}
				for _, index := range []int{0, 1} {
					c := append([]string(nil), f.Fields...)
					c[index] = "unknown"
					mutations = append(mutations, c)
				}
				for _, fields := range mutations {
					var err error
					if name == "private" {
						_, err = DecodePrivate(nulBytes(fields), privateDirection(f))
					} else {
						_, err = DecodeHelper(helperBytes(fields), helperDirection(f), helperPhase(f))
					}
					if err == nil {
						t.Fatal("accepted wrong arity/prefix/type")
					}
				}
			})
		}
	}
}

func privateOperation() *PrivateExecute {
	return &PrivateExecute{OperationID: "op", ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "shared", Inspections: []Inspection{}, Source: "true\n"}
}

func TestPrivateExecutionPolicyAndPaths(t *testing.T) {
	bad := []func(*PrivateExecute){
		func(m *PrivateExecute) { m.Source = "" }, func(m *PrivateExecute) { m.Source = "a\x00b" }, func(m *PrivateExecute) { m.Source = "__OMEGAFLOW_AWSH_BAD" },
		func(m *PrivateExecute) { m.Source = string([]byte{0xff}) }, func(m *PrivateExecute) { m.Source = strings.Repeat("s", MaxSourceBytes+1) },
		func(m *PrivateExecute) { m.Timing = "later" }, func(m *PrivateExecute) { m.ExecutionShape = "other" }, func(m *PrivateExecute) { m.Publication = "hidden" }, func(m *PrivateExecute) { m.Observation = "local" },
		func(m *PrivateExecute) { m.Timing = "presentation" }, func(m *PrivateExecute) { m.ExecutionShape = "split" }, func(m *PrivateExecute) { m.Publication = "replace" },
		func(m *PrivateExecute) { m.Inspections = nil }, func(m *PrivateExecute) {
			m.Inspections = []Inspection{{InspectionID: "i", Kind: "file_exists", Path: "x"}}
		},
		func(m *PrivateExecute) { m.StdoutFIFO = "/unexpected" }, func(m *PrivateExecute) { m.StderrFIFO = "/unexpected" },
	}
	for i, change := range bad {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			m := privateOperation()
			change(m)
			if _, err := EncodePrivate(m, EnvoyToAwsh); err == nil {
				t.Fatal("accepted invalid execute")
			}
		})
	}
	m := privateOperation()
	m.Source = strings.Repeat("s", MaxSourceBytes)
	if _, err := EncodePrivate(m, EnvoyToAwsh); err != nil {
		t.Fatal(err)
	}
	m.ExecutionShape = "split"
	m.Timing = "presentation"
	m.Publication = "suppress"
	m.Observation = "exclusive"
	m.StdoutFIFO = "/run/omegaflow/session/split/op.stdout"
	m.StderrFIFO = "/run/omegaflow/session/split/op.stderr"
	if _, err := EncodePrivate(m, EnvoyToAwsh); err != nil {
		t.Fatal(err)
	}
	for _, paths := range [][2]string{{m.StderrFIFO, m.StdoutFIFO}, {"", m.StderrFIFO}, {m.StdoutFIFO, ""}, {"/run/omegaflow/session/split/other.stdout", m.StderrFIFO}, {m.StdoutFIFO + "/../op.stdout", m.StderrFIFO}} {
		c := *m
		c.StdoutFIFO, c.StderrFIFO = paths[0], paths[1]
		if _, err := EncodePrivate(c, EnvoyToAwsh); err == nil {
			t.Fatal("accepted wrong FIFO derivation")
		}
		q := HelperSourceReply{OperationID: "op", Status: 0, HistExpand: "off", EditingMode: "emacs", ExecutionShape: "split", StdoutFIFO: paths[0], StderrFIFO: paths[1], Source: "true"}
		if _, err := EncodeHelper(q, AwshToHelper, HelperSource); err == nil {
			t.Fatal("accepted wrong source FIFO derivation")
		}
	}
}

func TestNestedCanonicalJSON(t *testing.T) {
	badEnv := []string{`null`, `[]`, `{"A":null}`, `{"A":1}`, `{"A":true}`, `{"A":"x","A":"y"}`, `{"a-b":"x"}`, `{"9A":"x"}`, `{"A":"\u0000"}`, `{"A":"\ud800"}`, `{"A":"\udc00"}`, `{"A":"\ud83d\ude00"}`, `{"A":"\u0061"}`, `{"A":"\/"}`, `{"A":"\u000A"}`, `{"A":"\u001B"}`, `{ "A":"x"}`, `{"Z":"z","A":"a"}`, `{"A":"x"} {}`, `{"A":"x"}\n`}
	for i, s := range badEnv {
		t.Run(fmt.Sprintf("env/%d", i), func(t *testing.T) {
			f := []string{helperPrefix, "prompt_state", "0", "off", "emacs", "/work", "", s}
			if _, err := DecodeHelper(helperBytes(f), HelperToAwsh, HelperStartup); err == nil {
				t.Fatal("accepted invalid/noncanonical environment")
			}
		})
	}
	badInspections := []string{`null`, `{}`, `[null]`, `[{"inspection_id":"i","kind":"file_exists","path":"x","extra":1}]`, `[{"inspection_id":"i","kind":"file_exists","path":"x","producer_id":"p"}]`, `[{"inspection_id":"i","kind":"produces","path":"x"}]`, `[{"inspection_id":"i","kind":"file_exists","path":""}]`, `[{"inspection_id":"i","kind":"file_exists","path":null}]`, `[{"inspection_id":"i","kind":"file_exists","path":"\u0000"}]`, `[{"kind":"file_exists","inspection_id":"i","path":"x"}]`, `[ {"inspection_id":"i","kind":"file_exists","path":"x"}]`, `[{"inspection_id":"i","inspection_id":"j","kind":"file_exists","path":"x"}]`}
	for i, s := range badInspections {
		t.Run(fmt.Sprintf("inspection/%d", i), func(t *testing.T) {
			f := []string{privatePrefix, "execute", "op", "pty", "realtime", "real", "exclusive", s, "", "", "true"}
			if _, err := DecodePrivate(nulBytes(f), EnvoyToAwsh); err == nil {
				t.Fatal("accepted invalid/noncanonical inspection")
			}
		})
	}
	badResolved := []string{`[{"inspection_id":"i","kind":"file_exists","resolved_path":"relative"}]`, `[{"inspection_id":"i","kind":"file_exists","resolved_path":"/x","path_kind":"file"}]`, `[{"inspection_id":"i","kind":"produces","resolved_path":"/x","producer_id":"p"}]`, `[{"inspection_id":"i","kind":"produces","resolved_path":"/x","producer_id":"","output_id":"o"}]`}
	for _, s := range badResolved {
		f := []string{privatePrefix, "completed", "op", "0", "/work", s}
		if _, err := DecodePrivate(nulBytes(f), AwshToEnvoy); err == nil {
			t.Fatal("accepted invalid resolved plan")
		}
	}
	// Freeze all control escapes, literal Unicode, slash and HTML characters.
	env := map[string]string{"Z": "tail", "A": "\b\t\n\f\r\x01\x1b\"\\/<>é😀\u2028\u2029\\u2028"}
	m := HelperPromptState{PromptState{Status: 0, HistExpand: "off", EditingMode: "emacs", PhysicalCWD: "/work", ExportedEnv: env}}
	data, err := EncodeHelper(m, HelperToAwsh, HelperStartup)
	if err != nil {
		t.Fatal(err)
	}
	expected := `{"A":"\b\t\n\f\r\u0001\u001b\"\\/<>é😀` + "\u2028\u2029" + `\\u2028","Z":"tail"}`
	fields, _ := splitFields(data[4:], helperPrefix)
	if fields[7] != expected {
		t.Fatalf("canonical JSON differs: %q", fields[7])
	}
	parsed, err := DecodeHelper(data, HelperToAwsh, HelperStartup)
	if err != nil || !reflect.DeepEqual(parsed.(*HelperPromptState).ExportedEnv, env) {
		t.Fatalf("values changed: %v", err)
	}
	for _, value := range []map[string]string{nil, {"bad-key": "x"}, {"A": "\x00"}, {"A": string([]byte{0xff})}} {
		m.ExportedEnv = value
		if _, err := EncodeHelper(m, HelperToAwsh, HelperStartup); err == nil {
			t.Fatal("encoder replaced/omitted invalid environment")
		}
	}
}

func TestHelperPhaseSelection(t *testing.T) {
	for _, f := range nulFixtures(t, "helper") {
		for p := HelperPhase(0); p <= HelperGate+1; p++ {
			allowed := p == helperPhase(f)
			if f.Fields[1] == "prompt_state" {
				allowed = p == HelperStartup || p == HelperCompletion
			}
			if f.Fields[1] == "accepted" && len(f.Fields) == 2 {
				allowed = p == HelperStartup || p == HelperCompletion || p == HelperStartPrepared
			}
			_, err := DecodeHelper(helperBytes(f.Fields), helperDirection(f), p)
			if (err == nil) != allowed {
				t.Fatalf("%s phase %d: %v", f.ID, p, err)
			}
		}
	}
	for _, m := range []HelperMessage{HelperStartupReady{}, HelperCompletionReady{PromptState{Status: 0, HistExpand: "off", EditingMode: "emacs", PhysicalCWD: "/work", ExportedEnv: map[string]string{}}}, HelperAccepted{}, HelperGateAccepted{GateID: "gate"}} {
		phase := HelperCompletion
		d := HelperToAwsh
		switch m.(type) {
		case HelperCompletionReady:
			phase = HelperStartup
		case HelperAccepted:
			phase = HelperGate
			d = AwshToHelper
		case HelperGateAccepted:
			d = AwshToHelper
		}
		if _, err := EncodeHelper(m, d, phase); err == nil {
			t.Fatal("encoder accepted model in wrong phase")
		}
	}
}

func TestPrivateModelEdges(t *testing.T) {
	// Embedding a sealed interface must not extend the accepted model set.
	if _, err := EncodePrivate(struct{ PrivateMessage }{}, EnvoyToAwsh); err == nil {
		t.Fatal("accepted foreign private model")
	}
	if _, err := EncodeHelper(struct{ HelperMessage }{}, HelperToAwsh, HelperStartup); err == nil {
		t.Fatal("accepted foreign helper model")
	}
	for _, raw := range []string{`{"kind":7}`, `{"kind":"unknown"}`} {
		fields := []string{privatePrefix, "completed", "op", "0", "/work", "[" + raw + "]"}
		if _, err := DecodePrivate(nulBytes(fields), AwshToEnvoy); err == nil {
			t.Fatal("accepted invalid resolved kind")
		}
	}
	for _, m := range []PrivateMessage{nil, (*PrivateReady)(nil)} {
		if _, err := EncodePrivate(m, AwshToEnvoy); err == nil {
			t.Fatal("nil model accepted")
		}
	}
	for _, m := range []HelperMessage{nil, (*HelperAccepted)(nil)} {
		if _, err := EncodeHelper(m, AwshToHelper, HelperStartup); err == nil {
			t.Fatal("nil helper accepted")
		}
	}
	if _, err := DecodePrivate(nulBytes([]string{privatePrefix, "shutdown"}), 0); err == nil {
		t.Fatal("invalid direction accepted")
	}
	if _, err := DecodeHelper(helperBytes([]string{helperPrefix, "accepted"}), 0, HelperStartup); err == nil {
		t.Fatal("invalid helper direction accepted")
	}
	for _, m := range []PrivateMessage{PrivateReady{AwshPID: 1, ShellPID: 2147483647, CWD: "/" + strings.Repeat("c", 4095)}, PrivateShellExit{Status: 255, CWD: "/"}, PrivateProtocolError{Code: "a" + strings.Repeat("b", 63), Message: strings.Repeat("m", 4096)}, PrivateRejected{OperationID: "a" + strings.Repeat("b", 63), Code: "source-policy", Message: "rejected"}, PrivateContinue{OperationID: "a.b-0_", GateID: "Z"}} {
		if _, err := EncodePrivate(m, AwshToEnvoy); err != nil {
			if _, ok := m.(PrivateContinue); !ok {
				t.Fatal(err)
			} else if _, err := EncodePrivate(m, EnvoyToAwsh); err != nil {
				t.Fatal(err)
			}
		}
	}
}
