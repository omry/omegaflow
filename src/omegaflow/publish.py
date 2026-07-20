"""Closed-allowlist validation and atomic publication for presentation bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .presentation import (
    PresentationValidationError,
    validate_presentation_manifest,
    validate_relative_presentation_path,
)
from .presentation_schema import (
    NarrationAudioMetadataV3,
    NarrationTimestampSidecarV1,
    PublishedRecordingMetadataV1,
)


class PublicBundleError(RuntimeError):
    """Raised when a public bundle is unsafe, invalid, or cannot be replaced."""


T = TypeVar("T")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
WARNING_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
PUBLIC_AUDIO_SUFFIXES = {".aac", ".flac", ".mp3", ".opus", ".pcm", ".wav"}
GENERIC_PRIVATE_PATH_RE = re.compile(
    r"(?<![:A-Za-z0-9])(?:/(?:Users|home|tmp|private|var|etc|workspace)/|[A-Za-z]:\\)"
)


def validate_public_staging(
    root: Path,
    *,
    secrets: Iterable[str] = (),
    private_paths: Iterable[Path | str] = (),
    ffprobe: str | None = None,
) -> dict[str, Any]:
    """Validate the complete public reference graph rooted at the manifest."""

    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise PublicBundleError("public staging root must be a real directory")
    files = _walk_allowlisted_files(root)
    manifest_path = root / "recording.presentation.json"
    metadata_path = root / "recording.recording.json"
    if manifest_path not in files or metadata_path not in files:
        raise PublicBundleError(
            "public staging requires recording.presentation.json and recording.recording.json"
        )

    secret_values = tuple(value for value in secrets if isinstance(value, str) and value)
    private_values = tuple(
        str(Path(value).expanduser().absolute()) for value in private_paths
    )
    parsed_json: dict[Path, dict[str, Any]] = {}
    for path in sorted(files):
        if path.suffix.lower() in {".json", ".cast"}:
            text = _public_text(path)
            _scan_public_text(
                text,
                path=path,
                secrets=secret_values,
                private_paths=private_values,
            )
            if path.suffix.lower() == ".json":
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise PublicBundleError(f"public JSON is invalid: {path.name}") from exc
                if not isinstance(value, dict):
                    raise PublicBundleError(f"public JSON must be a mapping: {path.name}")
                parsed_json[path] = value

    manifest = parsed_json[manifest_path]
    try:
        validate_presentation_manifest(manifest, manifest_dir=root)
    except PresentationValidationError as exc:
        raise PublicBundleError(f"presentation manifest is invalid: {exc}") from exc
    recording_metadata = _validate_recording_metadata(parsed_json[metadata_path])
    if recording_metadata.recording != manifest["recording"]["id"]:
        raise PublicBundleError("published recording metadata does not match the manifest")

    referenced = {manifest_path, metadata_path}
    expected_dimensions: dict[str, set[tuple[int, int]]] = {}
    expected_media_suffixes: dict[str, set[str]] = {}
    clip_requirements: dict[str, tuple[int, int, int]] = {}
    for beat in manifest["beats"]:
        payload_path = root / beat["payload"]
        referenced.add(payload_path)
        if beat["renderer"] != "browser":
            continue
        payload = parsed_json.get(payload_path)
        if payload is None:
            raise PublicBundleError(f"browser payload is not JSON: {beat['payload']}")
        viewport = payload["viewport"]
        width = round(viewport["width"] * viewport["device_scale_factor"])
        height = round(viewport["height"] * viewport["device_scale_factor"])
        asset_ids = {payload["initial_state"]}
        expected_media_suffixes.setdefault(payload["initial_state"], set()).add(".webp")
        for event in payload["events"]:
            kind = event["kind"]
            if kind == "state":
                asset_ids.add(event["asset"])
                expected_media_suffixes.setdefault(event["asset"], set()).add(".webp")
            elif kind == "clip":
                asset_ids.add(event["asset"])
                expected_media_suffixes.setdefault(event["asset"], set()).add(".mp4")
            elif kind == "scroll":
                asset_ids.update((event["start_asset"], event["end_asset"]))
                expected_media_suffixes.setdefault(event["start_asset"], set()).add(".webp")
                expected_media_suffixes.setdefault(event["end_asset"], set()).add(".webp")
            if kind == "clip":
                clip_requirements[event["asset"]] = (
                    width,
                    height,
                    event["trim_end_ms"],
                )
        for asset_id in asset_ids:
            expected_dimensions.setdefault(asset_id, set()).add((width, height))

    for asset_id, asset in manifest["assets"].items():
        asset_path = root / asset["path"]
        referenced.add(asset_path)
        dimensions = expected_dimensions.get(asset_id, set())
        suffixes = expected_media_suffixes.get(asset_id, set())
        if suffixes and {asset_path.suffix.lower()} != suffixes:
            raise PublicBundleError(
                f"asset {asset_id!r} has the wrong media class for its browser event"
            )
        if len(dimensions) > 1:
            raise PublicBundleError(
                f"asset {asset_id!r} is shared across incompatible browser viewports"
            )
        if dimensions and asset_path.suffix.lower() in {".webp", ".mp4"}:
            probe = _probe_public_media(asset_path, ffprobe=ffprobe)
            if (probe["width"], probe["height"]) != next(iter(dimensions)):
                raise PublicBundleError(f"asset {asset_id!r} dimensions do not match its viewport")
            if asset_path.suffix.lower() == ".mp4":
                if (
                    probe["has_audio"]
                    or probe["codec"] != "h264"
                    or "mp4" not in probe["format_name"].split(",")
                    or probe["pixel_format"] != "yuv420p"
                ):
                    raise PublicBundleError(
                        f"clip asset {asset_id!r} must be muted H.264 MP4"
                    )
                required_duration = clip_requirements.get(asset_id, (0, 0, 0))[2]
                if probe["duration_ms"] < required_duration:
                    raise PublicBundleError(
                        f"clip asset {asset_id!r} is shorter than its presentation trim"
                    )

    audio = manifest.get("audio")
    if audio is not None:
        audio_metadata_path = root / audio["metadata"]
        referenced.add(audio_metadata_path)
        audio_metadata = parsed_json.get(audio_metadata_path)
        if audio_metadata is None:
            raise PublicBundleError("narration audio metadata is missing")
        typed_audio = _structured(
            audio_metadata,
            NarrationAudioMetadataV3,
            required={"version", "recording", "duration_ms", "takes"},
            field="narration audio metadata",
        )
        if (
            typed_audio.version != 3
            or typed_audio.recording != manifest["recording"]["id"]
            or typed_audio.duration_ms < 0
        ):
            raise PublicBundleError("narration audio metadata identity is invalid")
        expected_source_start = 0
        expected_playback_start = 0
        playback_end_ms: int | None = None
        take_ids: set[str] = set()
        beat_ids = {beat["id"] for beat in manifest["beats"]}
        for take in typed_audio.takes:
            relative_audio = _public_relative_path(
                take.src, field=f"audio for take {take.id!r}"
            )
            take_audio_path = root / relative_audio
            if (
                not take.id
                or take.id in take_ids
                or SHA256_RE.fullmatch(take.sha256) is None
                or take.sha256 not in relative_audio
                or take_audio_path not in files
                or hashlib.sha256(take_audio_path.read_bytes()).hexdigest()
                != take.sha256
                or take.source_start_ms != expected_source_start
                or take.source_end_ms < take.source_start_ms
                or take.source_end_ms > typed_audio.duration_ms
                or not take.members
            ):
                raise PublicBundleError("narration audio take boundaries are invalid")
            referenced.add(take_audio_path)
            playback_values = (
                take.playback_src,
                take.playback_sha256,
                take.playback_start_ms,
                take.playback_end_ms,
            )
            if any(value is not None for value in playback_values):
                if any(value is None for value in playback_values):
                    raise PublicBundleError(
                        "narration audio playback metadata is incomplete"
                    )
                relative_playback = _public_relative_path(
                    str(take.playback_src),
                    field=f"playback audio for take {take.id!r}",
                )
                playback_path = root / relative_playback
                if (
                    not isinstance(take.playback_sha256, str)
                    or SHA256_RE.fullmatch(take.playback_sha256) is None
                    or take.playback_sha256 not in relative_playback
                    or playback_path not in files
                    or hashlib.sha256(playback_path.read_bytes()).hexdigest()
                    != take.playback_sha256
                    or take.playback_start_ms != expected_playback_start
                    or not isinstance(take.playback_end_ms, int)
                    or take.playback_end_ms <= expected_playback_start
                ):
                    raise PublicBundleError(
                        "narration audio playback boundaries are invalid"
                    )
                referenced.add(playback_path)
                expected_playback_start = take.playback_end_ms
                playback_end_ms = take.playback_end_ms
            take_ids.add(take.id)
            expected_source_start = take.source_end_ms
            text_cursor = 0
            for index, member in enumerate(take.members):
                if (
                    member.beat_id not in beat_ids
                    or member.text_start != text_cursor
                    or member.text_end - member.text_start != len(member.text)
                ):
                    raise PublicBundleError(
                        f"narration audio members are invalid for take {take.id!r}"
                    )
                text_cursor = member.text_end + (1 if index + 1 < len(take.members) else 0)
            relative = _public_relative_path(
                take.timestamps, field=f"timestamps for take {take.id!r}"
            )
            timestamp_path = root / relative
            referenced.add(timestamp_path)
            value = parsed_json.get(timestamp_path)
            if value is None:
                raise PublicBundleError(f"timestamp sidecar is missing for take {take.id!r}")
            sidecar = _structured(
                value,
                NarrationTimestampSidecarV1,
                required={
                    "version",
                    "take_id",
                    "duration_ms",
                    "members",
                    "words",
                    "anchors",
                    "waits",
                },
                field=f"timestamp sidecar for take {take.id!r}",
            )
            if sidecar.version != 1 or sidecar.take_id != take.id:
                raise PublicBundleError(f"timestamp sidecar identity is invalid for {take.id!r}")
            _validate_timestamp_sidecar(take, sidecar)
        if expected_source_start != typed_audio.duration_ms:
            raise PublicBundleError("narration audio takes do not cover source time")
        if playback_end_ms is not None:
            final_interval_end = audio["intervals"][-1]["presentation_end_ms"]
            if playback_end_ms != final_interval_end:
                raise PublicBundleError(
                    "narration audio playback does not cover presentation time"
                )

    unreferenced = files - referenced
    if unreferenced:
        names = ", ".join(sorted(path.relative_to(root).as_posix() for path in unreferenced))
        raise PublicBundleError(f"public staging contains unreferenced files: {names}")
    return manifest


def publish_public_bundle(
    source: Path,
    destination: Path,
    *,
    secrets: Iterable[str] = (),
    private_paths: Iterable[Path | str] = (),
    ffprobe: str | None = None,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
) -> Path:
    """Validate, stage, and replace a public bundle with rollback on failure."""

    source = source.absolute()
    destination = destination.absolute()
    if source == destination or destination.is_symlink():
        raise PublicBundleError("public destination must be a distinct real path")
    validate_public_staging(
        source,
        secrets=secrets,
        private_paths=private_paths,
        ffprobe=ffprobe,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
    )
    backup: Path | None = None
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True, symlinks=True)
        validate_public_staging(
            temporary,
            secrets=secrets,
            private_paths=private_paths,
            ffprobe=ffprobe,
        )
        if destination.exists():
            backup = Path(
                tempfile.mkdtemp(prefix=f".{destination.name}.backup-", dir=destination.parent)
            )
            backup.rmdir()
            replace(destination, backup)
        try:
            replace(temporary, destination)
        except BaseException as replace_error:
            if backup is not None and backup.exists() and not destination.exists():
                try:
                    replace(backup, destination)
                except BaseException as rollback_error:
                    raise PublicBundleError(
                        "could not atomically replace public bundle; rollback also "
                        f"failed and the previous bundle remains at {backup}: "
                        f"{rollback_error}"
                    ) from replace_error
                else:
                    backup = None
            elif backup is not None and backup.exists():
                raise PublicBundleError(
                    "could not atomically replace public bundle; destination state "
                    f"is uncertain and the previous bundle remains at {backup}"
                ) from replace_error
            raise replace_error
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
        return destination
    except PublicBundleError:
        raise
    except BaseException as exc:
        raise PublicBundleError(f"could not atomically replace public bundle: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _walk_allowlisted_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    for directory, names, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            path = directory_path / name
            if path.is_symlink():
                raise PublicBundleError(f"public staging contains a symlink: {path.name}")
        for name in filenames:
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                raise PublicBundleError(f"public staging contains an unsafe file: {path.name}")
            relative = path.relative_to(root).as_posix()
            if not _allowlisted_path(relative):
                raise PublicBundleError(f"public staging path is not allowlisted: {relative}")
            files.add(path)
    return files


def _allowlisted_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.parts in {
        ("recording.presentation.json",),
        ("recording.recording.json",),
        ("audio.json",),
    }:
        return True
    if len(path.parts) != 2:
        return False
    directory, name = path.parts
    if directory == "timestamps":
        return name.endswith(".json")
    if directory == "beats":
        return name.endswith(".cast") or name.endswith(".browser.json")
    if directory == "media":
        return name.endswith(".webp") or name.endswith(".mp4")
    if directory == "audio":
        return path.suffix.lower() in PUBLIC_AUDIO_SUFFIXES
    return False


def _public_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublicBundleError(f"public text is invalid UTF-8: {path.name}") from exc


def _scan_public_text(
    text: str,
    *,
    path: Path,
    secrets: tuple[str, ...],
    private_paths: tuple[str, ...],
) -> None:
    if any(secret in text for secret in secrets):
        raise PublicBundleError(f"public text contains a registered secret: {path.name}")
    if any(value and value in text for value in private_paths):
        raise PublicBundleError(f"public text contains a private path: {path.name}")
    if GENERIC_PRIVATE_PATH_RE.search(text):
        raise PublicBundleError(f"public text appears to contain an absolute path: {path.name}")


def _validate_recording_metadata(
    value: dict[str, Any],
) -> PublishedRecordingMetadataV1:
    metadata = _structured(
        value,
        PublishedRecordingMetadataV1,
        required={
            "version",
            "recording",
            "capture_fingerprint",
            "presentation_fingerprint",
            "dependencies",
            "versions",
            "warnings",
        },
        field="published recording metadata",
    )
    if metadata.version != 1 or not metadata.recording:
        raise PublicBundleError("published recording metadata identity is invalid")
    for digest in (metadata.capture_fingerprint, metadata.presentation_fingerprint):
        if not SHA256_RE.fullmatch(digest):
            raise PublicBundleError("published recording metadata hash is invalid")
    for dependency in metadata.dependencies:
        _public_relative_path(dependency.path, field="published source dependency path")
        if not SHA256_RE.fullmatch(dependency.sha256):
            raise PublicBundleError("published source dependency hash is invalid")
    if any(not key or not value for key, value in metadata.versions.items()):
        raise PublicBundleError("published recording versions are invalid")
    if any(not WARNING_RE.fullmatch(value) for value in metadata.warnings):
        raise PublicBundleError("published recording warning code is invalid")
    return metadata


def _public_relative_path(value: object, *, field: str) -> str:
    try:
        return validate_relative_presentation_path(value, field=field)
    except PresentationValidationError as exc:
        raise PublicBundleError(str(exc)) from exc


def _validate_timestamp_sidecar(take: Any, sidecar: Any) -> None:
    duration_ms = take.source_end_ms - take.source_start_ms
    if sidecar.duration_ms != duration_ms or len(sidecar.members) != len(take.members):
        raise PublicBundleError(f"timestamp sidecar boundaries are invalid for {take.id!r}")
    previous_source_end = 0
    member_ranges: dict[str, tuple[int, int, int, int, str]] = {}
    for expected, actual in zip(take.members, sidecar.members, strict=True):
        if (
            actual.beat_id != expected.beat_id
            or actual.text_start != expected.text_start
            or actual.text_end != expected.text_end
            or actual.source_start_ms < previous_source_end
            or actual.source_end_ms < actual.source_start_ms
            or actual.source_end_ms > duration_ms
        ):
            raise PublicBundleError(f"timestamp sidecar members are invalid for {take.id!r}")
        member_ranges[actual.beat_id] = (
            actual.text_start,
            actual.text_end,
            actual.source_start_ms,
            actual.source_end_ms,
            expected.text,
        )
        previous_source_end = actual.source_end_ms
    if sidecar.members and (
        sidecar.members[0].source_start_ms != 0
        or sidecar.members[-1].source_end_ms != duration_ms
    ):
        raise PublicBundleError(f"timestamp sidecar members do not cover take {take.id!r}")

    previous_text_end = 0
    previous_time_end = 0
    for word in sidecar.words:
        trace = (
            word.timing_source,
            word.timing_confidence,
            word.raw_word_start,
            word.raw_word_end,
        )
        trace_is_legacy = trace == ("", "", None, None)
        trace_is_transcription = (
            word.timing_source == "transcription"
            and word.timing_confidence == "high"
            and isinstance(word.raw_word_start, int)
            and isinstance(word.raw_word_end, int)
            and word.raw_word_start >= 0
            and word.raw_word_end > word.raw_word_start
        )
        trace_is_interpolated = trace == ("interpolated", "low", None, None)
        owner = next(
            (
                bounds
                for bounds in member_ranges.values()
                if bounds[0] <= word.text_start < word.text_end <= bounds[1]
            ),
            None,
        )
        if (
            owner is None
            or not (
                trace_is_legacy
                or trace_is_transcription
                or trace_is_interpolated
            )
            or word.text_start < previous_text_end
            or word.start_ms < previous_time_end
            or word.end_ms <= word.start_ms
            or word.end_ms > duration_ms
            or owner[4][word.text_start - owner[0] : word.text_end - owner[0]]
            != word.text
        ):
            raise PublicBundleError(f"timestamp sidecar words are invalid for {take.id!r}")
        previous_text_end = word.text_end
        previous_time_end = word.end_ms

    for field, values in (("anchor", sidecar.anchors), ("wait", sidecar.waits)):
        previous_source_ms = 0
        for marker in values:
            bounds = member_ranges.get(marker.beat_id)
            if (
                bounds is None
                or not bounds[0] <= marker.text_offset <= bounds[1]
                or marker.source_ms < previous_source_ms
                or marker.source_ms > duration_ms
                or (field == "anchor" and not marker.id)
                or (field == "wait" and (not marker.target or marker.gap_ms < 0))
            ):
                raise PublicBundleError(
                    f"timestamp sidecar {field}s are invalid for {take.id!r}"
                )
            previous_source_ms = marker.source_ms


def _structured(
    value: dict[str, Any],
    schema: type[T],
    *,
    required: set[str],
    field: str,
) -> T:
    if set(value) != required:
        raise PublicBundleError(f"{field} fields are invalid")
    try:
        result = OmegaConf.to_object(OmegaConf.merge(OmegaConf.structured(schema), value))
    except (OmegaConfBaseException, TypeError, ValueError) as exc:
        raise PublicBundleError(f"{field} does not match its schema") from exc
    if not isinstance(result, schema):
        raise PublicBundleError(f"{field} does not match its schema")
    return result


def _probe_public_media(path: Path, *, ffprobe: str | None) -> dict[str, Any]:
    executable = ffprobe or shutil.which("ffprobe")
    if executable is None:
        raise PublicBundleError("ffprobe is required to validate browser media")
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type,pix_fmt,width,height:format=duration,format_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if process.returncode != 0:
        raise PublicBundleError(f"could not inspect public media: {path.name}")
    try:
        payload = json.loads(process.stdout)
        streams = payload["streams"]
        visual = next(stream for stream in streams if stream.get("codec_type") == "video")
        raw_duration = payload.get("format", {}).get("duration")
        duration_ms = round(float(raw_duration) * 1000) if raw_duration is not None else 0
        return {
            "codec": visual["codec_name"],
            "format_name": str(payload.get("format", {}).get("format_name", "")),
            "pixel_format": visual["pix_fmt"],
            "width": int(visual["width"]),
            "height": int(visual["height"]),
            "duration_ms": duration_ms,
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        }
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PublicBundleError(f"public media probe is invalid: {path.name}") from exc
