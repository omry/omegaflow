"""C3.1 owns real local TCP sockets; the server is a deterministic test peer."""

import asyncio
import io
import os
from tempfile import NamedTemporaryFile, TemporaryFile
import time

import pytest

from omegaflow import envoy_transport as transport
from omegaflow.envoy_codecs import EnvoyCodecError, SCHEMA, encode


SESSION_ID = "0123456789abcdef0123456789abcdef"


def ready(offset=0, seq=1):
    return encode({"schema": SCHEMA, "type": "ready", "seq": seq, "envoy_pid": 100,
                   "shell_pid": 102, "cwd": "/work", "columns": 80, "rows": 24,
                   "elapsed_us": 0, "output_through": offset}, controller=False)


def diagnostic(seq=1, severity="warning"):
    return encode({"schema": SCHEMA, "type": "diagnostic", "seq": seq,
                   "severity": severity, "code": "peer-diagnostic", "message": "retained"}, controller=False)


def output_mark(seq=2, offset=3):
    return encode({"schema": SCHEMA, "type": "output_mark", "seq": seq,
                   "offset": offset, "stream": "pty", "elapsed_us": 10000}, controller=False)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


class Peer:
    """Two loopback listeners with externally controlled reads, writes and EOF."""

    async def __aenter__(self):
        self.channels = {}
        self.tasks = []
        self.order = []
        self.servers = {}
        self.stop = asyncio.Event()
        for name in ("terminal", "telemetry"):
            async def accepted(reader, writer, name=name):
                self.tasks.append(asyncio.current_task())
                self.channels[name] = (reader, writer)
                self.order.append(name)
                await self.stop.wait()
            self.servers[name] = await asyncio.start_server(accepted, "127.0.0.1", 0)
        self.addresses = {name: ("127.0.0.1", server.sockets[0].getsockname()[1])
                          for name, server in self.servers.items()}
        return self

    async def __aexit__(self, *args):
        for server in self.servers.values():
            server.close()
        self.stop.set()
        for _, writer in self.channels.values():
            writer.transport.abort()
        await asyncio.gather(*self.tasks, *(w.wait_closed() for _, w in self.channels.values()), return_exceptions=True)
        await asyncio.gather(*(server.wait_closed() for server in self.servers.values()))

    async def write(self, name, data):
        await until(lambda: name in self.channels)
        self.channels[name][1].write(data)
        await self.channels[name][1].drain()

    async def eof(self, name):
        await until(lambda: name in self.channels)
        self.channels[name][1].write_eof()


async def connected(peer, raw, telemetry=None, **kwargs):
    owner = await transport.EnvoyTransport.connect(
        peer.addresses["terminal"], peer.addresses["telemetry"], session_id=SESSION_ID,
        bootstrap_written_at=asyncio.get_running_loop().time(), raw_log=raw,
        telemetry_log=telemetry if telemetry is not None else io.BytesIO(), **kwargs)
    await until(lambda: "telemetry" in peer.channels)
    hello = await peer.channels["telemetry"][0].readuntil(b"\n")
    assert hello == encode({"schema": SCHEMA, "type": "hello", "seq": 1, "session_id": SESSION_ID})
    assert peer.order == ["terminal", "telemetry"]
    return owner


async def first_terminal_receive(peer, owner, telemetry_data, *, yield_after_terminal):
    """Start both pull tasks before a write can complete the terminal pull."""
    first_receive = asyncio.create_task(owner.receive())
    try:
        await until(lambda: owner._reads.get("terminal") is not None
                     and owner._reads.get("telemetry") is not None
                     and not owner._reads["terminal"].done()
                     and not owner._reads["telemetry"].done())
        await peer.write("terminal", b"x" * (2 << 20))
        if yield_after_terminal:
            await asyncio.sleep(0.01)
        await peer.write("telemetry", telemetry_data)
        return await first_receive
    finally:
        if not first_receive.done():
            first_receive.cancel()
        await asyncio.gather(first_receive, return_exceptions=True)


def raw_bytes(raw):
    return os.pread(raw.fileno(), 100000, 0)


