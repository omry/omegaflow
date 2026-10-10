//go:build linux

package launch

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/awsh/submission"
	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type startPhase uint8

const (
	startSubmitted startPhase = iota + 1
	startSourceDelivered
	startPrepared
	startAcknowledgement
	startSignal
	startReleased
)

// This record owns only the adapter's start exchange. Envoy owns cancellation,
// deadlines and teardown; completion owns the later return to an idle boundary.
type startRecord struct {
	ctx      context.Context
	request  protocol.PrivateExecute
	source   protocol.HelperSourceReply
	phase    startPhase
	prepared *net.UnixConn
	stop     func() bool
	done     chan struct{}
}

// BeginStart admits a complete execute at the previously validated empty
// primary Readline boundary. The dispatcher serializes helper/control/signal
// events through these methods; it must not inject controller terminal input.
// Check uses the same exact qualified candidate contract as selected-shell launch.
func (s *Session) BeginStart(ctx context.Context, m protocol.PrivateExecute, expected string, target bashbuild.Target, inputs map[string]string) error {
	return s.beginStart(ctx, m, func(ctx context.Context, source protocol.HelperSourceReply) error {
		_, err := submission.Check(ctx, source, expected, target, inputs)
		return err
	})
}

func (s *Session) beginStart(ctx context.Context, m protocol.PrivateExecute, check func(context.Context, protocol.HelperSourceReply) error) error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if s.active != nil || s.startFailed {
		return s.failStart(fmt.Errorf("execute outside idle boundary"))
	}
	s.mu.Lock()
	ready := s.ready && !s.closing
	s.mu.Unlock()
	deadline, ok := ctx.Deadline()
	if !ready || !ok || ctx.Err() != nil {
		return s.failStart(fmt.Errorf("start requires ready shell and original deadline"))
	}
	if _, err := protocol.EncodePrivate(m, protocol.EnvoyToAwsh); err != nil {
		return s.failStart(err)
	}
	if err := s.verifyShell(); err != nil {
		return s.failStart(err)
	}
	active, err := s.terminal.snapshot(ctx)
	if err != nil || active != s.ActiveTermios {
		return s.failStart(fmt.Errorf("start is not at the saved Readline terminal boundary: %v", err))
	}
	if err := validateSplit(m); err != nil {
		return s.failStart(err)
	}
	source := protocol.HelperSourceReply{OperationID: m.OperationID, Status: s.State.Status,
		HistExpand: s.State.HistExpand, EditingMode: s.State.EditingMode,
		ExecutionShape: m.ExecutionShape, StdoutFIFO: m.StdoutFIFO, StderrFIFO: m.StderrFIFO, Source: m.Source}
	if err := s.result.SetWriteDeadline(deadline); err != nil {
		return s.failStart(err)
	}
	// The same epoch interrupts checker, helper and result I/O. There is no
	// replacement timeout at submit, preparation, acknowledgement or restoration.
	done := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		defer close(done)
		s.interruptIO()
		s.result.Close()
	})
	if err := check(ctx, source); err != nil {
		var rejection *submission.Rejection
		if ctx.Err() == nil && errors.As(err, &rejection) {
			err = s.startWrite(protocol.PrivateRejected{OperationID: m.OperationID, Code: rejection.Code, Message: rejection.Message})
			if err = finishCancellation(ctx, stop, done, err); err == nil {
				return nil // recoverable before any reservation or trigger
			}
		} else {
			err = finishCancellation(ctx, stop, done, err)
		}
		return s.failStart(err)
	}
	// Copy the policy slice before submit; strings are immutable exact bytes.
	m.Inspections = append([]protocol.Inspection{}, m.Inspections...)
	s.active = &startRecord{ctx: ctx, request: m, source: source, phase: startSubmitted, stop: stop, done: done}
	if err := s.startHealthy(); err != nil {
		return s.failStart(err)
	}
	if err := s.startWrite(protocol.PrivateSubmit{OperationID: m.OperationID}); err != nil {
		return s.failStart(err)
	}
	return nil
}

func validateSplit(m protocol.PrivateExecute) error {
	if m.ExecutionShape != "split" {
		return nil // exact paths and empty PTY fields were checked by the codec
	}
	for _, path := range []string{"/run", "/run/omegaflow", "/run/omegaflow/session", "/run/omegaflow/session/split", m.StdoutFIFO, m.StderrFIFO} {
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		// /run is the platform-owned ancestor and may remain root-owned. Every
		// path below the reserved runtime root belongs to the workload identity.
		if !ok || info.Mode()&os.ModeSymlink != 0 || (path != "/run" && stat.Uid != uint32(os.Geteuid())) {
			return fmt.Errorf("split path identity mismatch")
		}
		fifo := path == m.StdoutFIFO || path == m.StderrFIFO
		if fifo && (info.Mode()&os.ModeNamedPipe == 0 || stat.Mode&07777 != 0600) ||
			!fifo && (!info.IsDir() || path != "/run" && stat.Mode&07777 != 0700) {
			return fmt.Errorf("split path type or mode mismatch")
		}
	}
	return nil
}

