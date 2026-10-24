from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from omegaflow.reploy_codecs import (
    CLIENT_SCHEMA,
    MAX_LINE_BYTES,
    ReployCodecError,
    decode_event,
    decode_request,
    encode_event,
    encode_request,
)


FIXTURES = Path(__file__).parent / "fixtures" / "reploy-codecs"
EVENT_LINES = (FIXTURES / "client-v1-events.jsonl").read_bytes().splitlines(keepends=True)
REQUEST_LINES = (FIXTURES / "client-v1-requests.jsonl").read_bytes().splitlines(keepends=True)
EVENTS = [json.loads(line) for line in EVENT_LINES]
REQUESTS = [json.loads(line) for line in REQUEST_LINES]
OPENED = next(v for v in EVENTS if v["type"] == "opened")
RESULT = next(v for v in EVENTS if v["type"] == "terminated")["result"]


def wire(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"


def event(kind, **payload):
    return {"schema": CLIENT_SCHEMA, "type": kind, **payload}


def reject_event(value):
    with pytest.raises(ReployCodecError):
        encode_event(value)
    with pytest.raises(ReployCodecError):
        decode_event(wire(value))


def reject_request(value):
    with pytest.raises(ReployCodecError):
        encode_request(value)
    with pytest.raises(ReployCodecError):
        decode_request(wire(value))


def terminated(**updates):
    result = deepcopy(RESULT)
    result.update(updates)
    return event("terminated", result=result)


@pytest.mark.parametrize("line", EVENT_LINES, ids=lambda line: json.loads(line)["type"])
def test_C1_S001_event_goldens(line):
    value = json.loads(line)
    assert decode_event(line) == value
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("line", REQUEST_LINES, ids=lambda line: json.loads(line)["type"])
def test_C1_S001_request_goldens(line):
    value = json.loads(line)
    assert decode_request(line) == value
    assert decode_request(encode_request(value)) == value


def test_C1_S001_frozen_public_fixture_provenance():
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    assert provenance["revision"] == "7dfc956ceaa18638260d380f53d12bc4701dcda2"
    for name, source in provenance["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == source["sha256"]
    assert {v["type"] for v in EVENTS} == {
        "broker-ready", "opened", "ready", "workload-exit", "terminating",
        "diagnostic", "workload-outputs-finalized", "terminated", "client-error",
    }
    assert {v["type"] for v in REQUESTS} == {
        "resize", "terminate", "complete", "acknowledge-terminated",
    }


def test_C1_S001_invalid_request_goldens():
    lines = (FIXTURES / "client-v1-invalid-requests.jsonl").read_bytes().splitlines(keepends=True)
    assert len(lines) == 5
    for line in lines:
        with pytest.raises(ReployCodecError):
            decode_request(line)


@pytest.mark.parametrize("value", EVENTS + REQUESTS, ids=lambda v: v["type"])
def test_C1_S002_required_and_unknown_top_level_fields(value):
    reject = reject_request if value in REQUESTS else reject_event
    for field in value:
        invalid = deepcopy(value)
        del invalid[field]
        reject(invalid)
    for name in ("future_field", "Type", "SCHEMA", "", "τύπος"):
        reject({**value, name: True})


@pytest.mark.parametrize("decode", [decode_event, decode_request])
@pytest.mark.parametrize("line", [
    b"", b"\n", b"{}", b"{}\r\n", b" {}\n", b"{} \n", b"{}\n{}\n",
    b"{}{}\n", b"{\n}\n", b"[]\n", b"null\n", b"true\n", b"42\n",
    b"{\"schema\":\"\xff\"}\n", b"\xef\xbb\xbf{}\n", b"{\"x\":NaN}\n",
    b"{\"x\":Infinity}\n", b"{\"x\":-Infinity}\n", b"{\"x\":01}\n",
    b"{\"x\":1,}\n", b"{\"x\":\"unescaped\x00\"}\n",
])
def test_C1_S002_invalid_lexical_frames(decode, line):
    with pytest.raises(ReployCodecError):
        decode(line)


@pytest.mark.parametrize("decode", [decode_event, decode_request])
@pytest.mark.parametrize("value", [None, "{}\n", bytearray(b"{}\n"), memoryview(b"{}\n"), 1])
def test_C1_S002_wire_input_is_bytes(decode, value):
    with pytest.raises(ReployCodecError):
        decode(value)


@pytest.mark.parametrize("kind", [None, True, 1, [], {}, "input", "READY", "future-event"])
def test_C1_S002_unknown_or_mistyped_message_type(kind):
    reject_event(event(kind))
    reject_request(event(kind))


@pytest.mark.parametrize("schema", [None, True, 1, [], {}, "reploy-controlled-session-client-v2"])
def test_C1_S002_exact_schema(schema):
    reject_event({"schema": schema, "type": "ready"})
    reject_request({"schema": schema, "type": "complete"})


@pytest.mark.parametrize("field_path", [
    ("status",), ("result",), ("result", "workload_status"),
    ("result", "workload_output_finalization_status"),
    ("result", "runtime_observation_status"),
    ("result", "controller_finalization_status"), ("result", "cleanup_status"),
])
def test_C1_S002_unknown_and_duplicate_nested_fields(field_path):
    value = event("workload-exit", status={"kind": "exited", "code": 0}) if field_path == ("status",) else terminated()
    child = value
    for key in field_path:
        child = child[key]
    child["future_field"] = 1
    reject_event(value)
    del child["future_field"]
    first = next(iter(child))
    encoded_child = json.dumps(child, separators=(",", ":")).encode()
    duplicate = encoded_child[:-1] + b"," + json.dumps(first).encode() + b":" + json.dumps(child[first]).encode() + b"}"
    line = wire(value).replace(encoded_child, duplicate, 1)
    assert line != wire(value)
    with pytest.raises(ReployCodecError, match="duplicate"):
        decode_event(line)


def test_C1_S002_top_level_duplicate_and_excessive_nesting():
    line = wire(event("ready")).replace(b'"type":"ready"', b'"type":"ready","type":"ready"')
    with pytest.raises(ReployCodecError, match="duplicate"):
        decode_event(line)
    line = b'{"unknown":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}\n"
    with pytest.raises(ReployCodecError):
        decode_event(line)


@pytest.mark.parametrize("decode,kind", [(decode_event, "ready"), (decode_request, "terminate")])
def test_C1_S003_exact_maximum_line_includes_LF(decode, kind):
    compact = wire(event(kind))
    line = compact[:-2] + b" " * (MAX_LINE_BYTES - len(compact)) + b"}\n"
    assert len(line) == MAX_LINE_BYTES
    assert decode(line) == event(kind)
    with pytest.raises(ReployCodecError, match="bounds"):
        decode(line[:-2] + b" }\n")


def test_C1_S003_encoder_maximum_and_UTF8_byte_bound():
    overhead = len(encode_event(event("broker-ready", terminal_socket="x"))) - 1
    maximum = event("broker-ready", terminal_socket="x" * (MAX_LINE_BYTES - overhead))
    line = encode_event(maximum)
    assert len(line) == MAX_LINE_BYTES
    assert decode_event(line) == maximum
    maximum["terminal_socket"] += "x"
    with pytest.raises(ReployCodecError, match="bounds"):
        encode_event(maximum)
    maximum["terminal_socket"] = "é" * ((MAX_LINE_BYTES - overhead) // 2 + 1)
    assert len(maximum["terminal_socket"]) < MAX_LINE_BYTES
    with pytest.raises(ReployCodecError, match="bounds"):
        encode_event(maximum)


@pytest.mark.parametrize("bad", [0, -1, 65536, True, False, None, "80", 80.0, [], {}])
@pytest.mark.parametrize("field", ["columns", "rows"])
def test_C1_S004_dimension_rejection(bad, field):
    request = event("resize", columns=80, rows=24)
    request[field] = bad
    reject_request(request)
    opened = deepcopy(OPENED)
    opened[field] = bad
    reject_event(opened)


@pytest.mark.parametrize("dimension", [1, 65535])
def test_C1_S004_dimensions_and_timeout_boundaries(dimension):
    request = event("resize", columns=dimension, rows=dimension)
    assert decode_request(encode_request(request)) == request
    opened = deepcopy(OPENED)
    opened.update(columns=dimension, rows=dimension)
    for timeout in [1, (1 << 32) - 1]:
        opened["output_finalization_timeout_milliseconds"] = timeout
        assert decode_event(encode_event(opened)) == opened


@pytest.mark.parametrize("bad", [0, -1, 1 << 32, True, None, 1.0, "30000"])
def test_C1_S004_timeout_rejection(bad):
    opened = deepcopy(OPENED)
    opened["output_finalization_timeout_milliseconds"] = bad
    reject_event(opened)


@pytest.mark.parametrize("field,bad", [
    ("operations", None), ("operations", {}), ("operations", "input"),
    ("operations", ["input", "input"]), ("operations", ["unknown"]),
    ("operations", [None]), ("operations", [[]]), ("endpoints", None),
    ("endpoints", {}), ("endpoints", "api"), ("endpoints", [None]),
])
def test_C1_S004_collection_types_and_grants(field, bad):
    opened = deepcopy(OPENED)
    opened[field] = bad
    reject_event(opened)


def test_C1_S004_empty_and_non_sorted_grants_and_duplicate_endpoint():
    opened = deepcopy(OPENED)
    opened.update(operations=[], endpoints=[])
    assert decode_event(encode_event(opened)) == opened
    opened["operations"] = ["terminate", "input"]
    assert decode_event(encode_event(opened)) == opened
    opened["endpoints"] = deepcopy(OPENED["endpoints"] * 2)
    reject_event(opened)


@pytest.mark.parametrize("field,bad", [
    ("id", ""), ("id", "API"), ("id", "a/b"), ("id", "a:1"),
    ("id", "a___b"), ("id", "a" * 129), ("id", None),
    ("scheme", ""), ("scheme", "1http"), ("scheme", "http://"),
    ("scheme", None), ("host", "localhost"), ("host", "workload "),
    ("host", None), ("port", 0), ("port", 65536), ("port", True),
    ("port", 80.0), ("port", None),
])
def test_C1_S004_endpoint_rejection(field, bad):
    opened = deepcopy(OPENED)
    opened["endpoints"][0][field] = bad
    reject_event(opened)


@pytest.mark.parametrize("name", ["0", "api.v1", "api_v1", "api__v1", "api--v1", "a" * 128])
@pytest.mark.parametrize("scheme", ["http", "https", "git+ssh", "X.1-2"])
def test_C1_S004_valid_endpoint_syntax(name, scheme):
    opened = deepcopy(OPENED)
    opened["endpoints"][0].update(id=name, scheme=scheme, port=65535)
    assert decode_event(encode_event(opened)) == opened


def test_C1_S004_endpoint_exact_fields_and_opaque_socket():
    for field in OPENED["endpoints"][0]:
        opened = deepcopy(OPENED)
        del opened["endpoints"][0][field]
        reject_event(opened)
    opened = deepcopy(OPENED)
    opened["endpoints"][0]["future"] = 1
    reject_event(opened)
    value = event("broker-ready", terminal_socket=" ../opaque/é.sock ")
    assert decode_event(encode_event(value)) == value
    for bad in ["", None, 1, [], {}, "\ud800"]:
        with pytest.raises(ReployCodecError):
            encode_event(event("broker-ready", terminal_socket=bad))


@pytest.mark.parametrize("status", [
    {"kind": "exited", "code": 0}, {"kind": "exited", "code": -1},
    {"kind": "exited", "code": -(1 << 63)},
    {"kind": "exited", "code": (1 << 63) - 1},
    {"kind": "terminated", "reason": "SIGTERM"},
    {"kind": "unavailable"}, {"kind": "exited", "code": 1, "reason": "Exit failure."},
])
def test_C1_S005_process_statuses(status):
    value = event("workload-exit", status=status)
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("status", [
    None, [], {}, {"kind": "future"}, {"kind": "unknown"},
    {"kind": "exited"}, {"kind": "exited", "code": None},
    {"kind": "exited", "code": True}, {"kind": "exited", "code": 1.0},
    {"kind": "exited", "code": 1 << 63}, {"kind": "exited", "code": -(1 << 63) - 1},
    {"kind": "terminated", "code": 1}, {"kind": "unavailable", "code": None},
    {"kind": "terminated", "reason": None}, {"kind": "terminated", "reason": " bad"},
])
def test_C1_S005_invalid_process_statuses(status):
    reject_event(event("workload-exit", status=status))


@pytest.mark.parametrize("kind", ["diagnostic", "client-error"])
@pytest.mark.parametrize("code", ["future_code_v2", "a", "a" * 63])
def test_C1_S006_unknown_diagnostic_codes_are_data(kind, code):
    value = event(kind, code=code, message="未來 diagnostic.")
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("code", ["", "A", "bad-code", "a__b", "a_", "é", "a" * 64, None, True, 1])
def test_C1_S006_invalid_diagnostic_codes(code):
    reject_event(event("diagnostic", code=code, message="Message."))


@pytest.mark.parametrize("message", [
    "", " leading", "trailing ", "a\nb", "a\tb", "a\x00b", "a\x7fb",
    "a\u0085b", "a\u200bb", "a\u202eb", "\ud800", "x" * 513, "é" * 257,
    None, False, 1, [], {},
])
def test_C1_S006_invalid_safe_text(message):
    reject_event(event("diagnostic", code="valid_code", message=message))


@pytest.mark.parametrize("message", ["x" * 512, "é" * 256, "合法の diagnostic."])
def test_C1_S006_safe_text_UTF8_byte_boundary(message):
    value = event("diagnostic", code="valid_code", message=message)
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("cause", [
    "workload-exit", "controller-terminate", "host-cancel", "controller-lost",
    "runtime-observation-lost", "cleanup-containment-lost", "startup-failure",
])
def test_C1_S007_termination_causes(cause):
    value = event("terminating", cause=cause)
    assert decode_event(encode_event(value)) == value
    result = deepcopy(RESULT)
    result["cause"] = cause
    if cause == "controller-lost":
        result["controller_finalization_status"] = {"kind": "lost"}
    elif cause == "startup-failure":
        result["controller_finalization_status"] = {"kind": "startup-failed"}
        result["workload_status"] = {"kind": "unknown"}
    elif cause == "runtime-observation-lost":
        result["runtime_observation_status"] = {"kind": "lost"}
        result["workload_output_finalization_status"] = {"kind": "failed", "reason": "Output unavailable."}
    value = event("terminated", result=result)
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("kind", ["completed", "lost", "finalization-timeout", "not-completed", "startup-failed"])
def test_C1_S007_terminal_controller_status(kind):
    value = terminated(controller_finalization_status={"kind": kind, "reason": "Final state."})
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("recovery", ["retry-cleanup", "reconcile-next-operation"])
def test_C1_S007_cleanup_failure_preserved(recovery):
    value = terminated(cleanup_status={"kind": "failed", "message": "Cleanup failed."}, recovery_action=recovery)
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("updates", [
    {"cause": "future"}, {"cause": None}, {"recovery_action": "future"},
    {"workload_status": {"kind": "unknown"}},
    {"workload_status": {"kind": "unknown", "reason": "Unknown."}, "cause": "host-cancel"},
    {"cause": "controller-lost"}, {"cause": "startup-failure"},
    {"cause": "runtime-observation-lost"},
    {"cause": "runtime-observation-lost", "runtime_observation_status": {"kind": "lost"}},
    {"runtime_observation_status": {"kind": "maintained", "reason": "Unexpected."}},
    {"runtime_observation_status": {"kind": "future"}},
    {"workload_output_finalization_status": {"kind": "drained", "reason": "Unexpected."}},
    {"workload_output_finalization_status": {"kind": "failed"}},
    {"workload_output_finalization_status": {"kind": "failed", "reason": ""}},
    {"controller_finalization_status": {"kind": "active"}},
    {"controller_finalization_status": {"kind": "unknown"}},
    {"controller_finalization_status": {"kind": "completed", "reason": "bad\ntext"}},
    {"cleanup_status": {"kind": "succeeded", "message": "Unexpected."}},
    {"cleanup_status": {"kind": "failed"}, "recovery_action": "retry-cleanup"},
    {"cleanup_status": {"kind": "failed", "message": "Failure."}},
    {"recovery_action": "retry-cleanup"},
])
def test_C1_S007_result_consistency_rejection(updates):
    reject_event(terminated(**updates))


def test_C1_S007_every_nested_result_field_is_required_and_typed():
    for field in RESULT:
        value = terminated()
        del value["result"][field]
        reject_event(value)
        value = terminated()
        value["result"][field] = None
        reject_event(value)


@pytest.mark.parametrize("status,reason", [("drained", None), ("failed", "Drain timeout.")])
def test_C1_S007_output_finalization_events(status, reason):
    value = event("workload-outputs-finalized", status=status)
    if reason is not None:
        value["reason"] = reason
    assert decode_event(encode_event(value)) == value


@pytest.mark.parametrize("status,extra", [
    ("future", {}), (None, {}), ("failed", {}), ("failed", {"reason": None}),
    ("failed", {"reason": ""}), ("drained", {"reason": "Unexpected."}),
])
def test_C1_S007_invalid_output_finalization_events(status, extra):
    reject_event(event("workload-outputs-finalized", status=status, **extra))


@pytest.mark.parametrize("value", EVENTS + REQUESTS, ids=lambda v: v["type"])
def test_C1_S008_encoder_preserves_caller_data_and_emits_one_UTF8_line(value):
    original = deepcopy(value)
    encode = encode_request if value in REQUESTS else encode_event
    line = encode(value)
    assert value == original
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line.decode("utf-8")) == original
    assert line == encode(value)


@pytest.mark.parametrize("value", [None, [], True, 1, "ready", {}])
@pytest.mark.parametrize("encode", [encode_event, encode_request])
def test_C1_S008_encoder_rejects_non_message_values(encode, value):
    with pytest.raises(ReployCodecError):
        encode(value)


def test_C1_S009_codec_does_not_invent_lifecycle_order_or_authority():
    for kind in ["complete", "acknowledge-terminated", "terminate", "terminate"]:
        value = event(kind)
        assert decode_request(encode_request(value)) == value
    for value in [terminated(), event("ready"), event("ready")]:
        assert decode_event(encode_event(value)) == value
