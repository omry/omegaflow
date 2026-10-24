//go:build linux

package launch

import (
	"context"
	"errors"
	"os/exec"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestGateControlHasOneAdmissionAndRetainsInterruptedIdentity(t *testing.T) {
	s := &Session{parentControlled: true}
	key := gateKey{"operation", "gate"}
	g := &gateControl{continueGate: make(chan struct{}, 1)}
	s.gateControls.Store(key, g)
	for _, m := range []*protocol.PrivateContinue{{OperationID: "other", GateID: key.gate}, {OperationID: key.operation, GateID: "other"}} {
		if s.AdmitControl(m) {
			t.Fatal("wrong gate identity admitted")
		}
	}
	// The operation can already be accepting completion when an interrupted
	// gate's one late continue arrives. It must not consume completion admission.
	s.armAdmission("input_closed", key.operation)
	var wins atomic.Int32
	var joined sync.WaitGroup
	for i := 0; i < 32; i++ {
		joined.Add(1)
		go func() {
			defer joined.Done()
			if s.AdmitControl(&protocol.PrivateContinue{OperationID: key.operation, GateID: key.gate}) {
				wins.Add(1)
			}
		}()
	}
	joined.Wait()
	if wins.Load() != 1 || len(g.continueGate) != 1 || !s.AdmitControl(&protocol.PrivateInputClosed{OperationID: key.operation}) {
		t.Fatal("gate admission was reused or stole completion")
	}
	s.gateControls.Clear()
	if s.AdmitControl(&protocol.PrivateContinue{OperationID: key.operation, GateID: key.gate}) {
		t.Fatal("completed operation retained gate admission")
	}
}

func TestGateHelperLifetimeWaitAndCancellationJoin(t *testing.T) {
	for _, cancelFirst := range []bool{false, true} {
		t.Run(map[bool]string{false: "exit", true: "cancel"}[cancelFirst], func(t *testing.T) {
			cmd := exec.Command("/bin/cat")
			stdin, err := cmd.StdinPipe()
			if err != nil {
				t.Fatal(err)
			}
			if err = cmd.Start(); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { stdin.Close(); cmd.Process.Kill(); cmd.Wait() })
			fd, _, errno := syscall.Syscall(434, uintptr(cmd.Process.Pid), 0, 0)
			if errno != 0 {
				t.Fatal(errno)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			watch, err := watchHelper(ctx, int(fd))
			if err != nil {
				t.Fatal(err)
			}
			select {
			case <-watch.done:
				t.Fatal("live helper was treated as terminated")
			case <-time.After(20 * time.Millisecond):
			}
			if cancelFirst {
				cancel()
			} else {
				stdin.Close()
			}
			select {
			case <-watch.done:
			case <-time.After(time.Second):
				t.Fatal("lifetime wait did not wake")
			}
			if cancelFirst && !errors.Is(watch.err, context.Canceled) || !cancelFirst && watch.err != nil {
				t.Fatal("wrong lifetime outcome", watch.err)
			}
			watch.stop()
			raw, err := watch.file.SyscallConn()
			if err == nil {
				err = raw.Control(func(uintptr) {})
			}
			if err == nil {
				t.Fatal("pinned helper descriptor retained after join")
			}
		})
	}
}

func TestGateRequiresActiveAcceptedRecord(t *testing.T) {
	s := &Session{}
	if s.WaitGate() == nil || !s.startFailed {
		t.Fatal("gate wait accepted without operation")
	}
	if _, err := watchHelper(context.Background(), -1); err == nil {
		t.Fatal("invalid lifetime descriptor accepted")
	}
	s = &Session{active: &startRecord{request: protocol.PrivateExecute{OperationID: "op"}}, completion: &completionRecord{}}
	s.gateControls.Store(gateKey{"op", "used"}, &gateControl{})
	if err := s.beginGate("used"); err == nil {
		t.Fatal("gate ID reused in same operation")
	}
}
