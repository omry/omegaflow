//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

func censusContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func TestProcessStatIdentity(t *testing.T) {
	valid := "42 (a ) tricky ( name) S 12 42 12 " + strings.Repeat("0 ", 15) + "765 0\n"
	p, err := parseProcess(42, valid)
	if err != nil || p != (processInfo{PID: 42, Parent: 12, Group: 42, Session: 12, Start: 765}) {
		t.Fatal(p, err)
	}
	for _, text := range []string{"", "42 no name", "43 (a) S 12 42 12", "42 (a) S 12", strings.Replace(valid, "S 12", "SS 12", 1), strings.Replace(valid, "S 12", "S -1", 1), strings.Replace(valid, "S 12", "S bad", 1), strings.Replace(valid, "765", "-1", 1)} {
		if _, err := parseProcess(42, text); err == nil {
			t.Fatalf("accepted malformed stat %q", text)
		}
	}
	if _, err := parseProcess(0, valid); err == nil {
		t.Fatal("accepted invalid requested PID")
	}
	actual, err := readProcess(os.Getpid())
	if err != nil || actual.PID != os.Getpid() || actual.Parent != os.Getppid() {
		t.Fatal(actual, err)
	}
}

func TestProcessStatDeadTaskIsGone(t *testing.T) {
	dead := "42 (session.test) X 0 -1 -1 0 -1 " + strings.Repeat("0 ", 14) + "765 0\n"
	if _, err := parseProcess(42, dead); !processGone(err) {
		t.Fatalf("dead-task stat was not classified as process exit: %v", err)
	}
}

func TestProcessPinLifetimeAndOwnership(t *testing.T) {
	ctx := censusContext(t)
	r, p, err := pinProcess(ctx, os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.close() })
	if !p.Live || p.PID != os.Getpid() || r.start != p.Start {
		t.Fatal("unproved live identity", p, r)
	}
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(r.fd), syscall.F_GETFD, 0)
	if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
		t.Fatal("pidfd can cross exec", flags, errno)
	}
	r.start++
	if _, err := r.observe(ctx); err == nil {
		t.Fatal("changed start identity accepted")
	}
	r.start--
	r.pid++
	if _, err := r.observe(ctx); err == nil {
		t.Fatal("numeric PID accepted without matching lifetime")
	}
	r.pid--
	if err := r.close(); err != nil || r.fd != -1 {
		t.Fatal("pin not disposed", err)
	}
	if err := r.close(); err != nil {
		t.Fatal("close not idempotent", err)
	}
	if _, err := r.observe(ctx); !errors.Is(err, os.ErrClosed) {
		t.Fatal("closed pin usable", err)
	}
}

func TestProcessPinRejectsUnprovedDescriptor(t *testing.T) {
	f, err := os.Open("/dev/null")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	r := &processRef{pid: os.Getpid(), fd: int(f.Fd())}
	if _, err := r.observe(censusContext(t)); err == nil {
		t.Fatal("regular FD substituted for lifetime pin")
	}
	r.fd = 2147483647
	if err := r.identity(); !errors.Is(err, syscall.EBADF) || processGone(err) {
		t.Fatal("missing descriptor accepted")
	}
	if err := r.close(); !errors.Is(err, syscall.EBADF) || r.fd != -1 {
		t.Fatal("failed close retained retry authority", err, r.fd)
	}
}

func TestProcessPinRequiresCallerEpoch(t *testing.T) {
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	for _, c := range []context.Context{nil, context.Background(), ctx} {
		if _, _, err := pinProcess(c, os.Getpid()); err == nil {
			t.Fatal("missing/expired caller epoch accepted")
		}
	}
	for _, pid := range []int{0, -1, 2147483648} {
		if _, _, err := pinProcess(censusContext(t), pid); err == nil {
			t.Fatal("invalid pid accepted", pid)
		}
	}
	if _, _, err := pinProcess(censusContext(t), 2147483647); !processGone(err) {
		t.Fatal("absent PID not classified", err)
	}
}

func TestProcessPinCannotReviveReapedLifetime(t *testing.T) {
	cmd := processFixtureCommand("leaf")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cmd.Process.Kill(); cmd.Wait() })
	r, _, err := pinProcess(censusContext(t), cmd.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	defer r.close()
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	cmd.Wait()
	if _, err := r.observe(censusContext(t)); !processGone(err) {
		t.Fatal("reaped lifetime remained usable", err)
	}
	// Substitute a currently live numeric PID. The old pidfd must still report
	// its dead lifetime instead of adopting the new process identity.
	r.pid, r.start = os.Getpid(), 0
	if _, err := r.observe(censusContext(t)); !errors.Is(err, os.ErrProcessDone) {
		t.Fatal("reused number revived old lifetime", err)
	}
}

func processFixtureCommand(role string, extra ...string) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "-test.run=^TestProcessCensusFixture$")
	for _, value := range os.Environ() {
		if !strings.HasPrefix(value, "OMEGAFLOW_PROCESS_") && !strings.HasPrefix(value, "GORACE=") {
			cmd.Env = append(cmd.Env, value)
		}
	}
	cmd.Env = append(cmd.Env, "OMEGAFLOW_PROCESS_ROLE="+role)
	// Native fixtures have explicit join/reap barriers. Suppress only the race
	// runtime's one-second exit sleep, not race checking or its failure status.
	cmd.Env = append(cmd.Env, "GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
	cmd.Env = append(cmd.Env, extra...)
	if dir := os.Getenv("OMEGAFLOW_PROCESS_COVER_DIR"); role == "envoy" && dir != "" && testing.CoverMode() != "" {
		for _, value := range extra {
			if name, ok := strings.CutPrefix(value, "OMEGAFLOW_PROCESS_CASE="); ok {
				cmd.Args = append(cmd.Args, "-test.coverprofile="+filepath.Join(dir, "native-"+name+".cover"))
			}
		}
	}
	return cmd
}

