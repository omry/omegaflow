package launch

import (
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"strings"
	"testing"
)

func TestFinalInspectionPlans(t *testing.T) {
	state := protocol.PromptState{PhysicalCWD: "/final/work", Status: 19, HistExpand: "off", EditingMode: "vi", ExportedEnv: map[string]string{"OUT": "link/../artifact", "HOME": "/home/final", "EMPTY": ""}}
	for _, tt := range []struct{ path, want string }{
		{"$OUT", "/final/work/link/../artifact"}, {"${OUT}//", "/final/work/link/../artifact//"},
		{"$UNDEFINED/${MISSING}", "/final/work/$UNDEFINED/${MISSING}"},
		{"${OUT:-fallback}", "/final/work/${OUT:-fallback}"}, {"$(touch nope)", "/final/work/$(touch nope)"},
		{"$((1+2))", "/final/work/$((1+2))"}, {"*.txt", "/final/work/*.txt"},
		{"~/a", "/home/final/a"}, {"~known/x", "/database/known/x"}, {"~missing/x", "/final/work/~missing/x"},
		{"//root/./x/../y/", "//root/./x/../y/"}, {"$EMPTY", "/final/work/"}, {"$OUTα", "/final/work/link/../artifactα"},
	} {
		t.Run(tt.path, func(t *testing.T) {
			plans, err := resolvePlans(state, []protocol.Inspection{{InspectionID: "i", Kind: "produces", Path: tt.path, ProducerID: "p", OutputID: "o"}}, func(name string) (string, bool) { return "/database/known", name == "known" })
			if err != nil || len(plans) != 1 || plans[0].ResolvedPath != tt.want || plans[0].ProducerID != "p" || plans[0].OutputID != "o" {
				t.Fatalf("%#v, %v", plans, err)
			}
		})
	}
	delete(state.ExportedEnv, "HOME")
	plans, err := resolvePlans(state, []protocol.Inspection{{InspectionID: "i", Kind: "file_exists", Path: "~"}}, func(name string) (string, bool) { return "/effective", name == "" })
	if err != nil || plans[0].ResolvedPath != "/effective" {
		t.Fatalf("%#v %v", plans, err)
	}
	for _, path := range []string{"/" + strings.Repeat("x", 65537), "/invalid\xff", "/nul\x00"} {
		if _, err := resolvePlans(state, []protocol.Inspection{{InspectionID: "i", Kind: "file_exists", Path: path}}, func(string) (string, bool) { return "", false }); err == nil {
			t.Fatal("invalid resolved path accepted")
		}
	}
}

func TestMalformedExpansionStaysLiteral(t *testing.T) {
	env := map[string]string{"A": "expanded"}
	for _, path := range []string{"${A", "${A:-$A}", "${!A}", "${A-$A}", "$", "$9", "${}"} {
		if got := expandExported(path, env); got != path {
			t.Fatalf("%q became %q", path, got)
		}
	}
}
