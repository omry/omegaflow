#!/usr/bin/env python3
"""Generate a presentation-timed asciinema cast from a fast baseline cast."""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import hydra
from omegaconf import DictConfig

from .studio_config import (
    CONFIG_DIR,
    PROJECT_ROOT,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    container_from_hydra_cfg,
    load_recording_spec,
    load_recording_spec_from_hydra_cfg,
)
from .terminal_style import ANSI_GREEN_BOLD, print_status


REPO_ROOT = PROJECT_ROOT
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_SCREEN_CONTROL_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[HJK]")
PROMPT_PREFIX_RE = re.compile(r"^(?:\([^)\r\n]+\)\s*)?\$\s*")
PROMPT_MATCH_TOLERANCE_SECONDS = 0.4
IDLE_PROMPT_EPSILON_SECONDS = 0.001
IDLE_PROMPT_MIN_SECONDS = 0.1
CAPTION_PREFIX = "\x1b[36;1m# "


class RetimeError(RuntimeError):
    pass


def pass_line(message: str) -> None:
    print_status("pass", message, color=ANSI_GREEN_BOLD)


@dataclass(frozen=True)
class CastEvent:
    index: int
    absolute_time: float
    event_type: str
    payload: Any


@dataclass(frozen=True)
class ScheduledEvent:
    absolute_time: float
    order: float
    event_type: str
    payload: Any


@dataclass(frozen=True)
class TimelineInterval:
    start: float
    end: float
    start_event: dict[str, Any]
    end_event: dict[str, Any]


@dataclass(frozen=True)
class AudioWaitTiming:
    target: str
    marker: str
    seconds: float
    gap_seconds: float


@dataclass(frozen=True)
class AudioSegmentTiming:
    segment_id: str
    duration: float
    anchor_seconds: dict[str, float]
    waits: tuple[AudioWaitTiming, ...] = ()


@dataclass(frozen=True)
class OutputSpan:
    events: tuple[CastEvent, ...]


@dataclass(frozen=True)
class PresentationCommand:
    key: tuple[str, str, str]
    prompt_interval: TimelineInterval
    run_interval: TimelineInterval | None
    prompt_payload: str
    output_mode: str
    fake_output: str
    timing: str
    output_span: OutputSpan


@dataclass(frozen=True)
class PresentationBeat:
    beat: str
    start: float
    end: float
    caption_payload: str | None
    caption_source_index: int | None
    commands: tuple[PresentationCommand, ...]
    raw_events: tuple[CastEvent, ...]
    hold_seconds: float
    audio_timing: AudioSegmentTiming | None


@dataclass(frozen=True)
class CaptionSourceWindow:
    payload: str | None
    index: int | None
    time: float | None
    next_index: int | None
    next_time: float | None


@dataclass(frozen=True)
class TimingRules:
    typing_char_delay: float = 0.035
    typing_space_delay: float = 0.02
    typing_punctuation_delay: float = 0.05
    typing_newline_delay: float = 0.0
    post_enter_pause: float = 0.35
    post_command_pause: float = 0.85
    minimum_section_spacing: float = 0.0


@dataclass(frozen=True)
class SourceTimestampWord:
    raw: str
    normalized: str
    start: int
    end: int


@dataclass(frozen=True)
class TranscriptTimestampWord:
    raw: str
    normalized: str
    start_seconds: float
    index: int


def load_manifest(
    recording_id: str, overrides: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    try:
        return load_recording_spec(recording_id, overrides)
    except StudioConfigError as exc:
        raise RetimeError(str(exc)) from exc


def as_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def cast_path_from_manifest(spec: dict[str, Any]) -> Path:
    outputs = as_mapping(spec.get("outputs"))
    cast = outputs.get("cast")
    if not isinstance(cast, str) or not cast:
        raise RetimeError("recording config outputs.cast must be a non-empty string")
    return relative_path(cast)


def timeline_path_for_cast(cast_path: Path) -> Path:
    return cast_path.with_suffix(".timeline.jsonl")


def output_path_from_manifest(spec: dict[str, Any], cast_path: Path) -> Path:
    outputs = as_mapping(spec.get("outputs"))
    retimed = outputs.get("retimed_cast")
    if isinstance(retimed, str) and retimed:
        return relative_path(retimed)
    return cast_path.with_name(f"{cast_path.stem}.retimed{cast_path.suffix}")


def audio_metadata_path_from_manifest(spec: dict[str, Any]) -> Path | None:
    outputs = as_mapping(spec.get("outputs"))
    configured = outputs.get("audio_metadata")
    if configured is not None:
        if not isinstance(configured, str) or not configured:
            raise RetimeError(
                "recording config outputs.audio_metadata must be a string"
            )
        return relative_path(configured)
    audio = outputs.get("audio")
    if isinstance(audio, str) and audio:
        return relative_path(audio).with_suffix(".json")
    return None


def retime_freshness_inputs(
    cast_path: Path,
    timeline_path: Path,
    audio_metadata_path: Path | None,
) -> list[Path]:
    inputs = [cast_path, timeline_path, Path(__file__)]
    if audio_metadata_path is not None and audio_metadata_path.exists():
        inputs.append(audio_metadata_path)
    return inputs


def require_fresh_output(
    *,
    output_path: Path,
    input_paths: list[Path],
    artifact_name: str,
    rerun_hint: str,
) -> None:
    if not output_path.exists():
        raise RetimeError(
            f"{artifact_name} is missing: {display_path(output_path)}; {rerun_hint}"
        )
    try:
        output_mtime = output_path.stat().st_mtime_ns
    except OSError as exc:
        raise RetimeError(f"could not stat {artifact_name}: {output_path}") from exc
    stale_inputs = []
    for path in input_paths:
        if not path.exists():
            continue
        try:
            if path.stat().st_mtime_ns > output_mtime:
                stale_inputs.append(path)
        except OSError as exc:
            raise RetimeError(f"could not stat retime input: {path}") from exc
    if stale_inputs:
        formatted = ", ".join(display_path(path) for path in stale_inputs)
        raise RetimeError(
            f"{artifact_name} is stale: {display_path(output_path)} "
            f"is older than {formatted}; {rerun_hint}"
        )


def require_fresh_retimed_cast(
    *,
    cast_path: Path,
    timeline_path: Path,
    output_path: Path,
    audio_metadata_path: Path | None,
) -> None:
    require_fresh_output(
        output_path=output_path,
        input_paths=retime_freshness_inputs(
            cast_path,
            timeline_path,
            audio_metadata_path,
        ),
        artifact_name="retimed cast",
        rerun_hint="run omegaflow step=retime",
    )
    require_materialized_audio_waits(audio_metadata_path)


def require_materialized_audio_waits(metadata_path: Path | None) -> None:
    if metadata_path is None or not metadata_path.exists():
        return
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RetimeError(f"invalid audio metadata JSON: {metadata_path}") from exc
    if not isinstance(payload, dict):
        raise RetimeError(f"audio metadata must be a mapping: {metadata_path}")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise RetimeError(f"audio metadata missing segments list: {metadata_path}")
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("id", "<unknown>"))
        waits = segment.get("waits")
        if not isinstance(waits, list):
            continue
        for wait in waits:
            if not isinstance(wait, dict) or not isinstance(wait.get("marker"), str):
                continue
            has_logical_wait = (
                isinstance(wait.get("target"), str)
                and isinstance(wait.get("text_offset"), int)
            )
            if not has_logical_wait:
                continue
            audio_second = wait.get("audio_second")
            pause_seconds = wait.get("pause_seconds")
            if (
                not isinstance(audio_second, (int, float))
                or not isinstance(pause_seconds, (int, float))
                or not math.isfinite(float(audio_second))
                or not math.isfinite(float(pause_seconds))
                or float(audio_second) < 0
                or float(pause_seconds) < 0
            ):
                marker = wait.get("marker")
                raise RetimeError(
                    f"audio wait {marker} in segment {segment_id!r} "
                    "has not been materialized by retime; run omegaflow step=retime"
                )


