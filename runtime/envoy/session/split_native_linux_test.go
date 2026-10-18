//go:build linux

package session

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// This adapter observes the real owner without substituting resource I/O or
// adding a production option. The selected Bash, public sockets and private
// source checker are all the same qualified target used by ordinary proofs.
type nativeSplitOwner struct {
	*SplitResources
	prepared, rolled time.Time
	fds              []int
}

func (s *nativeSplitOwner) Prepare(ctx context.Context, r protocol.Execute) (string, string, error) {
	s.prepared, _ = ctx.Deadline()
	out, errout, err := s.SplitResources.Prepare(ctx, r)
	s.fds = splitFDs(s.SplitResources)
	return out, errout, err
}
func (s *nativeSplitOwner) Rollback(ctx context.Context, id string) error {
	s.rolled, _ = ctx.Deadline()
	return s.SplitResources.Rollback(ctx, id)
}

func TestNativeSplitSetupFixedPathsAndRejection(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_SPLIT_SETUP") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	for _, kind := range []string{"source-syntax", "pre-write-cancel", "partial-failure", "rollback-unlink"} {
		t.Run(kind, func(t *testing.T) {
			n := nativeOrdinaryFor(t)
			owner := &nativeSplitOwner{SplitResources: NewSplitSetup()}
			n.c.setup = owner
			entered := make(chan struct{}, 1)
			owner.step = func(ctx context.Context, name string) error {
				if (kind == "partial-failure" || kind == "rollback-unlink") && name == "stderr-reader" {
					if kind == "rollback-unlink" {
						if err := os.Chmod(filepath.Join(sessionRuntimeDir, "split"), 0500); err != nil {
							return err
						}
					}
					return os.ErrPermission
				}
				if name == "validation" {
					for _, stream := range []string{"stdout", "stderr"} {
						info, err := os.Lstat(filepath.Join(sessionRuntimeDir, "split", "operation-1."+stream))
						if err != nil || info.Mode()&os.ModeNamedPipe == 0 || info.Mode().Perm() != 0600 {
							return fmt.Errorf("wrong actual FIFO: %v/%v", info, err)
						}
					}
					entered <- struct{}{}
					if kind == "pre-write-cancel" {
						<-ctx.Done()
						return ctx.Err()
					}
				}
				return nil
			}
			req := splitRequest()
			req.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
			if kind == "source-syntax" {
				req.Source = "if"
			}
			n.request(t, &req)
			e := receiveInput(t, n.c.w.channels)
			actual, ok := e.Message.(*protocol.Execute)
			if !ok {
				t.Fatal(e)
			}
			done := make(chan startedResult, 1)
			go func() { out, err := n.c.Start(n.c.w.channels.ctx, *actual); done <- startedResult{out, err} }()
			if kind == "pre-write-cancel" {
				select {
				case <-entered:
				case <-time.After(5 * time.Second):
					t.Fatal("actual setup did not reach validation")
				}
				n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: req.OperationID, Reason: "native-setup"})
			}
			result := finishStart(t, done)
			if result.out.Started || owner.operation != nil || owner.prepared.IsZero() || !owner.rolled.Equal(owner.prepared) {
				t.Fatal("setup rollback/epoch", result, owner.prepared, owner.rolled)
			}
			splitAssertClosed(t, owner.fds)
			if kind == "rollback-unlink" {
				dir := filepath.Join(sessionRuntimeDir, "split")
				if result.err == nil {
					t.Fatal("unlink failure produced success")
				}
				if _, err := os.Lstat(filepath.Join(dir, "operation-1.stdout")); err != nil {
					t.Fatal("native unlink failure was not exercised", err)
				}
				// Test teardown restores only this test-owned directory so the workload
				// owner can perform its separate fatal session disposal.
				if err := os.Chmod(dir, 0700); err != nil {
					t.Fatal(err)
				}
				return
			}
			splitAssertGone(t, sessionRuntimeDir)
			if kind == "partial-failure" {
				if result.err == nil {
					t.Fatal("fatal setup failure accepted")
				}
				// No ordinary operation result is claimed on a fatal setup failure.
				return
			}
			if result.err != nil {
				t.Fatal(result.err)
			}
			event := nativeControlEvent(t, n)
			switch v := event.(type) {
			case *protocol.OperationFailed:
				if kind != "source-syntax" || v.Code != "source-syntax" || v.OutputStart != v.OutputThrough {
					t.Fatal(event)
				}
			case *protocol.OperationCancelled:
				if kind != "pre-write-cancel" || v.Status != nil || v.Reason != "native-setup" || v.OutputStart != v.OutputThrough {
					t.Fatal(event)
				}
			default:
				t.Fatal("unexpected pre-start result", event)
			}
			// Reuse proves the recoverable setup rejection did not consume the shell.
			// This ordinary PTY operation uses the existing deterministic cleanup peer.
			n.execute(t, "after-split-rejection", "builtin printf 'REUSABLE\\n'", "REUSABLE\n", "/", 0)
			if err := n.c.ShutdownIdle("recording-end"); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestNativeSplitSetupAncestorRefusal(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_SPLIT_SETUP") != "1" {
		t.Skip("genuinely qualified isolated target required")
	}
	for _, kind := range []string{"symlink", "wrong-mode"} {
		t.Run(kind, func(t *testing.T) {
			if os.Geteuid() == 0 {
				t.Fatal("non-root proof required")
			}
			var err error
			if kind == "symlink" {
				err = os.Symlink(t.TempDir(), sessionRuntimeDir)
			} else {
				err = os.Mkdir(sessionRuntimeDir, 0755)
			}
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				if err := os.Remove(sessionRuntimeDir); err != nil {
					t.Error(err)
				}
			})
			before, err := os.Lstat(sessionRuntimeDir)
			if err != nil {
				t.Fatal(err)
			}
			ctx := splitContext(t)
			s := NewSplitSetup()
			if _, _, err = s.Prepare(ctx, splitRequest()); err == nil {
				t.Fatal("invalid fixed ancestor accepted")
			}
			fds := splitFDs(s)
			if err = s.Rollback(ctx, "operation-1"); err != nil {
				t.Fatal(err)
			}
			splitAssertClosed(t, fds)
			after, err := os.Lstat(sessionRuntimeDir)
			if err != nil || !os.SameFile(before, after) {
				t.Fatal("foreign ancestor changed", err)
			}
		})
	}
}
