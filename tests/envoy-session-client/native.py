#!/usr/bin/env python3
"""Actual Python client against isolated production Envoy/Awsh/Bash owners.

Only the Reploy prerequisite observations are simulated. C8.2 owns that public
invocation. This controller starts the dedicated Go Envoy child; it is never a
workload descendant. No Go signal, census, inspection or cleanup is replaced.
"""

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import uuid

from omegaflow.controller_lifecycle import OperationState
from omegaflow.controller_session import ControllerSession
from omegaflow.envoy_client import EnvoyClientError
from omegaflow.envoy_controller import EnvoyController
from omegaflow.envoy_transport import EnvoyTransport
from omegaflow.reploy_codecs import encode_event


SCENARIOS = ("ordinary-state", "source-rejection", "inspection", "gate-input", "cancel",
             "finalize", "finalize-inspection", "split", "input", "reserved-input", "resize", "raw-barrier",
             "inspection-crossed-cancel", "finalize-inspection-crossed-cancel",
             "crossed-cancel", "crossed-finalize", "crossed-cancel-continued",
             "crossed-finalize-continued", "crossed-cancel-interrupted",
             "crossed-finalize-interrupted", "entry-idle-cancel", "entry-idle-finalize",
             "entry-idle-discard", "completion-idle-discard", "entry-selected-eof", "entry-signal")


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def public_prerequisite(events, errors):
    session = ControllerSession(event_sink=events, stderr_sink=errors, stderr_tail_limit=4096)
    session.start()
    fixture = Path("/c3-tests/fixtures/reploy-codecs/client-v1-events.jsonl")
    values = [json.loads(line) for line in fixture.read_bytes().splitlines()]
    opened = deepcopy(next(v for v in values if v["type"] == "opened"))
    broker = deepcopy(next(v for v in values if v["type"] == "broker-ready"))
    session.on_event(encode_event(broker), now=1)
    session.attachment_connected(now=1)
    session.on_event(encode_event(opened), now=1)
    ready = deepcopy(next(v for v in values if v["type"] == "ready"))
    session.on_event(encode_event(ready), now=1)
    return session


class Client:
    def __init__(self, owner, raw):
        self.owner, self.raw = owner, raw
        self.events = []

    async def observe(self):
        kind, value = await self.owner.receive()
        if kind == "event":
            self.events.append(value)
        return kind, value

    async def execute(self, source, *, inspections=(), shape="pty", inputs=(), gate=None, control=None):
        operation = "operation-" + str(len(self.events) + 1)
        payload = {"operation_id": operation, "source": "# C3_SOURCE_NEVER_REDISPLAY\n" + source,
                   "execution_shape": shape, "timing": "presentation" if shape == "split" else "realtime",
                   "publication": "real", "observation": "exclusive", "inspections": list(inspections)}
        await self.owner.request("execute", payload, inputs=inputs,
                                 deadline=asyncio.get_running_loop().time() + 15)
        with_input = bool(inputs)
        if with_input:
            try:
                await self.owner.write_input(operation)
            except EnvoyClientError:
                pass
            else:
                raise AssertionError("authored input was admitted before public operation_started")
        input_sent = False
        async with asyncio.timeout(20):
            while not self.owner.result_available:
                kind, value = await self.observe()
                if kind != "event":
                    continue
                if value["type"] == "operation_started" and with_input and gate is None:
                    for _ in inputs:
                        await self.owner.write_input(operation)
                    input_sent = True
                if value["type"] == "operation_ready":
                    assert value["gate_id"] == gate
                    if control:
                        await self.owner.request(control, {"operation_id": operation, "reason": "native"})
                    else:
                        for _ in inputs:
                            await self.owner.write_input(operation)
                        input_sent = bool(inputs)
                        while not self.owner.client.output_available(value["output_through"]):
                            await self.observe()
                        await self.owner.request("continue", {"operation_id": operation, "gate_id": gate})
            result = self.owner.take_result()
        assert not with_input or input_sent or control
        data = self.raw.read_bytes()[result["output_start"]:result["output_through"]]
        assert b"C3_SOURCE_NEVER_REDISPLAY" not in data
        assert self.owner.session.failure is None
        return result, data


