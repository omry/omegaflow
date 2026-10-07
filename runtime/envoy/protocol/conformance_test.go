package protocol

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
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
	bad(17, "C034", "stdout-EOF", "stdout-eof", "keepalive-close stdout-stderr-EOF FIFO-removal-blocked")
	bad(17, "C034", "stderr-EOF", "stderr-eof", "keepalive-close stdout-stderr-EOF FIFO-removal-blocked")
	bad(17, "C034", "keepalive-after-cleanup", "keepalive-after-cleanup", "keepalive-close stdout-stderr-EOF FIFO-removal-blocked")
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
	for _, name := range []string{"input-close", "input-closed", "prompt-ready", "completed"} {
		ok(26, "C064", name, "@accepted", "ordinary-completion", "ordinary-completion-frame", "operation-cleanup-epoch "+name+" "+name+"-accepted")
	}
	for _, name := range []string{"cleanup-helper-exclusion", "wait-record-removal", "empty-jobs", "repeat-adapter-validation", "final-PTY-drain"} {
		ok(27, "C064", name, "cleanup-postcondition-satisfied", "cleanup-postcondition", "ordinary-completion-frame", "cleanup-epoch "+name+" cleanup-postcondition-satisfied")
	}
	ok(28, "C064", "fresh-termios", "fresh-readline-transition-required", "readline-ready", "ordinary-completion-frame", "cleanup-complete fresh-termios-snapshot readline-transition-required")
	ok(29, "C064", "live-function live-alias live-positional live-unexported live-options", "persistent-state-live", "persistent-state", "ordinary-completion-frame", "operation-return persistent-state-captured persistent-state-live")
	for _, name := range []string{"stale-PID", "extra-PID", "wrong-parent", "wrong-executable", "early-release", "stale-status", "stale-cwd", "stale-env", "duplicate", "malformed"} {
		bad(30, "C064", name, "completion-evidence-rejected", "completion-evidence-received "+name+" completion-evidence-rejected")
	}
	ok(31, "C064", "PTY-EOF", "PTY-EOF-reached", "output-boundary", "ordinary-completion-frame", "cleanup-epoch PTY-EOF output-boundary-validated")
	for _, name := range []string{"stdout-EOF", "stderr-EOF"} {
		bad(31, "C064", name, name+"-missing-peer", "cleanup-epoch "+name+" missing-peer-EOF")
	}
	for _, name := range []string{"helper-INT-ignore", "canonical-INT-restore", "allowed-trap-final-state"} {
		ok(32, "C064", name, "final-adapter-state-validated", "final-adapter-state", "ordinary-completion-frame", "completion-helper-window "+name+" final-adapter-state-validated")
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

func checkCaseMap(cases []conformanceCase, inv inventory, traces map[string]expectedTrace, refs map[caseRef]bool, tests map[string]bool, aux auxiliaryFixtures) error {
	seen, coverage := map[string]bool{}, map[string]bool{}
	wireCoverage := map[caseRef]bool{}
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
		if c.Package != strings.Split(c.ImplementationLeaf, ".")[0] || c.Prerequisites != leaf.Prerequisites || c.ClosurePrerequisites != closure.Prerequisites {
			return fmt.Errorf("ownership or prerequisite drift: %s", c.ID)
		}
		if !refs[c.Fixture] || !tests[c.StaticTest] || !traced || len(trace.Input) == 0 || len(trace.Expected) == 0 || len(trace.Events) == 0 {
			return fmt.Errorf("dangling fixture, trace or test: %s", c.ID)
		}
		if contract, ok := trace.Expected["contract"].(string); ok && contract != c.Requirement {
			return fmt.Errorf("trace requirement pointer drift: %s", c.ID)
		}
		if trace.Input["expanded_case"] == true {
			expandedCount++
			if err := checkExpandedTrace(c.ID, c.Requirement, trace); err != nil {
				return err
			}
		} else if !strings.HasSuffix(c.ID, "-contract") && len(trace.Expected) == 1 && trace.Expected["contract"] != nil {
			return fmt.Errorf("generic expanded placeholder: %s", c.ID)
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
	}
	for _, tr := range fixtureRows[expectedTrace](t, "traces.jsonl") {
		if _, found := traces[tr.ID]; found {
			t.Fatal("duplicate trace", tr.ID)
		}
		traces[tr.ID], refs[caseRef{"traces.jsonl", tr.ID}] = tr, true
	}
	for _, name := range []string{"controller.jsonl", "envoy.jsonl", "public-invalid.jsonl", "public-maximum.jsonl", "private.jsonl", "helper.jsonl"} {
		for _, row := range bytes.Split(bytes.TrimSuffix(fixtureData(t, name), []byte{'\n'}), []byte{'\n'}) {
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
			outcome := "ready"
			if len(received) > 4096 || len(received) > len(expected) || !bytes.Equal(received, expected[:min(len(received), len(expected))]) || f.EOF && len(received) != len(expected) || f.DeadlineExpired {
				outcome = "fatal"
			} else if !f.ReadyReceived {
				outcome = "buffer"
			} else if int64(len(received)) < f.OutputThrough {
				outcome = "wait"
			}
			if outcome != f.Expected || f.OutputThrough != int64(len(expected)) {
				t.Fatal("inconsistent startup expectation", outcome, f.Expected)
			}
			b := []byte(fmt.Sprintf(`{"schema":"omegaflow-envoy-telemetry-v1","type":"ready","seq":1,"envoy_pid":1,"shell_pid":2,"cwd":"/work","columns":80,"rows":24,"elapsed_us":0,"output_through":%d}`+"\n", f.OutputThrough))
			if _, err := DecodeEnvoy(b); err != nil {
				t.Fatal(err)
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
			source := tr.Input["source"].(string)
			status := int(tr.Input["status"].(float64))
			redirects := ""
			if tr.Input["mode"] == "split" {
				redirects = " > /run/omegaflow/session/split/op.stdout 2> /run/omegaflow/session/split/op.stderr"
			}
			frame := fmt.Sprintf("{\n__OMEGAFLOW_AWSH_START_RELEASED\n__OMEGAFLOW_AWSH_ENTER %d off emacs\n__OMEGAFLOW_AWSH_INNER_ENTERED=0\n{\n__OMEGAFLOW_AWSH_INNER_ENTERED=1\n__OMEGAFLOW_AWSH_RETURN %d\n", status, status) + source + "\n\n" + "__OMEGAFLOW_AWSH_RETURN \"$?\" && (( 1 ))\n}" + redirects + "\n__OMEGAFLOW_AWSH_STATUS=$?\nif [[ $__OMEGAFLOW_AWSH_INNER_ENTERED != 1 ]]; then\n__OMEGAFLOW_AWSH_FAIL_STOP\nfi\n__OMEGAFLOW_AWSH_RETURN \"$__OMEGAFLOW_AWSH_STATUS\" && (( 1 ))\n}"
			if tr.Expected["frame_hex"] != hex.EncodeToString([]byte(frame)) || tr.Expected["loader_stdout_hex"] != hex.EncodeToString([]byte(frame+"x")) || tr.Expected["loader_input_hex"] != "1801" || tr.Expected["submit_input_hex"] != "1802" || tr.Expected["Bash_executed"] != false {
				t.Fatal("altered canonical frame, LF boundary or marker", tr.ID)
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
