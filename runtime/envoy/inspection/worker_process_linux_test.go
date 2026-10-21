//go:build linux

package inspection

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// The actual test executable hosts both Envoy roles. Only this test assembly
// recognizes fixture basenames; production has no fault or executable override.
func TestMain(m *testing.M) {
	if len(os.Args) == 2 && os.Args[1] == WorkerModeArgument {
		var inherited syscall.Stat_t
		if err := syscall.Fstat(142, &inherited); err != syscall.EBADF {
			os.Exit(72)
		}
		for fd := 600; fd < 610; fd++ {
			var s syscall.Stat_t
			if err := syscall.Fstat(fd, &s); err != syscall.EBADF {
				os.Exit(72)
			}
		}
		if name := filepath.Base(os.Args[0]); strings.HasPrefix(name, "b7-worker-") {
			if len(os.Environ()) != 0 {
				os.Exit(71)
			}
			workerFixture(name)
			os.Exit(0)
		}
		if err := RunWorkerMode(os.Args[1:]); err != nil {
			os.Exit(2)
		}
		os.Exit(0)
	}
	if err := protectTestParentDescriptors(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(79)
	}
	os.Exit(m.Run())
}

// The test runner can inherit descriptors from CI. Protect them only in the
// test parent assembly. Worker entry above must observe absence before this
// setup; production StartWorker must still reject later unprotected descriptors.
func protectTestParentDescriptors() error {
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
		if errno == syscall.EBADF { // ReadDir's directory descriptor is already closed.
			continue
		}
		if errno != 0 {
			return errno
		}
		if _, _, errno = syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_SETFD, flags|syscall.FD_CLOEXEC); errno != 0 {
			return errno
		}
	}
	return nil
}

func TestWorkerInheritedTestParentDescriptor(t *testing.T) {
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer readPipe.Close()
	defer writePipe.Close()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, "-test.run=^TestWorkerActualInspection$", "-test.timeout=8s")
	cmd.ExtraFiles = make([]*os.File, 140)
	cmd.ExtraFiles[139] = readPipe // fd 142 in the actual test parent.
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("inherited test-parent descriptor prevented worker launch: %v: %s", err, output)
	}
}

func workerFixture(name string) {
	input, err := io.ReadAll(io.LimitReader(os.Stdin, protocol.MaxFrameBytes+1))
	if err != nil {
		os.Exit(73)
	}
	m, err := protocol.DecodePrivate(input, protocol.AwshToEnvoy)
	if err != nil {
		os.Exit(74)
	}
	p, ok := m.(*protocol.PrivateCompleted)
	if !ok || len(p.Inspections) != 1 {
		os.Exit(75)
	}
	plan := p.Inspections[0]
	reply, err := encodeWorkerReply([]protocol.InspectionResult{{InspectionID: plan.InspectionID, Kind: plan.Kind, ResolvedPath: plan.ResolvedPath, PathKind: "file"}}, nil)
	if err != nil {
		os.Exit(76)
	}
	switch name {
	case "b7-worker-malformed":
		reply = []byte("R[")
	case "b7-worker-trailing":
		reply = append(reply, []byte("[]")...)
	case "b7-worker-large":
		reply = []byte("R" + strings.Repeat("a", protocol.MaxFrameBytes))
	case "b7-worker-mismatch":
		reply = []byte(strings.Replace(string(reply), `"inspection_id":"fixture"`, `"inspection_id":"different"`, 1))
	case "b7-worker-nonzero", "b7-worker-late-exit", "b7-worker-block":
	default:
		os.Exit(77)
	}
	result := os.NewFile(3, "test-only-worker-result")
	if name != "b7-worker-block" {
		if _, err := result.Write(reply); err != nil {
			os.Exit(78)
		}
	}
	if name == "b7-worker-large" {
		for { // The parent must reject the bound without waiting for EOF.
			syscall.Pause()
		}
	}
	if name == "b7-worker-nonzero" {
		result.Close()
		os.Exit(9)
	}
	if name == "b7-worker-late-exit" {
		result.Close() // A valid complete reply and EOF still do not prove reap.
	}
	if name == "b7-worker-block" || name == "b7-worker-late-exit" {
		for {
			syscall.Pause()
		}
	}
	result.Close()
}

