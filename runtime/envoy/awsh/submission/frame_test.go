package submission

import (
	"bufio"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func example(source string) protocol.HelperSourceReply {
	return protocol.HelperSourceReply{OperationID: "op", Status: 0, HistExpand: "off", EditingMode: "emacs", ExecutionShape: "pty", Source: source}
}

// The approved static oracle is independent of the production template.
func TestCanonicalFrozenFrames(t *testing.T) {
	f, err := os.Open("../../../../tests/fixtures/envoy-protocol-v1/traces.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	s := bufio.NewScanner(f)
	count := 0
	for s.Scan() {
		var tc struct {
			ID    string `json:"id"`
			Input struct {
				Mode, Source string
				Status       int64
			}
			Expected struct {
				Frame  string `json:"frame_hex"`
				Loader string `json:"loader_stdout_hex"`
			}
		}
		if err := json.Unmarshal(s.Bytes(), &tc); err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(tc.ID, "B1-C010-canonical-frame-") {
			continue
		}
		count++
		m := example(tc.Input.Source)
		m.Status = tc.Input.Status
		m.ExecutionShape = tc.Input.Mode
		if m.ExecutionShape == "split" {
			m.StdoutFIFO = "/run/omegaflow/session/split/op.stdout"
			m.StderrFIFO = "/run/omegaflow/session/split/op.stderr"
		}
		frame, err := Frame(m)
		if err != nil {
			t.Fatal(err)
		}
		want, err := hex.DecodeString(tc.Expected.Frame)
		if err != nil {
			t.Fatal(err)
		}
		if frame != string(want) {
			t.Fatalf("%s canonical bytes differ: %q vs %q", tc.ID, frame, want)
		}
		want, err = hex.DecodeString(tc.Expected.Loader)
		if err != nil {
			t.Fatal(err)
		}
		if frame+"x" != string(want) {
			t.Fatalf("%s marker bytes differ", tc.ID)
		}
	}
	if err := s.Err(); err != nil {
		t.Fatal(err)
	}
	if count != 4 {
		t.Fatalf("missing frozen frames: %d", count)
	}
}

func TestFrameBoundsAndScalars(t *testing.T) {
	for _, source := range []string{"#", "printf 'é\\n'\n", strings.Repeat("#", protocol.MaxSourceBytes)} {
		frame, err := Frame(example(source))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(frame, source+"\n\n__OMEGAFLOW_AWSH_RETURN") || !strings.HasSuffix(frame, "\n}") {
			t.Fatal("source boundary changed")
		}
	}
	for _, source := range []string{"", "\x00", string([]byte{0xff}), "__OMEGAFLOW_AWSH_x", strings.Repeat("x", protocol.MaxSourceBytes+1)} {
		if _, err := Frame(example(source)); err == nil {
			t.Fatalf("accepted invalid source length %d", len(source))
		}
	}
	for _, mutate := range []func(*protocol.HelperSourceReply){
		func(m *protocol.HelperSourceReply) { m.Status = -1 }, func(m *protocol.HelperSourceReply) { m.Status = 256 },
		func(m *protocol.HelperSourceReply) { m.HistExpand = "OFF" }, func(m *protocol.HelperSourceReply) { m.EditingMode = "none" },
		func(m *protocol.HelperSourceReply) { m.OperationID = "op'; echo injected" }, func(m *protocol.HelperSourceReply) { m.StdoutFIFO = "/tmp/out" },
		func(m *protocol.HelperSourceReply) {
			m.ExecutionShape = "split"
			m.StdoutFIFO = "/run/omegaflow/session/split/op.stderr"
			m.StderrFIFO = "/run/omegaflow/session/split/op.stdout"
		},
	} {
		m := example(":")
		mutate(&m)
		if _, err := Frame(m); err == nil {
			t.Fatal("accepted malformed frame input")
		}
	}
}
