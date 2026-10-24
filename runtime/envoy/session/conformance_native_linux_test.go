//go:build linux

package session

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// These are final assembly proofs. All process, signal, cleanup and private
// endpoints remain their production owners; the retained B8 observation overlay
// only holds an existing start boundary until a real controller frame is queued.
func TestNativeConformance(t *testing.T) {
	if os.Getenv("OMEGAFLOW_NATIVE_CONFORMANCE") != "1" {
		t.Skip("requires actual Envoy/Awsh and genuinely qualified Bash")
	}
	cases := []string{"adapter-state", "status-and-source", "source-rejection", "inspection-paths"}
	for _, mode := range []string{"pty", "split"} {
		for _, phase := range []string{"before-first-byte", "partial-execute", "complete-execute", "submit", "start-prepared", "start-release-written", "started", "public-operation-started", "started-ack", "start-released"} {
			cases = append(cases, "start-cancel/"+mode+"/"+phase)
		}
	}
	for _, row := range conformanceReservedCases(t) {
		cases = append(cases, "reserved/"+row.ID)
	}
	if selected := os.Getenv("OMEGAFLOW_CONFORMANCE_CASE"); selected != "" {
		found := false
		for _, name := range cases {
			found = found || name == selected
		}
		if !found {
			t.Fatal("unknown development case", selected)
		}
		cases = []string{selected}
	}
	before, err := processSubreaperState()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range cases {
		if !t.Run(name, func(t *testing.T) {
			ctx, end := context.WithTimeout(context.Background(), 25*time.Second)
			defer end()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeConformanceFixture$", "-test.v")
			cmd.Env = append(os.Environ(), "OMEGAFLOW_CONFORMANCE_CHILD="+name)
			out, err := cmd.CombinedOutput()
			if err != nil || !strings.Contains(string(out), "NATIVE CONFORMANCE "+name+" PROVED") {
				t.Fatalf("actual conformance %s: %v\n%s", name, err, out)
			}
			t.Logf("%s", out)
		}) {
			return // A failed child may have left live actors in this test image.
		}
	}
	after, err := processSubreaperState()
	if err != nil || before != after {
		t.Fatal("native child changed the harness subreaper", before, after, err)
	}
}

func conformanceComplete(t *testing.T, n *nativeOrdinary, owner *processCensus, id, source, output string, status int64) {
	t.Helper()
	ctx, end := context.WithTimeout(context.Background(), 12*time.Second)
	defer end()
	start := n.begin(t, ctx, id, source)
	if err := n.c.completeOperation(ctx, owner); err != nil {
		t.Fatal("actual complete process assembly", err)
	}
	for {
		m := n.frame(t)
		if _, mark := m.(*protocol.OutputMark); mark {
			continue
		}
		completed, ok := m.(*protocol.OperationCompleted)
		if !ok || completed.OperationID != id || completed.Status != status || completed.Cwd != "/" || completed.OutputStart != start || n.markOffset < completed.OutputThrough {
			t.Fatal("wrong actual completed operation", m)
		}
		raw := n.bytesThrough(t, completed.OutputThrough)
		if ordinaryVisible(raw) != output {
			t.Fatalf("output or canonical source bytes changed: %q", raw)
		}
		return
	}
}

