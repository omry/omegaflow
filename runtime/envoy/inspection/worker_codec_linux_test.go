//go:build linux

package inspection

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func workerPlans() []protocol.ResolvedInspection {
	return []protocol.ResolvedInspection{
		{InspectionID: "exists", Kind: "file_exists", ResolvedPath: "/work/file"},
		{InspectionID: "hash", Kind: "produces", ResolvedPath: "/work/tree", ProducerID: "producer", OutputID: "output"},
	}
}

func workerResults() []protocol.InspectionResult {
	return []protocol.InspectionResult{
		{InspectionID: "exists", Kind: "file_exists", ResolvedPath: "/canonical/file", PathKind: "file"},
		{InspectionID: "hash", Kind: "produces", ResolvedPath: "/canonical/tree", PathKind: "directory", ProducerID: "producer", OutputID: "output", SHA256: strings.Repeat("a", 64), DigestAlgorithm: "directory-v2"},
	}
}

func TestWorkerReplyContract(t *testing.T) {
	plans, results := workerPlans(), workerResults()
	data, err := encodeWorkerReply(results, nil)
	if err != nil {
		t.Fatal(err)
	}
	got, err := decodeWorkerReply(data, plans)
	if err != nil || len(got) != 2 || got[1] != results[1] {
		t.Fatalf("roundtrip: %#v %v", got, err)
	}
	for _, code := range []string{"inspection-resolution", "inspection-missing", "inspection-type", "inspection-limit", "inspection-unstable", "inspection-read"} {
		t.Run(code, func(t *testing.T) {
			data, err := encodeWorkerReply(results, &Failure{code, "bounded cause"})
			if err != nil {
				t.Fatal(err)
			}
			got, err := decodeWorkerReply(data, plans)
			if got != nil {
				t.Fatalf("partial result on failure: %#v", got)
			}
			wantFailure(t, err, code)
		})
	}
	data, err = encodeWorkerReply([]protocol.InspectionResult{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := decodeWorkerReply(data, nil); err != nil || got == nil || len(got) != 0 {
		t.Fatalf("empty result: %#v %v", got, err)
	}
	if _, err := encodeWorkerReply(nil, errors.New("not a typed inspection failure")); err == nil {
		t.Fatal("untyped failure accepted")
	}
	large := make([]protocol.InspectionResult, protocol.MaxInspections)
	for i := range large {
		large[i] = results[0]
		large[i].ResolvedPath = "/" + strings.Repeat("\x01", protocol.MaxPathBytes-1)
	}
	data, err = encodeWorkerReply(large, nil)
	if err != nil || len(data) > protocol.MaxFrameBytes {
		t.Fatalf("unbounded encode: %d %v", len(data), err)
	}
	if got, err := decodeWorkerReply(data, plans); got != nil {
		t.Fatal("oversized result exposed partial data")
	} else {
		wantFailure(t, err, "inspection-limit")
	}
}

func TestWorkerRejectsMalformedReplies(t *testing.T) {
	plans, results := workerPlans(), workerResults()
	valid, err := encodeWorkerReply(results, nil)
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string][]byte{
		"empty": nil, "tag-only": {'R'}, "unknown-tag": []byte("X[]"),
		"null": []byte("Rnull"), "object": []byte("R{}"), "missing": []byte("R[]"),
		"malformed": []byte("R["), "trailing": append(append([]byte{}, valid...), []byte("[]")...),
		"oversized":           []byte("R" + strings.Repeat(" ", protocol.MaxFrameBytes)),
		"invalid-utf8":        {'F', '[', '"', 0xff, '"', ',', '"', 'x', '"', ']'},
		"failure-arity":       []byte(`F["inspection-read"]`),
		"failure-extra":       []byte(`F["inspection-read","cause","extra"]`),
		"failure-code":        []byte(`F["protocol-error","cause"]`),
		"non-inspection-code": []byte(`F["source-policy","cause"]`),
		"failure-surrogate":   []byte(`F["inspection-read","\ud800"]`),
		"failure-empty":       []byte(`F["inspection-read",""]`),
		"failure-nul":         []byte(`F["inspection-read","a\u0000b"]`),
		"failure-long":        []byte(`F["inspection-read","` + strings.Repeat("a", protocol.MaxMessageBytes+1) + `"]`),
	}
	mutations := map[string]func([]protocol.InspectionResult){
		"id":            func(v []protocol.InspectionResult) { v[0].InspectionID = "other" },
		"kind":          func(v []protocol.InspectionResult) { v[0].Kind = "other" },
		"order":         func(v []protocol.InspectionResult) { v[0], v[1] = v[1], v[0] },
		"producer":      func(v []protocol.InspectionResult) { v[1].ProducerID = "other" },
		"output":        func(v []protocol.InspectionResult) { v[1].OutputID = "other" },
		"digest":        func(v []protocol.InspectionResult) { v[1].SHA256 = "bad" },
		"algorithm":     func(v []protocol.InspectionResult) { v[1].DigestAlgorithm = "directory-v1" },
		"produced-kind": func(v []protocol.InspectionResult) { v[1].PathKind = "other" },
		"unknown-kind":  func(v []protocol.InspectionResult) { v[0].PathKind = "device" },
		"path-empty":    func(v []protocol.InspectionResult) { v[0].ResolvedPath = "" },
		"path-relative": func(v []protocol.InspectionResult) { v[0].ResolvedPath = "relative" },
		"path-parent":   func(v []protocol.InspectionResult) { v[0].ResolvedPath = "/a/../b" },
		"path-nul":      func(v []protocol.InspectionResult) { v[0].ResolvedPath = "/a\x00b" },
		"path-long": func(v []protocol.InspectionResult) {
			v[0].ResolvedPath = "/" + strings.Repeat("a", protocol.MaxPathBytes)
		},
	}
	for name, mutate := range mutations {
		v := append([]protocol.InspectionResult{}, results...)
		mutate(v)
		data, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		cases[name] = append([]byte{'R'}, data...)
	}
	cases["duplicate-field"] = []byte(strings.Replace(string(valid), `"inspection_id":"exists"`, `"inspection_id":"exists","inspection_id":"exists"`, 1))
	cases["unknown-field"] = []byte(strings.Replace(string(valid), `"inspection_id":"exists"`, `"extra":true,"inspection_id":"exists"`, 1))
	cases["escaped-duplicate"] = []byte(strings.Replace(string(valid), `"inspection_id":"exists"`, `"inspection_id":"exists","inspection_\u0069d":"exists"`, 1))
	cases["path-surrogate"] = []byte(strings.Replace(string(valid), `"resolved_path":"/canonical/file"`, `"resolved_path":"/\ud800"`, 1))
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			if got, err := decodeWorkerReply(data, plans); err == nil || got != nil {
				t.Fatalf("accepted malformed result %q: %#v %v", name, got, err)
			}
		})
	}
}