def metadata_relative_path(raw_path: str, metadata_path: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    repo_path = REPO_ROOT / path
    if repo_path.exists():
        return repo_path
    return metadata_path.parent / path


def read_timestamp_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RetimeError(f"invalid audio timestamp JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RetimeError(f"audio timestamp metadata must be a mapping: {path}")
    return payload


def read_timestamp_words(path: Path, *, source_text: str) -> list[dict[str, Any]]:
    payload = read_timestamp_payload(path)
    timestamp_source_text = payload.get("source_text")
    if not isinstance(timestamp_source_text, str):
        raise RetimeError(f"audio timestamp metadata missing source_text: {path}")
    if timestamp_source_text != source_text:
        raise RetimeError(f"audio timestamp source text is stale: {path}")
    words = payload.get("words")
    if not isinstance(words, list):
        raise RetimeError(f"audio timestamp metadata missing words list: {path}")
    return [word for word in words if isinstance(word, dict)]


def normalized_timestamp_word(word: str) -> str:
    return word.strip().lower().strip(".,;:!?\"'()[]{}")


def timestamp_words_are_close(left: str, right: str) -> bool:
    if len(left) < 3 or len(right) < 3:
        return False
    if left[0] != right[0] or abs(len(left) - len(right)) > 2:
        return False
    ratio = SequenceMatcher(None, left, right).ratio()
    if len(left) < 5 or len(right) < 5:
        return ratio >= 0.66
    return ratio >= 0.70


def source_timestamp_words(text: str) -> list[SourceTimestampWord]:
    words: list[SourceTimestampWord] = []
    for match in re.finditer(r"\S+", text):
        raw = match.group(0)
        normalized = normalized_timestamp_word(raw)
        if not normalized:
            continue
        stripped_start = match.start()
        while stripped_start < match.end() and not normalized_timestamp_word(
            text[stripped_start]
        ):
            stripped_start += 1
        words.append(
            SourceTimestampWord(
                raw=raw,
                normalized=normalized,
                start=stripped_start,
                end=stripped_start + len(normalized),
            )
        )
    return words


def transcript_timestamp_words(
    words: list[dict[str, Any]],
) -> list[TranscriptTimestampWord]:
    timestamp_words: list[TranscriptTimestampWord] = []
    for index, word in enumerate(words):
        raw_word = word.get("word")
        start = word.get("start")
        if (
            not isinstance(raw_word, str)
            or not raw_word.strip()
            or not isinstance(start, (int, float))
        ):
            continue
        normalized = normalized_timestamp_word(raw_word)
        if not normalized:
            continue
        timestamp_words.append(
            TranscriptTimestampWord(
                raw=raw_word,
                normalized=normalized,
                start_seconds=float(start),
                index=index,
            )
        )
    return timestamp_words


def timestamp_alignment_score(source: str, transcript: str) -> int:
    if source == transcript:
        return 6
    if timestamp_words_are_close(source, transcript):
        return 3
    return -2


def align_timestamp_words(
    source_words: list[SourceTimestampWord],
    transcript_words: list[TranscriptTimestampWord],
) -> list[tuple[SourceTimestampWord, TranscriptTimestampWord]]:
    if not source_words or not transcript_words:
        return []
    gap_penalty = -3
    rows = len(source_words) + 1
    cols = len(transcript_words) + 1
    scores = [[0] * cols for _ in range(rows)]
    moves = [[""] * cols for _ in range(rows)]
    for row in range(1, rows):
        scores[row][0] = scores[row - 1][0] + gap_penalty
        moves[row][0] = "up"
    for col in range(1, cols):
        scores[0][col] = scores[0][col - 1] + gap_penalty
        moves[0][col] = "left"

    for row in range(1, rows):
        source_word = source_words[row - 1]
        for col in range(1, cols):
            transcript_word = transcript_words[col - 1]
            diagonal = scores[row - 1][col - 1] + timestamp_alignment_score(
                source_word.normalized,
                transcript_word.normalized,
            )
            up = scores[row - 1][col] + gap_penalty
            left = scores[row][col - 1] + gap_penalty
            best = max(diagonal, up, left)
            scores[row][col] = best
            if best == diagonal:
                moves[row][col] = "diagonal"
            elif best == up:
                moves[row][col] = "up"
            else:
                moves[row][col] = "left"

    aligned: list[tuple[SourceTimestampWord, TranscriptTimestampWord]] = []
    row = len(source_words)
    col = len(transcript_words)
    while row > 0 or col > 0:
        move = moves[row][col]
        if move == "diagonal":
            source_word = source_words[row - 1]
            transcript_word = transcript_words[col - 1]
            aligned.append((source_word, transcript_word))
            row -= 1
            col -= 1
        elif move == "up":
            row -= 1
        elif move == "left":
            col -= 1
        else:
            break
    aligned.reverse()
    return aligned


def timestamp_alignment_is_reliable(
    aligned: list[tuple[SourceTimestampWord, TranscriptTimestampWord]],
    *,
    source_word_count: int,
    transcript_word_count: int,
) -> bool:
    if not aligned:
        return False
    strong_matches = 0
    for source_word, transcript_word in aligned:
        if (
            timestamp_alignment_score(
                source_word.normalized,
                transcript_word.normalized,
            )
            > 0
        ):
            strong_matches += 1
    denominator = max(source_word_count, transcript_word_count)
    return denominator > 0 and strong_matches / denominator >= 0.6


def timestamp_word_spans(
    *, text: str, words: list[dict[str, Any]], timestamp_path: Path
) -> list[tuple[int, int, float]]:
    source_words = source_timestamp_words(text)
    transcript_words = transcript_timestamp_words(words)
    aligned = align_timestamp_words(source_words, transcript_words)
    if not timestamp_alignment_is_reliable(
        aligned,
        source_word_count=len(source_words),
        transcript_word_count=len(transcript_words),
    ):
        raise RetimeError(f"could not align audio timestamps in {timestamp_path}")
    spans: list[tuple[int, int, float]] = []
    for source_word, transcript_word in aligned:
        if (
            timestamp_alignment_score(
                source_word.normalized,
                transcript_word.normalized,
            )
            <= 0
        ):
            continue
        spans.append(
            (source_word.start, source_word.end, transcript_word.start_seconds)
        )
    return spans


def marker_seconds_from_offset(
    *,
    marker: str,
    text_offset: int,
    spans: list[tuple[int, int, float]],
    segment_duration: float | None,
    text: str,
    segment_id: str,
) -> float:
    if segment_duration is not None and text_offset >= len(text.rstrip()):
        return segment_duration
    for start_index, end_index, start_seconds in spans:
        if start_index >= text_offset or end_index > text_offset:
            return start_seconds
    raise RetimeError(
        f"could not resolve audio marker {marker!r} in segment {segment_id!r}"
    )


def timestamp_word_spans_for_segment(
    *, segment: dict[str, Any], metadata_path: Path, reason: str
) -> tuple[str, float | None, list[tuple[int, int, float]]]:
    timestamp_reference = segment.get("timestamps")
    if not isinstance(timestamp_reference, str) or not timestamp_reference:
        segment_id = segment.get("id", "<unknown>")
        raise RetimeError(
            f"audio segment {segment_id!r} has {reason} but no timestamps"
        )
    text = segment.get("text")
    if not isinstance(text, str):
        segment_id = segment.get("id", "<unknown>")
        raise RetimeError(f"audio segment {segment_id!r} has {reason} but no text")
    duration = segment.get("duration")
    segment_duration = float(duration) if isinstance(duration, (int, float)) else None
    timestamp_path = metadata_relative_path(timestamp_reference, metadata_path)
    spans = timestamp_word_spans(
        text=text,
        words=read_timestamp_words(timestamp_path, source_text=text),
        timestamp_path=timestamp_path,
    )
    return text, segment_duration, spans


def anchor_seconds_from_segment(
    *, segment: dict[str, Any], metadata_path: Path
) -> dict[str, float]:
    anchors = segment.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        return {}
    text, segment_duration, spans = timestamp_word_spans_for_segment(
        segment=segment,
        metadata_path=metadata_path,
        reason="command anchors",
    )
    segment_id = str(segment.get("id", "<unknown>"))
    resolved: dict[str, float] = {}
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        marker = anchor.get("marker")
        text_offset = anchor.get("text_offset")
        if not isinstance(marker, str) or not isinstance(text_offset, int):
            continue
        resolved[marker] = marker_seconds_from_offset(
            marker=marker,
            text_offset=text_offset,
            spans=spans,
            segment_duration=segment_duration,
            text=text,
            segment_id=segment_id,
        )
    return resolved


def wait_timings_from_segment(
    *, segment: dict[str, Any], metadata_path: Path
) -> tuple[AudioWaitTiming, ...]:
    waits = segment.get("waits")
    if not isinstance(waits, list) or not waits:
        return ()
    text, segment_duration, spans = timestamp_word_spans_for_segment(
        segment=segment,
        metadata_path=metadata_path,
        reason="wait markers",
    )
    segment_id = str(segment.get("id", "<unknown>"))
    resolved: list[AudioWaitTiming] = []
    for wait in waits:
        if not isinstance(wait, dict):
            continue
        target = wait.get("target")
        marker = wait.get("marker")
        text_offset = wait.get("text_offset")
        gap_seconds = wait.get("gap_seconds", 0.0)
        if (
            not isinstance(target, str)
            or not target
            or not isinstance(marker, str)
            or not isinstance(text_offset, int)
            or not isinstance(gap_seconds, (int, float))
            or gap_seconds < 0
        ):
            continue
        resolved.append(
            AudioWaitTiming(
                target=target,
                marker=marker,
                seconds=marker_seconds_from_offset(
                    marker=marker,
                    text_offset=text_offset,
                    spans=spans,
                    segment_duration=segment_duration,
                    text=text,
                    segment_id=segment_id,
                ),
                gap_seconds=float(gap_seconds),
            )
        )
    return tuple(sorted(resolved, key=lambda item: item.seconds))


def read_audio_segment_timings(path: Path | None) -> dict[str, AudioSegmentTiming]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RetimeError(f"invalid audio metadata JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RetimeError(f"audio metadata must be a mapping: {path}")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise RetimeError(f"audio metadata missing segments list: {path}")
    timings: dict[str, AudioSegmentTiming] = {}
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise RetimeError(
                f"audio metadata segment must be a mapping: {path}:{index}"
            )
        segment_id = segment.get("id")
        duration = segment.get("duration")
        if not isinstance(segment_id, str) or not segment_id:
            raise RetimeError(f"audio metadata segment missing id: {path}:{index}")
        if not isinstance(duration, (int, float)) or duration < 0:
            raise RetimeError(
                f"audio metadata segment {segment_id!r} has invalid duration"
            )
        timings[segment_id] = AudioSegmentTiming(
            segment_id=segment_id,
            duration=float(duration),
            anchor_seconds=anchor_seconds_from_segment(
                segment=segment,
                metadata_path=path,
            ),
            waits=wait_timings_from_segment(segment=segment, metadata_path=path),
        )
    return timings


def read_audio_segment_durations(path: Path | None) -> dict[str, float]:
    return {
        segment_id: timing.duration
        for segment_id, timing in read_audio_segment_timings(path).items()
    }


def write_audio_presentation_waits(
    metadata_path: Path | None,
    waits_by_segment: dict[str, list[dict[str, Any]]],
) -> None:
    if metadata_path is None or not waits_by_segment or not metadata_path.exists():
        return
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RetimeError(f"invalid audio metadata JSON: {metadata_path}") from exc
    if not isinstance(payload, dict):
        raise RetimeError(f"audio metadata must be a mapping: {metadata_path}")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise RetimeError(f"audio metadata missing segments list: {metadata_path}")
    changed = False
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = segment.get("id")
        if not isinstance(segment_id, str) or segment_id not in waits_by_segment:
            continue
        waits = segment.get("waits")
        if not isinstance(waits, list):
            continue
        concrete_by_marker = {
            wait["marker"]: wait
            for wait in waits_by_segment[segment_id]
            if isinstance(wait.get("marker"), str)
        }
        for wait in waits:
            if not isinstance(wait, dict):
                continue
            marker = wait.get("marker")
            concrete = concrete_by_marker.get(marker)
            if concrete is None:
                continue
            merged = {**wait, **concrete}
            if merged != wait:
                wait.clear()
                wait.update(merged)
                changed = True
    if changed:
        metadata_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def normalize_audio_timings(
    audio_durations: dict[str, float | AudioSegmentTiming] | None,
) -> dict[str, AudioSegmentTiming]:
    timings: dict[str, AudioSegmentTiming] = {}
    for segment_id, value in (audio_durations or {}).items():
        if isinstance(value, AudioSegmentTiming):
            timings[segment_id] = value
        elif isinstance(value, (int, float)):
            timings[segment_id] = AudioSegmentTiming(
                segment_id=segment_id,
                duration=float(value),
                anchor_seconds={},
            )
    return timings


def require_number(mapping: dict[str, Any], key: str, default: float) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, (int, float)) or value < 0:
        raise RetimeError(f"timing.{key} must be a non-negative number")
    return float(value)


def timing_rules_from_manifest(spec: dict[str, Any]) -> TimingRules:
    timing = as_mapping(spec.get("timing"))
    return TimingRules(
        typing_char_delay=require_number(
            timing, "typing_char_delay", TimingRules.typing_char_delay
        ),
        typing_space_delay=require_number(
            timing, "typing_space_delay", TimingRules.typing_space_delay
        ),
        typing_punctuation_delay=require_number(
            timing, "typing_punctuation_delay", TimingRules.typing_punctuation_delay
        ),
        typing_newline_delay=require_number(
            timing, "typing_newline_delay", TimingRules.typing_newline_delay
        ),
        post_enter_pause=require_number(
            timing, "post_enter_pause", TimingRules.post_enter_pause
        ),
        post_command_pause=require_number(
            timing, "post_command_pause", TimingRules.post_command_pause
        ),
        minimum_section_spacing=require_number(
            timing,
            "minimum_section_spacing",
            TimingRules.minimum_section_spacing,
        ),
    )


def read_cast(path: Path) -> tuple[dict[str, Any], list[CastEvent]]:
    if not path.exists():
        raise RetimeError(f"cast file not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RetimeError(f"cast file is empty: {path}")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RetimeError(f"invalid asciinema header in {path}") from exc
    if not isinstance(header, dict):
        raise RetimeError(f"asciinema header must be a mapping: {path}")

    absolute_time = 0.0
    events: list[CastEvent] = []
    for index, line in enumerate(lines[1:]):
        try:
            raw_event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetimeError(f"invalid asciinema event in {path}:{index + 2}") from exc
        if (
            not isinstance(raw_event, list)
            or len(raw_event) != 3
            or not isinstance(raw_event[0], (int, float))
            or not isinstance(raw_event[1], str)
        ):
            raise RetimeError(f"invalid asciinema event in {path}:{index + 2}")
        absolute_time += float(raw_event[0])
        events.append(
            CastEvent(
                index=index,
                absolute_time=absolute_time,
                event_type=raw_event[1],
                payload=raw_event[2],
            )
        )
    return header, events


def write_cast(
    path: Path, header: dict[str, Any], events: list[ScheduledEvent]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(events, key=lambda event: (event.absolute_time, event.order))
    lines = [json.dumps(header, separators=(",", ":"))]
    previous = 0.0
    for event in ordered:
        absolute = max(previous, event.absolute_time)
        delay = round(absolute - previous, 6)
        previous = absolute
        lines.append(
            json.dumps([delay, event.event_type, event.payload], separators=(",", ":"))
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_timeline(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RetimeError(f"timeline file not found: {path}")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetimeError(
                f"invalid timeline event in {path}:{line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise RetimeError(f"timeline event must be a mapping: {path}:{line_number}")
        if not isinstance(event.get("time"), (int, float)):
            raise RetimeError(
                f"timeline event missing numeric time: {path}:{line_number}"
            )
        events.append(event)
    return sorted(events, key=lambda event: float(event["time"]))


def interval_key(event: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("beat", "")),
        str(event.get("action_id", "")),
        str(event.get("chunk_index", "")),
    )


def pair_intervals(
    timeline: list[dict[str, Any]],
    *,
    start_phase: str,
    end_phase: str,
) -> list[TimelineInterval]:
    starts: dict[tuple[str, str, str], dict[str, Any]] = {}
    intervals: list[TimelineInterval] = []
    for event in timeline:
        phase = event.get("phase")
        if phase == start_phase:
            starts[interval_key(event)] = event
        elif phase == end_phase:
            key = interval_key(event)
            start_event = starts.pop(key, None)
            if start_event is None:
                continue
            start = float(start_event["time"])
            end = float(event["time"])
            if end >= start:
                intervals.append(TimelineInterval(start, end, start_event, event))
    return intervals


def pair_hold_intervals(timeline: list[dict[str, Any]]) -> list[TimelineInterval]:
    starts: dict[str, dict[str, Any]] = {}
    intervals: list[TimelineInterval] = []
    for event in timeline:
        phase = event.get("phase")
        beat = str(event.get("beat", ""))
        if phase == "hold_start":
            starts[beat] = event
        elif phase == "hold_end":
            start_event = starts.pop(beat, None)
            if start_event is None:
                continue
            start = float(start_event["time"])
            end = float(event["time"])
            if end >= start:
                intervals.append(TimelineInterval(start, end, start_event, event))
    return intervals


def first_output_event_time(events: list[CastEvent]) -> float | None:
    for event in events:
        if event.event_type == "o" and isinstance(event.payload, str) and event.payload:
            return event.absolute_time
    return None


def first_visible_timeline_anchor(timeline: list[dict[str, Any]]) -> float | None:
    preferred_phases = (
        "caption_start",
        "command_prompt_start",
        "command_run_start",
        "action_start",
        "beat_start",
    )
    for phase in preferred_phases:
        for event in timeline:
            if event.get("beat") == "__setup__" or event.get("phase") != phase:
                continue
            time_value = event.get("time")
            if isinstance(time_value, int | float):
                return float(time_value)
    return None


def align_timeline_to_cast(
    timeline: list[dict[str, Any]], events: list[CastEvent]
) -> list[dict[str, Any]]:
    if not any(event.get("beat") == "__setup__" for event in timeline):
        return timeline
    cast_start = first_output_event_time(events)
    timeline_start = first_visible_timeline_anchor(timeline)
    if cast_start is None or timeline_start is None:
        return timeline
    offset = timeline_start - cast_start
    if offset <= 0:
        return timeline

    aligned: list[dict[str, Any]] = []
    for event in timeline:
        copied = dict(event)
        time_value = copied.get("time")
        if isinstance(time_value, int | float):
            copied["time"] = float(time_value) - offset
        aligned.append(copied)
    return aligned


def token_delay(token: str, rules: TimingRules) -> float:
    if not token or ANSI_RE.fullmatch(token):
        return 0.0
    if token in {"\r", "\n"}:
        return rules.typing_newline_delay
    if token.isspace():
        return rules.typing_space_delay
    if token in "|;&,.:=/\"'{}[]()-_":
        return rules.typing_punctuation_delay
    return rules.typing_char_delay


def tokenize_terminal_payload(payload: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    for match in ANSI_RE.finditer(payload):
        tokens.extend(payload[position : match.start()])
        tokens.append(match.group(0))
        position = match.end()
    tokens.extend(payload[position:])
    return [token for token in tokens if token]


def plain_terminal_text(payload: str) -> str:
    return ANSI_RE.sub("", payload).replace("\r", "")


def has_terminal_screen_control(payload: str) -> bool:
    return bool(ANSI_SCREEN_CONTROL_RE.search(payload))


def is_caption_payload(payload: Any) -> bool:
    if not isinstance(payload, str) or CAPTION_PREFIX not in payload:
        return False
    lines = [
        line.strip()
        for line in plain_terminal_text(payload).splitlines()
        if line.strip()
    ]
    return len(lines) == 1 and lines[0].startswith("# ")


def is_plain_caption_payload(payload: Any) -> bool:
    if not isinstance(payload, str):
        return False
    lines = [
        line.strip()
        for line in plain_terminal_text(payload).splitlines()
        if line.strip()
    ]
    return len(lines) == 1 and lines[0].startswith("# ")


def is_structural_visible_payload(payload: Any) -> bool:
    return isinstance(payload, str) and (
        is_prompt_payload(payload) or is_plain_caption_payload(payload)
    )


def caption_payload_with_continuation(
    events: list[CastEvent], caption_event: CastEvent
) -> tuple[str, tuple[int, ...]]:
    payload = str(caption_event.payload)
    indexes = [caption_event.index]
    for event in events:
        if event.index <= caption_event.index:
            continue
        if abs(event.absolute_time - caption_event.absolute_time) > 0.001:
            break
        if event.event_type != "o" or not isinstance(event.payload, str):
            break
        if plain_terminal_text(event.payload).strip():
            break
        payload += event.payload
        indexes.append(event.index)
    return payload, tuple(indexes)


def caption_continuation_indexes(
    events: list[CastEvent], caption_index: int | None
) -> tuple[int, ...]:
    if caption_index is None:
        return ()
    for event in events:
        if event.index == caption_index:
            return caption_payload_with_continuation(events, event)[1][1:]
    return ()


def add_section_spacing_insertions(
    insertions: list[tuple[float, float]],
    events: list[ScheduledEvent],
    minimum_spacing: float,
) -> None:
    if minimum_spacing <= 0:
        return
    captions = [
        event
        for event in sorted(events, key=lambda item: (item.absolute_time, item.order))
        if event.event_type == "o" and is_caption_payload(event.payload)
    ]
    if len(captions) < 2:
        return
    previous_time = captions[0].absolute_time
    for caption in captions[1:]:
        caption_time = shifted_time(caption.absolute_time, insertions, inclusive=True)
        insertion = max(0.0, (previous_time + minimum_spacing) - caption_time)
        if insertion > 0:
            insertions.append((caption.absolute_time, insertion))
            caption_time += insertion
        previous_time = caption_time


def normalized_command_text(text: str) -> str:
    lines: list[str] = []
    for line in plain_terminal_text(text).splitlines():
        stripped = line.lstrip()
        stripped = PROMPT_PREFIX_RE.sub("", stripped, count=1)
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def is_prompt_payload(payload: str) -> bool:
    return bool(PROMPT_PREFIX_RE.match(plain_terminal_text(payload).lstrip()))


def timeline_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def timeline_non_negative_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
    elif isinstance(value, str):
        try:
            candidate = float(value)
        except ValueError:
            return None
    else:
        return None
    if candidate < 0:
        return None
    return candidate


def command_prompt_payload(
    command: str,
    *,
    prompt: str = "$",
    color: bool = False,
    include_prompt: bool = True,
) -> str:
    lines = command.splitlines()
    if not lines:
        lines = [""]
    payload_parts: list[str] = []
    continuation = False
    omit_next_prompt = not include_prompt
    for line in lines:
        if line == "":
            payload_parts.append("\r\n")
            continuation = False
            continue
        is_continuation = continuation or line[:1].isspace()
        if color:
            if is_continuation:
                payload_parts.append("  \x1b[1m")
            elif omit_next_prompt:
                payload_parts.append(" \x1b[1m")
            else:
                payload_parts.append(f"\x1b[32;1m{prompt}\x1b[0m \x1b[1m")
            payload_parts.append(line)
            payload_parts.append("\x1b[0m\r\n")
        else:
            if is_continuation:
                payload_parts.append("  ")
            elif omit_next_prompt:
                payload_parts.append(" ")
            else:
                payload_parts.append(f"{prompt} ")
            payload_parts.append(line)
            payload_parts.append("\r\n")
        continuation = line.endswith("\\")
        omit_next_prompt = False
    return "".join(payload_parts)


def command_prompt_prefix(
    command: str, *, prompt: str = "$", color: bool = False
) -> str | None:
    lines = command.splitlines()
    if not lines:
        lines = [""]
    first_line = lines[0]
    if first_line == "":
        return None
    is_continuation = first_line[:1].isspace()
    if color:
        if is_continuation:
            return "  \x1b[1m"
        return f"\x1b[32;1m{prompt}\x1b[0m \x1b[1m"
    if is_continuation:
        return "  "
    return f"{prompt} "


def idle_prompt_payload(*, prompt: str = "$", color: bool = False) -> str:
    if color:
        return f"\x1b[32;1m{prompt}\x1b[0m"
    return prompt


def candidate_prompt_events_for_command(
    events: list[CastEvent],
    *,
    start: float,
    end: float,
    command: str,
    prefer_time: float | None = None,
) -> list[CastEvent]:
    if not command:
        return []
    normalized_command = normalized_command_text(command)
    window = [
        event
        for event in events
        if event.event_type == "o"
        and isinstance(event.payload, str)
        and start <= event.absolute_time <= end
    ]

    def with_trailing_prompt_events(
        candidate: list[CastEvent],
        *,
        last_index: int,
    ) -> list[CastEvent]:
        extended = list(candidate)
        for trailing_event in window[last_index + 1 :]:
            if has_terminal_screen_control(trailing_event.payload):
                break
            trailing_plain = plain_terminal_text(trailing_event.payload)
            if trailing_plain.lstrip().startswith("$") or trailing_plain.strip():
                break
            extended.append(trailing_event)
        return extended

    matches: list[list[CastEvent]] = []
    for index, event in enumerate(window):
        plain = plain_terminal_text(event.payload)
        if not is_prompt_payload(event.payload):
            continue
        candidate = [event]
        combined = plain
        if normalized_command == normalized_command_text(combined):
            matches.append(with_trailing_prompt_events(candidate, last_index=index))
            continue
        for next_index, next_event in enumerate(window[index + 1 :], start=index + 1):
            next_plain = plain_terminal_text(next_event.payload)
            next_combined = combined + next_plain
            next_normalized = normalized_command_text(next_combined)
            if is_prompt_payload(next_event.payload):
                current_normalized = normalized_command_text(combined)
                if current_normalized and not normalized_command.startswith(
                    current_normalized + "\n"
                ):
                    break
                candidate.append(next_event)
                combined = next_combined
                if normalized_command == next_normalized:
                    matches.append(
                        with_trailing_prompt_events(candidate, last_index=next_index)
                    )
                    break
                continue
            if next_plain.strip() and not (
                normalized_command.startswith(next_normalized)
                or normalized_command in next_normalized
            ):
                break
            candidate.append(next_event)
            combined = next_combined
            if normalized_command == next_normalized:
                matches.append(
                    with_trailing_prompt_events(candidate, last_index=next_index)
                )
                break
    if not matches:
        return []
    if prefer_time is None:
        return matches[0]
    return min(
        matches, key=lambda candidate: abs(candidate[0].absolute_time - prefer_time)
    )


def prompt_events_for_interval(
    events: list[CastEvent],
    interval: TimelineInterval,
    *,
    fallback_start: float | None = None,
) -> list[CastEvent]:
    start = interval.start - PROMPT_MATCH_TOLERANCE_SECONDS
    end = interval.end + PROMPT_MATCH_TOLERANCE_SECONDS
    command = str(interval.start_event.get("command", "")).strip()
    command_matched = candidate_prompt_events_for_command(
        events,
        start=start,
        end=end,
        command=command,
        prefer_time=interval.start,
    )
    if command_matched:
        return command_matched
    if command:
        if fallback_start is not None and fallback_start < start:
            command_matched = candidate_prompt_events_for_command(
                events,
                start=fallback_start,
                end=end,
                command=command,
                prefer_time=interval.start,
            )
            if command_matched:
                return command_matched
        return []

    matched: list[CastEvent] = []
    collecting = False
    for event in events:
        if event.event_type != "o" or not isinstance(event.payload, str):
            continue
        if event.absolute_time < start:
            continue
        if event.absolute_time > end:
            if collecting:
                break
            continue
        plain = plain_terminal_text(event.payload)
        if not collecting:
            if is_prompt_payload(event.payload):
                collecting = True
                matched.append(event)
            continue
        if plain.strip():
            break
        matched.append(event)
    return matched


def shifted_time(
    timestamp: float,
    insertions: list[tuple[float, float]],
    *,
    inclusive: bool,
) -> float:
    shift = 0.0
    for anchor, amount in insertions:
        if anchor < timestamp or (inclusive and anchor <= timestamp):
            shift += amount
    return timestamp + shift


def shift_before(timestamp: float, insertions: list[tuple[float, float]]) -> float:
    return sum(amount for anchor, amount in insertions if anchor < timestamp)


def timeline_beat_anchor_times(timeline: list[dict[str, Any]]) -> dict[str, float]:
    anchors: dict[str, float] = {}
    for event in timeline:
        beat = event.get("beat")
        if not isinstance(beat, str) or not beat or beat == "__setup__":
            continue
        phase = event.get("phase")
        time_value = event.get("time")
        if not isinstance(time_value, (int, float)):
            continue
        if phase == "beat_start":
            anchors.setdefault(beat, float(time_value))
        elif phase == "caption_start":
            anchors[beat] = float(time_value)
    return anchors


def timeline_beat_end_times(timeline: list[dict[str, Any]]) -> dict[str, float]:
    ends: dict[str, float] = {}
    for event in timeline:
        beat = event.get("beat")
        if not isinstance(beat, str) or not beat or beat == "__setup__":
            continue
        if event.get("phase") != "beat_end":
            continue
        time_value = event.get("time")
        if not isinstance(time_value, (int, float)):
            continue
        ends[beat] = float(time_value)
    return ends


def add_command_anchor_insertions(
    insertions: list[tuple[float, float]],
    timeline: list[dict[str, Any]],
    *,
    prompt_start_by_key: dict[tuple[str, str, str], float],
    audio_timings: dict[str, AudioSegmentTiming],
) -> None:
    if not audio_timings:
        return
    beat_anchor_times = timeline_beat_anchor_times(timeline)
    for event in timeline:
        if event.get("phase") != "command_prompt_start":
            continue
        after = event.get("after")
        if not isinstance(after, str) or not after:
            continue
        beat = event.get("beat")
        if not isinstance(beat, str) or not beat:
            continue
        timing = audio_timings.get(beat)
        if timing is None:
            raise RetimeError(
                f"command waits for {after} in beat {beat!r}, "
                "but audio metadata has no matching segment"
            )
        anchor_seconds = timing.anchor_seconds.get(after)
        if anchor_seconds is None:
            raise RetimeError(
                f"command waits for unknown audio anchor {after} in beat {beat!r}"
            )
        beat_anchor = beat_anchor_times.get(beat)
        if beat_anchor is None:
            raise RetimeError(
                f"command waits for {after} in beat {beat!r}, "
                "but the timeline has no beat or caption start"
            )
        command_anchor = prompt_start_by_key.get(interval_key(event))
        if command_anchor is None:
            time_value = event.get("time")
            if not isinstance(time_value, (int, float)):
                continue
            command_anchor = float(time_value)
        target_time = (
            shifted_time(beat_anchor, insertions, inclusive=True) + anchor_seconds
        )
        current_time = command_anchor + shift_before(command_anchor, insertions)
        insertion = max(0.0, target_time - current_time)
        if insertion > 0:
            insertions.append((command_anchor - 0.000001, insertion))
            insertions.sort()


def add_audio_duration_insertions(
    insertions: list[tuple[float, float]],
    timeline: list[dict[str, Any]],
    *,
    audio_timings: dict[str, AudioSegmentTiming],
) -> None:
    if not audio_timings:
        return
    beat_anchor_times = timeline_beat_anchor_times(timeline)
    beat_end_times = timeline_beat_end_times(timeline)
    for beat, timing in audio_timings.items():
        beat_anchor = beat_anchor_times.get(beat)
        beat_end = beat_end_times.get(beat)
        if beat_anchor is None or beat_end is None:
            continue
        target_end = (
            shifted_time(beat_anchor, insertions, inclusive=True) + timing.duration
        )
        current_end = shifted_time(beat_end, insertions, inclusive=True)
        insertion = max(0.0, target_end - current_end)
        if insertion > 0:
            insertions.append((beat_end, insertion))
            insertions.sort()


def timeline_intervals_by_key(
    intervals: list[TimelineInterval],
) -> dict[tuple[str, str, str], TimelineInterval]:
    return {interval_key(interval.start_event): interval for interval in intervals}


def event_after_same_beat(
    intervals: list[TimelineInterval], index: int
) -> TimelineInterval | None:
    beat = intervals[index].start_event.get("beat")
    for candidate in intervals[index + 1 :]:
        if candidate.start_event.get("beat") == beat:
            return candidate
    return None


def command_output_mode(interval: TimelineInterval | None) -> tuple[str, str]:
    if interval is None:
        return "real", ""
    mode = interval.start_event.get("output_mode", "real")
    if not isinstance(mode, str) or mode not in {"real", "suppress", "fake"}:
        mode = "real"
    fake_output = interval.start_event.get("fake_output", "")
    if not isinstance(fake_output, str):
        fake_output = ""
    return mode, fake_output


def command_timing_mode(interval: TimelineInterval | None) -> str:
    if interval is None:
        return "presentation"
    mode = interval.start_event.get("timing", "presentation")
    if not isinstance(mode, str) or mode not in {"presentation", "realtime"}:
        return "presentation"
    return mode


def is_clear_command_event(event: dict[str, Any] | None) -> bool:
    if event is None:
        return False
    command = event.get("command")
    return isinstance(command, str) and command.strip() == "clear"


def output_span_for_command(
    *,
    events: list[CastEvent],
    prompt_intervals: list[TimelineInterval],
    command_index: int,
    run_interval: TimelineInterval | None,
    beat_anchor_times: dict[str, float],
    beat_end_times: dict[str, float],
    baseline_prompt_start_by_key: dict[tuple[str, str, str], float],
    matched_prompt_keys: set[tuple[str, str, str]],
    caption_source_windows: dict[str, CaptionSourceWindow],
    removed_event_indexes: set[int],
    assigned_event_indexes: set[int],
) -> OutputSpan:
    prompt_interval = prompt_intervals[command_index]
    beat = prompt_interval.start_event.get("beat")
    has_beat_anchor = isinstance(beat, str) and beat in beat_anchor_times
    source_time_offset = 0.0
    caption_window = None
    if isinstance(beat, str):
        caption_window = caption_source_windows.get(beat)
        if (
            caption_window is not None
            and caption_window.time is not None
            and beat in beat_anchor_times
        ):
            source_time_offset = caption_window.time - beat_anchor_times[beat]

    def source_time(timestamp: float) -> float:
        return timestamp + source_time_offset

    if has_beat_anchor:
        window_start = source_time(beat_anchor_times[str(beat)])
    else:
        window_start = source_time(prompt_interval.end)
    for previous_index in range(command_index - 1, -1, -1):
        previous = prompt_intervals[previous_index]
        if previous.start_event.get("beat") != beat:
            continue
        previous_run = previous
        break
    else:
        previous_run = None
    if previous_run is not None:
        window_start = max(window_start, source_time(previous_run.end))

    next_interval = (
        prompt_intervals[command_index + 1]
        if command_index + 1 < len(prompt_intervals)
        else None
    )
    next_command_is_clear = is_clear_command_event(
        next_interval.start_event if next_interval is not None else None
    )
    current_command_is_clear = is_clear_command_event(prompt_interval.start_event)
    if (
        next_interval is not None
        and interval_key(next_interval.start_event) in matched_prompt_keys
    ):
        window_end = baseline_prompt_start_by_key[
            interval_key(next_interval.start_event)
        ]
    elif next_interval is not None and next_interval.start_event.get("beat") == beat:
        window_end = source_time(next_interval.start)
    elif run_interval is not None:
        window_end = (
            source_time(max(run_interval.end, prompt_interval.end))
            + PROMPT_MATCH_TOLERANCE_SECONDS
        )
    else:
        window_end = source_time(prompt_interval.end) + PROMPT_MATCH_TOLERANCE_SECONDS
    if next_interval is None and isinstance(beat, str):
        beat_end = beat_end_times.get(beat)
        if beat_end is not None:
            window_end = max(window_end, source_time(beat_end))
    if caption_window is not None and caption_window.next_time is not None:
        window_end = min(window_end, caption_window.next_time)
    window_end += 0.001

    candidates: list[CastEvent] = []
    for event in events:
        if (
            event.index in removed_event_indexes
            or event.index in assigned_event_indexes
        ):
            continue
        if event.event_type != "o" or not isinstance(event.payload, str):
            continue
        if event.absolute_time < window_start or event.absolute_time > window_end:
            continue
        if is_structural_visible_payload(event.payload):
            continue
        if (
            not current_command_is_clear
            and next_command_is_clear
            and has_terminal_screen_control(event.payload)
        ):
            continue
        candidates.append(event)
    while (
        candidates
        and not plain_terminal_text(candidates[-1].payload).strip()
        and not has_terminal_screen_control(candidates[-1].payload)
    ):
        candidates.pop()
    for event in candidates:
        assigned_event_indexes.add(event.index)
    return OutputSpan(tuple(candidates))


def build_presentation_commands(
    *,
    events: list[CastEvent],
    prompt_intervals: list[TimelineInterval],
    run_intervals: list[TimelineInterval],
    beat_anchor_times: dict[str, float],
    beat_end_times: dict[str, float],
    baseline_prompt_start_by_key: dict[tuple[str, str, str], float],
    matched_prompt_keys: set[tuple[str, str, str]],
    caption_source_windows: dict[str, CaptionSourceWindow],
    prompt_payload_by_key: dict[tuple[str, str, str], str],
    removed_event_indexes: set[int],
) -> list[PresentationCommand]:
    run_by_key = timeline_intervals_by_key(run_intervals)
    assigned_event_indexes: set[int] = set()
    commands: list[PresentationCommand] = []
    for index, prompt_interval in enumerate(prompt_intervals):
        key = interval_key(prompt_interval.start_event)
        run_interval = run_by_key.get(key)
        output_mode, fake_output = command_output_mode(run_interval)
        timing = command_timing_mode(run_interval)
        commands.append(
            PresentationCommand(
                key=key,
                prompt_interval=prompt_interval,
                run_interval=run_interval,
                prompt_payload=prompt_payload_by_key.get(key, ""),
                output_mode=output_mode,
                fake_output=fake_output,
                timing=timing,
                output_span=output_span_for_command(
                    events=events,
                    prompt_intervals=prompt_intervals,
                    command_index=index,
                    run_interval=run_interval,
                    beat_anchor_times=beat_anchor_times,
                    beat_end_times=beat_end_times,
                    baseline_prompt_start_by_key=baseline_prompt_start_by_key,
                    matched_prompt_keys=matched_prompt_keys,
                    caption_source_windows=caption_source_windows,
                    removed_event_indexes=removed_event_indexes,
                    assigned_event_indexes=assigned_event_indexes,
                ),
            )
        )
    return commands


def beat_order_from_timeline(timeline: list[dict[str, Any]]) -> list[str]:
    beats: list[str] = []
    seen: set[str] = set()
    for event in timeline:
        beat = event.get("beat")
        if not isinstance(beat, str) or not beat or beat == "__setup__":
            continue
        if beat in seen:
            continue
        seen.add(beat)
        beats.append(beat)
    return beats


def timeline_events_by_beat(
    timeline: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in timeline:
        beat = event.get("beat")
        if not isinstance(beat, str) or not beat or beat == "__setup__":
            continue
        grouped.setdefault(beat, []).append(event)
    return grouped


def command_order_key(command: PresentationCommand) -> tuple[float, str, str]:
    beat, action_id, chunk_index = command.key
    del beat
    return (command.prompt_interval.start, action_id, chunk_index)


def caption_payload_for_beat(
    *,
    events: list[CastEvent],
    beat_events: list[dict[str, Any]],
    beat_start: float,
    beat_end: float,
    fallback_caption_event: CastEvent | None = None,
) -> tuple[str | None, int | None]:
    caption_texts = [
        str(event.get("caption", "")).strip()
        for event in beat_events
        if event.get("phase") == "caption_start"
        and isinstance(event.get("caption"), str)
        and str(event.get("caption", "")).strip()
    ]
    caption_events = [
        event
        for event in events
        if event.event_type == "o"
        and isinstance(event.payload, str)
        and is_plain_caption_payload(event.payload)
    ]
    for caption_text in caption_texts:
        for event in caption_events:
            if caption_text in plain_terminal_text(event.payload):
                payload, _indexes = caption_payload_with_continuation(events, event)
                return payload, event.index
    for event in caption_events:
        if beat_start <= event.absolute_time <= beat_end + 0.001:
            payload, _indexes = caption_payload_with_continuation(events, event)
            return payload, event.index
    if fallback_caption_event is not None:
        payload, _indexes = caption_payload_with_continuation(
            events, fallback_caption_event
        )
        return payload, fallback_caption_event.index
    if caption_texts:
        return f"{CAPTION_PREFIX}{caption_texts[0]}\x1b[0m\r\n\r\n", None
    return None, None


def hold_seconds_for_beat(hold_intervals: list[TimelineInterval], beat: str) -> float:
    seconds = 0.0
    for interval in hold_intervals:
        if interval.start_event.get("beat") != beat:
            continue
        desired = interval.end_event.get(
            "seconds", interval.start_event.get("seconds", 0.0)
        )
        if isinstance(desired, (int, float)):
            seconds = max(seconds, float(desired))
    return seconds


def raw_events_for_beat(
    *,
    events: list[CastEvent],
    beat_start: float,
    beat_end: float,
    after_index: int | None,
    before_index: int | None,
    removed_event_indexes: set[int],
) -> tuple[CastEvent, ...]:
    raw: list[CastEvent] = []
    for event in events:
        if event.index in removed_event_indexes:
            continue
        if after_index is not None and event.index <= after_index:
            continue
        if before_index is not None and event.index >= before_index:
            continue
        if event.event_type != "o" or not isinstance(event.payload, str):
            continue
        if (
            event.absolute_time < beat_start
            or event.absolute_time > beat_end + PROMPT_MATCH_TOLERANCE_SECONDS
        ):
            continue
        if is_structural_visible_payload(event.payload):
            continue
        raw.append(event)
    while raw and not plain_terminal_text(raw[-1].payload).strip():
        raw.pop()
    return tuple(raw)


def build_presentation_beats(
    *,
    events: list[CastEvent],
    timeline: list[dict[str, Any]],
    commands: list[PresentationCommand],
    hold_intervals: list[TimelineInterval],
    audio_timings: dict[str, AudioSegmentTiming],
    caption_source_windows: dict[str, CaptionSourceWindow],
    removed_event_indexes: set[int],
) -> list[PresentationBeat]:
    commands_by_beat: dict[str, list[PresentationCommand]] = {}
    for command in commands:
        commands_by_beat.setdefault(command.key[0], []).append(command)
    beat_events_by_beat = timeline_events_by_beat(timeline)
    beat_start_times = timeline_beat_anchor_times(timeline)
    beat_end_times = timeline_beat_end_times(timeline)
    beats: list[PresentationBeat] = []
    for beat in beat_order_from_timeline(timeline):
        beat_events = beat_events_by_beat.get(beat, [])
        event_times = [
            float(event["time"])
            for event in beat_events
            if isinstance(event.get("time"), (int, float))
        ]
        beat_start = beat_start_times.get(beat)
        if beat_start is None:
            beat_start = min(event_times, default=0.0)
        beat_end = beat_end_times.get(beat)
        if beat_end is None:
            beat_end = max(event_times, default=beat_start)
        caption_window = caption_source_windows.get(beat)
        caption_payload = caption_window.payload if caption_window is not None else None
        caption_source_index = (
            caption_window.index if caption_window is not None else None
        )
        next_caption_source_index = (
            caption_window.next_index if caption_window is not None else None
        )
        beat_commands = tuple(
            sorted(commands_by_beat.get(beat, []), key=command_order_key)
        )
        raw_beat_start = (
            caption_window.time
            if caption_window is not None and caption_window.time is not None
            else beat_start
        )
        raw_beat_end = (
            caption_window.next_time
            if caption_window is not None and caption_window.next_time is not None
            else beat_end
        )
        raw_events = raw_events_for_beat(
            events=events,
            beat_start=raw_beat_start,
            beat_end=raw_beat_end,
            after_index=caption_source_index,
            before_index=next_caption_source_index,
            removed_event_indexes=removed_event_indexes,
        )
        if (
            not beat_commands
            and not raw_events
            and caption_payload is None
            and beat not in audio_timings
        ):
            continue
        beats.append(
            PresentationBeat(
                beat=beat,
                start=beat_start,
                end=max(beat_end, beat_start),
                caption_payload=caption_payload,
                caption_source_index=caption_source_index,
                commands=beat_commands,
                raw_events=raw_events,
                hold_seconds=hold_seconds_for_beat(hold_intervals, beat),
                audio_timing=audio_timings.get(beat),
            )
        )
    return beats


def caption_source_windows_by_beat(
    *,
    events: list[CastEvent],
    timeline: list[dict[str, Any]],
) -> dict[str, CaptionSourceWindow]:
    beat_events_by_beat = timeline_events_by_beat(timeline)
    beat_start_times = timeline_beat_anchor_times(timeline)
    beat_end_times = timeline_beat_end_times(timeline)
    caption_events = [
        event
        for event in events
        if event.event_type == "o"
        and isinstance(event.payload, str)
        and is_plain_caption_payload(event.payload)
    ]
    caption_by_index = {event.index: event for event in caption_events}
    caption_index = 0
    ordered: list[tuple[str, str | None, int | None, float | None]] = []
    for beat in beat_order_from_timeline(timeline):
        beat_events = beat_events_by_beat.get(beat, [])
        event_times = [
            float(event["time"])
            for event in beat_events
            if isinstance(event.get("time"), (int, float))
        ]
        beat_start = beat_start_times.get(beat, min(event_times, default=0.0))
        beat_end = beat_end_times.get(beat, max(event_times, default=beat_start))
        caption_payload, caption_source_index = caption_payload_for_beat(
            events=events,
            beat_events=beat_events,
            beat_start=beat_start,
            beat_end=beat_end,
            fallback_caption_event=(
                caption_events[caption_index]
                if caption_index < len(caption_events)
                else None
            ),
        )
        if caption_payload is not None:
            caption_index += 1
        caption_event = (
            caption_by_index.get(caption_source_index)
            if caption_source_index is not None
            else None
        )
        ordered.append(
            (
                beat,
                caption_payload,
                caption_source_index,
                caption_event.absolute_time if caption_event is not None else None,
            )
        )

    windows: dict[str, CaptionSourceWindow] = {}
    for index, (beat, payload, source_index, source_time) in enumerate(ordered):
        next_index = None
        next_time = None
        for _, _, candidate_index, candidate_time in ordered[index + 1 :]:
            if candidate_index is not None:
                next_index = candidate_index
                next_time = candidate_time
                break
        windows[beat] = CaptionSourceWindow(
            payload=payload,
            index=source_index,
            time=source_time,
            next_index=next_index,
            next_time=next_time,
        )
    return windows


def schedule_typing(
    *,
    scheduled: list[ScheduledEvent],
    start: float,
    order: float,
    payload: str,
    rules: TimingRules,
    pre_command_pause: float = 0.0,
    pre_command_pause_after_prefix: str | None = None,
    pre_enter_pause: float = 0.0,
) -> float:
    local_time = start
    local_order = order
    tokens = tokenize_terminal_payload(payload)
    emitted_length = 0
    pre_command_pause_applied = (
        pre_command_pause <= 0 or pre_command_pause_after_prefix is None
    )
    pre_command_pause_offset = (
        len(pre_command_pause_after_prefix)
        if pre_command_pause_after_prefix is not None
        else 0
    )
    enter_index = next(
        (
            index
            for index in range(len(tokens) - 1, -1, -1)
            if tokens[index] in {"\r", "\n"}
        ),
        None,
    )
    if (
        enter_index is not None
        and tokens[enter_index] == "\n"
        and enter_index > 0
        and tokens[enter_index - 1] == "\r"
    ):
        enter_index -= 1
    for index, token in enumerate(tokens):
        if not pre_command_pause_applied and emitted_length >= pre_command_pause_offset:
            local_time += pre_command_pause
            pre_command_pause_applied = True
        if index == enter_index:
            local_time += pre_enter_pause
        scheduled.append(
            ScheduledEvent(
                absolute_time=local_time,
                order=local_order,
                event_type="o",
                payload=token,
            )
        )
        emitted_length += len(token)
        local_order += 0.0001
        local_time += token_delay(token, rules)
    return local_time


def schedule_command_output(
    *,
    scheduled: list[ScheduledEvent],
    command: PresentationCommand,
    output_start: float,
    order: float,
) -> float:
    if command.output_mode == "suppress":
        return output_start
    if command.output_mode == "fake":
        fake_output = command.fake_output
        if fake_output and not fake_output.endswith("\n"):
            fake_output = f"{fake_output}\n"
        if not fake_output:
            return output_start
        scheduled.append(
            ScheduledEvent(
                absolute_time=output_start,
                order=order,
                event_type="o",
                payload=fake_output,
            )
        )
        return output_start
    if not command.output_span.events:
        return output_start
    if command.timing == "realtime" and command.run_interval is not None:
        source_start = command.run_interval.start
    else:
        source_start = command.output_span.events[0].absolute_time
    output_end = output_start
    for output_order, event in enumerate(command.output_span.events):
        absolute_time = output_start + max(0.0, event.absolute_time - source_start)
        output_end = max(output_end, absolute_time)
        scheduled.append(
            ScheduledEvent(
                absolute_time=absolute_time,
                order=order + (output_order * 0.0001),
                event_type="o",
                payload=event.payload,
            )
        )
    last_payload = str(command.output_span.events[-1].payload)
    if (
        last_payload
        and not last_payload.endswith(("\r", "\n"))
        and not has_terminal_screen_control(last_payload)
    ):
        output_end += 0.0001
        scheduled.append(
            ScheduledEvent(
                absolute_time=output_end,
                order=order + 1.0,
                event_type="o",
                payload="\r\n",
            )
        )
    return output_end


def adjusted_audio_seconds(
    *,
    base_seconds: float,
    timing: AudioSegmentTiming,
    command_end_by_id: dict[str, float],
    beat_start: float,
    beat_id: str,
    wait_windows: dict[str, dict[str, Any]] | None = None,
    include_boundary_waits: bool = True,
) -> float:
    pause_seconds = 0.0
    for wait in timing.waits:
        if wait.seconds > base_seconds or (
            not include_boundary_waits and wait.seconds >= base_seconds
        ):
            continue
        target_end = command_end_by_id.get(wait.target)
        if target_end is None:
            raise RetimeError(
                f"audio wait {wait.marker} in beat {beat_id!r} references "
                f"unknown or unfinished command id {wait.target!r}"
            )
        wait_presentation_time = beat_start + wait.seconds + pause_seconds
        resume_time = target_end + wait.gap_seconds
        wait_pause = max(0.0, resume_time - wait_presentation_time)
        if wait_windows is not None:
            wait_windows[wait.marker] = {
                "target": wait.target,
                "marker": wait.marker,
                "audio_second": round(wait.seconds, 3),
                "presentation_start": round(wait_presentation_time, 3),
                "presentation_end": round(wait_presentation_time + wait_pause, 3),
                "pause_seconds": round(wait_pause, 3),
                "gap_seconds": round(wait.gap_seconds, 3),
            }
        pause_seconds += wait_pause
    return base_seconds + pause_seconds


def schedule_presentation_beats(
    *,
    events: list[CastEvent],
    beats: list[PresentationBeat],
    rules: TimingRules,
    audio_waits_by_segment: dict[str, list[dict[str, Any]]] | None = None,
) -> list[ScheduledEvent]:
    if not beats:
        scheduled = [
            ScheduledEvent(
                absolute_time=event.absolute_time,
                order=float(event.index),
                event_type=event.event_type,
                payload=event.payload,
            )
            for event in events
        ]
        section_insertions: list[tuple[float, float]] = []
        add_section_spacing_insertions(
            section_insertions,
            scheduled,
            rules.minimum_section_spacing,
        )
        section_insertions.sort()
        if section_insertions:
            scheduled = [
                ScheduledEvent(
                    absolute_time=shifted_time(
                        event.absolute_time,
                        section_insertions,
                        inclusive=True,
                    ),
                    order=event.order,
                    event_type=event.event_type,
                    payload=event.payload,
                )
                for event in scheduled
            ]
        return scheduled

    first_beat_time = beats[0].start
    setup_events = [
        ScheduledEvent(
            absolute_time=event.absolute_time,
            order=float(event.index),
            event_type=event.event_type,
            payload=event.payload,
        )
        for event in events
        if event.absolute_time < first_beat_time
        and not (
            event.event_type == "o"
            and isinstance(event.payload, str)
            and is_structural_visible_payload(event.payload)
        )
    ]
    scheduled: list[ScheduledEvent] = setup_events
    cursor = max((event.absolute_time for event in setup_events), default=0.0)
    next_section_start = cursor

    for beat_index, beat in enumerate(beats):
        beat_start = max(cursor, next_section_start)
        if beat_index == 0:
            beat_start = max(cursor, beat.start)
        beat_cursor = beat_start
        beat_visible_end = beat_start
        if beat.caption_payload is not None:
            caption_offset = max(0.0, beat.start - beat.start)
            caption_time = beat_start + caption_offset
            caption_typing_end = schedule_typing(
                scheduled=scheduled,
                start=caption_time,
                order=-1000.0 + beat_index,
                payload=beat.caption_payload,
                rules=rules,
            )
            beat_cursor = max(beat_cursor, caption_typing_end)
            beat_visible_end = max(beat_visible_end, caption_typing_end)

        command_cursor = beat_cursor
        command_end_by_id: dict[str, float] = {}
        wait_windows: dict[str, dict[str, Any]] = {}
        ready_prompt_payload = ""
        ready_prompt_time: float | None = None
        ready_prompt_scheduled = False
        for command_index, command in enumerate(beat.commands):
            relative_prompt_start = max(0.0, command.prompt_interval.start - beat.start)
            command_start = max(beat_start + relative_prompt_start, command_cursor)
            after = command.prompt_interval.start_event.get("after")
            if isinstance(after, str) and after:
                if beat.audio_timing is None:
                    raise RetimeError(
                        f"command waits for {after} in beat {beat.beat!r}, "
                        "but audio metadata has no matching segment"
                    )
                anchor_seconds = beat.audio_timing.anchor_seconds.get(after)
                if anchor_seconds is None:
                    raise RetimeError(
                        f"command waits for unknown audio anchor {after} "
                        f"in beat {beat.beat!r}"
                    )
                if beat.audio_timing is not None:
                    anchor_seconds = adjusted_audio_seconds(
                        base_seconds=anchor_seconds,
                        timing=beat.audio_timing,
                        command_end_by_id=command_end_by_id,
                        beat_start=beat_start,
                        beat_id=beat.beat,
                        wait_windows=wait_windows,
                        include_boundary_waits=False,
                    )
                command_start = max(command_start, beat_start + anchor_seconds)
            command_text = str(command.prompt_interval.start_event.get("command", ""))
            prompt = str(command.prompt_interval.start_event.get("prompt", "$") or "$")
            color = timeline_bool(command.prompt_interval.start_event.get("color"))
            if (
                command_index == 0
                and beat.caption_payload is not None
                and command_text
                and not ready_prompt_payload
            ):
                ready_prompt_payload = idle_prompt_payload(prompt=prompt, color=color)
                ready_prompt_time = command_cursor + IDLE_PROMPT_EPSILON_SECONDS
                scheduled.append(
                    ScheduledEvent(
                        absolute_time=ready_prompt_time,
                        order=(beat_index * 10_000.0) + command_index - 0.25,
                        event_type="o",
                        payload=ready_prompt_payload,
                    )
                )
                ready_prompt_scheduled = True
                command_cursor = max(
                    command_cursor,
                    ready_prompt_time + IDLE_PROMPT_EPSILON_SECONDS,
                )
                command_start = max(command_start, command_cursor)
                beat_visible_end = max(beat_visible_end, ready_prompt_time)
            use_existing_prompt = (
                bool(ready_prompt_payload)
                and ready_prompt_time is not None
                and command_start >= ready_prompt_time
            )
            if command_text:
                prompt_payload = command_prompt_payload(
                    command_text,
                    prompt=prompt,
                    color=color,
                    include_prompt=not use_existing_prompt,
                )
                pre_command_pause_after_prefix = (
                    command_prompt_prefix(
                        command_text,
                        prompt=prompt,
                        color=color,
                    )
                    if not use_existing_prompt
                    else ""
                )
            else:
                prompt_payload = command.prompt_payload
                pre_command_pause_after_prefix = None
            pre_command_pause = (
                timeline_non_negative_number(
                    command.prompt_interval.start_event.get("pre_command_pause")
                )
                or 0.0
            )
            typing_end = schedule_typing(
                scheduled=scheduled,
                start=command_start,
                order=(beat_index * 10_000.0) + command_index,
                payload=prompt_payload,
                rules=rules,
                pre_command_pause=pre_command_pause,
                pre_command_pause_after_prefix=pre_command_pause_after_prefix,
                pre_enter_pause=(
                    timeline_non_negative_number(
                        command.prompt_interval.start_event.get("pre_enter_pause")
                    )
                    or 0.0
                ),
            )
            post_enter_pause = timeline_non_negative_number(
                command.prompt_interval.start_event.get("post_enter_pause")
            )
            if post_enter_pause is None:
                post_enter_pause = rules.post_enter_pause
            output_start = typing_end + post_enter_pause
            output_end = schedule_command_output(
                scheduled=scheduled,
                command=command,
                output_start=output_start,
                order=(beat_index * 10_000.0) + command_index + 0.5,
            )
            command_id = command.prompt_interval.start_event.get("command_id")
            if isinstance(command_id, str) and command_id:
                if command_id in command_end_by_id:
                    raise RetimeError(
                        f"duplicate command id {command_id!r} in beat {beat.beat!r}"
                    )
                command_end_by_id[command_id] = max(output_start, output_end)
            beat_visible_end = max(
                beat_visible_end, typing_end, output_start, output_end
            )
            show_prompt_after_raw = command.prompt_interval.start_event.get(
                "show_prompt_after"
            )
            show_prompt_after = (
                True
                if show_prompt_after_raw in {None, ""}
                else timeline_bool(show_prompt_after_raw)
            )
            post_command_pause = timeline_non_negative_number(
                command.prompt_interval.start_event.get("post_command_pause")
            )
            if post_command_pause is None:
                post_command_pause = rules.post_command_pause
            if show_prompt_after:
                prompt_ready_time = (
                    max(output_start, output_end) + IDLE_PROMPT_EPSILON_SECONDS
                )
                if command_text:
                    ready_prompt_payload = idle_prompt_payload(
                        prompt=prompt, color=color
                    )
                    ready_prompt_time = prompt_ready_time
                    scheduled.append(
                        ScheduledEvent(
                            absolute_time=ready_prompt_time,
                            order=(beat_index * 10_000.0) + command_index + 0.75,
                            event_type="o",
                            payload=ready_prompt_payload,
                        )
                    )
                    ready_prompt_scheduled = True
                else:
                    ready_prompt_payload = ""
                    ready_prompt_time = None
                    ready_prompt_scheduled = False
                beat_visible_end = max(beat_visible_end, prompt_ready_time)
                command_cursor = (
                    prompt_ready_time + post_command_pause + IDLE_PROMPT_EPSILON_SECONDS
                )
            else:
                ready_prompt_payload = ""
                ready_prompt_time = None
                ready_prompt_scheduled = False
                command_cursor = (
                    max(output_start, output_end)
                    + post_command_pause
                    + IDLE_PROMPT_EPSILON_SECONDS
                )

        if beat.raw_events:
            first_raw_time = beat.raw_events[0].absolute_time
            raw_start = max(
                command_cursor,
                beat_start + max(0.0, first_raw_time - beat.start),
            )
            for raw_order, event in enumerate(beat.raw_events):
                raw_time = raw_start + max(0.0, event.absolute_time - first_raw_time)
                scheduled.append(
                    ScheduledEvent(
                        absolute_time=raw_time,
                        order=(beat_index * 10_000.0) + 5_000.0 + raw_order,
                        event_type=event.event_type,
                        payload=event.payload,
                    )
                )
                command_cursor = max(command_cursor, raw_time)
                beat_visible_end = max(beat_visible_end, raw_time)
            ready_prompt_time = command_cursor + IDLE_PROMPT_EPSILON_SECONDS
            beat_visible_end = max(beat_visible_end, ready_prompt_time)

        audio_duration = beat.audio_timing.duration if beat.audio_timing else 0.0
        if beat.audio_timing is not None:
            audio_duration = adjusted_audio_seconds(
                base_seconds=audio_duration,
                timing=beat.audio_timing,
                command_end_by_id=command_end_by_id,
                beat_start=beat_start,
                beat_id=beat.beat,
                wait_windows=wait_windows,
            )
        if wait_windows and audio_waits_by_segment is not None:
            audio_waits_by_segment[beat.beat] = list(wait_windows.values())
        beat_end = max(
            beat_start + audio_duration,
            command_cursor + beat.hold_seconds,
        )
        if (
            ready_prompt_payload
            and ready_prompt_time is not None
            and not ready_prompt_scheduled
            and beat_end - command_cursor >= IDLE_PROMPT_MIN_SECONDS
        ):
            scheduled.append(
                ScheduledEvent(
                    absolute_time=ready_prompt_time,
                    order=(beat_index * 10_000.0) + 9_998.0,
                    event_type="o",
                    payload=ready_prompt_payload,
                )
            )
        if beat_end > command_cursor:
            scheduled.append(
                ScheduledEvent(
                    absolute_time=beat_end,
                    order=(beat_index * 10_000.0) + 9_999.0,
                    event_type="o",
                    payload="",
                )
            )
        cursor = beat_end
        next_section_start = beat_visible_end + rules.minimum_section_spacing
    return scheduled


def retime_events(
    events: list[CastEvent],
    timeline: list[dict[str, Any]],
    rules: TimingRules,
    audio_durations: dict[str, float | AudioSegmentTiming] | None = None,
    audio_waits_by_segment: dict[str, list[dict[str, Any]]] | None = None,
) -> list[ScheduledEvent]:
    audio_timings = normalize_audio_timings(audio_durations)
    timeline = align_timeline_to_cast(timeline, events)
    prompt_intervals = pair_intervals(
        timeline,
        start_phase="command_prompt_start",
        end_phase="command_prompt_end",
    )
    run_intervals = pair_intervals(
        timeline,
        start_phase="command_run_start",
        end_phase="command_run_end",
    )
    hold_intervals = pair_hold_intervals(timeline)
    removed_event_indexes: set[int] = set()
    baseline_prompt_start_by_key: dict[tuple[str, str, str], float] = {}
    matched_prompt_keys: set[tuple[str, str, str]] = set()
    prompt_payload_by_key: dict[tuple[str, str, str], str] = {}
    beat_anchor_times = timeline_beat_anchor_times(timeline)
    beat_end_times = timeline_beat_end_times(timeline)
    caption_source_windows = caption_source_windows_by_beat(
        events=events,
        timeline=timeline,
    )
    for caption_window in caption_source_windows.values():
        removed_event_indexes.update(
            caption_continuation_indexes(events, caption_window.index)
        )

    for interval in prompt_intervals:
        beat = interval.start_event.get("beat")
        fallback_start = beat_anchor_times.get(beat) if isinstance(beat, str) else None
        prompt_events = prompt_events_for_interval(
            events,
            interval,
            fallback_start=fallback_start,
        )
        if not prompt_events:
            prompt_events = candidate_prompt_events_for_command(
                events,
                start=0.0,
                end=float("inf"),
                command=str(interval.start_event.get("command", "")).strip(),
                prefer_time=interval.start,
            )
        key = interval_key(interval.start_event)
        if prompt_events:
            matched_prompt_keys.add(key)
            baseline_prompt_start_by_key[key] = prompt_events[0].absolute_time
            prompt_payload_by_key[key] = "".join(
                str(event.payload) for event in prompt_events
            )
        else:
            baseline_prompt_start_by_key[key] = interval.start
        for event in prompt_events:
            removed_event_indexes.add(event.index)

    if prompt_intervals:
        for event in events:
            if (
                event.event_type == "o"
                and isinstance(event.payload, str)
                and is_prompt_payload(event.payload)
            ):
                removed_event_indexes.add(event.index)

    commands = build_presentation_commands(
        events=events,
        prompt_intervals=prompt_intervals,
        run_intervals=run_intervals,
        beat_anchor_times=beat_anchor_times,
        beat_end_times=beat_end_times,
        baseline_prompt_start_by_key=baseline_prompt_start_by_key,
        matched_prompt_keys=matched_prompt_keys,
        caption_source_windows=caption_source_windows,
        prompt_payload_by_key=prompt_payload_by_key,
        removed_event_indexes=removed_event_indexes,
    )
    for command in commands:
        for event in command.output_span.events:
            removed_event_indexes.add(event.index)

    beats = build_presentation_beats(
        events=events,
        timeline=timeline,
        commands=commands,
        hold_intervals=hold_intervals,
        audio_timings=audio_timings,
        caption_source_windows=caption_source_windows,
        removed_event_indexes=removed_event_indexes,
    )
    for beat in beats:
        if beat.caption_source_index is not None:
            removed_event_indexes.add(beat.caption_source_index)
    if beats:
        for event in events:
            if (
                event.event_type == "o"
                and isinstance(event.payload, str)
                and is_plain_caption_payload(event.payload)
            ):
                removed_event_indexes.add(event.index)

    return schedule_presentation_beats(
        events=[event for event in events if event.index not in removed_event_indexes],
        beats=beats,
        rules=rules,
        audio_waits_by_segment=audio_waits_by_segment,
    )


def retime_cast(
    *,
    cast_path: Path,
    timeline_path: Path,
    output_path: Path,
    rules: TimingRules,
    audio_durations: dict[str, float | AudioSegmentTiming] | None = None,
) -> None:
    header, events = read_cast(cast_path)
    timeline = read_timeline(timeline_path)
    if not events:
        raise RetimeError(f"cast contains no events: {cast_path}")
    scheduled = retime_events(events, timeline, rules, audio_durations)
    write_cast(output_path, header, scheduled)


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
        spec = load_recording_spec_from_hydra_cfg(cfg)
        action = config.get("step") or config.get("action", "retime")
        if action == "build":
            action = "retime"
        if action not in {"retime", "check"}:
            raise RetimeError("action must be 'retime' or 'check'")
        cast_override = config.get("cast")
        timeline_override = config.get("timeline")
        audio_metadata_override = config.get("audio_metadata")
        output_override = config.get("output")
        for name, value in [
            ("cast", cast_override),
            ("timeline", timeline_override),
            ("audio_metadata", audio_metadata_override),
            ("output", output_override),
        ]:
            if value is not None and not isinstance(value, str):
                raise RetimeError(f"{name} must be a string or null")
        cast_path = (
            relative_path(cast_override)
            if cast_override
            else cast_path_from_manifest(spec)
        )
        timeline_path = (
            relative_path(timeline_override)
            if timeline_override
            else timeline_path_for_cast(cast_path)
        )
        output_path = (
            relative_path(output_override)
            if output_override
            else output_path_from_manifest(spec, cast_path)
        )
        audio_metadata_path = (
            relative_path(audio_metadata_override)
            if audio_metadata_override
            else audio_metadata_path_from_manifest(spec)
        )
        audio_timings = read_audio_segment_timings(audio_metadata_path)
        rules = timing_rules_from_manifest(spec)
        header, events = read_cast(cast_path)
        timeline = read_timeline(timeline_path)
        if action == "check":
            if not events:
                raise RetimeError(f"cast contains no events: {cast_path}")
            if not timeline:
                raise RetimeError(f"timeline contains no events: {timeline_path}")
            require_fresh_retimed_cast(
                cast_path=cast_path,
                timeline_path=timeline_path,
                output_path=output_path,
                audio_metadata_path=audio_metadata_path,
            )
            pass_line(
                f"{spec['_recording_id']} retime "
                f"cast={display_path(cast_path)} "
                f"timeline={display_path(timeline_path)} "
                f"output={display_path(output_path)} "
                f"audio_segments={len(audio_timings)}"
            )
            return 0
        waits_by_segment: dict[str, list[dict[str, Any]]] = {}
        scheduled = retime_events(
            events,
            timeline,
            rules,
            audio_timings,
            audio_waits_by_segment=waits_by_segment,
        )
        write_audio_presentation_waits(audio_metadata_path, waits_by_segment)
        write_cast(output_path, header, scheduled)
        pass_line(f"wrote retimed cast: {display_path(output_path)}")
        return 0
    except StudioConfigError as exc:
        raise RetimeError(str(exc)) from exc


@hydra.main(
    version_base=None,
    config_path=str(CONFIG_DIR),
    config_name=STUDIO_CONFIG_NAME,
)
def main(cfg: DictConfig) -> None:
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except RetimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
