//go:build linux

package helper

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestGateHelperRequiresMatchingCompleteReply(t *testing.T) {
	valid, err := protocol.EncodeHelper(protocol.HelperGateAccepted{GateID: "gate"}, protocol.AwshToHelper, protocol.HelperGate)
	if err != nil {
		t.Fatal(err)
	}
	wrong, err := protocol.EncodeHelper(protocol.HelperGateAccepted{GateID: "other"}, protocol.AwshToHelper, protocol.HelperGate)
	if err != nil {
		t.Fatal(err)
	}
	for name, frame := range map[string][]byte{
		"accepted": valid, "wrong-id": wrong, "empty": {}, "partial": valid[:len(valid)-1],
		"trailing": append(append([]byte{}, valid...), 'x'),
	} {
		t.Run(name, func(t *testing.T) {
			c, peer := pair(t)
			done := make(chan error, 1)
			go func() {
				defer peer.Close()
				m, err := ReadRequest(peer, protocol.HelperGate)
				if err == nil {
					request, ok := m.(*protocol.HelperGateRequest)
					if !ok || request.GateID != "gate" {
						done <- errors.New("wrong gate request")
						return
					}
					err = send(peer, frame)
				}
				done <- err
			}()
			err := exchangeGate(c, protocol.HelperGateRequest{GateID: "gate"})
			if (err == nil) != (name == "accepted") {
				t.Fatal("wrong gate release disposition", err)
			}
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		})
	}
	for _, args := range [][]string{nil, {"--socket=/tmp/other", "gate", "g"}, {"--socket=" + SocketPath, "gate"}, {"--socket=" + SocketPath, "gate", ""}, {"--socket=" + SocketPath, "gate", "g", "extra"}} {
		if GateCommand(args) == nil {
			t.Fatal("invalid invocation accepted", args)
		}
	}
}

func TestOperationHelperIntakeKeepsStrictForms(t *testing.T) {
	gate, _ := protocol.EncodeHelper(protocol.HelperGateRequest{GateID: "gate"}, protocol.HelperToAwsh, protocol.HelperGate)
	state, err := protocol.EncodeHelper(protocol.HelperPromptState{PromptState: protocol.PromptState{Status: 0, HistExpand: "off", EditingMode: "emacs", PhysicalCWD: "/work", ExportedEnv: map[string]string{}}}, protocol.HelperToAwsh, protocol.HelperCompletion)
	if err != nil {
		t.Fatal(err)
	}
	ready, _ := protocol.EncodeHelper(protocol.HelperCompletionReady{}, protocol.HelperToAwsh, protocol.HelperCompletion)
	for name, frame := range map[string][]byte{
		"gate": gate, "prompt_state": state, "premature-ready": ready,
		"trailing": append(append([]byte{}, gate...), 0), "partial": gate[:len(gate)-1],
		"concatenated": append(append([]byte{}, gate...), state...),
	} {
		t.Run(name, func(t *testing.T) {
			c, peer := pair(t)
			done := make(chan error, 1)
			go func() { done <- send(c, frame) }()
			message, err := ReadOperationRequest(peer)
			if (err == nil) != (name == "gate" || name == "prompt_state") {
				t.Fatal("operation intake weakened phase selection", message, err)
			}
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		})
	}
}

type gateTestWriter struct {
	bytes.Buffer
	deadlines []time.Time
	step      int
	err       error
}

func (w *gateTestWriter) SetWriteDeadline(deadline time.Time) error {
	w.deadlines = append(w.deadlines, deadline)
	return nil
}

func (w *gateTestWriter) Write(data []byte) (int, error) {
	n := len(data)
	if w.step < n {
		n = w.step
	}
	if n > 0 {
		w.Buffer.Write(data[:n])
	}
	return n, w.err
}

func TestGateReplyCommitAndOriginalDeadline(t *testing.T) {
	frame, err := protocol.EncodeHelper(protocol.HelperGateAccepted{GateID: "gate"}, protocol.AwshToHelper, protocol.HelperGate)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name string
		step int
		err  error
		pass bool
	}{
		{"fragmented", 1, nil, true},
		{"zero-progress", 0, nil, false},
		{"partial-error", 1, io.ErrClosedPipe, false},
		{"full-write-with-error", len(frame), io.ErrClosedPipe, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := &gateTestWriter{step: tc.step, err: tc.err}
			before := time.Now()
			err := WriteGateReply(context.Background(), w, "gate")
			if (err == nil) != tc.pass {
				t.Fatal("wrong reply commitment", err, w.Len())
			}
			if len(w.deadlines) != 1 || w.deadlines[0].Before(before.Add(5*time.Second)) || w.deadlines[0].After(time.Now().Add(5*time.Second)) {
				t.Fatal("reply did not retain one original five-second epoch", w.deadlines)
			}
			if tc.pass && !bytes.Equal(w.Bytes(), frame) {
				t.Fatal("successful reply changed frame bytes")
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	w := &gateTestWriter{step: len(frame)}
	if err := WriteGateReply(ctx, w, "gate"); !errors.Is(err, context.Canceled) || w.Len() != 0 {
		t.Fatal("cancelled actor wrote a gate reply", err, w.Len())
	}
}

func TestOperationHelperIntakeRejectsRightsAndOversize(t *testing.T) {
	frame, _ := protocol.EncodeHelper(protocol.HelperGateRequest{GateID: "gate"}, protocol.HelperToAwsh, protocol.HelperGate)
	for _, rights := range []bool{true, false} {
		c, peer := pair(t)
		file, err := os.Open("/dev/null")
		if err != nil {
			t.Fatal(err)
		}
		done := make(chan struct{})
		go func() {
			defer close(done)
			defer c.Close()
			if rights {
				_, _, _ = c.WriteMsgUnix(frame, syscall.UnixRights(int(file.Fd())), nil)
			} else {
				_ = WriteAll(c, make([]byte, protocol.MaxFrameBytes+6))
			}
			_ = c.CloseWrite()
		}()
		if _, err := ReadOperationRequest(peer); err == nil {
			t.Fatal("unsafe operation helper frame accepted", rights)
		}
		<-done
		file.Close()
	}
}
