//go:build linux

package launch

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type completionRecord struct {
	ctx            context.Context
	state          protocol.PromptState
	reference      syscall.Termios
	peer           *net.UnixConn
	peerUnregister func()
	pid, pidfd     int
	released       bool
	stop           func() bool
	done           chan struct{}
}

// BeginCompletion consumes the direct-exec prompt_state helper. The caller
// supplies the original cleanup epoch; the finished start epoch stays unchanged.
// No helper success is sent before the matching input_closed control.
func (s *Session) BeginCompletion(ctx context.Context) error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	deadline, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || s.startFailed || s.active == nil || s.active.phase != startReleased || s.completion != nil {
		return s.failStart(fmt.Errorf("completion outside released operation or cleanup epoch"))
	}
	r := &completionRecord{ctx: ctx, pidfd: -1, done: make(chan struct{})}
	s.completion = r
	if err := s.result.SetWriteDeadline(deadline); err != nil {
		return s.failStart(err)
	}
	r.stop = context.AfterFunc(ctx, func() { defer close(r.done); s.interruptIO(); s.result.Close() })
	c, unregister, err := s.completionPeer()
	if err != nil {
		return s.failStart(err)
	}
	r.peer = c
	r.peerUnregister = unregister
	group, fd, err := s.helperIdentity(c)
	if err != nil {
		return s.failStart(err)
	}
	r.pidfd = fd
	r.pid, err = helperPeerPID(c)
	if err != nil {
		return s.failStart(err)
	}
	if err = completionHelperState(r.pid); err != nil {
		return s.failStart(err)
	}
	message, err := helper.ReadRequest(c, protocol.HelperCompletion)
	state, ok := message.(*protocol.HelperPromptState)
	if err != nil || !ok {
		return s.failStart(fmt.Errorf("expected completion prompt_state: %v", err))
	}
	if err = validateLiveState(state.PromptState, s.ShellPID); err != nil {
		return s.failStart(err)
	}
	r.state = state.PromptState
	s.terminal.foreground = group
	if r.reference, err = s.terminal.snapshot(ctx); err != nil {
		return s.failStart(err)
	}
	if err = s.completionHealthy(); err != nil {
		return s.failStart(err)
	}
	if err = s.startWrite(protocol.PrivateInputClose{OperationID: s.active.request.OperationID, CompletionHelperPID: int64(r.pid)}); err != nil {
		return s.failStart(err)
	}
	return nil
}

// HandleCompletionControl releases only the validated first helper and only
// after Envoy has completed its owning cleanup and closed operation input.
func (s *Session) HandleCompletionControl(message protocol.PrivateMessage) error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if err := s.completionHealthy(); err != nil {
		return s.failStart(err)
	}
	r := s.completion
	unregister := r.peerUnregister
	defer func() {
		if unregister != nil {
			unregister()
			r.peerUnregister = nil
		}
	}()
	closed, ok := message.(*protocol.PrivateInputClosed)
	if !ok || closed.OperationID != s.active.request.OperationID || r.released {
		return s.failStart(fmt.Errorf("unexpected input_closed"))
	}
	if err := completionHelperState(r.pid); err != nil {
		return s.failStart(err)
	}
	if err := helper.Reply(r.peer, protocol.HelperCompletion, protocol.HelperAccepted{}); err != nil {
		return s.failStart(err)
	}
	r.peer = nil
	r.released = true
	return nil
}

