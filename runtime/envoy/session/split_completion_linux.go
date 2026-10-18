//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"syscall"
)

// Existing setup wrappers expose the same resource owner. A setup-only peer
// cannot authorize real split source release or successful split completion.
func (c *Coordinator) splitResources() *SplitResources {
	owner, ok := c.setup.(interface{ splitResources() *SplitResources })
	if !ok {
		return nil
	}
	return owner.splitResources()
}

func (s *SplitResources) closeKeepalives(ctx context.Context, id string) error {
	if s == nil || s.operation == nil || s.operation.id != id {
		return fmt.Errorf("split cleanup operation mismatch")
	}
	deadline, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || !s.operation.cleanupEnd.IsZero() {
		return fmt.Errorf("split cleanup requires its original live cleanup epoch")
	}
	if err := s.validate(); err != nil {
		return err
	}
	op := s.operation
	op.cleanupEnd = deadline
	var result error
	for i, fd := range op.keepalives {
		result = errors.Join(result, syscall.Close(fd))
		op.keepalives[i] = -1 // Never retry Close on a possibly reused descriptor.
	}
	return errors.Join(result, ctx.Err())
}

func (s *SplitResources) complete(ctx context.Context, id string) error {
	if s == nil || s.operation == nil || s.operation.id != id {
		return fmt.Errorf("split completion operation mismatch")
	}
	deadline, ok := ctx.Deadline()
	if !ok || !deadline.Equal(s.operation.cleanupEnd) || ctx.Err() != nil {
		return fmt.Errorf("split completion cleanup epoch mismatch")
	}
	for _, fd := range s.operation.keepalives {
		if fd >= 0 {
			return fmt.Errorf("split completion before keepalive closure")
		}
	}
	if err := s.validate(); err != nil {
		return err
	}
	return s.dispose(ctx, id)
}

// Called only after the existing ordinary cleanup or shell-end peer succeeds.
// The existing cleanup deadline covers both EOFs, readers and owned removals.
func (c *Coordinator) finishSplit(ctx context.Context) error {
	if c.active.request.ExecutionShape != "split" {
		return nil
	}
	id := c.active.request.OperationID
	owner := c.splitResources()
	if owner == nil {
		return fmt.Errorf("split completion requires its attached resource owner")
	}
	var done <-chan struct{}
	if _, err := c.r.boundary(ctx, false, func(int64) error {
		if c.r.split == nil || c.r.split.id != id {
			return fmt.Errorf("split completion resource operation mismatch")
		}
		done = c.r.split.done
		return nil
	}); err != nil {
		return err
	}
	if err := owner.closeKeepalives(ctx, id); err != nil {
		return err
	}
	select {
	case <-done:
	case <-ctx.Done():
		return ctx.Err()
	case <-c.r.Done():
		return errors.Join(fmt.Errorf("split reader failed before both EOFs"), c.r.Err())
	}
	if err := c.r.stopSplit(ctx); err != nil {
		return err
	}
	if err := owner.complete(ctx, id); err != nil {
		return err
	}
	c.active.splitAttached.Store(false)
	c.active.setup = false
	return nil
}
