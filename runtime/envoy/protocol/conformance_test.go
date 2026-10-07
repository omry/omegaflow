package protocol

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
)

// This checks declarative corpus completeness, never a runtime actor's behavior.
const fixtureRoot = "../../../tests/fixtures/envoy-protocol-v1/"

type caseRef struct {
	File string `json:"file"`
	ID   string `json:"id"`
}
type startupFixture struct {
	ID               string  `json:"id"`
	Synthetic        bool    `json:"synthetic"`
	ExpectedBytesHex string  `json:"expected_bytes_hex"`
	ReceivedBytesHex string  `json:"received_bytes_hex"`
	Fragments        []int   `json:"fragments"`
	OutputThrough    int64   `json:"ready_output_through"`
	ReadyReceived    bool    `json:"ready_received"`
	EOF              bool    `json:"EOF"`
	DeadlineExpired  bool    `json:"deadline_expired"`
	Expected         string  `json:"expected"`
	BuildIdentity    *string `json:"build_identity"`
}
type digestEntry struct {
	Kind       string `json:"kind"`
	Path       string `json:"path"`
	PayloadHex string `json:"payload_hex"`
}
type inspectionFixture struct {
	ID                    string        `json:"id"`
	Algorithm             string        `json:"algorithm"`
	Entries               []digestEntry `json:"entries"`
	HashInputHex          string        `json:"hash_input_hex"`
	SHA256                string        `json:"sha256"`
	OmittedSpecialEntries []string      `json:"omitted_special_entries"`
	LiveResolution        bool          `json:"live_resolution"`
}
type awshFrameFixture struct {
	ID         string  `json:"id"`
	File       string  `json:"file"`
	Direction  string  `json:"direction"`
	Phase      *string `json:"phase"`
	PayloadHex string  `json:"payload_hex"`
	FrameHex   string  `json:"frame_hex"`
}
type auxiliaryFixtures struct {
	Startups    map[string]startupFixture
	Inspections map[string]inspectionFixture
	Frames      map[string]awshFrameFixture
	Goldens     map[string]nulFixture
	WireRefs    map[string]caseRef
}
type runtimeProof struct {
	Leaf   string  `json:"leaf"`
	CaseID string  `json:"case_id"`
	Status string  `json:"status"`
	Test   *string `json:"test"`
}
type conformanceCase struct {
	ID                   string       `json:"id"`
	Requirement          string       `json:"requirement"`
	Package              string       `json:"package"`
	ImplementationLeaf   string       `json:"implementation_leaf"`
	Prerequisites        string       `json:"prerequisites"`
	ClosureLeaf          string       `json:"closure_leaf"`
	ClosurePrerequisites string       `json:"closure_prerequisites"`
	Fixture              caseRef      `json:"fixture"`
	StaticTest           string       `json:"static_test"`
	RuntimeTest          runtimeProof `json:"runtime_test"`
	BuildDependent       bool         `json:"build_dependent"`
}
type contractClause struct {
	ID   string `json:"id"`
	Text string `json:"text"`
}
type packageRequirement struct {
	ID         string `json:"id"`
	Leaf       string `json:"leaf"`
	Acceptance string `json:"acceptance"`
}
type leafContract struct {
	Prerequisites  string `json:"prerequisites"`
	Implementation string `json:"implementation"`
	Acceptance     string `json:"acceptance"`
}
type authorityBinding struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}
type inventory struct {
	Version             int                     `json:"version"`
	Source              string                  `json:"source"`
	SourceSHA256        string                  `json:"source_sha256"`
	Authorities         []authorityBinding      `json:"authorities"`
	Begin               string                  `json:"begin"`
	End                 string                  `json:"end"`
	Requirements        []contractClause        `json:"requirements"`
	Leaves              map[string]leafContract `json:"leaves"`
	PackageRequirements []packageRequirement    `json:"package_requirements"`
	StaticOnly          bool                    `json:"static_only"`
	QualifiedBuilds     []any                   `json:"qualified_builds"`
	CaseIDs             []string                `json:"case_ids"`
}
type deadlineExpectation struct {
	Owner         string `json:"owner"`
	Name          string `json:"name"`
	BudgetMS      int    `json:"budget_ms"`
	Reset         bool   `json:"reset"`
	EntryBoundary string `json:"entry_boundary"`
}
type expectedTrace struct {
	ID                     string               `json:"id"`
	Input                  map[string]any       `json:"input"`
	Expected               map[string]any       `json:"expected"`
	Events                 []string             `json:"events"`
	Epoch                  *deadlineExpectation `json:"epoch"`
	BuildValues            map[string]any       `json:"build_values"`
	BuildExpectationsOwner *string              `json:"build_expectations_owner"`
}

func fixtureData(t testing.TB, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(fixtureRoot + name)
	if err != nil {
		t.Fatal(err)
	}
	if len(b) == 0 || b[len(b)-1] != '\n' {
		t.Fatalf("%s: missing final LF", name)
	}
	return b
}
func decodeFixture[T any](t testing.TB, b []byte) T {
	t.Helper()
	var v T
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err := d.Decode(&v); err != nil {
		t.Fatal(err)
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		t.Fatal("trailing fixture value")
	}
	return v
}
func fixtureRows[T any](t testing.TB, name string) []T {
	t.Helper()
	var out []T
	for _, row := range bytes.Split(bytes.TrimSuffix(fixtureData(t, name), []byte{'\n'}), []byte{'\n'}) {
		out = append(out, decodeFixture[T](t, row))
	}
	return out
}
func sha(b []byte) string { return fmt.Sprintf("%x", sha256.Sum256(b)) }

func expectedString(values map[string]any, key string) (string, bool) {
	value, ok := values[key].(string)
	return value, ok
}

func checkAuxiliaryBinding(ref caseRef, trace expectedTrace, aux auxiliaryFixtures) error {
	staticExample, ok := expectedString(trace.Input, "static_example")
	if !ok || staticExample != ref.ID {
		return fmt.Errorf("trace/example identity mismatch: %s", ref.ID)
	}
	switch ref.File {
	case "startup.jsonl":
		fixture, found := aux.Startups[ref.ID]
		outcome, hasOutcome := expectedString(trace.Expected, "synthetic_outcome")
		if !found || !hasOutcome || outcome != fixture.Expected {
			return fmt.Errorf("startup trace expectation mismatch: %s", ref.ID)
		}
	case "inspection.jsonl":
		fixture, found := aux.Inspections[ref.ID]
		algorithm, hasAlgorithm := expectedString(trace.Expected, "algorithm")
		digest, hasDigest := expectedString(trace.Expected, "sha256")
		if !found || !hasAlgorithm || !hasDigest || algorithm != fixture.Algorithm || digest != fixture.SHA256 {
			return fmt.Errorf("inspection trace expectation mismatch: %s", ref.ID)
		}
		if err := checkInspectionOmissions(fixture); err != nil {
			return fmt.Errorf("inspection omission expectation mismatch: %w", err)
		}
	case "awsh-frames.json":
		fixture, found := aux.Frames[ref.ID]
		direction, hasDirection := expectedString(trace.Expected, "direction")
		payload, hasPayload := expectedString(trace.Expected, "payload_hex")
		frame, hasFrame := expectedString(trace.Expected, "frame_hex")
		if !found || !hasDirection || !hasPayload || !hasFrame || direction != fixture.Direction || payload != fixture.PayloadHex || frame != fixture.FrameHex {
			return fmt.Errorf("frame trace expectation mismatch: %s", ref.ID)
		}
		if fixture.File == "helper.jsonl" {
			golden, hasGolden := aux.Goldens[ref.ID]
			if fixture.Phase == nil || !hasGolden || *fixture.Phase != golden.Phase {
				return fmt.Errorf("helper phase mismatch: %s", ref.ID)
			}
		} else if fixture.Phase != nil {
			return fmt.Errorf("private frame unexpectedly declares a phase: %s", ref.ID)
		}
	}
	return nil
}

func checkPackageRequirementBijection(inv inventory) error {
	if len(inv.PackageRequirements) != len(inv.Leaves) {
		return fmt.Errorf("package requirement count does not match leaf catalogue")
	}
	seen := make(map[string]bool, len(inv.PackageRequirements))
	for _, r := range inv.PackageRequirements {
		leaf, ok := inv.Leaves[r.Leaf]
		if !ok {
			return fmt.Errorf("unknown package leaf %s", r.Leaf)
		}
		if seen[r.Leaf] {
			return fmt.Errorf("duplicate package leaf %s", r.Leaf)
		}
		if r.ID != "PACKAGE-"+r.Leaf || r.Acceptance != leaf.Acceptance {
			return fmt.Errorf("invalid package requirement %s", r.ID)
		}
		seen[r.Leaf] = true
	}
	for leaf := range inv.Leaves {
		if !seen[leaf] {
			return fmt.Errorf("missing package requirement for leaf %s", leaf)
		}
	}
	return nil
}

var approvedAuthorities = []string{
	"docs/design/envoy-protocol-v1.md",
	"docs/design/omegaflow-envoy-design.md",
	"docs/design/reploy-environments-design.md",
	"docs/design/reploy-integration-implementation-plan.md",
}

func checkAuthorityBindings(bindings []authorityBinding) error {
	if len(bindings) != len(approvedAuthorities) {
		return fmt.Errorf("expected %d authority bindings, got %d", len(approvedAuthorities), len(bindings))
	}
	seen := make(map[string]bool, len(bindings))
	for i, binding := range bindings {
		if binding.Path != approvedAuthorities[i] || seen[binding.Path] {
			return fmt.Errorf("authority binding is missing, unknown, duplicate or unsorted: %s", binding.Path)
		}
		seen[binding.Path] = true
		contents, err := os.ReadFile("../../../" + binding.Path)
		if err != nil {
			return fmt.Errorf("authority %s: %w", binding.Path, err)
		}
		if sha(contents) != binding.SHA256 {
			return fmt.Errorf("authority bytes changed: %s", binding.Path)
		}
	}
	return nil
}

