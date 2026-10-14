//go:build linux

package session

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"github.com/omry/omegaflow/runtime/envoy/runtimebundle"
)

const sessionRuntimeDir = "/run/omegaflow/session"
const finalDrainBudget = 5 * time.Second

// Workload owns the master and Envoy's private pipe ends. Operation and
// descendant lifecycle coordination remain with their subsequent owners.
type Workload struct {
	PTY, Control, Result *os.File
	Reader               *bufio.Reader
	ShellPID             int64
	CWD                  string
	ReadyAt              time.Time
	OutputThrough        int64
	command              *exec.Cmd
	reaped               chan struct{}
	waitErr              error
	channels             *Channels
	dirCreated           bool
	closeOnce            sync.Once
	closeMu              sync.Mutex
	closeErr             error
	relayOwned           atomic.Bool
	pendingPTY           []byte // at most one relay chunk, transferred after join
}

// stop joins a started callback, so a completed phase cannot affect reused I/O.
func joinedAfterFunc(ctx context.Context, f func()) func() {
	done := make(chan struct{})
	stop := context.AfterFunc(ctx, func() { defer close(done); f() })
	var once sync.Once
	return func() {
		once.Do(func() {
			if !stop() {
				<-done
			}
		})
	}
}

func ptyIOCTL(file *os.File, request uintptr, value unsafe.Pointer) error {
	raw, err := file.SyscallConn()
	if err != nil {
		return err
	}
	var native error
	err = raw.Control(func(fd uintptr) {
		_, _, e := syscall.Syscall(syscall.SYS_IOCTL, fd, request, uintptr(value))
		if e != 0 {
			native = e
		}
	})
	return errors.Join(err, native)
}

func openPTY(columns, rows int64) (master, slave *os.File, err error) {
	master, err = os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
	if err != nil {
		return
	}
	defer func() {
		if err != nil {
			master.Close()
		}
	}()
	var unlocked int32
	var number uint32
	if err = ptyIOCTL(master, syscall.TIOCSPTLCK, unsafe.Pointer(&unlocked)); err != nil {
		return
	}
	if err = ptyIOCTL(master, syscall.TIOCGPTN, unsafe.Pointer(&number)); err != nil {
		return
	}
	size := [4]uint16{uint16(rows), uint16(columns), 0, 0}
	if err = ptyIOCTL(master, syscall.TIOCSWINSZ, unsafe.Pointer(&size)); err != nil {
		return
	}
	slave, err = os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	return
}

type startupPump struct {
	mu       sync.Mutex
	data     []byte
	err      error
	stop     atomic.Bool
	done     chan struct{}
	progress chan struct{}
}

func (p *startupPump) append(data []byte) error {
	if len(data) > 4096-len(p.data) {
		return fmt.Errorf("startup output exceeds 4096 bytes")
	}
	p.data = append(p.data, data...)
	return nil
}

func pumpStartup(ctx context.Context, master *os.File, fail func()) (*startupPump, error) {
	deadline, ok := ctx.Deadline()
	if !ok {
		return nil, fmt.Errorf("missing launch deadline")
	}
	if err := master.SetReadDeadline(deadline); err != nil {
		return nil, err
	}
	p := &startupPump{data: make([]byte, 0, 4096), done: make(chan struct{}), progress: make(chan struct{}, 1)}
	go func() {
		defer close(p.done)
		var buffer [4097]byte
		for {
			n, err := master.Read(buffer[:])
			p.mu.Lock()
			p.err = p.append(buffer[:n])
			if p.err == nil && err != nil && !(p.stop.Load() && os.IsTimeout(err)) {
				p.err = err
			}
			failed := p.err != nil
			p.mu.Unlock()
			select {
			case p.progress <- struct{}{}:
			default:
			}
			if failed {
				fail()
				return
			}
			if p.stop.Load() || err != nil {
				return
			}
		}
	}()
	return p, nil
}

func (p *startupPump) join(master *os.File) error {
	p.stop.Store(true)
	err := master.SetReadDeadline(time.Now())
	if err != nil {
		master.Close()
	}
	<-p.done
	return err
}

