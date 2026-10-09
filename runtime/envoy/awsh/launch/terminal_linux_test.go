package launch

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

// The filter exists only in an isolated test process. Production performs real
// ioctls and has no injection switch or replaceable terminal backend.
func denyTerminalRequest(request uintptr) error {
	filter := []syscall.SockFilter{
		{Code: 0x20, K: 0}, // seccomp_data.nr
		{Code: 0x15, Jf: 3, K: uint32(syscall.SYS_IOCTL)},
		{Code: 0x20, K: 24}, // low word of ioctl's request argument
		{Code: 0x15, Jf: 1, K: uint32(request)},
		{Code: 0x06, K: 0x00050000 | uint32(syscall.EPERM)},
		{Code: 0x06, K: 0x7fff0000},
	}
	program := syscall.SockFprog{Len: uint16(len(filter)), Filter: &filter[0]}
	if _, _, e := syscall.Syscall6(syscall.SYS_PRCTL, 38, 1, 0, 0, 0, 0); e != 0 {
		return e
	}
	_, _, e := syscall.Syscall6(syscall.SYS_PRCTL, 22, 2, uintptr(unsafe.Pointer(&program)), 0, 0, 0)
	runtime.KeepAlive(filter)
	if e != 0 {
		return e
	}
	return nil
}

func TestTerminalDescriptorChild(t *testing.T) {
	if os.Getenv("OMEGAFLOW_TERMINAL_DESCRIPTOR_CHILD") != "1" {
		return
	}
	var stdin syscall.Stat_t
	if err := syscall.Fstat(0, &stdin); err != nil {
		t.Fatal(err)
	}
	fds, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		t.Fatal(err)
	}
	for _, fd := range fds {
		if fd.Name() == "0" || fd.Name() == "1" || fd.Name() == "2" {
			continue
		}
		var st syscall.Stat_t
		if syscall.Stat("/proc/self/fd/"+fd.Name(), &st) == nil && st.Mode&syscall.S_IFMT == syscall.S_IFCHR && st.Rdev == stdin.Rdev {
			t.Fatal("terminal lease inherited by exec child", fd.Name())
		}
	}
}

