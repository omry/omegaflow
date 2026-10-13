//go:build linux

package supervise

import (
	"bufio"
	"bytes"
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

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"github.com/omry/omegaflow/runtime/envoy/runtimebundle"
)

func nativePTY(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	m, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY|syscall.O_NONBLOCK, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Close() })
	raw, err := m.SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	var number uint32
	var unlocked int32
	if err := raw.Control(func(fd uintptr) {
		_, _, e := syscall.Syscall(syscall.SYS_IOCTL, fd, syscall.TIOCSPTLCK, uintptr(unsafe.Pointer(&unlocked)))
		if e == 0 {
			_, _, e = syscall.Syscall(syscall.SYS_IOCTL, fd, syscall.TIOCGPTN, uintptr(unsafe.Pointer(&number)))
		}
		if e != 0 {
			err = e
		}
	}); err != nil {
		t.Fatal(err)
	}
	if err != nil {
		t.Fatal(err)
	}
	s, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return m, s
}

func TestNativeRuntimeInputs(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_STARTUP") != "1" {
		t.Skip("native verified target only")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	inputs, err := runtimebundle.Load(ctx)
	if err != nil {
		t.Fatal("actual runtime inputs", err)
	}
	if _, err := bashbuild.LookupAwsh("/bin/bash", inputs.BashSHA256, inputs.Target, inputs.Adapter); err != nil {
		t.Fatal("actual qualified admission", err)
	}
}

