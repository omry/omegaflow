// Package helper implements Awsh's single-exchange Unix-stream boundary.
package helper

import (
	"errors"
	"fmt"
	"io"
	"net"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Deadlines belong to the caller's existing actor phase. These functions never
// set or reset a deadline, change signal dispositions, or select a lifecycle
// outcome. A helper waiting for its actor's reply remains under actor supervision.
// Callers must retain that supervision and set any applicable absolute deadline
// before invoking transport work.

func stream(c *net.UnixConn) error {
	if c == nil || c.LocalAddr() == nil || c.RemoteAddr() == nil ||
		c.LocalAddr().Network() != "unix" || c.RemoteAddr().Network() != "unix" {
		return fmt.Errorf("helper requires a connected Unix stream")
	}
	return nil
}

// ReadRequest requires the client's write half-close after one exact request.
// On success the caller owns the connection until Reply; an error closes it.
func ReadRequest(c *net.UnixConn, phase protocol.HelperPhase) (protocol.HelperMessage, error) {
	if err := stream(c); err != nil {
		if c != nil {
			_ = c.Close()
		}
		return nil, err
	}
	m, err := protocol.ReadHelper(socketReader{c}, protocol.HelperToAwsh, phase)
	if err != nil {
		_ = c.Close()
	}
	return m, err
}

// Reply writes one complete reply then closes the connection. Once every byte
// was written, a close error cannot undo that completed write (notably for gates).
func Reply(c *net.UnixConn, phase protocol.HelperPhase, m protocol.HelperMessage) error {
	if c != nil {
		defer c.Close()
	}
	if err := stream(c); err != nil {
		return err
	}
	frame, err := protocol.EncodeHelper(m, protocol.AwshToHelper, phase)
	if err != nil {
		return err
	}
	return WriteAll(c, frame)
}

// Exchange writes one request, half-closes, and reads one reply through EOF.
// It closes the connection on every exit and emits no terminal output.
func Exchange(c *net.UnixConn, phase protocol.HelperPhase, m protocol.HelperMessage) (protocol.HelperMessage, error) {
	if c != nil {
		defer c.Close()
	}
	if err := stream(c); err != nil {
		return nil, err
	}
	frame, err := protocol.EncodeHelper(m, protocol.HelperToAwsh, phase)
	if err != nil {
		return nil, err
	}
	if err := WriteAll(c, frame); err != nil {
		return nil, err
	}
	if err := c.CloseWrite(); err != nil {
		return nil, err
	}
	return protocol.ReadHelper(socketReader{c}, protocol.AwshToHelper, phase)
}

// WriteAll retries positive short writes without altering the writer's deadline.
// It is also usable for an already-encoded bounded private control frame.
func WriteAll(w io.Writer, data []byte) error {
	for len(data) > 0 {
		n, err := w.Write(data)
		if n < 0 || n > len(data) {
			return fmt.Errorf("invalid helper write count")
		}
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrNoProgress
		}
		data = data[n:]
	}
	return nil
}

type socketReader struct{ c *net.UnixConn }

func (r socketReader) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	// Linux permits at most 253 SCM_RIGHTS descriptors per message. This buffer
	// also accommodates credentials; truncation is fatal regardless of its size.
	var control [4096]byte
	n, oobn, flags, _, err := r.c.ReadMsgUnix(p, control[:])
	if rejectControl(control[:oobn], flags) {
		// Returning zero prevents io.ReadFull accepting a full payload together
		// with an ancillary error and subsequently discarding that error.
		return 0, fmt.Errorf("helper ancillary data or truncated message")
	}
	if n == 0 && (err == nil || errors.Is(err, io.EOF)) {
		err = io.EOF
	}
	return n, err
}

func rejectControl(control []byte, flags int) bool {
	// Received descriptors belong to this process even when the message is
	// rejected. Close every rights descriptor before returning the error.
	if len(control) > 0 {
		messages, _ := syscall.ParseSocketControlMessage(control)
		for _, m := range messages {
			if m.Header.Level == syscall.SOL_SOCKET && m.Header.Type == syscall.SCM_RIGHTS && len(m.Data)%4 == 0 {
				fds, err := syscall.ParseUnixRights(&m)
				if err == nil {
					for _, fd := range fds {
						_ = syscall.Close(fd)
					}
				}
			}
		}
	}
	return len(control) != 0 || flags&(syscall.MSG_CTRUNC|syscall.MSG_TRUNC) != 0
}
