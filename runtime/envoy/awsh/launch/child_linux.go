package launch

import (
	"fmt"
	"os"
	"runtime"
	"strconv"
	"syscall"
	"unsafe"
)

// childArguments is consumed only by architecture-specific raw syscall code.
// The child never returns into Go, allocates, locks, or runs a signal handler.
type childArguments struct {
	path                                   *byte
	argv, env                              **byte
	signals                                *uint64
	count, slave, report, release, fdLimit uint64
}

func forkChild(args *childArguments) (pid int64, errno uint64)

func stringVector(values []string) ([]*byte, error) {
	v := make([]*byte, len(values)+1)
	for i, s := range values {
		p, err := syscall.BytePtrFromString(s)
		if err != nil {
			return nil, err
		}
		v[i] = p
	}
	return v, nil
}

func spawn(slave int, signals []uint64) (pid int, report, release *os.File, err error) {
	if len(signals) == 0 {
		return 0, nil, nil, fmt.Errorf("missing selected-build signal inventory")
	}
	seen := map[uint64]bool{}
	for _, s := range signals {
		if s == 0 || s > 64 || s == 9 || s == 19 || seen[s] {
			return 0, nil, nil, fmt.Errorf("invalid catchable signal")
		}
		seen[s] = true
	}
	for _, s := range []uint64{1, 2, 3, 10, 20, 21, 22} {
		if !seen[s] {
			return 0, nil, nil, fmt.Errorf("incomplete signal reset inventory")
		}
	}
	argv, err := stringVector([]string{"/bin/bash", "--noprofile", "--rcfile", "/omegaflow-runtime/etc/awsh-bashrc", "-i"})
	if err != nil {
		return
	}
	env, err := stringVector(os.Environ())
	if err != nil {
		return
	}
	rr, rw, err := os.Pipe()
	if err != nil {
		return
	}
	lr, lw, err := os.Pipe()
	if err != nil {
		rr.Close()
		rw.Close()
		return
	}
	defer rw.Close()
	defer lr.Close()
	defer func() {
		if err != nil {
			rr.Close()
			lw.Close()
		}
	}()
	// Duplicate child inputs above the fixed barrier slots before raw fork.
	fds := []int{slave, int(rw.Fd()), int(lr.Fd())}
	dup := make([]int, 3)
	for i, fd := range fds {
		dup[i], err = fcntl(fd, syscall.F_DUPFD_CLOEXEC, 10)
		if err != nil {
			return
		}
		defer syscall.Close(dup[i])
	}
	var limit syscall.Rlimit
	if err = syscall.Getrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil {
		return
	}
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return
	}
	for _, e := range entries {
		n, _ := strconv.ParseUint(e.Name(), 10, 64)
		if n >= limit.Cur {
			limit.Cur = n + 1
		}
	}
	a := childArguments{argv[0], &argv[0], &env[0], &signals[0], uint64(len(signals)), uint64(dup[0]), uint64(dup[1]), uint64(dup[2]), limit.Cur}
	runtime.LockOSThread()
	child, e := forkChild(&a)
	runtime.UnlockOSThread()
	runtime.KeepAlive(argv)
	runtime.KeepAlive(env)
	runtime.KeepAlive(signals)
	runtime.KeepAlive(unsafe.Pointer(&a))
	if e != 0 {
		err = syscall.Errno(e)
		return
	}
	return int(child), rr, lw, nil
}
