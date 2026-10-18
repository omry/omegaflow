//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// controlPeers supplies the owning B6 boundaries. There is no successful
// default census, tree termination, reap or final-census implementation.
// Each callback must join its I/O when its original context is cancelled.
type controlPeers struct {
	sessionID int64
	census    foregroundCensus
	endShell  func(context.Context, string, bool) error
}

type gateRecord struct {
	id                string
	request           *protocol.Continue
	sent, interrupted bool
}

type signalProof struct {
	done   chan struct{}
	cancel context.CancelFunc
	live   bool
	err    error
}

type operationDispatch struct {
	ctx            context.Context
	life           *operationLifecycle
	peers          *controlPeers
	proof          *signalProof
	gate           *gateRecord
	gates          map[string]*gateRecord
	gateEvents     map[protocol.PrivateMessage]bool
	continuation   bool
	selection      string
	completed      *protocol.PrivateCompleted
	result         protocol.Message
	ending         bool
	crossedExecute string
	cleanup        context.Context
	endCleanup     context.CancelFunc
}

type continueOperation struct{}

func (*continueOperation) Error() string { return "continue operation gate" }

var errLifecycleEnd = errors.New("selected shell end or lifecycle timeout")

func (d *operationDispatch) close() {
	d.life.close()
	if d.proof != nil {
		d.proof.cancel()
		<-d.proof.done
		d.proof = nil
	}
}

// Only the existing serialized owner reads/mutates life. A proof worker holds
// an immutable grace/session/census input and cannot select a result or signal.
func (d *operationDispatch) step(c *Coordinator) error {
	if d.ending {
		return nil
	}
	if d.life.shellExit != nil || d.life.timeout != "" {
		return errLifecycleEnd
	}
	live := false
	if d.proof != nil {
		select {
		case <-d.proof.done:
			p := d.proof
			d.proof = nil
			p.cancel()
			if !d.life.signalNeeded() {
				return nil
			}
			if p.err != nil {
				return d.signalError(p.err)
			}
			live = p.live
		default:
		}
	}
	if selected, err := d.life.selectTimeout(time.Now()); err != nil {
		return err
	} else if selected {
		d.beginCleanup()
		return errLifecycleEnd
	}
	if live {
		return d.signalError(d.life.applyForegroundSignal(c.w.PTY))
	}
	if d.proof == nil && d.life.signalNeeded() {
		if d.peers == nil || d.peers.census == nil {
			return fmt.Errorf("missing controlled foreground owner")
		}
		ctx, cancel := context.WithCancel(d.life.grace)
		p := &signalProof{done: make(chan struct{}), cancel: cancel}
		d.proof = p
		master, sid, census := c.w.PTY, d.peers.sessionID, d.peers.census
		go func() {
			defer close(p.done)
			p.live, p.err = proveForeground(ctx, master, sid, census)
			if !p.live && p.err == nil {
				// Pace empty samples in this same joined worker. This is not a
				// new phase epoch: return facts and the original grace cancel it.
				pause := time.NewTimer(10 * time.Millisecond)
				defer pause.Stop()
				select {
				case <-ctx.Done():
					p.err = ctx.Err()
				case <-pause.C:
				}
			}
		}()
	}
	return nil
}

func (d *operationDispatch) accept(c *Coordinator, request protocol.Message, interrupt context.CancelFunc) error {
	switch m := request.(type) {
	case *protocol.Cancel, *protocol.Finalize:
		if err := d.life.accept(d.ctx, request); err != nil {
			return err
		}
		if d.continuation && d.life.kind != "" && interrupt != nil {
			interrupt()
		}
	case *protocol.Continue:
		if m.OperationID != d.life.operationID {
			return fmt.Errorf("continue names another operation")
		}
		g := d.gates[m.GateID]
		if g == nil || g.request != nil {
			return fmt.Errorf("unexpected or duplicate gate continue")
		}
		g.request = m
		if g != d.gate && !g.interrupted {
			return fmt.Errorf("continue after committed gate outcome")
		}
	default:
		return fmt.Errorf("unexpected public operation request")
	}
	return nil
}

