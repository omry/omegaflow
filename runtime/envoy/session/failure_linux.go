//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"os"
)

// disposeFailure joins a failed session after its operation owner has returned.
// The caller supplies the existing teardown epoch. A channel cancellation is
// neither a process reap nor proof that descendants released their descriptors.
func (c *Coordinator) disposeFailure(ctx context.Context, census *processCensus, cause error) error {
	if cause == nil || ctx == nil {
		return fmt.Errorf("failed disposal requires its cause and caller epoch")
	}
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("failed disposal requires the caller deadline")
	}
	if c == nil || c.w == nil || c.r == nil || c.private == nil || c.w.channels == nil ||
		census == nil || c.w.command == nil || c.w.command.Process == nil ||
		census.envoy != os.Getpid() || census.awsh != c.w.command.Process.Pid || int64(census.shell) != c.w.ShellPID {
		return fmt.Errorf("failed disposal process owner does not match workload")
	}
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("join active coordinator operation before failed disposal")
	}
	defer c.busy.Store(false)
	if c.failure == nil {
		c.failure = cause
	}
	if c.failureDisposed {
		return c.failure
	}
	if c.privateFailure != nil {
		var end context.CancelFunc
		ctx, end = context.WithDeadline(context.WithoutCancel(ctx), c.privateFailure.deadline)
		defer end()
	}
	c.w.channels.fail(c.failure)
	c.w.channels.Close()
	// Close joins the existing relay, including any admitted split I/O. The
	// coordinator then releases its remaining split resources and private pump.
	relayErr := c.r.Close(ctx)
	var disposal error
	disposal = errors.Join(disposal, c.releaseResources(ctx))
	// Kill descendants while the original root lifetime pins still exist, then
	// stop Bash and join its direct Awsh wait owner. A final census also reaps
	// descendants adopted during either root's exit.
	disposal = errors.Join(disposal, census.Cleanup(ctx, 0))
	disposal = errors.Join(disposal, census.terminateShell(ctx))
	disposal = errors.Join(disposal, c.w.Close(ctx))
	disposal = errors.Join(disposal, census.reapAdoptedShell(ctx))
	disposal = errors.Join(disposal, census.Cleanup(ctx, 0))
	disposal = errors.Join(disposal, census.proveEnded(ctx))
	// Relay.Close also returns the already selected transport failure after a
	// successful join. Preserve it as cause evidence, not as an unreaped actor.
	c.failure = errors.Join(c.failure, relayErr, disposal)
	c.failureDisposed = disposal == nil
	if ctx.Err() != nil {
		c.failureDisposed = false
	}
	return c.failure
}
