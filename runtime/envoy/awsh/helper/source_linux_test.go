package helper

import (
	"bytes"
	"fmt"
	"strings"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/awsh/submission"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func sourceReply(source string) protocol.HelperSourceReply {
	return protocol.HelperSourceReply{OperationID: "op", Status: 17, HistExpand: "on", EditingMode: "vi", ExecutionShape: "pty", Source: source}
}

func TestSourceHelperEmitsOnlyCompleteFrameAndMarker(t *testing.T) {
	for _, text := range []string{"printf 'é\\n'\n", strings.Repeat("#", protocol.MaxSourceBytes)} {
		c, s := pair(t)
		m := sourceReply(text)
		peer := make(chan error, 1)
		go func() {
			req, err := ReadRequest(s, protocol.HelperSource)
			if err == nil {
				if _, ok := req.(*protocol.HelperSourceRequest); !ok {
					err = fmt.Errorf("wrong request")
				}
			}
			if err == nil {
				err = Reply(s, protocol.HelperSource, m)
			}
			peer <- err
		}()
		w := &shortWriter{n: 7}
		if err := emitSource(c, w); err != nil {
			t.Fatal(err)
		}
		if err := <-peer; err != nil {
			t.Fatal(err)
		}
		frame, err := submission.Frame(m)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(w.Bytes(), []byte(frame+"x")) {
			t.Fatal("emitter changed complete canonical bytes")
		}
	}
}

func TestSourceHelperRejectsMalformedReplyBeforeOutput(t *testing.T) {
	valid, err := protocol.EncodeHelper(sourceReply(":"), protocol.AwshToHelper, protocol.HelperSource)
	if err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string][]byte{"short": valid[:len(valid)-1], "trailing": append(append([]byte{}, valid...), 1), "wrong-phase": helperFrame([]byte("awsh-helper-v1\x00accepted\x00")), "empty": {}} {
		t.Run(name, func(t *testing.T) {
			c, s := pair(t)
			peer := make(chan error, 1)
			go func() {
				_, err := ReadRequest(s, protocol.HelperSource)
				if err == nil {
					err = send(s, data)
				}
				s.Close()
				peer <- err
			}()
			var w bytes.Buffer
			if err := emitSource(c, &w); err == nil || w.Len() != 0 {
				t.Fatalf("bad reply emitted %d bytes: %v", w.Len(), err)
			}
			if err := <-peer; err != nil {
				t.Fatal(err)
			}
		})
	}
	for _, args := range [][]string{nil, {"--socket=" + SocketPath, "source", "extra"}, {"--socket=/tmp/other", "source"}, {"--socket=" + SocketPath, "SOURCE"}} {
		var w bytes.Buffer
		if err := SourceCommand(args, &w); err == nil || w.Len() != 0 {
			t.Fatal("invalid source invocation")
		}
	}
}
