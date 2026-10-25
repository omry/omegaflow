"""C3.1 wire admission against B1's frozen public bytes and adversarial values."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from omegaflow.envoy_codecs import (
    EnvoyCodecError, EventFrames, MAX_FRAME_BYTES, MAX_INTEGER, MAX_SOURCE_BYTES,
    SCHEMA, decode, encode,
)


CORPUS = Path(__file__).parent / "fixtures/envoy-protocol-v1"
GOLDEN = [(line, controller) for file, controller in [("controller.jsonl", True), ("envoy.jsonl", False)]
          for line in (CORPUS / file).read_bytes().splitlines(keepends=True)]
EVENTS = [line for line, controller in GOLDEN if not controller]
INVALID = (CORPUS / "public-invalid.jsonl").read_bytes().splitlines(keepends=True)
READY = json.loads(EVENTS[0])
EXECUTE = next(json.loads(line) for line, direction in GOLDEN
               if direction and json.loads(line)["type"] == "execute")


@pytest.mark.parametrize("line,controller", GOLDEN)
def test_frozen_public_roundtrip_and_direction(line, controller):
    value = decode(line, controller=controller)
    assert encode(value, controller=controller) == line
    with pytest.raises(EnvoyCodecError):
        decode(line, controller=not controller)
    # Caller insertion order cannot alter top-level or nested golden field order.
    reordered = dict(reversed(list(value.items())))
    for key in ("inspections", "inspection_results"):
        if key in reordered:
            reordered[key] = [dict(reversed(list(item.items()))) for item in reordered[key]]
    assert encode(reordered, controller=controller) == line


@pytest.mark.parametrize("line", INVALID)
@pytest.mark.parametrize("controller", [False, True])
def test_frozen_invalid_public_frames(line, controller):
    with pytest.raises(EnvoyCodecError):
        decode(line, controller=controller)


@pytest.mark.parametrize("change", [
    lambda line: line[:-1],
    lambda line: line[:-1] + b"\r\n",
    lambda line: line + line,
    lambda line: line.replace(b"100", b"\x00", 1),
    lambda line: line.replace(b"100", b"\xff", 1),
    lambda line: line.replace(b'"seq":1', b'"seq":1,"seq":1'),
    lambda line: line.replace(b'"seq":1', b'"seq":NaN'),
    lambda line: line.replace(b'"seq":1', b'"seq":Infinity'),
    lambda line: line.replace(b'"seq":1', b'"seq":1e999'),
    lambda line: line.replace(b'"seq":1', b'"seq":1.0'),
    lambda line: line.replace(b'"seq":1', b'"seq":true'),
    lambda line: line.replace(b'"cwd":"/work"', b'"cwd":"\\ud800"'),
    lambda line: line.replace(b'"cwd":"/work"', b'"cwd":"\\udc00"'),
    lambda line: line.replace(b'"cwd":"/work"', b'"cwd":"/\\u0000"'),
    lambda line: b"[]\n",
    lambda line: b"null\n",
    lambda line: b"{}\n",
    lambda line: b"\xef\xbb\xbf" + line,
])
def test_json_frame_rejections(change):
    with pytest.raises(EnvoyCodecError):
        decode(change(EVENTS[0]))


@pytest.mark.parametrize("line,controller", GOLDEN)
def test_each_required_field_is_required_and_typed(line, controller):
    original = decode(line, controller=controller)
    for key in original:
        missing = deepcopy(original)
        del missing[key]
        # Optional fields may legitimately be absent; null never substitutes.
        optional = ((key == "status" and original["type"] == "operation_cancelled")
                    or (key == "operation_id" and original["type"] == "diagnostic")
                    or key == "shell_ended")
        if not optional:
            with pytest.raises(EnvoyCodecError):
                encode(missing, controller=controller)
        for wrong in [None, {}, True, 1.0]:
            if key == "shell_ended" and wrong is True:
                continue
            value = deepcopy(original)
            value[key] = wrong
            with pytest.raises(EnvoyCodecError):
                encode(value, controller=controller)
    unknown = original | {"unexpected": 0}
    with pytest.raises(EnvoyCodecError):
        encode(unknown, controller=controller)


@pytest.mark.parametrize("field,valid,invalid", [
    ("seq", MAX_INTEGER, [0, -1, MAX_INTEGER + 1]),
    ("envoy_pid", (1 << 31) - 1, [0, -1, 1 << 31]),
    ("shell_pid", (1 << 31) - 1, [0, -1, 1 << 31]),
    ("columns", 1000, [0, -1, 1001]),
    ("rows", 1000, [0, -1, 1001]),
    ("output_through", 4096, [-1, 4097, MAX_INTEGER + 1]),
    ("elapsed_us", 0, [-1, 1, MAX_INTEGER + 1]),
    ("cwd", "/" + "é" * 2047 + "x", ["relative", "", "/" + "é" * 2048, "/\0"]),
])
def test_ready_scalar_boundaries(field, valid, invalid):
    assert decode(encode(READY | {field: valid}, controller=False))[field] == valid
    for item in invalid:
        with pytest.raises(EnvoyCodecError):
            encode(READY | {field: item}, controller=False)


@pytest.mark.parametrize("source", ["", "a" * (MAX_SOURCE_BYTES + 1), "é" * (MAX_SOURCE_BYTES // 2 + 1),
                                    "\0", "#__OMEGAFLOW_AWSH_private", "\ud800"])
def test_source_wire_bounds_and_namespace(source):
    with pytest.raises(EnvoyCodecError):
        encode(EXECUTE | {"source": source})


def test_source_size_and_enclosing_frame_are_distinct_bounds():
    value = EXECUTE | {"source": "é" * (MAX_SOURCE_BYTES // 2)}
    assert decode(encode(value), controller=True) == value
    with pytest.raises(EnvoyCodecError, match="frame exceeds"):
        encode(EXECUTE | {"source": "\t" * MAX_SOURCE_BYTES})
    line = (CORPUS / "public-maximum.jsonl").read_bytes()
    assert len(line) == MAX_FRAME_BYTES
    value = decode(line, controller=True)
    assert encode(value) == line
    with pytest.raises(EnvoyCodecError):
        decode(b" " + line, controller=True)


@pytest.mark.parametrize("kind,changes", [
    ("operation_completed", {"output_start": 2, "output_through": 1}),
    ("operation_completed", {"shell_ended": False}),
    ("operation_cancelled", {"output_through": 1}),
    ("operation_cancelled", {"status": None}),
    ("operation_failed", {"code": "future-unknown"}),
    ("operation_failed", {"code": "cancel-timeout"}),
    ("operation_failed", {"shell_ended": True}),
    ("operation_failed", {"output_through": 1}),
])
def test_event_specific_contracts(kind, changes):
    original = next(json.loads(line) for line in EVENTS if json.loads(line)["type"] == kind)
    with pytest.raises(EnvoyCodecError):
        encode(original | changes, controller=False)


def test_forward_diagnostic_code_and_optional_presence():
    value = {"schema": SCHEMA, "type": "diagnostic", "seq": MAX_INTEGER,
             "severity": "warning", "code": "future-bounded-code", "message": "é" * 2048,
             "operation_id": "A" + "_" * 63}
    assert decode(encode(value, controller=False)) == value
    for field, wrong in [("operation_id", "a" * 65), ("operation_id", "é"),
                         ("code", "Bad"), ("message", "é" * 2049)]:
        with pytest.raises(EnvoyCodecError):
            encode(value | {field: wrong}, controller=False)


def test_nested_inspections_and_duplicate_fields():
    value = EXECUTE | {"observation": "exclusive", "inspections": [
        {"inspection_id": "exists", "kind": "file_exists", "path": "$HOME/file"}]}
    line = encode(value)
    duplicate = line.replace(b'"kind":"file_exists"', b'"kind":"file_exists","kind":"file_exists"')
    with pytest.raises(EnvoyCodecError):
        decode(duplicate, controller=True)
    for bad in [None, {}, [None], value["inspections"] * 2,
                [value["inspections"][0] | {"sha256": "a" * 64}],
                [value["inspections"][0] | {"path": "x" * 4097}]]:
        with pytest.raises(EnvoyCodecError):
            encode(value | {"inspections": bad})
    assert decode(line, controller=True) == value


@pytest.mark.parametrize("path", ["relative", "//work", "/work/..", "/work/./file", "/work//file", "/work/"])
def test_result_paths_are_absolute_and_clean(path):
    value = next(json.loads(line) for line in EVENTS
                 if json.loads(line).get("inspection_results"))
    value["inspection_results"][0]["resolved_path"] = path
    with pytest.raises(EnvoyCodecError):
        encode(value, controller=False)


@pytest.mark.parametrize("line", EVENTS)
def test_every_fragmentation_boundary(line):
    for split in range(len(line) + 1):
        reader = EventFrames()
        rows = reader.feed(line[:split]) + reader.feed(line[split:])
        assert rows == [(line, json.loads(line))]
        reader.eof()


def test_coalesced_frames_sequence_and_one_byte_fragments():
    values = [READY | {"seq": i} for i in range(1, 4)]
    lines = [encode(x, controller=False) for x in values]
    stream = EventFrames()
    assert stream.feed(b"".join(lines)) == list(zip(lines, values))
    byte_stream = EventFrames()
    observed = []
    for byte in b"".join(lines):
        observed += byte_stream.feed(bytes([byte]))
    assert observed == list(zip(lines, values))
    byte_stream.eof()


@pytest.mark.parametrize("sequences", [[0], [2], [1, 1], [1, 3], [1, 2, 1]])
def test_sequence_errors_poison_stream(sequences):
    stream = EventFrames()
    with pytest.raises(EnvoyCodecError):
        for seq in sequences:
            line = json.dumps(READY | {"seq": seq}).encode() + b"\n"
            stream.feed(line)
    with pytest.raises(EnvoyCodecError):
        stream.feed(EVENTS[0])


def test_unterminated_limit_and_eof():
    reader = EventFrames()
    reader.feed(b"{" * (MAX_FRAME_BYTES - 1))
    with pytest.raises(EnvoyCodecError):
        reader.feed(b" ")
    reader = EventFrames()
    reader.feed(EVENTS[0][:-1])
    with pytest.raises(EnvoyCodecError):
        reader.eof()
    reader = EventFrames()
    with pytest.raises(EnvoyCodecError):
        reader.feed(b"x" * (MAX_FRAME_BYTES + 1))


def test_unicode_pair_and_literal_line_separators():
    value = READY | {"cwd": "/\U0001f680\u2028\u2029"}
    line = encode(value, controller=False)
    assert decode(line) == value
    escaped = line.replace("🚀".encode(), b"\\ud83d\\ude80")
    assert decode(escaped) == value
