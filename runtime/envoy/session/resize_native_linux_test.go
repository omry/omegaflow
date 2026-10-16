//go:build linux

package session

import (
	"context"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func nativeResize(t *testing.T, n *nativeOrdinary, columns, rows int64) {
	t.Helper()
	n.request(t, &protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: n.seq}, Columns: columns, Rows: rows})
}

func nativeResizeAck(t *testing.T, n *nativeOrdinary, columns, rows int64) *protocol.ResizeApplied {
	t.Helper()
	for {
		switch m := n.frame(t).(type) {
		case *protocol.OutputMark:
		case *protocol.ResizeApplied:
			if m.Columns != columns || m.Rows != rows || n.markOffset != m.OutputThrough || m.ElapsedUS < n.markElapsed {
				t.Fatal("actual resize lost dimensions, covering mark or sender order", m)
			}
			return m
		default:
			t.Fatal("unexpected actual resize event", m)
		}
	}
}

func TestNativePublicResizeSignalAndDimensions(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_RESIZE") != "1" {
		t.Skip("genuine qualified target only")
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
	defer end()
	id := "resize-signal"
	source := "trap 'builtin printf \"sigwinch\\n\"' WINCH; builtin printf 'before-resize\\n'; while ! IFS= read -r LINE; do :; done; /usr/bin/stty size; builtin printf 'done:%s\\n' \"$LINE\""
	start := n.begin(t, ctx, id, source)
	done := make(chan error, 1)
	go func() { done <- n.c.Complete(ctx, n.cleanup(t, id)) }()
	// Observe real authored output before sending resize. Read bytewise so the
	// exact raw prefix remains visible even with qualified Readline controls.
	var prefix strings.Builder
	n.terminal.SetReadDeadline(time.Now().Add(3 * time.Second))
	for !strings.HasSuffix(ordinaryVisible(prefix.String()), "before-resize\n") {
		var b [1]byte
		if _, err := io.ReadFull(n.terminal, b[:]); err != nil || prefix.Len() > 4096 {
			t.Fatal("actual Bash did not reach the pre-resize read", prefix.String(), err)
		}
		prefix.WriteByte(b[0])
	}
	n.through += int64(prefix.Len())
	t.Logf("Actual pre-resize PTY [%d,%d): %q", start, n.through, prefix.String())
	nativeResize(t, n, 119, 41)
	ack := nativeResizeAck(t, n, 119, 41)
	if ack.OutputThrough != n.through {
		t.Fatal("resize did not close the already-delivered authored prefix", ack, n.through)
	}
	if columns, rows := windowSize(t, n.c.w.PTY); columns != 119 || rows != 41 {
		t.Fatal("actual PTY master disagrees with resize", columns, rows)
	}
	n.terminal.SetWriteDeadline(time.Now().Add(time.Second))
	if _, err := io.WriteString(n.terminal, "typed\n"); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal("actual return after resize", err)
		}
	case <-ctx.Done():
		t.Fatal("actual resized command did not finish")
	}
	for {
		m := n.frame(t)
		if _, ok := m.(*protocol.OutputMark); ok {
			continue
		}
		completed, ok := m.(*protocol.OperationCompleted)
		if !ok || completed.OperationID != id || completed.Status != 0 || completed.OutputStart != start || completed.OutputThrough < ack.OutputThrough {
			t.Fatal("resize corrupted ordinary completion", m)
		}
		output := ordinaryVisible(n.bytesThrough(t, completed.OutputThrough))
		if !strings.Contains(output, "sigwinch\n") || !strings.Contains(output, "41 119\n") || !strings.Contains(output, "done:typed\n") {
			t.Fatal("real Bash foreground missed SIGWINCH, dimensions or input", output)
		}
		break
	}
}

func TestNativePublicResizeDrainWinners(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_RESIZE") != "1" {
		t.Skip("genuine qualified target only")
	}
	for _, drainFirst := range []bool{false, true} {
		t.Run(map[bool]string{false: "applied-before-drain", true: "drain-supersedes"}[drainFirst], func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
			defer end()
			nativeResize(t, n, 97, 33)
			e := receiveInput(t, n.c.w.channels)
			request, ok := e.Message.(*protocol.Resize)
			if !ok {
				t.Fatal("actual controller resize was not accepted", e)
			}
			if drainFirst {
				if err := n.c.r.Close(ctx); err != nil {
					t.Fatal(err)
				}
			}
			applied, err := n.c.r.Resize(ctx, request.Columns, request.Rows)
			if err != nil || applied == drainFirst {
				t.Fatal("wrong native resize/drain winner", applied, drainFirst, err)
			}
			if applied {
				ack := nativeResizeAck(t, n, 97, 33)
				_ = n.bytesThrough(t, ack.OutputThrough)
			}
			if err := n.c.ShutdownIdle("resize-proof"); err != nil {
				t.Fatal("actual idle shutdown", err)
			}
			for _, expected := range []string{"draining", "closed"} {
				for {
					switch m := n.frame(t).(type) {
					case *protocol.OutputMark:
						continue
					case *protocol.Draining:
						if expected != "draining" || m.OutputThrough < n.through {
							t.Fatal("drain ordering changed", m)
						}
						_ = n.bytesThrough(t, m.OutputThrough)
					case *protocol.Closed:
						if expected != "closed" || m.OutputThrough != n.through {
							t.Fatal("closed boundary changed", m)
						}
					default:
						t.Fatal("unapplied resize acknowledged or drain order changed", m)
					}
					break
				}
			}
		})
	}
}
