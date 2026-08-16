package envoy

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const (
	ProductionAwshPath = "/omegaflow-runtime/bin/awsh"
	DefaultTerminal    = "0.0.0.0:47001"
	DefaultTelemetry   = "0.0.0.0:47002"
)

// Addresses reports the concrete listeners, including test-assigned ports.
type Addresses struct {
	Terminal  string
	Telemetry string
}

// Config is the complete workload-side Envoy configuration. The production
// command fixes AwshPath; tests may substitute the reviewed local prototype.
type Config struct {
	TerminalListen   string
	TelemetryListen  string
	Columns          int
	Rows             int
	AwshPath         string
	ConnectTimeout   time.Duration
	HandshakeTimeout time.Duration
	WriteTimeout     time.Duration
	CancelGrace      time.Duration
	DrainTimeout     time.Duration
	OnListening      func(Addresses)
}

// DefaultConfig returns the frozen production defaults.
func DefaultConfig() Config {
	return Config{
		TerminalListen:   DefaultTerminal,
		TelemetryListen:  DefaultTelemetry,
		Columns:          80,
		Rows:             24,
		AwshPath:         ProductionAwshPath,
		ConnectTimeout:   10 * time.Second,
		HandshakeTimeout: 10 * time.Second,
		WriteTimeout:     5 * time.Second,
		CancelGrace:      5 * time.Second,
		DrainTimeout:     5 * time.Second,
	}
}

type session struct {
	config Config
	state  *protocol.SessionState

	terminal  *net.TCPConn
	telemetry *net.TCPConn
	master    *os.File
	request   *os.File
	result    *os.File
	command   *exec.Cmd
	pump      *outputPump
	waitDone  chan error
	exited    bool

	nextSeq        uint64
	outputStart    uint64
	operationID    string
	currentCWD     string
	cancelReason   string
	shutdownReason string
	shutdown       chan struct{}
	shutdownSent   bool
}

type streamItem struct {
	message any
	err     error
}

// Run accepts one controller and supervises one complete Envoy session.
func Run(ctx context.Context, config Config) (runErr error) {
	if err := ValidateConfig(config); err != nil {
		return fail(FailureProtocol, "invalid-invocation", err)
	}
	terminalListener, err := net.Listen("tcp", config.TerminalListen)
	if err != nil {
		return fail(FailureConnection, "terminal-listen", err)
	}
	defer terminalListener.Close()
	telemetryListener, err := net.Listen("tcp", config.TelemetryListen)
	if err != nil {
		return fail(FailureConnection, "telemetry-listen", err)
	}
	defer telemetryListener.Close()
	if config.OnListening != nil {
		config.OnListening(Addresses{Terminal: terminalListener.Addr().String(), Telemetry: telemetryListener.Addr().String()})
	}

	connectDeadline := time.Now().Add(config.ConnectTimeout)
	terminalConnection, err := acceptTCP(terminalListener, connectDeadline)
	if err != nil {
		return fail(FailureConnection, "terminal-accept", err)
	}
	defer terminalConnection.Close()
	telemetryConnection, err := acceptTCP(telemetryListener, connectDeadline)
	if err != nil {
		return fail(FailureConnection, "telemetry-accept", err)
	}
	defer telemetryConnection.Close()
	_ = terminalListener.Close()
	_ = telemetryListener.Close()

	s := &session{
		config: config, state: protocol.NewSessionState(), terminal: terminalConnection,
		telemetry: telemetryConnection, nextSeq: 1, shutdown: make(chan struct{}),
	}
	defer func() {
		cleanupErr := s.cleanup()
		if cleanupErr == nil {
			return
		}
		if runErr == nil {
			runErr = fail(FailureCleanup, "process-group-cleanup", cleanupErr)
			return
		}
		if failure, ok := runErr.(*Failure); ok {
			failure.Err = errors.Join(failure.Err, fmt.Errorf("cleanup: %w", cleanupErr))
		} else {
			runErr = fail(FailureCleanup, "process-group-cleanup", errors.Join(runErr, cleanupErr))
		}
	}()
	if err := s.run(ctx); err != nil {
		s.bestEffortDiagnostic(err)
		return err
	}
	return nil
}

