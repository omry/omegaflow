//go:build linux

package session

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type startFixture struct {
	relayFixture
	c       *Coordinator
	control *os.File
	reader  *bufio.Reader
	result  *os.File
	seq     int64
}

func startFixtureFor(t *testing.T, setup SplitSetup) *startFixture {
	t.Helper()
	f := &startFixture{relayFixture: relayFixtureFor(t), seq: 2}
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	f.control, f.reader, f.r.w.Control = r, bufio.NewReader(r), w
	r, w, err = os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	f.result, f.r.w.Result, f.r.w.Reader = w, r, bufio.NewReader(r)
	t.Cleanup(func() { f.control.Close(); f.result.Close(); f.r.w.Control.Close(); f.r.w.Result.Close() })
	f.c, err = NewCoordinator(f.r.w.channels.ctx, f.r.w, f.r, setup)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = f.c.Close(relayTestContext(t)) })
	return f
}

func startRequest() protocol.Execute {
	return protocol.Execute{OperationID: "operation-1", Source: ":", ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "shared", Inspections: []protocol.Inspection{}}
}

func (f *startFixture) public(t *testing.T, m protocol.Message) {
	t.Helper()
	var h *protocol.Header
	switch v := m.(type) {
	case *protocol.Execute:
		h = &v.Header
		h.Type = "execute"
	case *protocol.Cancel:
		h = &v.Header
		h.Type = "cancel"
	default:
		t.Fatal("unsupported test message")
	}
	h.Schema, h.Seq = protocol.Schema, f.seq
	f.seq++
	b, err := protocol.EncodeController(m)
	if err != nil {
		t.Fatal(err)
	}
	_ = f.telemetry.SetWriteDeadline(time.Now().Add(time.Second))
	if err := helper.WriteAll(f.telemetry, b); err != nil {
		t.Fatal(err)
	}
}

type startedResult struct {
	out StartOutcome
	err error
}

func (f *startFixture) start(t *testing.T, ctx context.Context, request protocol.Execute) <-chan startedResult {
	t.Helper()
	f.public(t, &request)
	e := receiveInput(t, f.r.w.channels)
	got, ok := e.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("missing execute", e)
	}
	done := make(chan startedResult, 1)
	go func() { out, err := f.c.Start(ctx, *got); done <- startedResult{out, err} }()
	return done
}

func finishStart(t *testing.T, done <-chan startedResult) startedResult {
	t.Helper()
	select {
	case r := <-done:
		return r
	case <-time.After(7 * time.Second):
		t.Fatal("start retained its worker")
		return startedResult{}
	}
}

