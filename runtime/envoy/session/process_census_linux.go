//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"sync"
)

// processCensus belongs to one dedicated Envoy. Its only initial child is Awsh;
// every later adopted non-shell child belongs to the controlled operation.
// B6.2 owns termination/reaping; B6.3/B6.5 own assembly with operation lifecycle.
type processCensus struct {
	mu          sync.Mutex
	envoy, awsh int
	shell       int
	refs        map[int]*processRef
	closed      bool
}

func newProcessCensus(ctx context.Context, awsh, shell int) (_ *processCensus, err error) {
	if err := processContext(ctx); err != nil {
		return nil, err
	}
	if err := requireSubreaper(); err != nil {
		return nil, err
	}
	if awsh <= 0 || shell <= 0 || awsh == shell || awsh == os.Getpid() || shell == os.Getpid() {
		return nil, fmt.Errorf("invalid controlled roots")
	}
	c := &processCensus{envoy: os.Getpid(), awsh: awsh, shell: shell, refs: make(map[int]*processRef)}
	defer func() {
		if err != nil {
			err = errors.Join(err, c.Close())
		}
	}()
	for _, pid := range []int{awsh, shell} {
		r, _, e := pinProcess(ctx, pid)
		if e != nil {
			return nil, e
		}
		c.refs[pid] = r
	}
	if err = c.ProveBoundary(ctx, int64(awsh)); err != nil {
		return nil, err
	}
	return c, nil
}

// ReadDir batches keep scanning responsive to the original caller epoch and
// impose no protocol-level numeric process admission ceiling.
func readProcesses(ctx context.Context) (map[int]processInfo, error) {
	if err := processContext(ctx); err != nil {
		return nil, err
	}
	dir, err := os.Open("/proc")
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	all := make(map[int]processInfo)
	for {
		entries, err := dir.ReadDir(128)
		if err != nil && err != io.EOF {
			return nil, err
		}
		for _, entry := range entries {
			if e := processContext(ctx); e != nil {
				return nil, e
			}
			pid, e := strconv.Atoi(entry.Name())
			if e != nil || pid <= 0 || !entry.IsDir() {
				continue
			}
			p, e := readProcess(pid)
			if processGone(e) {
				continue
			}
			if e != nil {
				return nil, e // An unreadable member cannot silently disappear.
			}
			all[pid] = p
		}
		if err == io.EOF {
			return all, processContext(ctx)
		}
	}
}

func (c *processCensus) scan(ctx context.Context) (map[int]processInfo, error) {
	_, controlled, err := c.scanWithAll(ctx)
	return controlled, err
}

// scanWithAll returns the same complete /proc observation used to update the
// controlled tree together with the controlled subset. Members must classify
// its foreground sample against this observation; a later read cannot infer
// ownership from a possibly stale numeric PID map.
func (c *processCensus) scanWithAll(ctx context.Context) (map[int]processInfo, map[int]processInfo, error) {
	if c.closed {
		return nil, nil, os.ErrClosed
	}
	if err := requireSubreaper(); err != nil {
		return nil, nil, err
	}
	all, err := readProcesses(ctx)
	if err != nil {
		return nil, nil, err
	}
	controlled := make(map[int]processInfo)
	for pid, ref := range c.refs {
		p, err := ref.observe(ctx)
		if processGone(err) {
			// Keep root handles so a reused root PID never becomes a new root.
			if pid != c.awsh && pid != c.shell {
				if err := ref.close(); err != nil {
					return nil, nil, err
				}
				delete(c.refs, pid)
			}
			continue
		}
		if err != nil {
			return nil, nil, err
		}
		controlled[pid] = p
	}
	for added := true; added; {
		added = false
		for pid, candidate := range all {
			if _, known := c.refs[pid]; known || pid == c.envoy {
				continue
			}
			if _, parent := controlled[candidate.Parent]; !parent && candidate.Parent != c.envoy {
				continue
			}
			ref, p, err := pinProcess(ctx, pid)
			if processGone(err) {
				continue
			}
			if err != nil {
				return nil, nil, err
			}
			// Recheck ancestry after acquiring the lifetime pin. Reparenting is
			// expected; a stale numeric parent must not admit a foreign process.
			parent := c.refs[p.Parent]
			if p.Parent != c.envoy {
				if parent == nil {
					if err := ref.close(); err != nil {
						return nil, nil, err
					}
					continue
				}
				if _, err := parent.observe(ctx); err != nil {
					closeErr := ref.close()
					if processGone(err) && closeErr == nil {
						continue
					}
					return nil, nil, errors.Join(err, closeErr)
				}
			}
			c.refs[pid], controlled[pid] = ref, p
			added = true
		}
	}
	return all, controlled, processContext(ctx)
}