@pytest.mark.parametrize("size", [0, 1, 4096])
@pytest.mark.parametrize("early", [False, True])
def test_exact_startup_barrier_before_prompt_or_request(size, early):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                telemetry = io.BytesIO()
                owner = await connected(peer, raw, telemetry)
                startup = (b"\xff\0\r\n\x1b[?2004h" * 400)[:size]
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    assert not owner.ready and owner.ready_event is None
                    with pytest.raises(transport.EnvoyTransportError):
                        await owner.receive()
                    if early and startup:
                        await peer.write("terminal", startup)
                        await until(lambda: len(owner._early) == len(startup))
                        assert raw_bytes(raw) == b"" and owner.raw_offset == 0
                        assert not waiter.done() and not owner.ready
                    await peer.write("telemetry", ready(size))
                    if not early and startup:
                        await until(lambda: len(telemetry.getvalue()) > 0)
                        assert not owner.ready and not waiter.done()
                        assert raw_bytes(raw) == b""
                        await peer.write("terminal", startup)
                    event = await asyncio.wait_for(waiter, 2)
                    assert event["output_through"] == size
                    assert owner.ready and owner.raw_offset == size
                    assert raw_bytes(raw) == startup
                    assert telemetry.getvalue() == ready(size)
                    event["cwd"] = "/mutated"
                    assert owner.ready_event["cwd"] == "/work"
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
                assert not owner._reads and not owner.ready
                assert not raw.closed and not telemetry.closed
    asyncio.run(run())


def test_post_ready_terminal_is_binary_and_never_telemetry():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                try:
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    binary = b'\xff\x00{"type":"operation_completed"}\n\r\x1b[31m'
                    await peer.write("terminal", binary)
                    assert await owner.receive() == ("terminal", binary)
                    assert raw_bytes(raw) == binary and owner.raw_offset == len(binary)
                    await peer.eof("terminal")
                    assert await owner.receive() == ("terminal-eof", b"")
                    assert owner.ready  # EOF alone never declares lifecycle success.
                    await peer.eof("telemetry")
                    assert await owner.receive() == ("telemetry-eof", b"")
                    with pytest.raises(transport.EnvoyTransportError, match="both selected"):
                        await owner.receive()
                    assert not owner.ready
                finally:
                    await owner.close()
    asyncio.run(run())


@pytest.mark.parametrize("yield_after_terminal", [False, True],
                         ids=["ordinary-write", "write-drain-yield"])
def test_fatal_telemetry_is_not_starved_by_sustained_terminal_readability(yield_after_terminal):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                try:
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    kind, _ = await first_terminal_receive(
                        peer, owner, diagnostic(2, "fatal"),
                        yield_after_terminal=yield_after_terminal)
                    assert kind == "terminal"
                    await until(lambda: owner._reads.get("telemetry") is not None
                                 and owner._reads["telemetry"].done())
                    with pytest.raises(transport.EnvoyTransportError, match="fatal"):
                        for _ in range(16):
                            await owner.receive()
                    assert log.getvalue() == ready() + diagnostic(2, "fatal")
                    assert 0 < owner.raw_offset <= transport.MAX_EARLY_BYTES + 1
                finally:
                    await owner.close()
    asyncio.run(run())


@pytest.mark.parametrize("yield_after_terminal", [False, True],
                         ids=["ordinary-write", "write-drain-yield"])
def test_warning_telemetry_is_not_starved_by_sustained_terminal_readability(yield_after_terminal):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                try:
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    kind, _ = await first_terminal_receive(
                        peer, owner, diagnostic(2),
                        yield_after_terminal=yield_after_terminal)
                    assert kind == "terminal"
                    await until(lambda: owner._reads.get("telemetry") is not None
                                 and owner._reads["telemetry"].done())
                    for _ in range(16):
                        kind, event = await owner.receive()
                        if kind == "event":
                            assert event == {
                                "schema": SCHEMA,
                                "type": "diagnostic",
                                "seq": 2,
                                "severity": "warning",
                                "code": "peer-diagnostic",
                                "message": "retained",
                            }
                            break
                    else:
                        pytest.fail("completed telemetry diagnostic was starved")
                    assert log.getvalue() == ready() + diagnostic(2)
                    assert 0 < owner.raw_offset <= transport.MAX_EARLY_BYTES + 1
                finally:
                    await owner.close()
    asyncio.run(run())


