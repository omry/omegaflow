from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path

import pytest

from omegaflow.controller_lifecycle import (
    ControllerLifecycleError, OperationLifecycle, OperationState, ReployLifecycle, ReployState,
)
from omegaflow.controller_session import ControllerSession
from omegaflow.reploy_codecs import CLIENT_SCHEMA, decode_request, encode_event


EVENTS = [json.loads(x) for x in (Path(__file__).parent /
          "fixtures/reploy-codecs/client-v1-events.jsonl").read_bytes().splitlines()]
OPENED = next(x for x in EVENTS if x["type"] == "opened")
RESULT = next(x["result"] for x in EVENTS if x["type"] == "terminated")


def event(kind, **payload):
    return encode_event({"schema": CLIENT_SCHEMA, "type": kind, **payload})


class Peer:
    def __init__(self, *, event_sink=None, stderr_sink=None, tail=17):
        self.events = event_sink if event_sink is not None else io.BytesIO()
        self.stderr = stderr_sink if stderr_sink is not None else io.BytesIO()
        self.controller = ControllerSession(event_sink=self.events, stderr_sink=self.stderr,
                                            stderr_tail_limit=tail, context={"recording": "demo"})
        self.effects = []
        self.requests = []
        self.now = 100.0
        self.accept(self.controller.start())

    def accept(self, effects):
        self.effects.extend(effects)
        for effect in effects:
            if effect.kind == "send-request":
                request = decode_request(effect.line)
                if request["type"] == "acknowledge-terminated":
                    assert self.events.getvalue().splitlines()[-1].find(b'"terminated"') >= 0
                self.requests.append(request)
        return [e.kind for e in effects]

    def emit(self, kind, **payload):
        return self.accept(self.controller.on_event(event(kind, **payload), now=self.now))

    def broker(self):
        return self.emit("broker-ready", terminal_socket="/opaque path;$(data)")

    def opened(self, **changes):
        value = deepcopy(OPENED)
        value.update(changes)
        return self.accept(self.controller.on_event(encode_event(value), now=self.now))

    def active(self):
        self.broker()
        self.accept(self.controller.attachment_connected(now=self.now))
        self.opened()
        self.emit("ready")
        self.controller.lifecycle.envoy_ready()
        return self

    def finish(self, *, cause="workload-exit", output="drained", artifact_error=None,
               result_changes=None, attachment_code=0, eof_first=False):
        if cause == "workload-exit":
            self.emit("workload-exit", status={"kind": "exited", "code": 0})
        self.emit("terminating", cause=cause)
        if eof_first:
            self.accept(self.controller.attachment_ended(attachment_code))
        payload = {"status": output}
        if output == "failed":
            payload["reason"] = "drain failed precisely"
        self.emit("workload-outputs-finalized", **payload)
        if not eof_first:
            self.accept(self.controller.attachment_ended(attachment_code))
        self.accept(self.controller.artifacts_finalized(error=artifact_error))
        result = deepcopy(RESULT)
        result["cause"] = cause
        result["workload_output_finalization_status"] = {"kind": output}
        if output == "failed":
            result["workload_output_finalization_status"]["reason"] = payload["reason"]
        result.update(result_changes or {})
        self.emit("terminated", result=result)
        return result


@pytest.mark.parametrize("eof_first", [False, True])
def test_C2_S016_complete_result_acknowledgement_and_clean_close(eof_first):
    peer = Peer().active()
    result = peer.finish(eof_first=eof_first)
    assert peer.controller.failure is None
    assert peer.controller.result == result
    assert [r["type"] for r in peer.requests] == ["complete", "acknowledge-terminated"]
    assert peer.controller.lifecycle.state is ReployState.RESULT
    assert not peer.controller.closed
    assert peer.accept(peer.controller.client_closed(0)) == []
    assert peer.controller.closed and not peer.controller.can_record
    assert json.loads(peer.events.getvalue())["result"] == result
    copied = peer.controller.result
    copied["cause"] = "forged"
    assert peer.controller.result == result


