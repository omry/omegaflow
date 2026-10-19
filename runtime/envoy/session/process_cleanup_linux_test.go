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
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// The existing census fixture supplies real kernel parentage, not Awsh/Bash
// behavior. Bash-specific escape cases run separately on qualified targets.
func TestProcessCleanupNative(t *testing.T) {
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		after, err := processSubreaperState()
		if err != nil || after != before {
			t.Error("shared harness subreaper state changed", before, after, err)
		}
	})
	for _, scenario := range []string{"direct-wait", "adopted", "helper", "invalid-helper", "epochs", "lost-pin", "closed", "reap-ownership", "awsh-wait"} {
		t.Run(scenario, func(t *testing.T) {
			outside := processFixtureCommand("leaf")
			if err := outside.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() { outside.Process.Kill(); outside.Wait() }()
			cmd := exec.Command(os.Args[0], "-test.run=^TestProcessCleanupFixture$", "-test.v")
			if dir := os.Getenv("OMEGAFLOW_PROCESS_COVER_DIR"); dir != "" {
				cmd.Args = append(cmd.Args, "-test.coverprofile="+filepath.Join(dir, "cleanup-native-"+scenario+".cover"))
			}
			cmd.Env = append(os.Environ(), "OMEGAFLOW_CLEANUP_CASE="+scenario,
				"OMEGAFLOW_PROCESS_OUTSIDE="+strconv.Itoa(outside.Process.Pid), "GORACE=atexit_sleep_ms=0")
			var output strings.Builder
			cmd.Stdout, cmd.Stderr = &output, &output
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			done := make(chan error, 1)
			go func() { done <- cmd.Wait() }()
			select {
			case err := <-done:
				if err != nil {
					t.Fatalf("native %s: %v\n%s", scenario, err, output.String())
				}
			case <-time.After(20 * time.Second):
				cmd.Process.Kill()
				<-done
				t.Fatalf("native %s exceeded fixture bound\n%s", scenario, output.String())
			}
			if err := outside.Process.Signal(syscall.Signal(0)); err != nil {
				t.Fatal("outside service was affected", err)
			}
		})
	}
}

// The qualified container supplies /bin/bash and the existing immutable
// adapter inputs. These cases prove Bash-authored process cleanup, not the
// later production Envoy/Awsh operation assembly.
func TestNativeProcessCleanup(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PROCESS_CLEANUP") != "1" {
		t.Skip("requires genuine qualified Bash container")
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{"background", "disown", "nohup", "setsid", "double-fork", "helper", "wrong-helper-executable", "ignores-term"} {
		t.Run(scenario, func(t *testing.T) {
			outside := processFixtureCommand("leaf")
			if err := outside.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() { outside.Process.Kill(); outside.Wait() }()
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeProcessCleanupFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_NATIVE_CLEANUP_ROLE=envoy", "OMEGAFLOW_NATIVE_CLEANUP_CASE="+scenario,
				"OMEGAFLOW_PROCESS_OUTSIDE="+strconv.Itoa(outside.Process.Pid), "GORACE=atexit_sleep_ms=0")
			output, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(output), "BASH CLEANUP "+scenario+" PROVED") {
				t.Fatalf("qualified Bash cleanup %s: %v\n%s", scenario, err, output)
			}
			if err := outside.Process.Signal(syscall.Signal(0)); err != nil {
				t.Fatal("outside-tree sibling did not survive", err)
			}
		})
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("shared qualified harness subreaper state changed", before, after, err)
	}
}

type bashCleanupFixture struct {
	cmd   *exec.Cmd
	input io.WriteCloser
	lines *bufio.Reader
	owner *processCensus
}

func nativeCleanupCommand(role string) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "-test.run=^TestNativeProcessCleanupFixture$")
	cmd.Env = append(os.Environ(), "OMEGAFLOW_NATIVE_CLEANUP_ROLE="+role, "GORACE=atexit_sleep_ms=0")
	return cmd
}

