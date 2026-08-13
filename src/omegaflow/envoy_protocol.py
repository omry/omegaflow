"""Strict protocol models for the OmegaFlow workload Envoy.

The controller owns these models.  Reploy transports neither protocol and does
not interpret any message defined here.
"""

from __future__ import annotations

import codecs
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal, TypeAlias


TELEMETRY_SCHEMA = "omegaflow-envoy-telemetry-v1"
AWSH_SCHEMA = "awsh-v1"
MAX_TELEMETRY_FRAME_BYTES = 1_048_576
MAX_AWSH_FRAME_BYTES = 1_048_576
MAX_OPERATION_SOURCE_BYTES = 786_432
MAX_DIAGNOSTIC_BYTES = 4_096
MAX_REASON_BYTES = 256
MAX_CWD_BYTES = 4_096
MAX_SEQUENCE = 2**63 - 1
MAX_OUTPUT_OFFSET = 2**63 - 1
MAX_PID = 2**31 - 1
MIN_COLUMNS = 1
MAX_COLUMNS = 1_000
MIN_ROWS = 1
MAX_ROWS = 1_000

CONNECT_TIMEOUT_SECONDS = 10.0
HANDSHAKE_TIMEOUT_SECONDS = 10.0
WRITE_TIMEOUT_SECONDS = 5.0
CANCEL_GRACE_SECONDS = 5.0
FINAL_DRAIN_TIMEOUT_SECONDS = 5.0

