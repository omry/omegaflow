//go:build linux

package session

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestNativeCapturedClosureDevelopment(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_CAPTURED_CLOSURE") != "1" {
		t.Skip("requires actual qualified Bash and Awsh")
	}
	cases := []string{"barrier-pty", "barrier-split", "barrier-pty-cancel", "barrier-split-cancel", "barrier-missing-eof"}
	if raw := os.Getenv("OMEGAFLOW_CAPTURED_CASES"); raw != "" {
		if err := json.Unmarshal([]byte(raw), &cases); err != nil || len(cases) == 0 {
			t.Fatal("invalid native captured case selection", err)
		}
	}
	if only := os.Getenv("OMEGAFLOW_CAPTURED_CASE"); only != "" {
		cases = []string{only}
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			ctx, end := context.WithTimeout(context.Background(), 25*time.Second)
			defer end()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeCapturedClosureFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_CAPTURED_CHILD="+scenario, "GORACE=atexit_sleep_ms=0")
			out, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(out), "NATIVE CAPTURED "+scenario+" PROVED") {
				t.Fatalf("actual captured closure %s: %v\n%s", scenario, err, out)
			}
			t.Logf("%s", out)
		})
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("native child changed shared harness subreaper state", before, after, err)
	}
}

func TestNativeCapturedClosureFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_CAPTURED_CHILD")
	if scenario == "" {
		return
	}
	if strings.HasPrefix(scenario, "B1-") || scenario == "extra-continue-reap-bound" {
		var mappings map[string]string
		if err := json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_CAPTURED_MAP")), &mappings); err != nil || mappings[scenario] == "" {
			t.Fatal("missing exact native case mapping", scenario, err)
		}
		name := mappings[scenario]
		if strings.HasPrefix(name, "start/") {
			nativeCapturedStart(t, name, scenario)
		} else if strings.HasPrefix(name, "epoch/") {
			nativeCapturedEpoch(t, name)
		} else if strings.HasPrefix(name, "continue/") {
			nativeCapturedContinuing(t, name)
		} else {
			t.Fatal("unknown native captured scenario", name)
		}
		t.Logf("NATIVE CAPTURED %s PROVED: %s", scenario, name)
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	request := startRequest()
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	request.OperationID, request.Source, request.InputThrough = "captured", "builtin printf 'MUST-NOT-EXECUTE'", 1
	if strings.Contains(scenario, "split") {
		request.ExecutionShape, request.Timing, request.Observation = "split", "presentation", "exclusive"
		n.c.setup = NewSplitSetup()
	}
	var heldWriter *os.File
	if scenario == "barrier-missing-eof" {
		// Retain one real writer to the existing result pipe. Awsh still
		// reports and exits; only the genuine kernel EOF is withheld.
		heldWriter, err = os.OpenFile(fmt.Sprintf("/proc/%d/fd/4", owner.awsh), os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		defer heldWriter.Close()
	}
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("actual public request was not decoded", event)
	}
	phase, stop := context.WithTimeout(ctx, time.Second)
	defer stop()
	deadline, _ := phase.Deadline()
	type result struct {
		out StartOutcome
		err error
	}
	done := make(chan result, 1)
	go func() { out, err := n.c.Start(phase, *actual); done <- result{out, err} }()
	input := n.c.w.channels.inputState()
	for {
		input.mu.Lock()
		held, watermark, through, wake := input.held, input.watermark, input.through, input.wake
		input.mu.Unlock()
		if held && watermark == 1 && through == 0 {
			break
		}
		select {
		case <-wake:
		case <-phase.Done():
			t.Fatal("actual input barrier did not become outstanding", phase.Err())
		}
	}
	if err := syscall.Kill(int(n.c.w.ShellPID), syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	// The actual Awsh supervisor must wait for Bash and report its status.
	select {
	case <-n.c.w.reaped:
		if n.c.w.waitErr != nil {
			t.Fatal("actual Awsh terminal report/reap failed", n.c.w.waitErr)
		}
	case <-phase.Done():
		t.Fatal("actual Awsh was not reaped under the original barrier", phase.Err())
	}
	if strings.HasSuffix(scenario, "-cancel") {
		n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: "captured", Reason: "crossed-cancel"})
	}
	var observed result
	select {
	case observed = <-done:
	case <-phase.Done():
		t.Fatal("actual terminal handoff did not finish", phase.Err())
	}
	if observed.err != nil || observed.out.Started || observed.out.ShellExit == nil || observed.out.ShellExit.Status != 137 ||
		observed.out.ShellExit.ActiveOperationID != "" || observed.out.ShellExitDeadline != deadline ||
		observed.out.ShellExitPhase != StartShellExitInputBarrier {
		t.Fatal("actual pre-write shell exit changed its original epoch or status", observed)
	}
	if n.c.active != nil {
		t.Fatal("discarded execute remains active")
	}
	// Later terminal bytes cannot satisfy the discarded execute or restart it.
	n.terminal.SetWriteDeadline(time.Now().Add(time.Second))
	if _, err := n.terminal.Write([]byte("late-input\n")); err != nil {
		t.Fatal(err)
	}
	err = n.c.completeStartShellExit(ctx, owner, observed.out)
	if heldWriter != nil {
		var failure *StartError
		if !errors.As(err, &failure) || failure.Code != "input-barrier-timeout" || failure.Deadline != deadline ||
			!errors.Is(err, context.DeadlineExceeded) || n.c.exitedEOF {
			t.Fatal("missing actual EOF did not fail at the captured epoch", err)
		}
		if err := heldWriter.Close(); err != nil {
			t.Fatal(err)
		}
	} else if err != nil || !n.c.exitedEOF {
		t.Fatal("actual captured terminal closure failed", err)
	}
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	closed := false
	for {
		line, err := readLine(n.reader)
		if err != nil {
			if err != io.EOF {
				t.Fatal("public terminal transcript did not close", err)
			}
			break
		}
		var frame struct{ Type, Reason string }
		if err := json.Unmarshal(line, &frame); err != nil {
			t.Fatal(err)
		}
		if strings.HasPrefix(frame.Type, "operation_") {
			t.Fatal("discarded execute emitted an operation result", string(line))
		}
		if frame.Type == "closed" {
			if heldWriter != nil || frame.Reason != "shell_ended" {
				t.Fatal("failed closure emitted successful closed", string(line))
			}
			closed = true
		}
	}
	if closed != (heldWriter == nil) {
		t.Fatal("wrong public closure outcome", closed)
	}
	if out, err := n.c.Start(ctx, startRequest()); err == nil || out.Started {
		t.Fatal("late input restarted a discarded execute", out, err)
	}
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("actual captured terminal actors remain", err)
	}
	t.Logf("NATIVE CAPTURED %s PROVED: actual shell137, Awsh0, original barrier %v and no operation result", scenario, deadline)
}