func TestTerminalLeaseProcess(t *testing.T) {
	mode := os.Getenv("OMEGAFLOW_TERMINAL_LEASE_CASE")
	if mode == "" {
		return
	}
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	terminal := Terminal{session: os.Getpid(), foreground: os.Getpid()}
	reference, err := terminal.snapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	fdsBefore, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		t.Fatal(err)
	}
	if mode == "write-failure" || mode == "read-failure" {
		request := uintptr(syscall.TCSETS)
		if mode == "read-failure" {
			request = syscall.TCGETS
		}
		if err = denyTerminalRequest(request); err != nil {
			t.Fatal(err)
		}
		if err = terminal.restore(ctx, reference); !errors.Is(err, syscall.EPERM) {
			t.Fatalf("injected kernel failure accepted: %v", err)
		}
	} else {
		switch mode {
		case "normal", "raw-no-echo":
			reference.Cc[syscall.VINTR] = 7
			reference.Cc[syscall.VEOF] = 5
			if mode == "raw-no-echo" {
				reference.Lflag &^= syscall.ICANON | syscall.ECHO | syscall.ISIG
				reference.Oflag &^= syscall.OPOST
				reference.Iflag &^= syscall.ICRNL | syscall.IXON
				reference.Cc[syscall.VMIN], reference.Cc[syscall.VTIME] = 1, 0
			}
			if err = terminal.restore(ctx, reference); err != nil {
				t.Fatal(err)
			}
			if err = terminal.prepareReadline(ctx, reference); err != nil {
				t.Fatal(err)
			}
			prepared, err := terminal.snapshot(ctx)
			if err != nil || prepared.Lflag&syscall.ICANON == 0 || prepared.Lflag&syscall.ECHO != 0 {
				t.Fatalf("echo-off canonical preparation: %#v %v", prepared, err)
			}
			expected := reference
			expected.Lflag |= syscall.ICANON
			expected.Lflag &^= syscall.ECHO
			if prepared != expected {
				t.Fatal("preparation changed other saved termios fields")
			}
			if err = terminal.restore(ctx, reference); err != nil {
				t.Fatal(err)
			}
			actual, err := terminal.snapshot(ctx)
			if err != nil || actual != reference {
				t.Fatalf("complete workload state not restored: %#v != %#v: %v", actual, reference, err)
			}
			if err = terminal.lease(ctx, func(fd int) error {
				flags, e := fcntl(fd, syscall.F_GETFD, 0)
				if e != nil || flags&syscall.FD_CLOEXEC == 0 {
					return fmt.Errorf("lease not close-on-exec: %v", e)
				}
				bin, e := os.Executable()
				if e != nil {
					return e
				}
				child := exec.Command(bin, "-test.run=^TestTerminalDescriptorChild$")
				child.Stdin = os.Stdin
				child.Env = append(os.Environ(), "OMEGAFLOW_TERMINAL_DESCRIPTOR_CHILD=1")
				if output, e := child.CombinedOutput(); e != nil {
					return fmt.Errorf("descriptor child: %s: %w", output, e)
				}
				return nil
			}); err != nil {
				t.Fatal(err)
			}
		case "wrong-session", "wrong-foreground":
			if mode == "wrong-session" {
				terminal.session++
			} else {
				terminal.foreground++
			}
			if err = terminal.restore(ctx, reference); err == nil {
				t.Fatal("wrong terminal identity accepted")
			}
			actual, e := termios(0)
			if e != nil || actual != reference {
				t.Fatal("identity rejection changed terminal state")
			}
		case "readback-mismatch":
			// Linux's legacy TCSETS/TCGETS ignores the final Go control slot.
			// The complete-state comparison must reject its normalized readback.
			reference.Cc[len(reference.Cc)-1] ^= 1
			if err = terminal.restore(ctx, reference); err == nil || !strings.Contains(err.Error(), "mismatch") {
				t.Fatalf("inexact readback accepted: %v", err)
			}
		case "cancelled", "expired":
			if mode == "cancelled" {
				cancel()
			} else {
				ctx, cancel = context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
				defer cancel()
			}
			if err = terminal.restore(ctx, reference); !errors.Is(err, ctx.Err()) {
				t.Fatalf("ended phase accepted: %v", err)
			}
		case "cancel-after-write":
			if err = terminal.lease(ctx, func(fd int) error {
				if e := ioctl(fd, syscall.TCSETS, unsafe.Pointer(&reference)); e != nil {
					return e
				}
				cancel() // deterministic cancellation before the lease returns
				return nil
			}); !errors.Is(err, context.Canceled) {
				t.Fatalf("post-write context failure accepted: %v", err)
			}
		default:
			t.Fatal("unknown test case", mode)
		}
	}
	fdsAfter, err := os.ReadDir("/proc/self/fd")
	if err != nil || len(fdsAfter) != len(fdsBefore) {
		t.Fatalf("lease retained descriptors: %d -> %d: %v", len(fdsBefore), len(fdsAfter), err)
	}
}

func TestTerminalLeaseRestoration(t *testing.T) {
	for _, mode := range []string{"normal", "raw-no-echo", "wrong-session", "wrong-foreground", "write-failure", "read-failure", "readback-mismatch", "cancelled", "expired", "cancel-after-write"} {
		t.Run(mode, func(t *testing.T) {
			_, slave := openPTY(t)
			bin, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command(bin, "-test.run=^TestTerminalLeaseProcess$", "-test.timeout=10s")
			cmd.Stdin = slave
			cmd.Env = append(os.Environ(), "OMEGAFLOW_TERMINAL_LEASE_CASE="+mode)
			cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("isolated lease case %s: %s: %v", mode, output, err)
			}
		})
	}
}
