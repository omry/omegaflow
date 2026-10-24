from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import permutations
import json
from pathlib import Path

import pytest

from omegaflow.controller_lifecycle import (
    ControllerLifecycleError, OperationLifecycle, OperationState,
    ReployLifecycle, ReployState,
)
from omegaflow.reploy_codecs import CLIENT_SCHEMA, decode_request, encode_event


FIXTURES = Path(__file__).parent / "fixtures/reploy-codecs/client-v1-events.jsonl"
EVENTS = [json.loads(line) for line in FIXTURES.read_bytes().splitlines()]
OPENED = next(e for e in EVENTS if e["type"] == "opened")
RESULT = next(e["result"] for e in EVENTS if e["type"] == "terminated")


def event(kind, **payload):
    return {"schema": CLIENT_SCHEMA, "type": kind, **payload}


class FakeClient:
    """Execute effects at the same public boundary as the future client owner."""

    def __init__(self):
        self.session = ReployLifecycle()
        self.effects = []
        self.now = 100.0
        self.requests = []

    def accept(self, effects):
        self.effects.extend(effects)
        for effect in effects:
            if effect.kind == "send-request":
                self.requests.append(decode_request(effect.line))
        return [e.kind for e in effects]

    def emit(self, kind, **payload):
        return self.accept(self.session.on_event(encode_event(event(kind, **payload)), now=self.now))

    def broker(self, socket="/opaque/socket"):
        return self.emit("broker-ready", terminal_socket=socket)

    def opened(self, **updates):
        value = deepcopy(OPENED)
        value.update(updates)
        return self.accept(self.session.on_event(encode_event(value), now=self.now))

    def attach(self):
        return self.accept(self.session.attachment_connected(now=self.now))

    def active(self):
        self.broker()
        self.attach()
        self.opened()
        self.emit("ready")
        self.session.envoy_ready()
        return self

    def terminate(self, *, cause="workload-exit", output="drained", eof_first=False):
        if cause == "workload-exit":
            self.emit("workload-exit", status={"kind": "exited", "code": 0})
        self.emit("terminating", cause=cause)
        if eof_first:
            self.accept(self.session.attachment_ended())
        payload = {"status": output}
        if output == "failed":
            payload["reason"] = "Drain expired."
        self.emit("workload-outputs-finalized", **payload)
        if not eof_first:
            self.accept(self.session.attachment_ended())
        self.accept(self.session.artifacts_finalized())
        result = deepcopy(RESULT)
        result["cause"] = cause
        result["workload_output_finalization_status"] = {"kind": output}
        if output == "failed":
            result["workload_output_finalization_status"]["reason"] = "Drain expired."
        return result


def test_C2_S001_public_peer_ordinary_session_and_operation():
    peer = FakeClient().active()
    operation = OperationLifecycle(peer.session)
    operation.execute("op-1")
    operation.started("op-1")
    operation.terminal_result("op-1", "completed")
    assert operation.state is OperationState.IDLE and operation.active_id is None
    assert peer.session.can_record
    result = peer.terminate()
    assert peer.emit("terminated", result=result) == ["session-result"]
    assert peer.session.result == result
    assert [x["type"] for x in peer.requests] == ["complete"]
    assert not peer.session.can_record
    assert [e.kind for e in peer.effects] == [
        "attach", "bootstrap-envoy", "session-terminating", "finalize-artifacts",
        "send-request", "session-result",
    ]
    # C2.2 owns acknowledgement and clean broker closure; neither is claimed here.
    assert peer.session.state is ReployState.RESULT


@pytest.mark.parametrize("order", [
    ("attach", "opened", "ready"), ("opened", "attach", "ready"),
    ("opened", "ready", "attach"),
])
def test_C2_S002_attachment_confirmation_and_ready_crossing(order):
    peer = FakeClient()
    assert peer.broker() == ["attach"]
    for action in order:
        assert not peer.session.can_record
        getattr(peer, action)() if action != "ready" else peer.emit("ready")
    assert [e.kind for e in peer.effects].count("bootstrap-envoy") == 1
    assert not peer.session.can_record
    peer.session.envoy_ready()
    assert peer.session.can_record


