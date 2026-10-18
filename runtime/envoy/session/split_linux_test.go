//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

var splitBoundaries = []string{"ancestors", "directory", "stdout-FIFO", "stdout-reader", "stdout-keepalive", "stderr-FIFO", "stderr-reader", "stderr-keepalive", "validation"}

type splitTestOwner struct {
	*SplitResources
	root string
	end  time.Time
}

func splitOwnerFor(t *testing.T) *splitTestOwner {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	return &splitTestOwner{SplitResources: NewSplitSetup(), root: root}
}
func (s *splitTestOwner) open() ([]splitEntry, error) {
	e, err := splitOpen(-100, s.root, splitPathDescriptor|syscall.O_DIRECTORY)
	if err != nil {
		return nil, err
	}
	return []splitEntry{e}, nil
}
func (s *splitTestOwner) Prepare(ctx context.Context, r protocol.Execute) (string, string, error) {
	return s.prepare(ctx, r, s.open)
}
func (s *splitTestOwner) Rollback(ctx context.Context, id string) error {
	s.end, _ = ctx.Deadline()
	return s.SplitResources.Rollback(ctx, id)
}
func splitContext(t *testing.T) context.Context {
	t.Helper()
	ctx, end := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(end)
	return ctx
}
func splitFDs(s *SplitResources) []int {
	op := s.operation
	fds := append([]int{}, op.readers[:]...)
	fds = append(fds, op.keepalives[:]...)
	for _, e := range op.chain {
		fds = append(fds, e.fd)
	}
	if op.directory != nil {
		fds = append(fds, op.directory.fd)
	}
	for _, e := range op.fifos {
		fds = append(fds, e.fd)
	}
	return fds
}
func splitAssertClosed(t *testing.T, fds []int) {
	t.Helper()
	for _, fd := range fds {
		if fd < 0 {
			continue
		}
		if _, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0); e != syscall.EBADF {
			t.Fatalf("descriptor %d survives: %v", fd, e)
		}
	}
}
func splitAssertGone(t *testing.T, root string) {
	t.Helper()
	if _, err := os.Lstat(filepath.Join(root, "split")); !os.IsNotExist(err) {
		t.Fatal("split resources survive", err)
	}
}

func TestSplitResourcesRealFIFOAndOwnership(t *testing.T) {
	s := splitOwnerFor(t)
	ctx := splitContext(t)
	out, errout, err := s.Prepare(ctx, splitRequest())
	if err != nil {
		t.Fatal(err)
	}
	if out != sessionRuntimeDir+"/split/operation-1.stdout" || errout != sessionRuntimeDir+"/split/operation-1.stderr" {
		t.Fatal(out, errout)
	}
	op := s.operation
	if len(op.fifos) != 2 || splitSame(op.fifos[0].stat, op.fifos[1].stat) {
		t.Fatal("streams share identity")
	}
	fds := splitFDs(s.SplitResources)
	seen := map[int]bool{}
	for _, fd := range fds {
		flags, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if seen[fd] || e != 0 || flags&syscall.FD_CLOEXEC == 0 {
			t.Fatal("descriptor isolation", fd, e)
		}
		seen[fd] = true
	}
	for i, stream := range []string{"stdout", "stderr"} {
		if !splitPrivate(op.fifos[i].stat, syscall.S_IFIFO, 0600) {
			t.Fatal("wrong FIFO mode")
		}
		if _, e := syscall.Write(op.keepalives[i], []byte(stream)); e != nil {
			t.Fatal(e)
		}
		b := make([]byte, 16)
		n, e := syscall.Read(op.readers[i], b)
		if e != nil || string(b[:n]) != stream {
			t.Fatal("crossed streams", n, e, string(b))
		}
		if _, e := syscall.Read(op.readers[i], b); e != syscall.EAGAIN {
			t.Fatal("keepalive did not retain writer", e)
		}
	}
	if _, _, e := s.Prepare(ctx, splitRequest()); e == nil {
		t.Fatal("adopted active resources")
	}
	wrong := context.Background()
	if e := s.Rollback(wrong, "operation-1"); e == nil || s.operation == nil {
		t.Fatal("accepted deadline-free rollback", e)
	}
	if e := s.Rollback(ctx, "another"); e == nil || s.operation == nil {
		t.Fatal("accepted wrong operation", e)
	}
	newer, end := context.WithTimeout(context.Background(), 6*time.Second)
	defer end()
	if e := s.Rollback(newer, "operation-1"); e == nil || s.operation == nil {
		t.Fatal("reset start epoch", e)
	}
	if e := s.Rollback(ctx, "operation-1"); e != nil {
		t.Fatal(e)
	}
	splitAssertClosed(t, fds)
	splitAssertGone(t, s.root)
	// A later operation acquires new resources instead of adopting old paths.
	if _, _, e := s.Prepare(ctx, splitRequest()); e != nil {
		t.Fatal(e)
	}
	if e := s.Rollback(ctx, "operation-1"); e != nil {
		t.Fatal(e)
	}
}

