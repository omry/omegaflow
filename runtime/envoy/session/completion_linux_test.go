//go:build linux

package session

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func completionFixture(t *testing.T) *startFixture {
	t.Helper()
	f := startFixtureFor(t, nil)
	done := f.start(t, context.Background(), startRequest())
	f.throughStarted(t)
	f.private(t, protocol.PrivateStartReleased{OperationID: "operation-1"})
	if r := finishStart(t, done); r.err != nil || !r.out.Started {
		t.Fatal(r)
	}
	return f
}

func completeFixture(t *testing.T, f *startFixture, ctx context.Context, cleanup ReturnCleanup) <-chan error {
	t.Helper()
	done := make(chan error, 1)
	go func() { done <- f.c.Complete(ctx, cleanup) }()
	return done
}

func finishCompletion(t *testing.T, done <-chan error) error {
	t.Helper()
	select {
	case err := <-done:
		return err
	case <-time.After(3 * time.Second):
		t.Fatal("ordinary completion retained its I/O worker")
		return nil
	}
}

func finishPrivateReturn(t *testing.T, f *startFixture, status int64, cwd string) {
	t.Helper()
	m, ok := f.readPrivate(t).(*protocol.PrivateInputClosed)
	if !ok || m.OperationID != "operation-1" {
		t.Fatal("matching input_closed required", m)
	}
	f.private(t, protocol.PrivateCompleted{OperationID: m.OperationID, Status: status,
		PhysicalCWD: cwd, Inspections: []protocol.ResolvedInspection{}})
}

func completedEvent(t *testing.T, f *startFixture) *protocol.OperationCompleted {
	t.Helper()
	for {
		switch m := nextRelayFrame(t, f.relayFixture).(type) {
		case *protocol.OutputMark:
		case *protocol.OperationCompleted:
			return m
		default:
			t.Fatal("unexpected ordinary-return event", m)
		}
	}
}

