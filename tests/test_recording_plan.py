from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
from omegaconf import OmegaConf

from omegaflow.audio import (
    AudioError,
    AudioSettings,
    narration_audio_metadata_v3_payload,
    narration_take_cache_key,
    narration_take_review_warning,
    narration_timestamp_sidecar_payload,
    plan_narration_take_audio,
)
from omegaflow.presentation_schema import BrowserPayloadV1, PresentationManifestV1
from omegaflow.recording_plan import (
    BrowserActionPlan,
    NarrationTakeAnchorPlan,
    NarrationTakeMemberPlan,
    NarrationTakePlan,
    NarrationTakeWaitPlan,
    RecordingPlanError,
    TerminalTextHighlightPlan,
    normalize_recording_plan,
    terminal_action_id,
    validate_recording_modalities,
)
from omegaflow.studio_config import RecordingSpec, USER_RECORDING_YAML_SCHEMAS


def browser_spec() -> dict:
    return {
        "id": "browser-demo",
        "title": "Browser demo",
        "browser": {"base_url": "http://127.0.0.1:3000"},
        "presentation": {"browser": {"chrome": {"mode": "full"}}},
        "beats": [
            {
                "id": "create",
                "medium": "browser",
                "heading": "Create",
                "narration": "@menu@ Open the menu. @wait:done+300ms@",
                "actions": [
                    {
                        "id": "open",
                        "open_page": {
                            "url": "/projects",
                            "display_url": "https://example.test/projects",
                        },
                    },
                    {
                        "id": "done",
                        "click": {
                            "target": {"role": "button", "name": "Create"}
                        },
                        "after": "@menu@",
                    },
                ],
                "checks": [
                    {"name": "created", "url": {"contains": "/projects/"}}
                ],
                "guide": {"success_hint": "The project opens."},
            }
        ],
    }


@pytest.mark.parametrize(
    ("action_index", "command_index", "command", "expected"),
    [
        (2, None, None, "__step_2"),
        (2, 3, {}, "__step_2_command_3"),
        (2, 3, {"id": "publish"}, "publish"),
    ],
)
def test_terminal_action_id_is_the_shared_capture_contract(
    action_index: int,
    command_index: int | None,
    command: dict[str, object] | None,
    expected: str,
) -> None:
    assert terminal_action_id(action_index, command_index, command) == expected


def terminal_spec() -> dict:
    return {
        "id": "terminal-demo",
        "beats": [
            {
                "id": "terminal",
                "narration": "@run@ Run it. @wait:done@",
                "actions": [
                    {
                        "commands": [
                            {"id": "done", "run": "echo ok", "after": "@run@"}
                        ]
                    }
                ],
            }
        ],
    }


def browser_handoff_spec() -> dict:
    return {
        "id": "browser-handoff",
        "browser": {},
        "presentation": {"browser": {"chrome": {"mode": "full"}}},
        "beats": [
            {
                "id": "watch",
                "actions": [
                    {
                        "commands": [
                            {
                                "id": "watch_command",
                                "run": "omegaflow recording=demo action=watch",
                                "browser_handoff": True,
                                "timing": "realtime",
                                "show_prompt_after": False,
                            }
                        ]
                    }
                ],
            },
            {
                "id": "browser",
                "medium": "browser",
                "actions": [
                    {
                        "id": "open",
                        "open_page": {
                            "handoff": "watch_command",
                            "display_url": "$handoff",
                        },
                    }
                ],
            },
        ],
    }


def test_omegaconf_schema_authority_supports_versioned_artifacts() -> None:
    for schema in (RecordingSpec, BrowserPayloadV1, PresentationManifestV1):
        assert OmegaConf.structured(schema) is not None


def annotation_contains_any(annotation: object) -> bool:
    return annotation is Any or any(
        annotation_contains_any(argument) for argument in get_args(annotation)
    )


def test_annotation_contains_any_recurses_through_nested_containers() -> None:
    assert annotation_contains_any(list[dict[str, Any]])
    assert not annotation_contains_any(list[dict[str, str | int]])


