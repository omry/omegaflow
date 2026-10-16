//go:build linux

package session

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type censusPeer struct {
	prove   func(context.Context, int64) error
	members func(context.Context, int64) ([]foregroundMember, error)
}

func (p censusPeer) ProveBoundary(ctx context.Context, sid int64) error { return p.prove(ctx, sid) }
func (p censusPeer) Members(ctx context.Context, pgid int64) ([]foregroundMember, error) {
	return p.members(ctx, pgid)
}

// This child is the sole foreground member of a test-owned controlling session.
// It never signals another process and never invokes application shell code.
func TestLifecyclePTYChild(t *testing.T) {
	if os.Getenv("OMEGAFLOW_LIFECYCLE_PTY_CHILD") != "1" {
		return
	}
	interrupts := make(chan os.Signal, 1)
	signal.Notify(interrupts, syscall.SIGINT)
	fmt.Println("READY")
	for range interrupts {
		fmt.Println("INT")
	}
}

type lifecyclePTY struct {
	master *os.File
	reader *bufio.Reader
	pid    int64
}

func lifecyclePTYFor(t *testing.T) *lifecyclePTY {
	t.Helper()
	master, slave, err := openPTY(80, 24)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { master.Close() })
	defer slave.Close()
	cmd := exec.Command(os.Args[0], "-test.run=^TestLifecyclePTYChild$")
	cmd.Env = append(os.Environ(), "OMEGAFLOW_LIFECYCLE_PTY_CHILD=1")
	cmd.Stdin, cmd.Stdout, cmd.Stderr = slave, slave, slave
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	p := &lifecyclePTY{master: master, reader: bufio.NewReader(master), pid: int64(cmd.Process.Pid)}
	if line := p.line(t); line != "READY\r\n" {
		t.Fatalf("child not ready: %q", line)
	}
	return p
}
func (p *lifecyclePTY) line(t *testing.T) string {
	t.Helper()
	p.master.SetReadDeadline(time.Now().Add(2 * time.Second))
	line, err := p.reader.ReadString('\n')
	if err != nil {
		t.Fatal("test-owned PTY read", line, err)
	}
	return line
}
func (p *lifecyclePTY) silent(t *testing.T) {
	t.Helper()
	p.master.SetReadDeadline(time.Now().Add(30 * time.Millisecond))
	b, err := p.reader.ReadByte()
	if err == nil || !os.IsTimeout(err) {
		t.Fatal("unexpected foreground output/signal", b, err)
	}
	p.master.SetReadDeadline(time.Time{})
}
func (p *lifecyclePTY) census() censusPeer {
	return censusPeer{prove: func(context.Context, int64) error { return nil }, members: func(_ context.Context, pgid int64) ([]foregroundMember, error) {
		if pgid != p.pid {
			return nil, fmt.Errorf("unexpected test group %d", pgid)
		}
		return []foregroundMember{{PID: p.pid, PGID: p.pid, SessionID: p.pid, Controlled: true}}, nil
	}}
}

func TestLifecyclePTYSignalExactlyOnce(t *testing.T) {
	p := lifecyclePTYFor(t)
	l := releasedLifecycle(t)
	if err := l.accept(context.Background(), lifecycleRequest("finalize", "operation", "recording-end")); err != nil {
		t.Fatal(err)
	}
	deadline, _ := l.grace.Deadline()
	if applied, err := l.signalForeground(p.master, p.pid, p.census()); !applied || err != nil {
		t.Fatal("direct integer ioctl failed", applied, err)
	}
	if line := p.line(t); line != "INT\r\n" {
		t.Fatalf("wrong signal evidence %q", line)
	}
	if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "crossed")); err != nil {
		t.Fatal(err)
	}
	if applied, err := l.signalForeground(p.master, p.pid, p.census()); applied || err != nil {
		t.Fatal("second interrupt permitted", applied, err)
	}
	again, _ := l.grace.Deadline()
	if !again.Equal(deadline) {
		t.Fatal("second request reset original grace")
	}
	p.silent(t)
	if ack, err := l.observeReturn(&protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: p.pid}); !ack || err != nil {
		t.Fatal(ack, err)
	}
	if kind, reason := l.outcome(); kind != "cancelled" || reason != "crossed" {
		t.Fatal(kind, reason)
	}
}

