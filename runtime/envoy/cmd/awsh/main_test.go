//go:build linux

package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestFixedFailStopCommand(t *testing.T) {
	bin := filepath.Join(t.TempDir(), "awsh")
	if out, e := exec.Command("go", "build", "-buildvcs=false", "-o", bin, ".").CombinedOutput(); e != nil {
		t.Fatalf("build %v %s", e, out)
	}
	for _, args := range [][]string{nil, {"bash-fail-stop", "extra"}, {"bash-helper"}, {"other"}} {
		out, e := exec.Command(bin, args...).CombinedOutput()
		exit, ok := e.(*exec.ExitError)
		if !ok || exit.ExitCode() != 2 || len(out) != 0 {
			t.Fatalf("args%v: %v %q", args, e, out)
		}
	}
	cmd := exec.Command(bin, "bash-fail-stop")
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if e := cmd.Start(); e != nil {
		t.Fatal(e)
	}
	defer func() { cmd.Process.Kill(); cmd.Wait() }()
	waitStopped := func() {
		t.Helper()
		until := time.Now().Add(5 * time.Second)
		for time.Now().Before(until) {
			b, e := os.ReadFile("/proc/" + strconv.Itoa(cmd.Process.Pid) + "/stat")
			if e != nil {
				t.Fatal(e)
			}
			f := strings.Fields(string(b[strings.LastIndex(string(b), ")")+1:]))
			if len(f) > 0 && f[0] == "T" {
				return
			}
			time.Sleep(time.Millisecond)
		}
		t.Fatal("command did not stop")
	}
	waitStopped()
	cmd.Process.Signal(syscall.SIGCONT)
	time.Sleep(10 * time.Millisecond)
	waitStopped()
	cmd.Process.Kill()
	e := cmd.Wait()
	exit, ok := e.(*exec.ExitError)
	if !ok || exit.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL || out.Len() != 0 {
		t.Fatalf("fail-stop %v %q", e, out.Bytes())
	}
}
