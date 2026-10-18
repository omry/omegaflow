//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func splitRelayFor(t *testing.T) (relayFixture, *splitTestOwner, [2]*os.File) {
	t.Helper()
	f := relayFixtureFor(t)
	s := splitOwnerFor(t)
	ctx := splitContext(t)
	if _, _, err := s.Prepare(ctx, splitRequest()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = f.r.Close(splitContext(t))
		if err := s.Rollback(ctx, "operation-1"); err != nil {
			t.Error(err)
		}
	})
	if err := f.r.attachSplit(ctx, s.SplitResources, "operation-1"); err != nil {
		t.Fatal(err)
	}
	var writers [2]*os.File
	for i, stream := range splitStreams {
		file, err := os.OpenFile(filepath.Join(s.root, "split", "operation-1."+stream), os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		writers[i] = file
		t.Cleanup(func() { _ = file.Close() })
	}
	return f, s, writers
}

func splitRelayManualFor(t *testing.T) (relayFixture, *splitTestOwner, [2]*os.File) {
	t.Helper()
	f := relayFixtureFor(t)
	s := splitOwnerFor(t)
	ctx := splitContext(t)
	if _, _, err := s.Prepare(ctx, splitRequest()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = f.r.Close(splitContext(t))
		if err := s.Rollback(ctx, "operation-1"); err != nil {
			t.Error(err)
		}
	})
	files, err := s.relayReaders("operation-1")
	if err != nil {
		t.Fatal(err)
	}
	attached := &splitRelay{id: "operation-1", files: files, done: make(chan struct{})}
	attached.stopIO = joinedAfterFunc(f.r.ctx, func() {
		for _, file := range files {
			_ = file.SetReadDeadline(time.Now())
		}
	})
	for i, file := range files {
		attached.pumps[i], err = readRelay(file)
		if err != nil {
			t.Fatal(err)
		}
	}
	f.r.mu.Lock()
	f.r.split = attached
	f.r.mu.Unlock()
	select {
	case f.r.wake <- struct{}{}:
	default:
	}
	var writers [2]*os.File
	for i, stream := range splitStreams {
		file, err := os.OpenFile(filepath.Join(s.root, "split", "operation-1."+stream), os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		writers[i] = file
		t.Cleanup(func() { _ = file.Close() })
	}
	return f, s, writers
}

func splitRead(t *testing.T, f relayFixture, n int) []byte {
	t.Helper()
	data := make([]byte, n)
	_ = f.terminal.SetReadDeadline(time.Now().Add(3 * time.Second))
	if _, err := io.ReadFull(f.terminal, data); err != nil {
		t.Fatal("raw output", n, err)
	}
	return data
}

// The temporary focused overlay calls this seam immediately after a complete
// terminal write and before writeOutputStream samples its context. The normal
// source has no call site; the test skips there and the overlay run supplies
// the deterministic scheduling boundary needed to prove the interruption.
type splitWriteSeam struct {
	stream      string
	entered     chan struct{}
	release     chan struct{}
	enteredOnce sync.Once
	releaseOnce sync.Once
}

var splitWriteSeamState struct {
	sync.Mutex
	current *splitWriteSeam
}

func armSplitWriteSeam(stream string) *splitWriteSeam {
	s := &splitWriteSeam{stream: stream, entered: make(chan struct{}), release: make(chan struct{})}
	splitWriteSeamState.Lock()
	splitWriteSeamState.current = s
	splitWriteSeamState.Unlock()
	return s
}

func (s *splitWriteSeam) disarm() {
	splitWriteSeamState.Lock()
	if splitWriteSeamState.current == s {
		splitWriteSeamState.current = nil
	}
	splitWriteSeamState.Unlock()
	s.releaseOnce.Do(func() { close(s.release) })
}

func splitAfterCompletedStreamWrite(_ *Relay, stream string, ctx context.Context) {
	splitWriteSeamState.Lock()
	seam := splitWriteSeamState.current
	if seam == nil || seam.stream != stream {
		splitWriteSeamState.Unlock()
		return
	}
	block := false
	seam.enteredOnce.Do(func() {
		close(seam.entered)
		block = true
	})
	splitWriteSeamState.Unlock()
	if !block {
		return
	}
	<-seam.release
	<-ctx.Done()
}

