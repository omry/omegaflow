"""C3.2 uses real TCP peers; actual Envoy/Awsh assembly remains C3.3."""

import asyncio
import socket
from contextlib import asynccontextmanager
from copy import deepcopy
from tempfile import TemporaryFile

import pytest

from omegaflow import envoy_client, envoy_transport
from omegaflow.controller_lifecycle import OperationState
from omegaflow.envoy_client import EnvoyClient, EnvoyClientError
from omegaflow.envoy_codecs import MAX_FRAME_BYTES, MAX_INTEGER, MAX_SOURCE_BYTES, SCHEMA, EnvoyCodecError, decode, encode
from test_controller_session import Peer as ControllerPeer
from test_envoy_transport import Peer, connected, raw_bytes, ready


EXECUTE = {"operation_id": "op-1", "source": "printf 'hello\\n'", "execution_shape": "pty",
           "timing": "realtime", "publication": "real", "observation": "shared", "inspections": []}


class Session:
    def __init__(self, peer, owner, raw):
        self.peer, self.owner, self.raw = peer, owner, raw
        peer.channels["telemetry"][0]._limit = MAX_FRAME_BYTES  # Test peer must admit maximum legal JSONL.
        self.operation = ControllerPeer().active().controller.operation
        self.client = EnvoyClient(owner, self.operation)
        self.sequence = 2

    async def request(self, kind="execute", payload=None, **kwargs):
        payload = deepcopy(EXECUTE) if payload is None else payload
        await self.client.request(kind, payload, **kwargs)
        return decode(await self.peer.channels["telemetry"][0].readuntil(b"\n"), controller=True)

    async def event(self, kind, **payload):
        value = {"schema": SCHEMA, "type": kind, "seq": self.sequence, **payload}
        self.sequence += 1
        await self.peer.write("telemetry", encode(value, controller=False))
        async with asyncio.timeout(2):
            while True:
                result_kind, result = await self.client.receive()
                if result_kind == "event":
                    assert result == value
                    return result

    async def mark(self, offset=0, elapsed_us=1, stream="pty"):
        return await self.event("output_mark", offset=offset, elapsed_us=elapsed_us, stream=stream)

    async def start(self, offset=0):
        await self.mark(offset)
        await self.event("operation_started", operation_id="op-1", output_start=offset)
        self.operation.started("op-1")

    async def complete(self, start=0, end=0):
        await self.mark(end, 5)
        await self.event("operation_completed", operation_id="op-1", status=0, cwd="/work",
                         output_start=start, output_through=end, inspection_results=[])
        self.operation.terminal_result("op-1", "completed")


@asynccontextmanager
async def session():
    async with Peer() as peer:
        with TemporaryFile() as raw:
            owner = await connected(peer, raw)
            await peer.write("telemetry", ready())
            await owner.wait_ready()
            try:
                yield Session(peer, owner, raw)
            finally:
                await owner.close()


def run(coroutine):
    asyncio.run(coroutine)


@pytest.mark.parametrize("source", ["", "a\0b", "\ud800", "__OMEGAFLOW_AWSH_X=1",
                                    "a" * (MAX_SOURCE_BYTES + 1), "\x01" * MAX_SOURCE_BYTES],
                         ids=["empty", "nul", "surrogate", "reserved", "source-limit", "frame-limit"])
def test_source_and_enclosing_frame_rejected_before_execute(source):
    async def case():
        async with session() as s:
            payload = {**EXECUTE, "source": source}
            with pytest.raises(EnvoyCodecError):
                await s.client.request("execute", payload)
            assert s.operation.state is OperationState.IDLE
            assert s.client.input_through == 0 and s.owner.ready
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(s.peer.channels["telemetry"][0].read(1), .02)
            assert (await s.request())["seq"] == 2
    run(case())


@pytest.mark.parametrize("source", ["a" * MAX_SOURCE_BYTES, "printf 'é🐚'\n# no final LF",
                                    "cat <<'EOF'\n\"$HOME\"\nEOF\n", "if true; then"], ids=["maximum", "utf8", "heredoc", "native-syntax"])
