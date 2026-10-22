//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const inputBarrierBudget = 5 * time.Second
const operationStartBudget = 5 * time.Second
const prestartDeliveryBudget = 5 * time.Second

// SplitSetup is the owning setup/rollback boundary. Implementations must join
// their I/O on cancellation and retain partial resources until Rollback.
// B5 supplies actual FIFO ownership; deterministic peers exercise B3 ordering.
type SplitSetup interface {
	Prepare(context.Context, protocol.Execute) (stdout, stderr string, err error)
	Rollback(context.Context, string) error
}

// StartShellExitPhase identifies the original Envoy epoch that governs the
// private EOF/reap and any pre-start rollback after an empty-ID shell exit.
type StartShellExitPhase string

const (
	StartShellExitInputBarrier     StartShellExitPhase = "terminal-input-barrier"
	StartShellExitPrestartDelivery StartShellExitPhase = "pre-start-result-delivery"
	StartShellExitOperationStart   StartShellExitPhase = "operation-start"
)

// StartOutcome preserves facts for the later ordinary/lifecycle owner. A queued
// cancel is actionable only after Started; it never becomes a private frame.
type StartOutcome struct {
	Started           bool
	ReleasedAt        time.Time
	OutputStart       int64
	QueuedCancel      *protocol.Cancel
	ShellExit         *protocol.PrivateShellExit
	ShellExitDeadline time.Time
	ShellExitPhase    StartShellExitPhase
	ShellExitEOF      bool
}

// StartError retains the controlling epoch for the caller's existing teardown.
// Deadline is the original absolute deadline, not a fresh cleanup allowance.
type StartError struct {
	Code     string
	Deadline time.Time
	Cause    error
}

func (e *StartError) Error() string { return e.Code + ": " + e.Cause.Error() }
func (e *StartError) Unwrap() error { return e.Cause }

type startingOperation struct {
	request        protocol.Execute
	attemptMu      sync.Mutex
	attempted      bool
	cancel         *protocol.Cancel
	setup          bool
	splitAttached  atomic.Bool
	rollbackTried  bool
	started        bool
	outputStart    int64
	releasedAt     time.Time
	shellExitPhase StartShellExitPhase
}

type startPhase uint8

const (
	awaitSubmit startPhase = iota + 1
	awaitPrepared
	awaitStarted
	awaitReleased
	awaitInputClose
	awaitCompleted
)

// Coordinator serializes operation facts on the existing channel owners.
// Its private pump is retained across starts and ordinary completion.
type Coordinator struct {
	w               *Workload
	r               *Relay
	private         *privateResults
	setup           SplitSetup
	active          *startingOperation
	pending         *privateResult
	exited          *protocol.PrivateShellExit
	exitedDeadline  time.Time
	exitedPhase     StartShellExitPhase
	exitedEOF       bool
	eofAccepted     chan struct{}
	busy            atomic.Bool
	phase           startPhase
	publication     chan chan error
	forwardingInput bool
	resize          *protocol.Resize
	resizing        bool
	running         *operationDispatch
	recent          string
	startInspection func([]protocol.ResolvedInspection) (inspectionWorker, error)
	failure         error
	failureDisposed bool
}

func NewCoordinator(ctx context.Context, w *Workload, r *Relay, setup SplitSetup) (*Coordinator, error) {
	if w == nil || w.channels == nil || w.Control == nil || r == nil || r.w != w || r.ctx.Err() != nil || w.channels.Err() != nil {
		return nil, fmt.Errorf("coordinator requires the ready workload's live relay")
	}
	p, err := readResults(ctx, w)
	if err != nil {
		return nil, err
	}
	return &Coordinator{w: w, r: r, private: p, setup: setup, eofAccepted: make(chan struct{}), publication: make(chan chan error)}, nil
}

