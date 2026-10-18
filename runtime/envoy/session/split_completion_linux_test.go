//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func splitCompletionFixture(t *testing.T) (*startFixture, *splitTestOwner, [2]*os.File) {
	t.Helper()
	owner := splitOwnerFor(t)
	f := startFixtureFor(t, owner)
	done := f.start(t, context.Background(), splitRequest())
	f.throughStarted(t)
	f.private(t, protocol.PrivateStartReleased{OperationID: "operation-1"})
	if result := finishStart(t, done); result.err != nil || !result.out.Started {
		t.Fatal(result)
	}
	if !f.r.ownsSplit("operation-1") {
		t.Fatal("production start did not attach real FIFO readers")
	}
	if got := splitRead(t, f.relayFixture, len("before-start\n")); string(got) != "before-start\n" {
		t.Fatal(got)
	}
	var writers [2]*os.File
	for i, stream := range splitStreams {
		file, err := os.OpenFile(filepath.Join(owner.root, "split", "operation-1."+stream), os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		writers[i] = file
		t.Cleanup(func() { _ = file.Close() })
	}
	return f, owner, writers
}

func splitCompletedBytes(t *testing.T, f *startFixture, raw []byte, start int64) *protocol.OperationCompleted {
	t.Helper()
	offset := start
	stream := "pty"
	got := map[string][]byte{}
	for {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
			if m.Offset < offset || m.Offset > start+int64(len(raw)) {
				t.Fatal("split completion frontier", m)
			}
			got[stream] = append(got[stream], raw[offset-start:m.Offset-start]...)
			offset, stream = m.Offset, m.Stream
		case *protocol.OperationCompleted:
			if m.OutputStart != start || m.OutputThrough != start+int64(len(raw)) || offset != m.OutputThrough {
				t.Fatal("result before covering marks", m, offset)
			}
			if !bytes.Equal(got["stdout"], []byte("out\x00\xff\r\né")) || !bytes.Equal(got["stderr"], []byte("err\x00\xfe\nλ")) {
				t.Fatal("logical bytes changed", got)
			}
			return m
		default:
			t.Fatal("unexpected split result", m)
		}
	}
}

func TestSplitCompletionCleanupBeforeKeepaliveEOFRemovalAndResult(t *testing.T) {
	f, owner, writers := splitCompletionFixture(t)
	start := f.c.active.outputStart
	fds := splitFDs(owner.SplitResources)
	var raw []byte
	for i, data := range [][]byte{[]byte("out\x00\xff\r\né"), []byte("err\x00\xfe\nλ")} {
		if _, err := writers[i].Write(data); err != nil {
			t.Fatal(err)
		}
		raw = append(raw, splitRead(t, f.relayFixture, len(data))...)
	}
	entered, release := make(chan context.Context, 1), make(chan struct{})
	done := completeFixture(t, f, relayTestContext(t), func(ctx context.Context, id string, pid int64) error {
		if id != "operation-1" || pid != 77 {
			return errors.New("wrong cleanup identity")
		}
		entered <- ctx
		select {
		case <-release:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	ctx := <-entered
	for _, fd := range owner.operation.keepalives {
		if _, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0); e != 0 {
			t.Fatal("keepalive closed before cleanup completed", e)
		}
	}
	_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("input_closed before cleanup", n, err)
	}
	_ = f.control.SetReadDeadline(time.Time{})
	for _, w := range writers {
		_ = w.Close()
	}
	close(release)
	if m, ok := f.readPrivate(t).(*protocol.PrivateInputClosed); !ok || m.OperationID != "operation-1" {
		t.Fatal(m)
	}
	if owner.operation != nil || f.r.ownsSplit("operation-1") {
		t.Fatal("input_closed before resource disposal")
	}
	splitAssertClosed(t, fds)
	splitAssertGone(t, owner.root)
	if deadline, ok := ctx.Deadline(); !ok || time.Now().After(deadline) {
		t.Fatal("cleanup exhausted its original epoch", deadline)
	}
	f.private(t, protocol.PrivateCompleted{OperationID: "operation-1", Status: 7, PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{}})
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if m := splitCompletedBytes(t, f, raw, start); m.Status != 7 {
		t.Fatal(m)
	}
	if f.c.active != nil {
		t.Fatal("completed split retained active operation")
	}
}

func TestSplitCompletionRequiresBothIndependentEOFs(t *testing.T) {
	f, owner, writers := splitCompletionFixture(t)
	done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error { _ = writers[0].Close(); return nil })
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	wait := relayTestContext(t)
	for {
		f.r.mu.Lock()
		s := f.r.split
		first, second := s.ended[0], s.ended[1]
		f.r.mu.Unlock()
		if first {
			if second {
				t.Fatal("live stderr writer reported EOF")
			}
			break
		}
		if wait.Err() != nil {
			t.Fatal("stdout EOF not observed")
		}
		runtime.Gosched()
	}
	_ = f.control.SetReadDeadline(time.Now().Add(30 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("one EOF released input_closed", n, err)
	}
	if _, err := os.Lstat(filepath.Join(owner.root, "split", "operation-1.stdout")); err != nil {
		t.Fatal("paths removed before second EOF", err)
	}
	_ = f.control.SetReadDeadline(time.Time{})
	_ = writers[1].Close()
	finishPrivateReturn(t, f, 0, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	splitAssertGone(t, owner.root)
}

func TestSplitCompletionMissingEOFUsesExistingCleanupBoundAndFailsClosed(t *testing.T) {
	f, owner, writers := splitCompletionFixture(t)
	fds := splitFDs(owner.SplitResources)
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	done := completeFixture(t, f, ctx, func(context.Context, string, int64) error { _ = writers[0].Close(); return nil })
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	if err := finishCompletion(t, done); err == nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("missing second EOF produced ordinary success", err)
	}
	if f.r.w.channels.Err() == nil {
		t.Fatal("missing EOF left session live")
	}
	_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("missing EOF released private acknowledgement", n, err)
	}
	_ = f.c.Close(ctx)
	splitAssertClosed(t, fds)
	splitAssertGone(t, owner.root)
	for {
		select {
		case m, open := <-f.frames:
			if !open {
				return
			}
			if _, ok := m.(*protocol.OperationCompleted); ok {
				t.Fatal("result after failed EOF proof", m)
			}
		default:
			return
		}
	}
}

