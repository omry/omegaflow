package protocol

import (
	"encoding/json"
	"strconv"
	"strings"
)

// InspectionKind is the closed v1 inspection request kind set.
const (
	InspectionFileExists = "file_exists"
	InspectionProduces   = "produces"
)

// PathKind is the closed v1 resolved-path kind set.
const (
	PathKindFile      = "file"
	PathKindDirectory = "directory"
	PathKindOther     = "other"
)

// DigestAlgorithm tags which encoding produced a directory digest. The tag is
// not recoverable from the digest, so retained records carry it alongside.
const (
	DigestDirectoryV2     = "directory-v2"
	DigestDirectoryNative = "directory"
)

// InspectionSpec is one bounded workload inspection request entry. A
// file_exists entry has no producer fields; a produces entry requires both.
type InspectionSpec struct {
	InspectionID string
	Kind         string
	Path         string
	ProducerID   string
	OutputID     string
}

// DeterministicInspectionID returns the controller's deterministic
// request-order inspection ID for a zero-based index: inspection-1,
// inspection-2, and so on.
func DeterministicInspectionID(index int) string {
	return "inspection-" + strconv.Itoa(index+1)
}

// AssignInspectionIDs sets each spec's InspectionID deterministically in
// request order, returning the same slice.
func AssignInspectionIDs(specs []InspectionSpec) []InspectionSpec {
	for index := range specs {
		specs[index].InspectionID = DeterministicInspectionID(index)
	}
	return specs
}

func (spec InspectionSpec) MarshalJSON() ([]byte, error) {
	if err := validateInspectionSpec(spec); err != nil {
		return nil, err
	}
	var builder strings.Builder
	builder.WriteString(`{"inspection_id":`)
	writeJSONString(&builder, spec.InspectionID)
	builder.WriteString(`,"kind":`)
	writeJSONString(&builder, spec.Kind)
	builder.WriteString(`,"path":`)
	writeJSONString(&builder, spec.Path)
	if spec.Kind == InspectionProduces {
		builder.WriteString(`,"producer_id":`)
		writeJSONString(&builder, spec.ProducerID)
		builder.WriteString(`,"output_id":`)
		writeJSONString(&builder, spec.OutputID)
	}
	builder.WriteString("}")
	return []byte(builder.String()), nil
}

func (spec *InspectionSpec) UnmarshalJSON(data []byte) error {
	fields, err := objectFields(data)
	if err != nil {
		return err
	}
	kind, err := exactStringField(fields, "kind")
	if err != nil {
		return err
	}
	expected := map[string]bool{"inspection_id": true, "kind": true, "path": true}
	if kind == InspectionProduces {
		expected["producer_id"] = true
		expected["output_id"] = true
	}
	if err := requireExactFields(fields, expected, "inspection entry"); err != nil {
		return err
	}
	spec.Kind = kind
	if spec.InspectionID, err = exactStringField(fields, "inspection_id"); err != nil {
		return err
	}
	if spec.Path, err = exactStringField(fields, "path"); err != nil {
		return err
	}
	if kind == InspectionProduces {
		if spec.ProducerID, err = exactStringField(fields, "producer_id"); err != nil {
			return err
		}
		if spec.OutputID, err = exactStringField(fields, "output_id"); err != nil {
			return err
		}
	}
	return validateInspectionSpec(*spec)
}

func validateInspectionSpec(spec InspectionSpec) error {
	if err := validateID("inspection_id", spec.InspectionID); err != nil {
		return err
	}
	if err := validateText("path", spec.Path, MaxInspectionPathBytes); err != nil {
		return err
	}
	switch spec.Kind {
	case InspectionFileExists:
		if spec.ProducerID != "" || spec.OutputID != "" {
			return protocolError("invalid-field", "file_exists entries have no producer fields")
		}
	case InspectionProduces:
		if err := firstError(validateID("producer_id", spec.ProducerID), validateID("output_id", spec.OutputID)); err != nil {
			return err
		}
	default:
		return protocolError("invalid-field", "kind must be file_exists or produces")
	}
	return nil
}

