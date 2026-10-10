package launch

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/awsh/submission"
	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Session alone closes the inherited control FD. An os.File wrapper with a
// finalizer would create a second closer and could hit a reused descriptor.
type startTestFDReader int

func (fd startTestFDReader) Read(b []byte) (int, error) {
	for {
		n, err := syscall.Read(int(fd), b)
		if err == syscall.EINTR {
			continue
		}
		if n < 0 {
			n = 0
		}
		if n == 0 && err == nil {
			err = io.EOF
		}
		return n, err
	}
}

// Compiled only in the isolated candidate test binary. Admission is injected
// at the checked-source boundary: B2.5's unchanged actual checker is tested by
// its own candidate suite; this suite measures the owning start transaction.
func TestStartSupervisorProcess(t *testing.T) {
	if os.Getenv("OMEGAFLOW_START_CHILD") != "1" {
		return
	}
	scenario := os.Getenv("OMEGAFLOW_START_CASE")
	digest := os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST")
	b, e := os.ReadFile("/bin/bash")
	hash := sha256.Sum256(b)
	if e != nil || hex.EncodeToString(hash[:]) != digest {
		os.Exit(70)
	}
	var signals []uint64
	if json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS")), &signals) != nil {
		os.Exit(71)
	}
	// The evidence descriptor is isolated from all parse children.
	syscall.CloseOnExec(6)
	evidence := os.NewFile(6, "start-evidence")
	launchCtx, cancelLaunch := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelLaunch()
	s, e := start(launchCtx, Handoff{3, 4, 5, "/run/omegaflow/session"}, signals)
	if e != nil {
		fmt.Fprintln(evidence, e)
		os.Exit(72)
	}
	defer s.Close()
	defer func() {
		// Test-only controlled-session disposal; never production descendant census.
		entries, _ := os.ReadDir("/proc")
		for _, entry := range entries {
			pid, err := strconv.Atoi(entry.Name())
			if err != nil || pid == os.Getpid() {
				continue
			}
			_, _, sid, err := processIdentity(pid)
			if err == nil && sid == os.Getpid() {
				syscall.Kill(pid, syscall.SIGKILL)
			}
		}
	}()
	budget := 3 * time.Second
	if scenario != "normal" && !strings.HasPrefix(scenario, "split-") && scenario != "raw" && scenario != "reject-retry" {
		budget = 650 * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(context.Background(), budget)
	defer cancel()
	reader := bufio.NewReader(startTestFDReader(3))
	var restored syscall.Termios
	var sourceStatus int64 = -1
	var entryStopped bool
	read := func() protocol.PrivateMessage {
		m, err := protocol.ReadPrivate(reader, protocol.EnvoyToAwsh)
		if err != nil {
			e = err
			return nil
		}
		return m
	}
	check := func(c context.Context, source protocol.HelperSourceReply) error {
		if c != ctx {
			return fmt.Errorf("checker context replaced")
		}
		if source.Source == "reject-me" {
			return &submission.Rejection{Code: "source-syntax", Message: "isolated rejection"}
		}
		_, err := submission.Frame(source)
		return err
	}
	execute, ok := read().(*protocol.PrivateExecute)
	if !ok {
		e = fmt.Errorf("missing execute")
	} else if scenario == "unqualified" {
		e = s.BeginStart(ctx, *execute, "unknown", bashbuild.Target{}, nil)
	} else {
		if scenario == "split-invalid-derivation" {
			execute.StdoutFIFO = "/run/omegaflow/session/split/other.stdout"
		}
		e = s.beginStart(ctx, *execute, check)
	}
	if e == nil && scenario == "reject-retry" {
		execute, ok = read().(*protocol.PrivateExecute)
		if !ok {
			e = fmt.Errorf("missing retry")
		} else {
			e = s.beginStart(ctx, *execute, check)
		}
	}
	if e == nil {
		e = s.AcceptStartHelper()
	}
	if e == nil {
		e = s.AcceptStartHelper()
	}
	if e == nil {
		e = s.HandleStartControl(read())
	}
	if e == nil {
		if scenario == "restore-identity" {
			s.terminal.foreground = -1
		}
		e = s.HandleStartControl(read())
		if e == nil {
			restored, e = s.terminal.snapshot(ctx)
			if e == nil && restored != s.workloadTermios {
				e = fmt.Errorf("workload state not restored")
			}
		}
	}
	if e == nil {
		select {
		case signal := <-s.Signals:
			e = s.HandleStartSignal(signal)
		case <-ctx.Done():
			e = s.failStart(ctx.Err())
		}
	}
	if e == nil {
		if scenario == "second-signal" {
			select {
			case signal := <-s.Signals:
				e = s.HandleStartSignal(signal)
			case <-ctx.Done():
				e = fmt.Errorf("second signal missing")
			}
		} else {
			if scenario == "split-stdout-failure" || scenario == "split-stderr-failure" {
				until := time.Now().Add(time.Second)
				for time.Now().Before(until) && !entryStopped {
					entries, _ := os.ReadDir("/proc")
					for _, entry := range entries {
						pid, err := strconv.Atoi(entry.Name())
						if err != nil {
							continue
						}
						parent, _, _, err := processIdentity(pid)
						if err != nil || parent != s.ShellPID {
							continue
						}
						b, _ := os.ReadFile("/proc/" + entry.Name() + "/stat")
						text := string(b)
						end := strings.LastIndex(text, ")")
						if end >= 0 && strings.HasPrefix(text[end+1:], " T ") {
							entryStopped = true
						}
					}
					time.Sleep(time.Millisecond)
				}
				if !entryStopped {
					e = fmt.Errorf("redirection failure did not fail-stop")
				}
			} else {
				c, err := s.listener.AcceptUnix()
				if err == nil {
					var m protocol.HelperMessage
					m, err = helper.ReadRequest(c, protocol.HelperStartup)
					if state, ok := m.(*protocol.HelperPromptState); ok {
						sourceStatus = state.Status
					} else if err == nil {
						err = fmt.Errorf("missing post-source state")
					}
					c.Close()
				}
				if err != nil {
					e = err
				}
			}
			// No completion implementation is claimed: the parent stops this isolated
			// proof after observing authored output or the entry-failure fail-stop.
			read()
		}
	}
	json.NewEncoder(evidence).Encode(map[string]any{"error": fmt.Sprint(e), "fatal": s.startFailed, "source_status": sourceStatus, "entry_stopped": entryStopped, "workload": s.workloadTermios, "restored": restored, "phase": func() startPhase {
		if s.active == nil {
			return 0
		}
		return s.active.phase
	}(), "source": func() string {
		if s.active == nil {
			return ""
		}
		return s.active.request.Source
	}()})
	// After all live intake/operation assertions, preserve teardown/runtime
	// diagnostics in the isolated evidence pipe instead of losing stderr.
	syscall.Dup2(6, 2)
}