func TestNativeConformanceFixture(t *testing.T) {
	name := os.Getenv("OMEGAFLOW_CONFORMANCE_CHILD")
	if name == "" {
		return
	}
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	n := nativeOrdinaryFor(t)
	ctx, end := context.WithTimeout(context.Background(), 18*time.Second)
	defer end()
	owner, err := newProcessCensus(ctx, n.c.w.command.Process.Pid, int(n.c.w.ShellPID))
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	switch name {
	case "adapter-state":
		// Spell the protected function name through ordinary expansion, so this
		// observes Bash's readonly property rather than the source-prefix check.
		source := `name=__OMEGA'FLOW_AWSH_FAIL_STOP';
builtin unset -f "$name" 2>/dev/null; result=$?;
[[ $result != 0 ]] && [[ $(builtin declare -p -F "$name") == "declare -fr $name" ]] || builtin exit 91;
for name in trap enable awsh; do [[ $(builtin declare -p -F "$name") == "declare -fr $name" ]] || builtin exit 92; done;
[[ ! -o posix && ! -v POSIXLY_CORRECT ]] && builtin printf 'READONLY_OK\n'`
		conformanceComplete(t, n, owner, "state", source, "READONLY_OK\n", 0)
	case "inspection-paths":
		conformanceInspections(t, n, owner, ctx)
	case "status-and-source":
		conformanceComplete(t, n, owner, "false", "builtin false", "", 1)
		conformanceComplete(t, n, owner, "status", "builtin printf 'PRIOR:%s\\n' \"$?\"", "PRIOR:1\n", 0)
		conformanceComplete(t, n, owner, "unicode", "# SOURCE_MUST_NOT_REDISPLAY\nbuiltin printf '%s\\n' 'αβé🙂'\n", "αβé🙂\n", 0)
		conformanceComplete(t, n, owner, "heredoc", "cat <<'EOF'\n} literal\nEOF", "} literal\n", 0)
		conformanceComplete(t, n, owner, "maximum", "#"+strings.Repeat("x", 786431), "", 0)
	case "source-rejection":
		reserved := startRequest()
		reserved.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
		if _, err := protocol.EncodeController(&reserved); err != nil {
			t.Fatal("valid source admission control failed", err)
		}
		reserved.Source = "printf __OMEGAFLOW_AWSH_BAD"
		if _, err := protocol.EncodeController(&reserved); err == nil {
			t.Fatal("reserved source crossed public admission")
		}
		for i, source := range []string{"if true; then"} {
			request := startRequest()
			request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
			request.OperationID, request.Source = fmt.Sprintf("rejected-%d", i), source
			n.request(t, &request)
			event := receiveInput(t, n.c.w.channels)
			actual, ok := event.Message.(*protocol.Execute)
			if !ok {
				t.Fatal("missing actual execute", event)
			}
			out, err := n.c.Start(ctx, *actual)
			if err != nil || out.Started {
				t.Fatal("source rejection did not preserve idle shell", out, err)
			}
			for {
				m := n.frame(t)
				if _, mark := m.(*protocol.OutputMark); mark {
					continue
				}
				failed, ok := m.(*protocol.OperationFailed)
				if !ok || failed.OperationID != request.OperationID || failed.OutputStart != failed.OutputThrough {
					t.Fatal("source rejection published an executed result", m)
				}
				if strings.Trim(ordinaryVisible(n.bytesThrough(t, failed.OutputThrough)), "\n") != "" {
					t.Fatal("rejected source was redisplayed")
				}
				break
			}
		}
		conformanceComplete(t, n, owner, "after-rejection", "builtin printf 'REUSABLE\\n'", "REUSABLE\n", 0)
	default:
		if strings.HasPrefix(name, "reserved/") {
			conformanceReserved(t, n, owner, ctx, strings.TrimPrefix(name, "reserved/"))
			fmt.Println("NATIVE CONFORMANCE " + name + " PROVED")
			return
		}
		if !strings.HasPrefix(name, "start-cancel/") {
			t.Fatal("unknown conformance case", name)
		}
		conformanceStartCancel(t, n, owner, ctx, name)
		nativeCapturedHeld.phase = "" // The crossed Start and its workers joined.
		conformanceComplete(t, n, owner, "after-cancel", "builtin printf 'AFTER_CANCEL\\n'", "AFTER_CANCEL\n", 0)
	}
	if err := n.c.shutdownIdle("test-complete", owner); err != nil {
		t.Fatal("actual full-assembly idle shutdown", err)
	}
	fmt.Println("NATIVE CONFORMANCE " + name + " PROVED")
}

type conformanceReservedCase struct {
	ID, Command, Setup, Mode string
}

func conformanceReservedCases(t *testing.T) []conformanceReservedCase {
	t.Helper()
	var rows []conformanceReservedCase
	if err := json.Unmarshal([]byte(os.Getenv("OMEGAFLOW_CONFORMANCE_RESERVED_CASES")), &rows); err != nil || len(rows) == 0 {
		t.Fatal("explicit native reserved-state cases required", err)
	}
	seen := make(map[string]bool)
	for _, row := range rows {
		if row.ID == "" || row.Command == "" || row.Mode != "fatal" || seen[row.ID] {
			t.Fatal("invalid native reserved-state case", row.ID)
		}
		seen[row.ID] = true
	}
	return rows
}

