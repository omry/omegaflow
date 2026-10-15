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
	"sync"
	"syscall"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type privateResult struct {
	message protocol.PrivateMessage
	err     error
}

// privateResults is a bounded I/O pump, shared by start and completion. Reader
// retains buffered concatenated frames; no other consumer reads the pipe.
type privateResults struct {
	w       *Workload
	ctx     context.Context
	cancel  context.CancelFunc
	events  chan privateResult
	fatal   chan privateResult
	done    chan struct{}
	stop    func()
	once    sync.Once
	raw     syscall.RawConn
	mu      sync.Mutex
	read    bool
	changed chan struct{}
}

func readResults(ctx context.Context, w *Workload) (*privateResults, error) {
	if w == nil || w.Result == nil || w.Reader == nil || ctx.Err() != nil {
		return nil, fmt.Errorf("private reader requires a live workload")
	}
	if !w.privateOwned.CompareAndSwap(false, true) {
		return nil, fmt.Errorf("private result reader already owned")
	}
	if err := w.Result.SetReadDeadline(time.Time{}); err != nil {
		w.privateOwned.Store(false)
		return nil, err
	}
	raw, err := w.Result.SyscallConn()
	if err != nil {
		w.privateOwned.Store(false)
		return nil, err
	}
	p := &privateResults{w: w, raw: raw, events: make(chan privateResult, 1), fatal: make(chan privateResult, 1), done: make(chan struct{}), changed: make(chan struct{}, 1)}
	// Keep startup's already-buffered bytes, but observe every subsequent kernel
	// read before it can disappear from pipe readiness into the decoder.
	buffered, _ := w.Reader.Peek(w.Reader.Buffered())
	prefix := append([]byte(nil), buffered...)
	p.read = len(prefix) != 0
	w.Reader = bufio.NewReader(io.MultiReader(bytes.NewReader(prefix), p))
	p.ctx, p.cancel = context.WithCancel(ctx)
	p.stop = joinedAfterFunc(p.ctx, func() { _ = w.Result.SetReadDeadline(time.Now()) })
	stopChannels := joinedAfterFunc(w.channels.ctx, p.cancel)
	go func() {
		defer close(p.done)
		defer stopChannels()
		for {
			m, err := protocol.ReadPrivate(w.Reader, protocol.AwshToEnvoy)
			result := privateResult{m, err}
			if _, ok := m.(*protocol.PrivateProtocolError); ok {
				select {
				case p.fatal <- result:
				case <-p.ctx.Done():
					return
				}
			} else {
				select {
				case p.events <- result:
				case <-p.ctx.Done():
					return
				}
			}
			p.mu.Lock()
			p.read = w.Reader.Buffered() != 0
			p.mu.Unlock()
			select {
			case p.changed <- struct{}{}:
			default:
			}
			if err != nil {
				return
			}
		}
	}()
	return p, nil
}

// Read is used only by the existing private pump. The lock covers the actual
// nonblocking read and its visibility marker, never an idle poller wait.
func (p *privateResults) Read(b []byte) (n int, err error) {
	pollErr := p.raw.Read(func(fd uintptr) bool {
		p.mu.Lock()
		defer p.mu.Unlock()
		for {
			n, err = syscall.Read(int(fd), b)
			if err != syscall.EINTR {
				break
			}
		}
		if err == syscall.EAGAIN {
			return false
		}
		if n < 0 {
			n = 0
		}
		p.read = true
		if n == 0 && err == nil {
			err = io.EOF
		}
		return true
	})
	if pollErr != nil {
		return 0, pollErr
	}
	return n, err
}

// publicationReady is the stream observation point for recoverable result
// selection. It sees both an unscheduled decoded fact and unread pipe data/EOF.
// No second decoder or private reader is created.
func (p *privateResults) publicationReady() (bool, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.read || len(p.events) != 0 || len(p.fatal) != 0 {
		return false, nil
	}
	var count uintptr
	var pollErr syscall.Errno
	err := p.raw.Control(func(fd uintptr) {
		poll := struct {
			fd              int32
			events, revents int16
		}{fd: int32(fd), events: 1} // Linux pollfd; POLLIN also reports HUP/ERR.
		zero := syscall.Timespec{}
		for {
			count, _, pollErr = syscall.Syscall6(syscall.SYS_PPOLL, uintptr(unsafe.Pointer(&poll)), 1, uintptr(unsafe.Pointer(&zero)), 0, 0, 0)
			if pollErr != syscall.EINTR {
				break
			}
		}
	})
	if err != nil {
		return false, err
	}
	if pollErr != 0 {
		return false, pollErr
	}
	return count == 0, nil
}

func (p *privateResults) close(ctx context.Context) error {
	p.once.Do(p.cancel)
	select {
	case <-p.done:
		p.stop()
		err := p.w.Result.SetReadDeadline(time.Time{})
		if err != nil {
			// SetReadDeadline can return the poller's unwrapped closed-file
			// error. Stat proves the File itself was closed after reader join.
			if _, stateErr := p.w.Result.Stat(); errors.Is(stateErr, os.ErrClosed) {
				return nil
			}
		}
		return err
	case <-ctx.Done():
		return errors.Join(ctx.Err(), p.ctx.Err())
	}
}
