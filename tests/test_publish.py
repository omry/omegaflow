from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import omegaflow.publish as publish_module
from omegaflow.presentation import (
    PresentationValidationError,
    serialize_presentation_manifest,
    validate_presentation_manifest,
)
from omegaflow.presentation_schema import (
    PresentationBeatV1,
    PresentationHeaderV1,
    PresentationManifestV1,
    PresentationRecordingV1,
    PresentationRendererV1,
)
from omegaflow.publish import (
    PublicBundleError,
    publish_public_bundle,
    validate_public_staging,
)


def recording_metadata(recording_id: str = "demo") -> dict[str, object]:
    return {
        "version": 1,
        "recording": recording_id,
        "capture_fingerprint": "1" * 64,
        "presentation_fingerprint": "2" * 64,
        "dependencies": [
            {"path": "recordings/demo.yaml", "sha256": "3" * 64}
        ],
        "versions": {"compiler": "presentation-v1", "renderer": "payload-v1"},
        "warnings": ["LIVE_NETWORK_REPRODUCIBILITY"],
    }


def write_terminal_bundle(root: Path, *, output: str = "ok") -> Path:
    beats = root / "beats"
    beats.mkdir(parents=True)
    cast = beats / "one.cast"
    cast.write_text(
        json.dumps({"version": 2, "width": 80, "height": 24})
        + "\n"
        + json.dumps([1.0, "o", output])
        + "\n",
        encoding="utf-8",
    )
    manifest = PresentationManifestV1(
        recording=PresentationRecordingV1(id="demo", duration_ms=1000),
        renderers={"terminal": PresentationRendererV1()},
        presentation=PresentationHeaderV1(),
        beats=[
            PresentationBeatV1(
                id="one",
                renderer="terminal",
                duration_ms=1000,
                payload="beats/one.cast",
            )
        ],
    )
    (root / "recording.presentation.json").write_text(
        json.dumps(serialize_presentation_manifest(manifest)),
        encoding="utf-8",
    )
    (root / "recording.recording.json").write_text(
        json.dumps(recording_metadata()), encoding="utf-8"
    )
    return root


