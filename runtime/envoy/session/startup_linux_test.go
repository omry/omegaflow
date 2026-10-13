//go:build linux

package session

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Delay only Wait's publication, after the real owned child has been reaped.
// This makes the kill-versus-reap crossing deterministic without a fake PID.
func delayedWaitWorkload(t *testing.T) (*Workload, func(), <-chan struct{}) {
	t.Helper()
	command := exec.Command("/bin/sleep", "30")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	w := &Workload{command: command, reaped: make(chan struct{})}
	gate, waited := make(chan struct{}), make(chan struct{})
	var once sync.Once
	release := func() { once.Do(func() { close(gate) }) }
	go func() {
		w.waitErr = command.Wait()
		close(waited)
		<-gate
		close(w.reaped)
	}()
	t.Cleanup(func() {
		release()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = w.Close(ctx)
	})
	return w, release, waited
}

func TestCleanupTimeoutRetainsReapJoin(t *testing.T) {
	w, release, waited := delayedWaitWorkload(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	if err := w.Close(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("missing original cleanup failure: %v", err)
	}
	if time.Since(started) > time.Second {
		t.Fatal("cleanup extended its expired caller context")
	}
	select {
	case <-waited:
	case <-time.After(time.Second):
		t.Fatal("killed child was not reaped by Wait")
	}
	join, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	done := make(chan error, 1)
	go func() { done <- w.Close(join) }()
	select {
	case err := <-done:
		t.Fatalf("later cleanup skipped outstanding reap publication: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	release()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("original fatal cleanup failure was cleared: %v", err)
		}
	case <-join.Done():
		t.Fatal("later cleanup did not join published reap")
	}
}

func testPTY(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	m, s, err := openPTY(80, 24)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Close(); s.Close() })
	return m, s
}

func TestStartupPTYBoundsAndExactBytes(t *testing.T) {
	for _, size := range []int{0, 4096, 4097} {
		for _, mismatch := range []bool{false, true} {
			t.Run(fmtSize(size, mismatch), func(t *testing.T) {
				m, s := testPTY(t)
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				p, err := pumpStartup(ctx, m, func() {})
				if err != nil {
					t.Fatal(err)
				}
				data := bytes.Repeat([]byte("x"), size)
				if n, err := s.Write(data); err != nil || n != size {
					t.Fatal(n, err)
				}
				expected := data
				if mismatch {
					expected = append(append([]byte(nil), data...), 'y')
				}
				got, err := p.finish(ctx, m, expected)
				if (size > 4096 || mismatch) != (err != nil) {
					t.Fatal("boundary mismatch", size, mismatch, err)
				}
				if err == nil && !bytes.Equal(got, data) {
					t.Fatal("changed bytes")
				}
				raw, err := m.SyscallConn()
				if err != nil {
					t.Fatal(err)
				}
				var flags uintptr
				if err := raw.Control(func(fd uintptr) { flags, _, _ = syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_GETFL, 0) }); err != nil {
					t.Fatal(err)
				}
				if flags&syscall.O_NONBLOCK == 0 {
					t.Fatal("drain converted pollable master to blocking")
				}
			})
		}
	}
	for _, size := range []int{8, 4096} {
		t.Run(fmt.Sprintf("%d/same-length-content-mismatch", size), func(t *testing.T) {
			m, s := testPTY(t)
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			p, err := pumpStartup(ctx, m, func() {})
			if err != nil {
				t.Fatal(err)
			}
			observed := bytes.Repeat([]byte("x"), size)
			expected := append([]byte(nil), observed...)
			expected[size-1] = 'y'
			if n, err := s.Write(observed); err != nil || n != size {
				t.Fatal(n, err)
			}
			if _, err := p.finish(ctx, m, expected); err == nil {
				t.Fatal("accepted different startup bytes with equal length")
			}
			if len(p.data) != size {
				t.Fatal("fixture did not establish equal byte counts")
			}
		})
	}
}

func fmtSize(size int, mismatch bool) string {
	if mismatch {
		return fmt.Sprintf("%d/mismatch", size)
	}
	return fmt.Sprintf("%d/exact", size)
}

func TestStartupEOFAndOriginalDeadline(t *testing.T) {
	for _, eof := range []bool{false, true} {
		t.Run(fmt.Sprintf("eof=%v", eof), func(t *testing.T) {
			m, s := testPTY(t)
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
			defer cancel()
			p, err := pumpStartup(ctx, m, func() {})
			if err != nil {
				t.Fatal(err)
			}
			if eof {
				s.Close()
			}
			select {
			case <-p.done:
			case <-time.After(time.Second):
				t.Fatal("startup reader retained")
			}
			_, err = p.finish(ctx, m, nil)
			if err == nil {
				t.Fatal("startup EOF/timeout accepted")
			}
			if !eof && !errors.Is(err, context.DeadlineExceeded) {
				t.Fatal("original timeout cause lost", err)
			}
			if eof && !errors.Is(err, syscall.EIO) && !errors.Is(err, io.EOF) {
				t.Fatal("native EOF cause lost", err)
			}
		})
	}
}

