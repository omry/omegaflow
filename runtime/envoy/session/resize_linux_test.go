//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func resizeFrame(t *testing.T, f relayFixture) *protocol.ResizeApplied {
	t.Helper()
	var mark int64 = -1
	var elapsed int64
	for {
		switch m := nextRelayFrame(t, f).(type) {
		case *protocol.OutputMark:
			if m.Offset < mark || m.ElapsedUS < elapsed || m.Stream != "pty" {
				t.Fatal("resize covering mark regressed", m)
			}
			mark, elapsed = m.Offset, m.ElapsedUS
		case *protocol.ResizeApplied:
			if mark != m.OutputThrough || m.ElapsedUS < elapsed {
				t.Fatal("resize preceded its exact covering mark", mark, m)
			}
			return m
		default:
			t.Fatal("unexpected resize event", m)
		}
	}
}

func windowSize(t *testing.T, file *os.File) (columns, rows int64) {
	t.Helper()
	var size [4]uint16
	if err := ptyIOCTL(file, syscall.TIOCGWINSZ, unsafe.Pointer(&size)); err != nil {
		t.Fatal(err)
	}
	return int64(size[1]), int64(size[0])
}

func publicResize(t *testing.T, f *startFixture, columns, rows int64) {
	t.Helper()
	m := protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: f.seq}, Columns: columns, Rows: rows}
	f.seq++
	frame, err := protocol.EncodeController(m)
	if err != nil {
		t.Fatal(err)
	}
	f.telemetry.SetWriteDeadline(time.Now().Add(time.Second))
	if err := helper.WriteAll(f.telemetry, frame); err != nil {
		t.Fatal(err)
	}
}

