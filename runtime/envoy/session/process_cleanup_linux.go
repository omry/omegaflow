//go:build linux

package session

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"syscall"
	"time"
)

// Cleanup terminates authored execution and reaps Envoy's adopted children.
// The caller supplies the existing cleanup epoch. A nonzero helper PID must
// come from Awsh's validated input_close, not an authored source request.
// B6.3 owns input_closed, Bash wait-record clearance, EOF and final drain.
func (c *processCensus) Cleanup(ctx context.Context, helperPID int64) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := processContext(ctx); err != nil {
		return err
	}
	if c.closed || c.envoy != os.Getpid() {
		return fmt.Errorf("cleanup requires its live Envoy census owner")
	}
	if helperPID < 0 || helperPID > 2147483647 || int(helperPID) == c.awsh || int(helperPID) == c.shell {
		return fmt.Errorf("invalid cleanup helper identity")
	}
	var helper *processRef
	if helperPID != 0 {
		var err error
		helper, err = c.cleanupHelper(ctx, int(helperPID))
		if err != nil {
			return err
		}
	}
	for {
		all, err := c.refresh(ctx)
		if err != nil {
			return err
		}
		if helper != nil {
			p, ok := all[helper.pid]
			if !ok || !p.Live || c.refs[helper.pid] != helper {
				return fmt.Errorf("cleanup helper lifetime changed or exited")
			}
		}
		pending := false
		for pid, p := range all {
			if pid == c.awsh || pid == c.shell || (helper != nil && c.refs[pid] == helper) {
				continue
			}
			ref := c.refs[pid]
			if ref == nil {
				return fmt.Errorf("cleanup member has no lifetime pin")
			}
			if p.Live {
				if err := ref.signal(ctx, syscall.SIGKILL); err != nil {
					return fmt.Errorf("terminate controlled process: %w", err)
				}
				pending = true
				continue
			}
			if p.Parent == c.envoy {
				if err := ref.reapAdopted(ctx, c.envoy); err != nil {
					return err
				}
				pending = true // Require a fresh complete census after a reap.
				continue
			}
			// Exited direct Bash children have no live descriptors. Their
			// wait records belong to the existing Bash completion hook.
			if p.Parent != c.shell {
				pending = true // Intermediate parents must reap or yield adoption.
			}
		}
		if !pending {
			return processContext(ctx)
		}
		// Scheduling delay only: the caller's existing deadline remains the
		// sole cleanup epoch, including when a task cannot finish exiting.
		pause := time.NewTimer(time.Millisecond)
		select {
		case <-ctx.Done():
			pause.Stop()
			return ctx.Err()
		case <-pause.C:
		}
	}
}

func (c *processCensus) cleanupHelper(ctx context.Context, pid int) (*processRef, error) {
	all, err := c.refresh(ctx)
	if err != nil {
		return nil, err
	}
	p, ok := all[pid]
	ref, awsh := c.refs[pid], c.refs[c.awsh]
	if !ok || ref == nil || awsh == nil || !p.Live || p.Parent != c.shell || p.Session != c.awsh {
		return nil, fmt.Errorf("completion helper is not a live selected-shell child")
	}
	// Helper and external Awsh use the same manifested executable. Keep both
	// lifetime pins valid around the executable observation as well.
	if _, err := awsh.observe(ctx); err != nil {
		return nil, err
	}
	want, err := os.Stat("/proc/" + strconv.Itoa(c.awsh) + "/exe")
	if err != nil {
		return nil, err
	}
	got, err := os.Stat("/proc/" + strconv.Itoa(pid) + "/exe")
	if err != nil {
		return nil, err
	}
	if !os.SameFile(want, got) {
		return nil, fmt.Errorf("completion helper executable changed")
	}
	if _, err := ref.observe(ctx); err != nil {
		return nil, err
	}
	if _, err := awsh.observe(ctx); err != nil {
		return nil, err
	}
	return ref, processContext(ctx)
}

func (r *processRef) signal(ctx context.Context, sig syscall.Signal) error {
	p, err := r.observe(ctx)
	if processGone(err) {
		return processContext(ctx)
	}
	if err != nil {
		return err
	}
	if !p.Live {
		return processContext(ctx)
	}
	for {
		if err := processContext(ctx); err != nil {
			return err
		}
		_, _, e := syscall.Syscall6(424, uintptr(r.fd), uintptr(sig), 0, 0, 0, 0) // pidfd_send_signal
		if e == syscall.EINTR {
			continue
		}
		if e != 0 && e != syscall.ESRCH {
			return e
		}
		return processContext(ctx)
	}
}

func (r *processRef) reapAdopted(ctx context.Context, envoy int) error {
	p, err := r.observe(ctx)
	if processGone(err) {
		return processContext(ctx)
	}
	if err != nil {
		return err
	}
	if p.Parent != envoy || envoy != os.Getpid() {
		return fmt.Errorf("reap requires an Envoy-owned adopted child")
	}
	if p.Live {
		return nil
	}
	// This exact unreaped child cannot recycle its PID before this sole
	// owner waits. Never wait for any child or consume Awsh/Bash's waits.
	for {
		if err := processContext(ctx); err != nil {
			return err
		}
		var status syscall.WaitStatus
		pid, err := syscall.Wait4(r.pid, &status, syscall.WNOHANG, nil)
		if err == syscall.EINTR {
			continue
		}
		if err != nil {
			return fmt.Errorf("reap adopted child: %w", err)
		}
		if pid != 0 && pid != r.pid {
			return fmt.Errorf("reaped a different process lifetime")
		}
		return processContext(ctx)
	}
}
