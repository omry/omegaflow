//go:build linux

package session

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/inspection"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// This test executable hosts the same restricted worker entry as the later
// production assembly. Normalize only initial test-parent descriptors. The
// worker must dispatch first, without inheriting any test/session handles.
func TestMain(m *testing.M) {
	if len(os.Args) == 2 && os.Args[1] == inspection.WorkerModeArgument {
		if err := inspection.RunWorkerMode(os.Args[1:]); err != nil {
			os.Exit(2)
		}
		os.Exit(0)
	}
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		os.Exit(79)
	}
	for _, entry := range entries {
		fd, err := strconv.Atoi(entry.Name())
		if err != nil || fd < 3 {
			continue
		}
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if errno == syscall.EBADF {
			continue
		}
		if errno != 0 {
			os.Exit(79)
		}
		if _, _, errno = syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_SETFD, flags|syscall.FD_CLOEXEC); errno != 0 {
			os.Exit(79)
		}
	}
	os.Exit(m.Run())
}

type inspectionTestWorker struct {
	done       chan struct{}
	killed     chan struct{}
	resultRead chan struct{}
	results    []protocol.InspectionResult
	err        error
	kills      atomic.Int32
	reads      atomic.Int32
}

func newInspectionTestWorker() *inspectionTestWorker {
	return &inspectionTestWorker{done: make(chan struct{}), killed: make(chan struct{}), resultRead: make(chan struct{})}
}
func (w *inspectionTestWorker) Result() ([]protocol.InspectionResult, error) {
	w.reads.Add(1)
	close(w.resultRead)
	return w.results, w.err
}
func (w *inspectionTestWorker) Kill() error {
	if w.kills.Add(1) == 1 {
		close(w.killed)
	}
	return nil
}
func (w *inspectionTestWorker) Reap(ctx context.Context) error {
	select {
	case <-w.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func inspectionFixture(t *testing.T, path string) (*startFixture, []protocol.ResolvedInspection) {
	t.Helper()
	f := startFixtureFor(t, nil)
	request := startRequest()
	request.Observation = "exclusive"
	request.Inspections = []protocol.Inspection{{InspectionID: "file", Kind: "file_exists", Path: path}}
	done := f.start(t, context.Background(), request)
	f.throughStarted(t)
	f.private(t, protocol.PrivateStartReleased{OperationID: request.OperationID})
	if r := finishStart(t, done); r.err != nil || !r.out.Started {
		t.Fatal(r)
	}
	return f, []protocol.ResolvedInspection{{InspectionID: "file", Kind: "file_exists", ResolvedPath: path}}
}
func inspectionReturn(t *testing.T, f *startFixture, plans []protocol.ResolvedInspection) {
	t.Helper()
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	if _, ok := f.readPrivate(t).(*protocol.PrivateInputClosed); !ok {
		t.Fatal("missing input_closed")
	}
	f.private(t, protocol.PrivateCompleted{OperationID: "operation-1", Status: 7, PhysicalCWD: "/tmp", Inspections: plans})
}
func inspectionContext(t *testing.T) context.Context {
	t.Helper()
	ctx, end := context.WithTimeout(context.Background(), 12*time.Second)
	t.Cleanup(end)
	return ctx
}
func awaitInspection(t *testing.T, c <-chan struct{}) {
	t.Helper()
	select {
	case <-c:
	case <-time.After(2 * time.Second):
		t.Fatal("inspection fact not observed")
	}
}

func TestInspectionActualWorkerCompletionFailureAndPlanCorrelation(t *testing.T) {
	for _, scenario := range []string{"success", "missing", "wrong-id", "wrong-kind", "wrong-count"} {
		t.Run(scenario, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "artifact")
			if scenario != "missing" {
				if err := os.WriteFile(path, []byte("payload"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			f, plans := inspectionFixture(t, path)
			done := completeFixture(t, f, inspectionContext(t), func(context.Context, string, int64) error { return nil })
			switch scenario {
			case "wrong-id":
				plans[0].InspectionID = "other"
			case "wrong-kind":
				plans[0].Kind = "produces"
				plans[0].ProducerID = "p"
				plans[0].OutputID = "o"
			case "wrong-count":
				plans = []protocol.ResolvedInspection{}
			}
			inspectionReturn(t, f, plans)
			err := finishCompletion(t, done)
			if scenario == "wrong-id" || scenario == "wrong-kind" || scenario == "wrong-count" {
				if err == nil || f.c.active == nil || f.c.w.channels.Err() == nil {
					t.Fatal("mismatched plan accepted", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			m := nextDispatch(t, f)
			if scenario == "missing" {
				failed, ok := m.(*protocol.OperationFailed)
				if !ok || failed.Code != "inspection-missing" {
					t.Fatal("wrong typed failure", m)
				}
			} else {
				result, ok := m.(*protocol.OperationCompleted)
				if !ok || result.Status != 7 || len(result.InspectionResults) != 1 || result.InspectionResults[0].ResolvedPath != path {
					t.Fatal("wrong actual worker result", m)
				}
			}
			if f.c.active != nil || f.c.w.CWD != "/tmp" {
				t.Fatal("worker result left active state")
			}
		})
	}
}

func TestInspectionCancelAcceptedFirstDiscardsResultAndJoinsReap(t *testing.T) {
	f, plans := inspectionFixture(t, "/tmp/artifact")
	w := newInspectionTestWorker()
	started := make(chan struct{})
	w.results = []protocol.InspectionResult{{InspectionID: "file", Kind: "file_exists", ResolvedPath: "/tmp/artifact", PathKind: "file"}}
	f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) { close(started); return w, nil }
	done := completeFixture(t, f, inspectionContext(t), func(context.Context, string, int64) error { return nil })
	inspectionReturn(t, f, plans)
	awaitInspection(t, started)
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "deadline"})
	awaitInspection(t, w.killed)
	select {
	case err := <-done:
		t.Fatal("published before actual reap", err)
	default:
	}
	close(w.done)
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	result, ok := nextDispatch(t, f).(*protocol.OperationCancelled)
	if !ok || result.Status == nil || *result.Status != 7 || result.Reason != "deadline" {
		t.Fatal("lost cancellation/status", result)
	}
	if w.kills.Load() != 1 || w.reads.Load() != 0 || f.c.active != nil {
		t.Fatal("unaccepted result or duplicate termination", w.kills.Load(), w.reads.Load())
	}
}

func TestInspectionBlockedReapIsFatalWithinOriginalFiveSeconds(t *testing.T) {
	f, plans := inspectionFixture(t, "/tmp/artifact")
	w := newInspectionTestWorker()
	started := make(chan struct{})
	f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) { close(started); return w, nil }
	done := completeFixture(t, f, inspectionContext(t), func(context.Context, string, int64) error { return nil })
	inspectionReturn(t, f, plans)
	awaitInspection(t, started)
	at := time.Now()
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "deadline"})
	awaitInspection(t, w.killed)
	select {
	case err := <-done:
		var fatal *StartError
		if !errors.As(err, &fatal) || fatal.Code != "inspection-cancel-timeout" || time.Since(at) < 4500*time.Millisecond || time.Since(at) > 6500*time.Millisecond {
			t.Fatal("wrong worker cancellation bound", err, time.Since(at))
		}
	case <-time.After(7 * time.Second):
		t.Fatal("unbounded worker disposal")
	}
	if f.c.active == nil || f.c.w.channels.Err() == nil || w.reads.Load() != 0 || w.kills.Load() != 1 {
		t.Fatal("fatal worker admitted a result or next operation")
	}
}