func TestStartRejectsEventsOutsideOperation(t *testing.T) {
	for _, event := range []string{"signal", "control", "helper", "execute"} {
		t.Run(event, func(t *testing.T) {
			s := &Session{}
			var err error
			switch event {
			case "signal":
				err = s.HandleStartSignal(syscall.SIGUSR1)
			case "control":
				err = s.HandleStartControl(&protocol.PrivateStartedAck{OperationID: "op"})
			case "helper":
				err = s.AcceptStartHelper()
			case "execute":
				err = s.BeginStart(context.Background(), protocol.PrivateExecute{}, "unknown", bashbuild.Target{}, nil)
			}
			if err == nil || !s.startFailed {
				t.Fatal("out-of-phase event accepted")
			}
		})
	}
}

func TestRealCandidateStart(t *testing.T) {
	if os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST") == "" {
		t.Skip("explicit pinned candidate required")
	}
	cases := []string{"normal", "raw", "reject-retry", "unqualified", "missing-source", "crossed-source", "duplicate-source", "early-signal", "wrong-release", "early-ack", "duplicate-release", "wrong-ack", "restore-identity", "missing-ack", "missing-release", "second-signal", "blocked-submit", "blocked-prepared", "blocked-started", "blocked-released", "split-status-one", "split-stdout-failure", "split-stderr-failure", "split-invalid-mode", "split-symlink", "split-invalid-owner", "split-invalid-type", "split-invalid-directory-mode", "split-invalid-derivation", "split-dir-symlink"}
	if single := os.Getenv("OMEGAFLOW_START_ONLY"); single != "" {
		cases = []string{single}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) { runStartCase(t, scenario) })
	}
}

