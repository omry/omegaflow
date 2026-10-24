//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"io"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func forwardGateTestInput(t *testing.T, ctx context.Context, relay *Relay, operation, want string) {
	t.Helper()
	for offset := 0; offset < len(want); {
		var event Event
		select {
		case event = <-relay.w.channels.Events():
		case <-ctx.Done():
			t.Fatal("gate input did not arrive", ctx.Err())
		}
		if event.InputOperation != operation || len(event.Input) == 0 || len(event.Input) > len(want)-offset || !bytes.Equal(event.Input, []byte(want[offset:offset+len(event.Input)])) {
			t.Fatal("gate input prefix changed", offset, event)
		}
		if err := relay.ForwardInput(ctx, event); err != nil {
			t.Fatal(err)
		}
		offset += len(event.Input)
	}
}

func TestGateInputWatermarkKeepsInputEligibleAndReadsPastBoundary(t *testing.T) {
	f := completionFixture(t)
	s := f.c.w.channels
	ctx := relayTestContext(t)
	done := make(chan error, 1)
	go func() { done <- s.WaitGateInput(ctx, "operation-1", 3) }()
	if _, err := f.terminal.Write([]byte("ab")); err != nil {
		t.Fatal(err)
	}
	forwardGateTestInput(t, ctx, f.r, "operation-1", "ab")
	select {
	case err := <-done:
		t.Fatal("released before watermark", err)
	default:
	}
	if _, err := f.terminal.Write([]byte("cdef")); err != nil {
		t.Fatal(err)
	}
	forwardGateTestInput(t, ctx, f.r, "operation-1", "cdef")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
	var actual [6]byte
	if _, err := io.ReadFull(f.slave, actual[:]); err != nil || string(actual[:]) != "abcdef" {
		t.Fatal("gated PTY input changed", string(actual[:]), err)
	}
	if err := s.WaitGateInput(ctx, "operation-1", 2); err == nil {
		t.Fatal("regressing watermark accepted")
	}
	if err := s.WaitGateInput(ctx, "other", 6); err == nil {
		t.Fatal("wrong operation accepted")
	}
	if err := s.WaitGateInput(context.Background(), "operation-1", 6); err == nil {
		t.Fatal("missing original epoch accepted")
	}
}

