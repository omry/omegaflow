package launch

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Session owns startup resources and selected-shell identity, not operation
// lifecycle decisions. Envoy supplies the one absolute launch deadline.
type Session struct {
	Handoff             Handoff
	ShellPID            int
	State               protocol.PromptState
	ActiveTermios       syscall.Termios
	workloadTermios     syscall.Termios
	Signals             chan os.Signal
	Reaped              chan shellReap
	child               *selectedChild
	terminal            Terminal
	listener            *net.UnixListener
	result              *os.File
	dirCreated          bool
	mu                  sync.Mutex
	peers               map[*net.UnixConn]struct{}
	helperFDs           []int
	ready               bool
	closing             bool
	stopOnce, closeOnce sync.Once
	startMu             sync.Mutex
	active              *startRecord
	startFailed         bool
	completion          *completionRecord
}

// Start admits only a qualified, exact build. No executable supervise command
// exposes this partial startup module before the remaining adapter is assembled.
func Start(ctx context.Context, h Handoff, expected string, target bashbuild.Target, inputs map[string]string) (*Session, error) {
	entry, err := bashbuild.LookupAwsh("/bin/bash", expected, target, inputs)
	if err != nil {
		return nil, err
	}
	var proof struct {
		Result struct {
			Measurements struct {
				Signals map[string]uint64 `json:"catchable_signals"`
			} `json:"measurements"`
		} `json:"result"`
	}
	if err = json.Unmarshal(entry.Qualification, &proof); err != nil {
		return nil, err
	}
	seen := map[uint64]bool{}
	signals := []uint64{}
	for _, n := range proof.Result.Measurements.Signals {
		if !seen[n] {
			seen[n] = true
			signals = append(signals, n)
		}
	}
	return start(ctx, h, signals)
}

func start(ctx context.Context, h Handoff, signals []uint64) (s *Session, err error) {
	deadline, ok := ctx.Deadline()
	if !ok {
		return nil, fmt.Errorf("launch requires the existing phase deadline")
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if err = h.intake(); err != nil {
		return nil, err
	}
	s = &Session{Handoff: h}
	// NewFile registers a nonblocking pipe with Go's poller, making the original
	// deadline and context closure effective even when Envoy stops reading.
	if err = syscall.SetNonblock(h.Result, true); err != nil {
		s.Close()
		return nil, err
	}
	s.result = os.NewFile(uintptr(h.Result), "awsh-result")
	if err = s.result.SetWriteDeadline(deadline); err != nil {
		s.Close()
		return nil, err
	}
	defer func() {
		if err != nil {
			s.stopStartup()
			frame, _ := protocol.EncodePrivate(protocol.PrivateProtocolError{Code: "shell-launch", Message: "selected-shell launch failed"}, protocol.AwshToEnvoy)
			_ = helper.WriteAll(s.result, frame) // best effort under the unchanged epoch
			s.Close()
		}
	}()
	cancelDone := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		defer close(cancelDone)
		s.interruptIO()
		s.result.Close()
	})
	defer func() {
		err = finishCancellation(ctx, stop, cancelDone, err)
		s.ready = err == nil
	}()
	dir := filepath.Dir(helper.SocketPath)
	if err = os.Mkdir(dir, 0700); err != nil {
		return
	}
	s.dirCreated = true
	if err = os.Chmod(dir, 0700); err != nil {
		return
	}
	info, e := os.Stat(dir)
	if e != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		err = fmt.Errorf("helper directory mode mismatch: %v", e)
		return
	}
	listener, e := net.ListenUnix("unix", &net.UnixAddr{Name: helper.SocketPath, Net: "unix"})
	if e != nil {
		err = e
		return
	}
	s.mu.Lock()
	if s.closing {
		s.mu.Unlock()
		listener.Close()
		err = fmt.Errorf("startup interrupted")
		return
	}
	s.listener = listener
	s.mu.Unlock()
	if err = os.Chmod(helper.SocketPath, 0600); err != nil {
		return
	}
	if err = s.listener.SetDeadline(deadline); err != nil {
		return
	}
	s.child, err = launchSelected(ctx, h, signals)
	if err != nil {
		return
	}
	s.ShellPID, s.Signals, s.Reaped = s.child.PID, s.child.Signals, s.child.Reaped
	s.terminal.session, s.terminal.foreground = os.Getpid(), s.ShellPID

	monitorStop, monitorDone := make(chan struct{}), make(chan struct{})
	monitorError := make(chan error, 1)
	go func() {
		defer close(monitorDone)
		var e error
		select {
		case <-monitorStop:
			return
		case <-s.child.done:
			e = fmt.Errorf("shell exited during startup")
		case <-s.Signals:
			e = fmt.Errorf("unexpected startup release signal")
		}
		monitorError <- e
		s.interruptIO()
		s.result.Close() // interrupt a blocked readiness write under the same epoch
	}()
	defer func() { err = s.finishStartup(monitorStop, monitorDone, monitorError, err) }()
	err = s.startup(ctx)
	if err != nil {
		return
	}
	if err = s.verifyShell(); err != nil {
		return
	}
	if err = terminalIdentity(h.Slave, os.Getpid(), s.ShellPID); err != nil {
		return
	}
	if err = syscall.Close(h.Slave); err != nil {
		return
	}
	s.Handoff.Slave = -1
	if err = s.terminal.drain(ctx); err != nil {
		return
	}
	if err = s.verifyShell(); err != nil {
		return
	}
	frame, e := protocol.EncodePrivate(protocol.PrivateReady{AwshPID: int64(os.Getpid()), ShellPID: int64(s.ShellPID), CWD: s.State.PhysicalCWD}, protocol.AwshToEnvoy)
	if e != nil {
		err = e
		return
	}
	err = helper.WriteAll(s.result, frame)
	if err == nil {
		err = ctx.Err()
	}
	return
}