def inspection_plan():
    return [{"kind": "file_exists", "path": "$C3_FILE"},
            {"kind": "produces", "path": "$C3_FILE", "producer_id": "producer", "output_id": "file"},
            {"kind": "produces", "path": "c3-directory", "producer_id": "producer", "output_id": "tree"}]


def inspection_source(base, *, gated=False):
    source = (f"/usr/bin/mkdir {base}; builtin cd {base}; "
              "export C3_FILE=c3-artifact; builtin printf payload >c3-artifact; "
              "/usr/bin/mkdir c3-directory; builtin printf tree >c3-directory/entry; "
              "builtin printf 'BEFORE_FINALIZE\\n'; ")
    return source + ("awsh gate inspection; builtin printf 'UNREACHABLE\\n'" if gated
                     else "builtin printf 'INSPECTED\\n'")


def assert_inspection_results(result, base):
    results = result["inspection_results"]
    assert [r["inspection_id"] for r in results] == ["inspection-1", "inspection-2", "inspection-3"]
    assert results[0]["kind"] == "file_exists"
    assert results[0]["resolved_path"] == base + "/c3-artifact"
    assert results[0]["path_kind"] == "file"
    assert results[1]["kind"] == "produces"
    assert results[1]["resolved_path"] == base + "/c3-artifact"
    assert results[1]["path_kind"] == "file"
    assert results[1]["producer_id"] == "producer" and results[1]["output_id"] == "file"
    assert results[1]["sha256"] == hashlib.sha256(b"payload").hexdigest()
    assert "digest_algorithm" not in results[1]
    assert results[2]["kind"] == "produces"
    assert results[2]["resolved_path"] == base + "/c3-directory"
    assert results[2]["path_kind"] == "directory"
    assert results[2]["producer_id"] == "producer" and results[2]["output_id"] == "tree"
    assert results[2]["digest_algorithm"] == "directory-v2"
    digest = hashlib.sha256()
    digest.update(b"directory-v2")
    digest.update(b"f")
    digest.update(hashlib.sha256(b"entry").digest())
    digest.update(hashlib.sha256(b"tree").digest())
    assert results[2]["sha256"] == digest.hexdigest()


async def inspection_result_crossing(client, *, finalized):
    operation = "inspection-crossed-finalized" if finalized else "inspection-crossed-completed"
    base = "/tmp/c3-crossed-" + ("finalized" if finalized else "completed")
    expected_type = "operation_finalized" if finalized else "operation_completed"
    # Hold only the authored terminal bytes after transport readiness. The
    # result frame can therefore cross cancel while its raw evidence remains
    # behind the existing transport barrier.
    client.proxy.hold = True
    await client.owner.request(
        "execute", {"operation_id": operation, "source": "# C3_SOURCE_NEVER_REDISPLAY\n" +
                     inspection_source(base, gated=finalized), "execution_shape": "pty",
                     "timing": "realtime", "publication": "real", "observation": "exclusive",
                     "inspections": inspection_plan()}, deadline=asyncio.get_running_loop().time() + 15)
    async with asyncio.timeout(20):
        while True:
            kind, value = await client.observe()
            if kind == "event" and value["type"] == "operation_started":
                assert value["operation_id"] == operation
                break
        if finalized:
            while True:
                kind, value = await client.observe()
                if kind == "event" and value["type"] == "operation_ready":
                    assert value["operation_id"] == operation and value["gate_id"] == "inspection"
                    break
            await client.owner.request("finalize", {"operation_id": operation, "reason": "native"})
            assert client.owner.session.operation.state is OperationState.FINALIZING
        async def observe_result():
            while True:
                kind, value = await client.observe()
                if kind == "event" and value["type"] == expected_type:
                    return kind, value

        observer = asyncio.create_task(observe_result())
        await asyncio.wait_for(client.proxy.holding_result.wait(), 10)
        await client.owner.request("cancel", {"operation_id": operation, "reason": "native"})
        assert client.owner.session.operation.state is OperationState.CANCELLING
        client.proxy.release_result.set()
        kind, value = await observer
        assert kind == "event" and value["type"] == expected_type
        assert value["operation_id"] == operation
        assert not client.owner.result_available
        assert client.owner.client.transport.raw_offset < value["output_through"]
        assert not any(event["type"] == "operation_cancelled" for event in client.events)
        client.proxy.release_raw.set()
        while not client.owner.result_available:
            await client.observe()
    result = client.owner.take_result()
    assert result["type"] == expected_type and result["operation_id"] == operation
    assert_inspection_results(result, base)
    data = client.raw.read_bytes()[result["output_start"]:result["output_through"]]
    assert b"BEFORE_FINALIZE\r\n" in data
    assert client.owner.session.failure is None
    next_result, _ = await client.execute("builtin true")
    assert next_result["status"] == 0


