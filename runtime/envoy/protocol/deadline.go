package protocol

import "time"

// The v1 timeout values. Every timer uses its owner's monotonic clock, is
// scoped to one session, and does not reset on partial progress. Operation
// duration is owned by the recording plan, not a fixed Envoy timeout: the
// controller converts an operation deadline into a typed cancel.
const (
	ConnectDeadline           = 10 * time.Second
	HelloReadyDeadline        = 10 * time.Second
	ControlWriteDeadline      = 5 * time.Second
	HelperExchangeDeadline    = 5 * time.Second
	OperationStartDeadline    = 5 * time.Second
	InputBarrierWait          = 5 * time.Second
	CancellationGracePeriod   = 5 * time.Second
	OperationCleanupDeadline  = 5 * time.Second
	ResizeTransactionDeadline = 5 * time.Second
	FinalDrainDeadline        = 5 * time.Second
	ReadlineEntryDeadline     = 5 * time.Second
	OutputMarkCadence         = 10 * time.Millisecond
)

// Deadline is one row of the normative timeout-ownership table: which actor
// owns the timer, the exact epoch that starts it, the work it covers, and
// the typed result of its expiry.
type Deadline struct {
	Name     string
	Owner    string
	Epoch    string
	Duration time.Duration
	Covers   string
	Expiry   string
}

// DeadlineTable freezes the normative ownership of every v1 startup,
// control-write, and operation deadline epoch. The two connect timers and
// the two handshake timers are intentionally independent actor-local bounds:
// neither side extends its timer because the other made partial progress.
var DeadlineTable = []Deadline{
	{
		Name:     "controller-connect",
		Owner:    "controller",
		Epoch:    "after the complete bootstrap exec command and newline have been written",
		Duration: ConnectDeadline,
		Covers:   "resolve the two already-opened coordinates and complete the terminal connection followed by the telemetry connection within one shared budget",
		Expiry:   "fail the capture and ask Reploy to terminate",
	},
	{
		Name:     "envoy-accept",
		Owner:    "envoy",
		Epoch:    "after both listeners are bound",
		Duration: ConnectDeadline,
		Covers:   "accept the one terminal connection and one telemetry connection within one shared budget",
		Expiry:   "emit a best-effort fatal diagnostic, close both listeners and accepted sockets, and exit nonzero",
	},
	{
		Name:     "envoy-hello",
		Owner:    "envoy",
		Epoch:    "after both connections are accepted",
		Duration: HelloReadyDeadline,
		Covers:   "read and validate one complete hello frame, including the exact session_id",
		Expiry:   "fail the handshake and exit nonzero",
	},
	{
		Name:     "controller-ready",
		Owner:    "controller",
		Epoch:    "after the complete hello frame is written",
		Duration: HelloReadyDeadline,
		Covers:   "read and validate one complete ready frame",
		Expiry:   "fail the capture and ask Reploy to terminate",
	},
	{
		Name:     "control-write",
		Owner:    "sender",
		Epoch:    "with the first attempted transport write of one already-encoded frame",
		Duration: ControlWriteDeadline,
		Covers:   "write every byte of one telemetry JSON Lines frame or one private awsh frame; terminal input and workload-output bytes are excluded",
		Expiry:   "fail the session; delivery of a partial frame never becomes success",
	},
	{
		Name:     "bash-helper-exchange",
		Owner:    "helper or awsh",
		Epoch:    "with connect for a helper and accept for awsh",
		Duration: HelperExchangeDeadline,
		Covers:   "connect, transfer each permitted complete packet and its applicable non-blocking acknowledgment, and close; intentional start and gate decision waits are excluded and use their owning lifecycle timer",
		Expiry:   "fatal adapter-state through the owning startup, operation-start, running, or final-drain failure path",
	},
	{
		Name:     "operation-start",
		Owner:    "envoy",
		Epoch:    "when the terminal-input barrier is satisfied and the private execute write begins",
		Duration: OperationStartDeadline,
		Covers:   "complete awsh source validation and framing, receive submit, drain legitimate pre-submission output, write the complete terminal submission, suppress its echo and redraw, and accept matching started",
		Expiry:   "emit best-effort fatal operation-start-timeout, close the session channels, terminate and reap the selected-shell tree, and exit nonzero without a terminal operation result",
	},
	{
		Name:     "input-barrier",
		Owner:    "envoy",
		Epoch:    "when an execute or continue begins waiting for its input_through watermark",
		Duration: InputBarrierWait,
		Covers:   "reach the request's terminal-input watermark",
		Expiry:   "fail an unstarted operation with input-barrier-timeout; a gated continue expiry is fatal input-barrier-timeout with no terminal operation result",
	},
	{
		Name:     "resize-transaction",
		Owner:    "envoy",
		Epoch:    "when the private resize_prepare write begins",
		Duration: ResizeTransactionDeadline,
		Covers:   "reserve the termios lane, close the output frontier, apply TIOCSWINSZ through matching resize_apply, and accept matching resized",
		Expiry:   "emit best-effort fatal resize-failed, close the session channels, terminate the controlled subtree, and exit nonzero without resize_applied or a terminal operation result",
	},
	{
		Name:     "cancellation-grace",
		Owner:    "envoy",
		Epoch:    "when an accepted cancel or finalize wins against the adapter result",
		Duration: CancellationGracePeriod,
		Covers:   "the private request/disposition round trip, any selected interruption, and the adapter's return",
		Expiry:   "terminate the selected-shell process group and report cancel-timeout or finalize-timeout with shell_ended true",
	},
	{
		Name:     "operation-cleanup",
		Owner:    "envoy",
		Epoch:    "when mandatory cleanup begins after an adapter result, lifecycle adapter return, or explicit shell_exit",
		Duration: OperationCleanupDeadline,
		Covers:   "census, terminate, reap, reach operation-stream EOF, and drain all operation-created processes and output",
		Expiry:   "emit best-effort fatal operation-cleanup, close the session, and exit nonzero without a terminal operation result",
	},
	{
		Name:     "inspection-cancellation",
		Owner:    "envoy",
		Epoch:    "when cancel is accepted while the operation's inspection worker is live",
		Duration: CancellationGracePeriod,
		Covers:   "stop and reap the isolated inspection worker",
		Expiry:   "emit best-effort fatal inspection-cancel-timeout, close the session, and exit nonzero without a terminal operation result",
	},
	{
		Name:     "envoy-final-drain",
		Owner:    "envoy",
		Epoch:    "when shutdown is accepted or an Envoy-initiated drain begins",
		Duration: FinalDrainDeadline,
		Covers:   "close awsh, supervise the persistent awsh session and subtree, drain terminal output, and emit closed",
		Expiry:   "emit a best-effort fatal diagnostic and exit nonzero",
	},
	{
		Name:     "controller-final-drain",
		Owner:    "controller",
		Epoch:    "when draining is accepted",
		Duration: FinalDrainDeadline,
		Covers:   "receive closed, retain raw output through its final offset, and observe terminal EOF",
		Expiry:   "fail the capture and ask Reploy to terminate",
	},
	{
		Name:     "readline-entry",
		Owner:    "awsh",
		Epoch:    "when the prompt_ready packet is accepted",
		Duration: ReadlineEntryDeadline,
		Covers:   "sentinel apply and read-back, acknowledgement, helper closure, and the observed READLINE_ACTIVE transition",
		Expiry:   "shell-launch initially, fatal adapter-state later; the outer startup or operation deadline may expire first",
	},
}
