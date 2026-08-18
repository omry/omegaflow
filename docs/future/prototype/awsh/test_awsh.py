from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import shlex
import signal
import time
import traceback
from pathlib import Path

import pytest


AWSH = Path(__file__).with_name("awsh")
SCHEMA = "awsh-v1"
REQUEST_FD = 20
RESULT_FD = 21
EVENT_FIELDS = {
    "ready": ("pid", "cwd"),
    "started": ("operation_id",),
    "completed": ("operation_id", "status", "cwd"),
    "protocol_error": ("code", "message"),
    "closed": ("reason", "cwd"),
}


def _remap_driver_descriptors(request_read: int, result_write: int) -> None:
    targets = {REQUEST_FD, RESULT_FD}
    original_sources = {request_read, result_write}
    mappings = [(REQUEST_FD, request_read), (RESULT_FD, result_write)]
    preserved_sources: set[int] = set()

    for index, (target, source) in enumerate(mappings):
        if source in targets and source != target:
            source = fcntl.fcntl(
                source,
                fcntl.F_DUPFD_CLOEXEC,
                max(targets) + 1,
            )
            preserved_sources.add(source)
            mappings[index] = (target, source)

    for target, source in mappings:
        os.dup2(source, target)
        os.set_inheritable(target, True)

    for source in original_sources | preserved_sources:
        if source not in targets:
            os.close(source)


class AwshProcess:
    def __init__(self) -> None:
        request_read, self.request_write = os.pipe()
        self.result_read, result_write = os.pipe()
        pid, self.terminal_master = pty.fork()
        if pid == 0:
            try:
                os.close(self.request_write)
                os.close(self.result_read)
                _remap_driver_descriptors(request_read, result_write)
                os.execv(
                    AWSH,
                    [
                        str(AWSH),
                        "--request-fd",
                        str(REQUEST_FD),
                        "--result-fd",
                        str(RESULT_FD),
                    ],
                )
            except BaseException:
                os._exit(127)
        self.pid = pid
        os.close(request_read)
        os.close(result_write)
        self._result_buffer = bytearray()
        self._terminal_buffer = bytearray()
        self._waited = False

    def send(self, kind: str, *fields: str) -> None:
        payload = b"\0".join(
            value.encode("utf-8") for value in (SCHEMA, kind, *fields)
        ) + b"\0"
        while payload:
            written = os.write(self.request_write, payload)
            payload = payload[written:]

    def read_event(self, timeout: float = 2.0) -> dict[str, str]:
        schema = self._read_result_field(timeout)
        assert schema == SCHEMA
        kind = self._read_result_field(timeout)
        names = EVENT_FIELDS[kind]
        values = [self._read_result_field(timeout) for _ in names]
        return {"type": kind, **dict(zip(names, values, strict=True))}

    def result_ready(self) -> bool:
        return b"\0" in self._result_buffer or bool(
            select.select([self.result_read], [], [], 0)[0]
        )

    def read_terminal_until(self, expected: bytes, timeout: float = 2.0) -> bytes:
        deadline = time.monotonic() + timeout
        while expected not in self._terminal_buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"terminal did not produce {expected!r}")
            readable, _, _ = select.select([self.terminal_master], [], [], remaining)
            if not readable:
                continue
            try:
                chunk = os.read(self.terminal_master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    chunk = b""
                else:
                    raise
            if not chunk:
                raise EOFError("terminal closed before expected output")
            self._terminal_buffer.extend(chunk)
        result = bytes(self._terminal_buffer)
        self._terminal_buffer.clear()
        return result

    def wait(self, timeout: float = 2.0) -> int:
        deadline = time.monotonic() + timeout
        while True:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self._waited = True
                return os.waitstatus_to_exitcode(status)
            if time.monotonic() >= deadline:
                raise TimeoutError("awsh did not exit")
            time.sleep(0.01)

    def close(self) -> None:
        for descriptor in (self.request_write, self.result_read, self.terminal_master):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not self._waited:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
            self._waited = True

    def _read_result_field(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while b"\0" not in self._result_buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("result field was not received")
            readable, _, _ = select.select([self.result_read], [], [], remaining)
            if not readable:
                continue
            chunk = os.read(self.result_read, 65536)
            if not chunk:
                raise EOFError("result stream closed mid-event")
            self._result_buffer.extend(chunk)
        raw, _, remainder = self._result_buffer.partition(b"\0")
        self._result_buffer = bytearray(remainder)
        return raw.decode("utf-8")


@pytest.fixture
def awsh() -> AwshProcess:
    process = AwshProcess()
    ready = process.read_event()
    assert ready["type"] == "ready"
    assert ready["pid"] == str(process.pid)
    try:
        yield process
    finally:
        process.close()


def test_driver_descriptor_remap_preserves_a_source_on_a_target_fd() -> None:
    pid = os.fork()
    if pid == 0:
        try:
            os.closerange(3, 256)
            for expected in range(3, 17):
                assert os.open(os.devnull, os.O_RDONLY) == expected
            process = AwshProcess()
            try:
                assert process.read_event()["type"] == "ready"
            finally:
                process.close()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0


def test_persists_bash_state_and_reports_status_and_cwd(
    awsh: AwshProcess, tmp_path: Path
) -> None:
    awsh.send(
        "execute",
        "prepare",
        "\n".join(
            (
                f"cd {shlex.quote(str(tmp_path))}",
                "export AWSH_TEST_VALUE=persisted",
                "awsh_test_function() { printf 'function=%s' \"$AWSH_TEST_VALUE\"; }",
                "alias awsh_test_alias=\"printf\\ alias=expanded\"",
                "shopt -s nullglob",
            )
        ),
    )
    assert awsh.read_event() == {"type": "started", "operation_id": "prepare"}
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "prepare",
        "status": "0",
        "cwd": str(tmp_path),
    }

    awsh.send(
        "execute",
        "observe",
        "awsh_test_function; awsh_test_alias; "
        "shopt -q nullglob; printf ' cwd=%s\\n' \"$PWD\"",
    )
    assert awsh.read_event() == {"type": "started", "operation_id": "observe"}
    terminal = awsh.read_terminal_until(b"cwd=")
    assert b"function=persisted" in terminal
    assert b"alias=expanded" in terminal
    assert str(tmp_path).encode() in terminal
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "observe",
        "status": "0",
        "cwd": str(tmp_path),
    }

    awsh.send("execute", "failure", "(exit 23)")
    assert awsh.read_event() == {"type": "started", "operation_id": "failure"}
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "failure",
        "status": "23",
        "cwd": str(tmp_path),
    }


