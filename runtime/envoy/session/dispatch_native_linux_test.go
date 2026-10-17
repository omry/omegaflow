//go:build linux

package session

import (
	"context"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func nativeControlEvent(t *testing.T, n *nativeOrdinary) protocol.Message {
	t.Helper()
	for {
		m := n.frame(t)
		if _, mark := m.(*protocol.OutputMark); !mark {
			return m
		}
	}
}
func nativeControlCensus(n *nativeOrdinary, extra int64) censusPeer {
	sid := int64(n.c.w.command.Process.Pid)
	return censusPeer{prove: func(ctx context.Context, observed int64) error {
		if observed != sid {
			return fmt.Errorf("wrong isolated session")
		}
		return ctx.Err()
	}, members: func(ctx context.Context, pgid int64) ([]foregroundMember, error) {
		if pgid != n.c.w.ShellPID {
			return nil, fmt.Errorf("unexpected isolated foreground group %d", pgid)
		}
		ids := []int64{n.c.w.ShellPID}
		if extra != 0 {
			ids = append(ids, extra)
		}
		members := make([]foregroundMember, 0, len(ids))
		for _, id := range ids {
			data, err := os.ReadFile("/proc/" + strconv.FormatInt(id, 10) + "/stat")
			if err != nil {
				return nil, err
			}
			fields := strings.Fields(string(data)[strings.LastIndex(string(data), ") ")+2:])
			if len(fields) < 4 || fields[0] == "Z" || fields[2] != strconv.FormatInt(pgid, 10) || fields[3] != strconv.FormatInt(sid, 10) {
				return nil, fmt.Errorf("wrong isolated foreground member %d", id)
			}
			members = append(members, foregroundMember{PID: id, PGID: pgid, SessionID: sid, Controlled: true})
		}
		return members, ctx.Err()
	}}
}
func nativeIgnoredINT(pid int64) error {
	data, err := os.ReadFile("/proc/" + strconv.FormatInt(pid, 10) + "/status")
	if err != nil {
		return err
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "SigIgn:") {
			v, err := strconv.ParseUint(strings.TrimSpace(strings.TrimPrefix(line, "SigIgn:")), 16, 64)
			if err != nil {
				return err
			}
			if v&2 == 0 {
				return fmt.Errorf("SIGINT ignore missing for %d", pid)
			}
			return nil
		}
	}
	return fmt.Errorf("missing signal disposition")
}
func nativeControlResult(t *testing.T, n *nativeOrdinary, done <-chan error, kind, id string, wantStatus int64) {
	t.Helper()
	if err := finishCompletion(t, done); err != nil {
		t.Fatal("native controlled completion", err)
	}
	m := nativeControlEvent(t, n)
	var through int64
	switch v := m.(type) {
	case *protocol.OperationCancelled:
		if kind != "cancel" || v.OperationID != id || v.Reason != "native-control" || v.Status == nil || *v.Status != wantStatus {
			t.Fatal("wrong cancelled result", m)
		}
		through = v.OutputThrough
	case *protocol.OperationFinalized:
		if kind != "finalize" || v.OperationID != id || v.Reason != "native-control" {
			t.Fatal("wrong finalized result", m)
		}
		through = v.OutputThrough
	default:
		t.Fatal("lifecycle replaced by wrong result", m)
	}
	if n.markOffset < through {
		t.Fatal("missing final covering mark")
	}
	n.bytesThrough(t, through)
}