def test_source_is_one_exact_control_field_not_typed_or_host_parsed(source):
    async def case():
        async with session() as s:
            value = await s.request(payload={**EXECUTE, "source": source})
            assert value == {"schema": SCHEMA, "type": "execute", "seq": 2,
                             **EXECUTE, "source": source, "input_through": 0}
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(s.peer.channels["terminal"][0].read(1), .02)
    run(case())


@pytest.mark.parametrize("suffix", [b"\x01", b"\x02"])
@pytest.mark.parametrize("chunks", [lambda s: [b"before\x18" + s + b"after"],
                                    lambda s: [b"before\x18", s + b"after"],
                                    lambda s: [b"before\x18", b"", b"", s],
                                    lambda s: [b"ok", b"\x18", b"", s]])
def test_C3_2_reserved_input_and_adjacent_steps_rejected_before_execute(suffix, chunks):
    async def case():
        async with session() as s:
            with pytest.raises(EnvoyClientError, match="reserved terminal"):
                await s.client.request("execute", EXECUTE, inputs=chunks(suffix))
            assert s.operation.state is OperationState.IDLE and s.owner.ready
            for channel in ("terminal", "telemetry"):
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(s.peer.channels[channel][0].read(1), .02)
    run(case())


@pytest.mark.parametrize("chunks", [[bytearray(b"a")], [memoryview(b"a")], ["a"], [None]])
def test_mutable_or_nonbyte_plan_rejected(chunks):
    async def case():
        async with session() as s:
            with pytest.raises(EnvoyClientError, match="immutable bytes"):
                await s.client.request("execute", EXECUTE, inputs=chunks)
            assert s.operation.state is OperationState.IDLE
    run(case())


@pytest.mark.parametrize("field,value", [("input_through", 0), ("seq", 2), ("schema", SCHEMA),
                                         ("type", "execute")])
def test_caller_cannot_replace_writer_owned_fields(field, value):
    async def case():
        async with session() as s:
            with pytest.raises(EnvoyClientError, match="writer-owned"):
                await s.client.request("execute", {**EXECUTE, field: value})
            assert s.operation.state is OperationState.IDLE
    run(case())


def test_frozen_input_plan_exact_binary_and_public_start_gate():
    async def case():
        async with session() as s:
            chunks = [b"\xff\0\r\n\x03", b"\x18z", b"", "é".encode()]
            frozen = tuple(chunks)
            await s.request(inputs=chunks)
            chunks[:] = [b"\x18\x01"]
            with pytest.raises(EnvoyClientError, match="matching public"):
                await s.client.write_input("op-1")
            # A caller's premature C2 transition alone is not start evidence.
            s.operation.started("op-1")
            with pytest.raises(EnvoyClientError, match="matching public"):
                await s.client.write_input("op-1")
            await s.mark()
            await s.event("operation_started", operation_id="op-1", output_start=0)
            with pytest.raises(EnvoyClientError):
                await s.client.write_input("another-op")
            for chunk in frozen:
                await s.client.write_input("op-1")
                if chunk:
                    assert await s.peer.channels["terminal"][0].readexactly(len(chunk)) == chunk
            assert s.client.input_through == sum(map(len, frozen))
            with pytest.raises(EnvoyClientError, match="no admitted"):
                await s.client.write_input("op-1")
    run(case())


def test_watermark_persists_across_gate_and_next_operation():
    async def case():
        async with session() as s:
            assert (await s.request(inputs=[b"abc", b"\x03"]))["input_through"] == 0
            await s.start()
            await s.client.write_input("op-1")
            await s.mark()
            await s.event("operation_ready", operation_id="op-1", gate_id="gate", output_through=0)
            s.operation.gate_ready("op-1", "gate")
            await s.client.write_input("op-1")
            continued = await s.request("continue", {"operation_id": "op-1", "gate_id": "gate"})
            assert continued["input_through"] == 4 and continued["seq"] == 3
            await s.mark()
            await s.event("operation_continued", operation_id="op-1", gate_id="gate", output_through=0)
            s.operation.continued("op-1", "gate")
            await s.complete()
            next_request = await s.request(payload={**EXECUTE, "operation_id": "op-2"})
            assert next_request["input_through"] == 4 and next_request["seq"] == 4
            assert await s.peer.channels["terminal"][0].readexactly(4) == b"abc\x03"
    run(case())