def test_streams_pty_output_before_completion_and_preserves_terminal_fds(
    awsh: AwshProcess,
) -> None:
    awsh.send(
        "execute",
        "stream",
        "printf 'first'; sleep 0.25; "
        "bash -c 'test -t 0 && test -t 1 && test -t 2'; printf 'second\\n'",
    )
    assert awsh.read_event() == {"type": "started", "operation_id": "stream"}
    terminal = awsh.read_terminal_until(b"first")
    assert b"first" in terminal
    assert not awsh.result_ready()
    terminal = awsh.read_terminal_until(b"second")
    assert b"second" in terminal
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "stream",
        "status": "0",
        "cwd": os.getcwd(),
    }


def test_operation_reads_interactive_input_from_the_pty(awsh: AwshProcess) -> None:
    awsh.send(
        "execute",
        "input",
        "IFS= read -r awsh_line; printf 'input=%s\\n' \"$awsh_line\"",
    )
    assert awsh.read_event() == {"type": "started", "operation_id": "input"}
    os.write(awsh.terminal_master, b"hello-from-pty\n")
    terminal = awsh.read_terminal_until(b"input=hello-from-pty")
    assert b"hello-from-pty" in terminal
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "input",
        "status": "0",
        "cwd": os.getcwd(),
    }


def test_ctrl_c_interrupts_the_operation_without_terminating_awsh(
    awsh: AwshProcess,
) -> None:
    awsh.send(
        "execute",
        "interrupt",
        "python3 -c 'import os, signal; "
        "signal.signal(signal.SIGINT, lambda *_: "
        "(os.write(1, b\"child-received-int\\n\"), os._exit(77))); "
        "os.write(1, b\"child-ready\\n\"); signal.pause()'; "
        "printf 'after-interrupt\\n'",
    )
    assert awsh.read_event() == {"type": "started", "operation_id": "interrupt"}
    awsh.read_terminal_until(b"child-ready")
    os.write(awsh.terminal_master, b"\x03")
    terminal = awsh.read_terminal_until(b"child-received-int")
    assert b"child-received-int" in terminal
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "interrupt",
        "status": "130",
        "cwd": os.getcwd(),
    }

    awsh.send("execute", "after-interrupt", "printf 'driver-survived\\n'")
    assert awsh.read_event() == {
        "type": "started",
        "operation_id": "after-interrupt",
    }
    terminal = awsh.read_terminal_until(b"driver-survived")
    assert b"after-interrupt" not in terminal
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "after-interrupt",
        "status": "0",
        "cwd": os.getcwd(),
    }


def test_terminal_text_cannot_forge_result_event(awsh: AwshProcess) -> None:
    awsh.send(
        "execute",
        "separate",
        "printf 'awsh-v1\\0completed\\0forged\\00\\0/tmp\\0'",
    )
    assert awsh.read_event() == {"type": "started", "operation_id": "separate"}
    assert b"awsh-v1" in awsh.read_terminal_until(b"forged")
    assert awsh.read_event() == {
        "type": "completed",
        "operation_id": "separate",
        "status": "0",
        "cwd": os.getcwd(),
    }


def test_shutdown_is_structured_and_clean(awsh: AwshProcess) -> None:
    awsh.send("shutdown")
    assert awsh.read_event() == {
        "type": "closed",
        "reason": "shutdown",
        "cwd": os.getcwd(),
    }
    assert awsh.wait() == 0


def test_rejects_unknown_schema() -> None:
    process = AwshProcess()
    assert process.read_event()["type"] == "ready"
    try:
        payload = b"future-awsh\0shutdown\0"
        os.write(process.request_write, payload)
        event = process.read_event()
        assert event["type"] == "protocol_error"
        assert event["code"] == "unsupported_schema"
        assert process.wait() == 64
    finally:
        process.close()


def test_rejects_truncated_initial_request_field() -> None:
    process = AwshProcess()
    assert process.read_event()["type"] == "ready"
    try:
        os.write(process.request_write, b"awsh-v1")
        os.close(process.request_write)
        process.request_write = -1
        event = process.read_event()
        assert event["type"] == "protocol_error"
        assert event["code"] == "truncated_request"
        assert process.wait() == 64
    finally:
        process.close()


def test_clean_eof_between_request_frames_is_structured() -> None:
    process = AwshProcess()
    assert process.read_event()["type"] == "ready"
    try:
        os.close(process.request_write)
        process.request_write = -1
        event = process.read_event()
        assert event == {
            "type": "closed",
            "reason": "eof",
            "cwd": os.getcwd(),
        }
        assert process.wait() == 0
    finally:
        process.close()