// Drain without File.Fd: that method would make a pollable master blocking.
func (p *startupPump) finish(ctx context.Context, master *os.File, expected []byte) ([]byte, error) {
	// Private ready proves terminal state, not that the kernel has delivered
	// every measured startup byte. Keep collecting under the original epoch.
	for {
		p.mu.Lock()
		complete := len(p.data) >= len(expected) || p.err != nil
		p.mu.Unlock()
		if complete {
			break
		}
		select {
		case <-p.progress:
		case <-p.done:
		case <-ctx.Done():
			joinErr := p.join(master)
			return nil, errors.Join(ctx.Err(), joinErr, p.err)
		}
	}
	if err := p.join(master); err != nil {
		return nil, err
	}
	if err := master.SetReadDeadline(time.Time{}); err != nil {
		return nil, err
	}
	if p.err != nil {
		return nil, helper.WithContextCause(ctx, p.err)
	}
	raw, err := master.SyscallConn()
	if err != nil {
		return nil, err
	}
	var native error
	err = raw.Control(func(fd uintptr) {
		var buffer [4097]byte
		for {
			if native = ctx.Err(); native != nil {
				return
			}
			n, e := syscall.Read(int(fd), buffer[:])
			if e == syscall.EINTR {
				continue
			}
			if e == syscall.EAGAIN {
				return
			}
			if n > 0 {
				native = p.append(buffer[:n])
				if native != nil {
					return
				}
			}
			if e != nil {
				native = e
				return
			}
			if n == 0 {
				native = io.ErrUnexpectedEOF
				return
			}
		}
	})
	if err = errors.Join(err, native); err != nil {
		return nil, helper.WithContextCause(ctx, err)
	}
	if !bytes.Equal(p.data, expected) {
		return nil, fmt.Errorf("startup bytes differ from qualification: observed %d bytes, expected %d", len(p.data), len(expected))
	}
	return p.data, nil
}