def test_input_and_sequence_bounds_checked_before_lifecycle_or_io():
    async def case():
        async with session() as s:
            s.client._input_through = MAX_INTEGER - 1
            with pytest.raises(EnvoyClientError, match="count"):
                await s.client.request("execute", EXECUTE, inputs=[b"aa"])
            s.client._sequence = MAX_INTEGER + 1
            with pytest.raises(EnvoyCodecError, match="integer"):
                await s.client.request("execute", EXECUTE)
            assert s.operation.state is OperationState.IDLE
    run(case())


@pytest.mark.parametrize("partial", [False, True])
def test_partial_control_failure_closes_pair_without_reusing_sequence(monkeypatch, partial):
    async def case():
        async with session() as s:
            loop = asyncio.get_running_loop()
            original = loop.sock_sendall
            async def fail(sock, data):
                if partial:
                    await original(sock, data[:7])
                raise OSError("selected write failed")
            monkeypatch.setattr(loop, "sock_sendall", fail)
            with pytest.raises(OSError, match="selected write"):
                await s.client.request("execute", EXECUTE)
            assert not s.owner.ready and not s.owner._reads
            assert s.client._sequence == 2
            with pytest.raises(EnvoyClientError):
                await s.client.request("resize", {"columns": 90, "rows": 30})
            assert await s.peer.channels["telemetry"][0].read() == (b'{"schem' if partial else b"")
    run(case())


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_input_failure_or_cancellation_closes_pair(monkeypatch, cancel):
    async def case():
        async with session() as s:
            await s.request(inputs=[b"abcdef"])
            await s.start()
            loop = asyncio.get_running_loop()
            original = loop.sock_sendall
            partial = asyncio.Event()
            async def fail(sock, data):
                await original(sock, data[:3])
                partial.set()
                if cancel:
                    await asyncio.Future()
                raise OSError("partial terminal write")
            monkeypatch.setattr(loop, "sock_sendall", fail)
            task = asyncio.create_task(s.client.write_input("op-1"))
            await partial.wait()
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else OSError):
                await task
            assert s.client.input_through == 0 and not s.owner.ready
            assert await s.peer.channels["terminal"][0].read() == b"abc"
    run(case())


def test_terminal_wait_excludes_control_timer_and_keeps_cancel_live(monkeypatch):
    async def case():
        async with session() as s:
            await s.request(inputs=[b"hello"])
            await s.start()
            monkeypatch.setattr(envoy_transport, "WRITE_SECONDS", .02)
            loop = asyncio.get_running_loop()
            original = loop.sock_sendall
            waiting, release = asyncio.Event(), asyncio.Event()
            async def blocked(sock, data):
                if sock is s.owner._channels["terminal"]:
                    waiting.set()
                    await release.wait()
                await original(sock, data)
            monkeypatch.setattr(loop, "sock_sendall", blocked)
            task = asyncio.create_task(s.client.write_input("op-1"))
            try:
                await waiting.wait()
                await asyncio.sleep(.04)
                assert not task.done() and s.owner.ready
                with pytest.raises(EnvoyClientError, match="watermark"):
                    await s.client.request("continue", {"operation_id": "op-1", "gate_id": "g"})
                cancel = await s.request("cancel", {"operation_id": "op-1", "reason": "user"})
                assert cancel["seq"] == 3 and s.operation.state is OperationState.CANCELLING
                release.set()
                await task
                assert s.client.input_through == 5
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    run(case())


def test_control_deadline_uses_existing_bound_for_post_ready_frames(monkeypatch):
    async def case():
        async with session() as s:
            monkeypatch.setattr(envoy_transport, "WRITE_SECONDS", .02)
            async def blocked(*args):
                await asyncio.Future()
            monkeypatch.setattr(asyncio.get_running_loop(), "sock_sendall", blocked)
            with pytest.raises(TimeoutError):
                await s.client.request("execute", EXECUTE)
            assert not s.owner.ready and s.client._sequence == 2
    run(case())


