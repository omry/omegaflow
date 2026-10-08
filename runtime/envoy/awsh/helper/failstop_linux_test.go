package helper

import (
	"bytes"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func stopped(t *testing.T, pid int) {
	t.Helper()
	until := time.Now().Add(5 * time.Second)
	for time.Now().Before(until) {
		b, e := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
		if e != nil {
			t.Fatal(e)
		}
		fields := strings.Fields(string(b[strings.LastIndex(string(b), ")")+1:]))
		if len(fields) > 0 && fields[0] == "T" {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("fail-stop did not stop")
}
func TestFailStop(t *testing.T) {
	if os.Getenv("AWSH_TEST_FAILSTOP") == "1" {
		FailStop()
		os.Exit(99)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestFailStop$")
	cmd.Env = append(os.Environ(), "AWSH_TEST_FAILSTOP=1")
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if e := cmd.Start(); e != nil {
		t.Fatal(e)
	}
	defer func() { cmd.Process.Kill(); cmd.Wait() }()
	stopped(t, cmd.Process.Pid)
	for range 3 {
		if e := cmd.Process.Signal(syscall.SIGCONT); e != nil {
			t.Fatal(e)
		}
		time.Sleep(10 * time.Millisecond)
		stopped(t, cmd.Process.Pid)
	}
	cmd.Process.Kill()
	e := cmd.Wait()
	exit, ok := e.(*exec.ExitError)
	if !ok || exit.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
		t.Fatalf("termination %v", e)
	}
	if out.Len() != 0 {
		t.Fatalf("output %q", out.Bytes())
	}
}
