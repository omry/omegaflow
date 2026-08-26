package protocol

import (
	"bytes"
	"strings"
	"testing"
)

func TestAwshFrameArityAndBounds(t *testing.T) {
	// A frame must end with NUL and carry the exact arity for its type.
	_, err := DecodeAwshRequest([]byte("awsh-v1\x00shutdown"))
	mustCode(t, err, "early-close")
	_, err = DecodeAwshRequest([]byte("awsh-v1\x00shutdown\x00extra\x00"))
	mustCode(t, err, "invalid-field-count")
	_, err = DecodeAwshRequest([]byte("other-v1\x00shutdown\x00"))
	mustCode(t, err, "unsupported-schema")
	_, err = DecodeAwshRequest([]byte("awsh-v1\x00unknown\x00"))
	mustCode(t, err, "unsupported-message")
	_, err = DecodeAwshResult([]byte("awsh-v1\x00ready\x00229\x00233\x00relative/cwd\x00"))
	mustCode(t, err, "invalid-field")
	// Integers must be canonical decimals.
	_, err = DecodeAwshResult([]byte("awsh-v1\x00ready\x00+229\x00233\x00/w\x00"))
	mustCode(t, err, "invalid-field")
	_, err = DecodeAwshResult([]byte("awsh-v1\x00shell_exit\x00op-5\x0007\x00/w\x00"))
	mustCode(t, err, "invalid-field")
}

func TestAwshClosedSets(t *testing.T) {
	_, err := EncodeAwshResult(AwshDisposition{OperationID: "op-1", RequestKind: "restart", Phase: PhaseSignal})
	mustCode(t, err, "invalid-field")
	_, err = EncodeAwshResult(AwshDisposition{OperationID: "op-1", RequestKind: DispositionCancel, Phase: "later"})
	mustCode(t, err, "invalid-field")
	_, err = EncodeAwshResult(AwshRejected{OperationID: "op-1", Code: "inspection-read", Message: "m", CWD: "/w"})
	mustCode(t, err, "invalid-field")
	_, err = EncodeAwshResult(AwshClosed{Reason: "crashed", Status: 1, CWD: "/w"})
	mustCode(t, err, "invalid-field")
	// The idle shell exit carries an empty operation ID; an active one
	// carries the exact active operation.
	if _, err := EncodeAwshResult(AwshShellExit{Status: 137, CWD: "/w"}); err != nil {
		t.Fatalf("idle shell exit: %v", err)
	}
}

func TestAwshExecuteFIFORules(t *testing.T) {
	base := AwshExecute{OperationID: "op-1", ExecutionShape: ExecutionPTY, Observation: ObservationShared, InspectionsJSON: "[]", Source: "true"}
	if _, err := EncodeAwshRequest(base); err != nil {
		t.Fatalf("pty execute: %v", err)
	}
	withFIFO := base
	withFIFO.StdoutFIFO = "/run/omegaflow/s1/op-1/stdout"
	if _, err := EncodeAwshRequest(withFIFO); err == nil {
		t.Fatal("pty execution carries empty FIFO fields")
	}
	split := base
	split.ExecutionShape = ExecutionSplit
	split.StdoutFIFO = "/run/omegaflow/s1/op-1/stdout"
	if _, err := EncodeAwshRequest(split); err == nil {
		t.Fatal("split execution requires both FIFOs")
	}
	split.StderrFIFO = "/run/omegaflow/s1/op-1/stderr"
	// Only presentation timing selects split execution, and presentation
	// timing requires exclusive observation, so no validated public policy
	// can produce split plus shared observation.
	if _, err := EncodeAwshRequest(split); err == nil {
		t.Fatal("split execution requires exclusive observation")
	}
	split.Observation = ObservationExclusive
	if _, err := EncodeAwshRequest(split); err != nil {
		t.Fatalf("split execute: %v", err)
	}
	// Inspections travel as validated JSON and require exclusive
	// observation when present.
	inspected := base
	inspected.InspectionsJSON = `[{"inspection_id":"inspection-1","kind":"file_exists","path":"out.txt"}]`
	if _, err := EncodeAwshRequest(inspected); err == nil {
		t.Fatal("inspections with shared observation must fail")
	}
	inspected.Observation = ObservationExclusive
	if _, err := EncodeAwshRequest(inspected); err != nil {
		t.Fatalf("inspected execute: %v", err)
	}
	malformed := base
	malformed.InspectionsJSON = `{"not":"an array"}`
	if _, err := EncodeAwshRequest(malformed); err == nil {
		t.Fatal("malformed inspections JSON must fail")
	}
}

