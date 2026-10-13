// Package runtimebundle verifies the fixed read-only workload runtime payload.
package runtimebundle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const Root = "/omegaflow-runtime"

type File struct {
	Path       string `json:"path"`
	Size       int64  `json:"size"`
	Executable bool   `json:"executable"`
	SHA256     string `json:"sha256"`
}

type Manifest struct {
	Schema         string `json:"schema"`
	Version        string `json:"version"`
	SourceRevision string `json:"source_revision"`
	GoToolchain    string `json:"go_toolchain"`
	Target         struct {
		OS   string `json:"os"`
		Arch string `json:"arch"`
	} `json:"target"`
	Schemas struct {
		Telemetry string `json:"telemetry"`
		Private   string `json:"private"`
		Helper    string `json:"helper"`
	} `json:"schemas"`
	Files []File `json:"files"`
}

type Inputs struct {
	BashSHA256 string
	Target     bashbuild.Target
	Adapter    map[string]string
}

// Load has no production path override. Each actor verifies the mounted bytes
// independently before invoking its own qualified-build consumer.
func Load(ctx context.Context) (Inputs, error) {
	var result Inputs
	files, err := verify(ctx, Root)
	if err != nil {
		return result, err
	}
	result.Adapter = map[string]string{}
	for path, digest := range files {
		if path != Root+"/bin/envoy" {
			result.Adapter[path] = digest
		}
	}
	result.BashSHA256, _, err = hashFile(ctx, "/bin/bash", false)
	if err != nil {
		return result, err
	}
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return result, err
	}
	identity := map[string]string{}
	for _, line := range strings.Split(string(data), "\n") {
		key, value, ok := strings.Cut(line, "=")
		if ok && (key == "ID" || key == "VERSION_ID") {
			if _, exists := identity[key]; exists {
				return result, fmt.Errorf("duplicate target identity")
			}
			identity[key] = strings.Trim(value, "\"'")
		}
	}
	if identity["ID"] == "" || identity["VERSION_ID"] == "" {
		return result, fmt.Errorf("missing workload target identity")
	}
	result.Target = bashbuild.Target{OS: runtime.GOOS, Arch: runtime.GOARCH,
		OSReleaseID: identity["ID"], VersionID: identity["VERSION_ID"]}
	return result, nil
}

func verify(ctx context.Context, root string) (map[string]string, error) {
	f, err := os.OpenFile(filepath.Join(root, "manifest.json"), os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0222 != 0 || info.Size() > 4<<20 {
		return nil, fmt.Errorf("invalid runtime manifest file: %v", err)
	}
	var m Manifest
	d := json.NewDecoder(io.LimitReader(f, (4<<20)+1))
	d.DisallowUnknownFields()
	if err := d.Decode(&m); err != nil {
		return nil, err
	}
	var extra any
	if d.Decode(&extra) != io.EOF || m.Schema != "omegaflow-runtime-manifest-v1" || m.Version == "" ||
		!hexSize(m.SourceRevision, 20) || m.GoToolchain != runtime.Version() ||
		m.Target.OS != runtime.GOOS || m.Target.Arch != runtime.GOARCH ||
		m.Schemas.Telemetry != protocol.Schema || m.Schemas.Private != "awsh-v1" || m.Schemas.Helper != "awsh-helper-v1" {
		return nil, fmt.Errorf("runtime manifest identity mismatch")
	}
	wanted := map[string]File{}
	for _, file := range m.Files {
		path := file.Path
		if path == "" || filepath.ToSlash(filepath.Clean(path)) != path || filepath.IsAbs(path) ||
			!(strings.HasPrefix(path, "bin/") || strings.HasPrefix(path, "etc/") ||
				strings.HasPrefix(path, "share/terminfo/") || strings.HasPrefix(path, "lib/locale/")) ||
			file.Size < 0 || !hexSize(file.SHA256, 32) {
			return nil, fmt.Errorf("invalid runtime payload entry")
		}
		if _, exists := wanted[path]; exists {
			return nil, fmt.Errorf("duplicate runtime payload path")
		}
		wanted[path] = file
	}
	for _, path := range []string{"bin/envoy", "bin/awsh", "etc/awsh-bashrc", "etc/inputrc", "etc/bash-builds.json"} {
		file, ok := wanted[path]
		if !ok || (strings.HasPrefix(path, "bin/") && !file.Executable) || (path == "etc/inputrc" && file.Size != 0) {
			return nil, fmt.Errorf("missing or invalid fixed runtime payload: %s", path)
		}
	}
	observed := map[string]string{}
	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0222 != 0 {
			return fmt.Errorf("untrusted runtime payload: %v", err)
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if relative == "manifest.json" {
			return nil
		}
		expected, ok := wanted[relative]
		if !ok || !info.Mode().IsRegular() || info.Size() != expected.Size ||
			(info.Mode().Perm()&0111 != 0) != expected.Executable {
			return fmt.Errorf("runtime payload inventory mismatch: %s", relative)
		}
		digest, _, err := hashFile(ctx, path, true)
		if err != nil || digest != expected.SHA256 {
			return fmt.Errorf("runtime payload digest mismatch: %s: %v", relative, err)
		}
		observed[Root+"/"+relative] = digest
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("runtime payload verification failed: %w", err)
	}
	if len(observed) != len(wanted) {
		return nil, fmt.Errorf("runtime payload verification incomplete")
	}
	return observed, nil
}

func hexSize(value string, size int) bool {
	data, err := hex.DecodeString(value)
	return err == nil && len(data) == size && hex.EncodeToString(data) == value
}

func hashFile(ctx context.Context, path string, noFollow bool) (string, int64, error) {
	flags := os.O_RDONLY | syscall.O_NONBLOCK
	if noFollow {
		flags |= syscall.O_NOFOLLOW
	}
	f, err := os.OpenFile(path, flags, 0)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return "", 0, fmt.Errorf("nonregular runtime input: %v", err)
	}
	h := sha256.New()
	var buffer [32768]byte
	var size int64
	for {
		if err := ctx.Err(); err != nil {
			return "", size, err
		}
		n, err := f.Read(buffer[:])
		h.Write(buffer[:n])
		size += int64(n)
		if err == io.EOF {
			return hex.EncodeToString(h.Sum(nil)), size, nil
		}
		if err != nil {
			return "", size, err
		}
	}
}