@pytest.mark.parametrize("socket", ["/tmp/socket with spaces", "$(touch /tmp/injected);x", "--socket=elsewhere", "a\nb", "路径"])
def test_C2_S003_socket_is_one_opaque_argv_value(socket):
    peer = FakeClient()
    peer.broker(socket)
    effect = peer.effects[0]
    assert effect.argv == ("reploy-session-client", "attach", "--socket", socket)
    assert effect.deadline == 110
    with pytest.raises(FrozenInstanceError):
        effect.argv = ()


@pytest.mark.parametrize("offset,valid", [(0, True), (9.999, True), (10, False), (10.001, False)])
def test_C2_S004_original_attachment_deadline(offset, valid):
    peer = FakeClient()
    peer.broker()
    peer.opened()
    peer.now += offset
    if valid:
        peer.attach()
    else:
        with pytest.raises(ControllerLifecycleError, match="deadline"):
            peer.attach()
        assert peer.session.state is ReployState.FAILED
    assert peer.session.attachment_deadline == 110


def test_C2_S004_confirmation_stops_attachment_deadline_not_an_unrelated_startup_timer():
    peer = FakeClient()
    peer.broker()
    peer.attach()
    peer.now += 100
    peer.opened()
    peer.emit("ready")
    assert peer.effects[-1].kind == "bootstrap-envoy"
    assert peer.session.attachment_deadline == 110


@pytest.mark.parametrize("now", [True, None, "100", float("nan"), float("inf"), float("-inf"), 99])
def test_C2_S004_invalid_or_backwards_clock_is_fatal(now):
    peer = FakeClient()
    peer.broker()
    with pytest.raises(ControllerLifecycleError):
        peer.session.attachment_connected(now=now)
    assert peer.session.state is ReployState.FAILED


@pytest.mark.parametrize("line", [b"{}\n", b"x", b"\xff\n", b'{"schema":"wrong"}\n', b'{"type":"ready","type":"ready"}\n'])
def test_C2_S005_malformed_public_event_never_authorizes_an_action(line):
    peer = FakeClient()
    with pytest.raises(ControllerLifecycleError):
        peer.session.on_event(line, now=100)
    assert peer.effects == [] and peer.session.state is ReployState.FAILED


@pytest.mark.parametrize("kind,payload", [
    ("opened", {k: v for k, v in OPENED.items() if k not in {"schema", "type"}}),
    ("ready", {}), ("diagnostic", {"code": "future_code", "message": "Notice."}),
    ("terminating", {"cause": "host-cancel"}),
    ("workload-exit", {"status": {"kind": "exited", "code": 0}}),
])
def test_C2_S005_broker_ready_is_first(kind, payload):
    peer = FakeClient()
    with pytest.raises(ControllerLifecycleError, match="first"):
        peer.emit(kind, **payload)
    assert peer.effects == []


@pytest.mark.parametrize("order", [p for p in permutations(("broker", "opened", "ready")) if p != ("broker", "opened", "ready")])
def test_C2_S005_bad_startup_order_is_fatal(order):
    peer = FakeClient()
    with pytest.raises(ControllerLifecycleError):
        for action in order:
            getattr(peer, action)() if action != "ready" else peer.emit("ready")
    assert not peer.session.can_record
    assert not any(e.kind == "bootstrap-envoy" for e in peer.effects)


@pytest.mark.parametrize("duplicate", ["broker", "opened", "ready", "attach", "envoy-ready"])
def test_C2_S005_duplicate_startup_observation_fails(duplicate):
    peer = FakeClient().active()
    with pytest.raises(ControllerLifecycleError):
        if duplicate == "envoy-ready":
            peer.session.envoy_ready()
        elif duplicate == "ready":
            peer.emit("ready")
        else:
            getattr(peer, duplicate)()
    assert not peer.session.can_record
    assert [e.kind for e in peer.effects].count("bootstrap-envoy") == 1


def test_C2_S006_missing_input_grant_cannot_bootstrap():
    peer = FakeClient()
    peer.broker()
    peer.attach()
    peer.opened(operations=["complete"])
    with pytest.raises(ControllerLifecycleError, match="input grant"):
        peer.emit("ready")
    assert [e.kind for e in peer.effects] == ["attach"]


@pytest.mark.parametrize("phase", ["initial", "attached", "opened", "reploy-ready"])
def test_C2_S006_no_execute_before_Envoy_barrier(phase):
    peer = FakeClient()
    if phase != "initial":
        peer.broker()
        peer.attach()
    if phase in {"opened", "reploy-ready"}:
        peer.opened()
    if phase == "reploy-ready":
        peer.emit("ready")
    operation = OperationLifecycle(peer.session)
    with pytest.raises(ControllerLifecycleError, match="not ready"):
        operation.execute("op-1")
    assert operation.active_id is None


