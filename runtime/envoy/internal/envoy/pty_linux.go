//go:build linux

package envoy

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

const (
	ioctlGetPTNumber = 0x80045430
	ioctlSetPTLock   = 0x40045431
	ioctlSetWinSize  = 0x5414
	ioctlGetPGRP     = 0x540f
	ioctlBytesReady  = 0x541b
)

type windowSize struct {
	Rows    uint16
	Columns uint16
	X       uint16
	Y       uint16
}

func openPTY(columns, rows int) (*os.File, *os.File, error) {
	masterFD, err := syscall.Open("/dev/ptmx", syscall.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, nil, fmt.Errorf("open /dev/ptmx: %w", err)
	}
	master := os.NewFile(uintptr(masterFD), "/dev/ptmx")
	closeMaster := true
	defer func() {
		if closeMaster {
			_ = master.Close()
		}
	}()

	var unlocked int32
	if err := ioctl(masterFD, ioctlSetPTLock, unsafe.Pointer(&unlocked)); err != nil {
		return nil, nil, fmt.Errorf("unlock PTY: %w", err)
	}
	var number uint32
	if err := ioctl(masterFD, ioctlGetPTNumber, unsafe.Pointer(&number)); err != nil {
		return nil, nil, fmt.Errorf("resolve PTY slave: %w", err)
	}
	slavePath := fmt.Sprintf("/dev/pts/%d", number)
	slaveFD, err := syscall.Open(slavePath, syscall.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, nil, fmt.Errorf("open PTY slave: %w", err)
	}
	slave := os.NewFile(uintptr(slaveFD), slavePath)
	if err := setWindowSize(masterFD, columns, rows); err != nil {
		_ = slave.Close()
		return nil, nil, err
	}
	closeMaster = false
	return master, slave, nil
}

func setWindowSize(fd, columns, rows int) error {
	size := windowSize{Rows: uint16(rows), Columns: uint16(columns)}
	if err := ioctl(fd, ioctlSetWinSize, unsafe.Pointer(&size)); err != nil {
		return fmt.Errorf("set PTY size: %w", err)
	}
	return nil
}

func foregroundProcessGroup(fd int) (int, error) {
	var group int32
	if err := ioctl(fd, ioctlGetPGRP, unsafe.Pointer(&group)); err != nil {
		return 0, fmt.Errorf("read PTY foreground process group: %w", err)
	}
	if group <= 0 {
		return 0, fmt.Errorf("PTY foreground process group is invalid: %d", group)
	}
	return int(group), nil
}

func bytesReady(fd int) (int, error) {
	var count int32
	if err := ioctl(fd, ioctlBytesReady, unsafe.Pointer(&count)); err != nil {
		return 0, err
	}
	return int(count), nil
}

func ioctl(fd int, request uintptr, value unsafe.Pointer) error {
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), request, uintptr(value))
	if errno != 0 {
		return errno
	}
	return nil
}