func waitSplitWriteSeam(t *testing.T, s *splitWriteSeam) bool {
	t.Helper()
	timer := time.NewTimer(500 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-s.entered:
		return true
	case <-timer.C:
		return false
	}
}

func closeSplitSources(s *splitTestOwner, writers [2]*os.File) {
	for i, writer := range writers {
		_ = writer.Close()
		if s.operation.keepalives[i] >= 0 {
			_ = syscall.Close(s.operation.keepalives[i])
			s.operation.keepalives[i] = -1
		}
	}
}

// Interpret only the public mark stream over the raw log. There is no second
// production log or per-stream byte counter to serve as the reconstruction.
func splitReconstruct(t *testing.T, f relayFixture, raw []byte) map[string][]byte {
	t.Helper()
	result := map[string][]byte{}
	stream, offset, elapsed := "pty", int64(0), int64(0)
	for {
		mark, ok := nextRelayFrame(t, f).(*protocol.OutputMark)
		if !ok || mark.Offset < offset || mark.Offset > int64(len(raw)) || mark.ElapsedUS < elapsed {
			t.Fatal("invalid stream frontier", mark, offset, len(raw))
		}
		result[stream] = append(result[stream], raw[offset:mark.Offset]...)
		stream, offset, elapsed = mark.Stream, mark.Offset, mark.ElapsedUS
		if offset == int64(len(raw)) {
			return result
		}
	}
}

func TestSplitRelayExactLogicalBytesAndFirstSource(t *testing.T) {
	for _, first := range []int{0, 1} {
		t.Run(splitStreams[first], func(t *testing.T) {
			f, _, writers := splitRelayFor(t)
			var raw []byte
			expected := map[string][]byte{}
			for _, i := range []int{first, 1 - first, first, 2, 1 - first} {
				data := []byte{'x', 0, 0xff, '\r', '\n', '\x1b', 3}
				data = append(data, []byte("éλ終")...)
				file, stream := f.slave, "pty"
				if i < 2 {
					file, stream = writers[i], splitStreams[i]
				}
				if _, err := file.Write(data); err != nil {
					t.Fatal(err)
				}
				raw = append(raw, splitRead(t, f, len(data))...)
				expected[stream] = append(expected[stream], data...)
			}
			if err := f.r.stopSplit(splitContext(t)); err != nil {
				t.Fatal(err)
			}
			actual := splitReconstruct(t, f, raw)
			for stream, want := range expected {
				if !bytes.Equal(actual[stream], want) {
					t.Fatalf("%s changed: %x != %x", stream, actual[stream], want)
				}
			}
			// An empty boundary repeats the most recently written source.
			if _, err := f.r.Drain(splitContext(t)); err != nil {
				t.Fatal(err)
			}
			mark := nextRelayFrame(t, f).(*protocol.OutputMark)
			if mark.Stream != splitStreams[1-first] || mark.Offset != int64(len(raw)) {
				t.Fatal("empty boundary changed current source", mark)
			}
		})
	}
}

func TestSplitRelayZeroBytesAndIndependentEOF(t *testing.T) {
	f, s, writers := splitRelayFor(t)
	f.r.mu.Lock()
	attached := f.r.split
	f.r.mu.Unlock()
	for i := range writers {
		_ = writers[i].Close()
		if err := syscall.Close(s.operation.keepalives[i]); err != nil {
			t.Fatal(err)
		}
		s.operation.keepalives[i] = -1
		if i == 0 {
			wait := splitContext(t)
			for {
				f.r.mu.Lock()
				ended := attached.ended[0]
				f.r.mu.Unlock()
				if ended {
					break
				}
				if wait.Err() != nil {
					t.Fatal("first source EOF was not observed")
				}
				runtime.Gosched()
			}
			// The other keepalive must prevent a false two-EOF assertion.
			select {
			case <-attached.done:
				t.Fatal("one closed source completed both EOF facts")
			default:
			}
		}
	}
	select {
	case <-attached.done:
	case <-splitContext(t).Done():
		t.Fatal("independent FIFO EOFs not observed")
	}
	if err := f.r.stopSplit(splitContext(t)); err != nil {
		t.Fatal(err)
	}
	// Attachment and detachment both cover zero bytes, still attributed to PTY.
	for range 3 {
		m := nextRelayFrame(t, f).(*protocol.OutputMark)
		if m.Offset != 0 || m.Stream != "pty" {
			t.Fatal("zero-byte split invented a source", m)
		}
	}
}

