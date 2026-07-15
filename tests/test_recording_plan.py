from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

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
    normalize_recording_plan,
    validate_recording_modalities,
)
from omegaflow.studio_config import RecordingSpec


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


def test_omegaconf_schema_authority_supports_versioned_artifacts() -> None:
    for schema in (RecordingSpec, BrowserPayloadV1, PresentationManifestV1):
        assert OmegaConf.structured(schema) is not None


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
