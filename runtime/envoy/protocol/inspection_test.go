package protocol

import (
	"encoding/json"
	"testing"
)

func TestDeterministicInspectionIDs(t *testing.T) {
	specs := AssignInspectionIDs([]InspectionSpec{
		{Kind: InspectionFileExists, Path: "a"},
		{Kind: InspectionProduces, Path: "b", ProducerID: "p", OutputID: "o"},
	})
	if specs[0].InspectionID != "inspection-1" || specs[1].InspectionID != "inspection-2" {
		t.Fatalf("IDs are assigned deterministically in request order: %+v", specs)
	}
}

func TestInspectionSpecExactness(t *testing.T) {
	// file_exists entries have no other fields.
	var spec InspectionSpec
	err := json.Unmarshal([]byte(`{"inspection_id":"inspection-1","kind":"file_exists","path":"a","producer_id":"p"}`), &spec)
	mustCode(t, err, "unknown-field")
	err = json.Unmarshal([]byte(`{"inspection_id":"inspection-1","kind":"produces","path":"a","producer_id":"p"}`), &spec)
	mustCode(t, err, "missing-field")
	err = json.Unmarshal([]byte(`{"inspection_id":"inspection-1","kind":"probe","path":"a"}`), &spec)
	mustCode(t, err, "invalid-field")
	err = json.Unmarshal([]byte(`{"inspection_id":"inspection-1","kind":"file_exists","path":null}`), &spec)
	mustCode(t, err, "invalid-field")

	// Duplicate IDs and non-exclusive observation are rejected at the
	// execute boundary.
	duplicated := []InspectionSpec{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "a"},
		{InspectionID: "inspection-1", Kind: InspectionFileExists, Path: "b"},
	}
	mustCode(t, validateInspectionSpecs(duplicated, ObservationExclusive), "invalid-field")

	oversized := make([]InspectionSpec, MaxInspectionsPerOperation+1)
	for index := range oversized {
		oversized[index] = InspectionSpec{InspectionID: DeterministicInspectionID(index), Kind: InspectionFileExists, Path: "a"}
	}
	mustCode(t, validateInspectionSpecs(oversized, ObservationExclusive), "invalid-field")
}

func TestInspectionResultExactness(t *testing.T) {
	var result InspectionResult
	// A produces directory result carries its digest algorithm tag.
	err := json.Unmarshal([]byte(`{"inspection_id":"inspection-1","kind":"produces","resolved_path":"/w/build","path_kind":"directory","producer_id":"p","output_id":"o","sha256":"`+FileDigest(nil)+`"}`), &result)
	mustCode(t, err, "missing-field")
	// A file result carries no tag.
	err = json.Unmarshal([]byte(`{"inspection_id":"inspection-1","kind":"produces","resolved_path":"/w/f","path_kind":"file","producer_id":"p","output_id":"o","sha256":"`+FileDigest(nil)+`","digest_algorithm":"directory-v2"}`), &result)
	mustCode(t, err, "unknown-field")
	// A produces path resolves only to a regular file or directory.
	bad := InspectionResult{InspectionID: "inspection-1", Kind: InspectionProduces, ResolvedPath: "/w/dev", PathKind: PathKindOther, ProducerID: "p", OutputID: "o", SHA256: FileDigest(nil)}
	mustCode(t, validateInspectionResult(bad), "invalid-field")
	// Digests are 64 lowercase hexadecimal characters.
	bad = InspectionResult{InspectionID: "inspection-1", Kind: InspectionProduces, ResolvedPath: "/w/f", PathKind: PathKindFile, ProducerID: "p", OutputID: "o", SHA256: "ABC"}
	mustCode(t, validateInspectionResult(bad), "invalid-field")
	// resolved_path is absolute.
	bad = InspectionResult{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "relative", PathKind: PathKindFile}
	mustCode(t, validateInspectionResult(bad), "invalid-field")
}

