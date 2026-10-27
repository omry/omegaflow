//go:build linux

package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/inspection"
	"github.com/omry/omegaflow/runtime/envoy/session"
)

func run(ctx context.Context, args []string) error {
	values := map[string]string{}
	if len(args) != 10 {
		return fmt.Errorf("Envoy requires --session-id, --terminal-listen, --telemetry-listen, --columns and --rows")
	}
	for i := 0; i < len(args); i += 2 {
		key, value := args[i], args[i+1]
		switch key {
		case "--session-id", "--terminal-listen", "--telemetry-listen", "--columns", "--rows":
		default:
			return fmt.Errorf("unknown Envoy argument %q", key)
		}
		if _, exists := values[key]; exists || value == "" {
			return fmt.Errorf("duplicate or empty Envoy argument %q", key)
		}
		values[key] = value
	}
	columns, err := strconv.ParseInt(values["--columns"], 10, 64)
	if err != nil {
		return fmt.Errorf("invalid --columns: %w", err)
	}
	rows, err := strconv.ParseInt(values["--rows"], 10, 64)
	if err != nil {
		return fmt.Errorf("invalid --rows: %w", err)
	}
	return session.Run(ctx, values["--session-id"], values["--terminal-listen"], values["--telemetry-listen"], columns, rows)
}

func main() {
	// Worker dispatch precedes signal/session setup and retains its empty-env,
	// private-pipe entry contract. It cannot become a public session invocation.
	if len(os.Args) > 1 && os.Args[1] == inspection.WorkerModeArgument {
		if inspection.RunWorkerMode(os.Args[1:]) != nil {
			os.Exit(2)
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
