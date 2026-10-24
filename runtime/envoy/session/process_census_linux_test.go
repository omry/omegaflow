//go:build linux

package session

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func runProcessFixtureShell() {
	var children []*exec.Cmd
	defer func() {
		for _, cmd := range children {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		switch scanner.Text() {
		case "spawn":
			cmd := processFixtureCommand("leaf")
			if err := cmd.Start(); err != nil {
				panic(err)
			}
			children = append(children, cmd)
			fmt.Println("CHILD", cmd.Process.Pid)
		case "orphan":
			cmd := processFixtureCommand("middle")
			cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
			if err := cmd.Run(); err != nil {
				panic(err)
			}
			fmt.Println("ADOPTED")
		case "reap":
			for _, cmd := range children {
				cmd.Process.Kill()
				cmd.Wait()
			}
			children = nil
			fmt.Println("REAPED")
		case "quit":
			return
		default:
			panic("unknown fixture command")
		}
	}
}

type censusFixture struct {
	command *exec.Cmd
	input   io.WriteCloser
	lines   *bufio.Reader
	awsh    int
	shell   int
	owner   *processCensus
	orphans []int
}

func censusFixtureFor(t *testing.T) *censusFixture {
	t.Helper()
	if err := enableSubreaper(); err != nil {
		t.Fatal(err)
	}
	cmd := processFixtureCommand("awsh")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	cmd.Stderr = os.Stderr
	input, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	f := &censusFixture{command: cmd, input: input, lines: bufio.NewReader(output)}
	t.Cleanup(func() {
		f.input.Close() // selected fixture kills/reaps its own direct children
		for _, pid := range f.orphans {
			if ref, _, err := pinProcess(censusContext(t), pid); err == nil {
				fixtureSignal(ref, syscall.SIGKILL)
				ref.close()
			}
			var status syscall.WaitStatus
			_, _ = syscall.Wait4(pid, &status, 0, nil)
		}
		cmd.Wait()
		if f.owner != nil {
			if err := f.owner.Close(); err != nil {
				t.Error(err)
			}
		}
	})
	line, err := f.lines.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Fields(line)
	if len(parts) != 3 || parts[0] != "ROOTS" {
		t.Fatal("invalid selected fixture roots", line)
	}
	f.awsh, err = strconv.Atoi(parts[1])
	if err == nil {
		f.shell, err = strconv.Atoi(parts[2])
	}
	if err != nil || f.awsh != cmd.Process.Pid {
		t.Fatal(line, err)
	}
	f.owner, err = newProcessCensus(censusContext(t), f.awsh, f.shell)
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *censusFixture) send(t *testing.T, command string) string {
	t.Helper()
	if _, err := fmt.Fprintln(f.input, command); err != nil {
		t.Fatal(err)
	}
	line, err := f.lines.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	if command == "orphan" {
		if ready, err := f.lines.ReadString('\n'); err != nil || ready != "ADOPTED\n" {
			t.Fatal("intermediate parent not reaped", ready, err)
		}
	}
	return line
}

func runProcessCensusCase(t *testing.T, scenario string) {
	f := censusFixtureFor(t)
	c := f.owner
	ctx := censusContext(t)
	outside, err := strconv.Atoi(os.Getenv("OMEGAFLOW_PROCESS_OUTSIDE"))
	if err != nil {
		t.Fatal(err)
	}
	initial, err := c.Census(ctx)
	if err != nil || len(initial) != 2 || initial[outside].PID != 0 {
		t.Fatal("initial controlled boundary includes outside service", initial, err)
	}
	if err := c.ProveIdle(ctx); err != nil {
		t.Fatal(err)
	}
	switch scenario {
	case "topology":
		if err := c.ProveBoundary(ctx, int64(f.awsh)); err != nil {
			t.Fatal(err)
		}
		if err := c.ProveBoundary(ctx, int64(f.shell)); err == nil {
			t.Fatal("wrong session accepted")
		}
		members, err := c.Members(ctx, int64(f.shell))
		if err != nil || len(members) != 1 || members[0].PID != int64(f.shell) {
			t.Fatal(members, err)
		}
		if err := validateForeground(int64(f.awsh), int64(f.awsh), int64(f.shell), members); err != nil {
			t.Fatal(err)
		}
		empty, err := c.Members(ctx, 2147483647)
		if err != nil || len(empty) != 0 {
			t.Fatal("stale empty group rejected", empty, err)
		}
	case "descendants":
		pid := processFixturePID(t, f.send(t, "spawn"), "CHILD")
		p := requireProcess(t, c, pid, true)
		if p.Parent != f.shell || p.Group != f.shell || p.Session != f.awsh {
			t.Fatal("wrong descendant ancestry", p)
		}
		if err := c.ProveIdle(ctx); err == nil {
			t.Fatal("live authored child allowed at idle")
		}
		members, err := c.Members(ctx, int64(f.shell))
		if err != nil || len(members) != 2 {
			t.Fatal("incomplete live foreground membership", members, err)
		}
		for _, member := range members {
			if !member.Controlled {
				t.Fatal("controlled descendant not pinned", member)
			}
		}
		if f.send(t, "reap") != "REAPED\n" {
			t.Fatal("fixture did not reap")
		}
		if err := c.ProveIdle(ctx); err != nil {
			t.Fatal("completed/reaped child retained as live", err)
		}
		if _, ok := c.refs[pid]; ok {
			t.Fatal("reaped child pin leaked")
		}
	case "current-sample":
		if _, err := c.refresh(ctx); err != nil {
			t.Fatal("pre-sample refresh failed", err)
		}
		pid := processFixturePID(t, f.send(t, "spawn"), "CHILD")
		members, err := c.membersAfterRefresh(ctx, int64(f.shell))
		if err != nil || len(members) != 2 {
			t.Fatal("incomplete current foreground membership", members, err)
		}
		found := false
		for _, member := range members {
			if !member.Controlled {
				t.Fatal("current controlled child classified foreign", member)
			}
			if member.PID == int64(pid) {
				found = true
			}
		}
		if !found {
			t.Fatal("post-refresh child missing from current foreground sample", pid, members)
		}
	case "rebind-modeled":
		pid := processFixturePID(t, f.send(t, "spawn"), "CHILD")
		if _, err := c.Census(ctx); err != nil {
			t.Fatal(err)
		}
		current, err := readProcess(pid)
		if err != nil {
			t.Fatal(err)
		}
		old := processFixtureCommand("leaf")
		if err := old.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			_ = old.Process.Kill()
			_ = old.Wait()
		})
		stale, _, err := pinProcess(ctx, old.Process.Pid)
		if err != nil {
			t.Fatal(err)
		}
		if err := old.Process.Kill(); err != nil {
			t.Fatal(err)
		}
		_ = old.Wait()
		// Model only the stale numeric map state that PID recycling could create.
		// This does not claim that the kernel reused this PID in the test.
		if err := c.refs[pid].close(); err != nil {
			t.Fatal(err)
		}
		stale.pid = pid
		c.refs[pid] = stale
		shell, err := readProcess(f.shell)
		if err != nil {
			t.Fatal(err)
		}
		all := map[int]processInfo{pid: current}
		controlled := map[int]processInfo{f.shell: shell, pid: current}
		members, err := c.foregroundMembers(ctx, int64(f.shell), all, controlled)
		if err == nil {
			t.Fatal("stale current lifetime was silently accepted", members)
		}
		if c.refs[pid] != stale {
			t.Fatal("stale lifetime pin was replaced")
		}
	case "root-exit":
		all, controlled, err := c.scanWithAll(ctx)
		if err != nil {
			t.Fatal(err)
		}
		original := c.refs[f.shell]
		if _, err := fmt.Fprintln(f.input, "quit"); err != nil {
			t.Fatal(err)
		}
		if err := f.command.Wait(); err != nil {
			t.Fatal(err)
		}
		if _, err := c.foregroundMembers(ctx, int64(f.shell), all, controlled); err == nil {
			t.Fatal("dead root authorized foreground membership")
		}
		if c.refs[f.shell] != original || original.fd < 0 {
			t.Fatal("foreground proof replaced retained root lifetime")
		}
	case "adoption", "rapid-adoption":
		n := 1
		if scenario == "rapid-adoption" {
			n = 24
		}
		for i := 0; i < n; i++ {
			pid := processFixturePID(t, f.send(t, "orphan"), "ORPHAN")
			f.orphans = append(f.orphans, pid)
		}
		all, err := c.Census(ctx)
		if err != nil || len(all) != n+2 {
			t.Fatal("missed adopted descendants", n, len(all), err)
		}
		for _, pid := range f.orphans {
			p := all[pid]
			if !p.Live || p.Parent != os.Getpid() || p.Session != pid || p.Group != pid {
				t.Fatal("setsid orphan escaped controlled census", p)
			}
		}
		if err := c.ProveIdle(ctx); err == nil {
			t.Fatal("adopted daemon admitted at idle")
		}
		// Repeated complete observations retain the same lifetime handles.
		before := c.refs[f.orphans[0]]
		if _, err := c.Census(ctx); err != nil || c.refs[f.orphans[0]] != before {
			t.Fatal("live adoption pin replaced", err)
		}
		pid := f.orphans[0]
		if err := fixtureSignal(c.refs[pid], syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		for {
			p, err := c.refs[pid].observe(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if !p.Live {
				break
			}
			if err := ctx.Err(); err != nil {
				t.Fatal(err)
			}
		}
		requireProcess(t, c, pid, false) // unreaped adopted lifetime remains bound
		if n == 1 && c.ProveIdle(ctx) == nil {
			t.Fatal("idle proof accepted an unreaped adopted child")
		}
		var status syscall.WaitStatus
		if got, err := syscall.Wait4(pid, &status, 0, nil); err != nil || got != pid {
			t.Fatal("test-owned adopted reap", got, err)
		}
		f.orphans = f.orphans[1:]
		if _, err := c.Census(ctx); err != nil {
			t.Fatal(err)
		}
		if _, ok := c.refs[pid]; ok {
			t.Fatal("reaped adopted pin leaked")
		}
		if n == 1 {
			if err := c.ProveIdle(ctx); err != nil {
				t.Fatal("reaped boundary did not become idle", err)
			}
		}
	case "concurrent-adoption":
		finished := make(chan struct{})
		finishedClosed := false
		defer func() {
			if !finishedClosed {
				close(finished)
			}
		}()
		observed := make(chan error, 1)
		go func() {
			for {
				if _, err := c.Census(ctx); err != nil {
					observed <- err
					return
				}
				select {
				case <-finished:
					observed <- nil
					return
				default:
				}
			}
		}()
		for i := 0; i < 48; i++ {
			pid := processFixturePID(t, f.send(t, "orphan"), "ORPHAN")
			f.orphans = append(f.orphans, pid)
		}
		close(finished)
		finishedClosed = true
		if err := <-observed; err != nil {
			t.Fatal("concurrent census failed", err)
		}
		all, err := c.Census(ctx)
		if err != nil || len(all) != 50 {
			t.Fatal("concurrent adoption lost process", len(all), err)
		}
		for _, pid := range f.orphans {
			if p := all[pid]; !p.Live || p.Parent != os.Getpid() || p.Session != pid {
				t.Fatal("concurrent adoption misclassified", pid, p)
			}
		}
	case "foreign-group":
		members, err := c.Members(ctx, int64(outside))
		if err != nil || len(members) != 1 || members[0].Controlled || members[0].PID != int64(outside) {
			t.Fatal("outside live member hidden or classified controlled", members, err)
		}
		if err := validateForeground(int64(f.awsh), int64(f.awsh), int64(outside), members); err == nil {
			t.Fatal("foreign foreground group allowed")
		}
		if _, ok := c.refs[outside]; ok {
			t.Fatal("foreign membership pin leaked into controlled tree")
		}
	case "dead-roots":
		if _, err := fmt.Fprintln(f.input, "quit"); err != nil {
			t.Fatal(err)
		}
		if err := f.command.Wait(); err != nil {
			t.Fatal(err)
		}
		if err := c.ProveBoundary(ctx, int64(f.awsh)); err == nil {
			t.Fatal("dead/reaped roots authorized foreground signal")
		}
		if err := c.ProveIdle(ctx); err == nil {
			t.Fatal("dead roots authorized another operation")
		}
	case "errors":
		if _, err := newProcessCensus(context.Background(), f.awsh, f.shell); err == nil {
			t.Fatal("constructor accepted no caller deadline")
		}
		for _, pair := range [][2]int{{0, f.shell}, {f.awsh, 0}, {f.awsh, f.awsh}, {os.Getpid(), f.shell}, {f.awsh, os.Getpid()}, {2147483647, f.shell}} {
			if owner, err := newProcessCensus(ctx, pair[0], pair[1]); err == nil {
				owner.Close()
				t.Fatal("invalid roots accepted", pair)
			}
		}
		// A valid pin of the wrong selected topology still cannot authorize it.
		if owner, err := newProcessCensus(ctx, f.awsh, outside); err == nil {
			owner.Close()
			t.Fatal("outside process substituted for selected shell")
		}
		expired, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		defer cancel()
		if _, err := c.Census(expired); !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("expired caller epoch ignored", err)
		}
		if _, err := c.Members(ctx, 0); err == nil {
			t.Fatal("invalid sampled group accepted")
		}
		if _, err := c.Members(ctx, 2147483648); err == nil {
			t.Fatal("overflow sampled group accepted")
		}
		broken, err := newProcessCensus(ctx, f.awsh, f.shell)
		if err != nil {
			t.Fatal(err)
		}
		if err := broken.refs[f.shell].close(); err != nil {
			t.Fatal(err)
		}
		if _, err := broken.Census(ctx); !errors.Is(err, os.ErrClosed) {
			t.Fatal("missing required lifetime pin silently ignored", err)
		}
		if err := broken.Close(); err != nil {
			t.Fatal(err)
		}
		var pinnedFDs []int
		for _, ref := range c.refs {
			pinnedFDs = append(pinnedFDs, ref.fd)
		}
		if err := c.Close(); err != nil {
			t.Fatal(err)
		}
		for _, fd := range pinnedFDs {
			if _, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0); errno != syscall.EBADF {
				t.Fatal("census leaked lifetime descriptor", fd, errno)
			}
		}
		if _, err := c.Census(ctx); !errors.Is(err, os.ErrClosed) {
			t.Fatal("closed census usable", err)
		}
		if err := c.Close(); err != nil || len(c.refs) != 0 {
			t.Fatal("census close not idempotent/disposed", err)
		}
		if _, _, errno := syscall.Syscall6(syscall.SYS_PRCTL, 36, 0, 0, 0, 0, 0); errno != 0 {
			t.Fatal(errno)
		}
		if err := requireSubreaper(); err == nil {
			t.Fatal("lost subreaper state accepted")
		}
		if owner, err := newProcessCensus(ctx, f.awsh, f.shell); err == nil {
			owner.Close()
			t.Fatal("constructor accepted absent subreaper")
		}
	default:
		t.Fatal("unknown native case", scenario)
	}
	if outsideInfo, err := readProcess(outside); err != nil || outsideInfo.PID != outside {
		t.Fatal("outside-tree service was affected", outsideInfo, err)
	}
}

func TestProcessCensusEquality(t *testing.T) {
	a := map[int]processInfo{1: {PID: 1, Parent: 2, Group: 3, Session: 3, Start: 4, Live: true}}
	b := map[int]processInfo{1: a[1]}
	if !sameProcesses(a, b) || sameProcesses(a, nil) {
		t.Fatal("bad complete observation equality")
	}
	b[2] = b[1]
	delete(b, 1)
	if sameProcesses(a, b) {
		t.Fatal("PID set change ignored")
	}
	delete(b, 2)
	p := a[1]
	p.Live = false
	b[1] = p
	if sameProcesses(a, b) {
		t.Fatal("lifetime liveness change ignored")
	}
}
