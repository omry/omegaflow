//go:build linux

package session

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// The Python controller is this process's parent, never a descendant that a
// workload census could terminate. Only this dedicated test Envoy is a
// subreaper. All shell, signal, cleanup and inspection owners are production.
func TestNativePythonClient(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PYTHON_CLIENT") != "1" {
		t.Skip("requires isolated qualified non-root runtime and actual Python client")
	}
	if os.Geteuid() == 0 {
		t.Fatal("native client requires non-root workload")
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	ctx, end := context.WithTimeout(context.Background(), 35*time.Second)
	defer end()
	l, err := Listen("127.0.0.1:0", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	a, b := l.Addresses()
	coordinates, err := json.Marshal(map[string]string{"terminal": a, "telemetry": b, "session_id": testID})
	if err != nil {
		t.Fatal(err)
	}
	fmt.Printf("C3_COORDINATES %s\n", coordinates)
	channels, launch, cancel, err := l.Handshake(ctx, testID)
	if err != nil {
		t.Fatal(err)
	}
	defer channels.Close()
	w, err := channels.Launch(launch, 80, 24)
	cancel()
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), finalDrainBudget)
		defer stop()
		if err := w.Close(cleanup); err != nil {
			t.Error(err)
		}
	}()
	relay, err := NewRelay(ctx, w)
	if err != nil {
		t.Fatal(err)
	}
	c, err := NewCoordinator(ctx, w, relay, NewSplitSetup())
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), finalDrainBudget)
		defer stop()
		_ = relay.Close(cleanup)
		_ = c.Close(cleanup)
	}()
	owner, err := newProcessCensus(ctx, w.command.Process.Pid, int(w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	for {
		select {
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-channels.Done():
			t.Fatal("Python client channel ended before orderly shutdown", channels.Err())
		case e := <-channels.Controls():
			switch m := e.Message.(type) {
			case *protocol.Execute:
				out, err := c.Start(ctx, *m)
				if err != nil || out.ShellExit != nil {
					t.Fatal("actual start", out, err)
				}
				if out.Started {
					// The harness bounds the test, but operation duration belongs
					// to the controller and is not an Envoy context deadline.
					if err := c.completeOperation(lifecycleContext{ctx}, owner); err != nil {
						t.Fatal("actual process/inspection completion", err)
					}
				}
			case *protocol.Resize:
				if _, err := relay.Resize(ctx, m.Columns, m.Rows); err != nil {
					t.Fatal(err)
				}
			case *protocol.Cancel:
				if m.OperationID != c.recent {
					t.Fatalf("unexpected idle cancel operation %q (recent %q)", m.OperationID, c.recent)
				}
				if err := c.publicRequest(ctx, false, nil, m); err != nil {
					t.Fatal("late cancel admission", err)
				}
			case *protocol.Finalize:
				if m.OperationID != c.recent {
					t.Fatalf("unexpected idle finalize operation %q (recent %q)", m.OperationID, c.recent)
				}
				if err := c.publicRequest(ctx, false, nil, m); err != nil {
					t.Fatal("late finalize admission", err)
				}
			case *protocol.Shutdown:
				if err := c.shutdownIdle(m.Reason, owner); err != nil {
					t.Fatal("actual closed/EOF/reap", err)
				}
				fmt.Println("C3_ACTUAL_CLOSED_AND_REAPED")
				return
			default:
				t.Fatalf("unexpected idle controller request %T", m)
			}
		}
	}
}