func TestResizeExactPrefixAndActualDimensions(t *testing.T) {
	f := relayFixtureFor(t)
	prefix := []byte{'a', 0, 255, '\n', '\r', 'z'}
	if _, err := f.slave.Write(prefix); err != nil {
		t.Fatal(err)
	}
	if _, err := f.r.Drain(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	for _, dimensions := range [][2]int64{{93, 31}, {94, 32}, {1000, 1}} {
		applied, err := f.r.Resize(relayTestContext(t), dimensions[0], dimensions[1])
		if err != nil || !applied {
			t.Fatal(applied, err)
		}
		m := resizeFrame(t, f)
		if m.Columns != dimensions[0] || m.Rows != dimensions[1] || m.OutputThrough != int64(len(prefix)) {
			t.Fatal("wrong exact resize frontier or dimensions", m)
		}
		if columns, rows := windowSize(t, f.slave); columns != m.Columns || rows != m.Rows {
			t.Fatal("acknowledged dimensions differ from actual PTY", columns, rows, m)
		}
	}
	got := make([]byte, len(prefix))
	f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(f.terminal, got); err != nil || !bytes.Equal(got, prefix) {
		t.Fatal("resize changed raw terminal bytes", got, err)
	}
}

func TestResizeContinuousOutputDoesNotWaitForProducerExit(t *testing.T) {
	f := relayFixtureFor(t)
	stop := make(chan struct{})
	var workers sync.WaitGroup
	workers.Add(2)
	go func() {
		defer workers.Done()
		_, _ = io.Copy(io.Discard, f.terminal)
	}()
	writing := make(chan struct{})
	go func() {
		defer workers.Done()
		data := bytes.Repeat([]byte("o"), 32768)
		close(writing)
		for {
			select {
			case <-stop:
				return
			default:
			}
			if _, err := f.slave.Write(data); err != nil {
				return
			}
		}
	}()
	t.Cleanup(func() { close(stop); f.slave.Close(); f.terminal.Close(); workers.Wait() })
	<-writing
	if applied, err := f.r.Resize(relayTestContext(t), 101, 43); err != nil || !applied {
		t.Fatal("continuous producer starved resize", applied, err)
	}
	m := resizeFrame(t, f)
	if m.Columns != 101 || m.Rows != 43 {
		t.Fatal(m)
	}
	select {
	case <-stop:
		t.Fatal("resize required stopping the producer")
	default:
	}
}

func TestResizeAdmissionAndDrainSupersession(t *testing.T) {
	f := relayFixtureFor(t)
	columns, rows := windowSize(t, f.slave)
	for _, size := range [][2]int64{{0, 1}, {1, 0}, {-1, 24}, {1001, 1}, {1, 1001}} {
		if applied, err := f.r.Resize(relayTestContext(t), size[0], size[1]); err == nil || applied {
			t.Fatal("invalid dimensions admitted", size, applied, err)
		}
	}
	if c, r := windowSize(t, f.slave); c != columns || r != rows {
		t.Fatal("rejected resize changed PTY")
	}
	ctx, cancel := context.WithCancel(relayTestContext(t))
	cancel()
	if applied, err := f.r.Resize(ctx, 90, 30); applied || !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled caller applied resize", applied, err)
	}
	if err := f.r.Close(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	if applied, err := f.r.Resize(relayTestContext(t), 90, 30); applied || err != nil {
		t.Fatal("drain did not supersede unapplied resize", applied, err)
	}
	for {
		select {
		case m := <-f.frames:
			if _, ok := m.(*protocol.ResizeApplied); ok {
				t.Fatal("drain-superseded resize was acknowledged", m)
			}
		default:
			return
		}
	}
}

func TestResizeDuringStartingInputBarrier(t *testing.T) {
	for _, outcome := range []string{"started", "rejected", "cancelled"} {
		t.Run(outcome, func(t *testing.T) {
			f := startFixtureFor(t, nil)
			request := startRequest()
			request.InputThrough = 3
			done := f.start(t, context.Background(), request)
			publicResize(t, f, 119, 41)
			m := resizeFrame(t, f.relayFixture)
			if m.Columns != 119 || m.Rows != 41 || m.OutputThrough != 0 {
				t.Fatal("Starting resize lost its exact idle prefix", m)
			}
			if outcome == "cancelled" {
				f.public(t, &protocol.Cancel{OperationID: request.OperationID, Reason: "stop"})
				if r := finishStart(t, done); r.err != nil || r.out.Started {
					t.Fatal(r)
				}
				return
			}
			if _, err := f.terminal.Write([]byte("abc")); err != nil {
				t.Fatal(err)
			}
			if outcome == "rejected" {
				if _, ok := f.readPrivate(t).(*protocol.PrivateExecute); !ok {
					t.Fatal("missing private execute")
				}
				f.private(t, protocol.PrivateRejected{OperationID: request.OperationID, Code: "source-syntax", Message: "invalid syntax"})
				if r := finishStart(t, done); r.err != nil || r.out.Started {
					t.Fatal(r)
				}
				return
			}
			f.throughStarted(t)
			f.private(t, protocol.PrivateStartReleased{OperationID: request.OperationID})
			if r := finishStart(t, done); r.err != nil || !r.out.Started {
				t.Fatal(r)
			}
		})
	}
}

func TestResizeDuringOrdinaryOperation(t *testing.T) {
	f := completionFixture(t)
	done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error { return nil })
	publicResize(t, f, 113, 39)
	m := resizeFrame(t, f.relayFixture)
	if m.Columns != 113 || m.Rows != 39 || m.OutputThrough < int64(len("before-start\n")) {
		t.Fatal("active resize lost the operation prefix", m)
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 0, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if result := completedEvent(t, f); result.OutputThrough < m.OutputThrough {
		t.Fatal("completion regressed accepted resize frontier", result, m)
	}
}

// Replace only after real PTY EOF and the reader join. The ioctl then executes
// against an actual non-terminal descriptor and must return ENOTTY.
func invalidResizeMaster(t *testing.T, f relayFixture) {
	t.Helper()
	if err := f.slave.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-f.r.ReadEnded():
	case <-relayTestContext(t).Done():
		t.Fatal("PTY EOF was not observed")
	}
	file, err := os.OpenFile("/dev/null", os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { file.Close() })
	f.r.mu.Lock()
	defer f.r.mu.Unlock()
	if f.r.reader != nil || !f.r.ended {
		t.Fatal("replacing a live PTY reader")
	}
	f.r.w.PTY = file
}

