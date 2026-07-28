from __future__ import annotations

import json
from pathlib import Path

import pytest

import omegaflow.presentation as presentation_module
from omegaflow.presentation import (
    PresentationValidationError,
    serialize_browser_payload,
    serialize_presentation_manifest,
    serialize_visualization_payload,
    validate_presentation_manifest,
    validate_relative_presentation_path,
    write_presentation_signatures,
)
from omegaflow.presentation_schema import (
    BrowserClickEventV1,
    BrowserPayloadV1,
    BrowserPointV1,
    BrowserPointerMoveEventV1,
    BrowserStateEventV1,
    BrowserViewportV1,
    PresentationAssetV1,
    PresentationAudioIntervalV1,
    PresentationAudioV1,
    PresentationBeatV1,
    PresentationBrowserHeaderV1,
    PresentationHeaderV1,
    PresentationManifestV1,
    PresentationPaneBeatV1,
    PresentationPaneLayoutV1,
    PresentationPaneTrackV1,
    PresentationPaneTransitionV1,
    PresentationPaneV1,
    PresentationRecordingV1,
    PresentationRendererV1,
    VisualizationPayloadV1,
    VisualizationHighlightV1,
    VisualizationTokenKind,
    VisualizationTokenV1,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def pane_title(text: str | None = None) -> dict[str, object]:
    return {
        "visible": True,
        "text": text,
        "alignment_x": "right",
        "alignment_y": "top",
        "position_x": "0.25rem",
        "position_y": "0.25rem",
    }


def browser_payload() -> BrowserPayloadV1:
    return BrowserPayloadV1(
        beat_id="browser",
        duration_ms=1000,
        viewport=BrowserViewportV1(width=1440, height=900),
        initial_state="initial",
        initial_display_url="https://example.test/",
        events=[
            BrowserClickEventV1(
                kind="click",
                action_id="open",
                at_ms=200,
                end_ms=250,
                point=BrowserPointV1(x=100, y=50),
            ),
            BrowserPointerMoveEventV1(
                kind="pointer_move",
                action_id="open",
                at_ms=200,
                end_ms=200,
                start=BrowserPointV1(x=0, y=0),
                end=BrowserPointV1(x=100, y=50),
            ),
            BrowserStateEventV1(
                kind="state",
                action_id="open",
                at_ms=250,
                end_ms=300,
                asset="final",
            ),
        ],
    )


def write_browser_bundle(tmp_path: Path, *, with_audio: bool = False) -> dict:
    media_dir = tmp_path / "media"
    beats_dir = tmp_path / "beats"
    media_dir.mkdir(parents=True)
    beats_dir.mkdir()
    initial = b"initial image"
    final = b"final image"
    (media_dir / "initial.png").write_bytes(initial)
    (media_dir / "final.png").write_bytes(final)
    payload = serialize_browser_payload(browser_payload(), action_ids=["open"])
    (beats_dir / "browser.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    audio = None
    if with_audio:
        audio_content = b"audio"
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        audio_name = "take.mp3"
        (audio_dir / audio_name).write_bytes(audio_content)
        (tmp_path / "audio.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "recording": "demo",
                    "duration_ms": 400,
                    "takes": [
                        {
                            "id": "take",
                            "src": f"audio/{audio_name}",
                            "source_start_ms": 0,
                            "source_end_ms": 400,
                            "timestamps": "timestamps/take.json",
                            "members": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        audio = PresentationAudioV1(
            metadata="audio.json",
            intervals=[
                PresentationAudioIntervalV1(
                    presentation_start_ms=100,
                    presentation_end_ms=500,
                    source_start_ms=0,
                    source_end_ms=400,
                )
            ],
        )
    manifest = PresentationManifestV1(
        recording=PresentationRecordingV1(id="demo", duration_ms=1000),
        renderers={"browser": PresentationRendererV1()},
        presentation=PresentationHeaderV1(browser=PresentationBrowserHeaderV1()),
        audio=audio,
        assets={
            "initial": PresentationAssetV1(
                path="media/initial.png",
                media_type="image/png",
            ),
            "final": PresentationAssetV1(
                path="media/final.png",
                media_type="image/png",
            ),
        },
        panes=[PresentationPaneV1(id="browser", renderer="browser")],
        beats=[
            PresentationBeatV1(
                id="outer",
                duration_ms=1000,
                layout=PresentationPaneLayoutV1(areas=[["browser"]]),
                pane_tracks=[
                    PresentationPaneTrackV1(
                        pane_id="browser",
                        beats=[
                            PresentationPaneBeatV1(
                                id="browser",
                                duration_ms=1000,
                                payload="beats/browser.json",
                                transition=PresentationPaneTransitionV1(),
                            )
                        ],
                    )
                ],
            )
        ],
    )
    serialized = serialize_presentation_manifest(manifest)
    write_presentation_signatures(tmp_path)
    return serialized


def multi_pane_manifest() -> dict:
    return {
        "manifest_version": 1,
        "recording": {
            "id": "fixture",
            "title": "Multi-pane fixture",
            "duration_ms": 2000,
        },
        "renderers": {
            "terminal": {"payload_version": 1},
            "browser": {"payload_version": 1},
        },
        "presentation": {
            "guided": False,
            "pane_chrome": {"style": "framed"},
            "browser": {
                "window": {
                    "mode": "none",
                    "theme": "kde-breeze",
                    "title": None,
                    "opening_transition": "cut",
                },
                "chrome": {"mode": "hidden"},
            },
        },
        "signatures": "signatures.json",
        "assets": {},
        "panes": [
            {"id": "source", "title": pane_title(), "renderer": "terminal"},
            {"id": "preview", "title": pane_title(), "renderer": "browser"},
        ],
        "beats": [
            {
                "id": "explain",
                "heading": "Explain",
                "offset_ms": 0,
                "duration_ms": 2000,
                "layout": {"areas": [["source", "preview"]]},
                "pane_tracks": [
                    {
                        "pane_id": "source",
                        "initial": "first",
                        "beats": [
                            {
                                "id": "definition",
                                "offset_ms": 0,
                                "duration_ms": 1000,
                                "payload": "beats/definition.cast",
                                "transition": {"kind": "cut", "duration_ms": 0},
                            },
                            {
                                "id": "result",
                                "offset_ms": 1200,
                                "duration_ms": 800,
                                "payload": "beats/result.cast",
                                "transition": {"kind": "fade", "duration_ms": 200},
                            },
                        ],
                    },
                    {
                        "pane_id": "preview",
                        "initial": "hidden",
                        "beats": [
                            {
                                "id": "preview",
                                "offset_ms": 500,
                                "duration_ms": 1500,
                                "payload": "beats/preview.browser.json",
                                "transition": {"kind": "fade", "duration_ms": 100},
                                "browser": {
                                    "window": {
                                        "mode": "none",
                                        "theme": "kde-breeze",
                                        "title": None,
                                        "opening_transition": "cut",
                                    },
                                    "chrome": {"mode": "hidden"},
                                },
                            }
                        ],
                    },
                ],
                "guide": None,
                "player": None,
                "transition_in": "cut",
            }
        ],
    }


def write_visualization_bundle(tmp_path: Path) -> dict:
    beats_dir = tmp_path / "beats"
    beats_dir.mkdir()
    text = 'title: "<script>alert(1)</script>"\nstatus: ready\n'
    payload = {
        "payload_version": 1,
        "beat_id": "definition",
        "duration_ms": 1000,
        "language": "yaml",
        "text": text,
        "highlights": [],
        "tokens": [
            {"start": 0, "end": 5, "kind": "key"},
            {"start": 7, "end": 34, "kind": "string"},
            {"start": 35, "end": 41, "kind": "key"},
            {"start": 43, "end": 48, "kind": "keyword"},
        ],
    }
    (beats_dir / "definition.visualization.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    manifest = {
        "manifest_version": 1,
        "recording": {
            "id": "visualization",
            "title": "Visualization fixture",
            "duration_ms": 1000,
        },
        "renderers": {"visualization": {"payload_version": 1}},
        "presentation": {
            "guided": False,
            "pane_chrome": {"style": "framed"},
        },
        "signatures": "signatures.json",
        "assets": {},
        "panes": [
            {"id": "definition", "title": pane_title(), "renderer": "visualization"}
        ],
        "beats": [
            {
                "id": "explain",
                "heading": "Explain a beat",
                "offset_ms": 0,
                "duration_ms": 1000,
                "layout": {"areas": [["definition"]]},
                "pane_tracks": [
                    {
                        "pane_id": "definition",
                        "initial": "first",
                        "beats": [
                            {
                                "id": "definition",
                                "offset_ms": 0,
                                "duration_ms": 1000,
                                "payload": "beats/definition.visualization.json",
                                "transition": {"kind": "cut", "duration_ms": 0},
                            }
                        ],
                    }
                ],
                "guide": None,
                "player": None,
                "transition_in": "cut",
            }
        ],
    }
    write_presentation_signatures(tmp_path)
    return manifest


def test_visualization_payload_serializes_typed_tokens() -> None:
    payload = VisualizationPayloadV1(
        beat_id="definition",
        duration_ms=1000,
        language="yaml",
        text="status: ready\n",
        highlights=[
            VisualizationHighlightV1(
                start=8,
                end=13,
                color="brand",
                start_ms=100,
                end_ms=900,
            )
        ],
        tokens=[
            VisualizationTokenV1(
                start=0,
                end=6,
                kind=VisualizationTokenKind.key,
            )
        ],
    )

    assert serialize_visualization_payload(payload) == {
        "payload_version": 1,
        "beat_id": "definition",
        "duration_ms": 1000,
        "language": "yaml",
        "text": "status: ready\n",
        "highlights": [
            {
                "start": 8,
                "end": 13,
                "color": "brand",
                "start_ms": 100,
                "end_ms": 900,
            }
        ],
        "tokens": [{"start": 0, "end": 6, "kind": "key"}],
    }


def test_manifest_validates_visualization_payload_and_token_ranges(
    tmp_path: Path,
) -> None:
    manifest = write_visualization_bundle(tmp_path)

    parsed = validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    assert parsed.panes[0].renderer == "visualization"


def test_checked_in_visualization_player_fixture_is_valid() -> None:
    root = REPO_ROOT / "tests/fixtures/visualization-player"
    manifest = json.loads(
        (root / "recording.presentation.json").read_text(encoding="utf-8")
    )

    parsed = validate_presentation_manifest(manifest, manifest_dir=root)

    assert [pane.renderer for pane in parsed.panes] == [
        "visualization",
        "terminal",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("overlap", "range must be ordered"),
        ("outside", "range must be ordered"),
        ("kind", "kind is unsupported"),
        ("unknown", "unknown fields"),
    ],
)
def test_manifest_rejects_invalid_visualization_tokens(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    manifest = write_visualization_bundle(tmp_path)
    payload_path = tmp_path / "beats/definition.visualization.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if mutation == "overlap":
        payload["tokens"][1]["start"] = 3
    elif mutation == "outside":
        payload["tokens"][0]["end"] = len(payload["text"]) + 1
    elif mutation == "kind":
        payload["tokens"][0]["kind"] = "html"
    else:
        payload["tokens"][0]["class_name"] = "unsafe"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    write_presentation_signatures(tmp_path)

    with pytest.raises(PresentationValidationError, match=message):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


def test_manifest_accepts_multi_pane_outer_beats_and_sequential_pane_beats() -> None:
    parsed = validate_presentation_manifest(multi_pane_manifest())

    assert [pane.id for pane in parsed.panes] == ["source", "preview"]
    assert parsed.beats[0].layout.areas == [["source", "preview"]]
    assert [beat.id for beat in parsed.beats[0].pane_tracks[0].beats] == [
        "definition",
        "result",
    ]


def test_manifest_rejects_duplicate_pane_beat_ids_across_outer_beats() -> None:
    manifest = multi_pane_manifest()
    first = manifest["beats"][0]
    first["duration_ms"] = 1000
    first["pane_tracks"][0]["beats"] = [first["pane_tracks"][0]["beats"][0]]
    first["pane_tracks"][1]["beats"][0].update(
        offset_ms=0,
        duration_ms=1000,
        transition={"kind": "cut", "duration_ms": 0},
    )
    second = json.loads(json.dumps(first))
    second.update(id="repeat", offset_ms=1000)
    manifest["beats"] = [first, second]

    with pytest.raises(
        PresentationValidationError,
        match=r"duplicate pane beat id 'definition' in pane 'source'",
    ):
        validate_presentation_manifest(manifest)


def test_manifest_bounds_panes_and_aggregate_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = multi_pane_manifest()
    monkeypatch.setattr(presentation_module, "PRESENTATION_PANE_LIMIT", 1)
    with pytest.raises(PresentationValidationError, match="panes exceeds 1"):
        validate_presentation_manifest(manifest)

    monkeypatch.setattr(presentation_module, "PRESENTATION_PANE_LIMIT", 2)
    monkeypatch.setattr(presentation_module, "PRESENTATION_ITEM_LIMIT", 1)
    with pytest.raises(
        PresentationValidationError,
        match="aggregate structure exceeds 1",
    ):
        validate_presentation_manifest(manifest)


def test_browser_payload_bounds_events(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = serialize_browser_payload(browser_payload(), action_ids=["open"])
    monkeypatch.setattr(presentation_module, "PRESENTATION_ITEM_LIMIT", 2)

    with pytest.raises(PresentationValidationError, match="events exceeds 2"):
        presentation_module.validate_browser_payload(payload)


def test_browser_clip_audio_flag_must_be_boolean() -> None:
    event = {
        "kind": "clip",
        "action_id": "play",
        "at_ms": 0,
        "end_ms": 500,
        "asset": "clip",
        "trim_start_ms": 0,
        "trim_end_ms": 500,
        "has_audio": "true",
    }

    with pytest.raises(
        PresentationValidationError,
        match=r"browser\.events\.0\.has_audio must be boolean",
    ):
        presentation_module.validate_browser_event(
            event,
            index=0,
            duration_ms=500,
        )


def test_terminal_cast_bounds_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cast = tmp_path / "beat.cast"
    cast.write_text(
        '{"version":2,"width":80,"height":24}\n'
        '[0.0,"o","A"]\n'
        '[0.1,"o","B"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(presentation_module, "PRESENTATION_ITEM_LIMIT", 1)

    with pytest.raises(PresentationValidationError, match="events exceeds 1"):
        presentation_module._validate_terminal_cast(
            cast,
            duration_ms=100,
            field="terminal payload",
        )


@pytest.mark.parametrize("pane_count", [3, 4])
def test_manifest_accepts_three_and_four_pane_layouts(pane_count: int) -> None:
    manifest = multi_pane_manifest()
    beat = manifest["beats"][0]
    for index in range(2, pane_count):
        pane_id = f"support-{index}"
        manifest["panes"].append(
            {"id": pane_id, "title": pane_title(), "renderer": "terminal"}
        )
        beat["layout"]["areas"][0].append(pane_id)
        beat["pane_tracks"].append(
            {
                "pane_id": pane_id,
                "initial": "first",
                "beats": [
                    {
                        "id": pane_id,
                        "offset_ms": 0,
                        "duration_ms": 2000,
                        "payload": f"beats/{pane_id}.cast",
                        "transition": {"kind": "cut", "duration_ms": 0},
                    }
                ],
            }
        )

    parsed = validate_presentation_manifest(manifest)

    assert len(parsed.panes) == pane_count
    assert len(parsed.beats[0].pane_tracks) == pane_count


def test_manifest_rejects_the_replaced_flat_beat_shape() -> None:
    manifest = multi_pane_manifest()
    beat = manifest["beats"][0]
    beat["renderer"] = "terminal"
    beat["payload"] = "beats/source.cast"
    beat.pop("layout")
    beat.pop("pane_tracks")

    with pytest.raises(
        PresentationValidationError,
        match=r"unknown fields: payload, renderer",
    ):
        validate_presentation_manifest(manifest)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest["beats"][0]["layout"]["areas"][0].append(
                "missing"
            ),
            "unknown pane",
        ),
        (
            lambda manifest: manifest["beats"][0]["pane_tracks"].pop(),
            "layout and pane tracks",
        ),
        (
            lambda manifest: manifest["beats"][0]["pane_tracks"][0]["beats"][
                1
            ].update(offset_ms=900),
            "overlaps",
        ),
        (
            lambda manifest: manifest["beats"][0]["pane_tracks"][0]["beats"][
                1
            ]["transition"].update(duration_ms=900),
            "transition duration",
        ),
        (
            lambda manifest: manifest["beats"][0]["pane_tracks"][0]["beats"][
                0
            ]["transition"].update(duration_ms=1),
            "cut transition",
        ),
        (
            lambda manifest: manifest["beats"][0]["layout"].update(
                areas=[
                    ["source", "preview"],
                    ["preview", "source"],
                ]
            ),
            "contiguous rectangle",
        ),
    ],
)
def test_manifest_rejects_invalid_multi_pane_structure(
    mutate,
    message: str,
) -> None:
    manifest = multi_pane_manifest()
    mutate(manifest)

    with pytest.raises(PresentationValidationError, match=message):
        validate_presentation_manifest(manifest)


def test_browser_payload_serialization_uses_fixed_event_order() -> None:
    payload = serialize_browser_payload(browser_payload(), action_ids=["open"])

    assert [event["kind"] for event in payload["events"]] == [
        "pointer_move",
        "click",
        "state",
    ]


def test_manifest_validates_paths_assets_payloads_and_audio(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path, with_audio=True)

    parsed = validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    assert parsed.recording.duration_ms == 1000
    assert parsed.audio is not None


def test_manifest_audio_source_gap_reports_when_presentation_gap_is_too_short(
    tmp_path: Path,
) -> None:
    manifest = write_browser_bundle(tmp_path, with_audio=True)
    manifest["audio"]["intervals"] = [
        {
            "presentation_start_ms": 100,
            "presentation_end_ms": 250,
            "source_start_ms": 0,
            "source_end_ms": 150,
        },
        {
            "presentation_start_ms": 275,
            "presentation_end_ms": 475,
            "source_start_ms": 200,
            "source_end_ms": 400,
        },
    ]

    with pytest.raises(
        PresentationValidationError,
        match=(
            r"manifest audio\.intervals\.1 source gap is 50ms but "
            r"presentation gap is only 25ms"
        ),
    ):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


@pytest.mark.parametrize(
    "path",
    ["/absolute/file", "../escape", "beats/../escape", "beats//payload.json", "a\\b"],
)
def test_manifest_paths_must_be_normalized_and_relative(path: str) -> None:
    with pytest.raises(PresentationValidationError):
        validate_relative_presentation_path(path, field="path")


def test_signature_sidecar_updates_content_identity_without_renaming_asset(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "audio/take.mp3"
    asset.parent.mkdir()
    asset.write_bytes(b"first")
    sidecar_path = write_presentation_signatures(tmp_path)
    first = json.loads(sidecar_path.read_text(encoding="utf-8"))

    asset.write_bytes(b"second")
    write_presentation_signatures(tmp_path)
    second = json.loads(sidecar_path.read_text(encoding="utf-8"))

    assert set(first["files"]) == {"audio/take.mp3"}
    assert set(second["files"]) == {"audio/take.mp3"}
    assert first["files"]["audio/take.mp3"]["sha256"] != (
        second["files"]["audio/take.mp3"]["sha256"]
    )


def test_manifest_rejects_timing_and_asset_integrity_errors(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["recording"]["duration_ms"] = 999
    with pytest.raises(PresentationValidationError, match="final beat end"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    root = tmp_path / "second"
    manifest = write_browser_bundle(root)
    (root / "media/initial.png").write_bytes(b"tampered")
    with pytest.raises(PresentationValidationError, match="does not match"):
        validate_presentation_manifest(manifest, manifest_dir=root)


def test_manifest_rejects_mismatched_audio_interval_durations(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path, with_audio=True)
    manifest["audio"]["intervals"][0]["source_start_ms"] = 1

    with pytest.raises(
        PresentationValidationError,
        match="presentation duration is 400ms but source duration is 399ms",
    ):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


def test_manifest_rejects_invalid_renderer_presentation_header(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["presentation"]["browser"]["chrome"]["mode"] = "captured"

    with pytest.raises(PresentationValidationError, match="chrome.mode is invalid"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


def test_manifest_presentation_header_accepts_typed_guided_default(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["presentation"]["guided"] = True

    validated = validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    assert validated.presentation.guided is True

    manifest["presentation"]["guided"] = "true"
    with pytest.raises(PresentationValidationError, match="guided must be a boolean"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


def test_manifest_rejects_invalid_pane_chrome_style(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["presentation"]["pane_chrome"]["style"] = "ornate"

    with pytest.raises(
        PresentationValidationError,
        match="pane_chrome.style must be none or framed",
    ):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


def test_manifest_guide_preserves_typed_commands(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["beats"][0]["guide"] = {
        "commands": ["python -m pip install omegaflow"],
        "summary": "Install the package before continuing.",
        "success_hint": "Install OmegaFlow.",
    }

    validated = validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    assert validated.beats[0].guide is not None
    assert validated.beats[0].guide.commands == [
        "python -m pip install omegaflow"
    ]
    assert validated.beats[0].guide.summary == (
        "Install the package before continuing."
    )

    manifest["beats"][0]["guide"]["commands"] = [""]
    with pytest.raises(PresentationValidationError, match="guide.commands.0"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    manifest["beats"][0]["guide"]["commands"] = []
    manifest["beats"][0]["guide"]["summary"] = 7
    with pytest.raises(PresentationValidationError, match="guide.summary"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)


def test_manifest_beat_accepts_only_known_player_toolbar_controls(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["beats"][0]["player"] = {
        "highlight": {
            "control": "guided",
            "start_ms": 200,
            "end_ms": 1000,
        }
    }

    validated = validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    assert validated.beats[0].player is not None
    assert validated.beats[0].player.highlight is not None
    assert validated.beats[0].player.highlight.control == "guided"
    assert validated.beats[0].player.highlight.start_ms == 200
    assert validated.beats[0].player.highlight.end_ms == 1000

    manifest["beats"][0]["player"]["highlight"]["control"] = "download"
    with pytest.raises(
        PresentationValidationError,
        match="player.highlight.control is invalid",
    ):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    manifest["beats"][0]["player"]["highlight"]["control"] = "guided"
    manifest["beats"][0]["player"]["highlight"]["end_ms"] = 1500
    with pytest.raises(PresentationValidationError, match="highlight timing is invalid"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)
