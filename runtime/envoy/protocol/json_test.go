package protocol

import (
	"bytes"
	"fmt"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestMalformedFrames(t *testing.T) {
	good := corpus(t)[0]
	for name, b := range map[string][]byte{
		"empty": {}, "whitespace": []byte("  \n"), "short": {'\n'}, "no LF": good[:len(good)-1], "CRLF": append(append([]byte(nil), good[:len(good)-1]...), '\r', '\n'),
		"embedded LF": bytes.Replace(good, []byte("hello"), []byte("hel\nlo"), 1), "NUL": bytes.Replace(good, []byte("hello"), []byte("hel\x00lo"), 1),
		"invalid UTF8": bytes.Replace(good, []byte("hello"), []byte{'h', 0xff}, 1), "array": []byte("[]\n"), "string": []byte("\"hello\"\n"), "null": []byte("null\n"), "number": []byte("1\n"),
		"trailing": append(append([]byte(nil), good[:len(good)-1]...), []byte(" {}\n")...), "concatenated": append(append([]byte(nil), good...), good...),
		"bad JSON": []byte("{bad}\n"), "incomplete": []byte("{\n"), "missing value": []byte("{\"seq\":}\n"), "nonfinite": []byte("{\"seq\":NaN}\n"),
		"duplicate":         bytes.Replace(good, []byte("\"seq\":1"), []byte("\"seq\":1,\"seq\":2"), 1),
		"escaped duplicate": bytes.Replace(good, []byte("\"seq\":1"), []byte("\"seq\":1,\"s\\u0065q\":2"), 1),
		"oversized":         append(bytes.Repeat([]byte{' '}, MaxFrameBytes), good...),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeController(b); err == nil {
				t.Fatal("accepted malformed frame")
			}
		})
	}
	for i := 0; i < len(good)-1; i++ {
		if _, err := DecodeController(good[:i]); err == nil {
			t.Fatalf("accepted fragmented prefix %d", i)
		}
	}
	// Whitespace and different incoming key order are legal; outgoing order is fixed.
	f := object(t, good)
	accept(t, append([]byte(" \t\r"), frame(t, f)...))
	b := corpus(t)[21]
	duplicate := bytes.Replace(b, []byte("\"path\":\"directory\""), []byte("\"path\":\"directory\",\"path\":\"other\""), 1)
	reject(t, duplicate)
}

