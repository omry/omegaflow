//go:build linux

package session

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type nativeOrdinary struct {
	c                   *Coordinator
	terminal, telemetry *net.TCPConn
	reader              *bufio.Reader
	seq, through        int64
	lastSeq, markOffset int64
	markElapsed         int64
	peer                ReturnCleanup
	input               string
	inputThrough        int64
}

func nativeOrdinaryFor(t *testing.T) *nativeOrdinary {
	t.Helper()
	if os.Geteuid() == 0 {
		t.Fatal("genuine non-root qualified target required")
	}
	r, terminal, telemetry := nativePair(t)
	w, err := r.s.Launch(r.ctx, 80, 24)
	if err != nil {
		if failed := r.s.FailedWorkload(); failed != nil {
			ctx, end := context.WithTimeout(context.Background(), finalDrainBudget)
			defer end()
			_ = failed.Close(ctx)
		}
		r.s.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, end := context.WithTimeout(context.Background(), finalDrainBudget)
		defer end()
		_ = w.Close(ctx)
		r.s.Close()
	})
	reader := bufio.NewReader(telemetry)
	ready, ok := nativeFrame(t, reader).(*protocol.Ready)
	if !ok || ready.ShellPID != w.ShellPID {
		t.Fatal("missing actual public startup identity", ready)
	}
	terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.CopyN(io.Discard, terminal, ready.OutputThrough); err != nil {
		t.Fatal(err)
	}
	relay, err := NewRelay(r.s.ctx, w)
	if err != nil {
		t.Fatal(err)
	}
	c, err := NewCoordinator(r.s.ctx, w, relay, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, end := context.WithTimeout(context.Background(), finalDrainBudget)
		defer end()
		_ = relay.Close(ctx)
		_ = c.Close(ctx)
	})
	return &nativeOrdinary{c: c, terminal: terminal, telemetry: telemetry, reader: reader, seq: 2, through: ready.OutputThrough, lastSeq: ready.Seq}
}

func (n *nativeOrdinary) request(t *testing.T, m protocol.Message) {
	t.Helper()
	b, err := protocol.EncodeController(m)
	if err != nil {
		t.Fatal(err)
	}
	n.telemetry.SetWriteDeadline(time.Now().Add(time.Second))
	if _, err := n.telemetry.Write(b); err != nil {
		t.Fatal(err)
	}
	n.seq++
}

func (n *nativeOrdinary) begin(t *testing.T, ctx context.Context, id, source string) int64 {
	t.Helper()
	n.telemetry.SetReadDeadline(time.Now().Add(10 * time.Second))
	n.request(t, &protocol.Execute{Header: protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq},
		OperationID: id, Source: source, InputThrough: n.inputThrough, ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "shared", Inspections: []protocol.Inspection{}})
	e := receiveInput(t, n.c.w.channels)
	request, ok := e.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("controller execute was not decoded", e)
	}
	out, err := n.c.Start(ctx, *request)
	if err != nil || !out.Started {
		t.Fatal("actual source did not start", out, err)
	}
	for {
		m := n.frame(t)
		if _, ok := m.(*protocol.OutputMark); ok {
			continue
		}
		started, ok := m.(*protocol.OperationStarted)
		if !ok || started.OperationID != id || started.OutputStart < n.through || out.OutputStart != started.OutputStart {
			t.Fatal("wrong actual operation start", m, out)
		}
		start := started.OutputStart
		if n.markOffset < start {
			t.Fatal("start lacks a covering output mark", n.markOffset, start)
		}
		if prefix := n.bytesThrough(t, start); strings.Trim(ordinaryVisible(prefix), "\n") != "" {
			t.Fatalf("source/frame or diagnostic before start: %q", prefix)
		}
		return start
	}
}

func (n *nativeOrdinary) cleanup(t *testing.T, id string) ReturnCleanup {
	t.Helper()
	return func(job context.Context, operation string, pid int64) error {
		if operation != id || pid <= 0 {
			return fmt.Errorf("wrong actual completion helper: %s/%d", operation, pid)
		}
		data, err := os.ReadFile("/proc/" + strconv.FormatInt(pid, 10) + "/stat")
		if err != nil {
			return err
		}
		fields := strings.Fields(string(data)[strings.LastIndex(string(data), ") ")+2:])
		if len(fields) < 2 || fields[1] != strconv.FormatInt(n.c.w.ShellPID, 10) {
			return fmt.Errorf("completion helper is not the selected shell's child")
		}
		if n.peer != nil {
			return n.peer(job, operation, pid)
		}
		return job.Err()
	}
}

