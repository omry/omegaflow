// Package launch owns only the selected-shell launch and terminal handoff.
package launch

import (
	"context"
	"fmt"
	"os"
	"sync"
	"syscall"
	"unsafe"
)

func ioctl(fd int, request uintptr, value unsafe.Pointer) error {
	_, _, e := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), request, uintptr(value))
	if e != 0 {
		return e
	}
	return nil
}

func terminalIdentity(fd, session, foreground int) error {
	var sid, pgid int32
	if err := ioctl(fd, syscall.TIOCGSID, unsafe.Pointer(&sid)); err != nil {
		return err
	}
	if err := ioctl(fd, syscall.TIOCGPGRP, unsafe.Pointer(&pgid)); err != nil {
		return err
	}
	if int(sid) != session || int(pgid) != foreground {
		return fmt.Errorf("terminal identity mismatch")
	}
	return nil
}

func termios(fd int) (syscall.Termios, error) {
	var t syscall.Termios
	err := ioctl(fd, syscall.TCGETS, unsafe.Pointer(&t))
	return t, err
}

// Terminal serializes short-lived leases; no slave descriptor is retained.
type Terminal struct {
	mu                  sync.Mutex
	session, foreground int
}

func (t *Terminal) lease(ctx context.Context, operation func(int) error) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	f, err := os.OpenFile("/dev/tty", os.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := terminalIdentity(int(f.Fd()), t.session, t.foreground); err != nil {
		return err
	}
	if err := operation(int(f.Fd())); err != nil {
		return err
	}
	return ctx.Err()
}

func (t *Terminal) snapshot(ctx context.Context) (state syscall.Termios, err error) {
	err = t.lease(ctx, func(fd int) error { state, err = termios(fd); return err })
	return
}

func (t *Terminal) prepareReadline(ctx context.Context, reference syscall.Termios) error {
	expected := reference
	expected.Lflag |= syscall.ICANON
	expected.Lflag &^= syscall.ECHO
	return t.restore(ctx, expected)
}

func (t *Terminal) restore(ctx context.Context, expected syscall.Termios) error {
	return t.lease(ctx, func(fd int) error {
		return restoreTermios(fd, expected)
	})
}

func restoreTermios(fd int, expected syscall.Termios) error {
	if err := ioctl(fd, syscall.TCSETS, unsafe.Pointer(&expected)); err != nil {
		return err
	}
	actual, err := termios(fd)
	if err == nil && actual != expected {
		err = fmt.Errorf("termios write mismatch")
	}
	return err
}

func (t *Terminal) drain(ctx context.Context) error {
	return t.lease(ctx, func(fd int) error {
		return drainTerminal(fd)
	})
}

func drainTerminal(fd int) error {
	// Linux tcdrain is TCSBRK with a nonzero argument, not a pointer.
	_, _, e := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), 0x5409, 1)
	if e != 0 {
		return e
	}
	return nil
}