func TestSubmissionCapsule(t *testing.T) {
	submission, err := BuildTerminalSubmission("printf 'ok\\n'", "on", 0, "", "")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	expected := "\x1b[200~if __awsh_restore_input_state on 0; then\n" +
		"    { printf 'ok\\n'\n" +
		"    }\n" +
		"else\n" +
		"    { printf 'ok\\n'\n" +
		"    }\n" +
		"fi\x1b[201~\n"
	if string(submission) != expected {
		t.Fatalf("capsule bytes changed:\n%q\n%q", submission, expected)
	}
	if err := ValidateTerminalSubmission(submission); err != nil {
		t.Fatalf("validate: %v", err)
	}

	split, err := BuildTerminalSubmission("make", "off", 1, "/run/omegaflow/s1/op/stdout", "/run/omegaflow/s1/op/stderr")
	if err != nil {
		t.Fatalf("split build: %v", err)
	}
	if !strings.Contains(string(split), "    } >/run/omegaflow/s1/op/stdout 2>/run/omegaflow/s1/op/stderr\n") {
		t.Fatal("split branches must carry the identical redirections")
	}
	// The two source copies are byte-identical: the frame contains the
	// source exactly twice.
	if count := strings.Count(string(split), "{ make\n"); count != 2 {
		t.Fatalf("expected two identical authored branches, found %d", count)
	}

	// The exact paste terminator is rejected in any source context.
	_, err = BuildTerminalSubmission("echo '\x1b[201~'", "on", 0, "", "")
	mustCode(t, err, "source-invalid")

	// The doubled-source capsule maximum and its submit envelope hold at
	// the exact source limit.
	largest := strings.Repeat("a", MaxOperationSourceBytes)
	submission, err = BuildTerminalSubmission(largest, "off", 255, "", "")
	if err != nil {
		t.Fatalf("largest source: %v", err)
	}
	if len(submission) > MaxTerminalSubmissionBytes {
		t.Fatalf("submission %d exceeds %d", len(submission), MaxTerminalSubmissionBytes)
	}
	frame, err := EncodeAwshResult(AwshSubmit{OperationID: strings.Repeat("o", 64), TerminalSubmission: string(submission)})
	if err != nil {
		t.Fatalf("largest submit: %v", err)
	}
	if len(frame) > MaxSubmitFrameBytes || len(frame) > MaxAwshFrameBytes {
		t.Fatalf("submit frame %d exceeds its envelope", len(frame))
	}
}

