//go:build linux

package launch

import (
	"context"
	"fmt"
	"os"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

type gateKey struct{ operation, gate string }
type gateControl struct {
	admitted     atomic.Bool
	continueGate chan struct{}
}
type gateRecord struct {
	id      string
	control *gateControl
	exit    *helperExit
}

// helperExit owns one pinned lifetime and one joined I/O wait. Request EOF is
// insufficient: the live helper has already half-closed its write direction.
type helperExit struct {
	file   *os.File
	cancel context.CancelFunc
	done   chan struct{}
	err    error
}

func watchHelper(ctx context.Context, fd int) (*helperExit, error) {
	if err := syscall.SetNonblock(fd, true); err != nil {
		syscall.Close(fd)
		return nil, err
	}
	f := os.NewFile(uintptr(fd), "gate-helper-pidfd")
	raw, err := f.SyscallConn()
	if err != nil {
		f.Close()
		return nil, err
	}
	ctx, cancel := context.WithCancel(ctx)
	w := &helperExit{file: f, cancel: cancel, done: make(chan struct{})}
	go func() {
		defer close(w.done)
		kicked := make(chan struct{})
		stop := context.AfterFunc(ctx, func() { defer close(kicked); _ = f.SetReadDeadline(time.Now()) })
		first := true
		w.err = raw.Read(func(uintptr) bool {
			if first {
				first = false
				return false
			}
			return true
		})
		if !stop() {
			<-kicked
		}
		w.err = helper.WithContextCause(ctx, w.err)
	}()
	return w, nil
}

func (w *helperExit) stop() { w.cancel(); <-w.done; _ = w.file.Close() }

// beginGate runs under startMu after exact operation-helper intake. Controls
// remain single-use for this operation even after a locally interrupted gate.
func (s *Session) beginGate(id string) error {
	r := s.completion
	key := gateKey{s.active.request.OperationID, id}
	control := &gateControl{continueGate: make(chan struct{}, 1)}
	if _, duplicate := s.gateControls.LoadOrStore(key, control); duplicate {
		return fmt.Errorf("gate ID reused within operation")
	}
	fd := r.pidfd
	r.pidfd = -1 // the watcher now owns and closes this exact pinned descriptor
	exit, err := watchHelper(r.ctx, fd)
	if err != nil {
		return err
	}
	s.gate = &gateRecord{id: id, control: control, exit: exit}
	return s.gateResult(r.ctx, protocol.PrivateGateReady{OperationID: key.operation, GateID: id})
}

func (s *Session) gateResult(ctx context.Context, message protocol.PrivateMessage) error {
	frame, err := protocol.EncodePrivate(message, protocol.AwshToEnvoy)
	if err != nil {
		return err
	}
	return helper.WriteControl(ctx, s.result, frame)
}

// WaitGate commits exactly one outcome. Cancellation remains session teardown;
// helper exit and incomplete accepted delivery are terminal-input interruption.
// The supervisor keeps decoding private controls while this joined job waits.
func (s *Session) WaitGate() error {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	if s.gate == nil || s.completion == nil {
		return s.failStart(fmt.Errorf("no accepted gate"))
	}
	g, r := s.gate, s.completion
	continued := false
	select {
	case <-r.ctx.Done():
		return s.failStart(r.ctx.Err())
	case <-g.exit.done:
	case <-g.control.continueGate:
		select {
		case <-g.exit.done:
		default:
			continued = helper.WriteGateReply(r.ctx, r.peer, g.id) == nil
		}
	}
	if err := r.ctx.Err(); err != nil {
		return s.failStart(err)
	}
	select {
	case <-g.exit.done:
		if g.exit.err != nil {
			return s.failStart(g.exit.err)
		}
	default:
	}
	if err := s.verifyShell(); err != nil {
		return s.failStart(err)
	}
	id, op := g.id, s.active.request.OperationID
	if err := s.stopCompletion(nil); err != nil {
		return s.failStart(err)
	}
	s.completion = nil
	var outcome protocol.PrivateMessage = protocol.PrivateGateInterrupted{OperationID: op, GateID: id}
	if continued {
		outcome = protocol.PrivateGateContinued{OperationID: op, GateID: id}
	}
	if err := s.gateResult(r.ctx, outcome); err != nil {
		return s.failStart(err)
	}
	return nil
}