@pytest.mark.parametrize("with_opened", [False, True])
def test_C2_S017_startup_failure_acknowledges_without_complete(with_opened):
    peer = Peer()
    peer.broker()
    if with_opened:
        peer.opened()
    result = deepcopy(RESULT)
    result["cause"] = "startup-failure"
    result["controller_finalization_status"] = {"kind": "startup-failed"}
    peer.emit("terminated", result=result)
    assert [r["type"] for r in peer.requests] == ["acknowledge-terminated"]
    assert peer.controller.failure.code == "session-result"
    assert peer.controller.result == result
    assert peer.accept(peer.controller.client_closed(0)) == []
    assert peer.controller.closed and peer.controller.failure is not None


@pytest.mark.parametrize("phase", ["before-broker", "opened", "ready", "result"])
def test_C2_S018_premature_or_duplicate_acknowledgement_is_fatal(phase):
    peer = Peer()
    if phase != "before-broker":
        peer.broker()
        peer.opened()
    if phase == "ready":
        peer.emit("ready")
    if phase == "result":
        result = deepcopy(RESULT)
        result["cause"] = "startup-failure"
        result["controller_finalization_status"] = {"kind": "startup-failed"}
        peer.emit("terminated", result=result)
    before = len(peer.requests)
    with pytest.raises(ControllerLifecycleError):
        peer.controller.lifecycle.acknowledge_terminated()
    assert len(peer.requests) == before
    assert peer.controller.lifecycle.state is ReployState.FAILED


@pytest.mark.parametrize("phase", ["new", "broker", "opened", "ready", "result"])
@pytest.mark.parametrize("code", [0, 1, -9, True, "0"])
def test_C2_S019_missing_result_or_bad_broker_exit_never_establishes_clean_close(phase, code):
    peer = Peer()
    if phase in {"broker", "opened", "ready", "result"}:
        peer.broker()
    if phase in {"opened", "ready", "result"}:
        peer.opened()
    if phase == "ready":
        peer.emit("ready")
    if phase == "result":
        result = deepcopy(RESULT)
        result["cause"] = "startup-failure"
        result["controller_finalization_status"] = {"kind": "startup-failed"}
        peer.emit("terminated", result=result)
    peer.accept(peer.controller.client_closed(code))
    assert peer.controller.closed is (phase == "result" and type(code) is int and code == 0)
    assert peer.controller.failure is not None


def test_C2_S019_zero_broker_exit_requires_acknowledgement_in_core():
    core = ReployLifecycle()
    core.on_event(event("broker-ready", terminal_socket="/opaque"), now=0)
    result = deepcopy(RESULT)
    result["cause"] = "startup-failure"
    result["controller_finalization_status"] = {"kind": "startup-failed"}
    core.on_event(event("terminated", result=result), now=1)
    with pytest.raises(ControllerLifecycleError):
        core.broker_closed(0)
    assert core.result == result


def test_C2_S019_duplicate_broker_close_preserves_failure():
    peer = Peer().active()
    peer.finish()
    peer.controller.client_closed(0)
    peer.controller.client_closed(0)
    assert peer.controller.failure.code == "client-close"
    assert not peer.controller.can_record


@pytest.mark.parametrize("output,artifact_error,attachment_code", [
    ("failed", None, 0), ("drained", "media render failed", 0), ("drained", None, 17),
])
def test_C2_S020_failed_output_artifacts_or_attachment_still_complete_and_ack(
        output, artifact_error, attachment_code):
    peer = Peer().active()
    result = peer.finish(output=output, artifact_error=artifact_error, attachment_code=attachment_code)
    assert [r["type"] for r in peer.requests] == ["complete", "acknowledge-terminated"]
    assert peer.controller.failure is not None
    saved = peer.controller.failure
    peer.controller.client_closed(0)
    assert peer.controller.closed and peer.controller.failure is saved
    assert peer.controller.result == result
    assert sum(e.kind == "retain-partial-artifacts" for e in peer.effects) == 1


@pytest.mark.parametrize("failure", ["channel", "Envoy-exit", "recording-plan"])
def test_C2_S021_capture_failure_requests_Reploy_termination_without_abandoning_result(failure):
    peer = Peer().active()
    op = peer.controller.operation
    op.execute("op-17")
    op.started("op-17")
    peer.accept(peer.controller.fail(failure, "precise original error"))
    assert not peer.controller.can_record and not peer.controller.lifecycle.can_record
    assert peer.controller.failure.operation_id == "op-17"
    assert peer.requests[-1]["type"] == "terminate"
    assert peer.controller.lifecycle.state is ReployState.ACTIVE
    assert peer.accept(peer.controller.fail("later", "later message")) == []
    peer.finish(cause="controller-terminate")
    peer.controller.client_closed(0)
    assert peer.controller.closed
    assert peer.controller.failure.code == failure
    assert [r["type"] for r in peer.requests] == ["terminate", "complete", "acknowledge-terminated"]


