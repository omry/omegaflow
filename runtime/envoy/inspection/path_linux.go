//go:build linux

package inspection

import (
	"errors"
	"os"
	"reflect"
	"strings"
	"syscall"
	"unicode/utf8"
	"unsafe"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const pathOnly = 0x200000 // Linux O_PATH; observing a FIFO must not open its reader.
type identity struct {
	dev, ino     uint64
	mode         uint32
	size         int64
	mtime, ctime syscall.Timespec
}

func state(f interface{ Stat() (os.FileInfo, error) }) (identity, error) {
	info, err := f.Stat()
	if err != nil {
		return identity{}, err
	}
	s, ok := info.Sys().(*syscall.Stat_t)
	if !ok || s.Ino == 0 || s.Size < 0 || s.Mtim.Nsec < 0 || s.Mtim.Nsec >= 1e9 || s.Ctim.Nsec < 0 || s.Ctim.Nsec >= 1e9 {
		return identity{}, errors.New("stable Linux identity and nanosecond metadata unavailable")
	}
	return identity{s.Dev, s.Ino, s.Mode, s.Size, s.Mtim, s.Ctim}, nil
}
func kind(mode uint32) string {
	switch mode & syscall.S_IFMT {
	case syscall.S_IFREG:
		return "file"
	case syscall.S_IFDIR:
		return "directory"
	default:
		return "other"
	}
}
func openAt(parent *os.File, name string, flags int) (*os.File, error) {
	fd, err := syscall.Openat(int(parent.Fd()), name, flags|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(fd), name), nil
}
func openFailure(err error) error {
	if errors.Is(err, syscall.ENOENT) || errors.Is(err, syscall.ENOTDIR) || errors.Is(err, syscall.ELOOP) {
		return unstable()
	}
	return failure("inspection-read", err)
}
func linkTarget(f *os.File) (string, error) {
	empty, _ := syscall.BytePtrFromString("")
	var data [protocol.MaxPathBytes + 1]byte
	n, _, errno := syscall.Syscall6(syscall.SYS_READLINKAT, f.Fd(), uintptr(unsafe.Pointer(empty)), uintptr(unsafe.Pointer(&data[0])), uintptr(len(data)), 0, 0)
	if errno != 0 {
		return "", failure("inspection-read", errno)
	}
	if n > protocol.MaxPathBytes {
		return "", failure("inspection-limit", errors.New("symlink target exceeds path bound"))
	}
	target := string(data[:n])
	if !utf8.ValidString(target) {
		return "", failure("inspection-resolution", errors.New("symlink target is not UTF-8"))
	}
	return target, nil
}

type component struct {
	name      string
	dev, ino  uint64
	mode      uint32
	link      string
	linkState identity
}
type selection struct {
	original, path, leaf string
	file, parent         *os.File
	state                identity
	chain                []component
	retained             []*os.File
}

func (s *selection) close() error {
	var err error
	for i := len(s.retained) - 1; i >= 0; i-- {
		err = errors.Join(err, s.retained[i].Close())
	}
	return err
}
func resolve(path string) (_ *selection, err error) {
	s := &selection{original: path}
	defer func() {
		if err != nil {
			err = errors.Join(err, s.close())
		}
	}()
	fd, e := syscall.Open("/", pathOnly|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if e != nil {
		return nil, failure("inspection-read", e)
	}
	root := os.NewFile(uintptr(fd), "/")
	s.retained = append(s.retained, root)
	dirs := []*os.File{root}
	names := []string{}
	todo := strings.Split(path, "/")
	links := 0
	s.file, s.parent, s.leaf = root, root, "."
	rootState, e := state(root)
	if e != nil {
		return nil, unstable()
	}
	s.chain = append(s.chain, component{name: "/", dev: rootState.dev, ino: rootState.ino, mode: rootState.mode & syscall.S_IFMT})
	for len(todo) != 0 {
		name := todo[0]
		todo = todo[1:]
		if name == "" || name == "." {
			continue
		}
		if name == ".." {
			if len(dirs) > 1 {
				dirs = dirs[:len(dirs)-1]
				names = names[:len(names)-1]
			}
			s.file, s.parent, s.leaf = dirs[len(dirs)-1], dirs[len(dirs)-1], "."
			continue
		}
		parent := dirs[len(dirs)-1]
		f, e := openAt(parent, name, pathOnly)
		if e != nil {
			code := "inspection-read"
			if errors.Is(e, syscall.ENOENT) {
				code = "inspection-missing"
			}
			if errors.Is(e, syscall.ENOTDIR) || errors.Is(e, syscall.ELOOP) {
				code = "inspection-resolution"
			}
			return nil, failure(code, e)
		}
		s.retained = append(s.retained, f)
		st, e := state(f)
		if e != nil {
			return nil, unstable()
		}
		c := component{name: name, dev: st.dev, ino: st.ino, mode: st.mode & syscall.S_IFMT}
		if c.mode == syscall.S_IFLNK {
			links++
			if links > 40 {
				return nil, failure("inspection-resolution", errors.New("symlink resolution loop"))
			}
			c.link, e = linkTarget(f)
			if e != nil {
				return nil, e
			}
			c.linkState = st
			s.chain = append(s.chain, c)
			if strings.HasPrefix(c.link, "/") {
				dirs = []*os.File{root}
				names = nil
				s.file, s.parent, s.leaf = root, root, "."
			}
			todo = append(strings.Split(c.link, "/"), todo...)
			continue
		}
		s.chain = append(s.chain, c)
		if len(todo) > 0 && c.mode != syscall.S_IFDIR {
			return nil, failure("inspection-resolution", syscall.ENOTDIR)
		}
		s.file, s.parent, s.leaf = f, parent, name
		names = append(names, name)
		if c.mode == syscall.S_IFDIR {
			dirs = append(dirs, f)
		}
	}
	s.path = "/" + strings.Join(names, "/")
	if len(s.path) > protocol.MaxPathBytes {
		return nil, failure("inspection-limit", errors.New("canonical path exceeds wire bound"))
	}
	s.state, e = state(s.file)
	if e != nil {
		return nil, unstable()
	}
	return s, nil
}
func (s *selection) readable() (*os.File, error) {
	f, err := openAt(s.parent, s.leaf, syscall.O_RDONLY|syscall.O_NONBLOCK)
	if err != nil {
		return nil, openFailure(err)
	}
	st, err := state(f)
	if err != nil || st != s.state {
		f.Close()
		return nil, unstable()
	}
	return f, nil
}
func (s *selection) verify() error {
	fresh, err := resolve(s.original)
	if err != nil {
		return unstable()
	}
	defer fresh.close()
	if fresh.path != s.path || fresh.state != s.state || !reflect.DeepEqual(fresh.chain, s.chain) {
		return unstable()
	}
	current, err := state(s.file)
	if err != nil || current != s.state {
		return unstable()
	}
	return nil
}
func sameOpened(f *os.File, expected identity) error {
	actual, err := state(f)
	if err != nil || actual != expected {
		return unstable()
	}
	return nil
}
func invalidName(name string) error {
	if !utf8.ValidString(name) || name == "" || name == "." || name == ".." || strings.ContainsAny(name, "/\x00") {
		return failure("inspection-read", errors.New("directory name is not valid UTF-8 POSIX data"))
	}
	return nil
}
