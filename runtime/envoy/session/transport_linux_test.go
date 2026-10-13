package session

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const testID = "0123456789abcdef0123456789abcdef"

func listeners(t *testing.T) *Listeners {
	t.Helper()
	l, err := Listen("127.0.0.1:0", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(l.Close)
	return l
}
func dial(t *testing.T, a string) *net.TCPConn {
	t.Helper()
	addr, err := net.ResolveTCPAddr("tcp", a)
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTCP("tcp", nil, addr)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	return c
}
func hello(t *testing.T, id string, seq int64) []byte {
	t.Helper()
	b, err := protocol.EncodeController(protocol.Hello{Header: protocol.Header{Schema: protocol.Schema, Type: "hello", Seq: seq}, SessionID: id})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

type handshakeResult struct {
	s      *Channels
	ctx    context.Context
	cancel context.CancelFunc
	err    error
}

func begin(l *Listeners, ctx context.Context, id string) <-chan handshakeResult {
	done := make(chan handshakeResult, 1)
	go func() { s, c, x, e := l.Handshake(ctx, id); done <- handshakeResult{s, c, x, e} }()
	return done
}
func receive(t *testing.T, done <-chan handshakeResult) handshakeResult {
	t.Helper()
	select {
	case r := <-done:
		if r.s != nil {
			t.Cleanup(r.s.Close)
			t.Cleanup(r.cancel)
		}
		return r
	case <-time.After(2 * time.Second):
		t.Fatal("handshake retained worker")
		return handshakeResult{}
	}
}
func established(t *testing.T) (*Channels, *net.TCPConn, *net.TCPConn, context.Context) {
	t.Helper()
	l := listeners(t)
	a, b := l.Addresses()
	done := begin(l, context.Background(), testID)
	terminal := dial(t, a)
	telemetry := dial(t, b)
	telemetry.Write(hello(t, testID, 1))
	r := receive(t, done)
	if r.err != nil {
		t.Fatal(r.err)
	}
	return r.s, terminal, telemetry, r.ctx
}
func fatal(t *testing.T, s *Channels) {
	t.Helper()
	select {
	case <-s.Done():
		if s.Err() == nil {
			t.Fatal("missing cause")
		}
	case <-time.After(time.Second):
		t.Fatal("failure not closed")
	}
}
func TestBothChannelsAndFragmentedHelloBeforeLaunch(t *testing.T) {
	l := listeners(t)
	a, b := l.Addresses()
	done := begin(l, context.Background(), testID)
	terminal := dial(t, a)
	select {
	case <-done:
		t.Fatal("launch before both channels")
	case <-time.After(20 * time.Millisecond):
	}
	telemetry := dial(t, b)
	frame := hello(t, testID, 1)
	telemetry.Write(frame[:len(frame)-1])
	select {
	case <-done:
		t.Fatal("launch before complete hello")
	case <-time.After(20 * time.Millisecond):
	}
	validated := time.Now()
	telemetry.Write(frame[len(frame)-1:])
	r := receive(t, done)
	if r.err != nil {
		t.Fatal(r.err)
	}
	deadline, ok := r.ctx.Deadline()
	if !ok || deadline.Before(validated.Add(10*time.Second)) || deadline.After(time.Now().Add(10*time.Second)) {
		t.Fatal("wrong launch origin")
	}
	for _, addr := range []string{a, b} {
		c, err := net.DialTimeout("tcp", addr, 100*time.Millisecond)
		if err == nil {
			c.Close()
			t.Fatal("listener survived handshake")
		}
	}
	ready := protocol.Ready{Header: protocol.Header{Schema: protocol.Schema, Type: "ready", Seq: 1}, EnvoyPID: 1, ShellPID: 2, Cwd: "/", Columns: 80, Rows: 24, ElapsedUS: 0, OutputThrough: 3}
	if err := r.s.Send(r.ctx, ready); err != nil {
		t.Fatal(err)
	}
	f, err := bufio.NewReader(telemetry).ReadBytes('\n')
	if err != nil {
		t.Fatal(err)
	}
	if _, err := protocol.DecodeEnvoy(f); err != nil {
		t.Fatal(err)
	}
	r.s.Terminal().Write([]byte("raw"))
	raw := make([]byte, 3)
	if _, err := io.ReadFull(terminal, raw); err != nil || !bytes.Equal(raw, []byte("raw")) {
		t.Fatal("startup bytes")
	}
	if err := r.s.CommitReadiness(r.ctx, func(context.Context) error { return nil }); err != nil {
		t.Fatal(err)
	}
	request, _ := protocol.EncodeController(protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: 2}, Columns: 90, Rows: 30})
	telemetry.Write(request)
	select {
	case e := <-r.s.Events():
		if _, ok := e.Message.(*protocol.Resize); !ok {
			t.Fatal("wrong event")
		}
	case <-time.After(time.Second):
		t.Fatal("missing request")
	}
	terminal.Write([]byte{0, 255, 10})
	select {
	case e := <-r.s.Events():
		if !bytes.Equal(e.Input, []byte{0, 255, 10}) {
			t.Fatal("binary input changed")
		}
	case <-time.After(time.Second):
		t.Fatal("missing input")
	}
	if err := r.s.CommitReadiness(r.ctx, func(context.Context) error { return nil }); err == nil {
		t.Fatal("duplicate readiness")
	}
}
func TestHelloStrictFailures(t *testing.T) {
	for name, frame := range map[string][]byte{"wrong-id": hello(t, strings.Repeat("a", 32), 1), "wrong-seq": hello(t, testID, 2), "bad-json": []byte("{}\n"), "oversize": bytes.Repeat([]byte("x"), protocol.MaxFrameBytes+1), "truncated": hello(t, testID, 1)[:20], "pipelined": append(hello(t, testID, 1), 'x')} {
		t.Run(name, func(t *testing.T) {
			l := listeners(t)
			a, b := l.Addresses()
			done := begin(l, context.Background(), testID)
			dial(t, a)
			c := dial(t, b)
			c.Write(frame)
			if name == "truncated" {
				c.CloseWrite()
			}
			if r := receive(t, done); r.err == nil {
				t.Fatal("invalid hello accepted")
			}
		})
	}
	for _, id := range []string{"", strings.ToUpper(testID), strings.Repeat("a", 31)} {
		l := listeners(t)
		if _, _, _, err := l.Handshake(context.Background(), id); err == nil {
			t.Fatal("invalid trusted ID")
		}
	}
}
func TestEarlyTerminalFailures(t *testing.T) {
	for _, kind := range []string{"early-data", "terminal-eof"} {
		t.Run(kind, func(t *testing.T) {
			l := listeners(t)
			a, b := l.Addresses()
			terminal := dial(t, a)
			if kind == "early-data" {
				terminal.Write([]byte("x"))
			}
			if kind == "terminal-eof" {
				terminal.CloseWrite()
			}
			telemetry := dial(t, b)
			telemetry.Write(hello(t, testID, 1))
			r := receive(t, begin(l, context.Background(), testID))
			if r.err == nil {
				fatal(t, r.s)
			}
		})
	}
}
func TestIndependentAcceptAndHelloEpochs(t *testing.T) {
	t.Run("accept", func(t *testing.T) {
		l := listeners(t)
		l.bound = time.Now().Add(-handshakeBudget + 90*time.Millisecond)
		a, _ := l.Addresses()
		done := begin(l, context.Background(), testID)
		dial(t, a)
		started := time.Now()
		r := receive(t, done)
		if r.err == nil || !strings.Contains(r.err.Error(), "accept phase") || time.Since(started) > time.Second {
			t.Fatal("accept epoch restarted", r.err)
		}
	})
	t.Run("hello", func(t *testing.T) {
		l := listeners(t)
		l.bound = time.Now().Add(-handshakeBudget + 80*time.Millisecond)
		a, b := l.Addresses()
		ctx, cancel := context.WithTimeout(context.Background(), 180*time.Millisecond)
		defer cancel()
		done := begin(l, ctx, testID)
		dial(t, a)
		c := dial(t, b)
		c.Write([]byte("{"))
		time.Sleep(100 * time.Millisecond)
		select {
		case r := <-done:
			t.Fatalf("hello inherited accept deadline: %v", r.err)
		default:
		}
		r := receive(t, done)
		if r.err == nil {
			t.Fatal("unterminated hello passed")
		}
		if !errors.Is(r.err, context.DeadlineExceeded) {
			t.Fatalf("hello deadline cause lost: %v", r.err)
		}
	})
	t.Run("cancel-hello", func(t *testing.T) {
		l := listeners(t)
		a, b := l.Addresses()
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		done := begin(l, ctx, testID)
		dial(t, a)
		c := dial(t, b)
		writeTestPeer(t, c, []byte("{"))
		time.Sleep(20 * time.Millisecond)
		cancel()
		r := receive(t, done)
		if !errors.Is(r.err, context.Canceled) {
			t.Fatalf("hello cancellation cause lost: %v", r.err)
		}
		var transport *net.OpError
		if !errors.As(r.err, &transport) {
			t.Fatalf("hello transport cause lost: %v", r.err)
		}
	})
	t.Run("cancel-partial-accept", func(t *testing.T) {
		l := listeners(t)
		a, _ := l.Addresses()
		ctx, cancel := context.WithCancel(context.Background())
		done := begin(l, ctx, testID)
		c := dial(t, a)
		cancel()
		r := receive(t, done)
		if r.err == nil {
			t.Fatal("cancellation passed")
		}
		c.SetReadDeadline(time.Now().Add(time.Second))
		if _, err := c.Read(make([]byte, 1)); err == nil {
			t.Fatal("socket retained")
		}
	})
}
func TestPostHandshakeFailuresAndSequences(t *testing.T) {
	for _, kind := range []string{"early-control", "early-input", "terminal-eof", "telemetry-eof", "sequence", "repeat-hello", "send-sequence"} {
		t.Run(kind, func(t *testing.T) {
			s, terminal, telemetry, ctx := established(t)
			switch kind {
			case "early-control":
				telemetry.Write([]byte("{"))
			case "early-input":
				terminal.Write([]byte("x"))
			case "terminal-eof":
				terminal.CloseWrite()
			case "telemetry-eof":
				telemetry.CloseWrite()
			case "sequence", "repeat-hello":
				s.CommitReadiness(ctx, func(context.Context) error { return nil })
				frame := hello(t, testID, 2)
				if kind == "sequence" {
					frame, _ = protocol.EncodeController(protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: 3}, Columns: 1, Rows: 1})
				}
				telemetry.Write(frame)
			case "send-sequence":
				if err := s.Send(ctx, protocol.Diagnostic{Header: protocol.Header{Schema: protocol.Schema, Type: "diagnostic", Seq: 2}, Severity: "fatal", Code: "test", Message: "test"}); err == nil {
					t.Fatal("outbound sequence accepted")
				}
			}
			fatal(t, s)
		})
	}
}
func TestBindFailureAndCallerCancellation(t *testing.T) {
	occupied, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer occupied.Close()
	if l, err := Listen("127.0.0.1:0", occupied.Addr().String()); err == nil {
		l.Close()
		t.Fatal("partial bind accepted")
	}
	l := listeners(t)
	a, b := l.Addresses()
	ctx, cancel := context.WithCancel(context.Background())
	done := begin(l, ctx, testID)
	dial(t, a)
	c := dial(t, b)
	c.Write(hello(t, testID, 1))
	r := receive(t, done)
	if r.err != nil {
		t.Fatal(r.err)
	}
	cancel()
	fatal(t, r.s)
}

