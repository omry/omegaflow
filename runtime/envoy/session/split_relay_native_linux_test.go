//go:build linux

package session

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Observe the actual setup deadline. Production start now owns attachment;
// this transport-only proof still does not claim completed-operation cleanup.
type nativeSplitTransport struct {
	*SplitResources
	r        *Relay
	deadline time.Time
}

func (s *nativeSplitTransport) Prepare(ctx context.Context, req protocol.Execute) (string, string, error) {
	s.deadline, _ = ctx.Deadline()
	out, errout, err := s.SplitResources.Prepare(ctx, req)
	return out, errout, err
}

func TestNativeSplitStreamsExactBytes(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_SPLIT_STREAMS") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	for _, first := range []string{"stdout", "stderr", "empty"} {
		t.Run(first, func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			owner := &nativeSplitTransport{SplitResources: NewSplitSetup(), r: n.c.r}
			n.c.setup = owner
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				_ = n.c.r.Close(ctx)
				if owner.operation != nil {
					original, end := context.WithDeadline(context.Background(), owner.deadline)
					defer end()
					if err := owner.Rollback(original, "operation-1"); err != nil {
						t.Error("test-owned transport disposal", err)
					}
				}
			})
			req := splitRequest()
			req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
			req.Source = ":"
			want := map[string][]byte{}
			if first != "empty" {
				redirect := ""
				if first == "stderr" {
					redirect = " >&2"
				}
				req.Source = "builtin printf 'first\\000\\377\\r\\n'" + redirect + "; for i in {1..2048}; do builtin printf 'out-é\\000\\377\\r\\n'; builtin printf 'err-λ\\000\\376\\n' >&2; done"
				want["stdout"] = bytes.Repeat([]byte("out-é\x00\xff\r\n"), 2048)
				want["stderr"] = bytes.Repeat([]byte("err-λ\x00\xfe\n"), 2048)
				want[first] = append([]byte("first\x00\xff\r\n"), want[first]...)
			}
			n.request(t, &req)
			e := receiveInput(t, n.c.w.channels)
			actual, ok := e.Message.(*protocol.Execute)
			if !ok {
				t.Fatal(e)
			}
			// Drain the actual terminal concurrently, so a blocked test reader
			// cannot substitute a small-output assumption for transport evidence.
			type rawResult struct {
				data []byte
				err  error
			}
			rawDone := make(chan rawResult, 1)
			sink := &nativeSplitSink{wake: make(chan struct{}, 1)}
			_ = n.terminal.SetReadDeadline(time.Now().Add(5 * time.Second))
			go func() {
				_, err := io.Copy(sink, n.terminal)
				rawDone <- rawResult{sink.bytes(), err}
			}()
			rawJoined := false
			t.Cleanup(func() {
				if !rawJoined {
					_ = n.terminal.SetReadDeadline(time.Now())
					select {
					case <-rawDone:
					case <-time.After(time.Second):
						t.Error("test terminal collector did not join")
					}
				}
			})
			out, err := n.c.Start(n.c.w.channels.ctx, *actual)
			if err != nil || !out.Started {
				t.Fatal("qualified split source did not start", out, err)
			}
			// The helper's proposal proves source return only. The test does not
			// release input_closed or claim an operation result/descendant proof.
			select {
			case result := <-n.c.private.events:
				proposal, ok := result.message.(*protocol.PrivateInputClose)
				if result.err != nil || !ok || proposal.OperationID != req.OperationID {
					t.Fatal("actual source return", result)
				}
			case result := <-n.c.private.fatal:
				t.Fatal(result)
			case <-time.After(5 * time.Second):
				t.Fatal("actual source did not return")
			}
			n.c.r.mu.Lock()
			attached := n.c.r.split
			n.c.r.mu.Unlock()
			// Only this source and the setup keepalives can write these FIFOs.
			// B5.3 must supply its real cleanup ordering before doing this.
			for i, fd := range owner.operation.keepalives {
				if err := syscall.Close(fd); err != nil {
					t.Fatal(err)
				}
				owner.operation.keepalives[i] = -1
			}
			select {
			case <-attached.done:
			case <-time.After(3 * time.Second):
				t.Fatal("actual FIFO streams did not end")
			}
			ctx := splitContext(t)
			if err := n.c.r.stopSplit(ctx); err != nil {
				t.Fatal(err)
			}
			through, err := n.c.r.Drain(ctx)
			if err != nil {
				t.Fatal(err)
			}
			for int64(len(sink.bytes())) < through-n.through {
				select {
				case <-sink.wake:
				case <-ctx.Done():
					t.Fatal("terminal did not receive covering range")
				}
			}
			_ = n.terminal.SetReadDeadline(time.Now())
			raw := <-rawDone
			rawJoined = true
			if !os.IsTimeout(raw.err) || int64(len(raw.data)) != through-n.through {
				t.Fatal("raw native range", len(raw.data), through, n.through, raw.err)
			}
			got := map[string][]byte{}
			stream, offset := "pty", n.through
			started := false
			for {
				m := nativeFrame(t, n.reader)
				encoded, err := protocol.EncodeEnvoy(m)
				var h protocol.Header
				if err != nil || json.Unmarshal(encoded, &h) != nil || h.Seq != n.lastSeq+1 {
					t.Fatal("public sequence", m, err)
				}
				n.lastSeq = h.Seq
				if mark, ok := m.(*protocol.OutputMark); ok {
					if mark.Offset < offset || mark.Offset > through || mark.ElapsedUS < n.markElapsed {
						t.Fatal("native mark frontier", mark)
					}
					got[stream] = append(got[stream], raw.data[offset-n.through:mark.Offset-n.through]...)
					stream, offset, n.markElapsed = mark.Stream, mark.Offset, mark.ElapsedUS
					if started && offset == through {
						break
					}
				} else if event, ok := m.(*protocol.OperationStarted); !ok || event.OperationID != req.OperationID || event.OutputStart != out.OutputStart {
					t.Fatal("unexpected native event", m)
				} else {
					started = true
				}
			}
			for _, name := range splitStreams {
				if !bytes.Equal(got[name], want[name]) {
					t.Fatal("native logical bytes changed", name, len(got[name]), len(want[name]))
				}
			}
			t.Logf("raw PTY controls: %q; exact stdout=%d stderr=%d", got["pty"], len(got["stdout"]), len(got["stderr"]))
			// Readline's submit line ending is PTY traffic, outside both exact
			// logical byte comparisons above. Keep its raw bytes in the evidence.
			if visible := ordinaryVisible(string(got["pty"])); visible != "" && visible != "\n" {
				t.Fatal("split source leaked into PTY", fmt.Sprintf("%q", visible))
			}
		})
	}
}

// A test-only terminal collector reports actual read progress, so stopping it
// cannot discard bytes that the writer sent but the socket has not delivered.
type nativeSplitSink struct {
	mu     sync.Mutex
	buffer bytes.Buffer
	wake   chan struct{}
}

func (s *nativeSplitSink) Write(p []byte) (int, error) {
	s.mu.Lock()
	n, err := s.buffer.Write(p)
	s.mu.Unlock()
	select {
	case s.wake <- struct{}{}:
	default:
	}
	return n, err
}
func (s *nativeSplitSink) bytes() []byte {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]byte(nil), s.buffer.Bytes()...)
}
