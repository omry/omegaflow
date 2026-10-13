//go:build linux

// Package supervise assembles the fixed Awsh startup entry and idle shutdown.
package supervise

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/awsh/launch"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"github.com/omry/omegaflow/runtime/envoy/runtimebundle"
)

// Run uses only the fixed descriptor arguments. Envoy's existing phase owner
// cancels through control EOF or termination; no Awsh lifecycle timer is added.
func Run(args []string) error {
	h, err := launch.ParseHandoff(args)
	if err != nil {
		return err
	}
	if os.Geteuid() == 0 {
		return fmt.Errorf("workload identity must be non-root")
	}
	if err := h.Validate(); err != nil {
		return err
	}
	if err := syscall.SetNonblock(h.Control, true); err != nil {
		return err
	}
	// The supervisor owns this inherited descriptor for the full controlled
	// session. Session.Close borrows it; the final defer closes the owner to wake
	// the reader, then joins the reader, so cancellation cannot race raw-fd
	// validation or reuse the descriptor number.
	control := os.NewFile(uintptr(h.Control), "awsh-control")
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM)
	ctx, cancel := context.WithCancel(ctx)
	defer stop()
	defer cancel()
	events := make(chan protocol.PrivateMessage, 1)
	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		reader := bufio.NewReader(control)
		for {
			m, err := protocol.ReadPrivate(reader, protocol.EnvoyToAwsh)
			if err != nil {
				cancel()
				return
			}
			select {
			case events <- m:
			case <-ctx.Done():
				return
			}
		}
	}()
	defer func() {
		cancel()
		// Keep the owner alive through Session.Close; then wake and join the
		// reader before returning from the supervisor.
		control.Close()
		<-readDone
	}()
	inputs, err := runtimebundle.Load(ctx)
	if err != nil {
		launch.ReportLaunchFailure(ctx, h.Result)
		return err
	}
	s, err := launch.StartControlled(ctx, h, inputs.BashSHA256, inputs.Target, inputs.Adapter)
	if err != nil {
		return err
	}
	defer s.Close()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case m := <-events:
			if _, ok := m.(*protocol.PrivateShutdown); !ok {
				return fmt.Errorf("private request outside implemented idle startup boundary")
			}
			return s.ShutdownIdle(ctx)
		case r := <-s.Reaped:
			if r.Err != nil {
				return r.Err
			}
			status := r.Status.ExitStatus()
			if r.Status.Signaled() {
				status = 128 + int(r.Status.Signal())
			}
			return s.ReportIdleExit(ctx, int64(status))
		case <-s.Signals:
			return fmt.Errorf("unexpected release signal at idle startup boundary")
		}
	}
}
