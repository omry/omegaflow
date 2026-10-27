//go:build linux

package session

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestRunRejectsCancelledSessionBeforeProcessSetup(t *testing.T) {
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := Run(ctx, strings.Repeat("a", 32), "127.0.0.1:0", "127.0.0.1:0", 80, 24); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled Run: %v", err)
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatalf("cancelled entry changed process-wide subreaper state: %d -> %d: %v", before, after, err)
	}
}
