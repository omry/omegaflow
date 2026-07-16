from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from omegaflow.presentation_compiler import (
    artifact_freshness,
    ArtifactFreshness,
    compile_artifact_fingerprints,
    compile_recording_timing,
    compile_browser_beat,
    ConstraintGraph,
    load_browser_capture_log,
    PresentationCompileError,
    materialize_terminal_beat,
    milliseconds_half_up,
    natural_text_duration_ms,
    pointer_motion,
    solved_intervals,
)
from omegaflow.recording_plan import normalize_recording_plan


def test_constraint_graph_solves_deterministic_longest_lower_bounds() -> None:
    graph = ConstraintGraph()
    graph.add_node("beat:start", minimum_ms=10)
    graph.constrain(
        "beat:start", "action:one:start", gap_ms=20, reason="beat ordering"
    )
    graph.constrain(
        "action:one:start", "action:one:end", gap_ms=125, reason="action duration"
    )
    graph.constrain(
        "beat:start", "anchor:ready", gap_ms=300, reason="narration anchor"
    )
    graph.constrain(
        "action:one:end", "action:two:start", gap_ms=5, reason="source order"
    )
    graph.constrain(
        "anchor:ready", "action:two:start", gap_ms=0, reason="after anchor"
    )
    graph.constrain(
        "action:two:start", "action:two:end", gap_ms=50, reason="action duration"
    )

    solution = graph.solve()

    assert solution.time("beat:start") == 10
    assert solution.time("action:one:end") == 155
    assert solution.time("action:two:start") == 310
    assert solved_intervals(
        solution,
        (("action:one:start", "action:one:end"), ("action:two:start", "action:two:end")),
    ) == ((30, 155), (310, 360))
    assert solution.order.index("action:one:end") < solution.order.index(
        "action:two:start"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.49, 1),
        (1.5, 2),
        (2.5, 3),
        (Decimal("10.500"), 11),
        (0, 0),
    ],
)
def test_millisecond_rounding_is_half_up(value: object, expected: int) -> None:
    assert milliseconds_half_up(value) == expected


def test_constraint_cycle_reports_the_shortest_dependency_chain() -> None:
    graph = ConstraintGraph()
    graph.constrain("action:start", "action:end", reason="action duration")
    graph.constrain("action:end", "wait", reason="narration wait")
    graph.constrain("wait", "anchor", reason="audio continuity")
    graph.constrain("anchor", "action:start", reason="after anchor")
    graph.constrain("action:end", "other", reason="longer cycle")
    graph.constrain("other", "wait", reason="longer cycle")

    with pytest.raises(PresentationCompileError, match="action:start.*action:end") as caught:
        graph.solve()

    assert caught.value.code == "PRESENTATION_CYCLE"
    chain = str(caught.value).split("timing dependency cycle: ", 1)[1].split(" -> ")
    assert chain[0] == chain[-1]
    assert len(chain) == 5


def timestamp_sidecar(
    plan: object,
    take_id: str,
    *,
    duration_ms: int,
    member_ranges: list[tuple[int, int]],
    anchor_times: list[int] | None = None,
    wait_times: list[int] | None = None,
) -> dict[str, object]:
    take = next(take for take in plan.narration_takes if take.id == take_id)
    return {
        "version": 1,
        "take_id": take.id,
        "duration_ms": duration_ms,
        "members": [
            {
                "beat_id": member.beat_id,
                "text_start": member.text_start,
                "text_end": member.text_end,
                "source_start_ms": source_start,
                "source_end_ms": source_end,
            }
            for member, (source_start, source_end) in zip(
                take.members, member_ranges, strict=True
            )
        ],
        "words": [],
        "anchors": [
            {
                "beat_id": anchor.beat_id,
                "id": anchor.id,
                "text_offset": anchor.text_offset,
                "source_ms": source_ms,
            }
            for anchor, source_ms in zip(
                take.anchors, anchor_times or [], strict=True
            )
        ],
        "waits": [
            {
                "beat_id": wait.beat_id,
                "target": wait.target,
                "text_offset": wait.text_offset,
                "source_ms": source_ms,
                "gap_ms": wait.gap_ms,
            }
            for wait, source_ms in zip(take.waits, wait_times or [], strict=True)
        ],
    }


