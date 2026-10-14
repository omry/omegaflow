//go:build linux

package session

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"github.com/omry/omegaflow/runtime/envoy/runtimebundle"
)

func nativePair(t *testing.T) (handshakeResult, *net.TCPConn, *net.TCPConn) {
	t.Helper()
	l := listeners(t)
	a, b := l.Addresses()
	done := begin(l, context.Background(), testID)
	terminal, telemetry := dial(t, a), dial(t, b)
	if _, err := telemetry.Write(hello(t, testID, 1)); err != nil {
		t.Fatal(err)
	}
	r := receive(t, done)
	if r.err != nil {
		t.Fatal(r.err)
	}
	return r, terminal, telemetry
}

func nativeFrame(t *testing.T, reader *bufio.Reader) protocol.Message {
	t.Helper()
	b, err := readLine(reader)
	if err != nil {
		t.Fatal("public frame", err)
	}
	m, err := protocol.DecodeEnvoy(b)
	if err != nil {
		t.Fatal("public frame", err)
	}
	return m
}

func TestNativePublicCleanupTimeout(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_STARTUP") != "1" {
		t.Skip("isolated non-root runtime only")
	}
	if err := os.Mkdir(sessionRuntimeDir, 0700); err != nil {
		t.Fatal(err)
	}
	w, release, waited := delayedWaitWorkload(t)
	w.dirCreated = true
	marker := filepath.Join(sessionRuntimeDir, "owned-cleanup-proof")
	if err := os.WriteFile(marker, []byte("retained until reap"), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	s := &Channels{}
	if err := s.disposeFailedLaunch(ctx, w); !errors.Is(err, context.Canceled) {
		t.Fatalf("missing bounded fatal timeout: %v", err)
	}
	w = nil
	owner := s.FailedWorkload()
	if owner == nil {
		t.Fatal("failed launch lost its session-state cleanup owner")
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("session state removed before direct-child reap publication: %v", err)
	}
	select {
	case <-waited:
	case <-time.After(time.Second):
		t.Fatal("owned child did not reach Wait")
	}
	release()
	join, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := owner.Close(join); !errors.Is(err, context.Canceled) {
		t.Fatalf("original failure must remain fatal after recovery: %v", err)
	}
	if _, err := os.Stat(sessionRuntimeDir); !os.IsNotExist(err) {
		t.Fatalf("reaped owned session state remains: %v", err)
	}
}