def test_output_frontier_can_precede_raw_log_without_hiding_telemetry():
    async def case():
        async with session() as s:
            await s.request()
            await s.start()
            await s.mark(3, 2, "stdout")
            assert s.client.output_through == 3 and not s.client.output_available(3)
            assert s.owner.raw_offset == 0
            await s.peer.write("terminal", b"a\xffb")
            while s.owner.raw_offset < 3:
                await s.client.receive()
            assert raw_bytes(s.raw) == b"a\xffb" and s.client.output_available(3)
            await s.complete(end=3)
            assert s.client.output_through == 3 and s.client.output_available(3)
    run(case())


def test_raw_bytes_can_precede_marks_and_do_not_invent_frontiers():
    async def case():
        async with session() as s:
            await s.peer.write("terminal", b"raw")
            while s.owner.raw_offset < 3:
                await s.client.receive()
            assert s.client.output_through == 0
            with pytest.raises(EnvoyClientError, match="validated"):
                s.client.output_available(3)
            await s.mark(3)
            assert s.client.output_available(3)
    run(case())


@pytest.mark.parametrize("change", [{"offset": 2}, {"elapsed_us": 0}])
def test_regressing_marks_fail_closed(monkeypatch, change):
    async def case():
        async with session() as s:
            await s.mark(3, 1)
            with pytest.raises(EnvoyClientError, match="regressing"):
                await s.mark(**({"offset": 3, "elapsed_us": 1} | change))
            assert not s.owner.ready
    run(case())


def test_equal_offset_marks_can_change_stream_and_session_budget_is_bounded(monkeypatch):
    async def case():
        async with session() as s:
            monkeypatch.setattr(envoy_client, "MAX_OUTPUT_MARKS", 3)
            await s.mark(0, 1, "pty")
            await s.mark(0, 1, "stdout")
            await s.mark(0, 1, "stderr")
            with pytest.raises(EnvoyClientError, match="budget"):
                await s.mark(0, 2)
            assert not s.owner.ready
    run(case())


@pytest.mark.parametrize("problem", ["no-mark", "wrong-mark", "wrong-id", "duplicate-start"])
def test_start_requires_exact_identity_and_covering_boundary(problem):
    async def case():
        async with session() as s:
            await s.request(inputs=[b"unsafe"])
            if problem == "duplicate-start":
                await s.start()
            if problem != "no-mark":
                await s.mark()
            with pytest.raises(EnvoyClientError):
                await s.event("operation_started", operation_id="other" if problem == "wrong-id" else "op-1",
                              output_start=1 if problem == "wrong-mark" else 0)
            assert not s.owner.ready
    run(case())


@pytest.mark.parametrize("start,end", [(0, 3), (2, 3), (0, 0)])
def test_result_keeps_opened_range_anchor_instead_of_reinterpreting_as_new_frontier(start, end):
    async def case():
        async with session() as s:
            await s.request()
            await s.start(start)
            await s.complete(start=start, end=end)
            assert s.operation.state is OperationState.IDLE
            assert s.client.output_through == end
    run(case())


@pytest.mark.parametrize("problem", ["changed-anchor", "no-start", "prestart-after-start", "wrong-id"])
def test_terminal_range_mismatch_fails_closed(problem):
    async def case():
        async with session() as s:
            await s.request()
            if problem != "no-start":
                await s.start()
            await s.mark(3, 2)
            with pytest.raises(EnvoyClientError):
                if problem == "prestart-after-start":
                    await s.event("operation_failed", operation_id="op-1", output_start=3, output_through=3,
                                  code="source-syntax", message="invalid", cwd="/work")
                else:
                    await s.event("operation_completed", operation_id="other" if problem == "wrong-id" else "op-1",
                                  status=0, cwd="/work", output_start=1 if problem == "changed-anchor" else 0,
                                  output_through=3, inspection_results=[])
            assert not s.owner.ready
    run(case())