func TestAwshStreamDecoderLifecycle(t *testing.T) {
	requestFrame, err := EncodeAwshRequest(AwshCancel{OperationID: "op-1", Reason: "deadline"})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	shutdownFrame, err := EncodeAwshRequest(AwshShutdown{})
	if err != nil {
		t.Fatalf("encode shutdown: %v", err)
	}
	decoder := NewAwshRequestStreamDecoder()
	var decoded []any
	stream := append(append([]byte{}, requestFrame...), shutdownFrame...)
	for _, chunk := range splitChunks(stream, 5) {
		messages, err := decoder.Feed(chunk)
		if err != nil {
			t.Fatalf("feed: %v", err)
		}
		decoded = append(decoded, messages...)
	}
	if len(decoded) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(decoded))
	}
	if err := decoder.Finish(); err != nil {
		t.Fatalf("finish after shutdown: %v", err)
	}

	// EOF before shutdown is a supervisor failure from Awsh's side.
	decoder = NewAwshRequestStreamDecoder()
	if _, err := decoder.Feed(requestFrame); err != nil {
		t.Fatalf("feed: %v", err)
	}
	mustCode(t, decoder.Finish(), "early-close")

	// EOF on the result descriptor before a valid closed result is
	// awsh-failed, never evidence that Bash exited — including after an
	// explicit shell_exit.
	shellExit, err := EncodeAwshResult(AwshShellExit{OperationID: "op-1", Status: 7, CWD: "/w"})
	if err != nil {
		t.Fatalf("encode shell_exit: %v", err)
	}
	results := NewAwshResultStreamDecoder()
	if _, err := results.Feed(shellExit); err != nil {
		t.Fatalf("feed: %v", err)
	}
	mustCode(t, results.Finish(), "early-close")
	closedFrame, err := EncodeAwshResult(AwshClosed{Reason: ClosedReasonShellEnded, Status: 7, CWD: "/w"})
	if err != nil {
		t.Fatalf("encode closed: %v", err)
	}
	if _, err := results.Feed(closedFrame); err != nil {
		t.Fatalf("feed closed: %v", err)
	}
	if err := results.Finish(); err != nil {
		t.Fatalf("finish after closed: %v", err)
	}

	// An earlier writer exiting with bytes still buffered closes
	// mid-frame.
	results = NewAwshResultStreamDecoder()
	if _, err := results.Feed(closedFrame[:len(closedFrame)-3]); err != nil {
		t.Fatalf("feed partial: %v", err)
	}
	mustCode(t, results.Finish(), "early-close")

	// Unterminated data cannot exceed the frame bound.
	results = NewAwshResultStreamDecoder()
	_, err = results.Feed(bytes.Repeat([]byte{'a'}, MaxAwshFrameBytes))
	mustCode(t, err, "frame-too-large")
}

func TestNoPrivateFrameAfterTerminalMessage(t *testing.T) {
	closedFrame, err := EncodeAwshResult(AwshClosed{Reason: ClosedReasonShutdown, Status: 137, CWD: "/w"})
	if err != nil {
		t.Fatalf("encode closed: %v", err)
	}
	readyFrame, err := EncodeAwshResult(AwshReady{AwshPID: 2, ShellPID: 3, CWD: "/w"})
	if err != nil {
		t.Fatalf("encode ready: %v", err)
	}
	results := NewAwshResultStreamDecoder()
	if _, err := results.Feed(closedFrame); err != nil {
		t.Fatalf("feed closed: %v", err)
	}
	_, err = results.Feed(readyFrame)
	mustCode(t, err, "out-of-state")

	shutdownFrame, err := EncodeAwshRequest(AwshShutdown{})
	if err != nil {
		t.Fatalf("encode shutdown: %v", err)
	}
	cancelFrame, err := EncodeAwshRequest(AwshCancel{OperationID: "op-1", Reason: "late"})
	if err != nil {
		t.Fatalf("encode cancel: %v", err)
	}
	requests := NewAwshRequestStreamDecoder()
	if _, err := requests.Feed(shutdownFrame); err != nil {
		t.Fatalf("feed shutdown: %v", err)
	}
	_, err = requests.Feed(cancelFrame)
	mustCode(t, err, "out-of-state")
}

func TestSubmissionValidationRejectsBrokenFraming(t *testing.T) {
	good, err := BuildTerminalSubmission("true", "on", 0, "", "")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	cases := [][]byte{
		[]byte("plain bytes"),
		[]byte(BracketedPasteBegin + "if __awsh_restore_input_state on 0; then\nfi"),
		[]byte("prefix" + string(good)),
		[]byte(BracketedPasteBegin + "echo hi" + BracketedPasteEnd + "\n"),
		[]byte(BracketedPasteBegin + "if __awsh_restore_input_state on 0; then\n" + BracketedPasteEnd + "x\nfi" + BracketedPasteEnd + "\n"),
		[]byte(string(good) + "\x00"),
	}
	for index, submission := range cases {
		if err := ValidateTerminalSubmission(submission); err == nil {
			t.Fatalf("case %d: expected rejection", index)
		}
	}
	if err := ValidateTerminalSubmission(good); err != nil {
		t.Fatalf("valid submission rejected: %v", err)
	}

	// The stream decoder rejects unknown types and non-UTF-8 headers.
	if _, err := NewAwshResultStreamDecoder().Feed([]byte("awsh-v1\x00mystery\x00")); err == nil {
		t.Fatal("unknown result type must fail before arity is known")
	}
	if _, err := NewAwshRequestStreamDecoder().Feed([]byte("awsh-v1\x00\xff\x00")); err == nil {
		t.Fatal("non-UTF-8 header must fail")
	}
}

