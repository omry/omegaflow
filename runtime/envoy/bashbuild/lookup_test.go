package bashbuild

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

func TestEmptyQualifiedTableRejectsBothActors(t *testing.T) {
	path := filepath.Join(t.TempDir(), "synthetic-bash")
	if err := os.WriteFile(path, []byte("synthetic"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, lookup := range []func(string, string, Target, map[string]string) (Entry, error){LookupEnvoy, LookupAwsh} {
		if _, err := lookup(path, "unknown", Target{}, nil); err == nil {
			t.Fatal("unqualified entry accepted")
		}
	}
}

func TestSpecialBuildDoesNotBlock(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fifo")
	if err := syscall.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LookupAwsh(path, "unknown", Target{}, nil); err == nil {
		t.Fatal("nonregular entry accepted")
	}
}

func TestManifestTableIntake(t *testing.T) {
	data := []byte(`{"version":1,"builds":{}}`)
	digest := sha256.Sum256(data)
	expected := hex.EncodeToString(digest[:])
	name := filepath.Join(t.TempDir(), "table.json")
	if err := os.WriteFile(name, data, 0600); err != nil {
		t.Fatal(err)
	}
	got, err := readTable(name, expected)
	if err != nil || string(got) != string(data) {
		t.Fatalf("regular table: %q %v", got, err)
	}
	for _, bad := range []string{"", "A" + expected[1:], strings.Repeat("f", 64)} {
		if _, err := readTable(name, bad); err == nil {
			t.Fatal("accepted missing or wrong manifest digest")
		}
	}
	link := name + "-link"
	if err := os.Symlink(name, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readTable(link, expected); err == nil {
		t.Fatal("accepted symlink table")
	}
	fifo := name + "-fifo"
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readTable(fifo, expected); err == nil {
		t.Fatal("accepted FIFO table")
	}
	if _, err := readTable(filepath.Dir(name), expected); err == nil {
		t.Fatal("accepted directory table")
	}
	if _, err := readTable(name+"-missing", expected); err == nil {
		t.Fatal("accepted absent table")
	}
	big := bytes.Repeat([]byte(" "), tableLimit+1)
	if err := os.WriteFile(name, big, 0600); err != nil {
		t.Fatal(err)
	}
	digest = sha256.Sum256(big)
	if _, err := readTable(name, hex.EncodeToString(digest[:])); err == nil {
		t.Fatal("accepted overbound table")
	}
}

func TestStrictTableEncoding(t *testing.T) {
	for _, data := range [][]byte{
		[]byte(`{"version":1,"builds":{}} {}`),
		[]byte(`{"version":1,"v\u0065rsion":1,"builds":{}}`),
		[]byte(`{"version":1,"builds":{},"unknown":true}`),
		[]byte(`{"version":1,"builds":{},"x":"\ud800"}`),
		[]byte(`{"version":1,"builds":{},"x":"\udc00"}`),
		[]byte(`{"version":1.0,"builds":{}}`),
		[]byte(`{"version":true,"builds":{}}`),
		[]byte(`{"version":1,"builds":null}`),
		[]byte(`{"version":1,"builds":{"a":null}}`),
		[]byte(`{"version":1,"builds":{"a":{"candidate":{},"qualification":null}}}`),
		{0xff}, bytes.Repeat([]byte(" "), tableLimit+1),
	} {
		if _, err := parseTable(data); err == nil {
			t.Fatalf("accepted invalid table %q", data[:min(len(data), 128)])
		}
	}
	valid := []byte(" \n{\"version\":1,\"builds\":{}}\n ")
	if _, err := parseTable(valid); err != nil {
		t.Fatal(err)
	}
}

func TestTableCanonicalUTF8(t *testing.T) {
	value := map[string]any{"z": "<>&\u2028\u2029🙂", "a": "\x00\b\f\n\r\t\"\\"}
	want := "{\"a\":\"\\u0000\\b\\f\\n\\r\\t\\\"\\\\\",\"z\":\"<>&\u2028\u2029🙂\"}"
	if got := tableCanonical(value); got != want {
		t.Fatalf("canonical %q want %q", got, want)
	}
}

func FuzzTableParse(f *testing.F) {
	f.Add([]byte(`{"version":1,"builds":{}}`))
	f.Add([]byte(`{"version":1,"builds":{"a":{"candidate":{},"qualification":{"inputs":{"x":{}},"adapter_inputs":{"x":{}}}}}}`))
	f.Fuzz(func(t *testing.T, data []byte) { _, _ = parseTable(data) })
}