func (f *startFixture) private(t *testing.T, m protocol.PrivateMessage) {
	t.Helper()
	b, err := protocol.EncodePrivate(m, protocol.AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	_ = f.result.SetWriteDeadline(time.Now().Add(time.Second))
	if err = helper.WriteAll(f.result, b); err != nil {
		t.Fatal(err)
	}
}

func (f *startFixture) readPrivate(t *testing.T) protocol.PrivateMessage {
	t.Helper()
	_ = f.control.SetReadDeadline(time.Now().Add(2 * time.Second))
	m, err := protocol.ReadPrivate(f.reader, protocol.EnvoyToAwsh)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func (f *startFixture) throughStarted(t *testing.T) *protocol.OperationStarted {
	t.Helper()
	execute, ok := f.readPrivate(t).(*protocol.PrivateExecute)
	if !ok || execute.OperationID != "operation-1" || execute.Source != ":" {
		t.Fatal("wrong private execute", execute)
	}
	f.private(t, protocol.PrivateSubmit{OperationID: execute.OperationID})
	_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
	var trigger [2]byte
	if _, err := io.ReadFull(f.slave, trigger[:]); err != nil || trigger != [2]byte{0x18, 0x02} {
		t.Fatal("wrong PTY trigger", trigger, err)
	}
	if _, err := f.slave.Write([]byte("before-start\n")); err != nil {
		t.Fatal(err)
	}
	f.private(t, protocol.PrivateStartPrepared{OperationID: execute.OperationID})
	if m, ok := f.readPrivate(t).(*protocol.PrivateStartRelease); !ok || m.OperationID != execute.OperationID {
		t.Fatal("missing start_release", m)
	}
	covered := false
	for !covered {
		m, ok := nextRelayFrame(t, f.relayFixture).(*protocol.OutputMark)
		if !ok {
			t.Fatal("event before prepared mark")
		}
		covered = m.Offset == int64(len("before-start\n"))
	}
	f.private(t, protocol.PrivateStarted{OperationID: execute.OperationID})
	var started *protocol.OperationStarted
	for started == nil {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationStarted:
			started = m
		default:
			t.Fatal("unexpected start event", m)
		}
	}
	if started.OutputStart != int64(len("before-start\n")) {
		t.Fatal("wrong output_start", started)
	}
	if m, ok := f.readPrivate(t).(*protocol.PrivateStartedAck); !ok || m.OperationID != execute.OperationID {
		t.Fatal("missing started_ack", m)
	}
	return started
}

func TestStartTransactionHoldsInputThroughMatchingRelease(t *testing.T) {
	f := startFixtureFor(t, nil)
	done := f.start(t, context.Background(), startRequest())
	started := f.throughStarted(t)
	input := []byte("authored-input")
	if _, err := f.terminal.Write(input); err != nil {
		t.Fatal(err)
	}
	select {
	case e := <-f.r.w.channels.Events():
		t.Fatal("controller input became eligible before release", e)
	case <-time.After(40 * time.Millisecond):
	}
	if got := f.r.w.channels.InputThrough(); got != 0 {
		t.Fatal("read before release", got)
	}
	f.private(t, protocol.PrivateStartReleased{OperationID: "operation-1"})
	r := finishStart(t, done)
	if r.err != nil || !r.out.Started || r.out.OutputStart != started.OutputStart {
		t.Fatal(r)
	}
	e := receiveInput(t, f.r.w.channels)
	if e.InputOperation != "operation-1" || !bytes.Equal(e.Input, input) {
		t.Fatal("held input lost its operation", e)
	}
	if err := f.r.ForwardInput(relayTestContext(t), e); err != nil {
		t.Fatal(err)
	}
	got := make([]byte, len(input))
	if _, err := io.ReadFull(f.slave, got); err != nil || !bytes.Equal(got, input) {
		t.Fatal("input not forwarded after release", got, err)
	}
	if _, err := f.c.Start(relayTestContext(t), startRequest()); err == nil {
		t.Fatal("second start while operation active")
	}
}

func TestStartQueuesCancelAfterPrivateAttempt(t *testing.T) {
	for _, afterPublic := range []bool{false, true} {
		t.Run(fmt.Sprint(afterPublic), func(t *testing.T) {
			f := startFixtureFor(t, nil)
			done := f.start(t, context.Background(), startRequest())
			if afterPublic {
				f.throughStarted(t)
				f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "user"})
			} else {
				// Pipe bytes prove the first attempted execute before cancellation.
				m := f.readPrivate(t).(*protocol.PrivateExecute)
				f.public(t, &protocol.Cancel{OperationID: m.OperationID, Reason: "user"})
				f.private(t, protocol.PrivateSubmit{OperationID: m.OperationID})
				var b [2]byte
				_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
				if _, err := io.ReadFull(f.slave, b[:]); err != nil {
					t.Fatal(err)
				}
				f.private(t, protocol.PrivateStartPrepared{OperationID: m.OperationID})
				f.readPrivate(t)
				f.private(t, protocol.PrivateStarted{OperationID: m.OperationID})
				f.readPrivate(t)
			}
			// The hold mutex publishes the earlier operation creation. Wait on
			// the attempt mutex for actual owner acceptance, not socket arrival.
			g := f.r.w.channels.inputState()
			g.mu.Lock()
			if !g.held {
				g.mu.Unlock()
				t.Fatal("input hold ended before release")
			}
			g.mu.Unlock()
			op := f.c.active
			ctx := relayTestContext(t)
			for {
				op.attemptMu.Lock()
				accepted := op.cancel != nil
				op.attemptMu.Unlock()
				if accepted {
					break
				}
				if ctx.Err() != nil {
					t.Fatal("cancel never accepted")
				}
				runtime.Gosched()
			}
			f.private(t, protocol.PrivateStartReleased{OperationID: "operation-1"})
			r := finishStart(t, done)
			if r.err != nil || !r.out.Started || r.out.QueuedCancel == nil || r.out.QueuedCancel.OperationID != "operation-1" {
				t.Fatal("cancel did not stay queued through release", r)
			}
		})
	}
}

func TestStartWatermarkTimeoutRemainsRecoverable(t *testing.T) {
	f := startFixtureFor(t, nil)
	req := startRequest()
	req.InputThrough = 1
	ctx, cancel := context.WithTimeout(context.Background(), 35*time.Millisecond)
	defer cancel()
	r := finishStart(t, f.start(t, ctx, req))
	if r.err != nil || r.out.Started || f.r.w.channels.Err() != nil {
		t.Fatal("barrier timeout was fatal", r, f.r.w.channels.Err())
	}
	var failed *protocol.OperationFailed
	for failed == nil {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationFailed:
			failed = m
		default:
			t.Fatal("unexpected timeout event", m)
		}
	}
	if failed.Code != "input-barrier-timeout" || failed.OutputStart != failed.OutputThrough || failed.Cwd != "/tmp" {
		t.Fatal(failed)
	}
	if _, err := f.terminal.Write([]byte("discard")); err != nil {
		t.Fatal(err)
	}
	waitInputCount(t, f.r.w.channels, 7)
}

type testSplitSetup struct {
	entered         chan context.Context
	proceed         chan struct{}
	rollbackEntered chan struct{}
	rollbackRelease chan struct{}
	mu              sync.Mutex
	rolled          bool
	end             time.Time
}

