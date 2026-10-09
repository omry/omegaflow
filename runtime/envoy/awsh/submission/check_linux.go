//go:build linux

package submission

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Check admits only a current qualified /bin/bash and uses the caller's existing
// operation-start deadline. It neither launches an operation nor creates a timer.
func Check(ctx context.Context, m protocol.HelperSourceReply, expected string, target bashbuild.Target, inputs map[string]string) (string, error) {
	return check(ctx, m, func() error {
		_, err := bashbuild.LookupAwsh("/bin/bash", expected, target, inputs)
		return err
	})
}

// parseOutput remembers presence, never retains unbounded checker diagnostics.
// Each command stream has its own writer; Run joins its copying goroutines.
type parseOutput struct{ present bool }

func (p *parseOutput) Write(b []byte) (int, error) {
	p.present = p.present || len(b) != 0
	return len(b), nil
}

func check(ctx context.Context, m protocol.HelperSourceReply, verify func() error) (string, error) {
	if _, ok := ctx.Deadline(); !ok {
		return "", fmt.Errorf("source checker requires the existing start deadline")
	}
	frame, err := checkedFrame(m)
	if err != nil {
		return "", err
	}
	for _, text := range []string{m.Source, frame} {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		if err := verify(); err != nil { // rehash before each fork, not once per operation
			return "", err
		}
		if err := protectedDescriptors(); err != nil {
			return "", err
		}
		cmd := exec.CommandContext(ctx, "/bin/bash", "--noprofile", "--norc", "-n")
		cmd.Stdin = strings.NewReader(text)
		stdout, stderr := &parseOutput{}, &parseOutput{}
		cmd.Stdout, cmd.Stderr = stdout, stderr
		err := cmd.Run()
		if e := ctx.Err(); e != nil {
			return "", e
		}
		var exit *exec.ExitError
		if err != nil && !errors.As(err, &exit) {
			return "", err // failed launch is not an authored syntax error
		}
		if err != nil || stdout.present || stderr.present {
			return "", &Rejection{"source-syntax", "source or canonical frame failed output-empty syntax check"}
		}
	}
	return frame, nil
}

// Intake restores CLOEXEC on every session handoff, and Go opens helper/poller
// descriptors with CLOEXEC. Reject any broken inheritance protection before
// invoking os/exec; never repair arbitrary parent descriptors or pass ExtraFiles.
func protectedDescriptors() error {
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return err
	}
	for _, entry := range entries {
		fd, err := strconv.Atoi(entry.Name())
		if err != nil || fd < 3 {
			continue
		}
		flags, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if e == syscall.EBADF { // the directory's own descriptor has closed
			continue
		}
		if e != 0 || flags&syscall.FD_CLOEXEC == 0 {
			return fmt.Errorf("unprotected checker descriptor %d", fd)
		}
	}
	return nil
}