def test_fragmented_ready_and_coalesced_following_event_are_not_lost():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    line = ready()
                    for chunk in (line[:3], line[3:70], line[70:-1]):
                        await peer.write("telemetry", chunk)
                        await asyncio.sleep(0)
                        assert not owner.ready
                    await peer.write("telemetry", line[-1:] + diagnostic(2))
                    await asyncio.wait_for(waiter, 2)
                    kind, event = await owner.receive()
                    assert kind == "event" and event["seq"] == 2 and event["type"] == "diagnostic"
                    assert log.getvalue() == line + diagnostic(2)
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("schedule", ["separate", "coalesced", "terminal-first"])
def test_following_output_mark_waits_for_exact_startup_barrier(schedule):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    await until(lambda: owner._reads.get("terminal") is not None
                                 and owner._reads.get("telemetry") is not None
                                 and not owner._reads["terminal"].done()
                                 and not owner._reads["telemetry"].done())
                    startup = b"abc"
                    mark = output_mark()
                    if schedule == "terminal-first":
                        await peer.write("terminal", startup)
                        await until(lambda: len(owner._early) == len(startup))
                        await peer.write("telemetry", ready(len(startup)) + mark)
                    else:
                        telemetry_data = ready(len(startup)) + mark if schedule == "coalesced" else ready(len(startup))
                        await peer.write("telemetry", telemetry_data)
                        await until(lambda: owner._ready_event is not None)
                        if schedule == "separate":
                            await peer.write("telemetry", mark)
                        await until(lambda: owner._events or owner.ready)
                        assert not owner.ready
                        assert owner._events
                        assert len(owner._events) < transport._READ_BYTES
                        assert not owner._reads.get("telemetry")
                        await peer.write("terminal", startup)
                    event = await asyncio.wait_for(waiter, 2)
                    assert event["output_through"] == len(startup)
                    assert owner.ready and owner.raw_offset == len(startup)
                    assert await owner.receive() == ("event", {
                        "schema": SCHEMA,
                        "type": "output_mark",
                        "seq": 2,
                        "offset": 3,
                        "stream": "pty",
                        "elapsed_us": 10000,
                    })
                    assert not owner._events
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


def test_telemetry_deferral_stops_reads_and_preserves_following_events():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    await until(lambda: owner._reads.get("terminal") is not None
                                 and owner._reads.get("telemetry") is not None
                                 and not owner._reads["terminal"].done()
                                 and not owner._reads["telemetry"].done())
                    burst = output_mark() + diagnostic(3)
                    await peer.write("telemetry", ready(3) + burst)
                    await until(lambda: owner._events)
                    assert not owner.ready
                    assert 1 < len(owner._events) < transport._READ_BYTES
                    assert not owner._reads.get("telemetry")
                    await peer.write("terminal", b"abc")
                    await asyncio.wait_for(waiter, 2)
                    assert await owner.receive() == ("event", {
                        "schema": SCHEMA,
                        "type": "output_mark",
                        "seq": 2,
                        "offset": 3,
                        "stream": "pty",
                        "elapsed_us": 10000,
                    })
                    assert await owner.receive() == ("event", {
                        "schema": SCHEMA,
                        "type": "diagnostic",
                        "seq": 3,
                        "severity": "warning",
                        "code": "peer-diagnostic",
                        "message": "retained",
                    })
                    assert log.getvalue() == ready(3) + burst
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("schedule", ["separate", "coalesced"])
def test_fatal_after_ready_fails_before_terminal_barrier_release(schedule):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    await until(lambda: owner._reads.get("terminal") is not None
                                 and owner._reads.get("telemetry") is not None
                                 and not owner._reads["terminal"].done()
                                 and not owner._reads["telemetry"].done())
                    fatal = diagnostic(2, "fatal")
                    if schedule == "coalesced":
                        await peer.write("telemetry", ready(3) + fatal)
                    else:
                        await peer.write("telemetry", ready(3))
                        await until(lambda: owner._ready_event is not None)
                        await peer.write("telemetry", fatal)
                    with pytest.raises(transport.EnvoyTransportError, match="fatal"):
                        await asyncio.wait_for(waiter, 2)
                    assert not owner.ready and owner.raw_offset == 0
                    assert raw_bytes(raw) == b""
                    assert log.getvalue() == ready(3) + fatal
                    assert not owner._reads
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