async def scenario(client, name):
    if name == "entry-idle-discard":
        await client.owner.client.transport.write_terminal(b"IDLE_BYTES_MUST_NOT_RUN")
        result, data = await client.execute("builtin printf 'AFTER_IDLE\\n'")
        assert result["status"] == 0 and b"AFTER_IDLE\r\n" in data
        assert b"IDLE_BYTES_MUST_NOT_RUN" not in client.raw.read_bytes()
    elif name == "completion-idle-discard":
        result, _ = await client.execute("/usr/bin/sleep 0.1; builtin true",
                                         inputs=[b"STALE_COMPLETION_INPUT"])
        assert result["status"] == 0
        # Let the existing idle owner consume an event queued at completion
        # before the next execute request can consume it as stale input.
        await asyncio.sleep(0.1)
        result, data = await client.execute("builtin printf 'AFTER_COMPLETION\\n'")
        assert result["status"] == 0 and b"AFTER_COMPLETION\r\n" in data
        assert b"STALE_COMPLETION_INPUT" not in data
    elif name == "ordinary-state":
        result, data = await client.execute("export C3_VALUE=kept; builtin cd /tmp; builtin printf 'ONE\\n'")
        assert result["status"] == 0 and result["cwd"] == "/tmp" and b"ONE\r\n" in data
        result, _ = await client.execute("builtin false")
        assert result["status"] == 1
        result, data = await client.execute("builtin printf '%s:%s\\n' \"$C3_VALUE\" \"$?\"")
        assert result["status"] == 0 and b"kept:1\r\n" in data
    elif name == "source-rejection":
        result, data = await client.execute("if then")
        assert result["type"] == "operation_failed" and result["code"] == "source-syntax"
        assert not data and result["output_start"] == result["output_through"]
        result, data = await client.execute("builtin printf 'AFTER_REJECTION\\n'")
        assert result["status"] == 0 and b"AFTER_REJECTION" in data
    elif name in {"inspection", "finalize-inspection"}:
        plan = inspection_plan()
        base = "/tmp/c3-" + name
        source = inspection_source(base, gated=name == "finalize-inspection")
        result, _ = await client.execute(source, inspections=plan,
                                        gate="inspection" if name == "finalize-inspection" else None,
                                        control="finalize" if name == "finalize-inspection" else None)
        assert result["type"] == ("operation_finalized" if name == "finalize-inspection" else "operation_completed")
        assert_inspection_results(result, base)
    elif name == "inspection-crossed-cancel":
        await inspection_result_crossing(client, finalized=False)
    elif name == "finalize-inspection-crossed-cancel":
        await inspection_result_crossing(client, finalized=True)
    elif name == "gate-input":
        result, data = await client.execute("awsh gate checkpoint; builtin read -r token; builtin printf 'TOKEN:%s\\n' \"$token\"",
                                            inputs=[b"hello\n"], gate="checkpoint")
        assert result["status"] == 0 and b"TOKEN:hello\r\n" in data
        assert client.owner.client.input_through == 6
        assert any(e["type"] == "operation_continued" for e in client.events)
    elif name in {"cancel", "finalize"}:
        result, data = await client.execute("awsh gate checkpoint; builtin printf 'UNREACHABLE\\n'",
                                            gate="checkpoint", control=name)
        assert result["type"] == ("operation_cancelled" if name == "cancel" else "operation_finalized")
        assert result["reason"] == "native" and b"UNREACHABLE" not in data
    elif name == "split":
        result, data = await client.execute("builtin printf 'OUT\\n'; builtin printf 'ERR\\n' >&2", shape="split")
        assert result["status"] == 0 and b"OUT\n" in data and b"ERR\n" in data
        assert {e["stream"] for e in client.events if e["type"] == "output_mark"} >= {"stdout", "stderr"}
    elif name == "input":
        result, data = await client.execute("builtin read -r token; builtin printf 'INPUT:%s\\n' \"$token\"",
                                            inputs=[b"one", b"two\n"])
        assert result["status"] == 0 and b"INPUT:onetwo\r\n" in data
        assert client.owner.client.input_through == 7
    elif name == "reserved-input":
        for suffix in (b"\x01", b"\x02"):
            payload = {"operation_id": "forbidden", "source": "builtin printf FORBIDDEN",
                       "execution_shape": "pty", "timing": "realtime", "publication": "real",
                       "observation": "shared", "inspections": []}
            try:
                await client.owner.request("execute", payload, inputs=[b"safe\x18", b"", suffix])
            except EnvoyClientError:
                pass
            else:
                raise AssertionError("reserved adjacent input was admitted")
            assert client.owner.session.operation.state is OperationState.IDLE
        result, data = await client.execute("builtin printf 'ADMITTED\\n'")
        assert result["status"] == 0 and b"FORBIDDEN" not in client.raw.read_bytes()
    elif name == "resize":
        await client.owner.request("resize", {"columns": 91, "rows": 31})
        async with asyncio.timeout(3):
            while True:
                kind, event = await client.observe()
                if kind == "event" and event["type"] == "resize_applied":
                    assert (event["columns"], event["rows"]) == (91, 31)
                    break
        result, _ = await client.execute("builtin true")
        assert result["status"] == 0
    elif name == "raw-barrier":
        # The opaque TCP delay below holds actual terminal bytes only. Neither
        # wire frames nor the production Python transport are replaced.
        client.proxy.hold = True
        operation = "lagged"
        await client.owner.request("execute", {"operation_id": operation,
            "source": "awsh gate raw; builtin printf RAW_BARRIER",
            "execution_shape": "pty", "timing": "realtime", "publication": "real",
            "observation": "exclusive", "inspections": []})
        async with asyncio.timeout(5):
            gate = None
            while True:
                kind, value = await client.observe()
                if kind == "event" and value["type"] == "operation_ready":
                    gate = value
                    assert value["operation_id"] == operation and value["gate_id"] == "raw"
                    assert client.owner.session.operation.state is OperationState.GATED
                    sequence = client.owner.client._sequence
                    input_through = client.owner.client.input_through
                    with_input_rejected = False
                    try:
                        await client.owner.request("continue", {"operation_id": operation,
                                                                  "gate_id": value["gate_id"]})
                    except EnvoyClientError:
                        with_input_rejected = True
                    assert with_input_rejected
                    assert client.owner.session.operation.state is OperationState.GATED
                    assert client.owner.client._sequence == sequence
                    assert client.owner.client.input_through == input_through
                    client.proxy.release_raw.set()
                    while not client.owner.client.output_available(value["output_through"]):
                        await client.observe()
                    await client.owner.request("continue", {"operation_id": operation,
                                                              "gate_id": value["gate_id"]})
                    break
            continued = False
            while not client.owner.result_available:
                kind, value = await client.observe()
                if kind == "event" and value["type"] == "operation_continued":
                    assert value["operation_id"] == operation and value["gate_id"] == gate["gate_id"]
                    continued = True
            assert continued
        result = client.owner.take_result()
        data = client.raw.read_bytes()[result["output_start"]:result["output_through"]]
        # The raw PTY range includes Readline's bracketed-paste enable bytes
        # at ordinary return. Check every byte without treating that control
        # sequence as authored output or stripping it in the controller.
        assert data.removesuffix(b"\x1b[?2004h") == b"RAW_BARRIER"
    elif name.startswith("crossed-"):
        control, outcome = name.removeprefix("crossed-").split("-", 1) if name.count("-") > 1 \
            else (name.removeprefix("crossed-"), "ready")
        event_type = {"ready": "operation_ready", "continued": "operation_continued",
                      "interrupted": "operation_gate_interrupted"}[outcome]
        gate = "held"
        operation = "crossed"
        interrupted = outcome == "interrupted"
        source = "awsh gate held" if interrupted else "awsh gate held; builtin read -r parked; builtin printf UNREACHABLE"
        await client.owner.request("execute", {"operation_id": "crossed", "source": source,
            "execution_shape": "pty", "timing": "realtime", "publication": "real",
            "observation": "exclusive", "inspections": []},
            inputs=[b"\x03", b"forbidden"])
        if interrupted:
            # Keep the original PTY evidence behind the result frame. The
            # opaque proxy still forwards one original stream in order.
            client.proxy.buffer_gate = True
        async with asyncio.timeout(8):
            while True:
                kind, value = await client.observe()
                if kind == "event" and value["type"] == "operation_started":
                    assert value["operation_id"] == operation
                    break
            selected = OperationState.CANCELLING if control == "cancel" else OperationState.FINALIZING
            observed_crossing = None
            if outcome == "ready":
                await client.proxy.holding_gate.wait()
                assert client.owner.session.operation.state is OperationState.RUNNING
                await client.owner.request(control, {"operation_id": operation, "reason": "native"})
                assert client.owner.session.operation.state is selected
            else:
                while True:
                    kind, value = await client.observe()
                    if kind == "event" and value["type"] == "operation_ready":
                        assert value["operation_id"] == operation and value["gate_id"] == gate
                        break
                if outcome == "continued":
                    await client.owner.request("continue", {"operation_id": operation, "gate_id": gate})
                    assert client.owner.session.operation.state is OperationState.CONTINUING
                    await client.proxy.holding_gate.wait()
                    assert client.owner.session.operation.state is OperationState.CONTINUING
                    await client.owner.request(control, {"operation_id": operation, "reason": "native"})
                else:
                    if interrupted:
                        client.proxy.hold = True
                    await client.owner.write_input(operation)
                    await client.proxy.holding_gate.wait()
                    if interrupted:
                        await client.proxy.holding_terminal.wait()
                        committed = deepcopy(client.proxy.committed_result)
                        assert committed["type"] == "operation_completed"
                    await client.owner.request(control, {"operation_id": operation, "reason": "native"})
                assert client.owner.session.operation.state is selected
            with_input_rejected = False
            try:
                await client.owner.write_input(operation)
            except EnvoyClientError as error:
                assert str(error) == "input requires its matching public operation_started"
                with_input_rejected = True
            assert with_input_rejected
            client.proxy.release.set()
            committed_event = None
            while not client.owner.result_available:
                kind, value = await client.observe()
                if kind == "event" and value["type"] == event_type:
                    assert value["operation_id"] == operation and value["gate_id"] == gate
                    assert client.owner.session.operation.state is selected
                    if outcome != "ready":
                        assert client.owner.session.operation.gate_id is None
                    observed_crossing = value["type"]
                if interrupted and kind == "event" and value["type"] in {
                        "operation_completed", "operation_cancelled", "operation_finalized", "operation_failed"}:
                    committed_event = value
                    assert value == committed
                    assert not client.owner.result_available
                    client.proxy.release_raw.set()
            result = client.owner.take_result()
        assert observed_crossing == event_type
        assert result["operation_id"] == operation
        if interrupted:
            assert committed_event == committed == result
            assert result["type"] == "operation_completed"
            next_result, _ = await client.execute("builtin true")
            assert next_result["status"] == 0
        else:
            assert result["type"] == ("operation_cancelled" if control == "cancel" else "operation_finalized")
            assert result["reason"] == "native"
    else:
        raise ValueError("unknown native scenario")


