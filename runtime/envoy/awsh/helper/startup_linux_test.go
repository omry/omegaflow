package helper

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStartupHelperArguments(t *testing.T) {
	for _, args := range [][]string{nil, {"wrong", "prompt-ready"}, {"--socket=" + SocketPath},
		{"--socket=" + SocketPath, "prompt-ready", "extra"}, {"--socket=" + SocketPath, "prompt-state"},
		{"--socket=" + SocketPath, "prompt-state", "01", "on", "emacs"}, {"--socket=" + SocketPath, "gate", "id"},
		{"--socket=" + SocketPath, "prompt-state", "256", "on", "emacs"}, {"--socket=" + SocketPath, "prompt-state", "0", "bad", "emacs"}} {
		if err := StartupCommand(args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
}

func TestPhysicalAndLogicalPromptState(t *testing.T) {
	root := t.TempDir()
	physical := filepath.Join(root, "physical")
	alias := filepath.Join(root, "alias")
	if err := os.Mkdir(physical, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(physical, alias); err != nil {
		t.Fatal(err)
	}
	t.Chdir(physical)
	t.Setenv("PWD", alias)
	t.Setenv("ORDINARY_VALUE", "x\ny")
	s, err := CapturePromptState(42, "on", "vi")
	if err != nil {
		t.Fatal(err)
	}
	if s.Status != 42 || s.PhysicalCWD != physical || s.LogicalCWD != alias || s.ExportedEnv["ORDINARY_VALUE"] != "x\ny" || s.HistExpand != "on" || s.EditingMode != "vi" {
		t.Fatalf("wrong state: %+v", s)
	}
	for _, bad := range []string{"relative", root, filepath.Join(root, "missing")} {
		t.Setenv("PWD", bad)
		s, err = CapturePromptState(0, "off", "emacs")
		if err != nil || s.LogicalCWD != "" {
			t.Fatalf("bad logical cwd %q: %+v %v", bad, s, err)
		}
	}
	t.Setenv("BAD-NAME", "value")
	if _, err = CapturePromptState(0, "off", "emacs"); err == nil {
		t.Fatal("unrepresentable exported name omitted")
	}
	os.Unsetenv("BAD-NAME")
	t.Setenv("BAD_VALUE", string([]byte{0xff}))
	if _, err = CapturePromptState(0, "off", "emacs"); err == nil {
		t.Fatal("invalid UTF-8 value omitted")
	}
}
