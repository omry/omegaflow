//go:build linux

package inspection

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"unicode/utf8"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func selected(t *testing.T, path string) *selection {
	t.Helper()
	s, err := resolve(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := s.close(); err != nil {
			t.Error(err)
		}
	})
	return s
}
func TestCanonicalSelection(t *testing.T) {
	root := t.TempDir()
	mkdir(t, filepath.Join(root, "real", "child"))
	write(t, filepath.Join(root, "real", "artifact"), []byte("selected"))
	write(t, filepath.Join(root, "artifact"), []byte("wrong lexical cleanup"))
	link(t, "real/child", filepath.Join(root, "alias"))
	link(t, "/", filepath.Join(root, "real", "root-link"))
	for _, path := range []string{root + "/alias/../artifact", root + "//real/./child/../artifact", root + "/real/child/../../real/artifact"} {
		r := inspectOne(t, produces(path))
		want := sha256.Sum256([]byte("selected"))
		canonical, err := filepath.EvalSymlinks(path)
		if err != nil {
			t.Fatal(err)
		}
		if r.ResolvedPath != canonical || r.SHA256 != hex.EncodeToString(want[:]) {
			t.Fatal(path, r)
		}
	}
	s := selected(t, root+"/real/root-link")
	if s.path != "/" || kind(s.state.mode) != "directory" {
		t.Fatal(s.path, s.state)
	}
	for _, suffix := range []string{"/real/artifact/", "/real/artifact/.", "/real/artifact/.."} {
		_, err := resolve(root + suffix)
		wantFailure(t, err, "inspection-resolution")
	}
	link(t, "absent", filepath.Join(root, "broken"))
	_, err := resolve(root + "/broken")
	wantFailure(t, err, "inspection-missing")
	link(t, "loop", filepath.Join(root, "loop"))
	_, err = resolve(root + "/loop")
	wantFailure(t, err, "inspection-resolution")
}
func TestRetainedPathIdentity(t *testing.T) {
	for _, name := range []string{"selected-replacement", "parent-replacement", "symlink-target", "symlink-replacement-same-target", "content", "metadata"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			dir := filepath.Join(root, "dir")
			mkdir(t, dir)
			path := filepath.Join(dir, "file")
			write(t, path, []byte("before"))
			alias := filepath.Join(root, "alias")
			link(t, "dir/file", alias)
			s := selected(t, alias)
			switch name {
			case "selected-replacement":
				if err := os.Rename(path, path+".old"); err != nil {
					t.Fatal(err)
				}
				write(t, path, []byte("before"))
			case "parent-replacement":
				if err := os.Rename(dir, dir+".old"); err != nil {
					t.Fatal(err)
				}
				mkdir(t, dir)
				write(t, path, []byte("before"))
			case "symlink-target":
				write(t, filepath.Join(dir, "other"), []byte("before"))
				if err := os.Remove(alias); err != nil {
					t.Fatal(err)
				}
				link(t, "dir/other", alias)
			case "symlink-replacement-same-target":
				if err := os.Remove(alias); err != nil {
					t.Fatal(err)
				}
				link(t, "dir/file", alias)
			case "content":
				write(t, path, []byte("after!"))
			case "metadata":
				if err := os.Chmod(path, 0640); err != nil {
					t.Fatal(err)
				}
			}
			wantFailure(t, s.verify(), "inspection-unstable")
		})
	}
	root := t.TempDir()
	file := filepath.Join(root, "file")
	write(t, file, []byte("same"))
	s := selected(t, file)
	if err := s.verify(); err != nil {
		t.Fatal(err)
	}
}

func TestStablePermissionFailure(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("mode-000 access rejection requires an unprivileged process")
	}
	for _, name := range []string{"selected-file", "selected-directory", "descendant-file", "descendant-directory"} {
		t.Run(name, func(t *testing.T) {
			plans, denied := openFailurePlans(t, name)
			if err := os.Chmod(denied, 0); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				if err := os.Chmod(denied, 0700); err != nil {
					t.Error(err)
				}
			})
			results, err := Run(plans)
			wantFailure(t, err, "inspection-read")
			if results != nil {
				t.Fatal("partial results after stable permission failure", results)
			}
		})
	}
}

