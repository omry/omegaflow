package protocol

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"strings"
	"testing"
)

func ioFragments(data []byte, split int) io.Reader {
	return io.MultiReader(bytes.NewReader(data[:split]), bytes.NewReader(data[split:]))
}

type tinyReader struct{ io.Reader }

func (r tinyReader) Read(p []byte) (int, error) { return r.Reader.Read(p[:min(len(p), 1)]) }

func TestPrivateConcatenationAndEOF(t *testing.T) {
	for _, fields := range [][]string{{privatePrefix, "unknown"}, {privatePrefix, "shutdown"}} {
		if _, err := ReadPrivate(bufio.NewReader(bytes.NewReader(nulBytes(fields))), AwshToEnvoy); err == nil {
			t.Fatal("reader accepted unknown type or wrong direction")
		}
	}
	for _, d := range []PrivateDirection{EnvoyToAwsh, AwshToEnvoy} {
		var stream []byte
		var forms []nulFixture
		for _, f := range nulFixtures(t, "private") {
			if privateDirection(f) == d {
				stream = append(stream, nulBytes(f.Fields)...)
				forms = append(forms, f)
			}
		}
		for _, reader := range []io.Reader{bytes.NewReader(stream), tinyReader{bytes.NewReader(stream)}} {
			r := bufio.NewReaderSize(reader, 16)
			for _, f := range forms {
				m, err := ReadPrivate(r, d)
				if err != nil || m.privateType() != f.Fields[1] {
					t.Fatalf("%s: %v", f.ID, err)
				}
			}
			if _, err := ReadPrivate(r, d); err != io.EOF {
				t.Fatalf("between-frame EOF: %v", err)
			}
		}
		if _, err := DecodePrivate(stream, d); err == nil {
			t.Fatal("one-frame decoder accepted concatenation")
		}
	}
	for _, f := range nulFixtures(t, "private") {
		frame := nulBytes(f.Fields)
		for n := 0; n < len(frame); n++ {
			t.Run(fmt.Sprintf("%s/%d", f.ID, n), func(t *testing.T) {
				if _, err := DecodePrivate(frame[:n], privateDirection(f)); err == nil {
					t.Fatal("accepted truncated frame")
				}
				_, err := ReadPrivate(bufio.NewReader(bytes.NewReader(frame[:n])), privateDirection(f))
				if n == 0 {
					if err != io.EOF {
						t.Fatal(err)
					}
				} else if !errors.Is(err, io.ErrUnexpectedEOF) {
					t.Fatalf("partial frame: %v", err)
				}
			})
		}
	}
	good := nulBytes([]string{privatePrefix, "shutdown"})
	for _, frame := range [][]byte{append(append([]byte(nil), good...), 0), append(append([]byte(nil), good...), []byte("extra\x00")...), []byte("awsh-v1\x00shutdown\xff\x00"), nil} {
		if _, err := DecodePrivate(frame, EnvoyToAwsh); err == nil {
			t.Fatal("accepted malformed bytes")
		}
	}
	// An undeclared extra field is the next frame and cannot pass its prefix check.
	r := bufio.NewReader(bytes.NewReader(append(good, []byte("extra\x00")...)))
	if _, err := ReadPrivate(r, EnvoyToAwsh); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadPrivate(r, EnvoyToAwsh); err == nil {
		t.Fatal("accepted extra field as next frame")
	}
}

func TestHelperLengthAndEOF(t *testing.T) {
	frame := helperBytes([]string{helperPrefix, "prompt_ready"})
	for n := 0; n < len(frame); n++ {
		if _, err := DecodeHelper(frame[:n], HelperToAwsh, HelperStartup); err == nil {
			t.Fatalf("accepted prefix/payload truncation %d", n)
		}
		if _, err := ReadHelper(bytes.NewReader(frame[:n]), HelperToAwsh, HelperStartup); err == nil {
			t.Fatalf("reader accepted truncation %d", n)
		}
	}
	for _, n := range []uint32{0, 1, uint32(len(frame) - 5), uint32(len(frame) - 3), MaxFrameBytes + 1, 1<<32 - 1} {
		c := append([]byte(nil), frame...)
		binary.BigEndian.PutUint32(c, n)
		if _, err := DecodeHelper(c, HelperToAwsh, HelperStartup); err == nil {
			t.Fatalf("accepted length %d", n)
		}
		if _, err := ReadHelper(bytes.NewReader(c), HelperToAwsh, HelperStartup); err == nil {
			t.Fatalf("reader accepted length %d", n)
		}
	}
	for _, suffix := range [][]byte{{0}, {'x'}, frame} {
		c := append(append([]byte(nil), frame...), suffix...)
		if _, err := DecodeHelper(c, HelperToAwsh, HelperStartup); err == nil {
			t.Fatal("accepted helper trailing data")
		}
		if _, err := ReadHelper(bytes.NewReader(c), HelperToAwsh, HelperStartup); err == nil {
			t.Fatal("reader accepted trailing data")
		}
	}
	for _, payload := range [][]byte{[]byte("awsh-helper-v1\x00prompt_ready\xff\x00"), []byte("awsh-helper-v1\x00prompt_ready"), []byte("awsh-helper-v1\x00prompt_ready\x00\x00")} {
		c := make([]byte, 4)
		binary.BigEndian.PutUint32(c, uint32(len(payload)))
		c = append(c, payload...)
		if _, err := DecodeHelper(c, HelperToAwsh, HelperStartup); err == nil {
			t.Fatal("accepted malformed helper payload")
		}
	}
	if _, err := ReadHelper(tinyReader{bytes.NewReader(frame)}, HelperToAwsh, HelperStartup); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadHelper(io.MultiReader(bytes.NewReader(frame), errorReader{}), HelperToAwsh, HelperStartup); err == nil {
		t.Fatal("accepted transport error instead of EOF")
	}
}