func TestSplitCompletionRejectsFreshEpochAndPrematureRemoval(t *testing.T) {
	owner := splitOwnerFor(t)
	start := splitContext(t)
	if _, _, err := owner.Prepare(start, splitRequest()); err != nil {
		t.Fatal(err)
	}
	cleanup, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := owner.complete(cleanup, "operation-1"); err == nil {
		t.Fatal("completion before keepalive closure")
	}
	if err := owner.closeKeepalives(cleanup, "wrong"); err == nil {
		t.Fatal("wrong cleanup operation")
	}
	if err := owner.closeKeepalives(cleanup, "operation-1"); err != nil {
		t.Fatal(err)
	}
	fresh, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := owner.complete(fresh, "operation-1"); err == nil {
		t.Fatal("fresh cleanup epoch accepted")
	}
	if err := owner.dispose(cleanup, "operation-1"); err != nil {
		t.Fatal(err)
	}
	splitAssertGone(t, owner.root)
}

func TestSplitResizeClosesEveryAdmittedSourceAndResumesReaders(t *testing.T) {
	f, _, writers := splitRelayFor(t)
	f.r.mu.Lock()
	admitted := f.r.split.pumps
	for i, w := range writers {
		if _, err := w.Write(bytes.Repeat([]byte{byte('a' + i)}, 4096)); err != nil {
			f.r.mu.Unlock()
			t.Fatal(err)
		}
	}
	ctx := splitContext(t)
	for _, p := range f.r.split.pumps {
		for {
			p.mu.Lock()
			n := len(p.data)
			p.mu.Unlock()
			if n > 0 {
				break
			}
			if ctx.Err() != nil {
				f.r.mu.Unlock()
				t.Fatal("source never admitted its prefix")
			}
			runtime.Gosched()
		}
	}
	done := make(chan error, 1)
	go func() {
		ok, err := f.r.Resize(ctx, 91, 37)
		if err == nil && !ok {
			err = errors.New("resize was not applied")
		}
		done <- err
	}()
	// Establish boundary ownership while the reader chunks are still held.
	for {
		f.r.phaseMu.Lock()
		owned := f.r.phase != nil
		f.r.phaseMu.Unlock()
		if owned {
			break
		}
		if ctx.Err() != nil {
			f.r.mu.Unlock()
			t.Fatal("resize boundary did not start")
		}
		runtime.Gosched()
	}
	f.r.mu.Unlock()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	for _, reader := range admitted {
		select {
		case <-reader.done:
		default:
			t.Fatal("resize published before joining admitted split reader")
		}
	}
	raw := splitRead(t, f, 8192)
	stream := "pty"
	offset := int64(0)
	got := map[string][]byte{}
	for {
		switch m := nextRelayFrame(t, f).(type) {
		case *protocol.OutputMark:
			if m.Offset < offset || m.Offset > int64(len(raw)) {
				t.Fatal("resize stream frontier", m)
			}
			got[stream] = append(got[stream], raw[offset:m.Offset]...)
			offset, stream = m.Offset, m.Stream
		case *protocol.ResizeApplied:
			if m.OutputThrough != 8192 || offset != m.OutputThrough {
				t.Fatal("resize omitted admitted split source", m, offset)
			}
			for i, name := range splitStreams {
				if !bytes.Equal(got[name], bytes.Repeat([]byte{byte('a' + i)}, 4096)) {
					t.Fatal("resize changed covered logical source", name)
				}
			}
			if columns, rows := windowSize(t, f.slave); columns != 91 || rows != 37 {
				t.Fatal("wrong actual resize", columns, rows)
			}
			if _, err := writers[1].Write([]byte("after-resize")); err != nil {
				t.Fatal(err)
			}
			if actual := splitRead(t, f, 12); string(actual) != "after-resize" {
				t.Fatal("paused reader did not resume", actual)
			}
			return
		default:
			t.Fatal("unexpected resize event", m)
		}
	}
}