func TestHelloProgressDoesNotResetTenSecondEpoch(t *testing.T) {
	l := listeners(t)
	a, b := l.Addresses()
	done := begin(l, context.Background(), testID)
	dial(t, a)
	c := dial(t, b)
	start := time.Now()
	c.Write([]byte("{"))
	time.Sleep(4 * time.Second)
	c.Write([]byte("\"schema\":"))
	select {
	case r := <-done:
		if r.err == nil {
			t.Fatal("partial hello accepted")
		}
		if elapsed := time.Since(start); elapsed < 9*time.Second || elapsed > 12*time.Second {
			t.Fatal("hello epoch reset", elapsed)
		}
	case <-time.After(8 * time.Second):
		t.Fatal("hello progress reset deadline")
	}
}

func TestReadLineBoundaryAndCoalescedFrames(t *testing.T) {
	first := append(bytes.Repeat([]byte("x"), protocol.MaxFrameBytes-1), '\n')
	r := bufio.NewReader(bytes.NewReader(append(first, []byte("next\n")...)))
	frame, err := readLine(r)
	if err != nil || !bytes.Equal(frame, first) {
		t.Fatal("exact size boundary", err)
	}
	frame, err = readLine(r)
	if err != nil || string(frame) != "next\n" {
		t.Fatal("coalesced suffix lost", err)
	}
	_, err = readLine(bufio.NewReader(bytes.NewReader(bytes.Repeat([]byte("x"), protocol.MaxFrameBytes))))
	if err == nil {
		t.Fatal("maximum nonterminated frame accepted")
	}
}

