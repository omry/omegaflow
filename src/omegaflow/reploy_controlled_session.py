"""Strict codecs for Reploy's public controlled-session v1 boundary.

This module deliberately contains no subprocess, lifecycle, attachment, or
private-socket behavior. It turns the public JSON Lines messages into immutable
OmegaFlow values and serializes the four public controller requests.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias, TypeVar


CLIENT_SCHEMA_V1 = "reploy-controlled-session-client-v1"
RUN_RESULT_SCHEMA_V1 = "reploy-controlled-session-run-result-v1"
MAX_CLIENT_MESSAGE_BYTES_V1 = 1 << 20

_PROTOCOL_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_ENDPOINT_ID_RE = re.compile(
    r"[a-z0-9]+(?:(?:__|[._]|-+)[a-z0-9]+)*\Z"
)
_ENDPOINT_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")


class ReployProtocolError(ValueError):
    """A public Reploy controlled-session message violated the v1 contract."""


class OperationV1(str, Enum):
    INPUT = "input"
    RESIZE = "resize"
    TERMINATE = "terminate"
    COMPLETE = "complete"


class TerminationCauseV1(str, Enum):
    CONTROLLER_TERMINATE = "controller-terminate"
    WORKLOAD_EXIT = "workload-exit"
    HOST_CANCEL = "host-cancel"
    CONTROLLER_LOST = "controller-lost"
    RUNTIME_OBSERVATION_LOST = "runtime-observation-lost"
    CLEANUP_CONTAINMENT_LOST = "cleanup-containment-lost"
    STARTUP_FAILURE = "startup-failure"


class ProcessStatusKindV1(str, Enum):
    UNKNOWN = "unknown"
    EXITED = "exited"
    TERMINATED = "terminated"
    UNAVAILABLE = "unavailable"


class WorkloadOutputFinalizationStatusKindV1(str, Enum):
    DRAINED = "drained"
    FAILED = "failed"


class RuntimeObservationStatusKindV1(str, Enum):
    MAINTAINED = "maintained"
    LOST = "lost"


class ControllerFinalizationStatusKindV1(str, Enum):
    COMPLETED = "completed"
    LOST = "lost"
    FINALIZATION_TIMEOUT = "finalization-timeout"
    NOT_COMPLETED = "not-completed"
    STARTUP_FAILED = "startup-failed"


class CleanupStatusKindV1(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RecoveryActionV1(str, Enum):
    NONE = "none"
    RETRY_CLEANUP = "retry-cleanup"
    RECONCILE_NEXT_OPERATION = "reconcile-next-operation"


class ControllerOutputKindV1(str, Enum):
    NOT_REQUESTED = "not-requested"
    DIRECTORY_RETAINED = "directory-retained"
    FILE_PUBLISHED = "file-published"
    FILE_DISCARDED = "file-discarded"
    FAILED = "failed"


@dataclass(frozen=True)
class EndpointV1:
    id: str
    scheme: str
    host: str
    port: int


@dataclass(frozen=True)
class ProcessStatusV1:
    kind: ProcessStatusKindV1
    code: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class WorkloadOutputFinalizationStatusV1:
    kind: WorkloadOutputFinalizationStatusKindV1
    reason: str | None = None


@dataclass(frozen=True)
class RuntimeObservationStatusV1:
    kind: RuntimeObservationStatusKindV1
    reason: str | None = None


@dataclass(frozen=True)
class ControllerFinalizationStatusV1:
    kind: ControllerFinalizationStatusKindV1
    reason: str | None = None


@dataclass(frozen=True)
class CleanupStatusV1:
    kind: CleanupStatusKindV1
    message: str | None = None


@dataclass(frozen=True)
class SessionResultV1:
    cause: TerminationCauseV1
    workload_status: ProcessStatusV1
    workload_output_finalization_status: WorkloadOutputFinalizationStatusV1
    runtime_observation_status: RuntimeObservationStatusV1
    controller_finalization_status: ControllerFinalizationStatusV1
    cleanup_status: CleanupStatusV1
    recovery_action: RecoveryActionV1


@dataclass(frozen=True)
class BrokerReadyEventV1:
    terminal_socket: str


@dataclass(frozen=True)
class OpenedEventV1:
    operations: tuple[OperationV1, ...]
    endpoints: tuple[EndpointV1, ...]
    columns: int
    rows: int
    output_finalization_timeout_milliseconds: int


@dataclass(frozen=True)
class ReadyEventV1:
    pass


@dataclass(frozen=True)
class WorkloadExitEventV1:
    status: ProcessStatusV1


@dataclass(frozen=True)
class TerminatingEventV1:
    cause: TerminationCauseV1


@dataclass(frozen=True)
class DiagnosticEventV1:
    code: str
    message: str


@dataclass(frozen=True)
class WorkloadOutputsFinalizedEventV1:
    status: WorkloadOutputFinalizationStatusKindV1
    reason: str | None = None


@dataclass(frozen=True)
class TerminatedEventV1:
    result: SessionResultV1


@dataclass(frozen=True)
class ClientErrorEventV1:
    code: str
    message: str


ClientEventV1: TypeAlias = (
    BrokerReadyEventV1
    | OpenedEventV1
    | ReadyEventV1
    | WorkloadExitEventV1
    | TerminatingEventV1
    | DiagnosticEventV1
    | WorkloadOutputsFinalizedEventV1
    | TerminatedEventV1
    | ClientErrorEventV1
)


@dataclass(frozen=True)
class ResizeRequestV1:
    columns: int
    rows: int


@dataclass(frozen=True)
class TerminateRequestV1:
    pass


@dataclass(frozen=True)
class CompleteRequestV1:
    pass


@dataclass(frozen=True)
class AcknowledgeTerminatedRequestV1:
    pass


ClientRequestV1: TypeAlias = (
    ResizeRequestV1
    | TerminateRequestV1
    | CompleteRequestV1
    | AcknowledgeTerminatedRequestV1
)


@dataclass(frozen=True)
class ControllerOutputStatusV1:
    kind: ControllerOutputKindV1
    reason: str | None = None


@dataclass(frozen=True)
class ControlledSessionRunResultV1:
    ok: bool
    error: str | None
    session_result: SessionResultV1 | None
    result_delivered: bool | None
    result_acknowledged: bool | None
    controller_status: ProcessStatusV1 | None
    controller_output: ControllerOutputStatusV1 | None
    delivery_tail_cleanup_status: CleanupStatusV1 | None
    delivery_tail_recovery_action: RecoveryActionV1 | None


EnumT = TypeVar("EnumT", bound=Enum)


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ReployProtocolError(f"duplicate JSON field {name!r}")
        result[name] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ReployProtocolError(f"non-standard JSON value {value!r}")


def _decode_json_line(
    data: bytes,
    *,
    field: str,
    maximum_bytes: int | None,
) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise ReployProtocolError(f"{field} must be bytes")
    if maximum_bytes is not None and len(data) > maximum_bytes:
        raise ReployProtocolError(
            f"{field} exceeds {maximum_bytes} bytes including its newline"
        )
    if not data.endswith(b"\n"):
        raise ReployProtocolError(f"{field} is not newline terminated")
    payload = data[:-1]
    if not payload:
        raise ReployProtocolError(f"{field} must not be empty")
    if b"\n" in payload or payload.strip() != payload:
        raise ReployProtocolError(f"{field} must contain exactly one JSON object")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReployProtocolError(f"{field} is not valid UTF-8 JSON") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_nonstandard_constant,
        )
    except ReployProtocolError:
        raise
    except json.JSONDecodeError as exc:
        raise ReployProtocolError(f"{field} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ReployProtocolError(f"{field} must contain a JSON object")
    return value


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReployProtocolError(f"{field} must be an object")
    return value


def _fields(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    field: str,
) -> None:
    allowed = required | (optional or set())
    missing = sorted(required - set(value))
    if missing:
        raise ReployProtocolError(
            f"{field} is missing fields: {', '.join(missing)}"
        )
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReployProtocolError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


def _string(value: object, *, field: str, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        requirement = "a non-empty string" if nonempty else "a string"
        raise ReployProtocolError(f"{field} must be {requirement}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReployProtocolError(f"{field} must contain valid Unicode") from exc
    return value


def _safe_text(value: object, *, field: str) -> str:
    text = _string(value, field=field)
    if len(text.encode("utf-8")) > 512 or text.strip() != text:
        raise ReployProtocolError(f"{field} must be non-empty safe text")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in text):
        raise ReployProtocolError(f"{field} must be non-empty safe text")
    return text


def _integer(
    value: object,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReployProtocolError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ReployProtocolError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ReployProtocolError(f"{field} must be at most {maximum}")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ReployProtocolError(f"{field} must be boolean")
    return value


def _enum(value: object, enum_type: type[EnumT], *, field: str) -> EnumT:
    text = _string(value, field=field)
    try:
        return enum_type(text)
    except ValueError as exc:
        raise ReployProtocolError(f"{field} has unsupported value {text!r}") from exc


def _optional_safe_text(
    value: dict[str, Any], *, name: str, field: str
) -> str | None:
    if name not in value:
        return None
    return _safe_text(value[name], field=f"{field}.{name}")


def _parse_endpoint(value: object, *, field: str) -> EndpointV1:
    mapping = _object(value, field=field)
    _fields(
        mapping,
        required={"id", "scheme", "host", "port"},
        field=field,
    )
    endpoint_id = _string(mapping["id"], field=f"{field}.id")
    if len(endpoint_id.encode("utf-8")) > 128 or _ENDPOINT_ID_RE.fullmatch(
        endpoint_id
    ) is None:
        raise ReployProtocolError(f"{field}.id must be a Docker-style endpoint ID")
    scheme = _string(mapping["scheme"], field=f"{field}.scheme")
    if _ENDPOINT_SCHEME_RE.fullmatch(scheme) is None:
        raise ReployProtocolError(f"{field}.scheme must use URI-scheme syntax")
    host = _string(mapping["host"], field=f"{field}.host")
    if host != "workload":
        raise ReployProtocolError(f"{field}.host must be 'workload'")
    port = _integer(mapping["port"], field=f"{field}.port", minimum=1, maximum=65535)
    return EndpointV1(id=endpoint_id, scheme=scheme, host=host, port=port)


def _parse_process_status(
    value: object,
    *,
    field: str,
    allow_unknown: bool,
) -> ProcessStatusV1:
    mapping = _object(value, field=field)
    _fields(
        mapping,
        required={"kind"},
        optional={"code", "reason"},
        field=field,
    )
    kind = _enum(mapping["kind"], ProcessStatusKindV1, field=f"{field}.kind")
    reason = _optional_safe_text(mapping, name="reason", field=field)
    if kind is ProcessStatusKindV1.UNKNOWN:
        if not allow_unknown or set(mapping) != {"kind"}:
            raise ReployProtocolError(
                f"{field} unknown status must not contain code or reason"
            )
        return ProcessStatusV1(kind=kind)
    if kind is ProcessStatusKindV1.EXITED:
        if "code" not in mapping:
            raise ReployProtocolError(f"{field} exited status requires code")
        code = _integer(mapping["code"], field=f"{field}.code")
        return ProcessStatusV1(kind=kind, code=code, reason=reason)
    if "code" in mapping:
        raise ReployProtocolError(f"{field} {kind.value} status must not contain code")
    return ProcessStatusV1(kind=kind, reason=reason)


def _parse_workload_output_status(
    value: object, *, field: str
) -> WorkloadOutputFinalizationStatusV1:
    mapping = _object(value, field=field)
    _fields(mapping, required={"kind"}, optional={"reason"}, field=field)
    kind = _enum(
        mapping["kind"],
        WorkloadOutputFinalizationStatusKindV1,
        field=f"{field}.kind",
    )
    reason = _optional_safe_text(mapping, name="reason", field=field)
    if kind is WorkloadOutputFinalizationStatusKindV1.DRAINED and reason is not None:
        raise ReployProtocolError(f"{field} drained status must not contain reason")
    if kind is WorkloadOutputFinalizationStatusKindV1.FAILED and reason is None:
        raise ReployProtocolError(f"{field} failed status requires reason")
    return WorkloadOutputFinalizationStatusV1(kind=kind, reason=reason)


def _parse_runtime_observation_status(
    value: object, *, field: str
) -> RuntimeObservationStatusV1:
    mapping = _object(value, field=field)
    _fields(mapping, required={"kind"}, optional={"reason"}, field=field)
    kind = _enum(
        mapping["kind"],
        RuntimeObservationStatusKindV1,
        field=f"{field}.kind",
    )
    reason = _optional_safe_text(mapping, name="reason", field=field)
    if kind is RuntimeObservationStatusKindV1.MAINTAINED and reason is not None:
        raise ReployProtocolError(f"{field} maintained status must not contain reason")
    return RuntimeObservationStatusV1(kind=kind, reason=reason)


def _parse_controller_finalization_status(
    value: object, *, field: str
) -> ControllerFinalizationStatusV1:
    mapping = _object(value, field=field)
    _fields(mapping, required={"kind"}, optional={"reason"}, field=field)
    kind = _enum(
        mapping["kind"],
        ControllerFinalizationStatusKindV1,
        field=f"{field}.kind",
    )
    reason = _optional_safe_text(mapping, name="reason", field=field)
    return ControllerFinalizationStatusV1(kind=kind, reason=reason)


def _parse_cleanup_status(value: object, *, field: str) -> CleanupStatusV1:
    mapping = _object(value, field=field)
    _fields(mapping, required={"kind"}, optional={"message"}, field=field)
    kind = _enum(mapping["kind"], CleanupStatusKindV1, field=f"{field}.kind")
    message = _optional_safe_text(mapping, name="message", field=field)
    if kind is CleanupStatusKindV1.SUCCEEDED and message is not None:
        raise ReployProtocolError(f"{field} succeeded status must not contain message")
    if kind is CleanupStatusKindV1.FAILED and message is None:
        raise ReployProtocolError(f"{field} failed status requires message")
    return CleanupStatusV1(kind=kind, message=message)


def _parse_session_result(value: object, *, field: str) -> SessionResultV1:
    mapping = _object(value, field=field)
    _fields(
        mapping,
        required={
            "cause",
            "workload_status",
            "workload_output_finalization_status",
            "runtime_observation_status",
            "controller_finalization_status",
            "cleanup_status",
            "recovery_action",
        },
        field=field,
    )
    cause = _enum(mapping["cause"], TerminationCauseV1, field=f"{field}.cause")
    workload_status = _parse_process_status(
        mapping["workload_status"],
        field=f"{field}.workload_status",
        allow_unknown=True,
    )
    output_status = _parse_workload_output_status(
        mapping["workload_output_finalization_status"],
        field=f"{field}.workload_output_finalization_status",
    )
    observation_status = _parse_runtime_observation_status(
        mapping["runtime_observation_status"],
        field=f"{field}.runtime_observation_status",
    )
    controller_status = _parse_controller_finalization_status(
        mapping["controller_finalization_status"],
        field=f"{field}.controller_finalization_status",
    )
    cleanup_status = _parse_cleanup_status(
        mapping["cleanup_status"], field=f"{field}.cleanup_status"
    )
    recovery_action = _enum(
        mapping["recovery_action"],
        RecoveryActionV1,
        field=f"{field}.recovery_action",
    )

    if (
        cause is TerminationCauseV1.WORKLOAD_EXIT
        and workload_status.kind is ProcessStatusKindV1.UNKNOWN
    ):
        raise ReployProtocolError(
            f"{field} workload-exit cause requires known workload status"
        )
    if cause is TerminationCauseV1.RUNTIME_OBSERVATION_LOST:
        if observation_status.kind is not RuntimeObservationStatusKindV1.LOST:
            raise ReployProtocolError(
                f"{field} runtime-observation-lost cause requires lost observation"
            )
        if output_status.kind is not WorkloadOutputFinalizationStatusKindV1.FAILED:
            raise ReployProtocolError(
                f"{field} runtime-observation-lost cause requires failed "
                "output finalization"
            )
    if (
        cause is TerminationCauseV1.CONTROLLER_LOST
        and controller_status.kind is not ControllerFinalizationStatusKindV1.LOST
    ):
        raise ReployProtocolError(
            f"{field} controller-lost cause requires lost controller finalization"
        )
    if (
        cause is TerminationCauseV1.STARTUP_FAILURE
        and controller_status.kind
        is not ControllerFinalizationStatusKindV1.STARTUP_FAILED
    ):
        raise ReployProtocolError(
            f"{field} startup-failure cause requires startup-failed "
            "controller finalization"
        )
    if cleanup_status.kind is CleanupStatusKindV1.SUCCEEDED:
        if recovery_action is not RecoveryActionV1.NONE:
            raise ReployProtocolError(
                f"{field} successful cleanup must not require recovery"
            )
    elif recovery_action not in {
        RecoveryActionV1.RETRY_CLEANUP,
        RecoveryActionV1.RECONCILE_NEXT_OPERATION,
    }:
        raise ReployProtocolError(
            f"{field} failed cleanup requires a recovery action"
        )

    return SessionResultV1(
        cause=cause,
        workload_status=workload_status,
        workload_output_finalization_status=output_status,
        runtime_observation_status=observation_status,
        controller_finalization_status=controller_status,
        cleanup_status=cleanup_status,
        recovery_action=recovery_action,
    )


def _parse_protocol_code(value: object, *, field: str) -> str:
    code = _string(value, field=field)
    if len(code.encode("utf-8")) > 63 or _PROTOCOL_CODE_RE.fullmatch(code) is None:
        raise ReployProtocolError(
            f"{field} must be a lowercase ASCII snake_case identifier"
        )
    return code


def _event_envelope(value: dict[str, Any]) -> str:
    if "schema" not in value or "type" not in value:
        _fields(value, required={"schema", "type"}, field="controller event")
    schema = _string(value["schema"], field="controller event.schema")
    if schema != CLIENT_SCHEMA_V1:
        raise ReployProtocolError(
            f"controller event.schema must be {CLIENT_SCHEMA_V1!r}"
        )
    return _string(value["type"], field="controller event.type")


def decode_client_event_v1(line: bytes) -> ClientEventV1:
    """Decode one newline-terminated controller event."""

    value = _decode_json_line(
        line,
        field="controller event",
        maximum_bytes=MAX_CLIENT_MESSAGE_BYTES_V1,
    )
    kind = _event_envelope(value)
    base = {"schema", "type"}
    if kind == "broker-ready":
        _fields(value, required=base | {"terminal_socket"}, field="broker-ready")
        return BrokerReadyEventV1(
            terminal_socket=_string(
                value["terminal_socket"], field="broker-ready.terminal_socket"
            )
        )
    if kind == "opened":
        _fields(
            value,
            required=base
            | {
                "operations",
                "endpoints",
                "columns",
                "rows",
                "output_finalization_timeout_milliseconds",
            },
            field="opened",
        )
        raw_operations = value["operations"]
        if not isinstance(raw_operations, list):
            raise ReployProtocolError("opened.operations must be an array")
        operations = tuple(
            _enum(item, OperationV1, field=f"opened.operations.{index}")
            for index, item in enumerate(raw_operations)
        )
        if len(set(operations)) != len(operations):
            raise ReployProtocolError("opened.operations must be unique")
        raw_endpoints = value["endpoints"]
        if not isinstance(raw_endpoints, list):
            raise ReployProtocolError("opened.endpoints must be an array")
        endpoints = tuple(
            _parse_endpoint(item, field=f"opened.endpoints.{index}")
            for index, item in enumerate(raw_endpoints)
        )
        endpoint_ids = [endpoint.id for endpoint in endpoints]
        if len(set(endpoint_ids)) != len(endpoint_ids):
            raise ReployProtocolError("opened.endpoints IDs must be unique")
        return OpenedEventV1(
            operations=operations,
            endpoints=endpoints,
            columns=_integer(
                value["columns"], field="opened.columns", minimum=1, maximum=65535
            ),
            rows=_integer(
                value["rows"], field="opened.rows", minimum=1, maximum=65535
            ),
            output_finalization_timeout_milliseconds=_integer(
                value["output_finalization_timeout_milliseconds"],
                field="opened.output_finalization_timeout_milliseconds",
                minimum=1,
                maximum=(1 << 32) - 1,
            ),
        )
    if kind == "ready":
        _fields(value, required=base, field="ready")
        return ReadyEventV1()
    if kind == "workload-exit":
        _fields(value, required=base | {"status"}, field="workload-exit")
        return WorkloadExitEventV1(
            status=_parse_process_status(
                value["status"], field="workload-exit.status", allow_unknown=False
            )
        )
    if kind == "terminating":
        _fields(value, required=base | {"cause"}, field="terminating")
        return TerminatingEventV1(
            cause=_enum(
                value["cause"], TerminationCauseV1, field="terminating.cause"
            )
        )
    if kind in {"diagnostic", "client-error"}:
        _fields(value, required=base | {"code", "message"}, field=kind)
        code = _parse_protocol_code(value["code"], field=f"{kind}.code")
        message = _safe_text(value["message"], field=f"{kind}.message")
        if kind == "diagnostic":
            return DiagnosticEventV1(code=code, message=message)
        return ClientErrorEventV1(code=code, message=message)
    if kind == "workload-outputs-finalized":
        _fields(
            value,
            required=base | {"status"},
            optional={"reason"},
            field="workload-outputs-finalized",
        )
        status = _enum(
            value["status"],
            WorkloadOutputFinalizationStatusKindV1,
            field="workload-outputs-finalized.status",
        )
        reason = _optional_safe_text(
            value, name="reason", field="workload-outputs-finalized"
        )
        if status is WorkloadOutputFinalizationStatusKindV1.DRAINED:
            if reason is not None:
                raise ReployProtocolError(
                    "workload-outputs-finalized drained status must not contain reason"
                )
        elif reason is None:
            raise ReployProtocolError(
                "workload-outputs-finalized failed status requires reason"
            )
        return WorkloadOutputsFinalizedEventV1(status=status, reason=reason)
    if kind == "terminated":
        _fields(value, required=base | {"result"}, field="terminated")
        return TerminatedEventV1(
            result=_parse_session_result(value["result"], field="terminated.result")
        )
    raise ReployProtocolError(f"controller event.type has unsupported value {kind!r}")


def _request_envelope(value: dict[str, Any]) -> str:
    if "schema" not in value or "type" not in value:
        _fields(value, required={"schema", "type"}, field="controller request")
    schema = _string(value["schema"], field="controller request.schema")
    if schema != CLIENT_SCHEMA_V1:
        raise ReployProtocolError(
            f"controller request.schema must be {CLIENT_SCHEMA_V1!r}"
        )
    return _string(value["type"], field="controller request.type")


def decode_client_request_v1(line: bytes) -> ClientRequestV1:
    """Decode one newline-terminated controller request for conformance tests."""

    value = _decode_json_line(
        line,
        field="controller request",
        maximum_bytes=MAX_CLIENT_MESSAGE_BYTES_V1,
    )
    kind = _request_envelope(value)
    base = {"schema", "type"}
    if kind == "resize":
        _fields(
            value,
            required=base | {"columns", "rows"},
            field="resize request",
        )
        return ResizeRequestV1(
            columns=_integer(
                value["columns"],
                field="resize request.columns",
                minimum=1,
                maximum=65535,
            ),
            rows=_integer(
                value["rows"],
                field="resize request.rows",
                minimum=1,
                maximum=65535,
            ),
        )
    request_types: dict[str, ClientRequestV1] = {
        "terminate": TerminateRequestV1(),
        "complete": CompleteRequestV1(),
        "acknowledge-terminated": AcknowledgeTerminatedRequestV1(),
    }
    if kind in request_types:
        _fields(value, required=base, field=f"{kind} request")
        return request_types[kind]
    raise ReployProtocolError(f"controller request.type has unsupported value {kind!r}")


def encode_client_request_v1(request: ClientRequestV1) -> bytes:
    """Encode one controller request as exact compact UTF-8 JSON Lines."""

    value: dict[str, object] = {"schema": CLIENT_SCHEMA_V1}
    if isinstance(request, ResizeRequestV1):
        value.update(
            {
                "type": "resize",
                "columns": _integer(
                    request.columns,
                    field="resize request.columns",
                    minimum=1,
                    maximum=65535,
                ),
                "rows": _integer(
                    request.rows,
                    field="resize request.rows",
                    minimum=1,
                    maximum=65535,
                ),
            }
        )
    elif isinstance(request, TerminateRequestV1):
        value["type"] = "terminate"
    elif isinstance(request, CompleteRequestV1):
        value["type"] = "complete"
    elif isinstance(request, AcknowledgeTerminatedRequestV1):
        value["type"] = "acknowledge-terminated"
    else:
        raise ReployProtocolError(
            f"unsupported controller request {type(request).__name__}"
        )
    result = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
        + b"\n"
    )
    if len(result) > MAX_CLIENT_MESSAGE_BYTES_V1:
        raise ReployProtocolError(
            f"controller request exceeds {MAX_CLIENT_MESSAGE_BYTES_V1} bytes"
        )
    return result


def _parse_controller_output(
    value: object, *, field: str
) -> ControllerOutputStatusV1:
    mapping = _object(value, field=field)
    _fields(mapping, required={"kind"}, optional={"reason"}, field=field)
    kind = _enum(mapping["kind"], ControllerOutputKindV1, field=f"{field}.kind")
    reason = _optional_safe_text(mapping, name="reason", field=field)
    return ControllerOutputStatusV1(kind=kind, reason=reason)


def _optional_bool(value: object, *, field: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, field=field)


def _run_result_is_successful(result: ControlledSessionRunResultV1) -> bool:
    session = result.session_result
    if result.error is not None or session is None:
        return False
    cause_succeeded = session.cause is TerminationCauseV1.CONTROLLER_TERMINATE
    if session.cause is TerminationCauseV1.WORKLOAD_EXIT:
        cause_succeeded = (
            session.workload_status.kind is ProcessStatusKindV1.EXITED
            and session.workload_status.code == 0
        )
    output_succeeded = (
        result.controller_output is not None
        and result.controller_output.kind
        in {
            ControllerOutputKindV1.NOT_REQUESTED,
            ControllerOutputKindV1.DIRECTORY_RETAINED,
            ControllerOutputKindV1.FILE_PUBLISHED,
        }
    )
    return (
        cause_succeeded
        and session.workload_output_finalization_status.kind
        is WorkloadOutputFinalizationStatusKindV1.DRAINED
        and session.runtime_observation_status.kind
        is RuntimeObservationStatusKindV1.MAINTAINED
        and session.controller_finalization_status.kind
        is ControllerFinalizationStatusKindV1.COMPLETED
        and session.cleanup_status.kind is CleanupStatusKindV1.SUCCEEDED
        and session.recovery_action is RecoveryActionV1.NONE
        and result.result_delivered is True
        and result.result_acknowledged is True
        and result.delivery_tail_cleanup_status is not None
        and result.delivery_tail_cleanup_status.kind is CleanupStatusKindV1.SUCCEEDED
        and result.delivery_tail_recovery_action is RecoveryActionV1.NONE
        and output_succeeded
    )


def decode_run_result_v1(line: bytes) -> ControlledSessionRunResultV1:
    """Decode the single structured result from ``reploy controlled-session``."""

    value = _decode_json_line(
        line,
        field="controlled-session host result",
        maximum_bytes=None,
    )
    _fields(
        value,
        required={
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
        },
        field="controlled-session host result",
    )
    schema = _string(value["schema"], field="controlled-session host result.schema")
    if schema != RUN_RESULT_SCHEMA_V1:
        raise ReployProtocolError(
            f"controlled-session host result.schema must be {RUN_RESULT_SCHEMA_V1!r}"
        )
    error_value = value["error"]
    error = (
        None
        if error_value is None
        else _string(error_value, field="controlled-session host result.error")
    )
    session_result = (
        None
        if value["session_result"] is None
        else _parse_session_result(
            value["session_result"],
            field="controlled-session host result.session_result",
        )
    )
    result_delivered = _optional_bool(
        value["result_delivered"],
        field="controlled-session host result.result_delivered",
    )
    result_acknowledged = _optional_bool(
        value["result_acknowledged"],
        field="controlled-session host result.result_acknowledged",
    )
    if session_result is None:
        if result_delivered is not None or result_acknowledged is not None:
            raise ReployProtocolError(
                "controlled-session host result without session_result must use "
                "null delivery fields"
            )
    elif result_delivered is None or result_acknowledged is None:
        raise ReployProtocolError(
            "controlled-session host result with session_result requires "
            "delivery fields"
        )
    controller_status = (
        None
        if value["controller_status"] is None
        else _parse_process_status(
            value["controller_status"],
            field="controlled-session host result.controller_status",
            allow_unknown=True,
        )
    )
    controller_output = (
        None
        if value["controller_output"] is None
        else _parse_controller_output(
            value["controller_output"],
            field="controlled-session host result.controller_output",
        )
    )
    delivery_tail_cleanup_status = (
        None
        if value["delivery_tail_cleanup_status"] is None
        else _parse_cleanup_status(
            value["delivery_tail_cleanup_status"],
            field="controlled-session host result.delivery_tail_cleanup_status",
        )
    )
    delivery_tail_recovery_action = (
        None
        if value["delivery_tail_recovery_action"] is None
        else _enum(
            value["delivery_tail_recovery_action"],
            RecoveryActionV1,
            field="controlled-session host result.delivery_tail_recovery_action",
        )
    )
    if delivery_tail_cleanup_status is not None:
        if delivery_tail_cleanup_status.kind is CleanupStatusKindV1.SUCCEEDED:
            if delivery_tail_recovery_action is not RecoveryActionV1.NONE:
                raise ReployProtocolError(
                    "successful delivery-tail cleanup must not require recovery"
                )
        elif delivery_tail_recovery_action not in {
            RecoveryActionV1.RETRY_CLEANUP,
            RecoveryActionV1.RECONCILE_NEXT_OPERATION,
        }:
            raise ReployProtocolError(
                "failed delivery-tail cleanup requires a recovery action"
            )
    elif delivery_tail_recovery_action is not None:
        raise ReployProtocolError(
            "delivery-tail recovery action requires cleanup status"
        )

    result = ControlledSessionRunResultV1(
        ok=_boolean(value["ok"], field="controlled-session host result.ok"),
        error=error,
        session_result=session_result,
        result_delivered=result_delivered,
        result_acknowledged=result_acknowledged,
        controller_status=controller_status,
        controller_output=controller_output,
        delivery_tail_cleanup_status=delivery_tail_cleanup_status,
        delivery_tail_recovery_action=delivery_tail_recovery_action,
    )
    if result.ok != _run_result_is_successful(result):
        raise ReployProtocolError(
            "controlled-session host result.ok contradicts its structured fields"
        )
    return result
