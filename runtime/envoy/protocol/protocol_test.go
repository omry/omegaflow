package protocol

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func fixturePath(name string) string {
	return filepath.Join("..", "..", "..", "tests", "fixtures", "envoy-protocol-v1", name)
}

func testExecutionPolicy() ExecutionPolicy {
	return ExecutionPolicy{
		ExecutionShape: ExecutionSplit,
		Timing:         TimingPresentation,
		Publication:    PublicationReal,
		Observation:    ObservationShared,
	}
}

func TestTelemetryGoldenFramesRoundTripExactly(t *testing.T) {
	for _, fixture := range []struct {
		name   string
		decode func([]byte) (any, error)
		encode func(any) ([]byte, error)
	}{
		{"controller.jsonl", DecodeController, EncodeController},
		{"envoy.jsonl", DecodeEnvoy, EncodeEnvoy},
	} {
		data, err := os.ReadFile(fixturePath(fixture.name))
		if err != nil {
			t.Fatal(err)
		}
		for _, line := range bytes.SplitAfter(data, []byte{'\n'}) {
			if len(line) == 0 {
				continue
			}
			message, err := fixture.decode(line)
			if err != nil {
				t.Fatalf("%s: %v", fixture.name, err)
			}
			actual, err := fixture.encode(message)
			if err != nil {
				t.Fatalf("%s: %v", fixture.name, err)
			}
			if !bytes.Equal(actual, line) {
				t.Fatalf("%s mismatch\nwant %s\n got %s", fixture.name, line, actual)
			}
		}
	}
}

