//go:build linux

package session

import (
	"context"
	"fmt"
	"os"
	"syscall"
)

// completeOperation supplies concrete process ownership to the existing fact
// dispatcher. The dedicated Envoy creates the census before authored work and
// retains it through this joined transaction; no missing callback means success.
func (c *Coordinator) completeOperation(ctx context.Context, census *processCensus) error {
	if census == nil || c.w.command == nil || c.w.command.Process == nil {
		return fmt.Errorf("operation requires its actual process owner")
	}
	peers := &controlPeers{sessionID: int64(census.awsh), census: census}
	peers.endShell = func(job context.Context, id string, timeout bool) error {
		if c.active == nil || id != c.active.request.OperationID {
			return fmt.Errorf("shell-end cleanup operation mismatch")
		}
		if timeout {
			if err := census.terminateShell(job); err != nil {
				return err
			}
		}
		if err := census.Cleanup(job, 0); err != nil {
			return err
		}
		// Awsh is the selected Bash wait owner. Keep its terminal result pipe
		// live while the existing coordinator consumes shell_exit and EOF.
		select {
		case <-c.w.reaped:
			if c.w.waitErr != nil {
				return c.w.waitErr
			}
		case <-job.Done():
			return job.Err()
		}
		// The final Awsh exit may adopt another child. Reuse the same cleanup
		// epoch and require a fresh empty tree, not just a zero Awsh exit.
		if err := census.Cleanup(job, 0); err != nil {
			return err
		}
		return census.proveEnded(job)
	}
	return c.completeReturn(ctx, nil, peers, census)
}

func (c *processCensus) terminateShell(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.envoy != os.Getpid() {
		return fmt.Errorf("shell termination requires its Envoy owner")
	}
	all, err := c.refresh(ctx)
	if err != nil {
		return err
	}
	ref := c.refs[c.shell]
	if ref == nil {
		return fmt.Errorf("selected shell lost its lifetime pin")
	}
	if p, exists := all[c.shell]; exists && p.Live {
		if err := c.validateRoots(all, int64(c.awsh)); err != nil {
			return err
		}
	}
	return ref.signal(ctx, syscall.SIGKILL)
}

func (c *processCensus) proveEnded(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.envoy != os.Getpid() {
		return fmt.Errorf("ended census requires its Envoy owner")
	}
	all, err := c.refresh(ctx)
	if err != nil {
		return err
	}
	if len(all) != 0 {
		return fmt.Errorf("controlled process remains after shell end")
	}
	return processContext(ctx)
}