def add_narration_bundle(root: Path) -> Path:
    audio_content = b"public audio fixture"
    audio_sha256 = hashlib.sha256(audio_content).hexdigest()
    audio_dir = root / "audio"
    audio_dir.mkdir()
    audio_name = f"take-{audio_sha256}.mp3"
    (audio_dir / audio_name).write_bytes(audio_content)
    (root / "timestamps").mkdir()
    (root / "timestamps/take.json").write_text(
        json.dumps(
            {
                "version": 1,
                "take_id": "take",
                "duration_ms": 1000,
                "members": [
                    {
                        "beat_id": "one",
                        "text_start": 0,
                        "text_end": 5,
                        "source_start_ms": 0,
                        "source_end_ms": 1000,
                    }
                ],
                "words": [
                    {
                        "text": "Hello",
                        "text_start": 0,
                        "text_end": 5,
                        "start_ms": 0,
                        "end_ms": 1000,
                    }
                ],
                "anchors": [],
                "waits": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "audio.json").write_text(
        json.dumps(
            {
                "version": 3,
                "recording": "demo",
                "duration_ms": 1000,
                "takes": [
                    {
                        "id": "take",
                        "src": f"audio/{audio_name}",
                        "sha256": audio_sha256,
                        "source_start_ms": 0,
                        "source_end_ms": 1000,
                        "timestamps": "timestamps/take.json",
                        "members": [
                            {
                                "beat_id": "one",
                                "text": "Hello",
                                "text_start": 0,
                                "text_end": 5,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = root / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["audio"] = {
        "metadata": "audio.json",
        "intervals": [
            {
                "presentation_start_ms": 0,
                "presentation_end_ms": 1000,
                "source_start_ms": 0,
                "source_end_ms": 1000,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_public_staging_accepts_only_the_closed_reachable_bundle(tmp_path: Path) -> None:
    root = write_terminal_bundle(tmp_path / "bundle")

    manifest = validate_public_staging(root)

    assert manifest["recording"]["id"] == "demo"


def test_public_staging_validates_v3_narration_metadata_and_sidecar(
    tmp_path: Path,
) -> None:
    root = add_narration_bundle(write_terminal_bundle(tmp_path / "valid"))
    validate_public_staging(root)

    root = add_narration_bundle(write_terminal_bundle(tmp_path / "tampered"))
    sidecar_path = root / "timestamps/take.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["members"][0]["source_end_ms"] = 900
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(PublicBundleError, match="cover take"):
        validate_public_staging(root)


@pytest.mark.parametrize("layer", ["presentation", "publish"])
@pytest.mark.parametrize("mutation", ["bytes", "hash", "hashless-path"])
def test_narration_audio_integrity_tampering_is_rejected_by_each_validation_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    layer: str,
    mutation: str,
) -> None:
    root = add_narration_bundle(write_terminal_bundle(tmp_path / f"{layer}-{mutation}"))
    metadata_path = root / "audio.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    take = metadata["takes"][0]
    audio_path = root / take["src"]

    if mutation == "bytes":
        audio_path.write_bytes(b"tampered audio")
    elif mutation == "hash":
        take["sha256"] = "0" * 64
    else:
        renamed = root / "audio/take.mp3"
        audio_path.rename(renamed)
        take["src"] = renamed.relative_to(root).as_posix()

    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    if layer == "presentation":
        manifest = json.loads(
            (root / "recording.presentation.json").read_text(encoding="utf-8")
        )
        with pytest.raises(PresentationValidationError, match="sha256|content hash"):
            validate_presentation_manifest(manifest, manifest_dir=root)
    else:
        monkeypatch.setattr(
            publish_module,
            "validate_presentation_manifest",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(PublicBundleError, match="boundaries"):
            validate_public_staging(root)


@pytest.mark.parametrize("audio_name", ["audio/take.mp3", "audio/take.opus", "audio/take.wav"])
def test_public_allowlist_retains_supported_audio_names(audio_name: str) -> None:
    assert publish_module._allowlisted_path(audio_name)


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("diagnostics/trace.zip", b"private"),
        ("capture/browser.capture.jsonl", b"private"),
        ("beats/unreferenced.cast", b'{"version":2}\n'),
        ("media/unreferenced.webp", b"image"),
    ],
)
def test_public_staging_rejects_private_or_unreferenced_files(
    tmp_path: Path, relative: str, content: bytes
) -> None:
    root = write_terminal_bundle(tmp_path / "bundle")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    with pytest.raises(PublicBundleError):
        validate_public_staging(root)


def test_public_staging_rejects_symlinks_secrets_and_private_paths(
    tmp_path: Path,
) -> None:
    root = write_terminal_bundle(tmp_path / "symlink")
    (root / "beats" / "one.cast").unlink()
    (root / "beats" / "one.cast").symlink_to(tmp_path / "outside.cast")
    with pytest.raises(PublicBundleError, match="symlink|unsafe"):
        validate_public_staging(root)

    root = write_terminal_bundle(tmp_path / "secret", output="private-token")
    with pytest.raises(PublicBundleError, match="secret"):
        validate_public_staging(root, secrets=["private-token"])

    root = write_terminal_bundle(tmp_path / "path", output=str(tmp_path / "private"))
    with pytest.raises(PublicBundleError, match="private path|absolute path"):
        validate_public_staging(root, private_paths=[tmp_path])


def test_public_staging_rejects_hash_and_schema_tampering(tmp_path: Path) -> None:
    root = write_terminal_bundle(tmp_path / "bundle")
    metadata_path = root / "recording.recording.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["capture_fingerprint"] = "not-a-hash"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(PublicBundleError, match="hash"):
        validate_public_staging(root)

    root = write_terminal_bundle(tmp_path / "unknown")
    metadata_path = root / "recording.recording.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["private_spec"] = {"password": "bad"}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(PublicBundleError, match="fields"):
        validate_public_staging(root)

    root = write_terminal_bundle(tmp_path / "traversal")
    metadata_path = root / "recording.recording.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["dependencies"][0]["path"] = "../private.yaml"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(PublicBundleError, match="normalized relative path"):
        validate_public_staging(root)


def test_public_staging_rejects_manifest_asset_hash_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "browser"
    (root / "beats").mkdir(parents=True)
    (root / "media").mkdir()
    content = b"not really webp"
    (root / "media" / "state.webp").write_bytes(content)
    payload = {
        "payload_version": 1,
        "beat_id": "browser",
        "duration_ms": 1000,
        "viewport": {"width": 100, "height": 50, "device_scale_factor": 1},
        "initial_state": "state",
        "initial_pointer": {"x": 50, "y": 25, "visible": True},
        "initial_display_url": None,
        "animation_policies": {"pointer": "pointer-v1", "typing": "natural-v1"},
        "events": [],
    }
    (root / "beats" / "browser.browser.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    manifest = {
        "manifest_version": 1,
        "recording": {"id": "demo", "title": None, "duration_ms": 1000},
        "renderers": {"browser": {"payload_version": 1}},
        "presentation": {
            "browser": {
                "window": {"mode": "none", "theme": "kde-breeze", "title": None},
                "chrome": {"mode": "hidden"},
            }
        },
        "assets": {
            "state": {
                "path": "media/state.webp",
                "media_type": "image/webp",
                "sha256": hashlib.sha256(b"different").hexdigest(),
                "bytes": len(content),
            }
        },
        "beats": [
            {
                "id": "browser",
                "heading": "",
                "renderer": "browser",
                "offset_ms": 0,
                "duration_ms": 1000,
                "payload": "beats/browser.browser.json",
                "guide": None,
                "transition_in": None,
            }
        ],
    }
    (root / "recording.presentation.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / "recording.recording.json").write_text(
        json.dumps(recording_metadata()), encoding="utf-8"
    )

    with pytest.raises(PublicBundleError, match="sha256"):
        validate_public_staging(root)


def test_public_staging_probes_valid_browser_state_and_muted_clip(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg and ffprobe are required for browser media validation")
    root = tmp_path / "browser-media"
    (root / "beats").mkdir(parents=True)
    (root / "media").mkdir()
    state = root / "media/state.webp"
    clip = root / "media/clip.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x18:d=0.1",
            "-frames:v",
            "1",
            str(state),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x18:d=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip),
        ],
        check=True,
    )
    payload = {
        "payload_version": 1,
        "beat_id": "browser",
        "duration_ms": 400,
        "viewport": {"width": 32, "height": 18, "device_scale_factor": 1},
        "initial_state": "state",
        "initial_pointer": {"x": 1, "y": 1, "visible": True},
        "initial_display_url": None,
        "animation_policies": {"pointer": "pointer-v1", "typing": "natural-v1"},
        "events": [
            {
                "kind": "clip",
                "action_id": "dynamic",
                "at_ms": 0,
                "end_ms": 400,
                "asset": "clip",
                "trim_start_ms": 0,
                "trim_end_ms": 400,
            }
        ],
    }
    (root / "beats/browser.browser.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assets = {}
    for asset_id, path, media_type in (
        ("state", state, "image/webp"),
        ("clip", clip, "video/mp4"),
    ):
        content = path.read_bytes()
        assets[asset_id] = {
            "path": path.relative_to(root).as_posix(),
            "media_type": media_type,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    manifest = {
        "manifest_version": 1,
        "recording": {"id": "demo", "title": None, "duration_ms": 400},
        "renderers": {"browser": {"payload_version": 1}},
        "presentation": {
            "browser": {
                "window": {"mode": "none", "theme": "kde-breeze", "title": None},
                "chrome": {"mode": "hidden"},
            }
        },
        "assets": assets,
        "beats": [
            {
                "id": "browser",
                "heading": "",
                "renderer": "browser",
                "offset_ms": 0,
                "duration_ms": 400,
                "payload": "beats/browser.browser.json",
                "guide": None,
                "transition_in": None,
            }
        ],
    }
    (root / "recording.presentation.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / "recording.recording.json").write_text(
        json.dumps(recording_metadata()), encoding="utf-8"
    )

    validate_public_staging(root, ffprobe=ffprobe)


def test_atomic_publish_replaces_valid_bundle_and_rolls_back_failure(
    tmp_path: Path,
) -> None:
    source = write_terminal_bundle(tmp_path / "source")
    destination = tmp_path / "public"
    destination.mkdir()
    marker = destination / "old.txt"
    marker.write_text("old", encoding="utf-8")

    publish_public_bundle(source, destination)

    assert not marker.exists()
    assert (destination / "recording.presentation.json").is_file()

    replacement = write_terminal_bundle(tmp_path / "replacement", output="new")
    calls = 0

    def fail_second_replace(source_path: object, destination_path: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        os.replace(source_path, destination_path)

    with pytest.raises(PublicBundleError, match="atomically replace"):
        publish_public_bundle(
            replacement,
            destination,
            replace=fail_second_replace,
        )

    assert (destination / "recording.presentation.json").is_file()
    assert "ok" in (destination / "beats" / "one.cast").read_text(encoding="utf-8")


def test_atomic_publish_preserves_backup_when_rollback_fails(tmp_path: Path) -> None:
    source = write_terminal_bundle(tmp_path / "source", output="new")
    destination = write_terminal_bundle(tmp_path / "public", output="old")
    calls = 0

    def fail_publish_and_rollback(source_path: object, destination_path: object) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError(f"injected failure {calls}")
        os.replace(source_path, destination_path)

    with pytest.raises(PublicBundleError, match="previous bundle remains at") as exc:
        publish_public_bundle(
            source,
            destination,
            replace=fail_publish_and_rollback,
        )

    backup = Path(str(exc.value).split("previous bundle remains at ", 1)[1].split(":", 1)[0])
    assert not destination.exists()
    assert (backup / "recording.presentation.json").is_file()
    assert "old" in (backup / "beats" / "one.cast").read_text(encoding="utf-8")


def test_atomic_publish_preserves_destination_when_staging_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_terminal_bundle(tmp_path / "source", output="new")
    destination = write_terminal_bundle(tmp_path / "public", output="old")

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected copy failure")

    monkeypatch.setattr(publish_module.shutil, "copytree", fail_copy)
    with pytest.raises(PublicBundleError, match="atomically replace"):
        publish_public_bundle(source, destination)

    assert "old" in (destination / "beats" / "one.cast").read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".public.staging-*"))