func TestSplitResourcesEveryPartialBoundary(t *testing.T) {
	for _, at := range splitBoundaries {
		for _, kind := range []string{"failure", "cancel", "expiry"} {
			t.Run(at+"/"+kind, func(t *testing.T) {
				s := splitOwnerFor(t)
				ctx := splitContext(t)
				job, cancel := context.WithCancel(ctx)
				defer cancel()
				var expire context.CancelFunc
				if kind == "expiry" {
					ctx, expire = context.WithTimeout(context.Background(), 20*time.Millisecond)
					defer expire()
					job = ctx
				}
				sentinel := fmt.Errorf("fault at %s", at)
				s.step = func(c context.Context, name string) error {
					if name != at {
						return nil
					}
					switch kind {
					case "failure":
						return sentinel
					case "cancel":
						cancel()
					case "expiry":
						<-c.Done()
					}
					return nil
				}
				if _, _, err := s.Prepare(job, splitRequest()); err == nil {
					t.Fatal("fault accepted")
				}
				fds := splitFDs(s.SplitResources)
				err := s.Rollback(ctx, "operation-1")
				if kind == "expiry" {
					if !errors.Is(err, context.DeadlineExceeded) {
						t.Fatal("lost expiry", err)
					}
				} else if err != nil {
					t.Fatal(err)
				}
				splitAssertClosed(t, fds)
				splitAssertGone(t, s.root)
			})
		}
	}
}

func TestSplitResourcesRejectsForeignAndInvalidInputs(t *testing.T) {
	for _, kind := range []string{"directory", "symlink", "file"} {
		t.Run(kind, func(t *testing.T) {
			s := splitOwnerFor(t)
			p := filepath.Join(s.root, "split")
			var err error
			switch kind {
			case "directory":
				err = os.Mkdir(p, 0700)
			case "file":
				err = os.WriteFile(p, []byte("foreign"), 0600)
			case "symlink":
				err = os.Symlink(s.root, p)
			}
			if err != nil {
				t.Fatal(err)
			}
			info, _ := os.Lstat(p)
			ctx := splitContext(t)
			if _, _, err = s.Prepare(ctx, splitRequest()); err == nil {
				t.Fatal("adopted preexisting entry")
			}
			if err = s.Rollback(ctx, "operation-1"); err != nil {
				t.Fatal(err)
			}
			after, e := os.Lstat(p)
			if e != nil || !os.SameFile(info, after) {
				t.Fatal("changed foreign entry", e)
			}
		})
	}
	for _, id := range []string{"", "..", "../outside", "a/b", strings.Repeat("a", 65), "a\x00b"} {
		t.Run(fmt.Sprintf("invalid-%q", id), func(t *testing.T) {
			s := splitOwnerFor(t)
			r := splitRequest()
			r.OperationID = id
			if _, _, err := s.Prepare(splitContext(t), r); err == nil || s.operation != nil {
				t.Fatal("invalid id acquired resources", err)
			}
			splitAssertGone(t, s.root)
		})
	}
	s := splitOwnerFor(t)
	if _, _, err := s.Prepare(context.Background(), splitRequest()); err == nil || s.operation != nil {
		t.Fatal("missing epoch accepted")
	}
	if _, _, err := s.Prepare(splitContext(t), startRequest()); err == nil {
		t.Fatal("PTY acquired FIFO resources")
	}
}

