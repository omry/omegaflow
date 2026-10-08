package launch

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

func TestHandoffGrammar(t *testing.T) {
	good := []string{"supervise", "--control-fd=3", "--result-fd=4", "--pty-slave-fd=5", "--session-runtime-dir=/run/omegaflow/session"}
	h, e := ParseHandoff(good)
	if e != nil || h.Control != 3 || h.Result != 4 || h.Slave != 5 {
		t.Fatalf("%+v %v", h, e)
	}
	for _, args := range [][]string{nil, good[:4], append(append([]string{}, good...), "extra")} {
		if _, e := ParseHandoff(args); e == nil {
			t.Fatalf("accepted %v", args)
		}
	}
	for i := range good {
		a := append([]string{}, good...)
		a[i] = "wrong"
		if _, e := ParseHandoff(a); e == nil {
			t.Fatalf("accepted %v", a)
		}
	}
	for _, bad := range []string{"2", "-1", "03", "+3", "x", "99999999999999999999"} {
		a := append([]string{}, good...)
		a[1] = "--control-fd=" + bad
		if _, e := ParseHandoff(a); e == nil {
			t.Fatal(bad)
		}
	}
	for _, h := range []Handoff{{}, {3, 3, 5, "/run/omegaflow/session"}, {3, 4, 5, "/wrong"}, {1, 4, 5, "/run/omegaflow/session"}} {
		if h.intake() == nil {
			t.Fatal(h)
		}
	}
	for _, s := range [][]uint64{nil, {1}, {0}, {9}, {19}, {65}, {1, 1}} {
		if _, _, _, e := spawn(-1, s); e == nil {
			t.Fatal(s)
		}
	}
	if _, e := stringVector([]string{"a\x00b"}); e == nil {
		t.Fatal("NUL accepted")
	}
	if _, _, _, e := spawn(-1, []uint64{1, 2, 3, 10, 20, 21, 22}); e == nil {
		t.Fatal("invalid slave accepted")
	}
}

// All candidate bypasses are compiled exclusively into this test binary.
func TestSelectedSupervisorProcess(t *testing.T) {
	mode := os.Getenv("OMEGAFLOW_LAUNCH_MODE")
	if mode == "" {
		return
	}
	evidence := os.NewFile(6, "evidence")
	fail := func(e error) { fmt.Fprintln(evidence, e); os.Exit(32) }
	bytes, e := os.ReadFile("/bin/bash")
	if e != nil {
		fail(e)
	}
	hash := sha256.Sum256(bytes)
	if hex.EncodeToString(hash[:]) != os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST") {
		fail(fmt.Errorf("candidate digest mismatch"))
	}
	var signals []uint64
	if e = json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS")), &signals); e != nil {
		fail(e)
	}
	if strings.HasPrefix(mode, "raw-") {
		if e := rawFailure(mode, signals); e != nil {
			fail(e)
		}
		fmt.Fprintln(evidence, "rejected", mode)
		os.Exit(0)
	}
	signalMask := uint64(1) << uint(syscall.SIGUSR2-1)
	runtime.LockOSThread()
	if mode == "fallback" {
		// Exercise the real raw-child fallback without changing production code.
		filter := []syscall.SockFilter{{Code: 0x20}, {Code: 0x15, Jf: 1, K: 436}, {Code: 0x06, K: 0x50000 | uint32(syscall.ENOSYS)}, {Code: 0x06, K: 0x7fff0000}}
		program := syscall.SockFprog{Len: uint16(len(filter)), Filter: &filter[0]}
		if _, _, e := syscall.Syscall6(syscall.SYS_PRCTL, 38 /* PR_SET_NO_NEW_PRIVS */, 1, 0, 0, 0, 0); e != 0 {
			fail(e)
		}
		if _, _, e := syscall.Syscall6(syscall.SYS_PRCTL, syscall.PR_SET_SECCOMP, 2, uintptr(unsafe.Pointer(&program)), 0, 0, 0); e != 0 {
			fail(e)
		}
		runtime.KeepAlive(filter)
	}
	_, _, errno := syscall.RawSyscall6(syscall.SYS_RT_SIGPROCMASK, 0, uintptr(unsafe.Pointer(&signalMask)), 0, 8, 0, 0)
	if errno != 0 {
		fail(errno)
	}
	// Ignore inheritance must be reset by the raw child, not merely by Bash.
	for _, s := range []syscall.Signal{syscall.SIGHUP, syscall.SIGINT, syscall.SIGQUIT, syscall.SIGTSTP, syscall.SIGTTIN, syscall.SIGTTOU, syscall.SIGUSR2, syscall.SIGTERM} {
		signal.Ignore(s)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	h := Handoff{3, 4, 5, "/run/omegaflow/session"}
	switch mode {
	case "direction":
		h.Control = 4
		h.Result = 3
	case "not-pty":
		h.Slave = 7
	case "stdio":
		if e := syscall.Dup3(5, 0, 0); e != nil {
			fail(e)
		}
	case "closed-stdin":
		syscall.Close(0)
	case "cancel":
		cancel()
	}
	c, e := launchSelected(ctx, h, signals)
	var parentMask uint64
	_, _, errno = syscall.RawSyscall6(syscall.SYS_RT_SIGPROCMASK, 0, 0, uintptr(unsafe.Pointer(&parentMask)), 8, 0, 0)
	runtime.UnlockOSThread()
	if errno != 0 || parentMask&signalMask == 0 {
		fail(fmt.Errorf("parent mask was not restored"))
	}
	if mode != "normal" && mode != "fallback" {
		if e == nil {
			c.close()
			fail(fmt.Errorf("invalid launch accepted"))
		}
		fmt.Fprintln(evidence, "rejected", e)
		os.Exit(0)
	}
	if e != nil {
		fail(e)
	}
	for _, fd := range []int{3, 4, 5} {
		flags, e := fcntl(fd, syscall.F_GETFD, 0)
		if e != nil || flags&syscall.FD_CLOEXEC == 0 {
			fail(fmt.Errorf("intake CLOEXEC %d", fd))
		}
	}
	fmt.Fprintf(evidence, "%d\n", c.PID)
	select {
	case r := <-c.Reaped:
		if r.Err != nil || !r.Status.Exited() || r.Status.ExitStatus() != 0 {
			fail(fmt.Errorf("unexpected reap %+v", r))
		}
	case <-ctx.Done():
		c.close()
		fail(ctx.Err())
	}
	c.close()
	c.close()
	var status syscall.WaitStatus
	if _, e = syscall.Wait4(c.PID, &status, syscall.WNOHANG, nil); e != syscall.ECHILD {
		fail(fmt.Errorf("selected child not reaped: %v", e))
	}
	os.Exit(0)
}

