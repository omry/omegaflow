//go:build linux

package inspection

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func write(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
}
func mkdir(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0700); err != nil {
		t.Fatal(err)
	}
}
func link(t *testing.T, target, path string) {
	t.Helper()
	if err := os.Symlink(target, path); err != nil {
		t.Fatal(err)
	}
}
func produces(path string) protocol.ResolvedInspection {
	return protocol.ResolvedInspection{InspectionID: "result", Kind: "produces", ResolvedPath: path, ProducerID: "producer", OutputID: "output"}
}
func wantFailure(t *testing.T, err error, code string) {
	t.Helper()
	var e *Failure
	if !errors.As(err, &e) || e.Code != code {
		t.Fatalf("failure %v; want %s", err, code)
	}
	if e.Message == "" || len(e.Message) > protocol.MaxMessageBytes {
		t.Fatalf("unbounded failure: %#v", e)
	}
	if text := err.Error(); !strings.Contains(text, code) || !strings.Contains(text, e.Message) {
		t.Fatalf("diagnostic lost its code or cause: %q", text)
	}
}
func inspectOne(t *testing.T, p protocol.ResolvedInspection) protocol.InspectionResult {
	t.Helper()
	result, err := Run([]protocol.ResolvedInspection{p})
	if err != nil {
		t.Fatal(err)
	}
	if len(result) != 1 {
		t.Fatal(result)
	}
	return result[0]
}
func nativeHash(t *testing.T, path string) (string, string) {
	t.Helper()
	// Execute the actual existing native runner function, without importing the
	// terminal/session module or launching any workload probe command.
	script := `import hashlib,json,os,sys
from pathlib import Path
text=Path(sys.argv[1]).read_text()
start=text.index("def path_hash(path):")
end=text.index("\n\nrecords = []",start)
exec(compile(text[start:end],sys.argv[1],"exec"))
print(json.dumps(path_hash(Path(sys.argv[2]))))`
	out, err := exec.Command("python3", "-c", script, "../../../src/omegaflow/terminal_capture.py", path).CombinedOutput()
	if err != nil {
		t.Fatalf("native compatibility: %v: %s", err, out)
	}
	var pair []string
	if err := json.Unmarshal(out, &pair); err != nil || len(pair) != 2 {
		t.Fatalf("native result %q %v", out, err)
	}
	return pair[0], pair[1]
}
func TestFrozenInspectionDigests(t *testing.T) {
	file, err := os.Open("../../../tests/fixtures/envoy-protocol-v1/inspection.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	scan := bufio.NewScanner(file)
	scan.Buffer(make([]byte, 4096), protocol.MaxFrameBytes)
	for scan.Scan() {
		// Fixture payload_hex uses an explicit tag; keep the corpus source unchanged.
		var raw struct {
			ID        string `json:"id"`
			Algorithm string `json:"algorithm"`
			SHA256    string `json:"sha256"`
			Entries   []struct {
				Kind       string `json:"kind"`
				Path       string `json:"path"`
				PayloadHex string `json:"payload_hex"`
			} `json:"entries"`
			HashInputHex string   `json:"hash_input_hex"`
			Omitted      []string `json:"omitted_special_entries"`
		}
		if err := json.Unmarshal(scan.Bytes(), &raw); err != nil {
			t.Fatal(err)
		}
		t.Run(raw.ID, func(t *testing.T) {
			root := t.TempDir()
			selected := root
			if raw.Algorithm == "file" {
				selected = filepath.Join(root, "file")
				data, e := hex.DecodeString(raw.HashInputHex)
				if e != nil {
					t.Fatal(e)
				}
				write(t, selected, data)
			} else {
				for _, entry := range raw.Entries {
					path := filepath.Join(root, filepath.FromSlash(entry.Path))
					data, e := hex.DecodeString(entry.PayloadHex)
					if e != nil {
						t.Fatal(e)
					}
					switch entry.Kind {
					case "d":
						mkdir(t, path)
					case "f":
						write(t, path, data)
					case "l":
						link(t, string(data), path)
					default:
						t.Fatal(entry.Kind)
					}
				}
				for _, name := range raw.Omitted {
					if err := syscall.Mkfifo(filepath.Join(root, name), 0600); err != nil {
						t.Fatal(err)
					}
				}
			}
			tag, sum := nativeHash(t, selected)
			if raw.Algorithm == "directory" {
				if tag != "directory" || sum != raw.SHA256 {
					t.Fatalf("native %s:%s", tag, sum)
				}
				return
			}
			result := inspectOne(t, produces(selected))
			if result.SHA256 != raw.SHA256 {
				t.Fatalf("digest %s want %s", result.SHA256, raw.SHA256)
			}
			if raw.Algorithm == "file" {
				if tag != "file" || sum != result.SHA256 || result.DigestAlgorithm != "" {
					t.Fatal(result, tag, sum)
				}
			} else if result.DigestAlgorithm != "directory-v2" || tag != "directory" || sum == result.SHA256 {
				t.Fatal(result, tag, sum)
			}
		})
	}
	if err := scan.Err(); err != nil {
		t.Fatal(err)
	}
}
func TestNativeFileCompatibility(t *testing.T) {
	for _, data := range [][]byte{nil, []byte("abc\n"), {0, 255, 128, '\r', '\n'}, []byte(strings.Repeat("data\x00", 100000))} {
		t.Run(fmt.Sprint(len(data)), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "artifact")
			write(t, path, data)
			kind, native := nativeHash(t, path)
			r := inspectOne(t, produces(path))
			if kind != "file" || native != r.SHA256 {
				t.Fatal(kind, native, r)
			}
		})
	}
}
func TestDirectoryFramingAndOrder(t *testing.T) {
	a, b := t.TempDir(), t.TempDir()
	write(t, filepath.Join(a, "a"), []byte("x\x00file\x00b\x00y"))
	write(t, filepath.Join(b, "a"), []byte("x"))
	write(t, filepath.Join(b, "b"), []byte("y"))
	_, nativeA := nativeHash(t, a)
	_, nativeB := nativeHash(t, b)
	if nativeA != nativeB {
		t.Fatal("control does not reproduce old delimiter ambiguity")
	}
	if inspectOne(t, produces(a)).SHA256 == inspectOne(t, produces(b)).SHA256 {
		t.Fatal("directory-v2 framing collision")
	}
	root := t.TempDir()
	mkdir(t, filepath.Join(root, "a"))
	write(t, filepath.Join(root, "a-thing"), []byte("first"))
	write(t, filepath.Join(root, "a", "child"), []byte("second"))
	h := sha256.New()
	h.Write([]byte("directory-v2"))
	for _, e := range []struct {
		tag        byte
		path, body string
	}{{'d', "a", ""}, {'f', "a-thing", "first"}, {'f', "a/child", "second"}} {
		p := sha256.Sum256([]byte(e.path))
		b := sha256.Sum256([]byte(e.body))
		h.Write([]byte{e.tag})
		h.Write(p[:])
		h.Write(b[:])
	}
	if got := inspectOne(t, produces(root)).SHA256; got != hex.EncodeToString(h.Sum(nil)) {
		t.Fatal("not global relative POSIX-path order", got)
	}
}
func TestKindsCorrelationAndNoPartialResults(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "file")
	write(t, file, []byte("x"))
	fifo := filepath.Join(root, "fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(root, "socket")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	for _, tc := range []struct{ path, kind string }{{file, "file"}, {root, "directory"}, {fifo, "other"}, {socket, "other"}} {
		r := inspectOne(t, protocol.ResolvedInspection{InspectionID: "exists", Kind: "file_exists", ResolvedPath: tc.path})
		if r.PathKind != tc.kind || r.SHA256 != "" || r.ProducerID != "" {
			t.Fatal(r)
		}
	}
	for _, path := range []string{fifo, socket} {
		result, err := Run([]protocol.ResolvedInspection{produces(path)})
		wantFailure(t, err, "inspection-type")
		if result != nil {
			t.Fatal(result)
		}
	}
	plans := []protocol.ResolvedInspection{{InspectionID: "first", Kind: "file_exists", ResolvedPath: file}, produces(filepath.Join(root, "missing"))}
	result, err := Run(plans)
	wantFailure(t, err, "inspection-missing")
	if result != nil {
		t.Fatal("partial result", result)
	}
	plans[1] = produces(file)
	result, err = Run(plans)
	if err != nil || len(result) != 2 || result[0].InspectionID != "first" || result[1].InspectionID != "result" || result[1].ProducerID != "producer" {
		t.Fatal(result, err)
	}
	empty, err := Run([]protocol.ResolvedInspection{})
	if err != nil || empty == nil || len(empty) != 0 {
		t.Fatal(empty, err)
	}
}
func TestInvalidPlan(t *testing.T) {
	for _, p := range []protocol.ResolvedInspection{{InspectionID: "x", Kind: "file_exists", ResolvedPath: "relative"}, {InspectionID: "x", Kind: "file_exists", ResolvedPath: "/\xff"}, {InspectionID: "x", Kind: "file_exists", ResolvedPath: "/x\x00y"}, {InspectionID: "x", Kind: "produces", ResolvedPath: "/"}, {InspectionID: "x", Kind: "future", ResolvedPath: "/"}} {
		result, err := Run([]protocol.ResolvedInspection{p})
		wantFailure(t, err, "inspection-resolution")
		if result != nil {
			t.Fatal(result)
		}
	}
	p := protocol.ResolvedInspection{InspectionID: "x", Kind: "file_exists", ResolvedPath: "/"}
	_, err := Run([]protocol.ResolvedInspection{p, p})
	wantFailure(t, err, "inspection-resolution")
}
func TestOperationBudgets(t *testing.T) {
	b := budget{maxEntries, maxEntries, maxBytes}
	for i := 0; i < maxEntries; i++ {
		if err := b.entry(false); err != nil {
			t.Fatal(err)
		}
	}
	wantFailure(t, b.entry(false), "inspection-limit")
	if b.second != maxEntries {
		t.Fatal("snapshots share counter")
	}
	for i := 0; i < maxEntries; i++ {
		if err := b.entry(true); err != nil {
			t.Fatal(err)
		}
	}
	wantFailure(t, b.entry(true), "inspection-limit")
	b.bytes = maxBytes
	if err := b.read(maxBytes); err != nil {
		t.Fatal(err)
	}
	wantFailure(t, b.read(1), "inspection-limit")
	b.bytes = maxBytes
	wantFailure(t, b.read(maxBytes+1), "inspection-limit")
	wantFailure(t, b.read(-1), "inspection-limit")
	root := t.TempDir()
	file := filepath.Join(root, "f")
	write(t, file, []byte("1234"))
	p := produces(file)
	q := p
	q.InspectionID = "second"
	for _, limit := range []int64{7, 8} {
		results, err := run([]protocol.ResolvedInspection{p, q}, &budget{100, 100, limit})
		if limit == 7 {
			wantFailure(t, err, "inspection-limit")
			if results != nil {
				t.Fatal(results)
			}
		} else if err != nil || len(results) != 2 {
			t.Fatal(results, err)
		}
	}
	for _, c := range []struct {
		first, second int
		pass          bool
	}{{1, 1, true}, {0, 1, false}, {1, 0, false}} {
		results, err := run([]protocol.ResolvedInspection{produces(root)}, &budget{c.first, c.second, 100})
		if c.pass {
			if err != nil {
				t.Fatal(err)
			}
		} else {
			wantFailure(t, err, "inspection-limit")
			if results != nil {
				t.Fatal(results)
			}
		}
	}
	directory := produces(root)
	other := directory
	other.InspectionID = "other-directory"
	for _, limits := range []struct{ first, second int }{{1, 2}, {2, 1}, {2, 2}} {
		results, err := run([]protocol.ResolvedInspection{directory, other}, &budget{limits.first, limits.second, 100})
		if limits.first == 2 && limits.second == 2 {
			if err != nil || len(results) != 2 {
				t.Fatal(results, err)
			}
		} else {
			wantFailure(t, err, "inspection-limit")
			if results != nil {
				t.Fatal("partial results after aggregate entry limit", results)
			}
		}
	}
}