func sameProcesses(a, b map[int]processInfo) bool {
	if len(a) != len(b) {
		return false
	}
	for pid, p := range a {
		if other, ok := b[pid]; !ok || other != p {
			return false
		}
	}
	return true
}

// refresh requires two successive matching complete observations. Callers repeat
// it across cleanup, EOF and final drain; no one snapshot freezes future forks.
func (c *processCensus) refresh(ctx context.Context) (map[int]processInfo, error) {
	var previous map[int]processInfo
	for {
		current, err := c.scan(ctx)
		if err != nil {
			return nil, err
		}
		if previous != nil && sameProcesses(previous, current) {
			return current, nil
		}
		previous = current
	}
}

func (c *processCensus) Census(ctx context.Context) (map[int]processInfo, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.refresh(ctx)
}

func (c *processCensus) ProveBoundary(ctx context.Context, sid int64) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	all, err := c.refresh(ctx)
	if err != nil {
		return err
	}
	return c.validateRoots(all, sid)
}

func (c *processCensus) validateRoots(all map[int]processInfo, sid int64) error {
	a, b := all[c.awsh], all[c.shell]
	if sid != int64(c.awsh) || !a.Live || !b.Live || a.Parent != c.envoy ||
		a.Session != c.awsh || a.Group != c.awsh || b.Parent != c.awsh || b.Session != c.awsh || b.Group != c.shell {
		return fmt.Errorf("controlled session topology changed")
	}
	return nil
}

func (c *processCensus) ProveIdle(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	all, err := c.refresh(ctx)
	if err != nil {
		return err
	}
	if err := c.validateRoots(all, int64(c.awsh)); err != nil {
		return err
	}
	for pid := range all {
		if pid != c.awsh && pid != c.shell {
			return fmt.Errorf("operation process remains at idle boundary")
		}
	}
	return nil
}

func (c *processCensus) Members(ctx context.Context, pgid int64) ([]foregroundMember, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if pgid <= 0 || pgid > 2147483647 {
		return nil, fmt.Errorf("invalid foreground group")
	}
	if _, err := c.refresh(ctx); err != nil {
		return nil, err
	}
	return c.membersAfterRefresh(ctx, pgid)
}

func (c *processCensus) membersAfterRefresh(ctx context.Context, pgid int64) ([]foregroundMember, error) {
	all, controlled, err := c.scanWithAll(ctx)
	if err != nil {
		return nil, err
	}
	return c.foregroundMembers(ctx, pgid, all, controlled)
}

// foregroundMembers classifies one complete /proc sample against the
// controlled subset produced from that same sample. If a retained descriptor
// exits while the sample still names its PID, return an explicit proof error
// under the original caller context without replacing that lifetime.
func (c *processCensus) foregroundMembers(ctx context.Context, pgid int64, all, controlled map[int]processInfo) ([]foregroundMember, error) {
	var members []foregroundMember
	for pid, candidate := range all {
		if candidate.Group != int(pgid) {
			continue
		}
		_, owned := controlled[pid]
		ref := c.refs[pid]
		if owned && ref == nil {
			return nil, fmt.Errorf("controlled foreground member lost its lifetime pin")
		}
		if owned {
			p, err := ref.observe(ctx)
			if processGone(err) {
				return nil, fmt.Errorf("foreground member lifetime ended during proof: %w", err)
			} else if err != nil {
				return nil, err
			}
			if p.Live && p.Group == int(pgid) {
				members = append(members, foregroundMember{PID: int64(pid), PGID: pgid, SessionID: int64(p.Session), Controlled: true})
			}
			continue
		}

		ref, p, err := pinProcess(ctx, pid)
		if processGone(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		p, err = ref.observe(ctx)
		if !owned {
			err = errors.Join(err, ref.close())
		}
		if processGone(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if p.Live && p.Group == int(pgid) {
			members = append(members, foregroundMember{PID: int64(pid), PGID: pgid, SessionID: int64(p.Session), Controlled: false})
		}
	}
	return members, processContext(ctx)
}

func (c *processCensus) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	var err error
	for _, ref := range c.refs {
		err = errors.Join(err, ref.close())
	}
	c.refs = nil
	return err
}