def test_C2_S021_termination_requires_grant_and_never_creates_an_acceptance_event():
    peer = Peer()
    peer.broker()
    peer.accept(peer.controller.attachment_connected(now=peer.now))
    peer.opened(operations=["input", "complete"])
    peer.emit("ready")
    peer.controller.lifecycle.envoy_ready()
    peer.accept(peer.controller.fail("channel", "connection lost"))
    assert peer.requests == [] and peer.controller.failure.code == "channel"
    assert peer.controller.lifecycle.state is ReployState.ACTIVE
    assert not peer.controller.can_record
    result = peer.finish()
    peer.controller.client_closed(0)
    assert [r["type"] for r in peer.requests] == ["complete", "acknowledge-terminated"]
    assert peer.controller.result == result and peer.controller.closed
    assert peer.controller.failure.code == "channel"


@pytest.mark.parametrize("phase", ["before-ready", "active", "terminating"])
def test_C2_S022_failed_attachment_launch_retains_no_reader_fact(phase):
    peer = Peer()
    peer.broker()
    peer.opened()
    if phase in {"active", "terminating"}:
        peer.emit("ready")
    if phase == "terminating":
        peer.emit("terminating", cause="controller-terminate")
    peer.accept(peer.controller.attachment_failed("exec failed precisely"))
    if phase == "before-ready":
        peer.emit("ready")
    if phase != "terminating":
        peer.emit("terminating", cause="controller-terminate")
    effects = peer.emit("workload-outputs-finalized", status="drained")
    assert "finalize-artifacts" in effects
    peer.accept(peer.controller.artifacts_finalized())
    result = deepcopy(RESULT)
    result["cause"] = "controller-terminate"
    peer.emit("terminated", result=result)
    peer.controller.client_closed(0)
    assert peer.controller.closed and peer.controller.failure.code == "attachment-start"
    assert not any(e.kind == "bootstrap-envoy" for e in peer.effects)


@pytest.mark.parametrize("code", [0, 1, -9])
def test_C2_S022_early_attachment_end_is_retained_until_public_termination(code):
    peer = Peer().active()
    peer.accept(peer.controller.attachment_ended(code))
    assert peer.controller.lifecycle.state is ReployState.ACTIVE
    assert not peer.controller.can_record
    cause = "controller-terminate" if code else "workload-exit"
    if code:
        assert peer.controller.failure.code == "attachment-exit"
        assert peer.requests[-1]["type"] == "terminate"
    else:
        assert peer.controller.failure is None and peer.requests == []
        peer.emit("workload-exit", status={"kind": "exited", "code": 0})
    peer.emit("terminating", cause=cause)
    assert peer.emit("workload-outputs-finalized", status="drained") == ["finalize-artifacts"]
    peer.accept(peer.controller.artifacts_finalized())
    result = deepcopy(RESULT)
    result["cause"] = cause
    peer.emit("terminated", result=result)
    assert peer.controller.result == result


@pytest.mark.parametrize("state", [OperationState.STARTING, OperationState.RUNNING,
                                  OperationState.GATED, OperationState.CONTINUING,
                                  OperationState.FINALIZING])
def test_C2_S023_operation_cancel_has_exact_identity_and_does_not_terminate_Reploy(state):
    peer = Peer().active()
    op = peer.controller.operation
    op.execute("op-17")
    op.state = state
    assert peer.accept(peer.controller.cancel("op-17")) == ["cancel-operation"]
    assert peer.effects[-1].argv == ("op-17",)
    assert op.state is OperationState.CANCELLING
    assert peer.requests == []
    op.terminal_result("op-17", "cancelled")
    assert op.state is OperationState.IDLE and op.active_id is None


