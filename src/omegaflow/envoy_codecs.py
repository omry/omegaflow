"""Strict Envoy v1 public wire values; no operation or transport decisions."""

from __future__ import annotations

import json
import posixpath
import re


SCHEMA = "omegaflow-envoy-telemetry-v1"
MAX_FRAME_BYTES = 1_048_576
MAX_SOURCE_BYTES = 786_432
MAX_INTEGER = (1 << 63) - 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CODE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SESSION = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_HEADER = ("schema", "type", "seq")
_REQUESTS = {
    "hello": ("session_id",),
    "execute": ("operation_id", "source", "execution_shape", "timing", "publication",
                "observation", "inspections", "input_through"),
    "continue": ("operation_id", "gate_id", "input_through"),
    "cancel": ("operation_id", "reason"),
    "finalize": ("operation_id", "reason"),
    "resize": ("columns", "rows"),
    "shutdown": ("reason",),
}
_EVENTS = {
    "ready": ("envoy_pid", "shell_pid", "cwd", "columns", "rows", "elapsed_us", "output_through"),
    "operation_started": ("operation_id", "output_start"),
    "operation_ready": ("operation_id", "gate_id", "output_through"),
    "operation_continued": ("operation_id", "gate_id", "output_through"),
    "operation_gate_interrupted": ("operation_id", "gate_id", "output_through"),
    "output_mark": ("offset", "stream", "elapsed_us"),
    "operation_completed": ("operation_id", "status", "cwd", "output_start", "output_through", "inspection_results"),
    "operation_cancelled": ("operation_id", "cwd", "output_start", "output_through", "reason"),
    "operation_finalized": ("operation_id", "cwd", "output_start", "output_through", "reason", "inspection_results"),
    "operation_failed": ("operation_id", "output_start", "output_through", "code", "message", "cwd"),
    "resize_applied": ("columns", "rows", "elapsed_us", "output_through"),
    "diagnostic": ("severity", "code", "message"),
    "draining": ("reason", "output_through"),
    "closed": ("reason", "output_through"),
}
_OPTIONAL = {"operation_completed": ("shell_ended",), "operation_failed": ("shell_ended",),
             "operation_cancelled": ("status",), "diagnostic": ("operation_id",)}
_ENUMS = {"execution_shape": {"pty", "split"}, "timing": {"realtime", "presentation"},
          "publication": {"real", "suppress", "replace"}, "observation": {"shared", "exclusive"},
          "stream": {"pty", "stdout", "stderr"}, "severity": {"info", "warning", "error", "fatal"},
          "kind": {"file_exists", "produces"}, "path_kind": {"file", "directory", "other"},
          "digest_algorithm": {"directory-v2"}}
_FAILURES = {"inspection-resolution", "inspection-missing", "inspection-type", "inspection-limit",
             "inspection-unstable", "inspection-read", "input-barrier-timeout", "source-syntax",
             "source-policy", "cancel-timeout", "finalize-timeout", "shell-ended-unresolved"}


