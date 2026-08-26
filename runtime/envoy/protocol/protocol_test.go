package protocol

import (
	"strings"
	"testing"
)

func mustCode(t *testing.T, err error, code string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected %s, got success", code)
	}
	protocolErr, ok := err.(*Error)
	if !ok {
		t.Fatalf("expected protocol error %s, got %v", code, err)
	}
	if protocolErr.Code != code {
		t.Fatalf("expected %s, got %s: %s", code, protocolErr.Code, protocolErr.Message)
	}
}

func TestMalformedTelemetryFramesFailClosed(t *testing.T) {
	cases := []struct {
		name  string
		frame string
		code  string
	}{
		{"missing-lf", `{"schema":"omegaflow-envoy-telemetry-v1","type":"shutdown","seq":1,"reason":"end"}`, "invalid-framing"},
		{"crlf", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"end\"}\r\n", "invalid-framing"},
		{"embedded-lf", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\n\"type\":\"shutdown\",\"seq\":1,\"reason\":\"end\"}\n", "invalid-framing"},
		{"nul-byte", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"e\x00d\"}\n", "invalid-framing"},
		{"invalid-utf8", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"\xff\"}\n", "invalid-utf8"},
		{"invalid-json", "{\"schema\"\n", "invalid-json"},
		{"non-object", "[1,2]\n", "invalid-json"},
		{"duplicate-field", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"seq\":2,\"reason\":\"end\"}\n", "duplicate-field"},
		{"unknown-schema", "{\"schema\":\"other\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"end\"}\n", "unsupported-schema"},
		{"unknown-type", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"other\",\"seq\":1}\n", "unsupported-message"},
		{"missing-type", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"seq\":1}\n", "missing-field"},
		{"missing-field", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1}\n", "missing-field"},
		{"unknown-field", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"end\",\"extra\":1}\n", "unknown-field"},
		{"null-field", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":null}\n", "invalid-field"},
		{"non-finite", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1e999,\"reason\":\"end\"}\n", "invalid-field"},
		{"unpaired-surrogate", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"\\ud800\"}\n", "invalid-field"},
		{"seq-zero", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":0,\"reason\":\"end\"}\n", "invalid-field"},
		{"trailing-value", "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"shutdown\",\"seq\":1,\"reason\":\"end\"} 1\n", "invalid-json"},
	}
	for _, entry := range cases {
		_, err := DecodeController([]byte(entry.frame))
		if err == nil {
			t.Fatalf("%s: expected failure", entry.name)
		}
		mustCode(t, err, entry.code)
	}
}

func TestSessionIDValidation(t *testing.T) {
	valid := Hello{Seq: 1, SessionID: "9f3c7a1e5b2d48c6a0e4f18d73b9c25a"}
	if _, err := EncodeController(valid); err != nil {
		t.Fatalf("valid hello: %v", err)
	}
	for _, sessionID := range []string{"", "SHOUTING", "9f3c", strings.Repeat("a", 33), "9F3C7A1E5B2D48C6A0E4F18D73B9C25A"} {
		_, err := EncodeController(Hello{Seq: 1, SessionID: sessionID})
		mustCode(t, err, "invalid-field")
	}
}

func TestExecutePolicyRules(t *testing.T) {
	base := Execute{Seq: 1, OperationID: "op-1", Source: "true", Inspections: []InspectionSpec{}, InputThrough: 0}

	policy := func(shape ExecutionShape, timing PublicationTiming, publication PublicationMode, observation ObservationMode) Execute {
		value := base
		value.ExecutionPolicy = ExecutionPolicy{ExecutionShape: shape, Timing: timing, Publication: publication, Observation: observation}
		return value
	}
	if _, err := EncodeController(policy(ExecutionPTY, TimingRealtime, PublicationReal, ObservationShared)); err != nil {
		t.Fatalf("realtime pty real: %v", err)
	}
	// Realtime timing requires PTY execution and real publication.
	if _, err := EncodeController(policy(ExecutionSplit, TimingRealtime, PublicationReal, ObservationShared)); err == nil {
		t.Fatal("realtime split must fail")
	}
	if _, err := EncodeController(policy(ExecutionPTY, TimingRealtime, PublicationSuppress, ObservationExclusive)); err == nil {
		t.Fatal("realtime suppress must fail")
	}
	// Presentation timing requires split execution and exclusive observation.
	if _, err := EncodeController(policy(ExecutionPTY, TimingPresentation, PublicationReal, ObservationExclusive)); err == nil {
		t.Fatal("presentation pty must fail")
	}
	if _, err := EncodeController(policy(ExecutionSplit, TimingPresentation, PublicationReal, ObservationShared)); err == nil {
		t.Fatal("presentation shared must fail")
	}
	// Suppressed and replaced output require exclusive observation.
	if _, err := EncodeController(policy(ExecutionSplit, TimingPresentation, PublicationSuppress, ObservationExclusive)); err != nil {
		t.Fatalf("presentation suppress exclusive: %v", err)
	}

	// An operation with inspections requires exclusive observation.
	withInspection := policy(ExecutionPTY, TimingRealtime, PublicationReal, ObservationShared)
	withInspection.Inspections = []InspectionSpec{{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "out.txt"}}
	if _, err := EncodeController(withInspection); err == nil {
		t.Fatal("inspections with shared observation must fail")
	}

	// Source is 1 through 491,520 UTF-8 bytes.
	oversize := policy(ExecutionPTY, TimingRealtime, PublicationReal, ObservationShared)
	oversize.Source = strings.Repeat("a", MaxOperationSourceBytes+1)
	if _, err := EncodeController(oversize); err == nil {
		t.Fatal("oversize source must fail")
	}
	empty := policy(ExecutionPTY, TimingRealtime, PublicationReal, ObservationShared)
	empty.Source = ""
	if _, err := EncodeController(empty); err == nil {
		t.Fatal("empty source must fail")
	}
}