func (s *session) run(ctx context.Context) error {
	controllerEvents := readTelemetry(s.telemetry)
	helloTimer := time.NewTimer(s.config.HandshakeTimeout)
	defer helloTimer.Stop()
	var hello protocol.Hello
	select {
	case <-ctx.Done():
		return fail(FailureHandshake, "cancelled", ctx.Err())
	case <-helloTimer.C:
		return fail(FailureHandshake, "hello-timeout", errors.New("hello was not received before deadline"))
	case item := <-controllerEvents:
		if item.err != nil {
			return fail(FailureHandshake, "hello-read", item.err)
		}
		value, ok := item.message.(protocol.Hello)
		if !ok {
			return fail(FailureHandshake, "expected-hello", fmt.Errorf("first message is %T", item.message))
		}
		hello = value
	}
	if err := s.state.AcceptController(hello); err != nil {
		return fail(FailureHandshake, "invalid-hello", err)
	}
	if err := s.startShell(); err != nil {
		return err
	}

	awshEvents := readAwsh(s.result, s.shutdown)
	processDone := s.waitDone

	readyTimer := time.NewTimer(s.config.HandshakeTimeout)
	defer readyTimer.Stop()
	select {
	case <-ctx.Done():
		return fail(FailureHandshake, "cancelled", ctx.Err())
	case <-readyTimer.C:
		return fail(FailureHandshake, "ready-timeout", errors.New("awsh ready was not received before deadline"))
	case err := <-processDone:
		s.exited = true
		return fail(FailureShell, "shell-exited-before-ready", processResult(err))
	case item := <-controllerEvents:
		if item.err != nil {
			return fail(FailureHandshake, "traffic-before-ready", item.err)
		}
		return fail(FailureHandshake, "traffic-before-ready", fmt.Errorf("received %T before ready", item.message))
	case item := <-awshEvents:
		if item.err != nil {
			return classifyAwshRead("ready-read", item.err)
		}
		ready, ok := item.message.(protocol.AwshReady)
		if !ok {
			return fail(FailureAwsh, "expected-ready", fmt.Errorf("first awsh result is %T", item.message))
		}
		if ready.ShellPID != s.command.Process.Pid {
			return fail(FailureAwsh, "shell-pid-mismatch", fmt.Errorf("awsh reported %d, started %d", ready.ShellPID, s.command.Process.Pid))
		}
		if err := rejectTerminalTrafficBeforeReady(s.terminal); err != nil {
			return fail(FailureHandshake, "traffic-before-ready", err)
		}
		if err := s.emit(protocol.Ready{Seq: s.takeSeq(), EnvoyPID: os.Getpid(), ShellPID: ready.ShellPID, CWD: ready.CWD, Columns: s.config.Columns, Rows: s.config.Rows}); err != nil {
			return err
		}
		s.currentCWD = ready.CWD
	}
	inputMaster, err := duplicateFile(s.master, "/dev/ptmx-input")
	if err != nil {
		return fail(FailurePTY, "duplicate-input", err)
	}
	inputDone := copyTerminalInput(inputMaster, s.terminal)

	var cancelTimer <-chan time.Time
	var cancelClock *time.Timer
	var cancelPulse <-chan time.Time
	var cancelTicker *time.Ticker
	processController := func(item streamItem) error {
		if item.err != nil {
			return fail(FailureConnection, "telemetry-closed", item.err)
		}
		if err := s.handleController(item.message); err != nil {
			return err
		}
		if cancel, ok := item.message.(protocol.Cancel); ok {
			s.cancelReason = cancel.Reason
			if cancelClock != nil {
				cancelClock.Stop()
			}
			cancelClock = time.NewTimer(s.config.CancelGrace)
			cancelTimer = cancelClock.C
			if cancelTicker != nil {
				cancelTicker.Stop()
			}
			cancelTicker = time.NewTicker(50 * time.Millisecond)
			cancelPulse = cancelTicker.C
		}
		return nil
	}
	defer func() {
		if cancelClock != nil {
			cancelClock.Stop()
		}
		if cancelTicker != nil {
			cancelTicker.Stop()
		}
	}()
	outputDone := s.pump.done
	var processExited, outputEOF, awshClosed bool
	for {
		if s.shutdownSent && awshClosed && processExited && outputEOF {
			offset, err := s.pump.barrier(s.config.DrainTimeout)
			if err != nil {
				return fail(FailureDrain, "final-output-barrier", err)
			}
			if err := s.terminal.CloseWrite(); err != nil {
				return fail(FailureDrain, "terminal-eof", err)
			}
			if err := s.emit(protocol.Closed{Seq: s.takeSeq(), Reason: "shutdown", OutputThrough: offset}); err != nil {
				return err
			}
			return nil
		}
		// Continue and cancel share the ordered telemetry connection. Drain a
		// request already decoded there before translating a racing awsh result.
		select {
		case item := <-controllerEvents:
			if err := processController(item); err != nil {
				return err
			}
			continue
		default:
		}

		select {
		case <-ctx.Done():
			return fail(FailureConnection, "controller-cancelled", ctx.Err())
		case <-cancelTimer:
			cause := errors.New("shell did not return after SIGINT")
			if err := s.failCancelledOperation(cause); err != nil {
				return err
			}
			return fail(FailureCancel, "grace-expired", cause)
		case <-cancelPulse:
			if err := s.interruptForeground(); err != nil {
				return err
			}
		case err := <-inputDone:
			return preserveFailure(err, FailureConnection, "terminal-input-closed")
		case err := <-outputDone:
			if err != nil {
				return preserveFailure(err, FailureDrain, "terminal-output")
			}
			outputEOF = true
			outputDone = nil
		case err := <-processDone:
			processExited = true
			s.exited = true
			processDone = nil
			if err != nil && !s.shutdownSent {
				return fail(FailureShell, "shell-exited", processResult(err))
			}
			if err != nil {
				return fail(FailureCleanup, "shell-shutdown", processResult(err))
			}
		case item := <-controllerEvents:
			if err := processController(item); err != nil {
				return err
			}
		case item := <-awshEvents:
			if item.err != nil {
				if s.shutdownSent && awshClosed && errors.Is(item.err, io.EOF) {
					awshEvents = nil
					continue
				}
				return classifyAwshRead("result-channel", item.err)
			}
			closed, err := s.handleAwsh(item.message)
			if err != nil {
				return err
			}
			if closed {
				awshClosed = true
			}
			if _, ok := item.message.(protocol.AwshCompleted); ok {
				if cancelClock != nil {
					cancelClock.Stop()
					cancelClock = nil
				}
				if cancelTicker != nil {
					cancelTicker.Stop()
					cancelTicker = nil
				}
				cancelTimer = nil
				cancelPulse = nil
				s.cancelReason = ""
			}
		}
	}
}

