from __future__ import annotations

from copy import deepcopy

import pytest

from omegaflow.recording_plan import (
    RecordingPlanError,
    normalize_recording_plan,
    validate_terminal_command,
    validate_terminal_step,
)


ASSERTIONS = ("output_contains", "output_regex")
SENDING_INPUT = ({"text": "authored"}, {"key": "enter"}, {"control": "d"})


def command(*, input_steps=(), expect=None, identifier=None, run_file=False):
    result = {
        "run_file" if run_file else "run": "command.sh" if run_file else "printf needle",
        "timing": "realtime",
        "input": deepcopy(list(input_steps)),
        "expect": {} if expect is None else deepcopy(expect),
    }
    if identifier is not None:
        result["id"] = identifier
    return result


def continuation(identifier, previous, input_steps):
    return {
        "id": identifier,
        "continue_from": previous,
        "timing": "realtime",
        "input": deepcopy(list(input_steps)),
    }


def legacy(*actions):
    return {
        "id": "eligibility",
        "beats": [{
            "id": "one",
            "actions": [
                action if "commands" in action else {"commands": [action]}
                for action in actions
            ],
        }],
    }


def explicit(actions, topology="same-beat"):
    batches = [[action] for action in actions] if topology == "cross-beat" else [actions]
    return {
        "id": "eligibility",
        "panes": [{"id": "terminal", "kind": "terminal"}],
        "beats": [
            {
                "id": f"beat-{index}",
                "layout": {"areas": [["terminal"]]},
                "panes": {"terminal": [{"id": f"pane-{index}", "actions": batch}]},
            }
            for index, batch in enumerate(batches)
        ],
    }


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
@pytest.mark.parametrize("run_file", [False, True])
def test_direct_authored_input_rejects_output_assertions(
    assertion, input_step, run_file
):
    spec = legacy(command(
        input_steps=[input_step], expect={assertion: ["needle"]}, run_file=run_file
    ))
    with pytest.raises(RecordingPlanError, match=r"output_(contains|regex)"):
        normalize_recording_plan(spec)


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
@pytest.mark.parametrize("validator", [validate_terminal_command, validate_terminal_step])
def test_public_validators_reject_direct_input_assertions(
    assertion, input_step, validator
):
    value = command(input_steps=[input_step], expect={assertion: ["needle"]})
    with pytest.raises(RecordingPlanError, match=r"output_(contains|regex)"):
        validator(value, field="command")


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
@pytest.mark.parametrize("sending_index", [0, 1])
def test_group_output_assertions_reject_any_sending_member(
    assertion, input_step, sending_index
):
    commands = [command(), command()]
    commands[sending_index]["input"] = [deepcopy(input_step)]
    with pytest.raises(RecordingPlanError, match=r"output_(contains|regex)"):
        normalize_recording_plan(legacy({
            "commands": commands, "expect": {assertion: ["needle"]}
        }))


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
def test_group_keeps_assertion_on_an_independent_non_sending_command(
    assertion, input_step
):
    spec = legacy({"commands": [
        command(expect={assertion: ["needle"]}),
        command(input_steps=[input_step]),
    ]})
    before = deepcopy(spec)
    normalize_recording_plan(spec)
    assert spec == before


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
@pytest.mark.parametrize("topology", ["same-beat", "cross-beat"])
def test_later_continuation_input_invalidates_root_assertion(
    assertion, input_step, topology
):
    actions = [
        command(identifier="root", expect={assertion: ["needle"]}),
        continuation("middle", "root", [{"wait_for": "Ready"}, {"pause": 0}]),
        continuation("finish", "middle", [input_step]),
    ]
    with pytest.raises(RecordingPlanError, match=r"output_(contains|regex)") as error:
        normalize_recording_plan(explicit(actions, topology))
    assert "root" in str(error.value)


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
@pytest.mark.parametrize("group_after_input", [False, True])
def test_explicit_panes_keep_rejecting_authored_command_groups(
    assertion, input_step, group_after_input
):
    actions = [
        {"id": "group", "commands": [command(identifier="root")],
         "expect": {assertion: ["needle"]}},
        continuation("finish", "root", [input_step]),
    ]
    if group_after_input:
        actions = [
            command(identifier="root", input_steps=[input_step]),
            {"id": "group",
             "commands": [continuation("finish", "root", [{"pause": 0}])],
             "expect": {assertion: ["needle"]}},
        ]
    with pytest.raises(RecordingPlanError, match=r"commands is not supported"):
        normalize_recording_plan(explicit(actions, "cross-beat"))