type nativeCapturedHook struct {
	phase            string
	entered, release chan struct{}
	mu               sync.Mutex
	terminal         *protocol.PrivateShellExit
	eof              bool
	submit           chan struct{}
	submitOnce       sync.Once
	barrierReady     chan struct{}
	barrier          time.Time
	freezeContinue   bool
	holdReap         bool
	waitReady        chan struct{}
	waitRelease      chan struct{}
	privatePublished chan struct{}
	cancelAvailable  chan struct{}
	factObserved     chan struct{}
	workerDone       chan struct{}
	workEntered      chan struct{}
	workRelease      chan struct{}
	joined           <-chan struct{}
	ownerStep        chan struct{}
	ownerStepRelease chan struct{}
	factRelease      chan struct{}
	armed            chan struct{}
	workContext      context.Context
	barrierContext   context.Context
	ownerFirst       bool
	forceWorkerFirst bool
	privateOnce      sync.Once
	cancelOnce       sync.Once
	factOnce         sync.Once
	workerOnce       sync.Once
	joinedOnce       sync.Once
	ownerStepOnce    sync.Once
}

var nativeCapturedHeld *nativeCapturedHook

// These observations are called only by the runner's retained source overlay.
// They freeze existing I/O/owner points and observe the original decoder.
func nativeCapturedBoundary(ctx context.Context, phase string) {
	h := nativeCapturedHeld
	if h == nil || h.phase != phase {
		return
	}
	close(h.entered)
	select {
	case <-h.release:
	case <-ctx.Done():
	}
}

func nativeCapturedExecute(ctx context.Context, writer executeWriter, frame []byte) error {
	nativeCapturedBoundary(ctx, "before-first-byte")
	err := WriteControl(ctx, writer, frame)
	if err == nil {
		nativeCapturedBoundary(ctx, "complete-execute")
	}
	return err
}

func nativeCapturedWrite(w executeWriter, data []byte) (int, error) {
	if h := nativeCapturedHeld; h != nil && h.phase == "partial-execute" && len(data) > 1 {
		n, err := w.DeadlineWriter.Write(data[:1])
		if n == 1 && err == nil {
			nativeCapturedBoundary(w.ctx, "partial-execute")
		}
		return n, err // Preserve the real partial write and original write loop.
	}
	return w.DeadlineWriter.Write(data)
}