func bashCleanupFixtureFor(t *testing.T) *bashCleanupFixture {
	t.Helper()
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	f := &bashCleanupFixture{cmd: nativeCleanupCommand("awsh")}
	f.cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	f.cmd.Stderr = os.Stderr
	var err error
	f.input, err = f.cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := f.cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	f.lines = bufio.NewReader(output)
	if err := f.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if f.owner != nil {
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			if err := f.owner.Cleanup(ctx, 0); err != nil {
				t.Error("dispose qualified cleanup fixture", err)
			}
			cancel()
		}
		f.input.Close()
		if err := f.cmd.Wait(); err != nil {
			t.Error("fixture Awsh/selected Bash reap", err)
		}
		if f.owner != nil {
			if err := f.owner.Close(); err != nil {
				t.Error(err)
			}
		}
	})
	line, err := f.lines.ReadString('\n')
	parts := strings.Fields(line)
	if err != nil || len(parts) != 3 || parts[0] != "ROOTS" {
		t.Fatal("invalid qualified fixture roots", line, err)
	}
	awsh, err := strconv.Atoi(parts[1])
	if err != nil || awsh != f.cmd.Process.Pid {
		t.Fatal("wrong external fixture Awsh", line, err)
	}
	shell, err := strconv.Atoi(parts[2])
	if err != nil {
		t.Fatal(err)
	}
	f.owner, err = newProcessCensus(censusContext(t), awsh, shell)
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *bashCleanupFixture) source(t *testing.T, source, marker string) string {
	t.Helper()
	if _, err := fmt.Fprintln(f.input, source); err != nil {
		t.Fatal(err)
	}
	line, err := f.lines.ReadString('\n')
	if err != nil || !strings.HasPrefix(line, marker) {
		t.Fatal("unexpected qualified fixture response", line, marker, err)
	}
	return line
}

func TestNativeProcessCleanupFixture(t *testing.T) {
	switch role := os.Getenv("OMEGAFLOW_NATIVE_CLEANUP_ROLE"); role {
	case "":
		return
	case "leaf":
		time.Sleep(30 * time.Second)
		os.Exit(0)
	case "awsh":
		shell := exec.Command("/bin/bash", "--noprofile", "--norc")
		shell.Env = os.Environ()
		shell.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		shell.Stdin, shell.Stdout, shell.Stderr = os.Stdin, os.Stdout, os.Stderr
		if err := shell.Start(); err != nil {
			panic(err)
		}
		fmt.Println("ROOTS", os.Getpid(), shell.Process.Pid)
		if err := shell.Wait(); err != nil {
			os.Exit(1)
		}
		os.Exit(0)
	case "envoy":
		runNativeCleanupCase(t, os.Getenv("OMEGAFLOW_NATIVE_CLEANUP_CASE"))
	default:
		t.Fatal("unknown native cleanup role", role)
	}
}