@pytest.mark.parametrize("ready,identity", [(False, "op-17"), (True, "other")])
def test_C2_S023_cancel_rejects_closed_admission_and_wrong_identity(ready, identity):
    peer = Peer().active()
    peer.controller.operation.execute("op-17")
    if not ready:
        peer.controller.lifecycle.stop_recording()
    effects = peer.controller.cancel(identity)
    assert not any(e.kind == "cancel-operation" for e in effects)
    assert peer.controller.failure.code == "operation-cancel"


def test_C2_S023_ordinary_terminal_result_is_representable_after_cancel():
    peer = Peer().active()
    op = peer.controller.operation
    op.execute("op-17")
    op.started("op-17")
    peer.controller.cancel("op-17")
    op.terminal_result("op-17", "completed")
    assert op.state is OperationState.IDLE
    # C2.3 owns the serialized inspection/cancel winner and full crossings.


def test_C2_S024_request_failure_preserves_result_without_false_ack_or_close():
    peer = Peer().active()
    peer.accept(peer.controller.request_failed("broken client stdin"))
    peer.finish(cause="controller-terminate")
    assert peer.requests == []
    peer.controller.client_closed(0)
    assert not peer.controller.closed and peer.controller.failure.code == "client-request"
    assert peer.controller.result is not None


def test_C2_S025_all_well_formed_diagnostics_and_client_error_are_retained_separately():
    peer = Peer().active()
    first = event("diagnostic", code="future_code", message="precise error with context")
    second = event("client-error", code="future_fatal", message="fatal source text")
    peer.accept(peer.controller.on_event(first, now=peer.now))
    peer.controller.stderr(b"client-only\x00\xff")
    peer.accept(peer.controller.on_event(second, now=peer.now))
    assert peer.events.getvalue() == first + second
    assert peer.stderr.getvalue() == b"client-only\x00\xff"
    assert peer.controller.failure.code == "future_fatal"
    assert peer.controller.failure.message == "fatal source text"
    assert not peer.controller.can_record


@pytest.mark.parametrize("bound", [0, 1, 17, 4096])
def test_C2_S026_complete_stderr_stream_and_bounded_memory_tail(tmp_path, bound):
    path = tmp_path / "client.stderr"
    data = bytes(range(256)) * 80
    with path.open("wb") as sink:
        peer = Peer(stderr_sink=sink, tail=bound)
        for offset in range(0, len(data), 137):
            assert peer.controller.stderr(data[offset:offset + 137]) == ()
            assert len(peer.controller.stderr_tail) <= bound
    assert path.read_bytes() == data
    assert peer.controller.stderr_tail == (data[-bound:] if bound else b"")
    assert peer.controller.stderr_bytes_observed == len(data)
    assert peer.events.getvalue() == b""


@pytest.mark.parametrize("bound", [-1, True, 1.5, None])
def test_C2_S026_invalid_tail_bound_is_not_an_implicit_default(bound):
    with pytest.raises(ValueError):
        ControllerSession(event_sink=io.BytesIO(), stderr_sink=io.BytesIO(), stderr_tail_limit=bound)


def test_C2_S026_event_and_stderr_sinks_must_be_distinct():
    sink = io.BytesIO()
    with pytest.raises(ValueError):
        ControllerSession(event_sink=sink, stderr_sink=sink, stderr_tail_limit=1)


class ShortSink(io.BytesIO):
    def write(self, data):
        return super().write(data[:3])


@pytest.mark.parametrize("stream", ["events", "stderr"])
def test_C2_S027_short_sink_writes_preserve_every_byte(stream):
    sink = ShortSink()
    peer = Peer(event_sink=sink if stream == "events" else None,
                stderr_sink=sink if stream == "stderr" else None).active()
    line = event("diagnostic", code="future", message="preserve every byte")
    if stream == "events":
        peer.controller.on_event(line, now=peer.now)
        assert sink.getvalue() == line
    else:
        peer.controller.stderr(b"full stderr, including\x00\xff")
        assert sink.getvalue() == b"full stderr, including\x00\xff"
    assert peer.controller.failure is None


@pytest.mark.parametrize("returned", [0, -1, None, True, "3", 10**9])
def test_C2_S028_invalid_write_counts_are_retained_failures(returned):
    class BadSink(io.BytesIO):
        def write(self, data):
            return returned
    peer = Peer(stderr_sink=BadSink()).active()
    peer.accept(peer.controller.stderr(b"observed stderr"))
    assert peer.controller.failure.code == "stderr-retention"
    assert not peer.controller.can_record
    assert [x["type"] for x in peer.requests] == ["terminate"]


