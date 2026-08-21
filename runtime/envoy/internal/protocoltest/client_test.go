package protocoltest

import (
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func acceptEvent(t *testing.T, client *Client, message any) {
	t.Helper()
	frame, err := protocol.EncodeEnvoy(message)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range frame {
		if _, err := client.Accept([]byte{value}); err != nil {
			t.Fatalf("%T: %v", message, err)
		}
	}
}

func splitPolicy() protocol.ExecutionPolicy {
	return protocol.ExecutionPolicy{
		ExecutionShape: protocol.ExecutionSplit,
		Timing:         protocol.TimingPresentation,
		Publication:    protocol.PublicationReal,
		Observation:    protocol.ObservationShared,
	}
}

func TestClientDrivesCompleteSession(t *testing.T) {
	client := NewClient()
	if _, err := client.Hello("session-1"); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.Ready{Seq: 1, EnvoyPID: 41, ShellPID: 42, CWD: "/work", Columns: 80, Rows: 24})
	if _, err := client.Execute("op-1", "printf ok", splitPolicy()); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.OperationStarted{Seq: 2, OperationID: "op-1", OutputStart: 0})
	acceptEvent(t, client, protocol.OperationReady{Seq: 3, OperationID: "op-1", GateID: "gate-1", OutputThrough: 3})
	if _, err := client.Continue("op-1", "gate-1"); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.OperationContinued{Seq: 4, OperationID: "op-1", GateID: "gate-1", OutputThrough: 3})
	acceptEvent(t, client, protocol.OperationCompleted{Seq: 5, OperationID: "op-1", Status: 0, CWD: "/work", OutputStart: 0, OutputThrough: 6})
	if _, err := client.Shutdown("capture-complete"); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.Draining{Seq: 6, Reason: "capture-complete", OutputThrough: 6})
	acceptEvent(t, client, protocol.Closed{Seq: 7, Reason: "shutdown", OutputThrough: 6})
	if err := client.Finish(); err != nil {
		t.Fatal(err)
	}
	if client.Phase() != protocol.PhaseClosed || client.OutputThrough() != 6 {
		t.Fatalf("unexpected final state: %s at %d", client.Phase(), client.OutputThrough())
	}
}

func TestClientRejectsEOFBeforeClosed(t *testing.T) {
	for _, test := range []struct {
		name     string
		draining bool
		want     string
	}{
		{name: "ready", want: "telemetry closed before closed event (phase idle)"},
		{name: "draining", draining: true, want: "telemetry closed before closed event (phase draining)"},
	} {
		t.Run(test.name, func(t *testing.T) {
			client := NewClient()
			if _, err := client.Hello("session-1"); err != nil {
				t.Fatal(err)
			}
			acceptEvent(t, client, protocol.Ready{Seq: 1, EnvoyPID: 41, ShellPID: 42, CWD: "/work", Columns: 80, Rows: 24})
			if test.draining {
				if _, err := client.Shutdown("capture-complete"); err != nil {
					t.Fatal(err)
				}
				acceptEvent(t, client, protocol.Draining{Seq: 2, Reason: "capture-complete", OutputThrough: 0})
			}
			if err := client.Finish(); err == nil || err.Error() != test.want {
				t.Fatalf("Finish() = %v, want %q", err, test.want)
			}
		})
	}
}

func TestClientDrivesPlannedFinalization(t *testing.T) {
	client := NewClient()
	if _, err := client.Hello("session-1"); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.Ready{Seq: 1, EnvoyPID: 41, ShellPID: 42, CWD: "/work", Columns: 80, Rows: 24})
	policy := splitPolicy()
	policy.Observation = protocol.ObservationExclusive
	if _, err := client.Execute("server", "serve_forever", policy); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.OperationStarted{Seq: 2, OperationID: "server", OutputStart: 0})
	acceptEvent(t, client, protocol.OperationOutput{Seq: 3, OperationID: "server", Stream: "stdout", DataBase64: "cmVhZHkK"})
	if _, err := client.Finalize("server", "recording-end"); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.OperationFinalized{
		Seq: 4, OperationID: "server", CWD: "/work", Reason: "recording-end",
		OutputStart: 0, OutputThrough: 6,
	})
	if _, err := client.Shutdown("capture-complete"); err != nil {
		t.Fatal(err)
	}
	acceptEvent(t, client, protocol.Draining{Seq: 5, Reason: "capture-complete", OutputThrough: 6})
	acceptEvent(t, client, protocol.Closed{Seq: 6, Reason: "shutdown", OutputThrough: 6})
	if err := client.Finish(); err != nil {
		t.Fatal(err)
	}
}

func TestClientDoesNotAdvanceAfterInvalidRequest(t *testing.T) {
	client := NewClient()
	if _, err := client.Execute("op-1", "true", splitPolicy()); err == nil {
		t.Fatal("expected out-of-state request to fail")
	}
	frame, err := client.Hello("session-1")
	if err != nil {
		t.Fatal(err)
	}
	message, err := protocol.DecodeController(frame)
	if err != nil {
		t.Fatal(err)
	}
	if message.(protocol.Hello).Seq != 1 {
		t.Fatalf("invalid request consumed sequence: %#v", message)
	}
}
