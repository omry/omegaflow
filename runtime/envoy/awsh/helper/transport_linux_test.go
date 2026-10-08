package helper

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"io"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"
)

func pair(t *testing.T) (*net.UnixConn, *net.UnixConn) {
	t.Helper()
	dir, e := os.MkdirTemp("", "awsh-")
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	addr := &net.UnixAddr{Name: filepath.Join(dir, "sock"), Net: "unix"}
	l, e := net.ListenUnix("unix", addr)
	if e != nil {
		t.Fatal(e)
	}
	defer l.Close()
	c, e := net.DialUnix("unix", nil, addr)
	if e != nil {
		t.Fatal(e)
	}
	s, e := l.AcceptUnix()
	if e != nil {
		c.Close()
		t.Fatal(e)
	}
	t.Cleanup(func() { c.Close(); s.Close() })
	for _, v := range []*net.UnixConn{c, s} {
		if e := v.SetDeadline(time.Now().Add(5 * time.Second)); e != nil {
			t.Fatal(e)
		}
	}
	return c, s
}

type frozenFrame struct {
	ID, File, Direction, Phase string
	Hex                        string `json:"frame_hex"`
}

func frozen(t *testing.T) []frozenFrame {
	t.Helper()
	b, e := os.ReadFile("../../../../tests/fixtures/envoy-protocol-v1/awsh-frames.json")
	if e != nil {
		t.Fatal(e)
	}
	var f []frozenFrame
	if e := json.Unmarshal(b, &f); e != nil {
		t.Fatal(e)
	}
	return f
}
func phase(s string) protocol.HelperPhase {
	return map[string]protocol.HelperPhase{"startup": protocol.HelperStartup, "completion": protocol.HelperCompletion, "source": protocol.HelperSource, "prepared": protocol.HelperStartPrepared, "gate": protocol.HelperGate}[s]
}
func raw(t *testing.T, f frozenFrame) []byte {
	t.Helper()
	b, e := hex.DecodeString(f.Hex)
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func send(c *net.UnixConn, parts ...[]byte) error {
	for _, b := range parts {
		if e := WriteAll(c, b); e != nil {
			return e
		}
	}
	return c.CloseWrite()
}
func ptr(m protocol.HelperMessage) protocol.HelperMessage {
	v := reflect.New(reflect.TypeOf(m))
	v.Elem().Set(reflect.ValueOf(m))
	return v.Interface().(protocol.HelperMessage)
}

func TestFrozenHelperSocketFormsAndFragments(t *testing.T) {
	count := 0
	for _, f := range frozen(t) {
		if f.File != "helper.jsonl" {
			continue
		}
		count++
		t.Run(f.ID, func(t *testing.T) {
			b := raw(t, f)
			d := protocol.HelperToAwsh
			if f.Direction == "awsh" {
				d = protocol.AwshToHelper
			}
			p := phase(f.Phase)
			want, e := protocol.DecodeHelper(b, d, p)
			if e != nil {
				t.Fatal(e)
			}
			for split := 0; split <= len(b); split++ {
				c, s := pair(t)
				done := make(chan error, 1)
				go func() { done <- send(c, b[:split], b[split:]) }()
				var got protocol.HelperMessage
				if d == protocol.HelperToAwsh {
					got, e = ReadRequest(s, p)
				} else {
					got, e = protocol.ReadHelper(socketReader{s}, d, p)
				}
				if e != nil || !reflect.DeepEqual(got, want) {
					t.Fatalf("split%d: %v %#v", split, e, got)
				}
				if e := <-done; e != nil {
					t.Fatal(e)
				}
				c.Close()
				s.Close()
			}
			c, s := pair(t)
			done := make(chan error, 1)
			if d == protocol.AwshToHelper {
				go func() { done <- Reply(c, p, want) }()
			} else {
				go func() { done <- send(c, b) }()
			}
			exact, e := io.ReadAll(s)
			if e != nil || !bytes.Equal(exact, b) {
				t.Fatalf("wire %x %v", exact, e)
			}
			if e := <-done; e != nil {
				t.Fatal(e)
			}
			c, s = pair(t)
			go send(c, b)
			if _, e := protocol.ReadHelper(socketReader{s}, 3-d, p); e == nil {
				t.Fatal("wrong direction accepted")
			}
		})
	}
	if count != 13 {
		t.Fatalf("inventory%d", count)
	}
}
func TestFrozenPrivateWrites(t *testing.T) {
	count := 0
	for _, f := range frozen(t) {
		if f.File != "private.jsonl" {
			continue
		}
		count++
		t.Run(f.ID, func(t *testing.T) {
			b := raw(t, f)
			r, w, e := os.Pipe()
			if e != nil {
				t.Fatal(e)
			}
			defer r.Close()
			defer w.Close()
			done := make(chan error, 1)
			go func() { done <- WriteAll(w, b); w.Close() }()
			got, e := io.ReadAll(r)
			if e != nil || !bytes.Equal(got, b) {
				t.Fatalf("wire %x %v", got, e)
			}
			if e := <-done; e != nil {
				t.Fatal(e)
			}
			d := protocol.EnvoyToAwsh
			if f.Direction == "awsh" {
				d = protocol.AwshToEnvoy
			}
			if _, e := protocol.ReadPrivate(bufio.NewReader(bytes.NewReader(got)), d); e != nil {
				t.Fatal(e)
			}
		})
	}
	if count != 22 {
		t.Fatalf("inventory%d", count)
	}
}

func TestExchangeHalfCloseAndReplyEOF(t *testing.T) {
	c, s := pair(t)
	done := make(chan error, 1)
	go func() {
		got, e := Exchange(c, protocol.HelperGate, protocol.HelperGateRequest{GateID: "gate"})
		if e == nil && !reflect.DeepEqual(got, &protocol.HelperGateAccepted{GateID: "gate"}) {
			e = fmt.Errorf("reply %#v", got)
		}
		done <- e
	}()
	got, e := ReadRequest(s, protocol.HelperGate)
	if e != nil || !reflect.DeepEqual(got, &protocol.HelperGateRequest{GateID: "gate"}) {
		t.Fatalf("request %#v %v", got, e)
	}
	b, _ := protocol.EncodeHelper(protocol.HelperGateAccepted{GateID: "gate"}, protocol.AwshToHelper, protocol.HelperGate)
	if e := WriteAll(s, b); e != nil {
		t.Fatal(e)
	}
	select {
	case e := <-done:
		t.Fatalf("accepted without EOF %v", e)
	case <-time.After(20 * time.Millisecond):
	}
	s.Close()
	if e := <-done; e != nil {
		t.Fatal(e)
	}
}
func TestRequestNeedsHalfClose(t *testing.T) {
	c, s := pair(t)
	b, _ := protocol.EncodeHelper(protocol.HelperStartupReady{}, protocol.HelperToAwsh, protocol.HelperStartup)
	done := make(chan error, 1)
	go func() { _, e := ReadRequest(s, protocol.HelperStartup); done <- e }()
	if e := WriteAll(c, b); e != nil {
		t.Fatal(e)
	}
	select {
	case e := <-done:
		t.Fatalf("accepted without half-close %v", e)
	case <-time.After(20 * time.Millisecond):
	}
	c.CloseWrite()
	if e := <-done; e != nil {
		t.Fatal(e)
	}
	if e := Reply(s, protocol.HelperStartup, protocol.HelperAccepted{}); e != nil {
		t.Fatal(e)
	}
	if _, e := protocol.ReadHelper(socketReader{c}, protocol.AwshToHelper, protocol.HelperStartup); e != nil {
		t.Fatal(e)
	}
}
func helperFrame(p []byte) []byte {
	b := make([]byte, 4)
	binary.BigEndian.PutUint32(b, uint32(len(p)))
	return append(b, p...)
}
func TestMalformedHelperSocket(t *testing.T) {
	valid, _ := protocol.EncodeHelper(protocol.HelperStartupReady{}, protocol.HelperToAwsh, protocol.HelperStartup)
	cases := map[string][]byte{"zero": {0, 0, 0, 0}, "oversize": {0, 16, 0, 1}, "trailing": append(append([]byte{}, valid...), 1), "concatenated": append(append([]byte{}, valid...), valid...), "utf8": helperFrame([]byte("awsh-helper-v1\x00prompt_ready\x00\xff\x00")), "arity": helperFrame([]byte("awsh-helper-v1\x00prompt_ready\x00extra\x00")), "phase": helperFrame([]byte("awsh-helper-v1\x00gate\x00gate\x00")), "json": helperFrame([]byte("awsh-helper-v1\x00prompt_state\x000\x00off\x00emacs\x00/work\x00\x00{\"X\":1}\x00"))}
	for n := 0; n < len(valid); n++ {
		cases[fmt.Sprint("short", n)] = valid[:n]
	}
	for name, b := range cases {
		t.Run(name, func(t *testing.T) {
			c, s := pair(t)
			go send(c, b)
			if _, e := ReadRequest(s, protocol.HelperStartup); e == nil {
				t.Fatal("malformed accepted")
			}
		})
	}
}
func TestMalformedReplyAndInvalidOutgoingClose(t *testing.T) {
	for _, b := range [][]byte{helperFrame([]byte("awsh-helper-v1\x00accepted\x00gate\x00")), {0, 0, 0, 0}, {0, 0, 0}, helperFrame([]byte("awsh-helper-v1\x00accepted\x00extra\x00"))} {
		c, s := pair(t)
		go func() {
			if _, e := ReadRequest(s, protocol.HelperStartup); e == nil {
				send(s, b)
			}
		}()
		if _, e := Exchange(c, protocol.HelperStartup, protocol.HelperStartupReady{}); e == nil {
			t.Fatal("malformed reply accepted")
		}
	}
	c, s := pair(t)
	if _, e := Exchange(c, protocol.HelperStartup, protocol.HelperGateRequest{GateID: "gate"}); e == nil {
		t.Fatal("invalid outgoing accepted")
	}
	if _, e := s.Read(make([]byte, 1)); e != io.EOF {
		t.Fatal("not closed", e)
	}
	c, s = pair(t)
	if e := Reply(c, protocol.HelperStartup, protocol.HelperGateAccepted{GateID: "gate"}); e == nil {
		t.Fatal("invalid reply accepted")
	}
	if _, e := s.Read(make([]byte, 1)); e != io.EOF {
		t.Fatal("not closed", e)
	}
	if _, e := Exchange(nil, protocol.HelperStartup, protocol.HelperStartupReady{}); e == nil {
		t.Fatal("nil accepted")
	}
	if _, e := ReadRequest(nil, protocol.HelperStartup); e == nil {
		t.Fatal("nil accepted")
	}
	if e := Reply(nil, protocol.HelperStartup, protocol.HelperAccepted{}); e == nil {
		t.Fatal("nil accepted")
	}
}

func TestMaximumPayloadsSmallSocketBuffers(t *testing.T) {
	state := protocol.HelperPromptState{PromptState: protocol.PromptState{HistExpand: "off", EditingMode: "emacs", PhysicalCWD: "/work", ExportedEnv: map[string]string{"X": ""}}}
	b, e := protocol.EncodeHelper(state, protocol.HelperToAwsh, protocol.HelperStartup)
	if e != nil {
		t.Fatal(e)
	}
	state.ExportedEnv["X"] = strings.Repeat("x", protocol.MaxFrameBytes-(len(b)-4))
	b, e = protocol.EncodeHelper(state, protocol.HelperToAwsh, protocol.HelperStartup)
	if e != nil || len(b) != protocol.MaxFrameBytes+4 {
		t.Fatalf("maximum%d %v", len(b), e)
	}
	reply := protocol.HelperSourceReply{OperationID: "op", HistExpand: "off", EditingMode: "emacs", ExecutionShape: "pty", Source: strings.Repeat("x", protocol.MaxSourceBytes)}
	for _, tc := range []struct {
		p          protocol.HelperPhase
		req, reply protocol.HelperMessage
	}{{protocol.HelperStartup, state, protocol.HelperAccepted{}}, {protocol.HelperSource, protocol.HelperSourceRequest{}, reply}} {
		c, s := pair(t)
		for _, v := range []*net.UnixConn{c, s} {
			if e := v.SetWriteBuffer(1024); e != nil {
				t.Fatal(e)
			}
			if e := v.SetReadBuffer(1024); e != nil {
				t.Fatal(e)
			}
		}
		done := make(chan error, 1)
		go func() {
			got, e := ReadRequest(s, tc.p)
			if e == nil && !reflect.DeepEqual(got, ptr(tc.req)) {
				e = fmt.Errorf("request mismatch")
			}
			if e == nil {
				e = Reply(s, tc.p, tc.reply)
			}
			done <- e
		}()
		got, e := Exchange(c, tc.p, tc.req)
		if e != nil || !reflect.DeepEqual(got, ptr(tc.reply)) {
			t.Fatalf("reply mismatch %v", e)
		}
		if e := <-done; e != nil {
			t.Fatal(e)
		}
	}
	state.ExportedEnv["X"] += "x"
	if _, e := protocol.EncodeHelper(state, protocol.HelperToAwsh, protocol.HelperStartup); e == nil {
		t.Fatal("request overflow")
	}
	reply.Source += "x"
	if _, e := protocol.EncodeHelper(reply, protocol.AwshToHelper, protocol.HelperSource); e == nil {
		t.Fatal("source overflow")
	}
}
func TestOriginalDeadlineNotExtended(t *testing.T) {
	for _, mode := range []string{"prefix", "payload", "request-eof", "reply-eof"} {
		t.Run(mode, func(t *testing.T) {
			c, s := pair(t)
			deadline := time.Now().Add(100 * time.Millisecond)
			s.SetDeadline(deadline)
			b, _ := protocol.EncodeHelper(protocol.HelperStartupReady{}, protocol.HelperToAwsh, protocol.HelperStartup)
			d := protocol.HelperToAwsh
			if mode == "reply-eof" {
				b, _ = protocol.EncodeHelper(protocol.HelperAccepted{}, protocol.AwshToHelper, protocol.HelperStartup)
				d = protocol.AwshToHelper
			}
			done := make(chan struct{})
			defer close(done)
			go func() {
				if mode == "prefix" {
					WriteAll(c, b[:1])
					return
				}
				if mode == "payload" {
					b = b[:len(b)-1]
				}
				for _, v := range b {
					select {
					case <-done:
						return
					case <-time.After(time.Millisecond):
					}
					if e := WriteAll(c, []byte{v}); e != nil {
						return
					}
				}
			}()
			if _, e := protocol.ReadHelper(socketReader{s}, d, protocol.HelperStartup); e == nil {
				t.Fatal("stall accepted")
			}
			elapsed := time.Since(deadline)
			if elapsed > 150*time.Millisecond || elapsed < -20*time.Millisecond {
				t.Fatalf("deadline changed %v", elapsed)
			}
		})
	}
	c, s := pair(t)
	c.SetWriteBuffer(1024)
	c.SetWriteDeadline(time.Now().Add(50 * time.Millisecond))
	if e := WriteAll(c, make([]byte, protocol.MaxFrameBytes)); e == nil {
		t.Fatal("blocked write accepted")
	}
	s.Close()
}

type shortWriter struct {
	bytes.Buffer
	n   int
	err error
}

func (w *shortWriter) Write(p []byte) (int, error) {
	if len(p) > w.n {
		p = p[:w.n]
	}
	n, _ := w.Buffer.Write(p)
	return n, w.err
}
func TestExactWriteLoop(t *testing.T) {
	w := &shortWriter{n: 1}
	b := []byte("fragmented control")
	if e := WriteAll(w, b); e != nil || !bytes.Equal(w.Bytes(), b) {
		t.Fatal(e)
	}
	if e := WriteAll(&shortWriter{n: 0}, b); !errors.Is(e, io.ErrNoProgress) {
		t.Fatal(e)
	}
	failure := errors.New("terminal error")
	if e := WriteAll(&shortWriter{n: 1, err: failure}, b); !errors.Is(e, failure) {
		t.Fatal(e)
	}
}

func TestAncillaryRightsRejectedWithoutLeak(t *testing.T) {
	frame, _ := protocol.EncodeHelper(protocol.HelperStartupReady{}, protocol.HelperToAwsh, protocol.HelperStartup)
	for _, offset := range []int{0, 4, len(frame)} {
		t.Run(fmt.Sprint(offset), func(t *testing.T) {
			c, s := pair(t)
			f, e := os.Open("/dev/null")
			if e != nil {
				t.Fatal(e)
			}
			defer f.Close()
			before, e := os.ReadDir("/proc/self/fd")
			if e != nil {
				t.Fatal(e)
			}
			done := make(chan error, 1)
			go func() {
				if e := WriteAll(c, frame[:offset]); e != nil {
					done <- e
					return
				}
				suffix := frame[offset:]
				if len(suffix) == 0 {
					suffix = []byte{0}
				}
				n, _, e := c.WriteMsgUnix(suffix[:1], syscall.UnixRights(int(f.Fd())), nil)
				if e == nil && n != 1 {
					e = io.ErrShortWrite
				}
				if e == nil {
					e = WriteAll(c, suffix[1:])
				}
				c.CloseWrite()
				done <- e
			}()
			if _, e := ReadRequest(s, protocol.HelperStartup); e == nil {
				t.Fatal("rights accepted")
			}
			<-done
			c.Close()
			after, e := os.ReadDir("/proc/self/fd")
			if e != nil {
				t.Fatal(e)
			}
			if len(after) > len(before)-2 {
				t.Fatalf("descriptor leaked before%d after%d", len(before), len(after))
			}
		})
	}
}
func TestCredentialsAndTruncationRejected(t *testing.T) {
	c, s := pair(t)
	rc, e := s.SyscallConn()
	if e != nil {
		t.Fatal(e)
	}
	var se error
	if e := rc.Control(func(fd uintptr) { se = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_PASSCRED, 1) }); e != nil || se != nil {
		t.Fatalf("passcred %v %v", e, se)
	}
	b, _ := protocol.EncodeHelper(protocol.HelperStartupReady{}, protocol.HelperToAwsh, protocol.HelperStartup)
	go send(c, b)
	if _, e := ReadRequest(s, protocol.HelperStartup); e == nil {
		t.Fatal("credentials accepted")
	}
	for _, flag := range []int{syscall.MSG_CTRUNC, syscall.MSG_TRUNC} {
		if !rejectControl(nil, flag) {
			t.Fatal("truncation accepted")
		}
	}
	fd, e := syscall.Open("/dev/null", syscall.O_RDONLY, 0)
	if e != nil {
		t.Fatal(e)
	}
	if !rejectControl(syscall.UnixRights(fd), syscall.MSG_CTRUNC) {
		t.Fatal("rights accepted")
	}
	var stat syscall.Stat_t
	if e := syscall.Fstat(fd, &stat); e != syscall.EBADF {
		syscall.Close(fd)
		t.Fatal("rights not closed", e)
	}
	if !rejectControl([]byte{1}, 0) || rejectControl(nil, 0) {
		t.Fatal("control validation")
	}
}
func TestRejectNonStream(t *testing.T) {
	dir := t.TempDir()
	s, e := net.ListenUnixgram("unixgram", &net.UnixAddr{Net: "unixgram", Name: filepath.Join(dir, "sock")})
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if _, e := ReadRequest(s, protocol.HelperStartup); e == nil {
		t.Fatal("datagram accepted")
	}
}

