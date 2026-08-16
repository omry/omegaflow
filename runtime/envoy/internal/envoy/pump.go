package envoy

import (
	"errors"
	"fmt"
	"net"
	"os"
	"sync"
	"syscall"
	"time"
)

type outputPump struct {
	master   *os.File
	fd       int
	terminal *net.TCPConn
	timeout  time.Duration

	mu       sync.Mutex
	offset   uint64
	inFlight bool
	done     chan error
	finished chan struct{}
}

func newOutputPump(master *os.File, terminal *net.TCPConn, timeout time.Duration) *outputPump {
	return &outputPump{
		master: master, fd: int(master.Fd()), terminal: terminal, timeout: timeout,
		done: make(chan error, 1), finished: make(chan struct{}),
	}
}

func (pump *outputPump) start() { go pump.run() }

func (pump *outputPump) run() {
	defer close(pump.finished)
	epollFD, err := syscall.EpollCreate1(syscall.EPOLL_CLOEXEC)
	if err != nil {
		pump.done <- fail(FailurePTY, "output-poller-create", err)
		return
	}
	defer syscall.Close(epollFD)
	event := syscall.EpollEvent{Events: syscall.EPOLLIN | syscall.EPOLLHUP | syscall.EPOLLERR, Fd: int32(pump.fd)}
	if err := syscall.EpollCtl(epollFD, syscall.EPOLL_CTL_ADD, pump.fd, &event); err != nil {
		pump.done <- fail(FailurePTY, "output-poller-register", err)
		return
	}
	events := make([]syscall.EpollEvent, 1)
	buffer := make([]byte, 32*1024)
	for {
		if _, err := syscall.EpollWait(epollFD, events, -1); err != nil {
			if errors.Is(err, syscall.EINTR) {
				continue
			}
			pump.done <- fail(FailurePTY, "output-poller-wait", err)
			return
		}
		pump.setInFlight(true)
		for {
			count, err := syscall.Read(pump.fd, buffer)
			if count > 0 {
				writeErr := writeAllWithDeadline(pump.terminal, buffer[:count], pump.timeout)
				if writeErr != nil {
					pump.setInFlight(false)
					pump.done <- fail(FailureConnection, "terminal-output-write", writeErr)
					return
				}
				pump.mu.Lock()
				pump.offset += uint64(count)
				pump.mu.Unlock()
				continue
			}
			if err == nil && count == 0 {
				pump.setInFlight(false)
				pump.done <- nil
				return
			}
			if errors.Is(err, syscall.EAGAIN) || errors.Is(err, syscall.EWOULDBLOCK) {
				pump.setInFlight(false)
				break
			}
			if errors.Is(err, syscall.EINTR) {
				continue
			}
			// Linux PTY masters report EIO after the final slave closes.
			if errors.Is(err, syscall.EIO) {
				pump.setInFlight(false)
				pump.done <- nil
				return
			}
			pump.setInFlight(false)
			pump.done <- fail(FailurePTY, "terminal-output-read", err)
			return
		}
	}
}

func (pump *outputPump) setInFlight(value bool) {
	pump.mu.Lock()
	pump.inFlight = value
	pump.mu.Unlock()
}

func (pump *outputPump) barrier(timeout time.Duration) (uint64, error) {
	deadline := time.Now().Add(timeout)
	for {
		ready, err := bytesReady(pump.fd)
		if err != nil && !errors.Is(err, syscall.EIO) {
			return 0, fmt.Errorf("inspect PTY drain: %w", err)
		}
		pump.mu.Lock()
		offset, inFlight := pump.offset, pump.inFlight
		pump.mu.Unlock()
		if outputDrained(ready, err, inFlight) {
			return offset, nil
		}
		if time.Now().After(deadline) {
			return 0, fmt.Errorf("PTY output barrier timed out")
		}
		time.Sleep(time.Millisecond)
	}
}

func outputDrained(ready int, err error, inFlight bool) bool {
	return !inFlight && ((err == nil && ready == 0) || errors.Is(err, syscall.EIO))
}

func copyTerminalInput(master *os.File, terminal *net.TCPConn) <-chan error {
	done := make(chan error, 1)
	go func() {
		defer master.Close()
		buffer := make([]byte, 32*1024)
		for {
			count, err := terminal.Read(buffer)
			if count > 0 {
				if writeErr := writePTY(master, buffer[:count]); writeErr != nil {
					done <- fail(FailurePTY, "terminal-input-write", writeErr)
					return
				}
			}
			if err != nil {
				done <- fail(FailureConnection, "terminal-input-read", err)
				return
			}
		}
	}()
	return done
}

func writePTY(master *os.File, data []byte) error {
	for len(data) != 0 {
		count, err := syscall.Write(int(master.Fd()), data)
		if count > 0 {
			data = data[count:]
		}
		if err == nil {
			continue
		}
		if errors.Is(err, syscall.EAGAIN) || errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EINTR) {
			time.Sleep(time.Millisecond)
			continue
		}
		return err
	}
	return nil
}

func writeAllWithDeadline(connection net.Conn, data []byte, timeout time.Duration) error {
	if err := connection.SetWriteDeadline(time.Now().Add(timeout)); err != nil {
		return err
	}
	defer connection.SetWriteDeadline(time.Time{}) //nolint:errcheck
	for len(data) != 0 {
		count, err := connection.Write(data)
		if err != nil {
			return err
		}
		data = data[count:]
	}
	return nil
}