func rawFailure(mode string, signals []uint64) error {
	rr, rw, err := os.Pipe()
	if err != nil {
		return err
	}
	defer rr.Close()
	defer rw.Close()
	lr, lw, err := os.Pipe()
	if err != nil {
		return err
	}
	defer lr.Close()
	defer lw.Close()
	inputs := []int{5, int(rw.Fd()), int(lr.Fd())}
	if mode == "raw-report" {
		// Duplication succeeds, but the actual setup report write must fail.
		inputs[1] = int(rr.Fd())
	}
	for i, fd := range inputs {
		inputs[i], err = fcntl(fd, syscall.F_DUPFD_CLOEXEC, 10)
		if err != nil {
			return err
		}
		defer syscall.Close(inputs[i])
	}
	if mode == "raw-dup" {
		inputs[0] = -1
	}
	argv, err := stringVector([]string{"/bin/bash", "--noprofile", "--rcfile", "/omegaflow-runtime/etc/awsh-bashrc", "-i"})
	if err != nil {
		return err
	}
	env, err := stringVector(os.Environ())
	if err != nil {
		return err
	}
	var limit syscall.Rlimit
	if err = syscall.Getrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil {
		return err
	}
	a := childArguments{argv[0], &argv[0], &env[0], &signals[0], uint64(len(signals)), uint64(inputs[0]), uint64(inputs[1]), uint64(inputs[2]), limit.Cur}
	if mode == "raw-high-limit" {
		a.fdLimit = 1 << 30 // Fast-path setup must not loop a billion times.
	}
	runtime.LockOSThread()
	pid, errno := forkChild(&a)
	runtime.UnlockOSThread()
	runtime.KeepAlive(argv)
	runtime.KeepAlive(env)
	runtime.KeepAlive(signals)
	if errno != 0 {
		return syscall.Errno(errno)
	}
	if mode == "raw-release-bad" {
		_, err = lw.Write([]byte{2})
	}
	lw.Close()
	var status syscall.WaitStatus
	for {
		_, err = syscall.Wait4(int(pid), &status, 0, nil)
		if err != syscall.EINTR {
			break
		}
	}
	if err != nil || !status.Exited() || status.ExitStatus() != 127 {
		return fmt.Errorf("raw fail-stop status %v: %v", status, err)
	}
	return nil
}

func openPTY(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	master, e := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY, 0)
	if e != nil {
		t.Fatal(e)
	}
	var unlock int32
	var number uint32
	if e = ioctl(int(master.Fd()), syscall.TIOCSPTLCK, unsafe.Pointer(&unlock)); e != nil {
		t.Fatal(e)
	}
	if e = ioctl(int(master.Fd()), syscall.TIOCGPTN, unsafe.Pointer(&number)); e != nil {
		t.Fatal(e)
	}
	slave, e := os.OpenFile("/dev/pts/"+strconv.FormatUint(uint64(number), 10), os.O_RDWR|syscall.O_NOCTTY, 0)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { master.Close(); slave.Close() })
	return master, slave
}

