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
	if len(c.active.request.Inspections) != 0 ||
		(c.active.request.ExecutionShape == "split" && (c.splitResources() == nil || !c.active.splitAttached.Load())) {
		return fmt.Errorf("operation requires its lifecycle, split or inspection owner")
	}
	var dispatch *operationDispatch
	defer func() {
		if err != nil {
			if dispatch != nil && dispatch.cleanup != nil {
				ctx = dispatch.cleanup
			}
			err = c.fatal(ctx, "operation-cleanup", err, false)
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
	if err = d.life.startReleased(ctx, StartOutcome{Started: true, QueuedCancel: c.active.cancel, ReleasedAt: c.active.releasedAt}); err != nil {
		return err
	}
	c.phase = awaitInputClose
	var m protocol.PrivateMessage
	for {
		m, err = c.result(ctx)
		if errors.Is(err, errLifecycleEnd) {
			return c.endControlled(ctx, d)
		}
		var continuing *continueOperation
		if errors.As(err, &continuing) {
			if err = d.continueGate(c, ctx); err != nil {
				if errors.Is(err, errLifecycleEnd) {
					return c.endControlled(ctx, d)
				}
				return err
			}
			continue
		}
		var input *operationInput
		if errors.As(err, &input) {
			c.forwardingInput = true
			err = c.work(ctx, false, func(job context.Context) error { return c.r.ForwardInput(job, input.event) })
			c.forwardingInput = false
			if errors.Is(err, errLifecycleEnd) {
				return c.endControlled(ctx, d)
			}
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
			if errors.Is(err, errLifecycleEnd) {
				return c.endControlled(ctx, d)
			}
			if err != nil {
				return err
			}
			continue
		}
		if _, ended := m.(*protocol.PrivateShellExit); ended {
			return c.endControlled(ctx, d)
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
		if errors.Is(err, errLifecycleEnd) {
			return c.endControlled(ctx, d)
		}
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
	d.completed, d.selection = completed, "result"
	if err = c.work(ctx, false, func(job context.Context) error {
		if census != nil {
			if err := census.ProveIdle(job); err != nil {
				return err
			}
		}
		_, e := c.r.boundary(job, true, func(offset int64) error {
			// Owner selection precedes construction, so a cancel accepted during
			// cleanup/mark delivery cannot race worker reads of lifecycle state.
			return c.w.channels.sendEvent(job, "operation-result", func(h protocol.Header) protocol.Message {
				switch result := d.result.(type) {
				case protocol.OperationCompleted:
					result.Header = h
					result.Type = "operation_completed"
					result.OutputStart = c.active.outputStart
					result.OutputThrough = offset
					return result
				case protocol.OperationCancelled:
					result.Header = h
					result.Type = "operation_cancelled"
					result.OutputStart = c.active.outputStart
					result.OutputThrough = offset
					return result
				case protocol.OperationFinalized:
					result.Header = h
					result.Type = "operation_finalized"
					result.OutputStart = c.active.outputStart
					result.OutputThrough = offset
					return result
				}
				return nil
			}, func() error { return c.selectPublication(job) })
		})
		return e
	}); err != nil {
		return err
	}
	c.recent = completed.OperationID
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
