//go:build linux

package session

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/inspection"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type nativePrivatePoint struct {
	ctx context.Context
	w   *Workload
}

type nativePrivateSchedule struct {
	phase, variant string
	point          chan nativePrivatePoint
	release        chan struct{}
	waitRelease    chan struct{}
	once           sync.Once
	resume         sync.Once
	waitResume     sync.Once
	worker         *inspection.Worker
	launchWriter   *os.File
	crossed        bool
	resizeReady    chan struct{}
	resizeOnce     sync.Once
	senderDeadline time.Time
	eofAt, waitAt  time.Time
	accepted       chan struct{}
	acceptedOnce   sync.Once
	continueWrites int
}

// Only retained native overlays call these hooks. They pause the original
// owner or original Wait; they never manufacture a codec, status or reap.
var nativePrivateScheduling *nativePrivateSchedule

func nativePrivateBoundary(ctx context.Context, w *Workload, phase string) {
	s := nativePrivateScheduling
	if s == nil || s.phase != phase {
		return
	}
	s.once.Do(func() {
		s.point <- nativePrivatePoint{ctx, w}
		if !s.crossed {
			<-s.release
		}
	})
}

func nativePrivateBeforeWait(w *Workload) {
	s := nativePrivateScheduling
	if s != nil && (s.variant == "unreaped" || s.variant == "late-closure") {
		<-s.waitRelease
	}
}

func nativePrivateAfterWait() {
	if s := nativePrivateScheduling; s != nil {
		s.waitAt = time.Now()
	}
}

func nativePrivateEOFObserved() {
	if s := nativePrivateScheduling; s != nil {
		s.eofAt = time.Now()
	}
}

// Keep the existing launch owner after real decode until the zero-reap
// fixture has produced and observed the actual zero exit.
func nativePrivateLaunchAccepted() {
	s := nativePrivateScheduling
	if s != nil && s.phase == "launch" && s.variant == "zero-reap" {
		nativePrivateAccepted()
		<-s.waitRelease
	}
}

func nativePrivateAccepted() {
	if s := nativePrivateScheduling; s != nil {
		s.acceptedOnce.Do(func() { close(s.accepted) })
	}
}

type nativePrivateSenderWriter struct {
	helper.DeadlineWriter
	ctx   context.Context
	once  sync.Once
	first bool
}

func nativePrivateSender(w helper.DeadlineWriter, ctx context.Context, m protocol.PrivateMessage) helper.DeadlineWriter {
	if s := nativePrivateScheduling; s != nil {
		if _, ok := m.(protocol.PrivateContinue); ok {
			s.continueWrites++
			if s.variant == "sender-timer" {
				return &nativePrivateSenderWriter{DeadlineWriter: w, ctx: ctx}
			}
		}
	}
	return w
}

func (w *nativePrivateSenderWriter) SetWriteDeadline(deadline time.Time) error {
	w.once.Do(func() { nativePrivateScheduling.senderDeadline = deadline })
	return w.DeadlineWriter.SetWriteDeadline(deadline)
}

func (w *nativePrivateSenderWriter) Write(data []byte) (int, error) {
	if w.first {
		return w.DeadlineWriter.Write(data)
	}
	w.first = true
	n, err := w.DeadlineWriter.Write(data[:1])
	if err != nil || n != 1 {
		return n, err
	}
	timer := time.NewTimer(time.Until(nativePrivateScheduling.senderDeadline.Add(-500 * time.Millisecond)))
	defer timer.Stop()
	select {
	case <-timer.C:
	case <-w.ctx.Done():
		return n, w.ctx.Err()
	}
	nativePrivateBoundary(w.ctx, nil, "continuing")
	<-w.ctx.Done() // Actual fatal selection interrupts this original writer.
	return n, w.ctx.Err()
}

func nativePrivateFrame(variant string) (string, []byte, error) {
	code := "source-error"
	if variant == "unknown-code" {
		code = "future-code"
	} else if variant == "zero-reap" || variant == "nonzero-reap" || variant == "unreaped" {
		code = "shell-launch"
	}
	frame, err := protocol.EncodePrivate(protocol.PrivateProtocolError{Code: code, Message: "bounded native cause"}, protocol.AwshToEnvoy)
	if variant == "malformed" {
		frame = []byte("wrong-v1\x00bogus\x00")
	}
	return code, frame, err
}