func (s *session) startShell() error {
	master, slave, err := openPTY(s.config.Columns, s.config.Rows)
	if err != nil {
		return fail(FailurePTY, "open", err)
	}
	s.master = master
	requestReader, requestWriter, err := os.Pipe()
	if err != nil {
		_ = slave.Close()
		return fail(FailureAwsh, "request-pipe", err)
	}
	resultReader, resultWriter, err := os.Pipe()
	if err != nil {
		_ = slave.Close()
		_ = requestReader.Close()
		_ = requestWriter.Close()
		return fail(FailureAwsh, "result-pipe", err)
	}
	s.request, s.result = requestWriter, resultReader

	extra, placeholders, err := privateFiles(requestReader, resultWriter)
	if err != nil {
		_ = slave.Close()
		return fail(FailureAwsh, "private-descriptors", err)
	}
	command := exec.Command(s.config.AwshPath, "--request-fd", "20", "--result-fd", "21")
	command.Stdin, command.Stdout, command.Stderr = slave, slave, slave
	command.ExtraFiles = extra
	command.Env = controlledEnvironment(os.Environ())
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
	if err := command.Start(); err != nil {
		_ = slave.Close()
		closeFiles(placeholders)
		_ = requestReader.Close()
		_ = resultWriter.Close()
		return fail(FailureAwsh, "start", err)
	}
	s.command = command
	s.waitDone = make(chan error, 1)
	go func() { s.waitDone <- command.Wait() }()
	_ = slave.Close()
	closeFiles(placeholders)
	_ = requestReader.Close()
	_ = resultWriter.Close()
	outputMaster, err := duplicateFile(master, "/dev/ptmx-output")
	if err != nil {
		return fail(FailurePTY, "duplicate-output", err)
	}
	s.pump = newOutputPump(outputMaster, s.terminal, s.config.WriteTimeout)
	s.pump.start()
	return nil
}