func (d *operationDispatch) observe(c *Coordinator, m protocol.PrivateMessage, interrupt context.CancelFunc) error {
	switch v := m.(type) {
	case *protocol.PrivateInputClose, *protocol.PrivateShellExit:
		if _, err := d.life.observeReturn(m); err != nil {
			return err
		}
		if d.life.timeout == "" {
			d.beginCleanup()
		}
		if d.proof != nil {
			d.proof.cancel()
		}
		if d.continuation && interrupt != nil {
			interrupt()
		}
	case *protocol.PrivateGateReady:
		if c.phase != awaitInputClose || d.life.inputClose != nil || v.OperationID != d.life.operationID || d.gate != nil || d.gates[v.GateID] != nil {
			return fmt.Errorf("unexpected gate readiness")
		}
		d.gate = &gateRecord{id: v.GateID}
		d.gates[v.GateID] = d.gate
		d.gateEvents[m] = d.life.kind == "" && d.life.timeout == ""
	case *protocol.PrivateGateContinued:
		if d.gate == nil || !d.gate.sent {
			return fmt.Errorf("continued gate without private continue")
		}
		return d.gateOutcome(v.OperationID, v.GateID, m, false, interrupt)
	case *protocol.PrivateGateInterrupted:
		return d.gateOutcome(v.OperationID, v.GateID, m, true, interrupt)
	}
	return nil
}

func (d *operationDispatch) gateOutcome(op, id string, m protocol.PrivateMessage, interrupted bool, interrupt context.CancelFunc) error {
	if op != d.life.operationID || d.gate == nil || d.gate.id != id {
		return fmt.Errorf("unexpected gate outcome")
	}
	d.gate.interrupted = interrupted
	d.gate = nil
	d.gateEvents[m] = d.life.kind == "" && d.life.timeout == ""
	if d.continuation && interrupt != nil {
		interrupt()
	}
	return nil
}

func (d *operationDispatch) continueGate(c *Coordinator, ctx context.Context) error {
	g := d.gate
	if g == nil || g.request == nil || g.sent || d.life.kind != "" {
		return nil
	}
	barrier, end := context.WithTimeout(ctx, inputBarrierBudget)
	defer end()
	d.continuation = true
	var err error
	for {
		err = c.work(barrier, false, func(job context.Context) error {
			return c.w.channels.WaitGateInput(job, g.request.OperationID, g.request.InputThrough)
		})
		var input *operationInput
		if !errors.As(err, &input) {
			break
		}
		c.forwardingInput = true
		err = c.work(barrier, false, func(job context.Context) error { return c.r.ForwardInput(job, input.event) })
		c.forwardingInput = false
		if err != nil {
			break
		}
	}
	d.continuation = false
	if err != nil && errors.Is(err, context.DeadlineExceeded) {
		deadline, _ := barrier.Deadline()
		fatal := &StartError{Code: "input-barrier-timeout", Deadline: deadline, Cause: err}
		c.w.channels.fail(fatal)
		return fatal
	}
	if err != nil {
		if errors.Is(err, context.Canceled) && (d.life.kind != "" || d.gate != g) && c.w.channels.Err() == nil {
			return nil
		}
		return err
	}
	// The same owner authorizes the first private write. A lifecycle accepted
	// before this selection suppresses it; a selected write finishes exactly.
	d.selection = "continue"
	defer func() { d.selection = "" }()
	err = c.work(ctx, false, func(job context.Context) error {
		if err := c.selectPublication(job); err != nil {
			return err
		}
		return SendPrivate(job, c.w.Control, protocol.PrivateContinue{OperationID: g.request.OperationID, GateID: g.id})
	})
	if errors.Is(err, errWorkInterrupted) {
		return nil
	}
	return err
}