// Queue fault bytes in Awsh's actual result pipe before exec can race startup.
// The original actor still starts, owns Bash, closes its pipe and is Waited.
func nativePrivateLaunchSeed(writer *os.File) error {
	s := nativePrivateScheduling
	if s == nil || s.phase != "launch" {
		return nil
	}
	fd, _, errno := syscall.Syscall(syscall.SYS_FCNTL, writer.Fd(), syscall.F_DUPFD_CLOEXEC, 0)
	if errno != 0 {
		return errno
	}
	s.launchWriter = os.NewFile(fd, "retained-actual-result-writer")
	_, frame, err := nativePrivateFrame(s.variant)
	if err == nil {
		_, err = writer.Write(frame)
	}
	return err
}

func nativePrivateOwnerHold(c *Coordinator) {
	s := nativePrivateScheduling
	if s != nil && s.crossed && c.resizing {
		s.resizeOnce.Do(func() {
			for {
				c.r.phaseMu.Lock()
				phase := c.r.phase
				c.r.phaseMu.Unlock()
				if phase != nil {
					close(s.resizeReady)
					break
				}
				time.Sleep(time.Millisecond)
			}
			<-s.release
		})
	}
	if s != nil && (s.phase == "partial-execute" || s.phase == "complete-execute") &&
		c.active != nil && !c.active.started && c.phase == awaitSubmit {
		<-s.release
	}
}

func nativePrivateExecuteWrite(w executeWriter, data []byte) (int, error) {
	s := nativePrivateScheduling
	if s == nil || (s.phase != "partial-execute" && s.phase != "complete-execute") {
		return w.DeadlineWriter.Write(data)
	}
	if s.phase == "partial-execute" {
		n, err := w.DeadlineWriter.Write(data[:1])
		if err != nil || n != 1 {
			return n, err
		}
		nativePrivateBoundary(w.ctx, nil, s.phase)
		if err := w.ctx.Err(); err != nil {
			return n, err
		}
		m, err := w.DeadlineWriter.Write(data[1:])
		return n + m, err
	}
	n, err := w.DeadlineWriter.Write(data)
	if err == nil {
		nativePrivateBoundary(w.ctx, nil, s.phase)
	}
	return n, err
}

func nativePrivateEpochName(phase string) string {
	var epochs map[string]string
	if err := json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_EPOCHS")), &epochs); err != nil || epochs[phase] == "" {
		panic("native private failure requires the unchanged trace's exact epoch oracle for " + phase)
	}
	return epochs[phase]
}

