//go:build linux

package inspection

import (
	"errors"
	"io"
	"os"
	"sort"
	"strings"
	"syscall"
)

// Parent indices avoid retaining an entire deep path prefix for every entry.
// Neither snapshot retains one descriptor per descendant.
type entry struct {
	parent int
	name   string
	state  identity
	link   string
}

func relative(entries []entry, index int) string {
	names := []string{}
	for index > 0 {
		names = append(names, entries[index].name)
		index = entries[index].parent
	}
	for i, j := 0, len(names)-1; i < j; i, j = i+1, j-1 {
		names[i], names[j] = names[j], names[i]
	}
	return strings.Join(names, "/")
}
func snapshot(root *os.File, b *budget, second bool) ([]entry, error) {
	st, err := state(root)
	if err != nil {
		return nil, unstable()
	}
	entries := []entry{{parent: -1, state: st}}
	var walk func(*os.File, int) error
	walk = func(dir *os.File, parent int) error {
		reader, err := openAt(dir, ".", syscall.O_RDONLY|syscall.O_DIRECTORY)
		if err != nil {
			return unstable()
		}
		var names []string
		for {
			batch, e := reader.Readdirnames(128)
			for _, name := range batch {
				if err := invalidName(name); err != nil {
					reader.Close()
					return err
				}
				if err := b.entry(second); err != nil {
					reader.Close()
					return err
				}
				names = append(names, name)
			}
			if e == io.EOF {
				break
			}
			if e != nil {
				reader.Close()
				return failure("inspection-read", e)
			}
		}
		if err := reader.Close(); err != nil {
			return failure("inspection-read", err)
		}
		sort.Strings(names)
		for _, name := range names {
			f, err := openAt(dir, name, pathOnly)
			if err != nil {
				return unstable()
			}
			st, err := state(f)
			if err != nil {
				f.Close()
				return unstable()
			}
			item := entry{parent: parent, name: name, state: st}
			if st.mode&syscall.S_IFMT == syscall.S_IFLNK {
				item.link, err = linkTarget(f)
			}
			if err == nil {
				index := len(entries)
				entries = append(entries, item)
				if st.mode&syscall.S_IFMT == syscall.S_IFDIR {
					err = walk(f, index)
				}
			}
			closeErr := f.Close()
			if err != nil {
				return err
			}
			if closeErr != nil {
				return failure("inspection-read", closeErr)
			}
		}
		return sameOpened(dir, entries[parent].state)
	}
	if err := walk(root, 0); err != nil {
		return nil, err
	}
	return entries, nil
}
func openEntry(root *os.File, entries []entry, index int) (*os.File, func() error, error) {
	var indices []int
	for i := index; i > 0; i = entries[i].parent {
		indices = append(indices, i)
	}
	retained := []*os.File{}
	closeChain := func() error {
		var e error
		for i := len(retained) - 1; i >= 0; i-- {
			e = errors.Join(e, retained[i].Close())
		}
		return e
	}
	parent := root
	if err := sameOpened(root, entries[0].state); err != nil {
		return nil, closeChain, err
	}
	for i := len(indices) - 1; i >= 0; i-- {
		node := entries[indices[i]]
		flags := pathOnly
		if i == 0 {
			flags = syscall.O_RDONLY | syscall.O_NONBLOCK
		}
		f, err := openAt(parent, node.name, flags)
		if err != nil {
			closeChain()
			return nil, closeChain, unstable()
		}
		retained = append(retained, f)
		if err := sameOpened(f, node.state); err != nil {
			closeChain()
			return nil, closeChain, err
		}
		parent = f
	}
	return parent, closeChain, nil
}