def cross_beat_terminal_plan(*, viewer_hold: float | None = None) -> object:
    first: dict[str, object] = {
        "id": "one",
        "narration_take": "joined",
        "narration": "First.",
        "actions": [{"run": "printf one"}],
    }
    if viewer_hold is not None:
        first["viewer_hold"] = viewer_hold
    return normalize_recording_plan(
        {
            "id": "cross-beat",
            "beats": [
                first,
                {
                    "id": "two",
                    "narration_take": "joined",
                    "narration": "Second.",
                    "actions": [{"run": "printf two"}],
                },
            ],
        }
    )


def test_cross_beat_take_uses_audio_boundary_and_holds_early_visual() -> None:
    plan = cross_beat_terminal_plan()
    sidecar = timestamp_sidecar(
        plan,
        "joined",
        duration_ms=2000,
        member_ranges=[(0, 800), (1000, 1900)],
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={"joined": sidecar},
        beat_visual_durations_ms={"one": 400, "two": 300},
    )

    assert [(beat.id, beat.offset_ms, beat.duration_ms) for beat in timing.beats] == [
        ("one", 0, 1000),
        ("two", 1000, 1000),
    ]
    assert timing.duration_ms == 2000
    assert [
        (
            interval.presentation_start_ms,
            interval.presentation_end_ms,
            interval.source_start_ms,
            interval.source_end_ms,
        )
        for interval in timing.audio_intervals
    ] == [(0, 2000, 0, 2000)]


@pytest.mark.parametrize(
    ("visual_duration", "viewer_hold"),
    [(1001, None), (950, 0.051)],
)
def test_cross_beat_take_rejects_visual_or_viewer_hold_overflow(
    visual_duration: int, viewer_hold: float | None
) -> None:
    plan = cross_beat_terminal_plan(viewer_hold=viewer_hold)
    sidecar = timestamp_sidecar(
        plan,
        "joined",
        duration_ms=2000,
        member_ranges=[(0, 800), (1000, 1900)],
    )

    with pytest.raises(PresentationCompileError) as caught:
        compile_recording_timing(
            plan,
            timestamp_sidecars={"joined": sidecar},
            beat_visual_durations_ms={"one": visual_duration, "two": 300},
        )

    assert caught.value.code == "PRESENTATION_OVERFLOW"


def test_authored_wait_pauses_audio_until_action_completion_and_gap() -> None:
    plan = normalize_recording_plan(
        {
            "id": "wait",
            "beats": [
                {
                    "id": "beat",
                    "narration": "@go@ Start. @wait:done+300ms@ Finish.",
                    "actions": [
                        {
                            "commands": [
                                {"id": "done", "run": "printf done", "after": "@go@"}
                            ]
                        }
                    ],
                }
            ],
        }
    )
    take = plan.narration_takes[0]
    sidecar = timestamp_sidecar(
        plan,
        take.id,
        duration_ms=1000,
        member_ranges=[(0, 1000)],
        anchor_times=[100],
        wait_times=[500],
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={take.id: sidecar},
        action_durations_ms={("beat", "done"): 400},
    )

    assert timing.anchor_times_ms[("beat", "go")] == 100
    assert timing.action("beat", "done").local_start_ms == 100
    assert timing.action("beat", "done").local_end_ms == 500
    assert timing.duration_ms == 1300
    assert [
        (interval.presentation_start_ms, interval.presentation_end_ms)
        for interval in timing.audio_intervals
    ] == [(0, 500), (800, 1300)]


def test_wait_at_shared_member_boundary_delays_boundary_without_audio_fragmentation() -> None:
    plan = normalize_recording_plan(
        {
            "id": "boundary-wait",
            "beats": [
                {
                    "id": "one",
                    "narration_take": "joined",
                    "narration": "First. @wait:done@",
                    "actions": [
                        {"commands": [{"id": "done", "run": "printf done"}]}
                    ],
                },
                {
                    "id": "two",
                    "narration_take": "joined",
                    "narration": "Second.",
                    "actions": [{"run": "printf two"}],
                },
            ],
        }
    )
    sidecar = timestamp_sidecar(
        plan,
        "joined",
        duration_ms=2000,
        member_ranges=[(0, 900), (1000, 2000)],
        wait_times=[1000],
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={"joined": sidecar},
        action_durations_ms={("one", "done"): 1200},
    )

    assert timing.beat("one").duration_ms == 1200
    assert timing.beat("two").offset_ms == 1200
    assert timing.duration_ms == 2200
    assert [
        (interval.presentation_start_ms, interval.presentation_end_ms)
        for interval in timing.audio_intervals
    ] == [(0, 1000), (1200, 2200)]


