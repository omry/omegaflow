//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"os"
	"syscall"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const splitPathDescriptor = 0x200000 // Linux O_PATH permits identity-only FIFO opens.

type splitEntry struct {
	name string
	stat syscall.Stat_t
	fd   int
}

type splitOperation struct {
	unproven   bool
	id         string
	deadline   time.Time
	chain      []splitEntry // root, platform /run, private runtime root and session
	directory  *splitEntry
	fifos      []splitEntry
	readers    [2]int
	keepalives [2]int
}

// SplitResources owns one serialized operation's FIFO setup and rollback.
// The Coordinator supplies its existing operation-start context. Setup starts
// no actor. This cooperative same-identity transport is not containment.
type SplitResources struct {
	operation *splitOperation
	// Nil in production. Tests inject faults after retaining real resources.
	step func(context.Context, string) error
}

func NewSplitSetup() *SplitResources { return &SplitResources{} }

func splitOpen(parent int, name string, flags int) (splitEntry, error) {
	fd, err := syscall.Openat(parent, name, flags|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return splitEntry{}, err
	}
	var st syscall.Stat_t
	if err = syscall.Fstat(fd, &st); err != nil {
		return splitEntry{}, errors.Join(err, syscall.Close(fd))
	}
	return splitEntry{name: name, stat: st, fd: fd}, nil
}

func splitSame(a, b syscall.Stat_t) bool {
	return a.Dev == b.Dev && a.Ino == b.Ino && a.Mode == b.Mode && a.Uid == b.Uid && a.Gid == b.Gid
}

func splitPrivate(st syscall.Stat_t, kind, mode uint32) bool {
	return st.Mode&syscall.S_IFMT == kind && st.Mode&07777 == mode && st.Uid == uint32(os.Geteuid())
}

func splitSession() ([]splitEntry, error) {
	root, err := splitOpen(-100, "/", splitPathDescriptor|syscall.O_DIRECTORY)
	if err != nil {
		return nil, err
	}
	chain := []splitEntry{root}
	for _, name := range []string{"run", "omegaflow", "session"} {
		next, err := splitOpen(chain[len(chain)-1].fd, name, splitPathDescriptor|syscall.O_DIRECTORY)
		if err != nil {
			return chain, err
		}
		chain = append(chain, next)
		if name != "run" && !splitPrivate(next.stat, syscall.S_IFDIR, 0700) {
			return chain, fmt.Errorf("split private ancestor identity or mode: %s", name)
		}
	}
	return chain, nil
}

func (s *SplitResources) boundary(ctx context.Context, name string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if s.step != nil {
		if err := s.step(ctx, name); err != nil {
			return err
		}
	}
	return ctx.Err()
}

// Prepare retains partial acquisitions even on error. The caller must Rollback
// them under the same start deadline before publishing an ordinary result.
func (s *SplitResources) Prepare(ctx context.Context, request protocol.Execute) (string, string, error) {
	return s.prepare(ctx, request, splitSession)
}

func (s *SplitResources) prepare(ctx context.Context, request protocol.Execute, session func() ([]splitEntry, error)) (string, string, error) {
	if s.operation != nil {
		return "", "", fmt.Errorf("split operation already owned")
	}
	base := sessionRuntimeDir + "/split/" + request.OperationID
	m := protocol.PrivateExecute{OperationID: request.OperationID, ExecutionShape: request.ExecutionShape, Timing: request.Timing, Publication: request.Publication, Observation: request.Observation, Inspections: request.Inspections, StdoutFIFO: base + ".stdout", StderrFIFO: base + ".stderr", Source: request.Source}
	if request.ExecutionShape != "split" {
		return "", "", fmt.Errorf("split setup requires split execution")
	}
	if _, err := protocol.EncodePrivate(m, protocol.EnvoyToAwsh); err != nil {
		return "", "", err
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		return "", "", fmt.Errorf("split setup requires the operation-start deadline")
	}
	op := &splitOperation{id: request.OperationID, deadline: deadline, readers: [2]int{-1, -1}, keepalives: [2]int{-1, -1}}
	s.operation = op
	fail := func(err error) (string, string, error) { return "", "", err }
	if err := s.boundary(ctx, "ancestors"); err != nil {
		return fail(err)
	}
	var err error
	op.chain, err = session()
	if err != nil {
		return fail(err)
	}
	parent := op.chain[len(op.chain)-1].fd
	if err = syscall.Mkdirat(parent, "split", 0700); err != nil {
		return fail(err)
	}
	op.unproven = true
	directory, err := splitOpen(parent, "split", splitPathDescriptor|syscall.O_DIRECTORY)
	if err != nil {
		return fail(fmt.Errorf("cannot prove created split directory: %w", err))
	}
	op.directory = &directory
	op.unproven = false
	if !splitPrivate(directory.stat, syscall.S_IFDIR, 0700) {
		return fail(fmt.Errorf("split directory identity or mode"))
	}
	if err = s.boundary(ctx, "directory"); err != nil {
		return fail(err)
	}
	for i, stream := range []string{"stdout", "stderr"} {
		name := request.OperationID + "." + stream
		if err = syscall.Mknodat(directory.fd, name, syscall.S_IFIFO|0600, 0); err != nil {
			return fail(err)
		}
		op.unproven = true
		entry, e := splitOpen(directory.fd, name, splitPathDescriptor)
		if e != nil {
			return fail(fmt.Errorf("cannot prove created split FIFO: %w", e))
		}
		op.fifos = append(op.fifos, entry)
		op.unproven = false
		if !splitPrivate(entry.stat, syscall.S_IFIFO, 0600) {
			return fail(fmt.Errorf("split FIFO identity or mode"))
		}
		if err = s.boundary(ctx, stream+"-FIFO"); err != nil {
			return fail(err)
		}
		reader, e := splitOpen(directory.fd, name, syscall.O_RDONLY|syscall.O_NONBLOCK)
		if e != nil {
			return fail(e)
		}
		op.readers[i] = reader.fd
		if !splitSame(reader.stat, entry.stat) {
			return fail(fmt.Errorf("split reader identity changed"))
		}
		if err = s.boundary(ctx, stream+"-reader"); err != nil {
			return fail(err)
		}
		writer, e := splitOpen(directory.fd, name, syscall.O_WRONLY|syscall.O_NONBLOCK)
		if e != nil {
			return fail(e)
		}
		op.keepalives[i] = writer.fd
		if !splitSame(writer.stat, entry.stat) {
			return fail(fmt.Errorf("split keepalive identity changed"))
		}
		if err = s.boundary(ctx, stream+"-keepalive"); err != nil {
			return fail(err)
		}
	}
	if err = s.validate(); err != nil {
		return fail(err)
	}
	if err = s.boundary(ctx, "validation"); err != nil {
		return fail(err)
	}
	if err = s.validate(); err != nil {
		return fail(err)
	}
	if err = ctx.Err(); err != nil {
		return fail(err)
	}
	return m.StdoutFIFO, m.StderrFIFO, nil
}

