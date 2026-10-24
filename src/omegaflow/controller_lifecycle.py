"""Serialized controller decisions, independent of subprocess and TCP transport.

C2's client owner executes returned effects and reports observations back here.
C3 supplies validated Envoy observations and proves the initial raw-log barrier.
There is deliberately no runnable capture entrypoint in this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any

from .reploy_codecs import CLIENT_SCHEMA, decode_event, encode_request


ATTACH_TIMEOUT_SECONDS = 10.0


class ControllerLifecycleError(RuntimeError):
    """The controller cannot safely continue the observed lifecycle."""


@dataclass(frozen=True)
class ControllerEffect:
    """An action for the owning client adapter; terminal bytes are never events."""

    kind: str
    argv: tuple[str, ...] = ()
    line: bytes | None = None
    deadline: float | None = None


def _same_status(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Compare C1-validated statuses with an optional empty reason."""
    return (left["kind"] == right["kind"] and left.get("code") == right.get("code")
            and left.get("reason", "") == right.get("reason", ""))


class ReployState(Enum):
    WAIT_BROKER = "wait-broker"
    WAIT_OPENED = "wait-opened"
    WAIT_READY = "wait-ready"
    ACTIVE = "active"
    TERMINATING = "terminating"
    OUTPUTS_FINALIZED = "outputs-finalized"
    RESULT = "result"
    FAILED = "failed"


