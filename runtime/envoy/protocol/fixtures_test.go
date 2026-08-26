package protocol

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

const fixtureDir = "../../../tests/fixtures/envoy-protocol-v1"

func updateFixtures() bool { return os.Getenv("UPDATE_ENVOY_FIXTURES") == "1" }

func boolPointer(value bool) *bool { return &value }
func intPointer(value int) *int    { return &value }

type sessionStep struct {
	controller bool
	message    any
}

// fixtureSession is the one canonical conformance session frozen by
// controller.jsonl and envoy.jsonl. Interleaved in this order it is one
// valid transcript in the Envoy's acceptance order, covering a completed
// PTY operation, a pre-start cancellation, a gate interruption with a
// crossed discarded continue, planned finalization with typed inspection
// results, a pre-start source rejection, an applied resize, and a
// shell-ended completion followed by the Envoy-initiated drain.
func fixtureSession(t *testing.T) []sessionStep {
	t.Helper()
	inspections := []InspectionSpec{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "$HOME/notes.txt"},
		{InspectionID: "inspection-2", Kind: InspectionProduces, Path: "build/out", ProducerID: "build-step", OutputID: "bundle"},
	}
	directoryDigest, err := DirectoryDigestV2(fixtureTree())
	if err != nil {
		t.Fatalf("fixture tree digest: %v", err)
	}
	results := []InspectionResult{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/home/dev/notes.txt", PathKind: PathKindFile},
		{
			InspectionID:    "inspection-2",
			Kind:            InspectionProduces,
			ResolvedPath:    "/workspace/project/build/out",
			PathKind:        PathKindDirectory,
			ProducerID:      "build-step",
			OutputID:        "bundle",
			SHA256:          directoryDigest,
			DigestAlgorithm: DigestDirectoryV2,
		},
	}
	cwd := "/workspace/project"
	return []sessionStep{
		{true, Hello{Seq: 1, SessionID: "9f3c7a1e5b2d48c6a0e4f18d73b9c25a"}},
		{false, Ready{Seq: 1, EnvoyPID: 214, ShellPID: 233, CWD: cwd, Columns: 120, Rows: 40, ElapsedUS: 0}},

		{true, Execute{Seq: 2, OperationID: "op-1", Source: "printf 'hello world\\n' 2>&1", ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationShared}, Inspections: []InspectionSpec{}, InputThrough: 0}},
		{false, OutputMark{Seq: 2, Offset: 0, Stream: "pty", ElapsedUS: 12_000}},
		{false, OperationStarted{Seq: 3, OperationID: "op-1", OutputStart: 0}},
		{false, OutputMark{Seq: 4, Offset: 12, Stream: "pty", ElapsedUS: 88_000}},
		{false, OperationCompleted{Seq: 5, OperationID: "op-1", Status: 0, CWD: cwd, OutputStart: 0, OutputThrough: 12, InspectionResults: []InspectionResult{}}},

		{true, Execute{Seq: 3, OperationID: "op-2", Source: "make bundle", ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationReplace, Observation: ObservationExclusive}, Inspections: []InspectionSpec{}, InputThrough: 0}},
		{true, Cancel{Seq: 4, OperationID: "op-2", Reason: "deadline"}},
		{false, OutputMark{Seq: 6, Offset: 12, Stream: "pty", ElapsedUS: 90_000}},
		{false, OperationCancelled{Seq: 7, OperationID: "op-2", CWD: cwd, Reason: "deadline", OutputStart: 12, OutputThrough: 12}},

		{true, Execute{Seq: 5, OperationID: "op-3", Source: "./serve --port 8080", ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionSplit, Timing: TimingPresentation, Publication: PublicationSuppress, Observation: ObservationExclusive}, Inspections: inspections, InputThrough: 0}},
		{false, OutputMark{Seq: 8, Offset: 12, Stream: "pty", ElapsedUS: 95_000}},
		{false, OperationStarted{Seq: 9, OperationID: "op-3", OutputStart: 12}},
		{false, OutputMark{Seq: 10, Offset: 12, Stream: "stdout", ElapsedUS: 100_000}},
		{false, OutputMark{Seq: 11, Offset: 40, Stream: "stdout", ElapsedUS: 150_000}},
		{false, OperationReady{Seq: 12, OperationID: "op-3", GateID: "gate-1", OutputThrough: 40}},
		{false, OutputMark{Seq: 13, Offset: 40, Stream: "stdout", ElapsedUS: 200_000}},
		{false, OperationGateInterrupted{Seq: 14, OperationID: "op-3", GateID: "gate-1", OutputThrough: 40}},
		{true, Continue{Seq: 6, OperationID: "op-3", GateID: "gate-1", InputThrough: 7}},
		{false, OutputMark{Seq: 15, Offset: 55, Stream: "stderr", ElapsedUS: 260_000}},
		{false, OperationReady{Seq: 16, OperationID: "op-3", GateID: "gate-2", OutputThrough: 55}},
		{true, Continue{Seq: 7, OperationID: "op-3", GateID: "gate-2", InputThrough: 42}},
		{false, OutputMark{Seq: 17, Offset: 55, Stream: "stderr", ElapsedUS: 300_000}},
		{false, OperationContinued{Seq: 18, OperationID: "op-3", GateID: "gate-2", OutputThrough: 55}},
		{true, Finalize{Seq: 8, OperationID: "op-3", Reason: "recording-end"}},
		{false, OutputMark{Seq: 19, Offset: 90, Stream: "stdout", ElapsedUS: 380_000}},
		{false, OperationFinalized{Seq: 20, OperationID: "op-3", CWD: cwd, Reason: "recording-end", OutputStart: 12, OutputThrough: 90, InspectionResults: results}},

		{true, Execute{Seq: 9, OperationID: "op-4", Source: "((", ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationShared}, Inspections: []InspectionSpec{}, InputThrough: 42}},
		{false, OutputMark{Seq: 21, Offset: 90, Stream: "stdout", ElapsedUS: 400_000}},
		{false, OperationFailed{Seq: 22, OperationID: "op-4", Code: "source-unsupported", Message: "fresh-parser rejection: source cannot form both authored branches", CWD: cwd, OutputStart: 90, OutputThrough: 90}},
		{false, Diagnostic{Seq: 23, Severity: "warning", Code: "source-rejected", Message: "operation op-4 was rejected before start and persistent Bash remains available", OperationID: stringPointer("op-4")}},

		{true, Resize{Seq: 10, Columns: 100, Rows: 30}},
		{false, OutputMark{Seq: 24, Offset: 90, Stream: "stdout", ElapsedUS: 410_000}},
		{false, ResizeApplied{Seq: 25, Columns: 100, Rows: 30, ElapsedUS: 415_000, OutputThrough: 90}},

		{true, Execute{Seq: 11, OperationID: "op-5", Source: "exit 7", ExecutionPolicy: ExecutionPolicy{ExecutionShape: ExecutionPTY, Timing: TimingRealtime, Publication: PublicationReal, Observation: ObservationShared}, Inspections: []InspectionSpec{}, InputThrough: 42}},
		{false, OutputMark{Seq: 26, Offset: 90, Stream: "stdout", ElapsedUS: 420_000}},
		{false, OperationStarted{Seq: 27, OperationID: "op-5", OutputStart: 90}},
		{false, OutputMark{Seq: 28, Offset: 90, Stream: "stdout", ElapsedUS: 500_000}},
		{false, OperationCompleted{Seq: 29, OperationID: "op-5", Status: 7, CWD: cwd, OutputStart: 90, OutputThrough: 90, InspectionResults: []InspectionResult{}, ShellEnded: boolPointer(true)}},

		{true, Shutdown{Seq: 12, Reason: "recording-complete"}},
		{false, OutputMark{Seq: 30, Offset: 90, Stream: "stdout", ElapsedUS: 505_000}},
		{false, Draining{Seq: 31, Reason: "shell_ended", OutputThrough: 90}},
		{false, OutputMark{Seq: 32, Offset: 90, Stream: "stdout", ElapsedUS: 510_000}},
		{false, Closed{Seq: 33, Reason: "shell_ended", OutputThrough: 90}},
	}
}