class ChannelDelay:
    """Test-only forwarding; hold one raw chunk or one original gate frame."""

    def __init__(self):
        self.hold = False
        self.release = asyncio.Event()
        self.release_raw = asyncio.Event()
        self.hold_result = None
        self.holding_result = asyncio.Event()
        self.release_result = asyncio.Event()
        self.hold_gate = None
        self.holding_gate = asyncio.Event()
        self.buffer_gate = False
        self.buffering_gate = False
        self.buffered_telemetry = []
        self.holding_terminal = asyncio.Event()
        self.committed_result = None
        self.servers = []
        self.tasks = set()

    async def start(self, terminal, telemetry):
        addresses = []
        for actual, delayed in [(terminal, True), (telemetry, False)]:
            async def connection(reader, writer, actual=actual, delayed=delayed):
                task = asyncio.current_task()
                self.tasks.add(task)
                upstream = None
                pumps = []
                try:
                    incoming, upstream = await asyncio.open_connection(*actual, limit=262144)
                    async def forward(source, destination, pause, telemetry=False):
                        while data := await (source.readline() if telemetry else source.read(65536)):
                            if pause and self.hold:
                                await self.release_raw.wait()
                            event = json.loads(data) if telemetry else None
                            if telemetry and self.buffering_gate:
                                self.buffered_telemetry.append(data)
                                if event["type"] in {"operation_completed", "operation_cancelled",
                                                      "operation_finalized", "operation_failed"}:
                                    self.committed_result = deepcopy(event)
                                    self.holding_terminal.set()
                                    await self.release.wait()
                                    for buffered in self.buffered_telemetry:
                                        destination.write(buffered)
                                        await destination.drain()
                                    self.buffered_telemetry.clear()
                                    self.buffering_gate = False
                                continue
                            if telemetry and self.hold_gate and event["type"] == self.hold_gate:
                                self.holding_gate.set()
                                if self.buffer_gate:
                                    self.buffering_gate = True
                                    self.buffered_telemetry.append(data)
                                    continue
                                await self.release.wait()
                            if telemetry and self.hold_result and json.loads(data)["type"] == self.hold_result:
                                self.holding_result.set()
                                await self.release_result.wait()
                            destination.write(data)
                            await destination.drain()
                        destination.write_eof()
                    pumps = [asyncio.create_task(forward(reader, upstream, False)),
                             asyncio.create_task(forward(incoming, writer, delayed, telemetry=not delayed))]
                    await asyncio.gather(*pumps)
                finally:
                    for pump in pumps:
                        pump.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)
                    for stream in [writer, upstream]:
                        if stream is not None:
                            stream.close()
                            await stream.wait_closed()
                    self.tasks.discard(task)
            server = await asyncio.start_server(connection, "127.0.0.1", 0)
            self.servers.append(server)
            addresses.append(server.sockets[0].getsockname()[:2])
        return addresses

    async def close(self):
        self.release.set()
        self.release_raw.set()
        self.release_result.set()
        for server in self.servers:
            server.close()
            await server.wait_closed()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def wait_listeners(proc, ports):
    # Observe the child's actual listening sockets without consuming either
    # single-admission channel. This is test-only bootstrap observation.
    while proc.returncode is None:
        inodes = set()
        for path in Path(f"/proc/{proc.pid}/fd").iterdir():
            try:
                link = os.readlink(path)
            except FileNotFoundError:
                continue
            if link.startswith("socket:["):
                inodes.add(link[8:-1])
        listeners = {int(fields[1].rsplit(":", 1)[1], 16)
                     for line in Path(f"/proc/{proc.pid}/net/tcp").read_text().splitlines()[1:]
                     if (fields := line.split())[3] == "0A" and fields[9] in inodes}
        if ports <= listeners:
            return
        await asyncio.sleep(.01)
    raise AssertionError("production Envoy exited before binding both listeners")


