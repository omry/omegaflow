//go:build linux

package session

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"runtime"
	"sync"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type relayFixture struct {
	r                   *Relay
	slave               *os.File
	terminal, telemetry *net.TCPConn
	frames              <-chan protocol.Message
}

func relayTestContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func relayFixtureFor(t *testing.T) relayFixture {
	t.Helper()
	s, terminal, telemetry, launch := established(t)
	master, original := testPTY(t)
	slave, err := os.OpenFile(original.Name(), os.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
	if err != nil {
		t.Fatal(err)
	}
	_ = original.Close()
	t.Cleanup(func() { slave.Close() })
	var state syscall.Termios
	if err = ptyIOCTL(slave, syscall.TCGETS, unsafe.Pointer(&state)); err != nil {
		t.Fatal(err)
	}
	state.Lflag &^= syscall.ICANON | syscall.ECHO | syscall.ISIG | syscall.IEXTEN
	state.Iflag &^= syscall.BRKINT | syscall.ICRNL | syscall.INPCK | syscall.ISTRIP | syscall.IXON
	state.Oflag &^= syscall.OPOST
	state.Cc[syscall.VMIN], state.Cc[syscall.VTIME] = 1, 0
	if err = ptyIOCTL(slave, syscall.TCSETS, unsafe.Pointer(&state)); err != nil {
		t.Fatal(err)
	}
	if err = s.CommitReadiness(launch, func(ctx context.Context) error {
		return s.Send(ctx, protocol.Ready{Header: s.header("ready"), EnvoyPID: int64(os.Getpid()), ShellPID: int64(os.Getpid()), Cwd: "/tmp", Columns: 80, Rows: 24})
	}); err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(telemetry)
	frame, err := readLine(reader)
	if err != nil {
		t.Fatal(err)
	}
	if m, e := protocol.DecodeEnvoy(frame); e != nil {
		t.Fatal(e)
	} else if _, ok := m.(*protocol.Ready); !ok {
		t.Fatal("missing ready")
	}
	w := &Workload{PTY: master, channels: s, ReadyAt: time.Now(), CWD: "/tmp"}
	r, err := NewRelay(s.ctx, w)
	if err != nil {
		t.Fatal(err)
	}
	frames := make(chan protocol.Message, 256)
	stop, done := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		defer close(frames)
		for {
			line, e := readLine(reader)
			if e != nil {
				return
			}
			m, e := protocol.DecodeEnvoy(line)
			if e != nil {
				return
			}
			select {
			case frames <- m:
			case <-stop:
				return
			}
		}
	}()
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = r.Close(ctx)
		close(stop)
		telemetry.Close()
		select {
		case <-done:
		case <-ctx.Done():
			t.Error("retained telemetry test reader")
		}
	})
	return relayFixture{r, slave, terminal, telemetry, frames}
}

func waitInputCount(t *testing.T, s *Channels, count int64) {
	t.Helper()
	ctx := relayTestContext(t)
	g := s.inputState()
	for {
		g.mu.Lock()
		got, wake := g.through, g.wake
		g.mu.Unlock()
		if got == count {
			return
		}
		if got > count {
			t.Fatalf("input counter crossed expected boundary: %d > %d", got, count)
		}
		select {
		case <-wake:
		case <-ctx.Done():
			t.Fatal("input count did not reach", count)
		}
	}
}

func nextRelayFrame(t *testing.T, f relayFixture) protocol.Message {
	t.Helper()
	select {
	case m, ok := <-f.frames:
		if !ok {
			t.Fatal("telemetry closed without expected frame")
		}
		return m
	case <-relayTestContext(t).Done():
		t.Fatal("missing relay frame")
		return nil
	}
}

func receiveInput(t *testing.T, s *Channels) Event {
	t.Helper()
	select {
	case e := <-s.Events():
		return e
	case e := <-s.Controls():
		return e
	case <-relayTestContext(t).Done():
		t.Fatal("missing eligible input")
		return Event{}
	}
}