func TestCompleteWaitsForCleanupThenPublishesExactRangeAndReusesShell(t *testing.T) {
	f := completionFixture(t)
	entered, release := make(chan struct{}), make(chan struct{})
	done := completeFixture(t, f, relayTestContext(t), func(ctx context.Context, id string, pid int64) error {
		if id != "operation-1" || pid != 77 {
			return errors.New("cleanup received the wrong operation/helper identity")
		}
		close(entered)
		select {
		case <-release:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	select {
	case <-entered:
	case <-relayTestContext(t).Done():
		t.Fatal("cleanup peer did not receive the proposal")
	}
	f.control.SetReadDeadline(time.Now().Add(30 * time.Millisecond))
	var b [1]byte
	if n, err := f.control.Read(b[:]); n != 0 || !os.IsTimeout(err) {
		t.Fatal("helper released before cleanup peer success", n, err)
	}
	f.control.SetReadDeadline(time.Time{})
	if _, err := f.slave.Write([]byte("tail")); err != nil {
		t.Fatal(err)
	}
	close(release)
	finishPrivateReturn(t, f, 7, "/work/new")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	m := completedEvent(t, f)
	prefix := "before-start\n"
	if m.OperationID != "operation-1" || m.Status != 7 || m.Cwd != "/work/new" ||
		m.OutputStart != int64(len(prefix)) || m.OutputThrough != int64(len(prefix)+4) || len(m.InspectionResults) != 0 {
		t.Fatal("wrong completion or output range", m)
	}
	actual := make([]byte, m.OutputThrough)
	f.terminal.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := io.ReadFull(f.terminal, actual); err != nil || string(actual) != prefix+"tail" {
		t.Fatal("output range did not retain exact PTY bytes", string(actual), err)
	}
	if f.c.active != nil || f.r.w.CWD != "/work/new" {
		t.Fatal("completed operation was not reusable")
	}
	request := startRequest()
	request.OperationID = "operation-2"
	next := f.start(t, context.Background(), request)
	f.throughStarted(t)
	f.private(t, protocol.PrivateStartReleased{OperationID: request.OperationID})
	if r := finishStart(t, next); r.err != nil || !r.out.Started || r.out.OutputStart != m.OutputThrough+int64(len(prefix)) {
		t.Fatal("next operation failed", r)
	}
}

func TestCompleteInputCloseJoinsBlockedInputBeforeCleanup(t *testing.T) {
	f := completionFixture(t)
	peer := make(chan error, 1)
	done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error {
		select {
		case <-f.r.inputWriter:
			f.r.inputWriter <- struct{}{}
			peer <- nil
			return nil
		default:
			err := errors.New("cleanup raced an unjoined input writer")
			peer <- err
			return err
		}
	})
	if _, err := f.terminal.Write(bytes.Repeat([]byte("i"), 32768)); err != nil {
		t.Fatal(err)
	}
	// Actual kernel input proves the coordinator forwarded the bytes. The
	// unread slave cannot hold the complete chunk; its writer must be joined.
	waitKernelQueue(t, f.slave, nil, syscall.TIOCINQ)
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	finishPrivateReturn(t, f, 0, "/tmp")
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if err := <-peer; err != nil {
		t.Fatal(err)
	}
	completedEvent(t, f)
}

func TestCompleteCallerCancellationJoinsBlockedInput(t *testing.T) {
	f := completionFixture(t)
	ctx, cancel := context.WithCancel(relayTestContext(t))
	defer cancel()
	done := completeFixture(t, f, ctx, func(context.Context, string, int64) error {
		t.Error("cancelled input entered cleanup")
		return nil
	})
	if _, err := f.terminal.Write(bytes.Repeat([]byte("i"), 32768)); err != nil {
		t.Fatal(err)
	}
	waitKernelQueue(t, f.slave, nil, syscall.TIOCINQ)
	cancel()
	if err := finishCompletion(t, done); !errors.Is(err, context.Canceled) || f.c.active == nil {
		t.Fatal("caller cancellation lost its cause or made the operation reusable", err)
	}
	select {
	case <-f.r.inputWriter:
		f.r.inputWriter <- struct{}{}
	default:
		t.Fatal("completion returned before joining blocked input")
	}
}

func TestCompleteRejectsInvalidFactsAndNeverReleasesFailedCleanup(t *testing.T) {
	for _, name := range []string{"wrong-id", "early-completed", "private-eof", "completed-eof", "peer-failure", "duplicate-proposal", "unexpected-inspection"} {
		t.Run(name, func(t *testing.T) {
			f := completionFixture(t)
			var called atomic.Bool
			entered := make(chan struct{})
			done := completeFixture(t, f, relayTestContext(t), func(ctx context.Context, _ string, _ int64) error {
				called.Store(true)
				close(entered)
				if name == "peer-failure" {
					return errors.New("injected bounded cleanup failure")
				}
				if name == "duplicate-proposal" {
					<-ctx.Done()
					return ctx.Err()
				}
				return nil
			})
			switch name {
			case "wrong-id":
				f.private(t, protocol.PrivateInputClose{OperationID: "other", CompletionHelperPID: 77})
			case "early-completed":
				f.private(t, protocol.PrivateCompleted{OperationID: "operation-1", PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{}})
			case "private-eof":
				f.result.Close()
			default:
				f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
				if name == "duplicate-proposal" {
					select {
					case <-entered:
					case <-relayTestContext(t).Done():
						t.Fatal("peer never entered")
					}
					f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
				}
				if name == "unexpected-inspection" || name == "completed-eof" {
					if _, ok := f.readPrivate(t).(*protocol.PrivateInputClosed); !ok {
						t.Fatal("missing helper release")
					}
					if name == "completed-eof" {
						f.result.Close()
					} else {
						f.private(t, protocol.PrivateCompleted{OperationID: "operation-1", PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{{InspectionID: "unexpected", Kind: "file_exists", ResolvedPath: "/tmp"}}})
					}
				}
			}
			err := finishCompletion(t, done)
			if err == nil || f.r.w.channels.Err() == nil {
				t.Fatal("invalid return did not fail closed", err)
			}
			var failure *StartError
			if !errors.As(err, &failure) || failure.Code != "operation-cleanup" {
				t.Fatal("ordinary return did not retain the protocol cleanup code", err)
			}
			if name == "peer-failure" {
				for {
					m := nextRelayFrame(t, f.relayFixture)
					if _, ok := m.(*protocol.OutputMark); ok {
						continue
					}
					if d, ok := m.(*protocol.Diagnostic); !ok || d.Code != "operation-cleanup" || d.Severity != "fatal" {
						t.Fatal("cleanup failure did not publish the protocol fatal diagnostic", m)
					}
					break
				}
			}
			if (name == "wrong-id" || name == "early-completed" || name == "private-eof") && called.Load() {
				t.Fatal("invalid fact entered cleanup")
			}
			if f.c.active == nil {
				t.Fatal("failed operation became reusable")
			}
		})
	}
}

func TestCompleteRetainsCallerBoundAndJoinsPeer(t *testing.T) {
	f := completionFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Millisecond)
	defer cancel()
	deadline, _ := ctx.Deadline()
	joined := make(chan struct{})
	done := completeFixture(t, f, ctx, func(job context.Context, _ string, _ int64) error {
		defer close(joined)
		if got, _ := job.Deadline(); got != deadline {
			return errors.New("cleanup replaced the existing caller bound")
		}
		<-job.Done()
		return job.Err()
	})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	if err := finishCompletion(t, done); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("original bound was lost", err)
	}
	select {
	case <-joined:
	default:
		t.Fatal("completion returned before joining the cleanup peer")
	}
}