FIXED_BASH_EXECUTABLE = "/bin/bash"
FIXED_BASH_ARGUMENTS = ("--noprofile", "--norc")
RESERVED_BASH_ENVIRONMENT = frozenset(
    {
        "AWSH_BASH",
        "BASH_COMPAT",
        "BASHOPTS",
        "BASH_ENV",
        "BASH_XTRACEFD",
        "CDPATH",
        "ENV",
        "GLOBIGNORE",
        "POSIXLY_CORRECT",
        "PROMPT_COMMAND",
        "PS0",
        "PS1",
        "PS2",
        "PS3",
        "PS4",
        "SHELLOPTS",
        "TMOUT",
    }
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CODE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class EnvoyProtocolError(ValueError):
    """A stable fail-closed protocol error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class Hello:
    seq: int
    session_id: str


@dataclass(frozen=True, slots=True)
class Execute:
    seq: int
    operation_id: str
    source: str


@dataclass(frozen=True, slots=True)
class Continue:
    seq: int
    operation_id: str
    gate_id: str


@dataclass(frozen=True, slots=True)
class Cancel:
    seq: int
    operation_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class Resize:
    seq: int
    columns: int
    rows: int


@dataclass(frozen=True, slots=True)
class Shutdown:
    seq: int
    reason: str


ControllerMessage: TypeAlias = Hello | Execute | Continue | Cancel | Resize | Shutdown


@dataclass(frozen=True, slots=True)
class Ready:
    seq: int
    envoy_pid: int
    shell_pid: int
    cwd: str
    columns: int
    rows: int


@dataclass(frozen=True, slots=True)
class OperationStarted:
    seq: int
    operation_id: str
    output_start: int


@dataclass(frozen=True, slots=True)
class OperationReady:
    seq: int
    operation_id: str
    gate_id: str
    output_through: int


@dataclass(frozen=True, slots=True)
class OperationContinued:
    seq: int
    operation_id: str
    gate_id: str
    output_through: int


@dataclass(frozen=True, slots=True)
class OperationCompleted:
    seq: int
    operation_id: str
    status: int
    cwd: str
    output_start: int
    output_through: int


@dataclass(frozen=True, slots=True)
class OperationCancelled:
    seq: int
    operation_id: str
    status: int
    cwd: str
    reason: str
    output_start: int
    output_through: int


@dataclass(frozen=True, slots=True)
class OperationFailed:
    seq: int
    operation_id: str
    code: str
    message: str
    cwd: str
    output_start: int
    output_through: int


@dataclass(frozen=True, slots=True)
class ResizeApplied:
    seq: int
    columns: int
    rows: int


@dataclass(frozen=True, slots=True)
class Diagnostic:
    seq: int
    severity: str
    code: str
    message: str
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class Draining:
    seq: int
    reason: str
    output_through: int


@dataclass(frozen=True, slots=True)
class Closed:
    seq: int
    reason: str
    output_through: int


EnvoyMessage: TypeAlias = (
    Ready
    | OperationStarted
    | OperationReady
    | OperationContinued
    | OperationCompleted
    | OperationCancelled
    | OperationFailed
    | ResizeApplied
    | Diagnostic
    | Draining
    | Closed
)


@dataclass(frozen=True, slots=True)
class AwshExecute:
    operation_id: str
    source: str


@dataclass(frozen=True, slots=True)
class AwshContinue:
    operation_id: str
    gate_id: str


@dataclass(frozen=True, slots=True)
class AwshCancel:
    operation_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class AwshShutdown:
    pass


AwshRequest: TypeAlias = AwshExecute | AwshContinue | AwshCancel | AwshShutdown


@dataclass(frozen=True, slots=True)
class AwshReady:
    shell_pid: int
    cwd: str


@dataclass(frozen=True, slots=True)
class AwshStarted:
    operation_id: str


@dataclass(frozen=True, slots=True)
class AwshGateReady:
    operation_id: str
    gate_id: str


@dataclass(frozen=True, slots=True)
class AwshGateContinued:
    operation_id: str
    gate_id: str


@dataclass(frozen=True, slots=True)
class AwshCompleted:
    operation_id: str
    status: int
    cwd: str


@dataclass(frozen=True, slots=True)
class AwshProtocolError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AwshClosed:
    reason: str
    cwd: str


AwshResult: TypeAlias = (
    AwshReady
    | AwshStarted
    | AwshGateReady
    | AwshGateContinued
    | AwshCompleted
    | AwshProtocolError
    | AwshClosed
)


_CONTROLLER_TYPES: dict[str, type[ControllerMessage]] = {
    "hello": Hello,
    "execute": Execute,
    "continue": Continue,
    "cancel": Cancel,
    "resize": Resize,
    "shutdown": Shutdown,
}
_ENVOY_TYPES: dict[str, type[EnvoyMessage]] = {
    "ready": Ready,
    "operation_started": OperationStarted,
    "operation_ready": OperationReady,
    "operation_continued": OperationContinued,
    "operation_completed": OperationCompleted,
    "operation_cancelled": OperationCancelled,
    "operation_failed": OperationFailed,
    "resize_applied": ResizeApplied,
    "diagnostic": Diagnostic,
    "draining": Draining,
    "closed": Closed,
}
_AWSH_REQUEST_TYPES: dict[str, type[AwshRequest]] = {
    "execute": AwshExecute,
    "continue": AwshContinue,
    "cancel": AwshCancel,
    "shutdown": AwshShutdown,
}
_AWSH_RESULT_TYPES: dict[str, type[AwshResult]] = {
    "ready": AwshReady,
    "started": AwshStarted,
    "gate_ready": AwshGateReady,
    "gate_continued": AwshGateContinued,
    "completed": AwshCompleted,
    "protocol_error": AwshProtocolError,
    "closed": AwshClosed,
}


def _reverse(registry: dict[str, type[object]]) -> dict[type[object], str]:
    return {model: message_type for message_type, model in registry.items()}


_CONTROLLER_MODELS = _reverse(_CONTROLLER_TYPES)  # type: ignore[arg-type]
_ENVOY_MODELS = _reverse(_ENVOY_TYPES)  # type: ignore[arg-type]
_AWSH_REQUEST_MODELS = _reverse(_AWSH_REQUEST_TYPES)  # type: ignore[arg-type]
_AWSH_RESULT_MODELS = _reverse(_AWSH_RESULT_TYPES)  # type: ignore[arg-type]


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EnvoyProtocolError("duplicate-field", f"duplicate field {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise EnvoyProtocolError("invalid-json", f"non-finite number {value!r}")


def _load_jsonl_frame(frame: bytes) -> dict[str, object]:
    if not isinstance(frame, bytes):
        raise TypeError("telemetry frame must be bytes")
    if len(frame) > MAX_TELEMETRY_FRAME_BYTES:
        raise EnvoyProtocolError("frame-too-large", "telemetry frame exceeds limit")
    if not frame.endswith(b"\n") or frame.count(b"\n") != 1:
        raise EnvoyProtocolError("invalid-framing", "frame must end with one LF")
    body = frame[:-1]
    if not body or b"\r" in body or b"\0" in body:
        raise EnvoyProtocolError("invalid-framing", "frame body is empty or unsafe")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EnvoyProtocolError("invalid-utf8", "telemetry is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except EnvoyProtocolError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise EnvoyProtocolError("invalid-json", "telemetry is not valid JSON") from exc
    if not isinstance(value, dict):
        raise EnvoyProtocolError("invalid-message", "telemetry must be an object")
    return value


def _require_exact_fields(
    payload: dict[str, object],
    model: type[object],
    *,
    optional: frozenset[str] = frozenset(),
) -> None:
    model_fields = frozenset(model.__dataclass_fields__)  # type: ignore[attr-defined]
    allowed = model_fields | {"schema", "type"}
    required = allowed - optional
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - allowed)
    if missing:
        raise EnvoyProtocolError("missing-field", ", ".join(missing))
    if unknown:
        raise EnvoyProtocolError("unknown-field", ", ".join(unknown))


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvoyProtocolError("invalid-field", f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise EnvoyProtocolError("invalid-field", f"{field} is out of range")
    return value


def _string(value: object, field: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise EnvoyProtocolError("invalid-field", f"{field} must be a string")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise EnvoyProtocolError(
            "invalid-field", f"{field} is not valid Unicode"
        ) from exc
    if "\0" in value or len(encoded) > maximum:
        raise EnvoyProtocolError("invalid-field", f"{field} is unsafe or too large")
    return value


def _identifier(value: object, field: str) -> str:
    result = _string(value, field, 64)
    if _IDENTIFIER.fullmatch(result) is None:
        raise EnvoyProtocolError("invalid-field", f"{field} is not identifier-like")
    return result


def _code_value(value: object, field: str = "code") -> str:
    result = _string(value, field, 64)
    if _CODE.fullmatch(result) is None:
        raise EnvoyProtocolError("invalid-field", f"{field} is not a protocol code")
    return result


def _validate_fields(values: dict[str, object]) -> dict[str, object]:
    result = dict(values)
    for name, value in values.items():
        if name == "seq":
            result[name] = _integer(value, name, 1, MAX_SEQUENCE)
        elif name in {"envoy_pid", "shell_pid"}:
            result[name] = _integer(value, name, 1, MAX_PID)
        elif name in {"columns"}:
            result[name] = _integer(value, name, MIN_COLUMNS, MAX_COLUMNS)
        elif name in {"rows"}:
            result[name] = _integer(value, name, MIN_ROWS, MAX_ROWS)
        elif name in {"status"}:
            result[name] = _integer(value, name, 0, 255)
        elif name in {"output_start", "output_through"}:
            result[name] = _integer(value, name, 0, MAX_OUTPUT_OFFSET)
        elif name in {"session_id", "operation_id", "gate_id"}:
            if value is None and name == "operation_id":
                result[name] = None
            else:
                result[name] = _identifier(value, name)
        elif name == "source":
            result[name] = _string(value, name, MAX_OPERATION_SOURCE_BYTES)
        elif name == "cwd":
            cwd = _string(value, name, MAX_CWD_BYTES)
            if not cwd.startswith("/"):
                raise EnvoyProtocolError("invalid-field", "cwd must be absolute")
            result[name] = cwd
        elif name == "code":
            result[name] = _code_value(value)
        elif name == "severity":
            severity = _string(value, name, 16)
            if severity not in {"info", "warning", "error", "fatal"}:
                raise EnvoyProtocolError("invalid-field", "invalid severity")
            result[name] = severity
        elif name == "message":
            result[name] = _string(value, name, MAX_DIAGNOSTIC_BYTES)
        elif name == "reason":
            result[name] = _string(value, name, MAX_REASON_BYTES)
    if (
        "output_start" in result
        and "output_through" in result
        and result["output_through"] < result["output_start"]  # type: ignore[operator]
    ):
        raise EnvoyProtocolError(
            "invalid-output-range", "output_through precedes output_start"
        )
    return result


def _decode_telemetry(
    frame: bytes,
    registry: dict[str, type[object]],
) -> object:
    payload = _load_jsonl_frame(frame)
    if payload.get("schema") != TELEMETRY_SCHEMA:
        raise EnvoyProtocolError("unsupported-schema", "unsupported telemetry schema")
    message_type = payload.get("type")
    if not isinstance(message_type, str) or message_type not in registry:
        raise EnvoyProtocolError("unsupported-message", "unsupported message type")
    model = registry[message_type]
    optional = frozenset({"operation_id"}) if model is Diagnostic else frozenset()
    _require_exact_fields(payload, model, optional=optional)
    values = {
        name: payload.get(name)
        for name in model.__dataclass_fields__  # type: ignore[attr-defined]
    }
    return model(**_validate_fields(values))


def decode_controller_frame(frame: bytes) -> ControllerMessage:
    return _decode_telemetry(frame, _CONTROLLER_TYPES)  # type: ignore[return-value,arg-type]


def decode_envoy_frame(frame: bytes) -> EnvoyMessage:
    return _decode_telemetry(frame, _ENVOY_TYPES)  # type: ignore[return-value,arg-type]


def _encode_telemetry(message: object, models: dict[type[object], str]) -> bytes:
    try:
        message_type = models[type(message)]
    except KeyError as exc:
        raise TypeError(f"unsupported telemetry model: {type(message).__name__}") from exc
    values = _validate_fields(asdict(message))
    payload: dict[str, object] = {
        "schema": TELEMETRY_SCHEMA,
        "type": message_type,
    }
    payload.update({key: value for key, value in values.items() if value is not None})
    frame = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(frame) > MAX_TELEMETRY_FRAME_BYTES:
        raise EnvoyProtocolError("frame-too-large", "telemetry frame exceeds limit")
    return frame


def encode_controller_frame(message: ControllerMessage) -> bytes:
    return _encode_telemetry(message, _CONTROLLER_MODELS)


def encode_envoy_frame(message: EnvoyMessage) -> bytes:
    return _encode_telemetry(message, _ENVOY_MODELS)


class TelemetryStreamDecoder:
    """Incrementally decode one direction of bounded JSON Lines telemetry."""

    def __init__(self, direction: Literal["controller", "envoy"]) -> None:
        if direction not in {"controller", "envoy"}:
            raise ValueError(f"unsupported telemetry direction: {direction!r}")
        self._decode = (
            decode_controller_frame if direction == "controller" else decode_envoy_frame
        )
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[ControllerMessage | EnvoyMessage]:
        self._buffer.extend(data)
        messages: list[ControllerMessage | EnvoyMessage] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) >= MAX_TELEMETRY_FRAME_BYTES:
                    raise EnvoyProtocolError("frame-too-large", "unterminated frame")
                return messages
            frame = bytes(self._buffer[: newline + 1])
            del self._buffer[: newline + 1]
            messages.append(self._decode(frame))

    def finish(self) -> None:
        if self._buffer:
            raise EnvoyProtocolError("early-close", "telemetry closed mid-frame")


class PresentationUtf8Decoder:
    """Decode presentation text incrementally while raw bytes remain canonical."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data: bytes) -> str:
        return self._decoder.decode(data, final=False)

    def finish(self) -> str:
        return self._decoder.decode(b"", final=True)


def _encode_awsh(message: object, models: dict[type[object], str]) -> bytes:
    try:
        message_type = models[type(message)]
    except KeyError as exc:
        raise TypeError(f"unsupported awsh model: {type(message).__name__}") from exc
    values = _validate_fields(asdict(message))
    fields = [AWSH_SCHEMA, message_type]
    fields.extend(str(value) for value in values.values())
    frame = b"\0".join(field.encode("utf-8") for field in fields) + b"\0"
    if len(frame) > MAX_AWSH_FRAME_BYTES:
        raise EnvoyProtocolError("frame-too-large", "awsh frame exceeds limit")
    return frame


def _decode_awsh(
    frame: bytes,
    registry: dict[str, type[object]],
) -> object:
    if not isinstance(frame, bytes):
        raise TypeError("awsh frame must be bytes")
    if len(frame) > MAX_AWSH_FRAME_BYTES:
        raise EnvoyProtocolError("frame-too-large", "awsh frame exceeds limit")
    if not frame.endswith(b"\0"):
        raise EnvoyProtocolError("early-close", "awsh frame is not terminated")
    raw_fields = frame[:-1].split(b"\0")
    try:
        fields = [field.decode("utf-8", errors="strict") for field in raw_fields]
    except UnicodeDecodeError as exc:
        raise EnvoyProtocolError("invalid-utf8", "awsh frame is not UTF-8") from exc
    if len(fields) < 2 or fields[0] != AWSH_SCHEMA:
        raise EnvoyProtocolError("unsupported-schema", "unsupported awsh schema")
    try:
        model = registry[fields[1]]
    except KeyError as exc:
        raise EnvoyProtocolError("unsupported-message", "unsupported awsh message") from exc
    names = tuple(model.__dataclass_fields__)  # type: ignore[attr-defined]
    if len(fields) != len(names) + 2:
        raise EnvoyProtocolError("invalid-field-count", "invalid awsh field count")
    values: dict[str, object] = dict(zip(names, fields[2:], strict=True))
    for integer_field in {"shell_pid", "status"} & values.keys():
        try:
            values[integer_field] = int(values[integer_field])  # type: ignore[arg-type]
        except ValueError as exc:
            raise EnvoyProtocolError(
                "invalid-field", f"{integer_field} must be an integer"
            ) from exc
    return model(**_validate_fields(values))


def encode_awsh_request(message: AwshRequest) -> bytes:
    return _encode_awsh(message, _AWSH_REQUEST_MODELS)


def encode_awsh_result(message: AwshResult) -> bytes:
    return _encode_awsh(message, _AWSH_RESULT_MODELS)


def decode_awsh_request(frame: bytes) -> AwshRequest:
    return _decode_awsh(frame, _AWSH_REQUEST_TYPES)  # type: ignore[return-value,arg-type]


def decode_awsh_result(frame: bytes) -> AwshResult:
    return _decode_awsh(frame, _AWSH_RESULT_TYPES)  # type: ignore[return-value,arg-type]


class AwshStreamDecoder:
    """Incrementally decode NUL-delimited awsh frames by their fixed arity."""

    def __init__(self, direction: Literal["request", "result"]) -> None:
        if direction not in {"request", "result"}:
            raise ValueError(f"unsupported awsh direction: {direction!r}")
        self._registry = (
            _AWSH_REQUEST_TYPES if direction == "request" else _AWSH_RESULT_TYPES
        )
        self._decode = decode_awsh_request if direction == "request" else decode_awsh_result
        self._buffer = bytearray()
        self._fields: list[bytes] = []
        self._frame_bytes = 0

    def feed(self, data: bytes) -> list[AwshRequest | AwshResult]:
        self._buffer.extend(data)
        messages: list[AwshRequest | AwshResult] = []
        while True:
            delimiter = self._buffer.find(b"\0")
            if delimiter < 0:
                if self._frame_bytes + len(self._buffer) >= MAX_AWSH_FRAME_BYTES:
                    raise EnvoyProtocolError("frame-too-large", "unterminated awsh frame")
                return messages
            field = bytes(self._buffer[:delimiter])
            del self._buffer[: delimiter + 1]
            self._fields.append(field)
            self._frame_bytes += len(field) + 1
            if len(self._fields) < 2:
                continue
            try:
                schema = self._fields[0].decode("utf-8", errors="strict")
                message_type = self._fields[1].decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise EnvoyProtocolError("invalid-utf8", "awsh header is not UTF-8") from exc
            if schema != AWSH_SCHEMA:
                raise EnvoyProtocolError("unsupported-schema", "unsupported awsh schema")
            try:
                expected = 2 + len(self._registry[message_type].__dataclass_fields__)
            except KeyError as exc:
                raise EnvoyProtocolError(
                    "unsupported-message", "unsupported awsh message"
                ) from exc
            if len(self._fields) == expected:
                frame = b"\0".join(self._fields) + b"\0"
                messages.append(self._decode(frame))
                self._fields = []
                self._frame_bytes = 0

    def finish(self) -> None:
        if self._buffer or self._fields:
            raise EnvoyProtocolError("early-close", "awsh stream closed mid-frame")


class SessionProtocolState:
    """Validate the cross-direction v1 session state and sequence numbers."""

    def __init__(self) -> None:
        self.phase = "initial"
        self.operation_id: str | None = None
        self.gate_id: str | None = None
        self.output_start: int | None = None
        self.output_through = 0
        self._controller_seq = 0
        self._envoy_seq = 0
        self._pending_resize: tuple[int, int] | None = None
        self._used_gate_ids: set[str] = set()
        self._cancel_reason: str | None = None
        self._shutdown_reason: str | None = None

    def _sequence(self, direction: str, seq: int) -> None:
        attribute = "_controller_seq" if direction == "controller" else "_envoy_seq"
        expected = getattr(self, attribute) + 1
        if seq != expected:
            raise EnvoyProtocolError(
                "invalid-sequence", f"{direction} sequence must be {expected}"
            )
        setattr(self, attribute, seq)

    def accept_controller(self, message: ControllerMessage) -> None:
        self._sequence("controller", message.seq)
        if isinstance(message, Hello):
            self._require_phase("initial", message)
            self.phase = "hello-sent"
        elif isinstance(message, Resize):
            if self.phase not in {"idle", "starting", "running", "gated"}:
                self._out_of_state(message)
            if self._pending_resize is not None:
                self._out_of_state(message)
            self._pending_resize = (message.columns, message.rows)
        elif isinstance(message, Execute):
            self._require_phase("idle", message)
            self.phase = "starting"
            self.operation_id = message.operation_id
            self.gate_id = None
            self._used_gate_ids.clear()
            self._cancel_reason = None
        elif isinstance(message, Continue):
            self._require_phase("gated", message)
            self._require_operation(message.operation_id)
            if message.gate_id != self.gate_id:
                self._out_of_state(message)
            self.phase = "continuing"
        elif isinstance(message, Cancel):
            if self.phase not in {"running", "gated", "continuing"}:
                self._out_of_state(message)
            self._require_operation(message.operation_id)
            self._cancel_reason = message.reason
            self.phase = "cancelling"
        elif isinstance(message, Shutdown):
            self._require_phase("idle", message)
            if self._pending_resize is not None:
                self._out_of_state(message)
            self._shutdown_reason = message.reason
            self.phase = "shutdown-sent"

    def accept_envoy(self, message: EnvoyMessage) -> None:
        self._sequence("envoy", message.seq)
        if isinstance(message, Ready):
            self._require_phase("hello-sent", message)
            self.phase = "idle"
        elif isinstance(message, ResizeApplied):
            if self._pending_resize != (message.columns, message.rows):
                self._out_of_state(message)
            self._pending_resize = None
        elif isinstance(message, OperationStarted):
            self._require_phase("starting", message)
            self._require_operation(message.operation_id)
            if message.output_start < self.output_through:
                self._out_of_state(message)
            self.output_start = message.output_start
            self.phase = "running"
        elif isinstance(message, OperationReady):
            self._require_phase("running", message)
            self._require_operation(message.operation_id)
            if message.gate_id in self._used_gate_ids:
                raise EnvoyProtocolError("reused-gate", "gate id was already used")
            self._advance_output(message.output_through)
            self._used_gate_ids.add(message.gate_id)
            self.gate_id = message.gate_id
            self.phase = "gated"
        elif isinstance(message, OperationContinued):
            self._require_phase("continuing", message)
            self._require_operation(message.operation_id)
            if message.gate_id != self.gate_id:
                self._out_of_state(message)
            self._advance_output(message.output_through)
            self.gate_id = None
            self.phase = "running"
        elif isinstance(message, OperationCompleted):
            self._require_phase("running", message)
            self._finish_operation(message)
        elif isinstance(message, OperationCancelled):
            self._require_phase("cancelling", message)
            if message.reason != self._cancel_reason:
                raise EnvoyProtocolError(
                    "cancellation-reason-mismatch",
                    "cancellation reason does not match request",
                )
            self._finish_operation(message)
        elif isinstance(message, OperationFailed):
            if self.phase not in {
                "starting",
                "running",
                "gated",
                "continuing",
                "cancelling",
            }:
                self._out_of_state(message)
            if self.phase == "starting":
                if message.output_start < self.output_through:
                    self._out_of_state(message)
                self.output_start = message.output_start
            self._finish_operation(message)
        elif isinstance(message, Diagnostic):
            if self.phase in {"initial", "closed"}:
                self._out_of_state(message)
            if message.operation_id is not None:
                self._require_operation(message.operation_id)
        elif isinstance(message, Draining):
            self._require_phase("shutdown-sent", message)
            if message.reason != self._shutdown_reason:
                raise EnvoyProtocolError(
                    "shutdown-reason-mismatch",
                    "draining reason does not match shutdown request",
                )
            self._advance_output(message.output_through)
            self.phase = "draining"
        elif isinstance(message, Closed):
            self._require_phase("draining", message)
            self._advance_output(message.output_through)
            self.phase = "closed"

    def _finish_operation(
        self, message: OperationCompleted | OperationCancelled | OperationFailed
    ) -> None:
        self._require_operation(message.operation_id)
        if self.output_start is None or message.output_start != self.output_start:
            self._out_of_state(message)
        self._advance_output(message.output_through)
        self.operation_id = None
        self.gate_id = None
        self.output_start = None
        self._used_gate_ids.clear()
        self._cancel_reason = None
        self.phase = "idle"

    def _advance_output(self, offset: int) -> None:
        if offset < self.output_through:
            raise EnvoyProtocolError("invalid-output-order", "output offset regressed")
        if self.output_start is not None and offset < self.output_start:
            raise EnvoyProtocolError(
                "invalid-output-order", "output offset precedes operation start"
            )
        self.output_through = offset

    def _require_phase(self, phase: str, message: object) -> None:
        if self.phase != phase:
            self._out_of_state(message)

    def _require_operation(self, operation_id: str) -> None:
        if operation_id != self.operation_id:
            raise EnvoyProtocolError("wrong-operation", "operation id does not match")

    def _out_of_state(self, message: object) -> None:
        raise EnvoyProtocolError(
            "out-of-state",
            f"{type(message).__name__} is invalid while {self.phase}",
        )


def sanitized_bash_environment(environment: dict[str, str]) -> dict[str, str]:
    """Remove Bash control-plane injection from delegated application values."""

    result: dict[str, str] = {}
    for name, value in environment.items():
        if name in RESERVED_BASH_ENVIRONMENT or name.startswith("BASH_FUNC_"):
            continue
        if "\0" in name or "\0" in value or "=" in name or not name:
            raise EnvoyProtocolError("invalid-environment", "invalid environment entry")
        result[name] = value
    return result