func splitCheck(parent int, entry splitEntry) (err error) {
	current, err := splitOpen(parent, entry.name, splitPathDescriptor)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, syscall.Close(current.fd)) }()
	if !splitSame(current.stat, entry.stat) {
		return fmt.Errorf("split entry identity changed: %s", entry.name)
	}
	return nil
}

func (s *SplitResources) validate() error {
	op := s.operation
	for i := 1; i < len(op.chain); i++ {
		if err := splitCheck(op.chain[i-1].fd, op.chain[i]); err != nil {
			return err
		}
	}
	if op.directory == nil {
		return fmt.Errorf("missing split directory proof")
	}
	if err := splitCheck(op.chain[len(op.chain)-1].fd, *op.directory); err != nil {
		return err
	}
	for i, entry := range op.fifos {
		if err := splitCheck(op.directory.fd, entry); err != nil {
			return err
		}
		for _, fd := range []int{op.readers[i], op.keepalives[i]} {
			var st syscall.Stat_t
			if err := syscall.Fstat(fd, &st); err != nil {
				return err
			}
			if !splitSame(st, entry.stat) {
				return fmt.Errorf("split descriptor identity changed")
			}
		}
	}
	return nil
}

func splitRemoveDirectory(parent int, name string) error {
	p, err := syscall.BytePtrFromString(name)
	if err != nil {
		return err
	}
	_, _, errno := syscall.Syscall(syscall.SYS_UNLINKAT, uintptr(parent), uintptr(unsafe.Pointer(p)), 0x200) // AT_REMOVEDIR
	if errno != 0 {
		return errno
	}
	return nil
}

// Rollback attempts every close and identity-checked removal, even after
// cancellation. A new deadline cannot prove disposal in the original epoch.
func (s *SplitResources) Rollback(ctx context.Context, operation string) error {
	op := s.operation
	if op == nil || op.id != operation {
		return fmt.Errorf("split rollback operation mismatch")
	}
	deadline, ok := ctx.Deadline()
	if !ok || !deadline.Equal(op.deadline) {
		return fmt.Errorf("split rollback epoch mismatch")
	}
	var result error
	add := func(err error) { result = errors.Join(result, err) }
	add(ctx.Err())
	if op.unproven {
		add(fmt.Errorf("created split entry lacks retained identity"))
	}
	// Never retry a failed Close: the numeric descriptor may have been reused.
	closeFD := func(fd *int) {
		if *fd >= 0 {
			add(syscall.Close(*fd))
			*fd = -1
		}
	}
	for i := range op.keepalives {
		closeFD(&op.keepalives[i])
	}
	for i := range op.readers {
		closeFD(&op.readers[i])
	}
	if op.directory != nil {
		for i := range op.fifos {
			entry := &op.fifos[i]
			if err := splitCheck(op.directory.fd, *entry); err != nil {
				add(err)
			} else {
				add(syscall.Unlinkat(op.directory.fd, entry.name))
			}
			closeFD(&entry.fd)
		}
		for i := 1; i < len(op.chain); i++ {
			add(splitCheck(op.chain[i-1].fd, op.chain[i]))
		}
		parent := op.chain[len(op.chain)-1].fd
		if err := splitCheck(parent, *op.directory); err != nil {
			add(err)
		} else {
			add(splitRemoveDirectory(parent, op.directory.name))
		}
		closeFD(&op.directory.fd)
	}
	for i := len(op.chain) - 1; i >= 0; i-- {
		closeFD(&op.chain[i].fd)
	}
	add(ctx.Err())
	s.operation = nil
	return result
}
