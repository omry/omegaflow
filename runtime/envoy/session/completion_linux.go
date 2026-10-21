//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// ReturnCleanup is the ordinary-return cleanup boundary. The foundation uses a
// bounded deterministic peer; the process owner supplies actual cleanup later.
// There is no default successful cleanup or independent cleanup clock here.
type ReturnCleanup func(context.Context, string, int64) error

type operationInput struct{ event Event }

func (*operationInput) Error() string { return "operation terminal input" }

// Complete coordinates ordinary PTY or split return using the existing owners.
// The caller retains its bound and cleanup owner. Lifecycle and gate facts
// share this dispatcher; split cleanup and inspection retain their owners.
func (c *Coordinator) Complete(ctx context.Context, cleanup ReturnCleanup) error {
	return c.completeControlled(ctx, cleanup, nil)
}

// completeControlled is the isolated B4 assembly boundary. B6 provides the
// actual lifetime/census/cleanup peers; ordinary completion shares this path.
func (c *Coordinator) completeControlled(ctx context.Context, cleanup ReturnCleanup, peers *controlPeers) (err error) {
	return c.completeReturn(ctx, cleanup, peers, nil)
}

// completeOrdinary assembles real ordinary return with the caller's retained
// process owner. The dedicated Envoy creates that owner before authored work;
// it keeps the pins until the coordinator has joined this transaction.
func (c *Coordinator) completeOrdinary(ctx context.Context, census *processCensus) error {
	if census == nil {
		return fmt.Errorf("ordinary return requires its process census")
	}
	return c.completeReturn(ctx, nil, nil, census)
}