func expandedDeadline(id string) (deadlineExpectation, bool) {
	var name, owner string
	switch {
	case strings.HasPrefix(id, "B1-C028-") && oneOf(id, "B1-C028-success-short-writes", "B1-C028-peer-close", "B1-C028-terminal-write-error", "B1-C028-reply-timeout"):
		owner, name = "awsh", "gate-reply"
	case strings.HasPrefix(id, "B1-C028-") && oneOf(id, "B1-C028-continue-input-delayed", "B1-C028-continue-input-timeout"):
		owner, name = "envoy", "terminal-input-barrier"
	case strings.HasPrefix(id, "B1-C032-") && oneOf(id, "B1-C032-source-positive-marker", "B1-C032-PS0-positive-marker", "B1-C032-post-PS0-signal", "B1-C032-start-released"):
		owner, name = "envoy", "operation-start"
	case strings.HasPrefix(id, "B1-C032-") && oneOf(id, "B1-C032-partial-output", "B1-C032-missing-marker", "B1-C032-malformed-marker", "B1-C032-helper-nonzero", "B1-C032-helper-signal", "B1-C032-helper-disconnect"):
		owner, name = "envoy", "operation-start"
	case strings.HasPrefix(id, "B1-C034-") && oneOf(id, "B1-C034-setup-directory", "B1-C034-setup-stdout-FIFO", "B1-C034-setup-stderr-FIFO", "B1-C034-setup-reader", "B1-C034-setup-keepalive", "B1-C034-setup-validation", "B1-C034-rollback-close", "B1-C034-rollback-unlink"):
		owner, name = "envoy", "operation-start"
	case strings.HasPrefix(id, "B1-C034-") && oneOf(id, "B1-C034-stdout-EOF", "B1-C034-stderr-EOF", "B1-C034-keepalive-after-cleanup", "B1-C034-remove-after-dual-EOF"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C062-") && oneOf(id, "B1-C062-background", "B1-C062-disown", "B1-C062-nohup", "B1-C062-setsid", "B1-C062-double-fork", "B1-C062-outside-tree-service", "B1-C062-helper-exclusion", "B1-C062-adopted-child-reap"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C062-") && oneOf(id, "B1-C062-cleanup-timeout", "B1-C062-cancel-during-cleanup", "B1-C062-finalize-after-return"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C062-") && oneOf(id, "B1-C062-cancel-after-finalize", "B1-C062-shell-ended-cancel-race"):
		owner, name = "envoy", "grace"
	case strings.HasPrefix(id, "B1-C064-") && oneOf(id, "B1-C064-input-close", "B1-C064-input-closed", "B1-C064-prompt-ready", "B1-C064-completed"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C064-") && oneOf(id, "B1-C064-cleanup-helper-exclusion", "B1-C064-wait-record-removal", "B1-C064-empty-jobs", "B1-C064-repeat-adapter-validation", "B1-C064-final-PTY-drain"):
		owner, name = "envoy", "operation-cleanup"
	case id == "B1-C064-fresh-termios":
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C064-") && oneOf(id, "B1-C064-PTY-EOF", "B1-C064-stdout-EOF", "B1-C064-stderr-EOF"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C064-") && oneOf(id, "B1-C064-helper-INT-ignore", "B1-C064-canonical-INT-restore", "B1-C064-allowed-trap-final-state"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C065-") && oneOf(id, "B1-C065-clean-census", "B1-C065-nested-foreground", "B1-C065-foreground-switch", "B1-C065-positive-empty-then-input-close", "B1-C065-positive-empty-then-shell-exit", "B1-C065-positive-empty-then-live", "B1-C065-positive-empty-then-timeout"):
		owner, name = "envoy", "grace"
	case strings.HasPrefix(id, "B1-C065-") && oneOf(id, "B1-C065-completion-helper-cancel-window", "B1-C065-completion-helper-finalize-window"):
		owner, name = "envoy", "grace"
	case strings.HasPrefix(id, "B1-C065-") && oneOf(id, "B1-C065-cancel-after-input-close", "B1-C065-finalize-after-input-close"):
		owner, name = "envoy", "operation-cleanup"
	case strings.HasPrefix(id, "B1-C065-") && oneOf(id, "B1-C065-cancel-cross-finalize", "B1-C065-shell-exit-before-timeout", "B1-C065-shell-exit-after-timeout", "B1-C065-queued-input-close-after-timeout"):
		owner, name = "envoy", "grace"
	default:
		return deadlineExpectation{}, false
	}
	return deadlineExpectation{Owner: owner, Name: name, BudgetMS: 5000, Reset: false, EntryBoundary: expandedEntryBoundary(id)}, true
}

func expandedEntryBoundary(id string) string {
	switch {
	case oneOf(id, "B1-C028-success-short-writes", "B1-C028-peer-close", "B1-C028-terminal-write-error", "B1-C028-reply-timeout"):
		return "first attempted transport write of the already-encoded accepted reply after the matching private continue"
	case oneOf(id, "B1-C028-continue-input-delayed", "B1-C028-continue-input-timeout"):
		return "acceptance of a valid continue whose input_through exceeds Envoy's current terminal read count"
	case oneOf(id, "B1-C032-source-positive-marker", "B1-C032-PS0-positive-marker", "B1-C032-post-PS0-signal", "B1-C032-start-released"):
		return "immediately after execute.input_through is satisfied and before split setup or any private execute write"
	case oneOf(id, "B1-C032-partial-output", "B1-C032-missing-marker", "B1-C032-malformed-marker", "B1-C032-helper-nonzero", "B1-C032-helper-signal", "B1-C032-helper-disconnect"):
		return "the already-running operation-start epoch begun after execute.input_through was satisfied"
	case oneOf(id, "B1-C034-setup-directory", "B1-C034-setup-stdout-FIFO", "B1-C034-setup-stderr-FIFO", "B1-C034-setup-reader", "B1-C034-setup-keepalive", "B1-C034-setup-validation", "B1-C034-rollback-close", "B1-C034-rollback-unlink"):
		return "immediately after execute.input_through is satisfied, before the first split resource is created"
	case oneOf(id, "B1-C034-stdout-EOF", "B1-C034-stderr-EOF", "B1-C034-keepalive-after-cleanup", "B1-C034-remove-after-dual-EOF"):
		return "acceptance of matching input_close for ordinary return or start of mandatory lifecycle/shell-exit cleanup"
	case oneOf(id, "B1-C062-background", "B1-C062-disown", "B1-C062-nohup", "B1-C062-setsid", "B1-C062-double-fork", "B1-C062-outside-tree-service", "B1-C062-helper-exclusion", "B1-C062-adopted-child-reap"):
		return "acceptance of matching input_close or beginning mandatory cleanup for the selected lifecycle/shell-exit path"
	case oneOf(id, "B1-C062-cleanup-timeout", "B1-C062-cancel-during-cleanup", "B1-C062-finalize-after-return"):
		return "acceptance of matching input_close or beginning mandatory cleanup for cancellation/finalization/shell-exit"
	case oneOf(id, "B1-C062-cancel-after-finalize", "B1-C062-shell-ended-cancel-race"):
		return "selection of the ordinary started-operation cancel/finalize path, before foreground sampling"
	case oneOf(id, "B1-C064-input-close", "B1-C064-input-closed", "B1-C064-prompt-ready", "B1-C064-completed"):
		return "acceptance of matching input_close"
	case oneOf(id, "B1-C064-cleanup-helper-exclusion", "B1-C064-wait-record-removal", "B1-C064-empty-jobs", "B1-C064-repeat-adapter-validation", "B1-C064-final-PTY-drain"):
		return "acceptance of matching input_close"
	case oneOf(id, "B1-C064-fresh-termios"):
		return "acceptance of matching input_close"
	case oneOf(id, "B1-C064-PTY-EOF", "B1-C064-stdout-EOF", "B1-C064-stderr-EOF"):
		return "acceptance of matching input_close"
	case oneOf(id, "B1-C064-helper-INT-ignore", "B1-C064-canonical-INT-restore", "B1-C064-allowed-trap-final-state"):
		return "acceptance of matching input_close"
	case oneOf(id, "B1-C065-clean-census", "B1-C065-nested-foreground", "B1-C065-foreground-switch", "B1-C065-positive-empty-then-input-close", "B1-C065-positive-empty-then-shell-exit", "B1-C065-positive-empty-then-live", "B1-C065-positive-empty-then-timeout"):
		return "selection of the ordinary started-operation cancel/finalize path, before foreground sampling"
	case oneOf(id, "B1-C065-completion-helper-cancel-window", "B1-C065-completion-helper-finalize-window"):
		return "acceptance of cancel/finalize on the ordinary started-operation path, before foreground sampling"
	case oneOf(id, "B1-C065-cancel-after-input-close", "B1-C065-finalize-after-input-close"):
		return "the already-accepted matching input_close"
	case oneOf(id, "B1-C065-cancel-cross-finalize", "B1-C065-shell-exit-before-timeout", "B1-C065-shell-exit-after-timeout", "B1-C065-queued-input-close-after-timeout"):
		return "selection of the original ordinary started-operation cancel/finalize path, before foreground sampling"
	default:
		return ""
	}
}

var allowedPublications = map[string]bool{
	"assertion-evidence": true, "cleanup-evidence": true, "compiler-decision": true,
	"diagnostic-only": true, "fatal-diagnostic": true, "none": true,
	"operation-cancelled": true, "operation-completed": true, "operation-continued": true,
	"operation-failed": true, "operation-finalized": true, "operation-gate-interrupted": true,
	"operation-ready": true, "operation-result": true, "operation-started": true,
	"ordinary-completion-frame": true, "ordinary-selected-Bash-result": true,
	"private-handshake": true, "range-evidence": true, "resize-applied": true,
	"shell-ended": true, "typed-inspection-result": true,
}

func requireOutcome(id string, trace expectedTrace, eligible bool, result *string, fatal *bool, signalCount float64, publication *string) error {
	if trace.Expected["result_eligible"] != eligible {
		return fmt.Errorf("result eligibility mismatch: %s", id)
	}
	gotResult, resultPresent := trace.Expected["operation_result"]
	if result == nil {
		if !resultPresent || gotResult != nil {
			return fmt.Errorf("operation result should be null: %s", id)
		}
	} else if gotResult != *result {
		return fmt.Errorf("operation result mismatch: %s", id)
	}
	gotFatal, fatalPresent := trace.Expected["fatal"]
	if fatal == nil {
		if !fatalPresent || gotFatal != nil {
			return fmt.Errorf("fatality should remain pending: %s", id)
		}
	} else if !fatalPresent || gotFatal != *fatal {
		return fmt.Errorf("fatality mismatch: %s", id)
	}
	if trace.Expected["signal_count"] != signalCount {
		return fmt.Errorf("signal count mismatch: %s", id)
	}
	gotPublication, publicationPresent := trace.Expected["publication"]
	if publication == nil {
		if !publicationPresent || gotPublication != nil {
			return fmt.Errorf("publication should remain pending: %s", id)
		}
	} else if gotPublication != *publication {
		return fmt.Errorf("publication mismatch: %s", id)
	}
	return nil
}

func requireEventOrder(id string, events []string, required ...string) error {
	at := 0
	for _, want := range required {
		for at < len(events) && events[at] != want {
			at++
		}
		if at == len(events) {
			return fmt.Errorf("missing event order %q: %s", want, id)
		}
		at++
	}
	return nil
}

func requireExpectedField(id string, expected map[string]any, key string, want any) error {
	got, ok := expected[key]
	if !ok || got != want {
		return fmt.Errorf("expected %s=%v: %s", key, want, id)
	}
	return nil
}

func requireLifecycleFacts(id string, trace expectedTrace, cleanup string) error {
	if err := requireExpectedField(id, trace.Expected, "cleanup_termination", cleanup); err != nil {
		return err
	}
	return requireExpectedField(id, trace.Expected, "output_drained", true)
}

// These rules come from the approved contract, not the trace being checked.
// Group defaults share outcomes; case exceptions retain only the distinguishing
// winner and required relations. They deliberately do not copy entire JSON rows.
type expandedContractRule struct {
	group                       int
	winner, result, publication string
	fatal                       *bool
	signals                     float64
	order                       []string
}

var expandedContracts = buildExpandedContracts()

func buildExpandedContracts() map[string]expandedContractRule {
	rules := map[string]expandedContractRule{}
	bind := func(group int, prefix, names, winner, result string, fatal *bool, signals float64, publication, order string) {
		for _, name := range strings.Fields(names) {
			id := "B1-" + prefix + "-" + name
			if _, exists := rules[id]; exists {
				panic("duplicate expanded contract: " + id)
			}
			w := winner
			if w == "@accepted" {
				w = name + "-accepted"
			}
			rules[id] = expandedContractRule{group, w, result, publication, fatal, signals, strings.Fields(order)}
		}
	}
	ok := func(g int, p, names, winner, result, publication, order string) {
		bind(g, p, names, winner, result, boolPtr(false), 0, publication, order)
	}
	bad := func(g int, p, names, winner, order string) {
		bind(g, p, names, winner, "", boolPtr(true), 0, "fatal-diagnostic", order)
	}
	// Inspection and handshake identities: protocol typed results and readiness.
	ok(0, "C001", "inspection-id", "inspection-id-repeated", "typed-result", "typed-inspection-result", "inspection-request inspection-id-repeated typed-result")
	ok(0, "C001", "resolved-plan", "resolved-plan-ordered", "resolved-plan", "typed-inspection-result", "inspection-request absolute-path-resolution resolved-plan-ordered")
	ok(0, "C001", "typed-result", "typed-result-ordered", "typed-result", "typed-inspection-result", "inspection-request typed-result-validation typed-result-ordered")
	ok(1, "C001", "deterministic-operation-id", "operation-id-deterministic", "session-ready", "private-handshake", "operation-id-derived session-id-compared")
	ok(1, "C001", "matching-session", "matching-session-accepted", "session-ready", "private-handshake", "operation-id-derived session-id-compared matching-session-accepted")
	bad(1, "C001", "mismatching-session", "session-mismatch-rejected", "operation-id-derived session-id-compared session-mismatch-rejected")
	ok(2, "C001", "unknown-diagnostic", "unknown-code-retained", "", "diagnostic-only", "diagnostic-frame-validated unknown-code-retained diagnostic-not-operation-result")
	// A2.4/A2.6 reserved-state mediation; selected-build observations stay pending.
	bind(3, "C021", "redefine-awsh unset-awsh", "readonly-awsh-preserved", "", nil, 0, "", "source-mutation-request readonly-awsh-guard readonly-awsh-preserved")
	bad(4, "C022", "combined-options multiple-names mixed-reserved-nonreserved", "whole-request-rejected", "argv-expanded complete-preflight whole-request-rejected reserved-state-canonical")
	ok(5, "C022", "reserved-query", "query-preserved", "ordinary-result", "ordinary-selected-Bash-result", "ordinary-request query reserved-state-canonical")
	ok(5, "C022", "numeric-CHLD-query numeric-INT-query", "selected-build-numeric-query-preserved", "ordinary-result", "ordinary-selected-Bash-result", "ordinary-request selected-build-numeric-query reserved-state-canonical")
	ok(5, "C022", "positive-enable", "positive-enable-preserved", "ordinary-result", "ordinary-selected-Bash-result", "ordinary-request positive-enable reserved-state-canonical")
	ok(5, "C022", "nonreserved-numeric-trap", "nonreserved-numeric-trap-preserved", "ordinary-result", "ordinary-selected-Bash-result", "ordinary-request nonreserved-numeric-trap reserved-state-canonical")
	ok(5, "C022", "nonrequired-builtin-modes", "nonrequired-builtin-mode-preserved", "ordinary-result", "ordinary-selected-Bash-result", "ordinary-request nonrequired-builtin-mode reserved-state-canonical")
	ok(6, "C027", "posix-query posix-disabled-request", "posix-mode-remains-disabled", "ordinary-result", "ordinary-selected-Bash-result", "posix-request posix-mode-disabled trap-mediation-retained")
	ok(6, "C027", "set-positional", "positional-state-preserved", "positional-state", "ordinary-selected-Bash-result", "set-positional-request posix-mode-disabled positional-state-preserved")
	ok(7, "C027", "nested-CHLD nested-INT", "nested-handler-owned", "child-signal-result", "ordinary-selected-Bash-result", "child-process-start child-handler-owned selected-shell-state-unchanged")
	ok(7, "C027", "ordinary-child-handler", "ordinary-child-handler-owned", "child-signal-result", "ordinary-selected-Bash-result", "child-process-start child-handler-owned selected-shell-state-unchanged")
	bad(8, "C027", "explicit-builtin-bypass", "same-identity-bypass-fatal", "explicit-builtin-lookup same-identity-interference fatal-teardown")
	// Gate transport and terminal-input barrier, under their original epochs.
	ok(9, "C028", "hostile-PATH", "absolute-helper-selected", "gate-ready", "operation-ready", "gate-request absolute-helper-selected gate-id-preserved")
	ok(10, "C028", "success-short-writes", "gate-continued", "gate-continued", "operation-continued", "accepted-reply-encoded short-write-progress complete-reply-write")
	for _, name := range []string{"peer-close", "terminal-write-error", "reply-timeout"} {
		ok(10, "C028", name, "gate-interrupted", "gate-interrupted", "operation-gate-interrupted", "accepted-reply-encoded "+name+" gate-interrupted")
	}
	ok(11, "C028", "continue-input-delayed", "continue-after-watermark", "gate-continued", "operation-continued", "continue-received input-watermark-satisfied private-continue")
	bad(11, "C028", "continue-input-timeout", "input-barrier-timeout", "continue-received input-watermark-expired input-barrier-timeout")
	// A2.4 markers and split-entry status distinction.
	for _, name := range []string{"source-positive-marker", "PS0-positive-marker"} {
		ok(12, "C032", name, name+"-accepted", "", "none", "marker-request "+name+"-accepted source-remains-blocked")
	}
	ok(12, "C032", "post-PS0-signal", "post-PS0-release-signal", "", "none", "operation-start-barrier public-operation-started started-ack post-PS0-release-signal start-release-pending")
	ok(12, "C032", "start-released", "start-released", "operation-started", "operation-started", "public-operation-started publish-operation-started started-ack post-PS0-release-signal start-released")
	bad(13, "C032", "fail-stop-no-arguments", "fail-stop-no-return", "fail-stop-entry SIGCONT-ignored fail-stop-no-return")
	bad(14, "C032", "INNER_ENTERED-zero", "redirection-not-entered", "split-redirect-open INNER_ENTERED-zero source-not-executed")
	for _, name := range []string{"first-expansion-status", "first-command-status"} {
		ok(14, "C032", name, name+"-observed", "authored-status", "operation-result", "split-redirect-open INNER_ENTERED-one "+name+"-observed")
	}
	for _, name := range []string{"stdout-open-failure", "stderr-open-failure"} {
		bad(14, "C032", name, "split-redirect-rejected", "split-redirect-open "+name+" INNER_ENTERED-remains-zero")
	}
	ok(14, "C032", "authored-status-one", "authored-status-one-preserved", "authored-status", "operation-result", "split-redirect-open INNER_ENTERED-one authored-status-one-preserved")
	for _, name := range []string{"partial-output", "missing-marker", "malformed-marker", "helper-nonzero", "helper-signal", "helper-disconnect"} {
		bad(15, "C032", name, "prestart-fail-stop", "helper-result-received "+name+" prestart-fail-stop")
	}
	// Split rollback, independent EOFs, retained logical ranges and resize.
	for _, name := range []string{"setup-directory", "setup-stdout-FIFO", "setup-stderr-FIFO", "setup-reader", "setup-keepalive", "setup-validation", "rollback-close", "rollback-unlink"} {
		bad(16, "C034", name, "split-setup-rollback", "operation-start-epoch split-setup-"+name+" rollback-complete")
	}
	ok(17, "C034", "stdout-EOF", "stdout-eof", "", "none", "keepalive-close stdout-EOF wait-stderr-EOF FIFO-removal-blocked")
	ok(17, "C034", "stderr-EOF", "stderr-eof", "", "none", "keepalive-close stderr-EOF wait-stdout-EOF FIFO-removal-blocked")
	ok(17, "C034", "keepalive-after-cleanup", "keepalive-after-cleanup", "", "none", "descendant-cleanup-complete keepalive-close wait-independent-EOFs FIFO-removal-blocked")
	ok(17, "C034", "remove-after-dual-EOF", "dual-EOF-cleanup", "", "none", "keepalive-close stdout-stderr-EOF FIFO-removal-complete")
	ok(18, "C034", "interleaved-logical-bytes", "arrival-order-preserved", "logical-ranges", "range-evidence", "pty-boundary-mark stream-mark arrival-order-preserved")
	ok(18, "C034", "same-offset-first-stream", "same-offset-mark-before-byte", "logical-ranges", "range-evidence", "pty-boundary-mark stream-mark same-offset-mark-before-byte")
	ok(19, "C034", "continuous-output-resize", "resize-frontier-ordered", "resize-applied", "resize-applied", "output-frontier resize-frontier-classified resize-frontier-ordered")
	ok(19, "C034", "no-covered-prefix-resize", "resize-frontier-pre-span", "resize-applied", "resize-applied", "output-frontier resize-frontier-classified resize-frontier-pre-span")
	// B6 cleanup and exclusive observation. Public results require output drain.
	for _, name := range []string{"background", "disown", "nohup", "setsid", "double-fork", "helper-exclusion", "adopted-child-reap"} {
		ok(20, "C062", name, "controlled-tree-cleaned", "cleanup-complete", "cleanup-evidence", "census "+name+" controlled-tree-cleaned")
	}
	ok(20, "C062", "outside-tree-service", "outside-tree-preserved", "cleanup-complete", "cleanup-evidence", "census outside-tree-service controlled-tree-cleaned")
	bad(21, "C062", "cleanup-timeout", "cleanup-timeout", "cleanup-epoch-start cleanup-epoch-expired cleanup-timeout")
	ok(21, "C062", "cancel-during-cleanup", "cancel-during-cleanup", "operation-cancelled", "operation-cancelled", "cleanup-epoch-start cancel-local cleanup-complete output-drain")
	ok(21, "C062", "finalize-after-return", "observed-return-wins", "operation-completed", "operation-completed", "input-close finalize-local cleanup-complete output-drain observed-return-wins")
	bind(22, "C062", "cancel-after-finalize", "cancel-wins-before-input-close", "operation-cancelled", boolPtr(false), 1, "operation-cancelled", "grace-start TIOCSIG-once shell-returned operation-tree-cleanup output-drain")
	ok(22, "C062", "shell-ended-cancel-race", "shell-exit-wins", "shell-ended", "shell-ended", "grace-start shell-exit shell-exit-wins operation-tree-cleanup output-drain")
	for _, name := range []string{"exclusive-checked", "exclusive-suppress", "exclusive-replace"} {
		ok(23, "C062", name, "exclusive-evidence-accepted", "assertion-evidence", "assertion-evidence", "assertion-range-open "+name+" exclusive-evidence-accepted")
	}
	ok(23, "C062", "planned-end-status", "exclusive-evidence-accepted", "assertion-evidence", "assertion-evidence", "assertion-range-open planned-end-status planned-end-status-not-exit-evidence exclusive-evidence-accepted")
	bad(23, "C062", "finalization-failure-invalid-range", "assertion-eligibility-invalidated", "assertion-range-open finalization-failure-invalid-range assertion-eligibility-invalidated")
	ok(23, "C062", "user-cancel-invalid-range", "assertion-eligibility-invalidated", "operation-cancelled", "operation-cancelled", "assertion-range-open user-cancel-invalid-range assertion-eligibility-invalidated")
	for _, name := range []string{"compiler-operation-input", "compiler-continuation-input"} {
		ok(24, "C062", name, "output-assertion-rejected", "input-rejected", "compiler-decision", "input-compiled "+name+" output-assertion-rejected")
	}
	ok(24, "C062", "wait-for-preserved", "wait-for-synchronization", "input-accepted", "compiler-decision", "input-compiled wait-for-preserved wait-for-synchronization")
	// A2.5 completion and persistent-state handoff.
	ok(25, "C064", "prompt-state", "prompt-state-validated", "", "none", "prompt-state helper-identity-validated input-close-not-accepted")
	for _, name := range []string{"input-close", "input-closed", "prompt-ready"} {
		ok(26, "C064", name, "@accepted", "", "none", "operation-cleanup-epoch "+name+" "+name+"-accepted")
	}
	ok(26, "C064", "completed", "completed-accepted", "operation-completed", "operation-completed", "input_close close-operation-input descendant-cleanup dual-EOF FIFO-removal input_closed helper-release wait-empty-jobs final-prompt_ready Readline-proof completed final-census PTY-drain covering-mark completed-accepted")
	for _, name := range []string{"cleanup-helper-exclusion", "wait-record-removal", "empty-jobs", "repeat-adapter-validation", "final-PTY-drain"} {
		ok(27, "C064", name, "cleanup-postcondition-satisfied", "", "none", "cleanup-epoch "+name+" cleanup-postcondition-satisfied")
	}
	ok(28, "C064", "fresh-termios", "fresh-readline-transition-required", "", "none", "cleanup-complete fresh-termios-snapshot readline-transition-required")
	ok(29, "C064", "live-function live-alias live-positional live-unexported live-options", "persistent-state-live", "", "none", "operation-return persistent-state-captured persistent-state-live")
	for _, name := range []string{"stale-PID", "extra-PID", "wrong-parent", "wrong-executable", "early-release", "stale-status", "stale-cwd", "stale-env", "duplicate", "malformed"} {
		bad(30, "C064", name, "completion-evidence-rejected", "completion-evidence-received "+name+" completion-evidence-rejected")
	}
	bad(31, "C064", "PTY-EOF", "PTY-EOF-rejected", "cleanup-epoch PTY-EOF selected-shell-channel-lost")
	for _, name := range []string{"stdout-EOF", "stderr-EOF"} {
		ok(31, "C064", name, name+"-missing-peer", "", "none", "cleanup-epoch "+name+" wait-peer-EOF")
	}
	for _, name := range []string{"helper-INT-ignore", "canonical-INT-restore", "allowed-trap-final-state"} {
		ok(32, "C064", name, "final-adapter-state-validated", "", "none", "completion-helper-window "+name+" final-adapter-state-validated")
	}
	// A2.6 one-signal lifecycle winners; zero signals never waive cleanup.
	ok(33, "C065", "clean-census", "clean-census", "", "none", "grace-start TIOCGPGRP clean-census")
	bind(33, "C065", "nested-foreground", "nested-foreground-validated", "", boolPtr(false), 1, "none", "grace-start TIOCGPGRP TIOCSIG-once")
	bind(33, "C065", "foreground-switch", "foreground-switch-resampled", "", boolPtr(false), 1, "none", "grace-start TIOCGPGRP TIOCSIG-once")
	ok(33, "C065", "positive-empty-then-input-close", "input-close-wins", "operation-cancelled", "operation-cancelled", "grace-start TIOCGPGRP input-close-wins shell-returned operation-tree-cleanup output-drain")
	ok(33, "C065", "positive-empty-then-shell-exit", "shell-exit-wins", "shell-ended", "shell-ended", "grace-start TIOCGPGRP shell-exit-wins operation-tree-cleanup output-drain")
	bind(33, "C065", "positive-empty-then-live", "live-foreground-signalled", "operation-cancelled", boolPtr(false), 1, "operation-cancelled", "grace-start TIOCGPGRP TIOCSIG-once shell-returned operation-tree-cleanup output-drain")
	ok(33, "C065", "positive-empty-then-timeout", "grace-expiry", "cancel-timeout", "operation-failed", "grace-start TIOCGPGRP grace-expiry selected-shell-termination shell-exit-reap operation-tree-cleanup output-drain")
	bind(34, "C065", "completion-helper-cancel-window", "cancel-window-selected", "operation-cancelled", boolPtr(false), 1, "operation-cancelled", "grace-start TIOCSIG-once helper-survives input-close shell-returned operation-tree-cleanup output-drain")
	bind(34, "C065", "completion-helper-finalize-window", "finalize-window-selected", "operation-finalized", boolPtr(false), 1, "operation-finalized", "grace-start TIOCSIG-once helper-survives input-close shell-returned operation-tree-cleanup output-drain")
	ok(35, "C065", "cancel-after-input-close", "cancel-local-after-input-close", "operation-cancelled", "operation-cancelled", "input-close request-local existing-cleanup-finished inspection-skipped")
	ok(35, "C065", "finalize-after-input-close", "observed-return-wins-after-input-close", "operation-completed", "operation-completed", "input-close request-local existing-cleanup-finished inspection-complete")
	bind(36, "C065", "cancel-cross-finalize", "cancel-wins-before-input-close", "operation-cancelled", boolPtr(false), 1, "operation-cancelled", "grace-start TIOCSIG-once shell-returned operation-tree-cleanup output-drain")
	ok(36, "C065", "shell-exit-before-timeout", "shell-exit-wins-before-timeout", "shell-ended", "shell-ended", "grace-start shell-exit-before-timeout shell-exit-wins-before-timeout operation-tree-cleanup output-drain")
	ok(36, "C065", "shell-exit-after-timeout", "grace-timeout-selected", "cancel-timeout", "operation-failed", "grace-start grace-timeout-selected selected-shell-termination shell-exit-after-timeout-reap-evidence operation-tree-cleanup output-drain")
	ok(36, "C065", "queued-input-close-after-timeout", "grace-timeout-discarded-input-close", "cancel-timeout", "operation-failed", "grace-start input-close-queued grace-timeout-selected input-close-discarded selected-shell-termination shell-exit-reap operation-tree-cleanup output-drain")
	return rules
}

func checkExpandedContract(id string, trace expectedTrace) error {
	rule, exists := expandedContracts[id]
	if !exists {
		return fmt.Errorf("no independent expanded contract: %s", id)
	}
	if trace.Expected["winner"] != rule.winner {
		return fmt.Errorf("contract winner mismatch: %s", id)
	}
	var result, publication *string
	if rule.result != "" {
		result = stringPtr(rule.result)
	}
	if rule.publication != "" {
		publication = stringPtr(rule.publication)
	}
	if err := requireOutcome(id, trace, result != nil, result, rule.fatal, rule.signals, publication); err != nil {
		return err
	}
	order := append([]string{}, rule.order...)
	if rule.publication != "" && !containsEvent(order, "publish-"+rule.publication) {
		order = append(order, "publish-"+rule.publication)
	}
	order = append(order, "winner-"+rule.winner)
	return requireEventOrder(id, trace.Events, order...)
}

func checkExpandedTrace(id, requirement string, trace expectedTrace) error {
	if id == "B1-C064-completed" {
		plan, ok := trace.Input["inspections"].([]any)
		if trace.Input["final_census_clean"] != true || trace.Input["output_drained"] != true || !ok || len(plan) != 0 {
			return fmt.Errorf("completed lacks final census/drain or empty inspection plan: %s", id)
		}
	}
	if oneOf(id, "B1-C034-stdout-EOF", "B1-C034-stderr-EOF", "B1-C064-stdout-EOF", "B1-C064-stderr-EOF") {
		stdout := strings.HasSuffix(id, "stdout-EOF")
		if trace.Input["stdout_eof"] != stdout || trace.Input["stderr_eof"] != !stdout || trace.Input["cleanup_deadline_expired"] != false {
			return fmt.Errorf("single EOF progress lacks its live peer and remaining deadline: %s", id)
		}
	}
	if id == "B1-C034-keepalive-after-cleanup" && (trace.Input["descendants_cleaned"] != true || trace.Input["stdout_eof"] != false || trace.Input["stderr_eof"] != false || trace.Input["cleanup_deadline_expired"] != false) {
		return fmt.Errorf("keepalive close loses cleanup/EOF ordering: %s", id)
	}
	if id == "B1-C028-hostile-PATH" && (trace.Input["manifested_helper"] != "/omegaflow-runtime/bin/awsh" || trace.Expected["application_path_lookup"] != false) {
		return fmt.Errorf("gate helper path drifts: %s", id)
	}
	if err := checkExpandedContract(id, trace); err != nil {
		return err
	}
	caseID, hasCaseID := trace.Input["case_id"].(string)
	expanded, hasExpanded := trace.Input["expanded_case"].(bool)
	if !hasExpanded || !expanded || !hasCaseID || caseID != id {
		return fmt.Errorf("expanded trace lacks its concrete case identity: %s", id)
	}
	if trace.Expected["contract"] != requirement {
		return fmt.Errorf("expanded trace contract pointer drift: %s", id)
	}
	winner, winnerOK := trace.Expected["winner"].(string)
	inputWinner, inputWinnerOK := trace.Input["contract_winner"].(string)
	if !winnerOK || winner == "" || !inputWinnerOK || inputWinner != winner {
		return fmt.Errorf("expanded trace winner drift: %s", id)
	}
	eligible, eligibleOK := trace.Expected["result_eligible"].(bool)
	if !eligibleOK {
		return fmt.Errorf("expanded trace lacks result eligibility: %s", id)
	}
	if _, ok := trace.Expected["operation_result"]; !ok {
		return fmt.Errorf("expanded trace lacks operation result field: %s", id)
	}
	if count, ok := trace.Expected["signal_count"].(float64); !ok || count < 0 {
		return fmt.Errorf("expanded trace lacks valid signal count: %s", id)
	}
	publication, publicationString := trace.Expected["publication"].(string)
	if publicationString && !allowedPublications[publication] {
		return fmt.Errorf("unknown public publication: %s", id)
	}
	if len(trace.Events) < 3 || trace.Events[0] == "scenario-input" || trace.Events[len(trace.Events)-1] != "winner-"+winner {
		return fmt.Errorf("expanded trace lacks ordered case events: %s", id)
	}
	if publicationString && !containsEventBefore(trace.Events, "publish-"+publication, "winner-"+winner) {
		return fmt.Errorf("publication is not ordered before winner: %s", id)
	}
	if !publicationString && trace.Expected["publication"] != nil {
		return fmt.Errorf("invalid pending publication: %s", id)
	}
	_, resultString := trace.Expected["operation_result"].(string)
	if eligible != resultString {
		return fmt.Errorf("result eligibility does not match operation result: %s", id)
	}
	var fatal *bool
	if value, ok := trace.Expected["fatal"]; ok && value != nil {
		v, valid := value.(bool)
		if !valid {
			return fmt.Errorf("invalid fatality value: %s", id)
		}
		fatal = &v
	}
	if want, hasEpoch := expandedDeadline(id); hasEpoch {
		if trace.Epoch == nil || *trace.Epoch != want || want.EntryBoundary == "" {
			return fmt.Errorf("expanded trace has missing or wrong epoch: %s", id)
		}
	} else if trace.Epoch != nil {
		return fmt.Errorf("deadline-free expanded trace has an epoch: %s", id)
	}
	readonly := oneOf(id, "B1-C021-redefine-awsh", "B1-C021-unset-awsh")
	if readonly {
		if fatal != nil || publicationString || trace.Expected["fail_stop"] != nil || trace.Expected["selected_build_observation_pending"] != true {
			return fmt.Errorf("readonly Awsh outcome is not pending: %s", id)
		}
		return nil
	}
	if id == "B1-C034-no-covered-prefix-resize" {
		if err := requireOutcome(id, trace, true, stringPtr("resize-applied"), boolPtr(false), 0, stringPtr("resize-applied")); err != nil {
			return err
		}
		if trace.Expected["resize_applied"] != true {
			return fmt.Errorf("pre-span resize was not applied: %s", id)
		}
		if err := requireEventOrder(id, trace.Events, "resize-frontier-classified", "resize-frontier-pre-span", "publish-resize-applied"); err != nil {
			return err
		}
	}
	if oneOf(id, "B1-C022-nonreserved-numeric-trap", "B1-C022-nonrequired-builtin-modes") && trace.Expected["state_mutation"] != nil {
		return fmt.Errorf("selected-build mutation value invented: %s", id)
	}
	if id == "B1-C062-user-cancel-invalid-range" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-cancelled"), boolPtr(false), 0, stringPtr("operation-cancelled")); err != nil {
			return err
		}
		if trace.Expected["assertion_eligibility"] != "invalidated" {
			return fmt.Errorf("cancellation did not invalidate assertion: %s", id)
		}
		if err := requireEventOrder(id, trace.Events, "user-cancel-invalid-range", "assertion-eligibility-invalidated", "publish-operation-cancelled"); err != nil {
			return err
		}
	}
	if id == "B1-C062-planned-end-status" {
		if trace.Expected["exit_status_assertion"] != "indeterminate" {
			return fmt.Errorf("planned status became exit evidence: %s", id)
		}
		if err := requireEventOrder(id, trace.Events, "planned-end-status", "planned-end-status-not-exit-evidence", "publish-assertion-evidence"); err != nil {
			return err
		}
	}
	if id == "B1-C032-post-PS0-signal" {
		if trace.Input["public_started"] != true {
			return fmt.Errorf("public start was not recorded: %s", id)
		}
		if err := requireEventOrder(id, trace.Events, "public-operation-started", "started-ack", "post-PS0-release-signal", "start-release-pending", "publish-none"); err != nil {
			return err
		}
	}
	if id == "B1-C032-start-released" {
		if err := requireEventOrder(id, trace.Events, "public-operation-started", "publish-operation-started", "started-ack", "post-PS0-release-signal", "start-released"); err != nil {
			return err
		}
	}
	if oneOf(id, "B1-C065-nested-foreground", "B1-C065-foreground-switch") {
		if err := requireOutcome(id, trace, false, nil, boolPtr(false), 1, stringPtr("none")); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "cleanup_termination", "none"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", false); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "TIOCGPGRP", "TIOCSIG-once", "publish-none"); err != nil {
			return err
		}
	}
	if id == "B1-C065-positive-empty-then-input-close" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-cancelled"), boolPtr(false), 0, stringPtr("operation-cancelled")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", false); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "input-close-wins", "shell-returned", "operation-tree-cleanup", "output-drain", "publish-operation-cancelled"); err != nil {
			return err
		}
	}
	if id == "B1-C065-positive-empty-then-shell-exit" {
		if err := requireOutcome(id, trace, true, stringPtr("shell-ended"), boolPtr(false), 0, stringPtr("shell-ended")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", false); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "shell-exit-wins", "operation-tree-cleanup", "output-drain", "publish-shell-ended"); err != nil {
			return err
		}
	}
	if id == "B1-C065-positive-empty-then-live" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-cancelled"), boolPtr(false), 1, stringPtr("operation-cancelled")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", false); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "TIOCSIG-once", "shell-returned", "operation-tree-cleanup", "output-drain", "publish-operation-cancelled"); err != nil {
			return err
		}
	}
	if id == "B1-C065-positive-empty-then-timeout" {
		if err := requireOutcome(id, trace, true, stringPtr("cancel-timeout"), boolPtr(false), 0, stringPtr("operation-failed")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", true); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "grace-expiry", "selected-shell-termination", "shell-exit-reap", "operation-tree-cleanup", "output-drain", "publish-operation-failed"); err != nil {
			return err
		}
	}
	if id == "B1-C065-completion-helper-cancel-window" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-cancelled"), boolPtr(false), 1, stringPtr("operation-cancelled")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "TIOCSIG-once", "helper-survives", "input-close", "shell-returned", "operation-tree-cleanup", "output-drain", "publish-operation-cancelled"); err != nil {
			return err
		}
	}
	if id == "B1-C065-completion-helper-finalize-window" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-finalized"), boolPtr(false), 1, stringPtr("operation-finalized")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "TIOCSIG-once", "helper-survives", "input-close", "shell-returned", "operation-tree-cleanup", "output-drain", "publish-operation-finalized"); err != nil {
			return err
		}
	}
	if id == "B1-C065-cancel-after-input-close" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-cancelled"), boolPtr(false), 0, stringPtr("operation-cancelled")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "existing-cleanup"); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "existing-cleanup-finished", "inspection-skipped", "publish-operation-cancelled"); err != nil {
			return err
		}
	}
	if id == "B1-C065-finalize-after-input-close" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-completed"), boolPtr(false), 0, stringPtr("operation-completed")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "existing-cleanup"); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "existing-cleanup-finished", "inspection-complete", "publish-operation-completed"); err != nil {
			return err
		}
	}
	if id == "B1-C065-cancel-cross-finalize" {
		if err := requireOutcome(id, trace, true, stringPtr("operation-cancelled"), boolPtr(false), 1, stringPtr("operation-cancelled")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", false); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "TIOCSIG-once", "shell-returned", "operation-tree-cleanup", "output-drain", "publish-operation-cancelled"); err != nil {
			return err
		}
	}
	if id == "B1-C065-shell-exit-before-timeout" {
		if err := requireOutcome(id, trace, true, stringPtr("shell-ended"), boolPtr(false), 0, stringPtr("shell-ended")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", false); err != nil {
			return err
		}
		if err := requireEventOrder(id, trace.Events, "shell-exit-wins-before-timeout", "operation-tree-cleanup", "output-drain", "publish-shell-ended"); err != nil {
			return err
		}
	}
	if oneOf(id, "B1-C065-shell-exit-after-timeout", "B1-C065-queued-input-close-after-timeout") {
		if err := requireOutcome(id, trace, true, stringPtr("cancel-timeout"), boolPtr(false), 0, stringPtr("operation-failed")); err != nil {
			return err
		}
		if err := requireLifecycleFacts(id, trace, "operation-tree-cleanup"); err != nil {
			return err
		}
		if err := requireExpectedField(id, trace.Expected, "timeout_shell_ended", true); err != nil {
			return err
		}
		if id == "B1-C065-shell-exit-after-timeout" {
			if err := requireEventOrder(id, trace.Events, "grace-timeout-selected", "selected-shell-termination", "shell-exit-after-timeout-reap-evidence", "operation-tree-cleanup", "output-drain", "publish-operation-failed"); err != nil {
				return err
			}
		} else if err := requireEventOrder(id, trace.Events, "input-close-queued", "grace-timeout-selected", "input-close-discarded", "selected-shell-termination", "shell-exit-reap", "operation-tree-cleanup", "output-drain", "publish-operation-failed"); err != nil {
			return err
		}
	}
	if oneOf(id, "B1-C062-cancel-during-cleanup", "B1-C062-finalize-after-return", "B1-C062-cancel-after-finalize", "B1-C062-shell-ended-cancel-race") {
		if !containsEvent(trace.Events, "operation-tree-cleanup") && !containsEvent(trace.Events, "cleanup-complete") {
			return fmt.Errorf("lifecycle cleanup barrier missing: %s", id)
		}
		if !containsEventBefore(trace.Events, "output-drain", "publish-"+publication) {
			return fmt.Errorf("lifecycle output drain precedes publication: %s", id)
		}
	}
	return nil
}