// Close joins the private pump after the caller has joined its active operation.
// The same pump remains the result owner through ordinary completion/shutdown.
func (c *Coordinator) Close(ctx context.Context) error {
	if !c.busy.CompareAndSwap(false, true) {
		return fmt.Errorf("join active coordinator operation before closing private I/O")
	}
	defer c.busy.Store(false)
	return c.closeResources(ctx)
}

func (c *Coordinator) closeResources(ctx context.Context) error {
	var relayErr error
	if owner := c.splitResources(); owner != nil && owner.operation != nil {
		relayErr = c.r.Close(ctx)
	}
	return errors.Join(relayErr, c.releaseResources(ctx))
}

// releaseResources follows the relay join. It does not repeat a transport
// failure as if it were a failure to dispose of the retained split resources.
func (c *Coordinator) releaseResources(ctx context.Context) error {
	var disposal error
	if owner := c.splitResources(); owner != nil && owner.operation != nil {
		// Teardown is not successful completion or EOF evidence. The caller's
		// existing bound remains in force even when it has already expired.
		disposal = owner.dispose(ctx, owner.operation.id)
	}
	return errors.Join(disposal, c.private.close(ctx))
}

var errPreStartCancel = errors.New("cancel accepted before private execute attempt")
var errIdleShellExit = errors.New("shell exited before private execute attempt")

type startFactError struct{ error }

func (c *Coordinator) expected(m protocol.PrivateMessage) bool {
	id := c.active.request.OperationID
	if c.running != nil {
		switch v := m.(type) {
		case *protocol.PrivateGateReady:
			return c.phase == awaitInputClose && v.OperationID == id
		case *protocol.PrivateGateContinued:
			return c.phase == awaitInputClose && v.OperationID == id
		case *protocol.PrivateGateInterrupted:
			return c.phase == awaitInputClose && v.OperationID == id
		case *protocol.PrivateShellExit:
			return v.ActiveOperationID == id
		}
	}
	switch v := m.(type) {
	case *protocol.PrivateSubmit:
		return c.phase == awaitSubmit && v.OperationID == id
	case *protocol.PrivateRejected:
		return c.phase == awaitSubmit && v.OperationID == id
	case *protocol.PrivateStartPrepared:
		return c.phase == awaitPrepared && v.OperationID == id
	case *protocol.PrivateStarted:
		return c.phase == awaitStarted && v.OperationID == id
	case *protocol.PrivateStartReleased:
		return c.phase == awaitReleased && v.OperationID == id
	case *protocol.PrivateInputClose:
		return c.phase == awaitInputClose && v.OperationID == id
	case *protocol.PrivateCompleted:
		return c.phase == awaitCompleted && v.OperationID == id
	}
	return false
}

type executeWriter struct {
	helper.DeadlineWriter
	ctx context.Context
	op  *startingOperation
}

func (w executeWriter) Write(data []byte) (int, error) {
	w.op.attemptMu.Lock()
	if err := w.ctx.Err(); err != nil {
		w.op.attemptMu.Unlock()
		return 0, err
	}
	w.op.attempted = true
	w.op.attemptMu.Unlock()
	return w.DeadlineWriter.Write(data)
}

// work keeps the one public reader responsive while bounded I/O is in flight.
// It always joins that I/O before a following phase can reuse its descriptors.
func (c *Coordinator) work(ctx context.Context, preCancelable bool, run func(context.Context) error) error {
	job, cancelCause := context.WithCancelCause(ctx)
	cancel := func() { cancelCause(errWorkInterrupted) }
	defer cancel()
	done := make(chan struct{})
	var workErr error
	go func() { defer close(done); workErr = run(job) }()
	_, err := c.wait(ctx, preCancelable, cancel, done, false)
	cancel()
	<-done
	var terminalClose error
	if c.exited != nil {
		terminalClose = c.w.Control.Close()
		if errors.Is(terminalClose, os.ErrClosed) {
			terminalClose = nil
		}
	}
	return errors.Join(err, helper.WithContextCause(ctx, workErr), terminalClose)
}