@pytest.mark.parametrize("assertion", ASSERTIONS)
def test_continuations_keep_rejecting_their_own_expectation(assertion):
    follow_up = continuation("finish", "root", [{"wait_for": "needle"}])
    follow_up["expect"] = {assertion: ["needle"]}
    actions = [
        command(identifier="root", input_steps=[{"text": "authored"}]),
        follow_up,
    ]
    with pytest.raises(
        RecordingPlanError, match=r"continue_from cannot be combined with expect"
    ):
        normalize_recording_plan(explicit(actions))


@pytest.mark.parametrize("assertion", ASSERTIONS)
def test_wait_pause_only_groups_keep_output_assertions(assertion):
    normalize_recording_plan(legacy({
        "commands": [command(input_steps=[{"wait_for": "needle"}, {"pause": 0}])],
        "expect": {assertion: ["needle"]},
    }))


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("topology", ["same-beat", "cross-beat"])
def test_wait_pause_only_chains_keep_output_assertions(assertion, topology):
    actions = [
        command(identifier="root", input_steps=[{"wait_for": "Ready"}],
                expect={assertion: ["needle"]}),
        continuation("finish", "root", [
            {"pause": 0}, {"wait_for": "needle", "timeout": 1}
        ]),
    ]
    spec = explicit(actions, topology)
    before = deepcopy(spec)
    normalize_recording_plan(spec)
    assert spec == before


@pytest.mark.parametrize("assertion", ASSERTIONS)
def test_sending_chain_does_not_taint_next_root_or_another_pane(assertion):
    spec = explicit([
        command(identifier="root", input_steps=[{"text": "authored"}]),
        continuation("finish", "root", [{"key": "enter"}]),
        command(identifier="independent", expect={assertion: ["needle"]}),
    ])
    spec["panes"].append({"id": "other", "kind": "terminal"})
    spec["beats"][0]["layout"] = {"areas": [["terminal", "other"]]}
    spec["beats"][0]["panes"]["other"] = [{
        "id": "other-pane",
        "actions": [command(identifier="other", input_steps=[{"wait_for": "needle"}],
                            expect={assertion: ["needle"]})],
    }]
    normalize_recording_plan(spec)


@pytest.mark.parametrize("input_step", SENDING_INPUT)
@pytest.mark.parametrize("expect", [
    {}, {"output_contains": [], "output_regex": []},
    {"exit_code": 0, "file_exists": ["result"]},
])
def test_authored_input_keeps_empty_output_and_exit_file_assertions(
    input_step, expect
):
    normalize_recording_plan(legacy(command(
        input_steps=[input_step, {"wait_for": "needle"}], expect=expect
    )))


@pytest.mark.parametrize("input_step", SENDING_INPUT)
def test_continuations_keep_root_exit_and_file_assertions(input_step):
    plan = normalize_recording_plan(explicit([
        command(identifier="root", expect={"exit_code": 0, "file_exists": ["result"]}),
        continuation("finish", "root", [input_step]),
    ]))
    follow_up = plan.beats[0].pane_tracks[0].beats[0].actions[1].config["commands"][0]
    assert follow_up["_continuation_expect"]["exit_code"] == 0
    assert list(follow_up["_continuation_expect"]["file_exists"]) == ["result"]


@pytest.mark.parametrize("assertion", ASSERTIONS)
@pytest.mark.parametrize("input_step", SENDING_INPUT)
def test_inherited_realtime_timing_does_not_bypass_eligibility(assertion, input_step):
    action = command(input_steps=[input_step], expect={assertion: ["needle"]})
    del action["timing"]
    spec = legacy(action)
    spec["beats"][0]["timing"] = "realtime"
    with pytest.raises(RecordingPlanError, match=r"output_(contains|regex)"):
        normalize_recording_plan(spec)


@pytest.mark.parametrize("assertion", ASSERTIONS)
def test_noninteractive_presentation_output_assertions_remain_valid(assertion):
    action = command(expect={assertion: ["needle"]})
    action["timing"] = "presentation"
    normalize_recording_plan(legacy(action))