func TestPartialBindReleasesFirstListener(t *testing.T) {
	probe, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	first := probe.Addr().String()
	probe.Close()
	occupied, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer occupied.Close()
	if _, err = Listen(first, occupied.Addr().String()); err == nil {
		t.Fatal("conflicting second bind accepted")
	}
	rebound, err := net.Listen("tcp", first)
	if err != nil {
		t.Fatal("first bind leaked", err)
	}
	rebound.Close()
}

func TestConformingImmediateReadyResponse(t *testing.T) {
	for _, channel := range []string{"terminal", "telemetry"} {
		t.Run(channel, func(t *testing.T) {
			s, terminal, telemetry, ctx := established(t)
			observed := make(chan error, 1)
			go func() {
				frame, e := bufio.NewReader(telemetry).ReadBytes('\n')
				if e == nil {
					_, e = protocol.DecodeEnvoy(frame)
				}
				if e == nil {
					_, e = io.ReadFull(terminal, make([]byte, 3))
				}
				if e == nil {
					if channel == "terminal" {
						_, e = terminal.Write([]byte("valid"))
					} else {
						frame, e = protocol.EncodeController(protocol.Resize{Header: protocol.Header{Schema: protocol.Schema, Type: "resize", Seq: 2}, Columns: 80, Rows: 24})
						if e == nil {
							_, e = telemetry.Write(frame)
						}
					}
				}
				observed <- e
			}()
			err := s.CommitReadiness(ctx, func(group context.Context) error {
				ready := protocol.Ready{Header: protocol.Header{Schema: protocol.Schema, Type: "ready", Seq: 1}, EnvoyPID: 1, ShellPID: 2, Cwd: "/", Columns: 80, Rows: 24, OutputThrough: 3}
				if e := s.Send(group, ready); e != nil {
					return e
				}
				if _, e := s.Terminal().Write([]byte("raw")); e != nil {
					return e
				}
				return <-observed
			})
			if err != nil {
				t.Fatal(err)
			}
			select {
			case event := <-s.Events():
				if channel == "terminal" && string(event.Input) != "valid" {
					t.Fatal("input lost")
				}
				if channel == "telemetry" {
					if _, ok := event.Message.(*protocol.Resize); !ok {
						t.Fatal("control lost")
					}
				}
			case <-s.Done():
				t.Fatal("valid response rejected", s.Err())
			case <-time.After(time.Second):
				t.Fatal("response blocked")
			}
		})
	}
}
func TestReadinessGroupFailureAndOriginalEpoch(t *testing.T) {
	for _, kind := range []string{"publish-error", "expired", "missing"} {
		t.Run(kind, func(t *testing.T) {
			s, _, _, ctx := established(t)
			failure := io.ErrUnexpectedEOF
			publish := func(context.Context) error { return failure }
			if kind == "expired" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithDeadline(ctx, time.Now().Add(-time.Second))
				defer cancel()
				publish = func(context.Context) error { t.Fatal("expired group called publisher"); return nil }
			}
			if kind == "missing" {
				publish = nil
			}
			err := s.CommitReadiness(ctx, publish)
			if err == nil {
				t.Fatal("failed group admitted input")
			}
			fatal(t, s)
			if kind == "publish-error" && !errors.Is(err, failure) {
				t.Fatal("cause lost")
			}
		})
	}
}
func TestMissingTrustedCoordinates(t *testing.T) {
	for _, pair := range [][2]string{{"", ""}, {"", "127.0.0.1:0"}, {"127.0.0.1:0", ""}} {
		if l, err := Listen(pair[0], pair[1]); err == nil {
			l.Close()
			t.Fatal("missing coordinate bound endpoint")
		}
	}
}

