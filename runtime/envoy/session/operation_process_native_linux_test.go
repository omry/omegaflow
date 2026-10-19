//go:build linux

package session

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// These observations are called only by the runner's retained test overlays.
// Neither hook replaces a signal, census, cleanup, owner decision or deadline.
var processLifecycleSignal = make(chan error, 2)
var processLifecycleCleanupEntered chan struct{}
var processLifecycleCleanupRelease chan struct{}
var processLifecycleBeforeSignalFn func(*os.File)
var processLifecycleSampleFn func(int64)
var processLifecycleMembersFn func(int64, int)
var processLifecycleSignalFn func(error)
var processLifecycleSampleMaster *os.File

// These hooks are linked only by the runner's disposable overlays. They observe
// or coordinate the real census and PTY ioctl; they do not provide membership,
// signal success, cleanup, deadlines or lifecycle outcomes.
func nativeLifecycleBeforeSignal(master *os.File) {
	if processLifecycleBeforeSignalFn != nil {
		processLifecycleBeforeSignalFn(master)
	}
}

func nativeLifecycleSample(pgid int64) {
	if processLifecycleSampleFn != nil {
		processLifecycleSampleFn(pgid)
	}
}

func nativeLifecycleMembers(pgid int64, count int) {
	if processLifecycleMembersFn != nil {
		processLifecycleMembersFn(pgid, count)
	}
}

func nativeLifecycleSignal(err error) {
	if processLifecycleSignalFn != nil {
		processLifecycleSignalFn(err)
	}
	select {
	case processLifecycleSignal <- err:
	default:
	}
}
func nativeLifecycleCleanup(ctx context.Context) error {
	if processLifecycleCleanupEntered == nil {
		return nil
	}
	close(processLifecycleCleanupEntered)
	select {
	case <-processLifecycleCleanupRelease:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func TestNativeProcessLifecycle(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PROCESS_LIFECYCLE") != "1" {
		t.Skip("requires fresh genuine qualification and retained observation overlays")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	cases := []string{"cancel-foreground", "finalize-foreground", "cancel-split", "finalize-split", "cancel-helper", "finalize-helper", "cancel-after-return", "finalize-after-return", "cancel-timeout", "finalize-timeout", "natural-exit", "fast-exit", "cancel-foreground-switch", "cancel-stale-return", "cancel-stale-live", "cancel-stale-timeout"}
	if only := os.Getenv("OMEGAFLOW_PROCESS_LIFECYCLE_CASE"); only != "" {
		cases = []string{only}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			outside := processFixtureCommand("leaf")
			if err := outside.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() { outside.Process.Kill(); outside.Wait() }()
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeProcessLifecycleFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_PROCESS_LIFECYCLE_CHILD="+scenario,
				"OMEGAFLOW_PROCESS_OUTSIDE="+strconv.Itoa(outside.Process.Pid))
			output, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(output), "PROCESS LIFECYCLE "+scenario+" PROVED") {
				t.Fatalf("actual lifecycle %s: %v\n%s", scenario, err, output)
			}
			if err := outside.Process.Signal(syscall.Signal(0)); err != nil {
				t.Fatal("outside-tree service was affected", err)
			}
			t.Logf("%s", output)
		})
	}
	after, err := processSubreaperState()
	if err != nil || after != before {
		t.Fatal("shared harness subreaper state changed", before, after, err)
	}
}