func (c *Coordinator) privateFact(ctx context.Context, interrupt context.CancelFunc, result privateResult) (protocol.PrivateMessage, bool, error) {
	if result.err != nil {
		if errors.Is(result.err, io.EOF) && c.exited != nil {
			c.exitedEOF = true
			if c.eofAccepted != nil {
				close(c.eofAccepted)
			}
			return nil, true, nil
		}
		return nil, false, &startFactError{result.err}
	}
	if c.exited != nil {
		return nil, false, &startFactError{fmt.Errorf("private result after accepted shell_exit")}
	}
	if report, ok := result.message.(*protocol.PrivateProtocolError); ok {
		return nil, false, &startFactError{fmt.Errorf("private protocol_error %s: %s", report.Code, report.Message)}
	}
	if c.running != nil {
		gate := c.running.gate
		captureContinue := !c.running.continueDeadline.IsZero() && gate != nil && gate.request != nil &&
			c.w.channels.InputThrough() < gate.request.InputThrough
		if err := c.running.observe(c, result.message, interrupt); err != nil {
			return nil, false, &startFactError{err}
		}
		if _, ended := result.message.(*protocol.PrivateShellExit); ended && captureContinue {
			// The continue watermark has not completed. Later cleanup cannot
			// replace its original bound on private EOF, reap and split close.
			c.exitedDeadline = c.running.continueDeadline
			c.exitedPhase = StartShellExitInputBarrier
		}
	}
	if proposal, ok := result.message.(*protocol.PrivateInputClose); ok && c.expected(proposal) {
		if err := c.w.channels.ResumeInputContext(ctx, ""); err != nil {
			return nil, false, &startFactError{err}
		}
	}
	if exited, ok := result.message.(*protocol.PrivateShellExit); ok {
		if c.running != nil {
			c.exited = exited
			if interrupt == nil {
				if err := c.w.Control.Close(); err != nil && !errors.Is(err, os.ErrClosed) {
					return nil, false, err
				}
			}
			if err := c.w.channels.ResumeInputContext(ctx, ""); err != nil {
				return nil, false, err
			}
			return exited, false, nil
		}
		c.active.attemptMu.Lock()
		before := !c.active.attempted
		if before && interrupt != nil {
			interrupt()
		}
		c.active.attemptMu.Unlock()
		if before && exited.ActiveOperationID == "" {
			c.exited = exited
			if interrupt == nil {
				if err := c.w.Control.Close(); err != nil && !errors.Is(err, os.ErrClosed) {
					return nil, false, err
				}
			}
			c.exitedDeadline, _ = ctx.Deadline()
			c.exitedPhase = c.active.shellExitPhase
			return nil, false, errIdleShellExit
		}
		return nil, false, &startFactError{fmt.Errorf("shell_exit before accepted start_released")}
	}
	return result.message, false, nil
}