func TestSplitResourcesRetainsIdentityAfterReplacement(t *testing.T) {
	for _, replace := range []string{"stdout", "stderr", "directory", "ancestor", "mode", "extra-entry"} {
		t.Run(replace, func(t *testing.T) {
			s := splitOwnerFor(t)
			ctx := splitContext(t)
			if _, _, e := s.Prepare(ctx, splitRequest()); e != nil {
				t.Fatal(e)
			}
			fds := splitFDs(s.SplitResources)
			dir := filepath.Join(s.root, "split")
			var preserved string
			switch replace {
			case "stdout", "stderr":
				preserved = filepath.Join(dir, "operation-1."+replace)
				if e := os.Rename(preserved, preserved+".original"); e != nil {
					t.Fatal(e)
				}
				if e := os.WriteFile(preserved, []byte("foreign"), 0600); e != nil {
					t.Fatal(e)
				}
			case "directory":
				if e := os.Rename(dir, dir+".original"); e != nil {
					t.Fatal(e)
				}
				if e := os.Mkdir(dir, 0700); e != nil {
					t.Fatal(e)
				}
				preserved = dir
			case "ancestor":
				// The retained chain must detect replacement of a private ancestor.
				e, err := splitOpen(-100, filepath.Dir(s.root), splitPathDescriptor|syscall.O_DIRECTORY)
				if err != nil {
					t.Fatal(err)
				}
				s.operation.chain = append([]splitEntry{e}, s.operation.chain...)
				s.operation.chain[1].name = filepath.Base(s.root)
				fds = append(fds, e.fd)
				if e := os.Rename(s.root, s.root+".original"); e != nil {
					t.Fatal(e)
				}
				t.Cleanup(func() { os.RemoveAll(s.root + ".original") })
				if e := os.Mkdir(s.root, 0700); e != nil {
					t.Fatal(e)
				}
				preserved = s.root
			case "mode":
				preserved = filepath.Join(dir, "operation-1.stdout")
				if e := os.Chmod(preserved, 0666); e != nil {
					t.Fatal(e)
				}
			case "extra-entry":
				preserved = filepath.Join(dir, "foreign")
				if e := os.WriteFile(preserved, []byte("x"), 0600); e != nil {
					t.Fatal(e)
				}
			}
			if err := s.Rollback(ctx, "operation-1"); err == nil {
				t.Fatal("replacement permitted ordinary result")
			}
			splitAssertClosed(t, fds)
			if _, err := os.Lstat(preserved); err != nil {
				t.Fatal("foreign entry removed", err)
			}
		})
	}
}

