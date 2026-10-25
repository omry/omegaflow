"""Serial, bounded Envoy channel ownership; no recorder or Reploy entrypoint."""

from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import io
import math
import os
import socket
import stat
from typing import BinaryIO

from .envoy_codecs import EventFrames, SCHEMA, encode


CONNECT_SECONDS = 10.0
READY_SECONDS = 10.0
WRITE_SECONDS = 5.0
MAX_EARLY_BYTES = 4096
MAX_RAW_BYTES = 8 << 30
_READ_BYTES = 65536


class EnvoyTransportError(RuntimeError):
    """The selected pair cannot provide a usable Envoy session."""


def _write_all(sink: BinaryIO, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        count = sink.write(remaining)
        if type(count) is not int or not 0 < count <= len(remaining):
            raise OSError("invalid evidence write count")
        remaining = remaining[count:]
    sink.flush()


async def _connect_socket(host: str, port: int) -> socket.socket:
    """Resolve initial addresses without creating a background read buffer."""
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    error = OSError("endpoint resolved to no stream addresses")
    for family, kind, protocol, _, address in addresses:
        selected = socket.socket(family, kind, protocol)
        selected.setblocking(False)
        try:
            await loop.sock_connect(selected, address)
            return selected
        except OSError as failed:
            error = failed
            selected.close()
        except BaseException:
            selected.close()
            raise
    raise error


class EnvoyTransport:
    """Own one terminal/telemetry pair and one fresh caller-owned raw log.

    All calls belong to one event loop and one serial caller. The caller owns
    and closes both evidence files. This class closes only its connections and
    reads and in-flight socket writes. It never retries or reports a Reploy
    lifecycle outcome. Telemetry bytes go to their separate evidence sink,
    including malformed input.
    """

    def __init__(self, terminal: socket.socket, telemetry: socket.socket, *, raw_log: BinaryIO,
                 telemetry_log: BinaryIO) -> None:
        self._channels = {"terminal": terminal, "telemetry": telemetry}
        self._reads: dict[str, asyncio.Task] = {}
        self._writes: set[asyncio.Task] = set()
        self._events = deque()
        self._frames = EventFrames()
        self._raw_log = raw_log
        self._telemetry_log = telemetry_log
        self._early = bytearray()
        self._ready_event: dict | None = None
        self._ready = False
        self._closed = False
        self._raw_offset = 0
        self._eof: set[str] = set()
        self._hello_completed_at: float | None = None
        self._receiving = False
        self._control_writing = False
        self._terminal_writing = False
        raw_stat = os.fstat(raw_log.fileno())
        if raw_log is telemetry_log or not stat.S_ISREG(raw_stat.st_mode):
            raise ValueError("raw output requires a separate regular file")
        try:
            telemetry_stat = os.fstat(telemetry_log.fileno())
        except (AttributeError, io.UnsupportedOperation):
            pass  # In-memory telemetry evidence is useful for isolated callers.
        else:
            if (raw_stat.st_dev, raw_stat.st_ino) == (telemetry_stat.st_dev, telemetry_stat.st_ino):
                raise ValueError("raw output and telemetry logs identify the same file")
        if raw_log.seek(0, os.SEEK_END) != 0:
            raise ValueError("raw output log must be empty")
        raw_log.seek(0)
        if raw_log.write(b"") != 0:
            raise ValueError("raw output requires a writable binary file")

    @classmethod
    async def connect(cls, terminal: tuple[str, int], telemetry: tuple[str, int], *,
                      session_id: str, bootstrap_written_at: float,
                      raw_log: BinaryIO, telemetry_log: BinaryIO) -> EnvoyTransport:
        """Resolve/connect terminal first, sharing the original bootstrap epoch."""
        loop = asyncio.get_running_loop()
        if (type(bootstrap_written_at) not in {int, float}
                or not math.isfinite(bootstrap_written_at)
                or bootstrap_written_at > loop.time()):
            raise ValueError("invalid bootstrap completion epoch")
        hello = encode({"schema": SCHEMA, "type": "hello", "seq": 1, "session_id": session_id})
        for address in (terminal, telemetry):
            if (type(address) is not tuple or len(address) != 2
                    or type(address[0]) is not str or not address[0] or "\0" in address[0]
                    or type(address[1]) is not int or not 1 <= address[1] <= 65535):
                raise ValueError("invalid channel coordinate")
        opened = []
        owner = None
        try:
            deadline = bootstrap_written_at + CONNECT_SECONDS
            if loop.time() >= deadline:
                raise TimeoutError("connect deadline expired")
            async with asyncio.timeout_at(deadline):
                for host, port in (terminal, telemetry):
                    opened.append(await _connect_socket(host, port))
            owner = cls(*opened, raw_log=raw_log, telemetry_log=telemetry_log)
            await owner._write_control(hello)
            owner._hello_completed_at = loop.time()
            return owner
        except BaseException:
            if owner is not None:
                await owner.close()
            else:
                for selected in opened:
                    selected.close()
            raise

    @property
    def ready(self) -> bool:
        return self._ready and not self._closed

    @property
    def ready_event(self) -> dict | None:
        return deepcopy(self._ready_event) if self.ready else None

    @property
    def raw_offset(self) -> int:
        return self._raw_offset

    async def _send(self, channel: str, data: bytes) -> None:
        # Own only the socket operation, not the caller's controller task.
        task = asyncio.create_task(
            asyncio.get_running_loop().sock_sendall(self._channels[channel], data))
        self._writes.add(task)
        try:
            await task
        finally:
            self._writes.discard(task)

    async def _write_control(self, frame: bytes) -> None:
        if self._closed or self._control_writing:
            raise EnvoyTransportError("control write requires serial channel ownership")
        self._control_writing = True
        try:
            async with asyncio.timeout(WRITE_SECONDS):
                await self._send("telemetry", frame)
        except BaseException:
            await self.close()
            raise
        finally:
            self._control_writing = False

    async def write_control(self, frame: bytes) -> None:
        """Write one already-encoded request under its individual write budget.

        The Envoy client owns canonical encoding and request sequencing. No
        second writer may use this selected telemetry connection.
        """
        if not self.ready or type(frame) is not bytes:
            raise EnvoyTransportError("control write requires ready, encoded bytes")
        await self._write_control(frame)

    async def write_terminal(self, data: bytes) -> None:
        """Write exact admitted input; the control-frame timer does not apply.

        On partial failure or cancellation, close the pair. The caller must
        never use an uncertain byte count for a subsequent request.
        """
        if not self.ready or self._terminal_writing or type(data) is not bytes:
            raise EnvoyTransportError("terminal write requires ready, serial byte ownership")
        self._terminal_writing = True
        try:
            await self._send("terminal", data)
        except BaseException:
            await self.close()
            raise
        finally:
            self._terminal_writing = False

    def _append(self, data: bytes) -> None:
        if (self._raw_log.tell() != self._raw_offset
                or os.fstat(self._raw_log.fileno()).st_size != self._raw_offset):
            raise EnvoyTransportError("raw output log changed outside its serial owner")
        if self._raw_offset + len(data) > MAX_RAW_BYTES:
            raise EnvoyTransportError("raw output exceeds session byte bound")
        _write_all(self._raw_log, data)
        os.fsync(self._raw_log.fileno())
        self._raw_offset += len(data)

    def _commit_ready(self) -> None:
        if self._ready_event is not None and self._raw_offset == self._ready_event["output_through"]:
            self._ready = True

    async def _read_one(self, *, terminal_only: bool = False) -> tuple[str, bytes]:
        loop = asyncio.get_running_loop()
        # Consume a completed read before replenishing the other channel. A
        # continuously readable terminal must not bypass telemetry that has
        # already reached the event loop.
        for name in ("terminal", "telemetry"):
            if terminal_only and name == "telemetry":
                continue
            task = self._reads.get(name)
            if task is not None and task.done():
                del self._reads[name]
                return name, task.result()
        for name, selected in self._channels.items():
            if terminal_only and name == "telemetry":
                continue
            if name not in self._reads and name not in self._eof:
                count = _READ_BYTES
                if name == "terminal" and not self._ready:
                    remaining = (MAX_EARLY_BYTES - len(self._early) if self._ready_event is None
                                 else self._ready_event["output_through"] - self._raw_offset)
                    count = remaining + 1  # One extra byte detects an overrun without retaining it.
                self._reads[name] = asyncio.create_task(loop.sock_recv(selected, count))
        if not self._reads:
            if terminal_only and "terminal" in self._eof:
                raise EnvoyTransportError("terminal EOF before readiness barrier")
            raise EnvoyTransportError("both selected channels ended")
        await asyncio.wait(self._reads.values(), return_when=asyncio.FIRST_COMPLETED)
        # Completed reads on the other channel remain owned until consumed.
        for name in ("terminal", "telemetry"):
            if terminal_only and name == "telemetry":
                continue
            task = self._reads.get(name)
            if task is not None and task.done():
                del self._reads[name]
                return name, task.result()
        raise AssertionError("wait returned without a completed read")

    def _event(self, event: dict) -> tuple[str, dict]:
        kind = event["type"]
        if kind == "diagnostic" and event["severity"] == "fatal":
            raise EnvoyTransportError("fatal Envoy diagnostic: " + event["code"])
        if kind == "ready":
            if self._ready_event is not None:
                raise EnvoyTransportError("duplicate ready")
            if len(self._early) > event["output_through"]:
                raise EnvoyTransportError("early output exceeds ready barrier")
            self._ready_event = event
            self._append(bytes(self._early))
            self._early.clear()
            self._commit_ready()
        elif self._ready_event is not None and not self._ready:
            # Keep the complete parsed batch in sequence until the terminal
            # bytes reach the advertised raw offset. _read_one then applies
            # backpressure by not replenishing the telemetry read.
            self._events.appendleft(event)
            return "telemetry-deferred", b""
        elif not self._ready and kind != "diagnostic":
            raise EnvoyTransportError("event before complete readiness")
        return "event", event

    async def _receive(self) -> tuple[str, bytes | dict]:
        if self._events and not self._ready:
            fatal = next((event for event in self._events
                          if event["type"] == "diagnostic" and event["severity"] == "fatal"), None)
            if fatal is not None:
                self._event(fatal)
        if self._events and (self._ready or self._ready_event is None):
            return self._event(self._events.popleft())
        terminal_only = bool(self._events and self._ready_event is not None and not self._ready)
        name, data = await self._read_one(terminal_only=terminal_only)
        if not data:
            self._eof.add(name)
            if name == "telemetry":
                self._frames.eof()
            if not self._ready:
                raise EnvoyTransportError(name + " EOF before readiness barrier")
            return name + "-eof", b""
        if name == "telemetry":
            _write_all(self._telemetry_log, data)
            self._events.extend(event for _, event in self._frames.feed(data))
            if self._events:
                if not self._ready:
                    fatal = next((event for event in self._events
                                  if event["type"] == "diagnostic" and event["severity"] == "fatal"), None)
                    if fatal is not None:
                        self._event(fatal)
                if self._ready_event is not None and not self._ready:
                    return "telemetry-deferred", b""
                return self._event(self._events.popleft())
            return "telemetry-partial", b""
        if self._ready_event is None:
            if len(self._early) + len(data) > MAX_EARLY_BYTES:
                raise EnvoyTransportError("pre-ready output exceeds byte bound")
            self._early.extend(data)
            return "terminal-pending", b""
        if not self._ready and self._raw_offset + len(data) > self._ready_event["output_through"]:
            raise EnvoyTransportError("raw output exceeds readiness barrier")
        self._append(data)
        self._commit_ready()
        return "terminal", data

    async def wait_ready(self) -> dict:
        """Return readiness only after the exact startup raw-log barrier."""
        if self._closed or self._receiving or self._hello_completed_at is None or self._ready:
            raise EnvoyTransportError("readiness call is not admissible")
        self._receiving = True
        deadline = self._hello_completed_at + READY_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                while not self._ready:
                    await self._receive()
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("readiness deadline expired")
            return deepcopy(self._ready_event)
        except BaseException:
            await self.close()
            raise
        finally:
            self._receiving = False

    async def receive(self) -> tuple[str, bytes | dict]:
        """Observe one event or already-retained raw chunk after readiness.

        EOF is an observation for C3's lifecycle owner, never session success.
        No operation-level output barrier is inferred here.
        """
        if not self.ready or self._receiving:
            raise EnvoyTransportError("receive requires ready, serial channel ownership")
        self._receiving = True
        try:
            return await self._receive()
        except BaseException:
            await self.close()
            raise
        finally:
            self._receiving = False

    async def close(self) -> None:
        """Settle owned I/O before closing sockets; preserve caller-owned logs."""
        if self._closed:
            return
        self._closed = True
        self._ready = False
        pending = [task for task in (*self._reads.values(), *self._writes)
                   if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for selected in self._channels.values():
                selected.close()
            self._reads.clear()
            self._writes.clear()