func TestUnusedExtrasPreserveSelectedPair(t *testing.T) {
	l := listeners(t)
	a, b := l.Addresses()
	terminal := dial(t, a)
	extraTerminal := dial(t, a)
	telemetry := dial(t, b)
	extraTelemetry := dial(t, b)
	terminal.SetDeadline(time.Now().Add(2 * time.Second))
	telemetry.SetDeadline(time.Now().Add(2 * time.Second))
	// Queued extra data must never enter the selected session.
	writeTestPeer(t, extraTerminal, []byte("unused-input"))
	writeTestPeer(t, extraTelemetry, hello(t, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1))
	writeTestPeer(t, telemetry, hello(t, testID, 1))
	r := receive(t, begin(l, context.Background(), testID))
	if r.err != nil {
		t.Fatal("unused extra failed valid pair", r.err)
	}
	for _, extra := range []*net.TCPConn{extraTerminal, extraTelemetry} {
		extra.SetReadDeadline(time.Now().Add(time.Second))
		if _, err := extra.Read(make([]byte, 1)); err == nil {
			t.Fatal("unused extra received session output")
		} else if e, ok := err.(net.Error); ok && e.Timeout() {
			t.Fatal("unused extra remained open")
		}
	}
	if err := r.s.CommitReadiness(r.ctx, func(ctx context.Context) error {
		return r.s.Send(ctx, protocol.Ready{Header: protocol.Header{Schema: protocol.Schema, Type: "ready", Seq: 1}, EnvoyPID: 1, ShellPID: 2, Cwd: "/", Columns: 80, Rows: 24})
	}); err != nil {
		t.Fatal(err)
	}
	frame, err := bufio.NewReader(telemetry).ReadBytes('\n')
	if err != nil {
		t.Fatal(err)
	}
	if _, err := protocol.DecodeEnvoy(frame); err != nil {
		t.Fatal(err)
	}
	writeTestPeer(t, terminal, []byte("selected-input"))
	select {
	case event := <-r.s.Events():
		if string(event.Input) != "selected-input" {
			t.Fatal("extra input contaminated selected pair", event)
		}
	case <-time.After(time.Second):
		t.Fatal("selected pair no longer transfers input")
	}
	if _, _, _, err := l.Handshake(context.Background(), testID); err == nil {
		t.Fatal("selected pair was replaced")
	}
	writeTestPeer(t, terminal, []byte("same-pair"))
	select {
	case event := <-r.s.Events():
		if string(event.Input) != "same-pair" {
			t.Fatal("pair changed")
		}
	case <-time.After(time.Second):
		t.Fatal("repeat handshake damaged active pair")
	}
	terminal.CloseWrite()
	fatal(t, r.s)
}

