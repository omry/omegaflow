//go:build linux

package launch

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/inspection"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// The existing completion harness signals USR2 while the helper is blocked.
// Only its final state selects these real filesystem paths; the earlier OUT,
// HOME and cwd are deliberately wrong. No production probe operation is added.
func liveInspectionSource() string {
	return `B7_BASE=/tmp/omegaflow-b7-inspection-$BASHPID
export B7_BASE
/bin/mkdir -p "$B7_BASE/real/child" "$B7_BASE/home"
builtin printf 'abc\n' > "$B7_BASE/real/artifact"
builtin printf 'home' > "$B7_BASE/home/artifact"
builtin printf 'literal' > "$B7_BASE/"'${UNDEFINED}'
builtin printf 'malformed' > "$B7_BASE/"'${OUT:-bad}'
builtin printf 'unresolved-home' > "$B7_BASE/~omegaflow_missing_user_b7"
builtin printf 'utf8' > "$B7_BASE/é"
/bin/ln -sfn real/child "$B7_BASE/link"
builtin trap 'cd "$B7_BASE"; export OUT=link/../artifact HOME="$B7_BASE/home"' USR2
export OUT=wrong HOME=/wrong
cd /tmp
builtin printf 'LIVE_INSPECTION_READY\n'`
}
func liveInspectionPlans() []protocol.Inspection {
	return []protocol.Inspection{
		{InspectionID: "produced-file", Kind: "produces", Path: "$OUT", ProducerID: "live", OutputID: "file"},
		{InspectionID: "produced-directory", Kind: "produces", Path: "real", ProducerID: "live", OutputID: "directory"},
		{InspectionID: "home", Kind: "file_exists", Path: "~/artifact"},
		{InspectionID: "named-user", Kind: "file_exists", Path: "~root"},
		{InspectionID: "undefined", Kind: "file_exists", Path: "${UNDEFINED}"},
		{InspectionID: "malformed", Kind: "file_exists", Path: "${OUT:-bad}"},
		{InspectionID: "unresolved-home", Kind: "file_exists", Path: "~omegaflow_missing_user_b7"},
		{InspectionID: "utf8", Kind: "file_exists", Path: "é"},
	}
}
func verifyLiveInspections(t *testing.T, completed *protocol.PrivateCompleted, iteration int) {
	t.Helper()
	root := completed.PhysicalCWD
	if !strings.HasPrefix(root, "/tmp/omegaflow-b7-inspection-") {
		t.Fatalf("final cwd not accepted: %q", root)
	}
	results, err := inspection.Run(completed.Inspections)
	if err != nil {
		t.Fatalf("actual final-state inspection: %v; plans %#v", err, completed.Inspections)
	}
	plans := liveInspectionPlans()
	if len(results) != len(plans) {
		t.Fatal("missing live results", results)
	}
	paths := []string{root + "/real/artifact", root + "/real", root + "/home/artifact", "/root", root + "/${UNDEFINED}", root + "/${OUT:-bad}", root + "/~omegaflow_missing_user_b7", root + "/é"}
	for i, result := range results {
		if result.InspectionID != plans[i].InspectionID || result.Kind != plans[i].Kind || result.ResolvedPath != paths[i] {
			t.Fatalf("live result %d: %#v", i, result)
		}
		if i > 1 && (result.SHA256 != "" || result.ProducerID != "" || result.OutputID != "") {
			t.Fatal("existence result carries digest or producer", result)
		}
	}
	if results[0].PathKind != "file" || results[0].ProducerID != "live" || results[0].OutputID != "file" || results[0].SHA256 != "edeaaff3f1774ad2888673770c6d64097e391bc362d7d6fb34982ddf0efd18cb" {
		t.Fatal("native file digest mismatch", results[0])
	}
	h := sha256.New()
	h.Write([]byte("directory-v2"))
	for _, e := range []struct {
		tag        byte
		path, body string
	}{{'f', "artifact", "abc\n"}, {'d', "child", ""}} {
		p := sha256.Sum256([]byte(e.path))
		b := sha256.Sum256([]byte(e.body))
		h.Write([]byte{e.tag})
		h.Write(p[:])
		h.Write(b[:])
	}
	if results[1].PathKind != "directory" || results[1].DigestAlgorithm != "directory-v2" || results[1].SHA256 != hex.EncodeToString(h.Sum(nil)) {
		t.Fatal("live directory digest mismatch", results[1])
	}
	if completed.Inspections[0].ResolvedPath != root+"/link/../artifact" {
		t.Fatal("Awsh cleaned lexical path before inspection", completed.Inspections[0])
	}
	t.Logf("ACTUAL B7.1 INSPECTION %d: final-state,lexical-path,file,directory-v2,home,user,literals,utf8", iteration+1)
}
