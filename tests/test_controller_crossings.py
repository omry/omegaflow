"""C2.3: deterministic controller observations, not actual C3 transports."""

from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from omegaflow.controller_lifecycle import ControllerLifecycleError, OperationState
from test_controller_session import Peer, RESULT


OP = "crossed-op"
GATE = "crossed-gate"
ROOT = Path(__file__).resolve().parent
LIFECYCLE = {"cancel": OperationState.CANCELLING, "finalize": OperationState.FINALIZING}
OBSERVATIONS = ("started", "gate_ready", "continued", "gate_interrupted")
CROSSINGS = [(c, e) for c in LIFECYCLE for e in OBSERVATIONS
             if (c, e) != ("finalize", "started")]


def at_phase(phase):
    peer = Peer().active()
    op = peer.controller.operation
    if phase == "idle":
        return peer, op
    op.execute(OP)
    if phase == "starting":
        return peer, op
    op.started(OP)
    if phase in {"gated", "continuing"}:
        op.gate_ready(OP, GATE)
    if phase == "continuing":
        op.continue_operation(OP, GATE)
    if phase == "finalizing":
        op.finalize(OP)
    return peer, op


def before_observation(kind):
    phase = {"started": "starting", "gate_ready": "running",
             "continued": "continuing", "gate_interrupted": "gated"}[kind]
    return at_phase(phase)


def select(peer, op, control):
    if control == "cancel":
        assert peer.accept(peer.controller.cancel(OP)) == ["cancel-operation"]
    else:
        op.finalize(OP)


def observe(op, kind, operation_id=OP, gate_id=GATE):
    args = (operation_id,) if kind == "started" else (operation_id, gate_id)
    getattr(op, kind)(*args)


@pytest.mark.parametrize("control", LIFECYCLE)
@pytest.mark.parametrize("kind", OBSERVATIONS[1:])
def test_C2_S034_delayed_gate_observation_preserves_selected_lifecycle(control, kind):
    peer, op = before_observation(kind)
    select(peer, op, control)
    observe(op, kind)
    assert op.state is LIFECYCLE[control]
    assert op.gate_id == (GATE if kind == "gate_ready" else None)
    assert peer.requests == []  # Operation control is not Reploy termination.
    op.terminal_result(OP, "cancelled" if control == "cancel" else "finalized")
    assert op.state is OperationState.IDLE
    assert op.active_id is None and op.gate_id is None


@pytest.mark.parametrize("control", LIFECYCLE)
@pytest.mark.parametrize("kind", OBSERVATIONS[1:])
def test_C2_S035_gate_observation_before_control_keeps_ordinary_transition(control, kind):
    peer, op = before_observation(kind)
    observe(op, kind)
    assert op.state is (OperationState.GATED if kind == "gate_ready" else OperationState.RUNNING)
    select(peer, op, control)
    assert op.state is LIFECYCLE[control]
    op.terminal_result(OP, "cancelled" if control == "cancel" else "finalized")
    assert op.state is OperationState.IDLE


@pytest.mark.parametrize("kind", OBSERVATIONS[1:])
@pytest.mark.parametrize("cancel_first", [False, True])
def test_C2_S036_cancel_supersedes_finalize_without_losing_predecessor(kind, cancel_first):
    peer, op = before_observation(kind)
    op.finalize(OP)
    if cancel_first:
        select(peer, op, "cancel")
    observe(op, kind)
    assert op.state is (OperationState.CANCELLING if cancel_first else OperationState.FINALIZING)
    if not cancel_first:
        select(peer, op, "cancel")
    op.terminal_result(OP, "finalized")  # The inspection result won the cancel.
    assert op.state is OperationState.IDLE
    assert sum(e.kind == "cancel-operation" for e in peer.effects) == 1
    assert peer.requests == []


@pytest.mark.parametrize("phase,result", [
    ("running", "completed"), ("finalizing", "completed"),
    ("finalizing", "finalized"), ("running", "cancelled"),
    ("gated", "cancelled"), ("continuing", "failed"),
])
def test_C2_S037_committed_result_resolves_cancel_without_another_response(phase, result):
    peer, op = at_phase(phase)
    select(peer, op, "cancel")
    op.terminal_result(OP, result)
    assert op.state is OperationState.IDLE and op.active_id is None
    assert op.gate_id is None and peer.requests == []
    op.execute("next-op")
    op.started("next-op")
    assert op.state is OperationState.RUNNING


