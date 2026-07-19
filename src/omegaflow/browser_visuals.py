"""Versioned browser state, redaction, and dynamic-fragment policies."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .browser_runtime import BrowserRuntimeError, require_browser_media_runtime


STABLE_SAMPLE_INTERVAL_MS = 60
STABLE_CONSECUTIVE_FRAMES = 3
STABLE_TIMEOUT_MS = 1200
DYNAMIC_MINIMUM_WINDOW_MS = 300
IMPLICIT_DYNAMIC_MAX_DURATION_MS = 3000
DYNAMIC_MAX_ENCODED_BYTES = 2_000_000
DYNAMIC_CRF = 18
REDACTION_COLOR = "#111827"
FRAME_MATCH_WIDTH = 192
FRAME_MATCH_HEIGHT = 60
FRAME_MATCH_RATE = 25
FRAME_MATCH_DURATION_MS = 1000 // FRAME_MATCH_RATE
FRAME_MATCH_MAX_MAD = 4.0
FRAME_MATCH_MAX_PRECISE_MAD = 1.0
FRAME_MATCH_OUTLIER_DELTA = 8
FRAME_MATCH_MAX_OUTLIER_RATIO = 0.0007
FRAME_MATCH_CONSECUTIVE_FRAMES = 3
FRAME_MATCH_START_LOOKBACK_MS = 2000


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
    end_state_path: Path
    start_state_path: Path | None = None
    explicit_dynamic: bool = False
    preserve_start: bool = False


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

    def capture_unredacted_state_once(self) -> dict[str, Any]:
        """Capture the pristine initial page before any authored navigation."""

        try:
            content = self.page.screenshot(type="png")
        except BaseException as exc:
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                "could not capture initial browser visual state",
            ) from exc
        return self._store_state(content)

    def observe(
        self,
        *,
        beat_id: str,
        action_id: str,
        video_start_ms: int,
        video_end_ms: Callable[[], int],
        start_state_path: Path | None = None,
        extra_redactions: tuple[Mapping[str, Any], ...] = (),
        force_dynamic: bool = False,
        explicit_dynamic: bool = False,
        preserve_start: bool = False,
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
        end_content = self._screenshot(extra_redactions)
        # Screenshots synchronize with a rendered browser frame and may block
        # while Playwright's video pipeline catches up. Take the trim boundary
        # after that synchronization so the frame represented by end_state is
        # actually present in the retained video.
        current_end = video_end_ms()
        samples.append(end_content)
        hashes.append(hashlib.sha256(end_content).hexdigest())
        duration = current_end - video_start_ms
        if not explicit_dynamic and duration > IMPLICIT_DYNAMIC_MAX_DURATION_MS:
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                f"action {action_id!r} dynamic window exceeds "
                f"{IMPLICIT_DYNAMIC_MAX_DURATION_MS} ms",
            )
        self._store_stability_diagnostics(beat_id, action_id, samples, hashes)
        end_state = self._store_state(end_content)
        request = DynamicFragmentRequest(
            beat_id=beat_id,
            action_id=action_id,
            source_start_ms=video_start_ms,
            source_end_ms=current_end,
            end_state_path=self.run_dir / end_state["path"],
            start_state_path=start_state_path,
            explicit_dynamic=explicit_dynamic,
            preserve_start=preserve_start,
        )
        self.dynamic_requests.append(request)
        return {
            "kind": "clip",
            "policy": "playwright-video-v1",
            "end_state": end_state,
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
        try:
            media = require_browser_media_runtime(require_h264=True)
        except BrowserRuntimeError as exc:
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                str(exc),
            ) from exc
        ffmpeg = media.ffmpeg
        ffprobe = media.ffprobe
        assets: list[DynamicFragmentAsset] = []
        previous_request: DynamicFragmentRequest | None = None
        previous_source_end_ms: int | None = None
        for index, request in enumerate(self.dynamic_requests, 1):
            # Playwright's video can lag the authored action clock, and that
            # lag can grow during a recording. Align the end state first, then
            # search through that actual boundary for the final start-state
            # run immediately before the captured motion.
            try:
                source_end_ms = _matching_end_frame_ms(
                    ffmpeg,
                    source_video,
                    request.end_state_path,
                    minimum_ms=request.source_end_ms,
                    lookback_ms=(
                        FRAME_MATCH_START_LOOKBACK_MS
                        if request.start_state_path is not None
                        else 0
                    ),
                )
                source_start_ms = request.source_start_ms
                if request.start_state_path is not None:
                    source_start_ms = _matching_start_frame_ms(
                        ffmpeg,
                        source_video,
                        request.start_state_path,
                        reference_ms=request.source_start_ms,
                        maximum_ms=source_end_ms,
                        prefer_reference=request.preserve_start,
                    )
                if (
                    previous_request is not None
                    and previous_source_end_ms is not None
                    and request.beat_id == previous_request.beat_id
                    and abs(
                        request.source_start_ms - previous_request.source_end_ms
                    )
                    <= FRAME_MATCH_DURATION_MS
                ):
                    source_start_ms = previous_source_end_ms
            except BrowserVisualError as exc:
                detail = str(exc).split(": ", 1)[-1]
                raise BrowserVisualError(
                    exc.code,
                    f"beat {request.beat_id!r}, action {request.action_id!r}: {detail}",
                ) from exc
            duration_ms = source_end_ms - source_start_ms
            if duration_ms <= 0:
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    f"action {request.action_id!r} has no aligned dynamic frames",
                )
            if (
                not request.explicit_dynamic
                and duration_ms > IMPLICIT_DYNAMIC_MAX_DURATION_MS
            ):
                raise BrowserVisualError(
                    "BROWSER_UNSUPPORTED_MOTION",
                    f"action {request.action_id!r} dynamic window exceeds "
                    f"{IMPLICIT_DYNAMIC_MAX_DURATION_MS} ms",
                )
            temporary = self.fragments_dir / f".fragment-{index}.mp4"
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
                    f"{source_start_ms / 1000:.3f}",
                    "-i",
                    str(source_video),
                    "-t",
                    f"{duration_ms / 1000:.3f}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    str(DYNAMIC_CRF),
                    "-profile:v",
                    "baseline",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
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
            if (
                probe["codec"] != "h264"
                or "mp4" not in probe["format_name"].split(",")
                or probe["pixel_format"] != "yuv420p"
                or probe["has_audio"]
            ):
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
            destination = self.fragments_dir / f"{digest}.mp4"
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
                    source_start_ms=source_start_ms,
                    source_end_ms=source_end_ms,
                )
            )
            previous_request = request
            previous_source_end_ms = source_end_ms
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


def _normalized_state_frame(ffmpeg: str, state: Path, *, boundary: str) -> bytes:
    if state.is_symlink() or not state.is_file():
        raise BrowserVisualError(
            "BROWSER_SCHEMA", f"dynamic fragment {boundary} state is unavailable"
        )
    frame_size = FRAME_MATCH_WIDTH * FRAME_MATCH_HEIGHT
    target = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(state),
            "-vf",
            f"scale={FRAME_MATCH_WIDTH}:{FRAME_MATCH_HEIGHT}:flags=area,format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if target.returncode != 0 or len(target.stdout) != frame_size:
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION",
            f"could not normalize a dynamic fragment {boundary} state",
        )
    return target.stdout


def _matching_start_frame_ms(
    ffmpeg: str,
    source_video: Path,
    start_state: Path,
    *,
    reference_ms: int,
    maximum_ms: int,
    prefer_reference: bool = False,
) -> int:
    frame_size = FRAME_MATCH_WIDTH * FRAME_MATCH_HEIGHT
    target = _normalized_state_frame(ffmpeg, start_state, boundary="start")
    search_start_ms = max(0, reference_ms - FRAME_MATCH_START_LOOKBACK_MS)
    process = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{search_start_ms / 1000:.3f}",
            "-i",
            str(source_video),
            "-t",
            f"{(maximum_ms - search_start_ms) / 1000:.3f}",
            "-vf",
            f"fps={FRAME_MATCH_RATE},"
            f"scale={FRAME_MATCH_WIDTH}:{FRAME_MATCH_HEIGHT}:flags=area,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION",
            "could not inspect a dynamic fragment start state",
        )
    match_index: int | None = None
    match_distance_ms: int | None = None
    consecutive_matches = 0
    index = 0
    while True:
        frame = process.stdout.read(frame_size)
        if not frame:
            break
        if len(frame) != frame_size:
            process.kill()
            process.wait()
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                "dynamic fragment started with a partial frame",
            )
        differences = tuple(
            abs(target_value - frame_value)
            for target_value, frame_value in zip(target, frame, strict=True)
        )
        score = sum(differences) / frame_size
        outlier_ratio = (
            sum(
                difference > FRAME_MATCH_OUTLIER_DELTA
                for difference in differences
            )
            / frame_size
        )
        if (
            score <= FRAME_MATCH_MAX_MAD
            and outlier_ratio <= FRAME_MATCH_MAX_OUTLIER_RATIO
        ):
            consecutive_matches += 1
            if consecutive_matches >= FRAME_MATCH_CONSECUTIVE_FRAMES:
                candidate_index = index - FRAME_MATCH_CONSECUTIVE_FRAMES + 1
                candidate_ms = (
                    search_start_ms + candidate_index * FRAME_MATCH_DURATION_MS
                )
                candidate_distance_ms = abs(candidate_ms - reference_ms)
                if (
                    not prefer_reference
                    or match_distance_ms is None
                    or candidate_distance_ms < match_distance_ms
                ):
                    match_index = candidate_index
                    match_distance_ms = candidate_distance_ms
        else:
            consecutive_matches = 0
        index += 1
    stderr = process.stderr.read()
    returncode = process.wait()
    if returncode != 0 or stderr or match_index is None:
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION",
            "could not align a dynamic fragment with its initial browser frame",
        )
    return search_start_ms + match_index * FRAME_MATCH_DURATION_MS


def _matching_end_frame_ms(
    ffmpeg: str,
    source_video: Path,
    end_state: Path,
    *,
    minimum_ms: int,
    lookback_ms: int = 0,
) -> int:
    frame_size = FRAME_MATCH_WIDTH * FRAME_MATCH_HEIGHT
    target = _normalized_state_frame(ffmpeg, end_state, boundary="end")
    search_start_ms = max(0, minimum_ms - lookback_ms)
    process = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_video),
            "-ss",
            f"{search_start_ms / 1000:.3f}",
            "-vf",
            f"fps={FRAME_MATCH_RATE},"
            f"scale={FRAME_MATCH_WIDTH}:{FRAME_MATCH_HEIGHT}:flags=area,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION",
            "could not inspect a dynamic fragment end state",
        )
    match_index: int | None = None
    precise_lookback_match_index: int | None = None
    consecutive_matches = 0
    index = 0
    while True:
        frame = process.stdout.read(frame_size)
        if not frame:
            break
        if len(frame) != frame_size:
            process.kill()
            process.wait()
            raise BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                "dynamic fragment ended with a partial frame",
            )
        differences = tuple(
            abs(target_value - frame_value)
            for target_value, frame_value in zip(target, frame, strict=True)
        )
        score = sum(differences) / frame_size
        outlier_ratio = (
            sum(
                difference > FRAME_MATCH_OUTLIER_DELTA
                for difference in differences
            )
            / frame_size
        )
        frame_end_ms = search_start_ms + (index + 1) * FRAME_MATCH_DURATION_MS
        if match_index is None:
            if frame_end_ms <= minimum_ms:
                consecutive_matches = 0
            elif (
                score <= FRAME_MATCH_MAX_MAD
                and outlier_ratio <= FRAME_MATCH_MAX_OUTLIER_RATIO
            ):
                consecutive_matches += 1
            else:
                consecutive_matches = 0
            if consecutive_matches >= FRAME_MATCH_CONSECUTIVE_FRAMES:
                match_index = index - FRAME_MATCH_CONSECUTIVE_FRAMES + 1
        # The screenshot and Playwright video clocks can differ slightly. If
        # the exact captured state occurs inside that measured lag window,
        # prefer its closest frame even when animation changes the next frame.
        # Outside the lag window, require the stable run above so an earlier or
        # later transient cannot become the fragment boundary.
        if (
            lookback_ms > 0
            and frame_end_ms <= minimum_ms
            and score <= FRAME_MATCH_MAX_PRECISE_MAD
            and outlier_ratio == 0
        ):
            precise_lookback_match_index = index
        index += 1
    stderr = process.stderr.read()
    returncode = process.wait()
    if (
        returncode != 0
        or stderr
        or (precise_lookback_match_index is None and match_index is None)
    ):
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION",
            "could not align a dynamic fragment with its completed browser frame",
        )
    selected_index = (
        precise_lookback_match_index
        if precise_lookback_match_index is not None
        else match_index
    )
    assert selected_index is not None
    return search_start_ms + (selected_index + 1) * FRAME_MATCH_DURATION_MS


def _probe_video(ffprobe: str, path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height:format=duration,format_name",
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
            "format_name": str(payload.get("format", {}).get("format_name", "")),
            "pixel_format": stream["pix_fmt"],
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "duration_seconds": float(payload.get("format", {}).get("duration", 0)),
            "has_audio": bool(audio_streams),
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BrowserVisualError(
            "BROWSER_UNSUPPORTED_MOTION", "dynamic fragment metadata is invalid"
        ) from exc