@pytest.mark.parametrize("kind", ["source-syntax", "source-policy", "input-barrier-timeout", "cancelled"])
def test_prestart_result_uses_later_empty_range_without_admitting_input(kind):
    async def case():
        async with session() as s:
            await s.request(inputs=[b"not sent"])
            if kind == "cancelled":
                await s.request("cancel", {"operation_id": "op-1", "reason": "user"})
            await s.mark(3)
            common = dict(operation_id="op-1", cwd="/work", output_start=3, output_through=3)
            if kind == "cancelled":
                await s.event("operation_cancelled", reason="user", **common)
            else:
                await s.event("operation_failed", code=kind, message="rejected", **common)
            s.operation.terminal_result("op-1", "cancelled" if kind == "cancelled" else "failed")
            with pytest.raises(EnvoyClientError):
                await s.client.write_input("op-1")
            assert (await s.request())["seq"] == (4 if kind == "cancelled" else 3)
    run(case())


def test_draining_is_returned_before_raw_barrier_for_original_timer_epoch():
    async def case():
        async with session() as s:
            request = await s.request("shutdown", {"reason": "done"})
            assert request["seq"] == 2
            await s.mark(3)
            result = await s.event("draining", reason="done", output_through=3)
            assert result["type"] == "draining" and s.owner.raw_offset == 0
            assert not s.client.output_available(3)
            s.operation.draining("done")
            await s.mark(3)
            await s.event("closed", reason="done", output_through=3)
            s.operation.closed("done")
            assert s.operation.state is OperationState.DRAINING
            await s.peer.write("terminal", b"end")
            await s.peer.eof("terminal")
            while (await s.client.receive())[0] != "terminal-eof":
                pass
            assert s.client.output_available(3)
            s.operation.terminal_eof()
            assert s.operation.state is OperationState.CLOSED
    run(case())


@pytest.mark.parametrize("kind,payload", [
    ("cancel", {"operation_id": "op-1", "reason": "user"}),
    ("finalize", {"operation_id": "op-1", "reason": "complete"}),
    ("resize", {"columns": 90, "rows": 30}),
    ("shutdown", {"reason": "done"}),
])
def test_canonical_remaining_request_forms_share_existing_c2_policy(kind, payload):
    async def case():
        async with session() as s:
            sequence = 2
            if kind in {"cancel", "finalize"}:
                await s.request()
                await s.start()
                sequence = 3
            result = await s.request(kind, payload)
            assert result == {"schema": SCHEMA, "type": kind, "seq": sequence, **payload}
            assert s.client.input_through == 0
    run(case())


def test_successful_inflight_input_after_result_counts_for_next_execute(monkeypatch):
    async def case():
        async with session() as s:
            await s.request(inputs=[b"old input"])
            await s.start()
            loop = asyncio.get_running_loop()
            original = loop.sock_sendall
            waiting, release = asyncio.Event(), asyncio.Event()
            async def blocked(sock, data):
                if sock is s.owner._channels["terminal"]:
                    waiting.set()
                    await release.wait()
                await original(sock, data)
            monkeypatch.setattr(loop, "sock_sendall", blocked)
            task = asyncio.create_task(s.client.write_input("op-1"))
            try:
                await waiting.wait()
                await s.complete()
                with pytest.raises(EnvoyClientError, match="watermark"):
                    await s.client.request("execute", {**EXECUTE, "operation_id": "op-2"})
                assert s.operation.state is OperationState.IDLE
                release.set()
                await task
                result = await s.request(payload={**EXECUTE, "operation_id": "op-2"})
                assert result["input_through"] == len(b"old input")
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    run(case())