// FinishCompletion validates the final report and proves actual Readline entry
// under one descriptor-free terminal lease before writing completed.
func (s *Session) FinishCompletion() error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if err := s.completionHealthy(); err != nil {
		return s.failStart(err)
	}
	r := s.completion
	if !r.released {
		return s.failStart(fmt.Errorf("prompt_ready before input_closed"))
	}
	c, unregister, err := s.completionPeer()
	if err != nil {
		return s.failStart(err)
	}
	defer c.Close()
	defer unregister()
	// The next helper can be created only after Bash reaped the substitution.
	if _, err = os.Stat("/proc/" + strconv.Itoa(r.pid)); !os.IsNotExist(err) {
		return s.failStart(fmt.Errorf("completion helper not reaped"))
	}
	group, fd, err := s.helperIdentity(c)
	if err != nil {
		return s.failStart(err)
	}
	defer syscall.Close(fd)
	finalPID, err := helperPeerPID(c)
	if err != nil {
		return s.failStart(err)
	}
	message, err := helper.ReadRequest(c, protocol.HelperCompletion)
	state, ok := message.(*protocol.HelperCompletionReady)
	if err != nil || !ok || state.Status != r.state.Status {
		return s.failStart(fmt.Errorf("invalid final prompt_ready: %v", err))
	}
	if err = validateLiveState(state.PromptState, s.ShellPID); err != nil {
		return s.failStart(err)
	}
	plans, err := resolveInspections(state.PromptState, s.active.request.Inspections)
	if err != nil {
		return s.failStart(err)
	}
	s.terminal.foreground = group
	var workload, active syscall.Termios
	err = s.terminal.lease(r.ctx, func(terminalFD int) error {
		var err error
		workload, err = termios(terminalFD)
		if err != nil {
			return err
		}
		r.reference = workload // final fresh state replaces the early reference
		prepared := workload
		prepared.Lflag |= syscall.ICANON
		prepared.Lflag &^= syscall.ECHO
		if err = restoreTermios(terminalFD, prepared); err != nil {
			return err
		}

		if err = s.completionHealthy(); err != nil {
			return err
		}
		if err = helper.Reply(c, protocol.HelperCompletion, protocol.HelperAccepted{}); err != nil {
			return err
		}
		ticker := time.NewTicker(time.Millisecond)
		defer ticker.Stop()
		for {
			if err = s.completionHealthy(); err != nil {
				return err
			}
			active, err = termios(terminalFD)
			if err != nil {
				return err
			}
			if active.Lflag&(syscall.ICANON|syscall.ECHO) == 0 {
				break
			}
			select {
			case <-r.ctx.Done():
				return r.ctx.Err()
			case <-ticker.C:
			}
		}
		if err = terminalIdentity(terminalFD, os.Getpid(), s.ShellPID); err != nil {
			return err
		}
		// Both final helper and wait record must be absent at the reusable boundary.
		if _, err = os.Stat("/proc/" + strconv.Itoa(finalPID)); !os.IsNotExist(err) {
			return fmt.Errorf("final helper not reaped")
		}
		return drainTerminal(terminalFD)
	})
	if err != nil {
		return s.failStart(err)
	}
	if err = s.completionHealthy(); err != nil {
		return s.failStart(err)
	}
	if err = s.startWrite(protocol.PrivateCompleted{OperationID: s.active.request.OperationID, Status: state.Status, PhysicalCWD: state.PhysicalCWD, Inspections: plans}); err != nil {
		return s.failStart(err)
	}
	if err = s.stopCompletion(nil); err != nil {
		return s.failStart(err)
	}
	s.State, s.ActiveTermios, s.workloadTermios = state.PromptState, active, workload
	s.terminal.foreground = s.ShellPID
	s.active, s.completion = nil, nil
	return nil
}

func (s *Session) completionPeer() (*net.UnixConn, func(), error) {
	r := s.completion
	deadline, _ := r.ctx.Deadline()
	if err := s.listener.SetDeadline(deadline); err != nil {
		return nil, nil, err
	}
	c, err := s.listener.AcceptUnix()
	if err != nil {
		return nil, nil, err
	}
	unregister, closing := s.registerPeer(c)
	if closing {
		unregister()
		_ = c.Close()
		return nil, nil, fmt.Errorf("completion interrupted")
	}
	if err = c.SetDeadline(deadline); err != nil {
		_ = c.Close()
		unregister()
		return nil, nil, err
	}
	return c, unregister, nil
}

func (s *Session) completionHealthy() error {
	if s.startFailed || s.active == nil || s.active.phase != startReleased || s.completion == nil {
		return fmt.Errorf("no active completion")
	}
	if err := s.completion.ctx.Err(); err != nil {
		return err
	}
	select {
	case <-s.Signals:
		return fmt.Errorf("release signal during completion")
	default:
	}
	return s.verifyShell()
}

func (s *Session) stopCompletion(err error) error {
	r := s.completion
	if r.peerUnregister != nil {
		if r.peer != nil {
			_ = r.peer.Close()
			r.peer = nil
		}
		r.peerUnregister()
		r.peerUnregister = nil
	}
	if r.stop != nil {
		err = finishCancellation(r.ctx, r.stop, r.done, err)
		r.stop = nil
	}
	if r.pidfd >= 0 {
		syscall.Close(r.pidfd)
		r.pidfd = -1
	}
	return err
}

func completionHelperState(pid int) error {
	base := "/proc/" + strconv.Itoa(pid)
	b, err := os.ReadFile(base + "/status")
	if err != nil {
		return err
	}
	ignored := false
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "SigIgn:") {
			mask, e := strconv.ParseUint(strings.TrimSpace(strings.TrimPrefix(line, "SigIgn:")), 16, 64)
			ignored = e == nil && mask&(1<<(uint(syscall.SIGINT)-1)) != 0
		}
	}
	if !ignored {
		return fmt.Errorf("completion helper does not ignore INT")
	}
	children, err := filepath.Glob(base + "/task/*/children")
	if err != nil || len(children) == 0 {
		return fmt.Errorf("missing helper task identity")
	}
	for _, path := range children {
		b, err := os.ReadFile(path)
		if err != nil || len(strings.TrimSpace(string(b))) != 0 {
			return fmt.Errorf("completion helper has child: %v", err)
		}
	}
	return nil
}
