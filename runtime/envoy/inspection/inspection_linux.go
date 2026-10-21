//go:build linux

// Package inspection implements the bounded workload filesystem contract.
// The session actor must invoke it only through its supervised worker.
package inspection

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"reflect"
	"sort"
	"syscall"
	"unicode/utf8"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const maxEntries = 100_000
const maxBytes int64 = 16 << 30

// Failure uses only existing public operation-failure codes. It contains no
// file contents and never accompanies a partially accepted result array.
type Failure struct{ Code, Message string }

func (e *Failure) Error() string { return e.Code + ": " + e.Message }
func failure(code string, err error) error {
	s := err.Error()
	if len(s) > protocol.MaxMessageBytes {
		s = s[:protocol.MaxMessageBytes]
		for !utf8.ValidString(s) {
			s = s[:len(s)-1]
		}
	}
	return &Failure{code, s}
}
func unstable() error {
	return failure("inspection-unstable", errors.New("filesystem identity or source state changed"))
}

type budget struct {
	first, second int
	bytes         int64
}

func (b *budget) entry(second bool) error {
	n := &b.first
	if second {
		n = &b.second
	}
	if *n == 0 {
		return failure("inspection-limit", errors.New("directory entry limit exceeded"))
	}
	*n--
	return nil
}
func (b *budget) read(n int64) error {
	if n < 0 || n > b.bytes {
		return failure("inspection-limit", errors.New("regular-file byte limit exceeded"))
	}
	b.bytes -= n
	return nil
}

// Run inspects the complete validated plan in request order, with one shared
// budget. B7.2 supplies process isolation; this library creates no timer.
func Run(plans []protocol.ResolvedInspection) ([]protocol.InspectionResult, error) {
	return run(plans, &budget{maxEntries, maxEntries, maxBytes})
}
func run(plans []protocol.ResolvedInspection, b *budget) ([]protocol.InspectionResult, error) {
	if _, err := protocol.EncodePrivate(protocol.PrivateCompleted{OperationID: "inspection", PhysicalCWD: "/", Inspections: plans}, protocol.AwshToEnvoy); err != nil {
		return nil, failure("inspection-resolution", err)
	}
	results := make([]protocol.InspectionResult, 0, len(plans))
	for _, p := range plans {
		result, err := inspect(p, b)
		if err != nil {
			return nil, err
		}
		results = append(results, result)
	}
	return results, nil
}
func inspect(p protocol.ResolvedInspection, b *budget) (result protocol.InspectionResult, err error) {
	selected, err := resolve(p.ResolvedPath)
	if err != nil {
		return result, err
	}
	defer func() {
		if e := selected.close(); err == nil && e != nil {
			err = failure("inspection-read", e)
		}
	}()
	result = protocol.InspectionResult{InspectionID: p.InspectionID, Kind: p.Kind, ResolvedPath: selected.path, PathKind: kind(selected.state.mode)}
	if p.Kind == "file_exists" {
		return result, nil
	}
	if result.PathKind == "other" {
		return result, failure("inspection-type", errors.New("produced path is not a regular file or directory"))
	}
	var digest string
	if result.PathKind == "file" {
		f, e := selected.readable()
		if e != nil {
			return result, e
		}
		digest, err = hashFile(f, selected.state, b)
		closeErr := f.Close()
		if err == nil && closeErr != nil {
			err = failure("inspection-read", closeErr)
		}
	} else {
		var first, second []entry
		first, err = snapshot(selected.file, b, false)
		if err == nil {
			digest, err = hashTree(selected.file, first, b)
		}
		if err == nil {
			second, err = snapshot(selected.file, b, true)
		}
		if err == nil && !reflect.DeepEqual(first, second) {
			err = unstable()
		}
	}
	if err == nil {
		err = selected.verify()
	}
	if err != nil {
		return result, err
	}
	result.ProducerID, result.OutputID, result.SHA256 = p.ProducerID, p.OutputID, digest
	if result.PathKind == "directory" {
		result.DigestAlgorithm = "directory-v2"
	}
	return result, nil
}

type readableFile interface {
	io.Reader
	Stat() (os.FileInfo, error)
}

func hashFile(f readableFile, expected identity, b *budget) (string, error) {
	before, err := state(f)
	if err != nil || before != expected {
		return "", unstable()
	}
	if err := b.read(expected.size); err != nil {
		return "", err
	}
	h := sha256.New()
	_, readErr := io.CopyN(h, f, expected.size)
	var extra [1]byte
	n, eof := f.Read(extra[:])
	after, err := state(f)
	if err != nil || after != before || errors.Is(readErr, io.EOF) || errors.Is(readErr, io.ErrUnexpectedEOF) || n != 0 {
		return "", unstable()
	}
	if readErr != nil {
		return "", failure("inspection-read", readErr)
	}
	if eof != io.EOF {
		if eof == nil {
			return "", unstable()
		}
		return "", failure("inspection-read", eof)
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func hashTree(root *os.File, entries []entry, b *budget) (string, error) {
	order := make([]int, len(entries)-1)
	for i := range order {
		order[i] = i + 1
	}
	sort.Slice(order, func(i, j int) bool { return relative(entries, order[i]) < relative(entries, order[j]) })
	h := sha256.New()
	h.Write([]byte("directory-v2"))
	for _, i := range order {
		e := entries[i]
		tag := byte(0)
		payload := sha256.Sum256(nil)
		switch e.state.mode & syscall.S_IFMT {
		case syscall.S_IFREG:
			tag = 'f'
			f, closeChain, err := openEntry(root, entries, i)
			if err != nil {
				return "", err
			}
			sum, hashErr := hashFile(f, e.state, b)
			closeErr := closeChain()
			if hashErr != nil {
				return "", hashErr
			}
			if closeErr != nil {
				return "", failure("inspection-read", closeErr)
			}
			raw, err := hex.DecodeString(sum)
			if err != nil {
				return "", failure("inspection-read", err)
			}
			copy(payload[:], raw)
		case syscall.S_IFDIR:
			tag = 'd'
		case syscall.S_IFLNK:
			tag = 'l'
			payload = sha256.Sum256([]byte(e.link))
		default:
			continue
		}
		pathHash := sha256.Sum256([]byte(relative(entries, i)))
		h.Write([]byte{tag})
		h.Write(pathHash[:])
		h.Write(payload[:])
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
