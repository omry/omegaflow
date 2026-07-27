from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from omegaflow.presentation import serialize_presentation_manifest
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
    TerminalTextHighlightEvent,
    TerminalTextHighlightTargetEvent,
)
from omegaflow.presentation_schema import (
    PresentationAudioV1,
    PresentationBeatV1,
    PresentationHeaderV1,
    PresentationManifestV1,
    PresentationPaneBeatV1,
    PresentationPaneLayoutV1,
    PresentationPaneTrackV1,
    PresentationPaneTransitionV1,
    PresentationPaneV1,
    PresentationRecordingV1,
    PresentationRendererV1,
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


def sequential_visualization_plan() -> object:
    return normalize_recording_plan(
        {
            "id": "sequential-visualization",
            "audio": {"enabled": True},
            "narration": {"id": "voiceover"},
            "panes": [
                {"id": "definition", "kind": "visualization"},
                {"id": "terminal", "kind": "terminal"},
            ],
            "beats": [
                {
                    "id": "explain",
                    "narration": (
                        "Inspect the exact target. "
                        "@regex@ Inspect the regular expression. "
                        "@combined@ Combine both targets."
                    ),
                    "layout": {"areas": [["definition"], ["terminal"]]},
                    "panes": {
                        "definition": [
                            {
                                "id": "exact",
                                "actions": [
                                    {
                                        "id": "show-exact",
                                        "show": {
                                            "language": "yaml",
                                            "text": '- text: "ready"\n',
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
                                                '- text: "ready"\n'
                                                "- regex: 'Elapsed: .*'\n"
                                            ),
                                        },
                                    }
                                ],
                            },
                        ],
                        "terminal": [
                            {
                                "id": "status",
                                "actions": [
                                    {
                                        "id": "run-status",
                                        "run": "printf ready",
                                    }
                                ],
                            }
                        ],
                    },
                }
            ],
        }
    )


def test_narration_events_schedule_sequential_pane_beats() -> None:
    plan = sequential_visualization_plan()
    take = plan.narration_takes[0]
    sidecar = timestamp_sidecar(
        plan,
        take.id,
        duration_ms=1400,
        member_ranges=[(0, 1400)],
        anchor_times=[400, 900],
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={take.id: sidecar},
        beat_visual_durations_ms={"explain": 1200},
    )

    assert [
        (
            pane.pane_id,
            pane.pane_beat_id,
            pane.local_start_ms,
            pane.local_end_ms,
        )
        for pane in timing.pane_beats
    ] == [
        ("definition", "exact", 0, 400),
        ("definition", "regex", 400, 900),
        ("definition", "combined", 900, 1400),
        ("terminal", "status", 0, 1400),
    ]