def test_user_recording_yaml_schema_has_no_any_typed_fields() -> None:
    permissive: list[str] = []
    for schema in USER_RECORDING_YAML_SCHEMAS:
        hints = get_type_hints(schema)
        for item in fields(schema):
            if annotation_contains_any(hints[item.name]):
                permissive.append(f"{schema.__name__}.{item.name}")

    assert permissive == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "expect",
            {"output_contians": ["hello"]},
            r"beats\.0\.actions\.0\.commands\.0\.expect has unknown fields: output_contians",
        ),
        (
            "output",
            {"replce": "hello"},
            r"beats\.0\.actions\.0\.commands\.0\.output mapping must contain only: replace",
        ),
        (
            "follow_along",
            True,
            r"follow_along",
        ),
    ],
)
def test_terminal_action_metadata_is_validated_during_plan_normalization(
    field: str,
    value: object,
    message: str,
) -> None:
    spec = terminal_spec()
    command = spec["beats"][0]["actions"][0]["commands"][0]
    command[field] = value

    with pytest.raises(RecordingPlanError, match=message):
        normalize_recording_plan(spec)


def test_requirements_are_validated_during_plan_normalization() -> None:
    spec = terminal_spec()
    spec["requirements"] = {"commandz": ["bash"]}

    with pytest.raises(
        RecordingPlanError,
        match=r"requirements has unknown fields: commandz",
    ):
        normalize_recording_plan(spec)


def test_browser_beat_can_override_pointer_visibility() -> None:
    spec = browser_spec()
    spec["beats"][0]["pointer"] = {"visible": False}

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].browser_pointer_visible is False


def test_beat_can_highlight_a_typed_player_toolbar_control() -> None:
    spec = terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["player"] = {
        "highlight": {"control": "guided", "start": "@run@"}
    }

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].player_highlight is not None
    assert plan.beats[0].player_highlight.control == "guided"
    assert plan.beats[0].player_highlight.start_anchor == "run"
    assert plan.beats[0].player_highlight.end_anchor is None


def test_player_toolbar_highlight_requires_narration_audio() -> None:
    spec = terminal_spec()
    spec["beats"][0]["player"] = {
        "highlight": {"control": "guided", "start": "@run@"}
    }

    with pytest.raises(
        RecordingPlanError,
        match=r"beats\.0\.player\.highlight requires audio\.enabled=true",
    ):
        normalize_recording_plan(spec)


def test_beat_rejects_unknown_player_toolbar_control() -> None:
    spec = terminal_spec()
    spec["beats"][0]["player"] = {
        "highlight": {"control": "download", "start": "@run@"}
    }

    with pytest.raises(RecordingPlanError, match="player.highlight.control"):
        normalize_recording_plan(spec)


def test_player_toolbar_highlight_rejects_unknown_narration_anchor() -> None:
    spec = terminal_spec()
    spec["beats"][0]["player"] = {
        "highlight": {"control": "guided", "start": "@missing@"}
    }

    with pytest.raises(RecordingPlanError, match="unknown start anchor"):
        normalize_recording_plan(spec)


def test_terminal_beat_rejects_browser_pointer_visibility() -> None:
    spec = terminal_spec()
    spec["beats"][0]["pointer"] = {"visible": False}

    with pytest.raises(
        RecordingPlanError,
        match=r"beats\.0\.pointer is invalid for terminal beats",
    ):
        normalize_recording_plan(spec)


def test_terminal_text_highlight_is_typed_and_bound_to_narration_anchors() -> None:
    spec = terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = (
        "@highlight_start@ Project settings. @highlight_end@ @run@ Run it. "
        "@wait:done@"
    )
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "text": ".omegaflow/config.yaml",
                "start": "@highlight_start@",
                "end": "@highlight_end@",
                "occurrence": 2,
            }
        }
    ]

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].terminal_highlights == (
        TerminalTextHighlightPlan(
            text=".omegaflow/config.yaml",
            start_anchor="highlight_start",
            end_anchor="highlight_end",
            occurrence=2,
        ),
    )


def test_terminal_text_highlight_requires_narration_audio() -> None:
    spec = terminal_spec()
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "text": "config.yaml",
                "start": "@run@",
                "end": "@done@",
            }
        }
    ]
    spec["beats"][0]["narration"] = "@run@ Run it. @done@ Finished."

    with pytest.raises(
        RecordingPlanError,
        match=r"beats\.0\.effects\.highlight requires audio\.enabled=true",
    ):
        normalize_recording_plan(spec)


