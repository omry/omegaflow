//go:build linux

package session

import (
	"context"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestOrdinaryReturnRequiresConcreteOwner(t *testing.T) {
	f := completionFixture(t)
	if err := f.c.completeOrdinary(relayTestContext(t), nil); err == nil {
		t.Fatal("missing process owner authorized actual ordinary return")
	}
	if f.c.active == nil || f.c.busy.Load() {
		t.Fatal("missing owner changed operation ownership")
	}
}

func TestOrdinaryReturnRejectsFoundationOrMixedOwnership(t *testing.T) {
	for _, mixed := range []bool{false, true} {
		t.Run(map[bool]string{false: "foundation-workload", true: "mixed-cleanup-peer"}[mixed], func(t *testing.T) {
			f := completionFixture(t)
			var called atomic.Bool
			var peer ReturnCleanup
			if mixed {
				peer = func(context.Context, string, int64) error {
					called.Store(true)
					return nil
				}
			}
			// The foundation fixture has no launched Awsh identity. A caller
			// cannot manufacture real cleanup authority with a numeric census
			// or a successful deterministic callback.
			owner := &processCensus{envoy: os.Getpid(), awsh: 1, shell: 2}
			err := f.c.completeReturn(relayTestContext(t), peer, nil, owner)
			if err == nil || !strings.Contains(err.Error(), "process owner does not match workload") || called.Load() {
				t.Fatal("unproved process ownership authorized cleanup", err, called.Load())
			}
			f.control.SetReadDeadline(time.Now().Add(30 * time.Millisecond))
			var b [1]byte
			if n, err := f.control.Read(b[:]); n != 0 || err == nil {
				t.Fatal("invalid process owner released a completion helper", n, err)
			}
		})
	}
}