func TestRelayExactBinaryDrainAndMarks(t *testing.T) {
	f := relayFixtureFor(t)
	data := []byte{'a', 0, 0xff, '\n', '\r', 3, 'z'}
	if n, err := f.slave.Write(data); err != nil || n != len(data) {
		t.Fatal(n, err)
	}
	ctx := relayTestContext(t)
	offset, err := f.r.Drain(ctx)
	if err != nil || offset != int64(len(data)) {
		t.Fatal(offset, err)
	}
	got := make([]byte, len(data))
	f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err = io.ReadFull(f.terminal, got); err != nil || !bytes.Equal(got, data) {
		t.Fatal(got, err)
	}
	var lastOffset, lastTime int64
	for {
		m, ok := nextRelayFrame(t, f).(*protocol.OutputMark)
		if !ok {
			t.Fatal("non-mark")
		}
		if m.Stream != "pty" || m.Offset < lastOffset || m.ElapsedUS < lastTime {
			t.Fatal("regressed or misattributed mark", m)
		}
		lastOffset, lastTime = m.Offset, m.ElapsedUS
		if m.Offset == offset {
			break
		}
	}
	if again, err := f.r.Drain(ctx); err != nil || again != offset {
		t.Fatal(again, err)
	}
	m := nextRelayFrame(t, f).(*protocol.OutputMark)
	if m.Offset != offset || m.Stream != "pty" {
		t.Fatal("empty boundary did not repeat current stream", m)
	}
	if f.r.ReadError() != nil {
		t.Fatal("drain invented EOF", f.r.ReadError())
	}
}

func TestRelayFreshDrainCannotLoseReaderAdmittedPrefix(t *testing.T) {
	f := relayFixtureFor(t)
	data := bytes.Repeat([]byte("prefix"), 1000)
	f.r.mu.Lock()
	p := f.r.reader
	write := make(chan error, 1)
	go func() { _, err := f.slave.Write(data); write <- err }()
	wait := relayTestContext(t)
	for {
		p.mu.Lock()
		admitted := len(p.data) > 0
		p.mu.Unlock()
		if admitted {
			break
		}
		select {
		case <-wait.Done():
			f.r.mu.Unlock()
			t.Fatal("PTY reader did not admit output")
		default:
			runtime.Gosched()
		}
	}
	// The normal writer cannot consume this admitted prefix while its owner
	// lock is held. Drain must retain it even if the normal writer wins later.
	f.r.mu.Unlock()
	offset, err := f.r.Drain(relayTestContext(t))
	if err != nil || offset != int64(len(data)) {
		t.Fatal(offset, err)
	}
	if err = <-write; err != nil {
		t.Fatal(err)
	}
	got := make([]byte, len(data))
	f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err = io.ReadFull(f.terminal, got); err != nil || !bytes.Equal(got, data) {
		t.Fatal("lost/duplicated fresh prefix", err)
	}
	if _, err = NewRelay(f.r.ctx, f.r.w); err == nil {
		t.Fatal("two PTY read owners admitted")
	}
}

func TestRelayBudgetAndOverflowFailBeforeOutput(t *testing.T) {
	for _, kind := range []string{"marks", "offset"} {
		t.Run(kind, func(t *testing.T) {
			f := relayFixtureFor(t)
			<-time.NewTimer(markCadence).C // Actual cadence, not a race guess.
			f.r.mu.Lock()
			if kind == "marks" {
				f.r.w.marks = markLimit
			} else {
				f.r.w.OutputThrough = math.MaxInt64 - 1
			}
			f.r.mu.Unlock()
			if _, err := f.slave.Write([]byte("xx")); err != nil {
				t.Fatal(err)
			}
			select {
			case <-f.r.Done():
			case <-relayTestContext(t).Done():
				t.Fatal("budget failure did not stop relay")
			}
			if f.r.Err() == nil {
				t.Fatal("budget failure lost cause")
			}
			f.terminal.SetReadDeadline(time.Now().Add(10 * time.Millisecond))
			var b [1]byte
			if n, _ := f.terminal.Read(b[:]); n != 0 {
				t.Fatal("unmarked/overflowing bytes escaped")
			}
		})
	}
}

func TestRelayInputHoldUsesSocketBackpressure(t *testing.T) {
	f := relayFixtureFor(t)
	s := f.r.w.channels
	if err := s.HoldInput(relayTestContext(t), 0); err != nil {
		t.Fatal(err)
	}
	if err := f.terminal.SetWriteBuffer(1024); err != nil {
		t.Fatal(err)
	}
	f.terminal.SetWriteDeadline(time.Now().Add(40 * time.Millisecond))
	data := bytes.Repeat([]byte("held"), 1<<20)
	n, err := f.terminal.Write(data)
	if err == nil || n == len(data) {
		t.Fatal("suspended socket did not exert backpressure", n, err)
	}
	if got := s.InputThrough(); got != 0 {
		t.Fatal("read continued during hold", got)
	}
	if err = s.ResumeInput(""); err != nil {
		t.Fatal(err)
	}
	waitInputCount(t, s, int64(n))
	select {
	case e := <-s.Events():
		t.Fatal("idle/held bytes retained as operation input", len(e.Input))
	default:
	}
}

