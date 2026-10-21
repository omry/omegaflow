//go:build linux

package session

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/inspection"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestNativeInspectionLifecycle(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_INSPECTION") != "1" {
		t.Skip("requires actual qualified Bash/Awsh assembly")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{"success", "missing", "split", "cancel", "finalize", "finalize-cancel"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, end := context.WithTimeout(context.Background(), 25*time.Second)
			defer end()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeInspectionFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_INSPECTION_CHILD="+scenario, "GORACE=atexit_sleep_ms=0")
			output, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(output), "NATIVE INSPECTION "+scenario+" PROVED") {
				t.Fatalf("actual inspection %s: %v\n%s", scenario, err, output)
			}
			t.Logf("%s", output)
		})
	}
	after, err := processSubreaperState()
	if err != nil || after != before {
		t.Fatal("shared harness subreaper changed", before, after, err)
	}
}

// Hold acceptance after the real worker's transport and actual process reap.
// This models an already available result that the owner has not accepted.
// Cancellation must still discard it. No filesystem algorithm is replaced.
type heldInspectionWorker struct {
	inspectionWorker
	joined  chan struct{}
	release chan struct{}
	once    sync.Once
	reads   atomic.Int32
}

func (w *heldInspectionWorker) Done() <-chan struct{} { return w.release }

