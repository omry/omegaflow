package protocol

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPrivateInspectionPlanPreservesLexicalPaths(t *testing.T) {
	for _, path := range []string{"/work/link/../artifact", "/work/./artifact", "/work//artifact", "/work/artifact/", "//work/artifact", "/work/é", "/" + strings.Repeat("x", MaxPathBytes-1)} {
		for _, kind := range []string{"file_exists", "produces"} {
			entry := ResolvedInspection{InspectionID: "i", Kind: kind, ResolvedPath: path}
			if kind == "produces" {
				entry.ProducerID, entry.OutputID = "producer", "output"
			}
			m := PrivateCompleted{OperationID: "op", Status: 7, PhysicalCWD: "/work", Inspections: []ResolvedInspection{entry}}
			encoded, err := EncodePrivate(m, AwshToEnvoy)
			if err != nil {
				t.Fatalf("encode %q: %v", path, err)
			}
			decoded, err := DecodePrivate(encoded, AwshToEnvoy)
			if err != nil || decoded.(*PrivateCompleted).Inspections[0] != entry {
				t.Fatalf("path changed: %#v, %v", decoded, err)
			}
		}
	}
	for _, path := range []string{"", "relative", "/x\x00y", "/\xff", "/" + strings.Repeat("x", MaxPathBytes)} {
		m := PrivateCompleted{OperationID: "op", Status: 0, PhysicalCWD: "/work", Inspections: []ResolvedInspection{{InspectionID: "i", Kind: "file_exists", ResolvedPath: path}}}
		if _, err := EncodePrivate(m, AwshToEnvoy); err == nil {
			t.Fatalf("accepted invalid path %q", path)
		}
	}
}

func TestPrivateInspectionPlanRetainsSymlinkParentTarget(t *testing.T) {
	root := t.TempDir()
	work, nested := filepath.Join(root, "work"), filepath.Join(root, "target", "nested")
	for _, dir := range []string{work, nested} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(nested, filepath.Join(work, "link")); err != nil {
		t.Fatal(err)
	}
	for path, content := range map[string]string{filepath.Join(root, "target", "artifact"): "native", filepath.Join(work, "artifact"): "cleaned"} {
		if err := os.WriteFile(path, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	lexical := work + "/link/../artifact"
	m := PrivateCompleted{OperationID: "op", Status: 0, PhysicalCWD: work, Inspections: []ResolvedInspection{{InspectionID: "i", Kind: "file_exists", ResolvedPath: lexical}}}
	frame, err := EncodePrivate(m, AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodePrivate(frame, AwshToEnvoy)
	if err != nil {
		t.Fatal(err)
	}
	selected := decoded.(*PrivateCompleted).Inspections[0].ResolvedPath
	native, err := os.ReadFile(selected)
	if err != nil {
		t.Fatal(err)
	}
	cleaned, err := os.ReadFile(filepath.Clean(selected))
	if err != nil {
		t.Fatal(err)
	}
	if string(native) != "native" || string(cleaned) != "cleaned" {
		t.Fatalf("selected=%s native=%q cleaned=%q", selected, native, cleaned)
	}
}