func conformancePoll(t *testing.T, ctx context.Context, label string, observed func() bool) {
	t.Helper()
	tick := time.NewTicker(time.Millisecond)
	defer tick.Stop()
	for !observed() {
		select {
		case <-tick.C:
		case <-ctx.Done():
			t.Fatal(label, ctx.Err())
		}
	}
}

func conformanceReserved(t *testing.T, n *nativeOrdinary, owner *processCensus, ctx context.Context, id string) {
	t.Helper()
	var row conformanceReservedCase
	for _, candidate := range conformanceReservedCases(t) {
		if candidate.ID == id {
			row = candidate
		}
	}
	if row.ID == "" {
		t.Fatal("unknown reserved-state scenario", id)
	}
	dir := t.TempDir()
	initialTraps, finalTraps := filepath.Join(dir, "initial.traps"), filepath.Join(dir, "final.traps")
	initialBuiltins, finalBuiltins := filepath.Join(dir, "initial.builtins"), filepath.Join(dir, "final.builtins")
	done, returned := filepath.Join(dir, "snapshot.done"), filepath.Join(dir, "returned")
	snapshot := fmt.Sprintf("builtin trap -p > %q; builtin enable -a > %q; builtin enable printf; builtin printf DONE > %q", finalTraps, finalBuiltins, done)
	source := "CHLD_NUMBER=$(builtin kill -l CHLD); INT_NUMBER=$(builtin kill -l INT); USR2_NUMBER=$(builtin kill -l USR2); DEBUG_NUMBER=$(builtin kill -l DEBUG); ERR_NUMBER=$(builtin kill -l ERR); RETURN_NUMBER=$(builtin kill -l RETURN)\n" + row.Setup + "\n" +
		fmt.Sprintf("builtin trap '%s' WINCH\nbuiltin trap -p > %q\nbuiltin enable -a > %q\n", snapshot, initialTraps, initialBuiltins) +
		row.Command + fmt.Sprintf("\nbuiltin enable printf; builtin printf RETURNED > %q\n", returned)
	n.begin(t, ctx, id, source)
	result := make(chan error, 1)
	go func() { result <- n.c.completeOperation(ctx, owner) }()
	var helper *processRef
	conformancePoll(t, ctx, "manifested stopped helper not observed", func() bool {
		all, err := readProcesses(ctx)
		if err != nil {
			t.Fatal(err)
		}
		for pid, p := range all {
			if p.Parent != owner.shell {
				continue
			}
			ref, pinned, err := pinProcess(ctx, pid)
			if processGone(err) {
				continue // A sampled setup child can finish before its pin opens.
			}
			if err != nil {
				t.Fatal(err)
			}
			executable, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", pid))
			if err != nil || executable != "/omegaflow-runtime/bin/awsh" || pinned.Parent != owner.shell {
				ref.close()
				continue // Authored setup can also have ordinary short-lived children.
			}
			if !conformanceStopped(ctx, ref) {
				ref.close()
				continue
			}
			helper = ref
			return true
		}
		select {
		case err := <-result:
			t.Fatal("reserved mutation returned instead of fail-stop", err)
		default:
		}
		return false
	})
	defer helper.close()
	for i := 0; i < 3; i++ {
		if err := helper.signal(ctx, syscall.SIGCONT); err != nil {
			t.Fatal(err)
		}
		conformancePoll(t, ctx, "actual helper did not stop again", func() bool {
			return conformanceStopped(ctx, helper)
		})
	}
	if err := helper.signal(ctx, syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	conformancePoll(t, ctx, "actual helper was not reaped", func() bool {
		_, err := helper.observe(ctx)
		return processGone(err)
	})
	shell, _, err := pinProcess(ctx, owner.shell)
	if err != nil {
		t.Fatal(err)
	}
	defer shell.close()
	if err := shell.signal(ctx, syscall.SIGWINCH); err != nil {
		t.Fatal(err)
	}
	conformancePoll(t, ctx, "Bash fallback snapshot did not run", func() bool { _, err := os.Stat(done); return err == nil })
	for _, pair := range [][2]string{{initialTraps, finalTraps}, {initialBuiltins, finalBuiltins}} {
		before, err := os.ReadFile(pair[0])
		if err != nil {
			t.Fatal(err)
		}
		after, err := os.ReadFile(pair[1])
		if err != nil || string(before) != string(after) {
			t.Fatal("reserved mutation partially changed Bash state", pair, err)
		}
	}
	if _, err := os.Stat(returned); !os.IsNotExist(err) {
		t.Fatal("actual fail-stop returned to authored source", err)
	}
	if err := n.terminal.CloseWrite(); err != nil {
		t.Fatal(err)
	}
	var cause error
	select {
	case cause = <-result:
	case <-ctx.Done():
		t.Fatal("actual controller loss did not join operation", ctx.Err())
	}
	if !errors.Is(cause, io.EOF) {
		t.Fatal("controller EOF did not fail the real operation", cause)
	}
	conformanceFailedDisposal(t, n, owner, cause)
}

func conformanceStopped(ctx context.Context, ref *processRef) bool {
	stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", ref.pid))
	if err != nil {
		return false
	}
	fields := strings.Fields(string(stat)[strings.LastIndexByte(string(stat), ')')+1:])
	p, err := ref.observe(ctx)
	return err == nil && p.Live && len(fields) != 0 && fields[0] == "T"
}

var conformanceBoundaryOnce sync.Once

// The existing partial-write observation can cross more than once in the real
// write loop. This test-only hold releases every later crossing unchanged.
// The B8.4 runner inserts this call into a copy of the existing test observer.
func nativeConformanceBoundary(ctx context.Context) bool {
	if !strings.HasPrefix(os.Getenv("OMEGAFLOW_CONFORMANCE_CHILD"), "start-cancel/") {
		return false
	}
	conformanceBoundaryOnce.Do(func() {
		close(nativeCapturedHeld.entered)
		select {
		case <-nativeCapturedHeld.release:
		case <-ctx.Done():
		}
	})
	return true
}

func conformanceStartCancel(t *testing.T, n *nativeOrdinary, owner *processCensus, ctx context.Context, name string) {
	t.Helper()
	parts := strings.Split(name, "/")
	mode, phase := parts[1], parts[2]
	h := &nativeCapturedHook{phase: phase, entered: make(chan struct{}), release: make(chan struct{}), cancelAvailable: make(chan struct{})}
	nativeCapturedHeld = h
	request := startRequest()
	if mode == "split" {
		request = splitRequest()
		n.c.setup = NewSplitSetup()
	}
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	enteredSource := filepath.Join(t.TempDir(), "authored-entered")
	request.OperationID, request.Source = "crossed-cancel", fmt.Sprintf("builtin printf ENTERED > %q; builtin read -r hold", enteredSource)
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("missing actual start request", event)
	}
	type result struct {
		out StartOutcome
		err error
	}
	done := make(chan result, 1)
	go func() { out, err := n.c.Start(ctx, *actual); done <- result{out, err} }()
	var started result
	if phase == "start-released" {
		select {
		case started = <-done:
		case <-ctx.Done():
			t.Fatal("actual start did not release", ctx.Err())
		}
	} else {
		select {
		case <-h.entered:
		case <-ctx.Done():
			t.Fatal("actual start boundary was not observed", phase, ctx.Err())
		}
	}
	n.request(t, &protocol.Cancel{Header: protocol.Header{Schema: protocol.Schema, Type: "cancel", Seq: n.seq}, OperationID: request.OperationID, Reason: "conformance"})
	select {
	case <-h.cancelAvailable:
	case <-ctx.Done():
		t.Fatal("real cancel was not queued", ctx.Err())
	}
	if phase == "before-first-byte" {
		// Acceptance must cancel the still-held zero-byte writer. Merely
		// publishing a control into the queue does not prove this crossing.
		select {
		case started = <-done:
		case <-ctx.Done():
			t.Fatal("pre-write cancel was not accepted while held", ctx.Err())
		}
		close(h.release)
	} else {
		close(h.release)
		if phase != "start-released" {
			select {
			case started = <-done:
			case <-ctx.Done():
				t.Fatal("actual crossed start did not join", ctx.Err())
			}
		}
	}
	if started.out.Started != (phase != "before-first-byte") {
		t.Fatal("cancel crossed the wrong private-attempt boundary", phase, started.out)
	}
	if started.err != nil {
		t.Fatal("queued cancellation became a failed private transaction", started.err)
	}
	if started.out.Started {
		// The queued control stays at its original start crossing. Entering
		// authored builtins proves the short-lived PS0 helper has returned;
		// the intended live-foreground cancellation now has a stable target.
		conformancePoll(t, ctx, "actual authored entry was not reached", func() bool {
			_, err := os.Stat(enteredSource)
			return err == nil
		})
		if err := n.c.completeOperation(ctx, owner); err != nil {
			t.Fatal("actual queued-cancel process assembly", err)
		}
	}
	observedStart := false
	for {
		// Split output uses the same global byte frontier with explicit stream
		// marks. Preserve the sequence and covering-mark checks for both modes.
		m := nativeFrame(t, n.reader)
		encoded, err := protocol.EncodeEnvoy(m)
		var header protocol.Header
		if err != nil || json.Unmarshal(encoded, &header) != nil || header.Seq != n.lastSeq+1 {
			t.Fatal("invalid actual conformance sequence", m, err)
		}
		n.lastSeq = header.Seq
		switch value := m.(type) {
		case *protocol.OutputMark:
			if value.Offset < n.markOffset || value.ElapsedUS < n.markElapsed ||
				(value.Stream != "pty" && (mode != "split" || (value.Stream != "stdout" && value.Stream != "stderr"))) {
				t.Fatal("invalid actual conformance stream mark", value)
			}
			n.markOffset, n.markElapsed = value.Offset, value.ElapsedUS
			continue
		case *protocol.OperationStarted:
			if observedStart || value.OperationID != request.OperationID {
				t.Fatal("duplicate or wrong actual start", value)
			}
			observedStart = true
		case *protocol.OperationCancelled:
			if value.OperationID != request.OperationID || value.Reason != "conformance" || (value.Status != nil) != observedStart || (value.Status != nil && *value.Status != 130) || observedStart != started.out.Started || n.markOffset < value.OutputThrough {
				t.Fatal("wrong actual cancel winner or range", value, started.out)
			}
			n.bytesThrough(t, value.OutputThrough)
			if err := owner.ProveIdle(ctx); err != nil {
				t.Fatal("cancel did not restore the actual empty process tree", err)
			}
			return
		default:
			t.Fatal("crossed cancellation invented another terminal result", m)
		}
	}
}