class ReployLifecycle:
    """Accept public client events and authorize only their next local actions.

    Calls have one serialized owner. ``now`` is that owner's monotonic clock.
    An effect authorizes submission, never a successful remote outcome. This
    core owns no process, socket or artifact; the adapter retains those resources.
    """

    def __init__(self) -> None:
        self.state = ReployState.WAIT_BROKER
        self._opened: dict[str, Any] | None = None
        self._result: dict[str, Any] | None = None
        self._attachment_deadline: float | None = None
        self._last_time: float | None = None
        self._attached = False
        self._attachment_ended = False
        self._bootstrap_sent = False
        self._envoy_ready = False
        self._workload_status: dict[str, Any] | None = None
        self._cause: str | None = None
        self._output_status: dict[str, Any] | None = None
        self._finalization_requested = False
        self._complete_sent = False

    @property
    def opened(self) -> dict[str, Any] | None:
        return deepcopy(self._opened)

    @property
    def result(self) -> dict[str, Any] | None:
        """The complete authoritative session result, not a success shortcut."""
        return deepcopy(self._result)

    @property
    def can_record(self) -> bool:
        return self.state is ReployState.ACTIVE and self._envoy_ready

    @property
    def attachment_deadline(self) -> float | None:
        return self._attachment_deadline

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            self.state = ReployState.FAILED
            raise ControllerLifecycleError(message)

    def _live(self) -> None:
        self._require(self.state not in {ReployState.RESULT, ReployState.FAILED},
                      "session already has a terminal outcome")

    def _time(self, now: float) -> None:
        try:
            valid = type(now) in {int, float} and math.isfinite(now)
        except OverflowError:
            valid = False
        self._require(valid, "invalid monotonic time")
        self._require(self._last_time is None or now >= self._last_time,
                      "monotonic clock moved backwards")
        self._last_time = now

    def check_attachment_deadline(self, now: float) -> None:
        self._live()
        self._time(now)
        self._require(self._attached or self._attachment_deadline is None
                      or now < self._attachment_deadline,
                      "attachment deadline expired")

    def attachment_connected(self, *, now: float) -> tuple[ControllerEffect, ...]:
        self.check_attachment_deadline(now)
        self._require(self._attachment_deadline is not None and not self._attached,
                      "unexpected attachment confirmation")
        self._attached = True
        return self._bootstrap()

    def _bootstrap(self) -> tuple[ControllerEffect, ...]:
        if self.state is ReployState.ACTIVE and self._attached and not self._bootstrap_sent:
            self._require(self._opened is not None and "input" in self._opened["operations"],
                          "Envoy bootstrap requires the input grant")
            self._bootstrap_sent = True
            return (ControllerEffect("bootstrap-envoy"),)
        return ()

    def envoy_ready(self) -> None:
        """C3 calls this only after both channels and ready's raw-log barrier."""
        self._live()
        self._require(self.state is ReployState.ACTIVE and self._bootstrap_sent
                      and self._attached and not self._envoy_ready,
                      "unexpected Envoy readiness confirmation")
        self._envoy_ready = True

    def on_event(self, line: bytes, *, now: float) -> tuple[ControllerEffect, ...]:
        self._live()
        try:
            event = decode_event(line)
        except ValueError as error:
            self.state = ReployState.FAILED
            raise ControllerLifecycleError("invalid public Reploy event") from error
        self._time(now)
        kind = event["type"]
        if kind == "broker-ready":
            self._require(self.state is ReployState.WAIT_BROKER, "duplicate or late broker-ready")
            self._attachment_deadline = now + ATTACH_TIMEOUT_SECONDS
            self.state = ReployState.WAIT_OPENED
            return (ControllerEffect("attach", (
                "reploy-session-client", "attach", "--socket", event["terminal_socket"],
            ), deadline=self._attachment_deadline),)
        self._require(self.state is not ReployState.WAIT_BROKER, "broker-ready must be first")
        if kind == "opened":
            self._require(self.state is ReployState.WAIT_OPENED, "unexpected opened")
            self._opened = event
            self.state = ReployState.WAIT_READY
            return ()
        if kind == "ready":
            self._require(self.state is ReployState.WAIT_READY, "unexpected ready")
            self.check_attachment_deadline(now)
            self.state = ReployState.ACTIVE
            return self._bootstrap()
        if kind == "diagnostic":
            return ()  # The owning adapter retains diagnostics; they grant no action.
        if kind == "client-error":
            self._require(False, "public Reploy client reported a fatal error")
        if kind == "workload-exit":
            self._require(self.state in {ReployState.ACTIVE, ReployState.TERMINATING}
                          and self._workload_status is None, "unexpected workload-exit")
            self._workload_status = event["status"]
            return ()
        if kind == "terminating":
            self._require(self.state is ReployState.ACTIVE, "unexpected terminating")
            if event["cause"] == "workload-exit":
                self._require(self._workload_status is not None,
                              "workload-exit cause requires its preceding status")
            self._cause = event["cause"]
            self.state = ReployState.TERMINATING
            return (ControllerEffect("session-terminating"),)
        if kind == "workload-outputs-finalized":
            self._require(self.state is ReployState.TERMINATING,
                          "output finalization must follow terminating")
            self._output_status = {"kind": event["status"]}
            if "reason" in event:
                self._output_status["reason"] = event["reason"]
            self.state = ReployState.OUTPUTS_FINALIZED
            return self._finalize_artifacts()
        if kind == "terminated":
            result = event["result"]
            startup_failure = (self.state in {ReployState.WAIT_OPENED, ReployState.WAIT_READY}
                               and result["cause"] == "startup-failure")
            self._require(startup_failure or (self.state is ReployState.OUTPUTS_FINALIZED
                                             and self._complete_sent),
                          "unexpected authoritative terminated result")
            if not startup_failure:
                self._require(result["cause"] == self._cause,
                              "result changed the latched termination cause")
                self._require(_same_status(result["workload_output_finalization_status"], self._output_status),
                              "result changed observed output finalization")
            if self._workload_status is not None:
                self._require(_same_status(result["workload_status"], self._workload_status),
                              "result changed observed workload status")
            self._result = result
            self.state = ReployState.RESULT
            return (ControllerEffect("session-result"),)
        self._require(False, "unsupported Reploy lifecycle event")
        return ()

    def attachment_ended(self) -> tuple[ControllerEffect, ...]:
        """Report ordered terminal EOF, not a successful workload exit code."""
        self._live()
        self._require(self._attached and not self._attachment_ended
                      and self.state in {ReployState.TERMINATING, ReployState.OUTPUTS_FINALIZED},
                      "unexpected terminal attachment end")
        self._attachment_ended = True
        return self._finalize_artifacts()

    def _finalize_artifacts(self) -> tuple[ControllerEffect, ...]:
        if (self.state is ReployState.OUTPUTS_FINALIZED and self._attachment_ended
                and not self._finalization_requested):
            self._finalization_requested = True
            return (ControllerEffect("finalize-artifacts"),)
        return ()

    def artifacts_finalized(self) -> tuple[ControllerEffect, ...]:
        """Closed partial artifacts also permit complete after output failure."""
        self._live()
        self._require(self.state is ReployState.OUTPUTS_FINALIZED
                      and self._finalization_requested and not self._complete_sent,
                      "artifacts cannot complete at this lifecycle point")
        self._require(self._opened is not None and "complete" in self._opened["operations"],
                      "controller completion requires the complete grant")
        line = encode_request({"schema": CLIENT_SCHEMA, "type": "complete"})
        self._complete_sent = True
        return (ControllerEffect("send-request", line=line),)


class OperationState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    GATED = "gated"
    CONTINUING = "continuing"
    FINALIZING = "finalizing"
    SHUTDOWN_SENT = "shutdown-sent"
    DRAINING = "draining"
    CLOSED = "closed"
    FAILED = "failed"