func (s *testSplitSetup) Prepare(ctx context.Context, request protocol.Execute) (string, string, error) {
	s.entered <- ctx
	select {
	case <-s.proceed:
		root := "/run/omegaflow/session/split/" + request.OperationID
		return root + ".stdout", root + ".stderr", nil
	case <-ctx.Done():
		return "", "", ctx.Err()
	}
}

func (s *testSplitSetup) Rollback(ctx context.Context, _ string) error {
	if s.rollbackEntered != nil {
		close(s.rollbackEntered)
		select {
		case <-s.rollbackRelease:
		case <-ctx.Done():
			return ctx.Err()
		}
		s.mu.Lock()
		s.rolled = true
		s.end, _ = ctx.Deadline()
		s.mu.Unlock()
		return nil
	}
	s.mu.Lock()
	s.rolled = true
	s.end, _ = ctx.Deadline()
	s.mu.Unlock()
	return ctx.Err()
}

func splitRequest() protocol.Execute {
	r := startRequest()
	r.ExecutionShape, r.Timing, r.Observation = "split", "presentation", "exclusive"
	return r
}

func TestStartCancellationBeforePrivateWriteRollsBack(t *testing.T) {
	setup := &testSplitSetup{entered: make(chan context.Context, 1), proceed: make(chan struct{})}
	f := startFixtureFor(t, setup)
	done := f.start(t, context.Background(), splitRequest())
	ctx := <-setup.entered
	original, _ := ctx.Deadline()
	f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "user"})
	r := finishStart(t, done)
	if r.err != nil || r.out.Started || !setup.rolled || setup.end != original || f.r.w.channels.Err() != nil {
		t.Fatal("pre-start cancellation did not roll back under original epoch", r, setup.rolled, setup.end, original)
	}
	_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("private execute escaped pre-start cancellation", n, err)
	}
	for {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationCancelled:
			if m.Status != nil || m.OutputStart != m.OutputThrough || m.Reason != "user" {
				t.Fatal(m)
			}
			return
		default:
			t.Fatal("unexpected cancellation event", m)
		}
	}
}

func TestStartRejectsWrongPrivatePhaseAndID(t *testing.T) {
	for _, m := range []protocol.PrivateMessage{
		protocol.PrivateStartPrepared{OperationID: "operation-1"},
		protocol.PrivateStarted{OperationID: "operation-1"},
		protocol.PrivateStartReleased{OperationID: "operation-1"},
		protocol.PrivateSubmit{OperationID: "another-operation"},
		protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 7, CWD: "/tmp"},
		protocol.PrivateShellExit{Status: 7, CWD: "/tmp"},
		protocol.PrivateProtocolError{Code: "broken", Message: "peer failed"},
	} {
		t.Run(fmt.Sprintf("%T/%v", m, m), func(t *testing.T) {
			f := startFixtureFor(t, nil)
			done := f.start(t, context.Background(), startRequest())
			f.readPrivate(t)
			f.private(t, m)
			r := finishStart(t, done)
			var failure *StartError
			if !errors.As(r.err, &failure) || failure.Deadline.IsZero() || r.out.Started {
				t.Fatal("private ordering violation accepted", r)
			}
			fatal(t, f.r.w.channels)
		})
	}
}

func TestStartPrivateProtocolErrorSupersedesWatermarkTimeout(t *testing.T) {
	f := startFixtureFor(t, nil)
	req := startRequest()
	req.InputThrough = 1
	done := f.start(t, context.Background(), req)
	f.private(t, protocol.PrivateProtocolError{Code: "source-error", Message: "not a shell exit"})
	r := finishStart(t, done)
	if r.err == nil || !strings.Contains(r.err.Error(), "source-error") || r.out.Started {
		t.Fatal("fatal private report became an ordinary timeout", r)
	}
	fatal(t, f.r.w.channels)
}

func (f *startFixture) phase(t *testing.T, phase int) {
	t.Helper()
	f.readPrivate(t)
	if phase == 0 {
		return
	}
	f.private(t, protocol.PrivateSubmit{OperationID: "operation-1"})
	var b [2]byte
	_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(f.slave, b[:]); err != nil || b != [2]byte{0x18, 0x02} {
		t.Fatal("missing fixed trigger", b, err)
	}
	if phase == 1 {
		return
	}
	f.private(t, protocol.PrivateStartPrepared{OperationID: "operation-1"})
	if _, ok := f.readPrivate(t).(*protocol.PrivateStartRelease); !ok {
		t.Fatal("missing release request")
	}
	if phase == 2 {
		return
	}
	f.private(t, protocol.PrivateStarted{OperationID: "operation-1"})
	if _, ok := f.readPrivate(t).(*protocol.PrivateStartedAck); !ok {
		t.Fatal("missing started acknowledgement")
	}
}

