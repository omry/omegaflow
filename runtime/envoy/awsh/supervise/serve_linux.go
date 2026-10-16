//go:build linux

// Package supervise assembles the fixed Awsh private command and return entry.
package supervise

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/signal"
	"sync/atomic"
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
	var session atomic.Pointer[launch.Session]
	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		_ = readControls(ctx, control, events, func(m protocol.PrivateMessage) bool {
			s := session.Load()
			return s != nil && s.AdmitControl(m)
		})
		cancel()
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
	s, err := launch.StartControlled(ctx, h, inputs.BashSHA256, inputs.Target, inputs.Adapter, session.Store)
	if err != nil {
		return err
	}
	defer s.Close()
	// One joined job performs existing blocking Session methods. The dispatcher
	// stays responsive to EOF and invalid controls; it never adds a phase clock.
	var job chan error
	controls := make(chan protocol.PrivateMessage, 1)
	releases := make(chan os.Signal, 1)
	var pending protocol.PrivateMessage
	defer func() {
		cancel()
		if job != nil {
			<-job // join before Session.Close or reuse of owned descriptors
		}
	}()
	begin := func(m *protocol.PrivateExecute) {
		job = make(chan error, 1)
		go func() {
			job <- execute(ctx, s, inputs, *m, controls, releases)
		}()
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case m := <-events:
			switch v := m.(type) {
			case *protocol.PrivateContinue:
				// Admission delivered it to its exact gate. A late matching
				// continue after interruption cannot become another outcome.
			case *protocol.PrivateExecute, *protocol.PrivateShutdown:
				if job != nil {
					pending = m // preceding completed/rejected write is finishing
				} else if command, ok := v.(*protocol.PrivateExecute); ok {
					begin(command)
				} else {
					return s.ShutdownIdle(ctx)
				}
			default:
				controls <- m // admission guarantees at most one pending reply
			}
		case err := <-job:
			job = nil
			if err != nil {
				return err
			}
			if pending != nil {
				m := pending
				pending = nil
				if command, ok := m.(*protocol.PrivateExecute); ok {
					begin(command)
				} else {
					return s.ShutdownIdle(ctx)
				}
			}
		case r := <-s.Reaped:
			if r.Err != nil {
				return r.Err
			}
			if job != nil {
				return fmt.Errorf("selected shell exited during private operation")
			}
			status := r.Status.ExitStatus()
			if r.Status.Signaled() {
				status = 128 + int(r.Status.Signal())
			}
			return s.ReportIdleExit(ctx, int64(status))
		case signal := <-s.Signals:
			if !s.AdmitSignal(signal) {
				return fmt.Errorf("unexpected release signal")
			}
			releases <- signal
		}
	}
}

// Admit in the sole decoder, before buffering can carry a premature request
// across a phase boundary. Locked Session methods still own final validation.
func readControls(ctx context.Context, control io.Reader, events chan<- protocol.PrivateMessage, admit func(protocol.PrivateMessage) bool) error {
	reader := bufio.NewReader(control)
	for {
		m, err := protocol.ReadPrivate(reader, protocol.EnvoyToAwsh)
		if err != nil {
			return err
		}
		if !admit(m) {
			return fmt.Errorf("private request outside current phase")
		}
		select {
		case events <- m:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func execute(ctx context.Context, s *launch.Session, inputs runtimebundle.Inputs, m protocol.PrivateExecute, controls <-chan protocol.PrivateMessage, releases <-chan os.Signal) error {
	if err := s.BeginStart(ctx, m, inputs.BashSHA256, inputs.Target, inputs.Adapter); err != nil {
		return err
	}
	if !s.HasActiveOperation() {
		return nil // output-empty source rejection left the idle shell intact
	}
	for i := 0; i < 2; i++ {
		if err := s.AcceptStartHelper(); err != nil {
			return err
		}
	}
	for i := 0; i < 2; i++ {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case control := <-controls:
			if err := s.HandleStartControl(control); err != nil {
				return err
			}
		}
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case signal := <-releases:
		if err := s.HandleStartSignal(signal); err != nil {
			return err
		}
	}
	for {
		gated, err := s.BeginOperationEvent(ctx)
		if err != nil {
			return err
		}
		if !gated {
			break
		}
		if err := s.WaitGate(); err != nil {
			return err
		}
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case control := <-controls:
		if err := s.HandleCompletionControl(control); err != nil {
			return err
		}
	}
	return s.FinishCompletion()
}