func validateInspectionSpecs(specs []InspectionSpec, observation ObservationMode) error {
	if len(specs) > MaxInspectionsPerOperation {
		return protocolError("invalid-field", "too many inspections")
	}
	seen := make(map[string]bool, len(specs))
	for _, spec := range specs {
		if err := validateInspectionSpec(spec); err != nil {
			return err
		}
		if seen[spec.InspectionID] {
			return protocolError("invalid-field", "inspection_id values must be unique")
		}
		seen[spec.InspectionID] = true
	}
	if len(specs) > 0 && observation != ObservationExclusive {
		return protocolError("invalid-field", "an operation with inspections requires exclusive observation")
	}
	return nil
}

// InspectionResult is one typed workload inspection result in request order.
// A file_exists result carries no digest or producer fields. A produces
// result repeats the authored producer and output IDs and carries one
// lowercase SHA-256 digest; a directory result also carries the tag that
// domain-separated its hash input.
type InspectionResult struct {
	InspectionID    string
	Kind            string
	ResolvedPath    string
	PathKind        string
	ProducerID      string
	OutputID        string
	SHA256          string
	DigestAlgorithm string
}

func (result InspectionResult) MarshalJSON() ([]byte, error) {
	if err := validateInspectionResult(result); err != nil {
		return nil, err
	}
	var builder strings.Builder
	builder.WriteString(`{"inspection_id":`)
	writeJSONString(&builder, result.InspectionID)
	builder.WriteString(`,"kind":`)
	writeJSONString(&builder, result.Kind)
	builder.WriteString(`,"resolved_path":`)
	writeJSONString(&builder, result.ResolvedPath)
	builder.WriteString(`,"path_kind":`)
	writeJSONString(&builder, result.PathKind)
	if result.Kind == InspectionProduces {
		builder.WriteString(`,"producer_id":`)
		writeJSONString(&builder, result.ProducerID)
		builder.WriteString(`,"output_id":`)
		writeJSONString(&builder, result.OutputID)
		builder.WriteString(`,"sha256":`)
		writeJSONString(&builder, result.SHA256)
		if result.PathKind == PathKindDirectory {
			builder.WriteString(`,"digest_algorithm":`)
			writeJSONString(&builder, result.DigestAlgorithm)
		}
	}
	builder.WriteString("}")
	return []byte(builder.String()), nil
}

func (result *InspectionResult) UnmarshalJSON(data []byte) error {
	fields, err := objectFields(data)
	if err != nil {
		return err
	}
	kind, err := exactStringField(fields, "kind")
	if err != nil {
		return err
	}
	pathKind, err := exactStringField(fields, "path_kind")
	if err != nil {
		return err
	}
	expected := map[string]bool{"inspection_id": true, "kind": true, "resolved_path": true, "path_kind": true}
	if kind == InspectionProduces {
		expected["producer_id"] = true
		expected["output_id"] = true
		expected["sha256"] = true
		if pathKind == PathKindDirectory {
			expected["digest_algorithm"] = true
		}
	}
	if err := requireExactFields(fields, expected, "inspection result"); err != nil {
		return err
	}
	result.Kind = kind
	result.PathKind = pathKind
	if result.InspectionID, err = exactStringField(fields, "inspection_id"); err != nil {
		return err
	}
	if result.ResolvedPath, err = exactStringField(fields, "resolved_path"); err != nil {
		return err
	}
	if kind == InspectionProduces {
		if result.ProducerID, err = exactStringField(fields, "producer_id"); err != nil {
			return err
		}
		if result.OutputID, err = exactStringField(fields, "output_id"); err != nil {
			return err
		}
		if result.SHA256, err = exactStringField(fields, "sha256"); err != nil {
			return err
		}
		if pathKind == PathKindDirectory {
			if result.DigestAlgorithm, err = exactStringField(fields, "digest_algorithm"); err != nil {
				return err
			}
		}
	}
	return validateInspectionResult(*result)
}