func TestReadableOpenReplacement(t *testing.T) {
	for _, name := range []string{"removed", "symlink"} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "file")
			write(t, path, []byte("data"))
			s := selected(t, path)
			if err := os.Remove(path); err != nil {
				t.Fatal(err)
			}
			if name == "symlink" {
				link(t, "absent", path)
			}
			file, err := s.readable()
			wantFailure(t, err, "inspection-unstable")
			if file != nil {
				file.Close()
				t.Fatal("replacement was opened")
			}
		})
	}
}

type changedReader struct {
	*os.File
	change  func()
	changed bool
}

func (r *changedReader) Read(b []byte) (int, error) {
	n, err := r.File.Read(b)
	if n > 0 && !r.changed {
		r.changed = true
		r.change()
	}
	return n, err
}

type syntheticReader struct {
	io.Reader
	info    os.FileInfo
	statErr error
}

func (r syntheticReader) Stat() (os.FileInfo, error) { return r.info, r.statErr }

type modifiedInfo struct {
	os.FileInfo
	raw any
}

func (i modifiedInfo) Sys() any { return i.raw }

func TestStableFileReads(t *testing.T) {
	for _, name := range []string{"bytes", "append", "truncate", "metadata"} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "file")
			body := bytes.Repeat([]byte("initial-data"), 10000)
			write(t, path, body)
			f, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			before, err := state(f)
			if err != nil {
				t.Fatal(err)
			}
			reader := &changedReader{File: f, change: func() {
				switch name {
				case "bytes":
					write(t, path, bytes.Repeat([]byte("changed-data"), 10000))
				case "append":
					out, e := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
					if e != nil {
						t.Fatal(e)
					}
					_, e = out.WriteString("extra")
					if e != nil {
						t.Fatal(e)
					}
					out.Close()
				case "truncate":
					if err := os.Truncate(path, 1); err != nil {
						t.Fatal(err)
					}
				case "metadata":
					if err := os.Chmod(path, 0640); err != nil {
						t.Fatal(err)
					}
				}
			}}
			digest, err := hashFile(reader, before, &budget{bytes: maxBytes})
			if !reader.changed {
				t.Fatal("mutation did not cross read")
			}
			wantFailure(t, err, "inspection-unstable")
			if digest != "" {
				t.Fatal(digest)
			}
		})
	}
	path := filepath.Join(t.TempDir(), "file")
	write(t, path, []byte("abc"))
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	before, err := state(f)
	if err != nil {
		t.Fatal(err)
	}
	info, err := f.Stat()
	if err != nil {
		t.Fatal(err)
	}
	for _, text := range []string{"ab", "abcd"} {
		digest, err := hashFile(syntheticReader{Reader: strings.NewReader(text), info: info}, before, &budget{bytes: maxBytes})
		wantFailure(t, err, "inspection-unstable")
		if digest != "" {
			t.Fatal(digest)
		}
	}
	stale := before
	stale.ino++
	_, err = hashFile(f, stale, &budget{bytes: maxBytes})
	wantFailure(t, err, "inspection-unstable")
	_, err = hashFile(syntheticReader{Reader: strings.NewReader("abc"), info: info, statErr: errors.New("stat failed")}, before, &budget{bytes: maxBytes})
	wantFailure(t, err, "inspection-unstable")
	raw := *info.Sys().(*syscall.Stat_t)
	for _, value := range []any{nil, &syscall.Stat_t{}, func() any { s := raw; s.Mtim.Nsec = 1e9; return &s }(), func() any { s := raw; s.Ctim.Nsec = -1; return &s }()} {
		_, err := state(syntheticReader{info: modifiedInfo{FileInfo: info, raw: value}})
		if err == nil {
			t.Fatal("unsupported metadata accepted", value)
		}
		_, err = hashFile(syntheticReader{Reader: strings.NewReader("abc"), info: modifiedInfo{FileInfo: info, raw: value}}, before, &budget{bytes: maxBytes})
		wantFailure(t, err, "inspection-unstable")
	}
}
func TestSnapshotMutationBoundaries(t *testing.T) {
	for _, name := range []string{"insert", "remove", "rename", "content", "metadata", "link", "special", "directory"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			mkdir(t, root+"/dir")
			write(t, root+"/file", []byte("same"))
			link(t, "file", root+"/link")
			if err := syscall.Mkfifo(root+"/pipe", 0600); err != nil {
				t.Fatal(err)
			}
			s := selected(t, root)
			b := &budget{maxEntries, maxEntries, maxBytes}
			first, err := snapshot(s.file, b, false)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := hashTree(s.file, first, b); err != nil {
				t.Fatal(err)
			}
			switch name {
			case "insert":
				write(t, root+"/new", []byte("x"))
			case "remove":
				if err := os.Remove(root + "/file"); err != nil {
					t.Fatal(err)
				}
			case "rename":
				if err := os.Rename(root+"/file", root+"/moved"); err != nil {
					t.Fatal(err)
				}
			case "content":
				write(t, root+"/file", []byte("diff"))
			case "metadata":
				if err := os.Chmod(root+"/file", 0640); err != nil {
					t.Fatal(err)
				}
			case "link":
				if err := os.Remove(root + "/link"); err != nil {
					t.Fatal(err)
				}
				link(t, "dir", root+"/link")
			case "special":
				if err := os.Remove(root + "/pipe"); err != nil {
					t.Fatal(err)
				}
				if err := syscall.Mkfifo(root+"/pipe", 0600); err != nil {
					t.Fatal(err)
				}
			case "directory":
				if err := os.Chmod(root+"/dir", 0750); err != nil {
					t.Fatal(err)
				}
			}
			second, err := snapshot(s.file, b, true)
			if err != nil {
				wantFailure(t, err, "inspection-unstable")
				return
			}
			if reflect.DeepEqual(first, second) {
				t.Fatal("mutation absent from complete snapshots", name)
			}
		})
	}
}
func TestFileChangeBeforeOpenAndNoFollow(t *testing.T) {
	for _, name := range []string{"bytes", "replacement", "directory-link"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			mkdir(t, root+"/dir")
			write(t, root+"/dir/file", []byte("old"))
			s := selected(t, root)
			b := &budget{maxEntries, maxEntries, maxBytes}
			first, err := snapshot(s.file, b, false)
			if err != nil {
				t.Fatal(err)
			}
			switch name {
			case "bytes":
				write(t, root+"/dir/file", []byte("new"))
			case "replacement":
				if err := os.Rename(root+"/dir/file", root+"/old"); err != nil {
					t.Fatal(err)
				}
				write(t, root+"/dir/file", []byte("old"))
			case "directory-link":
				if err := os.Rename(root+"/dir", root+"/old-dir"); err != nil {
					t.Fatal(err)
				}
				link(t, "old-dir", root+"/dir")
			}
			digest, err := hashTree(s.file, first, b)
			wantFailure(t, err, "inspection-unstable")
			if digest != "" {
				t.Fatal(digest)
			}
		})
	}
}
func TestInvalidNamesAndLinkBounds(t *testing.T) {
	root := t.TempDir()
	write(t, root+"/\xff", []byte("x"))
	_, err := Run([]protocol.ResolvedInspection{produces(root)})
	wantFailure(t, err, "inspection-read")
	root = t.TempDir()
	link(t, "\xff", root+"/bad")
	_, err = Run([]protocol.ResolvedInspection{produces(root)})
	wantFailure(t, err, "inspection-resolution")
	for _, name := range []string{"", ".", "..", "a/b", "a\x00b", "\xff"} {
		if err := invalidName(name); err == nil {
			t.Fatal(name)
		}
	}
}