func (w *heldInspectionWorker) Reap(ctx context.Context) error {
	if err := w.inspectionWorker.Reap(ctx); err != nil {
		return err
	}
	w.once.Do(func() { close(w.joined) })
	select {
	case <-w.release:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (w *heldInspectionWorker) Kill() error {
	err := w.inspectionWorker.Kill()
	close(w.release)
	return err
}
func (w *heldInspectionWorker) Result() ([]protocol.InspectionResult, error) {
	w.reads.Add(1)
	return w.inspectionWorker.Result()
}

func TestNativeInspectionFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_INSPECTION_CHILD")
	if scenario == "" {
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(n.c.w.channels.ctx, 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	e := &evidenceTranscript{n: n, sink: &nativeSplitSink{wake: make(chan struct{}, 1)}, base: n.through, offset: n.through, stream: "pty", ctx: ctx}
	collected := make(chan error, 1)
	n.terminal.SetReadDeadline(time.Now().Add(20 * time.Second))
	go func() { _, err := io.Copy(e.sink, n.terminal); collected <- err }()
	defer func() {
		n.terminal.SetReadDeadline(time.Now())
		select {
		case <-collected:
		case <-time.After(time.Second):
			t.Error("inspection output collector not joined")
		}
	}()
	root := t.TempDir()
	req := startRequest()
	req.Observation = "exclusive"
	// Awsh must resolve from the final exported environment and physical cwd.
	req.Source = "builtin cd '" + root + "'; export B7_FILE=artifact; builtin printf 'payload' >artifact; /usr/bin/mkdir directory; builtin printf 'tree' >directory/entry; builtin printf 'INSPECTION_WAITING\\n'; builtin read -r token; (exit 7)"
	req.Inspections = []protocol.Inspection{{InspectionID: "file", Kind: "produces", Path: "$B7_FILE", ProducerID: "producer", OutputID: "file"}, {InspectionID: "tree", Kind: "produces", Path: "directory", ProducerID: "producer", OutputID: "tree"}}
	if scenario == "missing" {
		req.Inspections = append(req.Inspections, protocol.Inspection{InspectionID: "missing", Kind: "file_exists", Path: "absent"})
	}
	if scenario == "split" {
		req.ExecutionShape, req.Timing = "split", "presentation"
		n.c.setup = NewSplitSetup()
	}
	var held *heldInspectionWorker
	status := make(chan int64, 1)
	n.c.startInspection = func(plans []protocol.ResolvedInspection) (inspectionWorker, error) {
		if len(plans) < 2 || plans[0].ResolvedPath != filepath.Join(root, "artifact") || plans[1].ResolvedPath != filepath.Join(root, "directory") {
			return nil, fmt.Errorf("actual final-state paths were not resolved")
		}
		w, err := inspection.StartWorker(plans)
		if err != nil {
			return nil, err
		}
		if scenario == "cancel" || scenario == "finalize-cancel" {
			held = &heldInspectionWorker{inspectionWorker: w, joined: make(chan struct{}), release: make(chan struct{})}
			status <- n.c.running.completed.Status
			return held, nil
		}
		status <- n.c.running.completed.Status
		return w, nil
	}
	start := e.start(t, req)
	done := make(chan error, 1)
	go func() { done <- n.c.completeOperation(ctx, owner) }()
	for !bytes.Contains(e.sink.bytes(), []byte("INSPECTION_WAITING")) {
		select {
		case <-e.sink.wake:
		case <-ctx.Done():
			t.Fatal("authored readiness absent", ctx.Err())
		}
	}
	if strings.HasPrefix(scenario, "finalize") {
		n.request(t, &protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq}, OperationID: req.OperationID, Reason: "recording-end"})
	} else {
		n.terminal.SetWriteDeadline(time.Now().Add(time.Second))
		if written, err := io.WriteString(n.terminal, "release\n"); err != nil {
			t.Fatal(err)
		} else {
			n.inputThrough += int64(written)
		}
	}
	var reported int64
	select {
	case reported = <-status:
	case <-ctx.Done():
		t.Fatal("actual worker did not launch", ctx.Err())
	}
	if scenario == "cancel" || scenario == "finalize-cancel" {
		select {
		case <-held.joined:
		case <-ctx.Done():
			t.Fatal("actual worker did not reap")
		}
		n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: req.OperationID, Reason: "deadline"})
	}
	m := e.event(t)
	through := int64(-1)
	switch v := m.(type) {
	case *protocol.OperationCompleted:
		if scenario == "missing" || strings.HasPrefix(scenario, "finalize") || scenario == "cancel" || v.Status != 7 || v.Cwd != root || len(v.InspectionResults) != 2 {
			t.Fatal("wrong actual inspection completion", m)
		}
		digest := sha256.Sum256([]byte("payload"))
		if v.InspectionResults[0].SHA256 != hex.EncodeToString(digest[:]) || v.InspectionResults[1].DigestAlgorithm != "directory-v2" || v.InspectionResults[1].PathKind != "directory" {
			t.Fatal("wrong actual filesystem result", v)
		}
		through = v.OutputThrough
	case *protocol.OperationFailed:
		if scenario != "missing" || v.Code != "inspection-missing" || v.Cwd != root {
			t.Fatal("wrong actual typed failure", m)
		}
		through = v.OutputThrough
	case *protocol.OperationCancelled:
		if scenario != "cancel" && scenario != "finalize-cancel" || v.Status == nil || *v.Status != reported || v.Reason != "deadline" || held.reads.Load() != 0 {
			t.Fatal("actual cancellation accepted result or lost Awsh status", m, reported)
		}
		through = v.OutputThrough
	case *protocol.OperationFinalized:
		if scenario != "finalize" || v.Reason != "recording-end" || len(v.InspectionResults) != 2 {
			t.Fatal("wrong inspected finalization", m)
		}
		through = v.OutputThrough
	default:
		t.Fatal("unexpected actual inspection result", m)
	}
	if through < start || through != e.offset {
		t.Fatal("inspection changed frozen closing output boundary", start, through, e.offset)
	}
	if err := finishCompletion(t, done); err != nil {
		t.Fatal("actual inspection lifecycle", err)
	}
	if err := owner.ProveIdle(ctx); err != nil {
		t.Fatal("inspection child remained after result", err)
	}
	if n.c.active != nil || n.c.w.CWD != root {
		t.Fatal("persistent shell state not retained")
	}
	// Prove a next operation uses the same shell after success, failure or cancel.
	next := startRequest()
	next.OperationID = "next"
	next.Source = "builtin test \"$B7_FILE\" = artifact && builtin test \"$PWD\" = '" + root + "'"
	nextStart := e.start(t, next)
	nextDone := make(chan error, 1)
	go func() { nextDone <- n.c.completeOperation(ctx, owner) }()
	result, ok := e.event(t).(*protocol.OperationCompleted)
	if !ok || result.Status != 0 || result.OutputStart != nextStart || len(result.InspectionResults) != 0 {
		t.Fatal("next operation lost actual persistent state", result)
	}
	if err := finishCompletion(t, nextDone); err != nil {
		t.Fatal(err)
	}
	t.Logf("NATIVE INSPECTION %s PROVED", scenario)
}