func conformanceFailedDisposal(t *testing.T, n *nativeOrdinary, owner *processCensus, cause error) {
	t.Helper()
	n.telemetry.SetReadDeadline(time.Now().Add(time.Second))
	for {
		line, err := readLine(n.reader)
		if err != nil {
			if !errors.Is(err, io.EOF) {
				t.Fatal("failed-session telemetry", err)
			}
			break
		}
		var header protocol.Header
		if err := json.Unmarshal(line, &header); err != nil {
			t.Fatal(err)
		}
		if header.Type != "operation_started" && strings.HasPrefix(header.Type, "operation_") {
			t.Fatal("failed session emitted a terminal result", string(line))
		}
	}
	bound, end := context.WithTimeout(context.Background(), finalDrainBudget)
	defer end()
	if err := n.c.disposeFailure(bound, owner, cause); !errors.Is(err, cause) || !n.c.failureDisposed {
		t.Fatal("failed session lost original cause or disposal", err)
	}
	if err := owner.proveEnded(bound); err != nil {
		t.Fatal("failed session left actual descendants", err)
	}
	select {
	case <-n.c.w.reaped:
	default:
		t.Fatal("failed session did not join actual Awsh Wait")
	}
	t.Logf("Actual failed session disposed with no terminal operation result; original cause: %v", cause)
}