def test_fatal_after_normal_deferred_event_fails_readiness():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    await until(lambda: owner._reads.get("terminal") is not None
                                 and owner._reads.get("telemetry") is not None
                                 and not owner._reads["terminal"].done()
                                 and not owner._reads["telemetry"].done())
                    mark = output_mark()
                    fatal = diagnostic(3, "fatal")
                    await peer.write("telemetry", ready(3) + mark + fatal)
                    with pytest.raises(transport.EnvoyTransportError, match="fatal"):
                        await asyncio.wait_for(waiter, 2)
                    assert not owner.ready and owner.raw_offset == 0
                    assert raw_bytes(raw) == b""
                    assert log.getvalue() == ready(3) + mark + fatal
                    assert not owner._reads
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("early_count,barrier,after_count", [(4097, 0, 0), (4, 3, 0), (0, 3, 4)])
def test_overflow_and_barrier_overrun_fail_without_early_append(early_count, barrier, after_count):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    if early_count:
                        await peer.write("terminal", b"x" * early_count)
                        await until(lambda: waiter.done() or len(owner._early) == early_count)
                        assert raw_bytes(raw) == b""
                    if early_count <= 4096:
                        await peer.write("telemetry", ready(barrier))
                    if after_count:
                        await until(lambda: bool(log.getvalue()))
                        await peer.write("terminal", b"x" * after_count)
                    with pytest.raises(transport.EnvoyTransportError, match="bound|barrier"):
                        await asyncio.wait_for(waiter, 2)
                    assert raw_bytes(raw) == b"" and not owner.ready and not owner._reads
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("channel", ["terminal", "telemetry"])
@pytest.mark.parametrize("barrier", [0, 3])
def test_selected_eof_before_barrier_is_failure(channel, barrier):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    if barrier:
                        await peer.write("telemetry", ready(barrier))
                    await peer.eof(channel)
                    with pytest.raises(transport.EnvoyTransportError, match="EOF before"):
                        await asyncio.wait_for(waiter, 2)
                    assert not owner.ready and not owner._reads
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("wire", [b"{}\n", b"{}\r\n", b"\xff\n", diagnostic(severity="fatal"),
                                ready(seq=2), ready() + ready(seq=2),
                                encode({"schema": SCHEMA, "type": "operation_started", "seq": 1,
                                        "operation_id": "wrong-phase", "output_start": 0}, controller=False)])
def test_bad_handshake_and_duplicate_ready_close_pair(wire):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                try:
                    await peer.write("telemetry", wire)
                    with pytest.raises((EnvoyCodecError, transport.EnvoyTransportError)):
                        await owner.wait_ready()
                        await owner.receive()  # A coalesced duplicate follows zero-byte readiness.
                    assert not owner.ready and not owner._reads
                    assert log.getvalue() == wire
                finally:
                    await owner.close()
    asyncio.run(run())


def test_diagnostics_before_ready_are_retained_without_authorizing_capture():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                log = io.BytesIO()
                owner = await connected(peer, raw, log)
                waiter = asyncio.create_task(owner.wait_ready())
                try:
                    await peer.write("telemetry", diagnostic())
                    await until(lambda: bool(log.getvalue()))
                    assert not owner.ready and raw_bytes(raw) == b""
                    await peer.write("telemetry", ready(seq=2))
                    await asyncio.wait_for(waiter, 2)
                    await peer.write("telemetry", diagnostic(3, "fatal"))
                    with pytest.raises(transport.EnvoyTransportError, match="fatal"):
                        await owner.receive()
                    assert log.getvalue() == diagnostic() + ready(seq=2) + diagnostic(3, "fatal")
                finally:
                    await owner.close()
                    await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


def test_ready_timeout_includes_delay_before_wait_and_partial_progress(monkeypatch):
    monkeypatch.setattr(transport, "READY_SECONDS", 0.08)
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                try:
                    await asyncio.sleep(0.05)
                    await peer.write("telemetry", ready()[:-1])
                    with pytest.raises(TimeoutError):
                        await owner.wait_ready()
                    assert not owner.ready and not owner._reads
                finally:
                    await owner.close()
    asyncio.run(run())