func TestRelayWatermarkInputReleaseAndStaleEventDiscard(t *testing.T) {
	f := relayFixtureFor(t)
	s := f.r.w.channels
	done := make(chan error, 1)
	go func() { done <- s.HoldInput(relayTestContext(t), 5) }()
	if _, err := f.terminal.Write([]byte("old!!")); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if got := s.InputThrough(); got != 5 {
		t.Fatal(got)
	}
	if err := s.ResumeInput("one"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.terminal.Write([]byte("A")); err != nil {
		t.Fatal(err)
	}
	stale := receiveInput(t, s)
	if stale.InputOperation != "one" {
		t.Fatal("input did not bind operation")
	}
	if err := s.ResumeInput(""); err != nil {
		t.Fatal(err)
	}
	if err := s.HoldInput(relayTestContext(t), s.InputThrough()); err != nil {
		t.Fatal(err)
	}
	if err := s.ResumeInput("two"); err != nil {
		t.Fatal(err)
	}
	if err := f.r.ForwardInput(relayTestContext(t), stale); err != nil {
		t.Fatal(err)
	}
	if _, err := f.terminal.Write([]byte("B")); err != nil {
		t.Fatal(err)
	}
	fresh := receiveInput(t, s)
	if fresh.InputOperation != "two" {
		t.Fatal("new input wrong operation")
	}
	if err := f.r.ForwardInput(relayTestContext(t), fresh); err != nil {
		t.Fatal(err)
	}
	f.slave.SetReadDeadline(time.Now().Add(time.Second))
	var b [1]byte
	if _, err := io.ReadFull(f.slave, b[:]); err != nil || b[0] != 'B' {
		t.Fatal("stale input replayed", b, err)
	}
	if err := s.ResumeInput(""); err != nil {
		t.Fatal(err)
	}
	if err := s.HoldInput(relayTestContext(t), 4); err == nil {
		t.Fatal("regressing watermark admitted")
	}
}

func TestRelayInputHoldCancellationDoesNotResetEpoch(t *testing.T) {
	for _, tc := range []struct {
		name     string
		deadline bool
		want     error
	}{
		{
			name: "canceled",
			want: context.Canceled,
		},
		{
			name:     "unreachable-watermark-deadline",
			deadline: true,
			want:     context.DeadlineExceeded,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := relayFixtureFor(t)
			s := f.r.w.channels
			var ctx context.Context
			var cancel context.CancelFunc
			if tc.deadline {
				ctx, cancel = context.WithTimeout(context.Background(), 40*time.Millisecond)
			} else {
				ctx, cancel = context.WithCancel(relayTestContext(t))
			}
			defer cancel()
			done := make(chan error, 1)
			through := s.InputThrough() + 1
			go func() { done <- s.HoldInput(ctx, through) }()
			g := s.inputState()
			wait := relayTestContext(t)
			for {
				g.mu.Lock()
				held, wake := g.held, g.wake
				g.mu.Unlock()
				if held {
					break
				}
				select {
				case <-wake:
				case <-wait.Done():
					t.Fatal("input wait never armed")
				}
			}
			if !tc.deadline {
				cancel()
			}
			select {
			case err := <-done:
				if !errors.Is(err, tc.want) {
					t.Fatal(err)
				}
			case <-relayTestContext(t).Done():
				t.Fatal("input hold ignored original context")
			}
			select {
			case <-s.Done():
				t.Fatal("recoverable input wait failed channels", s.Err())
			default:
			}
			if s.Err() != nil {
				t.Fatal("recoverable input wait retained a fatal cause", s.Err())
			}
			if err := s.ResumeInput(""); err != nil {
				t.Fatal("resume idle discard", err)
			}
			stale := []byte("stale")
			if _, err := f.terminal.Write(stale); err != nil {
				t.Fatal(err)
			}
			waitInputCount(t, s, through+int64(len(stale))-1)
			select {
			case e := <-s.Events():
				t.Fatal("idle recovery retained stale input", e)
			default:
			}
			if err := s.HoldInput(relayTestContext(t), s.InputThrough()); err != nil {
				t.Fatal("later hold", err)
			}
			if err := s.ResumeInput("later"); err != nil {
				t.Fatal("later release", err)
			}
			fresh := []byte("fresh")
			if _, err := f.terminal.Write(fresh); err != nil {
				t.Fatal(err)
			}
			e := receiveInput(t, s)
			if e.InputOperation != "later" || !bytes.Equal(e.Input, fresh) {
				t.Fatal("later input was not bound to current operation", e)
			}
			if err := f.r.ForwardInput(relayTestContext(t), e); err != nil {
				t.Fatal("forward later input", err)
			}
			f.slave.SetReadDeadline(time.Now().Add(time.Second))
			got := make([]byte, len(fresh))
			if _, err := io.ReadFull(f.slave, got); err != nil || !bytes.Equal(got, fresh) {
				t.Fatal("stale input replayed or fresh input lost", got, err)
			}
			if err := s.ResumeInput(""); err != nil {
				t.Fatal("close later operation", err)
			}
		})
	}
}