func TestDescriptorDisposal(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "file")
	write(t, file, []byte("data"))
	link(t, "loop", root+"/loop")
	inspectOne(t, produces(root)) // Warm ordinary runtime bookkeeping before census.
	count := func() int {
		entries, err := os.ReadDir("/proc/self/fd")
		if err != nil {
			t.Fatal(err)
		}
		return len(entries)
	}
	before := count()
	directory := produces(root)
	directory.InspectionID = "directory"
	for i := 0; i < 50; i++ {
		if _, err := Run([]protocol.ResolvedInspection{produces(file), directory}); err != nil {
			t.Fatal(err)
		}
		for _, path := range []string{root + "/missing", root + "/loop", file + "/.."} {
			if _, err := Run([]protocol.ResolvedInspection{produces(path)}); err == nil {
				t.Fatal("invalid selection accepted", path)
			}
		}
	}
	if after := count(); after != before {
		t.Fatalf("inspection leaked descriptors: before%d after%d", before, after)
	}
}

func TestCanonicalPathBudget(t *testing.T) {
	root := t.TempDir()
	rootFile, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	files := []*os.File{rootFile}
	t.Cleanup(func() {
		for _, f := range files {
			f.Close()
		}
	})
	current := rootFile
	prefix, extension := []string{}, []string{}
	descend := func(name string) {
		if err := syscall.Mkdirat(int(current.Fd()), name, 0700); err != nil {
			t.Fatal(err)
		}
		next, err := openAt(current, name, pathOnly|syscall.O_DIRECTORY)
		if err != nil {
			t.Fatal(err)
		}
		files = append(files, next)
		current = next
	}
	for i := 0; i < 15; i++ {
		name := "p" + strings.Repeat("x", 179)
		prefix = append(prefix, name)
		descend(name)
	}
	prefixPath := root + "/" + strings.Join(prefix, "/")
	for i := 0; i < 8; i++ {
		name := "e" + strings.Repeat("x", 179)
		extension = append(extension, name)
		descend(name)
	}
	fd, err := syscall.Openat(int(current.Fd()), "file", syscall.O_CREAT|syscall.O_WRONLY|syscall.O_CLOEXEC, 0600)
	if err != nil {
		t.Fatal(err)
	}
	syscall.Close(fd)
	link(t, prefixPath, root+"/start")
	link(t, strings.Join(extension, "/"), prefixPath+"/expand")
	if len(prefixPath+"/"+strings.Join(extension, "/")+"/file") <= 4096 {
		t.Fatal("test canonical path did not cross the limit")
	}
	results, err := Run([]protocol.ResolvedInspection{produces(root + "/start/expand/file")})
	wantFailure(t, err, "inspection-limit")
	if results != nil {
		t.Fatal(results)
	}
}