def test_cross_stream_action_events_retime_both_captured_panes() -> None:
    plan = normalize_recording_plan(
        {
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
                                    {"id": "start-app", "run": "start"},
                                    {
                                        "id": "verify-ready",
                                        "run": "verify",
                                        "after": (
                                            "browser.interaction.mark-ready.ended"
                                        ),
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
                                        "after": (
                                            "terminal.session.start-app.ended"
                                        ),
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
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={},
        pane_action_intervals_ms={
            ("synchronize", "terminal", "session", "start-app"): (0, 200),
            ("synchronize", "terminal", "session", "verify-ready"): (300, 400),
            ("synchronize", "browser", "interaction", "open-app"): (0, 300),
            ("synchronize", "browser", "interaction", "mark-ready"): (400, 500),
        },
        pane_beat_visual_durations_ms={
            ("synchronize", "terminal", "session"): 400,
            ("synchronize", "browser", "interaction"): 500,
        },
    )

    assert [
        (
            action.pane_id,
            action.action_id,
            action.local_start_ms,
            action.local_end_ms,
        )
        for action in timing.pane_actions
    ] == [
        ("terminal", "start-app", 0, 200),
        ("terminal", "verify-ready", 700, 800),
        ("browser", "open-app", 200, 500),
        ("browser", "mark-ready", 600, 700),
    ]
    assert timing.beats[0].duration_ms == 800


def test_explicit_browser_handoff_orders_target_after_producer_start() -> None:
    plan = normalize_recording_plan(
        {
            "id": "handoff-timing",
            "browser": {},
            "panes": [
                {"id": "terminal", "kind": "terminal"},
                {"id": "preview", "kind": "browser"},
            ],
            "beats": [
                {
                    "id": "handoff",
                    "layout": {"areas": [["terminal", "preview"]]},
                    "panes": {
                        "terminal": [
                            {
                                "id": "session",
                                "actions": [
                                    {
                                        "id": "watch",
                                        "run": "watch",
                                        "browser_handoff": {
                                            "target": "preview",
                                        },
                                        "timing": "realtime",
                                    }
                                ],
                            }
                        ],
                        "preview": [
                            {
                                "id": "player",
                                "actions": [
                                    {
                                        "id": "open",
                                        "open_page": {"handoff": "watch"},
                                    },
                                    {
                                        "id": "inspect",
                                        "wait_for": {
                                            "visible": {"role": "main"},
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                }
            ],
        }
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={},
        pane_action_intervals_ms={
            ("handoff", "terminal", "session", "watch"): (500, 600),
            ("handoff", "preview", "player", "open"): (0, 200),
            ("handoff", "preview", "player", "inspect"): (300, 700),
        },
        pane_beat_visual_durations_ms={
            ("handoff", "terminal", "session"): 600,
            ("handoff", "preview", "player"): 700,
        },
    )

    action_intervals = {
        (action.pane_id, action.action_id): (
            action.local_start_ms,
            action.local_end_ms,
        )
        for action in timing.pane_actions
    }
    assert action_intervals == {
        ("terminal", "watch"): (500, 1200),
        ("preview", "open"): (500, 700),
        ("preview", "inspect"): (800, 1200),
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


def test_guided_shared_take_keeps_a_short_audio_lead_after_checkpoint() -> None:
    plan = normalize_recording_plan(
        {
            "id": "guided-shared-take",
            "beats": [
                {
                    "id": "one",
                    "narration_take": "joined",
                    "narration": "First.",
                    "guide": {"success_hint": "Pause here."},
                    "actions": [{"run": "printf one"}],
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
        member_ranges=[(0, 900), (1100, 2000)],
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={"joined": sidecar},
    )

    assert timing.beat("one").duration_ms == 900
    assert timing.beat("two").offset_ms == 900
    assert [
        (
            interval.presentation_start_ms,
            interval.presentation_end_ms,
            interval.source_start_ms,
            interval.source_end_ms,
        )
        for interval in timing.audio_intervals
    ] == [(0, 2000, 0, 2000)]

    manifest = PresentationManifestV1(
        recording=PresentationRecordingV1(
            id="guided-shared-take",
            duration_ms=timing.duration_ms,
        ),
        renderers={"terminal": PresentationRendererV1()},
        presentation=PresentationHeaderV1(guided=True),
        audio=PresentationAudioV1(
            metadata="audio.json",
            intervals=list(timing.audio_intervals),
        ),
        panes=[
            PresentationPaneV1(id=f"main-{beat.id}", renderer="terminal")
            for beat in plan.beats
        ],
        beats=[
            PresentationBeatV1(
                id=beat.id,
                offset_ms=timing.beat(beat.id).offset_ms,
                duration_ms=timing.beat(beat.id).duration_ms,
                layout=PresentationPaneLayoutV1(
                    areas=[[f"main-{beat.id}"]]
                ),
                pane_tracks=[
                    PresentationPaneTrackV1(
                        pane_id=f"main-{beat.id}",
                        beats=[
                            PresentationPaneBeatV1(
                                id=beat.id,
                                duration_ms=timing.beat(beat.id).duration_ms,
                                payload=f"beats/{beat.id}.cast",
                                transition=PresentationPaneTransitionV1(),
                            )
                        ],
                    )
                ],
            )
            for beat in plan.beats
        ],
    )

    serialized = serialize_presentation_manifest(manifest)

    assert len(serialized["audio"]["intervals"]) == 1


def test_guided_shared_take_caps_post_checkpoint_audio_lead_at_350ms() -> None:
    plan = normalize_recording_plan(
        {
            "id": "guided-shared-take",
            "beats": [
                {
                    "id": "one",
                    "narration_take": "joined",
                    "narration": "First.",
                    "guide": {"success_hint": "Pause here."},
                    "actions": [{"run": "printf one"}],
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
        duration_ms=2800,
        member_ranges=[(0, 900), (1900, 2800)],
    )

    timing = compile_recording_timing(
        plan,
        timestamp_sidecars={"joined": sidecar},
    )

    assert timing.beat("one").duration_ms == 1550
    assert timing.beat("two").offset_ms == 1550
    assert [
        (
            interval.presentation_start_ms,
            interval.presentation_end_ms,
            interval.source_start_ms,
            interval.source_end_ms,
        )
        for interval in timing.audio_intervals
    ] == [(0, 2800, 0, 2800)]


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
    timing: str = "presentation",
    viewer_hold: float = 0,
) -> object:
    command_config: dict[str, object] = {"run": command}
    if pre_command_pause is not None:
        command_config["pre_command_pause"] = pre_command_pause
    if timing != "presentation":
        command_config["timing"] = timing
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
    timing_change = artifact_fingerprints(fingerprint_plan(timing="realtime"))
    asset_change = artifact_fingerprints(fingerprint_plan(), asset="b")

    assert original.capture_fingerprint == presentation_change.capture_fingerprint
    assert (
        original.presentation_fingerprint
        != presentation_change.presentation_fingerprint
    )
    assert original.capture_fingerprint != capture_change.capture_fingerprint
    assert original.capture_fingerprint != display_change.capture_fingerprint
    assert original.capture_fingerprint != pause_change.capture_fingerprint
    assert original.capture_fingerprint != timing_change.capture_fingerprint
    assert original.presentation_fingerprint != asset_change.presentation_fingerprint


def test_browser_hold_before_is_presentation_only_for_freshness() -> None:
    def plan(hold_before_ms: int | None = None) -> object:
        action: dict[str, object] = {
            "id": "open",
            "open_page": {"url": "about:blank"},
        }
        if hold_before_ms is not None:
            action["hold_before_ms"] = hold_before_ms
        return normalize_recording_plan(
            {
                "id": "browser-fingerprint",
                "browser": {},
                "beats": [
                    {
                        "id": "browser",
                        "medium": "browser",
                        "actions": [action],
                    }
                ],
            }
        )

    original = artifact_fingerprints(plan())
    delayed = artifact_fingerprints(plan(250))

    assert original.capture_fingerprint == delayed.capture_fingerprint
    assert original.presentation_fingerprint != delayed.presentation_fingerprint


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


@pytest.mark.parametrize("version", [2, 3])
def test_terminal_materialization_inserts_timed_text_highlight_markers(
    tmp_path: Path, version: int
) -> None:
    source = tmp_path / "source.cast"
    destination = tmp_path / "published.cast"
    write_cast(source, version, [[0.1, "o", ".omegaflow/config.yaml\n"]])

    materialize_terminal_beat(
        source,
        destination,
        duration_ms=1000,
        text_highlights=(
            TerminalTextHighlightEvent(
                id="highlight-0",
                color="brand",
                targets=(
                    TerminalTextHighlightTargetEvent(
                        kind="text",
                        pattern="audio:\n  enabled: true",
                        occurrence=1,
                    ),
                    TerminalTextHighlightTargetEvent(
                        kind="regex",
                        pattern=r"config-\d+\.yaml",
                        occurrence=2,
                    ),
                ),
                start_ms=250,
                end_ms=750,
            ),
        ),
    )

    output = [json.loads(line) for line in destination.read_text().splitlines()][1:]
    absolute_ms = 0
    markers: list[tuple[int, dict[str, object]]] = []
    for event in output:
        event_ms = round(float(event[0]) * 1000)
        absolute_ms = absolute_ms + event_ms if version == 3 else event_ms
        if event[1] == "m":
            prefix = "omegaflow:highlight:"
            assert str(event[2]).startswith(prefix)
            markers.append((absolute_ms, json.loads(str(event[2])[len(prefix) :])))

    assert markers == [
        (
            250,
            {
                "active": True,
                "color": "brand",
                "id": "highlight-0",
                "targets": [
                    {
                        "occurrence": 1,
                        "text": "audio:\n  enabled: true",
                    },
                    {
                        "occurrence": 2,
                        "regex": r"config-\d+\.yaml",
                    },
                ],
            },
        ),
        (750, {"active": False, "id": "highlight-0"}),
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
        "path": "capture/fragments/" + "a" * 64 + ".mp4",
        "sha256": "a" * 64,
        "media_type": "video/mp4",
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


def test_browser_beat_pointer_override_is_published_in_its_payload() -> None:
    plan = normalize_recording_plan(
        {
            "id": "hidden-pointer",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "pointer": {"visible": False},
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {"url": "about:blank"},
                        }
                    ],
                }
            ],
        }
    )
    capture = {
        "action_id": "open",
        "kind": "open_page",
        "completion": {"kind": "navigation"},
        "visual": {"kind": "state", "state": state_asset("1")},
    }

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[0],
        action_captures=[capture],
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("0"),
        initial_pointer={"x": 10, "y": 20, "visible": True},
    )

    assert compiled.payload["initial_pointer"] == {
        "x": 10.0,
        "y": 20.0,
        "visible": False,
    }


def test_standalone_pointer_moves_compile_without_click_feedback() -> None:
    plan = normalize_recording_plan(
        {
            "id": "pointer-moves",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}},
                        {
                            "id": "move-viewport",
                            "move_pointer": {
                                "viewport": {"x": 0.4, "y": 0.12}
                            },
                            "hold_before_ms": 250,
                            "hold_after_ms": 100,
                        },
                        {
                            "id": "move-target",
                            "move_pointer": {
                                "target": {
                                    "role": "button",
                                    "name": "Submit",
                                }
                            },
                        },
                    ],
                }
            ],
        }
    )
    captures = [
        {
            "action_id": "open",
            "kind": "open_page",
            "completion": {"kind": "navigation"},
            "visual": {"kind": "state", "state": state_asset("1")},
        },
        {
            "action_id": "move-viewport",
            "kind": "move_pointer",
            "target": {"point": {"x": 576, "y": 108}},
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("2")},
        },
        {
            "action_id": "move-target",
            "kind": "move_pointer",
            "target": {
                "bounds": {"x": 100, "y": 200, "width": 80, "height": 40},
                "point": {"x": 140, "y": 220},
            },
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("3")},
        },
    ]

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[0],
        action_captures=captures,
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("0"),
    )

    pointer_events = [
        event
        for event in compiled.payload["events"]
        if event["kind"] == "pointer_move"
    ]
    assert compiled.payload["initial_pointer"] == {
        "x": 720.0,
        "y": 450.0,
        "visible": True,
    }
    assert [event["action_id"] for event in pointer_events] == [
        "move-viewport",
        "move-target",
    ]
    assert [event["end"] for event in pointer_events] == [
        {"x": 576.0, "y": 108.0},
        {"x": 140.0, "y": 220.0},
    ]
    assert compiled.action_starts_ms["move-viewport"] == 0
    assert pointer_events[0]["at_ms"] == 250
    assert compiled.action_completions_ms["move-viewport"] == (
        pointer_events[0]["end_ms"] + 100
    )
    assert compiled.action_starts_ms["move-target"] == (
        compiled.action_completions_ms["move-viewport"]
    )
    assert not any(event["kind"] == "click" for event in compiled.payload["events"])


def test_pointer_visibility_actions_compile_without_moving_the_pointer() -> None:
    plan = normalize_recording_plan(
        {
            "id": "pointer-visibility",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "pointer": {"visible": False},
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}},
                        {"id": "show", "set_pointer": {"visible": True}},
                        {
                            "id": "move",
                            "move_pointer": {"viewport": {"x": 0.4, "y": 0.12}},
                        },
                        {"id": "hide", "set_pointer": {"visible": False}},
                    ],
                }
            ],
        }
    )
    captures = [
        {
            "action_id": "open",
            "kind": "open_page",
            "completion": {"kind": "navigation"},
            "visual": {"kind": "state", "state": state_asset("0")},
        },
        {
            "action_id": "show",
            "kind": "set_pointer",
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("0")},
        },
        {
            "action_id": "move",
            "kind": "move_pointer",
            "target": {"point": {"x": 576, "y": 108}},
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("0")},
        },
        {
            "action_id": "hide",
            "kind": "set_pointer",
            "completion": {"kind": "action"},
            "visual": {"kind": "state", "state": state_asset("0")},
        },
    ]

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[0],
        action_captures=captures,
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("0"),
        initial_pointer={"x": 720, "y": 450, "visible": True},
    )

    visibility_events = [
        event
        for event in compiled.payload["events"]
        if event["kind"] == "pointer_visibility"
    ]
    assert compiled.payload["initial_pointer"] == {
        "x": 720.0,
        "y": 450.0,
        "visible": False,
    }
    assert [(event["action_id"], event["visible"]) for event in visibility_events] == [
        ("show", True),
        ("hide", False),
    ]
    assert visibility_events[0]["at_ms"] == visibility_events[0]["end_ms"]
    assert visibility_events[1]["at_ms"] == visibility_events[1]["end_ms"]