func assertResizeFatal(t *testing.T, f relayFixture, err error) {
	t.Helper()
	var fatal *StartError
	if !errors.As(err, &fatal) || fatal.Code != "resize-failed" || !errors.Is(err, syscall.ENOTTY) {
		t.Fatal("real failed ioctl lost its fatal diagnostic cause", err)
	}
	select {
	case <-f.r.w.channels.Done():
	default:
		t.Fatal("failed ioctl left the session open")
	}
	diagnostic := false
	for {
		select {
		case m, ok := <-f.frames:
			if !ok {
				if !diagnostic {
					t.Fatal("failed ioctl omitted its fatal diagnostic")
				}
				return
			}
			switch v := m.(type) {
			case *protocol.OutputMark:
			case *protocol.Diagnostic:
				if diagnostic || v.Code != "resize-failed" || v.Severity != "fatal" {
					t.Fatal("wrong or duplicate resize diagnostic", v)
				}
				diagnostic = true
			default:
				t.Fatal("failed ioctl published a successful event", m)
			}
		case <-relayTestContext(t).Done():
			t.Fatal("failed ioctl did not close telemetry")
		}
	}
}

func TestResizeRealIoctlFailureIdleAndActive(t *testing.T) {
	t.Run("idle", func(t *testing.T) {
		f := relayFixtureFor(t)
		invalidResizeMaster(t, f)
		applied, err := f.r.Resize(relayTestContext(t), 100, 40)
		if applied {
			t.Fatal("failed ioctl acknowledged dimensions")
		}
		assertResizeFatal(t, f, err)
	})
	t.Run("active", func(t *testing.T) {
		f := completionFixture(t)
		invalidResizeMaster(t, f.relayFixture)
		done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error {
			t.Error("failed resize entered ordinary cleanup")
			return nil
		})
		publicResize(t, f, 100, 40)
		assertResizeFatal(t, f.relayFixture, finishCompletion(t, done))
		if f.c.active == nil {
			t.Fatal("failed resize made the operation reusable")
		}
	})
}

func TestResizeStartingTimeoutKeepsEmptyRangeAndCurrentFrontier(t *testing.T) {
	f := startFixtureFor(t, nil)
	request := startRequest()
	request.InputThrough = 1
	ctx, end := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer end()
	done := f.start(t, ctx, request)
	if _, err := f.slave.Write([]byte("idle-prefix")); err != nil {
		t.Fatal(err)
	}
	publicResize(t, f, 106, 38)
	ack := resizeFrame(t, f.relayFixture)
	if result := finishStart(t, done); result.err != nil || result.out.Started {
		t.Fatal("resize changed the recoverable input barrier timeout", result)
	}
	for {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationFailed:
			if m.Code != "input-barrier-timeout" || m.OutputStart != m.OutputThrough || m.OutputThrough < ack.OutputThrough {
				t.Fatal("timeout range regressed the Starting resize frontier", ack, m)
			}
			return
		default:
			t.Fatal("unexpected Starting timeout result", m)
		}
	}
}

func TestResizeDoesNotConsumePendingPrivateReturn(t *testing.T) {
	f := completionFixture(t)
	f.c.phase = awaitInputClose
	f.c.pending = &privateResult{message: &protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77}}
	f.c.resize = &protocol.Resize{Columns: 92, Rows: 29}
	m, err := f.c.result(relayTestContext(t))
	if err != nil {
		t.Fatal("resize displaced the retained next-phase fact", err)
	}
	if proposal, ok := m.(*protocol.PrivateInputClose); !ok || proposal.CompletionHelperPID != 77 {
		t.Fatal("pending return was changed", m)
	}
	if ack := resizeFrame(t, f.relayFixture); ack.Columns != 92 || ack.Rows != 29 {
		t.Fatal(ack)
	}
}

func TestResizePrivateFailureInterruptsOutputOwnerWait(t *testing.T) {
	f := completionFixture(t)
	f.r.mu.Lock()
	locked := true
	defer func() {
		if locked {
			f.r.mu.Unlock()
		}
	}()
	done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error {
		t.Error("private failure entered cleanup")
		return nil
	})
	publicResize(t, f, 111, 42)
	wait := relayTestContext(t)
	var phase context.Context
	for phase == nil {
		f.r.phaseMu.Lock()
		phase = f.r.phase
		f.r.phaseMu.Unlock()
		if wait.Err() != nil {
			t.Fatal("accepted resize never waited for the actual output owner")
		}
		runtime.Gosched()
	}
	f.private(t, protocol.PrivateProtocolError{Code: "resize-private-fatal", Message: "retain this original cause"})
	select {
	case <-phase.Done():
	case <-wait.Done():
		t.Fatal("resize worker hid the private failure")
	}
	f.r.mu.Unlock()
	locked = false
	if err := finishCompletion(t, done); err == nil || !strings.Contains(err.Error(), "resize-private-fatal") {
		t.Fatal("resize lost the original private failure", err)
	}
	for m := range f.frames {
		if _, ok := m.(*protocol.ResizeApplied); ok {
			t.Fatal("failed, unapplied resize was acknowledged", m)
		}
	}
}

