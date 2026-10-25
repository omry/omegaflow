//go:build linux

package session

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestNativeEvidenceRanges(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_EVIDENCE") != "1" {
		t.Skip("requires genuinely qualified Bash targets")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	cases := []string{"pty-zero", "pty-seven", "pty-empty", "split-real", "suppress", "replace", "finalize", "cancel", "finalize-after-return", "cleanup-failure"}
	if only := os.Getenv("OMEGAFLOW_EVIDENCE_CASE"); only != "" {
		cases = []string{only}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeEvidenceRangeFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_EVIDENCE_CHILD="+scenario, "GORACE=atexit_sleep_ms=0")
			output, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(output), "EVIDENCE RANGE "+scenario+" PROVED") {
				t.Fatalf("evidence case %s: %v\n%s", scenario, err, output)
			}
			t.Logf("%s", output)
		})
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("shared harness subreaper state changed", before, after, err)
	}
}

type evidenceSegment struct {
	start, end int64
	stream     string
}

// These are the selected xterm/Readline controls, not authored output. Retain
// them in exact PTY evidence; split attribution keeps them out of stdout/stderr.
const evidenceReadlineExit = "\r\x1b[K\r\x1b[?2004l\r\r\n"
const evidenceReadlineEntry = "\x1b[?2004h"

// This collector consumes existing public marks and raw bytes. It neither
// evaluates authored assertions nor substitutes a cleanup/lifecycle owner.
type evidenceTranscript struct {
	n        *nativeOrdinary
	sink     *nativeSplitSink
	base     int64
	offset   int64
	elapsed  int64
	stream   string
	segments []evidenceSegment
	ctx      context.Context
}

func (e *evidenceTranscript) awaitBytes(t *testing.T, through int64) {
	t.Helper()
	for int64(len(e.sink.bytes())) < through-e.base {
		select {
		case <-e.sink.wake:
		case <-e.ctx.Done():
			t.Fatal("covering mark preceded terminal bytes", through, e.ctx.Err())
		}
	}
}

func (e *evidenceTranscript) event(t *testing.T) protocol.Message {
	t.Helper()
	var covering *protocol.OutputMark
	for {
		m := nativeFrame(t, e.n.reader)
		encoded, err := protocol.EncodeEnvoy(m)
		var header struct {
			protocol.Header
			OutputStart   *int64 `json:"output_start"`
			OutputThrough *int64 `json:"output_through"`
		}
		if err != nil || json.Unmarshal(encoded, &header) != nil || header.Seq != e.n.lastSeq+1 {
			t.Fatal("evidence public sequence", m, err)
		}
		e.n.lastSeq = header.Seq
		mark, ok := m.(*protocol.OutputMark)
		if !ok {
			boundary := header.OutputThrough
			if boundary == nil {
				boundary = header.OutputStart
			}
			if boundary != nil && (covering == nil || covering.Offset != *boundary || covering.Seq+1 != header.Seq) {
				t.Fatal("event lacks immediately preceding covering mark", m, covering)
			}
			return m
		}
		covering = mark
		if mark.Offset < e.offset || mark.ElapsedUS < e.elapsed ||
			(mark.Stream != "pty" && mark.Stream != "stdout" && mark.Stream != "stderr") {
			t.Fatal("invalid evidence mark", mark)
		}
		e.awaitBytes(t, mark.Offset)
		e.segments = append(e.segments, evidenceSegment{e.offset, mark.Offset, e.stream})
		e.offset, e.elapsed, e.stream = mark.Offset, mark.ElapsedUS, mark.Stream
	}
}

func (e *evidenceTranscript) logical(t *testing.T, start, end int64) map[string][]byte {
	t.Helper()
	if start < e.base || end < start || end > e.offset {
		t.Fatal("unclosed evidence range", start, end, e.offset)
	}
	raw := e.sink.bytes()
	got := map[string][]byte{}
	for _, segment := range e.segments {
		from, to := max(start, segment.start), min(end, segment.end)
		if from < to {
			got[segment.stream] = append(got[segment.stream], raw[from-e.base:to-e.base]...)
		}
	}
	return got
}