type prefixErrorReader struct {
	*strings.Reader
	err error
}

func (r prefixErrorReader) Read(p []byte) (int, error) {
	if r.Len() == 0 {
		return 0, r.err
	}
	return r.Reader.Read(p)
}
func TestReadFailuresAndBoundedDiagnostic(t *testing.T) {
	for _, body := range []string{"abc", ""} {
		path := filepath.Join(t.TempDir(), "file")
		write(t, path, []byte(body))
		f, err := os.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		info, err := f.Stat()
		if err != nil {
			t.Fatal(err)
		}
		expected, err := state(f)
		if err != nil {
			t.Fatal(err)
		}
		reader := prefixErrorReader{Reader: strings.NewReader(""), err: errors.New("injected read failure")}
		digest, err := hashFile(syntheticReader{Reader: reader, info: info}, expected, &budget{bytes: maxBytes})
		wantFailure(t, err, "inspection-read")
		if digest != "" {
			t.Fatal(digest)
		}
	}
	err := failure("inspection-read", errors.New("x"+strings.Repeat("é", 3000)))
	wantFailure(t, err, "inspection-read")
	var e *Failure
	if !errors.As(err, &e) || !utf8.ValidString(e.Message) || len(e.Message) > 4096 {
		t.Fatal(err)
	}
}