func TestSplitRelayConcurrentLargeStreams(t *testing.T) {
	f, _, writers := splitRelayFor(t)
	want := [2][]byte{bytes.Repeat([]byte("stdout\x00\xff\n"), 30000), bytes.Repeat([]byte("stderr\r\nλ"), 30000)}
	done := make(chan error, 2)
	for i, file := range writers {
		go func() { _, err := file.Write(want[i]); done <- err }()
	}
	raw := splitRead(t, f, len(want[0])+len(want[1]))
	for range writers {
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	}
	if err := f.r.stopSplit(splitContext(t)); err != nil {
		t.Fatal(err)
	}
	got := splitReconstruct(t, f, raw)
	for i, stream := range splitStreams {
		if !bytes.Equal(got[stream], want[i]) {
			t.Fatal("concurrent logical stream changed", stream, len(got[stream]), len(want[i]))
		}
	}
}

func TestSplitRelayReaderIdentityAndOwnedDisposal(t *testing.T) {
	f, s, _ := splitRelayFor(t)
	f.r.mu.Lock()
	var owned []int
	var pumps []*relayReader
	for i, file := range f.r.split.files {
		raw, err := file.SyscallConn()
		if err != nil {
			f.r.mu.Unlock()
			t.Fatal(err)
		}
		fd := -1
		if err := raw.Control(func(value uintptr) { fd = int(value) }); err != nil {
			f.r.mu.Unlock()
			t.Fatal(err)
		}
		owned = append(owned, fd)
		pumps = append(pumps, f.r.split.pumps[i])
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if errno != 0 || flags&syscall.FD_CLOEXEC == 0 || fd == s.operation.readers[i] {
			f.r.mu.Unlock()
			t.Fatal("reader ownership/isolation", fd, flags, errno)
		}
	}
	f.r.mu.Unlock()
	if err := f.r.stopSplit(splitContext(t)); err != nil {
		t.Fatal(err)
	}
	splitAssertClosed(t, owned)
	for _, pump := range pumps {
		select {
		case <-pump.done:
		default:
			t.Fatal("retained pump")
		}
	}
	if err := s.validate(); err != nil {
		t.Fatal("detachment consumed setup-owned handles", err)
	}
	if err := f.r.attachSplit(splitContext(t), s.SplitResources, "other"); err == nil {
		t.Fatal("wrong operation admitted")
	}
	ctx, cancel := context.WithCancel(splitContext(t))
	cancel()
	if err := f.r.attachSplit(ctx, s.SplitResources, "operation-1"); !errors.Is(err, context.Canceled) {
		t.Fatal("canceled attachment admitted", err)
	}
	if err := os.Chmod(filepath.Join(s.root, "split", "operation-1.stdout"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := f.r.attachSplit(splitContext(t), s.SplitResources, "operation-1"); err == nil {
		t.Fatal("changed reader identity admitted")
	}
	if err := os.Chmod(filepath.Join(s.root, "split", "operation-1.stdout"), 0600); err != nil {
		t.Fatal(err)
	}
}

func TestSplitRelayAdmittedChunksRemainBoundedAndSurviveClose(t *testing.T) {
	f, _, writers := splitRelayFor(t)
	f.r.mu.Lock()
	s := f.r.split
	for i, file := range writers {
		if _, err := file.Write(bytes.Repeat([]byte{byte('a' + i)}, 4096)); err != nil {
			f.r.mu.Unlock()
			t.Fatal(err)
		}
	}
	ctx := splitContext(t)
	for _, p := range s.pumps {
		for {
			p.mu.Lock()
			n := len(p.data)
			p.mu.Unlock()
			if n > 0 {
				if n > 32768 {
					t.Fatal("unbounded reader", n)
				}
				break
			}
			if ctx.Err() != nil {
				f.r.mu.Unlock()
				t.Fatal("no admitted chunk")
			}
			runtime.Gosched()
		}
	}
	closed := make(chan error, 1)
	go func() { closed <- f.r.Close(ctx) }()
	<-f.r.ctx.Done()
	f.r.mu.Unlock()
	if err := <-closed; err != nil {
		t.Fatal(err)
	}
	for i, pending := range f.r.w.pendingSplit {
		if !bytes.Equal(pending, bytes.Repeat([]byte{byte('a' + i)}, 4096)) {
			t.Fatal("lost admitted stream", i, len(pending))
		}
	}
	_ = f.slave.Close()
	_ = f.r.w.PTY.SetReadDeadline(time.Now().Add(time.Second))
	if err := f.r.w.drainTerminal(ctx); err != nil {
		t.Fatal(err)
	}
	got := splitReconstruct(t, f, splitRead(t, f, 8192))
	for i, stream := range splitStreams {
		if !bytes.Equal(got[stream], bytes.Repeat([]byte{byte('a' + i)}, 4096)) {
			t.Fatal("final drain misattributed retained output", stream)
		}
	}
}

func TestSplitRelayReadFailureFailsSession(t *testing.T) {
	f, _, _ := splitRelayFor(t)
	f.r.mu.Lock()
	file := f.r.split.files[0]
	f.r.mu.Unlock()
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-f.r.Done():
	case <-splitContext(t).Done():
		t.Fatal("read failure did not join the relay")
	}
	if f.r.Err() == nil || f.r.w.channels.Err() == nil {
		t.Fatal("read failure produced success")
	}
}

func TestSplitRelayCadenceAndExistingSessionBudgets(t *testing.T) {
	t.Run("cadence", func(t *testing.T) {
		f, _, writers := splitRelayFor(t)
		if _, err := writers[0].Write([]byte("first")); err != nil {
			t.Fatal(err)
		}
		_ = splitRead(t, f, 5)
		<-time.NewTimer(markCadence).C
		if _, err := writers[0].Write([]byte("second")); err != nil {
			t.Fatal(err)
		}
		_ = splitRead(t, f, 6)
		if err := f.r.stopSplit(splitContext(t)); err != nil {
			t.Fatal(err)
		}
		seen := false
		for {
			mark := nextRelayFrame(t, f).(*protocol.OutputMark)
			if mark.Offset == 5 && mark.Stream == "stdout" {
				seen = true
			}
			if mark.Offset == 11 {
				break
			}
		}
		if !seen {
			t.Fatal("same-stream sender cadence mark omitted")
		}
	})
	for _, kind := range []string{"marks", "bytes"} {
		t.Run(kind, func(t *testing.T) {
			f, _, writers := splitRelayFor(t)
			f.r.mu.Lock()
			if kind == "marks" {
				f.r.w.marks = markLimit
			} else {
				f.r.w.OutputThrough = outputLimit
			}
			before := f.r.w.OutputThrough
			f.r.mu.Unlock()
			if _, err := writers[1].Write([]byte("over budget")); err != nil {
				t.Fatal(err)
			}
			select {
			case <-f.r.Done():
			case <-splitContext(t).Done():
				t.Fatal("budget crossing did not fail relay")
			}
			if f.r.Err() == nil || f.r.w.OutputThrough != before {
				t.Fatal("split source reset or crossed session budget", f.r.Err(), f.r.w.OutputThrough)
			}
		})
	}
}

func TestSplitRelayRejectsDuplicateAttachment(t *testing.T) {
	f, s, _ := splitRelayFor(t)
	f.r.mu.Lock()
	owned := f.r.split
	f.r.mu.Unlock()
	if err := f.r.attachSplit(splitContext(t), s.SplitResources, "operation-1"); err == nil {
		t.Fatal("two split read owners admitted")
	}
	select {
	case <-f.r.Done():
	case <-splitContext(t).Done():
		t.Fatal("failed attachment did not join partial ownership")
	}
	for _, file := range owned.files {
		if file != nil {
			t.Fatal("duplicate failure retained original pump file")
		}
	}
	if err := s.validate(); err != nil {
		t.Fatal("failed attachment consumed setup owner", err)
	}
}

func TestSplitRelayInterruptedWriterRetainsExactSourceSuffix(t *testing.T) {
	for _, stream := range splitStreams {
		t.Run(stream, func(t *testing.T) {
			f := relayFixtureFor(t)
			_ = f.r.w.channels.terminal.SetWriteBuffer(1024)
			payload := bytes.Repeat([]byte("exact\x00\xff\r\n"), 300000)
			ctx, cancel := context.WithCancelCause(splitContext(t))
			defer cancel(nil)
			done := make(chan error, 1)
			go func() {
				f.r.mu.Lock()
				err := f.r.writeOutputStream(ctx, payload, stream)
				f.r.mu.Unlock()
				done <- err
			}()
			// The source mark proves the writer entered its actual write phase.
			m := nextRelayFrame(t, f).(*protocol.OutputMark)
			if m.Stream != stream || m.Offset != 0 {
				t.Fatal(m)
			}
			cancel(errWorkInterrupted)
			if err := <-done; !errors.Is(err, errWorkInterrupted) {
				t.Fatal("missing interrupted writer result", err)
			}
			i := 0
			if stream == "stderr" {
				i = 1
			}
			f.r.mu.Lock()
			through := f.r.w.OutputThrough
			pending := append([]byte(nil), f.r.w.pendingSplit[i]...)
			f.r.mu.Unlock()
			if len(pending) == 0 || !bytes.Equal(pending, payload[through:]) {
				t.Fatal("interrupted suffix lost source/bytes", through, len(pending))
			}
			read := make(chan error, 1)
			go func() {
				got := make([]byte, len(payload))
				_ = f.terminal.SetReadDeadline(time.Now().Add(3 * time.Second))
				_, err := io.ReadFull(f.terminal, got)
				if err == nil && !bytes.Equal(got, payload) {
					err = fmt.Errorf("interrupted bytes changed")
				}
				read <- err
			}()
			f.r.mu.Lock()
			err := f.r.flushSplitPending(splitContext(t))
			f.r.mu.Unlock()
			if e := <-read; err != nil || e != nil {
				t.Fatal("retry failed", err, e)
			}
		})
	}
}

func TestSplitRelayPhaseRetryResumesLiveSource(t *testing.T) {
	f, _, writers := splitRelayFor(t)
	if err := f.terminal.SetReadBuffer(4096); err != nil {
		t.Fatal(err)
	}
	if err := f.r.w.channels.terminal.SetWriteBuffer(1024); err != nil {
		t.Fatal(err)
	}
	f.r.mu.Lock()
	_ = f.r.w.channels.terminal.SetWriteDeadline(time.Now().Add(100 * time.Millisecond))
	n, err := f.r.w.channels.terminal.Write(bytes.Repeat([]byte("p"), 1<<20))
	if err == nil || n == 1<<20 {
		f.r.mu.Unlock()
		t.Fatal("backpressure not established", n, err)
	}
	f.r.w.OutputThrough += int64(n)
	f.r.mu.Unlock()
	first, second := bytes.Repeat([]byte("a\x00\xff\n"), 1024), []byte("following FIFO chunk\r\n")
	if _, err := writers[0].Write(first); err != nil {
		t.Fatal(err)
	}
	for {
		m := nextRelayFrame(t, f).(*protocol.OutputMark)
		if m.Stream == "stdout" {
			break
		}
	}
	job, interrupt := context.WithCancelCause(splitContext(t))
	defer interrupt(nil)
	boundary := make(chan error, 1)
	go func() { _, err := f.r.boundary(job, false, nil); boundary <- err }()
	wait := splitContext(t)
	for {
		f.r.phaseMu.Lock()
		entered := f.r.phase == job
		f.r.phaseMu.Unlock()
		if entered {
			break
		}
		if wait.Err() != nil {
			t.Fatal("boundary did not acquire the blocked writer's epoch")
		}
		runtime.Gosched()
	}
	interrupt(errWorkInterrupted)
	select {
	case err := <-boundary:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("wrong interruption", err)
		}
	case <-wait.Done():
		t.Fatal("boundary did not release")
	}
	if f.r.w.channels.Err() != nil {
		t.Fatal("phase interruption failed session", f.r.w.channels.Err())
	}
	want := append(bytes.Repeat([]byte("p"), n), first...)
	want = append(want, second...)
	read := make(chan error, 1)
	go func() {
		_ = f.terminal.SetReadDeadline(time.Now().Add(3 * time.Second))
		got := make([]byte, len(want))
		_, err := io.ReadFull(f.terminal, got)
		if err == nil && !bytes.Equal(got, want) {
			err = fmt.Errorf("live source changed after retry")
		}
		read <- err
	}()
	if _, err := writers[0].Write(second); err != nil {
		t.Fatal(err)
	}
	if err := <-read; err != nil {
		t.Fatal("live source did not resume after its retained suffix", err)
	}
	if err := f.r.stopSplit(splitContext(t)); err != nil {
		t.Fatal(err)
	}
	if f.r.w.OutputThrough != int64(len(want)) || len(f.r.w.pendingSplit[0]) != 0 {
		t.Fatal("retry lost or duplicated bytes")
	}
}