// Launch uses the original Handshake deadline, including public publication.
func (s *Channels) Launch(original context.Context, columns, rows int64) (w *Workload, err error) {
	code := "shell-launch"
	privateFailure := false
	var pump *startupPump
	ctx, cancel := context.WithCancel(original)
	stopSelected := joinedAfterFunc(s.ctx, cancel)
	var stopIO func()
	defer func() {
		stopSelected()
		if stopIO != nil {
			stopIO()
		}
		if pump != nil {
			joinErr := pump.join(w.PTY)
			err = errors.Join(err, joinErr, helper.WithContextCause(original, pump.err))
			if pump.err != nil && !privateFailure {
				code = "shell-launch-output"
			}
		}
		cancel()
		if err != nil {
			if errors.Is(err, context.DeadlineExceeded) {
				code = "shell-launch-timeout"
			}
			phase, end := context.WithTimeout(context.Background(), finalDrainBudget)
			defer end()
			if w != nil {
				err = errors.Join(err, s.disposeFailedLaunch(phase, w))
				w = nil
			}
			_ = s.Send(phase, protocol.Diagnostic{Header: s.header("diagnostic"), Severity: "fatal", Code: code, Message: "Selected shell startup failed"})
			s.Close()
		}
	}()
	deadline, ok := ctx.Deadline()
	if !ok || columns < 1 || columns > 1000 || rows < 1 || rows > 1000 || os.Geteuid() == 0 {
		return nil, fmt.Errorf("invalid qualified workload launch boundary")
	}
	if err = ctx.Err(); err != nil {
		return
	}
	inputs, err := runtimebundle.Load(ctx)
	if err != nil {
		return
	}
	entry, err := bashbuild.LookupEnvoy("/bin/bash", inputs.BashSHA256, inputs.Target, inputs.Adapter)
	if err != nil {
		return
	}
	var proof struct {
		Result struct {
			Measurements struct {
				Startup string `json:"startup_pty_base64"`
			} `json:"measurements"`
		} `json:"result"`
	}
	if err = json.Unmarshal(entry.Qualification, &proof); err != nil {
		return
	}
	expected, err := base64.StdEncoding.DecodeString(proof.Result.Measurements.Startup)
	if err != nil || len(expected) > 4096 {
		return nil, fmt.Errorf("invalid qualified startup bytes")
	}
	w = &Workload{channels: s, reaped: make(chan struct{})}
	if err = os.Mkdir(sessionRuntimeDir, 0700); err != nil {
		return
	}
	w.dirCreated = true
	var slave, readControl, writeResult *os.File
	w.PTY, slave, err = openPTY(columns, rows)
	if err != nil {
		return
	}
	defer slave.Close()
	readControl, w.Control, err = os.Pipe()
	if err != nil {
		return
	}
	defer readControl.Close()
	w.Result, writeResult, err = os.Pipe()
	if err != nil {
		return
	}
	defer writeResult.Close()
	input, err := os.OpenFile("/dev/null", os.O_RDONLY, 0)
	if err != nil {
		return
	}
	defer input.Close()
	output, err := os.OpenFile("/dev/null", os.O_WRONLY, 0)
	if err != nil {
		return
	}
	defer output.Close()
	w.command = exec.Command(runtimebundle.Root+"/bin/awsh", "supervise", "--control-fd=3", "--result-fd=4", "--pty-slave-fd=5", "--session-runtime-dir="+sessionRuntimeDir)
	w.command.Env = workloadEnvironment(os.Environ())
	w.command.Stdin, w.command.Stdout, w.command.Stderr = input, output, output
	w.command.ExtraFiles = []*os.File{readControl, writeResult, slave}
	if err = w.command.Start(); err != nil {
		return
	}
	go func() { w.waitErr = w.command.Wait(); close(w.reaped) }()
	readControl.Close()
	writeResult.Close()
	slave.Close()
	if err = w.Result.SetReadDeadline(deadline); err != nil {
		return
	}
	stopIO = joinedAfterFunc(ctx, func() { w.Result.Close(); w.PTY.Close(); w.Control.Close() })
	pump, err = pumpStartup(ctx, w.PTY, cancel)
	if err != nil {
		return
	}
	w.Reader = bufio.NewReader(w.Result)
	m, e := protocol.ReadPrivate(w.Reader, protocol.AwshToEnvoy)
	if e != nil {
		err = helper.WithContextCause(ctx, e)
		return
	}
	if failure, ok := m.(*protocol.PrivateProtocolError); ok {
		privateFailure = true
		err = fmt.Errorf("Awsh startup failure %s: %s", failure.Code, failure.Message)
		return
	}
	ready, ok := m.(*protocol.PrivateReady)
	if !ok || ready.AwshPID != int64(w.command.Process.Pid) {
		err = fmt.Errorf("invalid private startup ready")
		return
	}
	w.ShellPID, w.CWD = ready.ShellPID, ready.CWD
	if err = w.verifyIdentity(); err != nil {
		return
	}
	code = "shell-launch-output"
	startup, e := pump.finish(ctx, w.PTY, expected)
	if e != nil {
		err = e
		return
	}
	pump = nil
	if err = w.verifyIdentity(); err != nil {
		return
	}
	// Detach and join destructive cancellation before publishing reusable state.
	stopIO()
	stopIO = nil
	if err = helper.WithContextCause(ctx, w.Result.SetReadDeadline(time.Time{})); err != nil {
		return
	}
	w.OutputThrough = int64(len(startup))
	err = s.CommitReadiness(ctx, func(group context.Context) error {
		w.ReadyAt = time.Now()
		ready := protocol.Ready{Header: s.header("ready"), EnvoyPID: int64(os.Getpid()), ShellPID: w.ShellPID, Cwd: w.CWD, Columns: columns, Rows: rows, OutputThrough: w.OutputThrough}
		if err := s.Send(group, ready); err != nil {
			return err
		}
		return w.writeTerminal(group, startup, false)
	})
	return
}

func (s *Channels) disposeFailedLaunch(ctx context.Context, w *Workload) error {
	err := w.Close(ctx)
	if err != nil {
		s.errMu.Lock()
		s.failedWorkload = w
		s.errMu.Unlock()
	}
	return err
}

// FailedWorkload retains only unfinished teardown ownership. It is not usable
// readiness or permission to restart the failed session or reset its epoch.
// The caller retains the original cleanup bound and Reploy termination duty.
func (s *Channels) FailedWorkload() *Workload {
	s.errMu.Lock()
	defer s.errMu.Unlock()
	return s.failedWorkload
}

func (s *Channels) header(kind string) protocol.Header {
	return protocol.Header{Schema: protocol.Schema, Type: kind, Seq: s.sendSeq}
}

