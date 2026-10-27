//go:build linux

package session

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Run is the dedicated Envoy's assembly boundary. Operation duration remains
// controller-owned; transport, phase deadlines and teardown retain their owners.
func Run(ctx context.Context, sessionID, terminal, telemetry string, columns, rows int64) (err error) {
	if ctx == nil {
		return fmt.Errorf("missing Envoy context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	id, decodeErr := hex.DecodeString(sessionID)
	if decodeErr != nil || len(id) != 16 || hex.EncodeToString(id) != sessionID ||
		columns < 1 || columns > 1000 || rows < 1 || rows > 1000 || os.Geteuid() == 0 {
		return fmt.Errorf("invalid non-root Envoy invocation")
	}
	if err := enableSubreaper(); err != nil {
		return err
	}
	listeners, err := Listen(terminal, telemetry)
	if err != nil {
		return err
	}
	defer listeners.Close()
	channels, launch, cancel, err := listeners.Handshake(ctx, sessionID)
	if err != nil {
		return err
	}
	defer cancel()
	defer channels.Close()
	// Keep the existing transport owner in idle-discard mode for the complete
	// launch-to-relay transition. Public readiness is committed by Launch, so
	// readTerminal still rejects any bytes that arrive before that point.
	if err := channels.ResumeInputContext(launch, ""); err != nil {
		return err
	}
	w, err := channels.Launch(launch, columns, rows)
	if err != nil {
		return err // Launch retains its original failed-startup disposal epoch.
	}
	var relay *Relay
	var coordinator *Coordinator
	var census *processCensus
	defer func() {
		if census != nil {
			defer func() { err = errors.Join(err, census.Close()) }()
		}
		if err == nil {
			return // The selected terminal owner already proved closure and reap.
		}
		deadline := time.Now().Add(finalDrainBudget)
		var start *StartError
		if errors.As(err, &start) && !start.Deadline.IsZero() {
			deadline = start.Deadline
		}
		cleanup, stop := context.WithDeadline(context.Background(), deadline)
		defer stop()
		if coordinator != nil && census != nil {
			err = coordinator.disposeFailure(cleanup, census, err)
			return
		}
		channels.Close()
		if relay != nil {
			err = errors.Join(err, relay.Close(cleanup))
		}
		if coordinator != nil {
			err = errors.Join(err, coordinator.Close(cleanup))
		}
		err = errors.Join(err, w.Close(cleanup))
	}()
	// Pin the original roots under the handshake's retained launch epoch.
	// The lifetime contexts used by the relay/dispatcher have no operation timer.
	census, err = newProcessCensus(launch, w.command.Process.Pid, int(w.ShellPID))
	if err != nil {
		deadline, _ := launch.Deadline()
		err = &StartError{Code: "shell-launch", Deadline: deadline, Cause: err}
		return err
	}
	cancel()
	relay, err = NewRelay(ctx, w)
	if err != nil {
		return err
	}
	coordinator, err = NewCoordinator(ctx, w, relay, NewSplitSetup())
	if err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-channels.Done():
			return channels.Err()
		case <-relay.ReadEnded():
			return coordinator.idleShellExit(ctx, census)
		case <-relay.Done():
			if err := relay.Err(); err != nil {
				return err
			}
			return coordinator.idleShellExit(ctx, census)
		case <-coordinator.private.changed:
			// Observe the existing decoder's queue without consuming a frame.
			// Stale progress notifications after an operation are harmless.
			if len(coordinator.private.events) != 0 || len(coordinator.private.fatal) != 0 {
				return coordinator.idleShellExit(ctx, census)
			}
		case <-channels.Events():
			// A read admitted during the completed operation can enqueue after
			// its result. The idle owner discards it, just like idle socket
			// reads, rather than forwarding it into a later operation.
		case event := <-channels.Controls():
			switch m := event.Message.(type) {
			case *protocol.Execute:
				out, err := coordinator.Start(ctx, *m)
				if err != nil {
					return err
				}
				if out.ShellExit != nil {
					return coordinator.completeStartShellExit(ctx, census, out)
				}
				if out.Started {
					if err := coordinator.completeOperation(lifecycleContext{ctx}, census); err != nil {
						return err
					}
					if coordinator.exited != nil {
						return nil
					}
				}
			case *protocol.Resize:
				if _, err := relay.Resize(ctx, m.Columns, m.Rows); err != nil {
					return err
				}
			case *protocol.Cancel:
				if m.OperationID != coordinator.recent {
					return fmt.Errorf("idle cancel names another operation")
				}
			case *protocol.Finalize:
				if m.OperationID != coordinator.recent {
					return fmt.Errorf("idle finalize names another operation")
				}
			case *protocol.Shutdown:
				return coordinator.shutdownIdle(m.Reason, census)
			default:
				return fmt.Errorf("unexpected idle controller request %T", m)
			}
		}
	}
}