func validateInspectionResult(result InspectionResult) error {
	if err := validateID("inspection_id", result.InspectionID); err != nil {
		return err
	}
	if err := validateCWD(result.ResolvedPath); err != nil {
		return protocolError("invalid-field", "resolved_path must be one bounded absolute path")
	}
	switch result.Kind {
	case InspectionFileExists:
		switch result.PathKind {
		case PathKindFile, PathKindDirectory, PathKindOther:
		default:
			return protocolError("invalid-field", "path_kind must be file, directory, or other")
		}
		if result.ProducerID != "" || result.OutputID != "" || result.SHA256 != "" || result.DigestAlgorithm != "" {
			return protocolError("invalid-field", "file_exists results have no digest or producer fields")
		}
	case InspectionProduces:
		if err := firstError(validateID("producer_id", result.ProducerID), validateID("output_id", result.OutputID)); err != nil {
			return err
		}
		if !sha256Pattern.MatchString(result.SHA256) {
			return protocolError("invalid-field", "sha256 must be 64 lowercase hexadecimal characters")
		}
		switch result.PathKind {
		case PathKindFile:
			if result.DigestAlgorithm != "" {
				return protocolError("invalid-field", "file digests carry no digest_algorithm tag")
			}
		case PathKindDirectory:
			if result.DigestAlgorithm != DigestDirectoryV2 && result.DigestAlgorithm != DigestDirectoryNative {
				return protocolError("invalid-field", "digest_algorithm must be directory-v2 or directory")
			}
		default:
			return protocolError("invalid-field", "a produces result must be a file or directory")
		}
	default:
		return protocolError("invalid-field", "kind must be file_exists or produces")
	}
	return nil
}

func validateInspectionResults(results []InspectionResult) error {
	seen := make(map[string]bool, len(results))
	if len(results) > MaxInspectionsPerOperation {
		return protocolError("invalid-field", "too many inspection results")
	}
	for _, result := range results {
		if err := validateInspectionResult(result); err != nil {
			return err
		}
		if seen[result.InspectionID] {
			return protocolError("invalid-field", "inspection result IDs must be unique")
		}
		seen[result.InspectionID] = true
	}
	return nil
}

// ValidateInspectionResultsAgainstSpecs requires results in request order,
// each repeating its request's ID, kind, and produces identities.
func ValidateInspectionResultsAgainstSpecs(specs []InspectionSpec, results []InspectionResult) error {
	if len(results) != len(specs) {
		return protocolError("inspection-mismatch", "inspection results must answer every request in order")
	}
	for index, spec := range specs {
		result := results[index]
		if result.InspectionID != spec.InspectionID || result.Kind != spec.Kind {
			return protocolError("inspection-mismatch", "inspection results must repeat request IDs and kinds in order")
		}
		if spec.Kind == InspectionProduces && (result.ProducerID != spec.ProducerID || result.OutputID != spec.OutputID) {
			return protocolError("inspection-mismatch", "produces results must repeat the authored producer and output IDs")
		}
	}
	return nil
}

// ResolvedInspection is one entry of RESOLVED_INSPECTIONS_JSON: it retains
// the request identifiers and kind, replaces path with the absolute resolved
// path, and contains no filesystem results.
type ResolvedInspection struct {
	InspectionID string
	Kind         string
	ResolvedPath string
	ProducerID   string
	OutputID     string
}

func (resolved ResolvedInspection) MarshalJSON() ([]byte, error) {
	if err := validateResolvedInspection(resolved); err != nil {
		return nil, err
	}
	var builder strings.Builder
	builder.WriteString(`{"inspection_id":`)
	writeJSONString(&builder, resolved.InspectionID)
	builder.WriteString(`,"kind":`)
	writeJSONString(&builder, resolved.Kind)
	builder.WriteString(`,"resolved_path":`)
	writeJSONString(&builder, resolved.ResolvedPath)
	if resolved.Kind == InspectionProduces {
		builder.WriteString(`,"producer_id":`)
		writeJSONString(&builder, resolved.ProducerID)
		builder.WriteString(`,"output_id":`)
		writeJSONString(&builder, resolved.OutputID)
	}
	builder.WriteString("}")
	return []byte(builder.String()), nil
}