func TestSplitRelayCompletedInitialChunkInterruptionResumesLiveSource(t *testing.T) {
	for source, stream := range splitStreams {
		t.Run(stream, func(t *testing.T) {
			seam := armSplitWriteSeam(stream)
			t.Cleanup(seam.disarm)
			f, s, writers := splitRelayFor(t)
			first, second := []byte("first-complete-chunk"), []byte("next-fifo-chunk")
			if _, err := writers[source].Write(first); err != nil {
				t.Fatal(err)
			}
			if !waitSplitWriteSeam(t, seam) {
				t.Skip("requires the temporary completed-write scheduling overlay")
			}
			if got := splitRead(t, f, len(first)); !bytes.Equal(got, first) {
				t.Fatal("initial bytes changed", got)
			}

			job, interrupt := context.WithCancelCause(splitContext(t))
			defer interrupt(nil)
			boundary := make(chan error, 1)
			go func() { _, err := f.r.boundary(job, false, nil); boundary <- err }()
			wait := splitContext(t)
			for {
				f.r.phaseMu.Lock()
				entered := f.r.phase == job
				f.r.phaseMu.Unlock()
				if entered {
					break
				}
				if wait.Err() != nil {
					t.Fatal("boundary did not acquire the completed writer epoch")
				}
				runtime.Gosched()
			}
			interrupt(errWorkInterrupted)
			seam.releaseOnce.Do(func() { close(seam.release) })
			select {
			case err := <-boundary:
				if !errors.Is(err, context.Canceled) {
					t.Fatal("wrong interruption", err)
				}
			case <-wait.Done():
				t.Fatal("boundary did not release")
			}

			f.r.mu.Lock()
			attached := f.r.split
			pending, through := len(f.r.w.pendingSplit[source]), f.r.w.OutputThrough
			f.r.mu.Unlock()
			if pending != 0 || through != int64(len(first)) {
				t.Fatalf("complete interrupted chunk was not acknowledged: pending=%d through=%d", pending, through)
			}
			if f.r.w.channels.Err() != nil {
				t.Fatal("phase interruption failed session", f.r.w.channels.Err())
			}
			if _, err := writers[source].Write(second); err != nil {
				t.Fatal(err)
			}
			if got := splitRead(t, f, len(second)); !bytes.Equal(got, second) {
				t.Fatal("resumed source bytes changed", got)
			}
			closeSplitSources(s, writers)
			select {
			case <-attached.done:
			case <-splitContext(t).Done():
				f.r.mu.Lock()
				ended := attached.ended
				pumps := [2]bool{attached.pumps[0] != nil, attached.pumps[1] != nil}
				f.r.mu.Unlock()
				t.Fatalf("independent FIFO EOFs did not arrive: ended=%v pumps=%v channels=%v relay=%v", ended, pumps, f.r.w.channels.Err(), f.r.Err())
			}
			if err := f.r.stopSplit(splitContext(t)); err != nil {
				t.Fatal(err)
			}
			if f.r.w.OutputThrough != int64(len(first)+len(second)) || len(f.r.w.pendingSplit[source]) != 0 {
				t.Fatal("resumed source duplicated or retained bytes")
			}
		})
	}
}