func TestRealSelectedLaunch(t *testing.T) {
	digest := os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST")
	if digest == "" {
		t.Skip("verified Reploy candidate required")
	}
	for _, mode := range []string{"normal", "fallback", "direction", "not-pty", "stdio", "closed-stdin", "session-leader", "cancel", "raw-dup", "raw-report", "raw-release-eof", "raw-release-bad", "raw-high-limit"} {
		for attempt := 0; attempt < 3; attempt++ {
			t.Run(mode+strconv.Itoa(attempt), func(t *testing.T) {
				master, slave := openPTY(t)
				cr, cw, _ := os.Pipe()
				rr, rw, _ := os.Pipe()
				er, ew, _ := os.Pipe()
				nullIn, e := os.Open("/dev/null")
				if e != nil {
					t.Fatal(e)
				}
				nullOut, e := os.OpenFile("/dev/null", os.O_WRONLY, 0)
				if e != nil {
					t.Fatal(e)
				}
				nullRW, e := os.OpenFile("/dev/null", os.O_RDWR, 0)
				if e != nil {
					t.Fatal(e)
				}
				for _, f := range []*os.File{cr, cw, rr, rw, er, ew, nullIn, nullOut, nullRW} {
					defer f.Close()
				}
				bin, _ := os.Executable()
				ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
				defer cancel()
				cmd := exec.CommandContext(ctx, bin, "-test.run=^TestSelectedSupervisorProcess$")
				cmd.Env = []string{"PATH=/hostile/path", "HOME=/hostile/home", "HISTFILE=", "INPUTRC=/omegaflow-runtime/etc/inputrc", "TERM=xterm-256color", "LC_ALL=C.UTF-8", "LANG=C.UTF-8", "OMEGAFLOW_LAUNCH_MODE=" + mode, "OMEGAFLOW_CANDIDATE_DIGEST=" + digest, "OMEGAFLOW_CANDIDATE_SIGNALS=" + os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS")}
				if mode == "session-leader" {
					cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
				}
				if directory := os.Getenv("GOCOVERDIR"); directory != "" {
					cmd.Env = append(cmd.Env, "GOCOVERDIR="+directory)
					cmd.Args = append(cmd.Args, "-test.gocoverdir="+directory)
				}
				cmd.ExtraFiles = []*os.File{cr, rw, slave, ew, nullRW}
				cmd.Stdin = nullIn
				cmd.Stdout = nullOut
				cmd.Stderr = nullOut
				if e = cmd.Start(); e != nil {
					t.Fatal(e)
				}
				defer func() { cmd.Process.Kill(); cmd.Wait() }()
				cr.Close()
				rw.Close()
				ew.Close()
				slave.Close()
				output := make(chan []byte, 1)
				go func() {
					var b strings.Builder
					buffer := make([]byte, 4096)
					for {
						n, e := master.Read(buffer)
						b.Write(buffer[:n])
						if e != nil {
							output <- []byte(b.String())
							return
						}
					}
				}()
				er.SetReadDeadline(time.Now().Add(7 * time.Second))
				evidence, e := io.ReadAll(er)
				if e != nil {
					t.Fatal(e)
				}
				if e = cmd.Wait(); e != nil {
					t.Fatalf("supervisor %v: %s", e, evidence)
				}
				var bytes []byte
				select {
				case bytes = <-output:
				case <-ctx.Done():
					t.Fatal("PTY did not close")
				}
				if mode != "normal" && mode != "fallback" {
					if !strings.HasPrefix(string(evidence), "rejected ") || strings.Contains(string(bytes), "BASHSTAT") {
						t.Fatalf("invalid launch %s output %s", evidence, bytes)
					}
					return
				}
				shell, e := strconv.Atoi(strings.TrimSpace(string(evidence)))
				if e != nil {
					t.Fatal(string(evidence))
				}
				statLine := ""
				maskLine := ""
				observer := ""
				scanner := bufio.NewScanner(strings.NewReader(string(bytes)))
				for scanner.Scan() {
					line := strings.TrimSuffix(scanner.Text(), "\r")
					if strings.HasPrefix(line, "BASHSTAT ") {
						statLine = strings.TrimPrefix(line, "BASHSTAT ")
					}
					if strings.HasPrefix(line, "BASHMASK ") {
						maskLine = strings.TrimPrefix(line, "BASHMASK ")
					}
					if strings.HasPrefix(line, "OBSERVER ") {
						observer = strings.TrimPrefix(line, "OBSERVER ")
					}
				}
				f := strings.Fields(statLine[strings.LastIndex(statLine, ")")+1:])
				if len(f) < 6 {
					t.Fatalf("no Bash identity %q", bytes)
				}
				want := []string{strconv.Itoa(cmd.Process.Pid), strconv.Itoa(shell), strconv.Itoa(cmd.Process.Pid)}
				for i, w := range want {
					if f[i+1] != w {
						t.Fatalf("Bash identity %s expected %v", statLine, want)
					}
				}
				if f[5] != strconv.Itoa(shell) {
					t.Fatalf("Bash was not foreground before rcfile: %s", statLine)
				}
				if maskLine != "0000000000000000" {
					t.Fatalf("inherited signal mask %q", maskLine)
				}
				var obs struct {
					FDs     []string          `json:"fds"`
					Targets map[string]string `json:"targets"`
					Status  string            `json:"status"`
				}
				if e = json.Unmarshal([]byte(observer), &obs); e != nil {
					t.Fatalf("observer %q output=%s", observer, bytes)
				}
				if len(obs.FDs) != 3 {
					t.Fatalf("inherited descriptors %v targets=%v", obs.FDs, obs.Targets)
				}
				for _, fd := range obs.FDs {
					if fd != "0" && fd != "1" && fd != "2" {
						t.Fatal(obs.FDs)
					}
				}
				for _, line := range strings.Split(obs.Status, "\n") {
					if strings.HasPrefix(line, "SigIgn:") {
						v, e := strconv.ParseUint(strings.TrimSpace(strings.TrimPrefix(line, "SigIgn:")), 16, 64)
						if e != nil {
							t.Fatal(e)
						}
						for _, s := range []syscall.Signal{syscall.SIGHUP, syscall.SIGINT, syscall.SIGQUIT, syscall.SIGTERM, syscall.SIGUSR2, syscall.SIGTSTP, syscall.SIGTTIN, syscall.SIGTTOU} {
							if v&(uint64(1)<<uint(s-1)) != 0 {
								t.Fatalf("inherited ignore survived %v: %s", s, line)
							}
						}
					}
				}
				t.Logf("candidate=%s shell=%d direct-parent=%d foreground-before-rcfile=%s descriptors=%v", digest, shell, cmd.Process.Pid, f[5], obs.FDs)
			})
		}
	}
}

func TestReaperChildProcess(t *testing.T) {
	if os.Getenv("OMEGAFLOW_REAPER_CHILD") != "1" {
		return
	}
	var b [1]byte
	if _, err := io.ReadFull(os.Stdin, b[:]); err != nil {
		os.Exit(38)
	}
	os.Exit(37)
}

func TestSelectedReaper(t *testing.T) {
	for _, outcome := range []string{"exit", "kill"} {
		t.Run(outcome, func(t *testing.T) {
			bin, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command(bin, "-test.run=^TestReaperChildProcess$")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_REAPER_CHILD=1")
			cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			input, err := cmd.StdinPipe()
			if err != nil {
				t.Fatal(err)
			}
			defer input.Close()
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			defer cmd.Process.Release()
			c := &selectedChild{PID: cmd.Process.Pid, Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
			c.startReaper()
			defer c.close()
			deadline := time.Now().Add(3 * time.Second)
			blocked := false
			for time.Now().Before(deadline) && !blocked {
				tasks, err := os.ReadDir("/proc/self/task")
				if err != nil {
					t.Fatal(err)
				}
				for _, task := range tasks {
					data, err := os.ReadFile("/proc/self/task/" + task.Name() + "/syscall")
					fields := strings.Fields(string(data))
					if err != nil || len(fields) < 5 {
						continue
					}
					n, _ := strconv.ParseUint(fields[0], 0, 64)
					pid, _ := strconv.ParseUint(fields[2], 0, 64)
					flags, _ := strconv.ParseUint(fields[4], 0, 64)
					if n == syscall.SYS_WAITID && pid == uint64(c.PID) && flags == syscall.WEXITED|syscall.WNOWAIT {
						blocked = true
					}
				}
				if !blocked {
					time.Sleep(time.Millisecond)
				}
			}
			if !blocked {
				t.Fatal("selected reaper never blocked in waitid(WEXITED|WNOWAIT)")
			}
			if outcome == "exit" {
				if _, err := input.Write([]byte{1}); err != nil {
					t.Fatal(err)
				}
			} else {
				c.close()
			}
			select {
			case result := <-c.Reaped:
				if result.Err != nil || (outcome == "exit" && (!result.Status.Exited() || result.Status.ExitStatus() != 37)) || (outcome == "kill" && (!result.Status.Signaled() || result.Status.Signal() != syscall.SIGKILL)) {
					t.Fatalf("reap: %+v", result)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("reaper did not finish")
			}
			c.close()
			var status syscall.WaitStatus
			if _, err := syscall.Wait4(c.PID, &status, syscall.WNOHANG, nil); err != syscall.ECHILD {
				t.Fatalf("selected child not reaped: %v", err)
			}
		})
	}
}