func (s *Session) finishStartup(monitorStop, monitorDone chan struct{}, monitorError <-chan error, err error) error {
	close(monitorStop)
	<-monitorDone
	select {
	case e := <-monitorError:
		err = e
	default:
		// A monitor-stop tie must not discard either queued fatal observation.
		select {
		case <-s.child.done:
			err = fmt.Errorf("shell exited during startup")
		case <-s.Signals:
			err = fmt.Errorf("unexpected startup release signal")
		default:
		}
	}
	if err == nil {
		// Reaper publication can lag kernel exit even after the monitor joins.
		err = s.verifyShell()
	}
	return err
}

func finishCancellation(ctx context.Context, stop func() bool, done <-chan struct{}, err error) error {
	// AfterFunc's stop does not join a callback that has already started.
	if !stop() {
		<-done
	}
	if err == nil {
		err = ctx.Err()
	}
	return err
}

func (s *Session) startup(ctx context.Context) error {
	var reference syscall.Termios
	deadline, _ := ctx.Deadline()
	for i := 0; i < 2; i++ {
		c, err := s.listener.AcceptUnix()
		if err != nil {
			return err
		}
		unregister, closing := s.registerPeer(c)
		if closing {
			unregister()
			_ = c.Close()
			return fmt.Errorf("startup closed")
		}
		err = func() error {
			defer unregister()
			defer c.Close()
			if err := c.SetDeadline(deadline); err != nil {
				return err
			}
			group, fd, err := s.helperIdentity(c)
			if err != nil {
				return err
			}
			s.mu.Lock()
			if s.closing {
				s.mu.Unlock()
				syscall.Close(fd)
				return fmt.Errorf("startup closed")
			}
			s.helperFDs = append(s.helperFDs, fd)
			s.mu.Unlock()
			m, err := helper.ReadRequest(c, protocol.HelperStartup)
			if err != nil {
				return err
			}
			s.terminal.foreground = group
			if i == 0 {
				state, ok := m.(*protocol.HelperPromptState)
				if !ok {
					return fmt.Errorf("expected startup prompt_state")
				}
				if err = validateLiveState(state.PromptState, s.ShellPID); err != nil {
					return err
				}
				s.State = state.PromptState
				reference, err = s.terminal.snapshot(ctx)
				s.workloadTermios = reference
			} else {
				if _, ok := m.(*protocol.HelperStartupReady); !ok {
					return fmt.Errorf("expected startup no-state prompt_ready")
				}
				err = s.terminal.prepareReadline(ctx, reference)
			}
			if err != nil {
				return err
			}
			if err = s.verifyShell(); err != nil {
				return err
			}
			return helper.Reply(c, protocol.HelperStartup, protocol.HelperAccepted{})
		}()
		if err != nil {
			return err
		}
	}
	s.terminal.foreground = s.ShellPID
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	for {
		active, err := termios(s.Handoff.Slave)
		if err != nil {
			return err
		}
		if active.Lflag&(syscall.ICANON|syscall.ECHO) == 0 {
			if err = terminalIdentity(s.Handoff.Slave, os.Getpid(), s.ShellPID); err != nil {
				return err
			}
			s.ActiveTermios = active
			return ctx.Err()
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-s.child.done:
			return fmt.Errorf("shell exited during readiness")
		case <-s.Signals:
			return fmt.Errorf("unexpected startup release signal")
		case <-ticker.C:
		}
	}
}