// The protocol's launch filter preserves permitted application values, then
// installs the sole fixed history, terminal and locale values before Awsh exec.
func workloadEnvironment(inherited []string) []string {
	removed := strings.Fields("AWSH_BASH BASH_COMPAT BASHOPTS BASH_ENV BASH_XTRACEFD CDPATH ENV GLOBIGNORE HISTFILE INPUTRC LANG LANGUAGE LOCPATH MAIL MAILCHECK MAILPATH POSIXLY_CORRECT PROMPT_COMMAND PS0 PS1 PS2 PS3 PS4 SHELLOPTS TERM TERMINFO TERMINFO_DIRS TMOUT")
	env := make([]string, 0, len(inherited)+8)
	for _, value := range inherited {
		name, _, ok := strings.Cut(value, "=")
		if !ok {
			continue
		}
		blocked := false
		for _, exact := range removed {
			if name == exact {
				blocked = true
				break
			}
		}
		for _, prefix := range []string{"BASH_FUNC_", "LD_", "LC_", "AWSH_"} {
			if strings.HasPrefix(name, prefix) {
				blocked = true
				break
			}
		}
		if !blocked {
			env = append(env, value)
		}
	}
	return append(env, "HISTFILE=", "INPUTRC="+runtimebundle.Root+"/etc/inputrc", "TERM=xterm-256color", "TERMINFO="+runtimebundle.Root+"/share/terminfo", "TERMINFO_DIRS="+runtimebundle.Root+"/share/terminfo", "LC_ALL=C.UTF-8", "LANG=C.UTF-8", "LOCPATH="+runtimebundle.Root+"/lib/locale")
}

func (w *Workload) verifyIdentity() error {
	data, err := os.ReadFile("/proc/" + strconv.FormatInt(w.ShellPID, 10) + "/stat")
	if err != nil {
		return err
	}
	i := strings.LastIndex(string(data), ")")
	if i < 0 {
		return fmt.Errorf("invalid shell identity")
	}
	f := strings.Fields(string(data)[i+1:])
	if len(f) < 4 || f[0] == "Z" || f[1] != strconv.Itoa(w.command.Process.Pid) || f[2] != strconv.FormatInt(w.ShellPID, 10) || f[3] != strconv.Itoa(w.command.Process.Pid) {
		return fmt.Errorf("selected-shell topology mismatch")
	}
	var sid, group int32
	if err := ptyIOCTL(w.PTY, syscall.TIOCGSID, unsafe.Pointer(&sid)); err != nil {
		return err
	}
	if err := ptyIOCTL(w.PTY, syscall.TIOCGPGRP, unsafe.Pointer(&group)); err != nil {
		return err
	}
	if int(sid) != w.command.Process.Pid || int64(group) != w.ShellPID {
		return fmt.Errorf("startup terminal identity mismatch")
	}
	return nil
}

func (w *Workload) checkOutput(data []byte) error {
	if w.OutputThrough > outputLimit || int64(len(data)) > outputLimit-w.OutputThrough {
		return fmt.Errorf("session output budget exhausted")
	}
	return nil
}

func (w *Workload) writeTerminal(ctx context.Context, data []byte, advance bool) error {
	if err := w.checkOutput(data); err != nil {
		return err
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		return fmt.Errorf("missing terminal phase deadline")
	}
	if err := w.channels.terminal.SetWriteDeadline(deadline); err != nil {
		return err
	}
	stop := joinedAfterFunc(ctx, func() { _ = w.channels.terminal.SetWriteDeadline(time.Now()) })
	defer stop()
	for len(data) > 0 {
		if err := ctx.Err(); err != nil {
			return err
		}
		n, err := w.channels.terminal.Write(data)
		if advance {
			w.OutputThrough += int64(n)
		}
		data = data[n:]
		if err != nil {
			return helper.WithContextCause(ctx, err)
		}
		if n == 0 {
			return io.ErrShortWrite
		}
	}
	stop()
	return helper.WithContextCause(ctx, w.channels.terminal.SetWriteDeadline(time.Time{}))
}

