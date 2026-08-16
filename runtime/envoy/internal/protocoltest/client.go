// Package protocoltest provides a small controller harness for Envoy tests.
package protocoltest

import (
	"fmt"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Client emits canonical controller frames and validates streamed Envoy events.
type Client struct {
	nextSeq uint64
	decoder *protocol.StreamDecoder
	state   *protocol.SessionState
}

// NewClient returns an initial controller harness.
func NewClient() *Client {
	return &Client{
		nextSeq: 1,
		decoder: protocol.NewEnvoyStreamDecoder(),
		state:   protocol.NewSessionState(),
	}
}

func (client *Client) Hello(sessionID string) ([]byte, error) {
	return client.send(protocol.Hello{Seq: client.nextSeq, SessionID: sessionID})
}

func (client *Client) Execute(operationID, source string) ([]byte, error) {
	return client.send(protocol.Execute{Seq: client.nextSeq, OperationID: operationID, Source: source})
}

func (client *Client) Continue(operationID, gateID string) ([]byte, error) {
	return client.send(protocol.Continue{Seq: client.nextSeq, OperationID: operationID, GateID: gateID})
}

func (client *Client) Cancel(operationID, reason string) ([]byte, error) {
	return client.send(protocol.Cancel{Seq: client.nextSeq, OperationID: operationID, Reason: reason})
}

func (client *Client) Resize(columns, rows int) ([]byte, error) {
	return client.send(protocol.Resize{Seq: client.nextSeq, Columns: columns, Rows: rows})
}

func (client *Client) Shutdown(reason string) ([]byte, error) {
	return client.send(protocol.Shutdown{Seq: client.nextSeq, Reason: reason})
}

// Accept validates zero or more streamed Envoy frames.
func (client *Client) Accept(data []byte) ([]any, error) {
	messages, err := client.decoder.Feed(data)
	if err != nil {
		return nil, err
	}
	for index, message := range messages {
		if err := client.state.AcceptEnvoy(message); err != nil {
			return messages[:index], err
		}
	}
	return messages, nil
}

// Finish rejects a telemetry stream that closes mid-frame or before closed.
func (client *Client) Finish() error {
	if err := client.decoder.Finish(); err != nil {
		return err
	}
	if client.state.Phase() != protocol.PhaseClosed {
		return fmt.Errorf("telemetry closed before closed event (phase %s)", client.state.Phase())
	}
	return nil
}

// Phase returns the validated session phase.
func (client *Client) Phase() protocol.Phase { return client.state.Phase() }

// OutputThrough returns the largest accepted exclusive output offset.
func (client *Client) OutputThrough() uint64 { return client.state.OutputThrough() }

func (client *Client) send(message any) ([]byte, error) {
	frame, err := protocol.EncodeController(message)
	if err != nil {
		return nil, err
	}
	if err := client.state.AcceptController(message); err != nil {
		return nil, err
	}
	client.nextSeq++
	return frame, nil
}
