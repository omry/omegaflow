//go:build linux

package session

import (
	"context"
	"fmt"
	"math"
	"net"
	"sync"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// terminalInput coordinates the existing sole socket reader. It owns no fd,
// lifecycle timer, or unbounded input queue.
type terminalInput struct {
	mu                            sync.Mutex
	wake                          chan struct{}
	through, watermark            int64
	enabled, held, paused, inRead bool
	kick                          bool
	operation                     string
	epoch                         context.Context
	cancel                        context.CancelFunc
}

func (s *Channels) inputState() *terminalInput {
	s.inputOnce.Do(func() { s.input = &terminalInput{wake: make(chan struct{})} })
	return s.input
}

func (g *terminalInput) changed() { close(g.wake); g.wake = make(chan struct{}) }

func (s *Channels) inputReadPermit() (int, string, error) {
	g := s.inputState()
	for {
		g.mu.Lock()
		if g.held && g.through >= g.watermark {
			g.paused = true
			g.changed()
			wake := g.wake
			g.mu.Unlock()
			select {
			case <-wake:
				continue
			case <-s.ctx.Done():
				return 0, "", s.ctx.Err()
			}
		}
		limit := int64(32768)
		if g.held && g.watermark-g.through < limit {
			limit = g.watermark - g.through
		}
		g.inRead, g.paused = true, false
		op := g.operation
		g.mu.Unlock()
		return int(limit), op, nil
	}
}

func (s *Channels) recordInput(n int, operation string) (string, bool, error) {
	g := s.inputState()
	g.mu.Lock()
	defer g.mu.Unlock()
	g.inRead = false
	if int64(n) > math.MaxInt64-g.through {
		return "", false, fmt.Errorf("terminal input count overflow")
	}
	g.through += int64(n)
	if g.held || operation != g.operation {
		operation = ""
	}
	g.paused = g.held && g.through >= g.watermark
	g.changed()
	// Before relay ownership, preserve the foundation's bounded Event API.
	// Once owned, discard idle input here so a watermark wait cannot deadlock
	// behind an unconsumed queue of bytes it is required to discard.
	return operation, !g.enabled || operation != "", nil
}

func (s *Channels) inputReadInterrupted(err error) bool {
	timeout, ok := err.(net.Error)
	if !ok || !timeout.Timeout() {
		return false
	}
	g := s.inputState()
	g.mu.Lock()
	defer g.mu.Unlock()
	if !g.kick {
		return false
	}
	g.kick = false
	if err := s.terminal.SetReadDeadline(time.Time{}); err != nil {
		s.fail(err)
		return false
	}
	return true
}

// InputThrough counts actual socket reads, including idle bytes discarded.
func (s *Channels) InputThrough() int64 {
	g := s.inputState()
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.through
}

// HoldInput joins an in-flight read and suspends further socket reads once
// the caller's watermark is reached. Its original input-barrier context owns
// the wait; no replacement epoch begins here.
func (s *Channels) HoldInput(ctx context.Context, through int64) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("input hold requires an active original deadline")
	}
	g := s.inputState()
	g.mu.Lock()
	if through < 0 || through < g.watermark || g.held || g.operation != "" || s.terminal == nil {
		g.mu.Unlock()
		return fmt.Errorf("invalid input hold boundary")
	}
	g.enabled, g.held, g.watermark = true, true, through
	g.paused = !g.inRead && g.through >= through
	g.kick = true
	err := s.terminal.SetReadDeadline(time.Now())
	g.changed()
	g.mu.Unlock()
	if err != nil {
		s.fail(err)
		return err
	}
	for {
		g.mu.Lock()
		paused, wake := g.paused, g.wake
		g.mu.Unlock()
		if err := ctx.Err(); err != nil {
			// The caller owns this input-barrier epoch. Its cancellation or
			// deadline ends only the wait; the held reader remains recoverable
			// through ResumeInput("").
			return err
		}
		if paused {
			return s.Err()
		}
		select {
		case <-wake:
		case <-ctx.Done():
		case <-s.Done():
			return s.Err()
		}
	}
}

// ResumeInput opens only the caller's current operation, or resumes idle
// discard for an empty ID. Cancelling the old eligibility context lets the
// relay join an old blocked PTY write before its next writer acquires the fd.
func (s *Channels) ResumeInput(operation string) error {
	return s.ResumeInputContext(context.Background(), operation)
}
func (s *Channels) ResumeInputContext(ctx context.Context, operation string) error {
	if operation != "" {
		if _, err := protocol.EncodePrivate(protocol.PrivateInputClosed{OperationID: operation}, protocol.EnvoyToAwsh); err != nil {
			return err
		}
	}
	g := s.inputState()
	for !g.mu.TryLock() {
		if err := ctx.Err(); err != nil {
			return err
		}
		time.Sleep(time.Millisecond)
	}
	defer g.mu.Unlock()
	if operation != "" && (!g.held || !g.paused) {
		return fmt.Errorf("input release without a held watermark")
	}
	if g.cancel != nil {
		g.cancel()
	}
	g.epoch, g.cancel = context.WithCancel(s.ctx)
	g.enabled, g.held, g.paused, g.operation = true, false, false, operation
	g.changed()
	return helper.WithContextCause(ctx, nil)
}