// ShutdownIdle starts one final-drain epoch at the accepted idle shutdown. The
// caller supplies the validated controller reason, not Awsh's fixed constant.
func (w *Workload) ShutdownIdle(reason string) (err error) {
	phase, end := context.WithTimeout(context.Background(), finalDrainBudget)
	defer end()
	// Selected-channel loss interrupts work, but cleanup retains the remainder
	// of the same final-drain epoch so Awsh can reap its owned shell.
	ctx, cancel := context.WithCancel(phase)
	stopSelected := joinedAfterFunc(w.channels.ctx, cancel)
	defer func() { stopSelected(); cancel(); err = errors.Join(err, w.Close(phase)); w.channels.Close() }()
	if _, err = protocol.EncodeEnvoy(protocol.Draining{Header: w.channels.header("draining"), Reason: reason, OutputThrough: w.OutputThrough}); err != nil {
		return
	}
	stop := joinedAfterFunc(ctx, func() { w.Result.Close(); w.PTY.Close(); w.Control.Close() })
	defer stop()
	if err = SendPrivate(ctx, w.Control, protocol.PrivateShutdown{}); err != nil {
		return
	}
	deadline, _ := ctx.Deadline()
	if err = w.Result.SetReadDeadline(deadline); err != nil {
		return
	}
	m, e := protocol.ReadPrivate(w.Reader, protocol.AwshToEnvoy)
	if e != nil {
		return helper.WithContextCause(ctx, e)
	}
	closed, ok := m.(*protocol.PrivateClosed)
	if !ok || closed.Reason != "shutdown" || closed.CWD != w.CWD {
		return fmt.Errorf("invalid idle shutdown result")
	}
	if _, e = protocol.ReadPrivate(w.Reader, protocol.AwshToEnvoy); e != io.EOF {
		return errors.Join(fmt.Errorf("private EOF required after closed"), helper.WithContextCause(ctx, e))
	}
	select {
	case <-w.reaped:
		if w.waitErr != nil {
			return w.waitErr
		}
	case <-ctx.Done():
		return ctx.Err()
	}
	if err = w.PTY.SetReadDeadline(deadline); err != nil {
		return
	}
	if err = w.drainTerminal(ctx); err != nil {
		return
	}
	if err = w.channels.Send(ctx, protocol.Draining{Header: w.channels.header("draining"), Reason: reason, OutputThrough: w.OutputThrough}); err != nil {
		return
	}
	if err = w.channels.terminal.CloseWrite(); err != nil {
		return
	}
	return w.channels.Send(ctx, protocol.Closed{Header: w.channels.header("closed"), Reason: reason, OutputThrough: w.OutputThrough})
}

// Called only after the selected shell and direct Awsh child have been reaped.
func (w *Workload) drainTerminal(ctx context.Context) error {
	before := w.OutputThrough
	err := w.writeTerminal(ctx, w.pendingPTY, true)
	w.pendingPTY = w.pendingPTY[w.OutputThrough-before:]
	if err != nil {
		return err
	}
	var buffer [32768]byte
	for {
		n, e := w.PTY.Read(buffer[:])
		if n > 0 {
			if err := w.writeTerminal(ctx, buffer[:n], true); err != nil {
				return err
			}
		}
		// Linux masters return EIO after all slave owners have closed; only this
		// post-reap final phase accepts it as EOF. Startup never does.
		if e == io.EOF || errors.Is(e, syscall.EIO) {
			break
		}
		if e != nil {
			return helper.WithContextCause(ctx, e)
		}
	}
	return nil
}

// Close uses the caller's existing cleanup epoch. Closing control requests
// cancellation in the direct Awsh child; it never invents a descendant policy.
func (w *Workload) Close(ctx context.Context) error {
	w.closeMu.Lock()
	defer w.closeMu.Unlock()
	w.closeOnce.Do(func() {
		for _, file := range []*os.File{w.Control, w.Result, w.PTY} {
			if file != nil {
				file.Close()
			}
		}
	})
	if w.command != nil && w.command.Process != nil {
		select {
		case <-w.reaped:
		case <-ctx.Done():
			if w.closeErr == nil {
				killErr := w.command.Process.Kill()
				w.closeErr = fmt.Errorf("Awsh cleanup did not reap within original final drain: %w", errors.Join(ctx.Err(), killErr))
			}
			// SIGKILL is not a reap receipt. Retain path ownership until Wait
			// publishes it; a later caller may join under its own existing
			// context, but the original fatal timeout is never cleared.
			select {
			case <-w.reaped:
			default:
				return w.closeErr
			}
		}
	}
	if w.dirCreated {
		removeErr := os.RemoveAll(sessionRuntimeDir)
		w.closeErr = errors.Join(w.closeErr, removeErr)
		if removeErr == nil {
			w.dirCreated = false
		}
	}
	return w.closeErr
}
