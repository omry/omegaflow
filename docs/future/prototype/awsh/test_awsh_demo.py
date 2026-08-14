from __future__ import annotations

import io
import os
import select
import time

from awsh_demo import (
    POST_COMPLETION_DRAIN_SECONDS,
    POST_COMPLETION_QUIET_SECONDS,
    AwshSession,
    EventDecoder,
    EventLog,
    ProtocolError,
    _complete_path,
    _completion_drain_finished,
    _decode_completion,
    _relay_descriptors,
)


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


def test_relative_completion_uses_the_reported_shell_cwd(tmp_path) -> None:
    (tmp_path / "shell-file").write_text("", encoding="utf-8")
    (tmp_path / "shell-directory").mkdir()
    (tmp_path / "two words").write_text("", encoding="utf-8")

    assert _complete_path("shell-f", 0, str(tmp_path)) == "shell-file"
    assert _complete_path("shell-d", 0, str(tmp_path)) == "shell-directory/"
    assert _complete_path("two", 0, str(tmp_path)) == "'two words'"


def test_named_user_completion_preserves_the_home_prefix(
    tmp_path, monkeypatch
) -> None:
    named_home = tmp_path / "ubuntu"
    named_home.mkdir()
    (named_home / "two words").write_text("", encoding="utf-8")
    real_expanduser = os.path.expanduser

    def expanduser(path: str) -> str:
        if path == "~ubuntu":
            return str(named_home)
        if path.startswith("~ubuntu/"):
            return str(named_home) + path[len("~ubuntu") :]
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", expanduser)

    assert _complete_path("~ubuntu/two", 0, str(tmp_path)) == (
        "~ubuntu/'two words'"
    )


def test_completion_descends_into_a_quoted_directory(tmp_path) -> None:
    directory = tmp_path / "dir words"
    directory.mkdir()
    (directory / "child").write_text("", encoding="utf-8")

    assert _complete_path("dir", 0, str(tmp_path)) == "'dir words/'"
    assert _complete_path("'dir words/'chi", 0, str(tmp_path)) == (
        "'dir words/child'"
    )


def test_completion_treats_escaped_glob_characters_as_literals(tmp_path) -> None:
    (tmp_path / "a1").write_text("", encoding="utf-8")
    (tmp_path / "a?target").write_text("", encoding="utf-8")

    assert _complete_path(r"a\?", 0, str(tmp_path)) == "'a?target'"


def test_completion_decodes_unfinished_quotes(tmp_path) -> None:
    (tmp_path / "two words").write_text("", encoding="utf-8")

    assert _decode_completion("'two") == "two"
    assert _decode_completion('"two') == "two"
    assert _complete_path("'two", 0, str(tmp_path)) == "'two words'"
    assert _complete_path('"two', 0, str(tmp_path)) == "'two words'"


def test_unresolved_tilde_completion_remains_relative(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "~missing-target").write_text("", encoding="utf-8")
    (tmp_path / "~missing words").write_text("", encoding="utf-8")
    real_expanduser = os.path.expanduser

    def expanduser(path: str) -> str:
        if path.startswith("~missing"):
            return path
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", expanduser)

    assert _complete_path("~missing", 0, str(tmp_path)) == "'~missing words'"
    assert _complete_path("~missing", 1, str(tmp_path)) == "'~missing-target'"


def test_completed_operation_stops_monitoring_terminal_input() -> None:
    class Session:
        readable_fds = [10, 11]

    assert _relay_descriptors(Session(), 12, completed=False) == [10, 11, 12]
    assert _relay_descriptors(Session(), 12, completed=True) == [10, 11]


def test_completion_drain_has_quiet_and_fixed_deadlines() -> None:
    assert not _completion_drain_finished(True, None, 10.0, 9.99)
    assert _completion_drain_finished(True, None, 10.0, 10.0)
    assert _completion_drain_finished(
        True,
        5.0,
        5.0 + POST_COMPLETION_DRAIN_SECONDS,
        5.0 + POST_COMPLETION_QUIET_SECONDS + 0.001,
    )


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


def test_session_poll_detects_driver_exit_before_inherited_fds_close() -> None:
    session = AwshSession()
    events: list[dict[str, str]] = []
    terminal = bytearray()
    try:
        _collect_until(session, events, terminal, "ready")
        session.send(
            "execute",
            "demo-exit",
            "(trap '' HUP; sleep 0.5) & kill -KILL $$",
        )

        deadline = time.monotonic() + 1.0
        status = session.poll()
        while status is None and time.monotonic() < deadline:
            time.sleep(0.01)
            status = session.poll()

        assert status is not None
        assert status != 0
        assert session.result_open
    finally:
        session.close()


def test_session_executes_with_fixed_fd_source_collisions() -> None:
    layouts = (
        (range(3, 20), 21, 22),
        (range(6, 20), 4, 5),
    )
    for occupied, expected_request_write, expected_result_read in layouts:
        pid = os.fork()
        if pid == 0:
            try:
                os.closerange(3, 256)
                source = os.open(os.devnull, os.O_RDONLY)
                for descriptor in occupied:
                    if descriptor != source:
                        os.dup2(source, descriptor)
                if source not in occupied:
                    os.close(source)

                session = AwshSession()
                assert session.request_write == expected_request_write
                assert session.result_read == expected_result_read
                events: list[dict[str, str]] = []
                terminal = bytearray()
                _collect_until(session, events, terminal, "ready")
                session.send("shutdown")
                _collect_until(session, events, terminal, "closed")
                assert session.wait(1.0) == 0
                session.close()
            except BaseException:
                os._exit(1)
            os._exit(0)

        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0


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