func (resolved *ResolvedInspection) UnmarshalJSON(data []byte) error {
	fields, err := objectFields(data)
	if err != nil {
		return err
	}
	kind, err := exactStringField(fields, "kind")
	if err != nil {
		return err
	}
	expected := map[string]bool{"inspection_id": true, "kind": true, "resolved_path": true}
	if kind == InspectionProduces {
		expected["producer_id"] = true
		expected["output_id"] = true
	}
	if err := requireExactFields(fields, expected, "resolved inspection"); err != nil {
		return err
	}
	resolved.Kind = kind
	if resolved.InspectionID, err = exactStringField(fields, "inspection_id"); err != nil {
		return err
	}
	if resolved.ResolvedPath, err = exactStringField(fields, "resolved_path"); err != nil {
		return err
	}
	if kind == InspectionProduces {
		if resolved.ProducerID, err = exactStringField(fields, "producer_id"); err != nil {
			return err
		}
		if resolved.OutputID, err = exactStringField(fields, "output_id"); err != nil {
			return err
		}
	}
	return validateResolvedInspection(*resolved)
}

func validateResolvedInspection(resolved ResolvedInspection) error {
	if err := validateID("inspection_id", resolved.InspectionID); err != nil {
		return err
	}
	if err := validateText("resolved_path", resolved.ResolvedPath, MaxInspectionPathBytes); err != nil {
		return err
	}
	if !strings.HasPrefix(resolved.ResolvedPath, "/") {
		return protocolError("invalid-field", "resolved_path must be absolute")
	}
	switch resolved.Kind {
	case InspectionFileExists:
		if resolved.ProducerID != "" || resolved.OutputID != "" {
			return protocolError("invalid-field", "file_exists entries have no producer fields")
		}
	case InspectionProduces:
		if err := firstError(validateID("producer_id", resolved.ProducerID), validateID("output_id", resolved.OutputID)); err != nil {
			return err
		}
	default:
		return protocolError("invalid-field", "kind must be file_exists or produces")
	}
	return nil
}

// EncodeInspectionSpecs returns the compact INSPECTIONS_JSON encoding of the
// already validated public inspection array.
func EncodeInspectionSpecs(specs []InspectionSpec) ([]byte, error) {
	if err := validateInspectionSpecs(specs, ObservationExclusive); err != nil {
		return nil, err
	}
	if specs == nil {
		specs = []InspectionSpec{}
	}
	return marshalCanonical(specs)
}

// EncodeResolvedInspections returns the compact RESOLVED_INSPECTIONS_JSON
// encoding of one resolved plan.
func EncodeResolvedInspections(resolved []ResolvedInspection) ([]byte, error) {
	if resolved == nil {
		resolved = []ResolvedInspection{}
	}
	if err := validateResolvedInspections(resolved); err != nil {
		return nil, err
	}
	return marshalCanonical(resolved)
}

// DecodeInspectionSpecs parses one bounded INSPECTIONS_JSON field.
func DecodeInspectionSpecs(data []byte) ([]InspectionSpec, error) {
	specs := []InspectionSpec{}
	if err := decodeExact(data, &specs); err != nil {
		return nil, err
	}
	if err := validateInspectionSpecs(specs, ObservationExclusive); err != nil {
		return nil, err
	}
	return specs, nil
}

// DecodeResolvedInspections parses one bounded RESOLVED_INSPECTIONS_JSON
// field, enforcing the per-operation inspection bound and unique IDs.
func DecodeResolvedInspections(data []byte) ([]ResolvedInspection, error) {
	resolved := []ResolvedInspection{}
	if err := decodeExact(data, &resolved); err != nil {
		return nil, err
	}
	if err := validateResolvedInspections(resolved); err != nil {
		return nil, err
	}
	return resolved, nil
}

func validateResolvedInspections(resolved []ResolvedInspection) error {
	if len(resolved) > MaxInspectionsPerOperation {
		return protocolError("invalid-field", "too many resolved inspections")
	}
	seen := make(map[string]bool, len(resolved))
	for _, entry := range resolved {
		if err := validateResolvedInspection(entry); err != nil {
			return err
		}
		if seen[entry.InspectionID] {
			return protocolError("invalid-field", "resolved inspection IDs must be unique")
		}
		seen[entry.InspectionID] = true
	}
	return nil
}

