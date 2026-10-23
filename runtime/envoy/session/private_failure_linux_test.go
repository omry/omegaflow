//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestFatalPrivateCancellationDoesNotHideFramingFailure(t *testing.T) {
	ctx, end := context.WithCancel(context.Background())
	end()
	for _, err := range []error{context.Canceled, os.ErrClosed, os.ErrDeadlineExceeded, errors.Join(context.Canceled, os.ErrClosed)} {
		if !privateIOCancelled(ctx, err) || privateIOCancelled(context.Background(), err) {
			t.Fatal("cooperative interruption lost its context boundary", err)
		}
	}
	for _, err := range []error{io.EOF, errors.New("invalid private prefix"), errors.Join(errors.New("invalid private prefix"), os.ErrClosed), fmt.Errorf("wrapped: %w", errors.Join(errors.New("invalid private prefix"), context.Canceled))} {
		if privateIOCancelled(ctx, err) {
			t.Fatal("substantive private framing failure became cancellation", err)
		}
	}
}

// These focused tests use the existing pipe fixture. Native qualification
// separately proves the same boundary with the original qualified Awsh Wait.
func TestFatalPrivateRetainsBarrierUntilMissingEOF(t *testing.T) {
	for _, malformed := range []bool{false, true} {
		t.Run(map[bool]string{false: "report", true: "malformed"}[malformed], func(t *testing.T) {
			f := startFixtureFor(t, nil)
			ctx, end := context.WithTimeout(context.Background(), 150*time.Millisecond)
			defer end()
			deadline, _ := ctx.Deadline()
			request := startRequest()
			request.InputThrough = 1
			done := f.start(t, ctx, request)
			cause := "future-code"
			if malformed {
				cause = "private"
				if _, err := f.result.Write([]byte("wrong-v1\x00bogus\x00")); err != nil {
					t.Fatal(err)
				}
			} else {
				f.private(t, protocol.PrivateProtocolError{Code: cause, Message: "retained cause"})
			}
			result := finishStart(t, done)
			var failure *StartError
			if result.out.Started || !errors.As(result.err, &failure) || !failure.Deadline.Equal(deadline) {
				t.Fatal("private failure replaced the original barrier", result, failure)
			}
			if time.Now().Before(deadline) || !strings.Contains(result.err.Error(), cause) || !strings.Contains(result.err.Error(), "private EOF") {
				t.Fatal("missing EOF or original cause was not retained to expiry", result.err)
			}
			for len(f.frames) != 0 {
				switch (<-f.frames).(type) {
				case *protocol.OperationFailed, *protocol.OperationCancelled, *protocol.OperationCompleted:
					t.Fatal("fatal private traffic published an operation result")
				}
			}
		})
	}
}

func TestFatalPrivateFallbackIgnoresSenderDeadline(t *testing.T) {
	f := startFixtureFor(t, nil)
	// Preserve an actual direct-child Wait receipt in this focused pipe test.
	w := f.c.w
	w.command = exec.Command("/bin/sh", "-c", "exit 0")
	w.reaped = make(chan struct{})
	if err := w.command.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { w.waitErr = w.command.Wait(); close(w.reaped) }()
	if err := f.result.Close(); err != nil {
		t.Fatal(err)
	}
	f.c.running = &operationDispatch{life: newOperationLifecycle("operation-1")}
	defer func() { f.c.running = nil }()
	ctx, end := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer end()
	accepted := time.Now()
	_, _, err := f.c.privateFact(ctx, nil, privateResult{message: &protocol.PrivateProtocolError{Code: "future-code", Message: "bounded cause"}})
	var failure *StartError
	if !errors.As(err, &failure) || failure.Deadline.Before(accepted.Add(finalDrainBudget)) || failure.Deadline.After(time.Now().Add(finalDrainBudget)) {
		t.Fatal("sender-local bound replaced fatal final drain", err, failure)
	}
	if !strings.Contains(err.Error(), "future-code") || strings.Contains(err.Error(), "missing private EOF") || strings.Contains(err.Error(), "unreaped Awsh") {
		t.Fatal("actual closure facts or fatal cause lost", err)
	}
}

func TestFatalPrivateQueuedReportSurvivesLaterReaderFailure(t *testing.T) {
	for _, malformed := range []bool{false, true} {
		t.Run(map[bool]string{false: "report", true: "malformed"}[malformed], func(t *testing.T) {
			f := startFixtureFor(t, nil)
			f.c.active = &startingOperation{request: startRequest(), shellExitPhase: StartShellExitInputBarrier}
			ctx, end := context.WithTimeout(context.Background(), time.Second)
			defer end()
			want := "queued-cause: before reader failure"
			if malformed {
				want = "invalid private prefix"
				if _, err := f.result.Write([]byte("wrong-v1\x00bogus\x00")); err != nil {
					t.Fatal(err)
				}
			} else {
				f.private(t, protocol.PrivateProtocolError{Code: "queued-cause", Message: "before reader failure"})
			}
			for len(f.c.private.fatal) == 0 {
				select {
				case <-f.c.private.changed:
				case <-ctx.Done():
					t.Fatal("actual decoder did not queue fatal traffic")
				}
			}
			if err := f.c.w.Result.Close(); err != nil {
				t.Fatal(err)
			}
			_, _, err := f.c.privateFact(ctx, nil, privateResult{err: os.ErrClosed})
			if err == nil || !strings.Contains(err.Error(), want) || !strings.Contains(err.Error(), "missing private EOF") {
				t.Fatal("reader failure displaced the already decoded fatal cause", err)
			}
			if ctx.Err() != nil || !errors.Is(f.c.privateFailure.readErr, os.ErrClosed) {
				t.Fatal("observed raw failure was lost and waited for another reader outcome", err)
			}
		})
	}
}