type errorReader struct{}

func (errorReader) Read([]byte) (int, error) { return 0, errors.New("transport failed") }

func TestPrivateNestedLimits(t *testing.T) {
	m := privateOperation()
	m.Observation = "exclusive"
	for i := 0; i < MaxInspections; i++ {
		m.Inspections = append(m.Inspections, Inspection{InspectionID: fmt.Sprintf("i%d", i), Kind: "file_exists", Path: strings.Repeat("p", MaxPathBytes)})
	}
	if _, err := EncodePrivate(m, EnvoyToAwsh); err != nil {
		t.Fatal(err)
	}
	c := *m
	c.Inspections = append(append([]Inspection(nil), m.Inspections...), Inspection{InspectionID: "extra", Kind: "file_exists", Path: "x"})
	if _, err := EncodePrivate(c, EnvoyToAwsh); err == nil {
		t.Fatal("accepted 65 inspections")
	}
	c = *m
	c.Inspections = append([]Inspection(nil), m.Inspections...)
	c.Inspections[1].InspectionID = c.Inspections[0].InspectionID
	if _, err := EncodePrivate(c, EnvoyToAwsh); err == nil {
		t.Fatal("accepted duplicate IDs")
	}
	c = *m
	c.Inspections = append([]Inspection(nil), m.Inspections...)
	c.Inspections[0].Path += "p"
	if _, err := EncodePrivate(c, EnvoyToAwsh); err == nil {
		t.Fatal("accepted long path")
	}
	for _, entry := range []Inspection{{InspectionID: "i", Kind: "file_exists", Path: "x", ProducerID: "p"}, {InspectionID: "i", Kind: "produces", Path: "x"}, {InspectionID: "i", Kind: "produces", Path: "x", ProducerID: "p", OutputID: "!"}} {
		c := *m
		c.Inspections = []Inspection{entry}
		if _, err := EncodePrivate(c, EnvoyToAwsh); err == nil {
			t.Fatal("accepted invalid model inspection")
		}
	}
	completed := PrivateCompleted{OperationID: "op", Status: 0, PhysicalCWD: "/work", Inspections: []ResolvedInspection{{InspectionID: "i", Kind: "produces", ResolvedPath: "/work/file", ProducerID: "p", OutputID: "o"}}}
	if _, err := EncodePrivate(completed, AwshToEnvoy); err != nil {
		t.Fatal(err)
	}
	for _, entry := range []ResolvedInspection{{InspectionID: "i", Kind: "file_exists", ResolvedPath: "/x", OutputID: "o"}, {InspectionID: "i", Kind: "produces", ResolvedPath: "/x"}, {InspectionID: "i", Kind: "file_exists", ResolvedPath: "relative"}} {
		c := completed
		c.Inspections = []ResolvedInspection{entry}
		if _, err := EncodePrivate(c, AwshToEnvoy); err == nil {
			t.Fatal("accepted invalid resolved model")
		}
	}
}