func (n *nativeOrdinary) execute(t *testing.T, id, source, output, cwd string, status int64) {
	t.Helper()
	ctx, end := context.WithTimeout(n.c.w.channels.ctx, 10*time.Second)
	defer end()
	start := n.begin(t, ctx, id, source)
	if n.input != "" {
		n.terminal.SetWriteDeadline(time.Now().Add(time.Second))
		written, err := io.WriteString(n.terminal, n.input)
		if err != nil || written != len(n.input) {
			t.Fatal("actual terminal input failed", written, err)
		}
		n.inputThrough += int64(written)
		n.input = ""
	}
	if err := n.c.Complete(ctx, n.cleanup(t, id)); err != nil {
		t.Fatal("actual ordinary completion", err)
	}
	for {
		m := n.frame(t)
		if _, ok := m.(*protocol.OutputMark); ok {
			continue
		}
		completed, ok := m.(*protocol.OperationCompleted)
		if !ok || completed.OperationID != id || completed.Status != status || completed.Cwd != cwd ||
			completed.OutputStart != start || completed.OutputThrough < start || len(completed.InspectionResults) != 0 {
			t.Fatal("wrong actual ordinary completion", m)
		}
		if n.markOffset < completed.OutputThrough {
			t.Fatal("completion lacks a covering output mark", n.markOffset, completed.OutputThrough)
		}
		actual := n.bytesThrough(t, completed.OutputThrough)
		if ordinaryVisible(actual) != strings.ReplaceAll(output, "\r", "") {
			t.Fatalf("actual PTY output changed or contained source bytes: %q", actual)
		}
		break
	}
}

func (n *nativeOrdinary) frame(t *testing.T) protocol.Message {
	t.Helper()
	m := nativeFrame(t, n.reader)
	b, err := protocol.EncodeEnvoy(m)
	if err != nil {
		t.Fatal(err)
	}
	// Every public frame, including output marks, occupies the next sequence.
	// Read the common header without making a second telemetry reader.
	var h protocol.Header
	if err := json.Unmarshal(b, &h); err != nil || h.Seq != n.lastSeq+1 {
		t.Fatal("public frame sequence changed", h, err)
	}
	n.lastSeq = h.Seq
	if mark, ok := m.(*protocol.OutputMark); ok {
		if mark.Stream != "pty" || mark.Offset < n.markOffset || mark.ElapsedUS < n.markElapsed {
			t.Fatal("output mark regressed", mark)
		}
		n.markOffset, n.markElapsed = mark.Offset, mark.ElapsedUS
	}
	return m
}

func (n *nativeOrdinary) bytesThrough(t *testing.T, through int64) string {
	t.Helper()
	if through < n.through || through-n.through > 4096 {
		t.Fatal("invalid native output range", n.through, through)
	}
	actual := make([]byte, through-n.through)
	n.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(n.terminal, actual); err != nil {
		t.Fatal("native output range was not delivered", err)
	}
	t.Logf("PTY [%d,%d): %q", n.through, through, actual)
	n.through = through
	return string(actual)
}

func ordinaryVisible(raw string) string {
	// The qualified adapter permits only these Readline terminal controls.
	// Retain every raw byte and range in the log; never strip source text.
	for _, control := range []string{"\x1b[?2004h", "\x1b[?2004l", "\x1b[K", "\r"} {
		raw = strings.ReplaceAll(raw, control, "")
	}
	return raw
}

