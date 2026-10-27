//go:build linux

package main

import (
	"context"
	"strings"
	"testing"
)

func TestRejectInvocationBeforeSessionSetup(t *testing.T) {
	valid := []string{"--session-id", strings.Repeat("a", 32), "--terminal-listen", "127.0.0.1:1", "--telemetry-listen", "127.0.0.1:2", "--columns", "80", "--rows", "24"}
	cases := [][]string{nil, valid[:8], append(append([]string{}, valid...), "extra")}
	for _, change := range []struct {
		index int
		value string
	}{
		{0, "--unknown"}, {2, "--session-id"}, {1, ""}, {1, "invalid-session"},
		{7, "wide"}, {7, "0"}, {7, "1001"}, {9, "tall"}, {9, "-1"},
	} {
		args := append([]string{}, valid...)
		args[change.index] = change.value
		cases = append(cases, args)
	}
	for _, args := range cases {
		if err := run(context.Background(), args); err == nil {
			t.Fatalf("invalid invocation accepted: %q", args)
		}
	}
}