func (e *evidenceTranscript) start(t *testing.T, req protocol.Execute) int64 {
	t.Helper()
	n := e.n
	req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	req.InputThrough = n.inputThrough
	n.request(t, &req)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal(event)
	}
	out, err := n.c.Start(e.ctx, *actual)
	if err != nil || !out.Started {
		t.Fatal("evidence source did not start", out, err)
	}
	started, ok := e.event(t).(*protocol.OperationStarted)
	if !ok || started.OperationID != req.OperationID || started.OutputStart != out.OutputStart || e.offset != out.OutputStart {
		t.Fatal("evidence opening lacks original covering boundary", started, out, e.offset)
	}
	return started.OutputStart
}

func evidenceStatus(t *testing.T, m protocol.Message, id string, start int64, kind string, status int64) int64 {
	t.Helper()
	var gotID string
	var from, through int64
	switch v := m.(type) {
	case *protocol.OperationCompleted:
		if kind != "completed" {
			t.Fatal("planned finalization or cancellation claimed natural exit", m)
		}
		if v.Status != status || v.ShellEnded != nil {
			t.Fatal("genuine workload status was replaced", v)
		}
		gotID, from, through = v.OperationID, v.OutputStart, v.OutputThrough
	case *protocol.OperationFinalized:
		if kind != "finalized" || v.Reason != "recording-end" {
			t.Fatal("wrong planned finalization outcome", v)
		}
		encoded, err := protocol.EncodeEnvoy(v)
		var fields map[string]any
		if err != nil || json.Unmarshal(encoded, &fields) != nil {
			t.Fatal(err)
		}
		if _, syntheticStatus := fields["status"]; syntheticStatus {
			t.Fatal("planned finalization exposed synthetic exit evidence")
		}
		gotID, from, through = v.OperationID, v.OutputStart, v.OutputThrough
	case *protocol.OperationCancelled:
		if kind != "cancelled" || v.Reason != "user" || v.Status == nil || *v.Status != status {
			t.Fatal("partial cancelled range became successful assertion evidence", v)
		}
		gotID, from, through = v.OperationID, v.OutputStart, v.OutputThrough
	default:
		t.Fatal("missing typed terminal evidence outcome", m)
	}
	if gotID != id || from != start || through < from {
		t.Fatal("evidence range start mismatch", m, start)
	}
	return through
}

func (e *evidenceTranscript) result(t *testing.T, done <-chan error, id string, start int64, kind string, status int64) int64 {
	t.Helper()
	m := e.event(t)
	through := evidenceStatus(t, m, id, start, kind, status)
	if through != e.offset {
		t.Fatal("result omitted final covering mark", through, e.offset)
	}
	if err := finishCompletion(t, done); err != nil {
		t.Fatal("evidence transaction did not finish", err)
	}
	e.awaitBytes(t, through)
	return through
}

func (e *evidenceTranscript) natural(t *testing.T, owner *processCensus, req protocol.Execute, status int64) (int64, int64) {
	t.Helper()
	start := e.start(t, req)
	done := make(chan error, 1)
	go func() { done <- e.n.c.completeOrdinary(e.ctx, owner) }()
	return start, e.result(t, done, req.OperationID, start, "completed", status)
}

