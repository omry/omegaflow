//go:build linux

package session

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestNativeReturnCleanup(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_RETURN_CLEANUP") != "1" {
		t.Skip("requires genuine qualified Bash target")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	cases := []string{"background", "disown", "nohup", "setsid", "double-fork", "ignores-term", "split", "closed-owner", "lost-pin", "held-stdout", "held-stderr"}
	if os.Getenv("OMEGAFLOW_RETURN_FAULT") != "" {
		cases = []string{"overlay"}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			outside := processFixtureCommand("leaf")
			if err := outside.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() { outside.Process.Kill(); outside.Wait() }()
			ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeReturnCleanupFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_RETURN_CHILD="+scenario,
				"OMEGAFLOW_PROCESS_OUTSIDE="+strconv.Itoa(outside.Process.Pid), "GORACE=atexit_sleep_ms=0")
			bridge := t.TempDir()
			cmd.Env = append(cmd.Env, "OMEGAFLOW_RETURN_BRIDGE="+bridge)
			ended := make(chan struct{})
			held := make(chan error, 1)
			if strings.HasPrefix(scenario, "held-") {
				go func() { held <- returnOutsideWriter(ctx, bridge, ended) }()
			}
			output, err := cmd.CombinedOutput()
			close(ended)
			if strings.HasPrefix(scenario, "held-") {
				if e := <-held; e != nil {
					t.Fatal(e)
				}
			}
			if err != nil || !strings.Contains(string(output), "ACTUAL RETURN "+scenario+" PROVED") {
				t.Fatalf("actual return %s: %v\n%s", scenario, err, output)
			}
			if err := outside.Process.Signal(syscall.Signal(0)); err != nil {
				t.Fatal("outside-tree sibling was affected", err)
			}
			t.Logf("%s", output)
		})
	}
	after, err := processSubreaperState()
	if err != nil || after != before {
		t.Fatal("shared qualification harness subreaper changed", before, after, err)
	}
}

func returnAuthoredSource(scenario, pidfile string) string {
	if scenario == "overlay" && os.Getenv("OMEGAFLOW_RETURN_FAULT") == "removal" {
		return returnAuthoredSource("split", pidfile)
	}
	quoted := "'" + strings.ReplaceAll(pidfile, "'", "'\\''") + "'"
	child := "sleep 60 >/dev/null 2>&1 & builtin printf '%s' \"$!\" >" + quoted
	switch scenario {
	case "background", "closed-owner", "lost-pin", "overlay":
	case "disown":
		child += "; disown \"$!\""
	case "nohup":
		child = "nohup " + child
	case "setsid":
		child = "setsid /bin/sh -c 'echo $$ >\"$1\"; exec sleep 60' child " + quoted + " >/dev/null 2>&1 &"
	case "double-fork", "split", "held-stdout", "held-stderr":
		child = "/usr/bin/python3 -c 'import os,sys,time\nif os.fork(): os._exit(0)\nos.setsid()\nif os.fork(): os._exit(0)\nwith open(sys.argv[1],\"w\") as f: f.write(str(os.getpid()))\ntime.sleep(60)' " + quoted
	case "ignores-term":
		child = "/usr/bin/python3 -c 'import os,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open(sys.argv[1],\"w\").write(str(os.getpid()));time.sleep(60)' " + quoted + " &"
	default:
		panic("unknown actual return case")
	}
	return child + "\n builtin read -r release; proof_state=retained; builtin printf 'out-é\\000\\377\\r\\n'; builtin printf 'err-λ\\000\\376\\n' >&2; builtin cd /tmp; (exit 7)"
}

func returnPID(t *testing.T, ctx context.Context, path string) int {
	t.Helper()
	for {
		data, err := os.ReadFile(path)
		if err == nil {
			if pid, err := strconv.Atoi(strings.TrimSpace(string(data))); err == nil && pid > 0 {
				return pid
			}
		} else if !os.IsNotExist(err) {
			t.Fatal(err)
		}
		select {
		case <-time.After(time.Millisecond):
		case <-ctx.Done():
			t.Fatal("actual authored process did not publish its identity", ctx.Err())
		}
	}
}

func TestNativeReturnCleanupFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_RETURN_CHILD")
	returnFaultTest = t
	if scenario == "" {
		return
	}
	// Only this dedicated Envoy process changes subreaper state, before its
	// actual manifested Awsh launch. It owns no unrelated child process.
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, cancel := context.WithTimeout(n.c.w.channels.ctx, 20*time.Second)
	defer cancel()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	outside, err := strconv.Atoi(os.Getenv("OMEGAFLOW_PROCESS_OUTSIDE"))
	if err != nil {
		t.Fatal(err)
	}
	outsidePin, _, err := pinProcess(ctx, outside)
	if err != nil {
		t.Fatal(err)
	}
	defer outsidePin.close()
	pidfile := filepath.Join(t.TempDir(), "authored-pid")
	req := startRequest()
	req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	req.Source = returnAuthoredSource(scenario, pidfile)
	req.InputThrough = n.inputThrough
	if scenario == "split" || strings.HasPrefix(scenario, "held-") || os.Getenv("OMEGAFLOW_RETURN_FAULT") == "removal" {
		req.ExecutionShape, req.Timing, req.Observation = "split", "presentation", "exclusive"
		n.c.setup = NewSplitSetup()
	}
	sink := &nativeSplitSink{wake: make(chan struct{}, 1)}
	rawDone := make(chan error, 1)
	base := n.through
	_ = n.terminal.SetReadDeadline(time.Now().Add(20 * time.Second))
	go func() { _, err := io.Copy(sink, n.terminal); rawDone <- err }()
	joined := false
	t.Cleanup(func() {
		if !joined {
			_ = n.terminal.SetReadDeadline(time.Now())
			select {
			case <-rawDone:
			case <-time.After(time.Second):
				t.Error("actual return collector did not join")
			}
		}
	})
	n.request(t, &req)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal(event)
	}
	started, err := n.c.Start(ctx, *actual)
	if err != nil || !started.Started {
		t.Fatal("actual source start", started, err)
	}
	done := make(chan error, 1)
	go func() { done <- n.c.completeOrdinary(ctx, owner) }()
	pid := returnPID(t, ctx, pidfile)
	authored := cleanupFixturePin(t, pid)
	if scenario == "double-fork" || req.ExecutionShape == "split" {
		for {
			p, err := authored.observe(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if p.Parent == os.Getpid() && p.Session != owner.awsh {
				break
			}
		}
	}
	if req.ExecutionShape == "split" {
		// A real escaped daemon retains both inherited split writers.
		for _, fd := range []string{"1", "2"} {
			link, err := os.Readlink(fmt.Sprintf("/proc/%d/fd/%s", pid, fd))
			if err != nil || !strings.HasPrefix(link, filepath.Join(sessionRuntimeDir, "split")) {
				t.Fatal("escaped daemon did not retain real split writer", link, err)
			}
		}
	}
	if strings.HasPrefix(scenario, "held-") {
		bridge := os.Getenv("OMEGAFLOW_RETURN_BRIDGE")
		path := filepath.Join(sessionRuntimeDir, "split", req.OperationID+"."+strings.TrimPrefix(scenario, "held-"))
		if err := os.WriteFile(filepath.Join(bridge, "path"), []byte(path), 0600); err != nil {
			t.Fatal(err)
		}
		for {
			if _, err := os.Stat(filepath.Join(bridge, "held")); err == nil {
				break
			}
			select {
			case <-time.After(time.Millisecond):
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
		}
	}
	if scenario == "closed-owner" {
		if err := owner.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if scenario == "lost-pin" {
		owner.mu.Lock()
		err := owner.refs[owner.shell].close()
		owner.mu.Unlock()
		if err != nil {
			t.Fatal(err)
		}
	}
	released := time.Now()
	if written, err := n.terminal.Write([]byte("release\n")); err != nil || written != 8 {
		t.Fatal("release actual Bash read", written, err)
	}
	n.inputThrough += 8
	if scenario == "closed-owner" || scenario == "lost-pin" || strings.HasPrefix(scenario, "held-") || scenario == "overlay" {
		if strings.HasPrefix(scenario, "held-") {
			returnOneEOF(t, ctx, n, owner, scenario)
		}
		var failure error
		select {
		case failure = <-done:
		case <-time.After(7 * time.Second):
			t.Fatal("failed transaction did not join")
		}
		if failure == nil || n.c.w.channels.Err() == nil {
			t.Fatal("lost cleanup proof published success", failure)
		}
		if strings.HasPrefix(scenario, "held-") || os.Getenv("OMEGAFLOW_RETURN_FAULT") == "blocked-drain" {
			var startErr *StartError
			if !errors.Is(failure, context.DeadlineExceeded) || !errors.As(failure, &startErr) || startErr.Deadline.Before(released) || startErr.Deadline.After(released.Add(6*time.Second)) {
				t.Fatal("missing EOF did not retain original cleanup epoch", failure)
			}
		}
		_ = n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
		for {
			line, err := readLine(n.reader)
			if errors.Is(err, io.EOF) {
				break
			}
			if err != nil {
				t.Fatal("fatal return telemetry did not close", err)
			}
			m, err := protocol.DecodeEnvoy(line)
			if err != nil {
				t.Fatal(err)
			}
			switch m.(type) {
			case *protocol.OperationCompleted, *protocol.OperationCancelled, *protocol.OperationFinalized:
				t.Fatal("terminal result after failed cleanup", m)
			}
		}
		post, stop := context.WithTimeout(context.Background(), time.Second)
		defer stop()
		if p, err := outsidePin.observe(post); err != nil || !p.Live {
			t.Fatal("failed cleanup affected outside sibling", p, err)
		}
		_ = n.terminal.SetReadDeadline(time.Now())
		<-rawDone
		joined = true
		t.Logf("ACTUAL RETURN %s PROVED fail-closed=true error=%v elapsed=%s", scenario, failure, time.Since(released))
		return
	}
	offset, stream := base, "pty"
	logical := map[string][]byte{}
	var completed *protocol.OperationCompleted
	for completed == nil {
		message := nativeFrame(t, n.reader)
		encoded, err := protocol.EncodeEnvoy(message)
		var header protocol.Header
		if err != nil || json.Unmarshal(encoded, &header) != nil || header.Seq != n.lastSeq+1 {
			t.Fatal("actual return sequence", message, err)
		}
		n.lastSeq = header.Seq
		switch m := message.(type) {
		case *protocol.OutputMark:
			if m.Offset < offset || m.ElapsedUS < n.markElapsed {
				t.Fatal("actual return mark regressed", m)
			}
			for int64(len(sink.bytes())) < m.Offset-base {
				select {
				case <-sink.wake:
				case <-ctx.Done():
					t.Fatal("actual output did not reach covering mark", ctx.Err())
				}
			}
			raw := sink.bytes()
			logical[stream] = append(logical[stream], raw[offset-base:m.Offset-base]...)
			offset, stream, n.markElapsed = m.Offset, m.Stream, m.ElapsedUS
		case *protocol.OperationStarted:
			if m.OperationID != req.OperationID || m.OutputStart != started.OutputStart {
				t.Fatal(m)
			}
		case *protocol.OperationCompleted:
			if m.OperationID != req.OperationID || m.Status != 7 || m.Cwd != "/tmp" || m.OutputStart != started.OutputStart || m.OutputThrough != offset {
				t.Fatal("actual result before final covered drain", m, offset)
			}
			completed = m
		default:
			t.Fatal("unexpected actual return event", message)
		}
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal("actual ordinary transaction", err)
		}
	case <-ctx.Done():
		t.Fatal("actual ordinary worker did not join", ctx.Err())
	}
	_ = n.terminal.SetReadDeadline(time.Now())
	if err := <-rawDone; !os.IsTimeout(err) {
		t.Fatal("actual terminal collector", err)
	}
	joined = true
	if int64(len(sink.bytes())) != completed.OutputThrough-base || bytes.Contains(sink.bytes(), []byte("builtin eval --")) {
		t.Fatal("actual final range differs or contains adapter frame bytes")
	}
	if !processGone(authored.identity()) {
		t.Fatal("authored descendant remains live or waitable after actual Bash return")
	}
	if err := owner.ProveIdle(ctx); err != nil || n.c.active != nil {
		t.Fatal("actual final census did not close", err)
	}
	if p, err := outsidePin.observe(ctx); err != nil || !p.Live {
		t.Fatal("actual cleanup affected outside sibling", p, err)
	}
	if req.ExecutionShape == "split" {
		if n.c.splitResources().operation != nil || n.c.r.ownsSplit(req.OperationID) {
			t.Fatal("actual return retained split resources")
		}
		if _, err := os.Lstat(filepath.Join(sessionRuntimeDir, "split")); !os.IsNotExist(err) {
			t.Fatal("actual return retained FIFO paths", err)
		}
		if !bytes.Equal(logical["stdout"], []byte("out-é\x00\xff\r\n")) || !bytes.Equal(logical["stderr"], []byte("err-λ\x00\xfe\n")) {
			t.Fatalf("actual logical split bytes changed: stdout=%x stderr=%x", logical["stdout"], logical["stderr"])
		}
	}
	n.through, n.markOffset = completed.OutputThrough, offset
	pump, reader := n.c.private, n.c.w.Reader
	reuse := "builtin printf '%s:%s:%s\\n' \"$proof_state\" \"$PWD\" \"$?\"; jobs -p"
	start := n.begin(t, ctx, "reuse", reuse)
	if err := n.c.completeOrdinary(ctx, owner); err != nil {
		t.Fatal("actual shell reuse cleanup", err)
	}
	for {
		m := n.frame(t)
		if _, ok := m.(*protocol.OutputMark); ok {
			continue
		}
		result, ok := m.(*protocol.OperationCompleted)
		if !ok || result.OperationID != "reuse" || result.Status != 0 || result.Cwd != "/tmp" || result.OutputStart != start || result.OutputThrough != n.markOffset {
			t.Fatal("actual shell reuse result", m)
		}
		if got := ordinaryVisible(n.bytesThrough(t, result.OutputThrough)); got != "retained:/tmp:7\n" {
			t.Fatalf("persistent state/status or empty jobs proof changed: %q", got)
		}
		break
	}
	if n.c.private != pump || n.c.w.Reader != reader {
		t.Fatal("reuse replaced private reader")
	}
	t.Logf("ACTUAL RETURN %s PROVED status=7 cwd=/tmp descendant-gone=true final-idle=true raw=%x", scenario, sink.bytes())
}

// The outside controller owns this one writer. It is not a controlled child,
// so descendant cleanup must preserve it and fail when its EOF cannot arrive.
func returnOutsideWriter(ctx context.Context, bridge string, ended <-chan struct{}) error {
	for {
		data, err := os.ReadFile(filepath.Join(bridge, "path"))
		if err == nil {
			fd, err := syscall.Open(string(data), syscall.O_WRONLY|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
			if err != nil {
				return err
			}
			defer syscall.Close(fd)
			if err := os.WriteFile(filepath.Join(bridge, "held"), []byte("held"), 0600); err != nil {
				return err
			}
			select {
			case <-ended:
				return nil
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		if !os.IsNotExist(err) {
			return err
		}
		select {
		case <-ended:
			return fmt.Errorf("child stopped before writer handoff")
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Millisecond):
		}
	}
}

func returnOneEOF(t *testing.T, ctx context.Context, n *nativeOrdinary, owner *processCensus, scenario string) {
	t.Helper()
	held := 0
	if scenario == "held-stderr" {
		held = 1
	}
	for {
		n.c.r.mu.Lock()
		s := n.c.r.split
		ready := s != nil && s.ended[1-held] && !s.ended[held]
		n.c.r.mu.Unlock()
		if ready {
			break
		}
		select {
		case <-time.After(time.Millisecond):
		case <-ctx.Done():
			t.Fatal("independent unheld EOF not observed", ctx.Err())
		}
	}
	// With one EOF still missing, the actual completion helper must remain
	// blocked as a live Bash child. No private acknowledgement was sent.
	var helper *processRef
	awsh, err := os.Stat(fmt.Sprintf("/proc/%d/exe", owner.awsh))
	if err != nil {
		t.Fatal(err)
	}
	for helper == nil {
		all, err := readProcesses(ctx)
		if err != nil {
			t.Fatal(err)
		}
		for pid, p := range all {
			if p.Parent != owner.shell {
				continue
			}
			exe, err := os.Stat(fmt.Sprintf("/proc/%d/exe", pid))
			if err == nil && os.SameFile(awsh, exe) {
				helper = cleanupFixturePin(t, pid)
				break
			}
		}
		if helper == nil {
			select {
			case <-time.After(time.Millisecond):
			case <-ctx.Done():
				t.Fatal("missing EOF released the actual completion helper", ctx.Err())
			}
		}
	}
	select {
	case <-time.After(30 * time.Millisecond):
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if p, err := helper.observe(ctx); err != nil || !p.Live {
		t.Fatal("one EOF released completion helper", p, err)
	}
}

// These hooks are linked only by the runner's disposable Go overlays. The
// production checkout has no callback, fault selector or mutable test seam.
var returnFaultTest *testing.T
var returnFaultArmed atomic.Bool

func returnFaultHelper(pid int64) int64 {
	if os.Getenv("OMEGAFLOW_RETURN_FAULT") == "helper-identity" {
		return -1
	}
	return pid
}

func returnFaultStep(ctx context.Context, c *Coordinator, point string) error {
	switch os.Getenv("OMEGAFLOW_RETURN_FAULT") {
	case "removal":
		if point != "before-split" {
			return nil
		}
		path := filepath.Join(sessionRuntimeDir, "split", c.active.request.OperationID+".stdout")
		if err := os.Rename(path, path+".moved"); err != nil {
			return err
		}
		returnFaultTest.Cleanup(func() {
			if err := os.Rename(path+".moved", path); err != nil {
				returnFaultTest.Error(err)
			}
		})
		if err := syscall.Mkfifo(path, 0600); err != nil {
			return err
		}
		returnFaultTest.Cleanup(func() {
			if err := os.Remove(path); err != nil {
				returnFaultTest.Error(err)
			}
		})
	case "late-census":
		if point != "before-idle" {
			return nil
		}
		cmd := exec.Command("/bin/sleep", "60")
		if err := cmd.Start(); err != nil {
			return err
		}
		returnFaultTest.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	case "blocked-drain":
		if point == "before-idle" {
			returnFaultArmed.Store(true)
		}
	}
	return ctx.Err()
}

func returnFaultDrain(ctx context.Context) error {
	if !returnFaultArmed.Load() {
		return nil
	}
	<-ctx.Done() // Model a drain syscall which cannot complete in this epoch.
	return ctx.Err()
}
