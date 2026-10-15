//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// ReturnCleanup is the ordinary-return cleanup boundary. The foundation uses a
// bounded deterministic peer; the process owner supplies actual cleanup later.
// There is no default successful cleanup or independent cleanup clock here.
type ReturnCleanup func(context.Context, string, int64) error

type operationInput struct{ event Event }

func (*operationInput) Error() string { return "operation terminal input" }

// Complete coordinates ordinary shared-PTY return using the existing owners.
// The caller retains the bound; lifecycle, split cleanup and inspection remain
// separate owners. This foundation path does not supply their implementations.
func (c *Coordinator) Complete(ctx context.Context, cleanup ReturnCleanup) (err error) {
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("coordinator is already in use")
	}
	defer c.busy.Store(false)
	if c.active == nil || !c.active.started || c.exited != nil || cleanup == nil || ctx.Err() != nil {
		return fmt.Errorf("ordinary completion requires an active operation and cleanup owner")
	}
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("ordinary completion requires the caller's existing bound")
	}
	if c.active.cancel != nil || len(c.active.request.Inspections) != 0 || c.active.request.ExecutionShape != "pty" {
		return fmt.Errorf("operation requires its lifecycle, split or inspection owner")
	}
	defer func() {
		if err != nil {
			err = c.fatal(ctx, "operation-return", err, false)
		}
	}()
	c.phase = awaitInputClose
	var m protocol.PrivateMessage
	for {
		m, err = c.result(ctx)
		var input *operationInput
		if !errors.As(err, &input) {
			break
		}
		c.forwardingInput = true
		err = c.work(ctx, false, func(job context.Context) error { return c.r.ForwardInput(job, input.event) })
		c.forwardingInput = false
		if err != nil {
			return err
		}
	}
	if err != nil {
		return err
	}
	proposal := m.(*protocol.PrivateInputClose) // result validated phase and ID.
	c.phase = 0
	if err = c.work(ctx, false, func(job context.Context) error {
		return cleanup(job, proposal.OperationID, proposal.CompletionHelperPID)
	}); err != nil {
		return err
	}
	c.phase = awaitCompleted // Arm before input_closed can release the helper.
	if err = c.send(ctx, protocol.PrivateInputClosed{OperationID: proposal.OperationID}); err != nil {
		return err
	}
	m, err = c.result(ctx)
	if err != nil {
		return err
	}
	completed := m.(*protocol.PrivateCompleted)
	if len(completed.Inspections) != 0 {
		return fmt.Errorf("unexpected resolved inspections for foundation operation")
	}
	c.phase = 0
	if err = c.work(ctx, false, func(job context.Context) error {
		_, e := c.r.boundary(job, true, func(offset int64) error {
			return c.w.channels.sendEvent(job, "operation_completed", func(h protocol.Header) protocol.Message {
				return protocol.OperationCompleted{Header: h, OperationID: completed.OperationID,
					Status: completed.Status, Cwd: completed.PhysicalCWD, OutputStart: c.active.outputStart,
					OutputThrough: offset, InspectionResults: []protocol.InspectionResult{}}
			}, func() error { return c.selectPublication(job) })
		})
		return e
	}); err != nil {
		return err
	}
	c.w.CWD = completed.PhysicalCWD
	c.active = nil
	return nil
}

// ShutdownIdle retains the same private pump through closed and EOF. The
// existing final-drain epoch includes relay join, shell reap and final output.
func (c *Coordinator) ShutdownIdle(reason string) (err error) {
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("coordinator is already in use")
	}
	defer c.busy.Store(false)
	if c.active != nil || c.pending != nil || c.exited != nil {
		return fmt.Errorf("shutdown requires an idle coordinator")
	}
	phase, end := context.WithTimeout(context.Background(), finalDrainBudget)
	defer end()
	entered := false
	defer func() {
		if !entered {
			c.w.channels.Close()
			err = errors.Join(err, c.w.Close(phase))
		}
		if joinErr := c.private.close(phase); joinErr != nil {
			err = errors.Join(err, fmt.Errorf("private pump join: %w", joinErr))
		}
	}()
	if err = c.r.Close(phase); err != nil {
		return fmt.Errorf("relay shutdown join: %w", err)
	}
	entered = true
	err = c.w.shutdownIdle(phase, reason, func(ctx context.Context) (protocol.PrivateMessage, error) {
		var result privateResult
		select {
		case result = <-c.private.fatal:
		default:
			select {
			case result = <-c.private.events:
			case result = <-c.private.fatal:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		return result.message, result.err
	})
	if err != nil {
		return fmt.Errorf("idle shutdown: %w", err)
	}
	return nil
}