@pytest.mark.parametrize(
    ("effect", "message"),
    [
        (
            {"highlight": {"text": "", "start": "@start@", "end": "@end@"}},
            r"beats\.0\.effects\.0\.highlight\.text must be non-empty",
        ),
        (
            {
                "highlight": {
                    "text": "config.yaml",
                    "start": "@missing@",
                    "end": "@end@",
                }
            },
            r"references unknown start anchor @missing@",
        ),
        (
            {
                "highlight": {
                    "text": "config.yaml",
                    "start": "@end@",
                    "end": "@start@",
                }
            },
            r"start anchor @end@ must precede end anchor @start@",
        ),
        (
            {
                "highlight": {
                    "text": "config.yaml",
                    "start": "@start@",
                    "end": "@end@",
                    "occurrence": 0,
                }
            },
            r"beats\.0\.effects\.0\.highlight\.occurrence must be positive",
        ),
        ({}, r"beats\.0\.effects\.0 must contain exactly one of: highlight"),
    ],
)
def test_terminal_text_highlight_rejects_invalid_configuration(
    effect: dict[str, object], message: str
) -> None:
    spec = terminal_spec()
    spec["beats"][0]["narration"] = (
        "@start@ Project settings. @end@ @run@ Run it. @wait:done@"
    )
    spec["beats"][0]["effects"] = [effect]

    with pytest.raises(RecordingPlanError, match=message):
        normalize_recording_plan(spec)


def test_browser_beat_rejects_terminal_text_highlight() -> None:
    spec = browser_spec()
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "text": "Create",
                "start": "@menu@",
                "end": "@menu@",
            }
        }
    ]

    with pytest.raises(
        RecordingPlanError,
        match=r"beats\.0\.effects are invalid for browser beats",
    ):
        normalize_recording_plan(spec)


def test_run_files_resolve_from_the_recording_source_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "recordings" / "demo"
    scripts = source_dir / "scripts"
    scripts.mkdir(parents=True)
    for name in ("setup.sh", "action.sh", "check.sh", "cleanup.sh"):
        (scripts / name).write_text("true\n", encoding="utf-8")

    plan = normalize_recording_plan(
        {
            "id": "demo",
            "_script_dir": str(source_dir),
            "setup": [{"run_file": "scripts/setup.sh"}],
            "beats": [
                {
                    "id": "run",
                    "actions": [
                        {"commands": [{"run_file": "scripts/action.sh"}]}
                    ],
                    "checks": [{"run_file": "scripts/check.sh"}],
                }
            ],
            "cleanup": [{"run_file": "scripts/cleanup.sh"}],
        }
    )

    assert plan.setup[0].config["run_file"] == str(scripts / "setup.sh")
    assert plan.beats[0].actions[0].config["commands"][0]["run_file"] == str(
        scripts / "action.sh"
    )
    assert plan.beats[0].checks[0].config["run_file"] == str(scripts / "check.sh")
    assert plan.cleanup[0].config["run_file"] == str(scripts / "cleanup.sh")


def test_normalizes_browser_actions_checks_and_references() -> None:
    plan = normalize_recording_plan(browser_spec())

    beat = plan.beats[0]
    assert beat.medium.value == "browser"
    assert [action.id for action in beat.actions] == ["open", "done"]
    assert isinstance(beat.actions[0], BrowserActionPlan)
    assert beat.waits[0].target == "done"
    assert beat.waits[0].gap_ms == 300


def test_internal_narration_supplies_heading_and_viewer_hold() -> None:
    plan = normalize_recording_plan(
        {
            "id": "script-backed",
            "narration": {
                "beats": [
                    {
                        "id": "beat",
                        "heading": "Script heading",
                        "text": "Script narration.",
                        "viewer_hold": 0.25,
                    }
                ]
            },
            "beats": [{"id": "beat", "actions": [{"run": "printf ok"}]}],
        }
    )

    assert plan.beats[0].heading == "Script heading"
    assert plan.beats[0].viewer_hold_ms == 250