func TestLifecyclePTYEmptyForegroundWinners(t *testing.T) {
	for _, winner := range []string{"input_close", "shell_exit", "live", "timeout"} {
		t.Run(winner, func(t *testing.T) {
			p := lifecyclePTYFor(t)
			l := releasedLifecycle(t)
			if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "reason")); err != nil {
				t.Fatal(err)
			}
			deadline, _ := l.grace.Deadline()
			peer := p.census()
			peer.members = func(context.Context, int64) ([]foregroundMember, error) { return nil, nil }
			for i := 0; i < 3; i++ {
				if applied, err := l.signalForeground(p.master, p.pid, peer); applied || err != nil {
					t.Fatal("empty positive group not retained for resample", applied, err)
				}
				again, _ := l.grace.Deadline()
				if !again.Equal(deadline) {
					t.Fatal("resample reset deadline")
				}
			}
			switch winner {
			case "input_close":
				if ack, err := l.observeReturn(&protocol.PrivateInputClose{OperationID: "operation", CompletionHelperPID: p.pid}); !ack || err != nil {
					t.Fatal(ack, err)
				}
			case "shell_exit":
				if _, err := l.observeReturn(&protocol.PrivateShellExit{ActiveOperationID: "operation", Status: 0, CWD: "/"}); err != nil {
					t.Fatal(err)
				}
			case "timeout":
				if selected, err := l.selectTimeout(deadline); !selected || err != nil {
					t.Fatal(selected, err)
				}
			case "live":
				if applied, err := l.signalForeground(p.master, p.pid, p.census()); !applied || err != nil {
					t.Fatal(applied, err)
				}
				if line := p.line(t); line != "INT\r\n" {
					t.Fatal(line)
				}
			}
			if applied, err := l.signalForeground(p.master, p.pid, p.census()); applied || err != nil {
				t.Fatal("winner allowed another signal", applied, err)
			}
			p.silent(t)
		})
	}
}

func TestLifecyclePTYProofFailureNeverSignals(t *testing.T) {
	for _, kind := range []string{"missing-peer", "unproved-boundary", "wrong-session", "unknown-member", "wrong-member-session", "census-failure", "closed-before-signal"} {
		t.Run(kind, func(t *testing.T) {
			p := lifecyclePTYFor(t)
			l := releasedLifecycle(t)
			if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "reason")); err != nil {
				t.Fatal(err)
			}
			peer := p.census()
			sid := p.pid
			var census foregroundCensus = peer
			switch kind {
			case "missing-peer":
				census = nil
			case "unproved-boundary":
				peer.prove = func(context.Context, int64) error { return errors.New("clean boundary unavailable") }
				census = peer
			case "wrong-session":
				sid++
			case "unknown-member", "wrong-member-session":
				peer.members = func(context.Context, int64) ([]foregroundMember, error) {
					session := p.pid
					if kind == "wrong-member-session" {
						session++
					}
					return []foregroundMember{{PID: p.pid, PGID: p.pid, SessionID: session, Controlled: kind != "unknown-member"}}, nil
				}
				census = peer
			case "census-failure":
				peer.members = func(context.Context, int64) ([]foregroundMember, error) {
					return nil, errors.New("unclassifiable member")
				}
				census = peer
			case "closed-before-signal":
				peer.members = func(context.Context, int64) ([]foregroundMember, error) {
					p.master.Close()
					return []foregroundMember{{PID: p.pid, PGID: p.pid, SessionID: p.pid, Controlled: true}}, nil
				}
				census = peer
			}
			applied, err := l.signalForeground(p.master, sid, census)
			var fatal *StartError
			if applied || !errors.As(err, &fatal) || fatal.Code != "lifecycle-signal" {
				t.Fatal("unproved boundary did not fail closed", applied, err)
			}
			if result, _ := l.outcome(); result != "" {
				t.Fatal("fatal proof authorized a terminal result", result)
			}
			if applied, again := l.signalForeground(p.master, sid, census); applied || again != err {
				t.Fatal("fatal proof was retried/forgotten", applied, again, err)
			}
			if kind != "closed-before-signal" {
				p.silent(t)
			}
		})
	}
}

func TestLifecyclePTYExpiredProofDoesNotBecomeTopologyFailure(t *testing.T) {
	p := lifecyclePTYFor(t)
	l := releasedLifecycle(t)
	ctx, end := context.WithCancel(context.Background())
	defer end()
	if err := l.accept(ctx, lifecycleRequest("finalize", "operation", "reason")); err != nil {
		t.Fatal(err)
	}
	peer := p.census()
	peer.members = func(context.Context, int64) ([]foregroundMember, error) { end(); return nil, context.Canceled }
	if applied, err := l.signalForeground(p.master, p.pid, peer); applied || !errors.Is(err, context.Canceled) || l.failure != nil {
		t.Fatal("phase cancellation fabricated topology failure", applied, err, l.failure)
	}
	p.silent(t)
}