func TestSubmissionValidationRequiresCanonicalCapsule(t *testing.T) {
	branch := func(source, redirections string) string {
		return "    { " + source + "\n    }" + redirections + "\n"
	}
	capsule := func(arguments, first, second string) []byte {
		return []byte(BracketedPasteBegin + "if " + InputStateFunctionName + " " + arguments +
			"; then\n" + first + "else\n" + second + "fi" + BracketedPasteEnd + "\n")
	}
	plain := branch("true", "")
	outFIFO := "/run/omegaflow/s1/op-1/stdout"
	errFIFO := "/run/omegaflow/s1/op-1/stderr"
	split := branch("make", " >"+outFIFO+" 2>"+errFIFO)
	cases := map[string][]byte{
		"unknown histexpand value":     capsule("maybe 0", plain, plain),
		"non-canonical status":         capsule("on 007", plain, plain),
		"signed status":                capsule("on +0", plain, plain),
		"out-of-range status":          capsule("on 256", plain, plain),
		"extra condition argument":     capsule("on 0 extra", plain, plain),
		"missing condition argument":   capsule("on", plain, plain),
		"divergent branch source":      capsule("on 0", plain, branch("false", "")),
		"divergent branch redirection": capsule("on 0", split, branch("make", " >"+outFIFO+" 2>"+outFIFO)),
		"ungenerated redirection form": capsule("on 0", branch("make", " > "+outFIFO+" 2> "+errFIFO), branch("make", " > "+outFIFO+" 2> "+errFIFO)),
		"unbraced branch":              capsule("on 0", "    true\n", "    true\n"),
		"relative fifo path":           capsule("on 0", branch("make", " >run/out 2>run/err"), branch("make", " >run/out 2>run/err")),
		"foreign condition function":   []byte(BracketedPasteBegin + "if other_function on 0; then\n" + plain + "else\n" + plain + "fi" + BracketedPasteEnd + "\n"),
		"non-numeric status":           capsule("on x", plain, plain),
		"missing frame terminator":     []byte(BracketedPasteBegin + "if " + InputStateFunctionName + " on 0; then\n" + plain + "else\n" + plain + "fx" + BracketedPasteEnd + "\n"),
		"branch without trailing LF":   capsule("on 0", "    { true\n    }", "    { true\n    }"),
		"branch without brace close":   capsule("on 0", "    { true\n", "    { true\n"),
		"branch closing indent":        capsule("on 0", "    { true\n  }\n", "    { true\n  }\n"),
		"redirection without space":    capsule("on 0", "    { make\n    }>/a 2>/b\n", "    { make\n    }>/a 2>/b\n"),
		"redirection without stderr":   capsule("on 0", "    { make\n    } >/a 2/b\n", "    { make\n    } >/a 2/b\n"),
	}
	for name, submission := range cases {
		if failure := ValidateTerminalSubmission(submission); failure == nil {
			t.Fatalf("%s: expected rejection", name)
		}
	}
	// The canonical PTY and split capsules remain accepted, and a capsule
	// whose authored source itself contains the generated branch closing
	// bytes still parses to exactly one canonical frame.
	for _, source := range []string{"true", "printf '\\n    } not a brace\\n'"} {
		accepted, buildErr := BuildTerminalSubmission(source, "off", 255, "", "")
		if buildErr != nil {
			t.Fatalf("build %q: %v", source, buildErr)
		}
		if failure := ValidateTerminalSubmission(accepted); failure != nil {
			t.Fatalf("canonical capsule %q rejected: %v", source, failure)
		}
	}
	accepted, buildErr := BuildTerminalSubmission("make", "on", 0, outFIFO, errFIFO)
	if buildErr != nil {
		t.Fatalf("build split: %v", buildErr)
	}
	if failure := ValidateTerminalSubmission(accepted); failure != nil {
		t.Fatalf("canonical split capsule rejected: %v", failure)
	}
}