func TestResizeInFlightPublicationDoesNotDeadlock(t *testing.T) {
	f := completionFixture(t)
	f.c.phase = awaitInputClose
	frame, err := protocol.EncodePrivate(protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77}, protocol.AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	half := len(frame) / 2
	if _, err := f.result.Write(frame[:half]); err != nil {
		t.Fatal(err)
	}
	if ready, err := f.c.private.publicationReady(); err != nil || ready {
		t.Fatal("actual partial private frame was not retained", ready, err)
	}
	ctx := relayTestContext(t)
	entered := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- f.c.work(ctx, false, func(job context.Context) error {
			_, err := f.r.boundary(job, false, func(int64) error {
				close(entered)
				return f.r.w.channels.sendEvent(job, "diagnostic", func(h protocol.Header) protocol.Message {
					return protocol.Diagnostic{Header: h, Severity: "info", Code: "publication-peer", Message: "one existing boundary"}
				}, func() error { return f.c.selectPublication(job) })
			})
			return err
		})
	}()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("existing worker did not own output and telemetry publication")
	}
	publicResize(t, f, 104, 37)
	if _, err := f.result.Write(frame[half:]); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal("resize deadlocked the existing boundary or lost private return", err)
		}
	case <-ctx.Done():
		t.Fatal("resize and existing publication retained each other's owners")
	}
	// Transport receipt and owner acceptance are distinct. If work completion
	// won before request acceptance, keep the same fact owner live through the
	// next bounded phase rather than guessing that the queue must still be full.
	release := make(chan struct{})
	next := make(chan error, 1)
	go func() {
		next <- f.c.work(ctx, false, func(job context.Context) error {
			select {
			case <-release:
				return nil
			case <-job.Done():
				return job.Err()
			}
		})
	}()
	seen := false
	for !seen {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.Diagnostic:
			if m.Code != "publication-peer" {
				t.Fatal(m)
			}
			seen = true
		default:
			t.Fatal("resize displaced already-selected publication", m)
		}
	}
	resizeFrame(t, f.relayFixture)
	close(release)
	select {
	case err := <-next:
		if err != nil {
			t.Fatal("resize did not join the following phase", err)
		}
	case <-ctx.Done():
		t.Fatal("resize retained the next phase")
	}
	if f.c.pending == nil {
		t.Fatal("resize consumed the pending original completion fact")
	}
}

func TestResizeAcrossPrivateStartPhases(t *testing.T) {
	for _, phase := range []string{"submit", "prepared", "started", "released"} {
		t.Run(phase, func(t *testing.T) {
			f := startFixtureFor(t, nil)
			done := f.start(t, context.Background(), startRequest())
			resize := func(at string) {
				if at != phase {
					return
				}
				publicResize(t, f, 123, 45)
				m := resizeFrame(t, f.relayFixture)
				if m.Columns != 123 || m.Rows != 45 {
					t.Fatal("private start phase changed dimensions", at, m)
				}
			}
			if _, ok := f.readPrivate(t).(*protocol.PrivateExecute); !ok {
				t.Fatal("missing private execute")
			}
			resize("submit")
			f.private(t, protocol.PrivateSubmit{OperationID: "operation-1"})
			var trigger [2]byte
			f.slave.SetReadDeadline(time.Now().Add(time.Second))
			if _, err := io.ReadFull(f.slave, trigger[:]); err != nil || trigger != [2]byte{0x18, 0x02} {
				t.Fatal("wrong actual adapter trigger", trigger, err)
			}
			resize("prepared")
			if _, err := f.slave.Write([]byte("phase-output")); err != nil {
				t.Fatal(err)
			}
			f.private(t, protocol.PrivateStartPrepared{OperationID: "operation-1"})
			if _, ok := f.readPrivate(t).(*protocol.PrivateStartRelease); !ok {
				t.Fatal("missing private start_release")
			}
			resize("started")
			f.private(t, protocol.PrivateStarted{OperationID: "operation-1"})
			var start *protocol.OperationStarted
			for start == nil {
				switch m := nextRelayFrame(t, f.relayFixture).(type) {
				case *protocol.OutputMark:
				case *protocol.OperationStarted:
					start = m
				default:
					t.Fatal("wrong public start event", m)
				}
			}
			if _, ok := f.readPrivate(t).(*protocol.PrivateStartedAck); !ok {
				t.Fatal("missing private started_ack")
			}
			resize("released")
			f.private(t, protocol.PrivateStartReleased{OperationID: "operation-1"})
			if result := finishStart(t, done); result.err != nil || !result.out.Started || result.out.OutputStart != int64(len("phase-output")) {
				t.Fatal("resize corrupted original start transaction", result)
			}
		})
	}
}

