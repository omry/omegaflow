from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any, Literal, get_args, get_type_hints

import pytest
from omegaconf import OmegaConf

import omegaflow.recording_plan as recording_plan_module
from omegaflow.audio import (
    AudioError,
    AudioSettings,
    narration_audio_metadata_v1_payload,
    narration_take_cache_key,
    narration_take_filename_id,
    narration_take_review_warning,
    narration_timestamp_sidecar_payload,
    plan_narration_take_audio,
)
from omegaflow.presentation_schema import BrowserPayloadV1, PresentationManifestV1
from omegaflow.recording_plan import (
    BrowserActionPlan,
    BrowserPaneRecordingPlan,
    EventEndpoint,
    EventRef,
    JoinPlan,
    NarrationTakeAnchorPlan,
    NarrationTakeMemberPlan,
    NarrationTakePlan,
    NarrationTakeWaitPlan,
    NarrationSegmentPlan,
    NarrationStreamPlan,
    OuterBeatPlan,
    OuterBeatTransitionPlan,
    OuterPaneTrackPlan,
    PaneBeatPlan,
    PaneKind,
    PaneLayoutPlan,
    PanePlan,
    PanePresentationPlan,
    PaneTitlePlan,
    PaneTransitionPlan,
    RecordingPlanError,
    StreamKind,
    StreamPosition,
    StreamRef,
    TerminalPaneRecordingPlan,
    TextHighlightEffectPlan,
    TextHighlightTargetPlan,
    normalize_recording_plan,
    terminal_action_id,
    validate_recording_modalities,
)
from omegaflow.studio_config import (
    OuterBeatPaneTrackConfig,
    PaneBeatConfig,
    PaneChromeStyle,
    PaneConfig,
    PaneLayoutConfig,
    PaneTitleAlignmentX,
    PaneTitleAlignmentY,
    PaneTitleConfig,
    PaneTransitionConfig,
    RecordingPaneChromeConfig,
    RecordingNarrationConfig,
    RecordingPresentationConfig,
    RecordingSpec,
    USER_RECORDING_YAML_SCHEMAS,
)


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


def test_normalization_bounds_authored_plan_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recording_plan_module, "PRESENTATION_ITEM_LIMIT", 1)

    with pytest.raises(RecordingPlanError, match="aggregate structure exceeds 1"):
        normalize_recording_plan(browser_spec())


