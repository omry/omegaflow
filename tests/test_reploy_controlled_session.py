from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from omegaflow.reploy_controlled_session import (
    AcknowledgeTerminatedRequestV1,
    BrokerReadyEventV1,
    CleanupStatusKindV1,
    ClientErrorEventV1,
    CompleteRequestV1,
    ControllerFinalizationStatusKindV1,
    ControllerOutputKindV1,
    DiagnosticEventV1,
    OpenedEventV1,
    OperationV1,
    ProcessStatusKindV1,
    ReadyEventV1,
    RecoveryActionV1,
    ReployProtocolError,
    ResizeRequestV1,
    TerminateRequestV1,
    TerminatedEventV1,
    TerminatingEventV1,
    TerminationCauseV1,
    WorkloadExitEventV1,
    WorkloadOutputFinalizationStatusKindV1,
    WorkloadOutputsFinalizedEventV1,
    decode_client_event_v1,
    decode_client_request_v1,
    decode_run_result_v1,
    encode_client_request_v1,
)


FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "reploy" / "controlled-session-v1"
)


def _lines(name: str) -> list[bytes]:
    return (FIXTURE_DIR / name).read_bytes().splitlines(keepends=True)


def _json_line(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def _decoded_json_line(line: bytes) -> dict[str, object]:
    value = json.loads(line)
    assert isinstance(value, dict)
    return value


def test_vendored_reploy_fixture_manifest_matches_every_file() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["reploy_version"] == "0.7.0.dev1"
    assert (
        manifest["source_commit"]
        == "cb4ed96519b97ecdee6dca28edc78a74c045f7c2"
    )
    assert set(manifest["files"]) == {
        "client-v1-events.jsonl",
        "client-v1-invalid-requests.jsonl",
        "client-v1-requests.jsonl",
        "omegaflow-conformance-v1.json",
        "run-results-v1.jsonl",
    }
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest() == expected


def test_decodes_every_public_controller_event_fixture() -> None:
    events = [decode_client_event_v1(line) for line in _lines("client-v1-events.jsonl")]

    assert [type(event) for event in events] == [
        BrokerReadyEventV1,
        OpenedEventV1,
        ReadyEventV1,
        WorkloadExitEventV1,
        TerminatingEventV1,
        DiagnosticEventV1,
        WorkloadOutputsFinalizedEventV1,
        WorkloadOutputsFinalizedEventV1,
        TerminatedEventV1,
        ClientErrorEventV1,
    ]
    broker = events[0]
    assert isinstance(broker, BrokerReadyEventV1)
    assert broker.terminal_socket.endswith("/terminal.sock")
    opened = events[1]
    assert isinstance(opened, OpenedEventV1)
    assert opened.operations == (
        OperationV1.INPUT,
        OperationV1.RESIZE,
        OperationV1.TERMINATE,
        OperationV1.COMPLETE,
    )
    assert opened.endpoints[0].id == "api"
    assert opened.endpoints[0].host == "workload"
    assert opened.endpoints[0].port == 8080
    workload_exit = events[3]
    assert isinstance(workload_exit, WorkloadExitEventV1)
    assert workload_exit.status.kind is ProcessStatusKindV1.EXITED
    assert workload_exit.status.code == 0
    terminating = events[4]
    assert isinstance(terminating, TerminatingEventV1)
    assert terminating.cause is TerminationCauseV1.WORKLOAD_EXIT
    diagnostic = events[5]
    assert isinstance(diagnostic, DiagnosticEventV1)
    assert diagnostic.code == "future_diagnostic"
    failed_outputs = events[7]
    assert isinstance(failed_outputs, WorkloadOutputsFinalizedEventV1)
    assert failed_outputs.status is WorkloadOutputFinalizationStatusKindV1.FAILED
    assert failed_outputs.reason == "Output drain expired."
    terminated = events[8]
    assert isinstance(terminated, TerminatedEventV1)
    assert terminated.result.cleanup_status.kind is CleanupStatusKindV1.SUCCEEDED
    assert terminated.result.recovery_action is RecoveryActionV1.NONE
    client_error = events[9]
    assert isinstance(client_error, ClientErrorEventV1)
    assert client_error.code == "future_client_error"


def test_decodes_and_exactly_reencodes_every_public_request_fixture() -> None:
    lines = _lines("client-v1-requests.jsonl")
    requests = [decode_client_request_v1(line) for line in lines]

    assert requests == [
        ResizeRequestV1(columns=120, rows=40),
        TerminateRequestV1(),
        CompleteRequestV1(),
        AcknowledgeTerminatedRequestV1(),
    ]
    assert [encode_client_request_v1(request) for request in requests] == lines


@pytest.mark.parametrize(
    "line",
    _lines("client-v1-invalid-requests.jsonl"),
)
def test_rejects_every_public_invalid_request_fixture(line: bytes) -> None:
    with pytest.raises(ReployProtocolError):
        decode_client_request_v1(line)


def test_decodes_every_public_host_result_fixture() -> None:
    results = [decode_run_result_v1(line) for line in _lines("run-results-v1.jsonl")]

    assert [result.ok for result in results] == [
        False,
        False,
        True,
        True,
        True,
        False,
        False,
    ]
    assert results[0].error == "admission rejected"
    assert results[0].session_result is None
    assert results[0].result_delivered is None
    assert results[1].controller_output is not None
    assert results[1].controller_output.kind is ControllerOutputKindV1.NOT_REQUESTED
    assert results[2].session_result is not None
    assert (
        results[2].session_result.controller_finalization_status.kind
        is ControllerFinalizationStatusKindV1.COMPLETED
    )
    assert results[3].controller_output is not None
    assert (
        results[3].controller_output.kind
        is ControllerOutputKindV1.DIRECTORY_RETAINED
    )
    assert results[4].controller_output is not None
    assert results[4].controller_output.kind is ControllerOutputKindV1.FILE_PUBLISHED
    assert results[5].controller_output is not None
    assert results[5].controller_output.kind is ControllerOutputKindV1.FILE_DISCARDED
    assert results[6].controller_output is not None
    assert results[6].controller_output.kind is ControllerOutputKindV1.FAILED


@pytest.mark.parametrize(
    ("line", "match"),
    [
        (b"", "newline terminated"),
        (b"{}", "newline terminated"),
        (b"\n", "must not be empty"),
        (b" {}\n", "exactly one JSON object"),
        (b"{} \n", "exactly one JSON object"),
        (b"{}\n{}\n", "exactly one JSON object"),
        (b"\xff\n", "not valid UTF-8"),
        (b"[]\n", "must contain a JSON object"),
        (b'{"schema":NaN}\n', "non-standard JSON value"),
        (
            b'{"schema":"reploy-controlled-session-client-v1",'
            b'"type":"ready","type":"ready"}\n',
            "duplicate JSON field",
        ),
    ],
)
def test_controller_event_rejects_invalid_jsonl_envelopes(
    line: bytes, match: str
) -> None:
    with pytest.raises(ReployProtocolError, match=match):
        decode_client_event_v1(line)


def test_controller_event_rejects_oversized_message() -> None:
    line = b"{" + b" " * ((1 << 20) - 1) + b"}\n"

    with pytest.raises(ReployProtocolError, match="exceeds"):
        decode_client_event_v1(line)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"schema": "reploy-controlled-session-client-v2"}, "schema"),
        ({"type": "future-event"}, "unsupported"),
        ({"extra": True}, "unknown fields"),
    ],
)
def test_ready_event_rejects_unknown_schema_type_or_field(
    mutation: dict[str, object], match: str
) -> None:
    value: dict[str, object] = {
        "schema": "reploy-controlled-session-client-v1",
        "type": "ready",
    }
    value.update(mutation)

    with pytest.raises(ReployProtocolError, match=match):
        decode_client_event_v1(_json_line(value))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("columns", True, "integer"),
        ("columns", 0, "at least"),
        ("rows", 65536, "at most"),
        ("output_finalization_timeout_milliseconds", 0, "at least"),
        ("operations", ["input", "input"], "unique"),
        ("operations", ["future"], "unsupported"),
        (
            "endpoints",
            [{"id": "API", "scheme": "http", "host": "workload", "port": 80}],
            "Docker-style",
        ),
        (
            "endpoints",
            [{"id": "api", "scheme": "http", "host": "other", "port": 80}],
            "host must",
        ),
        (
            "endpoints",
            [{"id": "api", "scheme": "http", "host": "workload", "port": 0}],
            "at least",
        ),
    ],
)
def test_opened_event_rejects_invalid_public_fields(
    field: str, value: object, match: str
) -> None:
    opened = _decoded_json_line(_lines("client-v1-events.jsonl")[1])
    opened[field] = value

    with pytest.raises(ReployProtocolError, match=match):
        decode_client_event_v1(_json_line(opened))