func containsEvent(events []string, want string) bool {
	for _, event := range events {
		if event == want {
			return true
		}
	}
	return false
}
func containsEventBefore(events []string, first, second string) bool {
	seen := false
	for _, event := range events {
		if event == first {
			seen = true
		}
		if event == second {
			return seen
		}
	}
	return false
}
func boolPtr(value bool) *bool       { return &value }
func stringPtr(value string) *string { return &value }

// Handwritten representatives of each approved shared clause. These frozen
// input/outcome/order contracts are independent of the mutable JSONL corpus;
// the corpus cannot substitute generic text pointers for concrete predicates.
var clauseScenarios = map[string][3]string{
	"B1-C001-contract": {`{"clause_scenario":true,"session_id":"session-A","peer_session_id":"session-B","inspection_ids":["inspect-1","inspect-2"]}`, `{"contract":"INV-001","handshake_accepted":false,"operation_result":null,"inspection_order":["inspect-1","inspect-2"]}`, "hello session-id-compare handshake-rejected"},
	"B1-C002-contract": {`{"clause_scenario":true,"writer_exited":true,"buffered_bytes_hex":"41","drain_offset":0}`, `{"contract":"INV-002","output_through":1,"operation_started_before_drain":false}`, "writer-exit exclusive-prestart-drain covering-mark operation_started"},
	"B1-C003-contract": {`{"clause_scenario":true,"path":"$UNDEFINED/file","exported_environment":{},"cwd":"/work"}`, `{"contract":"INV-003","undefined_expansion":"literal","expanded_absolute_path":"/work/$UNDEFINED/file","live_proof":"pending-B7.1"}`, "final-prompt_ready path-expansion absolute-resolution"},
	"B1-C004-contract": {`{"clause_scenario":true,"native_tag":"directory","protocol_tag":"directory-v2"}`, `{"contract":"INV-004","encodings_equal":false,"digest_domains_separate":true}`, "native-encoding protocol-encoding separate-digest-expectations"},
	"B1-C005-contract": {`{"clause_scenario":true,"first_operation":true,"bytes_written":0,"cancelled_before_start":true}`, `{"contract":"INV-005","output_start":0,"output_through":0,"boundary_stream":"pty"}`, "initial-pty-boundary prestart-cancel zero-byte-covering-mark"},
	"B1-C006-contract": {`{"clause_scenario":true,"current_stream":"pty","offset":0,"next_stream":"stdout","next_bytes_hex":"41"}`, `{"contract":"INV-006","first_mark":"pty","split_mark_offset":0,"mark_before_byte":true}`, "pty-boundary stdout-same-offset-mark stdout-first-byte"},
	"B1-C007-contract": {`{"clause_scenario":true,"startup_size":4096,"build_identity":null,"synthetic":true}`, `{"contract":"INV-007","ready_output_through":4096,"support_admitted":false,"build_measurements":"pending-B2.8"}`, "synthetic-startup-bound ready-watermark qualification-pending"},
	"B1-C008-contract": {`{"clause_scenario":true,"ready_received":false,"received_bytes_hex":"41","expected_bytes_hex":"41","EOF":true}`, `{"contract":"INV-008","fatal":true,"release_terminal":false}`, "bytes-before-ready terminal-EOF fatal-teardown"},
	"B1-C009-contract": {`{"clause_scenario":true,"startup_bytes_hex":"","synthetic":true}`, `{"contract":"INV-009","raw_start":0,"stream":"pty","elapsed_us":0,"prompt_bytes":0}`, "zero-offset-pty-mark startup-range ready-barrier"},
	"B1-C010-contract": {`{"clause_scenario":true,"loader_hex":"1801","submit_hex":"1802","source":"printf A\n"}`, `{"contract":"INV-010","independent_parse_checks":2,"post_source_LF":true,"operation_start_reset":false}`, "source-parse frame-parse loader submit operation-start-barrier"},
	"B1-C011-contract": {`{"clause_scenario":true,"source_defines_colon":true,"preceding_status":7,"errexit":true}`, `{"contract":"INV-011","suffix_command_lookup":false,"restored_status":7}`, "source-end hard-LF suffix-status-restore"},
	"B1-C012-contract": {`{"clause_scenario":true,"mutation":"trap","target":"INT","scope":"selected-persistent-Bash"}`, `{"contract":"INV-012","allowed":false,"partial_mutation":false,"required_builtin_set":"adapter-owned"}`, "expanded-argv reserved-target-check fail-stop-before-mutation"},
	"B1-C013-contract": {`{"clause_scenario":true,"request":"set -o posix","POSIXLY_CORRECT":"readonly-unset"}`, `{"contract":"INV-013","posix_enabled":false,"trap_mediation":"retained"}`, "posix-request readonly-assignment-rejection mediation-retained"},
	"B1-C014-contract": {`{"clause_scenario":true,"signal_spec":"sIgChLd","selected_build_signal_table":"pending-B2.8"}`, `{"contract":"INV-014","canonical_target":"CHLD","reserved":true,"portable_numeric_constant":false}`, "argv-expansion build-signal-canonicalization reserved-target-preflight"},
	"B1-C015-contract": {`{"clause_scenario":true,"request":"trap -p INT"}`, `{"contract":"INV-015","query_allowed":true,"signal_state_changed":false,"selected_build_result":"pending-B2.8"}`, "query-preflight ordinary-query-return"},
	"B1-C016-contract": {`{"clause_scenario":true,"adapter_helper_exits":true,"selected_shell_CHLD_trap":"unset"}`, `{"contract":"INV-016","application_CHLD_trap_runs":false,"persistent_state_mutated_by_CHLD":false}`, "helper-exit CHLD-unset persistent-state-preserved"},
	"B1-C017-contract": {`{"clause_scenario":true,"request":"trap 'exit 42' INT","scope":"top-level"}`, `{"contract":"INV-017","fail_stop":true,"INT_state_changed":false}`, "expanded-trap-request INT-preflight fail-stop"},
	"B1-C018-contract": {`{"clause_scenario":true,"helper_window":true,"INT_disposition":"ignored"}`, `{"contract":"INV-018","helper_survives_SIGINT":true,"final_INT_disposition":"unset","final_trap_query":""}`, "temporary-INT-ignore helper-cleanup builtin-trap-reset empty-INT-query"},
	"B1-C019-contract": {`{"clause_scenario":true,"scope":"nested-child","request":"trap 'exit 42' INT"}`, `{"contract":"INV-019","child_owns_handler":true,"selected_shell_INT":"unset"}`, "child-launch child-trap-install selected-shell-reservation-retained"},
	"B1-C020-contract": {`{"clause_scenario":true,"argv":["-n","kill"],"required_builtin":"kill"}`, `{"contract":"INV-020","fail_stop":true,"partial_mutation":false}`, "expanded-enable-argv complete-preflight fail-stop"},
	"B1-C021-contract": {`{"clause_scenario":true,"request":"unset -f awsh","readonly_function":true}`, `{"contract":"INV-021","function_preserved":true,"build_status_diagnostic":"pending-B2.8"}`, "unset-request readonly-function-rejection fixed-awsh-retained"},
	"B1-C022-contract": {`{"clause_scenario":true,"targets":["USR1","INT"],"mutation":"install-trap"}`, `{"contract":"INV-022","complete_preflight":true,"USR1_mutated":false,"INT_mutated":false,"fail_stop":true}`, "all-arguments-expanded whole-request-preflight reject-before-any-mutation"},
	"B1-C023-contract": {`{"clause_scenario":true,"request":"enable kill","required_builtin":"kill"}`, `{"contract":"INV-023","positive_enable_allowed":true,"reserved_state":"canonical"}`, "positive-enable-preflight ordinary-enable canonical-required-state"},
	"B1-C024-contract": {`{"clause_scenario":true,"request":"builtin shopt -so posix","next_request":"trap 'exit 42' INT"}`, `{"contract":"INV-024","posix_enabled":false,"next_request_mediated":true}`, "readonly-POSIXLY_CORRECT-rejection posix-stays-disabled next-INT-preflight"},
	"B1-C025-contract": {`{"clause_scenario":true,"request":"set -o posix","candidate_build":"unqualified"}`, `{"contract":"INV-025","status":null,"diagnostic":null,"qualification_owner":"B2.8","adapter_fail_stop_added":false}`, "ordinary-Bash-attempt observation-pending qualification-required"},
	"B1-C026-contract": {`{"clause_scenario":true,"request":"set -- alpha beta","posix_enabled":false}`, `{"contract":"INV-026","positional_parameters":["alpha","beta"],"posix_enabled":false}`, "set-positional ordinary-return positional-state-retained"},
	"B1-C027-contract": {`{"clause_scenario":true,"child_scope":"nested-Bash","child_traps":["CHLD","INT"]}`, `{"contract":"INV-027","child_traps_allowed":true,"selected_shell_traps":"unset"}`, "nested-shell-start child-trap-install selected-shell-unchanged"},
	"B1-C028-contract": {`{"clause_scenario":true,"command":"awsh gate gate-1","PATH":"/tmp/hostile"}`, `{"contract":"INV-028","helper":"/omegaflow-runtime/bin/awsh","argv":["bash-helper","--socket=/run/omegaflow/session/bash/helper.sock","gate","gate-1"],"PATH_lookup":false}`, "readonly-awsh-function absolute-helper-exec gate-ready"},
	"B1-C029-contract": {`{"clause_scenario":true,"prompt_state_cwd":"/work","allowed_trap_final_cwd":"/work/after","allowed_trap_export":"after"}`, `{"contract":"INV-029","reported_cwd":"/work/after","reported_export":"after","live_and_reported_state_aligned":true}`, "prompt_state helper-child-exit allowed-trap final-prompt_ready final-state-validation"},
	"B1-C030-contract": {`{"clause_scenario":true,"request":"builtin trap exit INT","same_identity_bypass":true}`, `{"contract":"INV-030","fatal":true,"supported_operation":false,"operation_result":null}`, "explicit-builtin-lookup interference-detected fatal-teardown"},
	"B1-C031-contract": {`{"clause_scenario":true,"source":"cat <<END\n","checker_status":0,"checker_stderr":"warning","checker_stdout":""}`, `{"contract":"INV-031","source_accepted":false,"requires_zero_status_and_empty_outputs":true}`, "source-syntax-check warning-observed source-rejected"},
	"B1-C032-contract": {`{"clause_scenario":true,"PS0_marker_valid":true,"public_started":true,"post_PS0_signal":false}`, `{"contract":"INV-032","source_released":false,"requires_start_released":true}`, "PS0-marker public-operation_started started_ack wait-post-PS0-signal"},
	"B1-C033-contract": {`{"clause_scenario":true,"prefix_bytes":4,"length_endianness":"big","request_half_closed":false}`, `{"contract":"INV-033","request_complete":false,"await_half_close":true,"phase_timer_reset":false}`, "length-prefix bounded-payload wait-request-EOF"},
	"B1-C034-contract": {`{"clause_scenario":true,"stdout_eof":true,"stderr_eof":false,"cleanup_deadline_expired":false}`, `{"contract":"INV-034","fatal":false,"operation_result":null,"FIFO_removal_allowed":false,"waiting_for":"stderr-EOF"}`, "cleanup keepalive-close stdout-EOF wait-stderr-EOF"},
	"B1-C035-contract": {`{"clause_scenario":true,"cancel_phase":"after-public-start-before-start_released"}`, `{"contract":"INV-035","queued":true,"signal_before_start_released":false,"operation_start_timer_reset":false}`, "public-operation_started queue-cancel started_ack start_released ordinary-cancel"},
	"B1-C036-contract": {`{"clause_scenario":true,"phase":"after-complete-execute-before-submit","shell_exit":true}`, `{"contract":"INV-036","fatal":true,"wire_operation_id":"op-active","operation_result":null}`, "execute-registered shell_exit fatal-start-teardown"},
	"B1-C037-contract": {`{"clause_scenario":true,"private_execute_bytes_attempted":0,"shell_exit_operation_id":""}`, `{"contract":"INV-037","operation_result":null,"public_reason":"shell_ended"}`, "empty-ID-shell_exit public-draining private-EOF zero-Awsh-reap"},
	"B1-C038-contract": {`{"clause_scenario":true,"execute_watermark_outstanding":true,"shell_exit":true}`, `{"contract":"INV-038","governing_epoch":"terminal-input-barrier","retain_for":["private-EOF","Awsh-reap","split-rollback"],"reset":false}`, "watermark-wait shell_exit capture-epoch closure-and-rollback"},
	"B1-C039-contract": {`{"clause_scenario":true,"captured_started_ms":0,"final_drain_started_ms":1000,"budget_ms":5000}`, `{"contract":"INV-039","captured_expires_ms":5000,"final_drain_expires_ms":6000,"captured_extended":false}`, "captured-epoch drain-entry independent-final-drain"},
	"B1-C040-contract": {`{"clause_scenario":true,"captured_due":true,"final_drain_due":true,"closure_complete":false}`, `{"contract":"INV-040","selected_expiry":"captured","successful_closed":false,"operation_result":null}`, "same-turn-deadlines captured-expiry-first fatal-teardown"},
	"B1-C041-contract": {`{"clause_scenario":true,"closure_complete_ms":4000,"final_drain_started_ms":1000,"now_ms":4500}`, `{"contract":"INV-041","captured_epoch_ended":true,"final_drain_remaining_ms":1500,"timer_reset":false}`, "private-closure rollback-complete captured-epoch-end remaining-final-drain"},
	"B1-C042-contract": {`{"clause_scenario":true,"execute_discarded":true,"late_input_arrived":true}`, `{"contract":"INV-042","operation_start_epoch_started":false,"source_submitted":false}`, "discard-execute late-terminal-input drain-only"},
	"B1-C043-contract": {`{"clause_scenario":true,"phase":"after-started_ack-before-start_released","shell_exit":true}`, `{"contract":"INV-043","fatal":true,"operation_result":null,"governing_epoch":"operation-start","reset":false}`, "started_ack shell_exit fatal-start-teardown"},
	"B1-C044-contract": {`{"clause_scenario":true,"execute_registered":true,"phase":"start_prepared-before-start_release"}`, `{"contract":"INV-044","shell_exit_operation_id":"op-active","fatal":true,"operation_result":null}`, "start_prepared active-ID-shell_exit fatal-start-teardown"},
	"B1-C045-contract": {`{"clause_scenario":true,"watermark_request":"execute","watermark_outstanding":true,"protocol_error_accepted":true}`, `{"contract":"INV-045","governing_epoch":"terminal-input-barrier","reset":false,"operation_result":null,"synthetic_status":null,"input_barrier_timeout_result":false,"late_input_starts_operation":false}`, "input-watermark-wait protocol_error retain-original-barrier private-closure-reap"},
	"B1-C046-contract": {`{"clause_scenario":true,"envoy_epoch":"terminal-input-barrier","sender_timer":"control-write"}`, `{"contract":"INV-046","governing_epoch":"terminal-input-barrier","sender_timer_controls_teardown":false}`, "protocol_error-accepted retain-Envoy-epoch ignore-sender-timer-as-lifecycle-owner"},
	"B1-C047-contract": {`{"clause_scenario":true,"active_epoch_started_ms":0,"private_EOF_ms":4000,"Awsh_reap_ms":4999}`, `{"contract":"INV-047","closure_within_ms":5000,"epoch_reset":false}`, "protocol_error private-EOF Awsh-reap"},
	"B1-C048-contract": {`{"clause_scenario":true,"private_EOF":true,"accepted_terminal_frame":null,"Awsh_status":0}`, `{"contract":"INV-048","fatal":true,"orderly":false,"operation_result":null}`, "unannounced-private-EOF zero-reap-is-teardown-only fatal-session"},
	"B1-C049-contract": {`{"clause_scenario":true,"protocol_error_code":"shell-launch","Awsh_status":0}`, `{"contract":"INV-049","fatal":true,"required_Awsh_exit":"nonzero","orderly_EOF":false}`, "shell-launch-error zero-reap-rejected fatal-session"},
	"B1-C050-contract": {`{"clause_scenario":true,"original_cause":"protocol-error","private_EOF":false,"epoch_expired":true}`, `{"contract":"INV-050","fatal":true,"original_cause_retained":true,"additional_failure":"missing-EOF"}`, "protocol_error closure-timeout retain-original-cause"},
	"B1-C051-contract": {`{"clause_scenario":true,"diagnostic_code":"unknown-future-code","message":"bounded explanation"}`, `{"contract":"INV-051","code_retained":true,"message_retained":true,"Reploy_termination_owner":"C8.2","operation_result":null}`, "diagnostic-capture bounded-retention termination-report-pending-C8.2"},
	"B1-C052-contract": {`{"clause_scenario":true,"watermark_outstanding":true,"queued_cancel":true,"resize_outstanding":true,"protocol_error":true}`, `{"contract":"INV-052","fatal":true,"new_state":false,"timer_reset":false,"resize_applied":false,"operation_result":null,"late_input_starts_operation":false}`, "protocol_error fatal-channel-failure resolve-crossed-requests"},
	"B1-C053-contract": {`{"clause_scenario":true,"execute_first_byte_attempted":true,"execute_complete":false}`, `{"contract":"INV-053","wire_operation_id":"","fatal":true,"governing_epoch":"operation-start","operation_result":null}`, "first-execute-byte partial-frame shell_exit fatal-start-phase"},
	"B1-C054-contract": {`{"clause_scenario":true,"mode":"split","execute_complete":false,"queued_cancel":true}`, `{"contract":"INV-054","fatal":true,"cancel_result":null,"rollback_required":true,"wire_operation_id":""}`, "partial-execute queued-cancel fatal-start rollback-under-captured-epoch"},
	"B1-C055-contract": {`{"clause_scenario":true,"phase":"live-inspection","protocol_error_variant":"unknown-code"}`, `{"contract":"INV-055","fatal":true,"operation_result":null,"synthetic_status":null,"cause_retained":true}`, "inspection-active protocol_error worker-and-channel-teardown"},
	"B1-C056-contract": {`{"clause_scenario":true,"active_Envoy_epoch":null,"protocol_error":true}`, `{"contract":"INV-056","new_epoch":"final-drain","budget_ms":5000,"reset_existing_epoch":false}`, "protocol_error Envoy-drain-entry start-existing-final-drain-epoch"},
	"B1-C057-contract": {`{"clause_scenario":true,"active_Envoy_epoch":null,"sender_control_write_active":true}`, `{"contract":"INV-057","fallback_epoch":"final-drain","sender_timer_suppresses_fallback":false}`, "protocol_error no-Envoy-epoch final-drain-fallback"},
	"B1-C058-contract": {`{"clause_scenario":true,"protocol_error":"valid","Awsh_reap":"signalled"}`, `{"contract":"INV-058","fatal":true,"operation_result":null,"synthetic_status":null,"Reploy_termination_owner":"C8.2"}`, "bounded-diagnostic signalled-reap fatal-termination-report"},
	"B1-C059-contract": {`{"clause_scenario":true,"private_EOF":true,"terminal_frame":null,"Awsh_reap":0}`, `{"contract":"INV-059","fatal":true,"reap_is_orderly":false,"operation_result":null}`, "protocol_error unannounced-EOF zero-reap-teardown-only"},
	"B1-C060-contract": {`{"clause_scenario":true,"protocol_error_code":"shell-launch","Awsh_reaped":false,"epoch_expired":true}`, `{"contract":"INV-060","required_exit":"nonzero","additional_failure":"unreaped-Awsh","original_cause_retained":true}`, "shell-launch-error epoch-expiry retain-cause-and-reap-failure"},
	"B1-C061-contract": {`{"clause_scenario":true,"frame":"input_close","operation_id":"op-1","completion_helper_pid":123}`, `{"contract":"INV-061","fields":["awsh-v1","input_close","operation-id","helper-pid"],"additional_fields_allowed":false}`, "direct-exec-helper-identity input_close exact-four-fields"},
	"B1-C062-contract": {`{"clause_scenario":true,"source_status":7,"mode":"split","completion_helper":"helper-1"}`, `{"contract":"INV-062","saved_source_status":7,"result_before_final_barrier":false,"helper_preserved_during_cleanup":true}`, "input_close close-operation-input terminate-descendants reap-adopted close-keepalives stdout-EOF stderr-EOF remove-FIFOs input_closed release-helper wait-empty-jobs final-prompt_ready readline-proof completed final-census PTY-drain covering-mark terminal-result"},
	"B1-C063-contract": {`{"clause_scenario":true,"live_function":"f","live_alias":"a","positional":["x"],"unexported_variable":"v"}`, `{"contract":"INV-063","Bash_local_state_retained":true,"Bash_local_state_in_wire":false}`, "source-return Bash-state-retained exported-wire-snapshot-only"},
	"B1-C064-contract": {`{"clause_scenario":true,"report":"prompt_ready","saved_status":7,"reported_status":0}`, `{"contract":"INV-064","fatal":true,"completion_accepted":false,"terminal_result":null}`, "completion-report stale-status-validation fatal-teardown"},
	"B1-C065-contract": {`{"clause_scenario":true,"cancel_before_input_close":true,"start_released":true,"foreground_validated":true}`, `{"contract":"INV-065","lifecycle_owner":"Envoy","TIOCSIG_count":1,"Awsh_lifecycle_transaction":false,"grace_reset":false}`, "grace-start foreground-sample validate-census TIOCSIG-once cleanup"},
	"B1-C066-contract": {`{"clause_scenario":true,"frame":"shell_exit","operation_id":"","status":7,"physical_cwd":"/work"}`, `{"contract":"INV-066","ordered_fields":["awsh-v1","shell_exit","","7","/work"],"arity":5,"direction":"Awsh-to-Envoy"}`, "empty-operation-ID status-scalar exact-terminal-frame"},
	"B1-C067-contract": {`{"clause_scenario":true,"failure":"operation-start-timeout","private_start_released":false}`, `{"contract":"INV-067","fatal":true,"operation_result":null,"synthetic_status":null}`, "operation-start-expiry fatal-diagnostic no-terminal-operation-result"},
	"B1-C068-contract": {`{"clause_scenario":true,"evidence_kind":"static-corpus","real_actors_ran":false}`, `{"contract":"INV-068","runtime_passed":false,"runtime_test_locator":null,"proof_status":"pending-owning-leaf"}`, "static-fixture-validation runtime-proof-pending last-prerequisite-owner"},
}