func TestUnicodeEscapes(t *testing.T) {
	original := corpus(t)[18]
	for _, value := range []string{`"\ud800"`, `"\udc00"`, `"\ud800\u0041"`, `"\ud800x"`, `"\ud800\ud800"`, `"\uZZZZ"`, `"\u12"`, `"\u0000"`, `"unterminated\`} {
		data := bytes.Replace(original, []byte(`"Retained unknown code"`), []byte(value), 1)
		reject(t, data)
	}
	for _, value := range []string{`"\ud83d\ude00"`, `"\u00e9"`, `"\\ud800"`, `"\n\t\r\"\\"`, `"é😀"`} {
		data := bytes.Replace(original, []byte(`"Retained unknown code"`), []byte(value), 1)
		m := accept(t, data)
		encoded, err := EncodeEnvoy(m)
		if err != nil {
			t.Fatal(err)
		}
		accept(t, encoded)
	}
	// Frame boundaries and JSON escapes are checked independently.
	for _, b := range [][]byte{[]byte("{\\\n"), []byte("{\\u1\n"), []byte("{\\uD800\n")} {
		reject(t, b)
	}
}

func TestExactFrameMaximum(t *testing.T) {
	f := object(t, corpus(t)[1])
	f["source"] = strings.Repeat("x", MaxSourceBytes)
	ordinary := frame(t, f)
	extra := MaxFrameBytes - len(ordinary)
	f["source"] = strings.Repeat("\t", extra) + strings.Repeat("x", MaxSourceBytes-extra)
	maximal := frame(t, f)
	if len(maximal) != MaxFrameBytes {
		t.Fatal(len(maximal))
	}
	m := accept(t, maximal)
	encoded, err := EncodeController(m)
	if err != nil || len(encoded) != MaxFrameBytes {
		t.Fatalf("maximum encoding: %d %v", len(encoded), err)
	}
	f["source"] = strings.Repeat("\t", extra+1) + strings.Repeat("x", MaxSourceBytes-extra-1)
	tooBig := frame(t, f)
	if len(tooBig) != MaxFrameBytes+1 {
		t.Fatal(len(tooBig))
	}
	reject(t, tooBig)
	m.(*Execute).Source = f["source"].(string)
	if output, err := EncodeController(m); err == nil || output != nil {
		t.Fatal("oversized encoding returned bytes")
	}
}

func TestInspectionShapesAndBounds(t *testing.T) {
	for _, idx := range []int{21, 22} {
		original := object(t, corpus(t)[idx])
		arrayName := "inspections"
		if idx == 22 {
			arrayName = "inspection_results"
		}
		entries := original[arrayName].([]any)
		for j, entry := range entries {
			e := entry.(map[string]any)
			for name := range e {
				t.Run(fmt.Sprintf("%d/%d/%s", idx, j, name), func(t *testing.T) {
					f := object(t, corpus(t)[idx])
					nested := f[arrayName].([]any)[j].(map[string]any)
					delete(nested, name)
					reject(t, frame(t, f))
					for _, bad := range []any{nil, 0, false, "", map[string]any{}, []any{}} {
						f = object(t, corpus(t)[idx])
						f[arrayName].([]any)[j].(map[string]any)[name] = bad
						reject(t, frame(t, f))
					}
					f = object(t, corpus(t)[idx])
					nested = f[arrayName].([]any)[j].(map[string]any)
					nested["unknown"] = "x"
					reject(t, frame(t, f))
					if oneOf(name, "inspection_id", "producer_id", "output_id") {
						for _, bad := range []string{"-a", "with space", strings.Repeat("a", 65)} {
							f = object(t, corpus(t)[idx])
							f[arrayName].([]any)[j].(map[string]any)[name] = bad
							reject(t, frame(t, f))
						}
						f = object(t, corpus(t)[idx])
						f[arrayName].([]any)[j].(map[string]any)[name] = strings.Repeat("a", 64)
						accept(t, frame(t, f))
					}
					if oneOf(name, "path", "resolved_path") {
						prefix := ""
						if name == "resolved_path" {
							prefix = "/"
						}
						f = object(t, corpus(t)[idx])
						f[arrayName].([]any)[j].(map[string]any)[name] = prefix + strings.Repeat("a", MaxPathBytes-len(prefix))
						accept(t, frame(t, f))
						for _, bad := range []string{strings.Repeat("a", MaxPathBytes+1), "a\x00b"} {
							f = object(t, corpus(t)[idx])
							f[arrayName].([]any)[j].(map[string]any)[name] = bad
							reject(t, frame(t, f))
						}
						if name == "resolved_path" {
							f = object(t, corpus(t)[idx])
							f[arrayName].([]any)[j].(map[string]any)[name] = "relative"
							reject(t, frame(t, f))
						}
					}
				})
			}
		}
		for _, count := range []int{0, 64, 65} {
			f := object(t, corpus(t)[idx])
			list := make([]any, 0, count)
			for j := 0; j < count; j++ {
				e := map[string]any{}
				for k, v := range entries[0].(map[string]any) {
					e[k] = v
				}
				e["inspection_id"] = fmt.Sprintf("inspection-%d", j)
				list = append(list, e)
			}
			f[arrayName] = list
			if count <= 64 {
				accept(t, frame(t, f))
			} else {
				reject(t, frame(t, f))
			}
		}
		f := object(t, corpus(t)[idx])
		f[arrayName].([]any)[1].(map[string]any)["inspection_id"] = "inspection-1"
		reject(t, frame(t, f))
		for _, forbidden := range []string{"producer_id", "output_id", "sha256", "digest_algorithm"} {
			f = object(t, corpus(t)[idx])
			f[arrayName].([]any)[0].(map[string]any)[forbidden] = "x"
			reject(t, frame(t, f))
		}
		for _, bad := range []any{nil, 1, []any{}} {
			f = object(t, corpus(t)[idx])
			f[arrayName] = []any{bad}
			reject(t, frame(t, f))
		}
	}
	f := object(t, corpus(t)[21])
	f["execution_shape"] = "pty"
	f["timing"] = "realtime"
	f["publication"] = "real"
	f["observation"] = "shared"
	reject(t, frame(t, f))
	f = object(t, corpus(t)[22])
	r := f["inspection_results"].([]any)[1].(map[string]any)
	for _, bad := range []string{"directory", "unknown", ""} {
		r["digest_algorithm"] = bad
		reject(t, frame(t, f))
	}
	r["digest_algorithm"] = "directory-v2"
	for _, bad := range []string{strings.Repeat("A", 64), strings.Repeat("g", 64), strings.Repeat("a", 63), strings.Repeat("a", 65)} {
		r["sha256"] = bad
		reject(t, frame(t, f))
	}
	r["sha256"] = strings.Repeat("f", 64)
	r["path_kind"] = "other"
	reject(t, frame(t, f))
	r["path_kind"] = "file"
	reject(t, frame(t, f))
	delete(r, "digest_algorithm")
	accept(t, frame(t, f))
	for _, kind := range []string{"file", "directory", "other"} {
		f = object(t, corpus(t)[22])
		f["inspection_results"].([]any)[0].(map[string]any)["path_kind"] = kind
		accept(t, frame(t, f))
	}
	// Individually valid maximum fields can exceed their enclosing frame limit.
	f = object(t, corpus(t)[21])
	f["source"] = strings.Repeat("x", MaxSourceBytes)
	list := make([]any, 64)
	for i := range list {
		list[i] = map[string]any{"inspection_id": fmt.Sprint("i", i), "kind": "file_exists", "path": strings.Repeat("x", MaxPathBytes)}
	}
	f["inspections"] = list
	reject(t, frame(t, f))
}

type customWireModel struct {
	Message
	frame []byte
}

func (m customWireModel) MarshalJSON() ([]byte, error) { return m.frame, nil }

func TestEncodePublicValuesAndRejectWrappers(t *testing.T) {
	for _, frame := range corpus(t)[:21] {
		m := accept(t, frame)
		encode := EncodeEnvoy
		if _, err := DecodeController(frame); err == nil {
			encode = EncodeController
		}
		value := reflect.ValueOf(m).Elem().Interface().(Message)
		if out, err := encode(value); err != nil || !bytes.Equal(out, frame) {
			t.Fatalf("value model %T: %s %v", value, out, err)
		}
		// Move seq ahead of schema while preserving otherwise valid JSON.
		seq := []byte(`"seq":1,`)
		reordered := bytes.Replace(frame[:len(frame)-1], seq, nil, 1)
		reordered = append(append([]byte{'{'}, seq...), reordered[1:]...)
		for _, wrapper := range []Message{customWireModel{m, reordered}, &customWireModel{m, reordered}} {
			if out, err := encode(wrapper); err == nil || out != nil {
				t.Fatalf("custom model accepted: %s %v", out, err)
			}
		}
	}
}

func TestEncodeRejectsInvalidModels(t *testing.T) {
	m := accept(t, corpus(t)[0]).(*Hello)
	m.SessionID = string([]byte{0xff})
	if out, err := EncodeController(m); err == nil || out != nil {
		t.Fatal("invalid UTF8 replaced")
	}
	if _, err := EncodeController(nil); err == nil {
		t.Fatal("nil model accepted")
	}
	var absent *Hello
	if _, err := EncodeController(absent); err == nil {
		t.Fatal("typed nil accepted")
	}
	cancel := accept(t, corpus(t)[3]).(*Cancel)
	cancel.Type = "finalize"
	if _, err := EncodeController(cancel); err == nil {
		t.Fatal("concrete model mismatch accepted")
	}
	ended := false
	m2 := accept(t, corpus(t)[13]).(*OperationCompleted)
	m2.ShellEnded = &ended
	if _, err := EncodeEnvoy(m2); err == nil {
		t.Fatal("false shell_ended accepted")
	}
	m3 := accept(t, corpus(t)[1]).(*Execute)
	m3.Inspections = nil
	if _, err := EncodeController(m3); err == nil {
		t.Fatal("null inspections accepted")
	}
	m3.Inspections = []Inspection{{InspectionID: "i", Kind: "file_exists", Path: "p", ProducerID: "extra"}}
	if _, err := EncodeController(m3); err == nil {
		t.Fatal("extra nested field accepted")
	}
	unsupported := struct {
		Hello
		Channel chan int `json:"channel"`
	}{Hello: Hello{Header: Header{Schema: Schema, Type: "hello", Seq: 1}, SessionID: strings.Repeat("0", 32)}, Channel: make(chan int)}
	if _, err := EncodeController(unsupported); err == nil {
		t.Fatal("unsupported JSON value accepted")
	}
	m4 := accept(t, corpus(t)[18]).(*Diagnostic)
	bad := string([]byte{0xff})
	m4.OperationID = &bad
	if _, err := EncodeEnvoy(m4); err == nil {
		t.Fatal("invalid pointer string accepted")
	}
	m5 := accept(t, corpus(t)[21]).(*Execute)
	m5.Inspections[0].Path = bad
	if _, err := EncodeController(m5); err == nil {
		t.Fatal("invalid nested string accepted")
	}
	m6 := accept(t, corpus(t)[2]).(*Continue)
	m6.InputThrough = math.MaxInt64
	out, err := EncodeController(m6)
	if err != nil || !bytes.Contains(out, []byte("9223372036854775807")) {
		t.Fatal("int64 precision lost")
	}
}

func FuzzPublicFrame(f *testing.F) {
	for _, b := range [][]byte{[]byte("{}\n"), []byte("[]\n"), []byte("{\"a\":1,\"a\":2}\n")} {
		f.Add(b)
	}
	f.Fuzz(func(t *testing.T, b []byte) {
		for _, controller := range []bool{true, false} {
			m, e := decode(b, controller)
			if e != nil {
				continue
			}
			out, e := encode(m, controller)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = decode(out, controller); e != nil {
				t.Fatal(e)
			}
		}
	})
}

func TestFrozenInvalidAndMaximumCorpus(t *testing.T) {
	invalid, err := os.ReadFile("../../../tests/fixtures/envoy-protocol-v1/public-invalid.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	for i, line := range bytes.Split(bytes.TrimSuffix(invalid, []byte{'\n'}), []byte{'\n'}) {
		t.Run(fmt.Sprint(i), func(t *testing.T) { reject(t, append(append([]byte(nil), line...), '\n')) })
	}
	maximum, err := os.ReadFile("../../../tests/fixtures/envoy-protocol-v1/public-maximum.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	if len(maximum) != MaxFrameBytes {
		t.Fatalf("frozen maximum has %d bytes", len(maximum))
	}
	m := accept(t, maximum)
	encoded, err := EncodeController(m)
	if err != nil || !bytes.Equal(encoded, maximum) {
		t.Fatal("maximum canonical bytes changed", err)
	}
}

func TestLiteralLineSeparators(t *testing.T) {
	for _, source := range []string{
		strings.Repeat("\u2028", MaxSourceBytes/3),
		strings.Repeat("\u2029", MaxSourceBytes/3),
		"literal \\u2028 and \\u2029; actual \u2028\u2029; backslashes \\\u2028 \\\\u2029",
	} {
		m := accept(t, corpus(t)[1]).(*Execute)
		m.Source = source
		out, err := EncodeController(m)
		if err != nil {
			t.Fatal("valid bounded Unicode source cannot be encoded", err)
		}
		decoded := accept(t, out).(*Execute)
		if decoded.Source != source {
			t.Fatal("line-separator conversion changed decoded string values")
		}
		if !bytes.Contains(out, []byte("\u2028")) && !bytes.Contains(out, []byte("\u2029")) {
			t.Fatal("line separators were escaped instead of literal UTF-8")
		}
		again, err := EncodeController(decoded)
		if err != nil || !bytes.Equal(out, again) {
			t.Fatal("Unicode frame is not stable through decode and encode", err)
		}
	}
}

func TestResolvedPathCanonicalSpelling(t *testing.T) {
	for _, p := range []string{"/", "/work/file", "/work/...", "/work/é", "/work/../secret", "/work/./file", "/work//file", "/work/file/", "//work/file"} {
		want := oneOf(p, "/", "/work/file", "/work/...", "/work/é")
		m := accept(t, corpus(t)[13]).(*OperationCompleted)
		m.InspectionResults = []InspectionResult{{InspectionID: "inspection-1", Kind: "file_exists", ResolvedPath: p, PathKind: "file"}}
		_, err := EncodeEnvoy(m)
		if (err == nil) != want {
			t.Fatalf("encode resolved path %q: %v", p, err)
		}
		f := object(t, corpus(t)[13])
		f["inspection_results"] = []any{map[string]any{"inspection_id": "inspection-1", "kind": "file_exists", "resolved_path": p, "path_kind": "file"}}
		_, err = DecodeEnvoy(frame(t, f))
		if (err == nil) != want {
			t.Fatalf("decode resolved path %q: %v", p, err)
		}
	}
	// Cwd is lexical shell state, not resolved artifact evidence.
	m := accept(t, corpus(t)[7]).(*Ready)
	m.Cwd = "/work/../work"
	if _, err := EncodeEnvoy(m); err != nil {
		t.Fatal("canonical evidence check narrowed lexical cwd", err)
	}
}