def test_connect_resolution_cancellation_is_inside_original_budget(monkeypatch):
    monkeypatch.setattr(transport, "CONNECT_SECONDS", 0.03)
    async def run():
        loop = asyncio.get_running_loop()
        cancelled = []
        async def stalled_resolution(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        monkeypatch.setattr(loop, "getaddrinfo", stalled_resolution)
        with TemporaryFile() as raw:
            with pytest.raises(TimeoutError):
                await transport.EnvoyTransport.connect(
                    ("workload", 10001), ("workload", 10002), session_id=SESSION_ID,
                    bootstrap_written_at=loop.time(), raw_log=raw, telemetry_log=io.BytesIO())
        assert cancelled == [True]
    asyncio.run(run())


def test_second_connect_failure_disposes_first_socket(monkeypatch):
    original = transport._connect_socket
    async def run():
        async with Peer() as peer:
            calls = []
            async def connect(host, port):
                calls.append((host, port))
                if len(calls) == 2:
                    raise ConnectionRefusedError("second selected endpoint refused")
                return await original(host, port)
            monkeypatch.setattr(transport, "_connect_socket", connect)
            with TemporaryFile() as raw:
                with pytest.raises(ConnectionRefusedError):
                    await connected(peer, raw)
            await until(lambda: "terminal" in peer.channels)
            assert await asyncio.wait_for(peer.channels["terminal"][0].read(), 2) == b""
            assert peer.order == ["terminal"]
    asyncio.run(run())


def test_connect_and_ready_epochs_are_shared_and_never_reset(monkeypatch):
    original = asyncio.timeout_at
    original_relative = asyncio.timeout
    async def run():
        deadlines = []
        relative = []
        def timeout_at(deadline):
            deadlines.append(deadline)
            return original(deadline)
        def timeout(delay):
            relative.append(delay)
            return original_relative(delay)
        async with Peer() as peer:
            with TemporaryFile() as raw:
                monkeypatch.setattr(asyncio, "timeout_at", timeout_at)
                monkeypatch.setattr(asyncio, "timeout", timeout)
                loop = asyncio.get_running_loop()
                epoch = loop.time() - 1
                owner = await transport.EnvoyTransport.connect(
                    peer.addresses["terminal"], peer.addresses["telemetry"], session_id=SESSION_ID,
                    bootstrap_written_at=epoch, raw_log=raw, telemetry_log=io.BytesIO())
                try:
                    assert deadlines[0] == epoch + 10
                    assert len(deadlines) == 1  # One absolute budget for both connects.
                    assert relative == [5]  # The separate fixed hello-write budget.
                    await until(lambda: "telemetry" in peer.channels)
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    assert owner._hello_completed_at + 10 in deadlines
                finally:
                    await owner.close()
    asyncio.run(run())


class FaultSink:
    def __init__(self, file, mode):
        self.file, self.mode = file, mode
    def __getattr__(self, name):
        return getattr(self.file, name)
    def write(self, data):
        if not data:
            return self.file.write(data)
        if self.mode == "short":
            return self.file.write(data[:1])
        if self.mode == "error":
            raise OSError("injected sink failure")
        return {"zero": 0, "none": None, "bool": True, "too-many": len(data) + 1}[self.mode]


@pytest.mark.parametrize("mode", ["short", "error", "zero", "none", "bool", "too-many"])
def test_raw_log_write_contract_and_partial_artifact_preservation(mode):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, FaultSink(raw, mode))
                try:
                    await peer.write("telemetry", ready(3))
                    await peer.write("terminal", b"abc")
                    if mode == "short":
                        await owner.wait_ready()
                        assert raw_bytes(raw) == b"abc" and owner.raw_offset == 3
                    else:
                        with pytest.raises(OSError):
                            await owner.wait_ready()
                        assert not owner.ready and owner.raw_offset == 0
                finally:
                    await owner.close()
    asyncio.run(run())


def test_fsync_failure_does_not_publish_ready(monkeypatch):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                def failure(fd):
                    raise OSError("injected fsync failure")
                monkeypatch.setattr(os, "fsync", failure)
                await peer.write("telemetry", ready())
                with pytest.raises(OSError, match="fsync"):
                    await owner.wait_ready()
                assert not owner.ready and not owner._reads
    asyncio.run(run())


def test_raw_bound_rejects_crossing_chunk_without_truncating(monkeypatch):
    monkeypatch.setattr(transport, "MAX_RAW_BYTES", 4)
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                try:
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    await peer.write("terminal", b"abcd")
                    assert await owner.receive() == ("terminal", b"abcd")
                    await peer.write("terminal", b"xy")
                    with pytest.raises(transport.EnvoyTransportError, match="byte bound"):
                        await owner.receive()
                    assert raw_bytes(raw) == b"abcd" and owner.raw_offset == 4
                finally:
                    await owner.close()
    asyncio.run(run())