// RestoreWorkloadTermios restores the saved workload state, not Readline's
// active state. The start owner supplies its existing phase deadline and must
// withhold helper success if restoration fails; that integration is separate.
func (s *Session) RestoreWorkloadTermios(ctx context.Context) error {
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("restoration requires the existing phase deadline")
	}
	s.mu.Lock()
	ready := s.ready && !s.closing
	s.mu.Unlock()
	if !ready {
		return fmt.Errorf("selected shell is not ready")
	}
	if err := s.verifyShell(); err != nil {
		return err
	}
	if err := s.terminal.restore(ctx, s.workloadTermios); err != nil {
		return err
	}
	return s.verifyShell()
}

// The wire validator checks encoding/bounds; the actor checks directory identity.
func validateLiveState(state protocol.PromptState, shell int) error {
	physical, err := os.Stat(state.PhysicalCWD)
	if err != nil || !physical.IsDir() {
		return fmt.Errorf("invalid physical cwd")
	}
	physicalPath, err := os.Readlink("/proc/" + strconv.Itoa(shell) + "/cwd")
	if err != nil || physicalPath != state.PhysicalCWD {
		return fmt.Errorf("nonphysical cwd")
	}
	current, err := os.Stat("/proc/" + strconv.Itoa(shell) + "/cwd")
	if err != nil || !os.SameFile(physical, current) {
		return fmt.Errorf("stale shell cwd")
	}
	if state.LogicalCWD != "" {
		logical, err := os.Stat(state.LogicalCWD)
		if err != nil || !os.SameFile(physical, logical) {
			return fmt.Errorf("invalid logical cwd")
		}
	}
	return nil
}

func processIdentity(pid int) (parent, group, session int, err error) {
	b, e := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if e != nil {
		return 0, 0, 0, e
	}
	end := strings.LastIndex(string(b), ")")
	if end < 0 {
		return 0, 0, 0, fmt.Errorf("malformed process identity")
	}
	f := strings.Fields(string(b[end+1:]))
	if len(f) < 4 {
		return 0, 0, 0, fmt.Errorf("short process identity")
	}
	if f[0] == "Z" || f[0] == "X" {
		return 0, 0, 0, fmt.Errorf("process already ended")
	}
	values := []*int{&parent, &group, &session}
	for i, v := range values {
		*v, err = strconv.Atoi(f[i+1])
		if err != nil {
			return
		}
	}
	return
}

func (s *Session) verifyShell() error {
	s.child.mu.Lock()
	defer s.child.mu.Unlock()
	if s.child.ended {
		return fmt.Errorf("selected shell ended")
	}
	parent, group, session, err := processIdentity(s.ShellPID)
	if err != nil || parent != os.Getpid() || group != s.ShellPID || session != os.Getpid() {
		return fmt.Errorf("selected-shell identity mismatch")
	}
	return nil
}

func (s *Session) helperIdentity(c *net.UnixConn) (group, fd int, err error) {
	pid, err := helperPeerPID(c)
	if err != nil {
		return 0, -1, err
	}
	// A pidfd holds only this startup helper's lifetime identity. It is not an
	// operation-descendant census and cannot authorize operation lifecycle signals.
	n, _, errno := syscall.Syscall(434, uintptr(pid), 0, 0)
	if errno != 0 {
		return 0, -1, errno
	}
	fd = int(n)
	defer func() {
		if err != nil {
			syscall.Close(fd)
		}
	}()
	parent, group, session, err := processIdentity(pid)
	if err != nil {
		return 0, fd, err
	}
	exe, e := os.Readlink("/proc/" + strconv.Itoa(pid) + "/exe")
	if e != nil || parent != s.ShellPID || session != os.Getpid() || (group != s.ShellPID && group != pid) || exe != "/omegaflow-runtime/bin/awsh" {
		return 0, fd, fmt.Errorf("helper identity mismatch")
	}
	if err = s.verifyShell(); err != nil {
		return 0, fd, err
	}
	return group, fd, nil
}