func TestStartEveryPhaseRejectsCrossedPrivateFacts(t *testing.T) {
	for phase := range 4 {
		for name, m := range map[string]protocol.PrivateMessage{
			"wrong-id-submit":   protocol.PrivateSubmit{OperationID: "wrong"},
			"wrong-id-prepared": protocol.PrivateStartPrepared{OperationID: "wrong"},
			"wrong-id-started":  protocol.PrivateStarted{OperationID: "wrong"},
			"wrong-id-released": protocol.PrivateStartReleased{OperationID: "wrong"},
			"active-exit":       protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 7, CWD: "/tmp"},
			"empty-exit":        protocol.PrivateShellExit{Status: 7, CWD: "/tmp"},
			"private-error":     protocol.PrivateProtocolError{Code: "crossing", Message: "fatal fact"},
		} {
			t.Run(fmt.Sprintf("%d/%s", phase, name), func(t *testing.T) {
				f := startFixtureFor(t, nil)
				done := f.start(t, context.Background(), startRequest())
				f.phase(t, phase)
				f.private(t, m)
				r := finishStart(t, done)
				var failure *StartError
				if !errors.As(r.err, &failure) || r.out.Started || failure.Deadline.IsZero() {
					t.Fatal("crossing became a successful operation", r)
				}
				fatal(t, f.r.w.channels)
			})
		}
		for _, malformed := range []bool{false, true} {
			t.Run(fmt.Sprintf("%d/eof-partial-%t", phase, malformed), func(t *testing.T) {
				f := startFixtureFor(t, nil)
				done := f.start(t, context.Background(), startRequest())
				f.phase(t, phase)
				if malformed {
					_, _ = f.result.Write([]byte("awsh-v1\x00start_released\x00partial"))
				}
				_ = f.result.Close()
				if r := finishStart(t, done); r.err == nil || r.out.Started {
					t.Fatal("EOF became an operation result", r)
				}
				fatal(t, f.r.w.channels)
			})
		}
	}
}

func TestStartSourceRejectionRollsBackAndAllowsNextOperation(t *testing.T) {
	for _, code := range []string{"source-syntax", "source-policy"} {
		t.Run(code, func(t *testing.T) {
			setup := &testSplitSetup{entered: make(chan context.Context, 2), proceed: make(chan struct{})}
			close(setup.proceed)
			f := startFixtureFor(t, setup)
			for index := range 2 {
				req := splitRequest()
				req.OperationID = fmt.Sprintf("operation-%d", index+1)
				done := f.start(t, context.Background(), req)
				setupCtx := <-setup.entered
				original, _ := setupCtx.Deadline()
				m := f.readPrivate(t).(*protocol.PrivateExecute)
				root := "/run/omegaflow/session/split/" + req.OperationID
				if m.StdoutFIFO != root+".stdout" || m.StderrFIFO != root+".stderr" {
					t.Fatal("setup paths not passed to the existing frame", m)
				}
				f.private(t, protocol.PrivateRejected{OperationID: req.OperationID, Code: code, Message: "source rejected"})
				r := finishStart(t, done)
				if r.err != nil || r.out.Started || !setup.rolled || setup.end != original || f.r.w.channels.Err() != nil {
					t.Fatal("rejection did not preserve idle after original-epoch rollback", r)
				}
				for {
					event := nextRelayFrame(t, f.relayFixture)
					if _, ok := event.(*protocol.OutputMark); ok {
						continue
					}
					failed, ok := event.(*protocol.OperationFailed)
					if !ok || failed.OperationID != req.OperationID || failed.Code != code || failed.OutputStart != failed.OutputThrough || failed.Cwd != "/tmp" {
						t.Fatal("wrong empty-range source result", event)
					}
					break
				}
			}
		})
	}
}

func TestStartResultReaderContinuesIntoCompletion(t *testing.T) {
	f := startFixtureFor(t, nil)
	done := f.start(t, context.Background(), startRequest())
	f.throughStarted(t)
	first, _ := protocol.EncodePrivate(protocol.PrivateStartReleased{OperationID: "operation-1"}, protocol.AwshToEnvoy)
	next, _ := protocol.EncodePrivate(protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 123}, protocol.AwshToEnvoy)
	if err := helper.WriteAll(f.result, append(first, next...)); err != nil {
		t.Fatal(err)
	}
	if r := finishStart(t, done); r.err != nil || !r.out.Started {
		t.Fatal(r)
	}
	select {
	case r := <-f.c.private.events:
		m, ok := r.message.(*protocol.PrivateInputClose)
		if r.err != nil || !ok || m.OperationID != "operation-1" || m.CompletionHelperPID != 123 {
			t.Fatal("concatenated completion fact was lost", r)
		}
	case <-relayTestContext(t).Done():
		t.Fatal("private reader did not continue after release")
	}
}

