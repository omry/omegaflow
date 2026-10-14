//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"sync"
	"syscall"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const markCadence = 10 * time.Millisecond
const markLimit = 1000000

type relayReader struct {
	mu                       sync.Mutex
	data                     []byte
	err                      error
	wake, resume, stop, done chan struct{}
	once                     sync.Once
}

func readRelay(master *os.File) (*relayReader, error) {
	if err := master.SetReadDeadline(time.Time{}); err != nil {
		return nil, err
	}
	p := &relayReader{wake: make(chan struct{}, 1), resume: make(chan struct{}, 1), stop: make(chan struct{}), done: make(chan struct{})}
	go func() {
		defer close(p.done)
		var buffer [32768]byte
		for {
			n, err := master.Read(buffer[:])
			select {
			case <-p.stop:
				if os.IsTimeout(err) {
					err = nil
				}
			default:
			}
			p.mu.Lock()
			p.data = append([]byte(nil), buffer[:n]...)
			p.err = err
			p.mu.Unlock()
			select {
			case p.wake <- struct{}{}:
			default:
			}
			if err != nil {
				return
			}
			select {
			case <-p.resume:
			case <-p.stop:
				return
			}
		}
	}()
	return p, nil
}

func (p *relayReader) take() ([]byte, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	data, err := p.data, p.err
	p.data = nil
	return data, err
}

func (p *relayReader) join(master *os.File) error {
	var err error
	p.once.Do(func() { close(p.stop); err = master.SetReadDeadline(time.Now()) })
	<-p.done
	return err
}

// Relay borrows the validated Workload descriptors. Its one reader admits at
// most one chunk ahead of the output owner. The caller retains lifecycle and
// private-result decisions; a PTY EOF is a fact, not an operation result.
type Relay struct {
	w                              *Workload
	ctx                            context.Context
	cancel                         context.CancelFunc
	mu, phaseMu                    sync.Mutex
	phase                          context.Context
	reader                         *relayReader
	wake, done, readEnded, closing chan struct{}
	barrier, inputWriter           chan struct{}
	err, readErr                   error
	marks                          int
	lastMark                       time.Duration
	ended                          bool
	stopOnce                       sync.Once
}

func NewRelay(ctx context.Context, w *Workload) (*Relay, error) {
	if ctx.Err() != nil || w == nil || w.channels == nil || w.PTY == nil || w.ReadyAt.IsZero() || w.ReadyAt.After(time.Now()) || w.OutputThrough < 0 {
		return nil, fmt.Errorf("relay requires a ready workload")
	}
	if !w.relayOwned.CompareAndSwap(false, true) {
		return nil, fmt.Errorf("PTY relay already owned")
	}
	r := &Relay{w: w, wake: make(chan struct{}, 1), done: make(chan struct{}), readEnded: make(chan struct{}), closing: make(chan struct{}), barrier: make(chan struct{}, 1), inputWriter: make(chan struct{}, 1)}
	r.ctx, r.cancel = context.WithCancel(ctx)
	var err error
	r.reader, err = readRelay(w.PTY)
	if err != nil {
		r.cancel()
		w.relayOwned.Store(false)
		return nil, err
	}
	r.barrier <- struct{}{}
	r.inputWriter <- struct{}{}
	g := w.channels.inputState()
	g.mu.Lock()
	g.enabled = true
	g.mu.Unlock()
	go r.run()
	return r, nil
}

func (r *Relay) Done() <-chan struct{}      { return r.done }
func (r *Relay) ReadEnded() <-chan struct{} { return r.readEnded }
func (r *Relay) Err() error                 { r.mu.Lock(); defer r.mu.Unlock(); return r.err }
func (r *Relay) ReadError() error           { r.mu.Lock(); defer r.mu.Unlock(); return r.readErr }

func (r *Relay) mark(ctx context.Context, force bool) error {
	elapsed := time.Since(r.w.ReadyAt)
	if elapsed < r.lastMark {
		return fmt.Errorf("output mark time regressed")
	}
	if !force && elapsed-r.lastMark < markCadence {
		return nil
	}
	if r.marks >= markLimit {
		return fmt.Errorf("output mark budget exhausted")
	}
	if err := r.w.channels.sendEvent(ctx, "output_mark", func(h protocol.Header) protocol.Message {
		return protocol.OutputMark{Header: h, Offset: r.w.OutputThrough, Stream: "pty", ElapsedUS: elapsed.Microseconds()}
	}); err != nil {
		return err
	}
	r.marks++
	r.lastMark = elapsed
	return nil
}