func TestProcessGoneKeepsDisposalFailure(t *testing.T) {
	if !processGone(errors.Join(os.ErrNotExist, syscall.ESRCH)) ||
		processGone(errors.Join(os.ErrProcessDone, syscall.EBADF)) || processGone(nil) {
		t.Fatal("exit race classification hid a disposal failure")
	}
}

func TestProcessCensusNative(t *testing.T) {
	beforeSubreaper, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		afterSubreaper, err := processSubreaperState()
		if err != nil {
			t.Error(err)
		} else if afterSubreaper != beforeSubreaper {
			t.Errorf("shared test process subreaper state changed: before=%d after=%d", beforeSubreaper, afterSubreaper)
		}
	})
	for _, scenario := range []string{"topology", "descendants", "current-sample", "rebind-modeled", "root-exit", "adoption", "rapid-adoption", "concurrent-adoption", "foreign-group", "dead-roots", "errors"} {
		t.Run(scenario, func(t *testing.T) {
			outside := processFixtureCommand("leaf")
			outside.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			if err := outside.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() { outside.Process.Kill(); outside.Wait() }()
			cmd := processFixtureCommand("envoy", "OMEGAFLOW_PROCESS_CASE="+scenario, "OMEGAFLOW_PROCESS_OUTSIDE="+strconv.Itoa(outside.Process.Pid))
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			// Retain native subprocess output; no process-wide subreaper change
			// occurs in this shared test process.
			done := make(chan error, 1)
			var output strings.Builder
			cmd.Stdout, cmd.Stderr = &output, &output
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			go func() { done <- cmd.Wait() }()
			select {
			case err := <-done:
				if err != nil {
					t.Fatalf("native %s: %v\n%s", scenario, err, output.String())
				}
			case <-ctx.Done():
				cmd.Process.Kill()
				<-done
				t.Fatalf("native %s exceeded bound\n%s", scenario, output.String())
			}
		})
	}
}

func processSubreaperState() (int32, error) {
	var enabled int32
	_, _, errno := syscall.Syscall6(syscall.SYS_PRCTL, 37, uintptr(unsafe.Pointer(&enabled)), 0, 0, 0, 0)
	if errno != 0 {
		return 0, errno
	}
	return enabled, nil
}

// The review/acceptance fault runner overlays only the native ppoll call. No
// injectable syscall or fault state is added to production. Ordinary runs use
// the concurrent native adoption case and leave this explicit fault case out.
func TestProcessInterruptedPollOverlay(t *testing.T) {
	mode := os.Getenv("OMEGAFLOW_PROCESS_POLL_FAULT")
	if mode == "" {
		t.Skip("requires explicit test-only ppoll fault overlay")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	r, p, err := pinProcess(ctx, os.Getpid())
	if r != nil {
		defer r.close()
	}
	switch mode {
	case "once":
		if err != nil || !p.Live {
			t.Fatal("one interrupted poll did not resume", p, err)
		}
	case "always":
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("repeated interruption escaped original epoch", err)
		}
	case "badfd":
		if !errors.Is(err, syscall.EBADF) || processGone(err) {
			t.Fatal("non-interruption poll failure was hidden", err)
		}
	default:
		t.Fatal("unknown poll fault mode", mode)
	}
}

func processFixturePID(t *testing.T, line, kind string) int {
	t.Helper()
	fields := strings.Fields(line)
	if len(fields) != 2 || fields[0] != kind {
		t.Fatalf("unexpected fixture response %q", line)
	}
	pid, err := strconv.Atoi(fields[1])
	if err != nil || pid <= 0 {
		t.Fatal("invalid fixture PID", line, err)
	}
	return pid
}

func fixtureSignal(ref *processRef, signal syscall.Signal) error {
	_, _, errno := syscall.Syscall6(424, uintptr(ref.fd), uintptr(signal), 0, 0, 0, 0)
	if errno != 0 {
		return errno
	}
	return nil
}

func requireProcess(t *testing.T, c *processCensus, pid int, live bool) processInfo {
	t.Helper()
	all, err := c.Census(censusContext(t))
	if err != nil {
		t.Fatal(err)
	}
	p, ok := all[pid]
	if !ok || p.Live != live {
		t.Fatal("missing/wrong pinned census identity", pid, live, all)
	}
	return p
}

func TestProcessCensusFixture(t *testing.T) {
	switch role := os.Getenv("OMEGAFLOW_PROCESS_ROLE"); role {
	case "":
		return
	case "leaf":
		time.Sleep(30 * time.Second)
		os.Exit(0)
	case "middle":
		leaf := processFixtureCommand("leaf")
		leaf.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
		if err := leaf.Start(); err != nil {
			panic(err)
		}
		fmt.Println("ORPHAN", leaf.Process.Pid)
		os.Exit(0)
	case "awsh":
		shell := processFixtureCommand("shell")
		shell.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		shell.Stdin, shell.Stdout, shell.Stderr = os.Stdin, os.Stdout, os.Stderr
		if err := shell.Start(); err != nil {
			panic(err)
		}
		fmt.Println("ROOTS", os.Getpid(), shell.Process.Pid)
		shell.Wait()
		os.Exit(0)
	case "shell":
		runProcessFixtureShell()
		os.Exit(0)
	case "envoy":
		runProcessCensusCase(t, os.Getenv("OMEGAFLOW_PROCESS_CASE"))
	default:
		t.Fatal("unknown fixture role", role)
	}
}