func TestCompleteCancellationDuringCleanupJoinsWithoutPublishingReturn(t *testing.T) {
	f := completionFixture(t)
	entered, joined := make(chan struct{}), make(chan struct{})
	done := completeFixture(t, f, relayTestContext(t), func(ctx context.Context, _ string, _ int64) error {
		close(entered)
		defer close(joined)
		<-ctx.Done()
		return ctx.Err()
	})
	f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
	select {
	case <-entered:
	case <-relayTestContext(t).Done():
		t.Fatal("cleanup peer did not start")
	}
	f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "user"})
	if err := finishCompletion(t, done); err == nil || !strings.Contains(err.Error(), "requires the lifecycle owner") || f.c.active == nil || f.r.w.channels.Err() == nil {
		t.Fatal("foundation consumed lifecycle cancellation as ordinary success", err)
	}
	select {
	case <-joined:
	default:
		t.Fatal("failed completion retained the cleanup peer")
	}
}

func TestCompleteRequiresItsBoundedOwner(t *testing.T) {
	for _, name := range []string{"unbounded", "expired", "no-cleanup", "concurrent", "split", "inspection", "queued-cancel"} {
		t.Run(name, func(t *testing.T) {
			f := completionFixture(t)
			ctx := relayTestContext(t)
			cleanup := ReturnCleanup(func(context.Context, string, int64) error {
				t.Error("unowned completion entered cleanup")
				return nil
			})
			switch name {
			case "unbounded":
				ctx = context.Background()
			case "expired":
				expired, cancel := context.WithCancel(ctx)
				cancel()
				ctx = expired
			case "no-cleanup":
				cleanup = nil
			case "concurrent":
				f.c.busy.Store(true)
				defer f.c.busy.Store(false)
			case "split":
				f.c.active.request.ExecutionShape = "split"
			case "inspection":
				f.c.active.request.Inspections = []protocol.Inspection{{InspectionID: "later", Kind: "file_exists", Path: "/tmp"}}
			case "queued-cancel":
				f.c.active.cancel = &protocol.Cancel{OperationID: "operation-1", Reason: "user"}
			}
			if err := f.c.Complete(ctx, cleanup); err == nil || f.c.active == nil {
				t.Fatal("unowned completion advanced the operation", err)
			}
			if err := f.c.ShutdownIdle("requested-shutdown"); err == nil {
				t.Fatal("idle shutdown accepted an active operation")
			}
		})
	}
}