func TestJoinedCancellationCannotOutlivePhase(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	entered, release, joined := make(chan struct{}), make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	stop := joinedAfterFunc(ctx, func() { calls.Add(1); close(entered); <-release })
	cancel()
	<-entered
	go func() { stop(); close(joined) }()
	select {
	case <-joined:
		t.Fatal("callback not joined")
	case <-time.After(10 * time.Millisecond):
	}
	close(release)
	<-joined
	stop()
	if calls.Load() != 1 {
		t.Fatal("callback repeated")
	}
	ctx, cancel = context.WithCancel(context.Background())
	stop = joinedAfterFunc(ctx, func() { calls.Add(1) })
	stop()
	cancel()
	stop()
	if calls.Load() != 1 {
		t.Fatal("stopped phase altered reusable resource")
	}
}

func TestLaunchEnvironmentReservationsAndDelegation(t *testing.T) {
	// Independent protocol fixture, including every reserved family and values
	// consumed by the loader before Bash can inspect its own flags.
	reserved := strings.Fields("AWSH_BASH BASH_COMPAT BASHOPTS BASH_ENV BASH_XTRACEFD CDPATH ENV GLOBIGNORE HISTFILE INPUTRC LANG LANGUAGE LOCPATH MAIL MAILCHECK MAILPATH POSIXLY_CORRECT PROMPT_COMMAND PS0 PS1 PS2 PS3 PS4 SHELLOPTS TERM TERMINFO TERMINFO_DIRS TMOUT BASH_FUNC_probe%% LD_PRELOAD LC_CTYPE AWSH_OVERRIDE")
	inherited := []string{"PATH=/application/bin", "HOME=/application/home", "VALUE=a=b"}
	for _, name := range reserved {
		inherited = append(inherited, name+"=/hostile")
	}
	got := workloadEnvironment(inherited)
	values := map[string]string{}
	for _, value := range got {
		key, v, _ := strings.Cut(value, "=")
		if _, duplicate := values[key]; duplicate {
			t.Fatal("duplicate environment entry", key)
		}
		values[key] = v
	}
	wanted := map[string]string{"PATH": "/application/bin", "HOME": "/application/home", "VALUE": "a=b", "HISTFILE": "", "INPUTRC": "/omegaflow-runtime/etc/inputrc", "TERM": "xterm-256color", "TERMINFO": "/omegaflow-runtime/share/terminfo", "TERMINFO_DIRS": "/omegaflow-runtime/share/terminfo", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "LOCPATH": "/omegaflow-runtime/lib/locale"}
	if !reflect.DeepEqual(values, wanted) {
		t.Fatal("launch environment violates reservations or drops application values", values)
	}
}

func TestExpectedStartupBytesCanArriveAfterPrivateReady(t *testing.T) {
	m, s := testPTY(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	p, err := pumpStartup(ctx, m, func() {})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	expected := []byte("late-startup")
	go func() {
		data, err := p.finish(ctx, m, expected)
		if err == nil && !bytes.Equal(data, expected) {
			err = fmt.Errorf("changed startup bytes")
		}
		done <- err
	}()
	select {
	case err := <-done:
		t.Fatal("stopped before expected bytes arrived", err)
	case <-time.After(10 * time.Millisecond):
	}
	if _, err := s.Write(expected); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal("did not collect under original epoch", err)
	}
}

func TestLaunchRefusalClosesSelectedPublicPair(t *testing.T) {
	for _, dimensions := range [][2]int64{{0, 24}, {80, 1001}, {80, 24}} {
		s, terminal, telemetry, ctx := established(t)
		if dimensions == [2]int64{80, 24} {
			ctx = context.Background()
		}
		w, err := s.Launch(ctx, dimensions[0], dimensions[1])
		if w != nil || err == nil {
			t.Fatal("invalid launch admitted", w, err)
		}
		fatal(t, s)
		telemetry.SetReadDeadline(time.Now().Add(time.Second))
		line, err := readLine(bufio.NewReader(telemetry))
		if err != nil {
			t.Fatal("bounded launch diagnostic missing", err)
		}
		m, err := protocol.DecodeEnvoy(line)
		d, ok := m.(*protocol.Diagnostic)
		if err != nil || !ok || d.Severity != "fatal" || d.Code != "shell-launch" {
			t.Fatal(m, err)
		}
		terminal.SetReadDeadline(time.Now().Add(time.Second))
		if n, err := terminal.Read(make([]byte, 1)); n != 0 || err != io.EOF {
			t.Fatal("terminal pair retained", n, err)
		}
	}
}

func TestFinalPTYDrainPreservesBytesAndOffset(t *testing.T) {
	s, terminal, _, _ := established(t)
	m, slave := testPTY(t)
	data := bytes.Repeat([]byte("final"), 800)
	if n, err := slave.Write(data); err != nil || n != len(data) {
		t.Fatal(n, err)
	}
	slave.Close()
	w := &Workload{PTY: m, channels: s, OutputThrough: 7}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	deadline, _ := ctx.Deadline()
	m.SetReadDeadline(deadline)
	got := make(chan []byte, 1)
	go func() { b := make([]byte, len(data)); _, _ = io.ReadFull(terminal, b); got <- b }()
	if err := w.drainTerminal(ctx); err != nil {
		t.Fatal(err)
	}
	if w.OutputThrough != 7+int64(len(data)) || !bytes.Equal(<-got, data) {
		t.Fatal("final exclusive offset or bytes lost")
	}
}