func TestStartPublicWriteFailurePreventsPrivateAck(t *testing.T) {
	f := startFixtureFor(t, nil)
	done := f.start(t, context.Background(), startRequest())
	f.phase(t, 2)
	// Refuse the output direction while leaving the public reader live.
	_ = f.r.w.channels.telemetry.CloseWrite()
	f.private(t, protocol.PrivateStarted{OperationID: "operation-1"})
	if r := finishStart(t, done); r.err == nil || r.out.Started {
		t.Fatal("public write failure released Bash", r)
	}
	_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("started_ack followed a failed public frame", n, err)
	}
}

func TestStartEpochIncludesSetupAndAllProgress(t *testing.T) {
	setup := &testSplitSetup{entered: make(chan context.Context, 1), proceed: make(chan struct{})}
	f := startFixtureFor(t, setup)
	begin := time.Now()
	done := f.start(t, context.Background(), splitRequest())
	ctx := <-setup.entered
	original, ok := ctx.Deadline()
	if !ok || original.Before(begin.Add(4*time.Second)) || original.After(time.Now().Add(operationStartBudget)) {
		t.Fatal("setup is outside the original five-second start epoch", original, begin)
	}
	// Progress through every barrier, then withhold the last release. The
	// returned failure must retain setup's exact deadline, not start a new one.
	close(setup.proceed)
	// Intermediate progress happens late enough to expose a renewed result
	// timer. This is a protocol timeout test, not a product latency benchmark.
	<-time.After(time.Until(original.Add(-2 * time.Second)))
	f.phase(t, 3)
	r := finishStart(t, done)
	var failure *StartError
	if !errors.As(r.err, &failure) || failure.Code != "operation-start-timeout" || failure.Deadline != original || !errors.Is(r.err, context.DeadlineExceeded) || r.out.Started || !setup.rolled || setup.end != original || time.Now().After(original.Add(500*time.Millisecond)) {
		t.Fatal("progress or rollback replaced the original epoch", r, original, setup.end)
	}
	fatal(t, f.r.w.channels)
}

func TestStartIdleExitDuringSetupIsAnUnstartedShellFact(t *testing.T) {
	setup := &testSplitSetup{entered: make(chan context.Context, 1), proceed: make(chan struct{})}
	f := startFixtureFor(t, setup)
	done := f.start(t, context.Background(), splitRequest())
	<-setup.entered
	f.private(t, protocol.PrivateShellExit{Status: 7, CWD: "/tmp"})
	r := finishStart(t, done)
	if r.err != nil || r.out.Started || r.out.ShellExit == nil || r.out.ShellExit.Status != 7 || !setup.rolled || f.r.w.channels.Err() != nil {
		t.Fatal("idle shell fact became a fabricated operation", r)
	}
	select {
	case event := <-f.frames:
		t.Fatal("operation event emitted for idle shell exit", event)
	default:
	}
}

func TestStartShellExitHandoffRetainsOriginalEpoch(t *testing.T) {
	cases := []struct {
		name  string
		setup bool
		want  StartShellExitPhase
	}{
		{name: "input-barrier", want: StartShellExitInputBarrier},
		{name: "split-setup", setup: true, want: StartShellExitOperationStart},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var setup *testSplitSetup
			request := startRequest()
			if tc.setup {
				setup = &testSplitSetup{entered: make(chan context.Context, 1), proceed: make(chan struct{})}
				request = splitRequest()
			} else {
				request.InputThrough = 1
			}
			f := startFixtureFor(t, setup)
			ctx, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
			defer cancel()
			wantDeadline, _ := ctx.Deadline()
			done := f.start(t, ctx, request)
			if setup != nil {
				setupCtx := <-setup.entered
				if got, _ := setupCtx.Deadline(); got != wantDeadline {
					t.Fatal("setup changed the original deadline", got, wantDeadline)
				}
			} else {
				g := f.r.w.channels.inputState()
				wait := relayTestContext(t)
				for {
					g.mu.Lock()
					held, watermark, through, wake := g.held, g.watermark, g.through, g.wake
					g.mu.Unlock()
					if held && watermark == request.InputThrough && through < watermark {
						break
					}
					select {
					case <-wake:
					case <-wait.Done():
						t.Fatal("input barrier did not remain outstanding")
					}
				}
			}
			f.private(t, protocol.PrivateShellExit{Status: 7, CWD: "/tmp"})
			r := finishStart(t, done)
			if r.err != nil || r.out.Started || r.out.ShellExit == nil || r.out.ShellExit.Status != 7 {
				t.Fatal("shell exit was not handed off as a terminal fact", r)
			}
			if r.out.ShellExitDeadline != wantDeadline {
				t.Fatal("handoff changed the original absolute deadline", r.out.ShellExitDeadline, wantDeadline)
			}
			if r.out.ShellExitPhase != tc.want {
				t.Fatal("handoff lost the governing timeout phase", r.out.ShellExitPhase, tc.want)
			}
			if tc.setup && !setup.rolled {
				t.Fatal("split shell exit did not retain original-epoch rollback")
			}
			if !tc.setup {
				if got := f.r.w.channels.InputThrough(); got >= request.InputThrough {
					t.Fatal("input barrier was satisfied before shell exit", got, request.InputThrough)
				}
				_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
				var b [1]byte
				if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
					t.Fatal("private execute escaped outstanding input barrier", n, err)
				}
			}

			// The result writer remains open, so EOF/reap is deliberately withheld.
			retained, stop := context.WithDeadline(context.Background(), r.out.ShellExitDeadline)
			defer stop()
			select {
			case <-retained.Done():
			case <-time.After(time.Until(r.out.ShellExitDeadline) + time.Second):
				t.Fatal("consumer did not time out at the retained shell-exit bound")
			}
			if !errors.Is(retained.Err(), context.DeadlineExceeded) {
				t.Fatal("retained shell-exit bound ended with the wrong error", retained.Err())
			}
			select {
			case <-f.c.private.done:
				t.Fatal("withheld EOF unexpectedly closed the private reader")
			default:
			}
		})
	}
}