func TestCoordinatorIdleShutdownRejectsPrivateFailureAndJoinsReader(t *testing.T) {
	f := startFixtureFor(t, nil)
	f.result.Close()
	if err := f.c.ShutdownIdle("requested-shutdown"); err == nil {
		t.Fatal("private EOF substituted for closed and reap")
	}
	if f.r.w.channels.Err() == nil {
		t.Fatal("failed shutdown left public channels live")
	}
	select {
	case <-f.c.private.done:
	default:
		t.Fatal("failed idle shutdown retained its private reader")
	}
}

func TestCoordinatorIdleShutdownAfterChannelLossDisposesOwners(t *testing.T) {
	f := startFixtureFor(t, nil)
	f.r.w.channels.Close()
	if err := f.c.ShutdownIdle("requested-shutdown"); err == nil {
		t.Fatal("selected-channel loss became successful idle shutdown")
	}
	select {
	case <-f.c.private.done:
	default:
		t.Fatal("shutdown error retained its private reader")
	}
	if _, err := f.r.w.Result.Stat(); !errors.Is(err, os.ErrClosed) {
		t.Fatal("failed relay join left workload descriptors open", err)
	}
}

func TestCompleteFailedReleaseOrOutputBoundaryCannotPublishSuccess(t *testing.T) {
	for _, where := range []string{"input-closed", "output-boundary"} {
		t.Run(where, func(t *testing.T) {
			f := completionFixture(t)
			done := completeFixture(t, f, relayTestContext(t), func(context.Context, string, int64) error {
				if where == "input-closed" {
					return f.r.w.Control.Close()
				}
				return nil
			})
			f.private(t, protocol.PrivateInputClose{OperationID: "operation-1", CompletionHelperPID: 77})
			if where == "output-boundary" {
				if _, ok := f.readPrivate(t).(*protocol.PrivateInputClosed); !ok {
					t.Fatal("missing matching helper release")
				}
				if err := f.r.Close(relayTestContext(t)); err != nil {
					t.Fatal(err)
				}
				f.private(t, protocol.PrivateCompleted{OperationID: "operation-1", PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{}})
			}
			if err := finishCompletion(t, done); err == nil || f.r.w.channels.Err() == nil || f.c.active == nil {
				t.Fatal("failed completion became reusable", err)
			}
		})
	}
}

func TestCoordinatorIdleShutdownKeepsPrivatePump(t *testing.T) {
	f := startFixtureFor(t, nil)
	f.r.w.reaped = make(chan struct{})
	reader, pump := f.r.w.Reader, f.c.private
	if err := f.r.w.ShutdownIdle("requested-shutdown"); err == nil {
		t.Fatal("standalone decoder bypassed the existing private owner")
	}
	done := make(chan error, 1)
	go func() { done <- f.c.ShutdownIdle("requested-shutdown") }()
	if _, ok := f.readPrivate(t).(*protocol.PrivateShutdown); !ok {
		t.Fatal("missing private shutdown")
	}
	f.private(t, protocol.PrivateClosed{Reason: "shutdown", CWD: "/tmp"})
	f.result.Close()
	f.slave.Close()
	close(f.r.w.reaped)
	if err := finishCompletion(t, done); err != nil {
		t.Fatal(err)
	}
	if f.r.w.Reader != reader || f.c.private != pump {
		t.Fatal("shutdown replaced the private result owner")
	}
	select {
	case <-pump.done:
	default:
		t.Fatal("private reader survived shutdown")
	}
}