@pytest.mark.parametrize("result", ["completed", "finalized"])
def test_C2_S038_unstarted_cancel_cannot_manufacture_success(result):
    peer, op = at_phase("starting")
    select(peer, op, "cancel")
    with pytest.raises(ControllerLifecycleError):
        op.terminal_result(OP, result)
    assert op.state is OperationState.FAILED and op.active_id == OP


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("eof_first", [False, True])
def test_C2_S039_shell_end_resolves_unstarted_execute_and_cancel(cancelled, eof_first):
    peer, op = at_phase("starting")
    if cancelled:
        select(peer, op, "cancel")
    op.draining("shell_ended")
    assert op.state is OperationState.DRAINING and op.shell_ended
    assert op.active_id is None and op.gate_id is None
    assert peer.controller.result is None  # A drain is not an operation/session result.
    if eof_first:
        op.terminal_eof()
        op.closed("shell_ended")
    else:
        op.closed("shell_ended")
        op.terminal_eof()
    assert op.state is OperationState.CLOSED
    with pytest.raises(ControllerLifecycleError):
        op.execute("unrunnable-beat")


@pytest.mark.parametrize("shutdown,reason", [
    (False, "shell_ended"), (True, "shell_ended"), (True, "recording-end"),
])
@pytest.mark.parametrize("eof_first", [False, True])
def test_C2_S040_both_idle_shutdown_winners_close_cleanly(shutdown, reason, eof_first):
    peer, op = at_phase("idle")
    if shutdown:
        op.shutdown("recording-end")
    op.draining(reason)
    assert op.state is OperationState.DRAINING
    assert op.shell_ended is (reason == "shell_ended")
    if eof_first:
        op.terminal_eof()
        op.closed(reason)
    else:
        op.closed(reason)
        op.terminal_eof()
    assert op.state is OperationState.CLOSED
    result = peer.finish()
    peer.controller.client_closed(0)
    assert peer.controller.result == result and peer.controller.closed
    assert peer.controller.failure is None


@pytest.mark.parametrize("phase,control,reason", [
    ("running", None, "shell_ended"), ("gated", None, "shell_ended"),
    ("continuing", None, "shell_ended"), ("running", "cancel", "shell_ended"),
    ("running", "finalize", "shell_ended"), ("starting", None, "invented"),
    ("starting", "cancel", "invented"),
])
def test_C2_S041_drain_cannot_skip_started_result_or_change_reason(phase, control, reason):
    peer, op = at_phase(phase)
    if control:
        select(peer, op, control)
    with pytest.raises(ControllerLifecycleError):
        op.draining(reason)
    assert op.state is OperationState.FAILED and op.active_id == OP


def test_C2_S041_crossed_started_closes_unstarted_drain_path():
    peer, op = at_phase("starting")
    select(peer, op, "cancel")
    op.started(OP)
    with pytest.raises(ControllerLifecycleError):
        op.draining("shell_ended")
    assert op.state is OperationState.FAILED and op.active_id == OP


@pytest.mark.parametrize("control,kind", CROSSINGS)
def test_C2_S042_crossed_observations_keep_exact_operation_identity(control, kind):
    peer, op = before_observation(kind)
    select(peer, op, control)
    with pytest.raises(ControllerLifecycleError, match="identity"):
        observe(op, kind, operation_id="other-op")
    assert op.state is OperationState.FAILED and op.active_id == OP


@pytest.mark.parametrize("control,kind", CROSSINGS)
def test_C2_S043_selected_lifecycle_does_not_allow_duplicate_observations(control, kind):
    peer, op = before_observation(kind)
    select(peer, op, control)
    observe(op, kind)
    with pytest.raises(ControllerLifecycleError):
        observe(op, kind)
    assert op.state is OperationState.FAILED and op.active_id == OP