var clauseBarrierEpoch = deadlineExpectation{Owner: "envoy", Name: "terminal-input-barrier", BudgetMS: 5000, Reset: false, EntryBoundary: "acceptance of execute or continue with input_through beyond received terminal bytes"}

func checkClauseScenario(trace expectedTrace) error {
	rule, exists := clauseScenarios[trace.ID]
	if oneOf(trace.ID, "B1-C045-contract", "B1-C052-contract") {
		if trace.Epoch == nil || *trace.Epoch != clauseBarrierEpoch {
			return fmt.Errorf("clause loses captured barrier epoch: %s", trace.ID)
		}
	} else if trace.Epoch != nil {
		return fmt.Errorf("unexpected clause epoch: %s", trace.ID)
	}
	if !exists {
		return fmt.Errorf("missing concrete clause contract: %s", trace.ID)
	}
	var input, expected map[string]any
	if err := json.Unmarshal([]byte(rule[0]), &input); err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(rule[1]), &expected); err != nil {
		return err
	}
	if !reflect.DeepEqual(trace.Input, input) || !reflect.DeepEqual(trace.Expected, expected) || !reflect.DeepEqual(trace.Events, strings.Fields(rule[2])) {
		return fmt.Errorf("concrete clause predicates or order drift: %s", trace.ID)
	}
	return nil
}

// Independently authored matrix rules, never loaded from traces.jsonl. Loops
// express the contract's Cartesian cases; fixture expectations are compared
// with these rules before they can count as static coverage.
var matrixContracts = buildMatrixContracts()