@pytest.mark.parametrize(
    "action",
    [
        {
            "id": "fill_text",
            "fill": {"target": {"label": "Project name"}, "text": "Demo"},
        },
        {
            "id": "fill_secret",
            "fill": {
                "target": {"test_id": "password"},
                "secret": {"env": "DEMO_PASSWORD", "presentation": "masked"},
            },
        },
        {
            "id": "type",
            "type_keys": {
                "target": {"placeholder": "Search"},
                "text": "query",
                "capture_delay_ms": 0,
            },
        },
        {
            "id": "move_viewport",
            "move_pointer": {"viewport": {"x": 0.4, "y": 0.12}},
        },
        {
            "id": "move_target",
            "move_pointer": {
                "target": {"role": "button", "name": "Create"},
                "position": {"x": 0.25, "y": 0.75},
            },
        },
        {"id": "show_pointer", "set_pointer": {"visible": True}},
        {"id": "press", "press": {"key": "Control+K", "target": {"text": "Search"}}},
        {"id": "scroll_target", "scroll": {"target": {"text": "Results"}}},
        {"id": "scroll_by", "scroll": {"by": {"x": 0, "y": 400}}},
        {
            "id": "scroll_to",
            "scroll": {"to": {"x": 0, "y": 0}, "container": {"css": ".panel"}},
        },
        {"id": "wait_visible", "wait_for": {"visible": {"role": "main"}}},
        {"id": "wait_url", "wait_for": {"url": {"matches": "/projects/[^/]+$"}}},
        {
            "id": "wait_response",
            "wait_for": {
                "response": {"contains": "/api/projects", "method": "POST", "status": 201}
            },
        },
    ],
)
def test_accepts_each_browser_action_variant(action: dict) -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"].append(action)

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].actions[-1].id == action["id"]


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_set_pointer_requires_boolean_visibility(value: object) -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"].append(
        {"id": "show_pointer", "set_pointer": {"visible": value}}
    )

    with pytest.raises(RecordingPlanError, match="set_pointer.visible must be boolean"):
        normalize_recording_plan(spec)


def test_browser_action_accepts_hold_before() -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["hold_before_ms"] = 250

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].actions[1].config["hold_before_ms"] == 250


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_browser_action_rejects_invalid_hold_before(value: object) -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["hold_before_ms"] = value

    with pytest.raises(RecordingPlanError, match="hold_before_ms"):
        normalize_recording_plan(spec)


@pytest.mark.parametrize(
    ("move_pointer", "match"),
    [
        ({}, "exactly one"),
        (
            {
                "viewport": {"x": 0.4, "y": 0.12},
                "target": {"role": "button", "name": "Create"},
            },
            "exactly one",
        ),
        ({"viewport": {"x": -0.1, "y": 0.5}}, "between 0 and 1"),
        ({"viewport": {"x": 0.5, "y": 1.1}}, "between 0 and 1"),
        ({"viewport": {"x": True, "y": 0.5}}, "numbers between 0 and 1"),
        (
            {
                "target": {"role": "button", "name": "Create"},
                "position": {"x": -0.1, "y": 0.5},
            },
            "position values must be between 0 and 1",
        ),
        (
            {
                "viewport": {"x": 0.5, "y": 0.5},
                "position": {"x": 0.5, "y": 0.5},
            },
            "position requires a target",
        ),
    ],
)
def test_rejects_invalid_pointer_move_destination(
    move_pointer: dict,
    match: str,
) -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"].append(
        {"id": "move", "move_pointer": move_pointer}
    )

    with pytest.raises(RecordingPlanError, match=match):
        normalize_recording_plan(spec)


@pytest.mark.parametrize(
    "target",
    [
        {"role": "button", "name": "Create"},
        {"label": "Project name"},
        {"placeholder": "Search"},
        {"text": "Create project", "exact": True},
        {"test_id": "create-project"},
        {"css": "button.primary"},
        {"xpath": "//button[@type='submit']"},
    ],
)
def test_accepts_each_browser_target_family(target: dict) -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["click"]["target"] = target

    normalize_recording_plan(spec)


@pytest.mark.parametrize(
    "check",
    [
        {"name": "url", "url": {"contains": "/projects"}},
        {"name": "visible", "visible": {"role": "main"}},
        {"name": "hidden", "hidden": {"text": "Loading"}},
        {
            "name": "text",
            "text": {"target": {"test_id": "status"}, "equals": "Ready"},
        },
        {
            "name": "value",
            "value": {"target": {"label": "Project name"}, "contains": "Demo"},
        },
        {"name": "count", "count": {"target": {"css": ".result"}, "equals": 0}},
        {
            "name": "response",
            "response": {"matches": "/api/projects/[^/]+", "status": 200},
        },
    ],
)
def test_accepts_each_browser_check_variant(check: dict) -> None:
    spec = browser_spec()
    spec["beats"][0]["checks"] = [check]

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].checks[0].name == check["name"]


def test_normalizes_terminal_default_without_changing_action_shape() -> None:
    plan = normalize_recording_plan(terminal_spec())

    beat = plan.beats[0]
    assert beat.medium.value == "terminal"
    assert beat.actions[0].config["commands"][0]["run"] == "echo ok"