@pytest.mark.parametrize("control", LIFECYCLE)
@pytest.mark.parametrize("kind", ["continued", "gate_interrupted"])
def test_C2_S044_crossed_gate_outcome_keeps_exact_gate_identity(control, kind):
    peer, op = before_observation(kind)
    select(peer, op, control)
    with pytest.raises(ControllerLifecycleError):
        observe(op, kind, gate_id="other-gate")
    assert op.state is OperationState.FAILED and op.gate_id == GATE


@pytest.mark.parametrize("control", LIFECYCLE)
@pytest.mark.parametrize("gate", ["", None, True, 14])
def test_C2_S044_crossed_ready_still_rejects_invalid_gate(control, gate):
    peer, op = at_phase("running")
    select(peer, op, control)
    with pytest.raises(ControllerLifecycleError):
        op.gate_ready(OP, gate)
    assert op.state is OperationState.FAILED


@pytest.mark.parametrize("control", LIFECYCLE)
@pytest.mark.parametrize("method,args", [
    ("execute", ("other-op",)), ("continue_operation", (OP, GATE)),
    ("finalize", (OP,)), ("shutdown", ("recording-end",)),
])
def test_C2_S045_crossed_ready_never_reopens_ordinary_admission(control, method, args):
    peer, op = at_phase("running")
    select(peer, op, control)
    op.gate_ready(OP, GATE)
    assert op.state is LIFECYCLE[control]
    with pytest.raises(ControllerLifecycleError):
        getattr(op, method)(*args)
    assert op.state is OperationState.FAILED


@pytest.mark.parametrize("eof_slot", range(4))
@pytest.mark.parametrize("local_failure", [False, True])
def test_C2_S046_completion_winner_composes_with_public_teardown(eof_slot, local_failure):
    peer, op = at_phase("running")
    select(peer, op, "cancel")
    op.terminal_result(OP, "completed")
    if local_failure:
        peer.accept(peer.controller.fail("capture-proof", "retained first cause"))
    first_failure = peer.controller.failure
    order = ["exit", "terminating", "outputs", "artifacts"]
    order.insert(eof_slot, "eof")
    for fact in order:
        if fact == "exit":
            peer.emit("workload-exit", status={"kind": "exited", "code": 0})
        elif fact == "terminating":
            peer.emit("terminating", cause="workload-exit")
        elif fact == "outputs":
            peer.emit("workload-outputs-finalized", status="drained")
        elif fact == "eof":
            peer.accept(peer.controller.attachment_ended(0))
        else:
            peer.accept(peer.controller.artifacts_finalized())
        if fact != "artifacts":
            assert not any(r["type"] == "complete" for r in peer.requests)
    assert sum(e.kind == "finalize-artifacts" for e in peer.effects) == 1
    result = deepcopy(RESULT)
    peer.emit("terminated", result=result)
    peer.controller.client_closed(0)
    expected = (["terminate"] if local_failure else []) + ["complete", "acknowledge-terminated"]
    assert [r["type"] for r in peer.requests] == expected
    assert peer.controller.result == result and peer.controller.closed
    assert peer.controller.failure is first_failure
    assert not peer.controller.can_record
    assert op.state is OperationState.IDLE


@pytest.mark.parametrize("control", LIFECYCLE)
@pytest.mark.parametrize("kind", OBSERVATIONS[1:])
@pytest.mark.parametrize("artifact_error", [None, "partial artifact close failed"])
def test_C2_S047_late_gate_during_public_termination_preserves_first_failure(
    control, kind, artifact_error,
):
    peer, op = before_observation(kind)
    select(peer, op, control)
    original_gate = op.gate_id
    peer.accept(peer.controller.stderr(b"before\x00\xff"))
    peer.accept(peer.controller.fail("capture-proof", "first retained failure"))
    first = peer.controller.failure
    assert first.operation_id == OP and first.gate_id == original_gate
    peer.emit("terminating", cause="controller-terminate")
    observe(op, kind)
    assert op.state is LIFECYCLE[control]
    op.terminal_result(OP, "cancelled" if control == "cancel" else "finalized")
    peer.accept(peer.controller.stderr(b"after\x01"))
    peer.emit("workload-outputs-finalized", status="drained")
    peer.accept(peer.controller.attachment_ended(0))
    peer.accept(peer.controller.artifacts_finalized(error=artifact_error))
    result = deepcopy(RESULT)
    result["cause"] = "controller-terminate"
    peer.emit("terminated", result=result)
    peer.controller.client_closed(0)
    assert peer.controller.failure is first and first.message == "first retained failure"
    assert peer.controller.result == result and peer.controller.closed
    assert peer.stderr.getvalue() == b"before\x00\xffafter\x01"
    assert [r["type"] for r in peer.requests] == ["terminate", "complete", "acknowledge-terminated"]
    assert not peer.controller.can_record and op.active_id is None