func TestGatePublicationQueuesInputUntilJoinedWrite(t *testing.T) {
	for _, kind := range []string{"gate-event", "private-continue"} {
		t.Run(kind, func(t *testing.T) {
			f := completionFixture(t)
			ctx := relayTestContext(t)
			f.c.phase = awaitInputClose
			f.c.publishingGate = kind == "gate-event"
			if kind == "private-continue" {
				d := &operationDispatch{ctx: ctx, life: newOperationLifecycle("operation-1"), selection: "continue"}
				f.c.running = d
				defer func() { d.close(); f.c.running = nil }()
			}
			if _, err := f.terminal.Write([]byte("queued\n")); err != nil {
				t.Fatal(err)
			}
			for f.c.w.channels.InputThrough() != int64(len("queued\n")) {
				select {
				case <-ctx.Done():
					t.Fatal("actual input was not read into the existing transport queue")
				default:
					time.Sleep(time.Millisecond)
				}
			}
			release, entered, done := make(chan struct{}), make(chan struct{}), make(chan error, 1)
			go func() {
				done <- f.c.work(ctx, false, func(job context.Context) error {
					close(entered)
					select {
					case <-release:
						if kind == "private-continue" {
							return SendPrivate(job, f.c.w.Control, protocol.PrivateContinue{OperationID: "operation-1", GateID: "gate"})
						}
						return nil
					case <-job.Done():
						return job.Err()
					}
				})
			}()
			<-entered
			select {
			case err := <-done:
				t.Fatal("queued input cancelled the active publication worker", err)
			case <-time.After(20 * time.Millisecond):
			}
			close(release)
			if err := finishCompletion(t, done); err != nil {
				t.Fatal("terminal input cancelled an in-flight gate publication", err)
			}
			f.c.publishingGate = false
			if kind == "private-continue" {
				if m, ok := f.readPrivate(t).(*protocol.PrivateContinue); !ok || m.GateID != "gate" {
					t.Fatal("actual private continue frame missing", m)
				}
				f.c.running.selection = ""
			}
			_, err := f.c.result(ctx)
			var input *operationInput
			if !errors.As(err, &input) || string(input.event.Input) != "queued\n" {
				t.Fatal("gate publication lost queued terminal input", err)
			}
			if err := f.r.ForwardInput(ctx, input.event); err != nil {
				t.Fatal(err)
			}
			_ = f.slave.SetReadDeadline(time.Now().Add(time.Second))
			var actual [7]byte
			if _, err := io.ReadFull(f.slave, actual[:]); err != nil || string(actual[:]) != "queued\n" {
				t.Fatal("queued gate input was lost or changed", string(actual[:]), err)
			}
		})
	}
}
func TestGateInputWaitRetainsOriginalDeadlineAndOperation(t *testing.T) {
	f := completionFixture(t)
	s := f.c.w.channels
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := s.WaitGateInput(ctx, "operation-1", 10); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("outstanding input did not expire", err)
	}
	g := s.inputState()
	g.mu.Lock()
	if g.operation != "operation-1" || g.held || g.through != 0 || g.watermark != 10 {
		t.Fatal("wait changed gated input eligibility")
	}
	g.mu.Unlock()
	var fatal *StartError
	if !errors.As(s.Err(), &fatal) || fatal.Code != "input-barrier-timeout" {
		t.Fatal("expired gate barrier did not fail the session", s.Err())
	}
	deadline, _ := ctx.Deadline()
	if !fatal.Deadline.Equal(deadline) {
		t.Fatal("fatal barrier renewed its deadline")
	}
	// The lifecycle owner disposes the workload after channel failure. This wait
	// must never send continue or an ordinary operation result itself.
	_ = f.control.SetReadDeadline(time.Now().Add(10 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || err == nil {
		t.Fatal("barrier expiry released helper", n, err)
	}
}

func TestGatePublicEventsHaveCoveringOutputBoundaries(t *testing.T) {
	f := relayFixtureFor(t)
	facts := []protocol.PrivateMessage{
		&protocol.PrivateGateReady{OperationID: "op", GateID: "gate"},
		&protocol.PrivateGateContinued{OperationID: "op", GateID: "gate"},
		&protocol.PrivateGateInterrupted{OperationID: "op", GateID: "other"},
	}
	wanted := []string{"operation_ready", "operation_continued", "operation_gate_interrupted"}
	var mark int64
	for i, fact := range facts {
		if _, err := f.slave.Write([]byte("output")); err != nil {
			t.Fatal(err)
		}
		if err := f.r.PublishGate(relayTestContext(t), fact); err != nil {
			t.Fatal(err)
		}
		for {
			m := nextRelayFrame(t, f)
			if v, ok := m.(*protocol.OutputMark); ok {
				mark = v.Offset
				continue
			}
			var offset int64
			var kind string
			switch v := m.(type) {
			case *protocol.OperationReady:
				kind, offset = "operation_ready", v.OutputThrough
			case *protocol.OperationContinued:
				kind, offset = "operation_continued", v.OutputThrough
			case *protocol.OperationGateInterrupted:
				kind, offset = "operation_gate_interrupted", v.OutputThrough
			default:
				t.Fatal("unexpected gate event", m)
			}
			if kind != wanted[i] || offset != int64(6*(i+1)) || mark < offset {
				t.Fatal("gate event lacks exact covering boundary", kind, offset, mark)
			}
			break
		}
	}
	_ = f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	var output [18]byte
	if _, err := io.ReadFull(f.terminal, output[:]); err != nil || string(output[:]) != "outputoutputoutput" {
		t.Fatal("gate output changed", string(output[:]), err)
	}
	if f.r.PublishGate(relayTestContext(t), &protocol.PrivateStarted{OperationID: "op"}) == nil {
		t.Fatal("non-gate fact published")
	}
}