func buildMatrixContracts() map[string]expectedTrace {
	rules := map[string]expectedTrace{}
	add := func(group, suffix, input, expected, events string, epoch *deadlineExpectation) {
		id := "B1-" + group + "-" + suffix
		tr := expectedTrace{ID: id, Events: strings.Fields(events), Epoch: epoch}
		if err := json.Unmarshal([]byte(input), &tr.Input); err != nil {
			panic(err)
		}
		if err := json.Unmarshal([]byte(expected), &tr.Expected); err != nil {
			panic(err)
		}
		tr.Expected["contract"] = "INV-" + strings.TrimPrefix(group, "C")
		if _, exists := rules[id]; exists {
			panic("duplicate matrix rule: " + id)
		}
		rules[id] = tr
	}
	epoch := func(owner, name string, budget int) *deadlineExpectation {
		return &deadlineExpectation{Owner: owner, Name: name, BudgetMS: budget}
	}
	generic := "scenario-input contract-defined-outcome"
	phases := strings.Fields("before-first-byte partial-execute complete-execute submit start-prepared start-release-written started public-operation-started started-ack start-released")
	for i, name := range strings.Fields("controller-connect envoy-accept envoy-hello envoy-launch controller-ready") {
		owners := []string{"controller", "envoy", "envoy", "envoy", "controller"}
		names := []string{"controller-connect", "accept", "hello", "launch-readiness", "controller-ready"}
		starts := []string{"bootstrap-exec-LF-complete", "both-listeners-bound", "both-connections-accepted", "complete-valid-hello-accepted", "complete-hello-written"}
		add("C001", name, fmt.Sprintf(`{"scenario":%q}`, name), `{"actor_local":true,"partial_progress_resets":false}`, starts[i]+" independent-actor-epoch-start complete-covered-work-or-timeout", epoch(owners[i], names[i], 10000))
	}
	for _, sender := range strings.Fields("controller envoy awsh") {
		suffix, input := "control-write-"+sender, fmt.Sprintf(`{"sender":%q}`, sender)
		if sender == "envoy" {
			suffix = "individual-control-write"
			input = `{"sender":"envoy","scenario":"individual-control-write"}`
		}
		add("C001", suffix, input, `{"partial_progress_resets":false,"governs_lifecycle_crossing":false}`, "first-attempted-frame-byte complete-write-or-session-failure", epoch(sender, "control-write", 5000))
	}
	for _, stream := range strings.Fields("pty stdout stderr") {
		for _, outcome := range strings.Fields("start failed-before-start cancelled-before-start repeat-current-stream") {
			boundary, status := stream, "null"
			if oneOf(outcome, "failed-before-start", "cancelled-before-start") {
				boundary, status = "pty", "false"
			}
			add("C005", stream+"-"+outcome, fmt.Sprintf(`{"stream":%q,"offset":0,"outcome":%q}`, stream, outcome), fmt.Sprintf(`{"output_start":0,"output_through":0,"status_present":%s,"boundary_stream":%q}`, status, boundary), "initial-pty-mark-at-zero "+boundary+"-boundary-mark-at-zero "+outcome, nil)
		}
	}
	for _, name := range strings.Fields("undefined-variable defined-variable tilde tilde-user relative-after-cd symlink nested-special missing unsupported-top-level directory-tag directory-v2-tag") {
		add("C003", name, fmt.Sprintf(`{"path_scenario":%q}`, name), `{"live_resolution":"pending-B7.1"}`, generic, nil)
	}
	for _, name := range strings.Fields("mutate-before-read mutate-during-read mutate-between-snapshots byte-change length-change replace-path metadata-change directory-insert directory-remove directory-rename symlink-target-change missing-identity-metadata unchanged entry-budget file-byte-budget path-budget worker-channel-isolation worker-result-first worker-cancel-first finalized-result-first blocked-worker-cancel-timeout finalize-preserves-status") {
		failure := "null"
		if oneOf(name, "mutate-before-read", "mutate-during-read", "mutate-between-snapshots", "byte-change", "length-change", "replace-path", "metadata-change", "directory-insert", "directory-remove", "directory-rename", "symlink-target-change", "missing-identity-metadata") {
			failure = `"inspection-unstable"`
		}
		if oneOf(name, "entry-budget", "file-byte-budget", "path-budget") {
			failure = `"inspection-limit"`
		}
		if name == "blocked-worker-cancel-timeout" {
			failure = `"inspection-cancel-timeout"`
		}
		add("C003", name, fmt.Sprintf(`{"scenario":%q}`, name), `{"inspection_failure":`+failure+`,"live_proof":"pending-B7.3"}`, generic, nil)
	}
	for i, name := range strings.Fields("relative defined undefined tilde tilde-user") {
		paths := []string{"out", "$HOME/out", "$UNDEFINED/out", "~/out", "~other/out"}
		resolved := []string{"/work/out", "/home/test/out", "/work/$UNDEFINED/out", "/home/test/out", "/home/other/out"}
		add("C003", "path-example-"+name, fmt.Sprintf(`{"configured_path":%q,"physical_cwd":"/work","exported_env":{"HOME":"/home/test"},"user_homes":{"other":"/home/other"}}`, paths[i]), fmt.Sprintf(`{"resolved_path":%q,"live_Bash_expansion":false}`, resolved[i]), generic, nil)
	}
	for _, name := range strings.Fields("suffix-colon-function suffix-colon-disabled") {
		for _, status := range []int{0, 1, 255} {
			add("C011", fmt.Sprintf("%s-%d", name, status), fmt.Sprintf(`{"preceding_status":%d,"source_variant":%q,"errexit":true}`, status, name), fmt.Sprintf(`{"status_preserved":%d,"command_lookup":false}`, status), generic, nil)
		}
	}
	for _, signal := range strings.Fields("CHLD SIGCHLD chld sigchld INT SIGINT int sigint DEBUG ERR RETURN selected-numeric-CHLD selected-numeric-INT") {
		for _, args := range strings.Fields("direct expanded mixed-targets multiple-targets") {
			add("C020", signal+"-"+args, fmt.Sprintf(`{"signal":%q,"arguments":%q}`, signal, args), `{"fail_stop":true,"partial_mutation":false}`, generic, nil)
		}
	}
	for _, action := range strings.Fields("disable dynamic-load replace dynamic-unload") {
		add("C021", "builtin-"+action, fmt.Sprintf(`{"required_builtin":"each-required-builtin","action":%q}`, action), `{"fail_stop":true,"partial_mutation":false}`, generic, nil)
	}
	for _, command := range strings.Fields("set shopt builtin-set builtin-shopt assignment unset") {
		for _, args := range strings.Fields("direct expanded combined") {
			add("C024", command+"-"+args, fmt.Sprintf(`{"command":%q,"arguments":%q,"next_command":"trap 'exit 42' INT"}`, command, args), `{"posix":false,"POSIXLY_CORRECT":"readonly-unset","next_mutation":"mediated"}`, generic, nil)
		}
	}
	for _, name := range strings.Fields("minimum-source maximum-source multiline comments quotes heredoc unterminated-heredoc-warning-zero-status trailing-LF aliases extglob posix history interactive-comments reserved-namespace reserved-input adjacent-step-input duplicate-helper crossed-helper wrong-ID no-redisplay pre-public-start-failure post-public-start-failure checker-nonzero checker-stdout checker-stderr") {
		add("C031", name, fmt.Sprintf(`{"scenario":%q}`, name), `{"both_parse_checks_require":{"status":0,"stdout":"","stderr":""},"build_concrete_value":"pending-B2.8"}`, generic, nil)
	}
	for _, name := range strings.Fields("prefix-every-split payload-every-split short-prefix short-payload zero-length oversized-length trailing-data ancillary-data missing-half-close premature-EOF small-socket-buffer max-request max-reply positive-short-write") {
		add("C033", name, fmt.Sprintf(`{"scenario":%q}`, name), `{"bounded_transport":true,"existing_phase_deadline":true,"socket_execution":"pending-B2.2"}`, generic, nil)
	}
	for _, phase := range phases {
		pending := !oneOf(phase, "before-first-byte", "start-released")
		signals, outcomes := "null", `["source-rejection-without-signal","committed-start-then-one-signal"]`
		if phase == "before-first-byte" {
			signals = "0"
			outcomes = "[]"
		}
		if phase == "start-released" {
			signals = "1"
			outcomes = "[]"
		}
		add("C035", phase, fmt.Sprintf(`{"cancel_phase":%q}`, phase), fmt.Sprintf(`{"prestart_cancel":%t,"retain_cancel":%t,"signal_after":"start-released","successful_signals":%s,"pending_outcomes":%s}`, phase == "before-first-byte", pending, signals, outcomes), "cancel-accepted finish-or-reject-start apply-selected-outcome", nil)
		for _, mode := range strings.Fields("pty split") {
			for _, cancel := range []bool{false, true} {
				for _, outstanding := range []bool{false, true} {
					if outstanding && !oneOf(phase, "before-first-byte", "start-released") {
						continue
					}
					suffix := fmt.Sprintf("%s-%s-cancel-%t", phase, mode, cancel)
					active := "op"
					if oneOf(phase, "before-first-byte", "partial-execute") {
						active = ""
					}
					inp := fmt.Sprintf(`{"phase":%q,"mode":%q,"queued_cancel":%t,"active_operation_id":%q,"watermark_outstanding":%t`, phase, mode, cancel, active, outstanding)
					fatal := !oneOf(phase, "before-first-byte", "start-released")
					result, drain := "null", "null"
					if !fatal {
						drain = `"shell_ended"`
					}
					if phase == "start-released" {
						result = `"shell-ended"`
					}
					out := fmt.Sprintf(`{"fatal":%t,"operation_result":%s,"public_drain":%s,"awsh_reap":0,"private_EOF":true`, fatal, result, drain)
					name := "operation-start"
					if phase == "start-released" {
						name = "operation-cleanup"
					}
					if outstanding {
						suffix += "-watermark-outstanding"
						req := "execute"
						if phase == "start-released" {
							req = "continue"
						}
						inp += fmt.Sprintf(`,"watermark_request":%q`, req)
						out += `,"late_input_starts_operation":false,"captured_epoch_retained_until":"private-closure-and-split-rollback"`
						name = "terminal-input-barrier"
					}
					add("C036", suffix, inp+"}", out+"}", phase+" private-shell-exit private-EOF awsh-reap", epoch("envoy", name, 5000))
				}
			}
		}
	}
	for _, captured := range strings.Fields("terminal-input-barrier operation-start") {
		for _, winner := range strings.Fields("captured-expiry both-expire closure-then-drain-expiry success") {
			deadline := "null"
			if oneOf(winner, "captured-expiry", "both-expire") {
				deadline = fmt.Sprintf("%q", captured)
			}
			if winner == "closure-then-drain-expiry" {
				deadline = `"final-drain"`
			}
			add("C040", captured+"-"+winner, fmt.Sprintf(`{"captured_epoch":%q,"winner":%q,"captured_started_ms":0,"drain_started_ms":1000}`, captured, winner), fmt.Sprintf(`{"selected_deadline":%s,"operation_result":null,"successful_closed":%t,"same_turn_priority":"captured","late_input_starts_operation":false}`, deadline, winner == "success"), "capture-epoch enter-final-drain "+winner, epoch("envoy", captured, 5000))
		}
	}
	for _, phase := range strings.Fields("launch ready idle execute-input-barrier continue-input-barrier partial-execute complete-execute submit start-prepared start-release-written started public-operation-started started-ack start-released running gated continuing grace cleanup live-inspection inspection-cancellation drain") {
		name, budget := "final-drain", 5000
		switch {
		case phase == "launch":
			name, budget = "launch-readiness", 10000
		case oneOf(phase, "execute-input-barrier", "continue-input-barrier"):
			name = "terminal-input-barrier"
		case oneOf(phase, phases[1:]...):
			name = "operation-start"
		case phase == "grace":
			name = "grace"
		case phase == "cleanup":
			name = "operation-cleanup"
		case phase == "inspection-cancellation":
			name = "inspection-cancellation"
		}
		for _, variant := range strings.Fields("valid malformed unknown-code stalled-EOF reset zero-reap nonzero-reap signalled-reap missing-EOF unreaped expiry") {
			add("C055", phase+"-"+variant, fmt.Sprintf(`{"phase":%q,"variant":%q}`, phase, variant), `{"fatal":true,"operation_result":null,"synthetic_status":null,"reap_is_orderly":false,"cause_retained":true,"termination_owner":"C8.2","control_write_is_governing_epoch":false}`, phase+" protocol-error fatal-teardown", epoch("envoy", name, budget))
		}
	}
	for _, request := range strings.Fields("execute continue") {
		for _, variant := range strings.Fields("valid malformed unknown-code") {
			add("C052", request+"-"+variant+"-cancel-resize", fmt.Sprintf(`{"watermark_request":%q,"variant":%q,"queued_cancel":true,"resize_outstanding":true}`, request, variant), `{"fatal":true,"operation_result":null,"input_barrier_timeout_result":false,"new_state":false,"cause_retained":true}`, "input-barrier-start protocol-error channel-failure", epoch("envoy", "terminal-input-barrier", 5000))
		}
	}
	for _, request := range strings.Fields("cancel finalize") {
		for _, winner := range strings.Fields("input-close shell-exit live-foreground grace-expiry invalid-foreground wrong-session unclassifiable unexpected-live-member ioctl-failure") {
			signals := 0
			if winner == "live-foreground" {
				signals = 1
			}
			fatal := oneOf(winner, "invalid-foreground", "wrong-session", "unclassifiable", "unexpected-live-member", "ioctl-failure")
			add("C065", request+"-"+winner, fmt.Sprintf(`{"request":%q,"winner":%q,"census":"real-pidfd-tree"}`, request, winner), fmt.Sprintf(`{"signal_count":%d,"fatal":%t,"grace_starts_before":"foreground-sampling","timeout_shell_ended":%t}`, signals, fatal, winner == "grace-expiry"), "grace-start TIOCGPGRP "+winner, epoch("envoy", "grace", 5000))
		}
	}
	for _, outcome := range strings.Fields("gate-ready gate-continued gate-interrupted") {
		for _, lifecycle := range []bool{false, true} {
			for _, sent := range []bool{false, true} {
				event := map[string]string{"gate-ready": "operation_ready", "gate-continued": "operation_continued", "gate-interrupted": "operation_gate_interrupted"}[outcome]
				fatal := outcome == "gate-continued" && !sent
				pub := fmt.Sprintf("%q", event)
				if lifecycle || fatal {
					pub = "null"
				}
				count := 1
				if fatal {
					count = 0
				}
				add("C065", fmt.Sprintf("%s-lifecycle-%s-continue-%s", outcome, map[bool]string{false: "False", true: "True"}[lifecycle], map[bool]string{false: "False", true: "True"}[sent]), fmt.Sprintf(`{"private_outcome":%q,"lifecycle_first":%t,"continue_sent":%t}`, outcome, lifecycle, sent), fmt.Sprintf(`{"public_gate_event":%s,"private_outcomes":%d,"repair_loop":false,"fatal":%t}`, pub, count, fatal), "serialized-acceptance "+outcome+" lifecycle-outcome", nil)
			}
		}
	}
	for _, state := range strings.Fields("Cancelling Finalizing") {
		for _, event := range strings.Fields("operation_ready operation_continued operation_gate_interrupted") {
			add("C065", "controller-"+strings.ToLower(state)+"-"+event, fmt.Sprintf(`{"controller_state":%q,"crossed_event":%q}`, state, event), fmt.Sprintf(`{"controller_state":%q,"handoff_work":false}`, state), generic, nil)
		}
	}
	for _, terminal := range strings.Fields("shell_exit closed") {
		for _, variant := range strings.Fields("orderly premature-EOF trailing-frame reset signal nonzero-reap missing-EOF missing-reap") {
			add("C048", terminal+"-"+variant, fmt.Sprintf(`{"terminal":%q,"variant":%q}`, terminal, variant), fmt.Sprintf(`{"orderly":%t,"private_EOF_required":true,"awsh_reap_required":0,"closed_after_shell_exit":false}`, variant == "orderly"), generic, nil)
		}
	}
	add("C037", "shutdown-first", `{"scenario":"shutdown-first"}`, `{"public_reason":"requested-shutdown","terminal_forms":["closed","shell_exit"]}`, generic, nil)
	add("C037", "shell-exit-first", `{"scenario":"shell-exit-first"}`, `{"public_reason":"shell_ended","terminal_forms":["shell_exit"]}`, generic, nil)
	add("C010", "start-order", `{"scenario":"start-order"}`, `{"reset_deadline":false,"authored_input_after":"public-operation-started-complete","cancel_actionable_after":"private-start-released"}`, "input-watermark-satisfied operation-start-epoch mode-setup private-execute private-submit PTY-1802 helper-source helper-start-prepared private-start-prepared fresh-output-drain-and-mark private-start-release private-started public-operation-started-complete private-started-ack post-PS0-signal private-start-released", epoch("envoy", "operation-start", 5000))
	add("C062", "completion-order", `{"scenario":"completion-order"}`, `{"cleanup_timer_resets":0,"inspection_after":"cleanup-epoch-ended","saved_source_status_preserved":true}`, "helper-prompt-state private-input-close cleanup-epoch input-permanently-closed terminate-and-reap-descendants-exclude-completion-helper close-keepalives stdout-EOF stderr-EOF close-readers remove-FIFOs private-input-closed helper-exit restore-unset-INT wait-record-removal empty-job-table adapter-validation helper-state-bearing-prompt-ready fresh-Readline-termios private-completed final-census fresh-PTY-drain-and-mark cleanup-epoch-ended inspection public-terminal-result", epoch("envoy", "operation-cleanup", 5000))
	add("C028", "gate-success-order", `{"scenario":"gate-success-order"}`, `{"gate_outcomes":1,"repair_loop":false}`, "helper-gate-connected private-gate-ready public-operation-ready continue-watermark-satisfied private-continue gate-reply-epoch complete-accepted-reply-write private-gate-continued public-operation-continued", epoch("awsh", "gate-reply", 5000))
	for _, channel := range strings.Fields("public private helper") {
		for _, variant := range strings.Fields("nominal wrong-direction wrong-arity unknown-type duplicate out-of-order invalid-utf8 nul signed-number padded-number non-decimal scalar-min scalar-max scalar-overflow aggregate-max aggregate-overflow fragmentation concatenation truncated trailing") {
			accept, pending := "false", "null"
			if oneOf(variant, "nominal", "scalar-min", "scalar-max", "aggregate-max", "fragmentation", "concatenation") {
				accept = "true"
			}
			if oneOf(variant, "duplicate", "out-of-order") {
				accept = "null"
				pending = `"pending"`
			}
			add("C066", channel+"-"+variant, fmt.Sprintf(`{"channel":%q,"variant":%q}`, channel, variant), fmt.Sprintf(`{"wire_accept":%s,"runtime_ordering":%s}`, accept, pending), generic, nil)
		}
	}
	return rules
}

// Each unexpanded trace has a rule independent of the trace file. Auxiliary
// examples bind to their separately validated corpus; wire rows bind to bytes.
func checkNonExpandedTrace(c conformanceCase, tr expectedTrace, inv inventory, aux auxiliaryFixtures) error {
	want, found := matrixContracts[c.ID]
	if !found {
		want = expectedTrace{ID: c.ID, Events: []string{"scenario-input", "contract-defined-outcome"}}
		switch {
		case strings.HasPrefix(c.ID, "B1-PACKAGE-"):
			leaf, ok := inv.Leaves[strings.TrimPrefix(c.ID, "B1-PACKAGE-")]
			if !ok {
				return fmt.Errorf("unknown package trace: %s", c.ID)
			}
			want.Input = map[string]any{"responsibility": leaf.Implementation}
			want.Expected = map[string]any{"acceptance": leaf.Acceptance, "contract": c.Requirement}
			want.Events = []string{"leaf-isolated-acceptance", "retained-crossings"}
		case strings.HasPrefix(c.ID, "B1-C066-wire-"):
			ref, ok := aux.WireRefs[c.ID]
			if !ok || c.Fixture != ref {
				return fmt.Errorf("wire trace row binding drift: %s", c.ID)
			}
			want.Input = map[string]any{"channel": strings.TrimSuffix(ref.File, ".jsonl"), "row_sha256": ref.ID}
			want.Expected = map[string]any{"static_codec_corpus": true, "contract": "INV-066"}
		case strings.HasPrefix(c.ID, "B1-AUX-"):
			if c.ID != "B1-AUX-"+c.Fixture.ID {
				return fmt.Errorf("auxiliary identity drift: %s", c.ID)
			}
			want.Input = map[string]any{"static_example": c.Fixture.ID, "live_evidence": false}
			want.Events = []string{"static-input", "contract-defined-expectation"}
			switch c.Fixture.File {
			case "startup.jsonl":
				f, ok := aux.Startups[c.Fixture.ID]
				if !ok {
					return fmt.Errorf("unknown startup trace: %s", c.ID)
				}
				want.Expected = map[string]any{"synthetic_outcome": f.Expected, "contract": "INV-008"}
			case "inspection.jsonl":
				f, ok := aux.Inspections[c.Fixture.ID]
				if !ok {
					return fmt.Errorf("unknown digest trace: %s", c.ID)
				}
				want.Expected = map[string]any{"algorithm": f.Algorithm, "sha256": f.SHA256, "contract": "INV-004"}
			case "awsh-frames.json":
				f, ok := aux.Frames[c.Fixture.ID]
				if !ok {
					return fmt.Errorf("unknown frame trace: %s", c.ID)
				}
				want.Expected = map[string]any{"direction": f.Direction, "payload_hex": f.PayloadHex, "frame_hex": f.FrameHex, "contract": "INV-066"}
			default:
				return fmt.Errorf("unknown auxiliary corpus: %s", c.ID)
			}
		case oneOf(c.ID, "B1-C010-canonical-frame-pty-0", "B1-C010-canonical-frame-pty-255", "B1-C010-canonical-frame-split-0", "B1-C010-canonical-frame-split-255"):
			mode := "pty"
			if strings.Contains(c.ID, "-split-") {
				mode = "split"
			}
			status := float64(0)
			if strings.HasSuffix(c.ID, "-255") {
				status = 255
			}
			want.Input = map[string]any{"mode": mode, "status": status, "source": "printf 'héllo\\n'\n"}
			if !reflect.DeepEqual(tr.Input, want.Input) || len(tr.Expected) != 6 || tr.Expected["contract"] != "INV-010" || !reflect.DeepEqual(tr.Events, want.Events) || tr.Epoch != nil {
				return fmt.Errorf("canonical frame scenario drift: %s", c.ID)
			}
			return checkCanonicalFrame(tr)
		default:
			return fmt.Errorf("missing independent trace rule: %s", c.ID)
		}
	}
	if !reflect.DeepEqual(tr.Input, want.Input) || !reflect.DeepEqual(tr.Expected, want.Expected) || !reflect.DeepEqual(tr.Events, want.Events) || !reflect.DeepEqual(tr.Epoch, want.Epoch) {
		return fmt.Errorf("independent trace rule drift: %s (input=%t expected=%t events=%t epoch=%t)", c.ID, reflect.DeepEqual(tr.Input, want.Input), reflect.DeepEqual(tr.Expected, want.Expected), reflect.DeepEqual(tr.Events, want.Events), reflect.DeepEqual(tr.Epoch, want.Epoch))
	}
	return nil
}