def test_C2_S028_failed_result_retention_never_acknowledges_unretained_result():
    class FailingSink(io.BytesIO):
        def write(self, data):
            super().write(data[:7])
            raise OSError("disk full")
    peer = Peer(event_sink=FailingSink()).active()
    peer.finish()
    assert peer.controller.failure.code == "event-retention"
    assert b"acknowledge-terminated" not in b"".join(e.line or b"" for e in peer.effects)
    assert peer.controller.result is None
    assert peer.events.getvalue()
    peer.controller.client_closed(0)
    assert not peer.controller.closed


@pytest.mark.parametrize("line", [b"bad\n", b"\xff\n", b"{}\n", b'{"schema":0,"schema":1}\n',
                                  b"\x00\n", b"{}", b"x" * ((1 << 20) + 1)])
def test_C2_S029_malformed_public_input_latches_failure_without_recording_it_as_valid(line):
    peer = Peer().active()
    peer.accept(peer.controller.on_event(line, now=peer.now))
    assert peer.controller.failure.code == "client-schema"
    assert peer.events.getvalue() == b""
    assert not peer.controller.can_record


def test_C2_S030_failure_and_project_context_survive_later_successful_teardown():
    peer = Peer().active()
    peer.accept(peer.controller.fail("recording-plan", "assertion failed"))
    saved = peer.controller.failure
    with pytest.raises(FrozenInstanceError):
        saved.code = "changed"
    copied = peer.controller.context
    copied["recording"] = "changed"
    peer.finish(cause="controller-terminate")
    peer.controller.client_closed(0)
    assert peer.controller.context == {"recording": "demo"}
    assert peer.controller.failure is saved
    assert peer.controller.closed and not peer.controller.can_record


def test_C2_S031_failed_structured_cleanup_cannot_be_overridden_by_zero_client_exit():
    peer = Peer().active()
    result = peer.finish(result_changes={"cleanup_status": {"kind": "failed", "message": "cleanup error"},
                                        "recovery_action": "retry-cleanup"})
    peer.controller.client_closed(0)
    assert peer.controller.closed and peer.controller.failure.code == "session-result"
    assert peer.controller.result == result
    assert json.loads(peer.events.getvalue())["result"]["cleanup_status"]["message"] == "cleanup error"


def test_C2_S025_retained_event_bytes_are_not_reencoded():
    peer = Peer().active()
    value = {"message": "original bytes", "code": "future", "type": "diagnostic", "schema": CLIENT_SCHEMA}
    line = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    peer.controller.on_event(line, now=peer.now)
    assert peer.events.getvalue() == line
    assert peer.controller.failure is None


def test_C2_S018_duplicate_terminated_is_retained_but_never_acknowledged_twice():
    peer = Peer().active()
    result = peer.finish()
    peer.emit("terminated", result=result)
    assert [r["type"] for r in peer.requests].count("acknowledge-terminated") == 1
    assert len(peer.events.getvalue().splitlines()) == 2
    assert peer.controller.failure.code == "client-lifecycle"
    assert peer.controller.result == result


def test_C2_S016_result_sink_is_flushed_before_acknowledgement_effect():
    class FlushSink(io.BytesIO):
        flushed = False

        def flush(self):
            self.flushed = True
            return super().flush()

    sink = FlushSink()
    peer = Peer(event_sink=sink).active()
    peer.finish()
    assert sink.flushed
    assert peer.requests[-1]["type"] == "acknowledge-terminated"


def test_C2_S028_event_flush_failure_cannot_acknowledge_or_close_cleanly():
    class FlushFailure(io.BytesIO):
        def flush(self):
            raise OSError("flush failed precisely")

    peer = Peer(event_sink=FlushFailure()).active()
    peer.finish()
    assert peer.controller.failure.code == "event-retention"
    assert peer.controller.failure.message == "flush failed precisely"
    assert [r["type"] for r in peer.requests] == ["complete"]
    peer.controller.client_closed(0)
    assert not peer.controller.closed


