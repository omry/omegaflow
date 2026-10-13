package runtimebundle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func fixture(t *testing.T) (string, Manifest) {
	t.Helper()
	root := t.TempDir()
	t.Cleanup(func() {
		filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
			if err == nil && entry.IsDir() {
				os.Chmod(path, 0700)
			}
			return nil
		})
	})
	m := Manifest{Schema: "omegaflow-runtime-manifest-v1", Version: "test", SourceRevision: strings.Repeat("a", 40), GoToolchain: runtime.Version()}
	m.Target.OS, m.Target.Arch = runtime.GOOS, runtime.GOARCH
	m.Schemas.Telemetry, m.Schemas.Private, m.Schemas.Helper = "omegaflow-envoy-telemetry-v1", "awsh-v1", "awsh-helper-v1"
	for _, path := range []string{"bin/envoy", "bin/awsh", "etc/awsh-bashrc", "etc/inputrc", "etc/bash-builds.json", "share/terminfo/x/xterm-256color", "lib/locale/C.utf8/LC_CTYPE"} {
		data := []byte("fixed:" + path)
		if path == "etc/inputrc" {
			data = nil
		}
		file := filepath.Join(root, path)
		if err := os.MkdirAll(filepath.Dir(file), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(file, data, 0644); err != nil {
			t.Fatal(err)
		}
		executable := strings.HasPrefix(path, "bin/")
		mode := os.FileMode(0444)
		if executable {
			mode = 0555
		}
		if err := os.Chmod(file, mode); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(data)
		m.Files = append(m.Files, File{Path: path, Size: int64(len(data)), Executable: executable, SHA256: hex.EncodeToString(h[:])})
	}
	writeManifest(t, root, m)
	if err := filepath.WalkDir(root, func(path string, e os.DirEntry, err error) error {
		if err == nil && e.IsDir() {
			return os.Chmod(path, 0555)
		}
		return err
	}); err != nil {
		t.Fatal(err)
	}
	return root, m
}

func writeManifest(t *testing.T, root string, m Manifest) {
	t.Helper()
	os.Chmod(root, 0755)
	path := filepath.Join(root, "manifest.json")
	os.Chmod(path, 0644)
	data, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0444); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0444); err != nil {
		t.Fatal(err)
	}
	os.Chmod(root, 0555)
}

func TestExactManifestPayload(t *testing.T) {
	root, m := fixture(t)
	got, err := verify(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != len(m.Files) {
		t.Fatal("incomplete inventory")
	}
	for _, file := range m.Files {
		if got[Root+"/"+file.Path] != file.SHA256 {
			t.Fatal("input identity mismatch")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := verify(ctx, root); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled verification lost its caller cause", err)
	}
}

func TestInvalidManifestIdentities(t *testing.T) {
	for _, kind := range []string{"schema", "toolchain", "target", "source", "protocol", "duplicate", "escape", "digest", "missing", "mode", "inputrc"} {
		t.Run(kind, func(t *testing.T) {
			root, m := fixture(t)
			switch kind {
			case "schema":
				m.Schema = "other"
			case "toolchain":
				m.GoToolchain = "go-other"
			case "target":
				m.Target.Arch = "other"
			case "source":
				m.SourceRevision = "unknown"
			case "protocol":
				m.Schemas.Helper = "other"
			case "duplicate":
				m.Files = append(m.Files, m.Files[0])
			case "escape":
				m.Files[0].Path = "../envoy"
			case "digest":
				m.Files[0].SHA256 = strings.Repeat("A", 64)
			case "missing":
				m.Files = m.Files[1:]
			case "mode":
				m.Files[0].Executable = false
			case "inputrc":
				m.Files[3].Size = 1
			}
			writeManifest(t, root, m)
			if _, err := verify(context.Background(), root); err == nil {
				t.Fatal("invalid manifest passed")
			}
		})
	}
}

func TestPayloadTamperingAndUnsafeFiles(t *testing.T) {
	for _, kind := range []string{"bytes", "missing", "extra", "symlink", "writable", "executable"} {
		t.Run(kind, func(t *testing.T) {
			root, _ := fixture(t)
			file := filepath.Join(root, "bin/awsh")
			os.Chmod(filepath.Dir(file), 0755)
			switch kind {
			case "bytes":
				os.Chmod(file, 0755)
				os.WriteFile(file, []byte("different bytes"), 0555)
				os.Chmod(file, 0555)
			case "missing":
				os.Remove(file)
			case "extra":
				os.WriteFile(filepath.Join(root, "bin/extra"), []byte("extra"), 0444)
			case "symlink":
				os.Remove(file)
				os.Symlink("/bin/sh", file)
			case "writable":
				os.Chmod(file, 0755)
			case "executable":
				os.Chmod(file, 0444)
			}
			os.Chmod(filepath.Dir(file), 0555)
			if _, err := verify(context.Background(), root); err == nil {
				t.Fatal("unsafe payload passed")
			}
		})
	}
}