func lifecycleSource(scenario, pidfile string) string {
	// The escaped authored process retains its inherited writers. It is not a
	// helper, a forged census entry or an outside-tree service.
	daemon := "/usr/bin/python3 -c 'import os,sys,time\nif os.fork(): os._exit(0)\nos.setsid()\nif os.fork(): os._exit(0)\nopen(sys.argv[1],\"w\").write(str(os.getpid()))\ntime.sleep(60)' " + strconv.Quote(pidfile) + "\n"
	if strings.Contains(scenario, "foreground-switch") || strings.Contains(scenario, "stale-") {
		actor := pidfile + ".actor"
		hit := pidfile + ".switch-hit"
		restored := pidfile + ".restored"
		switchRequest := pidfile + ".switch-request"
		switchAck := pidfile + ".switch-ack"
		staleRequest := pidfile + ".stale-request"
		emptyAck := pidfile + ".empty-ack"
		returnRequest := pidfile + ".return-request"
		returnAck := pidfile + ".return-ack"
		actorSource := "/usr/bin/python3 -c 'import os,signal,sys,time\ntty=os.open(\"/dev/tty\",os.O_RDWR)\nshell=os.getppid()\nsignal.signal(signal.SIGTTOU,signal.SIG_IGN)\nopen(sys.argv[1],\"w\").write(str(os.getpid()))\ndef write(path,text):\n open(path,\"w\").write(text)\ndef restore():\n try:\n  os.tcsetpgrp(tty,os.getpgid(shell))\n  write(sys.argv[3],\"restored\")\n except OSError as e:\n  write(sys.argv[3],\"restore:\"+str(e.errno))\ndef interrupted(sig,frame):\n write(sys.argv[2],\"INT\\n\")\n restore()\n try: os.kill(shell,signal.SIGCONT)\n except ProcessLookupError: pass\n os._exit(0)\nsignal.signal(signal.SIGINT,interrupted)\nwhile True:\n if os.path.exists(sys.argv[4]):\n  try:\n   os.tcsetpgrp(tty,os.getpgrp()); write(sys.argv[5],str(os.getpgrp()))\n  except OSError as e: write(sys.argv[5],\"error:\"+str(e.errno))\n  os.unlink(sys.argv[4])\n if os.path.exists(sys.argv[6]):\n  pid=int(open(sys.argv[10]).read())\n  while True:\n   try:\n    state=open(\"/proc/\"+str(pid)+\"/stat\").read().split(\") \",1)[1][0]\n   except FileNotFoundError:\n    break\n   if state == \"Z\": break\n   time.sleep(0.001)\n  write(sys.argv[7],\"empty\")\n  os.unlink(sys.argv[6])\n if os.path.exists(sys.argv[8]):\n  try:\n   os.kill(shell,signal.SIGCONT); write(sys.argv[9],\"continued\")\n  except ProcessLookupError: pass\n  os.unlink(sys.argv[8])\n time.sleep(0.001)' " + strconv.Quote(actor) + " " + strconv.Quote(hit) + " " + strconv.Quote(restored) + " " + strconv.Quote(switchRequest) + " " + strconv.Quote(switchAck) + " " + strconv.Quote(staleRequest) + " " + strconv.Quote(emptyAck) + " " + strconv.Quote(returnRequest) + " " + strconv.Quote(returnAck) + " " + strconv.Quote(pidfile+".foreground") + " &\n"
		if strings.Contains(scenario, "foreground-switch") {
			foreground := "/usr/bin/python3 -c 'import os,sys,time\nopen(sys.argv[1],\"w\").write(str(os.getpid()))\nprint(\"LIFECYCLE_READY\",flush=True)\nwhile not os.path.exists(sys.argv[2]): time.sleep(0.001)' " + strconv.Quote(pidfile+".foreground") + " " + strconv.Quote(hit) + "\n"
			return daemon + actorSource + foreground
		}
		foreground := "/usr/bin/python3 -c 'import os,signal,sys,time\nopen(sys.argv[1],\"w\").write(str(os.getpid()))\nprint(\"LIFECYCLE_READY\",flush=True)\nwhile not os.path.exists(sys.argv[2]): time.sleep(0.001)\nos.kill(os.getppid(),signal.SIGSTOP)\nos._exit(0)' " + strconv.Quote(pidfile+".foreground") + " " + strconv.Quote(staleRequest) + "\n"
		if strings.Contains(scenario, "stale-return") {
			return daemon + actorSource + foreground + "builtin read -r release; builtin printf 'LIFECYCLE_RETURNED\\n'\n"
		}
		return daemon + actorSource + foreground + "builtin read -r release\n"
	}
	if strings.Contains(scenario, "helper") {
		return daemon + "builtin printf 'LIFECYCLE_READY\\n'; (exit 7)"
	}
	if strings.Contains(scenario, "after-return") {
		return daemon + "builtin printf 'LIFECYCLE_READY\\n'; builtin read -r release; (exit 7)"
	}
	if scenario == "natural-exit" {
		return daemon + "builtin printf 'LIFECYCLE_READY\\n'; builtin read -r release; exit 7"
	}
	if scenario == "fast-exit" {
		return daemon + "builtin printf 'LIFECYCLE_READY\\n'; exit 7"
	}
	handler := "def interrupted(sig, frame):\n print(\"INT_ONCE\",flush=True)\n sys.exit(23)\n"
	if strings.Contains(scenario, "timeout") {
		handler = "def interrupted(sig, frame):\n print(\"INT_IGNORED\",flush=True)\n"
	}
	return daemon + "/usr/bin/python3 -c 'import signal,sys,time\n" + handler +
		"signal.signal(signal.SIGINT,interrupted)\nprint(\"LIFECYCLE_READY\",flush=True)\nwhile True: time.sleep(1)'"
}

