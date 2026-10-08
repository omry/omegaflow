// Package launch owns only the selected-shell launch and terminal handoff.
package launch

import (
	"fmt"
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
