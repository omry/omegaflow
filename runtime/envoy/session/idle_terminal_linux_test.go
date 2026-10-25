//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestIdleTerminalClosureFormsAndReap(t *testing.T) {
	for _, kind := range []string{"closed", "shell_exit"} {
		for _, status := range []int64{0, 23, 137} {
			t.Run(fmt.Sprintf("%s/%d", kind, status), func(t *testing.T) {
				f := startFixtureFor(t, nil)
				f.r.w.reaped = make(chan struct{})
				done := make(chan error, 1)
				go func() { done <- f.c.ShutdownIdle("requested-reason") }()
				if _, ok := f.readPrivate(t).(*protocol.PrivateShutdown); !ok {
					t.Fatal("missing actual private shutdown")
				}
				if kind == "closed" {
					f.private(t, protocol.PrivateClosed{Reason: "shutdown", Status: status, CWD: "/tmp"})
				} else {
					f.private(t, protocol.PrivateShellExit{Status: status, CWD: "/tmp"})
				}
				// Terminal acceptance closes control before EOF and reap. A
				// later request must never reach Awsh through this writer.
				f.control.SetReadDeadline(time.Now().Add(time.Second))
				if _, err := f.control.Read(make([]byte, 1)); err != io.EOF {
					t.Fatal("terminal acceptance left control writer open", err)
				}
				select {
				case err := <-done:
					t.Fatal("terminal frame substituted for EOF/reap", err)
				default:
				}
				f.result.Close()
				f.slave.Close()
				close(f.r.w.reaped)
				if err := finishCompletion(t, done); err != nil {
					t.Fatal("valid crossed terminal failed", err)
				}
				for _, kind := range []string{"draining", "closed"} {
					var covering *protocol.OutputMark
					for {
						m := nextRelayFrame(t, f.relayFixture)
						if mark, ok := m.(*protocol.OutputMark); ok {
							covering = mark
							continue
						}
						if covering == nil {
							t.Fatal("terminal event lacks immediately preceding covering mark", m)
						}
						switch v := m.(type) {
						case *protocol.Draining:
							if kind != "draining" || v.Reason != "requested-reason" || v.OutputThrough != covering.Offset || v.Seq != covering.Seq+1 {
								t.Fatal("terminal replaced requested reason", m)
							}
						case *protocol.Closed:
							if kind != "closed" || v.Reason != "requested-reason" || v.OutputThrough != covering.Offset || v.Seq != covering.Seq+1 {
								t.Fatal("wrong terminal ordering", m)
							}
						default:
							t.Fatal("unexpected terminal result", m)
						}
						break
					}
				}
			})
		}
	}
}

func TestIdleTerminalRejectsInvalidClosure(t *testing.T) {
	for _, name := range []string{"wrong-id", "wrong-cwd", "wrong-reason", "nonterminal", "duplicate", "trailing", "partial", "premature-eof", "nonzero-awsh", "signalled-awsh"} {
		t.Run(name, func(t *testing.T) {
			f := startFixtureFor(t, nil)
			f.r.w.reaped = make(chan struct{})
			var reapErr error
			if name == "nonzero-awsh" || name == "signalled-awsh" {
				reapErr = errors.New(name)
				f.r.w.waitErr = reapErr
			}
			done := make(chan error, 1)
			go func() { done <- f.c.ShutdownIdle("requested-reason") }()
			f.readPrivate(t)
			switch name {
			case "wrong-id":
				f.private(t, protocol.PrivateShellExit{ActiveOperationID: "stale", CWD: "/tmp"})
			case "wrong-cwd":
				f.private(t, protocol.PrivateShellExit{CWD: "/other"})
			case "wrong-reason":
				// The wire codec rejects a non-shutdown closed reason too.
				f.result.Write([]byte("awsh-v1\x00closed\x00other\x000\x00/tmp\x00"))
			case "nonterminal":
				f.private(t, protocol.PrivateCompleted{OperationID: "stale", PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{}})
			case "premature-eof":
			default:
				f.private(t, protocol.PrivateClosed{Reason: "shutdown", CWD: "/tmp"})
				if name == "duplicate" {
					f.private(t, protocol.PrivateShellExit{CWD: "/tmp"})
				} else if name == "trailing" {
					f.result.Write([]byte("garbage\x00"))
				} else if name == "partial" {
					f.result.Write([]byte("awsh-v1\x00shell_exit\x00"))
				}
			}
			f.result.Close()
			f.slave.Close()
			close(f.r.w.reaped)
			err := finishCompletion(t, done)
			if err == nil || (reapErr != nil && !errors.Is(err, reapErr)) {
				t.Fatal("invalid closure became success or lost reap cause", err)
			}
			if f.r.w.channels.Err() == nil {
				t.Fatal("invalid terminal left session usable")
			}
		})
	}
}

func TestIdleTerminalClosureKeepsMarkBudget(t *testing.T) {
	f := relayFixtureFor(t)
	ctx := relayTestContext(t)
	if err := f.r.Close(ctx); err != nil {
		t.Fatal(err)
	}
	f.slave.Close()
	// The drain can publish its mark, but closed must consume its own mark.
	f.r.w.marks = markLimit - 1
	err := f.r.w.finishTerminalDrain(ctx, "budget-test")
	if err == nil || !strings.Contains(err.Error(), "output mark budget exhausted") || f.r.w.marks != markLimit {
		t.Fatal("closure bypassed the session mark budget", err, f.r.w.marks)
	}
	if _, ok := nextRelayFrame(t, f).(*protocol.OutputMark); !ok {
		t.Fatal("missing drain mark")
	}
	if _, ok := nextRelayFrame(t, f).(*protocol.Draining); !ok {
		t.Fatal("drain did not precede closure mark failure")
	}
	f.r.w.channels.Close()
	for frame := range f.frames {
		if _, ok := frame.(*protocol.Closed); ok {
			t.Fatal("mark exhaustion produced successful closure")
		}
	}
}

func TestIdleTerminalKeepsCallerDeadline(t *testing.T) {
	for _, waiting := range []string{"EOF", "Awsh"} {
		t.Run(waiting, func(t *testing.T) {
			f := startFixtureFor(t, nil)
			ctx, end := context.WithTimeout(context.Background(), 30*time.Millisecond)
			defer end()
			if err := f.r.Close(relayTestContext(t)); err != nil {
				t.Fatal(err)
			}
			f.r.w.reaped = make(chan struct{})
			next := func(ctx context.Context) (protocol.PrivateMessage, error) {
				if waiting == "Awsh" {
					return nil, io.EOF
				}
				<-ctx.Done()
				return nil, ctx.Err()
			}
			err := f.r.w.finishIdleTerminal(ctx, "requested-reason", &protocol.PrivateClosed{Reason: "shutdown", CWD: "/tmp"}, next, nil)
			if !errors.Is(err, context.DeadlineExceeded) || (waiting == "EOF" && !strings.Contains(err.Error(), "private EOF required")) {
				t.Fatal("terminal closure changed deadline or failure", err)
			}
		})
	}
}
