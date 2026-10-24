//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// WaitGateInput waits for the controller's terminal socket read watermark.
// The existing fact owner supplies the original input-barrier epoch and keeps
// forwarding input Events while this wait runs. Unlike HoldInput, it neither
// discards gated input nor suspends reads at the watermark.
func (s *Channels) WaitGateInput(ctx context.Context, operation string, through int64) (err error) {
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("gate input requires the original barrier deadline")
	}
	defer func() {
		if errors.Is(err, context.DeadlineExceeded) {
			deadline, _ := ctx.Deadline()
			err = &StartError{Code: "input-barrier-timeout", Deadline: deadline, Cause: err}
			// The original barrier is exhausted. No helper release or ordinary
			// operation result is safe; teardown retains this absolute epoch.
			s.fail(err)
		}
	}()
	g := s.inputState()
	g.mu.Lock()
	if err := ctx.Err(); err != nil {
		g.mu.Unlock()
		return err
	}
	if operation == "" || g.operation != operation || g.held || through < 0 || through < g.watermark {
		g.mu.Unlock()
		return fmt.Errorf("invalid gate input watermark or operation")
	}
	g.watermark = through
	for {
		if err := ctx.Err(); err != nil {
			g.mu.Unlock()
			return err
		}
		if err := s.Err(); err != nil {
			g.mu.Unlock()
			return err
		}
		if g.operation != operation || g.held {
			g.mu.Unlock()
			return fmt.Errorf("gate input ownership changed")
		}
		if g.through >= through {
			g.mu.Unlock()
			return nil
		}
		wake := g.wake
		g.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-s.ctx.Done():
			return s.ctx.Err()
		case <-wake:
		}
		g.mu.Lock()
	}
}

// PublishGate establishes the existing finite output boundary before sending
// one already-accepted gate fact. It adds no private reader or lifecycle owner.
// The caller validates operation/gate phase and owns crossed lifecycle policy.
func (r *Relay) PublishGate(ctx context.Context, fact protocol.PrivateMessage) error {
	if _, err := protocol.EncodePrivate(fact, protocol.AwshToEnvoy); err != nil {
		return err
	}
	var event string
	var op, gate string
	switch m := fact.(type) {
	case *protocol.PrivateGateReady:
		event, op, gate = "operation_ready", m.OperationID, m.GateID
	case *protocol.PrivateGateContinued:
		event, op, gate = "operation_continued", m.OperationID, m.GateID
	case *protocol.PrivateGateInterrupted:
		event, op, gate = "operation_gate_interrupted", m.OperationID, m.GateID
	default:
		return fmt.Errorf("not a gate fact")
	}
	_, err := r.boundary(ctx, true, func(offset int64) error {
		return r.w.channels.sendEvent(ctx, event, func(h protocol.Header) protocol.Message {
			switch event {
			case "operation_ready":
				return protocol.OperationReady{Header: h, OperationID: op, GateID: gate, OutputThrough: offset}
			case "operation_continued":
				return protocol.OperationContinued{Header: h, OperationID: op, GateID: gate, OutputThrough: offset}
			default:
				return protocol.OperationGateInterrupted{Header: h, OperationID: op, GateID: gate, OutputThrough: offset}
			}
		})
	})
	return err
}