def test_overlapping_control_writers_do_not_interleave_or_mutate_c2(monkeypatch):
    async def case():
        async with session() as s:
            original = s.owner.write_control
            waiting, release = asyncio.Event(), asyncio.Event()
            async def blocked(frame):
                waiting.set()
                await release.wait()
                await original(frame)
            monkeypatch.setattr(s.owner, "write_control", blocked)
            payload = deepcopy(EXECUTE)
            payload.update(observation="exclusive", inspections=[
                {"inspection_id": "check", "kind": "file_exists", "path": "original"}])
            task = asyncio.create_task(s.client.request("execute", payload))
            try:
                await waiting.wait()
                payload["source"] = "changed"
                payload["inspections"][0]["path"] = "changed"
                with pytest.raises(EnvoyClientError, match="serial writer"):
                    await s.client.request("cancel", {"operation_id": "op-1", "reason": "user"})
                assert s.operation.state is OperationState.STARTING
                release.set()
                await task
                wire = decode(await s.peer.channels["telemetry"][0].readuntil(b"\n"), controller=True)
                assert wire["source"] == EXECUTE["source"]
                assert wire["inspections"][0]["path"] == "original"
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    run(case())


def test_cancelling_control_write_disposes_both_channels(monkeypatch):
    async def case():
        async with session() as s:
            waiting = asyncio.Event()
            async def blocked(*args):
                waiting.set()
                await asyncio.Future()
            monkeypatch.setattr(asyncio.get_running_loop(), "sock_sendall", blocked)
            task = asyncio.create_task(s.client.request("execute", EXECUTE))
            await waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not s.owner.ready and s.client._sequence == 2
            assert not s.client._control_writing
    run(case())


@pytest.mark.parametrize("offset", [envoy_transport.MAX_RAW_BYTES + 1, MAX_INTEGER])
def test_sender_output_cannot_exceed_raw_log_bound(offset):
    async def case():
        async with session() as s:
            with pytest.raises(EnvoyClientError, match="regressing output"):
                await s.mark(offset)
            assert not s.owner.ready
    run(case())


@pytest.mark.parametrize("change", [dict(output_through=2), dict(elapsed_us=0)])
def test_resize_frontier_and_time_cannot_regress(change):
    async def case():
        async with session() as s:
            await s.mark(3, 5)
            with pytest.raises(EnvoyClientError):
                await s.event("resize_applied", **({"columns": 90, "rows": 30,
                                                    "elapsed_us": 5, "output_through": 3} | change))
            assert not s.owner.ready
    run(case())


def test_input_never_resumes_after_cancellation_selection():
    async def case():
        async with session() as s:
            await s.request(inputs=[b"first", b"later"])
            await s.start()
            await s.client.write_input("op-1")
            await s.request("cancel", {"operation_id": "op-1", "reason": "user"})
            with pytest.raises(EnvoyClientError, match="matching public"):
                await s.client.write_input("op-1")
            assert s.client.input_through == 5
    run(case())


def test_successful_resize_retains_sender_time_for_following_marks():
    async def case():
        async with session() as s:
            await s.mark(3, 5)
            await s.event("resize_applied", columns=90, rows=30, elapsed_us=6, output_through=3)
            assert s.client.output_through == 3 and s.owner.raw_offset == 0
            with pytest.raises(EnvoyClientError, match="regressing output mark"):
                await s.mark(3, 5)
    run(case())


def test_transport_write_guards_reject_unencoded_or_closed_calls():
    async def case():
        async with session() as s:
            for method in (s.owner.write_control, s.owner.write_terminal):
                with pytest.raises(envoy_transport.EnvoyTransportError):
                    await method("not bytes")
            assert s.owner.ready
            await s.owner.close()
            for method in (s.owner.write_control, s.owner.write_terminal):
                with pytest.raises(envoy_transport.EnvoyTransportError):
                    await method(b"closed")
    run(case())