func TestAggregatePrivateAndHelperBounds(t *testing.T) {
	// Bring a frame to its exact bound using valid individually bounded fields.
	m := privateOperation()
	m.Observation = "exclusive"
	for i := 0; i < 64; i++ {
		m.Inspections = append(m.Inspections, Inspection{InspectionID: fmt.Sprintf("i%d", i), Kind: "file_exists", Path: strings.Repeat("p", 4096)})
	}
	base, err := EncodePrivate(m, EnvoyToAwsh)
	if err != nil {
		t.Fatal(err)
	}
	m.Source = strings.Repeat("s", MaxFrameBytes-len(base)+len(m.Source))
	if len(m.Source) > MaxSourceBytes {
		t.Fatal("bad boundary fixture")
	}
	frame, err := EncodePrivate(m, EnvoyToAwsh)
	if err != nil || len(frame) != MaxFrameBytes {
		t.Fatalf("exact private bound: %d %v", len(frame), err)
	}
	if _, err := ReadPrivate(bufio.NewReaderSize(bytes.NewReader(frame), 16), EnvoyToAwsh); err != nil {
		t.Fatal(err)
	}
	m.Source += "s"
	if _, err := EncodePrivate(m, EnvoyToAwsh); err == nil {
		t.Fatal("encoder accepted over-limit aggregate")
	}
	bad := append(append([]byte(nil), frame[:len(frame)-1]...), 's', 0)
	if _, err := DecodePrivate(bad, EnvoyToAwsh); err == nil {
		t.Fatal("decoder accepted over-limit aggregate")
	}
	if _, err := ReadPrivate(bufio.NewReader(bytes.NewReader(bad)), EnvoyToAwsh); err == nil {
		t.Fatal("reader accepted over-limit aggregate")
	}
	// Oversized field lacking a NUL is bounded before EOF or a model allocation.
	if _, err := ReadPrivate(bufio.NewReader(strings.NewReader(strings.Repeat("a", MaxFrameBytes+1))), EnvoyToAwsh); err == nil {
		t.Fatal("reader accepted unbounded field")
	}
	h := HelperPromptState{PromptState{Status: 0, HistExpand: "off", EditingMode: "emacs", PhysicalCWD: "/work", ExportedEnv: map[string]string{"A": ""}}}
	hbase, err := EncodeHelper(h, HelperToAwsh, HelperStartup)
	if err != nil {
		t.Fatal(err)
	}
	h.ExportedEnv["A"] = strings.Repeat("e", MaxFrameBytes-(len(hbase)-4))
	hframe, err := EncodeHelper(h, HelperToAwsh, HelperStartup)
	if err != nil || len(hframe) != MaxFrameBytes+4 {
		t.Fatalf("exact helper bound: %d %v", len(hframe), err)
	}
	if _, err := ReadHelper(ioFragments(hframe, 3), HelperToAwsh, HelperStartup); err != nil {
		t.Fatal(err)
	}
	h.ExportedEnv["A"] += "e"
	if _, err := EncodeHelper(h, HelperToAwsh, HelperStartup); err == nil {
		t.Fatal("helper accepted over-limit payload")
	}
	// Declared enormous length fails before payload allocation or read.
	if _, err := ReadHelper(bytes.NewReader([]byte{255, 255, 255, 255}), HelperToAwsh, HelperStartup); err == nil {
		t.Fatal("accepted enormous declared length")
	}
}

func FuzzPrivateCodec(f *testing.F) {
	for _, row := range nulFixtures(f, "private") {
		f.Add(nulBytes(row.Fields), uint8(privateDirection(row)))
	}
	f.Fuzz(func(t *testing.T, data []byte, d uint8) {
		if len(data) > MaxFrameBytes+1 {
			return
		}
		m, err := DecodePrivate(data, PrivateDirection(d))
		if err != nil {
			return
		}
		out, err := EncodePrivate(m, PrivateDirection(d))
		if err != nil || !bytes.Equal(data, out) {
			t.Fatalf("round trip: %v", err)
		}
		r := bufio.NewReader(bytes.NewReader(data))
		parsed, err := ReadPrivate(r, PrivateDirection(d))
		if err != nil || parsed.privateType() != m.privateType() {
			t.Fatal(err)
		}
	})
}

func FuzzHelperCodec(f *testing.F) {
	for _, row := range nulFixtures(f, "helper") {
		f.Add(helperBytes(row.Fields), uint8(helperDirection(row)), uint8(helperPhase(row)))
	}
	f.Fuzz(func(t *testing.T, data []byte, d, p uint8) {
		if len(data) > MaxFrameBytes+5 {
			return
		}
		m, err := DecodeHelper(data, HelperDirection(d), HelperPhase(p))
		if err != nil {
			return
		}
		out, err := EncodeHelper(m, HelperDirection(d), HelperPhase(p))
		if err != nil || !bytes.Equal(data, out) {
			t.Fatalf("round trip: %v", err)
		}
		if _, err := ReadHelper(bytes.NewReader(data), HelperDirection(d), HelperPhase(p)); err != nil {
			t.Fatal(err)
		}
	})
}
