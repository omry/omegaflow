//go:build linux

package helper

import (
	"fmt"
	"io"
	"net"

	"github.com/omry/omegaflow/runtime/envoy/awsh/submission"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// SourceCommand has one fixed invocation and receives one phase-validated reply
// through EOF. The actor owns active-operation identity and its start deadline.
func SourceCommand(args []string, output io.Writer) error {
	if len(args) != 2 || args[0] != "--socket="+SocketPath || args[1] != "source" {
		return fmt.Errorf("invalid source helper invocation")
	}
	c, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: SocketPath, Net: "unix"})
	if err != nil {
		return err
	}
	return emitSource(c, output)
}

func emitSource(c *net.UnixConn, output io.Writer) error {
	reply, err := Exchange(c, protocol.HelperSource, protocol.HelperSourceRequest{})
	if err != nil {
		return err
	}
	m, ok := reply.(*protocol.HelperSourceReply)
	if !ok {
		return fmt.Errorf("wrong source helper reply")
	}
	frame, err := submission.Frame(*m)
	if err != nil {
		return err
	}
	return WriteAll(output, []byte(frame+"x"))
}
