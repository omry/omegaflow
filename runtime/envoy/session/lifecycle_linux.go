//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const lifecycleGrace = 5 * time.Second

// operationLifecycle is serialized state of the existing Envoy fact owner.
// It owns no reader or worker and publishes nothing. The dispatcher must finish
// mandatory cleanup, reap and output drain before publishing its selected result.
type operationLifecycle struct {
	operationID string
	started     bool
	kind        string
	reason      string
	session     context.Context
	grace       context.Context
	endGrace    context.CancelFunc
	attempted   bool
	inputClose  *protocol.PrivateInputClose
	shellExit   *protocol.PrivateShellExit
	timeout     string
	committed   bool
	failure     error
}

// lifecycleContext preserves session cancellation while excluding an enclosing
// deadline from the lifecycle's own five-second grace epoch.
type lifecycleContext struct {
	context.Context
}

func (lifecycleContext) Deadline() (time.Time, bool) {
	return time.Time{}, false
}

func newOperationLifecycle(operationID string) *operationLifecycle {
	return &operationLifecycle{operationID: operationID}
}

// startReleased consumes the existing start outcome. Queued cancellation is
// actionable here, after public operation_started and private start_released.
// Rejection and pre-start cancellation never create a grace period or signal.
func (l *operationLifecycle) startReleased(ctx context.Context, out StartOutcome) error {
	if l.started || !out.Started || out.ShellExit != nil {
		return fmt.Errorf("lifecycle requires one accepted start_released")
	}
	l.started = true
	if out.QueuedCancel != nil {
		return l.acceptAt(ctx, out.QueuedCancel, out.ReleasedAt)
	}
	return nil
}

func (l *operationLifecycle) close() {
	if l.endGrace != nil {
		l.endGrace()
	}
}

// accept starts the one original grace before any foreground sampling. A
// cancellation during return cleanup is local and cannot create a grace.
func (l *operationLifecycle) accept(ctx context.Context, request protocol.Message) error {
	return l.acceptAt(ctx, request, time.Time{})
}

func (l *operationLifecycle) acceptAt(ctx context.Context, request protocol.Message, accepted time.Time) error {
	if _, err := protocol.EncodeController(request); err != nil {
		return err
	}
	var kind, id, reason string
	switch m := request.(type) {
	case *protocol.Cancel:
		kind, id, reason = "cancel", m.OperationID, m.Reason
	case *protocol.Finalize:
		kind, id, reason = "finalize", m.OperationID, m.Reason
	default:
		return fmt.Errorf("not a lifecycle request")
	}
	if id != l.operationID || !l.started {
		return fmt.Errorf("lifecycle request does not name the started operation")
	}
	if l.committed || l.timeout != "" || l.shellExit != nil {
		return nil // The already-selected result cannot be replaced.
	}
	if l.failure != nil {
		return l.failure
	}
	if kind == "finalize" {
		if l.inputClose != nil {
			return nil // The explicit observed-return-wins crossing.
		}
		if l.kind != "" {
			return fmt.Errorf("unexpected finalize during %s", l.kind)
		}
	}
	if kind == "cancel" && l.kind == "cancel" {
		return fmt.Errorf("duplicate operation cancellation")
	}
	l.kind, l.reason = kind, reason
	if l.inputClose == nil && l.grace == nil {
		l.session = ctx
		if accepted.IsZero() {
			accepted = time.Now()
		}
		l.grace, l.endGrace = context.WithDeadline(lifecycleContext{Context: ctx}, accepted.Add(lifecycleGrace))
	}
	return nil
}

// observeReturn accepts only the existing validated Bash-local facts. A valid
// late input_close is consumed but never acknowledged after timeout selection.
// The caller retains ordinary private framing and pipe-order validation.
func (l *operationLifecycle) observeReturn(message protocol.PrivateMessage) (acknowledge bool, err error) {
	if _, err = protocol.EncodePrivate(message, protocol.AwshToEnvoy); err != nil {
		return false, err
	}
	if !l.started || l.committed || l.failure != nil {
		return false, fmt.Errorf("return fact outside active lifecycle")
	}
	switch m := message.(type) {
	case *protocol.PrivateInputClose:
		if m.OperationID != l.operationID || l.inputClose != nil || l.shellExit != nil {
			return false, fmt.Errorf("unexpected lifecycle input_close")
		}
		l.inputClose = m
		return l.timeout == "", nil
	case *protocol.PrivateShellExit:
		if m.ActiveOperationID != l.operationID || l.shellExit != nil {
			return false, fmt.Errorf("unexpected lifecycle shell_exit")
		}
		l.shellExit = m
		return false, nil // Reap evidence, never an input_closed authorization.
	default:
		return false, fmt.Errorf("not a lifecycle return fact")
	}
}

// selectTimeout is called by the same fact owner after its ready-fact check.
// A context cancellation other than this deadline is session failure, not a
// synthetic timeout result. A returned shell already selected by that owner wins.
func (l *operationLifecycle) selectTimeout(now time.Time) (bool, error) {
	if l.failure != nil {
		return false, l.failure
	}
	if l.timeout != "" {
		return true, nil
	}
	if l.committed || l.inputClose != nil || l.shellExit != nil || l.grace == nil {
		return false, nil
	}
	deadline, _ := l.grace.Deadline()
	if err := l.session.Err(); err != nil {
		parentDeadline, hasParentDeadline := l.session.Deadline()
		if !errors.Is(err, context.DeadlineExceeded) || !hasParentDeadline || parentDeadline.Before(deadline) || now.Before(deadline) {
			return false, err
		}
	}
	if errors.Is(l.grace.Err(), context.Canceled) {
		return false, l.grace.Err()
	}
	if now.Before(deadline) {
		return false, nil
	}
	l.timeout = l.kind + "-timeout"
	return true, nil
}

func (l *operationLifecycle) signalNeeded() bool {
	return l.grace != nil && !l.attempted && l.inputClose == nil &&
		l.shellExit == nil && l.timeout == "" && !l.committed && l.failure == nil
}

// outcome is a selection only. No outcome authorizes publication before the
// dispatcher's mandatory cleanup and final output boundary have succeeded.
func (l *operationLifecycle) outcome() (kind, reason string) {
	if l.failure != nil {
		return "", ""
	}
	if l.timeout != "" {
		return l.timeout, ""
	}
	if l.shellExit != nil {
		return "shell-ended", ""
	}
	if l.inputClose == nil {
		return "", ""
	}
	switch l.kind {
	case "cancel":
		return "cancelled", l.reason
	case "finalize":
		return "finalized", l.reason
	default:
		return "completed", ""
	}
}