def test_C2_S006_opened_result_copies_do_not_mutate_authority():
    peer = FakeClient().active()
    peer.session.opened["operations"].clear()
    peer.session.opened["endpoints"][0]["port"] = 1
    assert peer.session.opened == OPENED
    result = peer.terminate()
    peer.emit("terminated", result=result)
    peer.session.result["workload_status"]["code"] = 8
    result["cause"] = "host-cancel"
    assert peer.session.result == RESULT


@pytest.mark.parametrize("eof_first", [True, False])
@pytest.mark.parametrize("output", ["drained", "failed"])
def test_C2_S007_artifact_completion_waits_for_both_output_and_attachment(eof_first, output):
    peer = FakeClient().active()
    result = peer.terminate(output=output, eof_first=eof_first)
    assert [e.kind for e in peer.effects].count("finalize-artifacts") == 1
    assert peer.requests == [{"schema": CLIENT_SCHEMA, "type": "complete"}]
    peer.emit("terminated", result=result)
    assert peer.session.result["workload_output_finalization_status"]["kind"] == output


@pytest.mark.parametrize("phase", ["active", "terminating", "output-finalized", "attachment-ended"])
def test_C2_S007_no_premature_complete(phase):
    peer = FakeClient().active()
    if phase != "active":
        peer.emit("terminating", cause="controller-terminate")
    if phase == "output-finalized":
        peer.emit("workload-outputs-finalized", status="drained")
    if phase == "attachment-ended":
        peer.accept(peer.session.attachment_ended())
    with pytest.raises(ControllerLifecycleError):
        peer.session.artifacts_finalized()
    assert peer.requests == []


def test_C2_S007_duplicate_complete_never_sends_twice():
    peer = FakeClient().active()
    peer.terminate()
    with pytest.raises(ControllerLifecycleError):
        peer.accept(peer.session.artifacts_finalized())
    assert len(peer.requests) == 1


def test_C2_S007_complete_requires_grant():
    peer = FakeClient()
    peer.broker()
    peer.attach()
    peer.opened(operations=["input"])
    peer.emit("ready")
    peer.emit("terminating", cause="controller-terminate")
    peer.emit("workload-outputs-finalized", status="drained")
    peer.accept(peer.session.attachment_ended())
    with pytest.raises(ControllerLifecycleError, match="complete grant"):
        peer.session.artifacts_finalized()
    assert peer.requests == []


@pytest.mark.parametrize("with_opened", [True, False])
def test_C2_S008_startup_failure_does_not_wait_for_normal_finalization(with_opened):
    peer = FakeClient()
    peer.broker()
    if with_opened:
        peer.opened()
    result = deepcopy(RESULT)
    result.update(cause="startup-failure", controller_finalization_status={"kind": "startup-failed"})
    assert peer.emit("terminated", result=result) == ["session-result"]
    assert peer.session.result == result and peer.requests == []
    assert not peer.session.can_record


@pytest.mark.parametrize("field,value", [
    ("cause", "host-cancel"), ("workload_status", {"kind": "exited", "code": 2}),
    ("workload_output_finalization_status", {"kind": "failed", "reason": "Changed."}),
])
def test_C2_S008_result_cannot_rewrite_observations(field, value):
    peer = FakeClient().active()
    result = peer.terminate()
    result[field] = value
    with pytest.raises(ControllerLifecycleError, match="changed"):
        peer.emit("terminated", result=result)
    assert peer.session.result is None


def test_C2_S008_workload_exit_cause_requires_prior_exit():
    peer = FakeClient().active()
    with pytest.raises(ControllerLifecycleError):
        peer.emit("terminating", cause="workload-exit")


def test_C2_S008_other_cause_can_precede_workload_exit():
    peer = FakeClient().active()
    peer.emit("terminating", cause="host-cancel")
    peer.emit("workload-exit", status={"kind": "terminated", "reason": "Signal."})
    assert peer.session.state is ReployState.TERMINATING


