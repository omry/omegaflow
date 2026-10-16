//go:build linux

package helper

import (
	"context"
	"fmt"
	"io"
	"net"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// GateCommand uses only the manifested socket and emits no terminal output.
// Its actor owns operation identity and supervision while the helper waits.
func GateCommand(args []string) error {
	if len(args) != 3 || args[0] != "--socket="+SocketPath || args[1] != "gate" {
		return fmt.Errorf("invalid gate helper invocation")
	}
	request := protocol.HelperGateRequest{GateID: args[2]}
	if _, err := protocol.EncodeHelper(request, protocol.HelperToAwsh, protocol.HelperGate); err != nil {
		return err
	}
	c, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: SocketPath, Net: "unix"})
	if err != nil {
		return err
	}
	return exchangeGate(c, request)
}

func exchangeGate(c *net.UnixConn, request protocol.HelperGateRequest) error {
	reply, err := Exchange(c, protocol.HelperGate, request)
	if err != nil {
		return err
	}
	accepted, ok := reply.(*protocol.HelperGateAccepted)
	if !ok || accepted.GateID != request.GateID {
		return fmt.Errorf("gate reply identity mismatch")
	}
	return nil
}

// ReadOperationRequest accepts only the two existing first operation-helper
// forms. Both alternatives use the same strict codec, EOF and ancillary checks.
func ReadOperationRequest(c *net.UnixConn) (protocol.HelperMessage, error) {
	if err := stream(c); err != nil {
		if c != nil {
			c.Close()
		}
		return nil, err
	}
	frame, err := io.ReadAll(io.LimitReader(socketReader{c}, protocol.MaxFrameBytes+5))
	if err == nil {
		if m, decode := protocol.DecodeHelper(frame, protocol.HelperToAwsh, protocol.HelperGate); decode == nil {
			if _, ok := m.(*protocol.HelperGateRequest); ok {
				return m, nil
			}
		}
		if m, decode := protocol.DecodeHelper(frame, protocol.HelperToAwsh, protocol.HelperCompletion); decode == nil {
			if _, ok := m.(*protocol.HelperPromptState); ok {
				return m, nil
			}
		}
		err = fmt.Errorf("expected gate or completion prompt_state")
	}
	c.Close()
	return nil, err
}

type gateReplyWriter struct {
	DeadlineWriter
	remaining int
}

func (w *gateReplyWriter) Write(data []byte) (int, error) {
	n, err := w.DeadlineWriter.Write(data)
	if n >= 0 && n <= len(data) {
		w.remaining -= n
	}
	return n, err
}

// WriteGateReply uses the existing single five-second stream-write epoch.
// Complete delivery is the commit point, including a final positive write
// accompanied by an error. A later close or cancellation cannot undo it.
func WriteGateReply(ctx context.Context, w DeadlineWriter, gateID string) error {
	frame, err := protocol.EncodeHelper(protocol.HelperGateAccepted{GateID: gateID}, protocol.AwshToHelper, protocol.HelperGate)
	if err != nil {
		return err
	}
	counted := &gateReplyWriter{DeadlineWriter: w, remaining: len(frame)}
	err = WriteControl(ctx, counted, frame)
	if counted.remaining == 0 {
		return nil
	}
	return err
}
