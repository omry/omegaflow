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
	_, err := s.beginOperationEvent(ctx, false)
	return err
}

// BeginOperationEvent accepts the next existing operation-helper form. A gate
// retains its peer until WaitGate commits one outcome; completion retains its
// stricter direct-child and ignored-INT requirements.
func (s *Session) BeginOperationEvent(ctx context.Context) (bool, error) {
	return s.beginOperationEvent(ctx, true)
}

func (s *Session) completionEntry(ctx context.Context) (time.Time, error) {
	deadline, ok := ctx.Deadline()
	if (!ok && (!s.parentControlled || ctx.Done() == nil)) || s.startFailed || s.active == nil || s.active.phase != startReleased || s.completion != nil {
		return time.Time{}, fmt.Errorf("completion outside released operation or cleanup epoch")
	}
	// A selected-child reap is accepted before the supervisor cancels this
	// operation context. Preserve that cancellation through entry so the
	// existing shell-exit fact can be reported. Any other cancellation remains
	// an invalid completion epoch and is still recorded by failStart.
	if ctx.Err() != nil && !s.shellExitInterruption(ctx.Err()) {
		return time.Time{}, fmt.Errorf("completion outside released operation or cleanup epoch")
	}
	return deadline, nil
}

func (s *Session) beginOperationEvent(ctx context.Context, allowGate bool) (gated bool, err error) {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	defer func() {
		if err != nil {
			err = s.failStart(err)
		}
	}()
	deadline, entryErr := s.completionEntry(ctx)
	if entryErr != nil {
		return false, entryErr
	}
	r := &completionRecord{ctx: ctx, pidfd: -1, done: make(chan struct{})}
	s.completion = r
	if err := s.result.SetWriteDeadline(deadline); err != nil {
		return false, err
	}
	r.stop = context.AfterFunc(ctx, func() {
		defer close(r.done)
		s.interruptIO()
		if !s.shellEnding.Load() {
			s.result.Close()
		}
	})
	c, unregister, err := s.completionPeer()
	if err != nil {
		return false, err
	}
	r.peer = c
	r.peerUnregister = unregister
	group, fd, err := s.operationHelperIdentity(c, !allowGate)
	if err != nil {
		return false, err
	}
	r.pidfd = fd
	r.pid, err = helperPeerPID(c)
	if err != nil {
		return false, err
	}
	message, err := helper.ReadOperationRequest(c)
	if gate, ok := message.(*protocol.HelperGateRequest); allowGate && err == nil && ok {
		return true, s.beginGate(gate.GateID)
	}
	state, ok := message.(*protocol.HelperPromptState)
	if err != nil {
		return false, fmt.Errorf("completion prompt_state: %w", err)
	}
	if !ok {
		return false, fmt.Errorf("expected completion prompt_state")
	}
	parent, actualGroup, session, err := processIdentity(r.pid)
	if err != nil || parent != s.ShellPID || session != os.Getpid() || actualGroup != group || (group != s.ShellPID && group != r.pid) {
		return false, fmt.Errorf("completion helper is not the selected shell's direct child")
	}
	if err = completionHelperState(r.pid); err != nil {
		return false, err
	}
	if err = validateLiveState(state.PromptState, s.ShellPID); err != nil {
		return false, err
	}
	r.state = state.PromptState
	s.terminal.foreground = group
	if r.reference, err = s.terminal.snapshot(ctx); err != nil {
		return false, err
	}
	if err = s.completionHealthy(); err != nil {
		return false, err
	}
	s.armAdmission("input_closed", s.active.request.OperationID)
	if err = s.startWrite(protocol.PrivateInputClose{OperationID: s.active.request.OperationID, CompletionHelperPID: int64(r.pid)}); err != nil {
		return false, err
	}
	return false, nil
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
	if err != nil {
		return s.failStart(fmt.Errorf("invalid final prompt_ready: %w", err))
	}
	state, ok := message.(*protocol.HelperCompletionReady)
	if !ok {
		return s.failStart(fmt.Errorf("invalid final prompt_ready: expected HelperCompletionReady"))
	}
	if state.Status != r.state.Status {
		return s.failStart(fmt.Errorf("invalid final prompt_ready status: got %d want %d", state.Status, r.state.Status))
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
	s.armAdmission("idle", "")
	if err = s.startWrite(protocol.PrivateCompleted{OperationID: s.active.request.OperationID, Status: state.Status, PhysicalCWD: state.PhysicalCWD, Inspections: plans}); err != nil {
		return s.failStart(err)
	}
	if err = s.stopCompletion(nil); err != nil {
		return s.failStart(err)
	}
	s.State, s.ActiveTermios, s.workloadTermios = state.PromptState, active, workload
	s.terminal.foreground = s.ShellPID
	s.active, s.completion = nil, nil
	s.gateControls.Clear()
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
		if err := r.ctx.Err(); err != nil {
			return nil, nil, fmt.Errorf("completion interrupted: %w", err)
		}
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
	if s.gate != nil {
		s.gate.exit.stop()
		s.gate = nil
	}
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