func TestInspectionCleanupCancelSkipsWorkerAndLateFinalizeLoses(t *testing.T) {
	for _, kind := range []string{"cancel", "finalize"} {
		t.Run(kind, func(t *testing.T) {
			f, plans := inspectionFixture(t, "/tmp/artifact")
			entered, release := make(chan struct{}), make(chan struct{})
			var launches atomic.Int32
			f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) {
				launches.Add(1)
				w := newInspectionTestWorker()
				w.results = []protocol.InspectionResult{{InspectionID: "file", Kind: "file_exists", ResolvedPath: "/tmp/artifact", PathKind: "file"}}
				close(w.done)
				return w, nil
			}
			done := completeFixture(t, f, inspectionContext(t), func(ctx context.Context, _ string, _ int64) error {
				close(entered)
				select {
				case <-release:
					return nil
				case <-ctx.Done():
					return ctx.Err()
				}
			})
			f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
			awaitInspection(t, entered)
			if kind == "cancel" {
				dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "deadline"})
			} else {
				dispatchRequest(t, f, &protocol.Finalize{OperationID: "operation-1", Reason: "recording-end"})
			}
			// FIFO control order plus a real acknowledgement proves the lifecycle was
			// accepted while the original cleanup job was still blocked.
			dispatchRequest(t, f, &protocol.Resize{Columns: 91, Rows: 33})
			if _, ok := nextDispatch(t, f).(*protocol.ResizeApplied); !ok {
				t.Fatal("missing ordered resize acknowledgement")
			}
			close(release)
			if _, ok := f.readPrivate(t).(*protocol.PrivateInputClosed); !ok {
				t.Fatal("missing cleanup release")
			}
			f.private(t, protocol.PrivateCompleted{OperationID: "operation-1", Status: 7, PhysicalCWD: "/tmp", Inspections: plans})
			if err := finishCompletion(t, done); err != nil {
				t.Fatal(err)
			}
			m := nextDispatch(t, f)
			if kind == "cancel" {
				v, ok := m.(*protocol.OperationCancelled)
				if !ok || v.Status == nil || *v.Status != 7 || launches.Load() != 0 {
					t.Fatal("cleanup cancellation launched inspection or lost status", m, launches.Load())
				}
			} else {
				v, ok := m.(*protocol.OperationCompleted)
				if !ok || v.Status != 7 || len(v.InspectionResults) != 1 || launches.Load() != 1 {
					t.Fatal("late finalize replaced ordinary return", m)
				}
			}
		})
	}
}

