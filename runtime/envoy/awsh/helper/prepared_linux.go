//go:build linux

package helper

import (
	"fmt"
	"io"
	"net"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// PreparedCommand emits its captured marker only after one accepted reply
// through EOF. The start owner withholds that reply until terminal restoration.
func PreparedCommand(args []string, output io.Writer) error {
	if len(args) != 2 || args[0] != "--socket="+SocketPath || args[1] != "start-prepared" {
		return fmt.Errorf("invalid prepared helper invocation")
	}
	c, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: SocketPath, Net: "unix"})
	if err != nil {
		return err
	}
	return emitPrepared(c, output)
}

func emitPrepared(c *net.UnixConn, output io.Writer) error {
	m, err := Exchange(c, protocol.HelperStartPrepared, protocol.HelperPreparedRequest{})
	if err != nil {
		return err
	}
	if _, ok := m.(*protocol.HelperAccepted); !ok {
		return fmt.Errorf("wrong prepared helper reply")
	}
	return WriteAll(output, []byte("x"))
}