func TestRelayTriggerAndControllerInputAreSerialized(t *testing.T) {
	f := relayFixtureFor(t)
	s := f.r.w.channels
	if err := s.HoldInput(relayTestContext(t), 0); err != nil {
		t.Fatal(err)
	}
	if err := f.r.SubmitTrigger(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	if err := s.ResumeInput("op"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.terminal.Write([]byte("input")); err != nil {
		t.Fatal(err)
	}
	e := receiveInput(t, s)
	if err := f.r.ForwardInput(relayTestContext(t), e); err != nil {
		t.Fatal(err)
	}
	f.slave.SetReadDeadline(time.Now().Add(time.Second))
	got := make([]byte, 7)
	if _, err := io.ReadFull(f.slave, got); err != nil || !bytes.Equal(got, []byte{0x18, 0x02, 'i', 'n', 'p', 'u', 't'}) {
		t.Fatal(got, err)
	}
}

func TestRelayConcurrentMarksAndPublicEventsHaveOneSequence(t *testing.T) {
	f := relayFixtureFor(t)
	ctx := relayTestContext(t)
	var workers sync.WaitGroup
	errors := make(chan error, 2)
	workers.Add(2)
	go func() {
		defer workers.Done()
		for i := 0; i < 30; i++ {
			if _, err := f.r.Drain(ctx); err != nil {
				errors <- err
				return
			}
		}
	}()
	go func() {
		defer workers.Done()
		for i := 0; i < 30; i++ {
			if err := f.r.w.channels.sendEvent(ctx, "diagnostic", func(h protocol.Header) protocol.Message {
				return protocol.Diagnostic{Header: h, Severity: "info", Code: "test", Message: "concurrent"}
			}); err != nil {
				errors <- err
				return
			}
		}
	}()
	workers.Wait()
	close(errors)
	for err := range errors {
		t.Fatal(err)
	}
	for seq := int64(2); seq < 62; seq++ {
		m := nextRelayFrame(t, f)
		var got int64
		switch x := m.(type) {
		case *protocol.OutputMark:
			got = x.Seq
		case *protocol.Diagnostic:
			got = x.Seq
		default:
			t.Fatal(fmt.Sprintf("unexpected %T", m))
		}
		if got != seq {
			t.Fatal("public sequences raced", got, seq)
		}
	}
}

func TestRelayEOFAndCloseRetainDescriptorOwnership(t *testing.T) {
	f := relayFixtureFor(t)
	if err := f.slave.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-f.r.ReadEnded():
	case <-relayTestContext(t).Done():
		t.Fatal("PTY end not reported")
	}
	if f.r.ReadError() == nil {
		t.Fatal("PTY EOF fact lost")
	}
	if _, err := f.r.Drain(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Close(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	if _, err := f.r.w.PTY.SyscallConn(); err != nil {
		t.Fatal("relay closed borrowed master", err)
	}
	if _, err := NewRelay(f.r.w.channels.ctx, f.r.w); err == nil {
		t.Fatal("relay restart reset session budget")
	}
	if err := f.r.SubmitTrigger(relayTestContext(t)); err == nil {
		t.Fatal("closed relay wrote borrowed descriptor")
	}
	if _, err := f.r.Drain(relayTestContext(t)); err == nil {
		t.Fatal("closed relay restarted its reader")
	}
}

func waitKernelQueue(t *testing.T, file *os.File, socket *net.TCPConn, request uintptr) {
	t.Helper()
	ctx := relayTestContext(t)
	for {
		var count int32
		var err error
		if file != nil {
			err = ptyIOCTL(file, request, unsafe.Pointer(&count))
		} else {
			raw, e := socket.SyscallConn()
			if e != nil {
				t.Fatal(e)
			}
			var native error
			err = raw.Control(func(fd uintptr) {
				_, _, e := syscall.Syscall(syscall.SYS_IOCTL, fd, request, uintptr(unsafe.Pointer(&count)))
				if e != 0 {
					native = e
				}
			})
			if err == nil {
				err = native
			}
		}
		if err != nil {
			t.Fatal(err)
		}
		if count > 0 {
			return
		}
		select {
		case <-ctx.Done():
			t.Fatal("kernel I/O queue did not make progress")
		default:
			runtime.Gosched()
		}
	}
}

func TestRelayDrainCapsAlreadyBlockedWriterWithOriginalEpoch(t *testing.T) {
	f := relayFixtureFor(t)
	f.terminal.SetReadBuffer(4096)
	f.r.w.channels.terminal.SetWriteBuffer(1024)
	f.slave.SetWriteDeadline(time.Now().Add(2 * time.Second))
	written := make(chan error, 1)
	go func() { _, err := f.slave.Write(bytes.Repeat([]byte("output"), 1<<18)); written <- err }()
	waitKernelQueue(t, nil, f.r.w.channels.terminal, syscall.TIOCOUTQ)
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Millisecond)
	defer cancel()
	started := time.Now()
	if _, err := f.r.Drain(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("lost original drain epoch", err)
	}
	if time.Since(started) > time.Second {
		t.Fatal("drain extended its original phase deadline")
	}
	fatal(t, f.r.w.channels)
	f.slave.SetWriteDeadline(time.Now())
	select {
	case <-written:
	case <-relayTestContext(t).Done():
		t.Fatal("retained workload-side test writer")
	}
}

func TestRelayBoundaryWithoutDeadlineJoinsBlockedOutputOnCancel(t *testing.T) {
	f := relayFixtureFor(t)
	f.terminal.SetReadBuffer(4096)
	f.r.w.channels.terminal.SetWriteBuffer(1024)
	f.slave.SetWriteDeadline(time.Now().Add(2 * time.Second))
	written := make(chan error, 1)
	go func() { _, err := f.slave.Write(bytes.Repeat([]byte("output"), 1<<18)); written <- err }()
	waitKernelQueue(t, nil, f.r.w.channels.terminal, syscall.TIOCOUTQ)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := f.r.boundary(ctx, true, nil); done <- err }()
	// Wait for the existing boundary owner to take over the blocked writer.
	wait := relayTestContext(t)
	for {
		f.r.phaseMu.Lock()
		active := f.r.phase == ctx
		f.r.phaseMu.Unlock()
		if active {
			break
		}
		select {
		case err := <-done:
			t.Fatal("deadline-free boundary did not acquire its writer", err)
		case <-wait.Done():
			t.Fatal("boundary did not take over the blocked writer")
		default:
			runtime.Gosched()
		}
	}
	cancel()
	if err := finishCompletion(t, done); !errors.Is(err, context.Canceled) {
		t.Fatal("boundary lost caller cancellation", err)
	}
	fatal(t, f.r.w.channels)
	f.slave.SetWriteDeadline(time.Now())
	select {
	case <-written:
	case <-relayTestContext(t).Done():
		t.Fatal("retained workload-side test writer")
	}
}

func TestRelayPTYSerializationDoesNotReplaceEarlierWriterDeadline(t *testing.T) {
	f := relayFixtureFor(t)
	s := f.r.w.channels
	if err := s.HoldInput(relayTestContext(t), 0); err != nil {
		t.Fatal(err)
	}
	if err := s.ResumeInput("op"); err != nil {
		t.Fatal(err)
	}
	data := bytes.Repeat([]byte("i"), 32768)
	if _, err := f.terminal.Write(data); err != nil {
		t.Fatal(err)
	}
	e := receiveInput(t, s)
	// Keep one actual PTY write active beyond the second writer's deadline.
	// Kernel slave input proves it admitted bytes; no sleep chooses the race.
	first := make(chan error, 1)
	go func() { first <- f.r.ForwardInput(relayTestContext(t), e) }()
	waitKernelQueue(t, f.slave, nil, syscall.TIOCINQ)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	if err := f.r.SubmitTrigger(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("second writer did not retain its own deadline", err)
	}
	select {
	case err := <-first:
		t.Fatal("second writer disturbed earlier PTY deadline", err)
	default:
	}
	if err := s.ResumeInput(""); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-first:
		if err != nil {
			t.Fatal("eligibility closure became a new input failure", err)
		}
	case <-relayTestContext(t).Done():
		t.Fatal("input-close did not join the pending PTY writer")
	}
}

