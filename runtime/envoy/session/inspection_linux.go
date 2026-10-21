//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"

	"github.com/omry/omegaflow/runtime/envoy/inspection"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// The existing dispatcher owns acceptance. A handle supplies only joined
// process facts; even a complete reply is not an accepted operation result.
type inspectionWorker interface {
	Done() <-chan struct{}
	Result() ([]protocol.InspectionResult, error)
	Kill() error
	Reap(context.Context) error
}

type inspectionRun struct {
	worker  inspectionWorker
	stop    context.Context
	endStop context.CancelFunc
	killErr error
	results []protocol.InspectionResult
	failure *inspection.Failure
}

var errInspectionCancelTimeout = errors.New("inspection worker was not reaped within cancellation bound")

// The worker's expired stop epoch remains the failure deadline. A diagnostic
// is best effort: never wait for telemetry serialization, and use only the
// existing individual control-write bound when the lock is available.
func (c *Coordinator) fatalInspection(stop context.Context, cause error) error {
	deadline, _ := stop.Deadline()
	failure := &StartError{Code: "inspection-cancel-timeout", Deadline: deadline, Cause: cause}
	channels := c.w.channels
	if channels.writeMu.TryLock() {
		_ = channels.sendLocked(context.WithoutCancel(stop), protocol.Diagnostic{
			Header: channels.header("diagnostic"), Severity: "fatal", Code: failure.Code, Message: "Operation failed",
		})
		channels.writeMu.Unlock()
	}
	channels.fail(failure)
	return failure
}

func validateInspectionPlan(request []protocol.Inspection, resolved []protocol.ResolvedInspection) error {
	if len(request) != len(resolved) {
		return fmt.Errorf("resolved inspection count differs from request")
	}
	for i, p := range resolved {
		r := request[i]
		if p.InspectionID != r.InspectionID || p.Kind != r.Kind || p.ProducerID != r.ProducerID || p.OutputID != r.OutputID {
			return fmt.Errorf("resolved inspection does not match admitted request")
		}
	}
	return nil
}

func (d *operationDispatch) stopInspection() {
	i := d.inspection
	if i == nil || i.worker == nil || i.stop != nil {
		return
	}
	// This is the protocol's existing worker-cancellation epoch. Neither the
	// completed cleanup epoch nor an individual pipe write owns this bound.
	i.stop, i.endStop = context.WithTimeout(context.WithoutCancel(d.ctx), lifecycleGrace)
	i.killErr = i.worker.Kill()
}

func (d *operationDispatch) inspectionDeadline() <-chan struct{} {
	if d.inspection != nil && d.inspection.worker != nil && d.inspection.stop != nil {
		return d.inspection.stop.Done()
	}
	return nil
}

func (c *Coordinator) inspectionDeadline() <-chan struct{} {
	if c.running != nil {
		return c.running.inspectionDeadline()
	}
	return nil
}

func (c *Coordinator) inspectionDone() <-chan struct{} {
	if c.running != nil && c.running.inspection != nil && c.running.inspection.worker != nil {
		return c.running.inspection.worker.Done()
	}
	return nil
}

func (d *operationDispatch) inspectionResults() []protocol.InspectionResult {
	if d.inspection != nil {
		return append([]protocol.InspectionResult{}, d.inspection.results...)
	}
	return []protocol.InspectionResult{}
}

// disposeInspection is also used on transport/private/session failure. It
// reuses an already-started cancellation bound and never claims reap from Kill.
func (d *operationDispatch) disposeInspection() error {
	i := d.inspection
	if i == nil {
		return nil
	}
	defer func() {
		if i.endStop != nil {
			i.endStop()
		}
	}()
	if i.worker == nil {
		return nil
	}
	d.stopInspection()
	if err := i.worker.Reap(i.stop); err != nil {
		return errors.Join(errInspectionCancelTimeout, err, i.killErr)
	}
	i.worker = nil
	return i.killErr
}

func (c *Coordinator) inspectCompleted(ctx context.Context, d *operationDispatch) error {
	if d.life.kind == "cancel" || len(d.completed.Inspections) == 0 {
		return nil // Cancellation selected during cleanup never launches a worker.
	}
	var worker inspectionWorker
	var err error
	if c.startInspection != nil {
		worker, err = c.startInspection(d.completed.Inspections)
	} else {
		worker, err = inspection.StartWorker(d.completed.Inspections)
	}
	if err != nil {
		return err
	}
	i := &inspectionRun{worker: worker}
	d.inspection = i
	// This job observes disposal only. The dispatcher still processes controls
	// and exclusively chooses the winner after the joined job returns.
	if err := c.work(ctx, false, worker.Reap); err != nil {
		return err
	}
	if err := d.acceptInspection(c); err != nil {
		return err
	}
	if i.worker != nil {
		return fmt.Errorf("inspection reap lacks joined completion")
	}
	return nil
}

// Joined completion is an ordinary fact of the existing owner, including
// while another I/O job is pending. Reap cannot leave its cancellation timer
// armed during later publication or resize delivery.
func (d *operationDispatch) acceptInspection(c *Coordinator) error {
	i := d.inspection
	if i == nil || i.worker == nil {
		return nil
	}
	if i.stop != nil && i.stop.Err() != nil {
		return errors.Join(errInspectionCancelTimeout, i.stop.Err())
	}
	select {
	case <-i.worker.Done():
	default:
		return nil
	}
	worker := i.worker
	i.worker = nil
	if i.endStop != nil {
		i.endStop()
	}
	if i.killErr != nil {
		return i.killErr
	}
	if d.life.kind != "cancel" {
		var err error
		i.results, err = worker.Result()
		if err != nil && !errors.As(err, &i.failure) {
			return err
		}
	}
	return d.selectWrite(c)
}

func (d *operationDispatch) inspectionOutcome(m *protocol.PrivateCompleted) protocol.Message {
	if d.inspection != nil && d.inspection.failure != nil {
		e := d.inspection.failure
		return protocol.OperationFailed{OperationID: m.OperationID, Cwd: m.PhysicalCWD, Code: e.Code, Message: e.Message}
	}
	return nil
}
