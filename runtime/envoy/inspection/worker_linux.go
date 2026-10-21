//go:build linux

package inspection

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const WorkerModeArgument = "inspection-worker"

// Worker owns one child of this Envoy executable. Done closes only after its
// transport and process wait have joined. Lifecycle result acceptance and the
// cancellation epoch belong to the caller, not to this handle.
type Worker struct {
	process *os.Process
	done    chan struct{}
	results []protocol.InspectionResult
	err     error
}

// StartWorker freezes and validates the complete resolved plan before launch.
// The current executable must dispatch WorkerModeArgument to RunWorkerMode.
// It performs no workload filesystem probes and creates no deadline.
func StartWorker(plans []protocol.ResolvedInspection) (*Worker, error) {
	frozen := make([]protocol.ResolvedInspection, len(plans))
	copy(frozen, plans)
	input, err := protocol.EncodePrivate(protocol.PrivateCompleted{
		OperationID: "inspection", PhysicalCWD: "/", Inspections: frozen,
	}, protocol.AwshToEnvoy)
	if err != nil {
		return nil, err
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	readResult, writeResult, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	defer writeResult.Close()
	cmd := exec.Command(executable, WorkerModeArgument)
	cmd.Dir, cmd.Env = "/", []string{}
	cmd.Stdin = bytes.NewReader(input) // os/exec owns this private input pipe.
	// Nil stdout/stderr become /dev/null; only the result writer crosses at fd3.
	cmd.ExtraFiles = []*os.File{writeResult}
	if err = protectedWorkerDescriptors(); err == nil {
		err = cmd.Start()
	}
	if err != nil {
		readResult.Close()
		return nil, err
	}
	w := &Worker{process: cmd.Process, done: make(chan struct{})}
	go func() {
		data, readErr := io.ReadAll(io.LimitReader(readResult, protocol.MaxFrameBytes+1))
		if len(data) > protocol.MaxFrameBytes {
			readErr = fmt.Errorf("inspection worker result exceeds bound")
		}
		readResult.Close()
		if readErr != nil {
			cmd.Process.Kill()
		}
		waitErr := cmd.Wait()
		w.err = errors.Join(readErr, waitErr)
		if w.err == nil {
			w.results, w.err = decodeWorkerReply(data, frozen)
		}
		close(w.done)
	}()
	return w, nil
}

func (w *Worker) PID() int              { return w.process.Pid }
func (w *Worker) Done() <-chan struct{} { return w.done }

// Result never exposes a reply before EOF and actual reap. Repeated calls
// return independent result slices. A typed Failure never has partial results.
func (w *Worker) Result() ([]protocol.InspectionResult, error) {
	select {
	case <-w.done:
		if w.err != nil {
			return nil, w.err
		}
		return append([]protocol.InspectionResult{}, w.results...), nil
	default:
		return nil, fmt.Errorf("inspection worker has not been reaped")
	}
}

// Kill uses the retained os.Process identity, never an arbitrary numeric PID.
func (w *Worker) Kill() error {
	err := w.process.Kill()
	if errors.Is(err, os.ErrProcessDone) {
		return nil
	}
	return err
}

// Reap proves disposal only. Call Result separately to accept an inspection.
// An expired caller bound does not claim reap or permit another operation.
func (w *Worker) Reap(ctx context.Context) error {
	select {
	case <-w.done:
		return nil
	default:
	}
	select {
	case <-w.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Session intake already restores CLOEXEC and Go opens new descriptors with
// CLOEXEC. Reuse the checker's fail-closed preflight; never repair arbitrary
// parent descriptors or pass session channels to the child.
func protectedWorkerDescriptors() error {
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return err
	}
	for _, entry := range entries {
		fd, err := strconv.Atoi(entry.Name())
		if err != nil || fd < 3 {
			continue
		}
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if errno == syscall.EBADF { // The directory descriptor has closed.
			continue
		}
		if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
			return fmt.Errorf("unprotected inspection descriptor %d", fd)
		}
	}
	return nil
}

// RunWorkerMode is the restricted entry used by the Envoy executable. It has
// no session channels, environment, helper commands or publication authority.
func RunWorkerMode(args []string) error {
	if len(args) != 1 || args[0] != WorkerModeArgument || len(os.Environ()) != 0 {
		return fmt.Errorf("invalid inspection worker mode")
	}
	cwd, err := os.Getwd()
	if err != nil || cwd != "/" {
		return fmt.Errorf("invalid inspection worker cwd")
	}
	var pipes [2]syscall.Stat_t
	for i, fd := range []int{0, 3} {
		if err := syscall.Fstat(fd, &pipes[i]); err != nil || pipes[i].Mode&syscall.S_IFMT != syscall.S_IFIFO {
			return fmt.Errorf("inspection worker requires private pipes")
		}
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFL, 0)
		want := uintptr(syscall.O_RDONLY)
		if fd == 3 {
			want = syscall.O_WRONLY
		}
		if errno != 0 || flags&syscall.O_ACCMODE != want {
			return fmt.Errorf("invalid inspection pipe direction")
		}
		syscall.CloseOnExec(fd)
	}
	if pipes[0].Dev == pipes[1].Dev && pipes[0].Ino == pipes[1].Ino {
		return fmt.Errorf("inspection pipes must be distinct")
	}
	for _, fd := range []int{1, 2} {
		var state syscall.Stat_t
		if err := syscall.Fstat(fd, &state); err != nil || state.Mode&syscall.S_IFMT != syscall.S_IFCHR || state.Rdev != 0x103 {
			return fmt.Errorf("inspection worker output must be /dev/null")
		}
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFL, 0)
		if errno != 0 || flags&syscall.O_ACCMODE != syscall.O_WRONLY {
			return fmt.Errorf("invalid inspection worker output direction")
		}
	}
	result := os.NewFile(3, "inspection-result")
	defer result.Close()
	data, err := io.ReadAll(io.LimitReader(os.Stdin, protocol.MaxFrameBytes+1))
	if err != nil || len(data) > protocol.MaxFrameBytes {
		return fmt.Errorf("invalid inspection worker request")
	}
	message, err := protocol.DecodePrivate(data, protocol.AwshToEnvoy)
	if err != nil {
		return err
	}
	plan, ok := message.(*protocol.PrivateCompleted)
	if !ok || plan.OperationID != "inspection" || plan.Status != 0 || plan.PhysicalCWD != "/" {
		return fmt.Errorf("invalid inspection worker plan")
	}
	results, runErr := Run(plan.Inspections)
	reply, err := encodeWorkerReply(results, runErr)
	if err != nil {
		return err
	}
	for len(reply) != 0 {
		n, err := result.Write(reply)
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		reply = reply[n:]
	}
	return nil
}