def test_C2_S048_owner_retains_invalid_observation_before_valid_public_teardown():
    peer, op = at_phase("continuing")
    select(peer, op, "cancel")
    with pytest.raises(ControllerLifecycleError) as error:
        op.continued(OP, "wrong-gate")
    # C3 will supply the real channel owner. This peer reports the typed fault
    # through the existing failure primitive; it does not implement transport.
    peer.accept(peer.controller.fail("envoy-lifecycle", str(error.value)))
    first = peer.controller.failure
    assert first.operation_id == OP and first.gate_id == GATE
    result = peer.finish(cause="controller-terminate")
    peer.controller.client_closed(0)
    assert peer.controller.failure is first and peer.controller.result == result
    assert peer.controller.closed and not peer.controller.can_record
    assert [r["type"] for r in peer.requests] == ["terminate", "complete", "acknowledge-terminated"]


def test_C2_S049_complete_package_map_preserves_static_owners_and_pending_actual_proofs():
    case_map = json.loads((ROOT / "controller-lifecycle/case-map.json").read_text())
    actual = {}
    for name in ["test_controller_lifecycle.py", "test_controller_session.py",
                 "test_controller_crossings.py"]:
        for node in ast.parse((ROOT / name).read_text()).body:
            if isinstance(node, ast.FunctionDef):
                match = re.match(r"test_(C2_S\d{3})_", node.name)
                if match:
                    entry = actual.setdefault(match[1], {"file": "tests/" + name, "functions": []})
                    assert entry["file"] == "tests/" + name
                    entry["functions"].append(node.name)
    assert set(case_map["tests"]) == set(actual)
    for identity, entry in actual.items():
        mapped = case_map["tests"][identity]
        assert mapped["file"] == entry["file"]
        assert len(mapped["functions"]) == len(set(mapped["functions"]))
        assert set(mapped["functions"]) == set(entry["functions"])
    assert set(actual) == {f"C2_S{i:03}" for i in range(1, 50)}
    requirement = case_map["requirements"]["crossed_observations_and_Cancelling_completion_winner"]
    assert requirement["owner"] == "C2.3" and requirement["status"] == "implemented"
    assert set(requirement["tests"]) == {f"C2_S{i:03}" for i in range(34, 50)}
    assert case_map["requirements"]["actual_Envoy_channels_and_readiness_barrier"]["status"] == "pending"
    assert case_map["requirements"]["actual_Reploy_termination"]["status"] == "pending"
    original = {r["id"]: r for r in map(json.loads,
                (ROOT / "fixtures/envoy-protocol-v1/cases.jsonl").read_text().splitlines())}
    gates = []
    for anchor in case_map["retained_static_anchors"]:
        source = original[anchor["id"]]
        assert anchor["implementation_leaf"] == source["implementation_leaf"]
        assert anchor["closure_leaf"] == source["closure_leaf"]
        if anchor["id"].startswith("B1-C065-controller-"):
            gates.append(anchor)
            assert anchor["status"] == "pending" and anchor["closure_leaf"] == "C3.3"
            assert anchor["implementation_status"] == "implemented"
            assert anchor["tests"] == ["C2_S034"]
        elif anchor["id"] == "B1-PACKAGE-C2.3":
            assert anchor["status"] == "implemented"
            assert set(anchor["tests"]) == set(requirement["tests"])
    assert len(gates) == 6