def test_C2_S008_diagnostic_never_advances_or_bootstraps():
    peer = FakeClient()
    peer.broker()
    assert peer.emit("diagnostic", code="future_code", message="Notice.") == []
    assert peer.session.state is ReployState.WAIT_OPENED
    with pytest.raises(ControllerLifecycleError):
        peer.emit("client-error", code="future_failure", message="Failed.")
    assert peer.session.state is ReployState.FAILED


def operation():
    return OperationLifecycle(FakeClient().active().session)


@pytest.mark.parametrize("gates", [0, 1, 3])
def test_C2_S009_ordinary_operation_and_multiple_gates(gates):
    core = operation()
    for number in range(3):
        op = f"op-{number}"
        core.execute(op)
        assert core.state is OperationState.STARTING
        core.started(op)
        for gate in range(gates):
            identity = f"gate-{gate}"
            core.gate_ready(op, identity)
            core.continue_operation(op, identity)
            assert core.state is OperationState.CONTINUING
            core.continued(op, identity)
        core.terminal_result(op, "completed")
        assert core.state is OperationState.IDLE
        assert core.active_id is None and core.gate_id is None


@pytest.mark.parametrize("continued", [False, True])
def test_C2_S010_gate_interruption(continued):
    core = operation()
    core.execute("op")
    core.started("op")
    core.gate_ready("op", "gate")
    if continued:
        core.continue_operation("op", "gate")
    core.gate_interrupted("op", "gate")
    assert core.state is OperationState.RUNNING and core.gate_id is None


def test_C2_S010_gate_cannot_be_reused_in_same_operation():
    core = operation()
    core.execute("op")
    core.started("op")
    core.gate_ready("op", "gate")
    core.gate_interrupted("op", "gate")
    with pytest.raises(ControllerLifecycleError):
        core.gate_ready("op", "gate")


@pytest.mark.parametrize("state", ["starting", "running", "gated", "continuing", "finalizing"])
@pytest.mark.parametrize("shell_ended", [False, True])
def test_C2_S011_failed_result_returns_to_idle(state, shell_ended):
    core = in_state(state)
    core.terminal_result("op", "failed", shell_ended=shell_ended)
    assert core.state is OperationState.IDLE and core.shell_ended is shell_ended


def in_state(state):
    core = operation()
    core.execute("op")
    if state == "starting":
        return core
    core.started("op")
    if state in {"gated", "continuing"}:
        core.gate_ready("op", "gate")
    if state == "continuing":
        core.continue_operation("op", "gate")
    if state == "finalizing":
        core.finalize("op")
    return core


@pytest.mark.parametrize("state", ["running", "gated", "continuing"])
@pytest.mark.parametrize("result", ["finalized", "completed", "failed"])
def test_C2_S011_finalize_accepts_committed_result(state, result):
    core = in_state(state)
    core.finalize("op")
    if result == "completed" and state in {"gated", "continuing"}:
        with pytest.raises(ControllerLifecycleError):
            core.terminal_result("op", result)
        assert core.state is OperationState.FAILED
    else:
        core.terminal_result("op", result)
        assert core.state is OperationState.IDLE


@pytest.mark.parametrize("state", ["gated", "continuing"])
def test_C2_S011_shell_ended_completed_result_requires_resolved_gate(state):
    core = in_state(state)
    with pytest.raises(ControllerLifecycleError):
        core.terminal_result("op", "completed", shell_ended=True)
    assert core.state is OperationState.FAILED and core.gate_id == "gate"


@pytest.mark.parametrize("state", ["gated", "continuing"])
@pytest.mark.parametrize("shell_ended", [False, True])
def test_C2_S011_finalizing_completed_result_requires_resolved_gate(state, shell_ended):
    core = in_state(state)
    core.finalize("op")
    with pytest.raises(ControllerLifecycleError):
        core.terminal_result("op", "completed", shell_ended=shell_ended)
    assert core.state is OperationState.FAILED and core.gate_id == "gate"


def test_C2_S011_shell_ended_completed_result_is_accepted_without_gate():
    core = in_state("running")
    core.terminal_result("op", "completed", shell_ended=True)
    assert core.state is OperationState.IDLE and core.shell_ended is True