func writeTestPeer(t *testing.T, c net.Conn, data []byte) {
	t.Helper()
	if err := c.SetWriteDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	if n, err := c.Write(data); err != nil || n != len(data) {
		t.Fatalf("peer write: %d/%d, %v", n, len(data), err)
	}
}

func TestConcurrentExtraClosurePreservesSelectedPair(t *testing.T) {
	for _, endpoint := range []string{"terminal", "telemetry"} {
		t.Run(endpoint, func(t *testing.T) {
			for i := 0; i < 128; i++ {
				t.Run(fmt.Sprint(i), func(t *testing.T) {
					l := listeners(t)
					a, b := l.Addresses()
					ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
					t.Cleanup(cancel)
					deadline, _ := ctx.Deadline()
					done := begin(l, ctx, testID)
					first := dial(t, a)
					second := dial(t, b)
					first.SetDeadline(deadline)
					second.SetDeadline(deadline)
					extraAddress := a
					if endpoint == "telemetry" {
						extraAddress = b
					}
					// Dial can race listener closure. Either refusal or disposal is valid.
					extra, _ := net.DialTimeout("tcp", extraAddress, 100*time.Millisecond)
					if extra != nil {
						t.Cleanup(func() { extra.Close() })
					}
					writeTestPeer(t, second, hello(t, testID, 1))
					result := receive(t, done)
					if result.err != nil {
						t.Fatal("closure race damaged valid pair", result.err)
					}
					launchDeadline, ok := result.ctx.Deadline()
					if !ok || !launchDeadline.Equal(deadline) {
						t.Fatal("extra reset caller epoch", launchDeadline, deadline)
					}
					if err := result.s.CommitReadiness(result.ctx, func(group context.Context) error {
						return result.s.Send(group, protocol.Ready{Header: protocol.Header{Schema: protocol.Schema, Type: "ready", Seq: 1}, EnvoyPID: 1, ShellPID: 2, Cwd: "/", Columns: 80, Rows: 24})
					}); err != nil {
						t.Fatal(err)
					}
					frame, err := bufio.NewReader(second).ReadBytes('\n')
					if err != nil {
						t.Fatal(err)
					}
					if _, err := protocol.DecodeEnvoy(frame); err != nil {
						t.Fatal(err)
					}
					writeTestPeer(t, first, []byte{42})
					select {
					case event := <-result.s.Events():
						if len(event.Input) != 1 || event.Input[0] != 42 {
							t.Fatal("selected data changed", event)
						}
					case <-ctx.Done():
						t.Fatal("selected input lost")
					}
					if extra != nil {
						extra.SetDeadline(deadline)
						// A completed extra handshake must still be disposed, without session output.
						extra.Write([]byte("unused-input"))
						if _, err := extra.Read(make([]byte, 1)); err == nil {
							t.Fatal("extra received output")
						} else if e, ok := err.(net.Error); ok && e.Timeout() {
							t.Fatal("extra connection retained")
						}
					}
					select {
					case <-result.s.Done():
						t.Fatal("extra failed valid pair", result.s.Err())
					default:
					}
				})
			}
		})
	}
}