func TestInspectionRunsBeyondRetiredCleanupEpoch(t *testing.T) {
	f, plans := inspectionFixture(t, "/tmp/artifact")
	w := newInspectionTestWorker()
	started := make(chan struct{})
	w.results = []protocol.InspectionResult{{InspectionID: "file", Kind: "file_exists", ResolvedPath: "/tmp/artifact", PathKind: "file"}}
	f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) { close(started); return w, nil }
	done := completeFixture(t, f, inspectionContext(t), func(context.Context, string, int64) error { return nil })
	inspectionReturn(t, f, plans)
	awaitInspection(t, started)
	timer := time.NewTimer(5200 * time.Millisecond)
	defer timer.Stop()
	select {
	case err := <-done:
		t.Fatal("inspection inherited retired cleanup deadline", err)
	case <-timer.C:
	}
	close(w.done)
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if _, ok := nextDispatch(t, f).(*protocol.OperationCompleted); !ok {
		t.Fatal("inspection did not complete after retired cleanup deadline")
	}
}

func TestInspectionWorkerErrorsAndExceptionalDisposal(t *testing.T) {
	for _, scenario := range []string{"typed", "malformed", "launch", "session-cancel"} {
		t.Run(scenario, func(t *testing.T) {
			f, plans := inspectionFixture(t, "/tmp/artifact")
			w := newInspectionTestWorker()
			started := make(chan struct{})
			w.results = []protocol.InspectionResult{{InspectionID: "partial", Kind: "file_exists", ResolvedPath: "/partial", PathKind: "file"}}
			if scenario == "typed" {
				w.err = &inspection.Failure{Code: "inspection-read", Message: "injected ordinary read failure"}
			} else {
				w.err = errors.New("malformed child reply")
			}
			f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) {
				close(started)
				if scenario == "launch" {
					return nil, errors.New("failed launch")
				}
				return w, nil
			}
			ctx, cancel := context.WithCancel(inspectionContext(t))
			defer cancel()
			done := completeFixture(t, f, ctx, func(context.Context, string, int64) error { return nil })
			inspectionReturn(t, f, plans)
			awaitInspection(t, started)
			if scenario == "session-cancel" {
				cancel()
				awaitInspection(t, w.killed)
				close(w.done)
			} else if scenario != "launch" {
				close(w.done)
			}
			err := finishCompletion(t, done)
			if scenario == "typed" {
				if err != nil {
					t.Fatal(err)
				}
				v, ok := nextDispatch(t, f).(*protocol.OperationFailed)
				if !ok || v.Code != "inspection-read" || f.c.active != nil {
					t.Fatal("typed failure exposed partial or killed session", v)
				}
			} else if err == nil || f.c.active == nil || f.c.w.channels.Err() == nil {
				t.Fatal("worker/session failure allowed success", err)
			}
		})
	}
}