func TestSplitResizeBeforeFirstOutputKeepsZeroSourceFrontier(t *testing.T) {
	f, _, _ := splitRelayFor(t)
	if ok, err := f.r.Resize(splitContext(t), 82, 26); err != nil || !ok {
		t.Fatal(ok, err)
	}
	for {
		switch m := nextRelayFrame(t, f).(type) {
		case *protocol.OutputMark:
			if m.Offset != 0 || m.Stream != "pty" {
				t.Fatal("empty split invented a source prefix", m)
			}
		case *protocol.ResizeApplied:
			if m.OutputThrough != 0 {
				t.Fatal(m)
			}
			return
		default:
			t.Fatal(m)
		}
	}
}

func TestSplitCompletionRemovalFailureCannotReleaseInputClosed(t *testing.T) {
	f, owner, writers := splitCompletionFixture(t)
	cleanupDone := make(chan struct{})
	done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error {
		for _, w := range writers {
			_ = w.Close()
		}
		// A same-identity authored mutation is outside containment; identity checks
		// still prohibit removing this foreign replacement or reporting success.
		path := filepath.Join(owner.root, "split", "operation-1.stdout")
		if err := os.Rename(path, path+".original"); err != nil {
			return err
		}
		if err := os.WriteFile(path, []byte("foreign"), 0600); err != nil {
			return err
		}
		close(cleanupDone)
		return nil
	})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	<-cleanupDone
	if err := finishCompletion(t, done); err == nil {
		t.Fatal("foreign FIFO replacement permitted success")
	}
	_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("failed split disposal released helper", n, err)
	}
	path := filepath.Join(owner.root, "split", "operation-1.stdout")
	if data, err := os.ReadFile(path); err != nil || string(data) != "foreign" {
		t.Fatal("foreign entry removed", string(data), err)
	}
}

