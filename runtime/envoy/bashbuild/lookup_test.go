package bashbuild

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func TestEmptyQualifiedTableRejectsBothActors(t *testing.T) {
	path := filepath.Join(t.TempDir(), "synthetic-bash")
	if err := os.WriteFile(path, []byte("synthetic"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, lookup := range []func(string, string, Target, map[string]string) (Entry, error){LookupEnvoy, LookupAwsh} {
		if _, err := lookup(path, "unknown", Target{}, nil); err == nil {
			t.Fatal("unqualified entry accepted")
		}
	}
}

func TestSpecialBuildDoesNotBlock(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fifo")
	if err := syscall.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LookupAwsh(path, "unknown", Target{}, nil); err == nil {
		t.Fatal("nonregular entry accepted")
	}
}