func nativeCapturedPrivate(m protocol.PrivateMessage, err error) {
	if h := nativeCapturedHeld; h != nil {
		h.mu.Lock()
		defer h.mu.Unlock()
		if terminal, ok := m.(*protocol.PrivateShellExit); ok {
			h.terminal = terminal
		}
		if _, ok := m.(*protocol.PrivateSubmit); ok && h.submit != nil {
			h.submitOnce.Do(func() { close(h.submit) })
		}
		if err == io.EOF {
			h.eof = true
		}
	}
}

// These observations are called by the runner's retained overlays after the
// original bounded queue publication. They never read or replace a frame.
func nativeCapturedPrivatePublished(m protocol.PrivateMessage) {
	h := nativeCapturedHeld
	if h == nil || h.privatePublished == nil {
		return
	}
	if _, ok := m.(*protocol.PrivateShellExit); ok {
		h.privateOnce.Do(func() { close(h.privatePublished) })
	}
}

func nativeCapturedControlPublished(m protocol.Message) {
	h := nativeCapturedHeld
	if h == nil || h.cancelAvailable == nil {
		return
	}
	if _, ok := m.(*protocol.Cancel); ok {
		h.cancelOnce.Do(func() { close(h.cancelAvailable) })
	}
}

func nativeCapturedFactObserved(m protocol.PrivateMessage) {
	h := nativeCapturedHeld
	if h == nil || !h.forceWorkerFirst || h.factObserved == nil || h.armed == nil {
		return
	}
	select {
	case <-h.armed:
	default:
		return
	}
	if _, ok := m.(*protocol.PrivateShellExit); !ok {
		return
	}
	if !h.ownerFirst {
		select {
		case <-h.workerDone:
		case <-h.factRelease:
		}
	}
	h.factOnce.Do(func() { close(h.factObserved) })
	<-h.factRelease
}

func nativeCapturedTrackWork(ctx context.Context, done <-chan struct{}) {
	h := nativeCapturedHeld
	if h == nil || h.barrierContext != ctx {
		return
	}
	h.joinedOnce.Do(func() {
		h.mu.Lock()
		h.joined = done
		h.mu.Unlock()
	})
}

func nativeCapturedWorkExit(ctx context.Context) {
	h := nativeCapturedHeld
	if h == nil || !h.forceWorkerFirst || h.workerDone == nil || h.armed == nil {
		return
	}
	select {
	case <-h.armed:
	default:
		return
	}
	if h.ownerFirst && h.workEntered != nil {
		close(h.workEntered)
		<-h.workRelease
	}
	h.mu.Lock()
	h.workContext = ctx
	h.mu.Unlock()
	h.workerOnce.Do(func() { close(h.workerDone) })
}

func nativeCapturedOwnerStep() {
	h := nativeCapturedHeld
	if h == nil || h.ownerStep == nil {
		return
	}
	h.ownerStepOnce.Do(func() { close(h.ownerStep) })
	<-h.ownerStepRelease
}

func nativeCapturedContinueBarrier(ctx context.Context) {
	if h := nativeCapturedHeld; h != nil && h.barrierReady != nil {
		h.barrier, _ = ctx.Deadline()
		h.barrierContext = ctx
		if h.armed != nil {
			close(h.armed)
		}
		close(h.barrierReady)
		if h.freezeContinue {
			select {
			case <-h.release:
			case <-ctx.Done():
			}
		}
	}
}

func nativeCapturedWaitPublication() {
	if h := nativeCapturedHeld; h != nil && h.holdReap {
		close(h.waitReady) // Called only after the original command.Wait.
		<-h.waitRelease
	}
}

