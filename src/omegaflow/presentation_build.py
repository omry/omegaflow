"""Build orchestration for seekable terminal, browser, and mixed recordings.

One private, persistent capture run is compiled into a public presentation
bundle while private capture logs and diagnostics stay out of that bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from . import audio, record
from .browser_capture import PersistentBrowserRunner
from .browser_runtime import (
    CHROMIUM_BROWSER_VERSION,
    CHROMIUM_REVISION,
    PLAYWRIGHT_PACKAGE_VERSION,
    BrowserMediaRuntime,
    BrowserRuntimeError,
    require_browser_media_runtime,
)
from .capture import (
    CaptureCoordinator,
    CaptureFailed,
    CaptureProgressCallback,
    CaptureResult,
)
from .presentation import serialize_presentation_manifest, validate_presentation_manifest
from .presentation_compiler import (
    ArtifactFingerprints,
    BrowserCaptureLog,
    CompiledBrowserBeat,
    CompiledRecordingTiming,
    TerminalTextHighlightEvent,
    compile_artifact_fingerprints,
    compile_browser_beat,
    compile_recording_timing,
    load_browser_capture_log,
    materialize_terminal_beat,
)
from .presentation_schema import (
    PresentationAssetV1,
    PresentationAudioIntervalV1,
    PresentationAudioV1,
    PresentationBeatPlayerV1,
    PresentationBeatV1,
    PresentationBrowserHeaderV1,
    PresentationChromeV1,
    PresentationHeaderV1,
    PresentationManifestV1,
    PresentationPlayerToolbarHighlightV1,
    PresentationRecordingV1,
    PresentationRendererV1,
    PresentationWindowV1,
    PlayerToolbarControl,
)
from .publish import publish_public_bundle, validate_public_staging
from .recording_plan import (
    BeatPlan,
    BrowserActionPlan,
    FrozenMapping,
    RecordingPlan,
    TerminalActionPlan,
    terminal_action_id,
)
from .studio_config import RecordingMedium, project_root
from .terminal_capture import PersistentTerminalRunner
from .tool_progress import format_activity_elapsed


CAPTURE_POLICY_VERSIONS = {
    "coordinator": "capture-v1",
    "terminal": "persistent-terminal-v4",
    "browser": "playwright-capture-v7-visual-state-aligned",
    "stability": "stable-v1",
    "redaction": "capture-mask-v1",
}
PRESENTATION_POLICY_VERSIONS = {
    "compiler": "presentation-v5-materialized-audio-waits",
    "terminal_renderer": "payload-v1",
    "browser_renderer": "payload-v1",
    "pointer": "pointer-v1",
    "typing": "natural-v1",
    "clip": "playwright-video-v2-h264",
}
FINGERPRINT_FILE = "recording.fingerprint.json"
PRESENTATION_DIRECTORY = "presentation"
MANIFEST_FILE = "recording.presentation.json"
RECORDING_METADATA_FILE = "recording.recording.json"


class PresentationBuildError(RuntimeError):
    """Raised when capture or presentation materialization cannot complete."""


def project_root_from_spec(spec: Mapping[str, Any]) -> Path:
    value = spec.get("_project_root")
    if isinstance(value, str) and value:
        return Path(value).expanduser().resolve()
    return project_root()


@dataclass(frozen=True)
class PresentationAudioArtifacts:
    metadata: Path
    timestamps: Mapping[str, Path]
    take_audio: Mapping[str, Path]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PresentationBuildResult:
    run_dir: Path
    bundle_dir: Path
    manifest: Path
    fingerprints: ArtifactFingerprints
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _AlignedSourceWord:
    start_ms: int
    end_ms: int
    timing_source: str
    timing_confidence: str
    raw_word_start: int | None
    raw_word_end: int | None


def _materialize_waited_audio(
    source: Path,
    output: Path,
    *,
    source_start_ms: int,
    playback_start_ms: int,
    intervals: tuple[PresentationAudioIntervalV1, ...],
    ffmpeg: str,
) -> Path:
    """Encode presentation gaps as silence without dropping source samples."""

    if not intervals:
        raise PresentationBuildError("presentation audio take has no intervals")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise PresentationBuildError("ffprobe is required to prepare narration waits")
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,channel_layout,bit_rate",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip()
        raise PresentationBuildError(
            f"ffprobe could not inspect narration audio: {detail or probe.returncode}"
        )
    try:
        stream = json.loads(probe.stdout)["streams"][0]
        sample_rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
        channel_layout = str(stream.get("channel_layout") or "")
        bit_rate = int(stream.get("bit_rate") or 0)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PresentationBuildError(
            "ffprobe returned invalid narration audio metadata"
        ) from exc
    if not channel_layout:
        channel_layout = {1: "mono", 2: "stereo"}.get(channels, "")
    if sample_rate <= 0 or not channel_layout:
        raise PresentationBuildError("narration audio layout is unsupported")

    filters: list[str] = []
    labels: list[str] = []
    previous_presentation_end = playback_start_ms
    previous_source_end = source_start_ms
    for index, interval in enumerate(intervals):
        if (
            interval.presentation_start_ms < previous_presentation_end
            or interval.presentation_end_ms <= interval.presentation_start_ms
            or interval.source_start_ms != previous_source_end
            or interval.source_end_ms <= interval.source_start_ms
        ):
            raise PresentationBuildError("presentation audio intervals are invalid")
        silence_ms = interval.presentation_start_ms - previous_presentation_end
        if silence_ms:
            label = f"silence{index}"
            filters.append(
                f"anullsrc=r={sample_rate}:cl={channel_layout}:"
                f"d={silence_ms / 1000:.6f}[{label}]"
            )
            labels.append(label)
        local_start_ms = interval.source_start_ms - source_start_ms
        local_end_ms = interval.source_end_ms - source_start_ms
        label = f"audio{index}"
        filters.append(
            f"[0:a]atrim=start={local_start_ms / 1000:.6f}:"
            f"end={local_end_ms / 1000:.6f},asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(label)
        previous_presentation_end = interval.presentation_end_ms
        previous_source_end = interval.source_end_ms
    filters.append(
        "".join(f"[{label}]" for label in labels)
        + f"concat=n={len(labels)}:v=0:a=1[out]"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[out]",
    ]
    if output.suffix.lower() == ".mp3":
        command.extend(["-b:a", str(bit_rate or 128_000)])
    command.append(str(output))
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PresentationBuildError(
            f"ffmpeg could not prepare narration waits: {detail or result.returncode}"
        )
    return output


def _stage_presentation_audio(
    artifacts: PresentationAudioArtifacts,
    timing: CompiledRecordingTiming,
    staging: Path,
) -> Path:
    metadata = json.loads(artifacts.metadata.read_text(encoding="utf-8"))
    takes = metadata.get("takes")
    if not isinstance(takes, list) or not takes:
        raise PresentationBuildError("narration audio metadata has no takes")
    ffmpeg = shutil.which("ffmpeg")
    previous_playback_end = 0
    for value in takes:
        if not isinstance(value, dict):
            raise PresentationBuildError("narration audio take metadata is invalid")
        take_id = value.get("id")
        if not isinstance(take_id, str) or take_id not in artifacts.take_audio:
            raise PresentationBuildError("narration audio take is missing")
        source_start = value.get("source_start_ms")
        source_end = value.get("source_end_ms")
        source_relative = value.get("src")
        if (
            isinstance(source_start, bool)
            or not isinstance(source_start, int)
            or isinstance(source_end, bool)
            or not isinstance(source_end, int)
            or not isinstance(source_relative, str)
        ):
            raise PresentationBuildError("narration audio take boundaries are invalid")
        intervals = tuple(
            interval
            for interval in timing.audio_intervals
            if source_start <= interval.source_start_ms < interval.source_end_ms <= source_end
        )
        if (
            not intervals
            or intervals[0].source_start_ms != source_start
            or intervals[-1].source_end_ms != source_end
        ):
            raise PresentationBuildError("narration audio intervals do not cover take")

        source = artifacts.take_audio[take_id]
        source_target = staging / source_relative
        source_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, source_target)
        playback_start = previous_playback_end
        playback_end = intervals[-1].presentation_end_ms
        presentation_cursor = playback_start
        has_wait = False
        for interval in intervals:
            has_wait = has_wait or interval.presentation_start_ms > presentation_cursor
            presentation_cursor = interval.presentation_end_ms

        if has_wait:
            if ffmpeg is None:
                raise PresentationBuildError(
                    "ffmpeg is required to prepare authored narration waits"
                )
            safe_id = audio.narration_take_filename_id(take_id)
            temporary = staging / "audio" / f".{safe_id}-playback.mp3"
            _materialize_waited_audio(
                source,
                temporary,
                source_start_ms=source_start,
                playback_start_ms=playback_start,
                intervals=intervals,
                ffmpeg=ffmpeg,
            )
            playback_content = temporary.read_bytes()
            playback_sha256 = hashlib.sha256(playback_content).hexdigest()
            playback_relative = f"audio/{safe_id}-playback-{playback_sha256}.mp3"
            playback_target = staging / playback_relative
            temporary.replace(playback_target)
        else:
            playback_relative = source_relative
            playback_sha256 = str(value.get("sha256") or "")
        value.update(
            {
                "playback_src": playback_relative,
                "playback_sha256": playback_sha256,
                "playback_start_ms": playback_start,
                "playback_end_ms": playback_end,
            }
        )
        previous_playback_end = playback_end

    metadata_target = staging / "audio.json"
    metadata_target.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata_target


def thaw(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def run_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "capture": run_dir / "capture",
        "browser_capture": run_dir / "capture" / "browser.capture.jsonl",
        "terminal_cast": run_dir / "capture" / "terminal.cast",
        "terminal_timeline": run_dir / "capture" / "terminal.timeline.jsonl",
        "terminal_beats": run_dir / "capture" / "terminal-beats",
        "audio": run_dir / "audio",
        "presentation": run_dir / PRESENTATION_DIRECTORY,
        "manifest": run_dir / PRESENTATION_DIRECTORY / MANIFEST_FILE,
        "fingerprint": run_dir / FINGERPRINT_FILE,
        "report": run_dir / "compilation-report.json",
    }


def public_bundle_dir(spec: Mapping[str, Any]) -> Path:
    outputs = spec.get("outputs", {})
    if not isinstance(outputs, Mapping):
        raise PresentationBuildError("outputs must be a mapping")
    asset_dir = outputs.get("asset_dir")
    if not isinstance(asset_dir, str) or not asset_dir:
        raise PresentationBuildError("outputs.asset_dir must be a non-empty string")
    return record.relative_path(asset_dir) / PRESENTATION_DIRECTORY


def public_manifest_path(spec: Mapping[str, Any]) -> Path:
    return public_bundle_dir(spec) / MANIFEST_FILE


def _capture_environment(
    spec: Mapping[str, Any],
) -> tuple[Path, dict[str, str | None]]:
    environment = spec.get("environment", {})
    if not isinstance(environment, Mapping):
        raise PresentationBuildError("environment must be a mapping")
    working_directory = environment.get("working_directory", ".")
    if not isinstance(working_directory, str):
        raise PresentationBuildError("environment.working_directory must be a string")
    workdir = record.relative_path(working_directory)
    path_prepend = environment.get("path_prepend", [])
    if not isinstance(path_prepend, list) or any(
        not isinstance(item, str) for item in path_prepend
    ):
        raise PresentationBuildError("environment.path_prepend must be a list of strings")
    path_entries = [str(record.relative_path(item)) for item in path_prepend]
    path_entries.append(str(Path(sys.executable).parent))
    variables = environment.get("variables", {})
    if not isinstance(variables, Mapping):
        raise PresentationBuildError("environment.variables must be a mapping")
    resolved: dict[str, str | None] = {
        str(key): str(value) for key, value in variables.items()
    }
    resolved["PATH"] = os.pathsep.join(
        [*path_entries, os.environ.get("PATH", "")]
    )
    style = spec.get("style", {})
    color = style.get("color", True) if isinstance(style, Mapping) else True
    if color:
        resolved.update(
            {
                "CLICOLOR_FORCE": "1",
                "FORCE_COLOR": "1",
                "PY_COLORS": "1",
                "TERM": "xterm-256color",
                "NO_COLOR": None,
            }
        )
    else:
        resolved.update(
            {
                "CLICOLOR_FORCE": None,
                "FORCE_COLOR": None,
                "PY_COLORS": None,
                "NO_COLOR": "1",
            }
        )
    return workdir, resolved


def _terminal_capture_options(spec: Mapping[str, Any]) -> dict[str, Any]:
    style = spec.get("style", {})
    if not isinstance(style, Mapping):
        raise PresentationBuildError("style must be a mapping")
    timing = spec.get("timing", {})
    if not isinstance(timing, Mapping):
        raise PresentationBuildError("timing must be a mapping")

    typing = style.get("typing", True)
    if not isinstance(typing, bool):
        raise PresentationBuildError("style.typing must be a boolean")

    def delay(mapping: Mapping[str, Any], key: str, default: float) -> float:
        value = mapping.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
        ):
            raise PresentationBuildError(f"{key} must be non-negative")
        return float(value)

    minimum = delay(style, "typing_min_delay", 0.012)
    maximum = delay(style, "typing_max_delay", 0.045)
    if minimum > maximum:
        raise PresentationBuildError(
            "style.typing_min_delay must be less than or equal to "
            "style.typing_max_delay"
        )
    seed = style.get("typing_seed", 17)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise PresentationBuildError("style.typing_seed must be an integer")
    return {
        "typing": typing,
        "typing_min_delay": minimum,
        "typing_max_delay": maximum,
        "typing_space_delay": delay(style, "typing_space_delay", 0.025),
        "typing_punctuation_delay": delay(
            style, "typing_punctuation_delay", 0.05
        ),
        "typing_newline_delay": delay(style, "typing_newline_delay", 0.16),
        "typing_seed": seed,
        "post_enter_pause": delay(timing, "post_enter_pause", 0.35),
        "post_command_pause": delay(timing, "post_command_pause", 0.85),
    }


def capture_recording(
    spec: Mapping[str, Any],
    plan: RecordingPlan,
    run_dir: Path,
    *,
    headed: bool = False,
    on_progress: CaptureProgressCallback | None = None,
) -> CaptureResult:
    """Capture every beat in one shared environment with failure diagnostics."""

    capture_config = spec.get("capture", {})
    if not isinstance(capture_config, Mapping):
        raise PresentationBuildError("capture must be a mapping")
    window_size = capture_config.get("window_size", "100x28")
    idle_time_limit = capture_config.get("idle_time_limit")
    headless = capture_config.get("headless", True)
    if not isinstance(window_size, str) or not window_size:
        raise PresentationBuildError("capture.window_size must be a non-empty string")
    if not isinstance(headless, bool):
        raise PresentationBuildError("capture.headless must be a boolean")
    if not isinstance(headed, bool):
        raise PresentationBuildError("headed must be a boolean")
    if idle_time_limit is not None and (
        isinstance(idle_time_limit, bool)
        or not isinstance(idle_time_limit, (int, float))
        or idle_time_limit <= 0
    ):
        raise PresentationBuildError("capture.idle_time_limit must be positive")
    working_directory, environment = _capture_environment(spec)
    terminal_options = _terminal_capture_options(spec)
    run_dir.mkdir(parents=True, exist_ok=True)
    configured_venv = environment.get("VIRTUAL_ENV", os.environ.get("VIRTUAL_ENV", ""))
    venv = configured_venv if isinstance(configured_venv, str) else ""
    try:
        record.write_postmortem_entrypoint(
            run_dir / "enter",
            run_dir=str(run_dir.absolute()),
            workdir=str(working_directory),
            venv=venv,
        )
    except OSError as exc:
        raise PresentationBuildError(
            f"could not create capture postmortem entrypoint: {exc}"
        ) from exc
    title = plan.title or plan.id
    effective_headless = headless and not headed
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            title=title,
            window_size=window_size,
            idle_time_limit=idle_time_limit,
            headless=effective_headless,
            color=environment.get("NO_COLOR") is None,
            **terminal_options,
        ),
        browser_runner_factory=(
            None
            if plan.browser is None
            else lambda: PersistentBrowserRunner(
                plan.browser, headless=effective_headless
            )
        ),
    )
    try:
        result = coordinator.capture(
            plan,
            run_dir,
            workspace=project_root_from_spec(spec),
            working_directory=working_directory,
            environment=environment,
            on_progress=on_progress,
        )
    except Exception as exc:
        _preserve_capture_diagnostics(spec, run_dir, exc)
        raise
    _copy_capture_logs(run_dir)
    return result


def _copy_capture_logs(run_dir: Path) -> tuple[Path, Path, Path]:
    capture_dir = run_dir / "capture"
    outputs = (
        (capture_dir / "terminal.stdout.log", run_dir / "stdout"),
        (capture_dir / "terminal.stderr.log", run_dir / "stderr"),
    )
    for source, destination in outputs:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, destination)
        elif not destination.exists():
            destination.write_text("", encoding="utf-8")
    progress = run_dir / "progress"
    if not progress.exists():
        progress.write_text("", encoding="utf-8")
    return run_dir / "stdout", run_dir / "stderr", progress


def _read_capped_failure_output(path: Path, *, max_chars: int = 12_000) -> tuple[str, bool]:
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"<unable to read captured output: {exc}>", False
    truncated = len(value) > max_chars
    return (value[-max_chars:] if truncated else value), truncated


def _preserve_capture_diagnostics(
    spec: Mapping[str, Any], run_dir: Path, error: BaseException
) -> None:
    """Write best-effort failure artifacts without masking the primary error."""

    try:
        stdout_path, stderr_path, progress_path = _copy_capture_logs(run_dir)
        capture_dir = run_dir / "capture"
        for source, destination in (
            (capture_dir / "terminal.cast", run_dir / "failed.cast"),
            (
                capture_dir / "terminal.timeline.jsonl",
                run_dir / "failed.timeline.jsonl",
            ),
        ):
            if source.is_file():
                shutil.copy2(source, destination)
        output_path = (
            stderr_path
            if stderr_path.is_file() and stderr_path.stat().st_size
            else stdout_path
        )
        output, output_truncated = _read_capped_failure_output(output_path)
        stderr, stderr_truncated = _read_capped_failure_output(stderr_path)
        progress, progress_truncated = _read_capped_failure_output(progress_path)
        operation = "capture"
        if isinstance(error, CaptureFailed) and error.primary is not None:
            operation = error.primary.operation
        message = str(error)
        stderr_lines = stderr.rstrip().splitlines()
        if stderr_lines and stderr_lines[-1] not in message:
            message = f"{message}: {stderr_lines[-1]}"
        failure_summary: dict[str, Any] = {}
        try:
            failure_summary = record.failure_summary_config(dict(spec))
        except (TypeError, ValueError, record.RecordingError):
            pass
        report = {
            "kind": "capture",
            "id": operation,
            "name": operation,
            "message": message,
            "output": output,
            "output_path": str(output_path),
            "output_truncated": output_truncated,
            "stderr": stderr,
            "stderr_path": str(stderr_path),
            "stderr_truncated": stderr_truncated,
            "progress": progress,
            "progress_path": str(progress_path),
            "progress_truncated": progress_truncated,
            "run_dir": str(run_dir),
            "postmortem_path": str(run_dir / "enter"),
            "recording_id": str(spec.get("id", "")),
            "run_id": run_dir.name,
            "failure_summary": failure_summary,
        }
        (run_dir / "failure.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        return


def capture_artifacts_exist(plan: RecordingPlan, run_dir: Path) -> bool:
    paths = run_paths(run_dir)
    terminal_ids = [
        beat.id for beat in plan.beats if beat.medium is RecordingMedium.terminal
    ]
    if any(
        not (paths["terminal_beats"] / f"{beat_id}.cast").is_file()
        or not (paths["terminal_beats"] / f"{beat_id}.actions.json").is_file()
        for beat_id in terminal_ids
    ):
        return False
    if any(beat.medium is RecordingMedium.browser for beat in plan.beats):
        try:
            load_browser_capture_log(paths["browser_capture"])
        except Exception:
            return False
    return True


def read_fingerprint(run_dir: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(run_paths(run_dir)["fingerprint"].read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise PresentationBuildError("recording fingerprint is invalid") from exc
    if not isinstance(value, dict):
        raise PresentationBuildError("recording fingerprint must be a mapping")
    return value


def _source_dependencies(spec: Mapping[str, Any]) -> dict[str, str]:
    dependencies: dict[str, str] = {}
    for path in _dependency_paths(spec):
        if not path.is_file():
            raise PresentationBuildError(f"recording dependency is missing: {path}")
        dependencies[_display_path(path, spec)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dependencies


def _dependency_paths(spec: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    manifest = spec.get("_manifest_path")
    if isinstance(manifest, str) and manifest:
        paths.append(record.relative_path(manifest))

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "run_file" and isinstance(item, str) and item:
                    paths.append(record.run_file_path(item, dict(spec)))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(spec)
    return sorted(set(paths), key=lambda item: str(item))


def _display_path(path: Path, spec: Mapping[str, Any]) -> str:
    try:
        return path.resolve().relative_to(project_root_from_spec(spec).resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _auth_state_sha256(spec: Mapping[str, Any]) -> str | None:
    browser = spec.get("browser")
    if not isinstance(browser, Mapping):
        return None
    auth = browser.get("auth", {})
    if not isinstance(auth, Mapping):
        return None
    configured = auth.get("storage_state_path")
    env_name = auth.get("storage_state_env")
    if env_name:
        configured = os.environ.get(str(env_name))
    if configured is None:
        return None
    if not isinstance(configured, str) or not configured:
        raise PresentationBuildError("browser auth storage-state path is invalid")
    workdir, _ = _capture_environment(spec)
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = workdir / path
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PresentationBuildError("could not read browser storage state") from exc


def artifact_fingerprints(
    spec: Mapping[str, Any],
    plan: RecordingPlan,
    *,
    visual_asset_hashes: Iterable[str] = (),
    narration_take_hashes: Mapping[str, str] | None = None,
    timestamp_hashes: Mapping[str, str] | None = None,
) -> ArtifactFingerprints:
    capture = spec.get("capture", {})
    environment = spec.get("environment", {})
    _, resolved_environment = _capture_environment(spec)
    return compile_artifact_fingerprints(
        plan,
        capture_environment={
            "capture": capture,
            "environment": environment,
            "terminal": {
                "color": resolved_environment.get("NO_COLOR") is None,
                **_terminal_capture_options(spec),
            },
            "playwright": PLAYWRIGHT_PACKAGE_VERSION,
            "chromium_revision": CHROMIUM_REVISION,
            "chromium_version": CHROMIUM_BROWSER_VERSION,
        },
        source_dependencies=_source_dependencies(spec),
        capture_policy_versions=CAPTURE_POLICY_VERSIONS,
        visual_asset_hashes=visual_asset_hashes,
        narration_take_hashes=narration_take_hashes,
        timestamp_hashes=timestamp_hashes,
        presentation_policy_versions=PRESENTATION_POLICY_VERSIONS,
        auth_state_sha256=_auth_state_sha256(spec),
    )


def capture_is_fresh(spec: Mapping[str, Any], plan: RecordingPlan, run_dir: Path) -> bool:
    stored = read_fingerprint(run_dir)
    if stored is None or not capture_artifacts_exist(plan, run_dir):
        return False
    current = artifact_fingerprints(spec, plan)
    return (
        stored.get("version") == 1
        and stored.get("capture_fingerprint") == current.capture_fingerprint
    )


def write_capture_fingerprint(
    spec: Mapping[str, Any], plan: RecordingPlan, run_dir: Path
) -> Path:
    fingerprints = artifact_fingerprints(spec, plan)
    path = run_paths(run_dir)["fingerprint"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                **fingerprints.payload(),
                "recording": plan.id,
                "dependencies": [
                    {"path": key, "sha256": value}
                    for key, value in _source_dependencies(spec).items()
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def prepare_narration_audio(
    spec: Mapping[str, Any],
    plan: RecordingPlan,
    run_dir: Path,
    *,
    force: bool = False,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> PresentationAudioArtifacts | None:
    """Generate cached take audio and run-local v3 per-take metadata."""

    settings = audio.audio_settings(dict(spec))
    if not settings.enabled or not plan.narration_takes:
        return None
    transcription = audio.transcription_settings(dict(spec))
    take_items = audio.plan_narration_take_audio(plan.id, plan.narration_takes, settings)
    audio_items: list[audio.AudioPlanItem] = []
    warnings: list[str] = []
    headings = {beat.id: beat.heading for beat in plan.beats}
    for item in take_items:
        warning = audio.narration_take_review_warning(
            item, audio.load_narration_take_index(item.index_path)
        )
        if warning is not None:
            warnings.append(str(warning["code"]))
        first_beat = item.take.members[0].beat_id
        audio_items.append(
            audio.AudioPlanItem(
                segment=audio.NarrationSegment(
                    segment_id=item.take.id,
                    heading=headings.get(first_beat, ""),
                    text=item.take.synthesis_text,
                ),
                cache_key=item.cache_key,
                output_path=item.output_path,
            )
        )
    total_steps = 3 * len(audio_items)
    current_step = 0

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message, current_step, total_steps)

    def complete(message: str) -> None:
        nonlocal current_step
        current_step += 1
        report(message)

    def streamed_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        raise AssertionError("unreachable")

    for take_item, audio_item in zip(take_items, audio_items, strict=True):
        label = headings.get(take_item.take.members[0].beat_id) or take_item.take.id
        message = f"Generate narration: {label}"
        report(message)

        def report_audio_activity(received: int, elapsed: float) -> None:
            if on_progress is not None:
                details = [message]
                elapsed_text = format_activity_elapsed(elapsed)
                if elapsed_text is not None:
                    details.append(elapsed_text)
                details.append(f"{streamed_size(received)} received")
                on_progress(
                    " · ".join(details),
                    current_step,
                    total_steps,
                )

        audio.generate_audio(
            [audio_item],
            settings,
            force=force,
            on_activity=report_audio_activity if on_progress is not None else None,
        )
        complete(message)
    for take_item, audio_item in zip(take_items, audio_items, strict=True):
        label = headings.get(take_item.take.members[0].beat_id) or take_item.take.id
        message = f"Time narration: {label}"
        report(message)
        audio.generate_timestamps(
            plan.id,
            [audio_item],
            settings,
            transcription,
            force=force,
        )
        complete(message)
    output_dir = run_paths(run_dir)["audio"]
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamps_dir = output_dir / "timestamps"
    timestamps_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    take_durations: dict[str, int] = {}
    take_audio_paths: dict[str, str] = {}
    take_audio_sha256: dict[str, str] = {}
    timestamp_paths: dict[str, str] = {}
    timestamp_files: dict[str, Path] = {}
    for take_item, audio_item in zip(take_items, audio_items, strict=True):
        label = headings.get(take_item.take.members[0].beat_id) or take_item.take.id
        message = f"Prepare narration: {label}"
        report(message)
        content_sha256 = hashlib.sha256(audio_item.output_path.read_bytes()).hexdigest()
        safe_id = audio.narration_take_filename_id(take_item.take.id)
        take_audio_paths[take_item.take.id] = (
            f"audio/{safe_id}-{content_sha256}.{settings.format}"
        )
        take_audio_sha256[take_item.take.id] = content_sha256
        duration_ms = round(audio.audio_duration_seconds(audio_item.output_path) * 1000)
        raw = json.loads(
            audio.timeline_path_for(audio_item).read_text(encoding="utf-8")
        )
        words = _source_words_with_timing(
            take_item.take.synthesis_text,
            raw.get("words", []) if isinstance(raw, dict) else [],
            duration_ms=duration_ms,
        )
        if any(word["timing_confidence"] == "low" for word in words):
            warnings.append("NARRATION_TIMING_LOW_CONFIDENCE")
        payload = audio.narration_timestamp_sidecar_payload(
            take_item.take,
            duration_ms=duration_ms,
            words=words,
        )
        filename = audio.narration_take_filename_id(take_item.take.id) + ".json"
        timestamp_path = timestamps_dir / filename
        timestamp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        take_durations[take_item.take.id] = duration_ms
        timestamp_paths[take_item.take.id] = f"timestamps/{filename}"
        timestamp_files[take_item.take.id] = timestamp_path
        audio.write_narration_take_index(take_item)
        complete(message)
    metadata_path = output_dir / "audio.json"
    metadata = audio.narration_audio_metadata_v3_payload(
        plan,
        take_audio_paths=take_audio_paths,
        take_audio_sha256=take_audio_sha256,
        take_durations_ms=take_durations,
        timestamp_paths=timestamp_paths,
    )
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return PresentationAudioArtifacts(
        metadata=metadata_path,
        timestamps=timestamp_files,
        take_audio={
            item.take.id: audio_item.output_path
            for item, audio_item in zip(take_items, audio_items, strict=True)
        },
        warnings=tuple(sorted(set(warnings))),
    )


def _source_words_with_timing(
    text: str,
    raw_words: object,
    *,
    duration_ms: int,
) -> list[dict[str, Any]]:
    source = list(re.finditer(r"\S+", text))
    if not source:
        raise PresentationBuildError("narration take has no words")
    raw = raw_words if isinstance(raw_words, list) else []
    raw_is_timed = bool(raw) and all(
        isinstance(item, Mapping)
        and isinstance(item.get("word"), str)
        and all(
            not isinstance(value, bool) and isinstance(value, (int, float))
            for value in (item.get("start"), item.get("end"))
        )
        for item in raw
    )
    source_normalized = [_normalized_spoken_word(match.group(0)) for match in source]
    raw_normalized = (
        [_normalized_spoken_word(str(item["word"])) for item in raw]
        if raw_is_timed
        else []
    )
    raw_character_ranges: list[tuple[int, int, int, int]] = []
    if raw_is_timed and all(raw_normalized):
        raw_character_offset = 0
        for item, normalized in zip(raw, raw_normalized, strict=True):
            next_offset = raw_character_offset + len(normalized)
            raw_character_ranges.append(
                (
                    raw_character_offset,
                    next_offset,
                    round(float(item["start"]) * 1000),
                    round(float(item["end"]) * 1000),
                )
            )
            raw_character_offset = next_offset
    candidate_ranges = _aligned_source_word_ranges(
        source_normalized,
        raw_normalized,
        raw_character_ranges,
        duration_ms=duration_ms,
    )
    if duration_ms < len(source):
        raise PresentationBuildError("narration audio is too short for word timing")
    words: list[dict[str, Any]] = []
    previous_end = 0
    for index, match in enumerate(source):
        candidate = candidate_ranges[index]
        candidate_start = candidate.start_ms
        candidate_end = candidate.end_ms
        remaining_words = len(source) - index - 1
        latest_end = duration_ms - remaining_words
        start_ms = min(max(previous_end, candidate_start), latest_end - 1)
        end_ms = min(latest_end, max(start_ms + 1, candidate_end))
        words.append(
            {
                "text": match.group(0),
                "text_start": match.start(),
                "text_end": match.end(),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timing_source": candidate.timing_source,
                "timing_confidence": candidate.timing_confidence,
                "raw_word_start": candidate.raw_word_start,
                "raw_word_end": candidate.raw_word_end,
            }
        )
        previous_end = end_ms
    return words


def _normalized_spoken_word(value: str) -> str:
    expanded = re.sub(
        r"[0-9]+",
        lambda match: _spoken_integer(int(match.group(0))),
        value.casefold(),
    )
    return "".join(character for character in expanded if character.isalnum())


def _spoken_integer(value: int) -> str:
    ones = (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    )
    tens = (
        "",
        "",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
    )
    if value < len(ones):
        return ones[value]
    if value < 100:
        return tens[value // 10] + (ones[value % 10] if value % 10 else "")
    if value < 1000:
        return ones[value // 100] + "hundred" + (
            _spoken_integer(value % 100) if value % 100 else ""
        )
    return "".join(ones[int(character)] for character in str(value))


def _aligned_source_word_ranges(
    source_words: list[str],
    raw_words: list[str],
    raw_character_ranges: list[tuple[int, int, int, int]],
    *,
    duration_ms: int,
) -> list[_AlignedSourceWord]:
    """Map source tokens to local ASR timing without poisoning later matches."""

    if not source_words:
        return []
    if not raw_words or not raw_character_ranges:
        return _interpolate_source_word_ranges(
            source_words, [None] * len(source_words), duration_ms=duration_ms
        )
    source_text = "".join(source_words)
    raw_text = "".join(raw_words)
    source_to_raw: dict[int, int] = {}
    for block in SequenceMatcher(
        None, source_text, raw_text, autojunk=False
    ).get_matching_blocks():
        for offset in range(block.size):
            source_to_raw[block.a + offset] = block.b + offset

    result: list[_AlignedSourceWord | None] = []
    source_offset = 0
    for word in source_words:
        mapped = [
            source_to_raw.get(index)
            for index in range(source_offset, source_offset + len(word))
        ]
        source_offset += len(word)
        if not mapped or any(value is None for value in mapped):
            result.append(None)
            continue
        raw_offsets = [int(value) for value in mapped if value is not None]
        if any(
            following != previous + 1
            for previous, following in zip(raw_offsets, raw_offsets[1:])
        ):
            result.append(None)
            continue
        start_ms = _spoken_character_time_ms(
            raw_offsets[0], raw_character_ranges, boundary="start"
        )
        end_ms = _spoken_character_time_ms(
            raw_offsets[-1] + 1, raw_character_ranges, boundary="end"
        )
        raw_word_indexes = [
            index
            for index, (start, end, _start_ms, _end_ms) in enumerate(
                raw_character_ranges
            )
            if raw_offsets[0] < end and raw_offsets[-1] + 1 > start
        ]
        result.append(
            _AlignedSourceWord(
                start_ms=min(duration_ms, max(0, start_ms)),
                end_ms=min(duration_ms, max(0, end_ms)),
                timing_source="transcription",
                timing_confidence="high",
                raw_word_start=raw_word_indexes[0],
                raw_word_end=raw_word_indexes[-1] + 1,
            )
        )
    return _interpolate_source_word_ranges(
        source_words, result, duration_ms=duration_ms
    )


def _interpolate_source_word_ranges(
    source_words: list[str],
    ranges: list[_AlignedSourceWord | None],
    *,
    duration_ms: int,
) -> list[_AlignedSourceWord]:
    """Fill only unmatched local spans, preserving later ASR landmarks."""

    result = list(ranges)
    index = 0
    while index < len(result):
        if result[index] is not None:
            index += 1
            continue
        start = index
        while index < len(result) and result[index] is None:
            index += 1
        end = index
        previous = result[start - 1] if start else None
        following = result[end] if end < len(result) else None
        interval_start = previous.end_ms if previous is not None else 0
        interval_end = following.start_ms if following is not None else duration_ms
        interval_end = max(interval_start, interval_end)
        weights = [max(1, len(source_words[item])) for item in range(start, end)]
        total_weight = sum(weights)
        consumed = 0
        for item, weight in zip(range(start, end), weights, strict=True):
            word_start = round(
                interval_start
                + (interval_end - interval_start) * consumed / total_weight
            )
            consumed += weight
            word_end = round(
                interval_start
                + (interval_end - interval_start) * consumed / total_weight
            )
            result[item] = _AlignedSourceWord(
                start_ms=word_start,
                end_ms=word_end,
                timing_source="interpolated",
                timing_confidence="low",
                raw_word_start=None,
                raw_word_end=None,
            )
    return [item for item in result if item is not None]


def _spoken_character_time_ms(
    offset: int,
    ranges: list[tuple[int, int, int, int]],
    *,
    boundary: str,
) -> int:
    if boundary == "start":
        candidates = (item for item in ranges if offset < item[1])
    else:
        candidates = (item for item in ranges if offset <= item[1])
    start, end, start_ms, end_ms = next(candidates, ranges[-1])
    if offset <= start:
        return start_ms
    if offset >= end:
        return end_ms
    fraction = (offset - start) / (end - start)
    return round(start_ms + fraction * (end_ms - start_ms))


def _terminal_duration_ms(path: Path) -> int:
    try:
        values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise PresentationBuildError(f"terminal beat cast is invalid: {path}") from exc
    if not values or not isinstance(values[0], dict):
        raise PresentationBuildError(f"terminal beat cast is invalid: {path}")
    version = values[0].get("version")
    times = [float(event[0]) for event in values[1:] if isinstance(event, list) and event]
    seconds = sum(times) if version == 3 else (times[-1] if times else 0.0)
    return round(seconds * 1000)


def _final_browser_state(
    initial: Mapping[str, Any], actions: Iterable[Mapping[str, Any]]
) -> Mapping[str, Any]:
    state: Mapping[str, Any] = initial
    for action in actions:
        visual = action.get("visual")
        if not isinstance(visual, Mapping):
            continue
        candidate = visual.get("state") if visual.get("kind") == "state" else visual.get("end_state")
        if isinstance(candidate, Mapping):
            state = candidate
    return state


def _next_pointer(compiled: CompiledBrowserBeat) -> dict[str, Any]:
    pointer = dict(compiled.payload["initial_pointer"])
    for event in compiled.payload["events"]:
        if event["kind"] == "pointer_move":
            pointer.update(event["end"])
        elif event["kind"] == "pointer_visibility":
            pointer["visible"] = event["visible"]
    return pointer


def _next_display_url(compiled: CompiledBrowserBeat) -> str | None:
    value = compiled.payload.get("initial_display_url")
    for event in compiled.payload["events"]:
        if event["kind"] == "display_url":
            value = event["value"]
    return value if isinstance(value, str) else None


def _browser_pass(
    plan: RecordingPlan,
    log: BrowserCaptureLog,
    *,
    action_starts: Mapping[tuple[str, str], int] | None = None,
    beat_durations: Mapping[str, int] | None = None,
) -> tuple[dict[str, CompiledBrowserBeat], dict[str, Mapping[str, Any]]]:
    presentation = thaw(plan.presentation)
    browser_header = presentation.get("browser", {})
    default_transition = browser_header.get("transitions", {}).get("default", "cut")
    default_pointer_visible = bool(
        browser_header.get("pointer", {}).get("visible", True)
    )
    pointer = {
        "x": float(log.viewport["width"]) / 2,
        "y": float(log.viewport["height"]) / 2,
        "visible": default_pointer_visible,
    }
    display_url: str | None = None
    state: Mapping[str, Any] = log.initial_state
    compiled_by_beat: dict[str, CompiledBrowserBeat] = {}
    initial_states: dict[str, Mapping[str, Any]] = {}
    for beat in plan.beats:
        if beat.medium is not RecordingMedium.browser:
            continue
        pointer["visible"] = (
            default_pointer_visible
            if beat.browser_pointer_visible is None
            else beat.browser_pointer_visible
        )
        captures = log.actions_by_beat.get(beat.id, ())
        initial_states[beat.id] = state
        starts = None
        if action_starts is not None:
            starts = {
                action.id: action_starts[(beat.id, action.id)]
                for action in beat.actions
                if isinstance(action, BrowserActionPlan)
            }
        compiled = compile_browser_beat(
            plan.id,
            beat,
            action_captures=captures,
            viewport=log.viewport,
            initial_state=state,
            clip_assets=log.clip_assets,
            action_starts_ms=starts,
            duration_ms=None if beat_durations is None else beat_durations[beat.id],
            initial_pointer=pointer,
            initial_display_url=display_url,
            default_transition=default_transition,
        )
        compiled_by_beat[beat.id] = compiled
        state = _final_browser_state(state, captures)
        pointer = _next_pointer(compiled)
        display_url = _next_display_url(compiled)
    return compiled_by_beat, initial_states


def _timing_plan(plan: RecordingPlan, with_audio: bool) -> RecordingPlan:
    if with_audio:
        return plan
    return replace(
        plan,
        beats=tuple(
            replace(beat, narration_text="", anchors=(), waits=()) for beat in plan.beats
        ),
        narration_takes=(),
    )


def _load_sidecars(artifacts: PresentationAudioArtifacts | None) -> dict[str, dict[str, Any]]:
    if artifacts is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for take_id, path in artifacts.timestamps.items():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise PresentationBuildError(f"timestamp sidecar is invalid: {path}")
        result[take_id] = value
    return result


def compile_presentation_bundle(
    spec: Mapping[str, Any],
    plan: RecordingPlan,
    run_dir: Path,
    *,
    audio_artifacts: PresentationAudioArtifacts | None = None,
) -> PresentationBuildResult:
    """Compile a completed private capture into a validated run-local bundle."""

    if not capture_artifacts_exist(plan, run_dir):
        raise PresentationBuildError("private capture is incomplete")
    paths = run_paths(run_dir)
    has_browser = any(beat.medium is RecordingMedium.browser for beat in plan.beats)
    browser_log = load_browser_capture_log(paths["browser_capture"]) if has_browser else None
    preliminary: dict[str, CompiledBrowserBeat] = {}
    if browser_log is not None:
        preliminary, _ = _browser_pass(plan, browser_log)
    action_durations: dict[tuple[str, str], int] = {}
    visual_durations: dict[str, int] = {}
    terminal_intervals: dict[str, dict[str, tuple[int, int]]] = {}
    for beat in plan.beats:
        if beat.medium is RecordingMedium.browser:
            compiled = preliminary[beat.id]
            visual_durations[beat.id] = int(compiled.payload["duration_ms"])
            for action_id, start in compiled.action_starts_ms.items():
                action_durations[(beat.id, action_id)] = (
                    compiled.action_completions_ms[action_id] - start
                )
        else:
            source = paths["terminal_beats"] / f"{beat.id}.cast"
            visual_durations[beat.id] = _terminal_duration_ms(source)
            intervals = _load_terminal_action_intervals(
                paths["terminal_beats"] / f"{beat.id}.actions.json",
                beat_id=beat.id,
                expected_action_ids=_terminal_action_ids(beat),
            )
            terminal_intervals[beat.id] = intervals
            for action_id, (start_ms, end_ms) in intervals.items():
                action_durations[(beat.id, action_id)] = end_ms - start_ms
    timing_plan = _timing_plan(plan, audio_artifacts is not None)
    timing = compile_recording_timing(
        timing_plan,
        timestamp_sidecars=_load_sidecars(audio_artifacts),
        action_durations_ms=action_durations,
        beat_visual_durations_ms=visual_durations,
    )
    compiled_browser: dict[str, CompiledBrowserBeat] = {}
    if browser_log is not None:
        compiled_browser, _ = _browser_pass(
            plan,
            browser_log,
            action_starts={
                (item.beat_id, item.action_id): item.local_start_ms
                for item in timing.actions
                if (item.beat_id, item.action_id) in action_durations
            },
            beat_durations={item.id: item.duration_ms for item in timing.beats},
        )

    staging = Path(tempfile.mkdtemp(prefix=".presentation-build-", dir=run_dir))
    try:
        (staging / "beats").mkdir()
        (staging / "media").mkdir()
        manifest_beats: list[PresentationBeatV1] = []
        manifest_assets: dict[str, PresentationAssetV1] = {}
        all_sources: dict[str, Any] = {}
        timing_by_id = {item.id: item for item in timing.beats}
        first_browser = True
        presentation_settings = thaw(plan.presentation)
        presentation_config = presentation_settings["browser"]
        for beat in plan.beats:
            beat_timing = timing_by_id[beat.id]
            if beat.medium is RecordingMedium.terminal:
                payload = f"beats/{beat.id}.cast"
                materialize_terminal_beat(
                    paths["terminal_beats"] / f"{beat.id}.cast",
                    staging / payload,
                    duration_ms=beat_timing.duration_ms,
                    captured_action_intervals_ms=terminal_intervals[beat.id],
                    action_starts_ms={
                        item.action_id: item.local_start_ms
                        for item in timing.actions
                        if item.beat_id == beat.id
                    },
                    text_highlights=tuple(
                        TerminalTextHighlightEvent(
                            id=f"{beat.id}-highlight-{index}",
                            text=highlight.text,
                            occurrence=highlight.occurrence,
                            start_ms=(
                                timing.anchor_times_ms[
                                    (beat.id, highlight.start_anchor)
                                ]
                                - beat_timing.offset_ms
                            ),
                            end_ms=(
                                timing.anchor_times_ms[(beat.id, highlight.end_anchor)]
                                - beat_timing.offset_ms
                            ),
                        )
                        for index, highlight in enumerate(beat.terminal_highlights)
                    ),
                )
                transition = None
            else:
                payload = f"beats/{beat.id}.browser.json"
                compiled = compiled_browser[beat.id]
                (staging / payload).write_text(
                    json.dumps(dict(compiled.payload), separators=(",", ":"), sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                all_sources.update(compiled.assets)
                transition = (
                    presentation_config["window"].get("opening_transition", "cut")
                    if first_browser
                    else presentation_config["transitions"].get("default", "cut")
                )
                first_browser = False
            guide = None
            if beat.guide is not None:
                guide_config = thaw(beat.guide)
                commands = guide_config.get("commands", [])
                summary = guide_config.get("summary")
                hint = guide_config.get("success_hint")
                if (
                    commands
                    or (isinstance(summary, str) and summary)
                    or (isinstance(hint, str) and hint)
                ):
                    from .presentation_schema import PresentationGuideV1

                    guide = PresentationGuideV1(
                        commands=list(commands),
                        summary=summary,
                        success_hint=hint,
                    )
            player = (
                None
                if beat.player_highlight is None
                else PresentationBeatPlayerV1(
                    highlight=PresentationPlayerToolbarHighlightV1(
                        control=PlayerToolbarControl(beat.player_highlight.control),
                        start_ms=(
                            timing.anchor_times_ms[
                                (beat.id, beat.player_highlight.start_anchor)
                            ]
                            - beat_timing.offset_ms
                        ),
                        end_ms=(
                            beat_timing.duration_ms
                            if beat.player_highlight.end_anchor is None
                            else timing.anchor_times_ms[
                                (beat.id, beat.player_highlight.end_anchor)
                            ]
                            - beat_timing.offset_ms
                        ),
                    )
                )
            )
            manifest_beats.append(
                PresentationBeatV1(
                    id=beat.id,
                    heading=beat.heading,
                    renderer=beat.medium.value,
                    offset_ms=beat_timing.offset_ms,
                    duration_ms=beat_timing.duration_ms,
                    payload=payload,
                    guide=guide,
                    player=player,
                    transition_in=transition,
                )
            )
        media_runtime: BrowserMediaRuntime | None = None
        if all_sources:
            try:
                media_runtime = require_browser_media_runtime(
                    require_h264=any(
                        source.path.suffix.lower() == ".mp4"
                        for source in all_sources.values()
                    )
                )
            except BrowserRuntimeError as exc:
                raise PresentationBuildError(str(exc)) from exc
        for asset_id, source in sorted(all_sources.items()):
            assert media_runtime is not None
            source_path = run_dir / source.path
            published_path, media_type = _publish_media_asset(
                source_path, staging, ffmpeg=media_runtime.ffmpeg
            )
            content = published_path.read_bytes()
            manifest_assets[asset_id] = PresentationAssetV1(
                path=published_path.relative_to(staging).as_posix(),
                media_type=media_type,
                sha256=hashlib.sha256(content).hexdigest(),
                bytes=len(content),
            )

        manifest_audio = None
        if audio_artifacts is not None:
            metadata_target = _stage_presentation_audio(
                audio_artifacts, timing, staging
            )
            (staging / "timestamps").mkdir()
            for path in audio_artifacts.timestamps.values():
                shutil.copy2(path, staging / "timestamps" / path.name)
            manifest_audio = PresentationAudioV1(
                metadata=metadata_target.name,
                intervals=list(timing.audio_intervals),
            )

        renderers = {
            medium.value: PresentationRendererV1()
            for medium in {beat.medium for beat in plan.beats}
        }
        window = presentation_config["window"]
        chrome = presentation_config["chrome"]
        header = PresentationHeaderV1(
            guided=bool(presentation_settings["guided"]),
            browser=(
                PresentationBrowserHeaderV1(
                    window=PresentationWindowV1(
                        mode=window["mode"], theme=window["theme"], title=window.get("title")
                    ),
                    chrome=PresentationChromeV1(mode=chrome["mode"]),
                )
                if has_browser
                else None
            )
        )
        manifest = PresentationManifestV1(
            recording=PresentationRecordingV1(
                id=plan.id, title=plan.title, duration_ms=timing.duration_ms
            ),
            renderers=renderers,
            presentation=header,
            audio=manifest_audio,
            assets=manifest_assets,
            beats=manifest_beats,
        )
        serialized = serialize_presentation_manifest(manifest)
        (staging / MANIFEST_FILE).write_text(
            json.dumps(serialized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        visual_hashes = [source.sha256 for source in all_sources.values()]
        take_hashes = (
            {}
            if audio_artifacts is None
            else {
                take_id: hashlib.sha256(path.read_bytes()).hexdigest()
                for take_id, path in audio_artifacts.take_audio.items()
            }
        )
        timestamp_hashes = (
            {}
            if audio_artifacts is None
            else {
                take_id: hashlib.sha256(path.read_bytes()).hexdigest()
                for take_id, path in audio_artifacts.timestamps.items()
            }
        )
        fingerprints = artifact_fingerprints(
            spec,
            plan,
            visual_asset_hashes=visual_hashes,
            narration_take_hashes=take_hashes,
            timestamp_hashes=timestamp_hashes,
        )
        warnings = sorted(
            set(_capture_warnings(paths["browser_capture"]) if has_browser else ())
            | set(audio_artifacts.warnings if audio_artifacts is not None else ())
        )
        dependencies = [
            {"path": key, "sha256": value}
            for key, value in _source_dependencies(spec).items()
            if not Path(key).is_absolute()
        ]
        metadata = {
            "version": 1,
            "recording": plan.id,
            "capture_fingerprint": fingerprints.capture_fingerprint,
            "presentation_fingerprint": fingerprints.presentation_fingerprint,
            "dependencies": dependencies,
            "versions": {
                "compiler": "presentation-v1",
                "renderer": "payload-v1",
            },
            "warnings": warnings,
        }
        (staging / RECORDING_METADATA_FILE).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_presentation_manifest(serialized, manifest_dir=staging)
        validate_public_staging(
            staging,
            secrets=_secret_values(spec),
            private_paths=(run_dir, project_root_from_spec(spec)),
            ffprobe=None if media_runtime is None else media_runtime.ffprobe,
        )
        bundle = publish_public_bundle(
            staging,
            paths["presentation"],
            secrets=_secret_values(spec),
            private_paths=(run_dir, project_root_from_spec(spec)),
            ffprobe=None if media_runtime is None else media_runtime.ffprobe,
        )
        fingerprint_payload = {
            **fingerprints.payload(),
            "recording": plan.id,
            "dependencies": [
                {"path": key, "sha256": value}
                for key, value in _source_dependencies(spec).items()
            ],
        }
        paths["fingerprint"].write_text(
            json.dumps(fingerprint_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths["report"].write_text(
            json.dumps(
                {
                    "version": 1,
                    "recording": plan.id,
                    "duration_ms": timing.duration_ms,
                    "beats": [
                        {"id": item.id, "offset_ms": item.offset_ms, "duration_ms": item.duration_ms}
                        for item in timing.beats
                    ],
                    "warnings": warnings,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return PresentationBuildResult(
            run_dir=run_dir,
            bundle_dir=bundle,
            manifest=bundle / MANIFEST_FILE,
            fingerprints=fingerprints,
            warnings=tuple(warnings),
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def publish_bundle(
    spec: Mapping[str, Any], run_dir: Path
) -> Path:
    source = run_paths(run_dir)["presentation"]
    if not source.is_dir():
        raise PresentationBuildError("run-local presentation bundle is missing")
    ffprobe = _bundle_ffprobe(source)
    try:
        return publish_public_bundle(
            source,
            public_bundle_dir(spec),
            secrets=_secret_values(spec),
            private_paths=(run_dir, project_root_from_spec(spec)),
            ffprobe=ffprobe,
        )
    except Exception as exc:
        raise PresentationBuildError(str(exc)) from exc


def validate_run_bundle(spec: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
    root = run_paths(run_dir)["presentation"]
    ffprobe = _bundle_ffprobe(root)
    try:
        return validate_public_staging(
            root,
            secrets=_secret_values(spec),
            private_paths=(run_dir, project_root_from_spec(spec)),
            ffprobe=ffprobe,
        )
    except Exception as exc:
        raise PresentationBuildError(str(exc)) from exc


def _bundle_ffprobe(root: Path) -> str | None:
    if not any(root.glob("media/*")):
        return None
    try:
        return require_browser_media_runtime(
            require_h264=any(root.glob("media/*.mp4"))
        ).ffprobe
    except BrowserRuntimeError as exc:
        raise PresentationBuildError(str(exc)) from exc


def _terminal_action_ids(beat: BeatPlan) -> tuple[str, ...]:
    result: list[str] = []
    for action_index, action in enumerate(beat.actions):
        if not isinstance(action, TerminalActionPlan):
            continue
        commands = action.config.get("commands")
        if commands:
            for command_index, command in enumerate(commands):
                result.append(
                    terminal_action_id(action_index, command_index, command)
                )
        else:
            result.append(terminal_action_id(action_index, None))
    return tuple(result)


def _load_terminal_action_intervals(
    path: Path,
    *,
    beat_id: str,
    expected_action_ids: tuple[str, ...],
) -> dict[str, tuple[int, int]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresentationBuildError(
            f"terminal action timing is invalid for beat {beat_id!r}"
        ) from exc
    if not isinstance(value, dict):
        raise PresentationBuildError(
            f"terminal action timing is invalid for beat {beat_id!r}"
        )
    actions = value.get("actions")
    if (
        value.get("version") != 1
        or value.get("beat_id") != beat_id
        or not isinstance(actions, list)
    ):
        raise PresentationBuildError(
            f"terminal action timing is invalid for beat {beat_id!r}"
        )
    result: dict[str, tuple[int, int]] = {}
    for item in actions:
        if not isinstance(item, dict):
            raise PresentationBuildError(
                f"terminal action timing is invalid for beat {beat_id!r}"
            )
        action_id = item.get("id")
        start_ms = item.get("start_ms")
        end_ms = item.get("end_ms")
        if (
            not isinstance(action_id, str)
            or isinstance(start_ms, bool)
            or not isinstance(start_ms, int)
            or isinstance(end_ms, bool)
            or not isinstance(end_ms, int)
            or start_ms < 0
            or end_ms < start_ms
            or action_id in result
        ):
            raise PresentationBuildError(
                f"terminal action timing is invalid for beat {beat_id!r}"
            )
        result[action_id] = (start_ms, end_ms)
    if tuple(result) != expected_action_ids:
        raise PresentationBuildError(
            f"terminal action timing does not match beat {beat_id!r} actions"
        )
    return result


def _publish_media_asset(
    source: Path, staging: Path, *, ffmpeg: str
) -> tuple[Path, str]:
    if not source.is_file():
        raise PresentationBuildError(f"captured media asset is missing: {source}")
    if source.suffix.lower() == ".png":
        temporary = staging / "media" / ".state.webp"
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-c:v",
                "libwebp",
                "-lossless",
                "1",
                "-compression_level",
                "6",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not temporary.is_file():
            detail = (result.stderr or result.stdout or "").strip()
            raise PresentationBuildError(
                "ffmpeg could not encode a lossless WebP browser state"
                + (f": {detail}" if detail else "")
            )
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        target = staging / "media" / f"{digest}.webp"
        if target.exists():
            temporary.unlink()
        else:
            temporary.replace(target)
        return target, "image/webp"
    if source.suffix.lower() == ".mp4":
        content = source.read_bytes()
        target = staging / "media" / f"{hashlib.sha256(content).hexdigest()}.mp4"
        if not target.exists():
            target.write_bytes(content)
        return target, "video/mp4"
    raise PresentationBuildError(f"unsupported captured media class: {source.suffix}")


def _capture_warnings(path: Path) -> tuple[str, ...]:
    warnings: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") == "warning":
            code = value.get("code")
            if isinstance(code, str) and code:
                warnings.add(code)
    return tuple(sorted(warnings))


def _secret_values(spec: Mapping[str, Any]) -> tuple[str, ...]:
    names: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            secret = value.get("secret")
            if isinstance(secret, Mapping) and isinstance(secret.get("env"), str):
                names.add(secret["env"])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(spec)
    return tuple(os.environ[name] for name in sorted(names) if os.environ.get(name))
