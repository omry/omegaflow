package bashbuild

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"unicode/utf8"
)

const tableLimit = 2 * 1024 * 1024

var hashPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var namePattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z_0-9]*$`)
var signalPattern = regexp.MustCompile(`^(?:[A-Z][A-Z0-9]*|(?:SIG)?RT(?:MIN\+|MAX-)[0-9]+)$`)
var integerPattern = regexp.MustCompile(`^(?:0|[1-9][0-9]*)$`)

// The expected digest comes from the independently verified manifest, never
// from the table or its qualification input inventory.
func readTable(name, expected string) ([]byte, error) {
	if !hashPattern.MatchString(expected) {
		return nil, fmt.Errorf("missing table manifest digest")
	}
	f, err := os.OpenFile(name, os.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("nonregular qualification table")
	}
	data, err := io.ReadAll(io.LimitReader(f, tableLimit+1))
	if err != nil {
		return nil, err
	}
	observed := sha256.Sum256(data)
	if len(data) > tableLimit || hex.EncodeToString(observed[:]) != expected {
		return nil, fmt.Errorf("qualification table bound or manifest mismatch")
	}
	return data, nil
}

// Check escaped Unicode before encoding/json can replace an invalid surrogate.
func tableEscapes(data []byte) error {
	for i := 0; i < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		i++
		if i >= len(data) {
			return fmt.Errorf("truncated escape")
		}
		if data[i] != 'u' {
			continue
		}
		if i+4 >= len(data) {
			return fmt.Errorf("truncated Unicode escape")
		}
		n, e := strconv.ParseUint(string(data[i+1:i+5]), 16, 16)
		if e != nil {
			return e
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return fmt.Errorf("unpaired low surrogate")
		}
		if n < 0xd800 || n > 0xdbff {
			continue
		}
		if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
			return fmt.Errorf("unpaired high surrogate")
		}
		low, e := strconv.ParseUint(string(data[i+3:i+7]), 16, 16)
		if e != nil || low < 0xdc00 || low > 0xdfff {
			return fmt.Errorf("invalid surrogate pair")
		}
		i += 6
	}
	return nil
}

func tableValue(d *json.Decoder, depth int) (any, error) {
	if depth > 64 {
		return nil, fmt.Errorf("qualification table nesting bound")
	}
	token, err := d.Token()
	if err != nil {
		return nil, err
	}
	switch token {
	case json.Delim('{'):
		out := map[string]any{}
		for d.More() {
			k, e := d.Token()
			if e != nil {
				return nil, e
			}
			key, ok := k.(string)
			if !ok {
				return nil, fmt.Errorf("invalid object key")
			}
			if _, ok := out[key]; ok {
				return nil, fmt.Errorf("duplicate qualification field")
			}
			value, e := tableValue(d, depth+1)
			if e != nil {
				return nil, e
			}
			out[key] = value
		}
		_, err = d.Token()
		return out, err
	case json.Delim('['):
		out := []any{}
		for d.More() {
			value, e := tableValue(d, depth+1)
			if e != nil {
				return nil, e
			}
			out = append(out, value)
		}
		_, err = d.Token()
		return out, err
	default:
		return token, nil
	}
}

// Canonical JSON follows tools/bash_builds.py: sorted keys, compact UTF-8,
// no HTML or line-separator escaping. Only schema-validated integers occur.
func tableCanonical(value any) string {
	switch v := value.(type) {
	case string:
		var b strings.Builder
		b.WriteByte('"')
		for _, r := range v {
			switch r {
			case '"', '\\':
				b.WriteByte('\\')
				b.WriteRune(r)
			case '\b':
				b.WriteString(`\b`)
			case '\f':
				b.WriteString(`\f`)
			case '\n':
				b.WriteString(`\n`)
			case '\r':
				b.WriteString(`\r`)
			case '\t':
				b.WriteString(`\t`)
			default:
				if r < 32 {
					fmt.Fprintf(&b, `\u%04x`, r)
				} else {
					b.WriteRune(r)
				}
			}
		}
		b.WriteByte('"')
		return b.String()
	case map[string]any:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, 0, len(keys))
		for _, k := range keys {
			parts = append(parts, tableCanonical(k)+":"+tableCanonical(v[k]))
		}
		return "{" + strings.Join(parts, ",") + "}"
	case []any:
		parts := make([]string, len(v))
		for i, x := range v {
			parts[i] = tableCanonical(x)
		}
		return "[" + strings.Join(parts, ",") + "]"
	default:
		data, _ := json.Marshal(v)
		return string(data)
	}
}
func tableDigest(value any) string {
	h := sha256.Sum256([]byte(tableCanonical(value)))
	return hex.EncodeToString(h[:])
}

// A sticky error keeps every invalid type on a safe empty validation path.
// No partially validated table is ever returned for admission.
type tableValidator struct{ err error }

func (v *tableValidator) require(ok bool) {
	if !ok && v.err == nil {
		v.err = fmt.Errorf("invalid qualification table schema or evidence")
	}
}
func (v *tableValidator) object(x any) map[string]any {
	m, ok := x.(map[string]any)
	v.require(ok)
	return m
}
func (v *tableValidator) shape(x any, fields string) map[string]any {
	m := v.object(x)
	keys := strings.Fields(fields)
	v.require(len(m) == len(keys))
	for _, k := range keys {
		_, ok := m[k]
		v.require(ok)
	}
	return m
}
func (v *tableValidator) array(x any) []any   { a, ok := x.([]any); v.require(ok); return a }
func (v *tableValidator) string(x any) string { s, ok := x.(string); v.require(ok); return s }
func (v *tableValidator) text(x any) string {
	s := v.string(x)
	v.require(len(s) > 0 && len(s) <= 4096 && !strings.ContainsRune(s, 0))
	return s
}
func (v *tableValidator) digest(x any) { v.require(hashPattern.MatchString(v.string(x))) }
func (v *tableValidator) absolute(x any) string {
	s := v.text(x)
	normalized := path.Clean(s)
	if strings.HasPrefix(s, "//") && !strings.HasPrefix(s, "///") {
		normalized = "/" + normalized
	}
	v.require(strings.HasPrefix(s, "/") && normalized == s)
	return s
}
func (v *tableValidator) target(x any) {
	m := v.shape(x, "os arch os_release_id version_id")
	v.require(m["os"] == "linux" && (m["arch"] == "amd64" || m["arch"] == "arm64"))
	v.text(m["os_release_id"])
	v.text(m["version_id"])
}
func (v *tableValidator) identities(x any) map[string]any {
	m := v.object(x)
	v.require(len(m) > 0)
	for k, d := range m {
		v.absolute(k)
		v.digest(d)
	}
	return m
}
func (v *tableValidator) adapter(x any) map[string]any {
	m := v.identities(x)
	for _, k := range []string{"/omegaflow-runtime/bin/awsh", "/omegaflow-runtime/etc/awsh-bashrc", "/omegaflow-runtime/etc/inputrc"} {
		_, ok := m[k]
		v.require(ok)
	}
	_, own := m[tablePath]
	v.require(!own)
	terminal, locale := false, false
	for k := range m {
		v.require(strings.HasPrefix(k, "/omegaflow-runtime/"))
		terminal = terminal || strings.HasPrefix(k, "/omegaflow-runtime/share/terminfo/")
		locale = locale || strings.HasPrefix(k, "/omegaflow-runtime/lib/locale/")
	}
	v.require(terminal && locale)
	return m
}
func (v *tableValidator) measurements(x any) {
	m := v.shape(x, "startup_pty_base64 startup_export catchable_signals readline")
	raw := v.string(m["startup_pty_base64"])
	data, e := base64.StdEncoding.Strict().DecodeString(raw)
	v.require(e == nil && len(data) <= 4096 && base64.StdEncoding.EncodeToString(data) == raw)
	ex := v.shape(m["startup_export"], "unset set")
	set := v.object(ex["set"])
	seen := map[string]bool{}
	for _, item := range v.array(ex["unset"]) {
		n := v.string(item)
		_, overlap := set[n]
		v.require(namePattern.MatchString(n) && !seen[n] && !overlap)
		seen[n] = true
	}
	for n, item := range set {
		v.require(namePattern.MatchString(n))
		v.require(!strings.ContainsRune(v.string(item), 0))
	}
	signals := v.object(m["catchable_signals"])
	for _, n := range []string{"INT", "CHLD", "USR1"} {
		_, ok := signals[n]
		v.require(ok)
	}
	for name, item := range signals {
		v.require(signalPattern.MatchString(name) && name != "KILL" && name != "STOP" && name != "SIGKILL" && name != "SIGSTOP")
		number, ok := item.(json.Number)
		n, e := strconv.ParseUint(string(number), 10, 8)
		v.require(ok && e == nil && n >= 1 && n <= 64 && integerPattern.MatchString(string(number)))
	}
	line := v.shape(m["readline"], "keymap loader_hex submit_hex readiness no_redisplay utf8_cursor maximum_line_bytes")
	v.text(line["keymap"])
	v.require(line["loader_hex"] == "1801" && line["submit_hex"] == "1802")
	for _, k := range []string{"readiness", "no_redisplay", "utf8_cursor"} {
		v.require(line[k] == true)
	}
	n, ok := line["maximum_line_bytes"].(json.Number)
	s := string(n)
	v.require(ok && integerPattern.MatchString(s) && (len(s) > 6 || (len(s) == 6 && s >= "786432")))
}
func (v *tableValidator) entry(key string, x any, required []string) {
	v.digest(key)
	entry := v.shape(x, "candidate qualification")
	c := v.shape(entry["candidate"], "release target artifact_sha256 executable_sha256 definition_sha256 lock_sha256 resolved_path system_rc")
	v.text(c["release"])
	v.target(c["target"])
	for _, k := range []string{"artifact_sha256", "executable_sha256", "definition_sha256", "lock_sha256"} {
		v.digest(c[k])
	}
	v.absolute(c["resolved_path"])
	if c["system_rc"] != "none" {
		v.absolute(c["system_rc"])
	}
	v.require(c["executable_sha256"] == key)
	q := v.shape(entry["qualification"], "version candidate_sha256 inputs assets adapter_inputs target probe repetitions result result_sha256")
	v.require(q["version"] == json.Number("1") && q["repetitions"] == json.Number("2"))
	v.require(q["candidate_sha256"] == tableDigest(c))
	inputs := v.identities(q["inputs"])
	adapter := v.adapter(q["adapter_inputs"])
	assets := v.object(q["assets"])
	v.require(len(assets) == len(adapter))
	for name, item := range assets {
		p := v.absolute(item)
		identity, ok := adapter[name]
		v.require(ok && reflect.DeepEqual(inputs[p], identity))
	}
	v.require(reflect.DeepEqual(inputs[v.string(c["resolved_path"])], c["executable_sha256"]))
	v.target(q["target"])
	v.require(reflect.DeepEqual(c["target"], q["target"]))
	probe := v.array(q["probe"])
	v.require(len(probe) > 0)
	for _, arg := range probe {
		v.text(arg)
	}
	if len(probe) > 0 {
		p := v.absolute(probe[0])
		_, ok := inputs[p]
		v.require(ok)
	}
	result := v.shape(q["result"], "measurements cases")
	v.measurements(result["measurements"])
	cases := v.object(result["cases"])
	v.require(len(cases) == len(required))
	for _, id := range required {
		v.require(cases[id] == true)
	}
	v.require(q["result_sha256"] == tableDigest(result))
}

func parseTable(data []byte) (struct{ Builds map[string]Entry }, error) {
	var out struct{ Builds map[string]Entry }
	if len(data) > tableLimit || !utf8.Valid(data) {
		return out, fmt.Errorf("invalid qualification bytes")
	}
	if err := tableEscapes(data); err != nil {
		return out, err
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	value, err := tableValue(d, 0)
	if err != nil {
		return out, err
	}
	if _, err = d.Token(); err != io.EOF {
		return out, fmt.Errorf("trailing qualification data")
	}
	var required []string
	if err = json.Unmarshal([]byte(requiredCasesJSON), &required); err != nil {
		return out, err
	}
	v := tableValidator{}
	m := v.shape(value, "version builds")
	v.require(m["version"] == json.Number("1"))
	for k, e := range v.object(m["builds"]) {
		v.entry(k, e, required)
	}
	if v.err != nil {
		return out, v.err
	}
	err = json.Unmarshal(data, &out)
	return out, err
}