@pytest.mark.parametrize("channel", ["terminal", "telemetry"])
def test_transport_excludes_second_writer_on_each_selected_channel(monkeypatch, channel):
    async def case():
        async with session() as s:
            waiting, release = asyncio.Event(), asyncio.Event()
            original = asyncio.get_running_loop().sock_sendall
            async def blocked(sock, data):
                waiting.set()
                await release.wait()
                await original(sock, data)
            monkeypatch.setattr(asyncio.get_running_loop(), "sock_sendall", blocked)
            write = s.owner.write_terminal if channel == "terminal" else s.owner.write_control
            data = b"input" if channel == "terminal" else encode(
                {"schema": SCHEMA, "type": "resize", "seq": 2, "columns": 80, "rows": 24})
            task = asyncio.create_task(write(data))
            try:
                await waiting.wait()
                with pytest.raises(envoy_transport.EnvoyTransportError, match="serial"):
                    await write(data)
                assert s.owner.ready
                release.set()
                await task
                assert await s.peer.channels[channel][0].readexactly(len(data)) == data
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    run(case())


@pytest.mark.parametrize("stop", ["capture-failure", "termination-request", "terminating"])
def test_input_admission_stops_with_the_authoritative_session(stop):
    async def case():
        async with session() as s:
            controller = ControllerPeer().active()
            s.operation = controller.controller.operation
            s.client = EnvoyClient(s.owner, s.operation)
            await s.request(inputs=[b"first", b"forbidden"])
            await s.start()
            await s.client.write_input("op-1")
            assert await s.peer.channels["terminal"][0].readexactly(5) == b"first"
            if stop == "capture-failure":
                controller.controller.fail("capture", "capture failed", terminate=False)
            elif stop == "termination-request":
                controller.controller.lifecycle.request_termination()
            else:
                controller.emit("workload-exit", status={"kind": "exited", "code": 0})
                controller.emit("terminating", cause="workload-exit")
            assert not s.operation.session.can_record
            assert s.operation.state is OperationState.RUNNING
            with pytest.raises(EnvoyClientError):
                await s.client.write_input("op-1")
            assert s.client.input_through == 5
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(s.peer.channels["terminal"][0].read(1), .02)
    run(case())


@pytest.mark.parametrize("channel", ["terminal", "telemetry"])
@pytest.mark.parametrize("stop", ["close", "receive-failure", "caller-cancel"])
def test_disposal_settles_real_backpressured_writes(channel, stop):
    async def case():
        async with session() as s:
            loop = asyncio.get_running_loop()
            errors = []
            original_handler = loop.get_exception_handler()
            loop.set_exception_handler(lambda _, context: errors.append(context))
            s.owner._channels[channel].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            s.peer.channels[channel][1].transport.pause_reading()
            write = s.owner.write_terminal if channel == "terminal" else s.owner.write_control
            task = asyncio.create_task(write(b"x" * (8 * 1024 * 1024)))
            try:
                done, _ = await asyncio.wait({task}, timeout=.02)
                assert not done  # The real socket write is blocked by the peer.
                if stop == "caller-cancel":
                    task.cancel()
                elif stop == "receive-failure":
                    await s.peer.write("telemetry", b"{broken}\n")
                    with pytest.raises(EnvoyCodecError):
                        await s.client.receive()
                else:
                    await s.owner.close()
                done, _ = await asyncio.wait({task}, timeout=.1)
                assert done, "transport disposal left a socket write pending"
                expected = (asyncio.CancelledError if stop == "caller-cancel"
                            else envoy_transport.EnvoyTransportError)
                with pytest.raises(expected):
                    await task
                assert not s.owner.ready and not asyncio.current_task().cancelling()
                await asyncio.sleep(0)  # Run selector cleanup before checking callbacks.
                assert errors == []
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                loop.set_exception_handler(original_handler)
    run(case())


def test_disposed_input_reports_failure_to_its_task_group():
    async def case():
        async with session() as s:
            s.owner._channels["terminal"].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            s.peer.channels["terminal"][1].transport.pause_reading()
            with pytest.raises(ExceptionGroup) as raised:
                async with asyncio.TaskGroup() as group:
                    task = group.create_task(s.owner.write_terminal(b"x" * (8 * 1024 * 1024)))
                    done, _ = await asyncio.wait({task}, timeout=.02)
                    assert not done
                    await s.owner.close()
            assert len(raised.value.exceptions) == 1
            assert isinstance(raised.value.exceptions[0], envoy_transport.EnvoyTransportError)
    run(case())