func TestRelayInputAPIsRejectInvalidBoundaries(t *testing.T) {
	f := relayFixtureFor(t)
	s := f.r.w.channels
	if err := s.HoldInput(context.Background(), 0); err == nil {
		t.Fatal("unbounded input wait admitted")
	}
	ctx, cancel := context.WithCancel(relayTestContext(t))
	cancel()
	if err := s.HoldInput(ctx, 0); !errors.Is(err, context.Canceled) {
		t.Fatal("expired input epoch lost cause", err)
	}
	if err := s.ResumeInput("op"); err == nil {
		t.Fatal("input opened without held watermark")
	}
	if err := s.ResumeInput("bad id"); err == nil {
		t.Fatal("invalid operation ID admitted")
	}
	if err := s.HoldInput(relayTestContext(t), 0); err != nil {
		t.Fatal(err)
	}
	if err := s.HoldInput(relayTestContext(t), 0); err == nil {
		t.Fatal("duplicate input hold admitted")
	}
}

func TestRelayCloseDuringOutputKeepsChannels(t *testing.T) {
	for i := 0; i < 16; i++ {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			f := relayFixtureFor(t)
			f.terminal.SetReadBuffer(4096)
			f.r.w.channels.terminal.SetWriteBuffer(1024)
			f.slave.SetWriteDeadline(time.Now().Add(2 * time.Second))
			written := make(chan error, 1)
			go func() { _, err := f.slave.Write(bytes.Repeat([]byte("output"), 1<<18)); written <- err }()
			t.Cleanup(func() {
				f.slave.SetWriteDeadline(time.Now())
				select {
				case <-written:
				case <-relayTestContext(t).Done():
					t.Error("retained workload-side writer")
				}
			})
			waitKernelQueue(t, nil, f.r.w.channels.terminal, syscall.TIOCOUTQ)
			if err := f.r.Close(relayTestContext(t)); err != nil {
				t.Fatal("normal close became fatal", err)
			}
			select {
			case <-f.r.w.channels.Done():
				t.Fatal("normal close destroyed channels", f.r.w.channels.Err())
			default:
			}
		})
	}
}