func TestResizeDrainJoinsAdmittedUnappliedWork(t *testing.T) {
	f := relayFixtureFor(t)
	f.r.mu.Lock()
	reader := f.r.reader
	reader.mu.Lock()
	locked := true
	defer func() {
		if locked {
			reader.mu.Unlock()
		}
	}()
	ctx := relayTestContext(t)
	type result struct {
		applied bool
		err     error
	}
	done := make(chan result, 1)
	go func() { applied, err := f.r.Resize(ctx, 109, 36); done <- result{applied, err} }()
	for {
		f.r.phaseMu.Lock()
		phase := f.r.phase
		f.r.phaseMu.Unlock()
		if phase != nil {
			break
		}
		if ctx.Err() != nil {
			f.r.mu.Unlock()
			t.Fatal("resize did not acquire its boundary")
		}
		runtime.Gosched()
	}
	f.r.mu.Unlock()
	closed := make(chan error, 1)
	go func() { closed <- f.r.Close(ctx) }()
	select {
	case <-f.r.closing:
	case <-ctx.Done():
		t.Fatal("existing drain did not select closure")
	}
	reader.mu.Unlock()
	locked = false
	select {
	case result := <-done:
		if result.applied || result.err != nil {
			t.Fatal("unapplied resize did not yield to drain", result)
		}
	case <-ctx.Done():
		t.Fatal("drain retained admitted resize work")
	}
	select {
	case err := <-closed:
		if err != nil {
			t.Fatal("drain damaged healthy channels", err)
		}
	case <-ctx.Done():
		t.Fatal("drain failed to join resize's original reader")
	}
	if columns, rows := windowSize(t, f.slave); columns == 109 && rows == 36 {
		t.Fatal("superseded resize changed actual dimensions")
	}
}

func TestResizeInterruptedBeforeBoundaryRetainsAcceptedRequest(t *testing.T) {
	f := completionFixture(t)
	f.c.resize = &protocol.Resize{Columns: 129, Rows: 47}
	<-f.r.barrier
	held := true
	defer func() {
		if held {
			f.r.barrier <- struct{}{}
		}
	}()
	ctx, end := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer end()
	err := f.c.applyPendingResize(ctx)
	if !errors.Is(err, context.DeadlineExceeded) || f.r.w.channels.Err() != nil {
		t.Fatal("interrupted, unattempted boundary should retain healthy phase handoff", err, f.r.w.channels.Err())
	}
	if f.c.resize == nil {
		t.Fatal("accepted resize was lost before any output or ioctl began")
	}
	f.r.barrier <- struct{}{}
	held = false
	if err := f.c.applyPendingResize(relayTestContext(t)); err != nil {
		t.Fatal("retained resize could not continue in the original next phase", err)
	}
	if ack := resizeFrame(t, f.relayFixture); ack.Columns != 129 || ack.Rows != 47 {
		t.Fatal("retained resize changed dimensions", ack)
	}
}

func TestCompletedPhaseRetainsUnattemptedResizeWithoutSpendingDeadline(t *testing.T) {
	f := completionFixture(t)
	request := &protocol.Resize{Columns: 129, Rows: 47}
	f.c.resize = request
	<-f.r.barrier
	defer func() { f.r.barrier <- struct{}{} }()
	done := make(chan struct{})
	close(done)
	ctx, end := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer end()
	if _, err := f.c.wait(ctx, false, nil, done, false); err != nil {
		t.Fatal("completed phase lost its successful result to pending resize", err)
	}
	if f.c.resize != request || f.r.w.channels.Err() != nil {
		t.Fatal("unattempted resize was not retained on healthy channels")
	}
}

