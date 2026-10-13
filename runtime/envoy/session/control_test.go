package session

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
	"io"
	"net"
	"sync"
	"testing"
	"time"
)

type writerProbe struct {
	data      bytes.Buffer
	deadlines []time.Time
	chunk     int
	failure   error
	zero      bool
	mu        sync.Mutex
}

func (w *writerProbe) SetWriteDeadline(d time.Time) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.deadlines = append(w.deadlines, d)
	return nil
}
func (w *writerProbe) Write(b []byte) (int, error) {
	if w.zero {
		return 0, nil
	}
	n := len(b)
	if w.chunk > 0 && n > w.chunk {
		n = w.chunk
	}
	w.data.Write(b[:n])
	return n, w.failure
}
func TestCompleteControlWrites(t *testing.T) {
	frame, err := protocol.EncodePrivate(protocol.PrivateShutdown{}, protocol.EnvoyToAwsh)
	if err != nil {
		t.Fatal(err)
	}
	for _, chunk := range []int{1, 2, 7, len(frame)} {
		w := &writerProbe{chunk: chunk}
		before := time.Now()
		ctx, cancel := context.WithDeadline(context.Background(), before.Add(time.Second))
		if err := SendPrivate(ctx, w, protocol.PrivateShutdown{}); err != nil {
			t.Fatal(err)
		}
		cancel()
		if !bytes.Equal(w.data.Bytes(), frame) || len(w.deadlines) != 1 || !w.deadlines[0].Equal(before.Add(time.Second)) {
			t.Fatal("incomplete write or changed epoch")
		}
	}
	w := &writerProbe{}
	before := time.Now()
	if err := WriteControl(context.Background(), w, frame); err != nil {
		t.Fatal(err)
	}
	if len(w.deadlines) != 1 || w.deadlines[0].Before(before.Add(5*time.Second)) || w.deadlines[0].After(time.Now().Add(5*time.Second)) {
		t.Fatal("individual write epoch")
	}
}
func TestIncompleteControlIsFailure(t *testing.T) {
	for name, w := range map[string]*writerProbe{"zero": {zero: true}, "partial-error": {chunk: 1, failure: io.ErrClosedPipe}} {
		t.Run(name, func(t *testing.T) {
			if err := WriteControl(context.Background(), w, []byte("frame")); err == nil {
				t.Fatal("incomplete delivery accepted")
			}
		})
	}
	for _, frame := range [][]byte{nil, make([]byte, protocol.MaxFrameBytes+1)} {
		w := &writerProbe{}
		if err := WriteControl(context.Background(), w, frame); err == nil || len(w.deadlines) != 0 {
			t.Fatal("invalid frame reached transport")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	w := &writerProbe{}
	if err := WriteControl(ctx, w, []byte("frame")); !errors.Is(err, context.Canceled) || len(w.deadlines) != 0 {
		t.Fatal("cancelled caller reached transport")
	}
}
func TestBlockedWriteOriginalEpochAndCancellation(t *testing.T) {
	for _, cancelEarly := range []bool{false, true} {
		left, right := net.Pipe()
		ctx, cancel := context.WithTimeout(context.Background(), 120*time.Millisecond)
		done := make(chan error, 1)
		started := time.Now()
		go func() { done <- WriteControl(ctx, left, []byte("partially received frame")) }()
		first := make([]byte, 1)
		if _, err := io.ReadFull(right, first); err != nil {
			t.Fatal(err)
		}
		if cancelEarly {
			cancel()
		}
		select {
		case err := <-done:
			if err == nil {
				t.Fatal("partial frame success")
			}
			if time.Since(started) > time.Second {
				t.Fatal("deadline reset")
			}
		case <-time.After(time.Second):
			t.Fatal("blocked writer retained")
		}
		cancel()
		left.Close()
		right.Close()
	}
}

func TestControlWriteFiveSecondLimit(t *testing.T) {
	left, right := net.Pipe()
	defer left.Close()
	defer right.Close()
	started := time.Now()
	err := WriteControl(context.Background(), left, []byte("blocked"))
	elapsed := time.Since(started)
	if err == nil || elapsed < 4900*time.Millisecond || elapsed > 7*time.Second {
		t.Fatalf("individual epoch: %v %v", elapsed, err)
	}
}

func TestPublicSequenceAdvancesOnlyOnFullWrite(t *testing.T) {
	s, _, telemetry, ctx := established(t)
	reader := bufio.NewReader(telemetry)
	for seq := int64(1); seq <= 3; seq++ {
		event := protocol.Diagnostic{Header: protocol.Header{Schema: protocol.Schema, Type: "diagnostic", Seq: seq}, Severity: "info", Code: "test", Message: "complete"}
		if err := s.Send(ctx, event); err != nil {
			t.Fatal(err)
		}
		frame, err := reader.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := protocol.DecodeEnvoy(frame)
		if err != nil {
			t.Fatal(err)
		}
		if decoded.(*protocol.Diagnostic).Seq != seq {
			t.Fatal("outbound sequence changed")
		}
	}
	telemetry.SetLinger(0)
	telemetry.Close()
	fatal(t, s)
	if err := s.Send(ctx, protocol.Diagnostic{Header: protocol.Header{Schema: protocol.Schema, Type: "diagnostic", Seq: 4}, Severity: "info", Code: "test", Message: "closed"}); err == nil {
		t.Fatal("closed channel write accepted")
	}
	if s.sendSeq != 4 {
		t.Fatal("failed write advanced sequence")
	}
}