func (r *Relay) writeOutput(ctx context.Context, data []byte) error {
	if len(data) == 0 {
		return nil
	}
	if int64(len(data)) > math.MaxInt64-r.w.OutputThrough {
		return fmt.Errorf("output offset overflow")
	}
	if err := r.mark(ctx, false); err != nil {
		return err
	}
	r.phaseMu.Lock()
	if r.phase != nil {
		ctx = r.phase
	}
	deadline, _ := ctx.Deadline()
	err := r.w.channels.terminal.SetWriteDeadline(deadline)
	r.phaseMu.Unlock()
	if err != nil {
		return err
	}
	stop := joinedAfterFunc(r.ctx, func() { _ = r.w.channels.terminal.SetWriteDeadline(time.Now()) })
	defer stop()
	for len(data) > 0 {
		if err := ctx.Err(); err != nil {
			return err
		}
		n, err := r.w.channels.terminal.Write(data)
		r.w.OutputThrough += int64(n)
		data = data[n:]
		if err != nil {
			return helper.WithContextCause(ctx, err)
		}
		if n == 0 {
			return io.ErrShortWrite
		}
	}
	return ctx.Err()
}

func (r *Relay) readOutcome(err error) {
	if err == nil || r.closeInterrupted(err) {
		return
	}
	if errors.Is(err, io.EOF) || errors.Is(err, syscall.EIO) {
		if !r.ended {
			r.ended = true
			r.readErr = err
			close(r.readEnded)
		}
		return
	}
	r.err = err
	r.w.channels.fail(err)
	r.cancel()
}

func (r *Relay) closeInterrupted(err error) bool {
	if r.w.channels.Err() != nil {
		return false
	}
	select {
	case <-r.closing:
		return err == context.Canceled || errors.Is(err, os.ErrDeadlineExceeded)
	default:
		return false
	}
}

func (r *Relay) run() {
	defer close(r.done)
	stopTransport := joinedAfterFunc(r.w.channels.ctx, r.cancel)
	defer stopTransport()
	stop := joinedAfterFunc(r.ctx, func() {
		_ = r.w.PTY.SetReadDeadline(time.Now())
		_ = r.w.PTY.SetWriteDeadline(time.Now())
		_ = r.w.channels.terminal.SetWriteDeadline(time.Now())
	})
	defer stop()
	for {
		r.mu.Lock()
		p := r.reader
		r.mu.Unlock()
		var output <-chan struct{}
		if p != nil {
			output = p.wake
		}
		select {
		case <-r.ctx.Done():
			r.mu.Lock()
			select {
			case <-r.closing:
			default:
				if r.err == nil {
					r.err = r.w.channels.Err()
					if r.err == nil {
						r.err = r.ctx.Err()
					}
				}
			}
			if r.reader != nil {
				_ = r.reader.join(r.w.PTY)
				r.reader = nil
			}
			r.mu.Unlock()
			return
		case <-r.wake:
		case <-output:
			r.mu.Lock()
			if r.ctx.Err() != nil {
				r.mu.Unlock()
				continue
			}
			if p == r.reader {
				data, err := p.take()
				// Public frame writes belong to Channels. Graceful relay close
				// joins an in-flight mark instead of cancelling a partial frame.
				if e := r.writeOutput(r.w.channels.ctx, data); e != nil && !r.closeInterrupted(e) {
					r.err = e
					r.w.channels.fail(e)
					r.cancel()
				}
				r.readOutcome(err)
				if err != nil {
					<-p.done
					r.reader = nil
				} else if r.ctx.Err() == nil {
					select {
					case p.resume <- struct{}{}:
					default:
					}
				}
			}
			r.mu.Unlock()
		}
	}
}

