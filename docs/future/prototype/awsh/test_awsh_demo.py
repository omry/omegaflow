from __future__ import annotations

import io
import select
import time

from awsh_demo import AwshSession, EventDecoder, EventLog, ProtocolError


def test_event_decoder_accepts_fragmented_frames() -> None:
    decoder = EventDecoder()

    assert decoder.feed(b"awsh-v1\0rea") == []
    assert decoder.feed(b"dy\0" b"123\0/tmp\0") == [
        {"type": "ready", "pid": "123", "cwd": "/tmp"}
    ]
    assert decoder.feed(b"awsh-v1\0closed\0shutdown\0/tmp\0") == [
        {"type": "closed", "reason": "shutdown", "cwd": "/tmp"}
    ]
    decoder.finish()


def test_event_decoder_rejects_unknown_result_kind() -> None:
    decoder = EventDecoder()

    try:
        decoder.feed(b"awsh-v1\0surprise\0")
    except ProtocolError as exc:
        assert "unexpected result kind" in str(exc)
    else:
        raise AssertionError("unknown result kind was accepted")


def test_event_decoder_rejects_eof_before_closed_event() -> None:
    decoder = EventDecoder()
    decoder.feed(b"awsh-v1\0ready\0" b"123\0/tmp\0")

    try:
        decoder.finish()
    except ProtocolError as exc:
        assert "before a closed event" in str(exc)
    else:
        raise AssertionError("result EOF without a closed event was accepted")


def test_event_decoder_rejects_invalid_utf8() -> None:
    decoder = EventDecoder()

    try:
        decoder.feed(b"awsh-v1\0ready\0\xff\0/tmp\0")
    except ProtocolError as exc:
        assert "not valid UTF-8" in str(exc)
    else:
        raise AssertionError("invalid UTF-8 was accepted")


def test_event_log_labels_outbound_request_with_source() -> None:
    stream = io.BytesIO()

    EventLog(stream).request("demo-1", "printf 'hello world\\n'")

    line = stream.getvalue().decode("utf-8")
    assert "request" in line
    assert "operation_id=demo-1" in line
    assert "source=" in line
    assert "printf" in line


def test_session_executes_and_shuts_down() -> None:
    session = AwshSession()
    events: list[dict[str, str]] = []
    terminal = bytearray()
    try:
        _collect_until(session, events, terminal, "ready")
        session.send("execute", "demo-1", "printf 'wrapper-smoke\\n'")
        _collect_until(session, events, terminal, "completed")
        session.send("shutdown")
        _collect_until(session, events, terminal, "closed")

        assert b"wrapper-smoke" in terminal
        assert [event["type"] for event in events] == [
            "ready",
            "started",
            "completed",
            "closed",
        ]
        assert events[2]["operation_id"] == "demo-1"
        assert events[2]["status"] == "0"
        assert events[3]["reason"] == "shutdown"
        assert session.wait(1.0) == 0
    finally:
        session.close()


def _collect_until(
    session: AwshSession,
    events: list[dict[str, str]],
    terminal: bytearray,
    kind: str,
) -> None:
    deadline = time.monotonic() + 2.0
    while not any(event["type"] == kind for event in events):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"did not receive {kind}")
        readable, _, _ = select.select(session.readable_fds, [], [], remaining)
        for descriptor in readable:
            if descriptor == session.terminal_master:
                terminal.extend(session.read_terminal())
            elif descriptor == session.result_read:
                events.extend(session.read_events())