def test_cancelled_wait_joins_owned_reads_and_is_idempotently_closed():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                task = asyncio.create_task(owner.wait_ready())
                await until(lambda: bool(owner._reads))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await owner.close()
                assert not owner._reads and not owner.ready
                with pytest.raises(transport.EnvoyTransportError):
                    await owner.wait_ready()
    asyncio.run(run())


def test_hello_write_timeout_closes_both_selected_sockets(monkeypatch):
    monkeypatch.setattr(transport, "WRITE_SECONDS", 0.03)
    async def run():
        async with Peer() as peer:
            loop = asyncio.get_running_loop()
            original = loop.sock_sendall
            async def partial_write(selected, data):
                await original(selected, data[:3])
                await asyncio.Event().wait()
            monkeypatch.setattr(loop, "sock_sendall", partial_write)
            with TemporaryFile() as raw:
                with pytest.raises(TimeoutError):
                    await transport.EnvoyTransport.connect(
                        peer.addresses["terminal"], peer.addresses["telemetry"], session_id=SESSION_ID,
                        bootstrap_written_at=asyncio.get_running_loop().time(), raw_log=raw, telemetry_log=io.BytesIO())
            await until(lambda: len(peer.channels) == 2)
            data = await asyncio.wait_for(peer.channels["telemetry"][0].read(), 2)
            assert data == encode({"schema": SCHEMA, "type": "hello", "seq": 1, "session_id": SESSION_ID})[:3]
            assert await asyncio.wait_for(peer.channels["terminal"][0].read(), 2) == b""
    asyncio.run(run())


@pytest.mark.parametrize("epoch", [True, float("nan"), float("inf"), "now", -100])
def test_invalid_or_expired_bootstrap_does_not_open_connections(epoch):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                with pytest.raises((ValueError, TimeoutError)):
                    await transport.EnvoyTransport.connect(
                        peer.addresses["terminal"], peer.addresses["telemetry"], session_id=SESSION_ID,
                        bootstrap_written_at=epoch, raw_log=raw, telemetry_log=io.BytesIO())
                assert peer.order == []
    asyncio.run(run())


@pytest.mark.parametrize("address", [("", 1), ("workload", True), ("workload", 0), ("workload", 65536),
                                    ("work\0load", 10001), ["workload", 10001]])
def test_invalid_coordinates_are_rejected_before_connect(address):
    async def run():
        with TemporaryFile() as raw:
            with pytest.raises(ValueError, match="coordinate"):
                await transport.EnvoyTransport.connect(
                    address, ("workload", 10002), session_id=SESSION_ID,
                    bootstrap_written_at=asyncio.get_running_loop().time(), raw_log=raw, telemetry_log=io.BytesIO())
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["not-empty", "same-sink", "not-regular", "text"])
def test_invalid_raw_log_disposes_connected_pair(fault):
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                sink = raw
                if fault == "not-empty":
                    raw.write(b"existing")
                elif fault == "not-regular":
                    sink = io.BytesIO()
                elif fault == "text":
                    sink = io.TextIOWrapper(raw)
                telemetry = sink if fault == "same-sink" else io.BytesIO()
                with pytest.raises((ValueError, OSError, TypeError)):
                    await transport.EnvoyTransport.connect(
                        peer.addresses["terminal"], peer.addresses["telemetry"], session_id=SESSION_ID,
                        bootstrap_written_at=asyncio.get_running_loop().time(), raw_log=sink, telemetry_log=telemetry)
                await until(lambda: len(peer.channels) == 2)
                assert await asyncio.wait_for(peer.channels["telemetry"][0].read(), 2) == b""
                assert await asyncio.wait_for(peer.channels["terminal"][0].read(), 2) == b""
                if fault == "text":
                    sink.detach()
    asyncio.run(run())


def test_telemetry_retention_failure_closes_without_raw_output():
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw, FaultSink(io.BytesIO(), "error"))
                await peer.write("telemetry", ready())
                with pytest.raises(OSError, match="sink failure"):
                    await owner.wait_ready()
                assert raw_bytes(raw) == b"" and not owner.ready and not owner._reads
    asyncio.run(run())


def test_unterminated_telemetry_eof_and_bad_later_sequence_fail():
    async def run(truncated):
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                try:
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    await peer.write("telemetry", diagnostic(2)[:-1] if truncated else diagnostic(1))
                    if truncated:
                        while True:
                            kind, _ = await owner.receive()
                            if kind == "telemetry-partial":
                                break
                        await peer.eof("telemetry")
                    with pytest.raises(EnvoyCodecError):
                        await owner.receive()
                    assert not owner.ready and not owner._reads
                finally:
                    await owner.close()
    asyncio.run(run(True))
    asyncio.run(run(False))


