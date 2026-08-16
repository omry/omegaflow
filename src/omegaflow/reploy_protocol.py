"""Strict public Reploy controlled-session v1 models.

This module deliberately mirrors only Reploy's published JSONL and host-result
contracts.  It neither imports Reploy internals nor opens the private session
socket.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


CLIENT_SCHEMA = "reploy-controlled-session-client-v1"
RUN_RESULT_SCHEMA = "reploy-controlled-session-run-result-v1"
MAX_FRAME_BYTES = 1 << 20
_CODE_RE = re.compile(r"[a-z][a-z0-9_-]{0,127}\Z")


class ReployProtocolError(ValueError):
    """A public Reploy message is malformed or unsupported."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReployProtocolError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _json_object(payload: bytes | str, *, limit: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not raw or len(raw) > limit or b"\x00" in raw:
        raise ReployProtocolError("JSON message is empty, oversized, or contains NUL")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ReployProtocolError(f"non-finite JSON number {value!r}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReployProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReployProtocolError("JSON message must be an object")
    return value


def _fields(value: dict[str, Any], required: set[str], optional: set[str] = set()) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise ReployProtocolError(f"missing field {sorted(missing)[0]!r}")
    if unknown:
        raise ReployProtocolError(f"unknown field {sorted(unknown)[0]!r}")


def _string(value: Any, name: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ReployProtocolError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ReployProtocolError(f"{name} contains NUL")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ReployProtocolError(f"{name} must be between {minimum} and {maximum}")
    return value


def _nullable_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


@dataclass(frozen=True)
class ReployEndpoint:
    id: str
    scheme: str
    host: str
    port: int


@dataclass(frozen=True)
class BrokerReady:
    terminal_socket: str
    type: Literal["broker-ready"] = "broker-ready"


@dataclass(frozen=True)
class Opened:
    operations: tuple[str, ...]
    endpoints: tuple[ReployEndpoint, ...]
    columns: int
    rows: int
    output_finalization_timeout_milliseconds: int
    type: Literal["opened"] = "opened"


@dataclass(frozen=True)
class Ready:
    type: Literal["ready"] = "ready"


@dataclass(frozen=True)
class ReployStatus:
    kind: str
    code: int | None = None
    reason: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class WorkloadExit:
    status: ReployStatus
    type: Literal["workload-exit"] = "workload-exit"


@dataclass(frozen=True)
class Terminating:
    cause: str
    type: Literal["terminating"] = "terminating"


@dataclass(frozen=True)
class ReployDiagnostic:
    code: str
    message: str
    type: Literal["diagnostic"] = "diagnostic"


@dataclass(frozen=True)
class WorkloadOutputsFinalized:
    status: Literal["drained", "failed"]
    reason: str | None = None
    type: Literal["workload-outputs-finalized"] = "workload-outputs-finalized"


@dataclass(frozen=True)
class ReployLifecycleResult:
    cause: str
    workload_status: ReployStatus
    workload_output_finalization_status: ReployStatus
    runtime_observation_status: ReployStatus
    controller_finalization_status: ReployStatus
    cleanup_status: ReployStatus
    recovery_action: str


@dataclass(frozen=True)
class Terminated:
    result: ReployLifecycleResult
    type: Literal["terminated"] = "terminated"


@dataclass(frozen=True)
class ClientError:
    code: str
    message: str
    type: Literal["client-error"] = "client-error"


ClientEvent: TypeAlias = (
    BrokerReady
    | Opened
    | Ready
    | WorkloadExit
    | Terminating
    | ReployDiagnostic
    | WorkloadOutputsFinalized
    | Terminated
    | ClientError
)


def _status(value: Any, name: str) -> ReployStatus:
    if not isinstance(value, dict):
        raise ReployProtocolError(f"{name} must be an object")
    _fields(value, {"kind"}, {"code", "reason", "message"})
    code = value.get("code")
    if code is not None:
        code = _integer(code, f"{name}.code", 0, 255)
    return ReployStatus(
        kind=_string(value["kind"], f"{name}.kind"),
        code=code,
        reason=_nullable_string(value.get("reason"), f"{name}.reason"),
        message=_nullable_string(value.get("message"), f"{name}.message"),
    )


def _endpoint(value: Any) -> ReployEndpoint:
    if not isinstance(value, dict):
        raise ReployProtocolError("endpoint must be an object")
    _fields(value, {"id", "scheme", "host", "port"})
    return ReployEndpoint(
        id=_string(value["id"], "endpoint.id"),
        scheme=_string(value["scheme"], "endpoint.scheme"),
        host=_string(value["host"], "endpoint.host"),
        port=_integer(value["port"], "endpoint.port", 1, 65535),
    )


def _lifecycle(value: Any) -> ReployLifecycleResult:
    if not isinstance(value, dict):
        raise ReployProtocolError("lifecycle result must be an object")
    required = {
        "cause",
        "workload_status",
        "workload_output_finalization_status",
        "runtime_observation_status",
        "controller_finalization_status",
        "cleanup_status",
        "recovery_action",
    }
    _fields(value, required)
    return ReployLifecycleResult(
        cause=_string(value["cause"], "result.cause"),
        workload_status=_status(value["workload_status"], "result.workload_status"),
        workload_output_finalization_status=_status(
            value["workload_output_finalization_status"],
            "result.workload_output_finalization_status",
        ),
        runtime_observation_status=_status(
            value["runtime_observation_status"], "result.runtime_observation_status"
        ),
        controller_finalization_status=_status(
            value["controller_finalization_status"],
            "result.controller_finalization_status",
        ),
        cleanup_status=_status(value["cleanup_status"], "result.cleanup_status"),
        recovery_action=_string(value["recovery_action"], "result.recovery_action"),
    )


def decode_client_event(payload: bytes | str) -> ClientEvent:
    """Decode one newline-free event emitted by ``reploy-session-client``."""

    value = _json_object(payload)
    if value.get("schema") != CLIENT_SCHEMA:
        raise ReployProtocolError("unsupported controlled-session client schema")
    kind = _string(value.get("type"), "type")
    base = {"schema", "type"}
    if kind == "broker-ready":
        _fields(value, base | {"terminal_socket"})
        socket = _string(value["terminal_socket"], "terminal_socket")
        if not socket.startswith("/"):
            raise ReployProtocolError("terminal_socket must be absolute")
        return BrokerReady(socket)
    if kind == "opened":
        _fields(
            value,
            base
            | {
                "operations",
                "endpoints",
                "columns",
                "rows",
                "output_finalization_timeout_milliseconds",
            },
        )
        operations = value["operations"]
        endpoints = value["endpoints"]
        if not isinstance(operations, list) or any(
            not isinstance(item, str) for item in operations
        ):
            raise ReployProtocolError("opened.operations must be a string array")
        if len(set(operations)) != len(operations):
            raise ReployProtocolError("opened.operations contains duplicates")
        if not isinstance(endpoints, list):
            raise ReployProtocolError("opened.endpoints must be an array")
        decoded = tuple(_endpoint(item) for item in endpoints)
        if len({item.id for item in decoded}) != len(decoded):
            raise ReployProtocolError("opened.endpoints contains duplicate ids")
        return Opened(
            operations=tuple(operations),
            endpoints=decoded,
            columns=_integer(value["columns"], "columns", 1, 65535),
            rows=_integer(value["rows"], "rows", 1, 65535),
            output_finalization_timeout_milliseconds=_integer(
                value["output_finalization_timeout_milliseconds"],
                "output_finalization_timeout_milliseconds",
                1,
                2**32 - 1,
            ),
        )
    if kind == "ready":
        _fields(value, base)
        return Ready()
    if kind == "workload-exit":
        _fields(value, base | {"status"})
        return WorkloadExit(_status(value["status"], "status"))
    if kind == "terminating":
        _fields(value, base | {"cause"})
        return Terminating(_string(value["cause"], "cause"))
    if kind in {"diagnostic", "client-error"}:
        _fields(value, base | {"code", "message"})
        code = _string(value["code"], "code")
        if not _CODE_RE.fullmatch(code):
            raise ReployProtocolError("diagnostic code has invalid syntax")
        message = _string(value["message"], "message")
        return (
            ReployDiagnostic(code, message)
            if kind == "diagnostic"
            else ClientError(code, message)
        )
    if kind == "workload-outputs-finalized":
        _fields(value, base | {"status"}, {"reason"})
        status = value["status"]
        if status not in {"drained", "failed"}:
            raise ReployProtocolError("unsupported workload output finalization status")
        reason = _nullable_string(value.get("reason"), "reason")
        if status == "failed" and reason is None:
            raise ReployProtocolError("failed output finalization requires a reason")
        if status == "drained" and reason is not None:
            raise ReployProtocolError("drained output finalization must not carry a reason")
        return WorkloadOutputsFinalized(status, reason)
    if kind == "terminated":
        _fields(value, base | {"result"})
        return Terminated(_lifecycle(value["result"]))
    raise ReployProtocolError(f"unsupported controlled-session event type {kind!r}")


def encode_client_request(kind: str, **payload: Any) -> bytes:
    """Encode one public controller request frame."""

    if kind == "resize":
        if set(payload) != {"columns", "rows"}:
            raise ReployProtocolError("resize requires columns and rows")
        body = {
            "schema": CLIENT_SCHEMA,
            "type": kind,
            "columns": _integer(payload["columns"], "columns", 1, 65535),
            "rows": _integer(payload["rows"], "rows", 1, 65535),
        }
    elif kind in {"terminate", "complete", "acknowledge-terminated"}:
        if payload:
            raise ReployProtocolError(f"{kind} does not accept a payload")
        body = {"schema": CLIENT_SCHEMA, "type": kind}
    else:
        raise ReployProtocolError(f"unsupported client request type {kind!r}")
    return json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\n"


@dataclass(frozen=True)
class ReployRunResult:
    ok: bool
    error: str | None
    session_result: ReployLifecycleResult | None
    result_delivered: bool | None
    result_acknowledged: bool | None
    controller_status: ReployStatus | None
    controller_output: ReployStatus | None
    delivery_tail_cleanup_status: ReployStatus | None
    delivery_tail_recovery_action: str | None


def decode_run_result(payload: bytes | str) -> ReployRunResult:
    """Decode the single authoritative host result written by Reploy."""

    value = _json_object(payload, limit=8 << 20)
    required = {
        "schema",
        "ok",
        "error",
        "session_result",
        "result_delivered",
        "result_acknowledged",
        "controller_status",
        "controller_output",
        "delivery_tail_cleanup_status",
        "delivery_tail_recovery_action",
    }
    _fields(value, required)
    if value["schema"] != RUN_RESULT_SCHEMA:
        raise ReployProtocolError("unsupported controlled-session run-result schema")
    if not isinstance(value["ok"], bool):
        raise ReployProtocolError("run result ok must be a boolean")

    def optional_status(name: str) -> ReployStatus | None:
        item = value[name]
        return None if item is None else _status(item, name)

    for name in ("result_delivered", "result_acknowledged"):
        if value[name] is not None and not isinstance(value[name], bool):
            raise ReployProtocolError(f"{name} must be boolean or null")
    return ReployRunResult(
        ok=value["ok"],
        error=_nullable_string(value["error"], "error"),
        session_result=(
            None if value["session_result"] is None else _lifecycle(value["session_result"])
        ),
        result_delivered=value["result_delivered"],
        result_acknowledged=value["result_acknowledged"],
        controller_status=optional_status("controller_status"),
        controller_output=optional_status("controller_output"),
        delivery_tail_cleanup_status=optional_status("delivery_tail_cleanup_status"),
        delivery_tail_recovery_action=_nullable_string(
            value["delivery_tail_recovery_action"], "delivery_tail_recovery_action"
        ),
    )
