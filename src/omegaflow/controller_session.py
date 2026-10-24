"""Retained controller decisions at the public Reploy client boundary.

The caller executes effects and reports observations serially. This module
starts no process and opens no socket. C3 supplies the actual channel adapter.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import BinaryIO, Mapping

from .controller_lifecycle import (
    ControllerEffect, ControllerLifecycleError, OperationLifecycle,
    ReployLifecycle, ReployState,
)
from .reploy_codecs import decode_event


@dataclass(frozen=True)
class ControllerFailure:
    """The first local failure, without replacing retained Reploy diagnostics."""

    code: str
    message: str
    operation_id: str | None
    gate_id: str | None
    reploy_state: str


def _write_all(sink: BinaryIO, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        count = sink.write(remaining)
        if type(count) is not int or not 0 < count <= len(remaining):
            raise OSError("evidence sink did not write a valid byte count")
        remaining = remaining[count:]
    sink.flush()


class ControllerSession:
    """Compose the existing core with mandatory, separate evidence sinks.

    Sinks are private run artifacts owned and closed by the caller. Complete
    selected public events and stderr are streamed, not accumulated in memory.
    The explicitly supplied tail bound affects only the in-memory stderr view.
    No property in this class establishes authoritative host-run success.
    """

    def __init__(self, *, event_sink: BinaryIO, stderr_sink: BinaryIO,
                 stderr_tail_limit: int, context: Mapping[str, str] | None = None) -> None:
        if type(stderr_tail_limit) is not int or stderr_tail_limit < 0:
            raise ValueError("stderr tail limit must be a nonnegative integer")
        if event_sink is stderr_sink:
            raise ValueError("public event and client stderr sinks must be separate")
        self.lifecycle = ReployLifecycle()
        self.operation = OperationLifecycle(self.lifecycle)
        self._event_sink = event_sink
        self._stderr_sink = stderr_sink
        self._stderr_tail_limit = stderr_tail_limit
        self._stderr_tail = b""
        self._context = dict(context or {})
        self._failure: ControllerFailure | None = None
        self._started = False
        self._attachment_connected = False
        self._attachment_end_seen = False
        self._attachment_end_reported = False
        self._request_failed = False
        self._result_retained = False
        self._closed = False
        self.stderr_bytes_observed = 0

    @property
    def failure(self) -> ControllerFailure | None:
        return self._failure

    @property
    def context(self) -> dict[str, str]:
        return deepcopy(self._context)

    @property
    def stderr_tail(self) -> bytes:
        return self._stderr_tail

    @property
    def result(self) -> dict | None:
        return self.lifecycle.result

    @property
    def closed(self) -> bool:
        """The client closed cleanly; this does not establish capture success."""
        return self._closed

    @property
    def can_record(self) -> bool:
        return not self._closed and self._failure is None and self.lifecycle.can_record

    def start(self) -> tuple[ControllerEffect, ...]:
        if self._started or self._closed:
            return self.fail("client-start", "duplicate client start", terminate=False)
        self._started = True
        return (ControllerEffect("launch-client", ("reploy-session-client", "client")),)

    def _terminate_if_possible(self) -> tuple[ControllerEffect, ...]:
        if self._request_failed or self.lifecycle.state is not ReployState.ACTIVE:
            return ()
        try:
            return self.lifecycle.request_termination()
        except ControllerLifecycleError:
            # The original failure stays precise. Missing public authority must
            # never be replaced by an unauthorized request.
            return ()

    def fail(self, code: str, message: str, *, terminate: bool = True) -> tuple[ControllerEffect, ...]:
        """Retain a capture failure without abandoning valid public teardown."""
        if type(code) is not str or not code or type(message) is not str:
            raise ValueError("failure code and message must be strings")
        effects: tuple[ControllerEffect, ...] = ()
        if self._failure is None:
            self._failure = ControllerFailure(code, message, self.operation.active_id,
                                              self.operation.gate_id, self.lifecycle.state.value)
            effects = (ControllerEffect("retain-partial-artifacts"),)
        self.lifecycle.stop_recording()
        return effects + (self._terminate_if_possible() if terminate else ())

    def _attachment_end(self) -> tuple[ControllerEffect, ...]:
        if (not self._attachment_end_seen or self._attachment_end_reported
                or self.lifecycle.state not in {ReployState.TERMINATING, ReployState.OUTPUTS_FINALIZED}):
            return ()
        self._attachment_end_reported = True
        if self._attachment_connected:
            return self.lifecycle.attachment_ended()
        return self.lifecycle.attachment_unavailable()

    def on_event(self, line: bytes, *, now: float) -> tuple[ControllerEffect, ...]:
        """Retain valid diagnostics/results before interpreting lifecycle order."""
        try:
            event = decode_event(line)
        except ValueError as error:
            return self.fail("client-schema", str(error))
        kind = event["type"]
        if kind in {"diagnostic", "client-error", "terminated"}:
            try:
                _write_all(self._event_sink, line)
            except (OSError, ValueError, TypeError) as error:
                return self.fail("event-retention", str(error))
        effects: tuple[ControllerEffect, ...] = ()
        if kind == "client-error":
            effects += self.fail(event["code"], event["message"], terminate=False)
        try:
            effects += self.lifecycle.on_event(line, now=now)
            effects += self._attachment_end()
        except ControllerLifecycleError as error:
            return effects + self.fail("client-lifecycle", str(error), terminate=False)
        if kind == "workload-outputs-finalized" and event["status"] == "failed":
            effects += self.fail("output-finalization", event.get("reason", ""), terminate=False)
        if kind == "terminated":
            self._result_retained = True
            result = event["result"]
            successful_cause = result["cause"] == "controller-terminate" or (
                result["cause"] == "workload-exit"
                and result["workload_status"]["kind"] == "exited"
                and result["workload_status"]["code"] == 0)
            if (not successful_cause
                    or result["workload_output_finalization_status"]["kind"] != "drained"
                    or result["runtime_observation_status"]["kind"] != "maintained"
                    or result["controller_finalization_status"]["kind"] != "completed"
                    or result["cleanup_status"]["kind"] != "succeeded"
                    or result["recovery_action"] != "none"):
                effects += self.fail("session-result", "Reploy retained a failed session outcome",
                                     terminate=False)
            if not self._request_failed:
                effects += self.lifecycle.acknowledge_terminated()
        elif self._failure is not None:
            effects += self._terminate_if_possible()
        return effects

    def attachment_connected(self, *, now: float) -> tuple[ControllerEffect, ...]:
        if self._attachment_end_seen:
            return self.fail("attachment-order", "attachment connected after its end")
        try:
            effects = self.lifecycle.attachment_connected(now=now)
        except ControllerLifecycleError as error:
            return self.fail("attachment-connect", str(error))
        self._attachment_connected = True
        return effects

    def attachment_ended(self, exit_code: int) -> tuple[ControllerEffect, ...]:
        if type(exit_code) is not int or self._attachment_end_seen:
            return self.fail("attachment-order", "invalid or duplicate attachment end")
        self._attachment_end_seen = True
        effects: tuple[ControllerEffect, ...] = ()
        if exit_code != 0:
            effects += self.fail("attachment-exit", f"attachment exited with status {exit_code}")
        elif self.lifecycle.state not in {ReployState.TERMINATING, ReployState.OUTPUTS_FINALIZED}:
            # The attachment can close before the separate JSONL reader observes
            # termination. Retain that fact without inferring a failed capture.
            self.lifecycle.stop_recording()
        try:
            return effects + self._attachment_end()
        except ControllerLifecycleError as error:
            return effects + self.fail("attachment-order", str(error))

    def attachment_failed(self, message: str) -> tuple[ControllerEffect, ...]:
        """Report failed launch/connect without inventing an attachment EOF."""
        if self._attachment_connected or self._attachment_end_seen:
            return self.fail("attachment-order", "unexpected attachment launch failure")
        self._attachment_end_seen = True
        effects = self.fail("attachment-start", message)
        try:
            return effects + self._attachment_end()
        except ControllerLifecycleError as error:
            return effects + self.fail("attachment-order", str(error))

    def stderr(self, data: bytes) -> tuple[ControllerEffect, ...]:
        if type(data) is not bytes:
            raise ValueError("client stderr must be bytes")
        self.stderr_bytes_observed += len(data)
        bound = self._stderr_tail_limit
        if bound:
            self._stderr_tail = (self._stderr_tail + data[-bound:])[-bound:]
        try:
            _write_all(self._stderr_sink, data)
        except (OSError, ValueError, TypeError) as error:
            return self.fail("stderr-retention", str(error))
        return ()

    def artifacts_finalized(self, *, error: str | None = None) -> tuple[ControllerEffect, ...]:
        effects = self.fail("artifact-finalization", error, terminate=False) if error is not None else ()
        try:
            requests = self.lifecycle.artifacts_finalized()
            return effects + (() if self._request_failed else requests)
        except ControllerLifecycleError as failure:
            return effects + self.fail("artifact-order", str(failure))

    def request_failed(self, message: str) -> tuple[ControllerEffect, ...]:
        """A broken public writer cannot carry another terminate or acknowledgement."""
        self._request_failed = True
        return self.fail("client-request", message, terminate=False)

    def cancel(self, operation_id: str) -> tuple[ControllerEffect, ...]:
        try:
            self.operation.cancel(operation_id)
        except ControllerLifecycleError as error:
            return self.fail("operation-cancel", str(error))
        return (ControllerEffect("cancel-operation", (operation_id,)),)

    def client_closed(self, exit_code: int) -> tuple[ControllerEffect, ...]:
        """The caller reports JSONL EOF and a reaped client, after stderr drain."""
        if self._request_failed or not self._result_retained:
            return self.fail("client-close", "client closed without retained, acknowledged result",
                             terminate=False)
        try:
            self.lifecycle.broker_closed(exit_code)
        except ControllerLifecycleError as error:
            return self.fail("client-close", str(error), terminate=False)
        self._closed = True
        return ()
