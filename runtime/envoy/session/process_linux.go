//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
)

// Subreaper status is process-wide. The dedicated Envoy enables it before
// creating Awsh; callers must not enable it in a shared test/controller process.
func enableSubreaper() error {
	if _, _, e := syscall.Syscall6(syscall.SYS_PRCTL, 36, 1, 0, 0, 0, 0); e != 0 {
		return e
	}
	return requireSubreaper()
}

func requireSubreaper() error {
	var enabled int32
	_, _, e := syscall.Syscall6(syscall.SYS_PRCTL, 37, uintptr(unsafe.Pointer(&enabled)), 0, 0, 0, 0)
	if e != 0 {
		return e
	}
	if enabled != 1 {
		return fmt.Errorf("Envoy is not a subreaper")
	}
	return nil
}

type processInfo struct {
	PID, Parent, Group, Session int
	Start                       uint64
	Live                        bool
}

type processRef struct {
	pid, fd int
	start   uint64
}

func processContext(ctx context.Context) error {
	if ctx == nil {
		return fmt.Errorf("missing process census context")
	}
	if _, ok := ctx.Deadline(); !ok {
		return fmt.Errorf("process census requires the caller's deadline")
	}
	return ctx.Err()
}

func readProcess(pid int) (processInfo, error) {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return processInfo{}, err
	}
	return parseProcess(pid, string(b))
}

func parseProcess(pid int, stat string) (processInfo, error) {
	start, end := strings.IndexByte(stat, '('), strings.LastIndexByte(stat, ')')
	if pid <= 0 || start < 1 || end <= start || strings.TrimSpace(stat[:start]) != strconv.Itoa(pid) {
		return processInfo{}, fmt.Errorf("malformed process identity")
	}
	f := strings.Fields(stat[end+1:])
	if len(f) < 20 || len(f[0]) != 1 {
		return processInfo{}, fmt.Errorf("short process identity")
	}
	if f[0] == "X" {
		return processInfo{}, os.ErrProcessDone
	}
	p := processInfo{PID: pid}
	for i, target := range []*int{&p.Parent, &p.Group, &p.Session} {
		n, err := strconv.ParseInt(f[i+1], 10, 32)
		if err != nil || n < 0 {
			return processInfo{}, fmt.Errorf("invalid process topology")
		}
		*target = int(n)
	}
	var err error
	p.Start, err = strconv.ParseUint(f[19], 10, 64)
	if err != nil {
		return processInfo{}, fmt.Errorf("invalid process start identity")
	}
	return p, nil
}

func processGone(err error) bool {
	// A disposal error must remain fatal even when joined with an expected
	// exit/reap race. Do not discard the non-exit half of a joined failure.
	if joined, ok := err.(interface{ Unwrap() []error }); ok {
		parts := joined.Unwrap()
		if len(parts) == 0 {
			return false
		}
		for _, part := range parts {
			if !processGone(part) {
				return false
			}
		}
		return true
	}
	return errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) || errors.Is(err, os.ErrProcessDone)
}

func pinProcess(ctx context.Context, pid int) (*processRef, processInfo, error) {
	if err := processContext(ctx); err != nil {
		return nil, processInfo{}, err
	}
	if pid <= 0 || int64(pid) > 2147483647 {
		return nil, processInfo{}, fmt.Errorf("invalid process PID")
	}
	fd, _, e := syscall.Syscall(434, uintptr(pid), 0, 0) // pidfd_open; CLOEXEC in the kernel
	if e != 0 {
		return nil, processInfo{}, e
	}
	r := &processRef{pid: pid, fd: int(fd)}
	p, err := r.observe(ctx)
	if err != nil {
		return nil, processInfo{}, errors.Join(err, r.close())
	}
	r.start = p.Start
	return r, p, nil
}

// fdinfo reports -1 after this exact lifetime is reaped. Checking it on both
// sides of a stat read binds even an unreaped zombie without trusting PID/start
// time alone. A reused numeric PID cannot revive the retained pidfd.
func (r *processRef) identity() error {
	if r.fd < 0 {
		return os.ErrClosed
	}
	b, err := os.ReadFile("/proc/self/fdinfo/" + strconv.Itoa(r.fd))
	if err != nil {
		return errors.Join(syscall.EBADF, err) // Lost pin metadata is not process exit.
	}
	for _, line := range strings.Split(string(b), "\n") {
		if !strings.HasPrefix(line, "Pid:") {
			continue
		}
		pid, err := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(line, "Pid:")))
		if err != nil {
			return err
		}
		if pid == -1 {
			return os.ErrProcessDone
		}
		if pid != r.pid {
			return fmt.Errorf("pidfd lifetime mismatch")
		}
		return nil
	}
	return fmt.Errorf("descriptor is not a pidfd")
}

func (r *processRef) observe(ctx context.Context) (processInfo, error) {
	if err := processContext(ctx); err != nil {
		return processInfo{}, err
	}
	if err := r.identity(); err != nil {
		return processInfo{}, err
	}
	p, err := readProcess(r.pid)
	if err != nil {
		return processInfo{}, err
	}
	if err := r.identity(); err != nil {
		return processInfo{}, err
	}
	if r.start != 0 && r.start != p.Start {
		return processInfo{}, fmt.Errorf("process lifetime changed")
	}
	fd := struct {
		fd              int32
		events, revents int16
	}{fd: int32(r.fd), events: 1}
	zero := syscall.Timespec{}
	for {
		_, _, e := syscall.Syscall6(syscall.SYS_PPOLL, uintptr(unsafe.Pointer(&fd)), 1, uintptr(unsafe.Pointer(&zero)), 0, 0, 0)
		if e == syscall.EINTR {
			if err := processContext(ctx); err != nil {
				return processInfo{}, err
			}
			continue
		}
		if e != 0 {
			return processInfo{}, e
		}
		break
	}
	if fd.revents & ^int16(1|16) != 0 {
		return processInfo{}, fmt.Errorf("invalid pidfd poll result")
	}
	p.Live = fd.revents == 0
	return p, processContext(ctx)
}

func (r *processRef) close() error {
	if r.fd < 0 {
		return nil
	}
	fd := r.fd
	r.fd = -1 // A failed close never authorizes closing a possibly reused FD.
	return syscall.Close(fd)
}
