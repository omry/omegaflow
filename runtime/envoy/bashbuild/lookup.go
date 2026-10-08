// Package bashbuild contains only qualified entries from the canonical table.
package bashbuild

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"syscall"
)

type Candidate struct {
	Release string `json:"release"`
	Target  Target `json:"target"`
	ArtifactSHA256   string `json:"artifact_sha256"`
	ExecutableSHA256 string `json:"executable_sha256"`
	DefinitionSHA256 string `json:"definition_sha256"`
	LockSHA256       string `json:"lock_sha256"`
	ResolvedPath     string `json:"resolved_path"`
	SystemRC         string `json:"system_rc"`
}

// Target is the complete manifest-verified target tuple for a qualified build.
type Target struct {
	OS          string `json:"os"`
	Arch        string `json:"arch"`
	OSReleaseID string `json:"os_release_id"`
	VersionID   string `json:"version_id"`
}

// Qualification retains the complete measured fields and proof identities.
// Table admission is performed by the generation tool before compilation.
type Entry struct {
	Candidate     Candidate       `json:"candidate"`
	Qualification json.RawMessage `json:"qualification"`
}

// inputs must be current manifest-verified adapter and trusted-input hashes.
func lookup(path, expected string, target Target, inputs map[string]string) (Entry, error) {
	var empty Entry
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return empty, err
	}
	resolved, err = filepath.Abs(resolved)
	if err != nil {
		return empty, err
	}
	f, err := os.OpenFile(resolved, os.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return empty, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return empty, err
	}
	if !info.Mode().IsRegular() {
		return empty, fmt.Errorf("nonregular Bash")
	}
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return empty, err
	}
	observed := hex.EncodeToString(h.Sum(nil))
	var table struct {
		Version int              `json:"version"`
		Builds  map[string]Entry `json:"builds"`
	}
	if err := json.Unmarshal([]byte(tableJSON), &table); err != nil {
		return empty, err
	}
	entry, ok := table.Builds[observed]
	if table.Version != 1 || !ok || observed != expected || entry.Candidate.ExecutableSHA256 != observed || len(entry.Qualification) == 0 || string(entry.Qualification) == "null" {
		return empty, fmt.Errorf("unknown or mismatched Bash build")
	}
	build := entry.Candidate
	if build.Target != target || build.ResolvedPath != resolved {
		return empty, fmt.Errorf("wrong Bash target or path")
	}
	if build.SystemRC != "none" {
		if _, err := os.Lstat(build.SystemRC); !os.IsNotExist(err) {
			return empty, fmt.Errorf("present or unreadable system Bash rc")
		}
	}
	var proof struct {
		Inputs map[string]string `json:"adapter_inputs"`
	}
	if err := json.Unmarshal(entry.Qualification, &proof); err != nil {
		return empty, err
	}
	if len(proof.Inputs) == 0 || !reflect.DeepEqual(proof.Inputs, inputs) {
		return empty, fmt.Errorf("adapter or trusted inputs require requalification")
	}
	return entry, nil
}
