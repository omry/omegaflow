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
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/awsh/submission"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestCompletionSupervisorProcess(t *testing.T) {
	if os.Getenv("OMEGAFLOW_COMPLETION_CHILD") != "1" {
		return
	}
	evidence := os.NewFile(6, "completion-evidence")
	syscall.CloseOnExec(6)
	b, err := os.ReadFile("/bin/bash")
	hash := sha256.Sum256(b)
	if err != nil || hex.EncodeToString(hash[:]) != os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST") {
		os.Exit(70)
	}
	var signals []uint64
	if json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS")), &signals) != nil {
		os.Exit(71)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	s, err := start(ctx, Handoff{3, 4, 5, "/run/omegaflow/session"}, signals)
	if err != nil {
		fmt.Fprintln(evidence, err)
		os.Exit(72)
	}
	defer s.Close()
	defer func() {
		entries, _ := os.ReadDir("/proc")
		for _, entry := range entries {
			pid, e := strconv.Atoi(entry.Name())
			if e != nil || pid == os.Getpid() {
				continue
			}
			_, _, sid, e := processIdentity(pid)
			if e == nil && sid == os.Getpid() {
				syscall.Kill(pid, syscall.SIGKILL)
			}
		}
	}()
	reader := bufio.NewReader(startTestFDReader(3))
	read := func() protocol.PrivateMessage {
		m, e := protocol.ReadPrivate(reader, protocol.EnvoyToAwsh)
		if e != nil {
			err = e
		}
		return m
	}
	scenario := os.Getenv("OMEGAFLOW_COMPLETION_CASE")
	leaseFDs := func() (int, int) {
		entries, _ := os.ReadDir("/proc/self/fd")
		tty, pidfd := 0, 0
		for _, entry := range entries {
			path, _ := os.Readlink("/proc/self/fd/" + entry.Name())
			if path == "/dev/tty" {
				tty++
			}
			if path == "anon_inode:[pidfd]" {
				pidfd++
			}
		}
		return tty, pidfd
	}
	beforeTTY, beforePIDFD := leaseFDs()
	var snapshots []map[string]any
	peerCount := func() int {
		s.mu.Lock()
		defer s.mu.Unlock()
		return len(s.peers)
	}
	startupPeers := peerCount()
	for n := 0; n < 8 && err == nil; n++ {
		startCtx, cancelStart := context.WithTimeout(context.Background(), 2*time.Second)
		request, ok := read().(*protocol.PrivateExecute)
		if !ok {
			err = fmt.Errorf("missing execute")
			cancelStart()
			break
		}
		err = s.beginStart(startCtx, *request, func(_ context.Context, source protocol.HelperSourceReply) error {
			_, e := submission.Frame(source)
			return e
		})
		if err == nil {
			err = s.AcceptStartHelper()
		}
		if err == nil && peerCount() != startupPeers {
			err = fmt.Errorf("source peer retained: got %d want %d", peerCount(), startupPeers)
		}
		if err == nil {
			err = s.AcceptStartHelper()
		}
		if err == nil && peerCount() != startupPeers+1 {
			err = fmt.Errorf("prepared peer missing: got %d want %d", peerCount(), startupPeers+1)
		}
		if err == nil {
			err = s.HandleStartControl(read())
		}
		if err == nil && peerCount() != startupPeers+1 {
			err = fmt.Errorf("prepared peer released too early: got %d want %d", peerCount(), startupPeers+1)
		}
		if err == nil {
			err = s.HandleStartControl(read())
		}
		if err == nil && peerCount() != startupPeers {
			err = fmt.Errorf("prepared peer retained after ack: got %d want %d", peerCount(), startupPeers)
		}
		if err == nil {
			select {
			case signal := <-s.Signals:
				err = s.HandleStartSignal(signal)
			case <-startCtx.Done():
				err = startCtx.Err()
			}
		}
		cancelStart() // completion must not consume this now-expired start epoch
		if err != nil {
			break
		}
		cleanupCtx, cancelCleanup := context.WithTimeout(context.Background(), 1500*time.Millisecond)
		if scenario == "expired-cleanup" {
			cancelCleanup()
		}
		err = s.BeginCompletion(cleanupCtx)
		if err == nil {
			if peerCount() != startupPeers+1 {
				err = fmt.Errorf("prompt_state peer missing: got %d want %d", peerCount(), startupPeers+1)
			}
			r := s.completion
			// Measure direct child, exact manifested peer and ignored INT while held.
			parent, _, _, e := processIdentity(r.pid)
			if e != nil || parent != s.ShellPID {
				err = fmt.Errorf("helper not direct child")
			}
			if err == nil {
				syscall.Kill(r.pid, syscall.SIGINT)
				time.Sleep(5 * time.Millisecond)
				err = completionHelperState(r.pid)
			}
			if scenario == "identity-damage" {
				s.terminal.session = -1
			}
			if scenario == "close-held" {
				s.Close()
				if r.pidfd != -1 || r.stop != nil {
					fmt.Fprintln(evidence, "Close leaked completion lease")
					os.Exit(73)
				}
				err = s.failStart(fmt.Errorf("session closed during completion"))
			}
			if scenario == "early-finish" {
				err = s.FinishCompletion()
			}
			if scenario == "release-signal" {
				s.Signals <- syscall.SIGUSR1
			}
			if err == nil {
				if scenario == "missing-control" {
					<-cleanupCtx.Done()
					err = s.failStart(cleanupCtx.Err())
				} else {
					err = s.HandleCompletionControl(read())
					if err == nil && peerCount() != startupPeers {
						err = fmt.Errorf("prompt_state peer retained after ack: got %d want %d", peerCount(), startupPeers)
					}
				}
				if err == nil && scenario == "duplicate-control" {
					err = s.HandleCompletionControl(&protocol.PrivateInputClosed{OperationID: request.OperationID})
				}
				if err == nil && scenario == "final-helper-failure" {
					s.listener.Close()
				}
			}
		}
		if err == nil && strings.HasPrefix(scenario, "terminal-") {
			runtime.LockOSThread()
			defer runtime.UnlockOSThread()
			request := uintptr(syscall.TCSETS)
			if scenario == "terminal-read-failure" {
				request = syscall.TCGETS
			}
			if scenario == "terminal-drain-failure" {
				request = 0x5409
			}
			err = denyTerminalRequest(request)
		}
		if err == nil {
			err = s.FinishCompletion()
			if err == nil && peerCount() != startupPeers {
				err = fmt.Errorf("prompt_ready peer retained: got %d want %d", peerCount(), startupPeers)
			}
		}
		if err == nil {
			tty, pidfd := leaseFDs()
			if tty != beforeTTY || pidfd != beforePIDFD {
				err = fmt.Errorf("completion leaked terminal or pidfd lease")
			}
		}
		if err == nil && (s.active != nil || s.completion != nil) {
			err = fmt.Errorf("completion did not release active record")
		}
		snapshots = append(snapshots, map[string]any{"state": s.State, "workload": s.workloadTermios, "active": s.ActiveTermios})
		cancelCleanup()
		if scenario != "persistent" && scenario != "status-one" && scenario != "editing" && scenario != "ordinary-disabled" && scenario != "raw" && scenario != "jobs" && scenario != "allowed-trap" {
			break
		}
	}
	json.NewEncoder(evidence).Encode(map[string]any{"error": fmt.Sprint(err), "fatal": s.startFailed, "snapshots": snapshots})
	syscall.Dup2(6, 2)
}