def test_wait_for_action_after_later_anchor_reports_cycle() -> None:
    plan = normalize_recording_plan(
        {
            "id": "cycle",
            "beats": [
                {
                    "id": "beat",
                    "narration": "Start. @wait:done@ Then @go@ continue.",
                    "actions": [
                        {
                            "commands": [
                                {"id": "done", "run": "printf done", "after": "@go@"}
                            ]
                        }
                    ],
                }
            ],
        }
    )
    take = plan.narration_takes[0]
    sidecar = timestamp_sidecar(
        plan,
        take.id,
        duration_ms=900,
        member_ranges=[(0, 900)],
        anchor_times=[500],
        wait_times=[100],
    )

    with pytest.raises(PresentationCompileError) as caught:
        compile_recording_timing(
            plan,
            timestamp_sidecars={take.id: sidecar},
            action_durations_ms={("beat", "done"): 50},
        )

    assert caught.value.code == "PRESENTATION_CYCLE"
    assert "done" in str(caught.value)


def test_viewer_hold_separates_ordinary_beats_without_narration() -> None:
    plan = normalize_recording_plan(
        {
            "id": "holds",
            "beats": [
                {
                    "id": "one",
                    "viewer_hold": 0.05,
                    "actions": [{"run": "printf one"}],
                },
                {"id": "two", "actions": [{"run": "printf two"}]},
            ],
        }
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={},
        beat_visual_durations_ms={"one": 100, "two": 200},
    )

    assert [(beat.offset_ms, beat.duration_ms) for beat in timing.beats] == [
        (0, 150),
        (150, 200),
    ]


def mixed_relocation_plan(*, first_hold: float) -> object:
    return normalize_recording_plan(
        {
            "id": "relocation",
            "browser": {},
            "beats": [
                {
                    "id": "terminal-one",
                    "viewer_hold": first_hold,
                    "actions": [{"run": "printf one"}],
                },
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}}
                    ],
                },
                {
                    "id": "terminal-two",
                    "actions": [{"run": "printf two"}],
                },
            ],
        }
    )


def test_terminal_and_browser_beats_relocate_without_changing_local_timing() -> None:
    original = compile_recording_timing(
        mixed_relocation_plan(first_hold=0),
        timestamp_sidecars={},
        action_durations_ms={("browser", "open"): 180},
        beat_visual_durations_ms={
            "terminal-one": 100,
            "browser": 180,
            "terminal-two": 200,
        },
    )
    relocated = compile_recording_timing(
        mixed_relocation_plan(first_hold=0.25),
        timestamp_sidecars={},
        action_durations_ms={("browser", "open"): 180},
        beat_visual_durations_ms={
            "terminal-one": 100,
            "browser": 180,
            "terminal-two": 200,
        },
    )

    assert original.beat("browser").offset_ms == 100
    assert relocated.beat("browser").offset_ms == 350
    assert original.beat("browser").duration_ms == 180
    assert relocated.beat("browser").duration_ms == 180
    assert original.beat("terminal-two").duration_ms == 200
    assert relocated.beat("terminal-two").duration_ms == 200
    assert original.action("browser", "open").local_start_ms == 0
    assert relocated.action("browser", "open").local_start_ms == 0
    assert original.action("browser", "open").local_end_ms == 180
    assert relocated.action("browser", "open").local_end_ms == 180


def artifact_fingerprints(plan: object, *, asset: str = "a") -> object:
    return compile_artifact_fingerprints(
        plan,
        capture_environment={
            "profile": "desktop-v1",
            "viewport": {"width": 1440, "height": 900},
            "browser_revision": "chromium-1",
        },
        source_dependencies={"demo.yaml": "1" * 64},
        capture_policy_versions={
            "stability": "stability-v1",
            "redaction": "redaction-v1",
        },
        visual_asset_hashes=[asset * 64],
        narration_take_hashes={},
        timestamp_hashes={},
        presentation_policy_versions={
            "compiler": "presentation-v1",
            "browser_renderer": "payload-v1",
        },
        auth_state_sha256="2" * 64,
    )


def fingerprint_plan(
    *,
    command: str = "printf one",
    display: str | None = None,
    pre_command_pause: float | None = None,
    follow_along: bool = False,
    viewer_hold: float = 0,
) -> object:
    command_config: dict[str, object] = {"run": command}
    if pre_command_pause is not None:
        command_config["pre_command_pause"] = pre_command_pause
    if follow_along:
        command_config["follow_along"] = True
    if display is not None:
        command_config["display"] = display
    action: dict[str, object] = {"commands": [command_config]}
    return normalize_recording_plan(
        {
            "id": "fingerprint",
            "beats": [
                {
                    "id": "one",
                    "viewer_hold": viewer_hold,
                    "actions": [action],
                }
            ],
        }
    )