func nativeCapturedStart(t *testing.T, scenario, caseID string) {
	t.Helper()
	fields := strings.Split(scenario, "/")
	if len(fields) != 5 {
		t.Fatal("invalid native start case", scenario)
	}
	phase, mode, cancel, watermark := fields[1], fields[2], fields[3] == "cancel", fields[4] == "watermark"
	lateInputCase := caseID == "B1-C042-contract"
	h := &nativeCapturedHook{phase: phase, entered: make(chan struct{}), release: make(chan struct{}), submit: make(chan struct{})}
	nativeCapturedHeld = h
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	// The original decoder may still be joining in t.Cleanup. The hook remains
	// valid until that reader has joined; do not clear it while a read is live.
	request := startRequest()
	if mode == "split" {
		request = splitRequest()
		n.c.setup = NewSplitSetup()
	}
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	request.OperationID, request.Source = "captured-start", "builtin read -r hold"
	if watermark {
		request.InputThrough = 1
	}
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("missing actual execute", event)
	}
	type result struct {
		out StartOutcome
		err error
	}
	done := make(chan result, 1)
	go func() { out, err := n.c.Start(ctx, *actual); done <- result{out, err} }()
	var observed result
	if watermark && phase == "before-first-byte" {
		input := n.c.w.channels.inputState()
		for {
			input.mu.Lock()
			held, through, wake := input.held, input.through, input.wake
			input.mu.Unlock()
			if held && through == 0 {
				break
			}
			select {
			case <-wake:
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
		}
	} else {
		if watermark {
			// A start-released case must first satisfy the real watermark.
			if _, err := n.terminal.Write([]byte("x")); err != nil {
				t.Fatal(err)
			}
		}
		if phase == "start-released" {
			select {
			case observed = <-done:
				if observed.err != nil || !observed.out.Started {
					t.Fatal("actual start release failed", observed)
				}
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
		} else {
			select {
			case <-h.entered:
			case early := <-done:
				t.Fatal("start returned before frozen boundary", phase, early)
			case <-ctx.Done():
				t.Fatal("native boundary not observed", phase, ctx.Err())
			}
		}
	}
	if phase == "complete-execute" {
		// Sender write completion does not prove receiver acceptance. The
		// real Awsh submit proves it parsed and registered the full execute.
		select {
		case <-h.submit:
		case <-ctx.Done():
			t.Fatal("actual complete execute was not registered", ctx.Err())
		}
	}
	if err := syscall.Kill(int(n.c.w.ShellPID), syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	select {
	case <-n.c.w.reaped:
		if n.c.w.waitErr != nil {
			t.Fatal("actual Awsh reap failed", n.c.w.waitErr)
		}
	case <-ctx.Done():
		t.Fatal("actual Awsh did not reap the selected shell", ctx.Err())
	}
	if cancel {
		n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: request.OperationID, Reason: "crossed-cancel"})
	}
	if phase != "before-first-byte" {
		close(h.release)
	}
	if phase != "start-released" {
		select {
		case observed = <-done:
		case <-ctx.Done():
			t.Fatal("frozen start owner did not join", ctx.Err())
		}
	}
	if phase == "before-first-byte" {
		want := StartShellExitOperationStart
		if watermark {
			want = StartShellExitInputBarrier
		}
		if observed.err != nil || observed.out.Started || observed.out.ShellExit == nil || observed.out.ShellExitPhase != want {
			t.Fatal("pre-write exit lost handoff", observed)
		}
		if lateInputCase {
			// Deliver real terminal bytes after the captured shell-exit handoff.
			// The discarded execute must not resume, submit source, or restart.
			const lateInput = "late-input\n"
			if err := n.terminal.SetWriteDeadline(time.Now().Add(time.Second)); err != nil {
				t.Fatal("late-input write deadline failed", err)
			}
			written, err := n.terminal.Write([]byte(lateInput))
			if err != nil || written != len(lateInput) {
				t.Fatal("late terminal input was not delivered", written, err)
			}
			if n.c.active != nil {
				t.Fatal("late input resumed the discarded execute")
			}
		}
		err = n.c.completeStartShellExit(ctx, owner, observed.out)
	} else if phase == "start-released" {
		err = n.c.completeOperation(ctx, owner)
	} else {
		var failure *StartError
		if observed.err == nil || !errors.As(observed.err, &failure) || observed.out.Started ||
			failure.Code != "operation-start" || failure.Deadline.IsZero() {
			t.Fatal("pre-release shell exit became recoverable", phase, observed)
		}
		// Join the original reader before consuming its remaining buffered bytes
		// through the same decoder. This supplies actual teardown evidence; no
		// second concurrent reader, successful Wait substitute or peer is used.
		bound, stop := context.WithDeadline(context.Background(), failure.Deadline)
		defer stop()
		if err := n.c.private.close(bound); err != nil {
			t.Fatal("original private reader did not join", err)
		}
		if err := n.c.w.Result.SetReadDeadline(failure.Deadline); err != nil {
			t.Fatal(err)
		}
		for {
			m, readErr := protocol.ReadPrivate(n.c.w.Reader, protocol.AwshToEnvoy)
			nativeCapturedPrivate(m, readErr)
			if readErr == io.EOF {
				break
			}
			if readErr != nil {
				t.Fatal("actual terminal remainder malformed", readErr)
			}
		}
		if ended := n.c.disposeFailure(bound, owner, observed.err); !errors.Is(ended, observed.err) || !n.c.failureDisposed {
			t.Fatal("original-epoch fatal disposal failed", ended)
		}
	}
	if err != nil {
		t.Fatal("actual shell-end transaction failed", scenario, err)
	}
	h.mu.Lock()
	terminal, eof := h.terminal, h.eof
	h.mu.Unlock()
	if terminal == nil || terminal.Status != 137 || !eof {
		t.Fatal("missing actual terminal frame, EOF or status", terminal, eof)
	}
	wantID := request.OperationID
	if phase == "before-first-byte" || phase == "partial-execute" {
		wantID = ""
	}
	if terminal.ActiveOperationID != wantID {
		t.Fatal("wrong actual active operation ID", terminal.ActiveOperationID, wantID)
	}
	nativeCapturedPublicEnd(t, n, phase == "start-released", phase == "before-first-byte" || phase == "start-released")
	if lateInputCase {
		select {
		case <-h.submit:
			t.Fatal("late input submitted discarded source")
		default:
		}
		if n.c.active != nil {
			t.Fatal("late input left a resumed operation active")
		}
		if out, err := n.c.Start(ctx, startRequest()); err == nil || out.Started {
			t.Fatal("late input restarted a discarded execute", out, err)
		}
	}
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("actual actors remain after original-epoch handling", err)
	}
}

