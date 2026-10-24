//go:build linux

package session

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestFailedDisposalRequiresExactOwnerAndEpoch(t *testing.T) {
	cause := errors.New("original transport failure")
	c := &Coordinator{w: &Workload{ShellPID: 42, command: &exec.Cmd{Process: &os.Process{Pid: 41}}, channels: &Channels{}}, r: &Relay{}, private: &privateResults{}}
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	owner := &processCensus{envoy: os.Getpid(), awsh: 41, shell: 42}
	for _, tc := range []struct {
		name  string
		ctx   context.Context
		owner *processCensus
		cause error
	}{
		{"no-cause", ctx, owner, nil}, {"no-context", nil, owner, cause},
		{"no-deadline", context.Background(), owner, cause}, {"no-owner", ctx, nil, cause},
		{"foreign-envoy", ctx, &processCensus{envoy: os.Getpid() + 1, awsh: 41, shell: 42}, cause},
		{"foreign-awsh", ctx, &processCensus{envoy: os.Getpid(), awsh: 43, shell: 42}, cause},
		{"foreign-shell", ctx, &processCensus{envoy: os.Getpid(), awsh: 41, shell: 44}, cause},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if c.disposeFailure(tc.ctx, tc.owner, tc.cause) == nil || c.failure != nil || c.busy.Load() {
				t.Fatal("invalid disposal changed ownership")
			}
		})
	}
	c.busy.Store(true)
	if c.disposeFailure(ctx, owner, cause) == nil || c.failure != nil || !c.busy.Load() {
		t.Fatal("active operation was disposed before its owner joined")
	}
}

func TestFatalRetainsSelectedChannelCauseAndCallerEpoch(t *testing.T) {
	lifetime, cancel := context.WithCancel(context.Background())
	defer cancel()
	channels := &Channels{ctx: lifetime, cancel: cancel, done: make(chan struct{})}
	channels.fail(io.ErrUnexpectedEOF)
	c := &Coordinator{w: &Workload{channels: channels}}
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	deadline, _ := ctx.Deadline()
	err := c.fatal(ctx, "operation-cleanup", context.Canceled, true)
	var failure *StartError
	if !errors.As(err, &failure) || !errors.Is(err, io.ErrUnexpectedEOF) || !errors.Is(err, context.Canceled) || !failure.Deadline.Equal(deadline) || failure.Code != "operation-cleanup" {
		t.Fatal("channel class or caller epoch replaced by joined I/O cancellation", err)
	}
	if channels.Err() != io.ErrUnexpectedEOF {
		t.Fatal("first selected transport failure changed", channels.Err())
	}
}