func nativePrivateRun(t *testing.T, s *nativePrivateSchedule, n *nativeOrdinary, ctx context.Context, owner *processCensus) <-chan startedResult {
	t.Helper()
	done := make(chan startedResult, 1)
	if s.phase == "ready" || s.phase == "idle" || s.phase == "drain" {
		go func() {
			var err error
			if s.phase == "drain" {
				err = n.c.shutdownIdle("recording-end", owner)
			} else {
				nativePrivateBoundary(ctx, n.c.w, s.phase)
				err = n.c.idleShellExit(ctx, owner)
			}
			done <- startedResult{err: err}
		}()
		return done
	}
	request := startRequest()
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	request.OperationID, request.Source = "fatal-private", "builtin read -r parked"
	if s.phase == "execute-input-barrier" {
		request.InputThrough = 1
	}
	if s.phase == "gated" || s.phase == "continuing" || s.phase == "continue-input-barrier" {
		request.Source = "awsh gate fatal-gate; builtin printf 'FORBIDDEN-CONTINUE\\n'; builtin read -r parked"
	}
	if s.phase == "cleanup" {
		request.Source = ":"
	}
	if s.phase == "live-inspection" || s.phase == "inspection-cancellation" {
		root := t.TempDir()
		for i := 0; i < 4000; i++ {
			if err := os.WriteFile(filepath.Join(root, fmt.Sprint(i)), []byte("actual inspection bytes"), 0600); err != nil {
				t.Fatal(err)
			}
		}
		request.Source = ":"
		request.Observation = "exclusive"
		request.Inspections = []protocol.Inspection{{InspectionID: "tree", Kind: "produces", Path: root, ProducerID: "producer", OutputID: "tree"}}
		workerReady := make(chan struct{})
		n.c.startInspection = func(plans []protocol.ResolvedInspection) (inspectionWorker, error) {
			w, err := inspection.StartWorker(plans)
			if err != nil {
				return nil, err
			}
			if err = syscall.Kill(w.PID(), syscall.SIGSTOP); err != nil {
				w.Kill()
				w.Reap(ctx)
				return nil, err
			}
			for {
				stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", w.PID()))
				state := strings.Fields(string(stat)[strings.LastIndexByte(string(stat), ')')+1:])
				if err == nil && len(state) != 0 && state[0] == "T" {
					break
				}
				if err != nil || len(state) == 0 || state[0] == "Z" || ctx.Err() != nil {
					w.Kill()
					w.Reap(ctx)
					return nil, fmt.Errorf("actual inspection worker was not live and stopped: %s: %w", stat, err)
				}
				time.Sleep(time.Millisecond)
			}
			s.worker = w
			close(workerReady)
			return w, nil
		}
		if s.phase == "inspection-cancellation" {
			go func() {
				select {
				case <-workerReady:
					n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: request.OperationID, Reason: "deadline"})
				case <-ctx.Done():
				}
			}()
		}
	}
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("actual execute was not decoded", event)
	}
	if nativePrivateEpochName(s.phase) == "operation-start" || s.phase == "execute-input-barrier" {
		go func() { out, err := n.c.Start(ctx, *actual); done <- startedResult{out, err} }()
		return done
	}
	out, err := n.c.Start(ctx, *actual)
	if err != nil || !out.Started {
		t.Fatal("actual operation did not start", out, err)
	}
	for {
		if _, started := n.frame(t).(*protocol.OperationStarted); started {
			break
		}
	}
	go func() { done <- startedResult{err: n.c.completeOperation(ctx, owner)} }()
	if s.phase == "grace" {
		n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: request.OperationID, Reason: "deadline"})
	}
	if s.phase == "continuing" || s.phase == "continue-input-barrier" {
		for {
			if _, ready := n.frame(t).(*protocol.OperationReady); ready {
				break
			}
		}
		through := int64(0)
		if s.phase == "continue-input-barrier" {
			through = 1
		}
		n.request(t, &protocol.Continue{Header: protocol.Header{Schema: protocol.Schema, Type: "continue", Seq: n.seq}, OperationID: request.OperationID, GateID: "fatal-gate", InputThrough: through})
	}
	return done
}

func TestNativePrivateFailureDevelopment(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_FAILURE") != "1" {
		t.Skip("requires actual qualified Awsh and Bash")
	}
	var cases []string
	if err := json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_CASES")), &cases); err != nil || len(cases) == 0 {
		t.Fatal("explicit native private-failure selection required", err)
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			ctx, end := context.WithTimeout(context.Background(), 30*time.Second)
			defer end()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativePrivateFailureFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_PRIVATE_FAILURE_CHILD="+scenario, "GORACE=atexit_sleep_ms=0")
			out, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(out), "NATIVE PRIVATE FAILURE "+scenario+" PROVED") {
				t.Fatalf("native private failure %s: %v\n%s", scenario, err, out)
			}
			t.Logf("%s", out)
		})
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("native children changed shared harness subreaper", before, after, err)
	}
}

func TestNativePrivateFailureFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_CHILD")
	if scenario == "" {
		return
	}
	parts := strings.Split(scenario, "/")
	if len(parts) != 2 {
		t.Fatal("invalid phase/variant", scenario)
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	s := &nativePrivateSchedule{phase: parts[0], variant: strings.TrimSuffix(parts[1], "-cancel-resize"), crossed: strings.HasSuffix(parts[1], "-cancel-resize"), point: make(chan nativePrivatePoint, 1), release: make(chan struct{}), waitRelease: make(chan struct{}), resizeReady: make(chan struct{}), accepted: make(chan struct{})}
	nativePrivateScheduling = s
	defer s.resume.Do(func() { close(s.release) })
	defer s.waitResume.Do(func() { close(s.waitRelease) })
	if s.phase == "launch" {
		nativePrivateLaunchFixture(t, s, scenario)
		return
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 22*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	done := nativePrivateRun(t, s, n, ctx, owner)
	var point nativePrivatePoint
	select {
	case point = <-s.point:
	case result := <-done:
		t.Fatal("native owner ended before phase", s.phase, result.err)
	case <-ctx.Done():
		t.Fatal("native phase was not reached", s.phase)
	}
	deadline, ok := point.ctx.Deadline()
	if !ok || (point.w != nil && point.w != n.c.w) {
		t.Fatal("phase did not retain its actual owner and epoch")
	}
	var resizePhase context.Context
	if s.crossed {
		n.c.r.mu.Lock()
		locked := true
		defer func() {
			if locked {
				n.c.r.mu.Unlock()
			}
		}()
		n.request(t, &protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: n.seq}, Columns: 123, Rows: 45})
		select {
		case <-s.resizeReady:
		case <-ctx.Done():
			t.Fatal("actual resize did not become outstanding")
		}
		n.c.r.phaseMu.Lock()
		resizePhase = n.c.r.phase
		n.c.r.phaseMu.Unlock()
		n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: "fatal-private", Reason: "deadline"})
		for len(n.c.w.channels.Controls()) == 0 {
			if ctx.Err() != nil {
				t.Fatal("actual cancel was not decoded and queued")
			}
			time.Sleep(time.Millisecond)
		}
		// Release the output lock only after fatal selection has cancelled its
		// actual resize worker. The terminal ioctl must never be reached.
		go func() {
			<-resizePhase.Done()
			n.c.r.mu.Unlock()
		}()
		locked = false
	}
	writer, err := os.OpenFile(fmt.Sprintf("/proc/%d/fd/4", owner.awsh), os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	code, frame, err := nativePrivateFrame(s.variant)
	if err != nil {
		t.Fatal(err)
	}
	faultWrittenAfter := time.Now()
	if _, err := writer.Write(frame); err != nil {
		t.Fatal(err)
	}
	// Observe the actual decoder queue before releasing the acceptance owner.
awaitDecoded:
	for len(n.c.private.fatal) == 0 {
		select {
		case <-s.accepted:
			break awaitDecoded
		case <-n.c.private.changed:
		case <-ctx.Done():
			t.Fatal("actual private fault was not decoded")
		}
	}
	if s.variant == "zero-reap" {
		if err := syscall.Kill(owner.shell, syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		select {
		case <-n.c.w.reaped:
			if n.c.w.waitErr != nil {
				t.Fatal("real Awsh did not exit zero after real Bash reap", n.c.w.waitErr)
			}
		case <-ctx.Done():
			t.Fatal("actual zero Awsh reap was not observed")
		}
	} else if s.variant == "signalled-reap" {
		if err := n.c.w.command.Process.Kill(); err != nil {
			t.Fatal(err)
		}
	} else if s.variant == "reset" {
		if err := n.c.w.Result.Close(); err != nil {
			t.Fatal(err)
		}
	}
	var delayed <-chan time.Time
	if s.variant == "late-closure" {
		go func() {
			time.Sleep(time.Until(deadline.Add(-time.Second)))
			writer.Close()
			time.Sleep(time.Until(deadline.Add(-200 * time.Millisecond)))
			s.waitResume.Do(func() { close(s.waitRelease) })
		}()
	} else if s.variant == "sender-timer" {
		go func() {
			time.Sleep(time.Until(s.senderDeadline.Add(300 * time.Millisecond)))
			writer.Close()
		}()
	} else if s.variant == "stalled-EOF" {
		if s.phase == "execute-input-barrier" {
			// The fault is already decoded while the actual Awsh pipe still
			// has this writer. Losing the selected public channel must not
			// replace the original private EOF deadline with cancellation.
			if err := n.terminal.Close(); err != nil {
				t.Fatal(err)
			}
			select {
			case <-n.c.private.ctx.Done():
			case <-ctx.Done():
				t.Fatal("actual terminal loss did not cancel private I/O")
			}
		}
		delayed = time.After(200 * time.Millisecond)
		go func() { <-delayed; writer.Close() }()
	} else if s.variant != "missing-EOF" && !(s.variant == "expiry" && nativePrivateEpochName(s.phase) == "final-drain" && s.phase != "drain") {
		writer.Close()
	}
	if s.variant == "expiry" && (nativePrivateEpochName(s.phase) != "final-drain" || s.phase == "drain") {
		<-point.ctx.Done()
	}
	accepted := time.Now()
	s.resume.Do(func() { close(s.release) })
	var result startedResult
	select {
	case result = <-done:
	case <-ctx.Done():
		t.Fatal("fatal acceptance did not return under original epoch")
	}
	f := n.c.privateFailure
	var failure *StartError
	expectedPhase := nativePrivateEpochName(s.phase)
	if f == nil || !errors.As(result.err, &failure) || result.out.Started || f.phase != expectedPhase || !failure.Deadline.Equal(f.deadline) {
		t.Fatal("fatal fact changed phase, deadline or result", result, f, failure)
	}
	if expectedPhase != "final-drain" || s.phase == "drain" {
		if !f.deadline.Equal(deadline) {
			t.Fatal("original named epoch was reset", f.deadline, deadline)
		}
	} else if f.deadline.Before(faultWrittenAfter.Add(finalDrainBudget)) || f.deadline.After(time.Now().Add(finalDrainBudget)) {
		t.Fatal("fallback inherited a caller or sender deadline", f.deadline, accepted)
	}
	if s.variant != "malformed" && !strings.Contains(result.err.Error(), code+": bounded native cause") {
		t.Fatal("bounded native cause lost", result.err)
	}
	if s.variant == "missing-EOF" || s.variant == "reset" {
		if f.eof || !strings.Contains(result.err.Error(), "private EOF") {
			t.Fatal("missing actual EOF was accepted", result.err, f)
		}
	} else if s.variant != "expiry" && !f.eof {
		t.Fatal("actual EOF was not retained", result.err)
	}
	if s.variant == "unreaped" {
		if f.reaped || !f.nonzero || !strings.Contains(result.err.Error(), "unreaped Awsh") {
			t.Fatal("missing original Wait became a reap receipt", result.err)
		}
		stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", owner.awsh))
		state := strings.Fields(string(stat)[strings.LastIndexByte(string(stat), ')')+1:])
		if err != nil || len(state) == 0 || state[0] != "Z" {
			t.Fatal("original Wait was not held before the actual kernel reap", string(stat), err)
		}
	} else if !f.reaped && (s.variant != "expiry" && s.variant != "missing-EOF" && s.variant != "reset" || time.Now().Before(f.deadline) || !strings.Contains(result.err.Error(), "unreaped Awsh")) {
		t.Fatal("missing original Awsh Wait was not retained as additional bounded failure", result.err)
	}
	if s.variant == "zero-reap" && (f.waitErr != nil || !strings.Contains(result.err.Error(), "requires nonzero")) {
		t.Fatal("zero shell-launch reap became successful", result.err)
	}
	if s.variant == "nonzero-reap" && f.waitErr == nil {
		t.Fatal("actual shell-launch nonzero reap was not retained")
	}
	if s.variant == "signalled-reap" {
		var exit *exec.ExitError
		if !errors.As(f.waitErr, &exit) || !exit.Sys().(syscall.WaitStatus).Signaled() {
			t.Fatal("actual signalled reap lost", f.waitErr)
		}
	}
	if s.variant == "stalled-EOF" && time.Since(accepted) < 180*time.Millisecond {
		t.Fatal("held writer did not delay actual EOF")
	}
	if s.variant == "late-closure" && (s.eofAt.Before(deadline.Add(-1050*time.Millisecond)) || !s.eofAt.Before(s.waitAt) || !s.waitAt.Before(deadline)) {
		t.Fatal("late actual EOF/Wait did not retain the original live bound", s.eofAt, s.waitAt, deadline)
	}
	if s.variant == "sender-timer" && (s.senderDeadline.IsZero() || !time.Now().After(s.senderDeadline) || !f.deadline.After(s.senderDeadline.Add(4*time.Second))) {
		t.Fatal("actual sender write epoch governed or suppressed fatal fallback", s.senderDeadline, f.deadline)
	}
	writer.Close()
	s.waitResume.Do(func() { close(s.waitRelease) })
	if s.worker != nil {
		select {
		case <-s.worker.Done():
		default:
			t.Fatal("fatal private traffic did not join the actual inspection worker")
		}
	}
	if s.crossed && (resizePhase == nil || resizePhase.Err() == nil || n.c.resize != nil || s.continueWrites != 0) {
		t.Fatal("fatal barrier did not resolve its outstanding resize", resizePhase, n.c.resize)
	}
	// The original epoch remains authoritative even if the harness must later
	// settle retained actors. Later cleanup cannot repair its recorded failure.
	_ = n.c.disposeFailure(ctx, owner, result.err)
	if s.crossed {
		n.terminal.SetWriteDeadline(time.Now().Add(time.Second))
		_, _ = n.terminal.Write([]byte("late-input\n"))
		n.terminal.SetReadDeadline(time.Now().Add(time.Second))
		output, err := io.ReadAll(n.terminal)
		if (err != nil && !errors.Is(err, syscall.ECONNRESET)) || strings.Contains(string(output), "FORBIDDEN-CONTINUE") {
			t.Fatal("late input released authored work after fatal barrier", string(output), err)
		}
	}
	// A prior expired disposal may have sent SIGKILL without observing its
	// original Wait yet. Join that owner before validating adopted-shell
	// topology; this fresh harness context cannot repair the original failure.
	_ = n.c.w.Close(ctx)
	select {
	case <-n.c.w.reaped:
	default:
		t.Fatal("original Awsh Wait did not settle during harness cleanup")
	}
	if err := owner.Cleanup(ctx, 0); err != nil {
		t.Fatal(err)
	}
	if err := owner.terminateShell(ctx); err != nil {
		t.Fatal(err)
	}
	_ = n.c.w.Close(ctx)
	if err := owner.reapAdoptedShell(ctx); err != nil {
		t.Fatal(err)
	}
	if err := owner.Cleanup(ctx, 0); err != nil {
		t.Fatal(err)
	}
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("actual actors remain after harness settlement", err)
	}
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	for {
		line, err := readLine(n.reader)
		if err != nil {
			if err != io.EOF {
				t.Fatal("fatal public stream did not close", err)
			}
			break
		}
		m, err := protocol.DecodeEnvoy(line)
		if err != nil {
			t.Fatal(err)
		}
		switch m.(type) {
		case *protocol.OperationCompleted, *protocol.OperationFailed, *protocol.OperationCancelled, *protocol.OperationFinalized, *protocol.Closed:
			t.Fatal("fatal private traffic became public success or operation result", m)
		case *protocol.ResizeApplied:
			if s.crossed {
				t.Fatal("fatal barrier applied its outstanding resize", m)
			}
		}
	}
	t.Logf("NATIVE PRIVATE FAILURE %s PROVED: phase=%s deadline=%s EOF=%v original-Wait=%v cause=%v", scenario, f.phase, f.deadline.Format(time.RFC3339Nano), f.eof, f.reaped, result.err)
}