func conformanceInspections(t *testing.T, n *nativeOrdinary, owner *processCensus, ctx context.Context) {
	t.Helper()
	root := t.TempDir()
	request := startRequest()
	request.Header = protocol.Header{Schema: protocol.Schema, Type: "execute", Seq: n.seq}
	request.OperationID = "inspection-paths"
	request.Observation = "exclusive"
	request.Source = fmt.Sprintf(`base=%q
/usr/bin/mkdir -p "$base/real/child" "$base/home"
builtin printf 'abc\n' > "$base/real/artifact"
builtin printf home > "$base/home/artifact"
builtin printf literal > "$base/"'${UNDEFINED}'
builtin printf malformed > "$base/"'${OUT:-bad}'
builtin printf unresolved > "$base/~omegaflow_missing_user_b7"
builtin printf utf8 > "$base/é"
/usr/bin/ln -s real/child "$base/link"
builtin unset UNDEFINED
export OUT=link/../artifact HOME="$base/home"
builtin cd "$base"`, root)
	request.Inspections = []protocol.Inspection{
		{InspectionID: "produced", Kind: "produces", Path: "$OUT", ProducerID: "source", OutputID: "file"},
		{InspectionID: "directory", Kind: "produces", Path: "real", ProducerID: "source", OutputID: "tree"},
		{InspectionID: "home", Kind: "file_exists", Path: "~/artifact"},
		{InspectionID: "user", Kind: "file_exists", Path: "~root"},
		{InspectionID: "undefined", Kind: "file_exists", Path: "${UNDEFINED}"},
		{InspectionID: "malformed", Kind: "file_exists", Path: "${OUT:-bad}"},
		{InspectionID: "unresolved", Kind: "file_exists", Path: "~omegaflow_missing_user_b7"},
		{InspectionID: "utf8", Kind: "file_exists", Path: "é"},
	}
	n.request(t, &request)
	event := receiveInput(t, n.c.w.channels)
	actual, ok := event.Message.(*protocol.Execute)
	if !ok {
		t.Fatal("missing actual inspection request", event)
	}
	if out, err := n.c.Start(ctx, *actual); err != nil || !out.Started {
		t.Fatal("actual inspection start failed", out, err)
	}
	var outputStart int64
	for {
		m := n.frame(t)
		if _, mark := m.(*protocol.OutputMark); mark {
			continue
		}
		started, ok := m.(*protocol.OperationStarted)
		if !ok || started.OperationID != request.OperationID || n.markOffset < started.OutputStart {
			t.Fatal("actual inspection start lacks its covering mark", m)
		}
		outputStart = started.OutputStart
		if prefix := n.bytesThrough(t, outputStart); strings.Trim(ordinaryVisible(prefix), "\n") != "" {
			t.Fatalf("inspection source before actual start: %q", prefix)
		}
		break
	}
	if err := n.c.completeOperation(ctx, owner); err != nil {
		t.Fatal("actual inspection worker assembly", err)
	}
	for {
		m := n.frame(t)
		if _, mark := m.(*protocol.OutputMark); mark {
			continue
		}
		completed, ok := m.(*protocol.OperationCompleted)
		if !ok || completed.OperationID != request.OperationID || completed.Status != 0 || completed.Cwd != root || len(completed.InspectionResults) != 8 || completed.OutputStart != outputStart || n.markOffset < completed.OutputThrough {
			t.Fatal("wrong actual inspection completion", m)
		}
		paths := []string{root + "/real/artifact", root + "/real", root + "/home/artifact", "/root", root + "/${UNDEFINED}", root + "/${OUT:-bad}", root + "/~omegaflow_missing_user_b7", root + "/é"}
		for i, value := range completed.InspectionResults {
			plan := request.Inspections[i]
			if value.InspectionID != plan.InspectionID || value.Kind != plan.Kind || value.ResolvedPath != paths[i] || value.ProducerID != plan.ProducerID || value.OutputID != plan.OutputID {
				t.Fatal("actual final-state inspection path or correlation changed", i, value)
			}
		}
		if completed.InspectionResults[0].SHA256 != "edeaaff3f1774ad2888673770c6d64097e391bc362d7d6fb34982ddf0efd18cb" || completed.InspectionResults[1].DigestAlgorithm != "directory-v2" {
			t.Fatal("actual inspection digest changed", completed.InspectionResults)
		}
		if raw := n.bytesThrough(t, completed.OutputThrough); ordinaryVisible(raw) != "" {
			t.Fatalf("inspection source was redisplayed: %q", raw)
		}
		if err := owner.ProveIdle(ctx); err != nil {
			t.Fatal("actual inspection worker was not reaped", err)
		}
		return
	}
}