class OperationLifecycle:
    """Controller operation decisions; Envoy alone owns cleanup and outcomes.

    C3 must validate complete wire values before passing observations here. No
    method infers output delivery, filesystem results or workload exit status.
    """

    def __init__(self, session: ReployLifecycle) -> None:
        self.session = session
        self.state = OperationState.IDLE
        self.active_id: str | None = None
        self.gate_id: str | None = None
        self.shell_ended = False
        self._shutdown_reason: str | None = None
        self._drain_reason: str | None = None
        self._closed_seen = False
        self._terminal_eof = False
        self._seen_gates: set[str] = set()

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            self.state = OperationState.FAILED
            raise ControllerLifecycleError(message)

    def _match(self, operation_id: str) -> None:
        self._require(self.active_id is not None and operation_id == self.active_id,
                      "operation observation has the wrong identity")

    def execute(self, operation_id: str) -> None:
        self._require(self.session.can_record and self.state is OperationState.IDLE
                      and not self.shell_ended, "controller is not ready for execute")
        self._require(type(operation_id) is str and bool(operation_id),
                      "invalid operation identity")
        self._seen_gates.clear()
        self.active_id = operation_id
        self.state = OperationState.STARTING

    def started(self, operation_id: str) -> None:
        self._match(operation_id)
        self._require(self.state is OperationState.STARTING, "unexpected operation_started")
        self.state = OperationState.RUNNING

    def gate_ready(self, operation_id: str, gate_id: str) -> None:
        self._match(operation_id)
        self._require(self.state is OperationState.RUNNING and type(gate_id) is str
                      and bool(gate_id) and gate_id not in self._seen_gates, "unexpected operation_ready")
        self._seen_gates.add(gate_id)
        self.gate_id = gate_id
        self.state = OperationState.GATED

    def continue_operation(self, operation_id: str, gate_id: str) -> None:
        self._match(operation_id)
        self._require(self.session.can_record and self.state is OperationState.GATED
                      and gate_id == self.gate_id, "invalid continue request")
        self.state = OperationState.CONTINUING

    def continued(self, operation_id: str, gate_id: str) -> None:
        self._match(operation_id)
        self._require(self.state is OperationState.CONTINUING and gate_id == self.gate_id,
                      "unexpected operation_continued")
        self.gate_id = None
        self.state = OperationState.RUNNING

    def gate_interrupted(self, operation_id: str, gate_id: str) -> None:
        self._match(operation_id)
        self._require(self.state in {OperationState.GATED, OperationState.CONTINUING}
                      and gate_id == self.gate_id, "unexpected operation_gate_interrupted")
        self.gate_id = None
        self.state = OperationState.RUNNING

    def finalize(self, operation_id: str) -> None:
        self._match(operation_id)
        self._require(self.state in {OperationState.RUNNING, OperationState.GATED,
                                     OperationState.CONTINUING}, "invalid finalize request")
        self.state = OperationState.FINALIZING

    def terminal_result(self, operation_id: str, kind: str, *, shell_ended: bool = False) -> None:
        self._match(operation_id)
        self._require(type(shell_ended) is bool and type(kind) is str,
                      "invalid terminal result type")
        allowed = {
            OperationState.STARTING: {"failed"},
            OperationState.RUNNING: {"completed", "failed"},
            OperationState.GATED: {"failed"},
            OperationState.CONTINUING: {"failed"},
            OperationState.FINALIZING: {"finalized", "completed", "failed"},
        }
        kinds = allowed.get(self.state, set())
        self._require(kind in kinds and (kind != "completed" or self.gate_id is None),
                      "unexpected terminal operation result")
        self.shell_ended = shell_ended
        self.active_id = None
        self.gate_id = None
        self.state = OperationState.IDLE

    def shutdown(self, reason: str) -> None:
        self._require(self.state is OperationState.IDLE and not self.shell_ended,
                      "shutdown requires an idle live shell")
        self._require(type(reason) is str and bool(reason), "invalid shutdown reason")
        self._shutdown_reason = reason
        self.state = OperationState.SHUTDOWN_SENT

    def draining(self, reason: str) -> None:
        valid = ((self.state is OperationState.IDLE and self.shell_ended and reason == "shell_ended")
                 or (self.state is OperationState.SHUTDOWN_SENT and reason == self._shutdown_reason)
                 or (self.state is OperationState.IDLE and reason == "shell_ended"))
        self._require(valid, "unexpected Envoy drain")
        self._drain_reason = reason
        self.state = OperationState.DRAINING

    def closed(self, reason: str) -> None:
        self._require(self.state is OperationState.DRAINING and not self._closed_seen
                      and reason == self._drain_reason, "unexpected Envoy closed")
        self._closed_seen = True
        self._finish_close()

    def terminal_eof(self) -> None:
        self._require(self.state is OperationState.DRAINING and not self._terminal_eof,
                      "unexpected terminal EOF")
        self._terminal_eof = True
        self._finish_close()

    def _finish_close(self) -> None:
        if self._closed_seen and self._terminal_eof:
            self.state = OperationState.CLOSED
