package launch

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestQualifiedLaunchFailsClosed(t *testing.T) {
	if _, e := Start(context.Background(), Handoff{}, "unknown", bashbuild.Target{}, nil); e == nil {
		t.Fatal("unqualified production launch")
	}
	for _, h := range []Handoff{{}, {3, 3, 5, "/run/omegaflow/session"}, {3, 4, 5, "/wrong"}, {1, 4, 5, "/run/omegaflow/session"}} {
		if h.intake() == nil {
			t.Fatalf("accepted invalid handoff %+v", h)
		}
	}
	for _, signals := range [][]uint64{nil, {1}, {0}, {9}, {19}, {65}, {1, 1}} {
		if _, _, _, e := spawn(-1, signals); e == nil {
			t.Fatalf("accepted signals %v", signals)
		}
	}
	if _, e := stringVector([]string{"x\x00y"}); e == nil {
		t.Fatal("accepted NUL")
	}
}

// This process mode is compiled into the test binary only; production has no
// candidate bypass, fake Envoy switch, or partial supervise command.
func TestSupervisorProcess(t *testing.T) {
	if os.Getenv("OMEGAFLOW_TEST_CHILD") == "" {
		return
	}
	digest := os.Getenv("OMEGAFLOW_TEST_DIGEST")
	scenario := os.Getenv("OMEGAFLOW_STARTUP_SCENARIO")
	os.Unsetenv("OMEGAFLOW_STARTUP_SCENARIO")
	var signals []uint64
	if json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_TEST_SIGNALS")), &signals) != nil {
		os.Exit(29)
	}
	os.Unsetenv("OMEGAFLOW_TEST_SIGNALS")
	os.Unsetenv("OMEGAFLOW_TEST_CHILD")
	os.Unsetenv("OMEGAFLOW_TEST_DIGEST")
	b, e := os.ReadFile("/bin/bash")
	if e != nil {
		os.Exit(30)
	}
	hash := sha256.Sum256(b)
	if hex.EncodeToString(hash[:]) != digest {
		os.Exit(31)
	}
	budget := 10 * time.Second
	if scenario != "" {
		budget = 500 * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(context.Background(), budget)
	defer cancel()
	if scenario == "cancel-startup" {
		time.AfterFunc(100*time.Millisecond, cancel)
	}
	if scenario == "existing-directory" {
		if e = os.Mkdir("/run/omegaflow/session/bash", 0700); e != nil {
			os.Exit(28)
		}
		if e = os.WriteFile("/run/omegaflow/session/bash/owner", []byte("preexisting"), 0600); e != nil {
			os.Exit(28)
		}
	}
	s, e := start(ctx, Handoff{3, 4, 5, "/run/omegaflow/session"}, signals)
	if e != nil {
		f := os.NewFile(6, "test-evidence")
		fmt.Fprintln(f, e)
		if scenario != "" {
			// This census is a test assertion, never an Awsh operation owner.
			files, _ := os.ReadDir("/proc")
			for _, file := range files {
				pid, err := strconv.Atoi(file.Name())
				if err != nil || pid == os.Getpid() {
					continue
				}
				_, _, sid, err := processIdentity(pid)
				if err != nil || sid != os.Getpid() {
					continue
				}
				b, _ := os.ReadFile("/proc/" + file.Name() + "/stat")
				end := strings.LastIndex(string(b), ")")
				if end < 0 {
					os.Exit(38)
				}
				fields := strings.Fields(string(b[end+1:]))
				if len(fields) == 0 || fields[0] != "Z" {
					fmt.Fprintln(f, "live launch member", pid, string(b))
					os.Exit(38)
				}
			}
			if s != nil && s.child != nil {
				select {
				case <-s.child.done:
				default:
					os.Exit(39)
				}
			}
			if scenario == "existing-directory" {
				b, e := os.ReadFile("/run/omegaflow/session/bash/owner")
				if e != nil || string(b) != "preexisting" {
					os.Exit(40)
				}
				os.Remove("/run/omegaflow/session/bash/owner")
				os.Remove("/run/omegaflow/session/bash")
			}
			if _, err := os.Stat("/run/omegaflow/session/bash"); !os.IsNotExist(err) {
				os.Exit(40)
			}
			os.Exit(32)
		}
		os.Exit(32)
	}
	if scenario != "" {
		s.Close()
		os.Exit(41)
	}
	if s.ActiveTermios.Lflag&(syscall.ICANON|syscall.ECHO) != 0 {
		os.Exit(33)
	}
	if _, e := fcntl(5, syscall.F_GETFD, 0); e == nil {
		os.Exit(34)
	}
	// Reopening a lease does not retain it or change the recorded active state.
	if e = s.terminal.drain(ctx); e != nil {
		os.Exit(35)
	}
	before, _ := os.ReadDir("/proc/self/fd")
	s.terminal.foreground = -1
	if e = s.terminal.drain(ctx); e == nil {
		os.Exit(35)
	}
	s.terminal.foreground = s.ShellPID
	after, _ := os.ReadDir("/proc/self/fd")
	if len(before) != len(after) {
		os.Exit(35)
	}
	evidence, _ := json.Marshal(map[string]any{"awsh_pid": os.Getpid(), "shell_pid": s.ShellPID, "state": s.State, "active_termios": s.ActiveTermios})
	f := os.NewFile(6, "test-evidence")
	f.Write(append(evidence, '\n'))
	var done [1]byte
	os.NewFile(3, "control").Read(done[:])
	s.Close()
	s.Close()
	if _, e = os.Stat("/run/omegaflow/session/bash"); !os.IsNotExist(e) {
		os.Exit(36)
	}
	os.Exit(0)
}