func TestRelayCloseRetainsTransportFailure(t *testing.T) {
	f := relayFixtureFor(t)
	want := errors.New("genuine transport failure")
	f.r.w.channels.fail(want)
	if err := f.r.Close(relayTestContext(t)); !errors.Is(err, want) {
		t.Fatal("close hid an existing transport failure", err)
	}
}

type cancelAtDeadline struct {
	context.Context
	intercept func()
}

func (c cancelAtDeadline) Deadline() (time.Time, bool) {
	c.intercept()
	return c.Context.Deadline()
}

func TestRelayTriggerCancellationCannotBeUndoneByDeadline(t *testing.T) {
	f := relayFixtureFor(t)
	raw, err := f.r.w.PTY.SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	// Fill the actual slave input queue without consuming it. The deadline
	// context supplies the exact crossing between the relay precheck and the
	// writer's deadline assignment, after the old one-shot interrupt finishes.
	var writeErr error
	for total := 0; total < 1<<20; {
		n := 0
		if err := raw.Control(func(fd uintptr) { n, writeErr = syscall.Write(int(fd), bytes.Repeat([]byte("i"), 4096)) }); err != nil {
			t.Fatal(err)
		}
		if errors.Is(writeErr, syscall.EAGAIN) {
			break
		}
		if writeErr != nil || n == 0 {
			t.Fatal("fill PTY input", n, writeErr)
		}
		total += n
	}
	if !errors.Is(writeErr, syscall.EAGAIN) {
		t.Fatal("PTY input did not reach backpressure")
	}
	base, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
	defer cancel()
	var once sync.Once
	ctx := cancelAtDeadline{Context: base, intercept: func() { once.Do(func() { f.r.cancel(); <-f.r.Done() }) }}
	started := time.Now()
	if err := f.r.SubmitTrigger(ctx); !errors.Is(err, context.Canceled) {
		t.Fatal("trigger lost relay cancellation", err)
	}
	if time.Since(started) > 250*time.Millisecond {
		t.Fatal("trigger waited for the later caller deadline")
	}
}