func TestSplitCompletionShellEndAndQueuedTimeoutUseSameCleanupBarrier(t *testing.T) {
	for _, kind := range []string{"shell-end", "queued-timeout", "cleanup-failure"} {
		t.Run(kind, func(t *testing.T) {
			f, owner, writers := splitCompletionFixture(t)
			fds := splitFDs(owner.SplitResources)
			if kind == "queued-timeout" {
				f.c.active.cancel = lifecycleRequest("cancel", "operation-1", "user").(*protocol.Cancel)
				f.c.active.releasedAt = time.Now().Add(-lifecycleGrace - time.Second)
			}
			entered, release := make(chan struct{}), make(chan struct{})
			peers := &controlPeers{endShell: func(ctx context.Context, id string, timeout bool) error {
				if id != "operation-1" || timeout != (kind == "queued-timeout") {
					return errors.New("wrong split shell-end cleanup")
				}
				for _, fd := range owner.operation.keepalives {
					if _, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0); e != 0 {
						return errors.New("keepalive closed before shell-end cleanup")
					}
				}
				close(entered)
				if kind == "cleanup-failure" {
					return errors.New("injected split shell-end cleanup failure")
				}
				select {
				case <-release:
				case <-ctx.Done():
					return ctx.Err()
				}
				for _, w := range writers {
					_ = w.Close()
				}
				if timeout {
					f.private(t, protocol.PrivateInputClose{OperationID: id, CompletionHelperPID: 77})
					f.private(t, protocol.PrivateShellExit{ActiveOperationID: id, Status: 137, CWD: "/tmp"})
				}
				_ = f.result.Close()
				_ = f.slave.Close()
				return nil
			}}
			done := controlledFixture(t, f, relayTestContext(t), cleanReturn, peers)
			if kind != "queued-timeout" {
				f.private(t, protocol.PrivateShellExit{ActiveOperationID: "operation-1", Status: 7, CWD: "/tmp"})
			}
			select {
			case <-entered:
			case <-relayTestContext(t).Done():
				t.Fatal("split shell-end cleanup was not entered")
			}
			if kind == "cleanup-failure" {
				if err := finishCompletion(t, done); err == nil {
					t.Fatal("failed split end cleanup produced success")
				}
				if _, ok := nextDispatch(t, f).(*protocol.Diagnostic); !ok {
					t.Fatal("result escaped failed cleanup")
				}
				return
			}
			close(release)
			if err := finishCompletion(t, done); err != nil {
				t.Fatal(err)
			}
			result := nextDispatch(t, f)
			if kind == "shell-end" {
				m, ok := result.(*protocol.OperationCompleted)
				if !ok || m.ShellEnded == nil || !*m.ShellEnded || m.Status != 7 {
					t.Fatal(result)
				}
			} else {
				m, ok := result.(*protocol.OperationFailed)
				if !ok || m.Code != "cancel-timeout" || m.ShellEnded == nil || !*m.ShellEnded {
					t.Fatal(result)
				}
			}
			if owner.operation != nil {
				t.Fatal("shell-end result preceded split disposal")
			}
			splitAssertClosed(t, fds)
			splitAssertGone(t, owner.root)
			if _, ok := nextDispatch(t, f).(*protocol.Draining); !ok {
				t.Fatal("missing drain")
			}
			if _, ok := nextDispatch(t, f).(*protocol.Closed); !ok {
				t.Fatal("missing closed")
			}
		})
	}
}

func TestSplitCompletionGateResizeAndNextPTYOperation(t *testing.T) {
	f, owner, writers := splitCompletionFixture(t)
	done := controlledFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error {
		for _, w := range writers {
			_ = w.Close()
		}
		return nil
	}, nil)
	f.private(t, protocol.PrivateGateReady{OperationID: "operation-1", GateID: "split-gate"})
	if m, ok := nextDispatch(t, f).(*protocol.OperationReady); !ok || m.GateID != "split-gate" {
		t.Fatal(m)
	}
	dispatchRequest(t, f, &protocol.Continue{OperationID: "operation-1", GateID: "split-gate", InputThrough: 4})
	waitWatermark(t, f, 4)
	for _, w := range writers {
		if _, err := w.Write([]byte("prefix")); err != nil {
			t.Fatal(err)
		}
	}
	_ = splitRead(t, f.relayFixture, 12) // Establish the admitted prefix before resize.
	dispatchRequest(t, f, &protocol.Resize{Columns: 97, Rows: 35})
	if m, ok := nextDispatch(t, f).(*protocol.ResizeApplied); !ok || m.OutputThrough != f.c.active.outputStart+12 {
		t.Fatal("gate resize omitted a split prefix", m)
	}
	if _, err := f.terminal.Write([]byte("data")); err != nil {
		t.Fatal(err)
	}
	_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
	var input [4]byte
	if _, err := io.ReadFull(f.slave, input[:]); err != nil || string(input[:]) != "data" {
		t.Fatal("gate input changed", input, err)
	}
	if m, ok := f.readPrivate(t).(*protocol.PrivateContinue); !ok || m.GateID != "split-gate" {
		t.Fatal(m)
	}
	f.private(t, protocol.PrivateGateContinued{OperationID: "operation-1", GateID: "split-gate"})
	if _, ok := nextDispatch(t, f).(*protocol.OperationContinued); !ok {
		t.Fatal("gate continue missing")
	}
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 0, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	previous := completedEvent(t, f)
	splitAssertGone(t, owner.root)
	request := startRequest()
	request.OperationID = "operation-2"
	request.InputThrough = 4
	started := f.start(t, context.Background(), request)
	f.throughStarted(t)
	f.private(t, protocol.PrivateStartReleased{OperationID: request.OperationID})
	if result := finishStart(t, started); result.err != nil || !result.out.Started || result.out.OutputStart != previous.OutputThrough+int64(len("before-start\n")) {
		t.Fatal("split to PTY reuse failed", result)
	}
	next := completeFixture(t, f, relayTestContext(t), cleanReturn)
	f.private(t, protocol.PrivateInputClose{OperationID: request.OperationID, CompletionHelperPID: 78})
	if m, ok := f.readPrivate(t).(*protocol.PrivateInputClosed); !ok || m.OperationID != request.OperationID {
		t.Fatal(m)
	}
	f.private(t, protocol.PrivateCompleted{OperationID: request.OperationID, Status: 0, PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{}})
	if err := finishCompletion(t, next); err != nil {
		t.Fatal(err)
	}
	if m := completedEvent(t, f); m.OperationID != request.OperationID {
		t.Fatal(m)
	}
}