// Drain transfers the sole reader, flushes every already-admitted chunk and
// the currently available kernel prefix, and emits a covering boundary mark.
// The supplied original phase deadline also caps an already-blocked writer.
func (r *Relay) Drain(ctx context.Context) (offset int64, err error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		return 0, fmt.Errorf("drain requires an active original deadline")
	}
	select {
	case <-r.barrier:
	case <-ctx.Done():
		return 0, ctx.Err()
	}
	defer func() { r.barrier <- struct{}{} }()
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	r.phaseMu.Lock()
	r.phase = ctx
	err = r.w.channels.terminal.SetWriteDeadline(deadline)
	r.phaseMu.Unlock()
	if err != nil {
		return 0, err
	}
	stop := joinedAfterFunc(ctx, func() { r.w.channels.fail(ctx.Err()); _ = r.w.PTY.SetReadDeadline(time.Now()) })
	r.mu.Lock()
	defer func() {
		stop()
		r.phaseMu.Lock()
		r.phase = nil
		reset := r.w.channels.terminal.SetWriteDeadline(time.Time{})
		r.phaseMu.Unlock()
		if err == nil {
			err = reset
		}
		if err == nil {
			err = ctx.Err()
		}
		if err != nil {
			err = helper.WithContextCause(ctx, err)
			r.err = err
			r.w.channels.fail(err)
			r.cancel()
		}
		r.mu.Unlock()
		select {
		case r.wake <- struct{}{}:
		default:
		}
	}()
	if r.err != nil {
		return r.w.OutputThrough, r.err
	}
	if p := r.reader; p != nil {
		if err = p.join(r.w.PTY); err != nil {
			return r.w.OutputThrough, err
		}
		data, e := p.take()
		r.reader = nil
		if err = r.writeOutput(ctx, data); err != nil {
			return r.w.OutputThrough, err
		}
		r.readOutcome(e)
		if r.err != nil {
			return r.w.OutputThrough, r.err
		}
	}
	var buffer [32768]byte
	for !r.ended {
		if err = ctx.Err(); err != nil {
			return r.w.OutputThrough, err
		}
		raw, e := r.w.PTY.SyscallConn()
		if e != nil {
			return r.w.OutputThrough, e
		}
		n := 0
		e = raw.Control(func(fd uintptr) { n, err = syscall.Read(int(fd), buffer[:]) })
		if e != nil {
			return r.w.OutputThrough, e
		}
		if errors.Is(err, syscall.EINTR) {
			continue
		}
		if errors.Is(err, syscall.EAGAIN) {
			err = nil
			break
		}
		if err != nil {
			r.readOutcome(err)
			if r.err != nil {
				return r.w.OutputThrough, r.err
			}
			err = nil
			break
		}
		if n == 0 {
			r.readOutcome(io.EOF)
			break
		}
		if err = r.writeOutput(ctx, buffer[:n]); err != nil {
			return r.w.OutputThrough, err
		}
	}
	if err = r.mark(ctx, true); err != nil {
		return r.w.OutputThrough, err
	}
	if !r.ended {
		r.reader, err = readRelay(r.w.PTY)
	}
	return r.w.OutputThrough, err
}

// ForwardInput consumes a transport Event without replaying an older read.
// All PTY writes share inputWriter with the fixed adapter trigger.
func (r *Relay) ForwardInput(ctx context.Context, e Event) error {
	g := r.w.channels.inputState()
	g.mu.Lock()
	op, epoch := g.operation, g.epoch
	g.mu.Unlock()
	if len(e.Input) == 0 || e.InputOperation == "" || e.InputOperation != op {
		return nil
	}
	ioctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := joinedAfterFunc(epoch, cancel)
	defer stop()
	stopRelay := joinedAfterFunc(r.ctx, cancel)
	defer stopRelay()
	err := r.writePTY(ioctx, e.Input)
	if ctx.Err() == nil && r.ctx.Err() == nil && epoch.Err() != nil {
		return nil
	}
	return err
}

func (r *Relay) writePTY(ctx context.Context, data []byte) error {
	if err := r.ctx.Err(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case <-r.inputWriter:
	case <-ctx.Done():
		return ctx.Err()
	}
	defer func() { r.inputWriter <- struct{}{} }()
	if err := r.ctx.Err(); err != nil {
		return err
	}
	deadline, _ := ctx.Deadline()
	if err := r.w.PTY.SetWriteDeadline(deadline); err != nil {
		return err
	}
	stop := joinedAfterFunc(ctx, func() { _ = r.w.PTY.SetWriteDeadline(time.Now()) })
	defer stop()
	stopRelay := joinedAfterFunc(r.ctx, func() { _ = r.w.PTY.SetWriteDeadline(time.Now()) })
	defer stopRelay()
	for len(data) > 0 {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := r.ctx.Err(); err != nil {
			return err
		}
		n, err := r.w.PTY.Write(data)
		data = data[n:]
		if err != nil {
			return helper.WithContextCause(r.ctx, helper.WithContextCause(ctx, err))
		}
		if n == 0 {
			return io.ErrShortWrite
		}
	}
	return helper.WithContextCause(r.ctx, ctx.Err())
}

// SubmitTrigger never accepts source/frame bytes or controller input.
func (r *Relay) SubmitTrigger(ctx context.Context) error {
	return r.writePTY(ctx, []byte{0x18, 0x02})
}

// Close stops and joins only relay-owned I/O; the Workload remains the fd and
// child owner. An expired close leaves the same Relay available for later join.
func (r *Relay) Close(ctx context.Context) error {
	r.stopOnce.Do(func() { close(r.closing); r.cancel(); _ = r.w.channels.ResumeInput("") })
	select {
	case <-r.done:
		select {
		case <-r.inputWriter:
			r.inputWriter <- struct{}{}
		case <-ctx.Done():
			return ctx.Err()
		}
		if err := r.Err(); err != nil {
			return err
		}
		return r.w.channels.Err()
	case <-ctx.Done():
		return ctx.Err()
	}
}