func nativePrivateLaunchFixture(t *testing.T, s *nativePrivateSchedule, scenario string) {
	r, terminal, telemetry := nativePair(t)
	defer terminal.Close()
	defer telemetry.Close()
	defer r.s.Close()
	ctx, end := context.WithTimeout(context.Background(), 22*time.Second)
	defer end()
	done := make(chan error, 1)
	go func() { _, err := r.s.Launch(r.ctx, 80, 24); done <- err }()
	var point nativePrivatePoint
	select {
	case point = <-s.point:
	case <-ctx.Done():
		t.Fatal("actual launch boundary not reached")
	}
	w := point.w
	deadline, _ := point.ctx.Deadline()
	code, frame, err := nativePrivateFrame(s.variant)
	if err != nil || w == nil || w.command == nil || s.launchWriter == nil {
		t.Fatal("actual launch actors were not started", err)
	}
	writer := s.launchWriter
	defer writer.Close()
	var shell int
	// Observe the actual selected child and startup bytes without a second
	// reader or decoder. The queued fault remains the first private frame.
	for shell == 0 {
		paths, err := filepath.Glob(fmt.Sprintf("/proc/%d/task/*/children", w.command.Process.Pid))
		if err != nil {
			t.Fatal(err)
		}
		for _, path := range paths {
			children, _ := os.ReadFile(path)
			for _, raw := range strings.Fields(string(children)) {
				var pid int
				fmt.Sscan(raw, &pid)
				p, err := readProcess(pid)
				if err == nil && p.Parent == w.command.Process.Pid && p.Session == w.command.Process.Pid && p.Group == pid {
					shell = pid
				}
			}
		}
		if ctx.Err() != nil {
			t.Fatal("actual selected Bash was not observed", ctx.Err())
		}
		if shell == 0 {
			time.Sleep(time.Millisecond)
		}
	}
	owner, err := newProcessCensus(ctx, w.command.Process.Pid, shell)
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	if s.variant == "zero-reap" {
		raw, err := w.Result.SyscallConn()
		if err != nil {
			t.Fatal(err)
		}
		for {
			var available int32
			var errno syscall.Errno
			err = raw.Control(func(fd uintptr) {
				_, _, errno = syscall.Syscall(syscall.SYS_IOCTL, fd, 0x541b, uintptr(unsafe.Pointer(&available)))
			})
			if err != nil || errno != 0 {
				t.Fatal("actual startup pipe observation failed", err, errno)
			}
			if available > int32(len(frame)) {
				break
			}
			if ctx.Err() != nil {
				t.Fatal("actual startup produced no subsequent private bytes")
			}
			time.Sleep(time.Millisecond)
		}
		// The seeded frame must be accepted before terminal hangup can cancel
		// startup I/O; a queued but unread fault is not accepted evidence.
		s.resume.Do(func() { close(s.release) })
		select {
		case <-s.accepted:
		case <-ctx.Done():
			t.Fatal("launch fault was not accepted before zero-reap injection", ctx.Err())
		}
		if err := syscall.Kill(shell, syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		select {
		case <-w.reaped:
			if w.waitErr != nil {
				t.Fatal("actual launch Awsh did not exit zero", w.waitErr)
			}
		case <-ctx.Done():
			t.Fatal("original launch Wait not observed")
		}
		s.waitResume.Do(func() { close(s.waitRelease) })
	} else if s.variant == "signalled-reap" {
		if err := w.command.Process.Kill(); err != nil {
			t.Fatal(err)
		}
	}
	// Reset after acceptance preserves the first decoded cause. Before the
	// startup decoder runs, closing its reader would instead prevent acceptance.
	if s.variant == "reset" {
		go func() { time.Sleep(100 * time.Millisecond); w.Result.Close() }()
	}
	if s.variant == "stalled-EOF" {
		go func() { time.Sleep(200 * time.Millisecond); writer.Close() }()
	} else if s.variant != "missing-EOF" && s.variant != "expiry" && s.variant != "reset" {
		writer.Close()
	}
	s.resume.Do(func() { close(s.release) })
	var failure error
	select {
	case failure = <-done:
	case <-ctx.Done():
		t.Fatal("fatal launch exceeded its original epoch")
	}
	var f *privateFailure
	var start *StartError
	if !errors.As(failure, &f) || !errors.As(failure, &start) || f.phase != "launch-readiness" || !f.deadline.Equal(deadline) || !start.Deadline.Equal(deadline) {
		t.Fatal("launch private failure lost original phase or epoch", failure)
	}
	if s.variant != "malformed" && !strings.Contains(failure.Error(), code+": bounded native cause") {
		t.Fatal("launch lost its bounded private cause", failure)
	}
	if s.variant == "missing-EOF" || s.variant == "expiry" || s.variant == "reset" {
		if f.eof || !strings.Contains(failure.Error(), "private EOF") {
			t.Fatal("missing launch EOF accepted", failure)
		}
	} else if !f.eof {
		t.Fatal("actual launch EOF not retained", failure)
	}
	if s.variant == "unreaped" {
		if f.reaped || !f.nonzero || !strings.Contains(failure.Error(), "unreaped Awsh") {
			t.Fatal("unreaped launch Awsh accepted", failure)
		}
		stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", owner.awsh))
		state := strings.Fields(string(stat)[strings.LastIndexByte(string(stat), ')')+1:])
		if err != nil || len(state) == 0 || state[0] != "Z" {
			t.Fatal("launch original Wait was not held before the kernel reap", string(stat), err)
		}
	} else if !f.reaped && (s.variant != "expiry" && s.variant != "missing-EOF" && s.variant != "reset" || time.Now().Before(f.deadline) || !strings.Contains(failure.Error(), "unreaped Awsh")) {
		t.Fatal("missing actual launch Wait was not retained as additional bounded failure", failure)
	}
	if s.variant == "zero-reap" && (f.waitErr != nil || !strings.Contains(failure.Error(), "requires nonzero")) {
		t.Fatal("zero shell-launch reap became successful", failure)
	}
	if s.variant == "nonzero-reap" && f.waitErr == nil {
		t.Fatal("actual launch nonzero reap was not retained")
	}
	if s.variant == "signalled-reap" {
		var exit *exec.ExitError
		if !errors.As(f.waitErr, &exit) || !exit.Sys().(syscall.WaitStatus).Signaled() {
			t.Fatal("launch signalled reap lost", f.waitErr)
		}
	}
	writer.Close()
	s.waitResume.Do(func() { close(s.waitRelease) })
	_ = owner.Cleanup(ctx, 0)
	_ = owner.terminateShell(ctx)
	_ = w.Close(ctx)
	_ = owner.reapAdoptedShell(ctx)
	_ = owner.Cleanup(ctx, 0)
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("actual launch actors remain after harness settlement", err)
	}
	reader := bufio.NewReader(telemetry)
	for {
		line, err := readLine(reader)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		m, err := protocol.DecodeEnvoy(line)
		if err != nil {
			t.Fatal(err)
		}
		if _, diagnostic := m.(*protocol.Diagnostic); !diagnostic {
			t.Fatal("fatal launch published readiness or a result", m)
		}
	}
	t.Logf("NATIVE PRIVATE FAILURE %s PROVED: phase=%s EOF=%v original-Wait=%v cause=%v", scenario, f.phase, f.eof, f.reaped, failure)
}