@pytest.mark.parametrize(
    "mutator,match",
    [
        (
            lambda spec: spec["beats"][0]["actions"][1]["click"]["target"].update(
                {"css": "button"}
            ),
            "exactly one",
        ),
        (
            lambda spec: spec["beats"][0]["actions"][0]["open_page"].update(
                {"ready": {"visible": {"role": "main"}, "url": {"contains": "/"}}}
            ),
            "exactly one",
        ),
        (
            lambda spec: spec["beats"][0]["checks"][0].update(
                {"visible": {"role": "main"}}
            ),
            "exactly one",
        ),
    ],
)
def test_rejects_ambiguous_browser_unions(mutator, match: str) -> None:
    spec = browser_spec()
    mutator(spec)
    with pytest.raises(RecordingPlanError, match=match):
        normalize_recording_plan(spec)


def test_rejects_action_for_wrong_modality() -> None:
    spec = terminal_spec()
    spec["beats"][0]["actions"] = [
        {"id": "open", "open_page": {"url": "about:blank"}}
    ]
    with pytest.raises(RecordingPlanError, match="open_page"):
        validate_recording_modalities(spec)


def test_requires_first_browser_action_to_open_page() -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"] = spec["beats"][0]["actions"][1:]
    with pytest.raises(RecordingPlanError, match="first browser action"):
        normalize_recording_plan(spec)


def test_requires_display_url_for_full_chrome() -> None:
    spec = browser_spec()
    del spec["beats"][0]["actions"][0]["open_page"]["display_url"]
    with pytest.raises(RecordingPlanError, match="requires display_url"):
        normalize_recording_plan(spec)


def test_browser_beat_presentation_overrides_are_typed_and_normalized() -> None:
    spec = browser_spec()
    spec["presentation"]["browser"].update(
        {
            "window": {"mode": "framed", "title": "Default"},
            "chrome": {"mode": "full"},
        }
    )
    spec["beats"][0]["window"] = {"mode": "none"}
    spec["beats"][0]["chrome"] = {"mode": "hidden"}

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].browser_window is not None
    assert plan.beats[0].browser_window["mode"] == "none"
    assert plan.beats[0].browser_chrome is not None
    assert plan.beats[0].browser_chrome["mode"] == "hidden"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("window", {"mode": "floating"}, r"beats\.0\.window\.mode"),
        ("chrome", {"mode": "captured"}, r"beats\.0\.chrome\.mode"),
    ],
)
def test_browser_beat_rejects_invalid_presentation_overrides(
    field: str, value: dict[str, str], message: str
) -> None:
    spec = browser_spec()
    spec["beats"][0][field] = value

    with pytest.raises(RecordingPlanError, match=message):
        normalize_recording_plan(spec)


def test_normalizes_recorder_owned_browser_handoff() -> None:
    plan = normalize_recording_plan(browser_handoff_spec())

    command = plan.beats[0].actions[0].config["commands"][0]
    open_page = plan.beats[1].actions[0].config["open_page"]
    assert command["browser_handoff"] is True
    assert open_page["handoff"] == "watch_command"
    assert open_page["url"] is None


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (
            lambda spec: spec["beats"][0]["actions"][0]["commands"][0].update(
                {"id": None}
            ),
            "browser_handoff.*id",
        ),
        (
            lambda spec: spec["beats"][0]["actions"][0]["commands"][0].update(
                {"timing": "presentation"}
            ),
            "browser_handoff.*timing.*realtime",
        ),
        (
            lambda spec: spec["beats"][0]["actions"][0]["commands"][0].update(
                {"show_prompt_after": True}
            ),
            "browser_handoff.*show_prompt_after",
        ),
        (
            lambda spec: spec["beats"][0]["actions"][0]["commands"][0].update(
                {"output": {"replace": "pretend"}}
            ),
            "browser_handoff.*real output",
        ),
        (
            lambda spec: spec["beats"][0]["actions"][0]["commands"].append(
                {"id": "later", "run": "true"}
            ),
            "browser_handoff.*last command",
        ),
        (
            lambda spec: spec["beats"][1]["actions"][0]["open_page"].update(
                {"handoff": "other"}
            ),
            "does not consume",
        ),
        (
            lambda spec: spec["beats"][1]["actions"][0]["open_page"].update(
                {"url": "about:blank"}
            ),
            "exactly one of.*url.*handoff",
        ),
    ],
)
def test_rejects_invalid_recorder_owned_browser_handoff(mutator, match: str) -> None:
    spec = browser_handoff_spec()
    mutator(spec)

    with pytest.raises(RecordingPlanError, match=match):
        normalize_recording_plan(spec)