func TestResolvedInspectionsRoundTrip(t *testing.T) {
	resolved := []ResolvedInspection{
		{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/w/a"},
		{InspectionID: "inspection-2", Kind: InspectionProduces, ResolvedPath: "/w/b", ProducerID: "p", OutputID: "o"},
	}
	encoded, err := EncodeResolvedInspections(resolved)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	decoded, err := DecodeResolvedInspections(encoded)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	reencoded, err := EncodeResolvedInspections(decoded)
	if err != nil || string(reencoded) != string(encoded) {
		t.Fatalf("round trip changed bytes: %v", err)
	}
}

func TestPathResolutionSemantics(t *testing.T) {
	resolver := PathResolver{
		Env: map[string]string{"HOME": "/home/dev", "OUT": "build"},
		CWD: "/workspace/project",
		LookupHome: func(user string) (string, bool) {
			homes := map[string]string{"": "/home/fallback", "deploy": "/srv/deploy"}
			home, found := homes[user]
			return home, found
		},
	}
	cases := map[string]string{
		"$HOME/notes.txt":     "/home/dev/notes.txt",
		"${OUT}/out":          "/workspace/project/build/out",
		"$UNDEFINED/data":     "/workspace/project/$UNDEFINED/data",
		"${UNTERMINATED/data": "/workspace/project/${UNTERMINATED/data",
		"${}/data":            "/workspace/project/${}/data",
		"~/media":             "/home/dev/media",
		"~deploy/releases":    "/srv/deploy/releases",
		"~nobody/releases":    "/workspace/project/~nobody/releases",
		"logs/latest.txt":     "/workspace/project/logs/latest.txt",
		"/var/log/app.log":    "/var/log/app.log",
	}
	for configured, expected := range cases {
		resolved, err := resolver.Resolve(configured)
		if err != nil {
			t.Fatalf("%s: %v", configured, err)
		}
		if resolved != expected {
			t.Fatalf("%s resolved to %s, expected %s", configured, resolved, expected)
		}
	}

	// Without HOME the workload's user database supplies the current
	// identity's home.
	withoutHome := resolver
	withoutHome.Env = map[string]string{}
	resolved, err := withoutHome.Resolve("~/media")
	if err != nil || resolved != "/home/fallback/media" {
		t.Fatalf("~ without HOME resolved to %s (%v)", resolved, err)
	}

	// Resolution rejects unbounded or unsafe configured paths.
	if _, err := resolver.Resolve(""); err == nil {
		t.Fatal("empty configured path must fail")
	}
	if _, err := resolver.Resolve("a\x00b"); err == nil {
		t.Fatal("NUL in configured path must fail")
	}
}

func TestResolvedInspectionValidation(t *testing.T) {
	base := ResolvedInspection{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/w/a"}
	relative := base
	relative.ResolvedPath = "relative"
	mustCode(t, validateResolvedInspection(relative), "invalid-field")
	produces := base
	produces.Kind = InspectionProduces
	mustCode(t, validateResolvedInspection(produces), "invalid-field")
	extra := base
	extra.ProducerID = "p"
	mustCode(t, validateResolvedInspection(extra), "invalid-field")
	unknown := base
	unknown.Kind = "probe"
	mustCode(t, validateResolvedInspection(unknown), "invalid-field")
	if message := validateResolvedInspection(unknown).Error(); message == "" {
		t.Fatal("protocol errors render a stable code and message")
	}
}

func TestResolvedInspectionBoundsAreEnforced(t *testing.T) {
	oversized := make([]ResolvedInspection, MaxInspectionsPerOperation+1)
	for index := range oversized {
		oversized[index] = ResolvedInspection{InspectionID: DeterministicInspectionID(index), Kind: InspectionFileExists, ResolvedPath: "/w/a"}
	}
	if _, err := EncodeResolvedInspections(oversized); err == nil {
		t.Fatal("oversized resolved plans must fail to encode")
	}
	encoded := []byte("[")
	for index := range oversized {
		if index > 0 {
			encoded = append(encoded, ',')
		}
		entry, err := oversized[index].MarshalJSON()
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		encoded = append(encoded, entry...)
	}
	encoded = append(encoded, ']')
	_, err := DecodeResolvedInspections(encoded)
	mustCode(t, err, "invalid-field")

	duplicated, err := EncodeResolvedInspections([]ResolvedInspection{{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/w/a"}})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	twice := append([]byte("["), duplicated[1:len(duplicated)-1]...)
	twice = append(twice, ',')
	twice = append(twice, duplicated[1:len(duplicated)-1]...)
	twice = append(twice, ']')
	_, err = DecodeResolvedInspections(twice)
	mustCode(t, err, "invalid-field")
}

func TestNullInspectionArraysAreRejected(t *testing.T) {
	if _, err := DecodeInspectionSpecs([]byte("null")); err == nil {
		t.Fatal("null INSPECTIONS_JSON must fail")
	}
	if _, err := DecodeResolvedInspections([]byte("null")); err == nil {
		t.Fatal("null RESOLVED_INSPECTIONS_JSON must fail")
	}
	base := AwshExecute{OperationID: "op-1", ExecutionShape: ExecutionPTY, Observation: ObservationShared, InspectionsJSON: "null", Source: "true"}
	if _, err := EncodeAwshRequest(base); err == nil {
		t.Fatal("execute with null inspections must fail")
	}
}

func TestRootHomeStaysAbsolute(t *testing.T) {
	resolver := PathResolver{
		Env: map[string]string{"HOME": "/"},
		CWD: "/workspace",
		LookupHome: func(user string) (string, bool) {
			if user == "root" {
				return "/", true
			}
			return "", false
		},
	}
	for configured, expected := range map[string]string{
		"~":           "/",
		"~/etc/motd":  "/etc/motd",
		"~root":       "/",
		"~root/state": "/state",
	} {
		resolved, err := resolver.Resolve(configured)
		if err != nil || resolved != expected {
			t.Fatalf("%s resolved to %s (%v), expected %s", configured, resolved, err, expected)
		}
	}
}

func TestPublicResolvedPathsMustBeCanonical(t *testing.T) {
	for _, path := range []string{"/workspace/../etc/file", "/workspace//file", "/workspace/./file", "/workspace/"} {
		bad := InspectionResult{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: path, PathKind: PathKindFile}
		mustCode(t, validateInspectionResult(bad), "invalid-field")
	}
	root := InspectionResult{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/", PathKind: PathKindDirectory}
	if err := validateInspectionResult(root); err != nil {
		t.Fatalf("the root directory is canonical: %v", err)
	}
	// The private resolved plan is lexical pre-canonicalization evidence
	// and deliberately keeps such paths.
	if err := validateResolvedInspection(ResolvedInspection{InspectionID: "inspection-1", Kind: InspectionFileExists, ResolvedPath: "/workspace/../etc/file"}); err != nil {
		t.Fatalf("private resolved plans stay lexical: %v", err)
	}
}