func runNativeCleanupCase(t *testing.T, scenario string) {
	f := bashCleanupFixtureFor(t)
	c := f.owner
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	outside, err := strconv.Atoi(os.Getenv("OMEGAFLOW_PROCESS_OUTSIDE"))
	if err != nil {
		t.Fatal(err)
	}
	outsidePin, _, err := pinProcess(ctx, outside)
	if err != nil {
		t.Fatal(err)
	}
	defer outsidePin.close()
	var helperPID int64
	var helper *processRef
	if scenario == "helper" {
		quoted := "'" + strings.ReplaceAll(os.Args[0], "'", "'\\''") + "'"
		source := "OMEGAFLOW_NATIVE_CLEANUP_ROLE=leaf " + quoted + " -test.run='^TestNativeProcessCleanupFixture$' >/dev/null 2>&1 & printf 'HELPER %s\\n' \"$!\""
		pid := processFixturePID(t, f.source(t, source, "HELPER "), "HELPER")
		helperPID = int64(pid)
		helper = cleanupFixturePin(t, pid)
	}
	source := "sleep 60 >/dev/null 2>&1 & printf 'AUTHORED %s\\n' \"$!\""
	switch scenario {
	case "background", "helper", "wrong-helper-executable":
	case "disown":
		source = "sleep 60 >/dev/null 2>&1 & p=$!; disown \"$p\"; printf 'AUTHORED %s\\n' \"$p\""
	case "nohup":
		source = "nohup sleep 60 >/dev/null 2>&1 & printf 'AUTHORED %s\\n' \"$!\""
	case "setsid":
		source = "setsid sleep 60 >/dev/null 2>&1 & printf 'AUTHORED %s\\n' \"$!\""
	case "double-fork":
		source = "/usr/bin/python3 -c 'import os,time\nif os.fork(): os._exit(0)\nos.setsid()\nif os.fork(): os._exit(0)\nprint(\"AUTHORED\",os.getpid(),flush=True)\ntime.sleep(60)'"
	case "ignores-term":
		source = "/usr/bin/python3 -c 'import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print(\"AUTHORED\",os.getpid(),flush=True);time.sleep(60)' &"
	default:
		t.Fatal("unknown qualified case", scenario)
	}
	pid := processFixturePID(t, f.source(t, source, "AUTHORED "), "AUTHORED")
	authored := cleanupFixturePin(t, pid)
	if scenario == "setsid" || scenario == "double-fork" {
		for {
			p, err := authored.observe(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if p.Session != c.awsh && (scenario != "double-fork" || p.Parent == c.envoy) {
				break
			}
		}
	}
	if scenario == "wrong-helper-executable" {
		if err := c.Cleanup(ctx, int64(pid)); err == nil {
			t.Fatal("authored sleep accepted as the manifested helper")
		}
		if p, err := authored.observe(ctx); err != nil || !p.Live {
			t.Fatal("rejected helper exclusion changed the authored lifetime", p, err)
		}
	}
	if err := c.Cleanup(ctx, helperPID); err != nil {
		t.Fatal("qualified authored cleanup", err)
	}
	if p, err := authored.observe(ctx); !processGone(err) && (err != nil || p.Live) {
		t.Fatal("authored process still live or identity unproved", p, err)
	}
	if scenario == "double-fork" {
		if !processGone(authored.identity()) {
			t.Fatal("escaped adopted daemon was not reaped")
		}
	}
	if helper != nil {
		if p, err := helper.observe(ctx); err != nil || !p.Live {
			t.Fatal("exact helper failed to survive cleanup", p, err)
		}
		if err := c.Cleanup(ctx, 0); err != nil {
			t.Fatal("remove helper only after dropping exclusion", err)
		}
	}
	f.source(t, "wait; test -z \"$(jobs -p)\" || exit 9; printf 'CLEARED\\n'", "CLEARED")
	if err := c.ProveIdle(ctx); err != nil {
		t.Fatal("real Bash wait/job-table boundary remained dirty", err)
	}
	if p, err := outsidePin.observe(ctx); err != nil || !p.Live {
		t.Fatal("outside service did not survive", p, err)
	}
	fmt.Println("BASH CLEANUP", scenario, "PROVED")
}

// Keep an independent fixture-owned lifetime pin even in lost-pin tests.
// Failure disposal never rediscovers a potentially recycled PID to signal it.
func cleanupFixturePin(t *testing.T, pid int) *processRef {
	t.Helper()
	r, _, err := pinProcess(censusContext(t), pid)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		defer r.close()
		if processGone(r.identity()) {
			return
		}
		if err := fixtureSignal(r, syscall.SIGKILL); err != nil && err != syscall.ESRCH {
			t.Error("dispose fixture lifetime", err)
		}
		limit := time.Now().Add(2 * time.Second)
		for time.Now().Before(limit) {
			if processGone(r.identity()) {
				return
			}
			var status syscall.WaitStatus
			got, err := syscall.Wait4(pid, &status, syscall.WNOHANG, nil)
			if got == pid || err == syscall.ECHILD {
				return // Non-adopted children remain their direct parent's wait.
			}
			if err != nil && err != syscall.EINTR {
				t.Error("dispose adopted fixture", err)
				return
			}
			time.Sleep(time.Millisecond)
		}
		t.Error("fixture lifetime did not become waitable")
	})
	return r
}

