"""Strict controller implementation of OmegaFlow Envoy telemetry v1."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

SCHEMA = "omegaflow-envoy-telemetry-v1"
MAX_FRAME_BYTES = 1 << 20
MAX_SOURCE_BYTES = 786_432
MAX_SEQUENCE = 2**63 - 1
MAX_OFFSET = 2**63 - 1
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CODE_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


class EnvoyProtocolError(ValueError):
    """An Envoy telemetry frame or state transition is invalid."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnvoyProtocolError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _json_object(payload: bytes | str) -> dict[str, Any]:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not raw or len(raw) > MAX_FRAME_BYTES or b"\x00" in raw:
        raise EnvoyProtocolError("JSON message is empty, oversized, or contains NUL")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                EnvoyProtocolError(f"non-finite JSON number {value!r}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvoyProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise EnvoyProtocolError("JSON message must be an object")
    return value


def _fields(
    value: dict[str, Any],
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise EnvoyProtocolError(f"missing field {sorted(missing)[0]!r}")
    if unknown:
        raise EnvoyProtocolError(f"unknown field {sorted(unknown)[0]!r}")


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise EnvoyProtocolError(f"{name} must be a non-empty string without NUL")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise EnvoyProtocolError(f"{name} must be between {minimum} and {maximum}")
    return value


def _identifier(value: Any, name: str) -> str:
    result = _string(value, name)
    if not _ID_RE.fullmatch(result):
        raise EnvoyProtocolError(f"{name} has invalid identifier syntax")
    return result


def _code(value: Any) -> str:
    result = _string(value, "code")
    if not _CODE_RE.fullmatch(result):
        raise EnvoyProtocolError("code has invalid syntax")
    return result


def _cwd(value: Any) -> str:
    result = _string(value, "cwd")
    if len(result.encode("utf-8")) > 4096 or not result.startswith("/"):
        raise EnvoyProtocolError("cwd must be a bounded absolute Linux path")
    return result


def _reason(value: Any) -> str:
    result = _string(value, "reason")
    if len(result.encode("utf-8")) > 256:
        raise EnvoyProtocolError("reason exceeds 256 UTF-8 bytes")
    return result


@dataclass(frozen=True)
class EnvoyReady:
    seq: int
    envoy_pid: int
    shell_pid: int
    cwd: str
    columns: int
    rows: int
    type: Literal["ready"] = "ready"


@dataclass(frozen=True)
class OperationStarted:
    seq: int
    operation_id: str
    output_start: int
    type: Literal["operation_started"] = "operation_started"


@dataclass(frozen=True)
class OperationReady:
    seq: int
    operation_id: str
    gate_id: str
    output_through: int
    type: Literal["operation_ready"] = "operation_ready"


@dataclass(frozen=True)
class OperationContinued:
    seq: int
    operation_id: str
    gate_id: str
    output_through: int
    type: Literal["operation_continued"] = "operation_continued"


@dataclass(frozen=True)
class OperationCompleted:
    seq: int
    operation_id: str
    status: int
    cwd: str
    output_start: int
    output_through: int
    type: Literal["operation_completed"] = "operation_completed"


@dataclass(frozen=True)
class OperationCancelled:
    seq: int
    operation_id: str
    status: int
    cwd: str
    reason: str
    output_start: int
    output_through: int
    type: Literal["operation_cancelled"] = "operation_cancelled"


@dataclass(frozen=True)
class OperationFailed:
    seq: int
    operation_id: str
    code: str
    message: str
    cwd: str
    output_start: int
    output_through: int
    type: Literal["operation_failed"] = "operation_failed"


@dataclass(frozen=True)
class ResizeApplied:
    seq: int
    columns: int
    rows: int
    type: Literal["resize_applied"] = "resize_applied"


@dataclass(frozen=True)
class EnvoyDiagnostic:
    seq: int
    severity: Literal["info", "warning", "error", "fatal"]
    code: str
    message: str
    operation_id: str | None = None
    type: Literal["diagnostic"] = "diagnostic"


@dataclass(frozen=True)
class Draining:
    seq: int
    reason: str
    output_through: int
    type: Literal["draining"] = "draining"


@dataclass(frozen=True)
class Closed:
    seq: int
    reason: str
    output_through: int
    type: Literal["closed"] = "closed"


EnvoyEvent: TypeAlias = (
    EnvoyReady
    | OperationStarted
    | OperationReady
    | OperationContinued
    | OperationCompleted
    | OperationCancelled
    | OperationFailed
    | ResizeApplied
    | EnvoyDiagnostic
    | Draining
    | Closed
)


def _base(
    value: dict[str, Any],
    required: set[str],
    optional: set[str] | None = None,
) -> tuple[str, int]:
    _fields(value, {"schema", "type", "seq"} | required, optional)
    if value["schema"] != SCHEMA:
        raise EnvoyProtocolError("unsupported Envoy telemetry schema")
    return _string(value["type"], "type"), _integer(value["seq"], "seq", 1, MAX_SEQUENCE)


def _range(value: dict[str, Any], *, with_start: bool) -> tuple[int, int]:
    start = _integer(value.get("output_start", 0), "output_start", 0, MAX_OFFSET)
    through = _integer(value["output_through"], "output_through", 0, MAX_OFFSET)
    if with_start and through < start:
        raise EnvoyProtocolError("output range regresses")
    return start, through


def decode_envoy_event(payload: bytes | str) -> EnvoyEvent:
    value = _json_object(payload)
    kind = value.get("type")
    if kind == "ready":
        _, seq = _base(value, {"envoy_pid", "shell_pid", "cwd", "columns", "rows"})
        return EnvoyReady(
            seq,
            _integer(value["envoy_pid"], "envoy_pid", 1, 2**31 - 1),
            _integer(value["shell_pid"], "shell_pid", 1, 2**31 - 1),
            _cwd(value["cwd"]),
            _integer(value["columns"], "columns", 1, 1000),
            _integer(value["rows"], "rows", 1, 1000),
        )
    if kind == "operation_started":
        _, seq = _base(value, {"operation_id", "output_start"})
        return OperationStarted(
            seq,
            _identifier(value["operation_id"], "operation_id"),
            _integer(value["output_start"], "output_start", 0, MAX_OFFSET),
        )
    if kind in {"operation_ready", "operation_continued"}:
        _, seq = _base(value, {"operation_id", "gate_id", "output_through"})
        args = (
            seq,
            _identifier(value["operation_id"], "operation_id"),
            _identifier(value["gate_id"], "gate_id"),
            _integer(value["output_through"], "output_through", 0, MAX_OFFSET),
        )
        return OperationReady(*args) if kind == "operation_ready" else OperationContinued(*args)
    if kind in {"operation_completed", "operation_cancelled"}:
        required = {"operation_id", "status", "cwd", "output_start", "output_through"}
        if kind == "operation_cancelled":
            required.add("reason")
        _, seq = _base(value, required)
        start, through = _range(value, with_start=True)
        common = (
            seq,
            _identifier(value["operation_id"], "operation_id"),
            _integer(value["status"], "status", 0, 255),
            _cwd(value["cwd"]),
        )
        if kind == "operation_completed":
            return OperationCompleted(*common, start, through)
        return OperationCancelled(*common, _reason(value["reason"]), start, through)
    if kind == "operation_failed":
        _, seq = _base(
            value,
            {"operation_id", "code", "message", "cwd", "output_start", "output_through"},
        )
        start, through = _range(value, with_start=True)
        message = _string(value["message"], "message")
        if len(message.encode("utf-8")) > 4096:
            raise EnvoyProtocolError("diagnostic message exceeds 4096 UTF-8 bytes")
        return OperationFailed(
            seq,
            _identifier(value["operation_id"], "operation_id"),
            _code(value["code"]),
            message,
            _cwd(value["cwd"]),
            start,
            through,
        )
    if kind == "resize_applied":
        _, seq = _base(value, {"columns", "rows"})
        return ResizeApplied(
            seq,
            _integer(value["columns"], "columns", 1, 1000),
            _integer(value["rows"], "rows", 1, 1000),
        )
    if kind == "diagnostic":
        _, seq = _base(value, {"severity", "code", "message"}, {"operation_id"})
        severity = value["severity"]
        if severity not in {"info", "warning", "error", "fatal"}:
            raise EnvoyProtocolError("unsupported diagnostic severity")
        message = _string(value["message"], "message")
        if len(message.encode("utf-8")) > 4096:
            raise EnvoyProtocolError("diagnostic message exceeds 4096 UTF-8 bytes")
        operation_id = value.get("operation_id")
        return EnvoyDiagnostic(
            seq,
            severity,
            _code(value["code"]),
            message,
            None if operation_id is None else _identifier(operation_id, "operation_id"),
        )
    if kind in {"draining", "closed"}:
        _, seq = _base(value, {"reason", "output_through"})
        args = (
            seq,
            _reason(value["reason"]),
            _integer(value["output_through"], "output_through", 0, MAX_OFFSET),
        )
        return Draining(*args) if kind == "draining" else Closed(*args)
    raise EnvoyProtocolError(f"unsupported Envoy event type {kind!r}")


class EnvoyStreamDecoder:
    """Incrementally decode LF-framed telemetry without losing fragmentation."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> tuple[EnvoyEvent, ...]:
        if b"\x00" in data:
            raise EnvoyProtocolError("telemetry contains NUL")
        self._buffer.extend(data)
        if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer:
            raise EnvoyProtocolError("unterminated telemetry frame is oversized")
        result: list[EnvoyEvent] = []
        while True:
            try:
                index = self._buffer.index(0x0A)
            except ValueError:
                break
            frame = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            if frame.endswith(b"\r"):
                raise EnvoyProtocolError("telemetry uses CRLF")
            if len(frame) + 1 > MAX_FRAME_BYTES:
                raise EnvoyProtocolError("telemetry frame is oversized")
            result.append(decode_envoy_event(frame))
        return tuple(result)

    def finish(self) -> None:
        if self._buffer:
            raise EnvoyProtocolError("telemetry closed mid-frame")

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)


class EnvoyClientState:
    """Encode controller requests and validate the joint v1 state machine."""

    def __init__(self) -> None:
        self.phase = "initial"
        self.next_request_seq = 1
        self.next_event_seq = 1
        self.operation_id: str | None = None
        self.gate_id: str | None = None
        self.used_gate_ids: set[str] = set()
        self.cancel_reason: str | None = None
        self.shutdown_reason: str | None = None
        self.pending_resize: tuple[int, int] | None = None
        self.output_through = 0
        self.operation_start = 0

    def hello(self, session_id: str) -> bytes:
        self._require("initial")
        self.phase = "hello-sent"
        return self._request("hello", session_id=_identifier(session_id, "session_id"))

    def execute(self, operation_id: str, source: str) -> bytes:
        self._require("idle")
        operation_id = _identifier(operation_id, "operation_id")
        if (
            not isinstance(source, str)
            or not 1 <= len(source.encode("utf-8")) <= MAX_SOURCE_BYTES
            or "\x00" in source
        ):
            raise EnvoyProtocolError(
                "source must contain 1 through 786432 UTF-8 bytes without NUL"
            )
        self.operation_id = operation_id
        self.used_gate_ids.clear()
        self.phase = "starting"
        return self._request("execute", operation_id=operation_id, source=source)

    def continue_gate(self, operation_id: str, gate_id: str) -> bytes:
        self._require("gated")
        self._match_operation(operation_id)
        if gate_id != self.gate_id:
            raise EnvoyProtocolError("continue does not match the active gate")
        self.phase = "continuing"
        return self._request("continue", operation_id=operation_id, gate_id=gate_id)

    def cancel(self, operation_id: str, reason: str) -> bytes:
        if self.phase not in {"running", "gated", "continuing"}:
            raise EnvoyProtocolError(f"cancel is invalid in phase {self.phase}")
        self._match_operation(operation_id)
        self.cancel_reason = _reason(reason)
        self.phase = "cancelling"
        return self._request("cancel", operation_id=operation_id, reason=self.cancel_reason)

    def resize(self, columns: int, rows: int) -> bytes:
        if (
            self.phase not in {"idle", "starting", "running", "gated"}
            or self.pending_resize is not None
        ):
            raise EnvoyProtocolError(f"resize is invalid in phase {self.phase}")
        size = (
            _integer(columns, "columns", 1, 1000),
            _integer(rows, "rows", 1, 1000),
        )
        self.pending_resize = size
        return self._request("resize", columns=size[0], rows=size[1])

    def shutdown(self, reason: str = "capture-complete") -> bytes:
        self._require("idle")
        if self.pending_resize is not None:
            raise EnvoyProtocolError("shutdown is invalid while resize is pending")
        self.shutdown_reason = _reason(reason)
        self.phase = "shutdown-sent"
        return self._request("shutdown", reason=self.shutdown_reason)

    def accept(self, event: EnvoyEvent) -> None:
        if event.seq != self.next_event_seq:
            raise EnvoyProtocolError(
                f"Envoy sequence {event.seq} does not match {self.next_event_seq}"
            )
        if isinstance(event, EnvoyReady):
            self._require("hello-sent")
            self.phase = "idle"
        elif isinstance(event, OperationStarted):
            self._require("starting")
            self._match_operation(event.operation_id)
            if event.output_start < self.output_through:
                raise EnvoyProtocolError("operation output start regresses")
            self.operation_start = event.output_start
            self.phase = "running"
        elif isinstance(event, OperationReady):
            self._require("running")
            self._match_operation(event.operation_id)
            if event.gate_id in self.used_gate_ids:
                raise EnvoyProtocolError("operation reused a gate id")
            self._barrier(event.output_through)
            self.used_gate_ids.add(event.gate_id)
            self.gate_id = event.gate_id
            self.phase = "gated"
        elif isinstance(event, OperationContinued):
            self._require("continuing")
            self._match_operation(event.operation_id)
            if event.gate_id != self.gate_id:
                raise EnvoyProtocolError("continued event does not match active gate")
            self._barrier(event.output_through)
            self.gate_id = None
            self.phase = "running"
        elif isinstance(event, OperationCompleted):
            if self.phase != "running":
                raise EnvoyProtocolError(f"completion is invalid in phase {self.phase}")
            self._finish_operation(event.operation_id, event.output_start, event.output_through)
        elif isinstance(event, OperationCancelled):
            self._require("cancelling")
            if event.reason != self.cancel_reason:
                raise EnvoyProtocolError("cancellation reason does not match request")
            self._finish_operation(event.operation_id, event.output_start, event.output_through)
        elif isinstance(event, OperationFailed):
            if self.phase not in {"starting", "running", "gated", "continuing", "cancelling"}:
                raise EnvoyProtocolError(f"operation failure is invalid in phase {self.phase}")
            if self.phase == "starting":
                if event.output_start < self.output_through:
                    raise EnvoyProtocolError("operation output start regresses")
                self.operation_start = event.output_start
            self._finish_operation(event.operation_id, event.output_start, event.output_through)
        elif isinstance(event, ResizeApplied):
            if self.pending_resize != (event.columns, event.rows):
                raise EnvoyProtocolError("resize acknowledgement does not match request")
            self.pending_resize = None
        elif isinstance(event, EnvoyDiagnostic):
            if self.phase in {"initial", "closed"}:
                raise EnvoyProtocolError(f"diagnostic is invalid in phase {self.phase}")
            if event.operation_id is not None:
                self._match_operation(event.operation_id)
        elif isinstance(event, Draining):
            self._require("shutdown-sent")
            if event.reason != self.shutdown_reason:
                raise EnvoyProtocolError("draining reason does not match shutdown")
            self._barrier(event.output_through)
            self.phase = "draining"
        elif isinstance(event, Closed):
            self._require("draining")
            if event.reason != "shutdown":
                raise EnvoyProtocolError("closed reason must be shutdown")
            self._barrier(event.output_through)
            self.phase = "closed"
        else:  # pragma: no cover - union exhaustiveness
            raise EnvoyProtocolError(f"unsupported event {type(event).__name__}")
        self.next_event_seq += 1

    def finish(self) -> None:
        if self.phase != "closed":
            raise EnvoyProtocolError(
                f"telemetry closed before closed event (phase {self.phase})"
            )

    def _request(self, kind: str, **fields: Any) -> bytes:
        body = {"schema": SCHEMA, "type": kind, "seq": self.next_request_seq, **fields}
        self.next_request_seq += 1
        return json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\n"

    def _require(self, phase: str) -> None:
        if self.phase != phase:
            raise EnvoyProtocolError(f"message is invalid in phase {self.phase}; expected {phase}")

    def _match_operation(self, operation_id: str) -> None:
        if operation_id != self.operation_id:
            raise EnvoyProtocolError("event does not match the active operation")

    def _barrier(self, offset: int) -> None:
        if offset < self.output_through:
            raise EnvoyProtocolError("output barrier regresses")
        self.output_through = offset

    def _finish_operation(self, operation_id: str, start: int, through: int) -> None:
        self._match_operation(operation_id)
        if start != self.operation_start or through < start:
            raise EnvoyProtocolError("completion output range does not match operation")
        self._barrier(through)
        self.operation_id = None
        self.gate_id = None
        self.cancel_reason = None
        self.phase = "idle"
