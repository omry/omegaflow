package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/internal/envoy"
)

func main() {
	config := envoy.DefaultConfig()
	flag.StringVar(&config.TerminalListen, "terminal-listen", config.TerminalListen, "terminal TCP listen coordinate")
	flag.StringVar(&config.TelemetryListen, "telemetry-listen", config.TelemetryListen, "telemetry TCP listen coordinate")
	flag.IntVar(&config.Columns, "columns", config.Columns, "initial terminal columns")
	flag.IntVar(&config.Rows, "rows", config.Rows, "initial terminal rows")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "omegaflow-envoy: positional arguments are not supported")
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := envoy.Run(ctx, config); err != nil {
		fmt.Fprintf(os.Stderr, "omegaflow-envoy: %v\n", err)
		os.Exit(envoy.ExitCode(err))
	}
}