func TestStartShellExitRollbackAcceptsImmediateEOF(t *testing.T) {
	setup := &testSplitSetup{
		entered:         make(chan context.Context, 1),
		proceed:         make(chan struct{}),
		rollbackEntered: make(chan struct{}),
		rollbackRelease: make(chan struct{}),
	}
	f := startFixtureFor(t, setup)
	ctx, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
	defer cancel()
	wantDeadline, _ := ctx.Deadline()
	done := f.start(t, ctx, splitRequest())
	setupCtx := <-setup.entered
	if got, _ := setupCtx.Deadline(); got != wantDeadline {
		t.Fatal("setup changed the original deadline", got, wantDeadline)
	}
	f.private(t, protocol.PrivateShellExit{Status: 7, CWD: "/tmp"})
	if err := f.result.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-setup.rollbackEntered:
	case <-time.After(time.Second):
		t.Fatal("split rollback did not start")
	}
	select {
	case <-f.c.eofAccepted:
	case <-time.After(time.Second):
		close(setup.rollbackRelease)
		t.Fatal("normal terminal EOF was not consumed during rollback")
	}
	select {
	case r := <-done:
		close(setup.rollbackRelease)
		t.Fatalf("rollback returned before its cleanup gate was released: %+v", r)
	default:
	}
	close(setup.rollbackRelease)
	r := finishStart(t, done)
	if r.err != nil || r.out.Started || r.out.ShellExit == nil || r.out.ShellExit.Status != 7 || !r.out.ShellExitEOF {
		t.Fatal("normal terminal EOF was not retained in the shell-exit handoff", r)
	}
	if r.out.ShellExitDeadline != wantDeadline || r.out.ShellExitPhase != StartShellExitOperationStart {
		t.Fatal("shell-exit handoff changed its original epoch", r.out.ShellExitDeadline, r.out.ShellExitPhase, wantDeadline)
	}
	if !setup.rolled || setup.end != wantDeadline || f.r.w.channels.Err() != nil {
		t.Fatal("split rollback did not complete under the original epoch", setup.rolled, setup.end, wantDeadline, f.r.w.channels.Err())
	}
	select {
	case event := <-f.frames:
		t.Fatal("operation event emitted for idle shell exit", event)
	default:
	}
}

func TestStartPrivateReaderHasOneOwnerAndJoinsCancellation(t *testing.T) {
	f := startFixtureFor(t, nil)
	if _, err := NewCoordinator(context.Background(), f.r.w, f.r, nil); err == nil {
		t.Fatal("a second private reader was admitted")
	}
	if err := f.c.Close(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	select {
	case <-f.c.private.done:
	default:
		t.Fatal("private close left a live reader")
	}
	if _, err := f.c.Start(context.Background(), startRequest()); err == nil {
		t.Fatal("closed private reader reused")
	}
	if _, err := NewCoordinator(context.Background(), f.r.w, f.r, nil); err == nil {
		t.Fatal("private stream ownership restarted after close")
	}
}

func TestStartSessionLossJoinsSetupAndOriginalEpoch(t *testing.T) {
	setup := &testSplitSetup{entered: make(chan context.Context, 1), proceed: make(chan struct{})}
	f := startFixtureFor(t, setup)
	done := f.start(t, context.Background(), splitRequest())
	ctx := <-setup.entered
	original, _ := ctx.Deadline()
	if err := f.c.Close(relayTestContext(t)); err == nil {
		t.Fatal("close claimed joined while setup was active")
	}
	_ = f.terminal.Close()
	r := finishStart(t, done)
	var failure *StartError
	if !errors.As(r.err, &failure) || failure.Deadline != original || r.out.Started || !setup.rolled {
		t.Fatal("session loss lost start ownership or reset its epoch", r)
	}
	if err := f.c.Close(relayTestContext(t)); err != nil {
		t.Fatal("private reader not joinable after failed start", err)
	}
}

type attemptTestWriter struct {
	writes int
	err    error
}

func (w *attemptTestWriter) Write([]byte) (int, error)        { w.writes++; return 0, w.err }
func (w *attemptTestWriter) SetWriteDeadline(time.Time) error { return nil }

func TestStartFirstAttemptIncludesZeroByteFailure(t *testing.T) {
	for _, canceled := range []bool{false, true} {
		t.Run(fmt.Sprint(canceled), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if canceled {
				cancel()
			}
			op := &startingOperation{}
			peer := &attemptTestWriter{err: io.ErrClosedPipe}
			n, err := (executeWriter{peer, ctx, op}).Write([]byte("one private byte"))
			if n != 0 || err == nil || op.attempted == canceled || (peer.writes == 1) == canceled {
				t.Fatal("first attempted byte boundary is wrong", n, err, op.attempted, peer.writes)
			}
		})
	}
}