// Authored responsibility rules freeze the approved catalogue assignments;
// neither case metadata nor mutable traces select an implementation owner.
func checkCaseBinding(c conformanceCase, inv inventory, aux auxiliaryFixtures) error {
	owner, closure, test := "", "", "TestConformanceInventory"
	fixture := caseRef{"traces.jsonl", c.ID}
	parts := strings.Split(c.ID, "-")
	if len(parts) < 3 {
		return fmt.Errorf("invalid case binding identity: %s", c.ID)
	}
	group := parts[1]
	switch group {
	case "PACKAGE":
		owner = strings.TrimPrefix(c.ID, "B1-PACKAGE-")
		closure = owner
	case "AUX":
		id := strings.TrimPrefix(c.ID, "B1-AUX-")
		switch {
		case strings.HasPrefix(id, "startup-"):
			owner, closure, test = "B3.2", "B3.4", "TestSyntheticStartupExpectations"
			fixture = caseRef{"startup.jsonl", id}
		case strings.HasPrefix(id, "digest-"):
			owner, closure, test = "B7.1", "B7.3", "TestStaticInspectionDigestExamples"
			fixture = caseRef{"inspection.jsonl", id}
		case strings.HasPrefix(id, "private-"), strings.HasPrefix(id, "helper-"):
			owner, closure, test = "B2.2", "B2.8", "TestFrozenAwshHexFrames"
			fixture = caseRef{"awsh-frames.json", id}
		}
	default:
		switch group {
		case "C001", "C068":
			owner, closure = "B1.3", "B8.4"
		case "C002":
			owner, closure = "B3.3", "B3.4"
		case "C003", "C004":
			owner, closure = "B7.1", "B7.3"
		case "C005", "C006":
			owner, closure = "B5.2", "B5.3"
		case "C007":
			owner, closure = "B2.8", "B3.2"
		case "C008", "C009":
			owner, closure = "B3.2", "B3.4"
		case "C010":
			owner, closure = "B2.5", "B3.4"
		case "C011", "C031":
			owner, closure = "B2.5", "B2.8"
		case "C012", "C013", "C014", "C015", "C016", "C017", "C019", "C020", "C021", "C022", "C023", "C024", "C025", "C026", "C027":
			owner, closure = "B2.4", "B2.8"
		case "C018":
			owner, closure = "B2.7", "B6.5"
		case "C028":
			owner, closure = "B4.3", "B4.4"
		case "C029", "C061":
			owner, closure = "B2.7", "B6.3"
		case "C030":
			owner, closure = "B8.1", "B8.4"
		case "C032":
			owner, closure = "B2.6", "B3.4"
		case "C033":
			owner, closure = "B2.2", "B2.8"
		case "C034":
			owner, closure = "B2.2", "B5.3"
		case "C035":
			owner, closure = "B4.2", "B6.5"
		case "C036", "C037", "C038", "C039", "C040", "C041", "C042", "C043", "C044", "C045", "C046", "C047", "C048", "C049", "C050", "C051", "C052", "C053", "C054", "C055", "C056", "C057", "C058", "C059", "C060":
			owner, closure = "B8.3", "B8.4"
		case "C062", "C064":
			owner, closure = "B6.3", "B6.5"
		case "C063":
			owner, closure = "B2.7", "B2.8"
		case "C065":
			owner, closure = "B4.4", "B8.4"
		case "C066":
			owner, closure = "B1.2", "B8.4"
		case "C067":
			owner, closure = "B1.1", "B8.4"
		}
		suffix := strings.TrimPrefix(c.ID, "B1-"+group+"-")
		switch group {
		case "C001":
			switch suffix {
			case "controller-connect", "controller-ready":
				owner, closure = "C3.1", "C3.3"
			case "envoy-accept", "envoy-hello", "individual-control-write":
				owner, closure = "B3.1", "B3.4"
			case "envoy-launch":
				owner, closure = "B3.2", "B3.4"
			case "control-write-controller":
				owner, closure, test = "C3.1", "C3.3", "TestDeclarativeTracePredicates"
			case "control-write-awsh":
				owner, closure, test = "B2.2", "B2.8", "TestDeclarativeTracePredicates"
			}
		case "C003":
			if strings.HasPrefix(suffix, "path-example-") {
				owner = "B2.7"
			}
			if oneOf(suffix, "worker-channel-isolation", "worker-result-first", "worker-cancel-first", "finalized-result-first", "blocked-worker-cancel-timeout", "finalize-preserves-status") {
				owner = "B7.3"
			}
		case "C010":
			if suffix == "start-order" {
				owner = "B3.3"
			}
			if strings.HasPrefix(suffix, "canonical-frame-") {
				closure = "B2.8"
			}
		case "C027":
			if suffix == "explicit-builtin-bypass" {
				owner, closure = "B8.1", "B8.4"
			}
		case "C034":
			if strings.HasPrefix(suffix, "setup-") || strings.HasPrefix(suffix, "rollback-") {
				owner, closure = "B5.1", "B6.3"
			} else if suffix != "contract" {
				owner, closure = "B5.3", "B6.3"
			}
		case "C036":
			if strings.HasSuffix(suffix, "-watermark-outstanding") {
				test = "TestDeclarativeTracePredicates"
			}
		case "C037", "C048":
			if suffix != "contract" {
				owner = "B8.2"
			}
		case "C062":
			if suffix != "contract" && suffix != "completion-order" {
				owner, closure = "B6.2", "B6.6"
				if strings.HasPrefix(suffix, "exclusive-") || oneOf(suffix, "planned-end-status", "finalization-failure-invalid-range", "user-cancel-invalid-range") {
					owner = "B6.4"
				}
				if strings.HasPrefix(suffix, "compiler-") || suffix == "wait-for-preserved" {
					owner = "B6.6"
				}
			}
		case "C065":
			if suffix != "contract" {
				closure = "B6.5"
			}
			for _, request := range []string{"cancel", "finalize"} {
				for _, winner := range strings.Fields("input-close shell-exit live-foreground grace-expiry invalid-foreground wrong-session unclassifiable unexpected-live-member ioctl-failure") {
					if suffix == request+"-"+winner {
						owner = "B4.2"
					}
				}
			}
			if strings.HasPrefix(suffix, "gate-") {
				owner, closure = "B4.3", "B4.4"
			}
			if strings.HasPrefix(suffix, "controller-") {
				owner, closure = "C2.3", "C3.3"
			}
		case "C066":
			if strings.HasPrefix(suffix, "wire-") {
				ref, ok := aux.WireRefs[c.ID]
				if !ok {
					return fmt.Errorf("unknown wire binding: %s", c.ID)
				}
				fixture = ref
				closure = "B1.3"
				switch ref.File {
				case "controller.jsonl", "envoy.jsonl":
					owner, test = "B1.1", "TestGoldenPublicCorpus"
				case "public-invalid.jsonl", "public-maximum.jsonl":
					owner, test = "B1.1", "TestFrozenInvalidAndMaximumCorpus"
				case "private.jsonl":
					owner, test = "B1.2", "TestPrivateGoldenForms"
				case "helper.jsonl":
					owner, test = "B1.2", "TestHelperGoldenForms"
				}
			}
		}
	}
	leaf, ok := inv.Leaves[owner]
	closing, closes := inv.Leaves[closure]
	if !ok || !closes || c.ImplementationLeaf != owner || c.ClosureLeaf != closure || c.Package != strings.Split(owner, ".")[0] || c.Prerequisites != leaf.Prerequisites || c.ClosurePrerequisites != closing.Prerequisites || c.RuntimeTest.Leaf != owner || c.StaticTest != test || c.Fixture != fixture {
		return fmt.Errorf("independent owner/test/fixture binding drift: %s", c.ID)
	}
	return nil
}

func checkCaseMap(cases []conformanceCase, inv inventory, traces map[string]expectedTrace, refs map[caseRef]bool, tests map[string]bool, aux auxiliaryFixtures) error {
	seen, coverage := map[string]bool{}, map[string]bool{}
	wireCoverage := map[caseRef]bool{}
	concreteClauses := map[string]bool{}
	requiredCases := map[string]bool{}
	for _, id := range inv.CaseIDs {
		if id == "" || requiredCases[id] {
			return fmt.Errorf("duplicate or empty frozen case identity: %s", id)
		}
		requiredCases[id] = true
	}
	requirements := map[string]bool{}
	for _, r := range inv.Requirements {
		if r.ID == "" || r.Text == "" || requirements[r.ID] {
			return fmt.Errorf("duplicate or empty requirement %s", r.ID)
		}
		requirements[r.ID] = true
	}
	if err := checkPackageRequirementBijection(inv); err != nil {
		return err
	}
	if err := checkAuthorityBindings(inv.Authorities); err != nil {
		return err
	}
	if inv.Source != approvedAuthorities[len(approvedAuthorities)-1] {
		return fmt.Errorf("clause extraction source is not the approved implementation plan")
	}
	expandedCount := 0
	for _, r := range inv.PackageRequirements {
		if requirements[r.ID] {
			return fmt.Errorf("invalid package requirement %s", r.ID)
		}
		requirements[r.ID] = true
	}
	for _, c := range cases {
		leaf, ok := inv.Leaves[c.ImplementationLeaf]
		closure, closes := inv.Leaves[c.ClosureLeaf]
		trace, traced := traces[c.ID]
		if c.ID == "" || !requiredCases[c.ID] || seen[c.ID] || !requirements[c.Requirement] || !ok || !closes {
			return fmt.Errorf("duplicate, missing or unknown identity: %s", c.ID)
		}
		if err := checkCaseBinding(c, inv, aux); err != nil {
			return err
		}
		if c.Package != strings.Split(c.ImplementationLeaf, ".")[0] || c.Prerequisites != leaf.Prerequisites || c.ClosurePrerequisites != closure.Prerequisites {
			return fmt.Errorf("ownership or prerequisite drift: %s", c.ID)
		}
		if !refs[c.Fixture] || !tests[c.StaticTest] || !traced || len(trace.Input) == 0 || len(trace.Expected) == 0 || len(trace.Events) == 0 {
			return fmt.Errorf("dangling fixture, trace or test: %s", c.ID)
		}
		if contract, ok := trace.Expected["contract"].(string); !ok || contract != c.Requirement {
			return fmt.Errorf("trace requirement pointer drift: %s", c.ID)
		}
		if trace.Input["expanded_case"] == true {
			expandedCount++
			if err := checkExpandedTrace(c.ID, c.Requirement, trace); err != nil {
				return err
			}
		} else if strings.HasSuffix(c.ID, "-contract") && strings.HasPrefix(c.Requirement, "INV-") {
			if err := checkClauseScenario(trace); err != nil {
				return err
			}
			concreteClauses[c.Requirement] = true
		} else if err := checkNonExpandedTrace(c, trace, inv, aux); err != nil {
			return err
		}
		if oneOf(c.Fixture.File, "startup.jsonl", "inspection.jsonl", "awsh-frames.json") {
			if err := checkAuxiliaryBinding(c.Fixture, trace, aux); err != nil {
				return fmt.Errorf("auxiliary binding %s: %w", c.ID, err)
			}
		}
		if expected := map[string]string{"startup.jsonl": "TestSyntheticStartupExpectations", "inspection.jsonl": "TestStaticInspectionDigestExamples", "awsh-frames.json": "TestFrozenAwshHexFrames"}[c.Fixture.File]; expected != "" && c.StaticTest != expected {
			return fmt.Errorf("wrong example test binding: %s", c.ID)
		}
		if c.RuntimeTest.CaseID != c.ID || c.RuntimeTest.Leaf != c.ImplementationLeaf || c.RuntimeTest.Status != "pending" || c.RuntimeTest.Test != nil {
			return fmt.Errorf("static fixture claims runtime evidence: %s", c.ID)
		}
		if c.BuildDependent && (trace.BuildValues != nil || trace.BuildExpectationsOwner == nil || *trace.BuildExpectationsOwner != "B2.8") || !c.BuildDependent && (trace.BuildValues == nil || trace.BuildExpectationsOwner != nil) {
			return fmt.Errorf("unqualified build values: %s", c.ID)
		}
		if len(trace.BuildValues) != 0 {
			return fmt.Errorf("concrete build values before qualification: %s", c.ID)
		}
		seen[c.ID], coverage[c.Requirement] = true, true
		wireCoverage[c.Fixture] = true
	}
	for _, clause := range inv.Requirements {
		if !concreteClauses[clause.ID] {
			return fmt.Errorf("missing concrete clause coverage: %s", clause.ID)
		}
	}
	for id := range requiredCases {
		if !seen[id] {
			return fmt.Errorf("lost frozen case: %s", id)
		}
	}
	for ref := range refs {
		if ref.File != "traces.jsonl" && !wireCoverage[ref] {
			return fmt.Errorf("unmapped accepted wire fixture: %s", ref)
		}
	}
	for id := range requirements {
		if !coverage[id] {
			return fmt.Errorf("unmapped requirement: %s", id)
		}
	}
	for id := range traces {
		if !seen[id] {
			return fmt.Errorf("unmapped trace: %s", id)
		}
	}
	if expandedCount != 134 {
		return fmt.Errorf("expected 134 concrete expanded traces, got %d", expandedCount)
	}
	return nil
}

