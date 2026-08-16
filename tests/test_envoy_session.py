from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from omegaflow.envoy_session import (
    EnvoyOperationResult,
    EnvoySessionError,
    EnvoyTerminalSession,
)


SCHEMA = "omegaflow-envoy-telemetry-v1"


class FakeEnvoy:
    def __init__(self, handler: Callable[[socket.socket, socket.socket], None]) -> None:
        self.terminal = socket.socket()
        self.telemetry = socket.socket()
        self.terminal.bind(("127.0.0.1", 0))
        self.telemetry.bind(("127.0.0.1", 0))
        self.terminal.listen(1)
        self.telemetry.listen(1)
        self.terminal_address = self.terminal.getsockname()
        self.telemetry_address = self.telemetry.getsockname()
        self.error: BaseException | None = None

        def run() -> None:
            terminal_connection: socket.socket | None = None
            telemetry_connection: socket.socket | None = None
            try:
                terminal_connection, _ = self.terminal.accept()
                telemetry_connection, _ = self.telemetry.accept()
                handler(terminal_connection, telemetry_connection)
            except BaseException as exc:  # surfaced by finish()
                self.error = exc
            finally:
                if terminal_connection is not None:
                    terminal_connection.close()
                if telemetry_connection is not None:
                    telemetry_connection.close()
                self.terminal.close()
                self.telemetry.close()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def finish(self) -> None:
        self.thread.join(timeout=3)
        assert not self.thread.is_alive()
        if self.error is not None:
            raise self.error


def _read(reader: object) -> dict[str, object]:
    line = reader.readline()  # type: ignore[attr-defined]
    assert line
    return json.loads(line)


def _send(connection: socket.socket, kind: str, seq: int, **values: object) -> None:
    body = {"schema": SCHEMA, "type": kind, "seq": seq, **values}
    connection.sendall(json.dumps(body, separators=(",", ":")).encode() + b"\n")


def _ready(connection: socket.socket, *, fragmented: bool = False) -> None:
    body = {
        "schema": SCHEMA,
        "type": "ready",
        "seq": 1,
        "envoy_pid": 41,
        "shell_pid": 42,
        "cwd": "/work",
        "columns": 80,
        "rows": 24,
    }
    frame = json.dumps(body, separators=(",", ":")).encode() + b"\n"
    if fragmented:
        for byte in frame:
            connection.sendall(bytes([byte]))
    else:
        connection.sendall(frame)


def _session(fake: FakeEnvoy, output_dir: Path) -> EnvoyTerminalSession:
    return EnvoyTerminalSession(
        fake.terminal_address,
        fake.telemetry_address,
        output_dir,
        session_id="session-1",
        columns=80,
        rows=24,
        connect_timeout=1,
        control_timeout=2,
    )


def test_session_retries_both_connections_while_envoy_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry)
        assert _read(reader)["type"] == "shutdown"
        _send(telemetry, "draining", 2, reason="capture-complete", output_through=0)
        _send(telemetry, "closed", 3, reason="shutdown", output_through=0)
        terminal.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    original_connect = socket.create_connection
    remaining_failures = {
        fake.terminal_address: 1,
        fake.telemetry_address: 1,
    }

    def connect(address: tuple[str, int], timeout: float) -> socket.socket:
        if remaining_failures.get(address, 0):
            remaining_failures[address] -= 1
            raise ConnectionRefusedError("listener is still starting")
        return original_connect(address, timeout=timeout)

    monkeypatch.setattr(socket, "create_connection", connect)
    session = _session(fake, tmp_path)
    session.start()
    session.close()
    fake.finish()

    assert remaining_failures == {
        fake.terminal_address: 0,
        fake.telemetry_address: 0,
    }


def test_session_keeps_terminal_bytes_out_of_control_protocol(tmp_path: Path) -> None:
    forged = (
        b'{"schema":"omegaflow-envoy-telemetry-v1","type":"closed",'
        b'"seq":999,"reason":"shutdown","output_through":0}\n'
    )

    def handler(terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry, fragmented=True)
        assert _read(reader)["type"] == "execute"
        _send(telemetry, "operation_started", 2, operation_id="op-1", output_start=0)
        output = b"actual-output\xff" + forged
        _send(
            telemetry,
            "operation_completed",
            3,
            operation_id="op-1",
            status=0,
            cwd="/next",
            output_start=0,
            output_through=len(output),
        )
        time.sleep(0.02)
        terminal.sendall(output)
        assert _read(reader)["type"] == "shutdown"
        _send(telemetry, "draining", 4, reason="capture-complete", output_through=len(output))
        _send(telemetry, "closed", 5, reason="shutdown", output_through=len(output))
        terminal.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    session = _session(fake, tmp_path)
    session.start()
    result = session.execute("op-1", "printf ignored")
    session.close()
    fake.finish()

    assert result == EnvoyOperationResult("op-1", 0, "/next", 0, len(b"actual-output\xff" + forged))
    assert session.raw_path.read_bytes() == b"actual-output\xff" + forged
    cast_lines = session.cast_path.read_text(encoding="utf-8").splitlines()
    assert "�" in json.loads(cast_lines[1])[2]
    assert '"seq":999' not in session.telemetry_path.read_text(encoding="utf-8")