func TestCompletionEventsOutsideOperation(t *testing.T) {
	for _, event := range []string{"begin", "control", "finish"} {
		t.Run(event, func(t *testing.T) {
			s := &Session{}
			var err error
			switch event {
			case "begin":
				err = s.BeginCompletion(context.Background())
			case "control":
				err = s.HandleCompletionControl(&protocol.PrivateInputClosed{OperationID: "x"})
			case "finish":
				err = s.FinishCompletion()
			}
			if err == nil || !s.startFailed {
				t.Fatal("completion accepted outside operation")
			}
		})
	}
}

func TestRealCandidateCompletion(t *testing.T) {
	if os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST") == "" {
		t.Skip("explicit pinned candidate required")
	}
	cases := []string{"persistent", "status-one", "editing", "ordinary-disabled", "raw", "jobs", "allowed-trap", "wrong-peer", "invalid-environment", "terminal-write-failure", "terminal-read-failure", "terminal-drain-failure", "trap-int", "trap-chld", "disable-kill", "disable-trap", "shadow-builtin", "shadow-command", "binding", "prompt", "wrong-id", "wrong-control", "missing-control", "duplicate-control", "expired-cleanup", "identity-damage", "final-helper-failure", "close-held", "early-finish", "release-signal"}
	if single := os.Getenv("OMEGAFLOW_COMPLETION_ONLY"); single != "" {
		cases = []string{single}
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) { runCompletionCase(t, scenario) })
	}
}