func stringPointer(value string) *string { return &value }

// fixtureTree is the frozen tree whose digests inspection-cases.json and the
// finalized inspection result share. It includes a nested directory, a
// symlink recorded as a link, an empty directory, and a special entry that
// both directory digests omit.
func fixtureTree() []TreeEntry {
	return []TreeEntry{
		{Path: "report.txt", Kind: TreeEntryFile, Content: []byte("42 items\n")},
		{Path: "empty", Kind: TreeEntryDir},
		{Path: "media", Kind: TreeEntryDir},
		{Path: "media/logo.svg", Kind: TreeEntryFile, Content: []byte("<svg/>")},
		{Path: "media/latest", Kind: TreeEntryLink, Target: "logo.svg"},
		{Path: "media/pipe", Kind: TreeEntrySpecial},
	}
}

func TestCanonicalSessionFixtures(t *testing.T) {
	steps := fixtureSession(t)

	state, err := NewSessionStateForSession("9f3c7a1e5b2d48c6a0e4f18d73b9c25a")
	if err != nil {
		t.Fatalf("session state: %v", err)
	}
	var controllerLines, envoyLines []byte
	for index, step := range steps {
		if step.controller {
			if err := state.AcceptController(step.message); err != nil {
				t.Fatalf("step %d (%T): %v", index, step.message, err)
			}
			frame, err := EncodeController(step.message)
			if err != nil {
				t.Fatalf("step %d encode: %v", index, err)
			}
			controllerLines = append(controllerLines, frame...)
			continue
		}
		if err := state.AcceptEnvoy(step.message); err != nil {
			t.Fatalf("step %d (%T): %v", index, step.message, err)
		}
		frame, err := EncodeEnvoy(step.message)
		if err != nil {
			t.Fatalf("step %d encode: %v", index, err)
		}
		envoyLines = append(envoyLines, frame...)
	}
	if state.Phase() != PhaseClosed {
		t.Fatalf("fixture session must end closed, not %s", state.Phase())
	}

	compareFixture(t, "controller.jsonl", controllerLines)
	compareFixture(t, "envoy.jsonl", envoyLines)

	// Every fixture line must round-trip through its decoder to the exact
	// bytes, through the incremental decoder as well.
	roundTripJSONL(t, controllerLines, NewControllerStreamDecoder(), EncodeController)
	roundTripJSONL(t, envoyLines, NewEnvoyStreamDecoder(), EncodeEnvoy)
}