func privateFiles(requestReader, resultWriter *os.File) ([]*os.File, []*os.File, error) {
	files := make([]*os.File, 0, 19)
	placeholders := make([]*os.File, 0, 17)
	for descriptor := 3; descriptor < 20; descriptor++ {
		file, err := os.OpenFile("/dev/null", os.O_RDWR, 0)
		if err != nil {
			closeFiles(placeholders)
			return nil, nil, err
		}
		placeholders = append(placeholders, file)
		files = append(files, file)
	}
	files = append(files, requestReader, resultWriter)
	return files, placeholders, nil
}

func duplicateFile(file *os.File, name string) (*os.File, error) {
	descriptor, err := syscall.Dup(int(file.Fd()))
	if err != nil {
		return nil, err
	}
	syscall.CloseOnExec(descriptor)
	return os.NewFile(uintptr(descriptor), name), nil
}

func (s *session) handleController(message any) error {
	previousPhase := s.state.Phase()
	if err := s.state.AcceptController(message); err != nil {
		return fail(FailureProtocol, "controller-state", err)
	}
	switch value := message.(type) {
	case protocol.Execute:
		if value.ExecutionShape != protocol.ExecutionPTY {
			return fail(
				FailureProtocol,
				"unsupported-execution-shape",
				fmt.Errorf("execution shape %q is not implemented", value.ExecutionShape),
			)
		}
		if value.Observation != protocol.ObservationShared {
			return fail(
				FailureProtocol,
				"unsupported-observation",
				fmt.Errorf("observation mode %q is not implemented", value.Observation),
			)
		}
		s.operationID = value.OperationID
		return s.writeAwsh(protocol.AwshExecute{
			OperationID:    value.OperationID,
			ExecutionShape: value.ExecutionShape,
			Observation:    value.Observation,
			Source:         value.Source,
		})
	case protocol.Continue:
		return s.writeAwsh(protocol.AwshContinue{OperationID: value.OperationID, GateID: value.GateID})
	case protocol.Cancel:
		if previousPhase == protocol.PhaseGated {
			// A gated driver consumes the typed cancel. A running driver is
			// interrupted by the PTY foreground process group.
			if err := s.writeAwsh(protocol.AwshCancel{OperationID: value.OperationID, Reason: value.Reason}); err != nil {
				return err
			}
		}
		return s.interruptForeground()
	case protocol.Resize:
		if err := setWindowSize(int(s.master.Fd()), value.Columns, value.Rows); err != nil {
			return fail(FailureResize, "apply", err)
		}
		return s.emit(protocol.ResizeApplied{Seq: s.takeSeq(), Columns: value.Columns, Rows: value.Rows})
	case protocol.Shutdown:
		s.shutdownReason = value.Reason
		s.shutdownSent = true
		close(s.shutdown)
		return s.writeAwsh(protocol.AwshShutdown{})
	default:
		return fail(FailureProtocol, "unexpected-controller-message", fmt.Errorf("unexpected %T", message))
	}
}

