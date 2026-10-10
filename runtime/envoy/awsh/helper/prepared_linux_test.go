package helper

import (
	"bytes"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestPreparedHelperRequiresCompleteAcceptedEOF(t *testing.T) {
	valid, err := protocol.EncodeHelper(protocol.HelperAccepted{}, protocol.AwshToHelper, protocol.HelperStartPrepared)
	if err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string][]byte{"accepted": valid, "empty": {}, "partial": valid[:len(valid)-1],
		"trailing": append(append([]byte{}, valid...), 'x'), "wrong-phase": helperFrame([]byte("awsh-helper-v1\x00source\x00"))} {
		t.Run(name, func(t *testing.T) {
			c, peer := pair(t)
			done := make(chan error, 1)
			go func() {
				m, err := ReadRequest(peer, protocol.HelperStartPrepared)
				if _, ok := m.(*protocol.HelperPreparedRequest); err == nil && !ok {
					t.Error("wrong prepared request")
				}
				if err == nil {
					err = send(peer, data)
				}
				peer.Close()
				done <- err
			}()
			var out bytes.Buffer
			err := emitPrepared(c, &out)
			if name == "accepted" {
				if err != nil || out.String() != "x" {
					t.Fatalf("%v %q", err, out.String())
				}
			} else if err == nil || out.Len() != 0 {
				t.Fatalf("bad reply released marker: %v %q", err, out.String())
			}
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		})
	}
	for _, args := range [][]string{nil, {"--socket=" + SocketPath, "start-prepared", "extra"}, {"--socket=/tmp/other", "start-prepared"}, {"--socket=" + SocketPath, "START-PREPARED"}} {
		var out bytes.Buffer
		if PreparedCommand(args, &out) == nil || out.Len() != 0 {
			t.Fatal("invalid invocation")
		}
	}
}