def test_handoff_display_url_requires_explicit_dynamic_value_or_safe_static_url() -> None:
    spec = browser_handoff_spec()
    spec["beats"][1]["actions"][0]["open_page"]["display_url"] = "$other"

    with pytest.raises(RecordingPlanError, match="display_url"):
        normalize_recording_plan(spec)


def test_rejects_handoff_consumer_without_a_matching_terminal_producer() -> None:
    spec = browser_handoff_spec()
    spec["beats"][0]["actions"][0]["commands"][0]["browser_handoff"] = False

    with pytest.raises(RecordingPlanError, match="no preceding terminal command"):
        normalize_recording_plan(spec)


def test_rejects_unknown_anchor_and_wait_targets() -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["after"] = "@missing@"
    with pytest.raises(RecordingPlanError, match="unknown anchor"):
        normalize_recording_plan(spec)

    spec = browser_spec()
    spec["beats"][0]["narration"] = "@menu@ Open it. @wait:missing@"
    with pytest.raises(RecordingPlanError, match="unknown action or command"):
        normalize_recording_plan(spec)


def test_recording_plan_is_deeply_immutable() -> None:
    plan = normalize_recording_plan(browser_spec())
    with pytest.raises(FrozenInstanceError):
        plan.id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        plan.beats[0].actions[0].config["id"] = "changed"  # type: ignore[index]


def test_plans_implicit_and_explicit_contiguous_takes() -> None:
    spec = terminal_spec()
    spec["beats"] = [
        {
            "id": "one",
            "narration_take": "joined",
            "narration": "First.",
            "actions": [{"run": "true"}],
        },
        {
            "id": "two",
            "narration_take": "joined",
            "narration": "Second.",
            "actions": [{"run": "true"}],
        },
        {
            "id": "three",
            "narration": "Third.",
            "actions": [{"run": "true"}],
        },
    ]
    plan = normalize_recording_plan(spec)

    assert [take.id for take in plan.narration_takes] == [
        "joined",
        "__beat__:three",
    ]
    assert plan.narration_takes[0].synthesis_text == "First. Second."
    assert [
        (member.text_start, member.text_end)
        for member in plan.narration_takes[0].members
    ] == [(0, 6), (7, 14)]


def test_rejects_fragmented_take_after_singleton_deduction() -> None:
    spec = terminal_spec()
    spec["beats"] = [
        {
            "id": "one",
            "narration_take": "joined",
            "narration": "First.",
            "actions": [{"run": "true"}],
        },
        {"id": "middle", "narration": "Middle.", "actions": [{"run": "true"}]},
        {
            "id": "two",
            "narration_take": "joined",
            "narration": "Second.",
            "actions": [{"run": "true"}],
        },
    ]
    with pytest.raises(RecordingPlanError, match="fragmented"):
        normalize_recording_plan(spec)


def test_take_cache_key_and_non_blocking_reorder_warning(tmp_path: Path) -> None:
    spec = terminal_spec()
    spec["beats"] = [
        {
            "id": "one",
            "narration_take": "joined",
            "narration": "First.",
            "actions": [{"run": "true"}],
        },
        {
            "id": "two",
            "narration_take": "joined",
            "narration": "Second.",
            "actions": [{"run": "true"}],
        },
    ]
    plan = normalize_recording_plan(spec)
    settings = AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_API_KEY",
        model="model",
        voice="voice",
        format="mp3",
        cache_dir=tmp_path,
    )
    item = plan_narration_take_audio(plan.id, plan.narration_takes, settings)[0]

    assert item.cache_key == narration_take_cache_key(plan.narration_takes[0], settings)
    warning = narration_take_review_warning(
        item,
        {"take_id": "joined", "ordered_beat_ids": ["two", "one"]},
    )
    assert warning == {
        "code": "NARRATION_TAKE_REVIEW",
        "take_id": "joined",
        "previous_beat_ids": ["two", "one"],
        "current_beat_ids": ["one", "two"],
    }