// These two launch regressions are separate from the retained 250-case
// private-failure mapping. The runner's test-only overlay pauses the original
// startup owner after pump setup or emits actual bytes through the PTY slave.
func TestNativePrivateFailureOwnerCancellation(t *testing.T) {
	if os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_OWNER_CANCEL") != "1" {
		t.Skip("qualified native startup regression only")
	}
	r, terminal, telemetry := nativePair(t)
	defer terminal.Close()
	defer telemetry.Close()
	defer r.s.Close()
	w, err := r.s.Launch(r.ctx, 80, 24)
	t.Logf("launch workload=%v cause=%v", w, err)
	var failure *privateFailure
	if errors.As(err, &failure) {
		t.Fatalf("owned cancellation manufactured private failure: phase=%s eof=%v reaped=%v cause=%v", failure.phase, failure.eof, failure.reaped, failure.cause)
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("owned cancellation cause missing: %v", err)
	}
	fatal(t, r.s)
	assertNoNativeChildren(t)
}

func TestNativePrivateFailureOutputOverflow(t *testing.T) {
	if os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_OUTPUT_OVERFLOW") != "1" {
		t.Skip("qualified native startup regression only")
	}
	r, terminal, telemetry := nativePair(t)
	defer terminal.Close()
	defer telemetry.Close()
	defer r.s.Close()
	w, err := r.s.Launch(r.ctx, 80, 24)
	t.Logf("launch workload=%v cause=%v", w, err)
	if w != nil || err == nil || !strings.Contains(err.Error(), "startup output exceeds 4096 bytes") {
		t.Fatalf("actual startup overflow was not retained: workload=%v cause=%v", w, err)
	}
	telemetry.SetReadDeadline(time.Now().Add(time.Second))
	m := nativeFrame(t, bufio.NewReader(telemetry))
	diagnostic, ok := m.(*protocol.Diagnostic)
	if !ok || diagnostic.Code != "shell-launch-output" {
		t.Fatalf("actual output overflow lost its required diagnostic: %+v", m)
	}
	fatal(t, r.s)
	assertNoNativeChildren(t)
}
