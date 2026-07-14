"""Versioned browser state, redaction, and dynamic-fragment policies."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STABLE_SAMPLE_INTERVAL_MS = 60
STABLE_CONSECUTIVE_FRAMES = 3
STABLE_TIMEOUT_MS = 1200
DYNAMIC_MINIMUM_WINDOW_MS = 300
DYNAMIC_MAX_DURATION_MS = 3000
DYNAMIC_MAX_ENCODED_BYTES = 2_000_000
DYNAMIC_CRF = 10
DYNAMIC_BITRATE = "2M"
REDACTION_COLOR = "#111827"


class BrowserVisualError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class DynamicFragmentRequest:
    beat_id: str
    action_id: str
    source_start_ms: int
    source_end_ms: int


@dataclass(frozen=True)
class DynamicFragmentAsset:
    beat_id: str
    action_id: str
    path: Path
    sha256: str
    width: int
    height: int
    duration_ms: int
    encoded_bytes: int
    codec: str
    has_audio: bool
    source_start_ms: int
    source_end_ms: int


class BrowserVisualCapture:
    """Capture stable masked states and defer Playwright-video trimming."""

    def __init__(
        self,
        page: Any,
        *,
        run_dir: Path,
        states_dir: Path,
        fragments_dir: Path,
        diagnostics_dir: Path,
        redaction_targets: tuple[Mapping[str, Any], ...],
        locator_factory: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        self.page = page
        self.run_dir = run_dir
        self.states_dir = states_dir
        self.fragments_dir = fragments_dir
        self.diagnostics_dir = diagnostics_dir
        self.redaction_targets = redaction_targets
        self.locator_factory = locator_factory
        self.dynamic_requests: list[DynamicFragmentRequest] = []
        for path in (states_dir, fragments_dir, diagnostics_dir):
            if path.is_symlink():
                raise BrowserVisualError(
                    "BROWSER_SCHEMA", "private browser directory is a symlink"
                )
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not path.is_dir():
                raise BrowserVisualError(
                    "BROWSER_SCHEMA", "private browser path is not a directory"
                )
            path.chmod(0o700)

    def capture_state_once(
        self,
        *,
        action_id: str,
        extra_redactions: tuple[Mapping[str, Any], ...] = (),
    ) -> dict[str, Any]:
        content = self._screenshot(extra_redactions)
        return self._store_state(content)

    def observe(
        self,
        *,
        beat_id: str,
        action_id: str,
        video_start_ms: int,
        video_end_ms: Callable[[], int],
        extra_redactions: tuple[Mapping[str, Any], ...] = (),
        force_dynamic: bool = False,
    ) -> dict[str, Any]:
        samples: list[bytes] = []
        hashes: list[str] = []
        deadline = time.monotonic() + STABLE_TIMEOUT_MS / 1000
        consecutive = 0
        previous: str | None = None
        while True:
            content = self._screenshot(extra_redactions)
            digest = hashlib.sha256(content).hexdigest()
            samples.append(content)
            hashes.append(digest)
            if digest == previous:
                consecutive += 1
            else:
                previous = digest
                consecutive = 1
            if not force_dynamic and consecutive >= STABLE_CONSECUTIVE_FRAMES:
                state = self._store_state(content)
                return {
                    "kind": "state",
                    "policy": "stable-v1",
                    "state": state,
                    "sample_count": len(samples),
                }
            if force_dynamic or time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(STABLE_SAMPLE_INTERVAL_MS)

        redactions = self.redaction_targets + extra_redactions
        if redactions:
            self._store_stability_diagnostics(beat_id, action_id, samples, hashes)
            raise BrowserVisualError(
                "BROWSER_REDACTION_UNSAFE",
                f"action {action_id!r} requires redaction in a dynamic fragment",
            )
        current_end = video_end_ms()
        remaining = DYNAMIC_MINIMUM_WINDOW_MS - (current_end - video_start_ms)
        if remaining > 0:
            self.page.wait_for_timeout(remaining)
            current_end = video_end_ms()
        duration = current_end - video_start_ms
        if duration > DYNAMIC_MAX_DURATION_MS:
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                f"action {action_id!r} dynamic window exceeds {DYNAMIC_MAX_DURATION_MS} ms",
            )
        self._store_stability_diagnostics(beat_id, action_id, samples, hashes)
        request = DynamicFragmentRequest(
            beat_id=beat_id,
            action_id=action_id,
            source_start_ms=video_start_ms,
            source_end_ms=current_end,
        )
        self.dynamic_requests.append(request)
        return {
            "kind": "clip",
            "policy": "playwright-video-v1",
            "request": {
                "beat_id": beat_id,
                "action_id": action_id,
                "source_start_ms": request.source_start_ms,
                "source_end_ms": request.source_end_ms,
            },
        }

    def finalize_dynamic_fragments(
        self, source_video: Path
    ) -> tuple[DynamicFragmentAsset, ...]:
        if not self.dynamic_requests:
            return ()
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                "dynamic fragments require ffmpeg and ffprobe",
            )
        assets: list[DynamicFragmentAsset] = []
        for index, request in enumerate(self.dynamic_requests, 1):
            duration_ms = request.source_end_ms - request.source_start_ms
            temporary = self.fragments_dir / f".fragment-{index}.webm"
            if temporary.exists() or temporary.is_symlink():
                raise BrowserVisualError(
                    "BROWSER_SCHEMA", "temporary dynamic fragment path is unsafe"
                )
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{request.source_start_ms / 1000:.3f}",
                    "-i",
                    str(source_video),
                    "-t",
                    f"{duration_ms / 1000:.3f}",
                    "-an",
                    "-c:v",
                    "libvpx",
                    "-deadline",
                    "good",
                    "-cpu-used",
                    "4",
                    "-crf",
                    str(DYNAMIC_CRF),
                    "-b:v",
                    DYNAMIC_BITRATE,
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0 or not temporary.is_file():
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    f"could not trim dynamic fragment for action {request.action_id!r}",
                )
            probe = _probe_video(ffprobe, temporary)
            if probe["codec"] != "vp8" or probe["has_audio"]:
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    f"dynamic fragment for action {request.action_id!r} has an unsupported stream",
                )
            actual_duration_ms = round(probe["duration_seconds"] * 1000)
            if abs(actual_duration_ms - duration_ms) > 120:
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    f"dynamic fragment timing drifted for action {request.action_id!r}",
                )
            encoded_bytes = temporary.stat().st_size
            if encoded_bytes > DYNAMIC_MAX_ENCODED_BYTES:
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    f"dynamic fragment for action {request.action_id!r} exceeds the size budget",
                )
            content = temporary.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            destination = self.fragments_dir / f"{digest}.webm"
            if destination.is_symlink():
                raise BrowserVisualError(
                    "BROWSER_SCHEMA",
                    "content-addressed dynamic fragment path is unsafe",
                )
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise BrowserVisualError(
                        "BROWSER_SCHEMA",
                        "content-addressed dynamic fragment path is unsafe",
                    )
                temporary.unlink()
            else:
                temporary.replace(destination)
                destination.chmod(0o600)
            assets.append(
                DynamicFragmentAsset(
                    beat_id=request.beat_id,
                    action_id=request.action_id,
                    path=destination,
                    sha256=digest,
                    width=probe["width"],
                    height=probe["height"],
                    duration_ms=actual_duration_ms,
                    encoded_bytes=encoded_bytes,
                    codec=probe["codec"],
                    has_audio=False,
                    source_start_ms=request.source_start_ms,
                    source_end_ms=request.source_end_ms,
                )
            )
        return tuple(assets)

    def _screenshot(
        self, extra_redactions: tuple[Mapping[str, Any], ...]
    ) -> bytes:
        locators = []
        for target in self.redaction_targets + extra_redactions:
            locator = self.locator_factory(target)
            count = locator.count()
            if count != 1 or locator.bounding_box() is None:
                raise BrowserVisualError(
                    "BROWSER_REDACTION_UNSAFE",
                    "redaction target is missing, ambiguous, or not visible",
                )
            locators.append(locator)
        try:
            return self.page.screenshot(
                type="png",
                mask=locators,
                mask_color=REDACTION_COLOR,
            )
        except BaseException as exc:
            if not locators:
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    "could not capture browser visual state",
                ) from exc
            raise BrowserVisualError(
                "BROWSER_REDACTION_UNSAFE",
                "could not apply capture-time redaction",
            ) from exc

    def _store_state(self, content: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.states_dir / f"{digest}.png"
        if path.is_symlink():
            raise BrowserVisualError(
                "BROWSER_SCHEMA", "content-addressed browser state path is unsafe"
            )
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise BrowserVisualError(
                    "BROWSER_SCHEMA", "content-addressed browser state path is unsafe"
                )
        else:
            path.write_bytes(content)
            path.chmod(0o600)
        width, height = _png_dimensions(content)
        return {
            "path": path.relative_to(self.run_dir).as_posix(),
            "sha256": digest,
            "media_type": "image/png",
            "width": width,
            "height": height,
            "bytes": len(content),
        }

    def _store_stability_diagnostics(
        self,
        beat_id: str,
        action_id: str,
        samples: list[bytes],
        hashes: list[str],
    ) -> None:
        if not samples:
            return
        selected = {0, len(samples) - 1}
        selected.update(
            index
            for index in range(1, len(hashes))
            if hashes[index] != hashes[index - 1]
        )
        for index in sorted(selected):
            path = self.diagnostics_dir / f"{beat_id}-{action_id}-{index:03d}.png"
            if path.is_symlink():
                raise BrowserVisualError(
                    "BROWSER_SCHEMA", "stability diagnostic path is unsafe"
                )
            path.write_bytes(samples[index])
            path.chmod(0o600)


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 24 or content[:8] != b"\x89PNG\r\n\x1a\n":
        raise BrowserVisualError("BROWSER_SCHEMA", "captured state is not a PNG")
    return struct.unpack(">II", content[16:24])


def _probe_video(ffprobe: str, path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    audio = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or audio.returncode != 0:
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION", "could not inspect dynamic fragment"
        )
    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        stream = streams[0]
        audio_streams = json.loads(audio.stdout).get("streams", [])
        return {
            "codec": stream["codec_name"],
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "duration_seconds": float(payload.get("format", {}).get("duration", 0)),
            "has_audio": bool(audio_streams),
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION", "dynamic fragment metadata is invalid"
        ) from exc