func nativeCapturedPublicEnd(t *testing.T, n *nativeOrdinary, started, wantClosed bool) {
	t.Helper()
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	closed, result := false, false
	for {
		line, err := readLine(n.reader)
		if err == io.EOF {
			break
		}
		if !wantClosed && errors.Is(err, syscall.ECONNRESET) {
			break // Fatal teardown may reset a socket with crossed unread input.
		}
		if err != nil {
			t.Fatal("public terminal transcript did not close", err)
		}
		var frame struct {
			Type, Reason, Code string
			Status             *int64
			ShellEnded         *bool `json:"shell_ended"`
		}
		if err := json.Unmarshal(line, &frame); err != nil {
			t.Fatal(err)
		}
		if frame.Type == "operation_completed" || frame.Type == "operation_failed" || frame.Type == "operation_cancelled" || frame.Type == "operation_finalized" {
			if !started {
				t.Fatal("unstarted/fatal crossing emitted operation result", string(line))
			}
			if frame.ShellEnded == nil || !*frame.ShellEnded {
				t.Fatal("actual shell exit lacks shell-ended result evidence", string(line))
			}
			if frame.Type == "operation_completed" {
				if frame.Status == nil || *frame.Status != 137 {
					t.Fatal("actual selected-shell status was replaced", string(line))
				}
			} else if frame.Type != "operation_failed" || frame.Status != nil || frame.Code != "shell-ended-unresolved" {
				t.Fatal("unresolved gate acquired synthetic status or result", string(line))
			}
			result = true
		}
		if frame.Type == "closed" {
			if !wantClosed || frame.Reason != "shell_ended" {
				t.Fatal("invalid successful public closure", string(line))
			}
			closed = true
		}
	}
	if closed != wantClosed || result != started {
		t.Fatal("wrong public terminal outcome", closed, result)
	}
}

type nativeCapturedEpochHold struct {
	variant             string
	captured, final     time.Time
	closureBeforeExpiry bool
}

var nativeCapturedEpochHeld *nativeCapturedEpochHold

func nativeCapturedClosureDecision(captured time.Time, drain context.Context) {
	if h := nativeCapturedEpochHeld; h != nil {
		h.final, _ = drain.Deadline()
		if h.variant == "both-expire" {
			// Hold this same dispatch turn until both original epochs are due.
			// The product then performs its normal absolute-deadline comparison.
			<-drain.Done()
		}
	}
}

func nativeCapturedDrainBoundary(ctx context.Context) {
	if h := nativeCapturedEpochHeld; h != nil {
		h.closureBeforeExpiry = time.Now().Before(h.captured)
		if h.variant == "closure-then-drain-expiry" {
			<-ctx.Done()
		} else if h.variant == "success" {
			// Successful EOF/reap retired captured closure. Delay only this
			// test observation until that former deadline passes, then finish
			// under the unchanged, already-running final-drain allowance.
			pause := time.NewTimer(time.Until(h.captured) + 20*time.Millisecond)
			defer pause.Stop()
			select {
			case <-pause.C:
			case <-ctx.Done():
			}
		}
	}
}