func TestRelayOutputBudgetIncludesExistingPrefix(t *testing.T) {
	f := relayFixtureFor(t)
	f.r.mu.Lock()
	f.r.w.OutputThrough = (8 << 30) - 3
	err := f.r.writeOutput(relayTestContext(t), []byte("abc"))
	if err != nil {
		f.r.mu.Unlock()
		t.Fatal(err)
	}
	got := make([]byte, 3)
	if _, err := io.ReadFull(f.terminal, got); err != nil || string(got) != "abc" {
		f.r.mu.Unlock()
		t.Fatal(got, err)
	}
	err = f.r.writeOutput(relayTestContext(t), []byte("x"))
	offset := f.r.w.OutputThrough
	f.r.mu.Unlock()
	if err == nil || offset != 8<<30 {
		t.Fatal("output cap not enforced", offset, err)
	}
	_ = f.terminal.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	if n, err := f.terminal.Read(got); n != 0 || !os.IsTimeout(err) {
		t.Fatal("over-budget bytes escaped", n, err)
	}
}

func TestRelayRejectsWholeCrossingChunk(t *testing.T) {
	f := relayFixtureFor(t)
	f.r.mu.Lock()
	f.r.w.OutputThrough = (8 << 30) - 1
	f.r.mu.Unlock()
	if _, err := f.slave.Write([]byte("crossing")); err != nil {
		t.Fatal(err)
	}
	select {
	case <-f.r.Done():
	case <-relayTestContext(t).Done():
		t.Fatal("crossing did not fail relay")
	}
	if f.r.Err() == nil || f.r.w.channels.Err() == nil || f.r.w.OutputThrough != (8<<30)-1 {
		t.Fatal("crossing not fatal before write", f.r.Err(), f.r.w.OutputThrough)
	}
	got := make([]byte, 8)
	_ = f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if n, err := f.terminal.Read(got); n != 0 || !errors.Is(err, io.EOF) {
		t.Fatal("crossing bytes escaped", n, err)
	}
}

func TestRelayCloseReturnsUsableBorrowedPTY(t *testing.T) {
	f := relayFixtureFor(t)
	if err := f.r.Close(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	read := make(chan error, 1)
	go func() {
		var b [1]byte
		_, err := io.ReadFull(f.r.w.PTY, b[:])
		if err == nil && b[0] != 'r' {
			err = fmt.Errorf("wrong byte %q", b)
		}
		read <- err
	}()
	if _, err := f.slave.Write([]byte("r")); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-read:
		if err != nil {
			t.Fatal("borrowed reader retained relay deadline", err)
		}
	case <-relayTestContext(t).Done():
		_ = f.r.w.PTY.SetReadDeadline(time.Now())
		<-read
		t.Fatal("borrowed reader did not complete")
	}
	if _, err := f.r.w.PTY.Write([]byte("w")); err != nil {
		t.Fatal("borrowed writer retained relay deadline", err)
	}
	var b [1]byte
	_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(f.slave, b[:]); err != nil || b[0] != 'w' {
		t.Fatal("borrowed writer did not reach slave", b, err)
	}
}

func TestRelayClosePreservesAdmittedOutputForWorkloadDrain(t *testing.T) {
	f := relayFixtureFor(t)
	payload := []byte("admitted before the ownership handoff\n")
	f.r.mu.Lock()
	f.r.w.marks = markLimit - 1
	if _, err := f.slave.Write(payload); err != nil {
		f.r.mu.Unlock()
		t.Fatal(err)
	}
	wait := relayTestContext(t)
	for {
		f.r.reader.mu.Lock()
		n := len(f.r.reader.data)
		f.r.reader.mu.Unlock()
		if n > 0 {
			break
		}
		if wait.Err() != nil {
			f.r.mu.Unlock()
			t.Fatal("reader did not admit output")
		}
		runtime.Gosched()
	}
	closed := make(chan error, 1)
	go func() { closed <- f.r.Close(relayTestContext(t)) }()
	<-f.r.ctx.Done()
	f.r.mu.Unlock()
	if err := <-closed; err != nil {
		t.Fatal(err)
	}
	_ = f.slave.Close()
	ctx := relayTestContext(t)
	_ = f.r.w.PTY.SetReadDeadline(time.Now().Add(time.Second))
	if err := f.r.w.drainTerminal(ctx); err != nil {
		t.Fatal(err)
	}
	mark, ok := nextRelayFrame(t, f).(*protocol.OutputMark)
	if !ok || mark.Offset != int64(len(payload)) || mark.Stream != "pty" || f.r.w.marks != markLimit {
		t.Fatal("missing final mark or reset session mark budget", mark, f.r.w.marks)
	}
	got := make([]byte, len(payload))
	_ = f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(f.terminal, got); err != nil || !bytes.Equal(got, payload) {
		t.Fatal("admitted bytes lost", got, err)
	}
	if f.r.w.OutputThrough != int64(len(payload)) || len(f.r.w.pendingPTY) != 0 {
		t.Fatal("wrong final accounting", f.r.w.OutputThrough, len(f.r.w.pendingPTY))
	}
}