// Foundation/control peers and actual ordinary return share one dispatcher,
// cleanup epoch, split barrier and result publication implementation.
func (c *Coordinator) completeReturn(ctx context.Context, cleanup ReturnCleanup, peers *controlPeers, census *processCensus) (err error) {
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("coordinator is already in use")
	}
	defer c.busy.Store(false)
	if c.active == nil || !c.active.started || c.exited != nil || (cleanup == nil && census == nil) || ctx.Err() != nil {
		return fmt.Errorf("ordinary completion requires an active operation and cleanup owner")
	}
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("ordinary completion requires the caller's existing bound")
	}
	if c.active.request.ExecutionShape == "split" && (c.splitResources() == nil || !c.active.splitAttached.Load()) {
		return fmt.Errorf("operation requires its attached split owner")
	}
	var dispatch *operationDispatch
	defer func() {
		if dispatch != nil {
			err = errors.Join(err, dispatch.disposeInspection())
		}
		if err != nil {
			code := "operation-cleanup"
			if errors.Is(err, errInspectionCancelTimeout) {
				code = "inspection-cancel-timeout"
				if dispatch != nil && dispatch.inspection != nil && dispatch.inspection.stop != nil {
					ctx = dispatch.inspection.stop
				}
			}
			if dispatch != nil && dispatch.cleanup != nil {
				ctx = dispatch.cleanup
			}
			err = c.fatal(ctx, code, err, false)
		}
		if dispatch != nil && dispatch.endCleanup != nil {
			dispatch.endCleanup()
		}
	}()
	if census != nil {
		if cleanup != nil || c.w.command == nil || c.w.command.Process == nil ||
			census.envoy != os.Getpid() || census.awsh != c.w.command.Process.Pid || int64(census.shell) != c.w.ShellPID {
			return fmt.Errorf("ordinary return process owner does not match workload")
		}
		operationID := c.active.request.OperationID
		cleanup = func(job context.Context, id string, helperPID int64) error {
			if id != operationID {
				return fmt.Errorf("ordinary cleanup operation mismatch")
			}
			return census.Cleanup(job, helperPID)
		}
	}
	d := &operationDispatch{ctx: ctx, life: newOperationLifecycle(c.active.request.OperationID), peers: peers, gates: make(map[string]*gateRecord), gateEvents: make(map[protocol.PrivateMessage]bool)}
	dispatch = d
	c.running = d
	defer func() { d.close(); c.running = nil }()
	// Keep the dispatcher alive until the selected shell-end transaction joins.
	// Interrupted worker cancellation is expected; substantive joined errors
	// still take the fatal path rather than becoming a successful shell end.
	defer func() {
		if errors.Is(err, errLifecycleEnd) && completionInterrupted(err) {
			err = c.endControlled(ctx, d)
		}
	}()
	if err = d.life.startReleased(ctx, StartOutcome{Started: true, QueuedCancel: c.active.cancel, ReleasedAt: c.active.releasedAt}); err != nil {
		return err
	}
	c.phase = awaitInputClose
	var m protocol.PrivateMessage
	for {
		m, err = c.result(ctx)
		var continuing *continueOperation
		if errors.As(err, &continuing) {
			if err = d.continueGate(c, ctx); err != nil {
				return err
			}
			continue
		}
		var input *operationInput
		if errors.As(err, &input) {
			c.forwardingInput = true
			err = c.work(ctx, false, func(job context.Context) error { return c.r.ForwardInput(job, input.event) })
			c.forwardingInput = false
			if err != nil {
				return err
			}
			continue
		}
		if err != nil {
			break
		}
		if publish, gate := d.gateEvents[m]; gate {
			delete(d.gateEvents, m)
			if publish {
				err = c.work(ctx, false, func(job context.Context) error { return c.r.PublishGate(job, m) })
			}
			if err != nil {
				return err
			}
			continue
		}
		if _, ended := m.(*protocol.PrivateShellExit); ended {
			return errLifecycleEnd
		}
		break
	}
	if err != nil {
		return err
	}
	proposal := m.(*protocol.PrivateInputClose) // result validated phase and ID.
	if d.gate != nil {
		return fmt.Errorf("input_close with an unresolved gate")
	}
	ctx = d.cleanup
	c.phase = 0
	if err = c.work(ctx, false, func(job context.Context) error {
		if err := cleanup(job, proposal.OperationID, proposal.CompletionHelperPID); err != nil {
			return err
		}
		return c.finishSplit(job)
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
	if _, ended := m.(*protocol.PrivateShellExit); ended {
		return errLifecycleEnd
	}
	completed, ok := m.(*protocol.PrivateCompleted)
	if !ok {
		return fmt.Errorf("unexpected final completion result %T", m)
	}
	if err = validateInspectionPlan(c.active.request.Inspections, completed.Inspections); err != nil {
		return err
	}
	c.phase = 0
	d.completed, d.selection = completed, "result"
	var closingOffset int64
	if err = c.work(ctx, false, func(job context.Context) error {
		if census != nil {
			if err := census.ProveIdle(job); err != nil {
				return err
			}
		}
		_, e := c.r.boundary(job, true, func(offset int64) error {
			closingOffset = offset
			return nil
		})
		return e
	}); err != nil {
		return err
	}
	if len(completed.Inspections) != 0 {
		// The final census and fresh output-through barrier finished the one
		// cleanup epoch. Inspection cannot inherit or restart that deadline.
		d.endCleanup()
		d.cleanup, d.endCleanup = nil, nil
		ctx = d.ctx
		if err = c.inspectCompleted(ctx, d); err != nil {
			return err
		}
	}
	if err = c.work(ctx, false, func(job context.Context) error {
		return c.w.channels.sendEvent(job, "operation-result", func(h protocol.Header) protocol.Message {
			switch result := d.result.(type) {
			case protocol.OperationCompleted:
				result.Header = h
				result.Type = "operation_completed"
				result.OutputStart, result.OutputThrough = c.active.outputStart, closingOffset
				return result
			case protocol.OperationCancelled:
				result.Header = h
				result.Type = "operation_cancelled"
				result.OutputStart, result.OutputThrough = c.active.outputStart, closingOffset
				return result
			case protocol.OperationFinalized:
				result.Header = h
				result.Type = "operation_finalized"
				result.OutputStart, result.OutputThrough = c.active.outputStart, closingOffset
				return result
			case protocol.OperationFailed:
				result.Header = h
				result.Type = "operation_failed"
				result.OutputStart, result.OutputThrough = c.active.outputStart, closingOffset
				return result
			}
			return nil
		}, func() error { return c.selectPublication(job) })
	}); err != nil {
		return err
	}
	c.recent = completed.OperationID
	c.w.CWD = completed.PhysicalCWD
	c.active = nil
	return nil
}

// completionInterrupted accepts only the lifecycle marker and cooperative
// worker cancellation. A joined protocol, cleanup or I/O failure stays fatal.
func completionInterrupted(err error) bool {
	cancelled := errors.Is(err, context.Canceled) || errors.Is(err, errWorkInterrupted)
	var interruption func(error) bool
	interruption = func(cause error) bool {
		if joined, ok := cause.(interface{ Unwrap() []error }); ok {
			for _, child := range joined.Unwrap() {
				if !interruption(child) {
					return false
				}
			}
			return true
		}
		return cause == errLifecycleEnd || cause == context.Canceled || cause == errWorkInterrupted ||
			(cancelled && errors.Is(cause, os.ErrDeadlineExceeded))
	}
	return interruption(err)
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