func TestProcessCleanupFixture(t *testing.T) {
	scenario := os.Getenv("OMEGAFLOW_CLEANUP_CASE")
	if scenario == "" {
		return
	}
	f := censusFixtureFor(t) // Enables subreaper only in this dedicated child.
	c := f.owner
	ctx := censusContext(t)
	spawn := func(command, kind string) (int, *processRef) {
		pid := processFixturePID(t, f.send(t, command), kind)
		return pid, cleanupFixturePin(t, pid)
	}
	outside, err := strconv.Atoi(os.Getenv("OMEGAFLOW_PROCESS_OUTSIDE"))
	if err != nil {
		t.Fatal(err)
	}
	outsidePin, _, err := pinProcess(ctx, outside)
	if err != nil {
		t.Fatal(err)
	}
	defer outsidePin.close()
	switch scenario {
	case "fault":
		runCleanupFaultCase(t, f)
	case "direct-wait":
		pid, pin := spawn("spawn", "CHILD")
		if err := c.Cleanup(ctx, 0); err != nil {
			t.Fatal(err)
		}
		p, err := pin.observe(ctx)
		if err != nil || p.Live || p.Parent != f.shell {
			t.Fatal("cleanup did not retain the exited direct-child wait", p, err)
		}
		var status syscall.WaitStatus
		if _, err := syscall.Wait4(pid, &status, syscall.WNOHANG, nil); err != syscall.ECHILD {
			t.Fatal("Envoy stole selected-shell wait ownership", err)
		}
		if err := c.ProveIdle(ctx); err == nil {
			t.Fatal("pre-hook wait record authorized final idle")
		}
		if f.send(t, "reap") != "REAPED\n" {
			t.Fatal("selected fixture could not reap")
		}
		if err := c.ProveIdle(ctx); err != nil {
			t.Fatal("post-hook boundary not clean", err)
		}
	case "adopted":
		var pins []*processRef
		for i := 0; i < 16; i++ {
			pid, pin := spawn("orphan", "ORPHAN")
			p := requireProcess(t, c, pid, true)
			if p.Parent != os.Getpid() || p.Session == f.awsh {
				t.Fatal("fixture did not escape and become adopted", p)
			}
			pins = append(pins, pin)
		}
		if err := c.Cleanup(ctx, 0); err != nil {
			t.Fatal(err)
		}
		for _, pin := range pins {
			if err := pin.identity(); !processGone(err) {
				t.Fatal("adopted lifetime remained unreaped", err)
			}
			var status syscall.WaitStatus
			if _, err := syscall.Wait4(pin.pid, &status, syscall.WNOHANG, nil); err != syscall.ECHILD {
				t.Fatal("adopted child was not reaped by cleanup", err)
			}
		}
		if err := c.ProveIdle(ctx); err != nil {
			t.Fatal(err)
		}
	case "helper":
		helperPID, helper := spawn("spawn", "CHILD")
		_, authored := spawn("spawn", "CHILD")
		if err := c.Cleanup(ctx, int64(helperPID)); err != nil {
			t.Fatal(err)
		}
		if p, err := helper.observe(ctx); err != nil || !p.Live {
			t.Fatal("exact helper did not survive", p, err)
		}
		if p, err := authored.observe(ctx); err != nil || p.Live {
			t.Fatal("another authored child survived helper exclusion", p, err)
		}
		if err := c.Cleanup(ctx, 0); err != nil {
			t.Fatal(err)
		}
		if p, err := helper.observe(ctx); err != nil || p.Live {
			t.Fatal("helper was retained without an exclusion", p, err)
		}
	case "invalid-helper":
		_, child := spawn("spawn", "CHILD")
		for _, pid := range []int64{-1, 2147483648, int64(f.awsh), int64(f.shell), int64(outside), 2147483647} {
			if err := c.Cleanup(ctx, pid); err == nil {
				t.Fatal("unproved exclusion accepted", pid)
			}
			if p, err := child.observe(ctx); err != nil || !p.Live {
				t.Fatal("rejected helper signalled authored execution", pid, p, err)
			}
		}
	case "epochs":
		_, child := spawn("spawn", "CHILD")
		expired, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		defer cancel()
		cancelled, end := context.WithTimeout(context.Background(), time.Second)
		end()
		for _, epoch := range []context.Context{nil, context.Background(), expired, cancelled} {
			if err := c.Cleanup(epoch, 0); err == nil {
				t.Fatal("missing or expired caller epoch accepted")
			}
			if p, err := child.observe(ctx); err != nil || !p.Live {
				t.Fatal("invalid caller epoch allowed a signal", p, err)
			}
		}
	case "lost-pin":
		pid, child := spawn("spawn", "CHILD")
		requireProcess(t, c, pid, true)
		if err := c.refs[pid].close(); err != nil {
			t.Fatal(err)
		}
		if err := c.Cleanup(ctx, 0); !errors.Is(err, os.ErrClosed) {
			t.Fatal("lost identity pin silently discarded", err)
		}
		if p, err := child.observe(ctx); err != nil || !p.Live {
			t.Fatal("missing pin authorized a signal", p, err)
		}
	case "closed":
		if err := c.Close(); err != nil {
			t.Fatal(err)
		}
		if err := c.Cleanup(ctx, 0); err == nil {
			t.Fatal("closed owner accepted cleanup")
		}
	case "reap-ownership":
		_, child := spawn("spawn", "CHILD")
		if err := child.reapAdopted(ctx, os.Getpid()); err == nil {
			t.Fatal("direct Bash wait accepted as adopted")
		}
		if err := c.refs[f.awsh].reapAdopted(ctx, os.Getpid()+1); err == nil {
			t.Fatal("different Envoy owner accepted")
		}
	case "awsh-wait":
		_, orphan := spawn("orphan", "ORPHAN")
		cleanupFixturePin(t, f.shell) // Dispose the selected root after Awsh dies.
		if err := fixtureSignal(c.refs[f.awsh], syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		for {
			p, err := c.refs[f.awsh].observe(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if !p.Live {
				break
			}
		}
		if err := c.Cleanup(ctx, 0); err != nil {
			t.Fatal("adopted cleanup consumed the root wait", err)
		}
		if !processGone(orphan.identity()) || c.refs[f.awsh].identity() != nil {
			t.Fatal("adopted wait/root wait boundaries were confused")
		}
		err := f.command.Wait() // The existing Awsh owner still receives its reap.
		var exited *exec.ExitError
		if !errors.As(err, &exited) || exited.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
			t.Fatal("Awsh owner lost its exact wait status", err)
		}
	default:
		t.Fatal("unknown cleanup fixture", scenario)
	}
	if p, err := outsidePin.observe(ctx); err != nil || !p.Live {
		t.Fatal("outside-tree lifetime was affected", p, err)
	}
	for _, pid := range []int{f.awsh, f.shell} {
		if scenario == "closed" || scenario == "awsh-wait" {
			break
		}
		if p, err := c.refs[pid].observe(ctx); err != nil || !p.Live {
			t.Fatal("cleanup changed a controlled root", p, err)
		}
	}
	fmt.Println("CLEANUP", scenario, "PROVED")
}

// An explicit Go overlay substitutes only signal/wait syscalls. Ordinary runs
// skip this case and production carries no injectable behavior or fault state.
func TestProcessCleanupFaultOverlay(t *testing.T) {
	if os.Getenv("OMEGAFLOW_CLEANUP_FAULT") == "" {
		t.Skip("requires the retained test-only signal/wait overlay")
	}
	outside := processFixtureCommand("leaf")
	if err := outside.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { outside.Process.Kill(); outside.Wait() }()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestProcessCleanupFixture$", "-test.v")
	cmd.Env = append(os.Environ(), "OMEGAFLOW_CLEANUP_CASE=fault",
		"OMEGAFLOW_PROCESS_OUTSIDE="+strconv.Itoa(outside.Process.Pid), "GORACE=atexit_sleep_ms=0")
	output, err := cmd.CombinedOutput()
	if err != nil || !strings.Contains(string(output), "CLEANUP fault PROVED") {
		t.Fatalf("bounded cleanup fault proof: %v\n%s", err, output)
	}
	t.Logf("%s", output)
}

func runCleanupFaultCase(t *testing.T, f *censusFixture) {
	t.Helper()
	mode := os.Getenv("OMEGAFLOW_CLEANUP_FAULT")
	command, marker := "spawn", "CHILD"
	if strings.HasPrefix(mode, "wait-") {
		command, marker = "orphan", "ORPHAN"
	}
	pid := processFixturePID(t, f.send(t, command), marker)
	pin := cleanupFixturePin(t, pid)
	requireProcess(t, f.owner, pid, true)
	var late *processRef
	if mode == "signal-late-adoption" {
		// The overlay pauses the first signal after its census. Forking an
		// actual orphan here proves that the following pass must collect and
		// reap the new lifetime; one pre-signal snapshot cannot close cleanup.
		cleanupBeforeSignal = func() {
			pid := processFixturePID(t, f.send(t, "orphan"), "ORPHAN")
			late = cleanupFixturePin(t, pid)
		}
		defer func() { cleanupBeforeSignal = nil }()
	}
	budget := time.Second
	if strings.Contains(mode, "always-eintr") || strings.HasSuffix(mode, "noop") {
		budget = 25 * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(context.Background(), budget)
	defer cancel()
	deadline, _ := ctx.Deadline()
	started := time.Now()
	err := f.owner.Cleanup(ctx, 0)
	switch mode {
	case "signal-once-eintr", "wait-once-eintr", "signal-late-adoption":
		if err != nil {
			t.Fatal("one interrupted syscall aborted cleanup", mode, err)
		}
	case "signal-always-eintr", "wait-always-eintr", "signal-noop", "wait-noop":
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("no-progress cleanup escaped original caller epoch", mode, err)
		}
	case "signal-denied":
		if !errors.Is(err, syscall.EPERM) || processGone(err) {
			t.Fatal("signal permission failure was hidden", err)
		}
	case "signal-badfd":
		if !errors.Is(err, syscall.EBADF) || processGone(err) {
			t.Fatal("lost signal descriptor was hidden", err)
		}
	case "wait-not-child":
		if !errors.Is(err, syscall.ECHILD) || processGone(err) {
			t.Fatal("lost wait ownership was hidden", err)
		}
	default:
		t.Fatal("unknown cleanup fault mode", mode)
	}
	if got, _ := ctx.Deadline(); !got.Equal(deadline) {
		t.Fatal("cleanup changed its original deadline")
	}
	if mode == "signal-always-eintr" || mode == "signal-noop" || mode == "signal-denied" || mode == "signal-badfd" {
		if p, err := pin.observe(censusContext(t)); err != nil || !p.Live {
			t.Fatal("failed/no-op signal unexpectedly terminated fixture", p, err)
		}
	}
	if mode == "signal-late-adoption" && (late == nil || !processGone(late.identity())) {
		t.Fatal("late adopted lifetime was missed or left unreaped")
	}
	t.Logf("original deadline retained; %s elapsed=%s error=%v", mode, time.Since(started), err)
}

// This barrier exists only in the test binary and is called only by the
// explicit syscall overlay. Production contains no callback or fault state.
var cleanupBeforeSignal func()

func cleanupSignalBarrier() {
	if cleanupBeforeSignal != nil {
		cleanupBeforeSignal()
	}
}