def test_diagnostic_codes_are_open_but_must_follow_public_grammar() -> None:
    future = {
        "schema": "reploy-controlled-session-client-v1",
        "type": "diagnostic",
        "code": "future_code_2",
        "message": "A future diagnostic.",
    }

    assert decode_client_event_v1(_json_line(future)) == DiagnosticEventV1(
        code="future_code_2", message="A future diagnostic."
    )
    future["code"] = "Future-Code"
    with pytest.raises(ReployProtocolError, match="snake_case"):
        decode_client_event_v1(_json_line(future))


@pytest.mark.parametrize(
    "message",
    [
        ResizeRequestV1(columns=0, rows=24),
        ResizeRequestV1(columns=80, rows=True),
        ResizeRequestV1(columns=65536, rows=24),
    ],
)
def test_request_encoder_rejects_invalid_dimensions(message: ResizeRequestV1) -> None:
    with pytest.raises(ReployProtocolError):
        encode_client_request_v1(message)


def test_host_result_distinguishes_null_from_missing_fields() -> None:
    value = _decoded_json_line(_lines("run-results-v1.jsonl")[0])
    del value["controller_status"]

    with pytest.raises(ReployProtocolError, match="missing fields: controller_status"):
        decode_run_result_v1(_json_line(value))


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"ok": 1}, "must be boolean"),
        ({"schema": "reploy-controlled-session-run-result-v2"}, "schema"),
        ({"extra": None}, "unknown fields"),
        ({"result_delivered": False}, "must use null delivery fields"),
        ({"delivery_tail_recovery_action": "none"}, "requires cleanup status"),
    ],
)
def test_host_result_rejects_invalid_top_level_shapes(
    mutation: dict[str, object], match: str
) -> None:
    value = _decoded_json_line(_lines("run-results-v1.jsonl")[0])
    value.update(mutation)

    with pytest.raises(ReployProtocolError, match=match):
        decode_run_result_v1(_json_line(value))


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"ok": False}, "ok contradicts"),
        ({"result_acknowledged": False}, "ok contradicts"),
        (
            {"controller_output": {"kind": "failed", "reason": "disk full"}},
            "ok contradicts",
        ),
        (
            {"delivery_tail_recovery_action": "retry-cleanup"},
            "successful delivery-tail cleanup",
        ),
    ],
)
def test_host_result_rejects_contradictory_success(
    mutation: dict[str, object], match: str
) -> None:
    value = _decoded_json_line(_lines("run-results-v1.jsonl")[2])
    value.update(mutation)

    with pytest.raises(ReployProtocolError, match=match):
        decode_run_result_v1(_json_line(value))