func runCompletionCase(t *testing.T, scenario string) {
	t.Helper()
	master, slave := openPTY(t)
	defer master.Close()
	controlR, controlW, _ := os.Pipe()
	resultR, resultW, _ := os.Pipe()
	evidenceR, evidenceW, _ := os.Pipe()
	for _, f := range []*os.File{controlR, controlW, resultR, resultW, evidenceR, evidenceW} {
		defer f.Close()
	}
	bin, _ := os.Executable()
	cmd := exec.Command(bin, "-test.run=^TestCompletionSupervisorProcess$")
	if cover := os.Getenv("GOCOVERDIR"); cover != "" {
		cmd.Args = append(cmd.Args, "-test.gocoverdir="+cover)
	}
	cmd.Env = []string{"PWD=/", "PATH=/hostile", "HOME=/hostile", "TERM=xterm-256color", "LC_ALL=C.UTF-8", "LANG=C.UTF-8", "INPUTRC=/omegaflow-runtime/etc/inputrc", "TERMINFO=/omegaflow-runtime/share/terminfo", "LOCPATH=/omegaflow-runtime/lib/locale", "GOCOVERDIR=" + os.Getenv("GOCOVERDIR"), "OMEGAFLOW_COMPLETION_CHILD=1", "OMEGAFLOW_COMPLETION_CASE=" + scenario, "OMEGAFLOW_CANDIDATE_DIGEST=" + os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST"), "OMEGAFLOW_CANDIDATE_SIGNALS=" + os.Getenv("OMEGAFLOW_CANDIDATE_SIGNALS")}
	cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000}}
	cmd.ExtraFiles = []*os.File{controlR, resultW, slave, evidenceW}
	in, _ := os.Open("/dev/null")
	out, _ := os.OpenFile("/dev/null", os.O_WRONLY, 0)
	defer in.Close()
	defer out.Close()
	cmd.Stdin = in
	cmd.Stdout = out
	cmd.Stderr = out
	if err := cmd.Start(); err != nil {
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
		os.Remove(helper.SocketPath)
		os.Remove("/run/omegaflow/session/bash")
	}()
	controlR.Close()
	resultW.Close()
	evidenceW.Close()
	slave.Close()
	var raw bytes.Buffer
	var mu sync.Mutex
	go func() {
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
	resultR.SetReadDeadline(time.Now().Add(6 * time.Second))
	reader := bufio.NewReader(resultR)
	receive := func() protocol.PrivateMessage {
		m, e := protocol.ReadPrivate(reader, protocol.AwshToEnvoy)
		if e != nil {
			evidenceR.SetReadDeadline(time.Now().Add(time.Second))
			detail, _ := io.ReadAll(evidenceR)
			t.Fatalf("private result: %v; evidence %s; pty %q", e, detail, snapshot())
		}
		return m
	}
	send := func(m protocol.PrivateMessage) {
		b, e := protocol.EncodePrivate(m, protocol.EnvoyToAwsh)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = controlW.Write(b); e != nil {
			t.Fatal(e)
		}
	}
	if _, ok := receive().(*protocol.PrivateReady); !ok {
		t.Fatal("missing ready")
	}
	source := "secret=live; f() { builtin printf '%s' \"$secret\"; }; alias persisted='true'; set -- one two; set -o pipefail; shopt -s nullglob nocasematch; export OUT='link/../artifact'; cd /tmp; builtin printf 'FIRST_WORKLOAD\\n'"
	fatal := false
	switch scenario {
	case "status-one":
		source = "false"
	case "editing":
		source = "set -H -o vi; false"
	case "ordinary-disabled":
		source = "enable -n set shopt bind declare local printf"
	case "helper-child":
		source = "export OMEGAFLOW_PROOF_CHILD=1"
		fatal = true
	case "wrong-peer":
		source = "/bin/sleep .1"
		fatal = true
	case "invalid-environment":
		source = "export INVALID_VALUE=$'\\xff'"
		fatal = true
	case "jobs":
		source = "/bin/sleep 100 & builtin printf 'BACKGROUND_WORKLOAD\\n'"
	case "allowed-trap":
		source = "trap 'cd /; export OUT=final/../artifact' USR2; export OUT=initial/../artifact; cd /tmp; builtin printf 'ALLOWED_TRAP_READY\\n'"
	case "raw":
		source = "/bin/stty -icanon -echo -isig -icrnl -ixon -opost intr '^G'"
	case "trap-int":
		source = "builtin trap '' INT"
		fatal = true
	case "trap-chld":
		source = "command trap ':' CHLD"
		fatal = true
	case "disable-kill":
		source = "builtin enable -n kill"
		fatal = true
	case "disable-trap":
		source = "command enable -n trap"
		fatal = true
	case "shadow-builtin":
		source = "builtin() { return 0; }"
		fatal = true
	case "shadow-command":
		source = "command() { return 0; }"
		fatal = true
	case "binding":
		source = "bind -m emacs-standard -r '\\C-x\\C-b'"
		fatal = true
	case "prompt":
		source = "PS1=damaged"
		fatal = true
	case "wrong-id", "wrong-control", "missing-control", "duplicate-control", "expired-cleanup", "identity-damage", "final-helper-failure", "terminal-write-failure", "terminal-read-failure", "terminal-drain-failure", "wrong-first-phase", "helper-int-damage", "duplicate-final-state", "final-status-damage", "close-held", "early-finish", "release-signal":
		fatal = true
	}
	var freshStates []syscall.Termios
	for n := 0; n < 8; n++ {
		id := fmt.Sprintf("op%d", n+1)
		request := protocol.PrivateExecute{OperationID: id, ExecutionShape: "pty", Timing: "realtime", Publication: "real", Observation: "exclusive", Inspections: []protocol.Inspection{{InspectionID: "i", Kind: "file_exists", Path: "$OUT"}}, Source: source}
		request.Inspections = append(request.Inspections, protocol.Inspection{InspectionID: "home", Kind: "file_exists", Path: "~/artifact"}, protocol.Inspection{InspectionID: "user", Kind: "file_exists", Path: "~root/../artifact"}, protocol.Inspection{InspectionID: "literal", Kind: "file_exists", Path: "${UNDEFINED}/../literal"})
		if n == 1 {
			request.Source = "builtin printf 'NEXT_STATUS=%s\\n' \"$?\"; f; builtin alias persisted; [[ -o pipefail ]] && shopt -q nullglob nocasematch && builtin printf 'OPTIONS_LIVE\\n'; builtin printf ' PERSIST=%s,%s\\n' \"$1\" \"$2\"; false"
			if scenario != "persistent" {
				request.Source = "builtin printf 'NEXT_STATUS=%s\\n' \"$?\"; false"
			}
		}
		send(request)
		if _, ok := receive().(*protocol.PrivateSubmit); !ok {
			t.Fatal("missing submit")
		}
		if _, err := master.Write([]byte{'\x18', '\x02'}); err != nil {
			t.Fatal(err)
		}
		if _, ok := receive().(*protocol.PrivateStartPrepared); !ok {
			t.Fatal("missing prepared")
		}
		send(protocol.PrivateStartRelease{OperationID: id})
		if _, ok := receive().(*protocol.PrivateStarted); !ok {
			t.Fatal("missing started")
		}
		send(protocol.PrivateStartedAck{OperationID: id})
		if _, ok := receive().(*protocol.PrivateStartReleased); !ok {
			t.Fatal("missing released")
		}
		if fatal && (scenario == "trap-int" || scenario == "trap-chld" || scenario == "disable-kill" || scenario == "disable-trap" || scenario == "shadow-builtin" || scenario == "shadow-command" || scenario == "binding" || scenario == "prompt" || scenario == "expired-cleanup" || scenario == "invalid-environment" || scenario == "wrong-first-phase" || scenario == "helper-child" || scenario == "helper-int-damage") {
			break
		}
		if scenario == "wrong-peer" {
			c, e := net.DialUnix("unix", nil, &net.UnixAddr{Name: helper.SocketPath, Net: "unix"})
			if e != nil {
				t.Fatal(e)
			}
			c.Close()
			break
		}
		closed, ok := receive().(*protocol.PrivateInputClose)
		if !ok || closed.OperationID != id {
			t.Fatal("missing input_close")
		}
		if closed.CompletionHelperPID <= 0 {
			t.Fatal("missing helper identity")
		}
		if scenario == "close-held" || scenario == "early-finish" {
			break
		}
		if scenario == "jobs" {
			shell, _, _, e := processIdentity(int(closed.CompletionHelperPID))
			if e != nil {
				t.Fatal(e)
			}
			entries, _ := os.ReadDir("/proc")
			killed := false
			for _, entry := range entries {
				pid, e := strconv.Atoi(entry.Name())
				if e != nil || int64(pid) == closed.CompletionHelperPID {
					continue
				}
				parent, _, _, e := processIdentity(pid)
				if e == nil && parent == shell {
					syscall.Kill(pid, syscall.SIGKILL)
					killed = true
				}
			}
			if n == 0 && !killed {
				t.Fatal("missing authored background job")
			}
		}
		// Mutate fresh termios during Envoy's simulated cleanup window. Completion
		// must preserve this state rather than its earlier prompt_state snapshot.
		fresh, e := termios(int(master.Fd()))
		if e != nil {
			t.Fatal(e)
		}
		fresh.Cc[syscall.VQUIT] = 8
		fresh.Iflag ^= syscall.IXOFF
		if e = ioctl(int(master.Fd()), syscall.TCSETS, unsafe.Pointer(&fresh)); e != nil {
			t.Fatal(e)
		}
		freshStates = append(freshStates, fresh)
		if scenario == "missing-control" {
			break
		}
		if scenario == "wrong-id" {
			send(protocol.PrivateInputClosed{OperationID: "other"})
			break
		}
		if scenario == "wrong-control" {
			send(protocol.PrivateStartedAck{OperationID: id})
			break
		}
		if scenario == "allowed-trap" {
			shell, _, _, e := processIdentity(int(closed.CompletionHelperPID))
			if e != nil {
				t.Fatal(e)
			}
			if e := syscall.Kill(shell, syscall.SIGUSR2); e != nil {
				t.Fatal(e)
			}
		}
		send(protocol.PrivateInputClosed{OperationID: id})
		if scenario == "duplicate-control" || scenario == "identity-damage" || scenario == "final-helper-failure" || scenario == "duplicate-final-state" || scenario == "final-status-damage" || scenario == "close-held" || scenario == "early-finish" || scenario == "release-signal" || strings.HasPrefix(scenario, "terminal-") {
			break
		}
		completed, ok := receive().(*protocol.PrivateCompleted)
		if !ok {
			t.Fatal("missing completed")
		}
		if fatal {
			t.Fatal("fatal case wrote completed")
		}
		wantStatus := int64(0)
		if n == 1 || scenario == "status-one" || scenario == "editing" {
			wantStatus = 1
		}
		if completed.Inspections[1].ResolvedPath != "/hostile/artifact" || completed.Inspections[2].ResolvedPath != "/root/../artifact" || scenario != "allowed-trap" && completed.Inspections[3].ResolvedPath != completed.PhysicalCWD+"/${UNDEFINED}/../literal" {
			t.Fatalf("home/literal path semantics: %#v", completed)
		}
		if completed.Status != wantStatus {
			t.Fatalf("source status %d want %d", completed.Status, wantStatus)
		}
		if scenario == "persistent" && (completed.PhysicalCWD != "/tmp" || completed.Inspections[0].ResolvedPath != "/tmp/link/../artifact") {
			t.Fatalf("final plans %#v", completed)
		}
		if scenario == "allowed-trap" && (completed.PhysicalCWD != "/" || completed.Inspections[0].ResolvedPath != "//final/../artifact" || completed.Inspections[3].ResolvedPath != "//${UNDEFINED}/../literal") {
			t.Fatalf("final cleanup state: %#v", completed)
		}
		boundary, e := termios(int(master.Fd()))
		if e != nil || boundary.Lflag&(syscall.ICANON|syscall.ECHO) != 0 {
			t.Fatalf("not Readline: %#v %v", boundary, e)
		}
	}
	evidenceR.SetReadDeadline(time.Now().Add(4 * time.Second))
	detail, e := io.ReadAll(evidenceR)
	if e != nil {
		t.Fatal(e)
	}
	var proof struct {
		Error     string `json:"error"`
		Fatal     bool   `json:"fatal"`
		Snapshots []struct {
			State    protocol.PromptState `json:"state"`
			Workload syscall.Termios      `json:"workload"`
			Active   syscall.Termios      `json:"active"`
		} `json:"snapshots"`
	}
	if e = json.Unmarshal(detail, &proof); e != nil {
		t.Fatalf("evidence: %s %v", detail, e)
	}
	if fatal {
		resultR.SetReadDeadline(time.Now().Add(time.Second))
		if m, e := protocol.ReadPrivate(reader, protocol.AwshToEnvoy); m != nil || e != io.EOF {
			t.Fatalf("private result after fatal transition: %#v %v", m, e)
		}
		if proof.Error == "<nil>" || !proof.Fatal {
			t.Fatalf("fatal not observed: %s", detail)
		}
	} else {
		if proof.Error != "<nil>" || proof.Fatal || len(proof.Snapshots) != 8 {
			t.Fatalf("completion: %s", detail)
		}
		for i, p := range proof.Snapshots {
			if p.Workload != freshStates[i] {
				t.Fatal("fresh workload state lost")
			}
		}
		if scenario == "editing" && (proof.Snapshots[0].State.HistExpand != "on" || proof.Snapshots[0].State.EditingMode != "vi" || !strings.Contains(snapshot(), "NEXT_STATUS=1")) {
			t.Fatalf("history/editing/status lost: %s PTY %q", detail, snapshot())
		}
		if scenario == "persistent" && (!strings.Contains(snapshot(), "PERSIST=one,two") || !strings.Contains(snapshot(), "alias persisted='true'") || !strings.Contains(snapshot(), "OPTIONS_LIVE")) {
			t.Fatalf("Bash persistence: %q", snapshot())
		}
		if strings.Contains(snapshot(), "__OMEGAFLOW_AWSH_") {
			t.Fatalf("canonical frame redisplayed: %q", snapshot())
		}
	}
}