func TestCompletedPhaseStillObservesReadyPrivateFatalBeforeResize(t *testing.T) {
	f := completionFixture(t)
	request := &protocol.Resize{Columns: 129, Rows: 47}
	f.c.resize = request
	f.c.private.fatal <- privateResult{message: &protocol.PrivateProtocolError{
		Code: "completed-phase-fatal", Message: "ready before phase selection",
	}}
	done := make(chan struct{})
	close(done)
	if _, err := f.c.wait(relayTestContext(t), false, nil, done, false); err == nil ||
		!strings.Contains(err.Error(), "completed-phase-fatal") {
		t.Fatal("work completion displaced a ready private fatal", err)
	}
	if f.c.resize != request {
		t.Fatal("fatal selection attempted the pending resize")
	}
}

func TestResizeFailurePreservesPendingReturnRejection(t *testing.T) {
	f := completionFixture(t)
	invalidResizeMaster(t, f.relayFixture)
	f.c.phase = awaitInputClose
	f.c.pending = &privateResult{message: &protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77}}
	f.c.resize = &protocol.Resize{Columns: 105, Rows: 35}
	m, err := f.c.result(relayTestContext(t))
	if m != nil {
		t.Fatal("failed resize released a pending successful private return", m)
	}
	assertResizeFatal(t, f.relayFixture, err)
}

func TestResizeRejectsTwoAcceptedOutstandingRequests(t *testing.T) {
	f := completionFixture(t)
	f.c.phase = awaitInputClose
	frame, err := protocol.EncodePrivate(protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77}, protocol.AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.result.Write(frame[:len(frame)/2]); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(relayTestContext(t))
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := f.c.wait(ctx, false, nil, nil, false); done <- err }()
	// The existing publication owner holds selection until the actual partial
	// private frame completes. This leaves accepted resizes pending, rather
	// than mistaking TCP arrival for already committed acknowledgement.
	held, joined := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(joined)
		reply := make(chan error, 1)
		select {
		case f.c.publication <- reply:
			close(held)
		case <-ctx.Done():
			return
		}
		select {
		case <-reply:
		case <-ctx.Done():
		}
	}()
	select {
	case <-held:
	case <-ctx.Done():
		t.Fatal("existing owner did not retain the partial-frame publication")
	}
	publicResize(t, f, 125, 49)
	publicResize(t, f, 126, 50)
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "multiple outstanding resizes") {
			t.Fatal("second accepted resize replaced the first", err)
		}
	case <-ctx.Done():
		t.Fatal("two outstanding resizes did not fail admission")
	}
	cancel()
	<-joined
	if columns, rows := windowSize(t, f.slave); columns == 125 || columns == 126 || rows == 49 || rows == 50 {
		t.Fatal("rejected outstanding requests changed the terminal", columns, rows)
	}
}

func TestSelectedPublicationJoinsBeforePendingResize(t *testing.T) {
	f := completionFixture(t)
	<-f.r.barrier
	defer func() { f.r.barrier <- struct{}{} }()
	ctx, end := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer end()
	workDone := make(chan struct{})
	result := make(chan error, 1)
	go func() { _, err := f.c.wait(ctx, false, nil, workDone, false); result <- err }()
	if err := f.c.selectPublication(ctx); err != nil {
		t.Fatal(err)
	}
	request := &protocol.Resize{Columns: 129, Rows: 47}
	f.r.w.channels.controls <- Event{Message: request}
	for len(f.r.w.channels.controls) != 0 {
		if ctx.Err() != nil {
			t.Fatal("resize was not accepted", ctx.Err())
		}
		runtime.Gosched()
	}
	// A second publication handshake proves that the accepted resize has
	// reached an owner iteration. Its boundary remains held throughout.
	if err := f.c.selectPublication(ctx); err != nil {
		t.Fatal(err)
	}
	close(workDone)
	select {
	case err := <-result:
		if err != nil {
			t.Fatal("selected phase result was replaced by pending resize", err)
		}
	case <-ctx.Done():
		t.Fatal("selected publication failed to join before resize", ctx.Err())
	}
	if f.c.resize != request || f.r.w.channels.Err() != nil {
		t.Fatal("unattempted resize was not retained")
	}
}