func evidenceIdleOutput(t *testing.T, n *nativeOrdinary, text string) {
	t.Helper()
	// Test-only outside output on the actual slave models session-scoped bytes.
	// No runtime descriptor lease or output provenance is inferred from it.
	fd, err := os.OpenFile("/proc/"+strconv.FormatInt(n.c.w.ShellPID, 10)+"/fd/1", os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer fd.Close()
	if written, err := io.WriteString(fd, text); err != nil || written != len(text) {
		t.Fatal("outside idle output", written, err)
	}
}

func TestNativeEvidenceRangeFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_EVIDENCE_CHILD")
	if scenario == "" {
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, cancel := context.WithTimeout(n.c.w.channels.ctx, 20*time.Second)
	defer cancel()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	e := &evidenceTranscript{n: n, sink: &nativeSplitSink{wake: make(chan struct{}, 1)}, base: n.through, offset: n.through, stream: "pty", ctx: ctx}
	readDone := make(chan error, 1)
	_ = n.terminal.SetReadDeadline(time.Now().Add(25 * time.Second))
	go func() { _, err := io.Copy(e.sink, n.terminal); readDone <- err }()
	defer func() {
		_ = n.terminal.SetReadDeadline(time.Now())
		select {
		case err := <-readDone:
			if err != nil && !os.IsTimeout(err) {
				t.Error("evidence collector failed", err)
			}
		case <-time.After(time.Second):
			t.Error("evidence collector did not join")
		}
	}()
	evidenceIdleOutput(t, n, "BEFORE_RANGE\n")
	req := startRequest()
	req.OperationID, req.Observation = "evidence-"+scenario, "exclusive"
	wantOut, wantErr := []byte("OUT-é\x00\xff\n\xc3"), []byte("\xa9ERR-λ\x00\xfe\n")
	req.Source = "builtin printf 'OUT-é\\000\\377\\n\\303'; builtin printf '\\251ERR-λ\\000\\376\\n' >&2; (exit 7)"
	status := int64(7)
	switch scenario {
	case "pty-zero":
		req.Source = strings.TrimSuffix(req.Source, "(exit 7)") + ":"
		status = 0
	case "pty-seven":
	case "pty-empty":
		req.Source, wantOut, wantErr = "(exit 7)", nil, nil
	case "split-real", "suppress", "replace":
		req.ExecutionShape, req.Timing = "split", "presentation"
		n.c.setup = NewSplitSetup()
		if scenario != "split-real" {
			req.Publication = scenario
		}
	case "finalize", "cancel", "cleanup-failure":
		req.Source = "builtin printf 'OPEN-é\\000\\377\\n'; builtin read -r unused"
	case "finalize-after-return":
	default:
		t.Fatal("unknown evidence scenario", scenario)
	}
	var start, through int64
	if scenario == "finalize" || scenario == "cancel" || scenario == "cleanup-failure" || scenario == "finalize-after-return" {
		start = e.start(t, req)
		entered, release := make(chan struct{}), make(chan struct{})
		cleanup := n.cleanup(t, req.OperationID)
		done := make(chan error, 1)
		peers := &controlPeers{sessionID: int64(n.c.w.command.Process.Pid), census: nativeControlCensus(n, 0)}
		go func() {
			done <- n.c.completeControlled(ctx, func(job context.Context, id string, pid int64) error {
				if err := cleanup(job, id, pid); err != nil {
					return err
				}
				if scenario == "cleanup-failure" {
					return errors.New("evidence cleanup proof denied")
				}
				if scenario == "finalize-after-return" {
					close(entered)
					select {
					case <-release:
					case <-job.Done():
						return job.Err()
					}
				}
				return nil
			}, peers)
		}()
		if scenario == "finalize-after-return" {
			select {
			case <-entered:
			case <-ctx.Done():
				t.Fatal("return cleanup was not entered", ctx.Err())
			}
		} else {
			for !bytes.Contains(e.sink.bytes(), []byte("OPEN-é\x00\xff\r\n")) {
				select {
				case <-e.sink.wake:
				case <-ctx.Done():
					t.Fatal("open operation did not produce its prefix", ctx.Err())
				}
			}
		}
		if scenario == "cancel" {
			n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: req.OperationID, Reason: "user"})
		} else {
			n.request(t, &protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq}, OperationID: req.OperationID, Reason: "recording-end"})
		}
		if scenario == "cleanup-failure" {
			failure := finishCompletion(t, done)
			if failure == nil || !strings.Contains(failure.Error(), "evidence cleanup proof denied") || n.c.w.channels.Err() == nil {
				t.Fatal("missing cleanup proof did not fail closed", failure)
			}
			_ = n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
			for {
				line, err := readLine(n.reader)
				if errors.Is(err, io.EOF) {
					break
				}
				if err != nil {
					t.Fatal(err)
				}
				m, err := protocol.DecodeEnvoy(line)
				if err != nil {
					t.Fatal(err)
				}
				switch m.(type) {
				case *protocol.OperationCompleted, *protocol.OperationCancelled, *protocol.OperationFinalized:
					t.Fatal("failed cleanup authorized partial evidence", m)
				}
			}
			t.Logf("EVIDENCE RANGE %s PROVED partial-range-invalid=true no-terminal-result=true", scenario)
			return
		}
		kind := "finalized"
		if scenario == "cancel" {
			kind, status = "cancelled", 130
		}
		if scenario == "finalize-after-return" {
			// The following resize acknowledgement proves the sole fact owner
			// accepted the preceding finalize while cleanup still held its helper.
			n.request(t, &protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: n.seq}, Columns: 93, Rows: 31})
			if _, ok := e.event(t).(*protocol.ResizeApplied); !ok {
				t.Fatal("post-return finalize was not ordered before cleanup release")
			}
			close(release)
			kind = "completed"
		}
		through = e.result(t, done, req.OperationID, start, kind, status)
	} else {
		start, through = e.natural(t, owner, req, status)
	}
	if !bytes.Equal(e.sink.bytes()[:start-e.base], []byte("BEFORE_RANGE\r\n"+evidenceReadlineExit)) {
		t.Fatal("preceding idle output entered the operation range", start, e.sink.bytes())
	}
	if scenario == "finalize" {
		wantOut, wantErr = []byte("OPEN-é\x00\xff\n"), nil
	}
	if scenario != "cancel" {
		got := e.logical(t, start, through)
		if req.ExecutionShape == "split" {
			if !bytes.Equal(got["stdout"], wantOut) || !bytes.Equal(got["stderr"], wantErr) || !bytes.Equal(got["pty"], []byte(evidenceReadlineEntry)) {
				t.Fatal("logical exclusive output lost exact stream bytes", got)
			}
		} else {
			want := append(bytes.ReplaceAll(wantOut, []byte("\n"), []byte("\r\n")), bytes.ReplaceAll(wantErr, []byte("\n"), []byte("\r\n"))...)
			if scenario == "finalize" {
				// Bash ends the interrupted read line before entering Readline.
				want = append(want, '\r', '\n')
			}
			want = append(want, []byte(evidenceReadlineEntry)...)
			if !bytes.Equal(got["pty"], want) || len(got["stdout"])+len(got["stderr"]) != 0 {
				t.Fatal("PTY evidence did not retain exact terminal bytes", got, want)
			}
		}
	}
	beforeSuffix := append([]byte(nil), e.sink.bytes()[start-e.base:through-e.base]...)
	evidenceIdleOutput(t, n, "AFTER_RANGE\n")
	next := startRequest()
	next.OperationID, next.Source = "following-operation", "builtin printf 'FOLLOWING\\n'"
	next.Observation = "exclusive"
	secondStart, secondEnd := e.natural(t, owner, next, 0)
	if !bytes.Equal(e.sink.bytes()[through-e.base:secondStart-e.base], []byte("AFTER_RANGE\r\n"+evidenceReadlineExit)) ||
		!bytes.Equal(e.logical(t, secondStart, secondEnd)["pty"], []byte("FOLLOWING\r\n"+evidenceReadlineEntry)) ||
		!bytes.Equal(beforeSuffix, e.sink.bytes()[start-e.base:through-e.base]) {
		t.Fatal("closed evidence range absorbed later output or lost persistence")
	}
	if err := owner.ProveIdle(ctx); err != nil {
		t.Fatal("evidence cases left an operation process", err)
	}
	t.Logf("EVIDENCE RANGE %s PROVED start=%d through=%d next-start=%d raw-policy=%s/%s/%s status-boundary=true", scenario, start, through, secondStart, req.ExecutionShape, req.Timing, req.Publication)
}