// AcceptStartHelper consumes exactly one connection under the stored epoch.
// Source is replied completely before a prepared request can be accepted.
// A prepared connection is retained, without success, until matching started_ack.
func (s *Session) AcceptStartHelper() error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if err := s.startHealthy(); err != nil {
		return s.failStart(err)
	}
	r := s.active
	phase := protocol.HelperSource
	switch r.phase {
	case startSubmitted:
	case startSourceDelivered:
		phase = protocol.HelperStartPrepared
	default:
		return s.failStart(fmt.Errorf("unexpected start helper"))
	}
	deadline, _ := r.ctx.Deadline()
	if err := s.listener.SetDeadline(deadline); err != nil {
		return s.failStart(err)
	}
	c, err := s.listener.AcceptUnix()
	if err != nil {
		return s.failStart(err)
	}
	s.mu.Lock()
	s.peers = append(s.peers, c)
	closing := s.closing
	s.mu.Unlock()
	if closing {
		c.Close()
		return s.failStart(fmt.Errorf("start interrupted"))
	}
	if err = c.SetDeadline(deadline); err != nil {
		return s.failStart(err)
	}
	if _, err = helper.ReadRequest(c, phase); err != nil {
		return s.failStart(err)
	}
	if err = s.startHealthy(); err != nil {
		return s.failStart(err)
	}
	if phase == protocol.HelperSource {
		if err = helper.Reply(c, phase, r.source); err != nil {
			return s.failStart(err)
		}
		r.phase = startSourceDelivered
		return nil
	}
	r.prepared, r.phase = c, startPrepared
	if err = s.startWrite(protocol.PrivateStartPrepared{OperationID: r.request.OperationID}); err != nil {
		return s.failStart(err)
	}
	return nil
}

// HandleStartControl accepts only the two matching start controls. The caller
// retains one buffered private decoder; unrelated lifecycle controls have owners
// in later slices, and are never silently consumed here.
func (s *Session) HandleStartControl(m protocol.PrivateMessage) error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if err := s.startHealthy(); err != nil {
		return s.failStart(err)
	}
	r := s.active
	switch v := m.(type) {
	case *protocol.PrivateStartRelease:
		if r.phase != startPrepared || v.OperationID != r.request.OperationID {
			return s.failStart(fmt.Errorf("unexpected start_release"))
		}
		if err := s.startWrite(protocol.PrivateStarted{OperationID: v.OperationID}); err != nil {
			return s.failStart(err)
		}
		r.phase = startAcknowledgement
	case *protocol.PrivateStartedAck:
		if r.phase != startAcknowledgement || v.OperationID != r.request.OperationID {
			return s.failStart(fmt.Errorf("unexpected started_ack"))
		}
		if err := s.RestoreWorkloadTermios(r.ctx); err != nil {
			return s.failStart(err)
		}
		if err := s.startHealthy(); err != nil {
			return s.failStart(err)
		}
		r.phase = startSignal // arm BEFORE the helper success can release Bash
		if err := helper.Reply(r.prepared, protocol.HelperStartPrepared, protocol.HelperAccepted{}); err != nil {
			return s.failStart(err)
		}
		r.prepared = nil
	default:
		return s.failStart(fmt.Errorf("unexpected private start control"))
	}
	if err := r.ctx.Err(); err != nil {
		return s.failStart(err)
	}
	return nil
}

// HandleStartSignal receives events from the launch-owned reserved signal path.
// Only the first SIGUSR1 in the armed phase authorizes start_released. Callers
// must continue dispatching it after release so a deliberate second is fatal.
func (s *Session) HandleStartSignal(signal os.Signal) error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	r := s.active
	if s.startFailed || r == nil || r.phase != startSignal || signal != syscall.SIGUSR1 {
		return s.failStart(fmt.Errorf("unexpected release signal"))
	}
	if err := r.ctx.Err(); err != nil {
		return s.failStart(err)
	}
	if err := s.verifyShell(); err != nil {
		return s.failStart(err)
	}
	if err := s.startWrite(protocol.PrivateStartReleased{OperationID: r.request.OperationID}); err != nil {
		return s.failStart(err)
	}
	if err := finishCancellation(r.ctx, r.stop, r.done, nil); err != nil {
		return s.failStart(err)
	}
	r.stop, r.phase = nil, startReleased
	return nil
}

func (s *Session) startHealthy() error {
	if s.startFailed || s.active == nil {
		return fmt.Errorf("no active start exchange")
	}
	if err := s.active.ctx.Err(); err != nil {
		return err
	}
	select {
	case <-s.Signals:
		return fmt.Errorf("release signal before armed phase")
	default:
	}
	return s.verifyShell()
}

func (s *Session) startWrite(m protocol.PrivateMessage) error {
	frame, err := protocol.EncodePrivate(m, protocol.AwshToEnvoy)
	if err != nil {
		return err
	}
	return helper.WriteAll(s.result, frame)
}

func (s *Session) failStart(err error) error {
	s.startFailed = true
	if s.active != nil && s.active.stop != nil {
		_ = finishCancellation(s.active.ctx, s.active.stop, s.active.done, err)
		s.active.stop = nil
	}
	// Closing helper I/O cannot release a success marker. Bash enters its
	// manifested non-returning fail-stop. Envoy retains fatal teardown ownership.
	s.interruptIO()
	if s.result != nil {
		s.result.Close()
	}
	return err
}