func roundTripJSONL(t *testing.T, lines []byte, decoder *StreamDecoder, encode func(any) ([]byte, error)) {
	t.Helper()
	var messages []any
	for _, chunk := range splitChunks(lines, 7) {
		decoded, err := decoder.Feed(chunk)
		if err != nil {
			t.Fatalf("fragmented decode: %v", err)
		}
		messages = append(messages, decoded...)
	}
	if err := decoder.Finish(); err != nil {
		t.Fatalf("finish: %v", err)
	}
	var reencoded []byte
	for _, message := range messages {
		frame, err := encode(message)
		if err != nil {
			t.Fatalf("re-encode: %v", err)
		}
		reencoded = append(reencoded, frame...)
	}
	if !bytes.Equal(reencoded, lines) {
		t.Fatalf("round trip does not reproduce the canonical bytes")
	}
}

func splitChunks(data []byte, size int) [][]byte {
	var chunks [][]byte
	for len(data) > 0 {
		if len(data) < size {
			size = len(data)
		}
		chunks = append(chunks, data[:size])
		data = data[size:]
	}
	return chunks
}

type awshFixture struct {
	Name      string `json:"name"`
	Direction string `json:"direction"`
	Hex       string `json:"hex"`
}

func awshFixtureFrames(t *testing.T) []awshFixture {
	t.Helper()
	inspectionsJSON, err := EncodeInspectionSpecs([]InspectionSpec{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "$HOME/notes.txt"},
		{InspectionID: "inspection-2", Kind: InspectionProduces, Path: "build/out", ProducerID: "build-step", OutputID: "bundle"},
	})
	if err != nil {
		t.Fatalf("inspections json: %v", err)
	}
	resolvedJSON, err := EncodeResolvedInspections([]ResolvedInspection{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/home/dev/notes.txt"},
		{InspectionID: "inspection-2", Kind: InspectionProduces, ResolvedPath: "/workspace/project/build/out", ProducerID: "build-step", OutputID: "bundle"},
	})
	if err != nil {
		t.Fatalf("resolved json: %v", err)
	}
	submission, err := BuildTerminalSubmission("printf 'hello world\\n' 2>&1", "on", 0, "", "")
	if err != nil {
		t.Fatalf("submission: %v", err)
	}
	splitSubmission, err := BuildTerminalSubmission("make bundle", "off", 1, "/run/omegaflow/s1/op-3/stdout", "/run/omegaflow/s1/op-3/stderr")
	if err != nil {
		t.Fatalf("split submission: %v", err)
	}

	type frameCase struct {
		name    string
		request bool
		message any
	}
	cases := []frameCase{
		{"execute-pty", true, AwshExecute{OperationID: "op-1", ExecutionShape: ExecutionPTY, Observation: ObservationShared, InspectionsJSON: "[]", Source: "printf 'hello world\\n' 2>&1"}},
		{"execute-split-inspections", true, AwshExecute{OperationID: "op-3", ExecutionShape: ExecutionSplit, Observation: ObservationExclusive, InspectionsJSON: string(inspectionsJSON), StdoutFIFO: "/run/omegaflow/s1/op-3/stdout", StderrFIFO: "/run/omegaflow/s1/op-3/stderr", Source: "./serve --port 8080"}},
		{"continue", true, AwshContinue{OperationID: "op-3", GateID: "gate-2"}},
		{"gate-interrupt-ack", true, AwshGateInterruptAck{OperationID: "op-3", GateID: "gate-1"}},
		{"cancel", true, AwshCancel{OperationID: "op-2", Reason: "deadline"}},
		{"finalize", true, AwshFinalize{OperationID: "op-3", Reason: "recording-end"}},
		{"started-ack", true, AwshStartedAck{OperationID: "op-1"}},
		{"resize-prepare", true, AwshResizePrepare{Columns: 100, Rows: 30}},
		{"resize-apply", true, AwshResizeApply{Columns: 100, Rows: 30}},
		{"shutdown", true, AwshShutdown{}},

		{"ready", false, AwshReady{AwshPID: 229, ShellPID: 233, CWD: "/workspace/project"}},
		{"submit-pty", false, AwshSubmit{OperationID: "op-1", TerminalSubmission: string(submission)}},
		{"submit-split", false, AwshSubmit{OperationID: "op-3", TerminalSubmission: string(splitSubmission)}},
		{"started", false, AwshStarted{OperationID: "op-1"}},
		{"gate-ready", false, AwshGateReady{OperationID: "op-3", GateID: "gate-1"}},
		{"gate-continued", false, AwshGateContinued{OperationID: "op-3", GateID: "gate-2"}},
		{"gate-interrupt", false, AwshGateInterrupt{OperationID: "op-3", GateID: "gate-1"}},
		{"completed", false, AwshCompleted{OperationID: "op-3", Status: 0, CWD: "/workspace/project", ResolvedInspectionsJSON: string(resolvedJSON)}},
		{"rejected-source-invalid", false, AwshRejected{OperationID: "op-6", Code: "source-invalid", Message: "source contains the reserved bracketed-paste terminator", CWD: "/workspace/project"}},
		{"rejected-source-unsupported", false, AwshRejected{OperationID: "op-4", Code: "source-unsupported", Message: "fresh-parser rejection", CWD: "/workspace/project"}},
		{"shell-exit-active", false, AwshShellExit{OperationID: "op-5", Status: 7, CWD: "/workspace/project"}},
		{"shell-exit-idle", false, AwshShellExit{Status: 137, CWD: "/workspace/project"}},
		{"resize-ready", false, AwshResizeReady{Columns: 100, Rows: 30}},
		{"resized", false, AwshResized{Columns: 100, Rows: 30}},
		{"protocol-error", false, AwshProtocolError{Code: "adapter-state", Message: "reserved trap state is invalid at the reached prompt boundary"}},
		{"closed-shutdown", false, AwshClosed{Reason: ClosedReasonShutdown, Status: 137, CWD: "/workspace/project"}},
		{"closed-shell-ended", false, AwshClosed{Reason: ClosedReasonShellEnded, Status: 7, CWD: "/workspace/project"}},
	}
	for _, kind := range []string{DispositionCancel, DispositionFinalize} {
		for _, phase := range []string{PhaseDisarmed, PhaseSignal, PhaseGateCancelled, PhaseSettled, PhaseAlreadyInterrupted} {
			cases = append(cases, frameCase{
				name:    "disposition-" + kind + "-" + phase,
				request: false,
				message: AwshDisposition{OperationID: "op-3", RequestKind: kind, Phase: phase},
			})
		}
	}

	fixtures := make([]awshFixture, 0, len(cases))
	for _, entry := range cases {
		var frame []byte
		var err error
		direction := "result"
		if entry.request {
			direction = "request"
			frame, err = EncodeAwshRequest(entry.message)
		} else {
			frame, err = EncodeAwshResult(entry.message)
		}
		if err != nil {
			t.Fatalf("%s: %v", entry.name, err)
		}
		fixtures = append(fixtures, awshFixture{Name: entry.name, Direction: direction, Hex: hex.EncodeToString(frame)})
	}
	return fixtures
}