func runStartCase(t *testing.T, scenario string) {
	master, slave := openPTY(t)
	workload, err := termios(int(slave.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	if scenario == "raw" {
		workload.Lflag &^= syscall.ICANON | syscall.ECHO | syscall.ISIG
		workload.Iflag &^= syscall.ICRNL | syscall.IXON
		workload.Oflag &^= syscall.OPOST
		workload.Cc[syscall.VINTR] = 7
		if err := ioctl(int(slave.Fd()), syscall.TCSETS, unsafe.Pointer(&workload)); err != nil {
			t.Fatal(err)
		}
	}
	controlR, controlW, _ := os.Pipe()
	resultR, resultW, _ := os.Pipe()
	evidenceR, evidenceW, _ := os.Pipe()
	for _, f := range []*os.File{controlR, controlW, resultR, resultW, evidenceR, evidenceW} {
		defer f.Close()
	}
	bin, _ := os.Executable()
	cmd := exec.Command(bin, "-test.run=^TestStartSupervisorProcess$")
	if cover := os.Getenv("GOCOVERDIR"); cover != "" {
		cmd.Args = append(cmd.Args, "-test.gocoverdir="+cover)
	}
	cmd.Env = []string{"PWD=/", "PATH=/hostile", "HOME=/hostile", "TERM=xterm-256color", "LC_ALL=C.UTF-8", "LANG=C.UTF-8", "INPUTRC=/omegaflow-runtime/etc/inputrc", "TERMINFO=/omegaflow-runtime/share/terminfo", "TERMINFO_DIRS=/omegaflow-runtime/share/terminfo", "LOCPATH=/omegaflow-runtime/lib/locale", "GOCOVERDIR=" + os.Getenv("GOCOVERDIR"), "OMEGAFLOW_START_CHILD=1", "OMEGAFLOW_START_CASE=" + scenario, "OMEGAFLOW_CANDIDATE_DIGEST=" + os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST"), "OMEGAFLOW_CANDIDATE_SIGNALS=" + os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS")}
	if os.Getenv("OMEGAFLOW_NONROOT_SUPERVISOR") == "1" {
		cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000}}
	}
	cmd.ExtraFiles = []*os.File{controlR, resultW, slave, evidenceW}
	in, _ := os.Open("/dev/null")
	out, _ := os.OpenFile("/dev/null", os.O_WRONLY, 0)
	defer in.Close()
	defer out.Close()
	cmd.Stdin = in
	cmd.Stdout = out
	cmd.Stderr = out
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		entries, _ := os.ReadDir("/proc")
		for _, entry := range entries {
			pid, e := strconv.Atoi(entry.Name())
			if e != nil {
				continue
			}
			_, _, sid, e := processIdentity(pid)
			if e == nil && sid == cmd.Process.Pid && pid != cmd.Process.Pid {
				syscall.Kill(pid, syscall.SIGKILL)
			}
		}
		cmd.Process.Kill()
		cmd.Wait()
		if t.Failed() {
			os.Remove(helper.SocketPath)
			os.Remove("/run/omegaflow/session/bash")
		}
	}()
	controlR.Close()
	blocked := strings.HasPrefix(scenario, "blocked-")
	if !blocked {
		resultW.Close()
	}
	evidenceW.Close()
	slave.Close()
	var mu sync.Mutex
	var raw bytes.Buffer
	rawDone := make(chan struct{})
	go func() {
		defer close(rawDone)
		b := make([]byte, 8192)
		for {
			n, e := master.Read(b)
			mu.Lock()
			raw.Write(b[:n])
			mu.Unlock()
			if e != nil {
				return
			}
		}
	}()
	snapshot := func() string { mu.Lock(); defer mu.Unlock(); return raw.String() }
	reader := bufio.NewReader(resultR)
	resultR.SetReadDeadline(time.Now().Add(5 * time.Second))
	recv := func(want string) {
		t.Helper()
		m, e := protocol.ReadPrivate(reader, protocol.AwshToEnvoy)
		if e != nil {
			evidenceR.SetReadDeadline(time.Now().Add(time.Second))
			detail, _ := io.ReadAll(evidenceR)
			t.Fatalf("want %s: %v; supervisor evidence: %s", want, e, detail)
		}
		encoded, _ := protocol.EncodePrivate(m, protocol.AwshToEnvoy)
		if !bytes.Contains(encoded, []byte("\x00"+want+"\x00")) {
			t.Fatalf("want %s: %#v", want, m)
		}
	}
	send := func(m protocol.PrivateMessage) {
		t.Helper()
		b, e := protocol.EncodePrivate(m, protocol.EnvoyToAwsh)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = controlW.Write(b); e != nil {
			t.Fatal(e)
		}
	}
	recv("ready")
	var filler []byte
	fillResult := func() {
		t.Helper()
		fd := int(resultW.Fd())
		if err := syscall.SetNonblock(fd, true); err != nil {
			t.Fatal(err)
		}
		for {
			b := make([]byte, 4096)
			n, err := syscall.Write(fd, b)
			if n > 0 {
				filler = append(filler, b[:n]...)
			}
			if err == syscall.EAGAIN {
				break
			}
			if err != nil {
				t.Fatal(err)
			}
		}
		resultW.Close()
	}
	if scenario == "blocked-submit" {
		fillResult()
	}
	request := protocol.PrivateExecute{OperationID: "op1", ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "shared", Inspections: []protocol.Inspection{}, Source: "printf 'AUTHORED_START\\n'"}
	if strings.HasPrefix(scenario, "split-") {
		targetUID := uint32(os.Geteuid())
		if raw := os.Getenv("OMEGAFLOW_START_UID"); raw != "" {
			parsed, e := strconv.ParseUint(raw, 10, 32)
			if e != nil {
				t.Fatal(e)
			}
			targetUID = uint32(parsed)
		}
		request.ExecutionShape = "split"
		request.Timing = "presentation"
		request.Observation = "exclusive"
		request.StdoutFIFO = "/run/omegaflow/session/split/op1.stdout"
		request.StderrFIFO = "/run/omegaflow/session/split/op1.stderr"
		if e := os.MkdirAll("/run/omegaflow/session/split", 0700); e != nil {
			t.Fatal(e)
		}
		for _, dir := range []string{"/run/omegaflow", "/run/omegaflow/session", "/run/omegaflow/session/split"} {
			if e := os.Chmod(dir, 0700); e != nil {
				t.Fatal(e)
			}
			if e := os.Chown(dir, int(targetUID), int(targetUID)); e != nil {
				t.Fatal(e)
			}
		}
		for _, p := range []string{request.StdoutFIFO, request.StderrFIFO} {
			os.Remove(p)
			if e := syscall.Mkfifo(p, 0600); e != nil {
				t.Fatal(e)
			}
			if e := os.Chown(p, int(targetUID), int(targetUID)); e != nil {
				t.Fatal(e)
			}
			defer os.Remove(p)
		}
		if scenario == "split-invalid-mode" {
			if e := os.Chmod(request.StdoutFIFO, 0666); e != nil {
				t.Fatal(e)
			}
		}
		if scenario == "split-symlink" {
			if e := os.Remove(request.StdoutFIFO); e != nil {
				t.Fatal(e)
			}
			if e := os.Symlink(request.StderrFIFO, request.StdoutFIFO); e != nil {
				t.Fatal(e)
			}
		}
		if scenario == "split-invalid-owner" {
			if targetUID == 0 {
				t.Skip("owner negative requires a distinct root owner")
			}
			if e := os.Chown(request.StdoutFIFO, 0, 0); e != nil {
				t.Fatal(e)
			}
		}
		if scenario == "split-invalid-type" {
			if e := os.Remove(request.StdoutFIFO); e != nil {
				t.Fatal(e)
			}
			if e := os.WriteFile(request.StdoutFIFO, nil, 0600); e != nil {
				t.Fatal(e)
			}
			if e := os.Chown(request.StdoutFIFO, int(targetUID), int(targetUID)); e != nil {
				t.Fatal(e)
			}
		}
		if scenario == "split-invalid-directory-mode" {
			if e := os.Chmod("/run/omegaflow/session/split", 0755); e != nil {
				t.Fatal(e)
			}
		}
		if scenario == "split-dir-symlink" {
			target := "/run/omegaflow/session/split-target"
			if e := os.Mkdir(target, 0700); e != nil {
				t.Fatal(e)
			}
			if e := os.Chown(target, int(targetUID), int(targetUID)); e != nil {
				t.Fatal(e)
			}
			for _, p := range []string{request.StdoutFIFO, request.StderrFIFO} {
				if e := os.Rename(p, strings.Replace(p, "/split/", "/split-target/", 1)); e != nil {
					t.Fatal(e)
				}
			}
			if e := os.Remove("/run/omegaflow/session/split"); e != nil {
				t.Fatal(e)
			}
			if e := os.Symlink(target, "/run/omegaflow/session/split"); e != nil {
				t.Fatal(e)
			}
			defer os.Remove("/run/omegaflow/session/split")
			defer os.RemoveAll(target)
		}
		request.Source = "printf 'AUTHORED_START\\n'; printf 'AUTHORED_ERR\\n' >&2; false"
	}
	splitReaders := map[string]*os.File{}
	if strings.HasPrefix(scenario, "split-") && scenario != "split-invalid-mode" && scenario != "split-symlink" {
		for _, path := range []string{request.StdoutFIFO, request.StderrFIFO} {
			f, e := os.OpenFile(path, os.O_RDWR|syscall.O_NONBLOCK, 0)
			if e != nil {
				t.Fatal(e)
			}
			splitReaders[path] = f
			defer f.Close()
		}
	}
	if scenario == "reject-retry" {
		rejected := request
		rejected.Source = "reject-me"
		send(rejected)
		recv("rejected")
	}
	send(request)
	fatal := scenario != "normal" && scenario != "raw" && scenario != "reject-retry" && !strings.HasPrefix(scenario, "split-") || strings.HasPrefix(scenario, "split-invalid-") || scenario == "split-symlink" || scenario == "split-dir-symlink"
	finish := func() {
		t.Helper()
		controlW.Close()
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		select {
		case e := <-done:
			if e != nil {
				b, _ := io.ReadAll(evidenceR)
				t.Fatalf("supervisor: %v %s", e, b)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("original start epoch did not bound failure")
		}
		remaining, err := io.ReadAll(reader)
		if err != nil {
			t.Fatal(err)
		}
		if blocked {
			if !bytes.Equal(remaining, filler) {
				t.Fatalf("blocked private write leaked bytes: %x", remaining)
			}
			remaining = nil
		}
		if fatal && len(remaining) != 0 {
			t.Fatalf("private result after fatal transition: %x", remaining)
		}
		var evidence struct {
			Error              string
			Fatal              bool
			Workload, Restored syscall.Termios
			Phase              startPhase
			Source             string
			SourceStatus       int64 `json:"source_status"`
			EntryStopped       bool  `json:"entry_stopped"`
		}
		if err := json.NewDecoder(evidenceR).Decode(&evidence); err != nil {
			t.Fatal(err)
		}
		if fatal && (!evidence.Fatal || evidence.Error == "<nil>") {
			t.Fatalf("failure accepted: %+v", evidence)
		}
		if !fatal && (evidence.Fatal || evidence.Error != "<nil>" || evidence.Restored != evidence.Workload || evidence.Source != request.Source) {
			t.Fatalf("start proof: %+v", evidence)
		}
		select {
		case <-rawDone:
		case <-time.After(time.Second):
			t.Fatal("test controlled-session teardown failed")
		}
		if fatal && scenario != "second-signal" && scenario != "blocked-released" && strings.Contains(snapshot(), "AUTHORED_START") {
			t.Fatal("authored source ran on fatal start")
		}
		if scenario == "split-status-one" && evidence.SourceStatus != 1 {
			t.Fatalf("ordinary status one lost: %+v", evidence)
		}
		if (scenario == "split-stdout-failure" || scenario == "split-stderr-failure") && !evidence.EntryStopped {
			t.Fatal("entry-failure sentinel did not fail-stop")
		}
		if strings.Contains(snapshot(), "__OMEGAFLOW_AWSH_") {
			t.Fatal("frame redisplay")
		}
		t.Logf("case=%s phase=%d fatal=%v raw=%q", scenario, evidence.Phase, evidence.Fatal, snapshot())
	}
	if scenario == "unqualified" || strings.HasPrefix(scenario, "split-invalid-") || scenario == "split-symlink" || scenario == "split-dir-symlink" {
		finish()
		return
	}
	if scenario == "blocked-submit" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	recv("submit")
	if scenario == "blocked-prepared" {
		fillResult()
	}
	if scenario == "missing-source" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	peer := func(phase protocol.HelperPhase, message protocol.HelperMessage) {
		c, e := net.DialUnix("unix", nil, &net.UnixAddr{Name: helper.SocketPath, Net: "unix"})
		if e != nil {
			t.Fatal(e)
		}
		c.SetDeadline(time.Now().Add(time.Second))
		b, e := protocol.EncodeHelper(message, protocol.HelperToAwsh, phase)
		if e != nil {
			t.Fatal(e)
		}
		c.Write(b)
		c.CloseWrite()
		io.ReadAll(c)
		c.Close()
	}
	if scenario == "crossed-source" {
		peer(protocol.HelperStartPrepared, protocol.HelperPreparedRequest{})
		finish()
		return
	}
	if scenario == "early-signal" {
		cmd.Process.Signal(syscall.SIGUSR1)
		time.Sleep(20 * time.Millisecond)
	}
	if scenario == "duplicate-source" {
		peer(protocol.HelperSource, protocol.HelperSourceRequest{})
		peer(protocol.HelperSource, protocol.HelperSourceRequest{})
		finish()
		return
	}
	master.Write([]byte{0x18, 0x02})
	if scenario == "early-signal" {
		finish()
		return
	}
	if scenario == "blocked-prepared" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	recv("start_prepared")
	noAuthoredBytes := func() {
		t.Helper()
		text := snapshot()
		for _, control := range []string{"\x1b[?2004h", "\x1b[?2004l", "\x1b[K", "\r", "\n"} {
			text = strings.ReplaceAll(text, control, "")
		}
		if text != "" {
			t.Fatalf("source/frame/PS0 bytes before release: %q", snapshot())
		}
	}
	time.Sleep(30 * time.Millisecond)
	noAuthoredBytes()
	if scenario == "missing-release" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	if scenario == "wrong-release" {
		send(&protocol.PrivateStartRelease{OperationID: "wrong"})
		finish()
		return
	}
	if scenario == "early-ack" {
		send(&protocol.PrivateStartedAck{OperationID: "op1"})
		finish()
		return
	}
	if scenario == "blocked-started" {
		fillResult()
	}
	send(&protocol.PrivateStartRelease{OperationID: "op1"})
	if scenario == "blocked-started" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	recv("started")
	if scenario == "blocked-released" {
		fillResult()
	}
	time.Sleep(30 * time.Millisecond)
	noAuthoredBytes()
	if scenario == "duplicate-release" {
		send(&protocol.PrivateStartRelease{OperationID: "op1"})
		finish()
		return
	}
	if scenario == "wrong-ack" {
		send(&protocol.PrivateStartedAck{OperationID: "wrong"})
		finish()
		return
	}
	if scenario == "missing-ack" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	if scenario == "split-stdout-failure" {
		os.Remove(request.StdoutFIFO)
		os.Mkdir(request.StdoutFIFO, 0700)
	}
	if scenario == "split-stderr-failure" {
		os.Remove(request.StderrFIFO)
		os.Mkdir(request.StderrFIFO, 0700)
	}
	send(&protocol.PrivateStartedAck{OperationID: "op1"})
	if scenario == "restore-identity" {
		finish()
		return
	}
	if scenario == "missing-signal" || scenario == "release-queue-failure" || scenario == "blocked-released" {
		time.Sleep(750 * time.Millisecond)
		finish()
		return
	}
	recv("start_released")
	if scenario == "second-signal" {
		cmd.Process.Signal(syscall.SIGUSR1)
		finish()
		return
	}
	if strings.HasPrefix(scenario, "split-") {
		// Readers are test fixtures, not an Envoy split-setup implementation.
		time.Sleep(60 * time.Millisecond)
		if scenario == "split-status-one" {
			for path, f := range splitReaders {
				if err := f.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
					t.Fatal(err)
				}
				b := make([]byte, 128)
				n, err := f.Read(b)
				if err != nil {
					t.Fatal(err)
				}
				want := "AUTHORED_START\n"
				if path == request.StderrFIFO {
					want = "AUTHORED_ERR\n"
				}
				if string(b[:n]) != want {
					t.Fatalf("split output: %q != %q", b[:n], want)
				}
			}
		}
	} else {
		until := time.Now().Add(time.Second)
		for !strings.Contains(snapshot(), "AUTHORED_START") && time.Now().Before(until) {
			time.Sleep(time.Millisecond)
		}
		if !strings.Contains(snapshot(), "AUTHORED_START") {
			t.Fatal("source did not run after release")
		}
	}
	send(&protocol.PrivateShutdown{})
	finish()
}