func TestSplitResizeContinuousSourcesPublishFiniteBoundary(t *testing.T) {
	f, _, writers := splitRelayFor(t)
	ctx := splitContext(t)
	stop := make(chan struct{})
	writerDone := make(chan struct{}, 2)
	for i, w := range writers {
		go func(i int, w *os.File) {
			defer func() { writerDone <- struct{}{} }()
			chunk := bytes.Repeat([]byte{byte('a' + i)}, 4096)
			for {
				select {
				case <-stop:
					return
				default:
				}
				if _, err := w.Write(chunk); err != nil {
					return
				}
			}
		}(i, w)
	}
	readDone := make(chan struct{})
	go func() { defer close(readDone); _, _ = io.Copy(io.Discard, f.terminal) }()
	defer func() {
		close(stop)
		for _, w := range writers {
			_ = w.SetWriteDeadline(time.Now())
		}
		<-writerDone
		<-writerDone
		_ = f.terminal.SetReadDeadline(time.Now())
		<-readDone
	}()
	// Require actual output from both sources before starting the boundary.
	seen := map[string]bool{}
	for !seen["stdout"] || !seen["stderr"] {
		if mark, ok := nextRelayFrame(t, f).(*protocol.OutputMark); ok {
			seen[mark.Stream] = true
		}
	}
	if ok, err := f.r.Resize(ctx, 103, 39); err != nil || !ok {
		t.Fatal("continuous FIFO output prevented finite resize", ok, err)
	}
	for {
		switch m := nextRelayFrame(t, f).(type) {
		case *protocol.OutputMark:
		case *protocol.ResizeApplied:
			if m.OutputThrough <= 0 {
				t.Fatal("continuous resize lost admitted output", m)
			}
			if columns, rows := windowSize(t, f.slave); columns != 103 || rows != 39 {
				t.Fatal("continuous resize did not apply ioctl", columns, rows)
			}
			select {
			case <-writerDone:
				t.Fatal("writer ended before resize acknowledgement")
			default:
			}
			return
		default:
			t.Fatal("unexpected continuous resize result", m)
		}
	}
}

func TestSplitCompletionRejectedSourceRollsBackBeforeAttachment(t *testing.T) {
	owner := splitOwnerFor(t)
	f := startFixtureFor(t, owner)
	done := f.start(t, context.Background(), splitRequest())
	if m, ok := f.readPrivate(t).(*protocol.PrivateExecute); !ok || m.ExecutionShape != "split" {
		t.Fatal("split admission did not receive actual paths", m)
	}
	fds := splitFDs(owner.SplitResources)
	if f.r.ownsSplit("operation-1") {
		t.Fatal("split readers attached before source admission")
	}
	f.private(t, protocol.PrivateRejected{OperationID: "operation-1", Code: "source-policy", Message: "rejected source"})
	if result := finishStart(t, done); result.err != nil || result.out.Started {
		t.Fatal("source rejection lost recoverability", result)
	}
	if _, ok := nextDispatch(t, f).(*protocol.OperationFailed); !ok {
		t.Fatal("source rejection did not report failure")
	}
	if owner.operation != nil || f.r.ownsSplit("operation-1") {
		t.Fatal("rejected source retained split ownership")
	}
	splitAssertClosed(t, fds)
	splitAssertGone(t, owner.root)
}

func (r *Relay) ownsSplit(id string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.split != nil && r.split.id == id
}

