// Package session owns Envoy's two public channels and bounded control writes.
// Launch, relay and operation lifecycle decisions belong to the caller.
package session

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const controlWriteBudget = 5 * time.Second

type deadlineWriter interface {
	Write([]byte) (int, error)
	SetWriteDeadline(time.Time) error
}

// WriteControl writes one already encoded control frame. Its five-second epoch
// begins here and is capped by the caller's original phase deadline. Positive
// short writes retain that deadline; any incomplete delivery is an error.
// The caller must close its session on error, including after partial delivery.
func WriteControl(ctx context.Context, w deadlineWriter, frame []byte) error {
	if len(frame) == 0 || len(frame) > protocol.MaxFrameBytes {
		return fmt.Errorf("invalid control frame size")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline := time.Now().Add(controlWriteBudget)
	if phase, ok := ctx.Deadline(); ok && phase.Before(deadline) {
		deadline = phase
	}
	if err := w.SetWriteDeadline(deadline); err != nil {
		return err
	}
	// Cancellation interrupts a blocked syscall without creating another epoch.
	var mu sync.Mutex
	finished := false
	stop := context.AfterFunc(ctx, func() {
		mu.Lock()
		defer mu.Unlock()
		if !finished {
			_ = w.SetWriteDeadline(time.Now())
		}
	})
	err := helper.WriteAll(w, frame)
	mu.Lock()
	finished = true
	stop()
	mu.Unlock()
	if err == nil {
		err = ctx.Err()
	}
	return err
}

// Send validates a complete public event and its independent Envoy sequence.
// Sequence advances only after every encoded byte has been written.
func (s *Channels) Send(ctx context.Context, m protocol.Message) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	frame, err := protocol.EncodeEnvoy(m)
	if err == nil {
		var h protocol.Header
		err = json.Unmarshal(frame, &h)
		if err == nil && h.Seq != s.sendSeq {
			err = fmt.Errorf("unexpected Envoy sequence")
		}
	}
	if err == nil {
		err = WriteControl(ctx, s.telemetry, frame)
	}
	if err != nil {
		s.fail(err)
		return err
	}
	s.sendSeq++
	return nil
}

// SendPrivate uses the existing private codec and the same exact write loop.
// It supplies no new framing, actor or lifecycle policy.
func SendPrivate(ctx context.Context, w deadlineWriter, m protocol.PrivateMessage) error {
	frame, err := protocol.EncodePrivate(m, protocol.EnvoyToAwsh)
	if err != nil {
		return err
	}
	return WriteControl(ctx, w, frame)
}
