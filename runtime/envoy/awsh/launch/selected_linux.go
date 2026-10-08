package launch

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"unsafe"
)

type shellReap struct {
	Status syscall.WaitStatus
	Err    error
}

// selectedChild owns only persistent Bash identity and reap, never operation
// descendants or lifecycle decisions. Its caller owns the launch deadline.
type selectedChild struct {
	PID     int
	Signals chan os.Signal
	Reaped  chan shellReap
	done    chan struct{}
	once    sync.Once
	mu      sync.Mutex
	ended   bool
	reap    shellReap
}

func launchSelected(ctx context.Context, h Handoff, signals []uint64) (c *selectedChild, err error) {
	if err = h.intake(); err != nil {
		return nil, err
	}
	if _, err = syscall.Setsid(); err != nil {
		return nil, err
	}
	pid := os.Getpid()
	pgid, e := syscall.Getpgid(0)
	sid, _, se := syscall.Syscall(syscall.SYS_GETSID, 0, 0, 0)
	if e != nil || se != 0 || pgid != pid || int(sid) != pid {
		return nil, fmt.Errorf("Awsh session/group identity")
	}
	if err = ioctl(h.Slave, syscall.TIOCSCTTY, nil); err != nil {
		return nil, err
	}
	c = &selectedChild{Signals: make(chan os.Signal, 1), Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	signal.Ignore(syscall.SIGHUP, syscall.SIGTTOU)
	signal.Notify(c.Signals, syscall.SIGUSR1) // installed before Bash exists
	defer func() {
		if err != nil {
			c.close()
		}
	}()
	var report, release *os.File
	c.PID, report, release, err = spawn(h.Slave, signals)
	if err != nil {
		return
	}
	defer report.Close()
	defer release.Close()
	c.startReaper()
	setup := make(chan error, 1)
	go func() {
		var b [1]byte
		_, e := io.ReadFull(report, b[:])
		if e == nil && b[0] != 1 {
			e = fmt.Errorf("invalid child setup report")
		}
		setup <- e
	}()
	select {
	case err = <-setup:
	case <-ctx.Done():
		err = ctx.Err()
	case <-c.Signals:
		err = fmt.Errorf("unexpected pre-exec release signal")
	}
	if err != nil {
		return
	}
	foreground := int32(c.PID)
	if err = ioctl(h.Slave, syscall.TIOCSPGRP, unsafe.Pointer(&foreground)); err != nil {
		return
	}
	if err = terminalIdentity(h.Slave, pid, c.PID); err != nil {
		return
	}
	if err = ctx.Err(); err != nil {
		return
	}
	n, e := release.Write([]byte{1})
	err = e
	if err == nil && n != 1 {
		err = io.ErrShortWrite
	}
	return
}

func (c *selectedChild) startReaper() {
	go func() {
		// WNOWAIT retains the zombie and its PID while close races this wait.
		// Only this owner reaps; actual reap and signalling share the mutex.
		var info [16]uint64
		var waitErr syscall.Errno
		for {
			_, _, waitErr = syscall.Syscall6(syscall.SYS_WAITID, 1, uintptr(c.PID), uintptr(unsafe.Pointer(&info[0])), syscall.WEXITED|syscall.WNOWAIT, 0, 0)
			if waitErr != syscall.EINTR {
				break
			}
		}
		c.mu.Lock()
		if waitErr != 0 {
			c.reap.Err = waitErr
		}
		if waitErr != syscall.ECHILD {
			if waitErr != 0 {
				// A failed wait cannot leave a still-owned live shell behind.
				_ = syscall.Kill(-c.PID, syscall.SIGKILL)
				_ = syscall.Kill(c.PID, syscall.SIGKILL)
			}
			for {
				_, e := syscall.Wait4(c.PID, &c.reap.Status, 0, nil)
				if e != syscall.EINTR {
					if c.reap.Err == nil {
						c.reap.Err = e
					}
					break
				}
			}
		}
		c.ended = true
		c.mu.Unlock()
		c.Reaped <- c.reap
		close(c.done)
	}()
}

func (c *selectedChild) close() {
	c.once.Do(func() {
		if c.PID > 0 {
			// Serialize reap with signals so the selected-child identity cannot
			// be reused between the liveness check and either kill.
			c.mu.Lock()
			if !c.ended {
				_ = syscall.Kill(-c.PID, syscall.SIGKILL)
				_ = syscall.Kill(c.PID, syscall.SIGKILL)
			}
			c.mu.Unlock()
			<-c.done
		}
		signal.Stop(c.Signals)
	})
}