func disposeWorker(t *testing.T, w *Worker) {
	t.Helper()
	t.Cleanup(func() {
		w.Kill()
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if err := w.Reap(ctx); err != nil {
			t.Errorf("worker %d remains unreaped: %v", w.PID(), err)
		}
		for !errors.Is(w.process.Signal(syscall.Signal(0)), os.ErrProcessDone) {
			if ctx.Err() != nil {
				t.Errorf("worker %d disposal did not join process wait", w.PID())
				break
			}
			time.Sleep(time.Millisecond)
		}
	})
}

func workerResult(t *testing.T, w *Worker) ([]protocol.InspectionResult, error) {
	t.Helper()
	disposeWorker(t, w)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := w.Reap(ctx); err != nil {
		t.Fatal(err)
	}
	var status syscall.WaitStatus
	if _, err := syscall.Wait4(w.PID(), &status, syscall.WNOHANG, nil); err != syscall.ECHILD {
		t.Fatalf("completion preceded actual reap: pid%d status%d err%v", w.PID(), status, err)
	}
	return w.Result()
}

func TestWorkerActualInspection(t *testing.T) {
	t.Setenv("B7_PARENT_SESSION_SECRET", "must-not-be-inherited")
	dir := t.TempDir()
	file := filepath.Join(dir, "file")
	write(t, file, []byte("worker body"))
	tree := filepath.Join(dir, "tree")
	mkdir(t, tree)
	write(t, filepath.Join(tree, "child"), []byte("child body"))
	alias := filepath.Join(dir, "alias")
	link(t, tree, alias)
	plans := []protocol.ResolvedInspection{
		{InspectionID: "file", Kind: "produces", ResolvedPath: file, ProducerID: "p", OutputID: "o"},
		{InspectionID: "tree", Kind: "produces", ResolvedPath: alias, ProducerID: "p", OutputID: "tree"},
		{InspectionID: "exists", Kind: "file_exists", ResolvedPath: dir},
	}
	w, err := StartWorker(plans)
	if err != nil {
		t.Fatal(err)
	}
	plans[0].InspectionID = "caller-mutated-after-launch"
	got, err := workerResult(t, w)
	want := sha256.Sum256([]byte("worker body"))
	if err != nil || len(got) != 3 || got[0].InspectionID != "file" || got[0].SHA256 != hex.EncodeToString(want[:]) ||
		got[1].ResolvedPath != tree || got[1].PathKind != "directory" || got[1].DigestAlgorithm != "directory-v2" || got[2].PathKind != "directory" {
		t.Fatalf("actual inspection: %#v %v", got, err)
	}
	got[0].InspectionID = "mutated-return"
	again, err := w.Result()
	if err != nil || again[0].InspectionID != "file" {
		t.Fatalf("caller changed retained result: %#v %v", again, err)
	}
	if err := w.Kill(); err != nil {
		t.Fatalf("kill after actual reap: %v", err)
	}
}

func TestWorkerMaximumAndTypedFailure(t *testing.T) {
	file := filepath.Join(t.TempDir(), "file")
	write(t, file, nil)
	plans := make([]protocol.ResolvedInspection, protocol.MaxInspections)
	for i := range plans {
		plans[i] = protocol.ResolvedInspection{InspectionID: "i" + strconv.Itoa(i), Kind: "file_exists", ResolvedPath: file}
	}
	w, err := StartWorker(plans)
	if err != nil {
		t.Fatal(err)
	}
	got, err := workerResult(t, w)
	if err != nil || len(got) != protocol.MaxInspections {
		t.Fatalf("maximum plan: %d %v", len(got), err)
	}
	for i, result := range got {
		if result.InspectionID != plans[i].InspectionID {
			t.Fatal("result order changed")
		}
	}
	plans = append(plans[:1], protocol.ResolvedInspection{InspectionID: "missing", Kind: "file_exists", ResolvedPath: file + "-absent"})
	w, err = StartWorker(plans)
	if err != nil {
		t.Fatal(err)
	}
	got, err = workerResult(t, w)
	if got != nil {
		t.Fatal("typed failure returned partial results")
	}
	wantFailure(t, err, "inspection-missing")
	w, err = StartWorker([]protocol.ResolvedInspection{})
	if err != nil {
		t.Fatal(err)
	}
	if got, err = workerResult(t, w); err != nil || got == nil || len(got) != 0 {
		t.Fatalf("empty plan: %#v %v", got, err)
	}
}