// selectPublication hands result selection to the same fact owner after the
// worker has acquired telemetry. It adds no reader or independently committing
// publisher: queued private facts are checked before the owner grants selection.
func (c *Coordinator) selectPublication(ctx context.Context) error {
	reply := make(chan error, 1)
	select {
	case c.publication <- reply:
	case <-ctx.Done():
		return ctx.Err()
	}
	select {
	case err := <-reply:
		return err
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (c *Coordinator) wait(ctx context.Context, preCancelable bool, interrupt context.CancelFunc, done <-chan struct{}, wantResult bool) (protocol.PrivateMessage, error) {
	var finished bool
	var finishErr error
	var publication chan error
	var selectedPublication bool
	for {
		fatalEvents := c.private.fatal
		if wantResult && c.pending != nil {
			select {
			case result := <-fatalEvents:
				_, consumed, err := c.privateFact(ctx, interrupt, result)
				if !consumed && err != nil {
					return nil, err
				}
			default:
			}
			if c.resize != nil && !c.resizing && ctx.Err() == nil {
				if err := c.applyPendingResize(ctx); err != nil {
					return nil, err
				}
				continue
			}
			m := c.pending.message
			c.pending = nil
			if !c.expected(m) {
				return nil, &startFactError{fmt.Errorf("private result does not match start phase %d", c.phase)}
			}
			return m, nil
		}
		privateEvents := c.private.events
		publicEvents := c.w.channels.Events()
		controlEvents := c.w.channels.Controls()
		var proofDone, graceDone, cleanupDone <-chan struct{}
		if d := c.running; d != nil {
			if d.cleanup != nil {
				cleanupDone = d.cleanup.Done()
			}
			if d.proof != nil {
				proofDone = d.proof.done
			}
			if d.life.grace != nil && d.life.inputClose == nil && !d.ending {
				graceDone = d.life.grace.Done()
			}
		}
		if c.forwardingInput || c.resizing {
			publicEvents = nil // One admitted I/O request; private facts stay observable.
		}
		if c.pending != nil {
			privateEvents = nil // Retain one early valid next-phase fact.
		}
		var result privateResult
		select {
		case result = <-privateEvents:
		case result = <-fatalEvents:
		default:
			// A ready private fact wins over work completion, expiry and a
			// recoverable result selection. A selected frame cannot be retracted.
			if !finished {
				select {
				case <-done:
					finished = true
				default:
				}
			}
			if finished {
				return nil, finishErr
			}
			if d := c.running; d != nil {
				if err := d.step(c); err != nil {
					return nil, err
				}
				if d.proof != nil {
					proofDone = d.proof.done
				} else {
					proofDone = nil
				}
				if wantResult && d.gate != nil && d.gate.request != nil && !d.gate.sent && d.life.kind == "" {
					return nil, &continueOperation{}
				}
			}
			if c.resize != nil && !c.resizing && publication == nil && !selectedPublication && ctx.Err() == nil {
				if err := c.applyPendingResize(ctx); err != nil {
					return nil, err
				}
				continue
			}
			if publication != nil {
				if err := ctx.Err(); err != nil {
					return nil, err
				}
				if err := c.w.channels.Err(); err != nil {
					return nil, err
				}
				if err := c.private.ctx.Err(); err != nil {
					return nil, &startFactError{err}
				}
				ready, err := c.private.publicationReady()
				if err != nil {
					return nil, &startFactError{err}
				}
				if ready {
					var selectionErr error
					if c.running != nil {
						selectionErr = c.running.selectWrite(c)
					}
					publication <- selectionErr
					publication = nil
					// Finish the selected write before spending this phase on resize.
					selectedPublication = true
					continue
				}
			}
			select {
			case <-done:
				finished = true
				continue
			case <-ctx.Done():
				finished, finishErr = true, ctx.Err()
				continue
			case <-c.w.channels.Done():
				finished, finishErr = true, c.w.channels.Err()
				continue
			case <-c.private.ctx.Done():
				finished, finishErr = true, &startFactError{c.private.ctx.Err()}
				continue
			case publication = <-c.publication:
				continue
			case <-c.private.changed:
				continue
			case result = <-privateEvents:
			case result = <-fatalEvents:
			case e := <-publicEvents:
				if len(e.Input) != 0 {
					if c.active.started && c.phase == awaitInputClose {
						return nil, &operationInput{e}
					}
					continue // Already-read idle/old-operation input is never replayed.
				}
				return nil, &startFactError{fmt.Errorf("typed request in terminal queue")}
			case e := <-controlEvents:
				if err := c.publicRequest(ctx, preCancelable, interrupt, e.Message); err != nil {
					return nil, err
				}
				continue
			case <-proofDone:
				continue
			case <-graceDone:
				continue
			case <-cleanupDone:
				return nil, c.running.cleanup.Err()
			case <-c.inspectionDone():
				continue
			case <-c.inspectionDeadline():
				return nil, errInspectionCancelTimeout
			}
		}
		m, consumed, err := c.privateFact(ctx, interrupt, result)
		if consumed {
			if wantResult && c.running != nil && c.running.ending && c.exitedEOF {
				return nil, io.EOF
			}
			continue
		}
		if err != nil {
			return nil, err
		}
		c.active.attemptMu.Lock()
		attempted := c.active.attempted
		c.active.attemptMu.Unlock()
		if !attempted {
			return nil, &startFactError{fmt.Errorf("private start result before execute attempt")}
		}
		if !c.expected(m) {
			return nil, &startFactError{fmt.Errorf("private result does not match start phase %d", c.phase)}
		}
		if wantResult {
			return m, nil
		}
		result.message = m
		c.pending = &result
	}
}

func (c *Coordinator) result(ctx context.Context) (m protocol.PrivateMessage, err error) {
	return c.wait(ctx, false, nil, nil, true)
}

func (c *Coordinator) send(ctx context.Context, m protocol.PrivateMessage) error {
	return c.work(ctx, false, func(job context.Context) error { return SendPrivate(job, c.w.Control, m) })
}

func (c *Coordinator) fatal(ctx context.Context, code string, cause error, skipDiagnostic bool) error {
	// Cancelling joined I/O may report context cancellation first. Preserve the
	// selected channel error so malformed input, EOF and reset remain distinct.
	cause = errors.Join(cause, c.w.channels.Err())
	var resize *StartError
	if errors.As(cause, &resize) && (resize.Code == "resize-failed" || resize.Code == "input-barrier-timeout") {
		return resize // The relay already selected and closed this fatal failure.
	}
	deadline, _ := ctx.Deadline()
	err := &StartError{Code: code, Deadline: deadline, Cause: helper.WithContextCause(ctx, cause)}
	if !skipDiagnostic {
		_ = c.w.channels.sendEvent(ctx, "diagnostic", func(h protocol.Header) protocol.Message {
			return protocol.Diagnostic{Header: h, Severity: "fatal", Code: code, Message: "Operation failed"}
		})
	}
	c.w.channels.fail(err)
	return err
}

func (c *Coordinator) rollback(ctx context.Context) error {
	if !c.active.setup {
		return nil
	}
	c.active.rollbackTried = true
	err := c.work(ctx, false, func(job context.Context) error {
		var transport error
		if c.active.splitAttached.Load() {
			transport = c.r.stopSplit(job)
			c.active.splitAttached.Store(false)
		}
		return errors.Join(transport, c.setup.Rollback(job, c.active.request.OperationID))
	})
	if !errors.Is(err, errIdleShellExit) {
		c.active.setup = false
	}
	return err
}

func (c *Coordinator) unstarted(ctx context.Context, code, message string) (StartOutcome, error) {
	c.phase = 0
	if err := c.rollback(ctx); err != nil {
		return StartOutcome{}, err
	}
	c.active.attemptMu.Lock()
	beforeCancel := c.active.cancel != nil && !c.active.attempted
	c.active.attemptMu.Unlock()
	err := c.work(ctx, false, func(job context.Context) error {
		if _, err := c.r.boundary(job, false, func(offset int64) error {
			return c.w.channels.sendEvent(job, "operation_failed", func(h protocol.Header) protocol.Message {
				if beforeCancel {
					h.Type = "operation_cancelled"
					return protocol.OperationCancelled{Header: h, OperationID: c.active.request.OperationID, Cwd: c.w.CWD, OutputStart: offset, OutputThrough: offset, Reason: c.active.cancel.Reason}
				}
				return protocol.OperationFailed{Header: h, OperationID: c.active.request.OperationID, Cwd: c.w.CWD, OutputStart: offset, OutputThrough: offset, Code: code, Message: message}
			}, func() error { return c.selectPublication(job) })
		}); err != nil {
			return err
		}
		return c.w.channels.ResumeInputContext(job, "")
	})
	if err == nil {
		c.recent = c.active.request.OperationID
	}
	return StartOutcome{}, err
}

// Start consumes the complete existing start handshake. It does not execute
// ordinary cleanup, signals, gates, inspection or actual split setup itself.
func (c *Coordinator) Start(ctx context.Context, request protocol.Execute) (out StartOutcome, err error) {
	if !c.busy.CompareAndSwap(false, true) {
		return out, fmt.Errorf("coordinator is already in use")
	}
	defer c.busy.Store(false)
	if c.active != nil || c.exited != nil || c.private.ctx.Err() != nil || ctx.Err() != nil {
		return out, fmt.Errorf("start requires idle coordinator and live context")
	}
	if _, err = protocol.EncodeController(request); err != nil {
		return out, err
	}
	c.recent = "" // A newly accepted execute supersedes the previous result.
	c.active = &startingOperation{request: request, shellExitPhase: StartShellExitInputBarrier}
	c.phase = 0
	defer func() {
		if !c.active.started {
			c.active = nil
		}
	}()
	barrier, endBarrier := context.WithTimeout(ctx, inputBarrierBudget)
	err = c.work(barrier, true, func(job context.Context) error { return c.w.channels.HoldInput(job, request.InputThrough) })
	defer endBarrier()
	if err != nil {
		if errors.Is(err, errIdleShellExit) && c.w.channels.Err() == nil {
			return StartOutcome{ShellExit: c.exited, ShellExitDeadline: c.exitedDeadline, ShellExitPhase: c.exitedPhase, ShellExitEOF: c.exitedEOF}, nil
		}
		var fact *startFactError
		if !errors.As(err, &fact) && c.w.channels.Err() == nil && (errors.Is(err, errPreStartCancel) || errors.Is(err, context.DeadlineExceeded)) {
			// The barrier has ended. Bound the complete finite-prefix result
			// publication separately, without renewing an operation-start epoch.
			delivery, endDelivery := context.WithTimeout(c.w.channels.ctx, prestartDeliveryBudget)
			defer endDelivery()
			c.active.shellExitPhase = StartShellExitPrestartDelivery
			out, err = c.unstarted(delivery, "input-barrier-timeout", "Terminal input watermark was not reached")
			if errors.Is(err, errIdleShellExit) && c.w.channels.Err() == nil {
				return StartOutcome{ShellExit: c.exited, ShellExitDeadline: c.exitedDeadline, ShellExitPhase: c.exitedPhase, ShellExitEOF: c.exitedEOF}, nil
			}
			if err != nil {
				code := "prestart-output"
				if deadline, ok := delivery.Deadline(); ok && !time.Now().Before(deadline) {
					code = "prestart-output-timeout"
					err = errors.Join(err, context.DeadlineExceeded)
				}
				err = c.fatal(delivery, code, err, code == "prestart-output-timeout")
			}
			return out, err
		}
		return out, c.fatal(barrier, "input-barrier", err, false)
	}
	endBarrier()
	// Start this epoch before setup, private encoding, or any private byte.
	c.active.shellExitPhase = StartShellExitOperationStart
	phase, end := context.WithTimeout(ctx, operationStartBudget)
	defer end()
	defer func() {
		if err != nil {
			if errors.Is(err, errIdleShellExit) && c.w.channels.Err() == nil {
				if c.active.rollbackTried && c.active.setup {
					err = fmt.Errorf("split rollback did not complete before shell exit")
					c.active.setup = false
				} else if rollbackErr := c.rollback(phase); rollbackErr == nil {
					out, err = StartOutcome{ShellExit: c.exited, ShellExitDeadline: c.exitedDeadline, ShellExitPhase: c.exitedPhase, ShellExitEOF: c.exitedEOF}, nil
					return
				} else {
					err = rollbackErr
				}
			}
			code := "operation-start"
			if errors.Is(helper.WithContextCause(phase, err), context.DeadlineExceeded) {
				code = "operation-start-timeout"
			}
			err = errors.Join(err, c.rollback(phase))
			err = c.fatal(phase, code, err, false)
		}
	}()
	var stdout, stderr string
	if request.ExecutionShape == "split" {
		if c.setup == nil {
			return out, fmt.Errorf("split setup owner is required")
		}
		c.active.setup = true
		err = c.work(phase, true, func(job context.Context) (e error) {
			stdout, stderr, e = c.setup.Prepare(job, request)
			return
		})
		if err != nil {
			if errors.Is(err, errPreStartCancel) {
				return c.unstarted(phase, "", "")
			}
			return out, err
		}
	}
	private := protocol.PrivateExecute{OperationID: request.OperationID, ExecutionShape: request.ExecutionShape, Timing: request.Timing, Publication: request.Publication, Observation: request.Observation, Inspections: request.Inspections, StdoutFIFO: stdout, StderrFIFO: stderr, Source: request.Source}
	frame, err := protocol.EncodePrivate(private, protocol.EnvoyToAwsh)
	if err != nil {
		return out, err
	}
	c.phase = awaitSubmit
	err = c.work(phase, true, func(job context.Context) error {
		return WriteControl(job, executeWriter{c.w.Control, job, c.active}, frame)
	})
	if err != nil {
		if errors.Is(err, errPreStartCancel) {
			return c.unstarted(phase, "", "")
		}
		return out, err
	}
	m, err := c.result(phase)
	if err != nil {
		return out, err
	}
	if rejected, ok := m.(*protocol.PrivateRejected); ok && rejected.OperationID == request.OperationID {
		return c.unstarted(phase, rejected.Code, rejected.Message)
	}
	c.phase = awaitPrepared
	if err = c.work(phase, false, c.r.SubmitTrigger); err != nil {
		return out, err
	}
	m, err = c.result(phase)
	if err != nil {
		return out, err
	}
	c.phase = 0
	if request.ExecutionShape == "split" {
		owner := c.splitResources()
		if owner == nil {
			return out, fmt.Errorf("split source release requires actual FIFO resources")
		}
		if err = c.work(phase, false, func(job context.Context) error {
			c.active.splitAttached.Store(true)
			return c.r.attachSplit(job, owner, request.OperationID)
		}); err != nil {
			return out, err
		}
	}
	if err = c.work(phase, false, func(job context.Context) error { _, e := c.r.Drain(job); return e }); err != nil {
		return out, err
	}
	c.phase = awaitStarted
	if err = c.send(phase, protocol.PrivateStartRelease{OperationID: request.OperationID}); err != nil {
		return out, err
	}
	m, err = c.result(phase)
	if err != nil {
		return out, err
	}
	c.phase = 0
	err = c.work(phase, false, func(job context.Context) error {
		_, e := c.r.boundary(job, false, func(offset int64) error {
			out.OutputStart = offset
			c.active.outputStart = offset
			return c.w.channels.sendEvent(job, "operation_started", func(h protocol.Header) protocol.Message {
				return protocol.OperationStarted{Header: h, OperationID: request.OperationID, OutputStart: offset}
			})
		})
		return e
	})
	if err != nil {
		return out, err
	}
	c.phase = awaitReleased
	if err = c.send(phase, protocol.PrivateStartedAck{OperationID: request.OperationID}); err != nil {
		return out, err
	}
	m, err = c.result(phase)
	if err != nil {
		return out, err
	}
	c.active.releasedAt = time.Now()
	if err = phase.Err(); err != nil {
		return out, err
	}
	if err = c.w.channels.ResumeInput(request.OperationID); err != nil {
		return out, err
	}
	out.ReleasedAt = c.active.releasedAt
	c.active.started, out.Started, out.QueuedCancel = true, true, c.active.cancel
	c.phase = 0
	return out, nil
}