@pytest.mark.parametrize("closed_method", ["execute", "continue_operation", "finalize", "cancel", "shutdown"])
def test_C2_S021_failed_capture_closes_every_outgoing_operation_request(closed_method):
    peer = Peer().active()
    op = peer.controller.operation
    if closed_method != "execute":
        op.execute("op-17")
        op.started("op-17")
    if closed_method == "continue_operation":
        op.gate_ready("op-17", "gate-1")
    if closed_method == "shutdown":
        op.terminal_result("op-17", "completed")
    peer.controller.fail("capture-error", "stop recording")
    args = {"execute": ("next",), "continue_operation": ("op-17", "gate-1"),
            "finalize": ("op-17",), "cancel": ("op-17",), "shutdown": ("done",)}
    with pytest.raises(ControllerLifecycleError):
        getattr(op, closed_method)(*args[closed_method])


def test_C2_S021_failure_before_readiness_waits_for_public_authority_to_terminate():
    peer = Peer()
    peer.accept(peer.controller.fail("recording-plan", "invalid plan"))
    assert peer.requests == []
    peer.broker()
    peer.accept(peer.controller.attachment_connected(now=peer.now))
    peer.opened()
    assert peer.requests == []
    peer.emit("ready")
    assert [r["type"] for r in peer.requests] == ["terminate"]
    assert not any(e.kind == "bootstrap-envoy" for e in peer.effects)
    assert not peer.controller.can_record


def test_C2_S022_unavailable_attachment_after_output_finalization_releases_artifact_close():
    peer = Peer()
    peer.broker()
    peer.opened()
    peer.emit("ready")
    peer.emit("terminating", cause="controller-terminate")
    assert peer.emit("workload-outputs-finalized", status="drained") == []
    effects = peer.accept(peer.controller.attachment_failed("launch never succeeded"))
    assert effects == ["retain-partial-artifacts", "finalize-artifacts"]
    assert peer.controller.failure.code == "attachment-start"


@pytest.mark.parametrize("kind,change", [
    ("runtime", {"runtime_observation_status": {"kind": "lost", "reason": "runtime lost"}}),
    ("controller", {"controller_finalization_status": {"kind": "not-completed", "reason": "artifact error"}}),
])
def test_C2_S031_structured_failure_fields_survive_successful_client_close(kind, change):
    peer = Peer().active()
    result = peer.finish(result_changes=change)
    peer.controller.client_closed(0)
    assert peer.controller.failure.code == "session-result"
    assert peer.controller.result == result
    assert peer.controller.closed


def test_C2_S023_cancelled_gate_clears_only_the_matching_operation():
    peer = Peer().active()
    op = peer.controller.operation
    op.execute("op-17")
    op.started("op-17")
    op.gate_ready("op-17", "gate-1")
    peer.controller.cancel("op-17")
    assert op.gate_id == "gate-1"
    op.terminal_result("op-17", "cancelled")
    assert op.state is OperationState.IDLE and op.active_id is None and op.gate_id is None


def test_C2_S032_duplicate_client_start_never_starts_another_process():
    peer = Peer()
    peer.accept(peer.controller.start())
    assert sum(e.kind == "launch-client" for e in peer.effects) == 1
    assert peer.controller.failure.code == "client-start"


@pytest.mark.parametrize("before_workload_exit", [False, True])
def test_C2_S033_clean_attachment_EOF_before_public_termination_is_not_a_failure(before_workload_exit):
    peer = Peer().active()
    if not before_workload_exit:
        peer.emit("workload-exit", status={"kind": "exited", "code": 0})
    peer.accept(peer.controller.attachment_ended(0))
    assert peer.controller.failure is None
    assert not peer.controller.can_record and not peer.controller.lifecycle.can_record
    assert peer.controller.lifecycle.state is ReployState.ACTIVE
    assert peer.requests == []
    if before_workload_exit:
        peer.emit("workload-exit", status={"kind": "exited", "code": 0})
    peer.emit("terminating", cause="workload-exit")
    assert peer.emit("workload-outputs-finalized", status="drained") == ["finalize-artifacts"]
    peer.accept(peer.controller.artifacts_finalized())
    peer.emit("terminated", result=deepcopy(RESULT))
    peer.controller.client_closed(0)
    assert peer.controller.closed and peer.controller.failure is None
    assert [r["type"] for r in peer.requests] == ["complete", "acknowledge-terminated"]