@pytest.mark.parametrize(
    "output",
    [
        {"kind": "failed"},
        {"kind": "file-discarded"},
        {"kind": "directory-retained", "reason": "Retained after failure."},
    ],
)
def test_controller_output_reason_is_optional_for_every_disposition(
    output: dict[str, object],
) -> None:
    value = _decoded_json_line(_lines("run-results-v1.jsonl")[0])
    value["controller_output"] = output

    result = decode_run_result_v1(_json_line(value))

    assert result.controller_output is not None
    assert result.controller_output.reason == output.get("reason")


def test_session_result_rejects_cross_field_contradictions() -> None:
    value = _decoded_json_line(_lines("client-v1-events.jsonl")[8])
    result = value["result"]
    assert isinstance(result, dict)
    result["cause"] = "controller-lost"

    with pytest.raises(ReployProtocolError, match="lost controller finalization"):
        decode_client_event_v1(_json_line(value))


def test_public_values_are_immutable() -> None:
    event = decode_client_event_v1(_lines("client-v1-events.jsonl")[0])
    assert isinstance(event, BrokerReadyEventV1)

    with pytest.raises(AttributeError):
        event.terminal_socket = "other"  # type: ignore[misc]
    assert replace(event, terminal_socket="other").terminal_socket == "other"