// The runner supplies a separate read-only fault image. No production runtime
// path or admission override is available to this test or to Awsh.
func TestNativePreSessionFailure(t *testing.T) {
	kind := os.Getenv("OMEGAFLOW_NATIVE_PRESESSION_FAILURE")
	if kind == "" {
		t.Skip("native fault image only")
	}
	if os.Geteuid() == 0 || (kind != "manifest" && kind != "qualification") {
		t.Fatal("invalid native fault boundary")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	inputs, err := runtimebundle.Load(ctx)
	if kind == "manifest" && err == nil {
		t.Fatal("manifest fixture did not refuse runtime inputs")
	}
	if kind == "qualification" {
		if err != nil {
			t.Fatal("qualification fixture failed before admission", err)
		}
		if _, err := bashbuild.LookupAwsh("/bin/bash", inputs.BashSHA256, inputs.Target, inputs.Adapter); err == nil {
			t.Fatal("qualification fixture admitted the build")
		}
	}
	_, slave := nativePTY(t)
	controlRead, controlWrite, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer controlRead.Close()
	defer controlWrite.Close()
	resultRead, resultWrite, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer resultRead.Close()
	defer resultWrite.Close()
	input, err := os.OpenFile("/dev/null", os.O_RDONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	output, err := os.OpenFile("/dev/null", os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer output.Close()
	cmd := exec.Command("/omegaflow-runtime/bin/awsh", "supervise", "--control-fd=3", "--result-fd=4", "--pty-slave-fd=5", "--session-runtime-dir=/run/omegaflow/session")
	cmd.Stdin, cmd.Stdout, cmd.Stderr = input, output, output
	cmd.ExtraFiles = []*os.File{controlRead, resultWrite, slave}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	wait := make(chan error, 1)
	go func() { wait <- cmd.Wait() }()
	controlRead.Close()
	resultWrite.Close()
	slave.Close()
	deadline, _ := ctx.Deadline()
	if err := resultRead.SetReadDeadline(deadline); err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(resultRead)
	m, err := protocol.ReadPrivate(reader, protocol.AwshToEnvoy)
	report, ok := m.(*protocol.PrivateProtocolError)
	if err != nil || !ok || report.Code != "shell-launch" {
		t.Fatalf("pre-session %s failure omitted launch diagnostic: %v %v", kind, m, err)
	}
	if _, err := protocol.ReadPrivate(reader, protocol.AwshToEnvoy); err != io.EOF {
		t.Fatal("one diagnostic followed by private EOF required", err)
	}
	select {
	case err := <-wait:
		exit, ok := err.(*exec.ExitError)
		if !ok || exit.ExitCode() != 2 {
			t.Fatal("pre-session failure appeared orderly", err)
		}
	case <-ctx.Done():
		t.Fatal("pre-session failure did not exit", ctx.Err())
	}
	if _, err := os.Stat(filepath.Dir(helper.SocketPath)); !os.IsNotExist(err) {
		t.Fatal("pre-session failure created helper state", err)
	}
}

// This test executes the actual fixed qualified Awsh binary on the genuine
// target. Host runs skip it; the native runner supplies the verified payload.
func TestNativePrivateStartup(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_STARTUP") != "1" {
		t.Skip("native verified target only")
	}
	if os.Geteuid() == 0 {
		t.Fatal("native workload must be non-root")
	}
	for _, outcome := range []string{"shutdown", "control-eof", "early-eof", "invalid-private", "release-idle", "blocked-startup-failure", "cancelled-startup-failure"} {
		t.Run(outcome, func(t *testing.T) {
			if err := os.Mkdir("/run/omegaflow/session", 0700); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { os.RemoveAll("/run/omegaflow/session") })
			startupFailure := outcome == "blocked-startup-failure" || outcome == "cancelled-startup-failure"
			foreignHelperDir := filepath.Dir(helper.SocketPath)
			foreignMarker := filepath.Join(foreignHelperDir, "foreign-owner")
			if startupFailure {
				if err := os.Mkdir(foreignHelperDir, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(foreignMarker, []byte("pre-existing"), 0600); err != nil {
					t.Fatal(err)
				}
				// The test owns this fixture and removes it only after proving that
				// production cleanup preserved the foreign directory.
				t.Cleanup(func() {
					os.Remove(foreignMarker)
					os.Remove(foreignHelperDir)
				})
			}
			master, slave := nativePTY(t)
			controlRead, controlWrite, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer controlRead.Close()
			defer controlWrite.Close()
			resultRead, resultWrite, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer resultRead.Close()
			defer resultWrite.Close()
			input, err := os.OpenFile("/dev/null", os.O_RDONLY, 0)
			if err != nil {
				t.Fatal(err)
			}
			defer input.Close()
			output, err := os.OpenFile("/dev/null", os.O_WRONLY, 0)
			if err != nil {
				t.Fatal(err)
			}
			defer output.Close()
			var filler []byte
			if startupFailure {
				fd := int(resultWrite.Fd())
				if err := syscall.SetNonblock(fd, true); err != nil {
					t.Fatal(err)
				}
				for {
					b := make([]byte, 4096)
					n, err := syscall.Write(fd, b)
					if n > 0 {
						filler = append(filler, b[:n]...)
					}
					if err == syscall.EAGAIN {
						break
					}
					if err != nil {
						t.Fatal(err)
					}
				}
			}
			cmd := exec.Command("/omegaflow-runtime/bin/awsh", "supervise", "--control-fd=3", "--result-fd=4", "--pty-slave-fd=5", "--session-runtime-dir=/run/omegaflow/session")
			cmd.Stdin, cmd.Stdout, cmd.Stderr = input, output, output
			cmd.ExtraFiles = []*os.File{controlRead, resultWrite, slave}
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			started := time.Now()
			defer cmd.Process.Kill()
			wait := make(chan error, 1)
			go func() { wait <- cmd.Wait() }()
			controlRead.Close()
			resultWrite.Close()
			slave.Close()
			if startupFailure {
				if outcome == "cancelled-startup-failure" {
					// Leave the result reader open and give the child time to enter
					// the blocked diagnostic write before control EOF cancels it.
					time.Sleep(250 * time.Millisecond)
					if err := cmd.Process.Signal(syscall.Signal(0)); err != nil {
						t.Fatalf("startup failure exited before control EOF: %v", err)
					}
					if err := controlWrite.Close(); err != nil {
						t.Fatal(err)
					}
				}
				var waitErr error
				select {
				case waitErr = <-wait:
				case <-time.After(7 * time.Second):
					t.Fatal("controlled startup failure exceeded bounded diagnostic write")
				}
				if waitErr == nil {
					t.Fatal("controlled startup failure returned success")
				}
				exit, ok := waitErr.(*exec.ExitError)
				if !ok || exit.ExitCode() != 2 {
					t.Fatalf("unexpected controlled startup failure status: %v", waitErr)
				}
				elapsed := time.Since(started)
				if outcome == "blocked-startup-failure" {
					if elapsed < 4*time.Second || elapsed > 6*time.Second {
						t.Fatalf("diagnostic write did not retain its five-second bound: %v", elapsed)
					}
				} else if elapsed > 2*time.Second {
					t.Fatalf("control EOF did not interrupt diagnostic write promptly: %v", elapsed)
				}
				raw, err := io.ReadAll(resultRead)
				if err != nil || !bytes.Equal(raw, filler) {
					t.Fatalf("startup diagnostic leaked into blocked result: %x %v", raw, err)
				}
				if _, err := os.Stat(foreignHelperDir); err != nil {
					t.Fatalf("production cleanup removed foreign helper directory: %v", err)
				}
				marker, err := os.ReadFile(foreignMarker)
				if err != nil || string(marker) != "pre-existing" {
					t.Fatalf("foreign helper directory changed: %q %v", marker, err)
				}
				return
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			deadline, _ := ctx.Deadline()
			if err := resultRead.SetReadDeadline(deadline); err != nil {
				t.Fatal(err)
			}
			pumpDone := make(chan struct{})
			master.SetReadDeadline(deadline)
			go func() { defer close(pumpDone); io.Copy(io.Discard, master) }()
			defer func() { master.Close(); <-pumpDone }()
			if outcome == "early-eof" {
				controlWrite.Close()
				select {
				case err := <-wait:
					if err == nil {
						t.Fatal("pre-ready EOF appeared orderly")
					}
				case <-ctx.Done():
					t.Fatal("pre-ready cleanup failed")
				}
				return
			}
			reader := bufio.NewReader(resultRead)
			m, err := protocol.ReadPrivate(reader, protocol.AwshToEnvoy)
			if err != nil {
				t.Fatal("real private startup failed", err)
			}
			ready, ok := m.(*protocol.PrivateReady)
			if !ok || ready.AwshPID != int64(cmd.Process.Pid) {
				t.Fatal("private identity mismatch", m)
			}
			data, err := os.ReadFile("/proc/" + strconv.FormatInt(ready.ShellPID, 10) + "/stat")
			if err != nil {
				t.Fatal(err)
			}
			fields := strings.Fields(string(data)[strings.LastIndex(string(data), ")")+1:])
			if len(fields) < 4 || fields[1] != strconv.Itoa(cmd.Process.Pid) || fields[2] != strconv.FormatInt(ready.ShellPID, 10) || fields[3] != strconv.Itoa(cmd.Process.Pid) {
				t.Fatal("real child topology mismatch")
			}
			if outcome == "shutdown" {
				frame, err := protocol.EncodePrivate(protocol.PrivateShutdown{}, protocol.EnvoyToAwsh)
				if err != nil {
					t.Fatal(err)
				}
				if err := helper.WriteControl(ctx, controlWrite, frame); err != nil {
					t.Fatal(err)
				}
				m, err = protocol.ReadPrivate(reader, protocol.AwshToEnvoy)
				closed, ok := m.(*protocol.PrivateClosed)
				if err != nil || !ok || closed.CWD != ready.CWD {
					t.Fatal("real idle shutdown failed", m, err)
				}
				if _, err := protocol.ReadPrivate(reader, protocol.AwshToEnvoy); err != io.EOF {
					t.Fatal("missing private EOF", err)
				}
			} else if outcome == "invalid-private" {
				if _, err := controlWrite.Write([]byte("awsh-v1\x00unexpected\x00")); err != nil {
					t.Fatal(err)
				}
			} else if outcome == "release-idle" {
				if err := cmd.Process.Signal(syscall.SIGUSR1); err != nil {
					t.Fatal(err)
				}
			} else {
				controlWrite.Close()
			}
			select {
			case err := <-wait:
				if outcome == "shutdown" && err != nil {
					t.Fatal(err)
				}
				if outcome != "shutdown" && err == nil {
					t.Fatal("fatal private boundary appeared orderly")
				}
			case <-ctx.Done():
				t.Fatal("real Awsh reap failed")
			}
			if _, err := os.Stat("/proc/" + strconv.FormatInt(ready.ShellPID, 10)); !os.IsNotExist(err) {
				t.Fatal("selected shell was not reaped", err)
			}
			if _, err := os.Stat(filepath.Dir(helper.SocketPath)); !os.IsNotExist(err) {
				t.Fatal("helper directory retained", err)
			}
			if err := os.Remove("/run/omegaflow/session"); err != nil {
				t.Fatal(err)
			}
		})
	}
}