// selectWrite runs only on the fact owner, before its publication reply.
func (d *operationDispatch) selectWrite(c *Coordinator) error {
	if d.selection == "continue" {
		if d.life.kind != "" || d.gate == nil || d.gate.request == nil {
			return errWorkInterrupted
		}
		d.gate.sent = true
		return nil
	}
	if d.selection != "result" {
		return nil
	}
	kind, reason := d.life.outcome()
	m := d.completed
	if m == nil {
		return fmt.Errorf("result selection lacks completed state")
	}
	switch kind {
	case "completed":
		d.result = protocol.OperationCompleted{OperationID: m.OperationID, Status: m.Status, Cwd: m.PhysicalCWD, InspectionResults: []protocol.InspectionResult{}}
	case "cancelled":
		status := m.Status
		d.result = protocol.OperationCancelled{OperationID: m.OperationID, Status: &status, Cwd: m.PhysicalCWD, Reason: reason}
	case "finalized":
		d.result = protocol.OperationFinalized{OperationID: m.OperationID, Cwd: m.PhysicalCWD, Reason: reason, InspectionResults: []protocol.InspectionResult{}}
	default:
		return fmt.Errorf("unresolved operation outcome %q", kind)
	}
	d.life.committed = true
	return nil
}

func (c *Coordinator) publicRequest(ctx context.Context, preCancelable bool, interrupt context.CancelFunc, request protocol.Message) error {
	switch m := request.(type) {
	case *protocol.Cancel:
		if m.OperationID == c.recent {
			return nil
		}
	case *protocol.Finalize:
		if m.OperationID == c.recent {
			return nil
		}
	}
	if d := c.running; d != nil && d.ending {
		switch m := request.(type) {
		case *protocol.Continue:
			// Ending cannot authorize a private write or bypass gate admission.
			return d.accept(c, request, nil)
		case *protocol.Execute:
			if d.crossedExecute != "" || m.OperationID == d.life.operationID {
				return fmt.Errorf("unexpected crossed execute during shell end")
			}
			d.crossedExecute = m.OperationID
			return nil
		case *protocol.Cancel:
			if m.OperationID == d.life.operationID || (d.crossedExecute != "" && m.OperationID == d.crossedExecute) {
				return nil
			}
		case *protocol.Finalize:
			if m.OperationID == d.life.operationID {
				return nil
			}
		case *protocol.Resize, *protocol.Shutdown:
			return nil
		}
		return fmt.Errorf("unexpected public request during shell end")
	}
	if resize, ok := request.(*protocol.Resize); ok {
		if c.resize != nil {
			return &startFactError{fmt.Errorf("multiple outstanding resizes")}
		}
		// Decode at most one pending request. Select/apply it only after the
		// prior resize I/O joins; its acknowledgement may already be visible.
		c.resize = resize
		return nil
	}
	if c.running != nil {
		return c.running.accept(c, request, interrupt)
	}
	m, ok := request.(*protocol.Cancel)
	if !ok || m.OperationID != c.active.request.OperationID || c.active.started {
		return &startFactError{fmt.Errorf("unexpected public request during start")}
	}
	c.active.attemptMu.Lock()
	defer c.active.attemptMu.Unlock()
	if c.active.cancel != nil {
		return &startFactError{fmt.Errorf("duplicate cancellation during start")}
	}
	c.active.cancel = m
	if preCancelable && !c.active.attempted {
		if interrupt != nil {
			interrupt()
		}
		return errPreStartCancel
	}
	return nil
}