func TestNativePublicOrdinaryReturn(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_RETURN") != "1" {
		t.Skip("genuine qualified target only")
	}
	n := nativeOrdinaryFor(t)
	reader, pump := n.c.w.Reader, n.c.private
	n.execute(t, "first", "printf 'one\\n'; export KEEP=retained; cd /tmp; false", "one\r\n", "/tmp", 1)
	n.execute(t, "second", "printf '%s:%s:%s\\n' \"$KEEP\" \"$PWD\" \"$?\"", "retained:/tmp:1\r\n", "/tmp", 0)
	n.input = "typed\n"
	n.execute(t, "input", "IFS= read -r LINE; printf 'got:%s\\n' \"$LINE\"", "typed\r\ngot:typed\r\n", "/tmp", 0)
	n.execute(t, "after-input", "printf '%s\\n' \"$LINE\"", "typed\r\n", "/tmp", 0)
	n.request(t, &protocol.Shutdown{Header: protocol.Header{Schema: protocol.Schema, Type: "shutdown", Seq: n.seq}, Reason: "requested-shutdown"})
	e := receiveInput(t, n.c.w.channels)
	shutdown, ok := e.Message.(*protocol.Shutdown)
	if !ok {
		t.Fatal("missing actual idle shutdown", e)
	}
	if err := n.c.ShutdownIdle(shutdown.Reason); err != nil {
		t.Fatal("actual idle shutdown after reuse", err)
	}
	for _, kind := range []string{"draining", "closed"} {
		for {
			m := n.frame(t)
			if _, ok := m.(*protocol.OutputMark); ok {
				continue
			}
			switch v := m.(type) {
			case *protocol.Draining:
				if kind != "draining" || v.OutputThrough != n.through {
					t.Fatal("wrong drain boundary", v)
				}
			case *protocol.Closed:
				if kind != "closed" || v.OutputThrough != n.through {
					t.Fatal("wrong close boundary", v)
				}
			default:
				t.Fatal("unexpected shutdown frame", m)
			}
			break
		}
	}
	if n.c.w.Reader != reader || n.c.private != pump {
		t.Fatal("ordinary return or idle shutdown replaced the private reader")
	}
	select {
	case <-pump.done:
	default:
		t.Fatal("actual private pump survived shutdown")
	}
}

func TestNativePublicOrdinaryCleanup(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_RETURN") != "1" {
		t.Skip("genuine qualified target only")
	}
	for _, failure := range []bool{false, true} {
		t.Run(fmt.Sprintf("peer-failure=%t", failure), func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			var helperPID int64
			n.peer = func(ctx context.Context, _ string, pid int64) error {
				helperPID = pid
				// The real completion helper must stay alive while cleanup waits.
				// No completion frame may precede the deterministic peer result.
				n.telemetry.SetReadDeadline(time.Now().Add(50 * time.Millisecond))
				for {
					line, err := n.reader.ReadBytes('\n')
					if len(line) != 0 {
						m, decodeErr := protocol.DecodeEnvoy(line)
						if _, ok := m.(*protocol.OutputMark); decodeErr != nil || !ok {
							return fmt.Errorf("public result before cleanup success: %q/%v", line, decodeErr)
						}
						var h protocol.Header
						if json.Unmarshal(line, &h) != nil || h.Seq != n.lastSeq+1 {
							return errors.New("output mark sequence changed during cleanup")
						}
						n.lastSeq = h.Seq
					}
					if os.IsTimeout(err) {
						break
					}
					if err != nil {
						return err
					}
				}
				n.telemetry.SetReadDeadline(time.Now().Add(5 * time.Second))
				if _, err := os.Stat("/proc/" + strconv.FormatInt(pid, 10)); err != nil {
					return fmt.Errorf("completion helper released before cleanup success: %w", err)
				}
				if failure {
					return errors.New("injected native cleanup peer failure")
				}
				return ctx.Err()
			}
			if !failure {
				n.execute(t, "delayed", "printf 'delayed\\n'", "delayed\r\n", n.c.w.CWD, 0)
				if _, err := os.Stat("/proc/" + strconv.FormatInt(helperPID, 10)); !errors.Is(err, os.ErrNotExist) {
					t.Fatal("completion helper was not reaped before public completion", err)
				}
				return
			}
			ctx, end := context.WithTimeout(n.c.w.channels.ctx, 5*time.Second)
			defer end()
			n.begin(t, ctx, "failed", "printf 'failed\\n'")
			if err := n.c.Complete(ctx, n.cleanup(t, "failed")); err == nil || n.c.w.channels.Err() == nil || n.c.active == nil || helperPID == 0 {
				t.Fatal("failed peer authorized reusable completion", err)
			}
			for {
				line, err := n.reader.ReadBytes('\n')
				if len(line) != 0 {
					m, decodeErr := protocol.DecodeEnvoy(line)
					if decodeErr != nil {
						t.Fatal(decodeErr)
					}
					if _, ok := m.(*protocol.OperationCompleted); ok {
						t.Fatal("failed peer published operation_completed")
					}
				}
				if err != nil {
					if err != io.EOF {
						t.Fatal("failed channel did not close", err)
					}
					break
				}
			}
		})
	}
}