func TestSplitCompletionBlockedOutputRetainsCleanupDeadline(t *testing.T) {
	f, owner, writers := splitCompletionFixture(t)
	if err := f.r.w.channels.terminal.SetWriteBuffer(1024); err != nil {
		t.Fatal(err)
	}
	_ = f.terminal.SetReadBuffer(1024)
	_ = f.r.w.channels.terminal.SetWriteDeadline(time.Now().Add(30 * time.Millisecond))
	if n, err := f.r.w.channels.terminal.Write(bytes.Repeat([]byte("x"), 4<<20)); n == 0 || !os.IsTimeout(err) {
		t.Fatal("backpressure not established", n, err)
	}
	_ = f.r.w.channels.terminal.SetWriteDeadline(time.Time{})
	if _, err := writers[0].Write([]byte("blocked")); err != nil {
		t.Fatal(err)
	}
	wait := relayTestContext(t)
	for {
		f.r.phaseMu.Lock()
		writing := f.r.writerCancel != nil
		f.r.phaseMu.Unlock()
		if writing {
			break
		}
		if wait.Err() != nil {
			t.Fatal("blocked output did not enter writer")
		}
		runtime.Gosched()
	}
	ctx, end := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer end()
	entered := make(chan struct{})
	done := completeFixture(t, f, ctx, func(context.Context, string, int64) error {
		close(entered)
		for _, w := range writers {
			_ = w.Close()
		}
		return nil
	})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	select {
	case err := <-done:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("blocked completion lost deadline", err)
		}
	case <-time.After(time.Second):
		_ = f.terminal.Close() // Release a broken unbounded implementation before failing.
		_ = finishCompletion(t, done)
		t.Fatal("completion admission blocked outside its cleanup epoch")
	}
	select {
	case <-entered:
	default:
		t.Fatal("blocked output prevented cleanup fact processing")
	}
	_ = f.control.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("blocked output released input_closed", n, err)
	}
	_ = f.c.Close(ctx)
	splitAssertGone(t, owner.root)
}

func TestSplitCompletionCleanupIdentityAndEpochGuards(t *testing.T) {
	owner := splitOwnerFor(t)
	start := splitContext(t)
	if _, _, err := owner.Prepare(start, splitRequest()); err != nil {
		t.Fatal(err)
	}
	for _, ctx := range []context.Context{context.Background(), func() context.Context { c, end := context.WithCancel(start); end(); return c }()} {
		if err := owner.closeKeepalives(ctx, "operation-1"); err == nil {
			t.Fatal("invalid cleanup epoch closed keepalives")
		}
	}
	cleanup, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := owner.complete(cleanup, "other"); err == nil {
		t.Fatal("foreign operation completed split ownership")
	}
	if err := owner.closeKeepalives(cleanup, "operation-1"); err != nil {
		t.Fatal(err)
	}
	if err := owner.closeKeepalives(cleanup, "operation-1"); err == nil {
		t.Fatal("repeated closure reset cleanup epoch")
	}
	path := filepath.Join(owner.root, "split", "operation-1.stderr")
	if err := os.Rename(path, path+".original"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("foreign"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := owner.complete(cleanup, "operation-1"); err == nil {
		t.Fatal("post-EOF identity drift permitted removal")
	}
	if data, err := os.ReadFile(path); err != nil || string(data) != "foreign" {
		t.Fatal("foreign path removed", err)
	}
	_ = owner.dispose(cleanup, "operation-1")
}

func TestSplitResizeRejectsWakeFromReplacedReader(t *testing.T) {
	f, _, writers := splitRelayFor(t)
	f.r.mu.Lock()
	s := f.r.split
	old := s.pumps[0]
	if err := f.r.pauseSplit(); err != nil {
		f.r.mu.Unlock()
		t.Fatal(err)
	}
	for i := range s.pumps {
		f.r.resumeSplitIfReady(i)
	}
	current := s.pumps[0]
	f.r.mu.Unlock()
	if current == nil || current == old {
		t.Fatal("boundary did not replace reader")
	}
	// The old select may already have consumed a wake before the boundary.
	// It must not acknowledge a chunk that the new reader has not delivered.
	f.r.consumeSplit(s, 0, old)
	select {
	case <-current.resume:
		t.Fatal("stale wake resumed replacement before chunk delivery")
	default:
	}
	for _, payload := range []string{"first-after-boundary", "second-after-boundary"} {
		if _, err := writers[0].Write([]byte(payload)); err != nil {
			t.Fatal(err)
		}
		if actual := splitRead(t, f, len(payload)); string(actual) != payload {
			t.Fatal("replacement lost exact bytes", actual)
		}
	}
}