def test_drag_compiles_pointer_move_and_pressed_drag_feedback() -> None:
    plan = normalize_recording_plan(
        {
            "id": "browser-drag",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}},
                        {
                            "id": "move-sun",
                            "drag": {
                                "from": {"target": {"test_id": "sun"}},
                                "to": {"target": {"test_id": "sky"}},
                            },
                        }
                    ],
                }
            ],
        }
    )
    capture = {
        "action_id": "move-sun",
        "kind": "drag",
        "from": {
            "bounds": {"x": 100, "y": 200, "width": 80, "height": 40},
            "point": {"x": 140, "y": 220},
        },
        "to": {
            "bounds": {"x": 400, "y": 100, "width": 200, "height": 100},
            "point": {"x": 500, "y": 150},
        },
        "motion": {
            "duration_ms": 900,
            "curve": {
                "x1": 260,
                "y1": 196.6666666667,
                "x2": 380,
                "y2": 173.3333333333,
            },
        },
        "completion": {"kind": "action"},
        "visual": {"kind": "state", "state": state_asset("1")},
    }
    open_capture = {
        "action_id": "open",
        "kind": "open_page",
        "completion": {"kind": "navigation"},
        "visual": {"kind": "state", "state": state_asset("0")},
    }

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[0],
        action_captures=[open_capture, capture],
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("0"),
        initial_pointer={"x": 20, "y": 30, "visible": True},
    )

    pointer_move, drag = [
        event
        for event in compiled.payload["events"]
        if event["kind"] in {"pointer_move", "drag"}
    ]
    assert pointer_move["start"] == {"x": 20.0, "y": 30.0}
    assert pointer_move["end"] == {"x": 140.0, "y": 220.0}
    assert drag["start"] == {"x": 140.0, "y": 220.0}
    assert drag["end"] == {"x": 500.0, "y": 150.0}
    assert drag["button"] == "left"
    assert drag["at_ms"] == pointer_move["end_ms"]
    assert compiled.action_completions_ms["move-sun"] >= drag["end_ms"]