// Host tests do not substitute a shell. The runner installs the actual fixed
// qualified Awsh and invokes the complete public transaction on its real target.
func TestNativePublicStartup(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_STARTUP") != "1" {
		t.Skip("genuine qualified target only")
	}
	if os.Geteuid() == 0 {
		t.Fatal("non-root target required")
	}
	for _, outcome := range []string{"idle-shutdown", "dispose-launch-context", "immediate-controller-response"} {
		t.Run(outcome, func(t *testing.T) {
			r, terminal, telemetry := nativePair(t)
			inputs, err := runtimebundle.Load(r.ctx)
			if err != nil {
				t.Fatal(err)
			}
			entry, err := bashbuild.LookupEnvoy("/bin/bash", inputs.BashSHA256, inputs.Target, inputs.Adapter)
			if err != nil {
				t.Fatal(err)
			}
			var proof struct {
				Result struct {
					Measurements struct {
						Startup string `json:"startup_pty_base64"`
					} `json:"measurements"`
				} `json:"result"`
			}
			if err := json.Unmarshal(entry.Qualification, &proof); err != nil {
				t.Fatal(err)
			}
			expected, err := base64.StdEncoding.DecodeString(proof.Result.Measurements.Startup)
			if err != nil {
				t.Fatal(err)
			}
			deadline, _ := r.ctx.Deadline()
			telemetry.SetReadDeadline(deadline)
			terminal.SetReadDeadline(deadline)
			type launched struct {
				w   *Workload
				err error
			}
			launchedCh := make(chan launched, 1)
			go func() { w, err := r.s.Launch(r.ctx, 80, 24); launchedCh <- launched{w, err} }()
			reader := bufio.NewReader(telemetry)
			first := nativeFrame(t, reader)
			ready, ok := first.(*protocol.Ready)
			if !ok {
				select {
				case result := <-launchedCh:
					t.Fatal("actual startup refused", first, result.err)
				case <-r.ctx.Done():
					t.Fatal("startup refusal retained launch", first, r.ctx.Err())
				}
			}
			if !ok || ready.Seq != 1 || ready.OutputThrough != int64(len(expected)) || ready.EnvoyPID != int64(os.Getpid()) || ready.Columns != 80 || ready.Rows != 24 || ready.ElapsedUS != 0 {
				t.Fatal("invalid actual public readiness", ready)
			}
			shutdown := protocol.Shutdown{Header: protocol.Header{Schema: protocol.Schema, Type: "shutdown", Seq: 2}, Reason: "requested-shutdown"}
			if outcome == "immediate-controller-response" {
				b, err := protocol.EncodeController(shutdown)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = telemetry.Write(b); err != nil {
					t.Fatal(err)
				}
			}
			got := make([]byte, len(expected))
			if _, err := io.ReadFull(terminal, got); err != nil || !bytes.Equal(got, expected) {
				t.Fatal("startup byte barrier", err)
			}
			var w *Workload
			select {
			case result := <-launchedCh:
				if result.err != nil {
					t.Fatal("actual launch", result.err)
				}
				w = result.w
			case <-r.ctx.Done():
				t.Fatal("original launch deadline", r.ctx.Err())
			}
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), finalDrainBudget)
				defer cancel()
				w.Close(ctx)
			})
			if ready.ShellPID != w.ShellPID || ready.Cwd != w.CWD || w.ReadyAt.IsZero() {
				t.Fatal("public/private identity mismatch")
			}
			if err := w.verifyIdentity(); err != nil {
				t.Fatal(err)
			}
			// Connected public sockets and the PTY master must be absent from
			// the direct Awsh exec and its selected shell.
			for _, file := range []syscall.Conn{r.s.terminal, r.s.telemetry, w.PTY} {
				raw, err := file.SyscallConn()
				if err != nil {
					t.Fatal(err)
				}
				var target string
				if err := raw.Control(func(fd uintptr) { target, err = os.Readlink("/proc/self/fd/" + strconv.Itoa(int(fd))) }); err != nil {
					t.Fatal(err)
				}
				if err != nil {
					t.Fatal(err)
				}
				for _, pid := range []int64{int64(w.command.Process.Pid), w.ShellPID} {
					fds, err := os.ReadDir("/proc/" + strconv.FormatInt(pid, 10) + "/fd")
					if err != nil {
						t.Fatal(err)
					}
					for _, fd := range fds {
						value, _ := os.Readlink("/proc/" + strconv.FormatInt(pid, 10) + "/fd/" + fd.Name())
						if value == target {
							t.Fatal("Envoy descriptor inherited", pid, target)
						}
					}
				}
			}
			if outcome == "dispose-launch-context" {
				r.cancel()
				if err := w.verifyIdentity(); err != nil {
					t.Fatal("completed phase disposed ready shell", err)
				}
			}
			if outcome == "immediate-controller-response" {
				select {
				case event := <-r.s.Events():
					m, ok := event.Message.(*protocol.Shutdown)
					if !ok || m.Reason != shutdown.Reason {
						t.Fatal("lost immediate response", event)
					}
				case <-r.s.Done():
					t.Fatal("readiness response classified as early", r.s.Err())
				case <-time.After(time.Second):
					t.Fatal("immediate response not released")
				}
			}
			finished := make(chan error, 1)
			go func() { finished <- w.ShutdownIdle(shutdown.Reason) }()
			draining, ok := nativeFrame(t, reader).(*protocol.Draining)
			if !ok || draining.Seq != 2 || draining.Reason != shutdown.Reason {
				t.Fatal("invalid draining", draining)
			}
			tail, err := io.ReadAll(terminal)
			if err != nil {
				t.Fatal("terminal EOF", err)
			}
			closed, ok := nativeFrame(t, reader).(*protocol.Closed)
			if !ok || closed.Seq != 3 || closed.Reason != shutdown.Reason || closed.OutputThrough != int64(len(expected)+len(tail)) || draining.OutputThrough != closed.OutputThrough {
				t.Fatal("invalid closure boundary", closed)
			}
			if err := <-finished; err != nil {
				t.Fatal("actual idle shutdown", err)
			}
			if _, err := os.Stat("/proc/" + strconv.FormatInt(w.ShellPID, 10)); !os.IsNotExist(err) {
				t.Fatal("selected shell not reaped", err)
			}
			for _, path := range []string{sessionRuntimeDir, filepath.Dir(helper.SocketPath)} {
				if _, err := os.Stat(path); !os.IsNotExist(err) {
					t.Fatal("owned private path retained", path, err)
				}
			}
		})
	}
}

func TestNativePublicStartupRefusal(t *testing.T) {
	kind := os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_FAILURE")
	if kind == "" {
		t.Skip("genuine target refusal only")
	}
	if kind != "manifest" && kind != "qualification" {
		t.Fatal("unknown refusal fixture")
	}
	r, terminal, telemetry := nativePair(t)
	w, err := r.s.Launch(r.ctx, 80, 24)
	if err == nil || w != nil {
		t.Fatal("invalid fixed input admitted", w, err)
	}
	fatal(t, r.s)
	telemetry.SetReadDeadline(time.Now().Add(time.Second))
	m, ok := nativeFrame(t, bufio.NewReader(telemetry)).(*protocol.Diagnostic)
	if !ok || m.Code != "shell-launch" || m.Severity != "fatal" {
		t.Fatal("missing public startup refusal", m)
	}
	terminal.SetReadDeadline(time.Now().Add(time.Second))
	if n, err := terminal.Read(make([]byte, 1)); n != 0 || err != io.EOF {
		t.Fatal("startup terminal leak", n, err)
	}
	if _, err := os.Stat(sessionRuntimeDir); !os.IsNotExist(err) {
		t.Fatal("pre-allocation refusal created private path", err)
	}
}

