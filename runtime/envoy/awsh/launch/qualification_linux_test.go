//go:build linux

package launch

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Only this test assembly reads a retained qualifier receipt as its manifest.
// Production Start and LookupAwsh still use the fixed table and exact inputs.
func qualificationStart(ctx context.Context, h Handoff, digest, path string) (*Session, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var receipt struct {
		Target bashbuild.Target  `json:"target"`
		Inputs map[string]string `json:"adapter_inputs"`
	}
	if err = json.Unmarshal(data, &receipt); err != nil {
		return nil, err
	}
	data, err = os.ReadFile("/omegaflow-runtime/etc/bash-builds.json")
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256(data)
	receipt.Inputs["/omegaflow-runtime/etc/bash-builds.json"] = hex.EncodeToString(hash[:])
	return Start(ctx, h, digest, receipt.Target, receipt.Inputs)
}

func TestQualificationInspectionExamples(t *testing.T) {
	root := os.Getenv("OMEGAFLOW_QUALIFICATION_ROOT")
	if root == "" {
		root = "../../../.."
	}
	f, err := os.Open(filepath.Join(root, "tests/fixtures/envoy-protocol-v1/traces.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	count := 0
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		var tc struct {
			ID    string `json:"id"`
			Input struct {
				Path  string            `json:"configured_path"`
				CWD   string            `json:"physical_cwd"`
				Env   map[string]string `json:"exported_env"`
				Homes map[string]string `json:"user_homes"`
			} `json:"input"`
			Expected struct {
				Path string `json:"resolved_path"`
			} `json:"expected"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &tc); err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(tc.ID, "B1-C003-path-example-") {
			continue
		}
		count++
		t.Run(tc.ID, func(t *testing.T) {
			state := protocol.PromptState{PhysicalCWD: tc.Input.CWD, ExportedEnv: tc.Input.Env}
			plans, err := resolvePlans(state, []protocol.Inspection{{InspectionID: "i", Kind: "file_exists", Path: tc.Input.Path}}, func(name string) (string, bool) { home, ok := tc.Input.Homes[name]; return home, ok })
			if err != nil || len(plans) != 1 || plans[0].ResolvedPath != tc.Expected.Path {
				t.Fatalf("actual resolver: %#v %v; want %q", plans, err, tc.Expected.Path)
			}
		})
	}
	if scanner.Err() != nil || count != 5 {
		t.Fatalf("incomplete path examples: %d %v", count, scanner.Err())
	}
}