async def one(envoy, output, name):
    folder = output / name
    folder.mkdir()
    reserved = [socket.socket(), socket.socket()]
    try:
        for sock in reserved:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in reserved]
    finally:
        for sock in reserved:
            sock.close()
    coordinates = {"terminal": f"127.0.0.1:{ports[0]}", "telemetry": f"127.0.0.1:{ports[1]}",
                   "session_id": uuid.uuid4().hex}
    bootstrap = asyncio.get_running_loop().time()
    proc = await asyncio.create_subprocess_exec(
        envoy, "--session-id", coordinates["session_id"], "--terminal-listen", coordinates["terminal"],
        "--telemetry-listen", coordinates["telemetry"], "--columns", "80", "--rows", "24",
        env=os.environ,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    transcript = bytearray()
    adapter = None
    proxy = None
    reader_task = None
    async def retain_fixture_output():
        while chunk := await proc.stdout.read(65536):
            transcript.extend(chunk)
    try:
        reader_task = asyncio.create_task(retain_fixture_output())
        async with asyncio.timeout_at(bootstrap + 5):
            await wait_listeners(proc, set(ports))
        with (folder / "raw.bin").open("w+b") as raw, (folder / "telemetry.jsonl").open("wb") as telemetry, \
                (folder / "reploy-prerequisite.jsonl").open("wb") as events, (folder / "client-stderr.bin").open("wb") as errors:
            public = public_prerequisite(events, errors)
            address = lambda value: (value.rsplit(":", 1)[0], int(value.rsplit(":", 1)[1]))
            terminal, telemetry_address = address(coordinates["terminal"]), address(coordinates["telemetry"])
            if name == "raw-barrier" or name.startswith("crossed-") \
                    or name in {"inspection-crossed-cancel", "finalize-inspection-crossed-cancel"}:
                proxy = ChannelDelay()
                if name in {"inspection-crossed-cancel", "finalize-inspection-crossed-cancel"}:
                    proxy.hold_result = ("operation_finalized" if name == "finalize-inspection-crossed-cancel"
                                         else "operation_completed")
                if name.startswith("crossed-"):
                    proxy.hold_gate = ("operation_ready" if name in {"crossed-cancel", "crossed-finalize"}
                                       else "operation_continued" if name.endswith("-continued")
                                       else "operation_gate_interrupted")
                terminal, telemetry_address = await proxy.start(terminal, telemetry_address)
            owner = await EnvoyTransport.connect(terminal, telemetry_address,
                                                  session_id=coordinates["session_id"],
                                                  bootstrap_written_at=bootstrap,
                                                  raw_log=raw, telemetry_log=telemetry)
            await owner.wait_ready()
            adapter = EnvoyController(owner, public)
            client = Client(adapter, folder / "raw.bin")
            client.proxy = proxy
            if name.startswith("entry-") and name != "entry-idle-discard":
                # Exercise the production assembly's fatal return and joined
                # disposal, rather than substituting a cleanup or signal peer.
                shell = owner.ready_event["shell_pid"]
                # Go can fork from a non-leader OS thread. Linux reports the
                # child on that thread,so inspect every task's children file.
                children = {pid for task in Path(f"/proc/{proc.pid}/task").iterdir()
                            for pid in (task / "children").read_text().split()}
                assert children and Path(f"/proc/{shell}").exists()
                if name in {"entry-idle-cancel", "entry-idle-finalize"}:
                    frame = {"schema": "omegaflow-envoy-telemetry-v1", "type": name.removeprefix("entry-idle-"),
                             "seq": 2, "operation_id": "never-started", "reason": "entry-fault"}
                    await owner.write_control(json.dumps(frame).encode() + b"\n")
                elif name == "entry-selected-eof":
                    await owner.close()
                else:
                    proc.send_signal(signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), 6)
                await reader_task
                assert proc.returncode == 2, transcript.decode(errors="replace")
                assert not Path(f"/proc/{shell}").exists()
                assert all(not Path(f"/proc/{pid}").exists() for pid in children)
                expected = {"entry-idle-cancel": b"idle cancel names another operation",
                            "entry-idle-finalize": b"idle finalize names another operation",
                            "entry-signal": b"context canceled"}.get(name)
                if expected is not None:
                    assert expected in transcript, transcript.decode(errors="replace")
                save(folder / "result.json", {"passed": True, "scenario": name, "envoy_reaped": True,
                     "envoy_exit": proc.returncode, "shell_reaped": True, "awsh_reaped": True,
                     "entrypoint": "./cmd/omegaflow-envoy", "actual_actors": ["production Envoy", "Awsh", "qualified Bash"],
                     "Reploy_prerequisite": "simulated;actual public invocation remains C8.2"})
                print("C4_ENTRY_PROVED " + name, flush=True)
                return
            await scenario(client, name)
            await adapter.request("shutdown", {"reason": "native-done"})
            async with asyncio.timeout(6):
                while not adapter.closed:
                    await client.observe()
            assert public.failure is None and not public.closed and public.result is None
            save(folder / "observations.json", {"scenario": name, "events": client.events,
                 "raw_bytes": owner.raw_offset, "Envoy_closed": True, "Reploy_result_claimed": False})
        await asyncio.wait_for(proc.wait(), 6)
        await reader_task
        assert proc.returncode == 0, transcript.decode(errors="replace")
        save(folder / "result.json", {"passed": True, "scenario": name, "envoy_reaped": True,
             "envoy_exit": proc.returncode, "entrypoint": "./cmd/omegaflow-envoy",
             "actual_actors": ["Python controller", "production Envoy command", "Awsh", "qualified Bash"],
             "Reploy_prerequisite": "simulated;actual public invocation remains C8.2"})
        print("C3_NATIVE_PROVED " + name, flush=True)
    finally:
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 8)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        if reader_task is not None:
            await reader_task
        if adapter is not None:
            await adapter.close()
        if proxy is not None:
            await proxy.close()
        (folder / "envoy.log").write_bytes(transcript)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envoy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in SCENARIOS:
        await one(args.envoy, args.output, name)


if __name__ == "__main__":
    asyncio.run(main())