func TestStartUnstartedBoundaryDoesNotFreshDrainKernelBytes(t *testing.T) {
	f := relayFixtureFor(t)
	// Transfer the idle reader without admitting bytes, then arrange a kernel
	// prefix. The empty admitted prefix must remain distinct from fresh drain.
	f.r.mu.Lock()
	p := f.r.reader
	if err := p.join(f.r.w.PTY); err != nil {
		f.r.mu.Unlock()
		t.Fatal(err)
	}
	data, err := p.take()
	if len(data) != 0 || err != nil {
		f.r.mu.Unlock()
		t.Fatal("unexpected admitted prefix", data, err)
	}
	f.r.reader = nil
	f.r.mu.Unlock()
	if _, err := f.slave.Write([]byte("kernel-only")); err != nil {
		t.Fatal(err)
	}
	var boundary int64 = -1
	if _, err := f.r.boundary(relayTestContext(t), false, func(offset int64) error { boundary = offset; return nil }); err != nil {
		t.Fatal(err)
	}
	if boundary != 0 {
		t.Fatal("pre-start terminal result consumed a fresh kernel prefix", boundary)
	}
	if m, ok := nextRelayFrame(t, f).(*protocol.OutputMark); !ok || m.Offset != 0 {
		t.Fatal("missing empty boundary mark", m)
	}
	got := make([]byte, len("kernel-only"))
	_ = f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(f.terminal, got); err != nil || string(got) != "kernel-only" {
		t.Fatal("normal relay lost deferred kernel bytes", string(got), err)
	}
}

func TestStartFastResultsRemainOrderedBeforeWriterReturns(t *testing.T) {
	f := startFixtureFor(t, nil)
	f.c.active = &startingOperation{request: startRequest(), attempted: true}
	f.c.phase = awaitReleased
	gate := make(chan struct{})
	entered := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- f.c.work(relayTestContext(t), false, func(ctx context.Context) error {
			close(entered)
			select {
			case <-gate:
				return nil
			case <-ctx.Done():
				return ctx.Err()
			}
		})
	}()
	<-entered
	first, _ := protocol.EncodePrivate(protocol.PrivateStartReleased{OperationID: "operation-1"}, protocol.AwshToEnvoy)
	next, _ := protocol.EncodePrivate(protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 123}, protocol.AwshToEnvoy)
	if err := helper.WriteAll(f.result, append(first, next...)); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		t.Fatal("a fast following result aborted the still-running write", err)
	case <-time.After(40 * time.Millisecond):
	}
	close(gate)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	m, err := f.c.result(relayTestContext(t))
	if _, ok := m.(*protocol.PrivateStartReleased); !ok || err != nil {
		t.Fatal("first private result lost", m, err)
	}
	select {
	case next := <-f.c.private.events:
		if _, ok := next.message.(*protocol.PrivateInputClose); !ok || next.err != nil {
			t.Fatal("following private result lost", next)
		}
	case <-relayTestContext(t).Done():
		t.Fatal("following private result was not retained for completion")
	}
}

func TestStartPartialPrivateWriteQueuesCancelAndRejectionWins(t *testing.T) {
	f := startFixtureFor(t, nil)
	req := startRequest()
	req.Source = strings.Repeat(":\n", 65536)
	done := f.start(t, context.Background(), req)
	_ = f.control.SetReadDeadline(time.Now().Add(time.Second))
	var first [1]byte
	if _, err := io.ReadFull(f.control, first[:]); err != nil {
		t.Fatal(err)
	}
	// The oversized frame cannot fit in the pipe. Positive partial progress
	// proves the attempt while the one writer remains blocked behind us.
	f.public(t, &protocol.Cancel{OperationID: req.OperationID, Reason: "user"})
	g := f.r.w.channels.inputState()
	g.mu.Lock()
	if !g.held {
		g.mu.Unlock()
		t.Fatal("private execute ran without held input")
	}
	g.mu.Unlock()
	op := f.c.active
	ctx := relayTestContext(t)
	for {
		op.attemptMu.Lock()
		accepted := op.cancel != nil
		op.attemptMu.Unlock()
		if accepted {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("cancel was not accepted during the blocked private write")
		}
		runtime.Gosched()
	}
	f.reader = bufio.NewReader(io.MultiReader(bytes.NewReader(first[:]), f.control))
	m := f.readPrivate(t).(*protocol.PrivateExecute)
	if m.Source != req.Source {
		t.Fatal("partial frame did not complete exactly")
	}
	f.private(t, protocol.PrivateRejected{OperationID: req.OperationID, Code: "source-policy", Message: "deterministic policy rejection"})
	r := finishStart(t, done)
	if r.err != nil || r.out.Started || f.r.w.channels.Err() != nil {
		t.Fatal("queued cancellation broke recoverable rejection", r)
	}
	for {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationFailed:
			if m.Code != "source-policy" || m.OutputStart != m.OutputThrough {
				t.Fatal(m)
			}
			return
		default:
			t.Fatal("post-attempt cancellation replaced the rejection result", m)
		}
	}
}

