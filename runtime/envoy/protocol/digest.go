package protocol

import (
	"crypto/sha256"
	"encoding/hex"
	"sort"
	"strings"
	"unicode/utf8"
)

// TreeEntry is one recorded entry below a selected directory, identified by
// its relative POSIX path. Kind selects the payload: a file's exact bytes, a
// symlink's exact target, or nothing for a directory. A special entry
// participates in stability comparison but is omitted from both directory
// digests, matching the native runner.
type TreeEntry struct {
	Path    string
	Kind    string
	Content []byte
	Target  string
}

const (
	TreeEntryFile    = "file"
	TreeEntryDir     = "dir"
	TreeEntryLink    = "link"
	TreeEntrySpecial = "special"
)

// FileDigest is the lowercase SHA-256 of a regular file's exact bytes. It is
// identical to the native runner's file digest.
func FileDigest(content []byte) string {
	digest := sha256.Sum256(content)
	return hex.EncodeToString(digest[:])
}

// DirectoryDigestV2 hashes fixed-size framing rather than delimited text,
// because delimiters are not injective over arbitrary file bytes. Each entry
// contributes a one-byte kind tag — ASCII f for a regular file, d for a
// directory, l for a symlink — the SHA-256 of its path, and the SHA-256 of
// its payload, so every entry occupies the same 65 bytes. The digest is the
// lowercase SHA-256 over the literal directory-v2 tag followed by those
// entries in sorted relative-path order.
func DirectoryDigestV2(entries []TreeEntry) (string, error) {
	ordered, err := orderedEntries(entries)
	if err != nil {
		return "", err
	}
	hasher := sha256.New()
	hasher.Write([]byte(DigestDirectoryV2))
	for _, entry := range ordered {
		var kind byte
		var payload []byte
		switch entry.Kind {
		case TreeEntryFile:
			kind, payload = 'f', entry.Content
		case TreeEntryDir:
			kind, payload = 'd', nil
		case TreeEntryLink:
			kind, payload = 'l', []byte(entry.Target)
		case TreeEntrySpecial:
			continue
		default:
			return "", protocolError("invalid-field", "unsupported tree entry kind")
		}
		pathDigest := sha256.Sum256([]byte(entry.Path))
		payloadDigest := sha256.Sum256(payload)
		hasher.Write([]byte{kind})
		hasher.Write(pathDigest[:])
		hasher.Write(payloadDigest[:])
	}
	return hex.EncodeToString(hasher.Sum(nil)), nil
}

// DirectoryDigestNative is the native runner's delimited directory encoding,
// frozen under the directory tag so a recording made under it stays
// identifiable. It deliberately disagrees with directory-v2 on every
// directory, including the empty one.
func DirectoryDigestNative(entries []TreeEntry) (string, error) {
	ordered, err := orderedEntries(entries)
	if err != nil {
		return "", err
	}
	hasher := sha256.New()
	hasher.Write([]byte("directory\x00"))
	for _, entry := range ordered {
		switch entry.Kind {
		case TreeEntryLink:
			hasher.Write([]byte("link\x00" + entry.Path + "\x00"))
			hasher.Write([]byte(entry.Target + "\x00"))
		case TreeEntryDir:
			hasher.Write([]byte("dir\x00" + entry.Path + "\x00"))
		case TreeEntryFile:
			hasher.Write([]byte("file\x00" + entry.Path + "\x00"))
			hasher.Write(entry.Content)
			hasher.Write([]byte{0})
		case TreeEntrySpecial:
			continue
		default:
			return "", protocolError("invalid-field", "unsupported tree entry kind")
		}
	}
	return hex.EncodeToString(hasher.Sum(nil)), nil
}

func orderedEntries(entries []TreeEntry) ([]TreeEntry, error) {
	ordered := make([]TreeEntry, len(entries))
	copy(ordered, entries)
	sort.Slice(ordered, func(left, right int) bool {
		return ordered[left].Path < ordered[right].Path
	})
	seen := make(map[string]bool, len(ordered))
	for _, entry := range ordered {
		if entry.Path == "" || strings.HasPrefix(entry.Path, "/") || strings.ContainsRune(entry.Path, 0) || !utf8.ValidString(entry.Path) {
			return nil, protocolError("invalid-field", "tree entry paths must be relative NUL-free UTF-8")
		}
		if !utf8.ValidString(entry.Target) || strings.ContainsRune(entry.Target, 0) {
			return nil, protocolError("invalid-field", "symlink targets must be NUL-free UTF-8")
		}
		if seen[entry.Path] {
			return nil, protocolError("invalid-field", "tree entry paths must be unique")
		}
		seen[entry.Path] = true
	}
	return ordered, nil
}
