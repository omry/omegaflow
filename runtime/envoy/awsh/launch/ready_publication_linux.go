//go:build linux

package launch

import (
	"context"
	"errors"
	"os"
	"sync"
	"syscall"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
)

// readyPublication orders startup failure against acceptance of the last ready
// byte by the kernel. The mutex is never held across poll waits or descriptor close.
type readyPublication struct {
	file                   *os.File
	raw                    syscall.RawConn
	mu                     sync.Mutex
	remaining              int
	committed, interrupted bool
}

func newReadyPublication(file *os.File) (*readyPublication, error) {
	raw, err := file.SyscallConn()
	if err != nil {
		return nil, err
	}
	return &readyPublication{file: file, raw: raw}, nil
}
func (p *readyPublication) write(ctx context.Context, frame []byte) error {
	p.mu.Lock()
	p.remaining = len(frame)
	p.mu.Unlock()
	return helper.WriteControl(ctx, p, frame)
}
func (p *readyPublication) SetWriteDeadline(t time.Time) error { return p.file.SetWriteDeadline(t) }
func (p *readyPublication) Write(data []byte) (n int, err error) {
	pollErr := p.raw.Write(func(fd uintptr) bool {
		p.mu.Lock()
		defer p.mu.Unlock()
		if p.interrupted {
			err = os.ErrClosed
			return true
		}
		n, err = syscall.Write(int(fd), data)
		if n < 0 {
			n = 0
		}
		if err == syscall.EAGAIN || err == syscall.EINTR {
			return false
		}
		p.remaining -= n
		if p.remaining == 0 && err == nil {
			p.committed = true
		}
		return true
	})
	return n, errors.Join(err, pollErr)
}

// interrupt is false only after complete ready publication. The caller may
// close descriptors after this method returns; Close under mu can deadlock RawWrite.
func (p *readyPublication) interrupt() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.committed {
		return false
	}
	p.interrupted = true
	return true
}
func (p *readyPublication) published() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.committed
}