type failingStartSetup struct {
	prepareErr, rollbackErr error
	badPaths                bool
	rolled                  int
}

func (s *failingStartSetup) Prepare(_ context.Context, request protocol.Execute) (string, string, error) {
	if s.badPaths {
		return "/tmp/wrong.stdout", "/tmp/wrong.stderr", nil
	}
	root := "/run/omegaflow/session/split/" + request.OperationID
	return root + ".stdout", root + ".stderr", s.prepareErr
}

func (s *failingStartSetup) Rollback(context.Context, string) error {
	s.rolled++
	return s.rollbackErr
}

func TestStartSetupAndEncodingFailureRetainRollback(t *testing.T) {
	for _, badPaths := range []bool{false, true} {
		t.Run(fmt.Sprint(badPaths), func(t *testing.T) {
			setup := &failingStartSetup{badPaths: badPaths}
			if !badPaths {
				setup.prepareErr = errors.New("injected setup failure")
			}
			f := startFixtureFor(t, setup)
			r := finishStart(t, f.start(t, context.Background(), splitRequest()))
			var failure *StartError
			if !errors.As(r.err, &failure) || failure.Deadline.IsZero() || r.out.Started || setup.rolled != 1 {
				t.Fatal("setup/encoding failure lost rollback or its original epoch", r, setup.rolled)
			}
			_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
			var b [1]byte
			if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
				t.Fatal("execute escaped failed setup/encoding", n, err)
			}
			fatal(t, f.r.w.channels)
		})
	}
}

func TestStartRollbackFailureCannotPublishRecoverableRejection(t *testing.T) {
	setup := &failingStartSetup{rollbackErr: errors.New("injected rollback failure")}
	f := startFixtureFor(t, setup)
	done := f.start(t, context.Background(), splitRequest())
	f.readPrivate(t)
	f.private(t, protocol.PrivateRejected{OperationID: "operation-1", Code: "source-policy", Message: "rejected"})
	r := finishStart(t, done)
	if r.err == nil || !strings.Contains(r.err.Error(), "injected rollback failure") || r.out.Started || setup.rolled != 1 {
		t.Fatal("rollback failure became ordinary rejection", r, setup.rolled)
	}
	fatal(t, f.r.w.channels)
	for {
		select {
		case m, ok := <-f.frames:
			if !ok {
				return
			}
			if _, ok := m.(*protocol.Diagnostic); !ok {
				t.Fatal("ordinary event followed incomplete rollback", m)
			}
		case <-relayTestContext(t).Done():
			t.Fatal("fatal telemetry did not close")
		}
	}
}

func TestStartPartialPrivateWriteFailureIsFatal(t *testing.T) {
	f := startFixtureFor(t, nil)
	req := startRequest()
	req.Source = strings.Repeat(":\n", 65536)
	done := f.start(t, context.Background(), req)
	_ = f.control.SetReadDeadline(time.Now().Add(time.Second))
	var first [1]byte
	if _, err := io.ReadFull(f.control, first[:]); err != nil {
		t.Fatal(err)
	}
	_ = f.control.Close()
	r := finishStart(t, done)
	var failure *StartError
	if !errors.As(r.err, &failure) || failure.Deadline.IsZero() || r.out.Started {
		t.Fatal("partial private write failure was accepted", r)
	}
	fatal(t, f.r.w.channels)
}

func TestStartPrivateCloseTimeoutDoesNotClaimJoinedReader(t *testing.T) {
	f := startFixtureFor(t, nil)
	ctx, cancel := context.WithCancel(context.Background())
	ctx, expire := context.WithDeadline(ctx, time.Now().Add(-time.Second))
	defer expire()
	p := &privateResults{w: f.r.w, ctx: ctx, cancel: cancel, done: make(chan struct{}), stop: func() {}}
	if err := p.close(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("unjoined reader claimed complete", err)
	}
	close(p.done)
	if err := p.close(relayTestContext(t)); err != nil {
		t.Fatal("later joined reader could not finish close", err)
	}
}