def test_timestamp_sidecar_and_per_take_audio_metadata_v3() -> None:
    spec = terminal_spec()
    spec["beats"] = [
        {
            "id": "one",
            "narration_take": "joined",
            "narration": "First word.",
            "actions": [{"run": "true"}],
        },
        {
            "id": "two",
            "narration_take": "joined",
            "narration": "Second.",
            "actions": [{"run": "true"}],
        },
    ]
    plan = normalize_recording_plan(spec)
    take = plan.narration_takes[0]
    sidecar = narration_timestamp_sidecar_payload(
        take,
        duration_ms=1500,
        words=[
            {
                "text": "First",
                "text_start": 0,
                "text_end": 5,
                "start_ms": 100,
                "end_ms": 400,
            },
            {
                "text": "word.",
                "text_start": 6,
                "text_end": 11,
                "start_ms": 450,
                "end_ms": 900,
            },
            {
                "text": "Second.",
                "text_start": 12,
                "text_end": 19,
                "start_ms": 950,
                "end_ms": 1400,
            },
        ],
    )
    metadata = narration_audio_metadata_v3_payload(
        plan,
        take_audio_paths={"joined": "audio/joined-" + ("a" * 64) + ".mp3"},
        take_audio_sha256={"joined": "a" * 64},
        take_durations_ms={"joined": 1500},
        timestamp_paths={"joined": "timestamps/joined.json"},
    )

    assert sidecar["version"] == 1
    assert sidecar["members"][0]["source_start_ms"] == 0
    assert sidecar["members"][1]["source_start_ms"] == 950
    assert sidecar["members"][1]["source_end_ms"] == 1500
    assert metadata["version"] == 3
    assert metadata["duration_ms"] == 1500
    assert metadata["takes"][0]["sha256"] == "a" * 64
    assert metadata["takes"][0]["members"][1]["beat_id"] == "two"


@pytest.mark.parametrize(
    ("synthesis_text", "wait_offset", "next_text_start"),
    [
        ("workspace. The", 10, 11),
        ("workspace The", 9, 10),
        ("workspace.   The", 10, 13),
        ("workspace.\n\nThe", 10, 12),
        ("workspace.   The", 12, 13),
    ],
)
def test_timestamp_sidecar_places_wait_inside_inter_word_silence(
    synthesis_text: str,
    wait_offset: int,
    next_text_start: int,
) -> None:
    take = NarrationTakePlan(
        id="take",
        explicit=True,
        members=(
            NarrationTakeMemberPlan(
                beat_id="beat",
                text=synthesis_text,
                text_start=0,
                text_end=len(synthesis_text),
            ),
        ),
        synthesis_text=synthesis_text,
        anchors=(
            NarrationTakeAnchorPlan(
                beat_id="beat", id="anchor", text_offset=wait_offset
            ),
        ),
        waits=(
            NarrationTakeWaitPlan(
                beat_id="beat",
                target="command",
                text_offset=wait_offset,
                gap_ms=200,
            ),
        ),
    )
    first_text_end = next(
        (index for index, character in enumerate(synthesis_text) if character.isspace()),
        len(synthesis_text),
    )
    first_text = synthesis_text[:first_text_end]
    words = [
        {
            "text": first_text,
            "text_start": 0,
            "text_end": len(first_text),
            "start_ms": 100,
            "end_ms": 500,
        },
        {
            "text": "The",
            "text_start": next_text_start,
            "text_end": next_text_start + 3,
            "start_ms": 900,
            "end_ms": 1100,
        },
    ]

    sidecar = narration_timestamp_sidecar_payload(
        take, duration_ms=1200, words=words
    )

    assert sidecar["waits"][0]["source_ms"] == 700
    expected_anchor_ms = 500 if wait_offset == len(first_text) else 900
    assert sidecar["anchors"][0]["source_ms"] == expected_anchor_ms


def test_timestamp_sidecar_places_final_wait_at_take_duration() -> None:
    take = NarrationTakePlan(
        id="take",
        explicit=True,
        members=(
            NarrationTakeMemberPlan(
                beat_id="beat", text="Done.", text_start=0, text_end=5
            ),
        ),
        synthesis_text="Done.",
        anchors=(),
        waits=(
            NarrationTakeWaitPlan(
                beat_id="beat", target="command", text_offset=5, gap_ms=200
            ),
        ),
    )

    sidecar = narration_timestamp_sidecar_payload(
        take,
        duration_ms=900,
        words=[
            {
                "text": "Done.",
                "text_start": 0,
                "text_end": 5,
                "start_ms": 100,
                "end_ms": 500,
            }
        ],
    )

    assert sidecar["waits"][0]["source_ms"] == 900


