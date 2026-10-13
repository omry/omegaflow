package session

import (
	"bufio"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const handshakeBudget = 10 * time.Second

// Listeners binds both lease-private coordinates before the shared accept epoch.
// Addresses are supplied by trusted bootstrap; there are no runtime defaults.
type Listeners struct {
	terminal, telemetry *net.TCPListener
	bound               time.Time
	once                sync.Once
	claimed             atomic.Bool
}

func Listen(terminal, telemetry string) (*Listeners, error) {
	if terminal == "" || telemetry == "" {
		return nil, fmt.Errorf("both trusted listener coordinates are required")
	}
	l := &Listeners{}
	for i, address := range []string{terminal, telemetry} {
		a, err := net.ResolveTCPAddr("tcp", address)
		if err != nil {
			l.Close()
			return nil, err
		}
		c, err := net.ListenTCP("tcp", a)
		if err != nil {
			l.Close()
			return nil, err
		}
		if i == 0 {
			l.terminal = c
		} else {
			l.telemetry = c
		}
	}
	l.bound = time.Now()
	return l, nil
}

func (l *Listeners) Addresses() (terminal, telemetry string) {
	return l.terminal.Addr().String(), l.telemetry.Addr().String()
}

func (l *Listeners) Close() {
	l.once.Do(func() {
		if l.terminal != nil {
			_ = l.terminal.Close()
		}
		if l.telemetry != nil {
			_ = l.telemetry.Close()
		}
	})
}

// Event is one bounded controller read or terminal input chunk. The caller must
// consume Events serially. A fatal error is retained in Err and Done even when
// the bounded event queue is full; EOF never becomes a reconnect opportunity.
type Event struct {
	Message protocol.Message
	Input   []byte
}

type Channels struct {
	terminal, telemetry *net.TCPConn
	ctx                 context.Context
	cancel              context.CancelFunc
	events              chan Event
	done                chan struct{}
	once                sync.Once
	workers             sync.WaitGroup
	errMu               sync.Mutex
	err                 error
	ready               bool
	readyMu             sync.Mutex
	writeMu             sync.Mutex
	sendSeq             int64
}

func (s *Channels) Events() <-chan Event  { return s.events }
func (s *Channels) Done() <-chan struct{} { return s.done }
func (s *Channels) Err() error            { s.errMu.Lock(); defer s.errMu.Unlock(); return s.err }

// Terminal is the sole byte-stream writer; readers remain owned by Channels.
func (s *Channels) Terminal() *net.TCPConn { return s.terminal }

func (s *Channels) fail(err error) {
	s.once.Do(func() {
		s.errMu.Lock()
		s.err = err
		s.errMu.Unlock()
		s.cancel()
		s.errMu.Lock()
		defer s.errMu.Unlock()
		if s.terminal != nil {
			_ = s.terminal.Close()
		}
		if s.telemetry != nil {
			_ = s.telemetry.Close()
		}
		close(s.done)
	})
}

// Close releases sockets and waits for every transport read owned here.
func (s *Channels) Close() { s.fail(net.ErrClosed); s.workers.Wait() }

func (s *Channels) emit(e Event) bool {
	select {
	case s.events <- e:
		return true
	case <-s.ctx.Done():
		return false
	}
}

func (s *Channels) readTerminal() {
	defer s.workers.Done()
	var buffer [32768]byte
	for {
		n, err := s.terminal.Read(buffer[:])
		if n > 0 {
			if !s.inputReady() {
				s.fail(fmt.Errorf("terminal traffic before public readiness"))
				return
			}
			if !s.emit(Event{Input: append([]byte(nil), buffer[:n]...)}) {
				return
			}
		}
		if err != nil {
			s.fail(fmt.Errorf("terminal channel: %w", err))
			return
		}
	}
}

// readLine bounds allocation and retains a single reader across coalesced frames.
func readLine(r *bufio.Reader) ([]byte, error) {
	var frame []byte
	for {
		part, err := r.ReadSlice('\n')
		if len(part) > protocol.MaxFrameBytes-len(frame) {
			return nil, fmt.Errorf("public frame too large")
		}
		frame = append(frame, part...)
		if err == bufio.ErrBufferFull {
			if len(frame) == protocol.MaxFrameBytes {
				return nil, fmt.Errorf("public frame too large")
			}
			continue
		}
		if err != nil {
			if len(frame) > 0 && err == io.EOF {
				err = io.ErrUnexpectedEOF
			}
			return nil, err
		}
		return frame, nil
	}
}

func (s *Channels) readController(r *bufio.Reader) {
	defer s.workers.Done()
	sequence := int64(2)
	for {
		// Observe the first byte, rather than waiting for a complete forbidden frame.
		if _, err := r.Peek(1); err != nil {
			s.fail(fmt.Errorf("telemetry channel: %w", err))
			return
		}
		if !s.inputReady() {
			s.fail(fmt.Errorf("controller traffic before public readiness"))
			return
		}
		frame, err := readLine(r)
		var m protocol.Message
		if err == nil {
			m, err = protocol.DecodeController(frame)
		}
		if err == nil {
			var h protocol.Header
			err = json.Unmarshal(frame, &h)
			if h.Seq != sequence || h.Type == "hello" {
				err = fmt.Errorf("unexpected controller sequence or repeated hello")
			}
		}
		if err != nil {
			s.fail(err)
			return
		}
		sequence++
		if !s.emit(Event{Message: m}) {
			return
		}
	}
}

// CommitReadiness coordinates one caller-owned publication group with input
// acceptance. The caller passes its unchanged launch context and publishes
// complete public ready followed by its verified startup bytes in publish.
// Read pumps retain bounded data during that owner transition; they cannot
// classify a valid response against an obsolete pre-publication flag. Any
// publication error or expired original context closes the channels.
func (s *Channels) CommitReadiness(ctx context.Context, publish func(context.Context) error) error {
	s.readyMu.Lock()
	defer s.readyMu.Unlock()
	if err := s.Err(); err != nil {
		return err
	}
	if s.ready {
		return fmt.Errorf("readiness already committed")
	}
	err := ctx.Err()
	if err == nil && publish == nil {
		err = fmt.Errorf("missing readiness publisher")
	}
	if err == nil {
		err = publish(ctx)
	}
	if err == nil {
		err = ctx.Err()
	}
	if err == nil {
		err = s.Err()
	}
	if err != nil {
		s.fail(err)
		return err
	}
	s.ready = true
	return nil
}

func (s *Channels) inputReady() bool {
	s.readyMu.Lock()
	defer s.readyMu.Unlock()
	return s.ready
}

// Handshake accepts exactly one connection on each channel, then one strict
// hello matching the trusted 128-bit ID. Each phase retains its own origin.
// The returned launch context starts at complete hello validation and remains
// the caller's deadline through private ready, public ready and startup output.
func (l *Listeners) Handshake(ctx context.Context, sessionID string) (_ *Channels, launch context.Context, cancel context.CancelFunc, err error) {
	id, decodeErr := hex.DecodeString(sessionID)
	if decodeErr != nil || len(id) != 16 || hex.EncodeToString(id) != sessionID {
		l.Close()
		return nil, nil, nil, fmt.Errorf("invalid trusted session ID")
	}
	if !l.claimed.CompareAndSwap(false, true) {
		return nil, nil, nil, fmt.Errorf("listeners already claimed")
	}
	acceptCtx, stopAccept := context.WithDeadline(ctx, l.bound.Add(handshakeBudget))
	defer stopAccept()
	defer l.Close()
	transportCtx, stop := context.WithCancel(ctx)
	s := &Channels{ctx: transportCtx, cancel: stop, events: make(chan Event, 1), done: make(chan struct{}), sendSeq: 1}
	defer func() {
		if err != nil {
			s.Close()
		}
	}()
	stopOnCancel := context.AfterFunc(transportCtx, func() { s.fail(transportCtx.Err()) })
	defer func() {
		if err != nil {
			stopOnCancel()
		}
	}()
	type accepted struct {
		index int
		conn  *net.TCPConn
		err   error
		at    time.Time
	}
	connections := make(chan accepted, 2)
	var acceptors sync.WaitGroup
	for i, listener := range []*net.TCPListener{l.terminal, l.telemetry} {
		acceptors.Add(1)
		go func(i int, listener *net.TCPListener) {
			defer acceptors.Done()
			conn, e := listener.AcceptTCP()
			connections <- accepted{i, conn, e, time.Now()}
		}(i, listener)
	}
	received := 0
	var acceptedAt time.Time
	for received < 2 && err == nil {
		select {
		case a := <-connections:
			received++
			if a.err != nil {
				err = a.err
				break
			}
			s.errMu.Lock()
			if s.err != nil {
				err = s.err
				s.errMu.Unlock()
				_ = a.conn.Close()
				break
			}
			if a.index == 0 {
				s.terminal = a.conn
				s.workers.Add(1)
				go s.readTerminal()
			} else {
				s.telemetry = a.conn
			}
			s.errMu.Unlock()
			if a.at.After(acceptedAt) {
				acceptedAt = a.at
			}
		case <-acceptCtx.Done():
			err = fmt.Errorf("accept phase: %w", acceptCtx.Err())
		case <-s.done:
			err = s.Err()
		}
	}
	if err == nil && acceptCtx.Err() != nil {
		err = fmt.Errorf("accept phase: %w", acceptCtx.Err())
	}
	// The selected pair is fixed. Closing listeners disposes unused queued
	// connections; none of their application data enters this session.
	l.Close()
	acceptors.Wait()
	for received < 2 {
		a := <-connections
		received++
		if a.conn != nil {
			_ = a.conn.Close()
		}
	}
	if err != nil {
		return nil, nil, nil, err
	}
	helloCtx, stopHello := context.WithDeadline(ctx, acceptedAt.Add(handshakeBudget))
	defer stopHello()
	deadline, _ := helloCtx.Deadline()
	if err = s.telemetry.SetReadDeadline(deadline); err != nil {
		return nil, nil, nil, err
	}
	interrupted := make(chan struct{})
	interrupt := context.AfterFunc(helloCtx, func() {
		_ = s.telemetry.SetReadDeadline(time.Now())
		close(interrupted)
	})
	r := bufio.NewReader(s.telemetry)
	frame, e := readLine(r)
	if !interrupt() {
		<-interrupted
	}
	e = helper.WithContextCause(helloCtx, e)
	var message protocol.Message
	if e == nil {
		message, e = protocol.DecodeController(frame)
	}
	hello, ok := message.(*protocol.Hello)
	if e == nil && (!ok || hello.Seq != 1 || hello.SessionID != sessionID) {
		e = fmt.Errorf("invalid session hello")
	}
	e = helper.WithContextCause(helloCtx, e)
	if e != nil {
		return nil, nil, nil, e
	}
	if r.Buffered() > 0 {
		return nil, nil, nil, fmt.Errorf("controller traffic before public readiness")
	}
	launch, cancel = context.WithTimeout(ctx, handshakeBudget)
	if err = s.telemetry.SetReadDeadline(time.Time{}); err != nil {
		cancel()
		return nil, nil, nil, err
	}
	s.workers.Add(1)
	go s.readController(r)
	select {
	case <-s.done:
		cancel()
		return nil, nil, nil, s.Err()
	default:
	}
	return s, launch, cancel, nil
}