@pytest.mark.parametrize("method,args", [
    ("started", ("wrong",)), ("gate_ready", ("wrong", "gate")),
    ("continue_operation", ("wrong", "gate")), ("continued", ("wrong", "gate")),
    ("gate_interrupted", ("wrong", "gate")), ("finalize", ("wrong",)),
    ("terminal_result", ("wrong", "completed")),
])
def test_C2_S012_wrong_operation_identity_never_changes_to_success(method, args):
    core = in_state("running")
    with pytest.raises(ControllerLifecycleError, match="identity"):
        getattr(core, method)(*args)
    assert core.state is OperationState.FAILED and core.active_id == "op"


@pytest.mark.parametrize("method", ["continue_operation", "continued", "gate_interrupted"])
def test_C2_S012_wrong_gate_identity(method):
    core = in_state("continuing" if method == "continued" else "gated")
    with pytest.raises(ControllerLifecycleError):
        getattr(core, method)("op", "wrong")
    assert core.state is OperationState.FAILED


@pytest.mark.parametrize("state,result", [("starting", "completed"), ("gated", "completed"), ("continuing", "completed"), ("running", "finalized"), ("running", "cancelled"), ("running", "unknown")])
def test_C2_S012_out_of_order_results_fail(state, result):
    core = in_state(state)
    with pytest.raises(ControllerLifecycleError):
        core.terminal_result("op", result)
    assert core.state is OperationState.FAILED


@pytest.mark.parametrize("shell_ended", [1, 0, "true", None])
def test_C2_S012_shell_flag_is_boolean(shell_ended):
    core = in_state("running")
    with pytest.raises(ControllerLifecycleError):
        core.terminal_result("op", "completed", shell_ended=shell_ended)


@pytest.mark.parametrize("close_first", [True, False])
@pytest.mark.parametrize("shell_end", [True, False])
def test_C2_S013_closed_requires_telemetry_and_terminal_EOF(close_first, shell_end):
    core = operation()
    reason = "shell_ended" if shell_end else "recording-end"
    if shell_end:
        core.execute("op")
        core.started("op")
        core.terminal_result("op", "completed", shell_ended=True)
    else:
        core.shutdown(reason)
    core.draining(reason)
    if close_first:
        core.closed(reason)
        assert core.state is OperationState.DRAINING
        core.terminal_eof()
    else:
        core.terminal_eof()
        assert core.state is OperationState.DRAINING
        core.closed(reason)
    assert core.state is OperationState.CLOSED
    with pytest.raises(ControllerLifecycleError):
        core.execute("next")


def test_C2_S013_idle_shell_exit_needs_no_operation_result():
    core = operation()
    core.draining("shell_ended")
    core.closed("shell_ended")
    core.terminal_eof()
    assert core.state is OperationState.CLOSED


@pytest.mark.parametrize("observation", ["closed", "terminal_eof"])
def test_C2_S013_no_terminal_closure_before_drain(observation):
    core = operation()
    with pytest.raises(ControllerLifecycleError):
        core.closed("shell_ended") if observation == "closed" else core.terminal_eof()


def test_C2_S013_drain_and_closed_reasons_match():
    core = operation()
    core.shutdown("recording-end")
    core.draining("recording-end")
    with pytest.raises(ControllerLifecycleError):
        core.closed("shell_ended")


def test_C2_S014_no_actions_after_Reploy_termination_begins():
    peer = FakeClient().active()
    core = OperationLifecycle(peer.session)
    peer.emit("terminating", cause="host-cancel")
    with pytest.raises(ControllerLifecycleError):
        core.execute("op")


def test_C2_S014_Envoy_success_is_not_Reploy_success():
    peer = FakeClient().active()
    core = OperationLifecycle(peer.session)
    core.execute("op")
    core.started("op")
    core.terminal_result("op", "completed")
    assert peer.session.result is None
    result = peer.terminate(cause="host-cancel", output="failed")
    peer.emit("terminated", result=result)
    assert peer.session.result["cause"] == "host-cancel"
    assert peer.session.result["workload_output_finalization_status"]["kind"] == "failed"


@pytest.mark.parametrize("result", [None, [], {}, 1, True])
def test_C2_S012_terminal_result_type_is_not_an_escape_from_fail_closed(result):
    core = in_state("running")
    with pytest.raises(ControllerLifecycleError):
        core.terminal_result("op", result)
    assert core.state is OperationState.FAILED


def test_C2_S004_unrepresentable_monotonic_time_is_fatal():
    peer = FakeClient()
    peer.broker()
    with pytest.raises(ControllerLifecycleError):
        peer.session.check_attachment_deadline(1 << 10000)
    assert peer.session.state is ReployState.FAILED


