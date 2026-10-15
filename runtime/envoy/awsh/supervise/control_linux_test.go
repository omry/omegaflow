//go:build linux

package supervise

import (
	"bytes"
	"context"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestReadControlsAdmitsBeforeQueue(t *testing.T) {
	frame, err := protocol.EncodePrivate(protocol.PrivateShutdown{}, protocol.EnvoyToAwsh)
	if err != nil {
		t.Fatal(err)
	}
	for _, available := range []int{0, 1} {
		t.Run(string(rune('0'+available)), func(t *testing.T) {
			events := make(chan protocol.PrivateMessage, 2)
			remaining := available
			err := readControls(context.Background(), bytes.NewReader(append(append([]byte{}, frame...), frame...)), events, func(protocol.PrivateMessage) bool {
				if remaining == 0 {
					return false
				}
				remaining--
				return true
			})
			if err == nil || err.Error() != "private request outside current phase" || len(events) != available {
				t.Fatal("premature or duplicate control entered queue", len(events), err)
			}
		})
	}
}
