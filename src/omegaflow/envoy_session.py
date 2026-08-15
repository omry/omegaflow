"""Controller-side terminal and telemetry client for an OmegaFlow Envoy."""

from __future__ import annotations

import codecs
import json
import os
import queue
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .envoy_protocol import (
    Closed,
    Draining,
    EnvoyClientState,
    EnvoyDiagnostic,
    EnvoyEvent,
    EnvoyProtocolError,
    EnvoyReady,
    EnvoyStreamDecoder,
    OperationCancelled,
    OperationCompleted,
    OperationContinued,
    OperationFailed,
    OperationReady,
    OperationStarted,
    ResizeApplied,
)


class EnvoySessionError(RuntimeError):
    """The controller could not complete a trusted Envoy session."""


@dataclass(frozen=True)
class EnvoyOperationResult:
    operation_id: str
    status: int | None
    cwd: str
    output_start: int
    output_through: int
    cancelled: bool = False
    failure_code: str | None = None
    failure_message: str | None = None
    suspended: bool = False


class EnvoyTerminalSession:
    """Drive one Envoy while retaining exact terminal bytes and typed telemetry."""

    def __init__(
        self,
        terminal_address: tuple[str, int],
        telemetry_address: tuple[str, int],
        output_dir: Path,
        *,
        session_id: str,
        columns: int,
        rows: int,
        title: str = "OmegaFlow recording",
        connect_timeout: float = 10.0,
        control_timeout: float = 30.0,
        record_cast: bool = True,
    ) -> None:
        self.terminal_address = terminal_address
        self.telemetry_address = telemetry_address
        self.output_dir = output_dir
        self.session_id = session_id
        self.columns = columns
        self.rows = rows
        self.title = title
        self.connect_timeout = connect_timeout
        self.control_timeout = control_timeout
        self.record_cast = record_cast
        self.raw_path = output_dir / "terminal.output.log"
        self.cast_path = output_dir / "terminal.cast"
        self.timeline_path = output_dir / "terminal.timeline.jsonl"
        self.telemetry_path = output_dir / "envoy.telemetry.jsonl"
        self.diagnostics_path = output_dir / "envoy.diagnostics.jsonl"
        self.state = EnvoyClientState()
        self.ready: EnvoyReady | None = None
        self.diagnostics: list[EnvoyDiagnostic] = []
        self._terminal: socket.socket | None = None
        self._telemetry: socket.socket | None = None
        self._events: queue.Queue[EnvoyEvent | BaseException] = queue.Queue()
        self._output_condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._artifact_lock = threading.Lock()
        self._raw_offset = 0
        self._terminal_eof = False
        self._telemetry_eof = False
        self._started = False
        self._closed = False
        self._start_ns = 0
        self._last_cast_us = 0
        self._cast_events = 0
        self._output_mode = "real"
        self._replacement_output = ""
        self._terminal_thread: threading.Thread | None = None
        self._telemetry_thread: threading.Thread | None = None

    @property
    def raw_offset(self) -> int:
        with self._output_condition:
            return self._raw_offset

    @property
    def elapsed_ms(self) -> int:
        return round((time.monotonic_ns() - self._start_ns) / 1_000_000)

    @property
    def cast_event_count(self) -> int:
        with self._artifact_lock:
            return self._cast_events

    def start(self) -> EnvoyReady:
        if self._started:
            if self.ready is None:  # pragma: no cover - defensive invariant
                raise EnvoySessionError("Envoy session started without readiness")
            return self.ready
        if self._closed:
            raise EnvoySessionError("Envoy session is already closed")
        self.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.timeline_path.write_text("", encoding="utf-8")
        self.telemetry_path.write_text("", encoding="utf-8")
        self.diagnostics_path.write_text("", encoding="utf-8")
        self.raw_path.write_bytes(b"")
        self._start_ns = time.monotonic_ns()
        if self.record_cast:
            header = {
                "version": 3,
                "term": {"cols": self.columns, "rows": self.rows},
                "title": self.title,
            }
            self.cast_path.write_text(
                json.dumps(header, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        try:
            # The ordering is part of the v1 handshake contract.
            self._terminal = socket.create_connection(
                self.terminal_address, timeout=self.connect_timeout
            )
            self._telemetry = socket.create_connection(
                self.telemetry_address, timeout=self.connect_timeout
            )
            self._terminal.settimeout(None)
            self._telemetry.settimeout(None)
        except OSError as exc:
            self.abort()
            raise EnvoySessionError(f"could not connect to Envoy: {exc}") from exc
        self._terminal_thread = threading.Thread(
            target=self._read_terminal,
            name="omegaflow-envoy-terminal",
            daemon=True,
        )
        self._telemetry_thread = threading.Thread(
            target=self._read_telemetry,
            name="omegaflow-envoy-telemetry",
            daemon=True,
        )
        self._terminal_thread.start()
        self._telemetry_thread.start()
        self._started = True
        self._send_telemetry(self.state.hello(self.session_id))
        event = self._next_event(self.connect_timeout)
        if not isinstance(event, EnvoyReady):
            self.abort()
            raise EnvoySessionError(
                f"Envoy first event was {event.type!r}, expected 'ready'"
            )
        self.ready = event
        self._append_timeline("session_start", cwd=event.cwd)
        return event

    def execute(
        self,
        operation_id: str,
        source: str,
        *,
        timeout: float | None = None,
        on_gate: Callable[[str], None] | None = None,
    ) -> EnvoyOperationResult:
        self._require_running()
        self._send_telemetry(self.state.execute(operation_id, source))
        deadline = time.monotonic() + (self.control_timeout if timeout is None else timeout)
        started: OperationStarted | None = None
        timed_out = False
        gate_error: BaseException | None = None
        while True:
            try:
                event = self._next_event(max(0.0, deadline - time.monotonic()))
            except EnvoySessionError as exc:
                if (
                    not timed_out
                    and "timed out" in str(exc)
                    and self.state.phase in {"running", "gated", "continuing"}
                ):
                    timed_out = True
                    self._send_telemetry(
                        self.state.cancel(operation_id, "operation-timeout")
                    )
                    deadline = time.monotonic() + 6.0
                    continue
                raise
            if isinstance(event, OperationStarted):
                started = event
                self._append_timeline(
                    "operation_start",
                    operation_id=operation_id,
                    output_start=event.output_start,
                )
            elif isinstance(event, OperationReady):
                self.wait_output(event.output_through, deadline=deadline)
                if on_gate is not None:
                    try:
                        on_gate(event.gate_id)
                    except BaseException as exc:
                        gate_error = exc
                        self._send_telemetry(
                            self.state.cancel(operation_id, "controller-gate-failed")
                        )
                        deadline = time.monotonic() + 6.0
                        continue
                self._send_telemetry(self.state.continue_gate(operation_id, event.gate_id))
            elif isinstance(event, OperationContinued):
                self.wait_output(event.output_through, deadline=deadline)
            elif isinstance(event, OperationCompleted):
                self.wait_output(event.output_through, deadline=deadline)
                self._append_timeline(
                    "operation_end",
                    operation_id=operation_id,
                    status=event.status,
                    cwd=event.cwd,
                    output_through=event.output_through,
                )
                return EnvoyOperationResult(
                    operation_id,
                    event.status,
                    event.cwd,
                    event.output_start,
                    event.output_through,
                )
            elif isinstance(event, OperationCancelled):
                self.wait_output(event.output_through, deadline=deadline)
                if gate_error is not None:
                    raise EnvoySessionError(f"action gate failed: {gate_error}") from gate_error
                return EnvoyOperationResult(
                    operation_id,
                    event.status,
                    event.cwd,
                    event.output_start,
                    event.output_through,
                    cancelled=True,
                )
            elif isinstance(event, OperationFailed):
                self.wait_output(event.output_through, deadline=deadline)
                if gate_error is not None:
                    raise EnvoySessionError(f"action gate failed: {gate_error}") from gate_error
                return EnvoyOperationResult(
                    operation_id,
                    None,
                    event.cwd,
                    event.output_start,
                    event.output_through,
                    failure_code=event.code,
                    failure_message=event.message,
                )
            elif isinstance(event, (Draining, Closed)):
                raise EnvoySessionError(
                    f"Envoy exited during operation {operation_id!r}"
                )
            if started is None and time.monotonic() >= deadline:
                raise EnvoySessionError(f"operation {operation_id!r} did not start")

    def send_input(self, payload: bytes) -> None:
        self._require_running()
        terminal = self._terminal
        if terminal is None:  # pragma: no cover - guarded above
            raise EnvoySessionError("terminal channel is unavailable")
        try:
            terminal.sendall(payload)
        except OSError as exc:
            raise EnvoySessionError(f"could not send terminal input: {exc}") from exc

    def cancel(self, operation_id: str, reason: str = "controller-cancelled") -> None:
        self._require_running()
        self._send_telemetry(self.state.cancel(operation_id, reason))

    def resize(self, columns: int, rows: int) -> None:
        self._require_running()
        self._send_telemetry(self.state.resize(columns, rows))
        deadline = time.monotonic() + self.control_timeout
        while True:
            event = self._next_event(max(0.0, deadline - time.monotonic()))
            if isinstance(event, ResizeApplied):
                self.columns, self.rows = event.columns, event.rows
                self._write_cast("r", f"{event.columns}x{event.rows}")
                return
            if isinstance(event, (OperationCompleted, OperationCancelled, OperationFailed)):
                raise EnvoySessionError("operation ended while resize was pending")

    def present(self, text: str, *, delay: float = 0.0, phase: str = "presentation") -> None:
        if delay < 0:
            raise ValueError("presentation delay must be non-negative")
        if delay:
            time.sleep(delay)
        self._write_cast("o", text)
        self._append_timeline(phase, text=text)

    def begin_operation_output(self, mode: str, replacement: str = "") -> None:
        if mode not in {"real", "hidden", "replace"}:
            raise ValueError(f"unsupported terminal output mode {mode!r}")
        with self._artifact_lock:
            self._output_mode = mode
            self._replacement_output = replacement

    def end_operation_output(self) -> None:
        with self._artifact_lock:
            mode = self._output_mode
            replacement = self._replacement_output
            self._output_mode = "real"
            self._replacement_output = ""
        if mode == "replace" and replacement:
            self._write_cast("o", replacement)

    def cast_checkpoint(self) -> tuple[int, int]:
        with self._artifact_lock:
            return self.cast_path.stat().st_size, self._cast_events

    def write_cast_slice(self, start: int, through: int, destination: Path) -> None:
        if start < 0 or through < start:
            raise ValueError("invalid cast byte range")
        with self._artifact_lock:
            with self.cast_path.open("rb") as handle:
                header = handle.readline()
                handle.seek(start)
                payload = handle.read(through - start)
        lines = payload.splitlines()
        if lines:
            first = json.loads(lines[0])
            first[0] = 0
            lines[0] = json.dumps(first, separators=(",", ":")).encode("utf-8")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            handle.write(header)
            for line in lines:
                handle.write(line + b"\n")

    def read_output_range(self, start: int, through: int) -> bytes:
        if start < 0 or through < start:
            raise ValueError("invalid terminal output range")
        self.wait_output(through)
        with self.raw_path.open("rb") as handle:
            handle.seek(start)
            return handle.read(through - start)

    def wait_output(self, through: int, *, deadline: float | None = None) -> None:
        if through < 0:
            raise ValueError("output barrier must be non-negative")
        effective_deadline = (
            time.monotonic() + self.control_timeout if deadline is None else deadline
        )
        with self._output_condition:
            while self._raw_offset < through:
                if self._terminal_eof:
                    raise EnvoySessionError(
                        f"terminal closed at {self._raw_offset} before barrier {through}"
                    )
                remaining = effective_deadline - time.monotonic()
                if remaining <= 0:
                    raise EnvoySessionError(
                        f"terminal output did not reach barrier {through}"
                    )
                self._output_condition.wait(min(remaining, 0.25))

    def close(self, reason: str = "capture-complete") -> None:
        if self._closed:
            return
        if not self._started:
            self._closed = True
            return
        deadline = time.monotonic() + self.control_timeout
        self._send_telemetry(self.state.shutdown(reason))
        saw_draining = False
        while self.state.phase != "closed":
            event = self._next_event(max(0.0, deadline - time.monotonic()))
            if isinstance(event, Draining):
                saw_draining = True
                self.wait_output(event.output_through, deadline=deadline)
            elif isinstance(event, Closed):
                if not saw_draining:
                    raise EnvoySessionError("Envoy closed without draining")
                self.wait_output(event.output_through, deadline=deadline)
        self.state.finish()
        with self._output_condition:
            while not self._terminal_eof:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EnvoySessionError("terminal channel did not close after Envoy closed")
                self._output_condition.wait(min(remaining, 0.25))
        self._append_timeline("session_end")
        self._closed = True
        self._close_sockets()
        self._join_readers()

    def abort(self) -> None:
        self._closed = True
        self._close_sockets()
        self._join_readers()

    def _next_event(self, timeout: float) -> EnvoyEvent:
        if timeout <= 0:
            raise EnvoySessionError("timed out waiting for Envoy telemetry")
        try:
            item = self._events.get(timeout=timeout)
        except queue.Empty as exc:
            raise EnvoySessionError("timed out waiting for Envoy telemetry") from exc
        if isinstance(item, BaseException):
            raise EnvoySessionError(f"Envoy telemetry failed: {item}") from item
        try:
            self.state.accept(item)
        except EnvoyProtocolError as exc:
            raise EnvoySessionError(f"invalid Envoy state transition: {exc}") from exc
        self._record_event(item)
        return item

    def _record_event(self, event: EnvoyEvent) -> None:
        payload = asdict(event)
        payload["schema"] = "omegaflow-envoy-telemetry-v1"
        ordered = {"schema": payload.pop("schema"), "type": payload.pop("type"), **payload}
        line = json.dumps(ordered, separators=(",", ":")) + "\n"
        with self._artifact_lock:
            with self.telemetry_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            if isinstance(event, EnvoyDiagnostic):
                with self.diagnostics_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                self.diagnostics.append(event)

    def _read_terminal(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending_mode: str | None = None
        terminal = self._terminal
        if terminal is None:
            return
        try:
            with self.raw_path.open("ab", buffering=0) as raw:
                while True:
                    chunk = terminal.recv(65536)
                    if not chunk:
                        break
                    raw.write(chunk)
                    os.fsync(raw.fileno())
                    with self._artifact_lock:
                        mode = self._output_mode
                    if decoder.getstate()[0] and pending_mode != mode:
                        tail = decoder.decode(b"", final=True)
                        if tail and pending_mode == "real":
                            self._write_cast("o", tail)
                        decoder = codecs.getincrementaldecoder("utf-8")(
                            errors="replace"
                        )
                    text = decoder.decode(chunk)
                    if text and mode == "real":
                        self._write_cast("o", text)
                    pending_mode = mode if decoder.getstate()[0] else None
                    # An output barrier covers both durable raw bytes and their
                    # corresponding cast decision.  Publishing the offset
                    # earlier lets end_operation_output() race this reader and
                    # apply the next operation's visibility policy.
                    with self._output_condition:
                        self._raw_offset += len(chunk)
                        self._output_condition.notify_all()
                tail = decoder.decode(b"", final=True)
                if tail and pending_mode == "real":
                    self._write_cast("o", tail)
        except BaseException as exc:
            self._events.put(exc)
        finally:
            with self._output_condition:
                self._terminal_eof = True
                self._output_condition.notify_all()

    def _read_telemetry(self) -> None:
        decoder = EnvoyStreamDecoder()
        telemetry = self._telemetry
        if telemetry is None:
            return
        try:
            while True:
                chunk = telemetry.recv(65536)
                if not chunk:
                    break
                for event in decoder.feed(chunk):
                    self._events.put(event)
            decoder.finish()
            if self.state.phase != "closed":
                self._events.put(
                    EnvoyProtocolError(
                        f"telemetry closed before closed event (phase {self.state.phase})"
                    )
                )
        except BaseException as exc:
            self._events.put(exc)
        finally:
            self._telemetry_eof = True

    def _send_telemetry(self, frame: bytes) -> None:
        telemetry = self._telemetry
        if telemetry is None:
            raise EnvoySessionError("telemetry channel is unavailable")
        try:
            with self._write_lock:
                telemetry.sendall(frame)
        except OSError as exc:
            raise EnvoySessionError(f"could not write Envoy telemetry: {exc}") from exc

    def _write_cast(self, kind: str, payload: str) -> None:
        if not self.record_cast or not payload:
            return
        with self._artifact_lock:
            now_us = max(
                self._last_cast_us,
                (time.monotonic_ns() - self._start_ns) // 1_000,
            )
            delta = (now_us - self._last_cast_us) / 1_000_000
            self._last_cast_us = now_us
            with self.cast_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps([delta, kind, payload], separators=(",", ":")) + "\n")
            self._cast_events += 1

    def _append_timeline(self, phase: str, **values: object) -> None:
        event = {
            "time_ms": self.elapsed_ms,
            "phase": phase,
            **values,
        }
        with self._artifact_lock:
            with self.timeline_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, separators=(",", ":")) + "\n")

    def _require_running(self) -> None:
        if not self._started or self._closed:
            raise EnvoySessionError("Envoy session is not running")

    def _close_sockets(self) -> None:
        for connection in (self._terminal, self._telemetry):
            if connection is None:
                continue
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._terminal = None
        self._telemetry = None

    def _join_readers(self) -> None:
        current = threading.current_thread()
        for thread in (self._terminal_thread, self._telemetry_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1.0)
