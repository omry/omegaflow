// Package session owns Envoy's two public channels and bounded control writes.
// Launch, relay and operation lifecycle decisions belong to the caller.
package session

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

var errWorkInterrupted = errors.New("coordinator work interrupted")

// WriteControl retains the shared original phase and individual write bounds.
func WriteControl(ctx context.Context, w helper.DeadlineWriter, frame []byte) error {
	return helper.WriteControl(ctx, w, frame)
}

// Send validates a complete public event and its independent Envoy sequence.
// Sequence advances only after every encoded byte has been written.
func (s *Channels) Send(ctx context.Context, m protocol.Message) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	return s.sendLocked(ctx, m)
}

// sendEvent constructs an owner-generated event under the same lock as its
// sequence commit. Relay marks cannot race another public event's header.
func (s *Channels) sendEvent(ctx context.Context, kind string, build func(protocol.Header) protocol.Message, selectResult ...func() error) error {
	for !s.writeMu.TryLock() {
		if err := ctx.Err(); err != nil {
			return err
		}
		time.Sleep(time.Millisecond)
	}
	defer s.writeMu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	for _, selectResult := range selectResult {
		if err := selectResult(); err != nil {
			return err
		}
	}
	return s.sendLocked(ctx, build(s.header(kind)))
}

// controlAttempt distinguishes an interrupted unattempted event from a failed
// frame. Once any transport Write is attempted, incomplete delivery is fatal.
type controlAttempt struct {
	helper.DeadlineWriter
	attempted bool
}

func (w *controlAttempt) Write(data []byte) (int, error) {
	w.attempted = true
	return w.DeadlineWriter.Write(data)
}

func (s *Channels) sendLocked(ctx context.Context, m protocol.Message) error {
	w := &controlAttempt{DeadlineWriter: s.telemetry}
	frame, err := protocol.EncodeEnvoy(m)
	if err == nil {
		var h protocol.Header
		err = json.Unmarshal(frame, &h)
		if err == nil && h.Seq != s.sendSeq {
			err = fmt.Errorf("unexpected Envoy sequence")
		}
	}
	if err == nil {
		err = WriteControl(ctx, w, frame)
	}
	if err != nil {
		if !w.attempted && errors.Is(context.Cause(ctx), errWorkInterrupted) && errors.Is(err, context.Canceled) {
			return err
		}
		s.fail(err)
		return err
	}
	s.sendSeq++
	return nil
}

// SendPrivate uses the existing private codec and the same exact write loop.
// It supplies no new framing, actor or lifecycle policy.
func SendPrivate(ctx context.Context, w helper.DeadlineWriter, m protocol.PrivateMessage) error {
	frame, err := protocol.EncodePrivate(m, protocol.EnvoyToAwsh)
	if err != nil {
		return err
	}
	return WriteControl(ctx, w, frame)
}