def test_terminal_command_accepts_allowlisted_scoped_environment() -> None:
    spec = {
        "id": "scoped-environment",
        "beats": [
            {
                "id": "build",
                "actions": [
                    {
                        "commands": [
                            {
                                "run": "omegaflow recording=test-video action=build",
                                "with_env": ["OPENAI_OMEGAFLOW_API_KEY"],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    plan = normalize_recording_plan(spec)

    assert list(plan.beats[0].actions[0].config["commands"][0]["with_env"]) == [
        "OPENAI_OMEGAFLOW_API_KEY"
    ]


def test_terminal_command_rejects_non_allowlisted_scoped_environment() -> None:
    spec = {
        "id": "scoped-environment",
        "beats": [
            {
                "id": "build",
                "actions": [
                    {
                        "commands": [
                            {
                                "run": "true",
                                "with_env": ["UNRELATED_SECRET"],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    with pytest.raises(RecordingPlanError, match="not an allowlisted"):
        normalize_recording_plan(spec)


def test_realtime_terminal_command_accepts_typed_input_steps() -> None:
    spec = terminal_spec()
    command = spec["beats"][0]["actions"][0]["commands"][0]
    command["timing"] = "realtime"
    command["input"] = [
        {"wait_for": "Ready", "timeout": 3},
        {"text": "updated", "interval": 0.01},
        {"key": "enter"},
        {"control": "x"},
        {"control": "_"},
        {"pause": 0.1},
    ]

    plan = normalize_recording_plan(spec)

    normalized = plan.beats[0].actions[0].config["commands"][0]
    assert normalized["input"][0]["wait_for"] == "Ready"
    assert normalized["input"][1]["interval"] == 0.01
    assert normalized["input"][2]["key"] == "enter"


@pytest.mark.parametrize(
    ("input_step", "message"),
    [
        ({"text": "a", "key": "enter"}, "exactly one operation"),
        ({"key": "f13"}, "unsupported key"),
        ({"control": "xx"}, "single ASCII letter"),
        ({"pause": -1}, "non-negative number"),
        ({"wait_for": ""}, "non-empty string"),
        ({"text": "value", "timeout": 1}, "timeout is only valid with wait_for"),
        ({"key": "enter", "interval": 0.1}, "interval is only valid with text"),
    ],
)
def test_realtime_terminal_command_rejects_invalid_input_steps(
    input_step: dict[str, object], message: str
) -> None:
    spec = terminal_spec()
    command = spec["beats"][0]["actions"][0]["commands"][0]
    command["timing"] = "realtime"
    command["input"] = [input_step]

    with pytest.raises(RecordingPlanError, match=message):
        normalize_recording_plan(spec)


def test_terminal_command_input_requires_realtime_timing() -> None:
    spec = terminal_spec()
    command = spec["beats"][0]["actions"][0]["commands"][0]
    command["input"] = [{"key": "enter"}]

    with pytest.raises(RecordingPlanError, match="input requires timing: realtime"):
        normalize_recording_plan(spec)


@pytest.mark.parametrize("output", ["suppress", {"replace": "hidden"}])
def test_terminal_command_input_requires_real_output(output: object) -> None:
    spec = terminal_spec()
    command = spec["beats"][0]["actions"][0]["commands"][0]
    command["timing"] = "realtime"
    command["output"] = output
    command["input"] = [{"key": "enter"}]

    with pytest.raises(RecordingPlanError, match="input requires output: real"):
        normalize_recording_plan(spec)


def test_terminal_step_does_not_silently_drop_scoped_environment() -> None:
    spec = {
        "id": "scoped-environment",
        "beats": [
            {
                "id": "build",
                "actions": [
                    {
                        "run": "true",
                        "with_env": ["OPENAI_OMEGAFLOW_API_KEY"],
                    }
                ],
            }
        ],
    }

    with pytest.raises(RecordingPlanError, match="only on entries inside commands"):
        normalize_recording_plan(spec)


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


def explicit_browser_handoff_spec(
    browser_handoff: object,
) -> dict[str, object]:
    return {
        "id": "explicit-browser-handoff",
        "browser": {},
        "panes": [
            {"id": "terminal", "kind": "terminal"},
            {"id": "primary", "kind": "browser"},
            {"id": "secondary", "kind": "browser"},
        ],
        "beats": [
            {
                "id": "handoff",
                "layout": {
                    "areas": [["terminal", "primary", "secondary"]],
                },
                "panes": {
                    "terminal": [
                        {
                            "id": "session",
                            "actions": [
                                {
                                    "id": "watch",
                                    "run": "omegaflow recording=demo action=watch",
                                    "browser_handoff": browser_handoff,
                                    "timing": "realtime",
                                }
                            ],
                        }
                    ],
                    "primary": [
                        {
                            "id": "main",
                            "actions": [
                                {
                                    "id": "open-main",
                                    "open_page": {
                                        "url": "https://example.test/main",
                                    },
                                }
                            ],
                        }
                    ],
                    "secondary": [
                        {
                            "id": "preview",
                            "actions": [
                                {
                                    "id": "open-preview",
                                    "open_page": {
                                        "handoff": "watch",
                                    },
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    }


def test_omegaconf_schema_authority_supports_versioned_artifacts() -> None:
    for schema in (RecordingSpec, BrowserPayloadV1, PresentationManifestV1):
        assert OmegaConf.structured(schema) is not None


def test_explicit_browser_handoff_targets_named_browser_pane() -> None:
    plan = normalize_recording_plan(
        explicit_browser_handoff_spec({"target": "secondary"})
    )

    assert len(plan.browser_handoffs) == 1
    handoff = plan.browser_handoffs[0]
    assert handoff.id == "watch"
    assert handoff.producer_pane_id == "terminal"
    assert handoff.target_pane_id == "secondary"
    assert handoff.consumer_action_id == "open-preview"


def test_explicit_browser_handoff_shortcut_rejects_ambiguous_target() -> None:
    with pytest.raises(
        RecordingPlanError,
        match="browser_handoff target is ambiguous.*primary.*secondary",
    ):
        normalize_recording_plan(explicit_browser_handoff_spec(True))


def test_explicit_browser_handoff_shortcut_resolves_unique_target() -> None:
    spec = explicit_browser_handoff_spec(True)
    spec["panes"] = [
        pane for pane in spec["panes"] if pane["id"] != "primary"
    ]
    beat = spec["beats"][0]
    beat["layout"]["areas"] = [["terminal", "secondary"]]
    del beat["panes"]["primary"]

    plan = normalize_recording_plan(spec)

    assert plan.browser_handoffs[0].target_pane_id == "secondary"


def test_explicit_browser_handoff_rejects_non_browser_target() -> None:
    with pytest.raises(
        RecordingPlanError,
        match="browser_handoff target 'terminal' is not a browser pane",
    ):
        normalize_recording_plan(
            explicit_browser_handoff_spec({"target": "terminal"})
        )


def test_explicit_browser_handoff_rejects_consumer_in_different_pane() -> None:
    spec = explicit_browser_handoff_spec({"target": "primary"})

    with pytest.raises(
        RecordingPlanError,
        match="target 'primary' does not consume exactly one",
    ):
        normalize_recording_plan(spec)


def test_explicit_browser_handoff_rejects_dependency_on_producer_end() -> None:
    spec = explicit_browser_handoff_spec({"target": "secondary"})
    consumer = spec["beats"][0]["panes"]["secondary"][0]["actions"][0]
    consumer["after"] = "terminal.session.watch.ended"

    with pytest.raises(
        RecordingPlanError,
        match="capture dependency cycle",
    ):
        normalize_recording_plan(spec)


def test_multi_pane_authoring_foundation_is_omegaconf_typed() -> None:
    for schema in (
        PaneConfig,
        PaneBeatConfig,
        PaneLayoutConfig,
        PaneTransitionConfig,
        OuterBeatPaneTrackConfig,
        RecordingNarrationConfig,
    ):
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


def test_terminal_text_highlight_targets_are_typed_and_bound_to_narration_anchors() -> None:
    spec = terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = (
        "@highlight_start@ Project settings. @highlight_end@ @run@ Run it. "
        "@wait:done@"
    )
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "targets": [
                    {
                        "text": "audio:\n  enabled: true",
                    },
                    {
                        "regex": r"config-\d+\.yaml",
                        "occurrence": 2,
                    },
                ],
                "color": "brand",
                "start": "@highlight_start@",
                "end": "@highlight_end@",
            }
        }
    ]

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].effects == (
        TextHighlightEffectPlan(
            pane_id="main",
            targets=(
                TextHighlightTargetPlan(
                    kind="text",
                    pattern="audio:\n  enabled: true",
                    occurrence=1,
                ),
                TextHighlightTargetPlan(
                    kind="regex",
                    pattern=r"config-\d+\.yaml",
                    occurrence=2,
                ),
            ),
            color="brand",
            start_anchor="highlight_start",
            end_anchor="highlight_end",
        ),
    )


def test_terminal_text_highlight_requires_narration_audio() -> None:
    spec = terminal_spec()
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "targets": [{"text": "config.yaml"}],
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
            {"highlight": {"targets": [], "start": "@start@", "end": "@end@"}},
            r"beats\.0\.effects\.0\.highlight\.targets must be non-empty",
        ),
        (
            {
                "highlight": {
                    "targets": [{"text": "", "regex": None}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0 must contain exactly "
            r"one of: text, regex",
        ),
        (
            {
                "highlight": {
                    "targets": [{"text": "status", "regex": r"status-\d+"}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0 must contain exactly "
            r"one of: text, regex",
        ),
        (
            {
                "highlight": {
                    "targets": [{"regex": "["}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0\.regex is invalid",
        ),
        (
            {
                "highlight": {
                    "targets": [{"regex": ".*"}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0\.regex "
            r"must not match empty text",
        ),
        (
            {
                "highlight": {
                    "targets": [{"regex": r"\b"}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0\.regex "
            r"must not match empty text",
        ),
        (
            {
                "highlight": {
                    "targets": [{"regex": r"\N{EM DASH}"}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0\.regex "
            r"uses unsupported syntax",
        ),
        (
            {
                "highlight": {
                    "targets": [{"text": "config.yaml"}],
                    "start": "@missing@",
                    "end": "@end@",
                }
            },
            r"references unknown start anchor @missing@",
        ),
        (
            {
                "highlight": {
                    "targets": [{"text": "config.yaml"}],
                    "start": "@end@",
                    "end": "@start@",
                }
            },
            r"start anchor @end@ must precede end anchor @start@",
        ),
        (
            {
                "highlight": {
                    "targets": [{"text": "config.yaml", "occurrence": 0}],
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"beats\.0\.effects\.0\.highlight\.targets\.0\.occurrence must be positive",
        ),
        (
            {
                "highlight": {
                    "targets": [{"text": "config.yaml"}],
                    "color": "red",
                    "start": "@start@",
                    "end": "@end@",
                }
            },
            r"Invalid value 'red', expected one of \[cue, brand\]",
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


def test_terminal_text_highlight_accepts_safe_grouping_and_alternation() -> None:
    spec = terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = (
        "@start@ Explain. @end@ @run@ Run it. @wait:done@"
    )
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "targets": [
                    {"regex": r"Renderer: (status|ready)+"},
                    {"regex": r"(a+)+$"},
                ],
                "start": "@start@",
                "end": "@end@",
            }
        }
    ]

    plan = normalize_recording_plan(spec)

    assert tuple(
        target.pattern
        for target in plan.beats[0].effects[0].targets
    ) == (
        r"Renderer: (status|ready)+",
        r"(a+)+$",
    )


def test_text_highlight_rejects_pane_without_text_surface() -> None:
    spec = browser_spec()
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "targets": [{"text": "Create"}],
                "start": "@menu@",
                "end": "@menu@",
            }
        }
    ]

    with pytest.raises(
        RecordingPlanError,
        match=r"pane 'main' does not expose a text surface",
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


def test_beat_timing_defaults_terminal_commands_and_browser_actions() -> None:
    terminal = normalize_recording_plan(
        {
            "id": "terminal-timing-default",
            "beats": [
                {
                    "id": "terminal",
                    "timing": "realtime",
                    "actions": [
                        {
                            "timing": "presentation",
                            "commands": [
                                {"id": "first", "run": "printf first"},
                                {
                                    "id": "second",
                                    "run": "printf second",
                                    "timing": "realtime",
                                },
                            ],
                        },
                        {"run": "printf inherited"},
                    ],
                }
            ],
        }
    )
    first_action = terminal.beats[0].actions[0].config
    assert [command["timing"] for command in first_action["commands"]] == [
        "presentation",
        "realtime",
    ]
    assert terminal.beats[0].actions[1].config["timing"] == "realtime"

    browser = browser_spec()
    browser["beats"][0]["timing"] = "realtime"
    browser["beats"][0]["actions"][1]["timing"] = "presentation"
    plan = normalize_recording_plan(browser)
    assert [action.config["timing"] for action in plan.beats[0].actions] == [
        "realtime",
        "presentation",
    ]


def test_browser_until_requires_realtime_non_wait_action() -> None:
    condition = {"visible": {"text": "Complete", "exact": True}, "timeout_ms": 5000}
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["until"] = condition
    with pytest.raises(RecordingPlanError, match="until requires timing: realtime"):
        normalize_recording_plan(spec)

    spec["beats"][0]["actions"][1]["timing"] = "realtime"
    plan = normalize_recording_plan(spec)
    assert plan.beats[0].actions[1].config["until"]["timeout_ms"] == 5000

    spec = browser_spec()
    spec["beats"][0]["actions"].append(
        {
            "id": "wait",
            "timing": "realtime",
            "wait_for": condition,
            "until": condition,
        }
    )
    with pytest.raises(RecordingPlanError, match="wait_for cannot also define until"):
        normalize_recording_plan(spec)


def test_browser_audio_capture_requires_realtime_non_navigation_action() -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["audio"] = "capture"
    with pytest.raises(
        RecordingPlanError,
        match="audio capture requires timing: realtime",
    ):
        normalize_recording_plan(spec)

    spec["beats"][0]["actions"][1]["timing"] = "realtime"
    plan = normalize_recording_plan(spec)
    assert plan.beats[0].actions[1].config["audio"] == "capture"

    spec = browser_spec()
    spec["beats"][0]["actions"][0].update(
        {"audio": "capture", "timing": "realtime"}
    )
    with pytest.raises(
        RecordingPlanError,
        match="open_page does not support audio capture",
    ):
        normalize_recording_plan(spec)

    spec = browser_spec()
    spec["beats"][0]["actions"][1] = {
        "id": "done",
        "timing": "realtime",
        "audio": "capture",
        "set_pointer": {"visible": True},
    }
    with pytest.raises(
        RecordingPlanError,
        match="set_pointer does not support audio capture",
    ):
        normalize_recording_plan(spec)


def test_browser_audio_rejects_unknown_mode() -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1].update(
        {"audio": "record", "timing": "realtime"}
    )

    with pytest.raises(
        RecordingPlanError,
        match="audio must be capture",
    ):
        normalize_recording_plan(spec)


def test_captured_transition_is_rejected_in_favor_of_realtime_timing() -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"][1]["transition"] = "captured"

    with pytest.raises(
        RecordingPlanError,
        match="transition must be cut or fade; use timing: realtime",
    ):
        normalize_recording_plan(spec)


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
            "type_text": {
                "target": {"placeholder": "Search"},
                "text": "query",
                "interval_ms": 40,
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
        {
            "id": "drag",
            "drag": {
                "from": {
                    "target": {"test_id": "sun"},
                    "position": {"x": 0.25, "y": 0.75},
                },
                "to": {"target": {"test_id": "sky"}},
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
    ("drag", "match"),
    [
        ({}, "must contain from and to"),
        (
            {
                "from": {"target": {"test_id": "sun"}},
                "to": {"target": {"test_id": "sky"}},
                "duration_ms": 500,
            },
            "unknown fields",
        ),
        (
            {
                "from": {
                    "target": {"test_id": "sun"},
                    "position": {"x": -0.1, "y": 0.5},
                },
                "to": {"target": {"test_id": "sky"}},
            },
            "position values must be between 0 and 1",
        ),
        (
            {
                "from": {"target": {"test_id": "sun"}},
                "to": {
                    "target": {"test_id": "sky"},
                    "position": {"x": 0.5},
                },
            },
            "position must contain x and y",
        ),
    ],
)
def test_rejects_invalid_browser_drag(drag: dict, match: str) -> None:
    spec = browser_spec()
    spec["beats"][0]["actions"].append({"id": "drag", "drag": drag})

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

    with pytest.raises(
        RecordingPlanError,
        match="no matching browser_handoff producer targeting its pane",
    ):
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


def test_single_pane_shorthand_normalizes_to_implicit_main_track() -> None:
    plan = normalize_recording_plan(terminal_spec())

    beat = plan.beats[0]
    assert isinstance(beat, OuterBeatPlan)
    assert beat.layout == PaneLayoutPlan(areas=(("main",),))
    assert len(beat.pane_tracks) == 1
    track = beat.pane_tracks[0]
    assert track.pane_id == "main"
    assert len(track.beats) == 1
    pane_beat = track.beats[0]
    assert isinstance(pane_beat, PaneBeatPlan)
    assert pane_beat.id == beat.id
    assert track.kind is PaneKind.terminal
    assert pane_beat.actions == beat.actions
    assert pane_beat.checks == beat.checks
    assert not hasattr(pane_beat, "narration_text")
    assert not hasattr(pane_beat, "viewer_hold_ms")


def visualization_terminal_spec() -> dict[str, object]:
    return {
        "id": "visualization-terminal",
        "panes": [
            {
                "id": "definition",
                "title": {
                    "text": "Beat definition",
                    "alignment_x": "right",
                    "alignment_y": "bottom",
                    "position_x": "0.8rem",
                    "position_y": "0.7rem",
                },
                "kind": "visualization",
            },
            {
                "id": "terminal",
                "title": "hidden",
                "kind": "terminal",
            },
        ],
        "beats": [
            {
                "id": "explain",
                "heading": "Explain the definition",
                "layout": {"areas": [["definition"], ["terminal"]]},
                "panes": {
                    "definition": [
                        {
                            "id": "show-definition",
                            "actions": [
                                {
                                    "id": "show-source",
                                    "show": {
                                        "language": "yaml",
                                        "text": (
                                            "effects:\n"
                                            "- highlight:\n"
                                            '    regex: "Renderer: .*"\n'
                                        ),
                                    },
                                }
                            ],
                        }
                    ],
                    "terminal": [
                        {
                            "id": "show-status",
                            "actions": [
                                {
                                    "id": "run-status",
                                    "run": "printf 'Renderer: ready\\n'",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    }


def sequential_visualization_spec() -> dict[str, object]:
    spec = visualization_terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["narration"] = {"id": "voiceover"}
    beat = spec["beats"][0]
    beat["narration"] = (
        "First, inspect the exact target. "
        "@regex@ Next, inspect the regular expression. "
        "@combined@ Finally, combine both targets."
    )
    beat["panes"]["definition"] = [
        {
            "id": "exact",
            "actions": [
                {
                    "id": "show-exact",
                    "show": {
                        "language": "yaml",
                        "text": '- text: "Renderer: ready"\n',
                    },
                }
            ],
        },
        {
            "id": "regex",
            "after": "voiceover.regex.started",
            "actions": [
                {
                    "id": "show-regex",
                    "show": {
                        "language": "yaml",
                        "text": "- regex: 'Elapsed: .*'\n",
                    },
                }
            ],
        },
        {
            "id": "combined",
            "after": "voiceover.combined.started",
            "actions": [
                {
                    "id": "show-combined",
                    "show": {
                        "language": "yaml",
                        "text": (
                            '- text: "Renderer: ready"\n'
                            "- regex: 'Elapsed: .*'\n"
                        ),
                    },
                }
            ],
        },
    ]
    return spec


def cross_capture_spec() -> dict[str, object]:
    return {
        "id": "cross-capture",
        "browser": {"base_url": "http://127.0.0.1:18765"},
        "panes": [
            {"id": "terminal", "kind": "terminal"},
            {"id": "browser", "kind": "browser"},
        ],
        "beats": [
            {
                "id": "synchronize",
                "layout": {"areas": [["terminal", "browser"]]},
                "panes": {
                    "terminal": [
                        {
                            "id": "session",
                            "actions": [
                                {
                                    "id": "start-app",
                                    "run": "python3 sync_demo.py start",
                                },
                                {
                                    "id": "verify-ready",
                                    "run": "python3 sync_demo.py status",
                                    "after": "browser.interaction.mark-ready.ended",
                                },
                            ],
                        }
                    ],
                    "browser": [
                        {
                            "id": "interaction",
                            "actions": [
                                {
                                    "id": "open-app",
                                    "open_page": {"url": "/"},
                                    "after": "terminal.session.start-app.ended",
                                },
                                {
                                    "id": "mark-ready",
                                    "click": {
                                        "target": {
                                            "role": "button",
                                            "name": "Mark ready",
                                        }
                                    },
                                },
                            ],
                        }
                    ],
                },
            }
        ],
    }


def test_explicit_visualization_and_terminal_authoring_normalizes_to_typed_plan() -> None:
    plan = normalize_recording_plan(visualization_terminal_spec())

    assert plan.presentation["pane_chrome"]["style"] == "framed"
    assert plan.panes == (
        PanePlan(
            id="definition",
            kind=PaneKind.visualization,
            title=PaneTitlePlan(
                text="Beat definition",
                alignment_x="right",
                alignment_y="bottom",
                position_x="0.8rem",
                position_y="0.7rem",
            ),
        ),
        PanePlan(
            id="terminal",
            kind=PaneKind.terminal,
            title=PaneTitlePlan(visible=False),
        ),
    )
    beat = plan.beats[0]
    assert beat.layout == PaneLayoutPlan(
        areas=(("definition",), ("terminal",))
    )
    assert [(track.pane_id, track.kind) for track in beat.pane_tracks] == [
        ("definition", PaneKind.visualization),
        ("terminal", PaneKind.terminal),
    ]
    visualization = beat.pane_tracks[0].beats[0]
    action = visualization.actions[0]
    assert type(action).__name__ == "VisualizationActionPlan"
    assert action.id == "show-source"
    assert action.language == "yaml"
    assert action.text.startswith("effects:\n")
    terminal = beat.pane_tracks[1].beats[0]
    assert terminal.actions[0].config["commands"][0]["id"] == "run-status"


def test_container_beat_owns_highlight_for_visualization_pane() -> None:
    spec = visualization_terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = (
        "@ready_start@ Explain readiness. @ready_end@ Done."
    )
    show = spec["beats"][0]["panes"]["definition"][0]["actions"][0]["show"]
    show["text"] = (
        "narration: '@ready@ Show readiness.'\n"
        "start: '@ready@'\n"
    )
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "pane": "definition",
                "color": "brand",
                "targets": [
                    {"text": "@ready@", "occurrence": 1},
                    {"text": "@ready@", "occurrence": 2},
                ],
                "start": "@ready_start@",
                "end": "@ready_end@",
            },
        }
    ]

    plan = normalize_recording_plan(spec)

    assert plan.beats[0].effects == (
        TextHighlightEffectPlan(
            pane_id="definition",
            targets=(
                TextHighlightTargetPlan("text", "@ready@", 1),
                TextHighlightTargetPlan("text", "@ready@", 2),
            ),
            color="brand",
            start_anchor="ready_start",
            end_anchor="ready_end",
        ),
    )


def test_multi_pane_highlight_requires_target_pane() -> None:
    spec = visualization_terminal_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = "@start@ Explain. @end@ Done."
    spec["beats"][0]["effects"] = [
        {
            "highlight": {
                "targets": [{"text": "effects:"}],
                "start": "@start@",
                "end": "@end@",
            },
        }
    ]

    with pytest.raises(
        RecordingPlanError,
        match=r"effects\.0\.highlight\.pane is required",
    ):
        normalize_recording_plan(spec)


def test_multi_pane_highlight_rejects_superseded_pane_local_effects() -> None:
    spec = visualization_terminal_spec()
    pane_beat = spec["beats"][0]["panes"]["definition"][0]
    pane_beat["effects"] = [
        {
            "highlight": {
                "targets": [{"text": "effects:"}],
                "start": "@start@",
                "end": "@end@",
            },
        }
    ]

    with pytest.raises(
        RecordingPlanError,
        match=r"invalid beats\.0\.panes\.definition\.0",
    ):
        normalize_recording_plan(spec)


def test_multi_pane_highlight_rejects_superseded_show_level_effects() -> None:
    spec = visualization_terminal_spec()
    show = spec["beats"][0]["panes"]["definition"][0]["actions"][0]["show"]
    show["highlight"] = {
        "targets": [{"text": "effects:"}],
        "start": "@start@",
        "end": "@end@",
    }

    with pytest.raises(
        RecordingPlanError,
        match=r"invalid beats\.0\.panes\.definition\.0",
    ):
        normalize_recording_plan(spec)


def test_sequential_visualization_beats_join_narration_segment_events() -> None:
    plan = normalize_recording_plan(sequential_visualization_spec())

    track = plan.beats[0].pane_tracks[0]
    assert [beat.id for beat in track.beats] == ["exact", "regex", "combined"]
    assert track.beats[0].start_join is None
    assert track.beats[1].start_join == JoinPlan(
        waiting_stream=StreamRef(StreamKind.pane, "definition"),
        waiting_position=StreamPosition(
            pane_beat_id="regex",
            action_id=None,
        ),
        event=EventRef(
            stream=StreamRef(StreamKind.narration, "voiceover"),
            action_id="regex",
            endpoint=EventEndpoint.started,
        ),
    )
    assert track.beats[2].start_join is not None
    assert track.beats[2].start_join.event.qualified_id == (
        "voiceover.combined.started"
    )


def test_first_pane_beat_rejects_a_transition_without_prior_content() -> None:
    spec = sequential_visualization_spec()
    spec["beats"][0]["panes"]["definition"][0]["transition"] = {
        "kind": "fade",
        "duration_ms": 100,
    }

    with pytest.raises(
        RecordingPlanError,
        match=r"panes\.definition\.0\.transition is only valid between pane beats",
    ):
        normalize_recording_plan(spec)


def test_sequential_visualization_beat_rejects_unknown_narration_event() -> None:
    spec = sequential_visualization_spec()
    spec["beats"][0]["panes"]["definition"][1]["after"] = (
        "voiceover.missing.started"
    )

    with pytest.raises(
        RecordingPlanError,
        match="unknown event 'voiceover.missing.started'",
    ):
        normalize_recording_plan(spec)


def test_pane_chrome_style_accepts_none_and_rejects_unknown_values() -> None:
    spec = visualization_terminal_spec()
    spec["presentation"] = {"pane_chrome": {"style": "none"}}

    plan = normalize_recording_plan(spec)

    chrome = plan.presentation["pane_chrome"]
    assert chrome["style"] == "none"
    assert (
        get_type_hints(RecordingPresentationConfig)["pane_chrome"]
        is RecordingPaneChromeConfig
    )
    assert get_type_hints(PaneTitleConfig) == {
        "visible": bool,
        "text": str | None,
        "alignment_x": PaneTitleAlignmentX,
        "alignment_y": PaneTitleAlignmentY,
        "position_x": str,
        "position_y": str,
    }
    assert get_type_hints(PaneConfig)["title"] == (
        Literal["hidden"] | str | PaneTitleConfig | None
    )

    spec["presentation"] = {"pane_chrome": {"style": "ornate"}}
    with pytest.raises(RecordingPlanError, match="pane_chrome"):
        normalize_recording_plan(spec)

    spec = visualization_terminal_spec()
    spec["panes"][0]["title"]["position_x"] = "calc(1rem + 2px)"
    with pytest.raises(
        RecordingPlanError,
        match="position_x must be a non-negative CSS length",
    ):
        normalize_recording_plan(spec)

    spec = visualization_terminal_spec()
    spec["panes"][0]["title"] = {"text": " "}
    with pytest.raises(RecordingPlanError, match="pane title must be non-empty"):
        normalize_recording_plan(spec)


def test_pane_title_shortcuts_cover_automatic_explicit_and_hidden() -> None:
    spec = visualization_terminal_spec()
    spec["panes"] = [
        {"id": "automatic", "kind": "visualization"},
        {"id": "explicit", "kind": "visualization", "title": "Live output"},
        {"id": "untitled", "kind": "visualization", "title": "hidden"},
        {
            "id": "literal-hidden",
            "kind": "visualization",
            "title": {"text": "hidden"},
        },
    ]
    spec["beats"][0]["layout"]["areas"] = [
        ["automatic"],
        ["explicit"],
        ["untitled"],
        ["literal-hidden"],
    ]
    spec["beats"][0]["panes"] = {
        "automatic": spec["beats"][0]["panes"]["definition"],
        "explicit": spec["beats"][0]["panes"]["definition"],
        "untitled": spec["beats"][0]["panes"]["definition"],
        "literal-hidden": spec["beats"][0]["panes"]["definition"],
    }

    plan = normalize_recording_plan(spec)

    assert [pane.title for pane in plan.panes] == [
        PaneTitlePlan(),
        PaneTitlePlan(text="Live output"),
        PaneTitlePlan(visible=False),
        PaneTitlePlan(text="hidden"),
    ]


def test_explicit_terminal_and_browser_actions_normalize_cross_stream_joins() -> None:
    plan = normalize_recording_plan(cross_capture_spec())

    terminal, browser = plan.beats[0].pane_tracks
    assert isinstance(terminal.beats[0].recording, TerminalPaneRecordingPlan)
    assert isinstance(browser.beats[0].recording, BrowserPaneRecordingPlan)
    assert terminal.beats[0].actions[1].start_join == JoinPlan(
        waiting_stream=StreamRef(StreamKind.pane, "terminal"),
        waiting_position=StreamPosition(
            pane_beat_id="session",
            action_id="verify-ready",
        ),
        event=EventRef(
            stream=StreamRef(StreamKind.pane, "browser"),
            pane_beat_id="interaction",
            action_id="mark-ready",
            endpoint=EventEndpoint.ended,
        ),
    )
    assert browser.beats[0].actions[0].start_join is not None
    assert browser.beats[0].actions[0].start_join.event.qualified_id == (
        "terminal.session.start-app.ended"
    )


def test_explicit_multi_pane_narration_wait_targets_unique_action() -> None:
    spec = cross_capture_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = (
        "Start both panes. @wait:mark-ready+300ms@ The result is ready."
    )

    plan = normalize_recording_plan(spec)

    assert [(wait.target, wait.gap_ms) for wait in plan.beats[0].waits] == [
        ("mark-ready", 300)
    ]


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("missing", "references unknown pane action 'missing'"),
        ("duplicate", "references ambiguous pane action 'duplicate'"),
    ],
)
def test_explicit_multi_pane_narration_wait_rejects_unresolved_action(
    target: str,
    message: str,
) -> None:
    spec = cross_capture_spec()
    spec["audio"] = {"enabled": True}
    spec["beats"][0]["narration"] = f"Wait. @wait:{target}@ Continue."
    if target == "duplicate":
        spec["beats"][0]["panes"]["terminal"][0]["actions"][0]["id"] = target
        spec["beats"][0]["panes"]["browser"][0]["actions"][0]["id"] = target

    with pytest.raises(RecordingPlanError, match=message):
        normalize_recording_plan(spec)


def test_nested_pane_beat_timing_inherits_and_overrides_outer_default() -> None:
    spec = cross_capture_spec()
    outer = spec["beats"][0]
    outer["timing"] = "realtime"
    outer["panes"]["browser"][0]["timing"] = "presentation"
    outer["panes"]["browser"][0]["actions"][1]["timing"] = "realtime"

    plan = normalize_recording_plan(spec)

    terminal, browser = plan.beats[0].pane_tracks
    assert [
        action.config["commands"][0]["timing"]
        for action in terminal.beats[0].actions
    ] == ["realtime", "realtime"]
    assert [action.config["timing"] for action in browser.beats[0].actions] == [
        "presentation",
        "realtime",
    ]


def test_explicit_multi_pane_authoring_accepts_two_terminal_pane_streams() -> None:
    spec = visualization_terminal_spec()
    spec["panes"].append({"id": "other-terminal", "kind": "terminal"})
    second_definition = {
        **spec["beats"][0]["panes"]["definition"][0],
        "id": "show-second-definition",
    }
    spec["beats"].append(
        {
            "id": "explain-more",
            "layout": {"areas": [["definition"], ["other-terminal"]]},
            "panes": {
                "definition": [second_definition],
                "other-terminal": [
                    {
                        "id": "show-more-status",
                        "actions": [
                            {
                                "id": "run-more-status",
                                "run": "printf 'Renderer: still ready\\n'",
                            }
                        ],
                    }
                ],
            },
        }
    )

    plan = normalize_recording_plan(spec)

    assert [
        (track.pane_id, track.kind.value)
        for beat in plan.beats
        for track in beat.pane_tracks
        if track.kind.value != "visualization"
    ] == [
        ("terminal", "terminal"),
        ("other-terminal", "terminal"),
    ]


def test_explicit_multi_pane_authoring_rejects_unused_declared_terminal_pane() -> None:
    spec = visualization_terminal_spec()
    spec["panes"].append({"id": "unused", "kind": "terminal"})

    with pytest.raises(
        RecordingPlanError,
        match=r"declared panes are not used: unused",
    ):
        normalize_recording_plan(spec)


def test_cross_stream_join_rejects_unknown_action_event() -> None:
    spec = cross_capture_spec()
    spec["beats"][0]["panes"]["terminal"][0]["actions"][1]["after"] = (
        "browser.interaction.missing.ended"
    )

    with pytest.raises(
        RecordingPlanError,
        match="unknown event 'browser.interaction.missing.ended'",
    ):
        normalize_recording_plan(spec)


def test_cross_stream_join_rejects_capture_dependency_cycle() -> None:
    spec = cross_capture_spec()
    spec["beats"][0]["panes"]["terminal"][0]["actions"][0]["after"] = (
        "browser.interaction.mark-ready.ended"
    )

    with pytest.raises(
        RecordingPlanError,
        match="capture dependency cycle",
    ):
        normalize_recording_plan(spec)


def test_explicit_multi_pane_authoring_rejects_unused_declared_pane() -> None:
    spec = visualization_terminal_spec()
    spec["panes"].append({"id": "unused", "kind": "visualization"})

    with pytest.raises(
        RecordingPlanError,
        match=r"declared panes are not used: unused",
    ):
        normalize_recording_plan(spec)


def test_explicit_multi_pane_authoring_rejects_duplicate_pane_beat_ids_across_outer_beats() -> None:
    spec = visualization_terminal_spec()
    spec["beats"].append(
        {
            "id": "explain-again",
            "layout": {"areas": [["definition"]]},
            "panes": {
                "definition": spec["beats"][0]["panes"]["definition"],
            },
        }
    )

    with pytest.raises(
        RecordingPlanError,
        match=(
            r"duplicate pane beat id 'show-definition' in pane "
            r"'definition' across recording"
        ),
    ):
        normalize_recording_plan(spec)


def test_explicit_multi_pane_authoring_accepts_visualization_only_outer_beat() -> None:
    spec = visualization_terminal_spec()
    spec["beats"].append(
        {
            "id": "visualization-only",
            "layout": {"areas": [["definition"]]},
            "panes": {
                "definition": [
                    {
                        "id": "show-another-definition",
                        "actions": [
                            {
                                "id": "show-another-source",
                                "show": {"text": "Another definition"},
                            }
                        ],
                    }
                ],
            },
        }
    )

    plan = normalize_recording_plan(spec)

    assert plan.beats[1].pane_tracks[0].kind is PaneKind.visualization


def test_explicit_multi_pane_authoring_rejects_mixed_shorthand_fields() -> None:
    spec = visualization_terminal_spec()
    spec["beats"][0]["actions"] = [{"run": "must not be accepted"}]

    with pytest.raises(
        RecordingPlanError,
        match="cannot mix explicit panes with single-pane",
    ):
        normalize_recording_plan(spec)


def test_implicit_main_track_is_local_to_each_mixed_medium_outer_beat() -> None:
    spec = terminal_spec()
    spec["browser"] = {"base_url": "https://example.test"}
    spec["presentation"] = {"browser": {"chrome": {"mode": "hidden"}}}
    spec["beats"].append(
        {
            "id": "browser",
            "medium": "browser",
            "actions": [
                {
                    "id": "open",
                    "open_page": {"url": "/demo"},
                }
            ],
        }
    )

    plan = normalize_recording_plan(spec)

    assert [beat.pane_tracks[0].pane_id for beat in plan.beats] == ["main", "main"]
    assert [beat.pane_tracks[0].kind for beat in plan.beats] == [
        PaneKind.terminal,
        PaneKind.browser,
    ]


def test_event_and_join_plan_identity_is_typed_and_immutable() -> None:
    narration = StreamRef(kind=StreamKind.narration, id="voiceover")
    terminal = StreamRef(kind=StreamKind.pane, id="terminal")
    event = EventRef(
        stream=terminal,
        pane_beat_id="build",
        action_id="start_server",
        endpoint=EventEndpoint.ended,
    )
    join = JoinPlan(
        waiting_stream=narration,
        waiting_position=StreamPosition(action_id="explain"),
        event=event,
        gap_ms=200,
    )

    assert event.qualified_id == "terminal.build.start_server.ended"
    with pytest.raises(FrozenInstanceError):
        join.gap_ms = 0  # type: ignore[misc]
    with pytest.raises(ValueError, match="another stream"):
        JoinPlan(
            waiting_stream=terminal,
            waiting_position=StreamPosition(
                pane_beat_id="verify",
                action_id="check_server",
            ),
            event=event,
        )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: PanePlan(id="not valid", kind=PaneKind.terminal),
            "pane id",
        ),
        (
            lambda: PaneTransitionPlan(duration_ms=-1),
            "pane transition duration",
        ),
        (
            lambda: OuterBeatTransitionPlan(duration_ms=-1),
            "outer beat transition duration",
        ),
        (
            lambda: NarrationStreamPlan(id="not valid", segments=()),
            "narration stream id",
        ),
        (
            lambda: NarrationSegmentPlan(
                id="segment",
                beat_id="beat",
                text_start=4,
                text_end=3,
            ),
            "narration segment range",
        ),
    ],
)
def test_multi_pane_plan_models_reject_invalid_invariants(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_pane_track_requires_unique_valid_pane_beats() -> None:
    pane_beat = PaneBeatPlan(
        id="show",
        start_join=None,
        recording=TerminalPaneRecordingPlan(actions=(), checks=()),
        presentation=PanePresentationPlan(),
        transition=PaneTransitionPlan(),
    )

    with pytest.raises(ValueError, match="pane track must contain"):
        OuterPaneTrackPlan(pane_id="terminal", kind=PaneKind.terminal, beats=())
    with pytest.raises(ValueError, match="duplicate pane beat id"):
        OuterPaneTrackPlan(
            pane_id="terminal",
            kind=PaneKind.terminal,
            beats=(pane_beat, pane_beat),
        )


def test_logical_narration_stream_uses_the_authored_id() -> None:
    spec = terminal_spec()
    spec["narration"] = {"id": "guide"}

    plan = normalize_recording_plan(spec)

    assert plan.narration_stream.id == "guide"
    assert [segment.id for segment in plan.narration_stream.segments] == ["run"]


def test_logical_narration_stream_rejects_an_invalid_id() -> None:
    spec = terminal_spec()
    spec["narration"] = {"id": "not qualified"}

    with pytest.raises(RecordingPlanError, match="narration stream id"):
        normalize_recording_plan(spec)


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


def test_timestamp_sidecar_and_per_take_audio_metadata_v1() -> None:
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
    metadata = narration_audio_metadata_v1_payload(
        plan,
        take_audio_paths={"joined": "audio/joined.mp3"},
        take_durations_ms={"joined": 1500},
        timestamp_paths={"joined": "timestamps/joined.json"},
    )

    assert sidecar["version"] == 1
    assert sidecar["members"][0]["source_start_ms"] == 0
    assert sidecar["members"][1]["source_start_ms"] == 950
    assert sidecar["members"][1]["source_end_ms"] == 1500
    assert metadata["version"] == 1
    assert metadata["duration_ms"] == 1500
    assert metadata["takes"][0]["src"] == "audio/joined.mp3"
    assert metadata["takes"][0]["members"][1]["beat_id"] == "two"


def test_narration_take_filename_ids_are_stable_and_collision_free() -> None:
    take_ids = (
        "plain",
        "__beat__:intro",
        "a:b",
        "a/b",
        "a b",
        "a-b",
        ".",
        "..",
        "日本語",
    )

    filenames = [narration_take_filename_id(take_id) for take_id in take_ids]

    assert filenames[0] == "plain"
    assert len(set(filenames)) == len(take_ids)
    assert filenames == [
        narration_take_filename_id(take_id) for take_id in take_ids
    ]
    assert all(
        "/" not in filename and filename not in {".", ".."} for filename in filenames
    )


def test_narration_take_filename_ids_are_bounded_for_long_authored_ids() -> None:
    take_ids = (
        "a" * 400,
        ("a" * 399) + "b",
        "日本語" * 100,
    )

    filenames = [narration_take_filename_id(take_id) for take_id in take_ids]

    assert len(set(filenames)) == len(take_ids)
    assert all(len(filename.encode("utf-8")) <= 160 for filename in filenames)


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
        narration_audio_metadata_v1_payload(
            plan,
            take_audio_paths={
                plan.narration_takes[0].id: "audio/terminal.mp3"
            },
            take_durations_ms={plan.narration_takes[0].id: duration},
            timestamp_paths={plan.narration_takes[0].id: "timestamps/terminal.json"},
        )


def test_terminal_output_dependencies_resolve_setup_and_explicit_pane_producers() -> None:
    plan = normalize_recording_plan(
        {
            "id": "declared-dependencies",
            "setup": [
                {
                    "id": "prepare",
                    "run": "mkdir -p build",
                    "produces": {"workspace": "build"},
                }
            ],
            "panes": [
                {"id": "left", "kind": "terminal"},
                {"id": "right", "kind": "terminal"},
            ],
            "beats": [
                {
                    "id": "exchange",
                    "layout": {"areas": [["left", "right"]]},
                    "panes": {
                        "left": [
                            {
                                "id": "producer",
                                "actions": [
                                    {
                                        "id": "generate",
                                        "run": "touch build/result",
                                        "inputs": [{"output": "prepare.workspace"}],
                                        "produces": {"result": "build/result"},
                                    }
                                ],
                            }
                        ],
                        "right": [
                            {
                                "id": "consumer",
                                "actions": [
                                    {
                                        "id": "inspect",
                                        "run": "cat build/result",
                                        "inputs": [{"output": "generate.result"}],
                                    }
                                ],
                            }
                        ],
                    },
                }
            ],
        }
    )

    assert plan.output_dependencies == (
        recording_plan_module.OutputDependencyPlan(
            producer_id="generate",
            output_name="result",
            producer_event_id="left.producer.generate",
            consumer_event_id="right.consumer.inspect",
        ),
    )


def test_terminal_output_dependency_rejects_unknown_output() -> None:
    with pytest.raises(RecordingPlanError, match="unknown output 'prepare.missing'"):
        normalize_recording_plan(
            {
                "id": "unknown-output",
                "setup": [
                    {
                        "id": "prepare",
                        "run": "touch result",
                        "produces": {"result": "result"},
                    }
                ],
                "beats": [
                    {
                        "id": "consume",
                        "actions": [
                            {
                                "run": "cat result",
                                "inputs": [{"output": "prepare.missing"}],
                            }
                        ],
                    }
                ],
            }
        )


def test_terminal_output_dependency_rejects_forward_reference_in_one_stream() -> None:
    with pytest.raises(RecordingPlanError, match="before it runs"):
        normalize_recording_plan(
            {
                "id": "forward-output",
                "beats": [
                    {
                        "id": "commands",
                        "actions": [
                            {
                                "run": "cat result",
                                "inputs": [{"output": "generate.result"}],
                            },
                            {
                                "id": "generate",
                                "run": "touch result",
                                "produces": {"result": "result"},
                            },
                        ],
                    }
                ],
            }
        )


def test_recording_plan_rejects_non_adjacent_terminal_continuation() -> None:
    with pytest.raises(
        RecordingPlanError,
        match="must continue the immediately preceding action 'intervening'",
    ):
        normalize_recording_plan(
            {
                "id": "invalid-continuation",
                "panes": [{"id": "terminal", "kind": "terminal"}],
                "beats": [
                    {
                        "id": "first",
                        "layout": {"areas": [["terminal"]]},
                        "panes": {
                            "terminal": [
                                {
                                    "id": "first-pane",
                                    "actions": [
                                        {
                                            "id": "start-editor",
                                            "run": "read -rsn1 value",
                                            "timing": "realtime",
                                            "input": [{"pause": 0}],
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                    {
                        "id": "second",
                        "layout": {"areas": [["terminal"]]},
                        "panes": {
                            "terminal": [
                                {
                                    "id": "second-pane",
                                    "actions": [
                                        {
                                            "id": "intervening",
                                            "run": "true",
                                        },
                                        {
                                            "id": "finish-editor",
                                            "continue_from": "start-editor",
                                            "timing": "realtime",
                                            "input": [{"control": "x"}],
                                        },
                                    ],
                                }
                            ]
                        },
                    },
                ],
            }
        )


@pytest.mark.parametrize("continue_from", ["", "bad id", 42])
def test_recording_plan_rejects_invalid_terminal_continuation_reference(
    continue_from: object,
) -> None:
    with pytest.raises(
        RecordingPlanError,
        match=r"continue_from must be identifier-like",
    ):
        normalize_recording_plan(
            {
                "id": "invalid-continuation",
                "panes": [{"id": "terminal", "kind": "terminal"}],
                "beats": [
                    {
                        "id": "first",
                        "layout": {"areas": [["terminal"]]},
                        "panes": {
                            "terminal": [
                                {
                                    "id": "first-pane",
                                    "actions": [
                                        {
                                            "id": "start-editor",
                                            "run": "true",
                                            "continue_from": continue_from,
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ],
            }
        )


def test_recording_plan_rejects_checks_before_cross_beat_continuation() -> None:
    with pytest.raises(
        RecordingPlanError,
        match=(
            r"terminal pane beat 'first-pane' cannot run checks while action "
            r"'start-editor' is awaiting continue_from"
        ),
    ):
        normalize_recording_plan(
            {
                "id": "invalid-continuation-checks",
                "panes": [{"id": "terminal", "kind": "terminal"}],
                "beats": [
                    {
                        "id": "first",
                        "layout": {"areas": [["terminal"]]},
                        "panes": {
                            "terminal": [
                                {
                                    "id": "first-pane",
                                    "actions": [
                                        {
                                            "id": "start-editor",
                                            "run": "read -rsn1 value",
                                            "timing": "realtime",
                                            "input": [{"pause": 0}],
                                        }
                                    ],
                                    "checks": [{"run": "true"}],
                                }
                            ]
                        },
                    },
                    {
                        "id": "second",
                        "layout": {"areas": [["terminal"]]},
                        "panes": {
                            "terminal": [
                                {
                                    "id": "second-pane",
                                    "actions": [
                                        {
                                            "id": "finish-editor",
                                            "continue_from": "start-editor",
                                            "timing": "realtime",
                                            "input": [{"control": "x"}],
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                ],
            }
        )
