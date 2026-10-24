"""Strict codecs for Reploy's public controlled-session v1 JSON Lines stream.

These functions validate individual wire values. Session ordering, authorization
and transport ownership belong to the controller, not to these codecs.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable


CLIENT_SCHEMA = "reploy-controlled-session-client-v1"
HOST_RESULT_SCHEMA = "reploy-controlled-session-run-result-v1"
MAX_LINE_BYTES = 1 << 20

_OPERATIONS = frozenset({"input", "resize", "terminate", "complete"})
_CAUSES = frozenset({
    "controller-terminate", "workload-exit", "host-cancel", "controller-lost",
    "runtime-observation-lost", "cleanup-containment-lost", "startup-failure",
})
_REQUESTS = frozenset({"resize", "terminate", "complete", "acknowledge-terminated"})
_EVENTS = frozenset({
    "broker-ready", "opened", "ready", "workload-exit", "terminating",
    "diagnostic", "workload-outputs-finalized", "terminated", "client-error",
})
_TERMINAL_CONTROLLER_STATES = frozenset({
    "completed", "lost", "finalization-timeout", "not-completed", "startup-failed",
})
_MACHINE_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_ENDPOINT_NAME = re.compile(r"[a-z0-9]+(?:(?:__|[._]|-+)[a-z0-9]+)*\Z")
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")

JSONObject = dict[str, Any]


class ReployCodecError(ValueError):
    """A public controlled-session wire value violates the v1 contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReployCodecError(message)