func TestWorkerSessionDescriptorIsolation(t *testing.T) {
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer readPipe.Close()
	defer writePipe.Close()
	socket, err := syscall.Socket(syscall.AF_UNIX, syscall.SOCK_STREAM|syscall.SOCK_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(socket)
	terminal, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer terminal.Close()
	for _, source := range []int{int(readPipe.Fd()), socket, int(terminal.Fd())} {
		fd, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(source), syscall.F_DUPFD_CLOEXEC, 600)
		if errno != 0 {
			t.Fatal(errno)
		}
		defer syscall.Close(int(fd))
	}
	file := filepath.Join(t.TempDir(), "file")
	write(t, file, nil)
	w, err := StartWorker([]protocol.ResolvedInspection{{InspectionID: "isolated", Kind: "file_exists", ResolvedPath: file}})
	if err != nil {
		t.Fatal(err)
	}
	if got, err := workerResult(t, w); err != nil || len(got) != 1 {
		t.Fatalf("session descriptors leaked into actual child: %#v %v", got, err)
	}
	fd, _, errno := syscall.Syscall(syscall.SYS_FCNTL, readPipe.Fd(), syscall.F_DUPFD, 605)
	if errno != 0 {
		t.Fatal(errno)
	}
	defer syscall.Close(int(fd))
	if w, err := StartWorker(nil); err == nil || w != nil {
		if w != nil {
			disposeWorker(t, w)
		}
		t.Fatalf("broken inheritance protection did not reject before fork: %#v %v", w, err)
	}
}

func TestWorkerInvalidPlanBeforeFork(t *testing.T) {
	for _, plan := range [][]protocol.ResolvedInspection{
		{{InspectionID: "i", Kind: "file_exists", ResolvedPath: "relative"}},
		{{InspectionID: "i", Kind: "produces", ResolvedPath: "/file"}},
		{{InspectionID: "i", Kind: "file_exists", ResolvedPath: "/a\x00b"}},
		make([]protocol.ResolvedInspection, protocol.MaxInspections+1),
	} {
		if w, err := StartWorker(plan); err == nil || w != nil {
			if w != nil {
				disposeWorker(t, w)
			}
			t.Fatalf("invalid plan started a worker: %#v %v", w, err)
		}
	}
	for _, args := range [][]string{nil, {"unknown"}, {WorkerModeArgument, "extra"}} {
		if err := RunWorkerMode(args); err == nil {
			t.Fatal("invalid worker arguments accepted")
		}
	}
}

// The committed proof runner executes renamed copies of this exact binary.
func TestWorkerFixture(t *testing.T) {
	name := filepath.Base(os.Args[0])
	if !strings.HasPrefix(name, "b7-worker-") {
		t.Skip("standalone worker fault fixture")
	}
	file := filepath.Join(t.TempDir(), "file")
	write(t, file, nil)
	w, err := StartWorker([]protocol.ResolvedInspection{{InspectionID: "fixture", Kind: "file_exists", ResolvedPath: file}})
	if err != nil {
		t.Fatal(err)
	}
	disposeWorker(t, w)
	if name == "b7-worker-block" || name == "b7-worker-late-exit" {
		outside := exec.Command("/bin/sleep", "30")
		if err := outside.Start(); err != nil {
			t.Fatal(err)
		}
		defer func() { outside.Process.Kill(); outside.Wait() }()
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
		err := w.Reap(ctx)
		cancel()
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("caller bound falsely proved reap: %v", err)
		}
		if got, err := w.Result(); got != nil || err == nil {
			t.Fatalf("reply accepted before actual reap: %#v %v", got, err)
		}
		if err := w.Kill(); err != nil {
			t.Fatal(err)
		}
		if got, err := workerResult(t, w); got != nil || err == nil {
			t.Fatalf("signalled worker accepted: %#v %v", got, err)
		}
		if err := outside.Process.Signal(syscall.Signal(0)); err != nil {
			t.Fatalf("termination affected unrelated child: %v", err)
		}
		return
	}
	if got, err := workerResult(t, w); got != nil || err == nil {
		t.Fatalf("invalid child result accepted by %s: %#v %v", name, got, err)
	}
}