func (s *session) handleAwsh(message any) (bool, error) {
	barrier := func() (uint64, error) {
		offset, err := s.pump.barrier(s.config.DrainTimeout)
		if err != nil {
			return 0, fail(FailureDrain, "output-barrier", err)
		}
		return offset, nil
	}
	switch value := message.(type) {
	case protocol.AwshStarted:
		offset, err := barrier()
		if err != nil {
			return false, err
		}
		s.outputStart = offset
		return false, s.emit(protocol.OperationStarted{Seq: s.takeSeq(), OperationID: value.OperationID, OutputStart: offset})
	case protocol.AwshGateReady:
		offset, err := barrier()
		if err != nil {
			return false, err
		}
		if s.state.Phase() == protocol.PhaseCancelling {
			if err := s.writeAwsh(protocol.AwshCancel{OperationID: value.OperationID, Reason: s.cancelReason}); err != nil {
				return false, err
			}
			return false, nil
		}
		return false, s.emit(protocol.OperationReady{Seq: s.takeSeq(), OperationID: value.OperationID, GateID: value.GateID, OutputThrough: offset})
	case protocol.AwshGateContinued:
		offset, err := barrier()
		if err != nil {
			return false, err
		}
		if s.state.Phase() == protocol.PhaseCancelling {
			if err := s.interruptForeground(); err != nil {
				return false, err
			}
			return false, nil
		}
		return false, s.emit(protocol.OperationContinued{Seq: s.takeSeq(), OperationID: value.OperationID, GateID: value.GateID, OutputThrough: offset})
	case protocol.AwshCompleted:
		offset, err := barrier()
		if err != nil {
			return false, err
		}
		if s.cancelReason != "" {
			err := s.emit(protocol.OperationCancelled{Seq: s.takeSeq(), OperationID: value.OperationID, Status: value.Status, CWD: value.CWD, Reason: s.cancelReason, OutputStart: s.outputStart, OutputThrough: offset})
			if err == nil {
				s.currentCWD = value.CWD
				s.operationID = ""
			}
			return false, err
		}
		err = s.emit(protocol.OperationCompleted{Seq: s.takeSeq(), OperationID: value.OperationID, Status: value.Status, CWD: value.CWD, OutputStart: s.outputStart, OutputThrough: offset})
		if err == nil {
			s.currentCWD = value.CWD
			s.operationID = ""
		}
		return false, err
	case protocol.AwshProtocolError:
		return false, fail(FailureAwsh, value.Code, errors.New(value.Message))
	case protocol.AwshClosed:
		if !s.shutdownSent || value.Reason != "shutdown" {
			return false, fail(FailureShell, "unexpected-close", fmt.Errorf("awsh closed with reason %q", value.Reason))
		}
		offset, err := barrier()
		if err != nil {
			return false, err
		}
		if err := s.emit(protocol.Draining{Seq: s.takeSeq(), Reason: s.shutdownReason, OutputThrough: offset}); err != nil {
			return false, err
		}
		return true, nil
	case protocol.AwshReady:
		return false, fail(FailureAwsh, "duplicate-ready", errors.New("awsh emitted ready twice"))
	default:
		return false, fail(FailureAwsh, "unexpected-result", fmt.Errorf("unexpected %T", message))
	}
}

func (s *session) emit(message any) error {
	frame, err := protocol.EncodeEnvoy(message)
	if err != nil {
		return fail(FailureProtocol, "encode-event", err)
	}
	if err := s.state.AcceptEnvoy(message); err != nil {
		return fail(FailureProtocol, "envoy-state", err)
	}
	s.nextSeq++
	if err := writeAllWithDeadline(s.telemetry, frame, s.config.WriteTimeout); err != nil {
		return fail(FailureConnection, "telemetry-write", err)
	}
	return nil
}

func (s *session) writeAwsh(message any) error {
	frame, err := protocol.EncodeAwshRequest(message)
	if err != nil {
		return fail(FailureProtocol, "encode-awsh-request", err)
	}
	if err := writeFileWithDeadline(s.request, frame, s.config.WriteTimeout); err != nil {
		return fail(FailureAwsh, "request-write", err)
	}
	return nil
}

func (s *session) takeSeq() uint64 {
	return s.nextSeq
}

func (s *session) bestEffortDiagnostic(cause error) {
	if s.telemetry == nil || s.state.Phase() == protocol.PhaseInitial || s.state.Phase() == protocol.PhaseClosed {
		return
	}
	failure, ok := cause.(*Failure)
	if !ok {
		return
	}
	message := failure.Err.Error()
	message = truncateUTF8(message, protocol.MaxDiagnosticBytes)
	var operationID *string
	if s.operationID != "" {
		value := s.operationID
		operationID = &value
	}
	_ = s.emit(protocol.Diagnostic{Seq: s.takeSeq(), Severity: "fatal", Code: failure.Code, Message: message, OperationID: operationID})
}

func (s *session) failCancelledOperation(cause error) error {
	groups := []int{s.command.Process.Pid}
	if foreground, err := foregroundProcessGroup(int(s.master.Fd())); err == nil && foreground != s.command.Process.Pid {
		groups = append(groups, foreground)
	}
	for _, group := range groups {
		if err := syscall.Kill(-group, syscall.SIGKILL); err != nil && !errors.Is(err, syscall.ESRCH) {
			return fail(FailureCleanup, "cancel-process-group", err)
		}
	}
	offset, err := s.pump.barrier(s.config.DrainTimeout)
	if err != nil {
		return fail(FailureDrain, "cancel-output-barrier", err)
	}
	message := truncateUTF8(cause.Error(), protocol.MaxDiagnosticBytes)
	if err := s.emit(protocol.OperationFailed{
		Seq: s.takeSeq(), OperationID: s.operationID, Code: "cancellation-timeout",
		Message: message, CWD: s.currentCWD, OutputStart: s.outputStart, OutputThrough: offset,
	}); err != nil {
		return err
	}
	s.operationID = ""
	return nil
}