def test_fingerprints_separate_recapture_from_presentation_changes() -> None:
    original = artifact_fingerprints(fingerprint_plan())
    presentation_change = artifact_fingerprints(
        fingerprint_plan(viewer_hold=0.25)
    )
    capture_change = artifact_fingerprints(
        fingerprint_plan(command="printf changed")
    )
    display_change = artifact_fingerprints(
        fingerprint_plan(display="visible command")
    )
    pause_change = artifact_fingerprints(
        fingerprint_plan(pre_command_pause=0.5)
    )
    follow_change = artifact_fingerprints(fingerprint_plan(follow_along=True))
    asset_change = artifact_fingerprints(fingerprint_plan(), asset="b")

    assert original.capture_fingerprint == presentation_change.capture_fingerprint
    assert (
        original.presentation_fingerprint
        != presentation_change.presentation_fingerprint
    )
    assert original.capture_fingerprint != capture_change.capture_fingerprint
    assert original.capture_fingerprint != display_change.capture_fingerprint
    assert original.capture_fingerprint != pause_change.capture_fingerprint
    assert original.capture_fingerprint != follow_change.capture_fingerprint
    assert original.presentation_fingerprint != asset_change.presentation_fingerprint


def test_fingerprints_normalize_sha256_case_without_hashing_secret_content() -> None:
    plan = fingerprint_plan()
    arguments = {
        "capture_environment": {"profile": "desktop-v1"},
        "source_dependencies": {"demo.yaml": "a" * 64},
        "capture_policy_versions": {"capture": "v1"},
    }

    lower = compile_artifact_fingerprints(
        plan, auth_state_sha256="b" * 64, **arguments
    )
    upper = compile_artifact_fingerprints(
        plan, auth_state_sha256="B" * 64, **arguments
    )

    assert lower == upper


@pytest.mark.parametrize(
    ("stored", "capture_exists", "presentation_exists", "expected"),
    [
        (None, True, True, ArtifactFreshness.recapture),
        ({"version": 0}, True, True, ArtifactFreshness.recapture),
        ("capture-mismatch", True, True, ArtifactFreshness.recapture),
        ("presentation-mismatch", True, True, ArtifactFreshness.recompile),
        ("current", True, False, ArtifactFreshness.recompile),
        ("current", True, True, ArtifactFreshness.fresh),
        ("current", False, True, ArtifactFreshness.recapture),
    ],
)
def test_artifact_freshness_selects_the_minimum_safe_repair(
    stored: object,
    capture_exists: bool,
    presentation_exists: bool,
    expected: ArtifactFreshness,
) -> None:
    current = artifact_fingerprints(fingerprint_plan())
    if stored == "current":
        stored = current.payload()
    elif stored == "capture-mismatch":
        stored = {
            **current.payload(),
            "capture_fingerprint": "0" * 64,
        }
    elif stored == "presentation-mismatch":
        stored = {
            **current.payload(),
            "presentation_fingerprint": "0" * 64,
        }

    assert artifact_freshness(
        stored,
        current,
        capture_artifacts_exist=capture_exists,
        presentation_artifacts_exist=presentation_exists,
    ) is expected


def write_cast(path: Path, version: int, events: list[list[object]]) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps({"version": version, "width": 100, "height": 28}),
                *(json.dumps(event) for event in events),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("version", "events", "expected_hold"),
    [
        (2, [[0.2, "o", "one"], [0.8, "o", "two"]], 1.2),
        (3, [[0.2, "o", "one"], [0.6, "o", "two"]], 0.4),
    ],
)
def test_terminal_materialization_preserves_local_events_and_extends_hold(
    tmp_path: Path,
    version: int,
    events: list[list[object]],
    expected_hold: float,
) -> None:
    source = tmp_path / "source.cast"
    destination = tmp_path / "beats" / "beat.cast"
    write_cast(source, version, events)

    result = materialize_terminal_beat(source, destination, duration_ms=1200)

    output = [json.loads(line) for line in destination.read_text().splitlines()]
    assert output[1:-1] == events
    assert output[-1] == [expected_hold, "o", ""]
    assert result.captured_duration_ms == 800
    assert result.duration_ms == 1200
    assert result.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()