func TestOperationFailedClosedCodeSet(t *testing.T) {
	event := OperationFailed{Seq: 1, OperationID: "op-1", Code: "cancel-timeout", Message: "grace expired", CWD: "/w", OutputStart: 3, OutputThrough: 3, ShellEnded: boolPointer(true)}
	if _, err := EncodeEnvoy(event); err != nil {
		t.Fatalf("closed-set code: %v", err)
	}
	event.Code = "made-up-code"
	_, err := EncodeEnvoy(event)
	mustCode(t, err, "invalid-field")
}

func TestConditionalFieldRules(t *testing.T) {
	// shell_ended is present only as true.
	completed := OperationCompleted{Seq: 1, OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 0, InspectionResults: []InspectionResult{}, ShellEnded: boolPointer(false)}
	if _, err := EncodeEnvoy(completed); err == nil {
		t.Fatal("shell_ended false must fail")
	}
	frame := "{\"schema\":\"omegaflow-envoy-telemetry-v1\",\"type\":\"operation_failed\",\"seq\":1,\"operation_id\":\"op-1\",\"code\":\"cancel-timeout\",\"message\":\"m\",\"cwd\":\"/w\",\"output_start\":0,\"output_through\":0,\"shell_ended\":false}\n"
	if _, err := DecodeEnvoy([]byte(frame)); err == nil {
		t.Fatal("decoded shell_ended false must fail")
	}

	// A cancelled status is optional but bounded when present.
	cancelled := OperationCancelled{Seq: 1, OperationID: "op-1", CWD: "/w", Reason: "deadline", OutputStart: 0, OutputThrough: 0}
	if _, err := EncodeEnvoy(cancelled); err != nil {
		t.Fatalf("pre-start cancellation: %v", err)
	}
	cancelled.Status = intPointer(300)
	if _, err := EncodeEnvoy(cancelled); err == nil {
		t.Fatal("status 300 must fail")
	}

	// ready carries elapsed_us 0.
	ready := Ready{Seq: 1, EnvoyPID: 2, ShellPID: 3, CWD: "/w", Columns: 80, Rows: 24, ElapsedUS: 5}
	if _, err := EncodeEnvoy(ready); err == nil {
		t.Fatal("nonzero ready elapsed_us must fail")
	}

	// Output ranges never invert.
	inverted := OperationCompleted{Seq: 1, OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 9, OutputThrough: 3, InspectionResults: []InspectionResult{}}
	_, err := EncodeEnvoy(inverted)
	mustCode(t, err, "invalid-output-range")

	// Marks carry a closed stream set.
	mark := OutputMark{Seq: 1, Offset: 0, Stream: "socket", ElapsedUS: 1}
	if _, err := EncodeEnvoy(mark); err == nil {
		t.Fatal("unknown mark stream must fail")
	}
}

func TestStreamDecoderBounds(t *testing.T) {
	decoder := NewControllerStreamDecoder()
	oversized := make([]byte, MaxTelemetryFrameBytes)
	for index := range oversized {
		oversized[index] = 'a'
	}
	_, err := decoder.Feed(oversized)
	mustCode(t, err, "frame-too-large")

	// A stream that closes mid-frame is an early close, and telemetry EOF
	// between complete frames is still not session success.
	decoder = NewControllerStreamDecoder()
	if _, err := decoder.Feed([]byte(`{"schema":"omegaflow-envoy-tel`)); err != nil {
		t.Fatalf("partial feed: %v", err)
	}
	mustCode(t, decoder.Finish(), "early-close")
}

func TestNilSlicesAreRejectedBeforeEncoding(t *testing.T) {
	// A nil slice would marshal as JSON null, which every receiver
	// rejects; the encoder must fail closed instead of emitting it.
	execute := realtimeExecute("op-1", 0)
	execute.Seq = 1
	execute.Inspections = nil
	_, err := EncodeController(execute)
	mustCode(t, err, "invalid-field")

	completed := OperationCompleted{Seq: 1, OperationID: "op-1", Status: 0, CWD: "/w", OutputStart: 0, OutputThrough: 0}
	_, err = EncodeEnvoy(completed)
	mustCode(t, err, "invalid-field")

	finalized := OperationFinalized{Seq: 1, OperationID: "op-1", CWD: "/w", Reason: "end", OutputStart: 0, OutputThrough: 0}
	_, err = EncodeEnvoy(finalized)
	mustCode(t, err, "invalid-field")
}

func TestCanonicalEncodingKeepsShellBytesLiteral(t *testing.T) {
	execute := realtimeExecute("op-1", 0)
	execute.Seq = 1
	execute.Source = "grep -c '<ready>' log.txt 2>&1 && echo ok"
	frame, err := EncodeController(execute)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if !strings.Contains(string(frame), execute.Source) {
		t.Fatalf("canonical frames emit &, <, and > literally: %s", frame)
	}
	decoded, err := DecodeController(frame)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	reencoded, err := EncodeController(decoded)
	if err != nil || string(reencoded) != string(frame) {
		t.Fatalf("round trip changed canonical bytes: %v", err)
	}
}
