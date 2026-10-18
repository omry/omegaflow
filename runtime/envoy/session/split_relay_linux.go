//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"syscall"
	"time"
)

var splitStreams = [2]string{"stdout", "stderr"}

type splitRelay struct {
	id     string
	files  [2]*os.File
	pumps  [2]*relayReader
	ended  [2]bool
	done   chan struct{} // Independent read EOF facts, not output/result completion.
	once   sync.Once
	stopIO func()
}

// The setup owner retains its raw handles. Each poller owns one atomic
// close-on-exec duplicate of the validated reader, without reopening a path.
func (s *SplitResources) relayReaders(id string) (files [2]*os.File, err error) {
	if s == nil || s.operation == nil || s.operation.id != id || len(s.operation.fifos) != 2 {
		return files, fmt.Errorf("split reader operation mismatch")
	}
	if err = s.validate(); err != nil {
		return files, err
	}
	defer func() {
		if err != nil {
			for _, file := range files {
				if file != nil {
					err = errors.Join(err, file.Close())
				}
			}
		}
	}()
	for i, fd := range s.operation.readers {
		copyFD, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_DUPFD_CLOEXEC, 0)
		if errno != 0 {
			return files, errno
		}
		files[i] = os.NewFile(copyFD, splitStreams[i])
		var st syscall.Stat_t
		if err = syscall.Fstat(int(copyFD), &st); err != nil {
			return files, err
		}
		if !splitSame(st, s.operation.fifos[i].stat) {
			return files, fmt.Errorf("split pump descriptor identity changed")
		}
	}
	return files, s.validate()
}

// attachSplit supplies the transport primitive. B5.3 owns its assembly into
// start, resize and cleanup; this method does not publish an operation result.
func (r *Relay) attachSplit(ctx context.Context, owner *SplitResources, id string) (err error) {
	if err = ctx.Err(); err != nil {
		return err
	}
	files, err := owner.relayReaders(id)
	if err != nil {
		return err
	}
	attached := false
	defer func() {
		if !attached {
			for _, file := range files {
				err = errors.Join(err, file.Close())
			}
		}
	}()
	_, err = r.boundary(ctx, false, func(int64) error {
		if r.split != nil {
			return fmt.Errorf("split relay already attached")
		}
		s := &splitRelay{id: id, files: files, done: make(chan struct{})}
		r.split, attached = s, true
		s.stopIO = joinedAfterFunc(r.ctx, func() {
			for _, file := range files {
				_ = file.SetReadDeadline(time.Now())
			}
		})
		for i, file := range files {
			var e error
			s.pumps[i], e = readRelay(file)
			if e != nil {
				return e
			}
		}
		return nil
	})
	return err
}

func (r *Relay) splitOutcome(s *splitRelay, i int, err error) {
	if errors.Is(err, io.EOF) {
		s.ended[i] = true
		if s.ended[0] && s.ended[1] {
			s.once.Do(func() { close(s.done) })
		}
	} else if err != nil && !r.closeInterrupted(err) {
		r.err = errors.Join(r.err, err)
		r.w.channels.fail(err)
		r.cancel()
	}
}

func (r *Relay) splitWriteOutcome(err error) bool {
	if err == nil {
		return true
	}
	if !r.closeInterrupted(err) && !errors.Is(err, errWorkInterrupted) {
		r.err = errors.Join(r.err, err)
		r.w.channels.fail(err)
		r.cancel()
	}
	return false
}

// A split source may resume only after its complete admitted chunk reached the
// terminal writer. An interrupted write can still have an empty suffix when
// the boundary arrived after the final Write call; keep that source live in
// the same way as an uninterrupted write. A nonempty suffix remains parked.
func (r *Relay) resumeSplitIfReady(i int) {
	s := r.split
	if s == nil || s.pumps[i] == nil || len(r.w.pendingSplit[i]) != 0 || r.ctx.Err() != nil || r.w.channels.Err() != nil {
		return
	}
	select {
	case s.pumps[i].resume <- struct{}{}:
	default:
	}
}

func (r *Relay) consumeSplit(s *splitRelay, i int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if s != r.split || r.ctx.Err() != nil || s.pumps[i] == nil {
		return
	}
	p := s.pumps[i]
	data, err := p.take()
	writeErr := r.writeOutputStream(r.w.channels.ctx, data, splitStreams[i])
	written := r.splitWriteOutcome(writeErr)
	r.splitOutcome(s, i, err)
	if err != nil {
		<-p.done
		s.pumps[i] = nil
	} else if written || errors.Is(writeErr, errWorkInterrupted) {
		r.resumeSplitIfReady(i)
	}
}

// A source with an undelivered suffix stays stopped. The existing boundary's
// wake resumes it only after that suffix reaches the sole terminal writer.
func (r *Relay) flushSplitPending(ctx context.Context) error {
	for i, stream := range splitStreams {
		data := r.w.pendingSplit[i]
		if len(data) == 0 {
			continue
		}
		r.w.pendingSplit[i] = nil
		err := r.writeOutputStream(ctx, data, stream)
		written := r.splitWriteOutcome(err)
		if written || errors.Is(err, errWorkInterrupted) {
			r.resumeSplitIfReady(i)
		}
		if !written {
			return err
		}
	}
	return nil
}

// Called with mu held after relay cancellation or inside its phase boundary.
// It preserves admitted bytes; observing two EOFs is a separate caller proof.
func (r *Relay) joinSplit() (err error) {
	s := r.split
	if s == nil {
		return nil
	}
	for i, p := range s.pumps {
		if p != nil {
			err = errors.Join(err, p.join(s.files[i]))
			data, e := p.take()
			r.w.pendingSplit[i] = append(r.w.pendingSplit[i], data...)
			r.splitOutcome(s, i, e)
			s.pumps[i] = nil
		}
	}
	s.stopIO()
	for i, file := range s.files {
		if file != nil {
			err = errors.Join(err, file.Close())
			s.files[i] = nil
		}
	}
	return err
}

// stopSplit joins only pump-owned I/O. The setup owner still owns its raw
// readers, writer keepalives and paths. This is not a cleanup/EOF barrier.
func (r *Relay) stopSplit(ctx context.Context) error {
	_, err := r.boundary(ctx, false, func(int64) error {
		if e := r.joinSplit(); e != nil {
			return e
		}
		if e := r.flushSplitPending(ctx); e != nil {
			return e
		}
		if r.err != nil {
			return r.err
		}
		r.split = nil
		return r.w.mark(ctx, true)
	})
	return err
}