def test_terminal_materialization_rejects_visual_overflow(tmp_path: Path) -> None:
    source = tmp_path / "source.cast"
    write_cast(source, 2, [[1.001, "o", "too late"]])

    with pytest.raises(PresentationCompileError) as caught:
        materialize_terminal_beat(source, tmp_path / "out.cast", duration_ms=1000)

    assert caught.value.code == "PRESENTATION_OVERFLOW"


def test_terminal_materialization_relocates_events_to_solved_action_start(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.cast"
    destination = tmp_path / "published.cast"
    write_cast(source, 3, [[0.1, "o", "$ command\n"], [0.2, "o", "done\n"]])

    materialize_terminal_beat(
        source,
        destination,
        duration_ms=1200,
        captured_action_intervals_ms={"command": (0, 300)},
        action_starts_ms={"command": 700},
    )

    output = [json.loads(line) for line in destination.read_text().splitlines()]
    assert output[1:] == [
        [0.8, "o", "$ command\n"],
        [0.2, "o", "done\n"],
        [0.2, "o", ""],
    ]


def test_terminal_materialization_removes_private_capture_header_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.cast"
    source.write_text(
        json.dumps(
            {
                "version": 3,
                "term": {"cols": 80, "rows": 24},
                "timestamp": 123,
                "command": "bash /private/run/session.sh",
                "title": "Demo",
                "env": {"SHELL": "/bin/zsh"},
            }
        )
        + "\n"
        + json.dumps([0.1, "o", "ok"])
        + "\n",
        encoding="utf-8",
    )
    destination = tmp_path / "published.cast"

    materialize_terminal_beat(source, destination, duration_ms=100)

    header = json.loads(destination.read_text(encoding="utf-8").splitlines()[0])
    assert header == {
        "version": 3,
        "term": {"cols": 80, "rows": 24},
        "title": "Demo",
    }


def state_asset(character: str) -> dict[str, object]:
    digest = character * 64
    return {
        "path": f"capture/states/{digest}.png",
        "sha256": digest,
        "media_type": "image/png",
        "width": 1440,
        "height": 900,
        "bytes": 100,
    }


def text_style() -> dict[str, object]:
    return {
        "font_family": "Inter",
        "font_size": 16,
        "font_weight": "400",
        "font_style": "normal",
        "line_height": 24,
        "letter_spacing": 0,
        "color": "rgb(0, 0, 0)",
        "text_align": "start",
        "padding_top": 4,
        "padding_right": 8,
        "padding_bottom": 4,
        "padding_left": 8,
        "clipping_rect": {"x": 20, "y": 20, "width": 200, "height": 32},
        "selection_start": 0,
        "selection_end": 0,
        "caret_visible": False,
    }


def test_browser_payload_compiles_all_selected_event_policies() -> None:
    plan = normalize_recording_plan(
        {
            "id": "browser-compile",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {
                                "url": "about:blank",
                                "display_url": "https://demo.example/",
                            },
                        },
                        {
                            "id": "click",
                            "click": {
                                "target": {"role": "button", "name": "Create"}
                            },
                            "transition": "fade",
                            "display_url_after": "https://demo.example/project",
                        },
                        {
                            "id": "name",
                            "fill": {
                                "target": {"label": "Project name"},
                                "text": "Hello, world!",
                            },
                        },
                        {
                            "id": "scroll",
                            "scroll": {
                                "by": {"x": 0, "y": 120},
                                "container": {"test_id": "list"},
                            },
                        },
                        {
                            "id": "shortcut",
                            "press": {
                                "key": "Control+K",
                                "target": {"label": "Project name"},
                            },
                            "transition": "captured",
                        },
                    ],
                }
            ],
        }
    )
    bounds = {"x": 20, "y": 20, "width": 200, "height": 32}
    captures = [
        {
            "action_id": "open",
            "kind": "open_page",
            "completion": {"kind": "navigation"},
            "visual": {"kind": "state", "state": state_asset("1")},
        },
        {
            "action_id": "click",
            "kind": "click",
            "target": {"bounds": bounds, "point": {"x": 120, "y": 36}},
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("2")},
        },
        {
            "action_id": "name",
            "kind": "fill",
            "target": {
                "bounds": bounds,
                "point": {"x": 120, "y": 36},
                "text_overlay": {"eligible": True, "style": text_style()},
            },
            "completion": {
                "kind": "action",
                "input": {"kind": "text", "text": "Hello, world!"},
            },
            "visual": {"kind": "state", "state": state_asset("3")},
        },
        {
            "action_id": "scroll",
            "kind": "scroll",
            "target": {
                "bounds": {"x": 0, "y": 100, "width": 500, "height": 300},
                "point": {"x": 250, "y": 250},
                "scroll": {
                    "eligible": True,
                    "start": {"x": 0, "y": 0},
                    "end": {"x": 0, "y": 120},
                },
            },
            "before_state": state_asset("3"),
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("4")},
        },
        {
            "action_id": "shortcut",
            "kind": "press",
            "target": {"bounds": bounds, "point": {"x": 120, "y": 36}},
            "completion": {"kind": "action"},
            "visual": {
                "kind": "clip",
                "request": {},
                "end_state": state_asset("5"),
            },
        },
    ]
    clip = {
        "path": "capture/fragments/" + "a" * 64 + ".webm",
        "sha256": "a" * 64,
        "media_type": "video/webm",
        "width": 1440,
        "height": 900,
        "duration_ms": 400,
        "encoded_bytes": 200,
    }

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[0],
        action_captures=captures,
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("0"),
        clip_assets={("browser", "shortcut"): clip},
    )

    kinds = [event["kind"] for event in compiled.payload["events"]]
    assert {
        "state",
        "pointer_move",
        "click",
        "focus",
        "text",
        "scroll",
        "key",
        "clip",
        "display_url",
        "complete",
    } <= set(kinds)
    assert compiled.payload["events"] == sorted(
        compiled.payload["events"], key=lambda event: event["at_ms"]
    )
    assert compiled.payload["duration_ms"] == compiled.action_completions_ms[
        "shortcut"
    ]
    assert compiled.action_starts_ms["name"] == compiled.action_completions_ms[
        "click"
    ]
    assert len(compiled.assets) == 7
    assert compiled.payload["initial_state"] == "state-" + "0" * 64
    assert [
        event["value"]
        for event in compiled.payload["events"]
        if event["kind"] == "display_url"
    ] == ["https://demo.example/", "https://demo.example/project"]
    shortcut_visuals = [
        event
        for event in compiled.payload["events"]
        if event["action_id"] == "shortcut" and event["kind"] in {"clip", "state"}
    ]
    assert [event["kind"] for event in shortcut_visuals] == ["clip", "state"]
    assert shortcut_visuals[1] == {
        "kind": "state",
        "action_id": "shortcut",
        "at_ms": shortcut_visuals[0]["end_ms"],
        "end_ms": shortcut_visuals[0]["end_ms"],
        "asset": "state-" + "5" * 64,
        "transition": "cut",
    }


