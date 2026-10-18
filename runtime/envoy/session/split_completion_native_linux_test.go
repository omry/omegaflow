//go:build linux

package session

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestNativeSplitCompletionProductionResizeAndExactBytes(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_SPLIT_COMPLETION") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	for _, first := range []string{"stdout", "stderr", "empty", "cancel", "finalize"} {
		t.Run(first, func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			owner := NewSplitSetup()
			n.c.setup = owner
			req := splitRequest()
			req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
			req.Source = "builtin read -r finish"
			want := map[string][]byte{"stdout": {}, "stderr": {}}
			controlled := first == "cancel" || first == "finalize"
			if first != "empty" {
				redirect := ""
				if first == "stderr" {
					redirect = " >&2"
				}
				req.Source = "builtin printf 'first-é\\000\\377\\r\\n'" + redirect + "; " + req.Source + "; builtin printf 'out-é\\000\\377\\r\\n'; builtin printf 'err-λ\\000\\376\\n' >&2"
				want["stdout"] = []byte("out-é\x00\xff\r\n")
				want["stderr"] = []byte("err-λ\x00\xfe\n")
				want[first] = append([]byte("first-é\x00\xff\r\n"), want[first]...)
				if controlled {
					req.Source = "builtin printf 'first-é\\000\\377\\r\\n'; builtin read -r finish"
					want = map[string][]byte{"stdout": []byte("first-é\x00\xff\r\n"), "stderr": {}}
				}
			}
			n.request(t, &req)
			event := receiveInput(t, n.c.w.channels)
			actual, ok := event.Message.(*protocol.Execute)
			if !ok {
				t.Fatal(event)
			}
			sink := &nativeSplitSink{wake: make(chan struct{}, 1)}
			rawDone := make(chan error, 1)
			_ = n.terminal.SetReadDeadline(time.Now().Add(10 * time.Second))
			go func() { _, err := io.Copy(sink, n.terminal); rawDone <- err }()
			joined := false
			t.Cleanup(func() {
				if !joined {
					_ = n.terminal.SetReadDeadline(time.Now())
					select {
					case <-rawDone:
					case <-time.After(time.Second):
						t.Error("native terminal collector did not join")
					}
				}
			})
			started, err := n.c.Start(n.c.w.channels.ctx, *actual)
			if err != nil || !started.Started {
				t.Fatal("actual split start", started, err)
			}
			var cleanupCalls atomic.Int32
			complete := make(chan error, 1)
			ctx, end := context.WithTimeout(n.c.w.channels.ctx, 8*time.Second)
			defer end()
			var peers *controlPeers
			if controlled {
				peers = &controlPeers{sessionID: int64(n.c.w.command.Process.Pid), census: nativeControlCensus(n, 0)}
			}
			go func() {
				complete <- n.c.completeControlled(ctx, func(job context.Context, id string, pid int64) error {
					if id != req.OperationID || pid <= 0 || owner.operation == nil || !owner.operation.cleanupEnd.IsZero() {
						return fmt.Errorf("wrong cleanup identity or premature keepalive closure")
					}
					for _, fd := range owner.operation.keepalives {
						if _, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0); e != 0 {
							return e
						}
					}
					if _, ok := job.Deadline(); !ok {
						return fmt.Errorf("cleanup lost existing deadline")
					}
					// The authored command uses builtins and has returned. Actual process-tree
					// cleanup is an explicit B6 peer, never inferred from these checks.
					cleanupCalls.Add(1)
					return nil
				}, peers)
			}()
			n.request(t, &protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: n.seq}, Columns: 93, Rows: 31})
			offset, stream := n.through, "pty"
			got := map[string][]byte{}
			resized := false
			completedThrough := int64(-1)
			for completedThrough < 0 {
				message := nativeFrame(t, n.reader)
				encoded, err := protocol.EncodeEnvoy(message)
				var h protocol.Header
				if err != nil || json.Unmarshal(encoded, &h) != nil || h.Seq != n.lastSeq+1 {
					t.Fatal("native sequence", message, err)
				}
				n.lastSeq = h.Seq
				switch m := message.(type) {
				case *protocol.OutputMark:
					if m.Offset < offset || m.ElapsedUS < n.markElapsed {
						t.Fatal("native stream mark regressed", m)
					}
					for int64(len(sink.bytes())) < m.Offset-n.through {
						select {
						case <-sink.wake:
						case <-ctx.Done():
							t.Fatal("raw output did not reach mark", ctx.Err())
						}
					}
					raw := sink.bytes()
					got[stream] = append(got[stream], raw[offset-n.through:m.Offset-n.through]...)
					offset, stream, n.markElapsed = m.Offset, m.Stream, m.ElapsedUS
				case *protocol.OperationStarted:
					if m.OperationID != req.OperationID || m.OutputStart != started.OutputStart {
						t.Fatal(m)
					}
				case *protocol.ResizeApplied:
					if resized || m.Columns != 93 || m.Rows != 31 || m.OutputThrough != offset {
						t.Fatal("resize before covering frontier", m, offset)
					}
					resized = true
					if controlled {
						// start_released precedes execution of the authored builtins.
						// Prove actual authored output before interrupting the read;
						// an earlier signal may legitimately skip the first printf.
						for !bytes.Contains(sink.bytes(), []byte("first-é\x00\xff\r\n")) {
							select {
							case <-sink.wake:
							case <-ctx.Done():
								t.Fatal("authored split prefix not observed before control", ctx.Err())
							}
						}
					}
					if first == "cancel" {
						n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: req.OperationID, Reason: "native-split"})
					} else if first == "finalize" {
						n.request(t, &protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq}, OperationID: req.OperationID, Reason: "native-split"})
					} else if _, err := n.terminal.Write([]byte("continue\n")); err != nil {
						t.Fatal("release builtin read", err)
					}
				case *protocol.OperationCompleted:
					if controlled || !resized || m.OperationID != req.OperationID || m.OutputStart != started.OutputStart || m.OutputThrough != offset || m.Status != 0 {
						t.Fatal("native result before split barrier", m, offset)
					}
					completedThrough = m.OutputThrough
				case *protocol.OperationCancelled:
					if first != "cancel" || !resized || m.OperationID != req.OperationID || m.OutputStart != started.OutputStart || m.OutputThrough != offset || m.Status == nil || *m.Status != 130 {
						t.Fatal("wrong native split cancellation", m)
					}
					completedThrough = m.OutputThrough
				case *protocol.OperationFinalized:
					if first != "finalize" || !resized || m.OperationID != req.OperationID || m.OutputStart != started.OutputStart || m.OutputThrough != offset {
						t.Fatal("wrong native split finalization", m)
					}
					completedThrough = m.OutputThrough
				default:
					t.Fatal("unexpected native split event", message)
				}
			}
			select {
			case err := <-complete:
				if err != nil {
					t.Fatal(err)
				}
			case <-ctx.Done():
				t.Fatal("completion retained worker", ctx.Err())
			}
			if cleanupCalls.Load() != 1 || owner.operation != nil || n.c.active != nil || n.c.r.ownsSplit(req.OperationID) {
				t.Fatal("native result retained operation resources")
			}
			if _, err := os.Lstat(filepath.Join(sessionRuntimeDir, "split")); !os.IsNotExist(err) {
				t.Fatal("actual FIFO paths survive completion", err)
			}
			for _, name := range splitStreams {
				if !bytes.Equal(got[name], want[name]) {
					t.Fatalf("%s exact logical bytes changed: %x != %x", name, got[name], want[name])
				}
			}
			_ = n.terminal.SetReadDeadline(time.Now())
			err = <-rawDone
			joined = true
			raw := sink.bytes()
			if !os.IsTimeout(err) || int64(len(raw)) != completedThrough-n.through {
				t.Fatal("native raw range", len(raw), completedThrough, n.through, err)
			}
			t.Logf("exact raw=%x stdout=%x stderr=%x pty=%x resize=true cleanup-peer=1 both-EOF=true resources-removed=true", raw, got["stdout"], got["stderr"], got["pty"])
		})
	}
}