func nativeCapturedEpoch(t *testing.T, scenario string) {
	t.Helper()
	fields := strings.Split(scenario, "/")
	if len(fields) != 3 {
		t.Fatal("invalid native epoch scenario", scenario)
	}
	name, variant := fields[1], fields[2]
	h := &nativeCapturedHook{phase: "before-first-byte", entered: make(chan struct{}), release: make(chan struct{})}
	nativeCapturedHeld = h
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	var held *os.File
	if variant == "captured-expiry" || variant == "both-expire" {
		held, err = os.OpenFile(fmt.Sprintf("/proc/%d/fd/4", owner.awsh), os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		defer held.Close()
	}
	request := startRequest()
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	request.OperationID = "captured-epoch"
	if name == "terminal-input-barrier" {
		request.InputThrough = 1
	} else if name != "operation-start" {
		t.Fatal("unknown original epoch", name)
	}
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal(event)
	}
	phase, stop := context.WithTimeout(ctx, time.Second)
	defer stop()
	deadline, _ := phase.Deadline()
	type result struct {
		out StartOutcome
		err error
	}
	done := make(chan result, 1)
	go func() { out, err := n.c.Start(phase, *actual); done <- result{out, err} }()
	if request.InputThrough != 0 {
		input := n.c.w.channels.inputState()
		for {
			input.mu.Lock()
			held, through, wake := input.held, input.through, input.wake
			input.mu.Unlock()
			if held && through == 0 {
				break
			}
			select {
			case <-wake:
			case <-phase.Done():
				t.Fatal(phase.Err())
			}
		}
	} else {
		select {
		case <-h.entered:
		case <-phase.Done():
			t.Fatal("actual pre-write boundary not observed", phase.Err())
		}
	}
	if err := syscall.Kill(int(n.c.w.ShellPID), syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	var observed result
	select {
	case observed = <-done:
	case <-phase.Done():
		t.Fatal("actual pre-write handoff did not join", phase.Err())
	}
	if observed.err != nil || observed.out.ShellExit == nil || observed.out.ShellExitDeadline != deadline ||
		string(observed.out.ShellExitPhase) != name {
		t.Fatal("captured epoch changed at handoff", observed)
	}
	epoch := &nativeCapturedEpochHold{variant: variant, captured: deadline}
	nativeCapturedEpochHeld = epoch
	err = n.c.completeStartShellExit(ctx, owner, observed.out)
	if !epoch.final.After(deadline) {
		t.Fatal("final drain did not retain its separate later deadline", epoch.final, deadline)
	}
	if held != nil {
		var failed *StartError
		want, _ := capturedClosureCode(observed.out.ShellExitPhase)
		if !errors.As(err, &failed) || failed.Code != want || failed.Deadline != deadline || !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("captured expiry lost same-turn priority or phase", err)
		}
		if variant == "captured-expiry" && !time.Now().Before(epoch.final) {
			t.Fatal("captured closure was extended through final drain", deadline, epoch.final)
		}
	} else if variant == "closure-then-drain-expiry" {
		var failed *StartError
		if !errors.As(err, &failed) || failed.Code != "final-drain" || failed.Deadline != epoch.final ||
			!epoch.closureBeforeExpiry || !n.c.exitedEOF {
			t.Fatal("retired closure replaced later final-drain failure", err, epoch)
		}
	} else if err != nil || !epoch.closureBeforeExpiry || !n.c.exitedEOF || time.Now().Before(deadline) {
		t.Fatal("successful closure did not retire only its captured epoch", err, epoch)
	}
	nativeCapturedPublicEnd(t, n, false, variant == "success")
	select {
	case <-n.c.w.reaped:
		if n.c.w.waitErr != nil {
			t.Fatal(n.c.w.waitErr)
		}
	default:
		t.Fatal("missing actual Awsh Wait receipt")
	}
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("actual selected-shell tree remains", err)
	}
}

