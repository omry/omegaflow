//go:build linux

package inspection

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// These functions are called only by explicitly retained Go test overlays.
// Normal production files have no hook, environment selector or test mode.
func testSnapshotBoundary(path string) {
	var err error
	switch os.Getenv("OMEGAFLOW_INSPECTION_MUTATION") {
	case "insert":
		err = os.WriteFile(path+"/new", []byte("new"), 0600)
	case "remove":
		err = os.Remove(path + "/file")
	case "rename":
		err = os.Rename(path+"/file", path+"/renamed")
	case "content":
		err = os.WriteFile(path+"/file", []byte("else"), 0600)
	case "metadata":
		err = os.Chmod(path+"/file", 0640)
	case "directory":
		err = os.Chmod(path+"/dir", 0750)
	case "link":
		err = os.Remove(path + "/link")
		if err == nil {
			err = os.Symlink("dir", path+"/link")
		}
	case "special":
		err = os.Remove(path + "/pipe")
		if err == nil {
			err = syscall.Mkfifo(path+"/pipe", 0600)
		}
	default:
		panic("unknown snapshot mutation")
	}
	if err != nil {
		panic(err)
	}
}
func testComponentBoundary(path string) {
	if err := os.Remove(path); err != nil {
		panic(err)
	}
	if err := os.Symlink("real/file", path); err != nil {
		panic(err)
	}
}
func testReadBoundary(file readableFile) {
	f, ok := file.(*os.File)
	if !ok {
		panic("read fault requires actual descriptor")
	}
	if err := syscall.Fchmod(int(f.Fd()), 0640); err != nil {
		panic(err)
	}
}
func TestInspectionBoundaryFaults(t *testing.T) {
	mode := os.Getenv("OMEGAFLOW_INSPECTION_BOUNDARY")
	if mode == "" {
		t.Skip("explicit retained test overlay required")
	}
	cases := []string{mode}
	if mode == "snapshot" {
		cases = []string{"insert", "remove", "rename", "content", "metadata", "directory", "link", "special"}
	}
	if mode != "snapshot" && mode != "component" && mode != "read" {
		t.Fatal("unknown boundary", mode)
	}
	for _, name := range cases {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			path := root
			if mode == "snapshot" {
				mkdir(t, root+"/dir")
				write(t, root+"/file", []byte("same"))
				link(t, "file", root+"/link")
				if err := syscall.Mkfifo(root+"/pipe", 0600); err != nil {
					t.Fatal(err)
				}
				t.Setenv("OMEGAFLOW_INSPECTION_MUTATION", name)
			} else if mode == "component" {
				mkdir(t, root+"/real")
				write(t, root+"/real/file", []byte("same"))
				path = filepath.Join(root, "alias")
				link(t, "real/file", path)
			} else {
				path = filepath.Join(root, "file")
				write(t, path, []byte("same"))
			}
			results, err := Run([]protocol.ResolvedInspection{produces(path)})
			wantFailure(t, err, "inspection-unstable")
			if results != nil {
				t.Fatalf("partial inspection accepted: %#v", results)
			}
			t.Log(fmt.Sprintf("OBSERVED INSPECTION MUTATION %s/%s", mode, name))
		})
	}
}

func TestReadlinkFailureIsTyped(t *testing.T) {
	if os.Getenv("OMEGAFLOW_INSPECTION_BOUNDARY") != "readlink-error" {
		t.Skip("explicit retained readlink-error overlay required")
	}
	for _, name := range []string{"selected", "descendant"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			write(t, root+"/file", []byte("data"))
			link(t, "file", root+"/link")
			path := root
			if name == "selected" {
				path += "/link"
			}
			first := protocol.ResolvedInspection{InspectionID: "first", Kind: "file_exists", ResolvedPath: root + "/file"}
			results, err := Run([]protocol.ResolvedInspection{first, produces(path)})
			wantFailure(t, err, "inspection-read")
			if results != nil {
				t.Fatal("partial results after readlink failure", results)
			}
			t.Logf("OBSERVED READLINK FAILURE %s", name)
		})
	}
}