def _object(value: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    _require(type(value) is dict, "expected a JSON object")
    _require(required <= value.keys() <= required | optional, "invalid object fields")


def _string(value: Any, *, nonempty: bool = True) -> str:
    _require(type(value) is str, "expected a string")
    _require(not nonempty or bool(value), "expected a nonempty string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ReployCodecError("string is not valid Unicode") from error
    return value


def _safe_text(value: Any, *, required: bool = True) -> None:
    _string(value, nonempty=required)
    if not value:
        return
    _require(len(value.encode("utf-8")) <= 512, "safe text exceeds 512 bytes")
    _require(value.strip() == value, "safe text has surrounding whitespace")
    _require(
        not any(unicodedata.category(c) in {"Cc", "Cf"} for c in value),
        "safe text contains control or format characters",
    )


def _enum(value: Any, choices: frozenset[str]) -> str:
    _string(value)
    _require(value in choices, "unsupported enum value: " + value)
    return value


def _integer(value: Any, lower: int, upper: int) -> None:
    _require(type(value) is int and lower <= value <= upper, "integer outside wire bounds")


def _dimensions(value: JSONObject) -> None:
    _integer(value["columns"], 1, 65535)
    _integer(value["rows"], 1, 65535)


def _process_status(value: Any, *, allow_unknown: bool) -> None:
    _object(value, {"kind"}, {"code", "reason"})
    kind = _enum(value["kind"], frozenset({"unknown", "exited", "terminated", "unavailable"}))
    if "reason" in value:
        _safe_text(value["reason"], required=False)
    if kind == "exited":
        _require("code" in value, "exited status requires a code")
        _integer(value["code"], -(1 << 63), (1 << 63) - 1)
    else:
        _require("code" not in value, "non-exited status must not contain a code")
    if kind == "unknown":
        _require(allow_unknown and not value.get("reason"), "unknown process status is invalid")


def _reason_status(value: Any, choices: frozenset[str]) -> str:
    _object(value, {"kind"}, {"reason"})
    kind = _enum(value["kind"], choices)
    if "reason" in value:
        _safe_text(value["reason"], required=False)
    return kind


def _output_status(value: Any) -> None:
    kind = _reason_status(value, frozenset({"drained", "failed"}))
    if kind == "failed":
        _safe_text(value.get("reason"))
    else:
        _require(not value.get("reason"), "drained output must not contain a reason")


def _cleanup_status(cleanup: Any, recovery: Any) -> None:
    _object(cleanup, {"kind"}, {"message"})
    kind = _enum(cleanup["kind"], frozenset({"succeeded", "failed"}))
    _enum(recovery, frozenset({"none", "retry-cleanup", "reconcile-next-operation"}))
    if "message" in cleanup:
        _safe_text(cleanup["message"], required=False)
    if kind == "succeeded":
        _require(not cleanup.get("message") and recovery == "none", "successful cleanup requires no recovery or error")
    else:
        _safe_text(cleanup.get("message"))
        _require(recovery != "none", "failed cleanup requires recovery")


def _session_result(value: Any) -> None:
    _object(value, {
        "cause", "workload_status", "workload_output_finalization_status",
        "runtime_observation_status", "controller_finalization_status",
        "cleanup_status", "recovery_action",
    })
    cause = _enum(value["cause"], _CAUSES)
    _process_status(value["workload_status"], allow_unknown=True)
    _output_status(value["workload_output_finalization_status"])
    observation = value["runtime_observation_status"]
    if _reason_status(observation, frozenset({"maintained", "lost"})) == "maintained":
        _require(not observation.get("reason"), "maintained observation must not contain a reason")
    controller = _reason_status(value["controller_finalization_status"], _TERMINAL_CONTROLLER_STATES)
    _cleanup_status(value["cleanup_status"], value["recovery_action"])
    if cause == "workload-exit":
        _require(value["workload_status"]["kind"] != "unknown", "workload-exit requires a known status")
    if cause == "runtime-observation-lost":
        _require(observation["kind"] == "lost", "observation-loss cause requires lost observation")
        _require(value["workload_output_finalization_status"]["kind"] == "failed", "observation loss requires failed output finalization")
    if cause == "controller-lost":
        _require(controller == "lost", "controller-loss cause requires lost finalization")
    if cause == "startup-failure":
        _require(controller == "startup-failed", "startup failure requires startup-failed finalization")


def _endpoint(value: Any) -> None:
    _object(value, {"id", "scheme", "host", "port"})
    name = _string(value["id"])
    _require(len(name.encode("utf-8")) <= 128 and _ENDPOINT_NAME.fullmatch(name) is not None, "invalid endpoint ID")
    _require(_URI_SCHEME.fullmatch(_string(value["scheme"])) is not None, "invalid endpoint URI scheme")
    _require(value["host"] == "workload", "endpoint host must be workload")
    _integer(value["port"], 1, 65535)


def _envelope(value: Any, kinds: frozenset[str]) -> str:
    _require(type(value) is dict, "expected a JSON object")
    _require(value.get("schema") == CLIENT_SCHEMA, "unsupported public client schema")
    return _enum(value.get("type"), kinds)


def _request(value: Any) -> None:
    kind = _envelope(value, _REQUESTS)
    fields = {"schema", "type"}
    if kind == "resize":
        fields |= {"columns", "rows"}
    _object(value, fields)
    if kind == "resize":
        _dimensions(value)


def _event(value: Any) -> None:
    kind = _envelope(value, _EVENTS)
    fields = {"schema", "type"}
    payloads = {
        "broker-ready": {"terminal_socket"},
        "opened": {"operations", "endpoints", "columns", "rows", "output_finalization_timeout_milliseconds"},
        "ready": set(),
        "workload-exit": {"status"},
        "terminating": {"cause"},
        "diagnostic": {"code", "message"},
        "client-error": {"code", "message"},
        "workload-outputs-finalized": {"status"},
        "terminated": {"result"},
    }
    optional = {"reason"} if kind == "workload-outputs-finalized" else set()
    _object(value, fields | payloads[kind], optional)
    if kind == "broker-ready":
        _string(value["terminal_socket"])
    elif kind == "opened":
        _dimensions(value)
        _integer(value["output_finalization_timeout_milliseconds"], 1, (1 << 32) - 1)
        operations = value["operations"]
        _require(type(operations) is list, "operations must be an array")
        for operation in operations:
            _enum(operation, _OPERATIONS)
        _require(len(set(operations)) == len(operations), "duplicate operation grant")
        endpoints = value["endpoints"]
        _require(type(endpoints) is list, "endpoints must be an array")
        for endpoint in endpoints:
            _endpoint(endpoint)
        _require(len({endpoint["id"] for endpoint in endpoints}) == len(endpoints), "duplicate endpoint ID")
    elif kind == "workload-exit":
        _process_status(value["status"], allow_unknown=False)
    elif kind == "terminating":
        _enum(value["cause"], _CAUSES)
    elif kind in {"diagnostic", "client-error"}:
        code = _string(value["code"])
        _require(len(code) <= 63 and _MACHINE_CODE.fullmatch(code) is not None, "invalid diagnostic machine code")
        _safe_text(value["message"])
    elif kind == "workload-outputs-finalized":
        status = {"kind": value["status"]}
        if "reason" in value:
            status["reason"] = value["reason"]
        _output_status(status)
    elif kind == "terminated":
        _session_result(value["result"])


def _host_result(value: Any) -> None:
    _object(value, {
        "schema", "ok", "error", "session_result", "result_delivered",
        "result_acknowledged", "controller_status", "controller_output",
        "delivery_tail_cleanup_status", "delivery_tail_recovery_action",
    })
    _require(value["schema"] == HOST_RESULT_SCHEMA, "unsupported public host-result schema")
    _require(type(value["ok"]) is bool, "host ok must be a boolean")
    if value["error"] is not None:
        _string(value["error"], nonempty=False)
    result = value["session_result"]
    delivered, acknowledged = value["result_delivered"], value["result_acknowledged"]
    if result is None:
        _require(delivered is None and acknowledged is None, "absent session result requires null delivery flags")
    else:
        _session_result(result)
        _require(type(delivered) is bool and type(acknowledged) is bool, "session delivery flags must be booleans")
        _require(not acknowledged or delivered, "acknowledgement requires delivery")
    if value["controller_status"] is not None:
        _process_status(value["controller_status"], allow_unknown=True)
    output = value["controller_output"]
    if output is not None:
        _object(output, {"kind"}, {"reason"})
        _enum(output["kind"], frozenset({
            "not-requested", "directory-retained", "file-published", "file-discarded", "failed",
        }))
        if "reason" in output:
            _string(output["reason"], nonempty=False)
    cleanup, recovery = value["delivery_tail_cleanup_status"], value["delivery_tail_recovery_action"]
    if cleanup is None:
        _require(recovery is None, "absent delivery-tail cleanup requires null recovery")
    else:
        _cleanup_status(cleanup, recovery)
    # Match the public host producer. Controller process exit status is retained
    # as evidence; the producer does not add it to this success predicate.
    success = (
        value["error"] is None and result is not None
        and (result["cause"] == "controller-terminate" or (
            result["cause"] == "workload-exit"
            and result["workload_status"]["kind"] == "exited"
            and result["workload_status"]["code"] == 0
        ))
        and result["workload_output_finalization_status"]["kind"] == "drained"
        and result["runtime_observation_status"]["kind"] == "maintained"
        and result["controller_finalization_status"]["kind"] == "completed"
        and result["cleanup_status"]["kind"] == "succeeded"
        and result["recovery_action"] == "none"
        and delivered is True and acknowledged is True
        and cleanup is not None and cleanup["kind"] == "succeeded" and recovery == "none"
        and output is not None and output["kind"] in {"not-requested", "directory-retained", "file-published"}
    )
    _require(value["ok"] == success, "host ok disagrees with structured outcome")


def _pairs(items: list[tuple[str, Any]]) -> JSONObject:
    value: JSONObject = {}
    for key, item in items:
        _require(key not in value, "duplicate JSON field: " + key)
        value[key] = item
    return value


def _nonfinite(value: str) -> Any:
    raise ReployCodecError("nonfinite JSON number: " + value)


def _decode(
    line: bytes, validate: Callable[[Any], None], *, max_line_bytes: int | None = MAX_LINE_BYTES,
) -> JSONObject:
    _require(type(line) is bytes, "wire input must be bytes")
    _require(bool(line) and (max_line_bytes is None or len(line) <= max_line_bytes), "public JSON line exceeds wire bounds")
    _require(line.endswith(b"\n"), "public JSON line must end with LF")
    payload = line[:-1]
    _require(payload.startswith(b"{") and payload.endswith(b"}"), "expected exactly one object without surrounding whitespace")
    _require(b"\n" not in payload, "expected exactly one JSON line")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except ReployCodecError:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ReployCodecError("invalid UTF-8 JSON object") from error
    validate(value)
    return value


def _encode(
    value: JSONObject, validate: Callable[[Any], None], *, max_line_bytes: int | None = MAX_LINE_BYTES,
) -> bytes:
    validate(value)
    try:
        line = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ReployCodecError("value cannot be encoded as public JSON") from error
    _require(max_line_bytes is None or len(line) <= max_line_bytes, "public JSON line exceeds wire bounds")
    return line


def decode_event(line: bytes) -> JSONObject:
    """Decode one complete event line without advancing controller state."""
    return _decode(line, _event)


def encode_event(value: JSONObject) -> bytes:
    """Encode one validated public event, including its terminating LF."""
    return _encode(value, _event)


def decode_request(line: bytes) -> JSONObject:
    """Decode one complete public request line without authorizing it."""
    return _decode(line, _request)


def encode_request(value: JSONObject) -> bytes:
    """Encode one validated public request, including its terminating LF."""
    return _encode(value, _request)


def decode_host_result(line: bytes) -> JSONObject:
    """Decode host stdout's single result line, preserving null phase values.

    The public host contract does not impose the client stream's 1 MiB bound.
    """
    return _decode(line, _host_result, max_line_bytes=None)


def encode_host_result(value: JSONObject) -> bytes:
    """Encode one host result without changing its structured failure evidence."""
    return _encode(value, _host_result, max_line_bytes=None)