func nativeCapturedContinuing(t *testing.T, scenario string) {
	ownerFirst := os.Getenv("OMEGAFLOW_CAPTURED_OWNER_ORDER") == "owner-first"
	t.Helper()
	fields := strings.Split(scenario, "/")
	if len(fields) != 3 {
		t.Fatal("invalid native continuation crossing", scenario)
	}
	h := &nativeCapturedHook{barrierReady: make(chan struct{}), release: make(chan struct{}), freezeContinue: fields[2] == "cancel",
		holdReap: fields[2] == "hold-reap", waitReady: make(chan struct{}), waitRelease: make(chan struct{}),
		privatePublished: make(chan struct{}), cancelAvailable: make(chan struct{}), factObserved: make(chan struct{}),
		workerDone: make(chan struct{}), factRelease: make(chan struct{}), armed: make(chan struct{}),
		ownerFirst: ownerFirst, forceWorkerFirst: fields[2] == "cancel"}
	if fields[2] == "cancel" {
		h.ownerStep, h.ownerStepRelease = make(chan struct{}), make(chan struct{})
	}
	if ownerFirst {
		h.workEntered, h.workRelease = make(chan struct{}), make(chan struct{})
	}
	var publishWait sync.Once
	releaseWait := func() { publishWait.Do(func() { close(h.waitRelease) }) }
	defer releaseWait()
	var factRelease sync.Once
	releaseFact := func() { factRelease.Do(func() { close(h.factRelease) }) }
	defer releaseFact()
	var workRelease sync.Once
	releaseWork := func() {
		workRelease.Do(func() {
			if h.workRelease != nil {
				close(h.workRelease)
			}
		})
	}
	defer releaseWork()
	var ownerStepRelease sync.Once
	releaseOwnerStep := func() {
		ownerStepRelease.Do(func() {
			if h.ownerStepRelease != nil {
				close(h.ownerStepRelease)
			}
		})
	}
	defer releaseOwnerStep()
	var barrierRelease sync.Once
	releaseBarrier := func() { barrierRelease.Do(func() { close(h.release) }) }
	defer releaseBarrier()
	nativeCapturedHeld = h
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	request := startRequest()
	if fields[1] == "split" {
		request = splitRequest()
		n.c.setup = NewSplitSetup()
	}
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	request.OperationID, request.Source = "captured-continue", "awsh gate captured; builtin read -r hold"
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal(event)
	}
	if out, err := n.c.Start(ctx, *actual); err != nil || !out.Started {
		t.Fatal("actual gated source did not start", out, err)
	}
	if m := nativeControlEvent(t, n); m.(*protocol.OperationStarted).OperationID != request.OperationID {
		t.Fatal("wrong gated start", m)
	}
	done := make(chan error, 1)
	go func() { done <- n.c.completeOperation(ctx, owner) }()
	m := nativeControlEvent(t, n)
	ready, ok := m.(*protocol.OperationReady)
	if !ok || ready.GateID != "captured" {
		t.Fatal("actual gate was not ready", m)
	}
	through := n.c.w.channels.InputThrough() + 1
	n.request(t, &protocol.Continue{Header: protocol.Header{Schema: protocol.Schema, Type: "continue", Seq: n.seq},
		OperationID: request.OperationID, GateID: ready.GateID, InputThrough: through})
	select {
	case <-h.barrierReady:
	case early := <-done:
		t.Fatal("operation ended before actual continuation barrier", early)
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	input := n.c.w.channels.inputState()
	for !h.freezeContinue {
		input.mu.Lock()
		watermark, received, wake := input.watermark, input.through, input.wake
		input.mu.Unlock()
		if watermark == through && received < through {
			break
		}
		select {
		case <-wake:
		case <-ctx.Done():
			t.Fatal("actual continue watermark was not outstanding", ctx.Err())
		}
	}
	if h.holdReap {
		// Spend part of the original five-second barrier before shell exit.
		// A wrongly renewed cleanup bound is then observably later.
		pause := time.NewTimer(time.Second)
		defer pause.Stop()
		select {
		case <-pause.C:
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	if err := syscall.Kill(int(n.c.w.ShellPID), syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	if fields[2] == "cancel" {
		// Awsh reap is separate from the original private pump. Require the
		// actual shell_exit queue publication before releasing the continuation
		// owner, then hold that owner after it observes shell_exit until the
		// canceled WaitGateInput worker has joined. Crossed Cancel availability
		// is established while that owner is held.
		select {
		case <-n.c.w.reaped:
			if n.c.w.waitErr != nil {
				t.Fatal(n.c.w.waitErr)
			}
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
		select {
		case <-h.privatePublished:
		case <-ctx.Done():
			t.Fatal("actual shell_exit was not published to the private queue", ctx.Err())
		}
		n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq},
			OperationID: request.OperationID, Reason: "crossed-cancel"})
		select {
		case <-h.cancelAvailable:
		case <-ctx.Done():
			t.Fatal("crossed Cancel was not published to the control queue", ctx.Err())
		}
		releaseBarrier()
		if ownerFirst {
			select {
			case <-h.factObserved:
			case <-ctx.Done():
				t.Fatal("actual shell_exit was not observed by the owner", ctx.Err())
			}
			select {
			case <-h.workEntered:
			case <-ctx.Done():
				t.Fatal("exact canceled WaitGateInput worker did not reach its local done gate", ctx.Err())
			}
			h.mu.Lock()
			exactDone := h.joined
			h.mu.Unlock()
			if exactDone == nil {
				t.Fatal("owner-first proof did not capture exact c.work done channel")
			}
			select {
			case <-exactDone:
				t.Fatal("owner-first proof released exact c.work before owner step")
			default:
			}
			releaseFact()
			select {
			case <-h.ownerStep:
			case <-ctx.Done():
				t.Fatal("owner did not select lifecycle marker before worker release", ctx.Err())
			}
			select {
			case <-exactDone:
				t.Fatal("owner-first proof joined exact c.work before owner marker release")
			default:
			}
			releaseOwnerStep()
			releaseWork()
			select {
			case <-exactDone:
			case <-ctx.Done():
				t.Fatal("exact c.wait done did not close after owner-first release", ctx.Err())
			}
		} else {
			select {
			case <-h.workerDone:
			case <-ctx.Done():
				t.Fatal("canceled WaitGateInput worker did not join", ctx.Err())
			}
			h.mu.Lock()
			exactDone := h.joined
			h.mu.Unlock()
			if exactDone == nil {
				t.Fatal("worker-first proof did not capture exact c.work done channel")
			}
			select {
			case <-exactDone:
			case <-ctx.Done():
				t.Fatal("exact c.work done did not close before worker-first owner release", ctx.Err())
			}
			select {
			case <-h.factObserved:
			case <-ctx.Done():
				t.Fatal("actual shell_exit was not observed by the owner", ctx.Err())
			}
			releaseFact()
			select {
			case <-h.ownerStep:
			case <-ctx.Done():
				t.Fatal("owner did not select lifecycle marker after worker join", ctx.Err())
			}
			select {
			case <-exactDone:
			default:
				t.Fatal("owner marker selected before exact c.work done closed")
			}
			releaseOwnerStep()
		}
	}
	var completed error
	select {
	case completed = <-done:
	case <-ctx.Done():
		t.Fatal("actual gated shell-end transaction did not join", ctx.Err())
	}
	if fields[2] == "cancel" {
		h.mu.Lock()
		workContext := h.workContext
		h.mu.Unlock()
		if workContext == nil || workContext.Err() == nil {
			t.Fatal("exact interrupted work did not retain its governing canceled context")
		}
	}
	if h.holdReap {
		var failed *StartError
		if !errors.As(completed, &failed) || failed.Code != "input-barrier-timeout" || failed.Deadline != h.barrier ||
			!errors.Is(completed, context.DeadlineExceeded) || n.c.w.channels.Err() == nil {
			t.Fatal("withheld continuation reap did not fail under original barrier", completed)
		}
		if time.Now().After(h.barrier.Add(500 * time.Millisecond)) {
			t.Fatal("continuation closure was extended to later cleanup deadline", h.barrier)
		}
		select {
		case <-h.waitReady:
		default:
			t.Fatal("probe lacked actual kernel Wait before withheld publication")
		}
		select {
		case <-n.c.w.reaped:
			t.Fatal("probe did not withhold actual reap publication")
		default:
		}
		nativeCapturedPublicEnd(t, n, false, false)
		// Failure is already proven at its original deadline. Release only
		// test-owned publication, then account for harness resource disposal
		// separately; this fresh allowance cannot make product teardown pass.
		releaseWait()
		harness, stop := context.WithTimeout(context.Background(), finalDrainBudget)
		defer stop()
		if err := n.c.disposeFailure(harness, owner, completed); !errors.Is(err, completed) || !n.c.failureDisposed {
			t.Fatal("post-failure harness actors did not settle", err)
		}
		t.Log("Original barrier failure proven; later test-owned publication release and harness disposal are not timely product success")
		return
	}
	if completed != nil {
		t.Fatal("actual gated shell-end transaction failed", completed)
	}
	if n.c.exitedDeadline != h.barrier || n.c.exitedPhase != StartShellExitInputBarrier || !n.c.exitedEOF {
		t.Fatal("continue shell exit did not retain its original barrier through closure", n.c.exitedDeadline, h.barrier, n.c.exitedPhase, n.c.exitedEOF)
	}
	nativeCapturedPublicEnd(t, n, true, true)
	if err := owner.proveEnded(ctx); err != nil {
		t.Fatal("gated shell left actual controlled actors", err)
	}
}