def test_C2_S004_diagnostic_does_not_reset_attachment_deadline():
    peer = FakeClient()
    peer.broker()
    peer.now += 9
    peer.emit("diagnostic", code="notice", message="Still starting.")
    assert peer.session.attachment_deadline == 110
    with pytest.raises(ControllerLifecycleError, match="deadline"):
        peer.session.check_attachment_deadline(110)


@pytest.mark.parametrize("phase", ["broker", "opened", "active", "terminating"])
def test_C2_S008_final_output_cannot_skip_lifecycle_order(phase):
    peer = FakeClient()
    peer.broker()
    if phase != "broker":
        peer.attach()
        peer.opened()
    if phase in {"active", "terminating"}:
        peer.emit("ready")
    if phase == "terminating":
        peer.emit("terminating", cause="controller-terminate")
        peer.emit("workload-outputs-finalized", status="drained")
    with pytest.raises(ControllerLifecycleError):
        peer.emit("workload-outputs-finalized", status="drained")


@pytest.mark.parametrize("observation", ["result", "attachment", "artifacts"])
def test_C2_S008_premature_end_is_not_ordinary_success(observation):
    peer = FakeClient().active()
    with pytest.raises(ControllerLifecycleError):
        if observation == "result":
            peer.emit("terminated", result=deepcopy(RESULT))
        elif observation == "attachment":
            peer.session.attachment_ended()
        else:
            peer.session.artifacts_finalized()
    assert peer.session.state is ReployState.FAILED
    assert peer.session.result is None and peer.requests == []


@pytest.mark.parametrize("state", ["starting", "running", "gated", "continuing", "finalizing"])
def test_C2_S012_execute_never_replaces_an_active_operation(state):
    core = in_state(state)
    with pytest.raises(ControllerLifecycleError):
        core.execute("replacement")
    assert core.active_id == "op" and core.state is OperationState.FAILED


@pytest.mark.parametrize("identity", [None, "", 1, True])
def test_C2_S012_missing_operation_identity_is_rejected(identity):
    core = operation()
    with pytest.raises(ControllerLifecycleError):
        core.execute(identity)
    assert core.active_id is None


def test_C2_S013_shell_ended_result_forbids_next_execute_even_before_drain():
    core = in_state("running")
    core.terminal_result("op", "completed", shell_ended=True)
    with pytest.raises(ControllerLifecycleError):
        core.execute("next")


def test_C2_S014_stale_started_after_terminal_result_is_rejected():
    core = in_state("running")
    core.terminal_result("op", "completed")
    with pytest.raises(ControllerLifecycleError):
        core.started("op")
    assert core.state is OperationState.FAILED


@pytest.mark.parametrize("field", ["workload_status", "workload_output_finalization_status"])
@pytest.mark.parametrize("event_reason,result_reason", [(None, ""), ("", None), ("", ""), (None, None)])
def test_C2_S015_optional_empty_status_reason_does_not_replace_authoritative_evidence(field, event_reason, result_reason):
    peer = FakeClient().active()
    status = {"kind": "exited", "code": 0}
    if field == "workload_status" and event_reason is not None:
        status["reason"] = event_reason
    peer.emit("workload-exit", status=status)
    peer.emit("terminating", cause="workload-exit")
    output = {"status": "drained"}
    if field == "workload_output_finalization_status" and event_reason is not None:
        output["reason"] = event_reason
    peer.emit("workload-outputs-finalized", **output)
    peer.accept(peer.session.attachment_ended())
    peer.accept(peer.session.artifacts_finalized())
    result = deepcopy(RESULT)
    if result_reason is not None:
        result[field]["reason"] = result_reason
    assert peer.emit("terminated", result=result) == ["session-result"]
    assert peer.session.result == result
    assert ("reason" in peer.session.result[field]) == (result_reason is not None)


@pytest.mark.parametrize("field", ["workload_status", "workload_output_finalization_status"])
def test_C2_S015_nonempty_status_reason_change_remains_fatal(field):
    peer = FakeClient().active()
    result = peer.terminate(output="failed")
    result[field]["reason"] = "Changed meaningful diagnostic."
    with pytest.raises(ControllerLifecycleError, match="changed"):
        peer.emit("terminated", result=result)
    assert peer.session.result is None