func helperPeerPID(c *net.UnixConn) (int, error) {
	raw, err := c.SyscallConn()
	if err != nil {
		return 0, err
	}
	var cred *syscall.Ucred
	var inner error
	err = raw.Control(func(n uintptr) {
		cred, inner = syscall.GetsockoptUcred(int(n), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	})
	if err != nil {
		return 0, err
	}
	if inner != nil {
		return 0, inner
	}
	if cred.Uid != uint32(os.Geteuid()) || cred.Pid <= 0 {
		return 0, fmt.Errorf("helper credentials mismatch")
	}
	return int(cred.Pid), nil
}

func (s *Session) interruptIO() {
	s.mu.Lock()
	s.closing = true
	listener := s.listener
	peers := make([]*net.UnixConn, 0, len(s.peers))
	for c := range s.peers {
		peers = append(peers, c)
	}
	s.peers = nil
	s.mu.Unlock()
	if listener != nil {
		listener.Close()
	}
	for _, c := range peers {
		c.Close()
	}
}

func (s *Session) registerPeer(c *net.UnixConn) (func(), bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closing {
		return func() {}, true
	}
	if s.peers == nil {
		s.peers = make(map[*net.UnixConn]struct{})
	}
	s.peers[c] = struct{}{}
	return func() { s.unregisterPeer(c) }, false
}

func (s *Session) unregisterPeer(c *net.UnixConn) {
	s.mu.Lock()
	delete(s.peers, c)
	s.mu.Unlock()
}

// Freeze only the selected Bash during failed startup, before closing helper
// I/O, so it cannot create a new fail-stop helper after a reply is interrupted.
// The fixed startup hook permits one foreground helper, not authored jobs.
func (s *Session) freezeLaunch() {
	if s.ready || s.child == nil {
		return
	}
	s.child.mu.Lock()
	defer s.child.mu.Unlock()
	if s.child.ended {
		return
	}
	if syscall.Kill(s.ShellPID, syscall.SIGSTOP) != nil {
		return
	}
	for {
		b, e := os.ReadFile("/proc/" + strconv.Itoa(s.ShellPID) + "/stat")
		if e != nil {
			return
		}
		end := strings.LastIndex(string(b), ")")
		if end < 0 {
			return
		}
		fields := strings.Fields(string(b[end+1:]))
		if len(fields) == 0 {
			return
		}
		if fields[0] == "Z" || fields[0] == "X" {
			return
		}
		if fields[0] == "T" {
			break
		}
		// This wait does not create an epoch. Envoy retains launch supervision and
		// takes over disposal if the selected shell cannot reach this boundary.
		time.Sleep(time.Millisecond)
	}
	if s.Handoff.Slave <= 2 {
		return
	}
	var group int32
	if ioctl(s.Handoff.Slave, syscall.TIOCGPGRP, unsafe.Pointer(&group)) != nil || int(group) == s.ShellPID || group <= 0 {
		return
	}
	n, _, e := syscall.Syscall(434, uintptr(group), 0, 0)
	if e != 0 {
		return
	}
	defer syscall.Close(int(n))
	parent, pgid, sid, err := processIdentity(int(group))
	exe, xe := os.Readlink("/proc/" + strconv.Itoa(int(group)) + "/exe")
	if err == nil && xe == nil && parent == s.ShellPID && pgid == int(group) && sid == os.Getpid() && exe == "/omegaflow-runtime/bin/awsh" {
		syscall.Syscall6(424, n, uintptr(syscall.SIGKILL), 0, 0, 0, 0)
	}
}

func (s *Session) stopStartup() {
	s.stopOnce.Do(func() {
		s.freezeLaunch()
		s.interruptIO()
		for _, fd := range s.helperFDs {
			// pidfd_send_signal cannot target a reused numeric PID after helper exit.
			syscall.Syscall6(424, uintptr(fd), uintptr(syscall.SIGKILL), 0, 0, 0, 0)
			syscall.Close(fd)
		}
		if s.child != nil {
			s.child.close()
		}
		if s.dirCreated {
			os.Remove(filepath.Dir(helper.SocketPath))
		}
	})
}

// Close disposes launch/session resources. Operation cleanup belongs to Envoy.
func (s *Session) Close() {
	s.closeOnce.Do(func() {
		s.stopStartup()
		// Closing I/O first wakes an in-flight completion before taking its lock.
		s.startMu.Lock()
		if s.completion != nil {
			s.stopCompletion(nil)
		}
		s.startMu.Unlock()
		if s.Handoff.Slave > 2 {
			syscall.Close(s.Handoff.Slave)
		}
		if s.result != nil {
			s.result.Close()
		} else if s.Handoff.Result > 2 {
			syscall.Close(s.Handoff.Result)
		}
		if s.Handoff.Control > 2 {
			syscall.Close(s.Handoff.Control)
		}
	})
}