func TestNativeControlBashInterruptAndReuse(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_CONTROL") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	for _, kind := range []string{"cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
			defer end()
			id := "control-" + kind
			n.begin(t, ctx, id, "builtin printf 'CONTROL_READ_READY\\n'; builtin read -r answer")
			var prefix strings.Builder
			n.terminal.SetReadDeadline(time.Now().Add(2 * time.Second))
			for !strings.HasSuffix(ordinaryVisible(prefix.String()), "CONTROL_READ_READY\n") {
				var b [1]byte
				if _, err := io.ReadFull(n.terminal, b[:]); err != nil || prefix.Len() > 4096 {
					t.Fatal("Bash read precondition", prefix.String(), err)
				}
				prefix.WriteByte(b[0])
			}
			n.through += int64(prefix.Len())
			peer := nativeControlCensus(n, 0)
			members := peer.members
			samples := 0
			peer.members = func(job context.Context, pgid int64) ([]foregroundMember, error) {
				samples++
				if samples == 1 {
					return nil, nil
				}
				return members(job, pgid)
			}
			peers := &controlPeers{sessionID: int64(n.c.w.command.Process.Pid), census: peer}
			done := make(chan error, 1)
			go func() { done <- n.c.completeControlled(ctx, n.cleanup(t, id), peers) }()
			if kind == "cancel" {
				n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: id, Reason: "native-control"})
			} else {
				n.request(t, &protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq}, OperationID: id, Reason: "native-control"})
			}
			nativeControlResult(t, n, done, kind, id, 130)
			n.execute(t, "after-control", "builtin printf 'REUSABLE\\n'", "REUSABLE\n", "/", 0)
			if err := n.c.ShutdownIdle("recording-end"); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestNativeControlCompletionHelperWindow(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_CONTROL") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	for _, kind := range []string{"cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
			defer end()
			id := "helper-window-" + kind
			n.begin(t, ctx, id, ":")
			// Interpose only on the existing decoder's validated event. Awsh has
			// accepted its real helper; Envoy has not accepted input_close yet.
			var held privateResult
			select {
			case held = <-n.c.private.events:
			case <-ctx.Done():
				t.Fatal("completion helper did not connect")
			}
			proposal, ok := held.message.(*protocol.PrivateInputClose)
			if held.err != nil || !ok || proposal.OperationID != id {
				t.Fatal("wrong held return", held)
			}
			if err := nativeIgnoredINT(proposal.CompletionHelperPID); err != nil {
				t.Fatal(err)
			}
			entered, release := make(chan struct{}), make(chan struct{})
			peer := nativeControlCensus(n, proposal.CompletionHelperPID)
			prove := peer.prove
			peer.prove = func(job context.Context, sid int64) error {
				close(entered)
				select {
				case <-release:
					return prove(job, sid)
				case <-job.Done():
					return job.Err()
				}
			}
			peers := &controlPeers{sessionID: int64(n.c.w.command.Process.Pid), census: peer}
			done := make(chan error, 1)
			go func() { done <- n.c.completeControlled(ctx, n.cleanup(t, id), peers) }()
			if kind == "cancel" {
				n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: id, Reason: "native-control"})
			} else {
				n.request(t, &protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq}, OperationID: id, Reason: "native-control"})
			}
			select {
			case <-entered:
			case <-ctx.Done():
				t.Fatal("lifecycle not selected in helper window")
			}
			// The blocked proof cannot finish or change this pointer until release.
			// The done channel then proves proof completion; a later resize ack must
			// pass the owner's ready-proof check and its actual signal syscall first.
			proof := n.c.running.proof
			close(release)
			select {
			case <-proof.done:
			case <-ctx.Done():
				t.Fatal("proof failed to join")
			}
			n.request(t, &protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: n.seq}, Columns: 91, Rows: 31})
			if _, ok := nativeControlEvent(t, n).(*protocol.ResizeApplied); !ok {
				t.Fatal("signal/resize owner did not advance")
			}
			if err := nativeIgnoredINT(proposal.CompletionHelperPID); err != nil {
				t.Fatal("helper did not survive SIGINT", err)
			}
			if err := nativeIgnoredINT(n.c.w.ShellPID); err != nil {
				t.Fatal("Bash did not retain temporary SIGINT ignore", err)
			}
			select {
			case n.c.private.events <- held:
			case <-ctx.Done():
				t.Fatal("return fact reinsertion failed")
			}
			nativeControlResult(t, n, done, kind, id, 0)
			n.execute(t, "after-helper-window", "builtin printf 'HELPER_WINDOW_OK\\n'", "HELPER_WINDOW_OK\n", "/", 0)
			if err := n.c.ShutdownIdle("recording-end"); err != nil {
				t.Fatal(err)
			}
			t.Logf("Real completion helper %d and Bash survived owner-selected SIGINT; %s result and reusable shell verified", proposal.CompletionHelperPID, kind)
		})
	}
}

func TestNativeControlGateInputAndContinue(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_CONTROL") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
	defer end()
	id := "native-control-gate"
	n.begin(t, ctx, id, "PATH=/hostile; awsh gate first; builtin read -r answer; builtin printf 'INPUT=%s\\n' \"$answer\"; awsh gate second; builtin printf 'GATES_DONE\\n'")
	done := make(chan error, 1)
	go func() { done <- n.c.Complete(ctx, n.cleanup(t, id)) }()
	var output strings.Builder
	for _, gate := range []string{"first", "second"} {
		m, ok := nativeControlEvent(t, n).(*protocol.OperationReady)
		if !ok || m.OperationID != id || m.GateID != gate {
			t.Fatal("wrong ready", m)
		}
		output.WriteString(n.bytesThrough(t, m.OutputThrough))
		input := ""
		if gate == "first" {
			input = "kept-input\n"
		}
		n.request(t, &protocol.Continue{Header: protocol.Header{Schema: protocol.Schema, Type: "continue", Seq: n.seq}, OperationID: id, GateID: gate, InputThrough: n.inputThrough + int64(len(input))})
		if input != "" {
			if _, err := io.WriteString(n.terminal, input); err != nil {
				t.Fatal(err)
			}
			n.inputThrough += int64(len(input))
		}
		continued, ok := nativeControlEvent(t, n).(*protocol.OperationContinued)
		if !ok || continued.GateID != gate {
			t.Fatal("wrong continued", continued)
		}
		output.WriteString(n.bytesThrough(t, continued.OutputThrough))
	}
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	m, ok := nativeControlEvent(t, n).(*protocol.OperationCompleted)
	if !ok || m.OperationID != id || m.Status != 0 {
		t.Fatal("wrong gate completion", m)
	}
	output.WriteString(n.bytesThrough(t, m.OutputThrough))
	if !strings.Contains(output.String(), "INPUT=kept-input") || !strings.Contains(output.String(), "GATES_DONE") || strings.Contains(output.String(), "__OMEGAFLOW_AWSH_") {
		t.Fatal("gate source/input or no-redisplay invariant", output.String())
	}
	if err := n.c.ShutdownIdle("recording-end"); err != nil {
		t.Fatal(err)
	}
}