// endControlled requires the explicit owning termination/reap boundary. A
// timeout has already been selected; late input_close is consumed without ack.
func (c *Coordinator) endControlled(ctx context.Context, d *operationDispatch) error {
	d.ending = true
	if d.proof != nil {
		d.proof.cancel()
	}
	d.beginCleanup()
	phase := d.cleanup
	if d.peers == nil || d.peers.endShell == nil {
		return fmt.Errorf("missing shell-end cleanup/reap owner")
	}
	timeout := d.life.timeout != ""
	c.phase = awaitInputClose
	if err := c.work(phase, false, func(job context.Context) error { return d.peers.endShell(job, d.life.operationID, timeout) }); err != nil {
		return err
	}
	for !c.exitedEOF {
		m, err := c.result(phase)
		if errors.Is(err, io.EOF) && c.exitedEOF {
			break
		}
		if err != nil {
			return err
		}
		switch m.(type) {
		case *protocol.PrivateInputClose:
			if !timeout {
				return fmt.Errorf("return after selected shell end")
			}
		case *protocol.PrivateShellExit:
		case *protocol.PrivateGateReady, *protocol.PrivateGateContinued, *protocol.PrivateGateInterrupted:
		default:
			return fmt.Errorf("unexpected shell-end result")
		}
	}
	if d.life.shellExit == nil {
		return fmt.Errorf("shell-end requires selected shell reap fact")
	}
	if c.w.command != nil {
		select {
		case <-c.w.reaped:
			if c.w.waitErr != nil {
				return c.w.waitErr
			}
		case <-phase.Done():
			return phase.Err()
		}
	}
	exited := d.life.shellExit
	if err := c.work(phase, false, c.finishSplit); err != nil {
		return err
	}
	ended := true
	if err := c.work(phase, false, func(job context.Context) error {
		_, err := c.r.boundary(job, true, func(offset int64) error {
			return c.w.channels.sendEvent(job, "operation-ended", func(h protocol.Header) protocol.Message {
				if timeout || d.gate != nil {
					code, message := d.life.timeout, "Operation did not return within its lifecycle grace"
					if !timeout {
						code, message = "shell-ended-unresolved", "Shell ended with an unresolved operation gate"
					}
					h.Type = "operation_failed"
					return protocol.OperationFailed{Header: h, OperationID: d.life.operationID, Cwd: exited.CWD, OutputStart: c.active.outputStart, OutputThrough: offset, Code: code, Message: message, ShellEnded: &ended}
				}
				h.Type = "operation_completed"
				return protocol.OperationCompleted{Header: h, OperationID: d.life.operationID, Cwd: exited.CWD, Status: exited.Status, OutputStart: c.active.outputStart, OutputThrough: offset, InspectionResults: []protocol.InspectionResult{}, ShellEnded: &ended}
			})
		})
		return err
	}); err != nil {
		return err
	}
	c.w.CWD = exited.CWD
	c.phase = 0
	if err := c.work(phase, false, func(job context.Context) error {
		if err := c.w.channels.sendEvent(job, "draining", func(h protocol.Header) protocol.Message {
			return protocol.Draining{Header: h, Reason: "shell_ended", OutputThrough: c.w.OutputThrough}
		}); err != nil {
			return err
		}
		if err := c.w.channels.terminal.CloseWrite(); err != nil {
			return err
		}
		return c.w.channels.sendEvent(job, "closed", func(h protocol.Header) protocol.Message {
			return protocol.Closed{Header: h, Reason: "shell_ended", OutputThrough: c.w.OutputThrough}
		})
	}); err != nil {
		return err
	}
	if err := c.private.close(phase); err != nil {
		return err
	}
	if err := c.r.Close(phase); err != nil {
		return err
	}
	if err := c.w.Close(phase); err != nil {
		return err
	}
	c.w.channels.Close()
	c.recent = d.life.operationID
	c.active = nil
	return nil
}

func (d *operationDispatch) beginCleanup() {
	if d.cleanup == nil {
		d.cleanup, d.endCleanup = context.WithTimeout(d.ctx, lifecycleGrace)
	}
}

// Proof/signal completion can cross expiry after the preceding owner check.
// Only the actual grace cancellation can select timeout; a census error alone
// remains fatal, and enclosing session failure keeps its original cause.
func (d *operationDispatch) signalError(cause error) error {
	if cause != nil && (errors.Is(cause, context.Canceled) || errors.Is(cause, context.DeadlineExceeded)) {
		selected, err := d.life.selectTimeout(time.Now())
		if err != nil {
			return err
		}
		if selected {
			d.beginCleanup()
			return errLifecycleEnd
		}
	}
	return cause
}