func TestLifecyclePTYIoctlFailure(t *testing.T) {
	file, err := os.OpenFile("/dev/null", os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := signalPTY(file); !errors.Is(err, syscall.ENOTTY) {
		t.Fatal("direct ioctl failure lost errno", err)
	}
	l := releasedLifecycle(t)
	if err := l.accept(context.Background(), lifecycleRequest("cancel", "operation", "reason")); err != nil {
		t.Fatal(err)
	}
	peer := censusPeer{prove: func(context.Context, int64) error { return nil }, members: func(context.Context, int64) ([]foregroundMember, error) {
		t.Fatal("members queried after failed terminal ioctl")
		return nil, nil
	}}
	if applied, err := l.signalForeground(file, 1, peer); applied || !errors.Is(err, syscall.ENOTTY) {
		t.Fatal("query failure not fatal", applied, err)
	}
}

func TestNativeLifecycleBashSignalAndReturn(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_LIFECYCLE") != "1" {
		t.Skip("genuine qualified target only")
	}
	for _, kind := range []string{"cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
			defer end()
			id := "lifecycle-" + kind
			n.begin(t, ctx, id, "builtin printf 'interrupt-ready\\n'; builtin read -r LINE")
			var prefix strings.Builder
			n.terminal.SetReadDeadline(time.Now().Add(3 * time.Second))
			for !strings.HasSuffix(ordinaryVisible(prefix.String()), "interrupt-ready\n") {
				var b [1]byte
				if _, err := io.ReadFull(n.terminal, b[:]); err != nil || prefix.Len() > 4096 {
					t.Fatal("Bash did not reach controlled read", prefix.String(), err)
				}
				prefix.WriteByte(b[0])
			}
			n.through += int64(prefix.Len())
			l := newOperationLifecycle(id)
			defer l.close()
			if err := l.startReleased(ctx, StartOutcome{Started: n.c.active.started, QueuedCancel: n.c.active.cancel}); err != nil {
				t.Fatal(err)
			}
			if err := l.accept(ctx, lifecycleRequest(kind, id, "native-reason")); err != nil {
				t.Fatal(err)
			}
			sid := int64(n.c.w.command.Process.Pid)
			peer := censusPeer{prove: func(context.Context, int64) error { return nil }, members: func(_ context.Context, pgid int64) ([]foregroundMember, error) {
				if pgid != n.c.w.ShellPID {
					return nil, fmt.Errorf("unexpected isolated Bash foreground %d", pgid)
				}
				return []foregroundMember{{PID: n.c.w.ShellPID, PGID: pgid, SessionID: sid, Controlled: true}}, nil
			}}
			if applied, err := l.signalForeground(n.c.w.PTY, sid, peer); !applied || err != nil {
				t.Fatal("real Bash interrupt failed", applied, err)
			}
			if applied, err := l.signalForeground(n.c.w.PTY, sid, peer); applied || err != nil {
				t.Fatal("second Bash interrupt permitted", applied, err)
			}
			n.c.phase = awaitInputClose
			m, err := n.c.result(ctx)
			if err != nil {
				t.Fatal(err)
			}
			proposal, ok := m.(*protocol.PrivateInputClose)
			if !ok {
				t.Fatal("missing Bash-local return fact", m)
			}
			if ack, err := l.observeReturn(proposal); !ack || err != nil {
				t.Fatal(ack, err)
			}
			// The approved isolated cleanup peer keeps the real completion helper
			// blocked. No lifecycle public result or real descendant proof is claimed.
			if err := n.cleanup(t, id)(ctx, id, proposal.CompletionHelperPID); err != nil {
				t.Fatal(err)
			}
			n.c.phase = awaitCompleted
			if err := n.c.send(ctx, protocol.PrivateInputClosed{OperationID: id}); err != nil {
				t.Fatal(err)
			}
			m, err = n.c.result(ctx)
			if err != nil {
				t.Fatal(err)
			}
			completed, ok := m.(*protocol.PrivateCompleted)
			if !ok || completed.Status != 130 {
				t.Fatal("interrupted Bash status not retained", m)
			}
			outcome, reason := l.outcome()
			want := map[string]string{"cancel": "cancelled", "finalize": "finalized"}[kind]
			if outcome != want || reason != "native-reason" {
				t.Fatal("selected outcome lost", outcome, reason)
			}
			t.Logf("Genuine Bash SIGINT and A2.5 return status130: %s, helper=%d", kind, proposal.CompletionHelperPID)
		})
	}
}