func TestLivePromptStateIdentity(t *testing.T) {
	cwd, e := syscall.Getwd()
	if e != nil {
		t.Fatal(e)
	}
	good := protocol.PromptState{PhysicalCWD: cwd}
	if e = validateLiveState(good, os.Getpid()); e != nil {
		t.Fatal(e)
	}
	for _, state := range []protocol.PromptState{{PhysicalCWD: t.TempDir()}, {PhysicalCWD: cwd, LogicalCWD: t.TempDir()}, {PhysicalCWD: "/missing-omegaflow-cwd"}} {
		if e = validateLiveState(state, os.Getpid()); e == nil {
			t.Fatalf("accepted stale state %+v", state)
		}
	}
	if _, e = start(context.Background(), Handoff{}, nil); e == nil {
		t.Fatal("missing existing launch deadline accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e = start(ctx, Handoff{}, nil); e == nil {
		t.Fatal("cancelled startup accepted")
	}
}

func TestStartupZombieIdentity(t *testing.T) {
	cmd := exec.Command("/bin/sh", "-c", "exit 0")
	if e := cmd.Start(); e != nil {
		t.Fatal(e)
	}
	defer cmd.Wait()
	var info [16]uint64
	for {
		_, _, e := syscall.Syscall6(syscall.SYS_WAITID, 1, uintptr(cmd.Process.Pid), uintptr(unsafe.Pointer(&info[0])), syscall.WEXITED|syscall.WNOWAIT, 0, 0)
		if e == syscall.EINTR {
			continue
		}
		if e != 0 {
			t.Fatal(e)
		}
		break
	}
	if _, _, _, e := processIdentity(cmd.Process.Pid); e == nil {
		t.Fatal("zombie accepted as live startup identity")
	}
}

func TestRealCandidateStartup(t *testing.T) {
	digest := os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST")
	scenario := os.Getenv("OMEGAFLOW_STARTUP_SCENARIO")
	if digest == "" {
		t.Skip("explicit pinned Reploy candidate required")
	}
	for attempt := 0; attempt < 3; attempt++ {
		t.Run(strconv.Itoa(attempt), func(t *testing.T) {
			master, slave := openPTY(t)
			controlR, controlW, e := os.Pipe()
			if e != nil {
				t.Fatal(e)
			}
			resultR, resultW, e := os.Pipe()
			if e != nil {
				t.Fatal(e)
			}
			evidenceR, evidenceW, e := os.Pipe()
			if e != nil {
				t.Fatal(e)
			}
			defer controlR.Close()
			defer controlW.Close()
			defer resultR.Close()
			defer resultW.Close()
			defer evidenceR.Close()
			defer evidenceW.Close()
			bin, e := os.Executable()
			if e != nil {
				t.Fatal(e)
			}
			cmd := exec.Command(bin, "-test.run=^TestSupervisorProcess$")
			cmd.Env = []string{"PATH=/hostile/path", "HOME=/hostile/home", "PWD=/", "HISTFILE=", "INPUTRC=/omegaflow-runtime/etc/inputrc", "TERM=xterm-256color", "TERMINFO=/omegaflow-runtime/share/terminfo", "TERMINFO_DIRS=/omegaflow-runtime/share/terminfo", "LC_ALL=C.UTF-8", "LANG=C.UTF-8", "LOCPATH=/omegaflow-runtime/lib/locale", "OMEGAFLOW_TEST_CHILD=1", "OMEGAFLOW_TEST_DIGEST=" + digest, "OMEGAFLOW_TEST_SIGNALS=" + os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS"), "OMEGAFLOW_STARTUP_SCENARIO=" + os.Getenv("OMEGAFLOW_STARTUP_SCENARIO")}
			cmd.ExtraFiles = []*os.File{controlR, resultW, slave, evidenceW}
			in, _ := os.Open("/dev/null")
			out, _ := os.OpenFile("/dev/null", os.O_WRONLY, 0)
			defer in.Close()
			defer out.Close()
			cmd.Stdin = in
			cmd.Stdout = out
			cmd.Stderr = out
			var filler []byte
			if scenario == "blocked-result" || scenario == "release-during-ready" || scenario == "exit-during-ready" {
				fd := int(resultW.Fd())
				if e = syscall.SetNonblock(fd, true); e != nil {
					t.Fatal(e)
				}
				for {
					b := make([]byte, 4096)
					n, e := syscall.Write(fd, b)
					if n > 0 {
						filler = append(filler, b[:n]...)
					}
					if e == syscall.EAGAIN {
						break
					}
					if e != nil {
						t.Fatal(e)
					}
				}
			}
			if e = cmd.Start(); e != nil {
				t.Fatal(e)
			}
			defer func() { cmd.Process.Kill(); cmd.Wait() }()
			resultW.Close()
			evidenceW.Close()
			controlR.Close()
			slave.Close()
			output := make(chan []byte, 1)
			go func() {
				var b strings.Builder
				buf := make([]byte, 4096)
				for {
					n, e := master.Read(buf)
					b.Write(buf[:n])
					if e != nil {
						output <- []byte(b.String())
						return
					}
				}
			}()
			if scenario == "unexpected-signal" {
				until := time.Now().Add(time.Second)
				for {
					found := false
					tasks, _ := os.ReadDir(fmt.Sprintf("/proc/%d/task", cmd.Process.Pid))
					for _, task := range tasks {
						b, e := os.ReadFile(fmt.Sprintf("/proc/%d/task/%s/children", cmd.Process.Pid, task.Name()))
						if e == nil && len(strings.TrimSpace(string(b))) > 0 {
							found = true
							break
						}
					}
					if found {
						break
					}
					if time.Now().After(until) {
						t.Fatal("selected shell not created")
					}
					time.Sleep(time.Millisecond)
				}
				if e = cmd.Process.Signal(syscall.SIGUSR1); e != nil {
					t.Fatal(e)
				}
			}
			if scenario == "wrong-peer" {
				until := time.Now().Add(time.Second)
				for {
					c, e := net.DialUnix("unix", nil, &net.UnixAddr{Net: "unix", Name: "/run/omegaflow/session/bash/helper.sock"})
					if e == nil {
						c.Close()
						break
					}
					if time.Now().After(until) {
						t.Fatal("startup listener unavailable", e)
					}
					time.Sleep(time.Millisecond)
				}
			}
			if scenario == "blocked-result" || scenario == "release-during-ready" || scenario == "exit-during-ready" {
				if scenario != "blocked-result" {
					// Slave closure proves startup reached the blocked private-ready write.
					until := time.Now().Add(time.Second)
					for {
						_, err := os.Readlink(fmt.Sprintf("/proc/%d/fd/5", cmd.Process.Pid))
						if os.IsNotExist(err) {
							break
						}
						if time.Now().After(until) {
							t.Fatal("startup did not reach ready write")
						}
						time.Sleep(time.Millisecond)
					}
					if scenario == "release-during-ready" {
						e = cmd.Process.Signal(syscall.SIGUSR1)
					} else {
						group := int32(0)
						e = ioctl(int(master.Fd()), syscall.TIOCGPGRP, unsafe.Pointer(&group))
						if e == nil && group > 0 {
							e = syscall.Kill(int(group), syscall.SIGKILL)
						} else {
							t.Fatal("selected foreground group unavailable", e)
						}
					}
					if e != nil {
						t.Fatal(e)
					}
					// A correct monitor interrupts immediately, before the launch epoch.
					waited := make(chan error, 1)
					go func() { waited <- cmd.Wait() }()
					select {
					case e = <-waited:
					case <-time.After(200 * time.Millisecond):
						t.Fatal("fatal observation did not interrupt private ready")
					}
				} else {
					e = cmd.Wait()
				}
				exit, ok := e.(*exec.ExitError)
				if !ok || exit.ExitCode() != 32 {
					evidence, _ := io.ReadAll(evidenceR)
					t.Fatalf("blocked result cleanup %v: %s", e, evidence)
				}
				raw, e := io.ReadAll(resultR)
				if e != nil || !bytes.Equal(raw, filler) {
					t.Fatalf("ready leaked into blocked result %x %v", raw, e)
				}
				evidence, _ := io.ReadAll(evidenceR)
				t.Logf("blocked private write cleanup=%s", evidence)
				return
			}
			resultR.SetReadDeadline(time.Now().Add(12 * time.Second))
			m, e := protocol.ReadPrivate(bufio.NewReader(resultR), protocol.AwshToEnvoy)
			if scenario != "" {
				if e == nil {
					if diagnostic, ok := m.(*protocol.PrivateProtocolError); !ok || diagnostic.Code != "shell-launch" {
						t.Fatalf("unexpected failure result %#v", m)
					}
				}
				if e = cmd.Wait(); e == nil {
					t.Fatal("launch failure returned success")
				}
				exit, ok := e.(*exec.ExitError)
				if !ok || exit.ExitCode() != 32 {
					evidence, _ := io.ReadAll(evidenceR)
					t.Fatalf("cleanup failed %v: %s", e, evidence)
				}
				evidence, _ := io.ReadAll(evidenceR)
				t.Logf("fault=%s cleanup=%s", scenario, evidence)
				return
			}
			if e != nil {
				evidence, _ := io.ReadAll(evidenceR)
				t.Fatalf("private readiness: %v evidence=%s", e, evidence)
			}
			ready, ok := m.(*protocol.PrivateReady)
			if !ok {
				evidence, _ := io.ReadAll(evidenceR)
				t.Fatalf("result=%+v evidence=%s", m, evidence)
			}
			if ready.AwshPID != int64(cmd.Process.Pid) || ready.ShellPID <= 0 || ready.CWD != "/" {
				t.Fatalf("wrong readiness %+v", ready)
			}
			evidenceR.SetReadDeadline(time.Now().Add(time.Second))
			line, e := bufio.NewReader(evidenceR).ReadBytes('\n')
			if e != nil {
				t.Fatal(e)
			}
			t.Logf("candidate=%s ready=%+v evidence=%s", digest, ready, line)
			fds, e := os.ReadDir("/proc/" + strconv.FormatInt(ready.AwshPID, 10) + "/fd")
			if e != nil {
				t.Fatal(e)
			}
			for _, fd := range fds {
				target, _ := os.Readlink("/proc/" + strconv.FormatInt(ready.AwshPID, 10) + "/fd/" + fd.Name())
				if strings.HasPrefix(target, "/dev/pts/") {
					t.Fatalf("retained slave %s", target)
				}
			}
			controlW.Write([]byte{1})
			if e = cmd.Wait(); e != nil {
				t.Fatal(e)
			}
			select {
			case bytes := <-output:
				if len(regexp.MustCompile("\x1b\\[[0-?]*[ -/]*[@-~]").ReplaceAll(bytes, nil)) != 0 {
					t.Fatalf("prompt bytes %q", bytes)
				}
				t.Logf("startup bytes %x", bytes)
			case <-time.After(time.Second):
				t.Fatal("no PTY EOF after selected-shell cleanup")
			}
		})
	}
}