func (s *session) interruptForeground() error {
	group, err := foregroundProcessGroup(int(s.master.Fd()))
	if err != nil {
		return fail(FailureCancel, "foreground-process-group", err)
	}
	if err := syscall.Kill(-group, syscall.SIGINT); err != nil {
		return fail(FailureCancel, "signal", err)
	}
	return nil
}

func (s *session) cleanup() (cleanupErr error) {
	defer func() {
		ptyErr := s.closePTY()
		if cleanupErr == nil {
			cleanupErr = ptyErr
		}
	}()
	if s.request != nil {
		_ = s.request.Close()
	}
	if s.result != nil {
		_ = s.result.Close()
	}
	if s.command == nil || s.command.Process == nil {
		return nil
	}
	err := syscall.Kill(-s.command.Process.Pid, syscall.SIGTERM)
	if err != nil && !errors.Is(err, syscall.ESRCH) {
		return err
	}
	if s.waitForProcessGroup(500 * time.Millisecond) {
		return nil
	}
	if err := syscall.Kill(-s.command.Process.Pid, syscall.SIGKILL); err != nil && !errors.Is(err, syscall.ESRCH) {
		return err
	}
	if s.waitForProcessGroup(s.config.DrainTimeout) {
		return nil
	}
	return errors.New("process group did not exit after SIGKILL")
}

