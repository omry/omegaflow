//go:build linux

package launch

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type publicationReturnBarrier struct {
	syscall.RawConn
	entered, release chan struct{}
	once             sync.Once
}

type publicationPollObservation struct {
	syscall.RawConn
	pending chan struct{}
	once    sync.Once
}

func (r *publicationPollObservation) Write(f func(uintptr) bool) error {
	return r.RawConn.Write(func(fd uintptr) bool {
		ready := f(fd)
		if !ready {
			r.once.Do(func() { close(r.pending) })
		}
		return ready
	})
}

func (r *publicationReturnBarrier) Write(f func(uintptr) bool) error {
	err := r.RawConn.Write(f)
	r.once.Do(func() { close(r.entered); <-r.release })
	return err
}
func publicationPipe(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	var fds [2]int
	if err := syscall.Pipe2(fds[:], syscall.O_NONBLOCK|syscall.O_CLOEXEC); err != nil {
		t.Fatal(err)
	}
	r, w := os.NewFile(uintptr(fds[0]), "read"), os.NewFile(uintptr(fds[1]), "write")
	t.Cleanup(func() { r.Close(); w.Close() })
	return r, w
}
func readyFrame(t *testing.T) []byte {
	t.Helper()
	frame, err := protocol.EncodePrivate(protocol.PrivateReady{AwshPID: 123, ShellPID: 456, CWD: "/"}, protocol.AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	return frame
}
func finishPublished(t *testing.T, p *readyPublication, signal bool, prior error) error {
	t.Helper()
	c := &selectedChild{done: make(chan struct{})}
	close(c.done)
	s := &Session{publication: p, child: c, Signals: make(chan os.Signal, 1)}
	if signal {
		s.Signals <- syscall.SIGUSR1
	}
	done := make(chan struct{})
	close(done)
	return s.finishStartup(make(chan struct{}), done, make(chan error, 1), prior)
}
func TestReadyPublicationKernelPublicationHandoff(t *testing.T) {
	for _, scenario := range []string{"shell-exit", "unexpected-signal", "cancellation"} {
		t.Run(scenario, func(t *testing.T) {
			r, w := publicationPipe(t)
			p, err := newReadyPublication(w)
			if err != nil {
				t.Fatal(err)
			}
			hold := &publicationReturnBarrier{RawConn: p.raw, entered: make(chan struct{}), release: make(chan struct{})}
			p.raw = hold
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			result := make(chan error, 1)
			go func() { result <- p.write(ctx, readyFrame(t)) }()
			<-hold.entered
			if _, err := protocol.ReadPrivate(bufio.NewReader(r), protocol.AwshToEnvoy); err != nil {
				t.Fatal(err)
			}
			child := exec.Command("/bin/sleep", "60")
			if err := child.Start(); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = child.Process.Kill() })
			if err := child.Process.Kill(); err != nil {
				t.Fatal(err)
			}
			if err := child.Wait(); err == nil {
				t.Fatal("actual child did not end by signal")
			}
			if p.interrupt() {
				t.Fatal("published ready selected startup shell failure")
			}
			if scenario == "cancellation" {
				cancel()
			}
			close(hold.release)
			err = <-result
			if scenario == "cancellation" {
				if !errors.Is(err, context.Canceled) {
					t.Fatal("complete ready discarded cancellation", err)
				}
			} else if err != nil {
				t.Fatal(err)
			}
			err = finishPublished(t, p, scenario == "unexpected-signal", err)
			if scenario == "shell-exit" && err != nil {
				t.Fatal("post-ready reap became launch failure", err)
			}
			if scenario == "unexpected-signal" && err == nil {
				t.Fatal("post-ready unexpected release was lost")
			}
			if scenario == "cancellation" && !errors.Is(err, context.Canceled) {
				t.Fatal("handoff lost cancellation", err)
			}
		})
	}
}
func TestReadyPublicationBlockedWriteStillInterrupts(t *testing.T) {
	_, w := publicationPipe(t)
	raw, _ := w.SyscallConn()
	if err := raw.Control(func(fd uintptr) {
		for {
			_, err := syscall.Write(int(fd), bytes.Repeat([]byte("p"), 4096))
			if err == syscall.EAGAIN {
				return
			}
			if err != nil {
				t.Error(err)
				return
			}
		}
	}); err != nil {
		t.Fatal(err)
	}
	p, err := newReadyPublication(w)
	if err != nil {
		t.Fatal(err)
	}
	poll := &publicationPollObservation{RawConn: p.raw, pending: make(chan struct{})}
	p.raw = poll
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- p.write(ctx, readyFrame(t)) }()
	select {
	case <-poll.pending: // The real nonblocking write returned EAGAIN.
	case <-ctx.Done():
		t.Fatal("ready writer never reached the full-pipe poll wait")
	}
	if !p.interrupt() {
		t.Fatal("unpublished write committed")
	}
	_ = w.Close()
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("interrupted ready succeeded")
		}
	case <-time.After(200 * time.Millisecond):
		t.Fatal("close did not wake and join ready writer")
	}
}
func TestReadyPublicationPartialAndOriginalDeadline(t *testing.T) {
	_, w := publicationPipe(t)
	p, err := newReadyPublication(w)
	if err != nil {
		t.Fatal(err)
	}
	p.remaining = 1 << 20
	n, err := p.Write(make([]byte, p.remaining))
	if err != nil || n <= 0 || n >= 1<<20 || p.published() {
		t.Fatal("partial kernel write committed ready", n, err)
	}
	if err := w.SetWriteDeadline(time.Now().Add(-time.Second)); err != nil {
		t.Fatal(err)
	}
	_, err = p.Write(make([]byte, 10))
	if !errors.Is(err, os.ErrDeadlineExceeded) {
		t.Fatal("write did not keep original deadline", err)
	}
	if !p.interrupt() {
		t.Fatal("partial ready committed")
	}
}
func TestReadyPublicationInterruptBeforeFirstByte(t *testing.T) {
	r, w := publicationPipe(t)
	p, err := newReadyPublication(w)
	if err != nil {
		t.Fatal(err)
	}
	if !p.interrupt() {
		t.Fatal("empty publication committed")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := p.write(ctx, readyFrame(t)); err == nil {
		t.Fatal("ready written after accepted interruption")
	}
	_ = w.Close()
	b, err := io.ReadAll(r)
	if err != nil || len(b) != 0 {
		t.Fatal("ready bytes leaked", b, err)
	}
}

// readyPublicationForTest is installed by the startup runner's test-only Go
// overlay. Production has no hook or configuration path to select this barrier.
var readyPublicationTestScenario string

func readyPublicationForTest(p *readyPublication) *readyPublication {
	if readyPublicationTestScenario == "post-ready-exit" {
		p.raw = &publicationControlBarrier{RawConn: p.raw}
	}
	return p
}

type publicationControlBarrier struct{ syscall.RawConn }

func (b *publicationControlBarrier) Write(f func(uintptr) bool) error {
	err := b.RawConn.Write(f)
	if err != nil {
		return err
	}
	fd, err := syscall.Dup(3)
	if err != nil {
		return err
	}
	if err = syscall.SetNonblock(fd, true); err != nil {
		syscall.Close(fd)
		return err
	}
	release := os.NewFile(uintptr(fd), "test-ready-release")
	defer release.Close()
	if err = release.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		return err
	}
	var token [1]byte
	_, err = io.ReadFull(release, token[:])
	return err
}
