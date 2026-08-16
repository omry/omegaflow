package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/internal/envoy"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stderr))
}

func run(args []string, stderr io.Writer) int {
	config := envoy.DefaultConfig()
	flags := flag.NewFlagSet("omegaflow-envoy", flag.ContinueOnError)
	flags.SetOutput(stderr)
	flags.StringVar(&config.TerminalListen, "terminal-listen", config.TerminalListen, "terminal TCP listen coordinate")
	flags.StringVar(&config.TelemetryListen, "telemetry-listen", config.TelemetryListen, "telemetry TCP listen coordinate")
	flags.IntVar(&config.Columns, "columns", config.Columns, "initial terminal columns")
	flags.IntVar(&config.Rows, "rows", config.Rows, "initial terminal rows")
	if err := flags.Parse(args); errors.Is(err, flag.ErrHelp) {
		return 0
	} else if err != nil {
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintln(stderr, "omegaflow-envoy: positional arguments are not supported")
		return 2
	}
	if err := envoy.ValidateConfig(config); err != nil {
		fmt.Fprintf(stderr, "omegaflow-envoy: %v\n", err)
		return 2
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := envoy.Run(ctx, config); err != nil {
		fmt.Fprintf(stderr, "omegaflow-envoy: %v\n", err)
		return envoy.ExitCode(err)
	}
	return 0
}