func TestNativePublicStartupExpiredEpoch(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_STARTUP") != "1" {
		t.Skip("genuine target only")
	}
	r, _, telemetry := nativePair(t)
	ctx, cancel := context.WithDeadline(r.ctx, time.Now().Add(-time.Second))
	defer cancel()
	w, err := r.s.Launch(ctx, 80, 24)
	if w != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("expired epoch restarted", w, err)
	}
	telemetry.SetReadDeadline(time.Now().Add(time.Second))
	m, ok := nativeFrame(t, bufio.NewReader(telemetry)).(*protocol.Diagnostic)
	if !ok || m.Code != "shell-launch-timeout" {
		t.Fatal("timeout cause lost", m)
	}
}

func TestNativePublicStartupForeignDirectory(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_STARTUP") != "1" {
		t.Skip("genuine target only")
	}
	dir := sessionRuntimeDir
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(dir, "foreign-owner")
	if err := os.WriteFile(marker, []byte("preserved"), 0600); err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	r, _, telemetry := nativePair(t)
	w, err := r.s.Launch(r.ctx, 80, 24)
	if w != nil || err == nil {
		t.Fatal("foreign session path collision admitted", w, err)
	}
	telemetry.SetReadDeadline(time.Now().Add(time.Second))
	m, ok := nativeFrame(t, bufio.NewReader(telemetry)).(*protocol.Diagnostic)
	if !ok || m.Code != "shell-launch" {
		t.Fatal("foreign session path diagnostic", m)
	}
	data, err := os.ReadFile(marker)
	if err != nil || string(data) != "preserved" {
		t.Fatal("foreign session path removed", err)
	}
	assertNoNativeChildren(t)
}

func assertNoNativeChildren(t *testing.T) {
	t.Helper()
	tasks, err := os.ReadDir("/proc/self/task")
	if err != nil {
		t.Fatal(err)
	}
	for _, task := range tasks {
		data, err := os.ReadFile("/proc/self/task/" + task.Name() + "/children")
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		if len(bytes.TrimSpace(data)) != 0 {
			t.Fatal("direct Awsh child retained", string(data))
		}
	}
}

func TestNativePublicStartupSelectedLoss(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_STARTUP") != "1" {
		t.Skip("genuine target only")
	}
	for attempt := 0; attempt < 8; attempt++ {
		r, terminal, _ := nativePair(t)
		done := make(chan error, 1)
		go func() {
			w, err := r.s.Launch(r.ctx, 80, 24)
			if w != nil {
				ctx, cancel := context.WithTimeout(context.Background(), finalDrainBudget)
				defer cancel()
				err = errors.Join(err, w.Close(ctx))
			}
			done <- err
		}()
		// An actual path-creation boundary ensures resources have been claimed.
		for {
			if _, err := os.Stat(sessionRuntimeDir); err == nil {
				break
			}
			select {
			case err := <-done:
				t.Fatal("launch ended before loss fixture", err)
			default:
			}
			if r.ctx.Err() != nil {
				t.Fatal(r.ctx.Err())
			}
			runtime.Gosched()
		}
		terminal.Close()
		select {
		case <-done:
		case <-time.After(6 * time.Second):
			t.Fatal("selected loss retained startup")
		}
		fatal(t, r.s)
		if _, err := os.Stat(sessionRuntimeDir); !os.IsNotExist(err) {
			t.Fatal("selected loss retained owned path", err)
		}
		assertNoNativeChildren(t)
	}
}

func TestNativePublicShutdownSelectedLoss(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_PUBLIC_STARTUP") != "1" {
		t.Skip("genuine target only")
	}
	for attempt := 0; attempt < 8; attempt++ {
		r, terminal, _ := nativePair(t)
		w, err := r.s.Launch(r.ctx, 80, 24)
		if err != nil {
			t.Fatal(err)
		}
		done := make(chan error, 1)
		go func() { done <- w.ShutdownIdle("requested-shutdown") }()
		terminal.Close()
		select {
		case <-done:
		case <-time.After(6 * time.Second):
			t.Fatal("channel loss restarted or retained final drain")
		}
		if _, err := os.Stat("/proc/" + strconv.FormatInt(w.ShellPID, 10)); !os.IsNotExist(err) {
			t.Fatal("channel loss killed Awsh before its shell cleanup", err)
		}
		if _, err := os.Stat(sessionRuntimeDir); !os.IsNotExist(err) {
			t.Fatal("shutdown loss retained owned path", err)
		}
		assertNoNativeChildren(t)
	}
}