def test_pointer_and_text_animation_are_deterministic() -> None:
    arguments = (
        "recording",
        "beat",
        "action",
        {"x": 0, "y": 0},
        {"x": 300, "y": 400},
    )
    first = pointer_motion(*arguments)

    assert first == pointer_motion(*arguments)
    assert 260 <= first[0] <= 1000
    assert first[1]["x1"] != 100
    assert first[1]["x2"] != 200
    assert natural_text_duration_ms("Hello!") > natural_text_duration_ms("Helloo")
    assert natural_text_duration_ms("same") == natural_text_duration_ms("same")


def test_browser_payload_serialization_and_content_asset_dedup_are_deterministic() -> None:
    plan = normalize_recording_plan(
        {
            "id": "dedup",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}}
                    ],
                }
            ],
        }
    )
    state = state_asset("d")
    capture = {
        "action_id": "open",
        "kind": "open_page",
        "completion": {"kind": "navigation"},
        "visual": {"kind": "state", "state": state},
    }
    arguments = {
        "action_captures": [capture],
        "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1},
        "initial_state": state,
    }

    first = compile_browser_beat(plan.id, plan.beats[0], **arguments)
    second = compile_browser_beat(plan.id, plan.beats[0], **arguments)

    assert first.payload == second.payload
    assert first.action_starts_ms == second.action_starts_ms
    assert tuple(first.assets) == ("state-" + "d" * 64,)
    assert first.assets == second.assets


def test_browser_capture_log_requires_successful_run_end(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    path.write_text(
        json.dumps(
            {
                "capture_version": 1,
                "seq": 1,
                "type": "run_start",
                "profile": {},
                "initial_state": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PresentationCompileError, match="incomplete"):
        load_browser_capture_log(path)