@pytest.mark.parametrize(
    ("synthesis_text", "wait_offset", "word_text_start"),
    [
        ("Hello", 0, 0),
        ("  Hello", 0, 2),
        ("  Hello", 1, 2),
        ("  Hello", 2, 2),
    ],
)
def test_timestamp_sidecar_places_leading_wait_before_first_word(
    synthesis_text: str,
    wait_offset: int,
    word_text_start: int,
) -> None:
    take = NarrationTakePlan(
        id="take",
        explicit=True,
        members=(
            NarrationTakeMemberPlan(
                beat_id="beat",
                text=synthesis_text,
                text_start=0,
                text_end=len(synthesis_text),
            ),
        ),
        synthesis_text=synthesis_text,
        anchors=(),
        waits=(
            NarrationTakeWaitPlan(
                beat_id="beat",
                target="command",
                text_offset=wait_offset,
                gap_ms=200,
            ),
        ),
    )

    sidecar = narration_timestamp_sidecar_payload(
        take,
        duration_ms=800,
        words=[
            {
                "text": "Hello",
                "text_start": word_text_start,
                "text_end": word_text_start + 5,
                "start_ms": 120,
                "end_ms": 620,
            }
        ],
    )

    assert sidecar["waits"][0]["source_ms"] == 0


def test_timestamp_sidecar_does_not_snap_markers_inside_a_word() -> None:
    take = NarrationTakePlan(
        id="take",
        explicit=True,
        members=(
            NarrationTakeMemberPlan(
                beat_id="beat", text="workspace", text_start=0, text_end=9
            ),
        ),
        synthesis_text="workspace",
        anchors=(NarrationTakeAnchorPlan(beat_id="beat", id="anchor", text_offset=4),),
        waits=(
            NarrationTakeWaitPlan(
                beat_id="beat", target="command", text_offset=4, gap_ms=200
            ),
        ),
    )

    sidecar = narration_timestamp_sidecar_payload(
        take,
        duration_ms=1000,
        words=[
            {
                "text": "workspace",
                "text_start": 0,
                "text_end": 9,
                "start_ms": 100,
                "end_ms": 900,
            }
        ],
    )

    assert sidecar["anchors"][0]["source_ms"] == 456
    assert sidecar["waits"][0]["source_ms"] == 456


def test_timestamp_sidecar_rejects_text_mismatch() -> None:
    plan = normalize_recording_plan(terminal_spec())
    with pytest.raises(AudioError, match="does not match"):
        narration_timestamp_sidecar_payload(
            plan.narration_takes[0],
            duration_ms=100,
            words=[
                {
                    "text": "Wrong",
                    "text_start": 0,
                    "text_end": 5,
                    "start_ms": 0,
                    "end_ms": 100,
                }
            ],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("text", 7), ("text_start", "0"), ("end_ms", True)],
)
def test_timestamp_sidecar_rejects_coerced_types(field: str, value: object) -> None:
    plan = normalize_recording_plan(browser_spec())
    word = {
        "text": "Welcome",
        "text_start": 0,
        "text_end": 7,
        "start_ms": 0,
        "end_ms": 500,
    }
    word[field] = value

    with pytest.raises(AudioError, match="invalid narration timestamp word"):
        narration_timestamp_sidecar_payload(
            plan.narration_takes[0], duration_ms=500, words=[word]
        )


def test_normalization_rejects_duplicate_internal_narration_entries() -> None:
    spec = browser_spec()
    entry = {"id": "create", "text": "Open the menu.", "anchors": [], "waits": []}
    spec["narration"] = {"beats": [entry, dict(entry)]}

    with pytest.raises(RecordingPlanError, match="duplicate internal narration"):
        normalize_recording_plan(spec)


@pytest.mark.parametrize("duration", [True, "500"])
def test_audio_metadata_rejects_coerced_duration_types(duration: object) -> None:
    plan = normalize_recording_plan(terminal_spec())

    with pytest.raises(AudioError, match="must be an integer"):
        narration_audio_metadata_v3_payload(
            plan,
            take_audio_paths={
                plan.narration_takes[0].id: "audio/terminal-" + ("a" * 64) + ".mp3"
            },
            take_audio_sha256={plan.narration_takes[0].id: "a" * 64},
            take_durations_ms={plan.narration_takes[0].id: duration},
            timestamp_paths={plan.narration_takes[0].id: "timestamps/terminal.json"},
        )