func TestAwshFrameFixtures(t *testing.T) {
	fixtures := awshFixtureFrames(t)
	encoded, err := json.MarshalIndent(fixtures, "", " ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	encoded = append(encoded, '\n')
	compareFixture(t, "awsh-frames.json", encoded)

	for _, fixture := range fixtures {
		frame, err := hex.DecodeString(fixture.Hex)
		if err != nil {
			t.Fatalf("%s: %v", fixture.Name, err)
		}
		var message any
		if fixture.Direction == "request" {
			message, err = DecodeAwshRequest(frame)
		} else {
			message, err = DecodeAwshResult(frame)
		}
		if err != nil {
			t.Fatalf("%s decode: %v", fixture.Name, err)
		}
		var reencoded []byte
		if fixture.Direction == "request" {
			reencoded, err = EncodeAwshRequest(message)
		} else {
			reencoded, err = EncodeAwshResult(message)
		}
		if err != nil {
			t.Fatalf("%s re-encode: %v", fixture.Name, err)
		}
		if !bytes.Equal(reencoded, frame) {
			t.Fatalf("%s round trip does not reproduce the canonical bytes", fixture.Name)
		}
	}
}

type resolutionCase struct {
	Name       string            `json:"name"`
	Env        map[string]string `json:"env"`
	CWD        string            `json:"cwd"`
	Homes      map[string]string `json:"homes"`
	Configured string            `json:"configured"`
	Resolved   string            `json:"resolved"`
}

type digestTreeEntry struct {
	Path    string `json:"path"`
	Kind    string `json:"kind"`
	Content string `json:"content,omitempty"`
	Target  string `json:"target,omitempty"`
}

type inspectionCases struct {
	Resolution []resolutionCase `json:"resolution"`
	Digests    struct {
		Tree            []digestTreeEntry `json:"tree"`
		FileContent     string            `json:"file_content"`
		FileSHA256      string            `json:"file_sha256"`
		DirectoryV2     string            `json:"directory_v2"`
		DirectoryNative string            `json:"directory_native"`
		EmptyV2         string            `json:"empty_directory_v2"`
		EmptyNative     string            `json:"empty_directory_native"`
	} `json:"digests"`
}

func inspectionCaseFixture(t *testing.T) inspectionCases {
	t.Helper()
	env := map[string]string{"HOME": "/home/dev", "OUT": "build"}
	homes := map[string]string{"": "/home/fallback", "deploy": "/srv/deploy"}
	baseCases := []resolutionCase{
		{Name: "defined-variable", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "$HOME/notes.txt"},
		{Name: "braced-variable", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "${OUT}/out"},
		{Name: "undefined-variable-stays-literal", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "$UNDEFINED/data"},
		{Name: "malformed-brace-stays-literal", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "${UNTERMINATED/data"},
		{Name: "tilde-uses-home", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "~/media"},
		{Name: "tilde-without-home-uses-user-database", Env: map[string]string{}, CWD: "/workspace/project", Homes: homes, Configured: "~/media"},
		{Name: "tilde-known-user", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "~deploy/releases"},
		{Name: "tilde-unknown-user-stays-literal", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "~nobody/releases"},
		{Name: "relative-after-cd", Env: env, CWD: "/workspace/project/sub", Homes: homes, Configured: "logs/latest.txt"},
		{Name: "absolute-unchanged", Env: env, CWD: "/workspace/project", Homes: homes, Configured: "/var/log/app.log"},
	}
	for index := range baseCases {
		entry := &baseCases[index]
		resolver := PathResolver{
			Env: entry.Env,
			CWD: entry.CWD,
			LookupHome: func(user string) (string, bool) {
				home, found := entry.Homes[user]
				return home, found
			},
		}
		resolved, err := resolver.Resolve(entry.Configured)
		if err != nil {
			t.Fatalf("%s: %v", entry.Name, err)
		}
		entry.Resolved = resolved
	}

	var fixture inspectionCases
	fixture.Resolution = baseCases
	tree := fixtureTree()
	for _, entry := range tree {
		fixture.Digests.Tree = append(fixture.Digests.Tree, digestTreeEntry{Path: entry.Path, Kind: entry.Kind, Content: string(entry.Content), Target: entry.Target})
	}
	fixture.Digests.FileContent = "42 items\n"
	fixture.Digests.FileSHA256 = FileDigest([]byte(fixture.Digests.FileContent))
	v2, err := DirectoryDigestV2(tree)
	if err != nil {
		t.Fatalf("directory-v2: %v", err)
	}
	native, err := DirectoryDigestNative(tree)
	if err != nil {
		t.Fatalf("directory native: %v", err)
	}
	emptyV2, err := DirectoryDigestV2(nil)
	if err != nil {
		t.Fatalf("empty directory-v2: %v", err)
	}
	emptyNative, err := DirectoryDigestNative(nil)
	if err != nil {
		t.Fatalf("empty directory native: %v", err)
	}
	fixture.Digests.DirectoryV2 = v2
	fixture.Digests.DirectoryNative = native
	fixture.Digests.EmptyV2 = emptyV2
	fixture.Digests.EmptyNative = emptyNative
	if v2 == native || emptyV2 == emptyNative {
		t.Fatal("the directory-v2 and native directory encodings must disagree on every directory")
	}
	return fixture
}

func TestInspectionCaseFixtures(t *testing.T) {
	fixture := inspectionCaseFixture(t)
	encoded, err := json.MarshalIndent(fixture, "", " ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	encoded = append(encoded, '\n')
	compareFixture(t, "inspection-cases.json", encoded)
}

func compareFixture(t *testing.T, name string, content []byte) {
	t.Helper()
	path := filepath.Join(fixtureDir, name)
	if updateFixtures() {
		if err := os.WriteFile(path, content, 0o644); err != nil {
			t.Fatalf("update %s: %v", name, err)
		}
		return
	}
	recorded, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v (run with UPDATE_ENVOY_FIXTURES=1 to create)", name, err)
	}
	if !bytes.Equal(recorded, content) {
		t.Fatalf("%s does not match the canonical encoding; accepted fixtures are never silently rewritten", name)
	}
}