func TestWorkerMalformedChildInput(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	inputs := [][]byte{nil, []byte("not-a-plan"), bytes.Repeat([]byte("a"), protocol.MaxFrameBytes+1)}
	for _, plan := range []protocol.PrivateCompleted{
		{OperationID: "different", PhysicalCWD: "/", Inspections: []protocol.ResolvedInspection{}},
		{OperationID: "inspection", Status: 7, PhysicalCWD: "/", Inspections: []protocol.ResolvedInspection{}},
		{OperationID: "inspection", PhysicalCWD: "/tmp", Inspections: []protocol.ResolvedInspection{}},
	} {
		input, err := protocol.EncodePrivate(plan, protocol.AwshToEnvoy)
		if err != nil {
			t.Fatal(err)
		}
		inputs = append(inputs, input)
	}
	for _, input := range inputs {
		readResult, writeResult, err := os.Pipe()
		if err != nil {
			t.Fatal(err)
		}
		cmd := exec.Command(executable, WorkerModeArgument)
		cmd.Env, cmd.Dir = []string{}, "/"
		cmd.Stdin, cmd.ExtraFiles = bytes.NewReader(input), []*os.File{writeResult}
		if err := cmd.Start(); err != nil {
			readResult.Close()
			writeResult.Close()
			t.Fatal(err)
		}
		writeResult.Close()
		data, readErr := io.ReadAll(io.LimitReader(readResult, protocol.MaxFrameBytes+1))
		readResult.Close()
		if err := cmd.Wait(); err == nil || readErr != nil || len(data) != 0 {
			t.Fatalf("invalid child request accepted: %s", fmt.Sprint(err, readErr, len(data)))
		}
	}
}

func TestWorkerInvalidHandoff(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	input, err := protocol.EncodePrivate(protocol.PrivateCompleted{OperationID: "inspection", PhysicalCWD: "/", Inspections: []protocol.ResolvedInspection{}}, protocol.AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"environment", "cwd", "input-not-pipe", "input-write-end", "result-not-pipe", "result-read-end", "result-missing", "same-pipe", "stdout-not-null", "stderr-read-only"} {
		t.Run(mode, func(t *testing.T) {
			reader, writer, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer reader.Close()
			defer writer.Close()
			readNull, err := os.Open("/dev/null")
			if err != nil {
				t.Fatal(err)
			}
			defer readNull.Close()
			regular, err := os.CreateTemp(t.TempDir(), "not-a-pipe")
			if err != nil {
				t.Fatal(err)
			}
			defer regular.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, executable, WorkerModeArgument)
			cmd.Env, cmd.Dir = []string{}, "/"
			cmd.Stdin, cmd.ExtraFiles = bytes.NewReader(input), []*os.File{writer}
			switch mode {
			case "environment":
				cmd.Env = []string{"WORKER_UNEXPECTED=value"}
			case "cwd":
				cmd.Dir = t.TempDir()
			case "input-not-pipe":
				cmd.Stdin = readNull
			case "input-write-end":
				cmd.Stdin = writer
			case "result-not-pipe":
				cmd.ExtraFiles = []*os.File{regular}
			case "result-read-end":
				cmd.ExtraFiles = []*os.File{reader}
			case "result-missing":
				cmd.ExtraFiles = nil
			case "same-pipe":
				cmd.Stdin = reader
			case "stdout-not-null":
				cmd.Stdout = regular
			case "stderr-read-only":
				cmd.Stderr = readNull
			}
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			writer.Close()
			data, readErr := io.ReadAll(io.LimitReader(reader, protocol.MaxFrameBytes+1))
			waitErr := cmd.Wait()
			var exited *exec.ExitError
			if ctx.Err() != nil || !errors.As(waitErr, &exited) || exited.ExitCode() != 2 || readErr != nil || len(data) != 0 {
				t.Fatalf("invalid %s handoff was not rejected by the real mode: exit%v context%v read%v bytes%d", mode, waitErr, ctx.Err(), readErr, len(data))
			}
		})
	}
}
