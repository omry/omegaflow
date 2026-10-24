//go:build linux

package launch

import (
	"sync"
	"sync/atomic"
	"syscall"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestDispatchAdmissionRejectsEarlyAndDuplicateControls(t *testing.T) {
	s := &Session{parentControlled: true}
	if s.AdmitControl(&protocol.PrivateInputClosed{OperationID: "op"}) || s.AdmitSignal(syscall.SIGUSR1) {
		t.Fatal("unarmed event admitted")
	}
	s.armAdmission("input_closed", "op")
	if s.AdmitControl(&protocol.PrivateContinue{OperationID: "op", GateID: "later-owner"}) {
		t.Fatal("unsupported lifecycle control acquired admission")
	}
	if s.AdmitControl(&protocol.PrivateInputClosed{OperationID: "wrong"}) || s.AdmitControl(&protocol.PrivateStartedAck{OperationID: "op"}) {
		t.Fatal("wrong identity or phase consumed admission")
	}
	var winners atomic.Int32
	var join sync.WaitGroup
	for i := 0; i < 32; i++ {
		join.Add(1)
		go func() {
			defer join.Done()
			if s.AdmitControl(&protocol.PrivateInputClosed{OperationID: "op"}) {
				winners.Add(1)
			}
		}()
	}
	join.Wait()
	if winners.Load() != 1 {
		t.Fatal("one phase authorized more than one control", winners.Load())
	}
	s.armAdmission("signal", "op")
	if s.AdmitSignal(syscall.SIGUSR2) || !s.AdmitSignal(syscall.SIGUSR1) || s.AdmitSignal(syscall.SIGUSR1) {
		t.Fatal("reserved signal admission is not exact and single-use")
	}
	s.armAdmission("idle", "")
	if !s.AdmitControl(&protocol.PrivateExecute{}) || s.AdmitControl(&protocol.PrivateShutdown{}) {
		t.Fatal("idle phase admitted concurrent execute/shutdown")
	}
	standalone := &Session{}
	standalone.armAdmission("idle", "")
	if standalone.AdmitControl(&protocol.PrivateExecute{}) {
		t.Fatal("standalone caller acquired controlled dispatch authority")
	}
}
