"""C3.3 deterministic crossings over real TCP; native.py owns actual actors."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from tempfile import TemporaryFile
import time

import pytest

from omegaflow import envoy_controller
from omegaflow.controller_lifecycle import ControllerLifecycleError, OperationState
from omegaflow.envoy_client import EnvoyClientError
from omegaflow.envoy_codecs import MAX_FRAME_BYTES, SCHEMA, decode, encode
from omegaflow.envoy_controller import EnvoyController
from test_controller_session import Peer as ControllerPeer
from test_envoy_client import EXECUTE
from test_envoy_transport import Peer, connected, ready


@asynccontextmanager
async def harness():
    async with Peer() as peer:
        with TemporaryFile() as raw:
            owner = await connected(peer, raw)
            await peer.write("telemetry", ready())
            await owner.wait_ready()
            public = ControllerPeer()
            public.broker()
            public.accept(public.controller.attachment_connected(now=public.now))
            public.opened()
            public.emit("ready")
            adapter = EnvoyController(owner, public.controller)
            peer.channels["telemetry"][0]._limit = MAX_FRAME_BYTES
            h = Harness(peer, raw, public, adapter)
            try:
                yield h
            finally:
                await adapter.close()


class Harness:
    def __init__(self, peer, raw, public, adapter):
        self.peer, self.raw, self.public, self.adapter = peer, raw, public, adapter
        self.seq = 2

    async def request(self, kind="execute", payload=None, **kw):
        await self.adapter.request(kind, deepcopy(EXECUTE) if payload is None else payload, **kw)
        return decode(await self.peer.channels["telemetry"][0].readuntil(b"\n"), controller=True)

    async def send(self, kind, **payload):
        event = {"schema": SCHEMA, "type": kind, "seq": self.seq, **payload}
        self.seq += 1
        await self.peer.write("telemetry", encode(event, controller=False))
        return event

    async def event(self, kind, **payload):
        event = await self.send(kind, **payload)
        async with asyncio.timeout(1):
            while True:
                observed_kind, observed = await self.adapter.receive()
                if observed_kind == "event":
                    assert observed == event
                    return event

    async def mark(self, offset=0):
        await self.event("output_mark", offset=offset, elapsed_us=self.seq, stream="pty")

    async def start(self):
        await self.mark()
        await self.event("operation_started", operation_id="op-1", output_start=0)

    async def result(self, kind="completed", end=0, inspections=None, **extra):
        await self.mark(end)
        payload = {"operation_id": "op-1", "cwd": "/work", "output_start": 0, "output_through": end}
        if kind in {"completed", "cancelled"}:
            payload["status"] = 0
        if kind in {"completed", "finalized"}:
            payload["inspection_results"] = inspections or []
        await self.event("operation_" + kind, **payload, **extra)


def run(case):
    asyncio.run(case)


def test_inspection_ids_and_results_follow_the_frozen_authored_plan():
    async def case():
        async with harness() as h:
            plan = [{"kind": "file_exists", "path": "$UNDEFINED/not-on-host"},
                    {"kind": "produces", "path": "~/output", "producer_id": "beat", "output_id": "out"}]
            value = {**EXECUTE, "observation": "exclusive", "inspections": plan}
            request = await h.request(payload=value)
            assert [x["inspection_id"] for x in request["inspections"]] == ["inspection-1", "inspection-2"]
            assert all("inspection_id" not in x for x in plan)
            plan[0]["kind"] = "mutated-after-write"
            await h.start()
            results = [{"inspection_id": "inspection-1", "kind": "file_exists",
                        "resolved_path": "/work/literal", "path_kind": "other"},
                       {"inspection_id": "inspection-2", "kind": "produces", "resolved_path": "/work/out",
                        "path_kind": "directory", "producer_id": "beat", "output_id": "out",
                        "sha256": "a" * 64, "digest_algorithm": "directory-v2"}]
            await h.result(inspections=results)
            assert h.adapter.take_result()["inspection_results"] == results
            assert h.public.controller.failure is None
    run(case())


@pytest.mark.parametrize("bad", ["missing", "extra", "order", "kind", "producer", "output"])
def test_inspection_mismatch_fails_capture_and_retains_termination_effect(bad):
    async def case():
        async with harness() as h:
            plan = [{"kind": "file_exists", "path": "a"},
                    {"kind": "produces", "path": "b", "producer_id": "p", "output_id": "o"}]
            await h.request(payload={**EXECUTE, "observation": "exclusive", "inspections": plan})
            await h.start()
            results = [{"inspection_id": "inspection-1", "kind": "file_exists",
                        "resolved_path": "/a", "path_kind": "file"},
                       {"inspection_id": "inspection-2", "kind": "produces", "resolved_path": "/b",
                        "path_kind": "file", "producer_id": "p", "output_id": "o", "sha256": "a" * 64}]
            if bad == "missing":
                results.pop()
            elif bad == "extra":
                results.append({**results[0], "inspection_id": "inspection-3"})
            elif bad == "order":
                results.reverse()
            elif bad == "kind":
                results[1] = {**results[0], "inspection_id": "inspection-2"}
            else:
                results[1][bad + "_id"] = "wrong"
            with pytest.raises(EnvoyClientError, match="inspection"):
                await h.result(inspections=results)
            assert h.public.controller.failure.code == "envoy-session"
            assert [e.kind for e in h.adapter.failure_effects] == ["retain-partial-artifacts", "send-request"]
            assert not h.adapter.client.transport.ready
    run(case())


def test_preflight_does_not_change_ready_session_or_take_a_caller_inspection_id():
    async def case():
        async with harness() as h:
            with pytest.raises(EnvoyClientError, match="IDs"):
                await h.request(payload={**EXECUTE, "inspections": [
                    {"kind": "file_exists", "path": "a", "inspection_id": "forged"}]})
            assert h.public.controller.operation.state is OperationState.IDLE
            assert h.adapter.client.transport.ready and not h.adapter.failure_effects
            assert (await h.request())["seq"] == 2
    run(case())


@pytest.mark.parametrize("control", ["cancel", "finalize"])
@pytest.mark.parametrize("gate_first", [False, True])
@pytest.mark.parametrize("observation", ["operation_ready", "operation_continued", "operation_gate_interrupted"])
def test_crossed_gate_keeps_selected_lifecycle_and_does_not_reopen_input(control, gate_first, observation):
    async def case():
        async with harness() as h:
            await h.request(inputs=[b"forbidden"])
            await h.start()
            if observation != "operation_ready":
                await h.mark()
                await h.event("operation_ready", operation_id="op-1", gate_id="gate", output_through=0)
                if observation == "operation_continued":
                    await h.request("continue", {"operation_id": "op-1", "gate_id": "gate"})
            if gate_first:
                await h.mark()
                await h.event(observation, operation_id="op-1", gate_id="gate", output_through=0)
            await h.request(control, {"operation_id": "op-1", "reason": "selected"})
            if not gate_first:
                await h.mark()
                await h.event(observation, operation_id="op-1", gate_id="gate", output_through=0)
            expected = OperationState.CANCELLING if control == "cancel" else OperationState.FINALIZING
            assert h.public.controller.operation.state is expected
            with pytest.raises(EnvoyClientError):
                await h.adapter.write_input("op-1")
            await h.result("cancelled" if control == "cancel" else "finalized", reason="selected")
            assert h.adapter.take_result()["reason"] == "selected"
    run(case())


def test_continue_waits_for_matching_gate_raw_barrier_before_wire_and_state_change():
    async def case():
        async with harness() as h:
            await h.request(inputs=[b"forbidden"])
            await h.start()
            await h.mark(3)
            await h.event("operation_ready", operation_id="op-1", gate_id="gate", output_through=3)
            sequence = h.adapter.client._sequence
            input_through = h.adapter.client.input_through
            with pytest.raises(EnvoyClientError, match="gate output barrier"):
                await h.adapter.request("continue", {"operation_id": "op-1", "gate_id": "gate"})
            assert h.public.controller.operation.state is OperationState.GATED
            assert h.adapter.client._sequence == sequence
            assert h.adapter.client.input_through == input_through == 0
            assert h.adapter.client.transport.raw_offset == 0
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(h.peer.channels["telemetry"][0].read(1), .05)
            await h.peer.write("terminal", b"abc")
            while not h.adapter.client.output_available(3):
                await h.adapter.receive()
            wire = await h.request("continue", {"operation_id": "op-1", "gate_id": "gate"})
            assert wire["seq"] == sequence and wire["operation_id"] == "op-1"
            assert wire["gate_id"] == "gate" and wire["input_through"] == input_through
            await h.mark(3)
            await h.event("operation_continued", operation_id="op-1", gate_id="gate",
                          output_through=3)
            await h.result(end=3)
            assert h.adapter.take_result()["operation_id"] == "op-1"
    run(case())


@pytest.mark.parametrize("control", ["cancel", "finalize"])
def test_cancel_and_finalize_remain_available_while_gate_raw_barrier_is_pending(control):
    async def case():
        async with harness() as h:
            await h.request(inputs=[b"forbidden"])
            await h.start()
            await h.mark(3)
            await h.event("operation_ready", operation_id="op-1", gate_id="gate", output_through=3)
            wire = await h.request(control, {"operation_id": "op-1", "reason": "selected"})
            assert wire["type"] == control and wire["operation_id"] == "op-1"
            expected = OperationState.CANCELLING if control == "cancel" else OperationState.FINALIZING
            assert h.public.controller.operation.state is expected
            await h.result("cancelled" if control == "cancel" else "finalized", end=3,
                           reason="selected")
            with pytest.raises(EnvoyClientError):
                h.adapter.take_result()
            await h.peer.write("terminal", b"abc")
            while not h.adapter.result_available:
                await h.adapter.receive()
            assert h.adapter.take_result()["reason"] == "selected"
    run(case())


def test_deadline_cancellation_remains_available_while_gate_raw_barrier_is_pending():
    async def case():
        async with harness() as h:
            deadline = asyncio.get_running_loop().time() + .5
            await h.request(inputs=[b"forbidden"], deadline=deadline)
            await h.start()
            await h.mark(3)
            await h.event("operation_ready", operation_id="op-1", gate_id="gate", output_through=3)
            receiver = asyncio.create_task(h.adapter.receive())
            cancel = decode(await asyncio.wait_for(
                h.peer.channels["telemetry"][0].readuntil(b"\n"), 2), controller=True)
            assert cancel["type"] == "cancel" and cancel["reason"] == "deadline"
            assert h.public.controller.operation.state is OperationState.CANCELLING
            assert h.adapter.client.transport.ready
            await h.peer.write("terminal", b"abc")
            await h.peer.write("telemetry", encode({"schema": SCHEMA, "type": "output_mark",
                                                      "seq": h.seq, "offset": 3,
                                                      "elapsed_us": h.seq, "stream": "pty"},
                                                     controller=False))
            h.seq += 1
            first_kind, first_value = await receiver
            if first_kind == "event":
                assert first_value["type"] == "output_mark"
            else:
                mark_kind, mark_value = await h.adapter.receive()
                assert mark_kind == "event" and mark_value["type"] == "output_mark"
            await h.event("operation_cancelled", operation_id="op-1", status=130, cwd="/",
                          output_start=0, output_through=3, reason="deadline")
            while not h.adapter.result_available:
                await h.adapter.receive()
            assert h.adapter.take_result()["reason"] == "deadline"
    run(case())


@pytest.mark.parametrize("control", ["cancel", "finalize"])
def test_committed_ordinary_result_wins_later_control(control):
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            await h.request(control, {"operation_id": "op-1", "reason": "late"})
            await h.result()
            assert h.adapter.take_result()["status"] == 0
            assert h.public.controller.operation.state is OperationState.IDLE
            assert (await h.request(payload={**EXECUTE, "operation_id": "next"}))["seq"] == 4
    run(case())


@pytest.mark.parametrize("finalized", [False, True])
def test_committed_inspection_result_wins_later_cancel(finalized):
    async def case():
        async with harness() as h:
            plan = [{"kind": "file_exists", "path": "artifact"},
                    {"kind": "produces", "path": "artifact", "producer_id": "p", "output_id": "o"}]
            await h.request(payload={**EXECUTE, "observation": "exclusive", "inspections": plan})
            await h.start()
            if finalized:
                await h.request("finalize", {"operation_id": "op-1", "reason": "native"})
                await h.request("cancel", {"operation_id": "op-1", "reason": "native"})
            else:
                await h.request("cancel", {"operation_id": "op-1", "reason": "native"})
            results = [{"inspection_id": "inspection-1", "kind": "file_exists",
                        "resolved_path": "/work/artifact", "path_kind": "file"},
                       {"inspection_id": "inspection-2", "kind": "produces",
                        "resolved_path": "/work/artifact", "path_kind": "file",
                        "producer_id": "p", "output_id": "o", "sha256": "a" * 64}]
            await h.result("finalized" if finalized else "completed", inspections=results,
                           **({"reason": "native"} if finalized else {}))
            assert h.adapter.take_result()["inspection_results"] == results
            assert h.public.controller.operation.state is OperationState.IDLE
            assert (await h.request(payload={**EXECUTE, "operation_id": "next"}))["seq"] == (5 if finalized else 4)
    run(case())


def test_terminal_result_returns_before_raw_but_next_execute_waits_for_exact_output():
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            await h.result(end=3)
            assert not h.adapter.result_available
            with pytest.raises(EnvoyClientError):
                h.adapter.take_result()
            with pytest.raises(EnvoyClientError, match="barrier"):
                await h.request(payload={**EXECUTE, "operation_id": "next"})
            await h.peer.write("terminal", b"abc")
            assert await h.adapter.receive() == ("terminal", b"abc")
            assert h.adapter.take_result()["output_through"] == 3
            assert (await h.request(payload={**EXECUTE, "operation_id": "next"}))["seq"] == 3
    run(case())


@pytest.mark.parametrize("requested,returned", [(None, (80, 24)), ((90, 30), (80, 24))])
def test_resize_acknowledgement_must_match_its_request(requested, returned):
    async def case():
        async with harness() as h:
            if requested:
                await h.request("resize", {"columns": requested[0], "rows": requested[1]})
            await h.mark()
            with pytest.raises(EnvoyClientError, match="resize"):
                await h.event("resize_applied", columns=returned[0], rows=returned[1],
                              elapsed_us=h.seq, output_through=0)
    run(case())


@pytest.mark.parametrize("eof_first", [False, True])
def test_final_close_requires_telemetry_and_terminal_eof_in_either_channel_order(eof_first):
    async def case():
        async with harness() as h:
            await h.request("shutdown", {"reason": "done"})
            if eof_first:
                h.peer.channels["terminal"][1].write_eof()
                assert (await h.adapter.receive())[0] == "terminal-eof"
                assert not h.adapter.closed
            await h.mark()
            await h.event("draining", reason="done", output_through=0)
            await h.mark()
            await h.event("closed", reason="done", output_through=0)
            if not eof_first:
                assert not h.adapter.closed
                h.peer.channels["terminal"][1].write_eof()
                assert (await h.adapter.receive())[0] == "terminal-eof"
            assert h.adapter.closed
            assert h.public.controller.operation.state is OperationState.CLOSED
            assert not h.public.controller.closed  # Reploy result remains a different boundary.
    run(case())


def test_terminal_eof_after_result_frontier_fails_capture_and_retains_effects():
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            await h.result(end=3)
            assert h.adapter.client.transport.raw_offset == 0
            h.peer.channels["terminal"][1].write_eof()
            with pytest.raises(EnvoyClientError, match="terminal EOF"):
                await h.adapter.receive()
            assert h.public.controller.failure.code == "envoy-session"
            assert [effect.kind for effect in h.adapter.failure_effects] == [
                "retain-partial-artifacts", "send-request"]
            assert not h.adapter.client.transport.ready
    run(case())


def test_terminal_eof_then_later_frontier_fails_before_result_release():
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            h.peer.channels["terminal"][1].write_eof()
            assert (await h.adapter.receive())[0] == "terminal-eof"
            assert h.public.controller.failure is None
            await h.send("output_mark", offset=3, elapsed_us=h.seq, stream="pty")
            with pytest.raises(EnvoyClientError, match="terminal EOF"):
                await h.adapter.receive()
            assert h.public.controller.failure.code == "envoy-session"
            assert [effect.kind for effect in h.adapter.failure_effects] == [
                "retain-partial-artifacts", "send-request"]
            assert not h.adapter.client.transport.ready
    run(case())


def test_final_drain_timer_is_not_delayed_or_reset_by_raw_or_telemetry(monkeypatch):
    monkeypatch.setattr(envoy_controller, "FINAL_DRAIN_SECONDS", .04)
    async def case():
        async with harness() as h:
            await h.request("shutdown", {"reason": "done"})
            await h.mark(3)
            await h.event("draining", reason="done", output_through=3)
            deadline = h.adapter._drain_deadline
            await h.mark(3)
            assert h.adapter._drain_deadline == deadline
            assert h.adapter.client.transport.raw_offset == 0
            with pytest.raises(TimeoutError, match="final drain"):
                await h.adapter.receive()
            assert h.public.controller.failure and h.adapter.failure_effects
            assert not h.adapter.client.transport.ready
    run(case())


def test_final_drain_rechecks_deadline_before_accepting_late_closed(monkeypatch):
    monkeypatch.setattr(envoy_controller, "FINAL_DRAIN_SECONDS", .04)

    async def case():
        async with harness() as h:
            await h.request("shutdown", {"reason": "done"})
            await h.mark()
            await h.event("draining", reason="done", output_through=0)
            h.peer.channels["terminal"][1].write_eof()
            assert (await h.adapter.receive())[0] == "terminal-eof"
            deadline = h.adapter._drain_deadline
            await h.mark()
            await h.send("closed", reason="done", output_through=0)

            original_wait = envoy_controller.asyncio.wait

            async def delayed_wait(*args, **kwargs):
                done, pending = await original_wait(*args, **kwargs)
                if done and "return_when" not in kwargs:
                    await asyncio.sleep(.06)
                return done, pending

            monkeypatch.setattr(envoy_controller.asyncio, "wait", delayed_wait)
            with pytest.raises(TimeoutError, match="final drain"):
                await h.adapter.receive()
            assert not h.adapter.closed
            assert h.adapter._drain_deadline == deadline
            assert h.public.controller.failure and h.adapter.failure_effects
            assert not h.adapter.client.transport.ready
    run(case())


def test_final_drain_rechecks_deadline_before_accepting_late_terminal_eof(monkeypatch):
    monkeypatch.setattr(envoy_controller, "FINAL_DRAIN_SECONDS", .04)

    async def case():
        async with harness() as h:
            await h.request("shutdown", {"reason": "done"})
            await h.mark()
            await h.event("draining", reason="done", output_through=0)
            await h.mark()
            await h.event("closed", reason="done", output_through=0)
            deadline = h.adapter._drain_deadline
            h.peer.channels["terminal"][1].write_eof()

            original_wait = envoy_controller.asyncio.wait

            async def delayed_wait(*args, **kwargs):
                done, pending = await original_wait(*args, **kwargs)
                if done and "return_when" not in kwargs:
                    await asyncio.sleep(.06)
                return done, pending

            monkeypatch.setattr(envoy_controller.asyncio, "wait", delayed_wait)
            with pytest.raises(TimeoutError, match="final drain"):
                await h.adapter.receive()
            assert not h.adapter.closed
            assert h.adapter._drain_deadline == deadline
            assert h.public.controller.failure and h.adapter.failure_effects
            assert not h.adapter.client.transport.ready
    run(case())


def test_plan_deadline_sends_cancel_without_cancelling_the_selected_read():
    async def case():
        async with harness() as h:
            deadline = asyncio.get_running_loop().time() + .03
            await h.request(deadline=deadline)
            waiter = asyncio.create_task(h.adapter.receive())
            request = decode(await asyncio.wait_for(h.peer.channels["telemetry"][0].readuntil(b"\n"), 1),
                             controller=True)
            assert request["type"] == "cancel" and request["reason"] == "deadline"
            assert h.adapter.client.transport.ready
            await h.send("output_mark", offset=0, elapsed_us=1, stream="pty")
            assert (await asyncio.wait_for(waiter, 1))[0] == "event"
            await h.event("operation_started", operation_id="op-1", output_start=0)
            await h.result("cancelled", reason="deadline")
            assert h.adapter.take_result()["reason"] == "deadline"
    run(case())


@pytest.mark.parametrize("observation", ["operation_ready", "operation_continued",
                                         "operation_gate_interrupted"])
def test_plan_deadline_rechecks_after_wait_before_nonterminal_observation(observation):
    async def case():
        async with harness() as h:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + .5
            await h.request(inputs=[b"forbidden"], deadline=deadline)
            await h.start()
            gate = "late-gate"
            if observation != "operation_ready":
                await h.mark()
                await h.event("operation_ready", operation_id="op-1", gate_id=gate,
                              output_through=0)
                if observation == "operation_continued":
                    await h.adapter.request("continue", {"operation_id": "op-1", "gate_id": gate})
                    continue_wire = decode(await h.peer.channels["telemetry"][0].readuntil(b"\n"),
                                            controller=True)
                    assert continue_wire["type"] == "continue"
            else:
                await h.mark()
            if observation != "operation_ready":
                await h.mark()
            receiver = asyncio.create_task(h.adapter.receive())
            await asyncio.sleep(0)
            selected_read = h.adapter._read
            assert selected_read is not None
            completed_reads = []
            selected_read.add_done_callback(
                lambda task: (completed_reads.append(task), time.sleep(.6)))
            await h.send(observation, operation_id="op-1", gate_id=gate,
                         output_through=0)
            kind, event = await asyncio.wait_for(receiver, 2)
            assert kind == "event" and event["type"] == observation
            cancel = decode(await asyncio.wait_for(
                h.peer.channels["telemetry"][0].readuntil(b"\n"), 1), controller=True)
            assert cancel["type"] == "cancel" and cancel["reason"] == "deadline"
            assert len(completed_reads) == 1 and completed_reads[0] is selected_read
            assert not selected_read.cancelled() and h.adapter.client.transport.ready
            assert h.public.controller.failure is None
            assert h.public.controller.operation.state is OperationState.CANCELLING
            assert h.adapter._operation_deadline is None
            with pytest.raises(EnvoyClientError):
                await h.adapter.write_input("op-1")
            with pytest.raises(ControllerLifecycleError):
                await h.adapter.request("continue", {"operation_id": "op-1", "gate_id": gate})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(h.peer.channels["telemetry"][0].read(1), .05)
    run(case())


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), -1, "tomorrow"])
def test_invalid_plan_deadline_is_rejected_before_execute(deadline):
    async def case():
        async with harness() as h:
            with pytest.raises(EnvoyClientError, match="deadline"):
                await h.request(deadline=deadline)
            assert h.public.controller.operation.state is OperationState.IDLE
            assert (await h.request())["seq"] == 2
    run(case())


@pytest.mark.parametrize("control", ["cancel", "finalize"])
def test_result_cannot_change_the_selected_control_reason(control):
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            await h.request(control, {"operation_id": "op-1", "reason": "selected"})
            with pytest.raises(EnvoyClientError, match="reason"):
                await h.result("cancelled" if control == "cancel" else "finalized", reason="other")
            assert h.public.controller.failure and not h.adapter.result_available
    run(case())


def test_resize_requests_are_single_flight_and_shutdown_waits_for_acknowledgement():
    async def case():
        async with harness() as h:
            assert (await h.request("resize", {"columns": 80, "rows": 24}))["seq"] == 2
            with pytest.raises(EnvoyClientError, match="resize"):
                await h.adapter.request("resize", {"columns": 91, "rows": 31})
            with pytest.raises(EnvoyClientError, match="resize"):
                await h.adapter.request("shutdown", {"reason": "done"})
            assert h.public.controller.operation.state is OperationState.IDLE

            await h.mark()
            await h.event("resize_applied", columns=80, rows=24,
                          elapsed_us=h.seq, output_through=0)
            assert (await h.request("resize", {"columns": 91, "rows": 31}))["seq"] == 3
            await h.mark()
            await h.event("resize_applied", columns=91, rows=31,
                          elapsed_us=h.seq, output_through=0)
            assert not h.adapter._resizes and h.public.controller.failure is None
    run(case())


def test_envoy_draining_resolves_one_pending_resize_without_an_applied_event():
    async def case():
        async with harness() as h:
            await h.request("resize", {"columns": 91, "rows": 31})
            await h.mark()
            await h.event("draining", reason="shell_ended", output_through=0)
            assert not h.adapter._resizes
    run(case())


def test_shell_ended_crossed_resize_is_discarded_by_draining():
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            await h.result("completed", shell_ended=True)
            assert h.adapter.take_result()["shell_ended"] is True
            assert (await h.request("resize", {"columns": 91, "rows": 31}))["seq"] == 3
            await h.mark()
            await h.event("draining", reason="shell_ended", output_through=0)
            assert not h.adapter._resizes
    run(case())


def test_raw_output_can_arrive_before_the_matching_result():
    async def case():
        async with harness() as h:
            await h.request()
            await h.start()
            await h.peer.write("terminal", b"abc")
            assert await h.adapter.receive() == ("terminal", b"abc")
            assert not h.adapter.result_available
            await h.result(end=3)
            assert h.adapter.take_result()["output_through"] == 3
    run(case())


@pytest.mark.parametrize("where", ["before-drain", "after-drain"])
def test_telemetry_eof_without_closed_is_capture_failure(where):
    async def case():
        async with harness() as h:
            if where == "after-drain":
                await h.request("shutdown", {"reason": "done"})
                await h.mark()
                await h.event("draining", reason="done", output_through=0)
            h.peer.channels["telemetry"][1].write_eof()
            with pytest.raises(EnvoyClientError, match="EOF"):
                await h.adapter.receive()
            assert h.public.controller.failure and not h.adapter.closed
    run(case())


@pytest.mark.parametrize("final", [0, 4])
def test_final_offset_must_equal_all_retained_terminal_bytes(final):
    async def case():
        async with harness() as h:
            await h.peer.write("terminal", b"abc")
            assert (await h.adapter.receive())[0] == "terminal"
            await h.request("shutdown", {"reason": "done"})
            await h.mark(final)
            await h.event("draining", reason="done", output_through=final)
            await h.mark(final)
            await h.event("closed", reason="done", output_through=final)
            h.peer.channels["terminal"][1].write_eof()
            with pytest.raises(EnvoyClientError, match="offset"):
                await h.adapter.receive()
            assert not h.adapter.closed and h.public.controller.failure
    run(case())


@pytest.mark.parametrize("stop", ["failure", "termination-request", "terminating"])
def test_reploy_recording_stop_blocks_even_non_operation_control(stop):
    async def case():
        async with harness() as h:
            if stop == "failure":
                h.public.controller.fail("capture", "failed", terminate=False)
            elif stop == "termination-request":
                h.public.controller.lifecycle.request_termination()
            else:
                h.public.emit("terminating", cause="controller-terminate")
            with pytest.raises(EnvoyClientError):
                await h.request("resize", {"columns": 91, "rows": 31})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(h.peer.channels["telemetry"][0].read(1), .02)
    run(case())


def test_disposal_joins_an_outstanding_pull_and_preserves_caller_owned_files():
    async def case():
        async with harness() as h:
            receiver = asyncio.create_task(h.adapter.receive())
            await asyncio.sleep(.01)
            with pytest.raises(EnvoyClientError, match="serial"):
                await h.adapter.receive()
            await h.adapter.close()
            with pytest.raises(asyncio.CancelledError):
                await receiver
            await h.adapter.close()
            assert not h.raw.closed and not h.adapter.closed
    run(case())