func (s *session) waitForProcessGroup(timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		if !s.exited {
			select {
			case <-s.waitDone:
				s.exited = true
			default:
			}
		}
		groupErr := syscall.Kill(-s.command.Process.Pid, 0)
		if s.exited && errors.Is(groupErr, syscall.ESRCH) {
			return true
		}
		if groupErr != nil && !errors.Is(groupErr, syscall.ESRCH) {
			return false
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func (s *session) closePTY() error {
	if s.pump != nil {
		select {
		case <-s.pump.finished:
		case <-time.After(s.config.DrainTimeout):
			_ = s.pump.master.Close()
			if s.master != nil {
				_ = s.master.Close()
			}
			return errors.New("PTY output pump did not stop")
		}
		_ = s.pump.master.Close()
	}
	if s.master != nil {
		_ = s.master.Close()
	}
	return nil
}

// ValidateConfig validates the complete Envoy configuration before any
// listener or workload-side process is created.
func ValidateConfig(config Config) error {
	if config.TerminalListen == "" || config.TelemetryListen == "" || config.AwshPath == "" {
		return errors.New("listen coordinates and awsh path are required")
	}
	if config.Columns < protocol.MinColumns || config.Columns > protocol.MaxColumns || config.Rows < protocol.MinRows || config.Rows > protocol.MaxRows {
		return errors.New("terminal size is out of range")
	}
	if config.ConnectTimeout <= 0 || config.HandshakeTimeout <= 0 || config.WriteTimeout <= 0 || config.CancelGrace <= 0 || config.DrainTimeout <= 0 {
		return errors.New("timeouts must be positive")
	}
	return nil
}

func acceptTCP(listener net.Listener, deadline time.Time) (*net.TCPConn, error) {
	if tcp, ok := listener.(*net.TCPListener); ok {
		if err := tcp.SetDeadline(deadline); err != nil {
			return nil, err
		}
	}
	connection, err := listener.Accept()
	if err != nil {
		return nil, err
	}
	tcp, ok := connection.(*net.TCPConn)
	if !ok {
		_ = connection.Close()
		return nil, errors.New("accepted connection is not TCP")
	}
	return tcp, nil
}

func rejectTerminalTrafficBeforeReady(connection *net.TCPConn) error {
	raw, err := connection.SyscallConn()
	if err != nil {
		return fmt.Errorf("inspect terminal channel: %w", err)
	}
	var count int
	var receiveErr error
	if err := raw.Control(func(descriptor uintptr) {
		buffer := []byte{0}
		count, _, receiveErr = syscall.Recvfrom(int(descriptor), buffer, syscall.MSG_PEEK|syscall.MSG_DONTWAIT)
	}); err != nil {
		return fmt.Errorf("inspect terminal channel: %w", err)
	}
	if errors.Is(receiveErr, syscall.EAGAIN) || errors.Is(receiveErr, syscall.EWOULDBLOCK) {
		return nil
	}
	if receiveErr != nil {
		return fmt.Errorf("inspect terminal channel: %w", receiveErr)
	}
	if count == 0 {
		return errors.New("terminal channel closed before ready")
	}
	return errors.New("terminal channel carried traffic before ready")
}

func preserveFailure(err error, class FailureClass, code string) error {
	var failure *Failure
	if errors.As(err, &failure) {
		return failure
	}
	return fail(class, code, err)
}

func readTelemetry(connection net.Conn) <-chan streamItem {
	items := make(chan streamItem, 16)
	go func() {
		decoder := protocol.NewControllerStreamDecoder()
		buffer := make([]byte, 32*1024)
		for {
			count, err := connection.Read(buffer)
			if count > 0 {
				messages, decodeErr := decoder.Feed(buffer[:count])
				if decodeErr != nil {
					items <- streamItem{err: decodeErr}
					return
				}
				for _, message := range messages {
					items <- streamItem{message: message}
				}
			}
			if err != nil {
				if finishErr := decoder.Finish(); finishErr != nil {
					items <- streamItem{err: finishErr}
				} else {
					items <- streamItem{err: err}
				}
				return
			}
		}
	}()
	return items
}

func readAwsh(reader *os.File, shutdown <-chan struct{}) <-chan streamItem {
	items := make(chan streamItem, 16)
	go func() {
		decoder := protocol.NewAwshResultStreamDecoder()
		buffer := make([]byte, 32*1024)
		for {
			count, err := reader.Read(buffer)
			if count > 0 {
				messages, decodeErr := decoder.Feed(buffer[:count])
				if decodeErr != nil {
					items <- streamItem{err: decodeErr}
					return
				}
				for _, message := range messages {
					items <- streamItem{message: message}
				}
			}
			if err != nil {
				select {
				case <-shutdown:
					_ = decoder.MarkShutdownRequested()
				default:
				}
				if finishErr := decoder.Finish(); finishErr != nil {
					items <- streamItem{err: finishErr}
				} else {
					items <- streamItem{err: err}
				}
				return
			}
		}
	}()
	return items
}

func controlledEnvironment(environment []string) []string {
	blocked := map[string]bool{
		"AWSH_BASH": true, "BASH_COMPAT": true, "BASHOPTS": true, "BASH_ENV": true,
		"BASH_XTRACEFD": true, "CDPATH": true, "ENV": true, "GLOBIGNORE": true,
		"POSIXLY_CORRECT": true, "PROMPT_COMMAND": true, "PS0": true, "PS1": true,
		"PS2": true, "PS3": true, "PS4": true, "SHELLOPTS": true, "TMOUT": true,
	}
	result := make([]string, 0, len(environment))
	for _, entry := range environment {
		name, _, ok := strings.Cut(entry, "=")
		if !ok || name == "" || blocked[name] || strings.HasPrefix(name, "BASH_FUNC_") || strings.ContainsRune(entry, 0) {
			continue
		}
		result = append(result, entry)
	}
	return result
}

func writeFileWithDeadline(file *os.File, data []byte, timeout time.Duration) error {
	if err := file.SetWriteDeadline(time.Now().Add(timeout)); err != nil {
		return err
	}
	defer file.SetWriteDeadline(time.Time{}) //nolint:errcheck
	for len(data) != 0 {
		count, err := file.Write(data)
		if err != nil {
			return err
		}
		data = data[count:]
	}
	return nil
}

func processResult(err error) error {
	if err == nil {
		return errors.New("process exited")
	}
	return err
}

func classifyAwshRead(code string, err error) error {
	var protocolErr *protocol.Error
	if errors.As(err, &protocolErr) && protocolErr.Code == "early-close" {
		return fail(FailureShell, "shell-result-closed", err)
	}
	return fail(FailureAwsh, code, err)
}

func truncateUTF8(value string, maximum int) string {
	if len(value) <= maximum {
		return value
	}
	value = value[:maximum]
	for !utf8.ValidString(value) {
		value = value[:len(value)-1]
	}
	return value
}

func closeFiles(files []*os.File) {
	for _, file := range files {
		_ = file.Close()
	}
}