def test_captured_drag_motion_plays_during_the_pressed_pointer_interval() -> None:
    plan = normalize_recording_plan(
        {
            "id": "captured-browser-drag",
            "browser": {},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}},
                        {
                            "id": "move-sun",
                            "drag": {
                                "from": {"target": {"test_id": "sun"}},
                                "to": {"target": {"test_id": "sky"}},
                            },
                            "transition": "captured",
                        }
                    ],
                }
            ],
        }
    )
    capture = {
        "action_id": "move-sun",
        "kind": "drag",
        "from": {
            "bounds": {"x": 100, "y": 200, "width": 80, "height": 40},
            "point": {"x": 140, "y": 220},
        },
        "to": {
            "bounds": {"x": 400, "y": 100, "width": 200, "height": 100},
            "point": {"x": 500, "y": 150},
        },
        "motion": {
            "duration_ms": 900,
            "curve": {
                "x1": 260,
                "y1": 196.6666666667,
                "x2": 380,
                "y2": 173.3333333333,
            },
        },
        "completion": {"kind": "action"},
        "visual": {
            "kind": "clip",
            "request": {},
            "end_state": state_asset("2"),
        },
    }
    open_capture = {
        "action_id": "open",
        "kind": "open_page",
        "completion": {"kind": "navigation"},
        "visual": {"kind": "state", "state": state_asset("1")},
    }
    clip = {
        "path": "capture/fragments/" + "a" * 64 + ".mp4",
        "sha256": "a" * 64,
        "media_type": "video/mp4",
        "width": 1440,
        "height": 900,
        "duration_ms": 1000,
        "encoded_bytes": 200,
    }

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[0],
        action_captures=[open_capture, capture],
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("1"),
        initial_pointer={"x": 20, "y": 30, "visible": True},
        clip_assets={("browser", "move-sun"): clip},
    )

    drag = next(
        event for event in compiled.payload["events"] if event["kind"] == "drag"
    )
    motion = next(
        event for event in compiled.payload["events"] if event["kind"] == "clip"
    )
    final_state = next(
        event
        for event in compiled.payload["events"]
        if event["kind"] == "state" and event["action_id"] == "move-sun"
    )

    assert (motion["at_ms"], motion["end_ms"]) == (
        drag["at_ms"],
        drag["end_ms"],
    )
    assert motion["trim_end_ms"] == 900
    assert drag["end_ms"] - drag["at_ms"] == 900
    assert drag["curve"] == capture["motion"]["curve"]
    assert final_state["at_ms"] == drag["end_ms"]


def test_handoff_display_url_uses_the_captured_watch_url() -> None:
    plan = normalize_recording_plan(
        {
            "id": "handoff",
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
                                    "run": "watch",
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
    )
    watch_url = "http://127.0.0.1:43123/cast-player.html?manifest=demo"

    compiled = compile_browser_beat(
        plan.id,
        plan.beats[1],
        action_captures=[
            {
                "action_id": "open",
                "kind": "open_page",
                "completion": {"kind": "navigation", "url": watch_url},
                "visual": {"kind": "state", "state": state_asset("1")},
            }
        ],
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1},
        initial_state=state_asset("0"),
    )

    assert [
        event["value"]
        for event in compiled.payload["events"]
        if event["kind"] == "display_url"
    ] == [watch_url]


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
