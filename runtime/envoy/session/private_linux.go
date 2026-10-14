//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type privateResult struct {
	message protocol.PrivateMessage
	err     error
}

// privateResults is a bounded I/O pump, shared by start and completion. Reader
// retains buffered concatenated frames; no other consumer reads the pipe.
type privateResults struct {
	w      *Workload
	ctx    context.Context
	cancel context.CancelFunc
	events chan privateResult
	done   chan struct{}
	stop   func()
	once   sync.Once
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
	p := &privateResults{w: w, events: make(chan privateResult, 1), done: make(chan struct{})}
	p.ctx, p.cancel = context.WithCancel(ctx)
	p.stop = joinedAfterFunc(p.ctx, func() { _ = w.Result.SetReadDeadline(time.Now()) })
	stopChannels := joinedAfterFunc(w.channels.ctx, p.cancel)
	go func() {
		defer close(p.done)
		defer stopChannels()
		for {
			m, err := protocol.ReadPrivate(w.Reader, protocol.AwshToEnvoy)
			select {
			case p.events <- privateResult{m, err}:
			case <-p.ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	return p, nil
}

func (p *privateResults) close(ctx context.Context) error {
	p.once.Do(p.cancel)
	select {
	case <-p.done:
		p.stop()
		return p.w.Result.SetReadDeadline(time.Time{})
	case <-ctx.Done():
		return errors.Join(ctx.Err(), p.ctx.Err())
	}
}
