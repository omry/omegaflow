//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Both idle winners share closure. The selected shell's reported status is
// distinct from the required zero-status reap of its Awsh supervisor.
func (w *Workload) finishIdleTerminal(ctx context.Context, reason string, terminal protocol.PrivateMessage, next func(context.Context) (protocol.PrivateMessage, error), census *processCensus) error {
	if err := w.finishTerminalClosure(ctx, terminal, next, census); err != nil {
		return err
	}
	return w.finishTerminalDrain(ctx, reason)
}

// Closure and output drain share a bound for idle shutdown. A pre-write
// shell-exit handoff instead retains its earlier closure epoch separately.
func (w *Workload) finishTerminalClosure(ctx context.Context, terminal protocol.PrivateMessage, next func(context.Context) (protocol.PrivateMessage, error), census *processCensus) error {
	switch m := terminal.(type) {
	case *protocol.PrivateClosed:
		if m.Reason != "shutdown" || m.CWD != w.CWD {
			return fmt.Errorf("invalid idle shutdown result")
		}
	case *protocol.PrivateShellExit:
		if m.ActiveOperationID != "" || m.CWD != w.CWD {
			return fmt.Errorf("invalid idle shell_exit")
		}
	default:
		return fmt.Errorf("invalid idle terminal result %T", terminal)
	}
	if err := w.Control.Close(); err != nil && !errors.Is(err, os.ErrClosed) {
		return err
	}
	if _, err := next(ctx); err != io.EOF {
		return errors.Join(fmt.Errorf("private EOF required after terminal result"), helper.WithContextCause(ctx, err))
	}
	select {
	case <-w.reaped:
		if w.waitErr != nil {
			return fmt.Errorf("terminal Awsh reap: %w", w.waitErr)
		}
	case <-ctx.Done():
		return ctx.Err()
	}
	if census != nil {
		if err := census.Cleanup(ctx, 0); err != nil {
			return err
		}
		if err := census.proveEnded(ctx); err != nil {
			return err
		}
	}
	return nil
}

func (w *Workload) finishTerminalDrain(ctx context.Context, reason string) error {
	deadline, ok := ctx.Deadline()
	if !ok {
		return fmt.Errorf("idle terminal requires its final-drain deadline")
	}
	if err := w.PTY.SetReadDeadline(deadline); err != nil {
		return err
	}
	if err := w.drainTerminal(ctx); err != nil {
		return err
	}
	if err := w.channels.Send(ctx, protocol.Draining{Header: w.channels.header("draining"), Reason: reason, OutputThrough: w.OutputThrough}); err != nil {
		return err
	}
	if err := w.channels.terminal.CloseWrite(); err != nil {
		return err
	}
	return w.channels.Send(ctx, protocol.Closed{Header: w.channels.header("closed"), Reason: reason, OutputThrough: w.OutputThrough})
}

func (c *Coordinator) nextIdleResult(ctx context.Context) (protocol.PrivateMessage, error) {
	phase, deadline := c.privateEpoch(ctx)
	return c.nextPrivateAt(ctx, phase, deadline)
}

func (c *Coordinator) nextFinalResult(ctx context.Context) (protocol.PrivateMessage, error) {
	deadline, _ := ctx.Deadline()
	return c.nextPrivateAt(ctx, "final-drain", deadline)
}

func (c *Coordinator) nextPrivateAt(ctx context.Context, phase string, deadline time.Time) (protocol.PrivateMessage, error) {
	m, err := c.readPrivateResult(ctx)
	result := c.private.preferFatal(privateResult{message: m, err: err})
	m, err = result.message, result.err
	if _, report := m.(*protocol.PrivateProtocolError); report || (err != nil && err != io.EOF && !privateIOCancelled(ctx, err) && !privateIOCancelled(c.private.ctx, err)) {
		return nil, c.failPrivate(ctx, nil, result, phase, deadline)
	}
	return m, err
}

func (c *Coordinator) idleProcessOwner(census *processCensus) error {
	if census == nil || c.w.command == nil || c.w.command.Process == nil ||
		census.envoy != os.Getpid() || census.awsh != c.w.command.Process.Pid || int64(census.shell) != c.w.ShellPID {
		return fmt.Errorf("idle terminal process owner does not match workload")
	}
	return nil
}

// idleShellExit is the existing coordinator's idle private-fact entry. Waiting
// for that fact does not start a new timer. Acceptance starts final-drain; a
// later shutdown cannot replace the selected shell_ended reason.
func (c *Coordinator) idleShellExit(ctx context.Context, census *processCensus) (err error) {
	if err = c.idleProcessOwner(census); err != nil {
		return err
	}
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("coordinator is already in use")
	}
	defer c.busy.Store(false)
	if ctx == nil || ctx.Err() != nil || c.active != nil || c.pending != nil || c.exited != nil {
		return fmt.Errorf("shell-exit entry requires an idle coordinator")
	}
	m, readErr := c.nextIdleResult(ctx)
	deadline := time.Now().Add(finalDrainBudget)
	if c.privateFailure != nil {
		deadline = c.privateFailure.deadline
	}
	phase, end := context.WithDeadline(context.WithoutCancel(ctx), deadline)
	defer end()
	relayJoined := false
	defer func() {
		if err != nil {
			c.w.channels.fail(err)
		}
		if !relayJoined {
			err = errors.Join(err, c.r.Close(phase))
		}
		err = errors.Join(err, c.private.close(phase), c.w.Close(phase))
		c.w.channels.Close()
	}()
	if readErr != nil {
		return readErr
	}
	terminal, ok := m.(*protocol.PrivateShellExit)
	if !ok || terminal.ActiveOperationID != "" || terminal.CWD != c.w.CWD {
		return fmt.Errorf("invalid idle shell_exit")
	}
	c.exited = terminal
	if err = c.r.Close(phase); err != nil {
		return err
	}
	relayJoined = true
	err = c.w.finishIdleTerminal(phase, "shell_ended", terminal, c.nextFinalResult, census)
	if err == nil {
		c.exitedEOF = true
	}
	return err
}