func TestInspectionJoinedResultCommitsWhileTelemetryBlocked(t *testing.T) {
	for _, kind := range []string{"completed", "finalized"} {
		t.Run(kind, func(t *testing.T) {
			f, plans := inspectionFixture(t, "/tmp/artifact")
			w := newInspectionTestWorker()
			w.results = []protocol.InspectionResult{{InspectionID: "file", Kind: "file_exists", ResolvedPath: "/tmp/artifact", PathKind: "file"}}
			started, proof := make(chan struct{}), make(chan struct{})
			f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) { close(started); return w, nil }
			ctx := inspectionContext(t)
			done := controlledFixture(t, f, ctx, cleanReturn, &controlPeers{sessionID: 123, census: heldCensus(proof)})
			if kind == "finalized" {
				dispatchRequest(t, f, &protocol.Finalize{OperationID: "operation-1", Reason: "recording-end"})
				awaitProof(t, proof)
			}
			inspectionReturn(t, f, plans)
			awaitInspection(t, started)
			f.c.w.channels.writeMu.Lock()
			locked := true
			defer func() {
				if locked {
					f.c.w.channels.writeMu.Unlock()
				}
			}()
			close(w.done)
			awaitInspection(t, w.resultRead)
			dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "crossed"})
			dispatchRequest(t, f, &protocol.Resize{Columns: 94, Rows: 34})
			// Entering resize's guarded output boundary proves the preceding FIFO
			// cancel was consumed while result delivery still cannot acquire telemetry.
			for {
				f.r.phaseMu.Lock()
				entered := f.r.phase != nil
				f.r.phaseMu.Unlock()
				if entered {
					break
				}
				select {
				case <-ctx.Done():
					t.Fatal("crossed control was not processed before publication")
				default:
					runtime.Gosched()
				}
			}
			f.c.w.channels.writeMu.Unlock()
			locked = false
			found := false
			for j := 0; j < 2; j++ {
				switch v := nextDispatch(t, f).(type) {
				case *protocol.ResizeApplied:
				case *protocol.OperationCompleted:
					if kind != "completed" || v.Status != 7 || len(v.InspectionResults) != 1 {
						t.Fatal("crossed cancel replaced accepted completion", v)
					}
					found = true
				case *protocol.OperationFinalized:
					if kind != "finalized" || v.Reason != "recording-end" || len(v.InspectionResults) != 1 {
						t.Fatal("crossed cancel replaced accepted finalization", v)
					}
					found = true
				default:
					t.Fatal("wrong blocked-publication outcome", v)
				}
			}
			if !found || w.reads.Load() != 1 || w.kills.Load() != 0 {
				t.Fatal("accepted result not preserved")
			}
			if err := finishCompletion(t, done); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func (w *inspectionTestWorker) Done() <-chan struct{} { return w.done }

func TestInspectionReapRetiresCancelDeadlineDuringBlockedResize(t *testing.T) {
	f, plans := inspectionFixture(t, "/tmp/artifact")
	w := newInspectionTestWorker()
	started := make(chan struct{})
	f.c.startInspection = func([]protocol.ResolvedInspection) (inspectionWorker, error) { close(started); return w, nil }
	ctx := inspectionContext(t)
	done := completeFixture(t, f, ctx, cleanReturn)
	inspectionReturn(t, f, plans)
	awaitInspection(t, started)
	f.c.w.channels.writeMu.Lock()
	locked := true
	defer func() {
		if locked {
			f.c.w.channels.writeMu.Unlock()
		}
	}()
	dispatchRequest(t, f, &protocol.Cancel{OperationID: "operation-1", Reason: "deadline"})
	awaitInspection(t, w.killed)
	dispatchRequest(t, f, &protocol.Resize{Columns: 95, Rows: 35})
	// Hold a nested resize after the accepted cancel, before actual reap.
	for {
		f.r.phaseMu.Lock()
		entered := f.r.phase != nil
		f.r.phaseMu.Unlock()
		if entered {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("resize did not enter while worker was live")
		default:
			runtime.Gosched()
		}
	}
	close(w.done)
	// The joined fact must retire its bound even while the nested I/O cannot
	// finish. This is not a longer worker-cancellation allowance.
	timer := time.NewTimer(5200 * time.Millisecond)
	defer timer.Stop()
	select {
	case err := <-done:
		t.Fatal("inspection cancellation deadline survived successful worker reap", err)
	case <-timer.C:
	}
	f.c.w.channels.writeMu.Unlock()
	locked = false
	found := false
	for j := 0; j < 2; j++ {
		switch v := nextDispatch(t, f).(type) {
		case *protocol.ResizeApplied:
		case *protocol.OperationCancelled:
			if v.Status == nil || *v.Status != 7 || v.Reason != "deadline" {
				t.Fatal("lost returned cancellation status", v)
			}
			found = true
		default:
			t.Fatal("unexpected post-reap result", v)
		}
	}
	if !found || w.reads.Load() != 0 || w.kills.Load() != 1 {
		t.Fatal("successful disposal was not retained")
	}
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
}