class EnvoyCodecError(ValueError):
    """A public frame or sequence violates the fixed Envoy contract."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise EnvoyCodecError(message)


def _fields(value: dict, required: tuple, optional: tuple = ()) -> None:
    _need(type(value) is dict, "expected an object")
    _need(set(required) <= value.keys() <= set(required + optional), "invalid object fields")


def _text(value: object, limit: int, *, absolute: bool = False) -> None:
    _need(type(value) is str, "expected text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise EnvoyCodecError("invalid Unicode text") from error
    _need(0 < size <= limit and "\0" not in value, "invalid text bounds")
    _need(not absolute or value.startswith("/"), "expected an absolute path")


def _scalar(name: str, value: object) -> None:
    if name in _ENUMS:
        _need(type(value) is str and value in _ENUMS[name], "invalid " + name)
    elif name in {"operation_id", "gate_id", "inspection_id", "producer_id", "output_id"}:
        _need(type(value) is str and _IDENTIFIER.fullmatch(value) is not None, "invalid identifier")
    elif name in {"session_id", "code", "sha256"}:
        pattern = {"session_id": _SESSION, "code": _CODE, "sha256": _DIGEST}[name]
        _need(type(value) is str and pattern.fullmatch(value) is not None, "invalid " + name)
    elif name in {"source", "cwd", "path", "resolved_path", "reason", "message"}:
        limit = MAX_SOURCE_BYTES if name == "source" else 256 if name == "reason" else 4096
        _text(value, limit, absolute=name in {"cwd", "resolved_path"})
        if name == "source":
            _need("__OMEGAFLOW_AWSH_" not in value, "reserved source namespace")
        if name == "resolved_path":
            # Linux path.Clean collapses a leading double slash, unlike posixpath.
            _need(posixpath.normpath("/" + value.lstrip("/")) == value, "unclean resolved path")
    elif name == "shell_ended":
        _need(value is True, "shell_ended must be true when present")
    else:
        lower, upper = 0, MAX_INTEGER
        if name in {"seq", "envoy_pid", "shell_pid", "columns", "rows"}:
            lower = 1
        if name in {"envoy_pid", "shell_pid"}:
            upper = (1 << 31) - 1
        elif name in {"columns", "rows"}:
            upper = 1000
        elif name == "status":
            upper = 255
        _need(type(value) is int and lower <= value <= upper, "invalid integer " + name)


def _inspections(values: object, *, results: bool) -> None:
    _need(type(values) is list and len(values) <= 64, "invalid inspections array")
    seen = set()
    for item in values:
        _need(type(item) is dict, "expected inspection object")
        required = ("inspection_id", "kind", "resolved_path", "path_kind") if results else ("inspection_id", "kind", "path")
        if item.get("kind") == "produces":
            required += ("producer_id", "output_id")
            if results:
                _need(item.get("path_kind") in ("file", "directory"), "invalid produced path kind")
                required += ("sha256",)
                if item["path_kind"] == "directory":
                    required += ("digest_algorithm",)
        _fields(item, required)
        for name, value in item.items():
            _scalar(name, value)
        _need(item["inspection_id"] not in seen, "duplicate inspection identity")
        seen.add(item["inspection_id"])


def validate(value: dict, *, controller: bool) -> tuple[str, ...]:
    """Validate an individual wire value and return its canonical field order."""
    _need(type(value) is dict and type(value.get("type")) is str, "expected typed object")
    kind = value["type"]
    models = _REQUESTS if controller else _EVENTS
    _need(kind in models, "unsupported type or wrong direction")
    order = _HEADER + models[kind]
    optional = _OPTIONAL.get(kind, ())
    _fields(value, order, optional)
    _need(value["schema"] == SCHEMA, "unsupported schema")
    for name, item in value.items():
        if name in {"schema", "type"}:
            continue
        if name in {"inspections", "inspection_results"}:
            _inspections(item, results=name == "inspection_results")
        else:
            _scalar(name, item)
    if "output_start" in value and "output_through" in value:
        _need(value["output_start"] <= value["output_through"], "reversed output range")
    if kind == "ready":
        _need(value["elapsed_us"] == 0 and value["output_through"] <= 4096, "invalid readiness boundary")
    elif kind == "execute":
        _need(value["timing"] != "realtime" or (value["execution_shape"] == "pty" and value["publication"] == "real"), "invalid realtime policy")
        _need(value["timing"] != "presentation" or (value["execution_shape"] == "split" and value["observation"] == "exclusive"), "invalid presentation policy")
        _need(value["observation"] == "exclusive" or (value["publication"] == "real" and not value["inspections"]), "exclusive observation required")
    elif kind == "operation_failed":
        code = value["code"]
        _need(code in _FAILURES, "unknown operation failure")
        if code in {"source-syntax", "source-policy", "input-barrier-timeout"}:
            _need(value["output_start"] == value["output_through"] and "shell_ended" not in value, "invalid pre-start failure")
        if code in {"cancel-timeout", "finalize-timeout", "shell-ended-unresolved"}:
            _need(value.get("shell_ended") is True, "shell-ended flag required")
    elif kind == "operation_cancelled" and "status" not in value:
        _need(value["output_start"] == value["output_through"], "invalid pre-start cancellation")
    return order + tuple(name for name in optional if name in value)


def _pairs(items: list) -> dict:
    value = {}
    for key, item in items:
        _need(key not in value, "duplicate JSON field")
        value[key] = item
    return value


def _nonfinite(value: str) -> None:
    raise EnvoyCodecError("nonfinite JSON number")


def decode(line: bytes, *, controller: bool = False) -> dict:
    """Decode exactly one LF-terminated frame without advancing a sequence."""
    _need(type(line) is bytes and 3 <= len(line) <= MAX_FRAME_BYTES, "invalid frame size")
    _need(line.endswith(b"\n") and not line.endswith(b"\r\n"), "invalid frame terminator")
    _need(b"\n" not in line[:-1] and b"\0" not in line, "invalid frame bytes")
    try:
        value = json.loads(line[:-1].decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise EnvoyCodecError("invalid UTF-8 JSON frame") from error
    validate(value, controller=controller)
    return value


def encode(value: dict, *, controller: bool = True) -> bytes:
    """Validate and encode the canonical compact public field order."""
    order = validate(value, controller=controller)
    ordered = {name: value[name] for name in order}
    for name in ("inspections", "inspection_results"):
        if name in ordered:
            fields = ("inspection_id", "kind", "path", "resolved_path", "path_kind",
                      "producer_id", "output_id", "sha256", "digest_algorithm")
            ordered[name] = [{key: item[key] for key in fields if key in item} for item in ordered[name]]
    try:
        line = json.dumps(ordered, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise EnvoyCodecError("unencodable public value") from error
    _need(len(line) <= MAX_FRAME_BYTES, "frame exceeds byte bound")
    return line


class EventFrames:
    """Bounded incremental Envoy JSONL framing with one exact sequence space."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._next = 1
        self._failed = False

    def feed(self, data: bytes) -> list[tuple[bytes, dict]]:
        _need(not self._failed, "frame stream already failed")
        try:
            _need(type(data) is bytes and len(data) <= MAX_FRAME_BYTES, "invalid read chunk")
            parts = data.split(b"\n")
            frames = []
            for index, part in enumerate(parts):
                _need(len(self._buffer) + len(part) + 1 <= MAX_FRAME_BYTES, "unterminated frame exceeds bound")
                self._buffer.extend(part)
                if index == len(parts) - 1:
                    break
                line = bytes(self._buffer) + b"\n"
                self._buffer.clear()
                event = decode(line)
                _need(event["seq"] == self._next, "invalid event sequence")
                self._next += 1
                frames.append((line, event))
            return frames
        except EnvoyCodecError:
            self._failed = True
            raise

    def eof(self) -> None:
        _need(not self._failed and not self._buffer, "incomplete frame at EOF")