def test_no_library_prefetch_before_wait_ready(monkeypatch):
    """The byte bound includes library buffers,not only the explicit prefix."""
    original = asyncio.StreamReader.feed_data
    prefetched = []
    def feed(reader, data):
        if data and not data.strip(b"x"):
            prefetched.append(len(data))
        return original(reader, data)
    monkeypatch.setattr(asyncio.StreamReader, "feed_data", feed)
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                try:
                    await peer.write("telemetry", ready(4096))
                    await peer.write("terminal", b"x" * 8192)
                    # Give registered library readers the chance to run. A
                    # pull-only connection must keep these bytes in the kernel.
                    for _ in range(10):
                        await asyncio.sleep(0)
                    assert sum(prefetched) == 0
                    assert not owner._reads and not owner._early
                    assert raw_bytes(raw) == b"" and not owner.ready
                    with pytest.raises(transport.EnvoyTransportError, match="bound"):
                        await owner.wait_ready()
                    assert raw_bytes(raw) == b"" and owner.raw_offset == 0
                finally:
                    await owner.close()
    asyncio.run(run())


@pytest.mark.parametrize("duplicate_descriptor", [False, True])
def test_same_file_sinks_are_rejected_before_hello(duplicate_descriptor):
    async def run():
        async with Peer() as peer:
            with NamedTemporaryFile() as raw:
                other = (os.fdopen(os.dup(raw.fileno()), "r+b") if duplicate_descriptor
                         else open(raw.name, "r+b"))
                with other:
                    with pytest.raises(ValueError, match="same file"):
                        await transport.EnvoyTransport.connect(
                            peer.addresses["terminal"], peer.addresses["telemetry"], session_id=SESSION_ID,
                            bootstrap_written_at=asyncio.get_running_loop().time(), raw_log=raw, telemetry_log=other)
                await until(lambda: len(peer.channels) == 2)
                assert await asyncio.wait_for(peer.channels["telemetry"][0].read(), 2) == b""
                assert raw_bytes(raw) == b""
    asyncio.run(run())


def test_hidden_sink_alias_cannot_publish_false_raw_offset():
    class Alias:
        def __init__(self, file):
            self.file = file
        def write(self, data):
            return self.file.write(data)
        def flush(self):
            return self.file.flush()
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw, Alias(raw))
                await peer.write("telemetry", ready())
                with pytest.raises(transport.EnvoyTransportError, match="outside its serial owner"):
                    await owner.wait_ready()
                assert not owner.ready and owner.raw_offset == 0
                assert raw_bytes(raw) == ready()  # Retain failed evidence;never claim it as valid output.
    asyncio.run(run())


@pytest.mark.parametrize("change", ["position", "size"])
def test_unexpected_raw_write_or_seek_fails_before_offset_acknowledgement(change):
    async def run():
        async with Peer() as peer:
            with NamedTemporaryFile() as raw:
                owner = await connected(peer, raw)
                try:
                    await peer.write("telemetry", ready())
                    await owner.wait_ready()
                    if change == "position":
                        raw.seek(1)
                    else:
                        with open(raw.name, "r+b") as other:
                            other.write(b"foreign")
                    await peer.write("terminal", b"abc")
                    with pytest.raises(transport.EnvoyTransportError, match="outside its serial owner"):
                        await owner.receive()
                    assert not owner.ready and owner.raw_offset == 0
                    assert raw_bytes(raw) == (b"foreign" if change == "size" else b"")
                finally:
                    await owner.close()
    asyncio.run(run())


def test_ready_deadline_covers_synchronous_raw_durability(monkeypatch):
    monkeypatch.setattr(transport, "READY_SECONDS", 0.03)
    original = os.fsync
    def slow_flush(fd):
        original(fd)
        time.sleep(0.04)
    async def run():
        async with Peer() as peer:
            with TemporaryFile() as raw:
                owner = await connected(peer, raw)
                monkeypatch.setattr(os, "fsync", slow_flush)
                await peer.write("telemetry", ready())
                with pytest.raises(TimeoutError):
                    await owner.wait_ready()
                assert not owner.ready and not owner._reads
    asyncio.run(run())