func TestRelayDrainCannotRestartReaderAfterClose(t *testing.T) {
	f := relayFixtureFor(t)
	f.r.phaseMu.Lock()
	done := make(chan error, 1)
	go func() { _, err := f.r.Drain(relayTestContext(t)); done <- err }()
	ctx := relayTestContext(t)
	for len(f.r.barrier) != 0 && ctx.Err() == nil {
		runtime.Gosched()
	}
	if ctx.Err() != nil {
		f.r.phaseMu.Unlock()
		t.Fatal("drain did not acquire its boundary")
	}
	if err := f.r.Close(relayTestContext(t)); err != nil {
		f.r.phaseMu.Unlock()
		t.Fatal(err)
	}
	f.r.phaseMu.Unlock()
	err := <-done
	f.r.mu.Lock()
	p := f.r.reader
	f.r.mu.Unlock()
	if p != nil {
		_ = p.join(f.r.w.PTY)
	} // also bound cleanup of the old-source fault overlay
	if !errors.Is(err, context.Canceled) || p != nil || f.r.w.channels.Err() != nil {
		t.Fatal("drain restarted or failed transport after close", err, p != nil, f.r.w.channels.Err())
	}
}

func TestRelayClosePreservesBlockedOutputSuffix(t *testing.T) {
	f := relayFixtureFor(t)
	_ = f.r.w.channels.terminal.SetWriteBuffer(1024)
	payload := bytes.Repeat([]byte("preserve-output-"), 200000)
	type written struct {
		n   int
		err error
	}
	producer := make(chan written, 1)
	go func() { n, err := f.slave.Write(payload); producer <- written{n, err} }()
	ctx := relayTestContext(t)
	busy := time.Time{}
	for ctx.Err() == nil {
		if f.r.mu.TryLock() {
			f.r.mu.Unlock()
			busy = time.Time{}
		} else if busy.IsZero() {
			busy = time.Now()
		} else if time.Since(busy) > 30*time.Millisecond {
			break
		}
		runtime.Gosched()
	}
	if err := ctx.Err(); err != nil {
		_ = f.slave.SetWriteDeadline(time.Now())
		<-producer
		t.Fatal("output did not block", err)
	}
	if err := f.r.Close(ctx); err != nil {
		_ = f.slave.SetWriteDeadline(time.Now())
		<-producer
		t.Fatal(err)
	}
	_ = f.slave.SetWriteDeadline(time.Now())
	produced := <-producer
	_ = f.slave.Close()
	if produced.n == 0 || len(f.r.w.pendingPTY) == 0 {
		t.Fatal("no pending output to exercise", produced, len(f.r.w.pendingPTY))
	}
	consumed := make(chan error, 1)
	go func() {
		got := make([]byte, produced.n)
		deadline, _ := ctx.Deadline()
		_ = f.terminal.SetReadDeadline(deadline)
		_, err := io.ReadFull(f.terminal, got)
		if err == nil && !bytes.Equal(got, payload[:produced.n]) {
			err = fmt.Errorf("stream bytes changed")
		}
		consumed <- err
	}()
	_ = f.r.w.PTY.SetReadDeadline(time.Now().Add(time.Second))
	drainErr := f.r.w.drainTerminal(ctx)
	readErr := <-consumed
	if drainErr != nil || readErr != nil || f.r.w.OutputThrough != int64(produced.n) {
		t.Fatal("interrupted output lost or duplicated", produced.n, f.r.w.OutputThrough, drainErr, readErr)
	}
}

func TestRelayFinalDrainDoesNotResetMarkBudget(t *testing.T) {
	f := relayFixtureFor(t)
	f.r.mu.Lock()
	f.r.w.marks = markLimit
	f.r.mu.Unlock()
	if err := f.r.Close(relayTestContext(t)); err != nil {
		t.Fatal(err)
	}
	_ = f.slave.Close()
	ctx := relayTestContext(t)
	deadline, _ := ctx.Deadline()
	_ = f.r.w.PTY.SetReadDeadline(deadline)
	if err := f.r.w.drainTerminal(ctx); err == nil || f.r.w.marks != markLimit {
		t.Fatal("final drain reset the existing mark budget", err, f.r.w.marks)
	}
}