func TestAwshGoldenFramesRoundTripExactly(t *testing.T) {
	data, err := os.ReadFile(fixturePath("awsh-frames.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []struct {
		Name     string `json:"name"`
		FrameHex string `json:"frame_hex"`
	}
	if err := json.Unmarshal(data, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, fixture := range fixtures {
		frame, err := hex.DecodeString(fixture.FrameHex)
		if err != nil {
			t.Fatal(err)
		}
		var message any
		var actual []byte
		if strings.HasPrefix(fixture.Name, "request_") {
			message, err = DecodeAwshRequest(frame)
			if err == nil {
				actual, err = EncodeAwshRequest(message)
			}
		} else {
			message, err = DecodeAwshResult(frame)
			if err == nil {
				actual, err = EncodeAwshResult(message)
			}
		}
		if err != nil {
			t.Fatalf("%s: %v", fixture.Name, err)
		}
		if !bytes.Equal(actual, frame) {
			t.Fatalf("%s mismatch", fixture.Name)
		}
	}
}

func TestAwshAcceptsByteFragmentation(t *testing.T) {
	data, err := os.ReadFile(fixturePath("awsh-frames.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []struct {
		Name     string `json:"name"`
		FrameHex string `json:"frame_hex"`
	}
	if err := json.Unmarshal(data, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, fixture := range fixtures {
		frame, err := hex.DecodeString(fixture.FrameHex)
		if err != nil {
			t.Fatal(err)
		}
		var decoder *AwshStreamDecoder
		if strings.HasPrefix(fixture.Name, "request_") {
			decoder = NewAwshRequestStreamDecoder()
		} else {
			decoder = NewAwshResultStreamDecoder()
		}
		var messages []any
		for _, value := range frame {
			observed, err := decoder.Feed([]byte{value})
			if err != nil {
				t.Fatalf("%s: %v", fixture.Name, err)
			}
			messages = append(messages, observed...)
		}
		if len(messages) != 1 {
			t.Fatalf("%s: want one message, got %d", fixture.Name, len(messages))
		}
	}
}

func TestAwshEOFRequiresShutdown(t *testing.T) {
	request := NewAwshRequestStreamDecoder()
	execute, err := EncodeAwshRequest(AwshExecute{
		OperationID: "op", ExecutionShape: ExecutionSplit,
		Observation: ObservationShared, Source: "true",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := request.Feed(execute); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "early-close", request.Finish())

	request = NewAwshRequestStreamDecoder()
	shutdown, err := EncodeAwshRequest(AwshShutdown{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := request.Feed(shutdown); err != nil {
		t.Fatal(err)
	}
	if err := request.Finish(); err != nil {
		t.Fatal(err)
	}

	result := NewAwshResultStreamDecoder()
	ready, err := EncodeAwshResult(AwshReady{ShellPID: 42, CWD: "/work"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := result.Feed(ready); err != nil {
		t.Fatal(err)
	}
	requireCode(t, "early-close", result.Finish())
	if err := result.MarkShutdownRequested(); err != nil {
		t.Fatal(err)
	}
	if err := result.Finish(); err != nil {
		t.Fatal(err)
	}

	requireCode(t, "out-of-state", request.MarkShutdownRequested())
}

func TestTelemetryAcceptsByteFragmentation(t *testing.T) {
	data, err := os.ReadFile(fixturePath("controller.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	decoder := NewControllerStreamDecoder()
	var messages []any
	for _, value := range data {
		observed, err := decoder.Feed([]byte{value})
		if err != nil {
			t.Fatal(err)
		}
		messages = append(messages, observed...)
	}
	if err := decoder.Finish(); err != nil {
		t.Fatal(err)
	}
	if len(messages) != 7 {
		t.Fatalf("want 7 messages, got %d", len(messages))
	}

	partial := NewControllerStreamDecoder()
	if _, err := partial.Feed([]byte(`{"schema":"omegaflow`)); err != nil {
		t.Fatal(err)
	}
	if err := partial.Finish(); err == nil || err.(*Error).Code != "early-close" {
		t.Fatalf("expected early-close, got %v", err)
	}
}

func TestMalformedTelemetryFailsClosed(t *testing.T) {
	tests := []struct {
		name  string
		frame []byte
		code  string
	}{
		{"framing", []byte(`{}`), "invalid-framing"},
		{"crlf", []byte("{}\r\n"), "invalid-framing"},
		{"nul", []byte("{}\x00\n"), "invalid-framing"},
		{"utf8", []byte{0xff, '\n'}, "invalid-utf8"},
		{"json", []byte("{broken}\n"), "invalid-json"},
		{"non-object", []byte("[]\n"), "invalid-json"},
		{"duplicate", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"hello","seq":1,"seq":2,"session_id":"s"}` + "\n"), "duplicate-field"},
		{"unknown", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"hello","seq":1,"session_id":"s","extra":true}` + "\n"), "unknown-field"},
		{"missing", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"hello","seq":1}` + "\n"), "missing-field"},
		{"schema", []byte(`{"schema":"other","type":"hello","seq":1,"session_id":"s"}` + "\n"), "unsupported-schema"},
		{"type", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"other","seq":1}` + "\n"), "unsupported-message"},
		{"field-type", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"hello","seq":"one","session_id":"s"}` + "\n"), "invalid-field"},
		{"lone-high-surrogate", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"execute","seq":1,"operation_id":"op","source":"\ud800","execution_shape":"split","timing":"presentation","publication":"real","observation":"shared"}` + "\n"), "invalid-field"},
		{"lone-low-surrogate", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"execute","seq":1,"operation_id":"op","source":"\udc00","execution_shape":"split","timing":"presentation","publication":"real","observation":"shared"}` + "\n"), "invalid-field"},
		{"trailing", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"hello","seq":1,"session_id":"s"} {}` + "\n"), "invalid-json"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := DecodeController(test.frame)
			if err == nil {
				t.Fatal("expected error")
			}
			protocolErr, ok := err.(*Error)
			if !ok || protocolErr.Code != test.code {
				t.Fatalf("want %s, got %v", test.code, err)
			}
		})
	}
}

func TestTelemetryUnicodeEscapePairsRemainExact(t *testing.T) {
	for _, test := range []struct {
		name   string
		source string
		want   string
	}{
		{name: "surrogate-pair", source: `\ud83d\ude00`, want: "😀"},
		{name: "escaped-literal", source: `\\ud800`, want: `\ud800`},
	} {
		t.Run(test.name, func(t *testing.T) {
			frame := []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"execute","seq":1,"operation_id":"op","source":"` + test.source + `","execution_shape":"split","timing":"presentation","publication":"real","observation":"shared"}` + "\n")
			message, err := DecodeController(frame)
			if err != nil {
				t.Fatal(err)
			}
			if got := message.(Execute).Source; got != test.want {
				t.Fatalf("source = %q, want %q", got, test.want)
			}
		})
	}
}

func TestNullTelemetryFieldsFailClosed(t *testing.T) {
	tests := []struct {
		name   string
		frame  []byte
		decode func([]byte) (any, error)
	}{
		{"schema", []byte(`{"schema":null,"type":"hello","seq":1,"session_id":"s"}` + "\n"), DecodeController},
		{"type", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":null,"seq":1,"session_id":"s"}` + "\n"), DecodeController},
		{"controller-sequence", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"hello","seq":null,"session_id":"s"}` + "\n"), DecodeController},
		{"envoy-status", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"operation_completed","seq":1,"operation_id":"op","status":null,"cwd":"/work","output_start":0,"output_through":0}` + "\n"), DecodeEnvoy},
		{"envoy-output", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"closed","seq":1,"reason":"shutdown","output_through":null}` + "\n"), DecodeEnvoy},
		{"optional-operation", []byte(`{"schema":"omegaflow-envoy-telemetry-v1","type":"diagnostic","seq":1,"severity":"info","code":"notice","message":"message","operation_id":null}` + "\n"), DecodeEnvoy},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := test.decode(test.frame)
			requireCode(t, "invalid-field", err)
		})
	}
}

func TestMalformedAwshFailsClosed(t *testing.T) {
	tests := []struct {
		name  string
		frame []byte
		code  string
	}{
		{"schema", []byte("other\x00shutdown\x00"), "unsupported-schema"},
		{"type", []byte("awsh-v1\x00other\x00"), "unsupported-message"},
		{"arity", []byte("awsh-v1\x00shutdown\x00extra\x00"), "invalid-field-count"},
		{"utf8", []byte{'a', 'w', 's', 'h', '-', 'v', '1', 0, 'e', 'x', 'e', 'c', 'u', 't', 'e', 0, 0xff, 0, 'x', 0}, "invalid-utf8"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := DecodeAwshRequest(test.frame)
			requireCode(t, test.code, err)
		})
	}
}

func TestBoundsAndEarlyClose(t *testing.T) {
	_, err := EncodeController(Execute{Seq: 1, OperationID: "op", Source: strings.Repeat("x", MaxOperationSourceBytes+1), ExecutionPolicy: testExecutionPolicy()})
	if err == nil {
		t.Fatal("expected source bound error")
	}
	_, err = DecodeAwshRequest([]byte("awsh-v1\x00execute\x00op\x00partial"))
	if err == nil || err.(*Error).Code != "early-close" {
		t.Fatalf("expected early-close, got %v", err)
	}

	telemetry := NewControllerStreamDecoder()
	if _, err := telemetry.Feed(bytes.Repeat([]byte{'x'}, MaxTelemetryFrameBytes)); err == nil || err.(*Error).Code != "frame-too-large" {
		t.Fatalf("expected telemetry frame-too-large, got %v", err)
	}
	if len(telemetry.buffer) != 0 {
		t.Fatal("oversized telemetry input was buffered")
	}
	awsh := NewAwshRequestStreamDecoder()
	if _, err := awsh.Feed(bytes.Repeat([]byte{'x'}, MaxAwshFrameBytes)); err == nil || err.(*Error).Code != "frame-too-large" {
		t.Fatalf("expected awsh frame-too-large, got %v", err)
	}
	if len(awsh.buffer) != 0 {
		t.Fatal("oversized awsh input was buffered")
	}

	invalidUTF8 := string([]byte{0xff})
	if _, err := EncodeController(Execute{Seq: 1, OperationID: "op", Source: invalidUTF8, ExecutionPolicy: testExecutionPolicy()}); err == nil || err.(*Error).Code != "invalid-field" {
		t.Fatalf("expected invalid UTF-8 field error, got %v", err)
	}
}

func TestExecutionPolicyAndLogicalOutputValidation(t *testing.T) {
	valid := []ExecutionPolicy{
		{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationShared},
		{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationExclusive},
		{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReal, Observation: ObservationShared},
		{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationSuppress, Observation: ObservationExclusive},
		{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReplace, Observation: ObservationExclusive},
	}
	for _, policy := range valid {
		if _, err := EncodeController(Execute{Seq: 1, OperationID: "op", Source: "true", ExecutionPolicy: policy}); err != nil {
			t.Fatalf("valid policy %#v: %v", policy, err)
		}
	}

	invalid := []ExecutionPolicy{
		{ExecutionShape: ExecutionSplit, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationShared},
		{ExecutionShape: ExecutionPTY, Timing: TimingPresentation, Publication: PublicationReal, Observation: ObservationShared},
		{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationSuppress, Observation: ObservationExclusive},
		{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReplace, Observation: ObservationShared},
	}
	for _, policy := range invalid {
		if _, err := EncodeController(Execute{Seq: 1, OperationID: "op", Source: "true", ExecutionPolicy: policy}); err == nil {
			t.Fatalf("invalid policy accepted: %#v", policy)
		}
	}

	validOutput := OperationOutput{Seq: 1, OperationID: "op", Stream: "stdout", DataBase64: "AP8K"}
	if _, err := EncodeEnvoy(validOutput); err != nil {
		t.Fatal(err)
	}
	for _, output := range []OperationOutput{
		{Seq: 1, OperationID: "op", Stream: "combined", DataBase64: "YQ=="},
		{Seq: 1, OperationID: "op", Stream: "stdout", DataBase64: ""},
		{Seq: 1, OperationID: "op", Stream: "stdout", DataBase64: "not-base64"},
	} {
		if _, err := EncodeEnvoy(output); err == nil {
			t.Fatalf("invalid logical output accepted: %#v", output)
		}
	}
}