func TestSplitRelayCompletedSuffixInterruptionResumesLiveSource(t *testing.T) {
	for source, stream := range splitStreams {
		t.Run(stream, func(t *testing.T) {
			f, s, writers := splitRelayManualFor(t)
			if err := f.r.w.channels.terminal.SetWriteBuffer(1024); err != nil {
				t.Fatal(err)
			}
			prefix := bytes.Repeat([]byte("p"), 1<<20)
			f.r.mu.Lock()
			_ = f.r.w.channels.terminal.SetWriteDeadline(time.Now().Add(100 * time.Millisecond))
			n, err := f.r.w.channels.terminal.Write(prefix)
			if err == nil || n == len(prefix) {
				f.r.mu.Unlock()
				t.Fatal("backpressure not established", n, err)
			}
			f.r.w.OutputThrough += int64(n)
			f.r.mu.Unlock()
			first := bytes.Repeat([]byte("source\x00\xff\r\n"), 3000)
			job, interrupt := context.WithCancelCause(splitContext(t))
			defer interrupt(nil)
			f.r.phaseMu.Lock()
			f.r.phase = job
			f.r.phaseMu.Unlock()
			t.Cleanup(func() {
				f.r.phaseMu.Lock()
				f.r.phase = nil
				f.r.phaseMu.Unlock()
			})
			if _, err := writers[source].Write(first); err != nil {
				t.Fatal(err)
			}
			for {
				mark := nextRelayFrame(t, f).(*protocol.OutputMark)
				if mark.Stream == stream {
					break
				}
			}
			interrupt(errWorkInterrupted)
			wait := splitContext(t)
			var through int64
			var pending []byte
			for {
				f.r.mu.Lock()
				through = f.r.w.OutputThrough
				pending = append([]byte(nil), f.r.w.pendingSplit[source]...)
				f.r.mu.Unlock()
				f.r.phaseMu.Lock()
				writerDone := f.r.writerCancel == nil
				f.r.phaseMu.Unlock()
				if writerDone && len(pending) != 0 {
					break
				}
				if wait.Err() != nil {
					t.Fatal("interrupted source did not retain a suffix")
				}
				runtime.Gosched()
			}
			f.r.phaseMu.Lock()
			f.r.phase = nil
			f.r.phaseMu.Unlock()
			before := int64(n)
			if through < before || through-before > int64(len(first)) || !bytes.Equal(pending, first[through-before:]) {
				t.Fatal("nonempty retained suffix was not preserved", through, len(pending))
			}

			seam := armSplitWriteSeam(stream)
			t.Cleanup(seam.disarm)
			retry, retryCancel := context.WithCancelCause(splitContext(t))
			defer retryCancel(nil)
			flushed := make(chan error, 1)
			go func() {
				f.r.mu.Lock()
				err := f.r.flushSplitPending(retry)
				f.r.mu.Unlock()
				flushed <- err
			}()
			read := make(chan struct {
				data []byte
				err  error
			}, 1)
			go func() {
				data := make([]byte, n+len(first))
				_ = f.terminal.SetReadDeadline(time.Now().Add(3 * time.Second))
				_, err := io.ReadFull(f.terminal, data)
				read <- struct {
					data []byte
					err  error
				}{data, err}
			}()
			if !waitSplitWriteSeam(t, seam) {
				t.Skip("requires the temporary completed-write scheduling overlay")
			}
			result := <-read
			want := append(prefix[:n], first...)
			if result.err != nil || !bytes.Equal(result.data, want) {
				t.Fatal("retained suffix bytes changed", result.err)
			}
			retryCancel(errWorkInterrupted)
			seam.releaseOnce.Do(func() { close(seam.release) })
			if err := <-flushed; !errors.Is(err, errWorkInterrupted) {
				t.Fatal("missing interrupted flush result", err)
			}

			f.r.mu.Lock()
			attached := f.r.split
			through = f.r.w.OutputThrough
			pendingLen := len(f.r.w.pendingSplit[source])
			f.r.mu.Unlock()
			if pendingLen != 0 || through != int64(n+len(first)) {
				t.Fatalf("complete interrupted suffix was not acknowledged: pending=%d through=%d", pendingLen, through)
			}
			if f.r.w.channels.Err() != nil {
				t.Fatal("suffix interruption failed session", f.r.w.channels.Err())
			}
			following := []byte("following-fifo-chunk")
			if _, err := writers[source].Write(following); err != nil {
				t.Fatal(err)
			}
			if got := splitRead(t, f, len(following)); !bytes.Equal(got, following) {
				t.Fatal("resumed suffix source bytes changed", got)
			}
			closeSplitSources(s, writers)
			select {
			case <-attached.done:
			case <-splitContext(t).Done():
				f.r.mu.Lock()
				ended := attached.ended
				pumps := [2]bool{attached.pumps[0] != nil, attached.pumps[1] != nil}
				f.r.mu.Unlock()
				t.Fatalf("independent FIFO EOFs did not arrive after suffix retry: ended=%v pumps=%v channels=%v relay=%v", ended, pumps, f.r.w.channels.Err(), f.r.Err())
			}
			if err := f.r.stopSplit(splitContext(t)); err != nil {
				t.Fatal(err)
			}
			if f.r.w.OutputThrough != int64(n+len(first)+len(following)) || len(f.r.w.pendingSplit[source]) != 0 {
				t.Fatal("suffix retry duplicated or retained bytes")
			}
		})
	}
}
