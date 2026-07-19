from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omegaflow.presentation import (
    PresentationValidationError,
    serialize_browser_payload,
    serialize_presentation_manifest,
    validate_presentation_manifest,
    validate_relative_presentation_path,
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
    PresentationRecordingV1,
    PresentationRendererV1,
)


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
        audio_sha256 = hashlib.sha256(audio_content).hexdigest()
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        audio_name = f"take-{audio_sha256}.mp3"
        (audio_dir / audio_name).write_bytes(audio_content)
        (tmp_path / "audio.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "recording": "demo",
                    "duration_ms": 400,
                    "takes": [
                        {
                            "id": "take",
                            "src": f"audio/{audio_name}",
                            "sha256": audio_sha256,
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
                sha256=hashlib.sha256(initial).hexdigest(),
                bytes=len(initial),
            ),
            "final": PresentationAssetV1(
                path="media/final.png",
                media_type="image/png",
                sha256=hashlib.sha256(final).hexdigest(),
                bytes=len(final),
            ),
        },
        beats=[
            PresentationBeatV1(
                id="browser",
                renderer="browser",
                duration_ms=1000,
                payload="beats/browser.json",
            )
        ],
    )
    return serialize_presentation_manifest(manifest)


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


def test_manifest_rejects_timing_and_asset_integrity_errors(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["recording"]["duration_ms"] = 999
    with pytest.raises(PresentationValidationError, match="final beat end"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    manifest = write_browser_bundle(tmp_path / "second")
    manifest["assets"]["initial"]["sha256"] = "0" * 64
    with pytest.raises(PresentationValidationError, match="does not match"):
        validate_presentation_manifest(manifest, manifest_dir=tmp_path / "second")


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


def test_manifest_guide_preserves_typed_commands(tmp_path: Path) -> None:
    manifest = write_browser_bundle(tmp_path)
    manifest["beats"][0]["guide"] = {
        "commands": ["python -m pip install omegaflow"],
        "success_hint": "Install OmegaFlow.",
    }

    validated = validate_presentation_manifest(manifest, manifest_dir=tmp_path)

    assert validated.beats[0].guide is not None
    assert validated.beats[0].guide.commands == [
        "python -m pip install omegaflow"
    ]

    manifest["beats"][0]["guide"]["commands"] = [""]
    with pytest.raises(PresentationValidationError, match="guide.commands.0"):
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