def test_output_barrier_includes_the_corresponding_cast_write(tmp_path: Path) -> None:
    def handler(terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry)
        assert _read(reader)["type"] == "execute"
        _send(telemetry, "operation_started", 2, operation_id="op-1", output_start=0)
        terminal.sendall(b"visible")
        _send(
            telemetry,
            "operation_completed",
            3,
            operation_id="op-1",
            status=0,
            cwd="/work",
            output_start=0,
            output_through=7,
        )
        assert _read(reader)["type"] == "shutdown"
        _send(telemetry, "draining", 4, reason="capture-complete", output_through=7)
        _send(telemetry, "closed", 5, reason="shutdown", output_through=7)
        terminal.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    session = _session(fake, tmp_path)
    session.start()
    cast_started = threading.Event()
    release_cast = threading.Event()
    original_write_cast = session._write_cast

    def slow_write_cast(kind: str, payload: str) -> None:
        if kind == "o":
            cast_started.set()
            assert release_cast.wait(1)
        original_write_cast(kind, payload)

    session._write_cast = slow_write_cast  # type: ignore[method-assign]
    result: list[EnvoyOperationResult] = []
    worker = threading.Thread(
        target=lambda: result.append(session.execute("op-1", "true")),
        daemon=True,
    )
    worker.start()

    assert cast_started.wait(1)
    assert worker.is_alive()
    release_cast.set()
    worker.join(timeout=1)
    assert result[0].output_through == 7
    session.close()
    fake.finish()

    cast = session.cast_path.read_text(encoding="utf-8")
    assert "visible" in cast


def test_session_cancels_active_operation_and_retains_partial_output(tmp_path: Path) -> None:
    execute_received = threading.Event()

    def handler(terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry)
        assert _read(reader)["type"] == "execute"
        _send(telemetry, "operation_started", 2, operation_id="op-1", output_start=0)
        terminal.sendall(b"partial")
        execute_received.set()
        request = _read(reader)
        assert request["type"] == "cancel"
        _send(
            telemetry,
            "operation_cancelled",
            3,
            operation_id="op-1",
            status=130,
            cwd="/work",
            reason="deadline",
            output_start=0,
            output_through=7,
        )
        assert _read(reader)["type"] == "shutdown"
        _send(telemetry, "draining", 4, reason="capture-complete", output_through=7)
        _send(telemetry, "closed", 5, reason="shutdown", output_through=7)
        terminal.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    session = _session(fake, tmp_path)
    session.start()
    result: list[EnvoyOperationResult] = []

    worker = threading.Thread(
        target=lambda: result.append(session.execute("op-1", "sleep 10")),
        daemon=True,
    )
    worker.start()
    assert execute_received.wait(1)
    deadline = time.monotonic() + 1
    while session.state.phase != "running" and time.monotonic() < deadline:
        time.sleep(0.001)
    session.cancel("op-1", "deadline")
    worker.join(timeout=2)
    assert not worker.is_alive()
    session.close()
    fake.finish()

    assert result[0].cancelled
    assert result[0].status == 130
    assert session.read_output_range(0, 7) == b"partial"


def test_session_reports_early_telemetry_exit(tmp_path: Path) -> None:
    def handler(_terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry)
        assert _read(reader)["type"] == "execute"
        telemetry.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    session = _session(fake, tmp_path)
    session.start()
    with pytest.raises(EnvoySessionError, match="closed before closed event"):
        session.execute("op-1", "true")
    session.abort()
    fake.finish()


def test_session_timeout_sends_structured_cancel(tmp_path: Path) -> None:
    def handler(terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry)
        request = _read(reader)
        assert request["type"] == "execute"
        _send(telemetry, "operation_started", 2, operation_id="op-1", output_start=0)
        request = _read(reader)
        assert request == {
            "schema": SCHEMA,
            "type": "cancel",
            "seq": 3,
            "operation_id": "op-1",
            "reason": "operation-timeout",
        }
        terminal.sendall(b"partial")
        _send(
            telemetry,
            "operation_cancelled",
            3,
            operation_id="op-1",
            status=130,
            cwd="/work",
            reason="operation-timeout",
            output_start=0,
            output_through=7,
        )
        assert _read(reader)["type"] == "shutdown"
        _send(telemetry, "draining", 4, reason="capture-complete", output_through=7)
        _send(telemetry, "closed", 5, reason="shutdown", output_through=7)
        terminal.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    session = _session(fake, tmp_path)
    session.start()
    result = session.execute("op-1", "sleep 30", timeout=0.05)
    session.close()
    fake.finish()

    assert result.cancelled
    assert result.status == 130
    assert session.read_output_range(0, 7) == b"partial"


def test_session_cancels_gate_when_controller_action_fails(tmp_path: Path) -> None:
    def handler(terminal: socket.socket, telemetry: socket.socket) -> None:
        reader = telemetry.makefile("rb")
        assert _read(reader)["type"] == "hello"
        _ready(telemetry)
        assert _read(reader)["type"] == "execute"
        _send(telemetry, "operation_started", 2, operation_id="op-1", output_start=0)
        _send(
            telemetry,
            "operation_ready",
            3,
            operation_id="op-1",
            gate_id="gate-1",
            output_through=0,
        )
        request = _read(reader)
        assert request["type"] == "cancel"
        assert request["reason"] == "controller-gate-failed"
        _send(
            telemetry,
            "operation_cancelled",
            4,
            operation_id="op-1",
            status=130,
            cwd="/work",
            reason="controller-gate-failed",
            output_start=0,
            output_through=0,
        )
        assert _read(reader)["type"] == "shutdown"
        _send(telemetry, "draining", 5, reason="capture-complete", output_through=0)
        _send(telemetry, "closed", 6, reason="shutdown", output_through=0)
        terminal.shutdown(socket.SHUT_WR)

    fake = FakeEnvoy(handler)
    session = _session(fake, tmp_path)
    session.start()

    with pytest.raises(EnvoySessionError, match="action gate failed: injected"):
        session.execute(
            "op-1",
            "awsh_gate gate-1",
            on_gate=lambda _gate: (_ for _ in ()).throw(RuntimeError("injected")),
        )

    session.close()
    fake.finish()