func TestConformanceInventory(t *testing.T) {
	inv := decodeFixture[inventory](t, fixtureData(t, "inventory.json"))
	if inv.Version != 1 || !inv.StaticOnly || len(inv.QualifiedBuilds) != 0 || inv.Source != "docs/design/reploy-integration-implementation-plan.md" {
		t.Fatal("wrong authority or invented qualification")
	}
	if err := checkAuthorityBindings(inv.Authorities); err != nil {
		t.Fatal(err)
	}
	source, err := os.ReadFile("../../../" + inv.Source)
	if err != nil {
		t.Fatal(err)
	}
	if sha(source) != inv.SourceSHA256 {
		t.Fatal("design source changed; explicitly reconcile the case inventory")
	}
	start, end := strings.Index(string(source), inv.Begin), strings.Index(string(source), inv.End)
	if start < 0 || end <= start {
		t.Fatal("missing source boundaries")
	}
	var clauses []string
	for i, r := range inv.Requirements {
		if r.ID != fmt.Sprintf("INV-%03d", i+1) {
			t.Fatal("changed stable clause identity", r.ID)
		}
		clauses = append(clauses, r.Text)
	}
	if strings.Join(clauses, " ") != strings.Join(strings.Fields(string(source[start:end])), " ") {
		t.Fatal("shared inventory has an unaccounted clause")
	}
	for _, line := range strings.Split(string(source), "\n") {
		fields := strings.Split(line, "|")
		if len(fields) != 7 || !regexp.MustCompile(`^[BCD][0-9]\.[0-9]$`).MatchString(strings.TrimSpace(fields[1])) {
			continue
		}
		id := strings.TrimSpace(fields[1])
		leaf, ok := inv.Leaves[id]
		if !ok || leaf.Prerequisites != strings.TrimSpace(fields[3]) || leaf.Implementation != strings.TrimSpace(fields[4]) || leaf.Acceptance != strings.TrimSpace(fields[5]) {
			t.Fatal("unmapped or altered leaf contract", id)
		}
	}
	if len(inv.Leaves) != 64 {
		t.Fatal("incomplete leaf catalogue")
	}
	if err := checkPackageRequirementBijection(inv); err != nil {
		t.Fatal(err)
	}
	t.Run("package-leaf-bijection", func(t *testing.T) {
		clone := func() inventory {
			candidate := inv
			candidate.PackageRequirements = append([]packageRequirement(nil), inv.PackageRequirements...)
			return candidate
		}
		t.Run("unknown-leaf-empty-acceptance", func(t *testing.T) {
			candidate := clone()
			candidate.PackageRequirements[0] = packageRequirement{ID: "PACKAGE-B9.9", Leaf: "B9.9"}
			if err := checkPackageRequirementBijection(candidate); err == nil {
				t.Fatal("accepted unknown package leaf with empty acceptance")
			}
		})
		t.Run("duplicate-leaf", func(t *testing.T) {
			candidate := clone()
			candidate.PackageRequirements[1] = candidate.PackageRequirements[0]
			if err := checkPackageRequirementBijection(candidate); err == nil {
				t.Fatal("accepted duplicate package leaf")
			}
		})
		t.Run("missing-approved-leaf", func(t *testing.T) {
			candidate := clone()
			candidate.PackageRequirements = candidate.PackageRequirements[:len(candidate.PackageRequirements)-1]
			if err := checkPackageRequirementBijection(candidate); err == nil {
				t.Fatal("accepted missing approved leaf")
			}
		})
	})
	traces, refs, tests := map[string]expectedTrace{}, map[caseRef]bool{}, map[string]bool{}
	aux := auxiliaryFixtures{
		Startups:    map[string]startupFixture{},
		Inspections: map[string]inspectionFixture{},
		Frames:      map[string]awshFrameFixture{},
		Goldens:     map[string]nulFixture{},
		WireRefs:    map[string]caseRef{},
	}
	for _, tr := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		if _, found := traces[tr.ID]; found {
			t.Fatal("duplicate trace", tr.ID)
		}
		traces[tr.ID], refs[caseRef{"traces.jsonl", tr.ID}] = tr, true
	}
	for _, name := range []string{"controller.jsonl", "envoy.jsonl", "public-invalid.jsonl", "public-maximum.jsonl", "private.jsonl", "helper.jsonl"} {
		for index, row := range bytes.Split(bytes.TrimSuffix(fixtureData(t, name), []byte{'\n'}), []byte{'\n'}) {
			aux.WireRefs[fmt.Sprintf("B1-C066-wire-%s-%d", strings.TrimSuffix(name, ".jsonl"), index+1)] = caseRef{name, sha(row)}
			refs[caseRef{name, sha(row)}] = true
		}
	}
	for _, f := range fixtureRows[startupFixture](t, "startup.jsonl") {
		ref := caseRef{"startup.jsonl", f.ID}
		if f.ID == "" || refs[ref] {
			t.Fatal("duplicate or missing example identity", ref.File, f.ID)
		}
		refs[ref], aux.Startups[f.ID] = true, f
	}
	for _, f := range fixtureRows[inspectionFixture](t, "inspection.jsonl") {
		ref := caseRef{"inspection.jsonl", f.ID}
		if f.ID == "" || refs[ref] {
			t.Fatal("duplicate or missing example identity", ref.File, f.ID)
		}
		refs[ref], aux.Inspections[f.ID] = true, f
	}
	for _, f := range decodeFixture[[]awshFrameFixture](t, fixtureData(t, "awsh-frames.json")) {
		ref := caseRef{"awsh-frames.json", f.ID}
		if f.ID == "" || refs[ref] {
			t.Fatal("duplicate or missing example identity", ref.File, f.ID)
		}
		refs[ref], aux.Frames[f.ID] = true, f
	}
	for _, name := range []string{"private", "helper"} {
		for _, golden := range nulFixtures(t, name) {
			if _, found := aux.Goldens[golden.ID]; found {
				t.Fatal("duplicate golden identity", golden.ID)
			}
			aux.Goldens[golden.ID] = golden
		}
	}
	files, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range files {
		if strings.HasSuffix(f.Name(), "_test.go") {
			b, err := os.ReadFile(f.Name())
			if err != nil {
				t.Fatal(err)
			}
			for _, m := range regexp.MustCompile(`func (Test\w+)\(`).FindAllStringSubmatch(string(b), -1) {
				tests[m[1]] = true
			}
		}
	}
	cases := fixtureRows[conformanceCase](t, "cases.jsonl")
	if err := checkCaseMap(cases, inv, traces, refs, tests, aux); err != nil {
		t.Fatal(err)
	}
	t.Run("non-expanded-trace-mutations", func(t *testing.T) {
		covered, probes := 0, 0
		usedRules := map[string]bool{}
		for _, c := range cases {
			original := traces[c.ID]
			if original.Input["expanded_case"] == true || strings.HasSuffix(c.ID, "-contract") && strings.HasPrefix(c.Requirement, "INV-") {
				continue
			}
			covered++
			if _, ok := matrixContracts[c.ID]; ok {
				usedRules[c.ID] = true
			}
			t.Run(c.ID, func(t *testing.T) {
				raw, err := json.Marshal(original)
				if err != nil {
					t.Fatal(err)
				}
				reject := func(label string, mutate func(*expectedTrace)) {
					candidate := decodeFixture[expectedTrace](t, raw)
					mutate(&candidate)
					probes++
					if err := checkNonExpandedTrace(c, candidate, inv, aux); err == nil {
						t.Fatal("accepted corrupted trace", label)
					}
				}
				for key := range original.Input {
					reject("input change "+key, func(tr *expectedTrace) { tr.Input[key] = "mutated" })
					reject("input omission "+key, func(tr *expectedTrace) { delete(tr.Input, key) })
				}
				for key := range original.Expected {
					reject("expected change "+key, func(tr *expectedTrace) { tr.Expected[key] = "mutated" })
					reject("expected omission "+key, func(tr *expectedTrace) { delete(tr.Expected, key) })
				}
				reject("extra input", func(tr *expectedTrace) { tr.Input["unexpected"] = true })
				reject("extra expectation", func(tr *expectedTrace) { tr.Expected["unexpected"] = true })
				for i := range original.Events {
					reject(fmt.Sprintf("event omission %d", i), func(tr *expectedTrace) { tr.Events = append(tr.Events[:i], tr.Events[i+1:]...) })
					reject(fmt.Sprintf("event change %d", i), func(tr *expectedTrace) { tr.Events[i] = "mutated" })
				}
				reject("event reordering", func(tr *expectedTrace) { tr.Events[0], tr.Events[1] = tr.Events[1], tr.Events[0] })
				if original.Epoch == nil {
					reject("unexpected epoch", func(tr *expectedTrace) {
						tr.Epoch = &deadlineExpectation{Owner: "envoy", Name: "final-drain", BudgetMS: 5000}
					})
				} else {
					reject("missing epoch", func(tr *expectedTrace) { tr.Epoch = nil })
					reject("epoch owner", func(tr *expectedTrace) { tr.Epoch.Owner = "mutated" })
					reject("epoch name", func(tr *expectedTrace) { tr.Epoch.Name = "mutated" })
					reject("epoch budget", func(tr *expectedTrace) { tr.Epoch.BudgetMS++ })
					reject("epoch reset", func(tr *expectedTrace) { tr.Epoch.Reset = true })
					reject("epoch boundary", func(tr *expectedTrace) { tr.Epoch.EntryBoundary = "mutated" })
				}
			})
		}
		if covered != 833 || len(usedRules) != len(matrixContracts) {
			t.Fatalf("incomplete independent trace coverage: %d traces, %d/%d matrix rules", covered, len(usedRules), len(matrixContracts))
		}
		t.Logf("rejected %d mutations across %d independently checked traces", probes, covered)
	})

	t.Run("case-owner-test-bindings", func(t *testing.T) {
		probes := 0
		for _, original := range cases {
			t.Run(original.ID, func(t *testing.T) {
				if err := checkCaseBinding(original, inv, aux); err != nil {
					t.Fatal(err)
				}
				reject := func(label string, mutate func(*conformanceCase)) {
					candidate := original
					mutate(&candidate)
					probes++
					if err := checkCaseBinding(candidate, inv, aux); err == nil {
						t.Fatal("accepted case binding drift", label)
					}
				}
				reject("coordinated implementation owner", func(c *conformanceCase) {
					owner := "B1.3"
					if c.ImplementationLeaf == owner {
						owner = "B1.2"
					}
					c.ImplementationLeaf = owner
					c.Package = "B1"
					c.Prerequisites = inv.Leaves[owner].Prerequisites
					c.RuntimeTest.Leaf = owner
				})
				reject("coordinated closure owner", func(c *conformanceCase) {
					closure := "B8.4"
					if c.ClosureLeaf == closure {
						closure = "B1.3"
					}
					c.ClosureLeaf = closure
					c.ClosurePrerequisites = inv.Leaves[closure].Prerequisites
				})
				reject("unrelated existing static test", func(c *conformanceCase) {
					c.StaticTest = "TestSyntheticStartupExpectations"
					if original.StaticTest == c.StaticTest {
						c.StaticTest = "TestConformanceInventory"
					}
				})
				reject("runtime owner", func(c *conformanceCase) {
					c.RuntimeTest.Leaf = "B1.3"
					if original.RuntimeTest.Leaf == c.RuntimeTest.Leaf {
						c.RuntimeTest.Leaf = "B1.2"
					}
				})
				reject("package", func(c *conformanceCase) { c.Package = "mutated" })
				reject("prerequisite", func(c *conformanceCase) { c.Prerequisites = "mutated" })
				reject("closure prerequisite", func(c *conformanceCase) { c.ClosurePrerequisites = "mutated" })
				reject("fixture identity", func(c *conformanceCase) {
					c.Fixture.ID = "B1-C001-contract"
					if original.Fixture.ID == c.Fixture.ID {
						c.Fixture.ID = "B1-C002-contract"
					}
				})
				reject("fixture corpus", func(c *conformanceCase) {
					c.Fixture.File = "startup.jsonl"
					if original.Fixture.File == c.Fixture.File {
						c.Fixture.File = "traces.jsonl"
					}
				})
			})
		}
		if len(cases) != 1035 || probes != 9315 {
			t.Fatalf("incomplete owner/test binding sweep: %d cases, %d probes", len(cases), probes)
		}
		t.Logf("rejected %d ownership/test/fixture mutations across %d cases", probes, len(cases))
	})

	t.Run("case-contract-binding", func(t *testing.T) {
		id := "B1-C066-public-nominal"
		candidateTraces := make(map[string]expectedTrace, len(traces))
		for key, trace := range traces {
			candidateTraces[key] = trace
		}
		encoded, err := json.Marshal(traces[id])
		if err != nil {
			t.Fatal(err)
		}
		trace := decodeFixture[expectedTrace](t, encoded)
		delete(trace.Expected, "contract")
		candidateTraces[id] = trace
		if err := checkCaseMap(cases, inv, candidateTraces, refs, tests, aux); err == nil {
			t.Fatal("accepted trace without its mapped contract")
		}
		candidateCases := append([]conformanceCase(nil), cases...)
		for i := range candidateCases {
			if candidateCases[i].ID == id {
				candidateCases[i].Requirement = "INV-067"
			}
		}
		if err := checkCaseMap(candidateCases, inv, traces, refs, tests, aux); err == nil {
			t.Fatal("accepted case reassigned to another valid requirement")
		}
	})
	t.Run("expanded-negative-probes", func(t *testing.T) {
		cloneTrace := func(trace expectedTrace) expectedTrace {
			copyTrace := trace
			copyTrace.Input = make(map[string]any, len(trace.Input))
			for key, value := range trace.Input {
				copyTrace.Input[key] = value
			}
			copyTrace.Expected = make(map[string]any, len(trace.Expected))
			for key, value := range trace.Expected {
				copyTrace.Expected[key] = value
			}
			copyTrace.Events = append([]string(nil), trace.Events...)
			if trace.Epoch != nil {
				epoch := *trace.Epoch
				copyTrace.Epoch = &epoch
			}
			if trace.BuildValues != nil {
				copyTrace.BuildValues = make(map[string]any, len(trace.BuildValues))
				for key, value := range trace.BuildValues {
					copyTrace.BuildValues[key] = value
				}
			}
			return copyTrace
		}
		clone := func() map[string]expectedTrace {
			candidate := make(map[string]expectedTrace, len(traces))
			for id, trace := range traces {
				candidate[id] = cloneTrace(trace)
			}
			return candidate
		}
		mutate := func(id string, fn func(expectedTrace) expectedTrace) error {
			candidate := clone()
			if err := checkCaseMap(cases, inv, candidate, refs, tests, aux); err != nil {
				return fmt.Errorf("unmodified deep copy rejected: %w", err)
			}
			candidate[id] = fn(candidate[id])
			return checkCaseMap(cases, inv, candidate, refs, tests, aux)
		}
		t.Run("generic-placeholder", func(t *testing.T) {
			err := mutate("B1-C065-positive-empty-then-timeout", func(trace expectedTrace) expectedTrace {
				trace.Expected = map[string]any{"contract": "INV-065"}
				trace.Events = []string{"scenario-input", "contract-defined-outcome"}
				return trace
			})
			if err == nil {
				t.Fatal("accepted generic expanded placeholder")
			}
		})
		t.Run("wrong-requirement-pointer", func(t *testing.T) {
			err := mutate("B1-C065-positive-empty-then-timeout", func(trace expectedTrace) expectedTrace {
				trace.Expected["contract"] = "INV-064"
				return trace
			})
			if err == nil {
				t.Fatal("accepted wrong expanded requirement pointer")
			}
		})
		t.Run("swapped-race-winner", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-after-timeout", func(trace expectedTrace) expectedTrace {
				trace.Expected["winner"] = "shell_exit_before_timeout"
				return trace
			})
			if err == nil {
				t.Fatal("accepted swapped concrete race winner")
			}
		})
		t.Run("dropped-race-winner", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-after-timeout", func(trace expectedTrace) expectedTrace {
				delete(trace.Expected, "winner")
				return trace
			})
			if err == nil {
				t.Fatal("accepted dropped concrete race winner")
			}
		})
		t.Run("dropped-operation-result", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-before-timeout", func(trace expectedTrace) expectedTrace {
				delete(trace.Expected, "operation_result")
				return trace
			})
			if err == nil {
				t.Fatal("accepted dropped concrete operation result")
			}
		})
		t.Run("swapped-publication", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-before-timeout", func(trace expectedTrace) expectedTrace {
				trace.Expected["publication"] = "operation-cancelled"
				return trace
			})
			if err == nil {
				t.Fatal("accepted swapped concrete publication")
			}
		})
		t.Run("wrong-fatality", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-before-timeout", func(trace expectedTrace) expectedTrace {
				trace.Expected["fatal"] = true
				return trace
			})
			if err == nil {
				t.Fatal("accepted wrong concrete fatality")
			}
		})
		t.Run("wrong-signal-count", func(t *testing.T) {
			err := mutate("B1-C065-positive-empty-then-live", func(trace expectedTrace) expectedTrace {
				trace.Expected["signal_count"] = float64(0)
				return trace
			})
			if err == nil {
				t.Fatal("accepted wrong concrete signal count")
			}
		})
		t.Run("swapped-required-event-order", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-after-timeout", func(trace expectedTrace) expectedTrace {
				trace.Events[1], trace.Events[2] = trace.Events[2], trace.Events[1]
				return trace
			})
			if err == nil {
				t.Fatal("accepted swapped required event order")
			}
		})
		t.Run("missing-applicable-epoch", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-after-timeout", func(trace expectedTrace) expectedTrace {
				trace.Epoch = nil
				return trace
			})
			if err == nil {
				t.Fatal("accepted missing applicable epoch")
			}
		})
		t.Run("wrong-applicable-epoch", func(t *testing.T) {
			err := mutate("B1-C065-shell-exit-after-timeout", func(trace expectedTrace) expectedTrace {
				trace.Epoch = &deadlineExpectation{Owner: "envoy", Name: "operation-start", BudgetMS: 5000, Reset: false, EntryBoundary: "wrong-boundary"}
				return trace
			})
			if err == nil {
				t.Fatal("accepted wrong applicable epoch")
			}
		})
	})
	t.Run("authority-binding-negative-probes", func(t *testing.T) {
		clone := func() []authorityBinding {
			return append([]authorityBinding(nil), inv.Authorities...)
		}
		for _, mutation := range []string{"drift", "missing", "duplicate", "unknown"} {
			t.Run(mutation, func(t *testing.T) {
				candidate := clone()
				switch mutation {
				case "drift":
					candidate[0].SHA256 = strings.Repeat("0", 64)
				case "missing":
					candidate = candidate[:len(candidate)-1]
				case "duplicate":
					candidate[1] = candidate[0]
				case "unknown":
					candidate[0].Path = "docs/design/unknown.md"
				}
				if err := checkAuthorityBindings(candidate); err == nil {
					t.Fatal("accepted invalid authority binding", mutation)
				}
			})
		}
	})
	// Mutations exercise the completeness checks, including accidental false proof claims.
	for _, mutation := range []string{"duplicate", "missing", "fixture", "prerequisite", "closure", "runtime-pass", "build-value", "test"} {
		t.Run(mutation, func(t *testing.T) {
			candidate := append([]conformanceCase(nil), cases...)
			switch mutation {
			case "duplicate":
				candidate = append(candidate, cases[0])
			case "missing":
				candidate = nil
			case "fixture":
				candidate[0].Fixture.ID = "absent"
			case "prerequisite":
				candidate[0].Prerequisites = "future work"
			case "closure":
				candidate[0].ClosureLeaf = "B9.9"
			case "runtime-pass":
				candidate[0].RuntimeTest.Status = "passed"
			case "build-value":
				candidate[0].BuildDependent = true
			case "test":
				candidate[0].StaticTest = "TestDoesNotExist"
			}
			if err := checkCaseMap(candidate, inv, traces, refs, tests, aux); err == nil {
				t.Fatal("invalid corpus was accepted")
			}
		})
	}
	t.Run("auxiliary-binding-valid-id-swap", func(t *testing.T) {
		candidate := append([]conformanceCase(nil), cases...)
		var zero, maximum int
		for i, c := range candidate {
			switch c.ID {
			case "B1-AUX-startup-zero":
				zero = i
			case "B1-AUX-startup-maximum":
				maximum = i
			}
		}
		candidate[zero].Fixture.ID, candidate[maximum].Fixture.ID = candidate[maximum].Fixture.ID, candidate[zero].Fixture.ID
		if err := checkCaseMap(candidate, inv, traces, refs, tests, aux); err == nil {
			t.Fatal("accepted valid-ID swap with broken trace bindings")
		}
	})
	t.Run("auxiliary-binding-trace-expectation-drift", func(t *testing.T) {
		candidate := make(map[string]expectedTrace, len(traces))
		for id, trace := range traces {
			candidate[id] = trace
		}
		trace := candidate["B1-AUX-startup-zero"]
		expected := make(map[string]any, len(trace.Expected))
		for key, value := range trace.Expected {
			expected[key] = value
		}
		expected["synthetic_outcome"] = "buffer"
		trace.Expected = expected
		candidate[trace.ID] = trace
		if err := checkCaseMap(cases, inv, candidate, refs, tests, aux); err == nil {
			t.Fatal("accepted trace-side expectation drift")
		}
	})
	t.Run("auxiliary-binding-missing-helper-phase", func(t *testing.T) {
		candidate := aux
		candidate.Frames = make(map[string]awshFrameFixture, len(aux.Frames))
		for id, frame := range aux.Frames {
			candidate.Frames[id] = frame
		}
		frame := candidate.Frames["helper-startup-state"]
		frame.Phase = nil
		candidate.Frames[frame.ID] = frame
		if err := checkCaseMap(cases, inv, traces, refs, tests, candidate); err == nil {
			t.Fatal("accepted helper frame with missing phase")
		}
	})
}

func TestFrozenAwshHexFrames(t *testing.T) {
	frames := decodeFixture[[]awshFrameFixture](t, fixtureData(t, "awsh-frames.json"))
	seen := map[string]bool{}
	for _, f := range frames {
		if seen[f.ID] {
			t.Fatal("duplicate frame", f.ID)
		}
		seen[f.ID] = true
		var found bool
		for _, golden := range nulFixtures(t, strings.TrimSuffix(f.File, ".jsonl")) {
			if golden.ID != f.ID {
				continue
			}
			found = true
			payload, encoded := nulBytes(golden.Fields), nulBytes(golden.Fields)
			if f.Direction != golden.Direction || f.File == "helper.jsonl" && (f.Phase == nil || *f.Phase != golden.Phase) || f.File != "helper.jsonl" && f.Phase != nil {
				t.Fatal("hex fixture changed direction or phase", f.ID)
			}
			if f.File == "helper.jsonl" {
				encoded = helperBytes(golden.Fields)
				if _, err := DecodeHelper(encoded, helperDirection(golden), helperPhase(golden)); err != nil {
					t.Fatal(err)
				}
			} else if _, err := DecodePrivate(encoded, privateDirection(golden)); err != nil {
				t.Fatal(err)
			}
			if f.PayloadHex != hex.EncodeToString(payload) || f.FrameHex != hex.EncodeToString(encoded) {
				t.Fatal("hex bytes do not match accepted fields", f.ID)
			}
		}
		if !found {
			t.Fatal("unknown hex frame", f.ID)
		}
	}
	if len(seen) != len(nulFixtures(t, "private"))+len(nulFixtures(t, "helper")) {
		t.Fatal("missing hex wire form")
	}
}

func syntheticStartupOutcome(f startupFixture, expected, received []byte) string {
	if len(received) > 4096 || len(received) > len(expected) || !bytes.Equal(received, expected[:min(len(received), len(expected))]) || f.EOF && (!f.ReadyReceived || len(received) != len(expected)) || f.DeadlineExpired {
		return "fatal"
	}
	if !f.ReadyReceived {
		return "buffer"
	}
	if int64(len(received)) < f.OutputThrough {
		return "wait"
	}
	return "ready"
}

func TestSyntheticStartupExpectations(t *testing.T) {
	for _, f := range fixtureRows[startupFixture](t, "startup.jsonl") {
		t.Run(f.ID, func(t *testing.T) {
			expected, err := hex.DecodeString(f.ExpectedBytesHex)
			if err != nil || len(expected) > 4096 || !f.Synthetic || f.BuildIdentity != nil {
				t.Fatal("invalid synthetic startup fixture")
			}
			received, err := hex.DecodeString(f.ReceivedBytesHex)
			if err != nil {
				t.Fatal(err)
			}
			last := 0
			for _, end := range f.Fragments {
				if end < last || end > len(received) {
					t.Fatal("invalid fragmentation")
				}
				last = end
			}
			if last != len(received) {
				t.Fatal("fragmentation loses bytes")
			}
			// Declarative classification only: this runs neither a startup pump nor a controller.
			outcome := syntheticStartupOutcome(f, expected, received)
			if outcome != f.Expected || f.OutputThrough != int64(len(expected)) {
				t.Fatal("inconsistent startup expectation", outcome, f.Expected)
			}
			b := []byte(fmt.Sprintf(`{"schema":"omegaflow-envoy-telemetry-v1","type":"ready","seq":1,"envoy_pid":1,"shell_pid":2,"cwd":"/work","columns":80,"rows":24,"elapsed_us":0,"output_through":%d}`+"\n", f.OutputThrough))
			if _, err := DecodeEnvoy(b); err != nil {
				t.Fatal(err)
			}
		})
	}
	for _, bytes := range [][]byte{nil, {'A'}} {
		t.Run(fmt.Sprintf("EOF-before-ready-%d", len(bytes)), func(t *testing.T) {
			if got := syntheticStartupOutcome(startupFixture{EOF: true, OutputThrough: int64(len(bytes))}, bytes, bytes); got != "fatal" {
				t.Fatal("terminal EOF before ready was not fatal", got)
			}
		})
	}
}