// Synthetic inherited-ignore proof; selected-Bash direct-exec identity is B2.7.
func TestInheritedINTIgnore(t *testing.T) {
	switch os.Getenv("AWSH_TEST_EXEC") {
	case "launcher":
		signal.Ignore(syscall.SIGINT)
		os.Setenv("AWSH_TEST_EXEC", "client")
		env := os.Environ()
		if e := syscall.Exec(os.Args[0], []string{os.Args[0], "-test.run=^TestInheritedINTIgnore$"}, env); e != nil {
			os.Exit(3)
		}
	case "client":
		c, e := net.DialUnix("unix", nil, &net.UnixAddr{Net: "unix", Name: os.Getenv("AWSH_TEST_SOCKET")})
		if e != nil {
			os.Exit(4)
		}
		_, e = Exchange(c, protocol.HelperCompletion, protocol.HelperCompletionReady{PromptState: protocol.PromptState{HistExpand: "off", EditingMode: "emacs", PhysicalCWD: "/work", ExportedEnv: map[string]string{}}})
		if e != nil {
			os.Exit(5)
		}
		os.Exit(0)
	}
	dir, e := os.MkdirTemp("", "awsh-int-")
	if e != nil {
		t.Fatal(e)
	}
	defer os.RemoveAll(dir)
	path := filepath.Join(dir, "sock")
	l, e := net.ListenUnix("unix", &net.UnixAddr{Net: "unix", Name: path})
	if e != nil {
		t.Fatal(e)
	}
	defer l.Close()
	l.SetDeadline(time.Now().Add(5 * time.Second))
	cmd := exec.Command(os.Args[0], "-test.run=^TestInheritedINTIgnore$")
	cmd.Env = append(os.Environ(), "AWSH_TEST_EXEC=launcher", "AWSH_TEST_SOCKET="+path)
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	if e := cmd.Start(); e != nil {
		t.Fatal(e)
	}
	defer func() { cmd.Process.Kill(); cmd.Wait() }()
	s, e := l.AcceptUnix()
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	s.SetDeadline(time.Now().Add(5 * time.Second))
	if _, e := ReadRequest(s, protocol.HelperCompletion); e != nil {
		t.Fatal(e)
	}
	if e := cmd.Process.Signal(syscall.SIGINT); e != nil {
		t.Fatal(e)
	}
	time.Sleep(20 * time.Millisecond)
	if e := cmd.Process.Signal(syscall.Signal(0)); e != nil {
		t.Fatal("helper died under ignored INT")
	}
	if e := Reply(s, protocol.HelperCompletion, protocol.HelperAccepted{}); e != nil {
		t.Fatal(e)
	}
	if e := cmd.Wait(); e != nil {
		t.Fatalf("exec helper %v %s", e, output.Bytes())
	}
	if output.Len() != 0 {
		t.Fatalf("helper output %q", output.Bytes())
	}
}