func lifecycleGroupFromFile(path string) (int32, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return 0, err
	}
	info, err := readProcess(pid)
	if err != nil {
		return 0, err
	}
	if info.Group <= 0 {
		return 0, fmt.Errorf("invalid native process group %d", info.Group)
	}
	return int32(info.Group), nil
}

func lifecycleForeground(t *testing.T, n *nativeOrdinary) int32 {
	t.Helper()
	var group int32
	if err := ptyIOCTL(n.c.w.PTY, syscall.TIOCGPGRP, unsafe.Pointer(&group)); err != nil {
		t.Fatal(err)
	}
	return group
}

func lifecycleIgnoredMask(t *testing.T, pid int64) string {
	t.Helper()
	data, err := os.ReadFile("/proc/" + strconv.FormatInt(pid, 10) + "/status")
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "SigIgn:") {
			return line
		}
	}
	t.Fatal("missing native ignored-signal mask")
	return ""
}

func TestNativeProcessLifecycleFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_PROCESS_LIFECYCLE_CHILD")
	if scenario == "" {
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, cancel := context.WithTimeout(context.Background(), 24*time.Second)
	defer cancel()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cleanup, end := context.WithTimeout(context.Background(), 3*time.Second)
		defer end()
		_ = owner.terminateShell(cleanup)
		_ = owner.Cleanup(cleanup, 0)
		_ = owner.Close()
	})
	outside, err := strconv.Atoi(os.Getenv("OMEGAFLOW_PROCESS_OUTSIDE"))
	if err != nil {
		t.Fatal(err)
	}
	outsidePin, _, err := pinProcess(ctx, outside)
	if err != nil {
		t.Fatal(err)
	}
	defer outsidePin.close()
	pidfile := filepath.Join(t.TempDir(), "escaped-pid")
	sink := &nativeSplitSink{wake: make(chan struct{}, 1)}
	collector := make(chan error, 1)
	_ = n.terminal.SetReadDeadline(time.Now().Add(25 * time.Second))
	go func() { _, err := io.Copy(sink, n.terminal); collector <- err }()
	joined := false
	t.Cleanup(func() {
		if joined {
			return
		}
		_ = n.terminal.SetReadDeadline(time.Now())
		select {
		case <-collector:
		case <-time.After(time.Second):
			t.Error("lifecycle raw collector did not join")
		}
	})
	e := &evidenceTranscript{n: n, sink: sink, base: n.through, offset: n.through, stream: "pty", ctx: ctx}
	req := startRequest()
	req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	req.Source = lifecycleSource(scenario, pidfile)
	req.InputThrough = n.inputThrough
	if strings.Contains(scenario, "split") {
		req.ExecutionShape, req.Timing, req.Observation = "split", "presentation", "exclusive"
		n.c.setup = NewSplitSetup()
	}
	n.request(t, &req)
	input := receiveInput(t, n.c.w.channels)
	actual, ok := input.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("execute not decoded", input)
	}
	started, err := n.c.Start(ctx, *actual)
	if err != nil || !started.Started {
		t.Fatal("actual lifecycle start", started, err)
	}
	if m, ok := e.event(t).(*protocol.OperationStarted); !ok || m.OutputStart != started.OutputStart {
		t.Fatal("actual lifecycle public start", m)
	}
	pid := returnPID(t, ctx, pidfile)
	authored := cleanupFixturePin(t, pid)
	for !strings.Contains(string(sink.bytes()), "LIFECYCLE_READY") {
		select {
		case <-sink.wake:
		case <-ctx.Done():
			t.Fatal("authored readiness absent", ctx.Err())
		}
	}
	foregroundSwitch := strings.Contains(scenario, "foreground-switch")
	staleReturn := strings.Contains(scenario, "stale-return")
	staleLive := strings.Contains(scenario, "stale-live")
	staleTimeout := strings.Contains(scenario, "stale-timeout")
	var staleSampleGroup, staleLiveGroup, switchedFrom int32
	staleEmptyObserved := false
	var hookErrors chan error
	if foregroundSwitch || staleReturn || staleLive || staleTimeout {
		hookErrors = make(chan error, 8)
		recordHookError := func(err error) {
			if err != nil {
				select {
				case hookErrors <- err:
				default:
				}
			}
		}
		writeMarker := func(path string) {
			if err := os.WriteFile(path, []byte("go"), 0600); err != nil {
				recordHookError(fmt.Errorf("write lifecycle marker %s: %w", path, err))
			}
		}
		waitMarker := func(path string) bool {
			deadline := time.Now().Add(5 * time.Second)
			for time.Now().Before(deadline) {
				if _, err := os.Stat(path); err == nil {
					return true
				}
				time.Sleep(time.Millisecond)
			}
			recordHookError(fmt.Errorf("lifecycle marker did not arrive: %s", path))
			return false
		}
		waitGroup := func(path string) int32 {
			deadline := time.Now().Add(5 * time.Second)
			for time.Now().Before(deadline) {
				if group, err := lifecycleGroupFromFile(path); err == nil {
					return group
				}
				time.Sleep(time.Millisecond)
			}
			recordHookError(fmt.Errorf("native actor group did not become available: %s", path))
			return 0
		}
		sampleCount := 0
		processLifecycleSampleMaster = n.c.w.PTY
		processLifecycleSampleFn = func(pgid int64) {
			sampleCount++
			if foregroundSwitch || (!staleReturn && !staleLive && !staleTimeout) {
				return
			}
			if sampleCount == 1 {
				staleSampleGroup = int32(pgid)
				foregroundGroup, err := lifecycleGroupFromFile(pidfile + ".foreground")
				if err != nil || foregroundGroup != staleSampleGroup {
					recordHookError(fmt.Errorf("stale sample group mismatch: sample=%d authored=%d err=%v", pgid, foregroundGroup, err))
				}
				writeMarker(pidfile + ".stale-request")
				waitMarker(pidfile + ".empty-ack")
				return
			}
			if staleLive && sampleCount == 2 {
				if !staleEmptyObserved {
					recordHookError(fmt.Errorf("native stale empty membership observation missing"))
					return
				}
				staleLiveGroup = waitGroup(pidfile + ".actor")
				if staleLiveGroup == 0 || int32(pgid) != staleLiveGroup {
					recordHookError(fmt.Errorf("later live group was not kernel foreground: sample=%d actor=%d", pgid, staleLiveGroup))
				}
			}
		}
		processLifecycleMembersFn = func(pgid int64, count int) {
			if !staleReturn && !staleLive && !staleTimeout {
				return
			}
			if count == 0 && !staleEmptyObserved {
				staleEmptyObserved = true
				if int32(pgid) != staleSampleGroup {
					recordHookError(fmt.Errorf("empty census returned for wrong stale group: got=%d want=%d", pgid, staleSampleGroup))
				}
				var current int32
				if err := ptyIOCTL(processLifecycleSampleMaster, syscall.TIOCGPGRP, unsafe.Pointer(&current)); err != nil {
					recordHookError(fmt.Errorf("stale foreground observation: %w", err))
				} else if current != staleSampleGroup {
					recordHookError(fmt.Errorf("Bash reclaimed stale group before empty observation: current=%d stale=%d", current, staleSampleGroup))
				}
				if staleLive {
					writeMarker(pidfile + ".switch-request")
					waitMarker(pidfile + ".switch-ack")
				} else if staleReturn {
					writeMarker(pidfile + ".return-request")
					waitMarker(pidfile + ".return-ack")
					if _, err := io.WriteString(n.terminal, "release\n"); err != nil {
						recordHookError(fmt.Errorf("stale return release input: %w", err))
					} else {
						n.inputThrough += int64(len("release\n"))
					}
					deadline := time.Now().Add(5 * time.Second)
					for !strings.Contains(string(sink.bytes()), "LIFECYCLE_RETURNED") && time.Now().Before(deadline) {
						time.Sleep(time.Millisecond)
					}
					if !strings.Contains(string(sink.bytes()), "LIFECYCLE_RETURNED") {
						recordHookError(fmt.Errorf("stale return did not reach the authored Bash read boundary"))
					} else {
						// Let the existing owner consume the real return fact before
						// this proof worker releases its observation pause.
						time.Sleep(250 * time.Millisecond)
					}
				}
				return
			}
			if staleLive && staleLiveGroup > 0 && int32(pgid) == staleLiveGroup && count == 0 {
				recordHookError(fmt.Errorf("later live group returned empty membership: group=%d", pgid))
			}
		}
		processLifecycleBeforeSignalFn = func(master *os.File) {
			if !foregroundSwitch {
				return
			}
			if err := ptyIOCTL(master, syscall.TIOCGPGRP, unsafe.Pointer(&switchedFrom)); err != nil {
				recordHookError(fmt.Errorf("sample-to-ioctl foreground read: %w", err))
				return
			}
			target := waitGroup(pidfile + ".actor")
			if target <= 0 || target == switchedFrom || target == int32(n.c.w.ShellPID) {
				recordHookError(fmt.Errorf("invalid authored sample-to-ioctl target: old=%d new=%d shell=%d", switchedFrom, target, n.c.w.ShellPID))
				return
			}
			writeMarker(pidfile + ".switch-request")
			if !waitMarker(pidfile + ".switch-ack") {
				return
			}
			if ackGroup := waitGroup(pidfile + ".actor"); ackGroup != target {
				recordHookError(fmt.Errorf("authored switch actor group changed: before=%d after=%d", target, ackGroup))
			}
		}
		processLifecycleSignalFn = func(err error) {
			if foregroundSwitch || staleLive {
				if !waitMarker(pidfile + ".restored") {
					return
				}
				var current int32
				if restoreErr := ptyIOCTL(processLifecycleSampleMaster, syscall.TIOCGPGRP, unsafe.Pointer(&current)); restoreErr != nil {
					recordHookError(fmt.Errorf("read authored restored foreground: %w", restoreErr))
				} else if current != int32(n.c.w.ShellPID) {
					recordHookError(fmt.Errorf("authored actor did not restore Bash foreground: got=%d want=%d", current, n.c.w.ShellPID))
				}
			}
		}
		t.Cleanup(func() {
			processLifecycleBeforeSignalFn = nil
			processLifecycleSampleFn = nil
			processLifecycleMembersFn = nil
			processLifecycleSignalFn = nil
			processLifecycleSampleMaster = nil
		})
	}
	helperWindow := strings.Contains(scenario, "helper")
	afterReturn := strings.Contains(scenario, "after-return")
	naturalExit := scenario == "natural-exit" || scenario == "fast-exit"
	if !helperWindow && !afterReturn && !naturalExit {
		if group := lifecycleForeground(t, n); group <= 0 || int64(group) == n.c.w.ShellPID {
			t.Fatal("actual foreground job not selected", group)
		}
	}
	var held privateResult
	var helperMask, shellMask string
	if afterReturn || scenario == "natural-exit" {
		if _, err := io.WriteString(n.terminal, "release\n"); err != nil {
			t.Fatal(err)
		}
		n.inputThrough += int64(len("release\n"))
	}
	if helperWindow {
		select {
		case held = <-n.c.private.events:
		case <-ctx.Done():
			t.Fatal("real helper did not connect")
		}
		proposal, ok := held.message.(*protocol.PrivateInputClose)
		if held.err != nil || !ok || proposal.OperationID != req.OperationID {
			t.Fatal("wrong held input_close", held)
		}
		if err := nativeIgnoredINT(proposal.CompletionHelperPID); err != nil {
			t.Fatal(err)
		}
		helperMask = lifecycleIgnoredMask(t, proposal.CompletionHelperPID)
		shellMask = lifecycleIgnoredMask(t, n.c.w.ShellPID)
	}
	if afterReturn {
		processLifecycleCleanupEntered = make(chan struct{})
		processLifecycleCleanupRelease = make(chan struct{})
	}
	done := make(chan error, 1)
	go func() { done <- n.c.completeOperation(ctx, owner) }()
	if afterReturn {
		select {
		case <-processLifecycleCleanupEntered:
		case <-ctx.Done():
			t.Fatal("actual input_close was not accepted")
		}
	}
	kind := strings.Split(scenario, "-")[0]
	controlSent := time.Time{}
	if !naturalExit {
		controlSent = time.Now()
		if kind == "cancel" {
			n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: req.OperationID, Reason: "native-control"})
		} else {
			n.request(t, &protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq}, OperationID: req.OperationID, Reason: "native-control"})
		}
	}
	if !naturalExit && !afterReturn && !staleReturn && !staleTimeout {
		select {
		case err := <-processLifecycleSignal:
			if err != nil {
				t.Fatal("actual TIOCSIG failed", err)
			}
		case err := <-hookErrors:
			t.Fatal("native lifecycle coordination failed", err)
		case <-ctx.Done():
			t.Fatal("actual TIOCSIG observation missing")
		}
	}
	if staleLive {
		if _, err := io.WriteString(n.terminal, "release\n"); err != nil {
			t.Fatal("stale-live release input", err)
		}
		n.inputThrough += int64(len("release\n"))
	}
	if helperWindow {
		proposal := held.message.(*protocol.PrivateInputClose)
		if err := nativeIgnoredINT(proposal.CompletionHelperPID); err != nil {
			t.Fatal("real helper did not survive actual SIGINT", err)
		}
		if err := nativeIgnoredINT(n.c.w.ShellPID); err != nil {
			t.Fatal("Bash lost ignored INT in helper window", err)
		}
		if lifecycleIgnoredMask(t, proposal.CompletionHelperPID) != helperMask || lifecycleIgnoredMask(t, n.c.w.ShellPID) != shellMask {
			t.Fatal("helper or Bash ignored-signal mask changed while blocked")
		}
		select {
		case n.c.private.events <- held:
		case <-ctx.Done():
			t.Fatal("held return could not be reinserted")
		}
	}
	if afterReturn {
		n.request(t, &protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: n.seq}, Columns: 91, Rows: 31})
		if _, ok := e.event(t).(*protocol.ResizeApplied); !ok {
			t.Fatal("owner did not accept control during cleanup")
		}
		close(processLifecycleCleanupRelease)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal("actual lifecycle transaction failed", err)
		}
	case <-ctx.Done():
		t.Fatal("actual lifecycle transaction did not join", ctx.Err())
	}
	if hookErrors != nil {
		select {
		case err := <-hookErrors:
			t.Fatal("native lifecycle coordination failed", err)
		default:
		}
	}
	if foregroundSwitch || staleReturn || staleLive || staleTimeout {
		markerNames := []string{".actor", ".foreground", ".switch-request", ".switch-ack", ".stale-request", ".empty-ack", ".return-request", ".return-ack", ".switch-hit", ".restored"}
		markers := make([]string, 0, len(markerNames))
		for _, suffix := range markerNames {
			data, err := os.ReadFile(pidfile + suffix)
			if err == nil {
				markers = append(markers, suffix+"="+strings.TrimSpace(string(data)))
			}
		}
		signalCount := 0
		if data, err := os.ReadFile(pidfile + ".switch-hit"); err == nil {
			for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
				if line != "" {
					signalCount++
				}
			}
		}
		t.Logf("lifecycle coordination: sample_group=%d empty_census=%t live_group=%d switched_from=%d signal_count=%d markers=%s", staleSampleGroup, staleEmptyObserved, staleLiveGroup, switchedFrom, signalCount, strings.Join(markers, ","))
	}
	if staleTimeout {
		elapsed := time.Since(controlSent)
		if elapsed < 4*time.Second || elapsed > 8*time.Second {
			t.Fatal("stale positive group did not retain the original five-second grace", elapsed)
		}
	}
	result := e.event(t)
	through := int64(-1)
	ended := naturalExit || strings.Contains(scenario, "timeout")
	switch m := result.(type) {
	case *protocol.OperationFailed:
		if !strings.Contains(scenario, "timeout") || m.Code != kind+"-timeout" || m.ShellEnded == nil || !*m.ShellEnded {
			t.Fatal("timeout lost selected outcome or shell reap", m)
		}
		through = m.OutputThrough
	case *protocol.OperationCompleted:
		if (!naturalExit && !(afterReturn && kind == "finalize")) || m.Status != 7 || (naturalExit && (m.ShellEnded == nil || !*m.ShellEnded)) {
			t.Fatal("natural return/exit winner changed", m)
		}
		through = m.OutputThrough
	case *protocol.OperationCancelled:
		want := int64(23)
		if helperWindow || afterReturn {
			want = 7
		}
		if (foregroundSwitch || staleReturn || staleLive) && (m.Status == nil || *m.Status < 0) {
			t.Fatal("native switched/stale return lost the actual shell status", m)
		}
		if !foregroundSwitch && !staleReturn && !staleLive && (kind != "cancel" || m.Reason != "native-control" || m.Status == nil || *m.Status != want) {
			t.Fatal("wrong actual cancellation", m)
		}
		through = m.OutputThrough
	case *protocol.OperationFinalized:
		if kind != "finalize" || afterReturn || m.Reason != "native-control" {
			t.Fatal("wrong actual finalization", m)
		}
		through = m.OutputThrough
	default:
		t.Fatal("missing typed lifecycle result", result)
	}
	if foregroundSwitch || staleLive {
		data, err := os.ReadFile(pidfile + ".switch-hit")
		lines := strings.Split(strings.TrimSpace(string(data)), "\n")
		if err != nil || len(lines) != 1 || lines[0] != "INT" {
			t.Fatal("kernel-selected switched group did not receive exactly one signal", string(data), err)
		}
	}
	if through < started.OutputStart || e.offset < through {
		t.Fatal("actual lifecycle result lacks final covering mark", result)
	}
	e.awaitBytes(t, through)
	if _, err := authored.observe(ctx); !processGone(err) {
		t.Fatal("authored daemon survived result", err)
	}
	if _, err := outsidePin.observe(ctx); err != nil {
		t.Fatal("outside process did not survive", err)
	}
	select {
	case err := <-processLifecycleSignal:
		t.Fatal("second or post-return signal", err)
	default:
	}
	if ended {
		if _, ok := e.event(t).(*protocol.Draining); !ok {
			t.Fatal("missing shell-ended drain")
		}
		if _, ok := e.event(t).(*protocol.Closed); !ok {
			t.Fatal("missing shell-ended close")
		}
		if n.c.exited == nil || !n.c.exitedEOF || n.c.w.waitErr != nil {
			t.Fatal("missing actual shell_exit, EOF or zero-status Awsh reap")
		}
		if err := owner.proveEnded(ctx); err != nil {
			t.Fatal(err)
		}
		select {
		case err := <-collector:
			joined = true
			if err != nil {
				t.Fatal("shell-ended raw stream did not reach EOF", err)
			}
		case <-ctx.Done():
			t.Fatal("missing actual PTY EOF", ctx.Err())
		}
	} else {
		if err := owner.ProveIdle(ctx); err != nil || int64(lifecycleForeground(t, n)) != n.c.w.ShellPID {
			t.Fatal("actual Bash did not reclaim a clean idle boundary", err)
		}
		processLifecycleCleanupEntered = nil
		for _, message := range []protocol.Message{
			&protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: req.OperationID, Reason: "crossed"},
			&protocol.Finalize{Header: protocol.Header{Schema: protocol.Schema, Type: "finalize", Seq: n.seq + 1}, OperationID: req.OperationID, Reason: "recording-end"},
		} {
			n.request(t, message)
		}
		// Both old controls must be discarded while the next execute starts.
		next := req
		next.Header.Seq = n.seq
		next.OperationID = "next-lifecycle"
		next.Source = "builtin printf 'LIFECYCLE_REUSABLE\\n'"
		next.ExecutionShape, next.Timing, next.Observation = "pty", "realtime", "shared"
		next.InputThrough = n.inputThrough
		n.request(t, &next)
		var command *protocol.Execute
		for command == nil {
			nextInput := receiveInput(t, n.c.w.channels)
			if execute, ok := nextInput.Message.(*protocol.Execute); ok {
				command = execute
				break
			}
			if err := n.c.publicRequest(ctx, false, nil, nextInput.Message); err != nil {
				t.Fatal("idle crossed request was not discarded by its actual owner", nextInput, err)
			}
		}
		nextStart, err := n.c.Start(ctx, *command)
		if err != nil || !nextStart.Started {
			t.Fatal("next actual source did not start", nextStart, err)
		}
		if m, ok := e.event(t).(*protocol.OperationStarted); !ok || m.OutputStart != nextStart.OutputStart {
			t.Fatal("next public start missing", m)
		}
		if err := n.c.completeOperation(ctx, owner); err != nil {
			t.Fatal("next actual operation after crossed controls", err)
		}
		if m, ok := e.event(t).(*protocol.OperationCompleted); !ok || m.OutputStart != nextStart.OutputStart || m.Status != 0 {
			t.Fatal("crossed controls replaced next operation", m)
		}
		if err := owner.ProveIdle(ctx); err != nil {
			t.Fatal(err)
		}
		if err := n.c.ShutdownIdle("recording-end"); err != nil {
			t.Fatal(err)
		}
	}
	t.Logf("PROCESS LIFECYCLE %s PROVED", scenario)
}