func TestSplitResourcesValidationAndDisposalFailures(t *testing.T) {
	for _, fault := range []string{"closed-reader", "missing-FIFO", "late-replacement", "unproven-create", "wrong-owner", "wrong-mode", "ancestor-open", "validation-descriptor"} {
		t.Run(fault, func(t *testing.T) {
			s := splitOwnerFor(t)
			ctx := splitContext(t)
			switch fault {
			case "ancestor-open":
				_, _, err := s.prepare(ctx, splitRequest(), func() ([]splitEntry, error) { return nil, syscall.EMFILE })
				if err == nil {
					t.Fatal("open failure accepted")
				}
			case "wrong-mode", "wrong-owner":
				s.step = func(_ context.Context, n string) error {
					if n == "directory" {
						if fault == "wrong-mode" {
							s.operation.directory.stat.Mode = 0777
						} else {
							s.operation.directory.stat.Uid++
						}
					}
					return nil
				}
				if _, _, err := s.Prepare(ctx, splitRequest()); err == nil {
					t.Fatal("invalid identity accepted")
				}
			default:
				s.step = func(_ context.Context, n string) error {
					if n == "validation" && fault == "validation-descriptor" {
						return syscall.Close(s.operation.readers[0])
					}
					if n == "validation" && fault == "late-replacement" {
						p := filepath.Join(s.root, "split/operation-1.stdout")
						if err := os.Remove(p); err != nil {
							return err
						}
						return os.Symlink("/dev/null", p)
					}
					return nil
				}
				_, _, err := s.Prepare(ctx, splitRequest())
				if (err != nil) != (fault == "late-replacement" || fault == "validation-descriptor") {
					t.Fatal(err)
				}
			}
			fds := splitFDs(s.SplitResources)
			switch fault {
			case "closed-reader":
				if e := syscall.Close(s.operation.readers[0]); e != nil {
					t.Fatal(e)
				}
			case "missing-FIFO":
				if e := os.Remove(filepath.Join(s.root, "split/operation-1.stderr")); e != nil {
					t.Fatal(e)
				}
			case "unproven-create":
				s.operation.unproven = true
			}
			err := s.Rollback(ctx, "operation-1")
			if fault == "ancestor-open" {
				if err != nil {
					t.Fatal(err)
				}
			} else if err == nil {
				t.Fatal("disposal failure accepted")
			}
			splitAssertClosed(t, fds)
		})
	}
}

func TestSplitResourcesCoordinatorRejectionAndCancel(t *testing.T) {
	for _, cancelRequest := range []bool{false, true} {
		t.Run(fmt.Sprint(cancelRequest), func(t *testing.T) {
			s := splitOwnerFor(t)
			entered := make(chan context.Context, 1)
			s.step = func(ctx context.Context, n string) error {
				if n == "validation" {
					entered <- ctx
					if cancelRequest {
						<-ctx.Done()
						return ctx.Err()
					}
				}
				return nil
			}
			f := startFixtureFor(t, s)
			done := f.start(t, context.Background(), splitRequest())
			job := <-entered
			deadline, _ := job.Deadline()
			if cancelRequest {
				f.public(t, &protocol.Cancel{OperationID: "operation-1", Reason: "user"})
			} else {
				if _, ok := f.readPrivate(t).(*protocol.PrivateExecute); !ok {
					t.Fatal("wrong private request")
				}
				f.private(t, protocol.PrivateRejected{OperationID: "operation-1", Code: "source-syntax", Message: "source rejected"})
			}
			result := finishStart(t, done)
			if result.err != nil || result.out.Started || s.operation != nil || !s.end.Equal(deadline) {
				t.Fatal("rollback did not retain start epoch", result, s.end, deadline)
			}
			splitAssertGone(t, s.root)
			for {
				m := nextRelayFrame(t, f.relayFixture)
				if _, mark := m.(*protocol.OutputMark); mark {
					continue
				}
				switch v := m.(type) {
				case *protocol.OperationFailed:
					if cancelRequest || v.Code != "source-syntax" || v.OutputStart != v.OutputThrough {
						t.Fatal(v)
					}
				case *protocol.OperationCancelled:
					if !cancelRequest || v.Status != nil || v.OutputStart != v.OutputThrough {
						t.Fatal(v)
					}
				default:
					t.Fatal("unexpected result", m)
				}
				break
			}
			if cancelRequest {
				f.control.SetReadDeadline(time.Now().Add(10 * time.Millisecond))
				var b [1]byte
				if n, e := f.control.Read(b[:]); n != 0 || (!os.IsTimeout(e) && !errors.Is(e, io.EOF)) {
					t.Fatal("private byte escaped cancel", n, e)
				}
			}
		})
	}
}
