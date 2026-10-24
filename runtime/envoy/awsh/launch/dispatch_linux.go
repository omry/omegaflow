//go:build linux

package launch

import (
	"os"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// This is a one-event admission projection of the locked Session phase. Publish
// before a result/helper write can elicit a reply. The sole dispatcher consumes
// it once, joins that write, then invokes the existing locked phase handler.
type dispatchAdmission struct{ kind, operation string }

func (s *Session) armAdmission(kind, operation string) {
	if s.parentControlled {
		s.admission.Store(&dispatchAdmission{kind, operation})
	}
}

// AdmitControl refuses premature, duplicate and wrong-operation controls even
// while the current Session method is blocked in I/O with startMu held.
func (s *Session) AdmitControl(message protocol.PrivateMessage) bool {
	kind, operation := "", ""
	switch m := message.(type) {
	case *protocol.PrivateExecute, *protocol.PrivateShutdown:
		kind = "idle"
	case *protocol.PrivateStartRelease:
		kind, operation = "start_release", m.OperationID
	case *protocol.PrivateStartedAck:
		kind, operation = "started_ack", m.OperationID
	case *protocol.PrivateInputClosed:
		kind, operation = "input_closed", m.OperationID
	case *protocol.PrivateContinue:
		value, ok := s.gateControls.Load(gateKey{m.OperationID, m.GateID})
		if !s.parentControlled || !ok {
			return false
		}
		gate := value.(*gateControl)
		if !gate.admitted.CompareAndSwap(false, true) {
			return false
		}
		gate.continueGate <- struct{}{}
		return true
	default:
		return false
	}
	r := s.admission.Load()
	return r != nil && r.kind == kind && r.operation == operation && s.admission.CompareAndSwap(r, nil)
}

// AdmitSignal preserves the same one-event rule for the reserved release signal.
func (s *Session) AdmitSignal(signal os.Signal) bool {
	r := s.admission.Load()
	return signal == syscall.SIGUSR1 && r != nil && r.kind == "signal" && s.admission.CompareAndSwap(r, nil)
}

// HasActiveOperation distinguishes recoverable source rejection after BeginStart
// returns. Call only after its I/O job joins, before starting the next method.
func (s *Session) HasActiveOperation() bool {
	s.startMu.Lock()
	defer s.startMu.Unlock()
	return s.active != nil
}