func expectedInspectionOmissions(id string) ([]string, bool) {
	switch id {
	case "digest-directory-tree", "digest-directory-v2-tree":
		return []string{"sub/fifo"}, true
	case "digest-file-bytes", "digest-directory-empty", "digest-directory-v2-empty":
		return []string{}, true
	default:
		return nil, false
	}
}

func checkInspectionOmissions(f inspectionFixture) error {
	expected, ok := expectedInspectionOmissions(f.ID)
	if !ok {
		return fmt.Errorf("unknown inspection example: %s", f.ID)
	}
	if len(f.OmittedSpecialEntries) != len(expected) {
		return fmt.Errorf("omitted special entries changed: %s", f.ID)
	}
	for i, entry := range expected {
		if f.OmittedSpecialEntries[i] != entry {
			return fmt.Errorf("omitted special entries changed: %s", f.ID)
		}
	}
	return nil
}

func TestStaticInspectionDigestExamples(t *testing.T) {
	fixtures := fixtureRows[inspectionFixture](t, "inspection.jsonl")
	byID := make(map[string]inspectionFixture, len(fixtures))
	for _, f := range fixtures {
		byID[f.ID] = f
	}
	for _, id := range []string{"digest-directory-tree", "digest-directory-v2-tree"} {
		original, ok := byID[id]
		if !ok {
			t.Fatal("missing inspection example", id)
		}
		for _, mutation := range []struct {
			name      string
			omissions []string
		}{
			{name: "clear", omissions: []string{}},
			{name: "replace", omissions: []string{"sub/socket"}},
		} {
			t.Run("omission-negative/"+id+"/"+mutation.name, func(t *testing.T) {
				candidate := original
				candidate.OmittedSpecialEntries = mutation.omissions
				if err := checkInspectionOmissions(candidate); err == nil {
					t.Fatal("invalid omitted special entries were accepted")
				}
			})
		}
	}
	for _, f := range fixtures {
		if err := checkInspectionOmissions(f); err != nil {
			t.Fatal(err)
		}
		input := []byte("directory-v2")
		if f.Algorithm == "directory" {
			input = []byte("directory\x00")
		} else if f.Algorithm == "file" {
			input = []byte("abc\n")
		} else if f.Algorithm != "directory-v2" {
			t.Fatal("unknown algorithm tag")
		}
		previous := ""
		for _, e := range f.Entries {
			payload, err := hex.DecodeString(e.PayloadHex)
			if err != nil || e.Path <= previous || !oneOf(e.Kind, "f", "d", "l") {
				t.Fatal("invalid sorted digest entry")
			}
			previous = e.Path
			if f.Algorithm == "directory-v2" {
				pathHash, payloadHash := sha256.Sum256([]byte(e.Path)), sha256.Sum256(payload)
				input = append(input, e.Kind[0])
				input = append(input, pathHash[:]...)
				input = append(input, payloadHash[:]...)
			} else {
				kind := map[string]string{"f": "file", "d": "dir", "l": "link"}[e.Kind]
				input = append(input, []byte(kind+"\x00"+e.Path+"\x00")...)
				if e.Kind != "d" {
					input = append(input, payload...)
					input = append(input, 0)
				}
			}
		}
		if hex.EncodeToString(input) != f.HashInputHex || sha(input) != f.SHA256 || f.LiveResolution {
			t.Fatal("altered hash input, digest or evidence class", f.ID)
		}
	}
}

func TestCompletionAndEOFInputMutations(t *testing.T) {
	for _, tr := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		keys := []string{}
		switch tr.ID {
		case "B1-C064-completed":
			keys = []string{"final_census_clean", "output_drained", "inspections"}
		case "B1-C034-stdout-EOF", "B1-C034-stderr-EOF", "B1-C064-stdout-EOF", "B1-C064-stderr-EOF":
			keys = []string{"stdout_eof", "stderr_eof", "cleanup_deadline_expired"}
		case "B1-C034-keepalive-after-cleanup":
			keys = []string{"descendants_cleaned", "stdout_eof", "stderr_eof", "cleanup_deadline_expired"}
		case "B1-C028-hostile-PATH":
			keys = []string{"manifested_helper"}
		}
		for _, key := range keys {
			t.Run(tr.ID+"/"+key, func(t *testing.T) {
				b, err := json.Marshal(tr)
				if err != nil {
					t.Fatal(err)
				}
				candidate := decodeFixture[expectedTrace](t, b)
				if err := checkExpandedTrace(tr.ID, tr.Expected["contract"].(string), candidate); err != nil {
					t.Fatal(err)
				}
				candidate.Input[key] = "mutated-fact"
				if err := checkExpandedTrace(tr.ID, tr.Expected["contract"].(string), candidate); err == nil {
					t.Fatal("accepted contradicted completion/EOF facts")
				}
			})
		}
	}
}

func TestClauseScenarioMutations(t *testing.T) {
	covered := 0
	for _, original := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		if _, ok := clauseScenarios[original.ID]; !ok {
			continue
		}
		covered++
		t.Run(original.ID, func(t *testing.T) {
			if err := checkClauseScenario(original); err != nil {
				t.Fatal(err)
			}
			check := func(mutate func(*expectedTrace)) {
				b, err := json.Marshal(original)
				if err != nil {
					t.Fatal(err)
				}
				candidate := decodeFixture[expectedTrace](t, b)
				mutate(&candidate)
				if err := checkClauseScenario(candidate); err == nil {
					t.Fatal("accepted missing or mutated concrete clause proof")
				}
			}
			for key := range original.Expected {
				check(func(tr *expectedTrace) { delete(tr.Expected, key) })
			}
			for key := range original.Input {
				check(func(tr *expectedTrace) { tr.Input[key] = "mutated-input" })
			}
			for i := range original.Events {
				check(func(tr *expectedTrace) { tr.Events = append(tr.Events[:i], tr.Events[i+1:]...) })
			}
			check(func(tr *expectedTrace) { tr.Events[0], tr.Events[1] = tr.Events[1], tr.Events[0] })
			check(func(tr *expectedTrace) {
				tr.Input = map[string]any{"scenario": "contract"}
				tr.Expected = map[string]any{"contract": original.Expected["contract"]}
				tr.Events = []string{"scenario-input", "contract-defined-outcome"}
			})
		})
	}
	if covered != 68 || len(clauseScenarios) != covered {
		t.Fatalf("concrete clause coverage: %d", covered)
	}
}

func TestExpandedContractMutations(t *testing.T) {
	groups, covered := map[int]bool{}, 0
	for _, original := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		rule, expanded := expandedContracts[original.ID]
		if !expanded {
			continue
		}
		covered++
		groups[rule.group] = true
		t.Run(original.ID, func(t *testing.T) {
			check := func(name string, mutate func(*expectedTrace)) {
				t.Helper()
				t.Run(name, func(t *testing.T) {
					// JSON roundtrip isolates every map and slice from the fixture.
					b, err := json.Marshal(original)
					if err != nil {
						t.Fatal(err)
					}
					candidate := decodeFixture[expectedTrace](t, b)
					contract := original.Expected["contract"].(string)
					if err := checkExpandedTrace(original.ID, contract, candidate); err != nil {
						t.Fatalf("unaltered trace: %v", err)
					}
					mutate(&candidate)
					if err := checkExpandedTrace(original.ID, contract, candidate); err == nil {
						t.Fatal("accepted contract-wrong, internally consistent trace")
					}
				})
			}
			check("joint-outcome", func(tr *expectedTrace) {
				publication, result := "private-handshake", "session-ready"
				fatal, signals := false, float64(0)
				if rule.result == result {
					publication, result, fatal, signals = "cleanup-evidence", "cleanup-complete", true, 7
				}
				old, _ := tr.Expected["publication"].(string)
				tr.Expected["result_eligible"], tr.Expected["operation_result"] = true, result
				tr.Expected["fatal"], tr.Expected["signal_count"], tr.Expected["publication"] = fatal, signals, publication
				found := false
				for i, event := range tr.Events {
					if event == "publish-"+old {
						tr.Events[i] = "publish-" + publication
						found = true
					}
				}
				if !found {
					at := len(tr.Events) - 1
					tr.Events = append(tr.Events[:at], "publish-"+publication, tr.Events[at])
				}
			})
			check("joint-winner", func(tr *expectedTrace) {
				wrong := "contract-wrong-winner"
				tr.Input["contract_winner"], tr.Expected["winner"] = wrong, wrong
				for i, event := range tr.Events {
					if event == rule.winner {
						tr.Events[i] = wrong
					}
					if event == "winner-"+rule.winner {
						tr.Events[i] = "winner-" + wrong
					}
				}
			})
			check("fatality", func(tr *expectedTrace) {
				if rule.fatal == nil {
					tr.Expected["fatal"] = false
				} else {
					tr.Expected["fatal"] = !*rule.fatal
				}
			})
			check("signal-count", func(tr *expectedTrace) { tr.Expected["signal_count"] = rule.signals + 1 })
			if rule.publication != "" {
				check("early-publication", func(tr *expectedTrace) {
					for i, event := range tr.Events {
						if event == "publish-"+rule.publication {
							tr.Events = append([]string{event}, append(tr.Events[:i], tr.Events[i+1:]...)...)
							return
						}
					}
					t.Fatal("test cannot locate publication")
				})
			}
			check("required-order", func(tr *expectedTrace) {
				a, b := -1, -1
				for i, event := range tr.Events {
					if event == rule.order[0] {
						a = i
					}
					if event == rule.order[1] {
						b = i
					}
				}
				if a < 0 || b < 0 || a >= b {
					t.Fatal("test cannot locate contract relation")
				}
				tr.Events[a], tr.Events[b] = tr.Events[b], tr.Events[a]
			})
		})
	}
	if covered != 134 || len(expandedContracts) != covered || len(groups) != 37 {
		t.Fatalf("contract coverage: %d cases/%d groups", covered, len(groups))
	}
}

func checkCanonicalFrame(tr expectedTrace) error {
	source := tr.Input["source"].(string)
	status := int(tr.Input["status"].(float64))
	redirects := ""
	if tr.Input["mode"] == "split" {
		redirects = " > '/run/omegaflow/session/split/op.stdout' 2> '/run/omegaflow/session/split/op.stderr'"
	}
	frame := fmt.Sprintf("{\n__OMEGAFLOW_AWSH_START_RELEASED\n__OMEGAFLOW_AWSH_ENTER %d off emacs\n__OMEGAFLOW_AWSH_INNER_ENTERED=0\n{\n__OMEGAFLOW_AWSH_INNER_ENTERED=1\n__OMEGAFLOW_AWSH_RETURN %d\n", status, status) + source + "\n\n" + "__OMEGAFLOW_AWSH_RETURN \"$?\" && (( 1 ))\n}" + redirects + "\n__OMEGAFLOW_AWSH_STATUS=$?\nif [[ $__OMEGAFLOW_AWSH_INNER_ENTERED != 1 ]]; then\n__OMEGAFLOW_AWSH_FAIL_STOP\nfi\n__OMEGAFLOW_AWSH_RETURN \"$__OMEGAFLOW_AWSH_STATUS\" && (( 1 ))\n}"
	if tr.Expected["frame_hex"] != hex.EncodeToString([]byte(frame)) || tr.Expected["loader_stdout_hex"] != hex.EncodeToString([]byte(frame+"x")) || tr.Expected["loader_input_hex"] != "1801" || tr.Expected["submit_input_hex"] != "1802" || tr.Expected["Bash_executed"] != false {
		return fmt.Errorf("altered canonical frame, LF boundary or marker: %s", tr.ID)
	}
	return nil
}

func TestCanonicalSplitFrameQuoting(t *testing.T) {
	count := 0
	for _, original := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		if !strings.Contains(original.ID, "canonical-frame-") || original.Input["mode"] != "split" {
			continue
		}
		count++
		t.Run(original.ID, func(t *testing.T) {
			if err := checkCanonicalFrame(original); err != nil {
				t.Fatal(err)
			}
			for _, path := range []string{"/run/omegaflow/session/split/op.stdout", "/run/omegaflow/session/split/op.stderr"} {
				raw, err := json.Marshal(original)
				if err != nil {
					t.Fatal(err)
				}
				tr := decodeFixture[expectedTrace](t, raw)
				for _, field := range []string{"frame_hex", "loader_stdout_hex"} {
					b, err := hex.DecodeString(tr.Expected[field].(string))
					if err != nil {
						t.Fatal(err)
					}
					text := strings.Replace(string(b), "'"+path+"'", path, 1)
					if text == string(b) {
						t.Fatal("missing canonical quoted path", path)
					}
					tr.Expected[field] = hex.EncodeToString([]byte(text))
				}
				if err := checkCanonicalFrame(tr); err == nil {
					t.Fatal("accepted unquoted FIFO path", path)
				}
			}
		})
	}
	if count != 2 {
		t.Fatalf("split frame coverage: %d", count)
	}
}

func TestDeclarativeTracePredicates(t *testing.T) {
	for _, tr := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		if oneOf(tr.ID, "B1-C001-controller-connect", "B1-C001-envoy-accept", "B1-C001-envoy-hello", "B1-C001-envoy-launch", "B1-C001-controller-ready", "B1-C001-individual-control-write", "B1-C001-control-write-controller", "B1-C001-control-write-awsh") && tr.Epoch == nil {
			t.Fatal("missing actor-local startup or sender-write epoch", tr.ID)
		}
		if strings.Contains(tr.ID, "path-example-") {
			path := tr.Input["configured_path"].(string)
			env := tr.Input["exported_env"].(map[string]any)
			switch {
			case strings.HasPrefix(path, "$HOME/"):
				path = env["HOME"].(string) + strings.TrimPrefix(path, "$HOME")
			case strings.HasPrefix(path, "~/"):
				path = env["HOME"].(string) + strings.TrimPrefix(path, "~")
			case strings.HasPrefix(path, "~other/"):
				path = tr.Input["user_homes"].(map[string]any)["other"].(string) + strings.TrimPrefix(path, "~other")
			case !strings.HasPrefix(path, "/"):
				path = tr.Input["physical_cwd"].(string) + "/" + path
			}
			if tr.Expected["resolved_path"] != path || tr.Expected["live_Bash_expansion"] != false {
				t.Fatal("altered deterministic path example", tr.ID)
			}
			plan, err := json.Marshal([]ResolvedInspection{{InspectionID: "example", Kind: "file_exists", ResolvedPath: path}})
			if err != nil {
				t.Fatal(err)
			}
			if _, err := DecodePrivate(nulBytes([]string{"awsh-v1", "completed", "op", "0", "/work", string(plan)}), AwshToEnvoy); err != nil {
				t.Fatal(err)
			}
		}
		if !strings.HasSuffix(tr.ID, "-contract") && (strings.HasPrefix(tr.ID, "B1-C036-") || strings.HasPrefix(tr.ID, "B1-C040-") || strings.HasPrefix(tr.ID, "B1-C055-") || strings.HasPrefix(tr.ID, "B1-C052-")) && tr.Epoch == nil {
			t.Fatal("missing captured or fatal deadline expectation", tr.ID)
		}
		if strings.Contains(tr.ID, "canonical-frame-") {
			if err := checkCanonicalFrame(tr); err != nil {
				t.Fatal(err)
			}
		}
		if tr.Epoch != nil {
			e := tr.Epoch
			budget := 5000
			if oneOf(e.Name, "controller-connect", "accept", "hello", "launch-readiness", "controller-ready") {
				budget = 10000
			}
			owner := "envoy"
			if e.Name == "gate-reply" {
				owner = "awsh"
			} else if strings.HasPrefix(e.Name, "controller-") {
				owner = "controller"
			} else if e.Name == "control-write" {
				owner, _ = tr.Input["sender"].(string)
				if !oneOf(owner, "controller", "envoy", "awsh") {
					t.Fatal("missing control-write sender", tr.ID)
				}
			}
			if e.Reset || e.BudgetMS != budget || e.Owner != owner || !oneOf(e.Name, "controller-connect", "accept", "hello", "controller-ready", "control-write", "launch-readiness", "operation-start", "operation-cleanup", "terminal-input-barrier", "grace", "inspection-cancellation", "final-drain", "gate-reply") {
				t.Fatal("reset or altered existing deadline", tr.ID)
			}
			if strings.Contains(tr.ID, "protocol-error") && e.Owner == "awsh" {
				t.Fatal("Awsh transport deadline cannot own Envoy fatal teardown")
			}
		}
		if outcome, ok := tr.Input["private_outcome"].(string); ok {
			invalid := outcome == "gate-continued" && tr.Input["continue_sent"] == false
			if tr.Expected["fatal"] != invalid || invalid && (tr.Expected["public_gate_event"] != nil || tr.Expected["private_outcomes"] != float64(0)) {
				t.Fatal("unsent continue commits a gate outcome", tr.ID)
			}
		}
		if tr.Input["watermark_outstanding"] == true && (tr.Epoch == nil || tr.Epoch.Name != "terminal-input-barrier" || tr.Expected["late_input_starts_operation"] != false) {
			t.Fatal("outstanding watermark loses captured epoch", tr.ID)
		}
		if strings.HasPrefix(tr.ID, "B1-C055-") && !strings.HasSuffix(tr.ID, "-contract") {
			if tr.Expected["fatal"] != true || tr.Expected["operation_result"] != nil || tr.Expected["synthetic_status"] != nil || tr.Expected["reap_is_orderly"] != false || tr.Expected["cause_retained"] != true {
				t.Fatal("protocol_error becomes a terminal result", tr.ID)
			}
		}
		if strings.HasPrefix(tr.ID, "B1-C040-") && !strings.HasSuffix(tr.ID, "-contract") && (tr.Expected["same_turn_priority"] != "captured" || tr.Expected["late_input_starts_operation"] != false) {
			t.Fatal("captured epoch loses priority or discarded operation starts", tr.ID)
		}
		if tr.ID == "B1-C010-start-order" || tr.ID == "B1-C062-completion-order" || tr.ID == "B1-C028-gate-success-order" {
			var pairs [][2]string
			switch tr.ID {
			case "B1-C010-start-order":
				pairs = [][2]string{{"operation-start-epoch", "mode-setup"}, {"fresh-output-drain-and-mark", "private-start-release"}, {"public-operation-started-complete", "private-started-ack"}, {"private-started-ack", "private-start-released"}}
			case "B1-C062-completion-order":
				pairs = [][2]string{{"terminate-and-reap-descendants-exclude-completion-helper", "close-keepalives"}, {"stdout-EOF", "remove-FIFOs"}, {"stderr-EOF", "remove-FIFOs"}, {"remove-FIFOs", "private-input-closed"}, {"restore-unset-INT", "helper-state-bearing-prompt-ready"}, {"private-completed", "final-census"}, {"fresh-PTY-drain-and-mark", "cleanup-epoch-ended"}, {"cleanup-epoch-ended", "inspection"}}
			case "B1-C028-gate-success-order":
				pairs = [][2]string{{"continue-watermark-satisfied", "private-continue"}, {"complete-accepted-reply-write", "private-gate-continued"}}
			}
			positions := map[string]int{}
			for i, event := range tr.Events {
				positions[event] = i
			}
			for _, pair := range pairs {
				a, okA := positions[pair[0]]
				b, okB := positions[pair[1]]
				if !okA || !okB || a >= b {
					t.Fatal("broken expected ordering", tr.ID, pair)
				}
			}
		}
	}
}