// PathResolver resolves configured inspection paths with the persistent
// shell's resulting cwd and exported environment, preserving the native
// runner's successful path behavior: defined $NAME and ${NAME} references
// expand, undefined or malformed references stay literal, a leading ~ or
// ~user expands through HOME or the injected user database, and the expanded
// relative path is anchored at the resulting cwd. Resolution performs no
// command substitution, arithmetic expansion, word splitting, or globbing.
type PathResolver struct {
	// Env is the exported environment reported at the operation boundary.
	Env map[string]string
	// CWD is the persistent shell's resulting absolute working directory.
	CWD string
	// LookupHome resolves a home directory from the workload's user
	// database; the empty user selects the current effective identity.
	LookupHome func(user string) (string, bool)
}

// Resolve expands one configured path lexically. The result is absolute when
// expansion produces an absolute path and is otherwise anchored at CWD. An
// unresolved home expression remains literal and will ordinarily fail the
// subsequent existence check; that failure belongs to the caller.
func (resolver PathResolver) Resolve(configured string) (string, error) {
	if err := validateText("path", configured, MaxInspectionPathBytes); err != nil {
		return "", err
	}
	if !strings.HasPrefix(resolver.CWD, "/") {
		return "", protocolError("invalid-field", "resolver cwd must be absolute")
	}
	expanded := expandEnvReferences(configured, resolver.Env)
	expanded = resolver.expandHome(expanded)
	if strings.HasPrefix(expanded, "/") {
		return expanded, nil
	}
	base := resolver.CWD
	if base != "/" {
		base += "/"
	}
	return base + expanded, nil
}

func expandEnvReferences(value string, env map[string]string) string {
	var builder strings.Builder
	for index := 0; index < len(value); {
		if value[index] != '$' {
			builder.WriteByte(value[index])
			index++
			continue
		}
		if index+1 < len(value) && value[index+1] == '{' {
			end := strings.IndexByte(value[index+2:], '}')
			if end < 0 {
				builder.WriteString(value[index:])
				return builder.String()
			}
			name := value[index+2 : index+2+end]
			if replacement, defined := env[name]; defined && name != "" {
				builder.WriteString(replacement)
			} else {
				builder.WriteString(value[index : index+2+end+1])
			}
			index += 2 + end + 1
			continue
		}
		nameEnd := index + 1
		for nameEnd < len(value) && isEnvNameByte(value[nameEnd]) {
			nameEnd++
		}
		name := value[index+1 : nameEnd]
		if replacement, defined := env[name]; defined && name != "" {
			builder.WriteString(replacement)
		} else {
			builder.WriteString(value[index:nameEnd])
		}
		index = nameEnd
	}
	return builder.String()
}

func isEnvNameByte(value byte) bool {
	return value == '_' || (value >= '0' && value <= '9') || (value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z')
}

func (resolver PathResolver) expandHome(value string) string {
	if !strings.HasPrefix(value, "~") {
		return value
	}
	slash := strings.IndexByte(value, '/')
	user := value[1:]
	rest := ""
	if slash >= 0 {
		user = value[1:slash]
		rest = value[slash:]
	}
	home := ""
	found := false
	if user == "" {
		if fromEnv, defined := resolver.Env["HOME"]; defined {
			home, found = fromEnv, true
		} else if resolver.LookupHome != nil {
			home, found = resolver.LookupHome("")
		}
	} else if resolver.LookupHome != nil {
		home, found = resolver.LookupHome(user)
	}
	if !found {
		return value
	}
	return strings.TrimSuffix(home, "/") + rest
}

func exactStringField(fields map[string]json.RawMessage, name string) (string, error) {
	raw, present := fields[name]
	if !present {
		return "", protocolError("missing-field", name)
	}
	if isJSONNull(raw) {
		return "", protocolError("invalid-field", name+" must not be null")
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", protocolError("invalid-field", name+" must be a string")
	}
	return value, nil
}

func requireExactFields(fields map[string]json.RawMessage, expected map[string]bool, label string) error {
	for name := range expected {
		if _, present := fields[name]; !present {
			return protocolError("missing-field", name)
		}
	}
	for name := range fields {
		if !expected[name] {
			return protocolError("unknown-field", label+" field "+name)
		}
	}
	return nil
}

func writeJSONString(builder *strings.Builder, value string) {
	encoded, _ := marshalCanonical(value)
	builder.Write(encoded)
}
