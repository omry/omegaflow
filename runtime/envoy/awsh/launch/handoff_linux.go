package launch

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
)

// Handoff is the exact one-exec intake. Its descriptors belong to Awsh.
type Handoff struct {
	Control, Result, Slave int
	RuntimeDir             string
}

func ParseHandoff(args []string) (Handoff, error) {
	h := Handoff{RuntimeDir: "/run/omegaflow/session"}
	if len(args) != 5 || args[0] != "supervise" {
		return h, fmt.Errorf("invalid supervise arguments")
	}
	names := []string{"--control-fd=", "--result-fd=", "--pty-slave-fd="}
	fds := []*int{&h.Control, &h.Result, &h.Slave}
	for i, prefix := range names {
		if !strings.HasPrefix(args[i+1], prefix) {
			return h, fmt.Errorf("invalid descriptor argument")
		}
		s := strings.TrimPrefix(args[i+1], prefix)
		n, err := strconv.Atoi(s)
		if err != nil || n <= 2 || strconv.Itoa(n) != s {
			return h, fmt.Errorf("invalid descriptor")
		}
		*fds[i] = n
	}
	if args[4] != "--session-runtime-dir="+h.RuntimeDir {
		return h, fmt.Errorf("invalid session runtime path")
	}
	return h, nil
}

func fcntl(fd, command, value int) (int, error) {
	n, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), uintptr(command), uintptr(value))
	if e != 0 {
		return 0, e
	}
	return int(n), nil
}

func (h Handoff) intake() error {
	if h.RuntimeDir != "/run/omegaflow/session" || h.Control <= 2 || h.Result <= 2 || h.Slave <= 2 ||
		h.Control == h.Result || h.Control == h.Slave || h.Result == h.Slave {
		return fmt.Errorf("invalid handoff")
	}
	// Restore inheritance protection on every intake descriptor before forking.
	for _, fd := range []int{h.Control, h.Result, h.Slave} {
		flags, err := fcntl(fd, syscall.F_GETFD, 0)
		if err != nil {
			return err
		}
		if _, err := fcntl(fd, syscall.F_SETFD, flags|syscall.FD_CLOEXEC); err != nil {
			return err
		}
	}
	for fd, mode := range map[int]int{h.Control: syscall.O_RDONLY, h.Result: syscall.O_WRONLY, h.Slave: syscall.O_RDWR} {
		flags, err := fcntl(fd, syscall.F_GETFL, 0)
		if err != nil {
			return err
		}
		if flags&syscall.O_ACCMODE != mode {
			return fmt.Errorf("wrong descriptor direction")
		}
	}
	for fd := 0; fd < 3; fd++ {
		var stat syscall.Stat_t
		if err := syscall.Fstat(fd, &stat); err != nil {
			return err
		}
		path, err := os.Readlink("/proc/self/fd/" + strconv.Itoa(fd))
		if err != nil {
			return err
		}
		flags, err := fcntl(fd, syscall.F_GETFL, 0)
		if err != nil {
			return err
		}
		mode := syscall.O_WRONLY
		if fd == 0 {
			mode = syscall.O_RDONLY
		}
		// Linux /dev/null is character device 1:3 on both supported targets.
		if path != "/dev/null" || stat.Mode&syscall.S_IFMT != syscall.S_IFCHR ||
			stat.Rdev != 0x103 || flags&syscall.O_ACCMODE != mode {
			return fmt.Errorf("Awsh stdio must be /dev/null")
		}
	}
	_, err := termios(h.Slave)
	return err
}
