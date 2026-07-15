#!/usr/bin/env python3
"""Generate cached narration audio from OmegaFlow scripts."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

import hydra
from omegaconf import DictConfig, OmegaConf

from .presentation_schema import (
    NarrationAudioMemberV2,
    NarrationAudioMetadataV3,
    NarrationAudioTakeV3,
    NarrationTimestampAnchorV1,
    NarrationTimestampMemberV1,
    NarrationTimestampSidecarV1,
    NarrationTimestampWaitV1,
    NarrationTimestampWordV1,
)
from .recording_plan import NarrationTakePlan, RecordingPlan

from .studio_config import (
    CONFIG_DIR,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    container_from_hydra_cfg,
    load_configured_env_file,
    load_env_file,
    load_recording_spec,
    load_recording_spec_from_hydra_cfg,
    project_root,
    studio_data_dir_from_config,
    studio_directive_blocks as parse_studio_directive_blocks,
)
from .terminal_style import ANSI_CYAN_BOLD, ANSI_GREEN_BOLD
from .tool_progress import LogProgressRenderer, ProgressBarRenderer, ToolProgress


OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
SUPPORTED_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
SUPPORTED_TIMESTAMP_GRANULARITIES = {"word", "segment"}
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_TIMESTAMP_GRANULARITIES = ("word", "segment")
DEFAULT_OPENAI_TTS_USD_PER_1M_CHARACTERS = 15.0
DEFAULT_OPENAI_TRANSCRIPTION_USD_PER_MINUTE = 0.006


class AudioError(RuntimeError):
    pass


AUDIO_PROGRESS = ToolProgress("audio")
StatusSummary = tuple[str, str, str]


def status_line(
    status: str,
    message: str,
    *,
    color: str,
    current: int | None = None,
    total: int | None = None,
) -> None:
    AUDIO_PROGRESS.status(
        status,
        message,
        color=color,
        current=current,
        total=total,
    )


def pass_line(
    message: str,
    *,
    current: int | None = None,
    total: int | None = None,
) -> None:
    status_line("pass", message, color=ANSI_GREEN_BOLD, current=current, total=total)


def info_line(
    message: str,
    *,
    current: int | None = None,
    total: int | None = None,
) -> None:
    status_line("info", message, color=ANSI_CYAN_BOLD, current=current, total=total)


@dataclass(frozen=True)
class NarrationSegment:
    segment_id: str
    heading: str
    text: str


@dataclass(frozen=True)
class ScriptNarration:
    scene_title: str
    segments: list[NarrationSegment]


@dataclass(frozen=True)
class AudioSettings:
    enabled: bool
    provider: str
    env: str
    model: str
    voice: str
    format: str
    cache_dir: Path
    env_file: Path | None = None
    env_override: bool = False
    instructions: str | None = None
    tts_usd_per_1m_characters: float = DEFAULT_OPENAI_TTS_USD_PER_1M_CHARACTERS


@dataclass(frozen=True)
class TranscriptionSettings:
    model: str
    timestamp_granularities: tuple[str, ...]
    usd_per_minute: float = DEFAULT_OPENAI_TRANSCRIPTION_USD_PER_MINUTE


@dataclass(frozen=True)
class AudioPlanItem:
    segment: NarrationSegment
    cache_key: str
    output_path: Path


@dataclass(frozen=True)
class NarrationTakeAudioPlanItem:
    take: NarrationTakePlan
    cache_key: str
    output_path: Path
    index_path: Path


@dataclass(frozen=True)
class AudioBillingSummary:
    generated_segments: int
    billable_characters: int
    estimated_cost_usd: float


@dataclass(frozen=True)
class AudioTranscriptionBillingSummary:
    generated_timestamp_files: int
    audio_seconds: float
    estimated_cost_usd: float


def load_manifest(
    recording_id: str, overrides: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    try:
        return load_recording_spec(recording_id, overrides)
    except StudioConfigError as exc:
        raise AudioError(str(exc)) from exc


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AudioError(f"recording config field {field!r} must be a mapping")
    return value


def require_string(mapping: dict[str, Any], key: str, *, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise AudioError(f"{field}.{key} must be a non-empty string")
    return value


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return project_root() / candidate


def normalize_narration_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def studio_directive_blocks(script_text: str) -> list[dict[str, Any]]:
    try:
        return parse_studio_directive_blocks(script_text)
    except StudioConfigError as exc:
        raise AudioError(str(exc)) from exc


def scene_title_from_directive(value: object) -> str:
    if isinstance(value, str):
        title = value.strip()
    elif isinstance(value, dict):
        title = str(value.get("title") or "").strip()
    else:
        title = ""
    if not title:
        raise AudioError("studio-directive scene must define a non-empty title")
    return title


def beat_from_directive(value: dict[str, Any]) -> NarrationSegment:
    segment_id = value.get("id")
    heading = value.get("heading")
    narration = value.get("narration")
    if not segment_id.strip():
        raise AudioError("studio-directive beat.id must be a non-empty string")
    if not heading.strip():
        raise AudioError("studio-directive beat.heading must be a non-empty string")
    if not narration.strip():
        raise AudioError("studio-directive beat.narration must be a non-empty string")
    return NarrationSegment(
        segment_id=segment_id.strip(),
        heading=heading.strip(),
        text=normalize_narration_text(narration),
    )


def beat_values_from_directive(block: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if block.get("beat") is not None:
        values.append(block["beat"])
    values.extend(block.get("beats") or [])
    return values


def extract_directive_narration(script_text: str) -> ScriptNarration:
    scene_title = ""
    segments: list[NarrationSegment] = []
    seen_ids: set[str] = set()
    for block in studio_directive_blocks(script_text):
        if "scene" in block:
            if scene_title:
                raise AudioError("duplicate studio-directive scene")
            scene_title = scene_title_from_directive(block["scene"])
        for beat_value in beat_values_from_directive(block):
            segment = beat_from_directive(beat_value)
            if segment.segment_id in seen_ids:
                raise AudioError(f"duplicate narration beat id: {segment.segment_id}")
            seen_ids.add(segment.segment_id)
            segments.append(segment)
    return ScriptNarration(scene_title=scene_title, segments=segments)


def extract_script_narration(script_text: str) -> ScriptNarration:
    script_narration = extract_directive_narration(script_text)
    if script_narration.scene_title or script_narration.segments:
        return script_narration
    if re.search(r"(?m)^(Scene:|Beat: `|Narration:)", script_text):
        raise AudioError(
            "machine-readable script fields must be inside "
            "```studio-directive``` blocks"
        )
    return script_narration


def extract_narration_segments(script_text: str) -> list[NarrationSegment]:
    return extract_script_narration(script_text).segments


def audio_settings(spec: dict[str, Any]) -> AudioSettings:
    audio = as_mapping(spec.get("audio"), field="audio")
    provider = audio.get("provider", "openai")
    if provider != "openai":
        raise AudioError(f"unsupported audio provider: {provider}")
    fmt = require_string(audio, "format", field="audio")
    if fmt not in SUPPORTED_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_FORMATS))
        raise AudioError(f"audio.format must be one of: {supported}")
    instructions = audio.get("instructions")
    if instructions is not None and (
        not isinstance(instructions, str) or not instructions
    ):
        raise AudioError("audio.instructions must be a non-empty string")
    env_file_value = audio.get("env_file")
    if env_file_value is None:
        env_file = None
    else:
        if not isinstance(env_file_value, str) or not env_file_value:
            raise AudioError("audio.env_file must be a non-empty string or null")
        env_file = relative_path(env_file_value)
    env_override = audio.get("env_override", False)
    if not isinstance(env_override, bool):
        raise AudioError("audio.env_override must be a boolean")
    cache_dir = audio.get("cache_dir", "recordings/.omegaflow/cache/audio")
    if not isinstance(cache_dir, str) or not cache_dir:
        raise AudioError("audio.cache_dir must be a non-empty string")
    billing = as_mapping(audio.get("billing"), field="audio.billing")
    tts_usd_per_1m_characters = billing.get(
        "tts_usd_per_1m_characters",
        DEFAULT_OPENAI_TTS_USD_PER_1M_CHARACTERS,
    )
    if isinstance(tts_usd_per_1m_characters, bool) or not isinstance(
        tts_usd_per_1m_characters,
        (int, float),
    ):
        raise AudioError("audio.billing.tts_usd_per_1m_characters must be a number")
    if tts_usd_per_1m_characters < 0:
        raise AudioError("audio.billing.tts_usd_per_1m_characters must not be negative")
    return AudioSettings(
        enabled=bool(audio.get("enabled", False)),
        provider=provider,
        env=require_string(audio, "env", field="audio"),
        model=require_string(audio, "model", field="audio"),
        voice=require_string(audio, "voice", field="audio"),
        format=fmt,
        env_file=env_file,
        env_override=env_override,
        instructions=instructions,
        cache_dir=relative_path(cache_dir),
        tts_usd_per_1m_characters=float(tts_usd_per_1m_characters),
    )


def load_audio_env_file(settings: AudioSettings) -> dict[str, str]:
    if settings.env_file is None:
        return {}
    try:
        return load_env_file(settings.env_file, override=settings.env_override)
    except StudioConfigError as exc:
        raise AudioError(str(exc)) from exc


def audio_environment(
    settings: AudioSettings,
    environ: dict[str, str] | None,
) -> dict[str, str]:
    if environ is not None:
        return environ
    load_audio_env_file(settings)
    return os.environ


def transcription_settings(spec: dict[str, Any]) -> TranscriptionSettings:
    audio = as_mapping(spec.get("audio"), field="audio")
    transcription = as_mapping(audio.get("transcription"), field="audio.transcription")
    model = transcription.get("model", DEFAULT_TRANSCRIPTION_MODEL)
    if not isinstance(model, str) or not model:
        raise AudioError("audio.transcription.model must be a non-empty string")
    granularities = transcription.get(
        "timestamp_granularities", list(DEFAULT_TIMESTAMP_GRANULARITIES)
    )
    if isinstance(granularities, str):
        granularities = [granularities]
    if not isinstance(granularities, list) or not granularities:
        raise AudioError(
            "audio.transcription.timestamp_granularities must be a non-empty list"
        )
    normalized: list[str] = []
    for granularity in granularities:
        if not isinstance(granularity, str):
            raise AudioError(
                "audio.transcription.timestamp_granularities entries must be strings"
            )
        if granularity not in SUPPORTED_TIMESTAMP_GRANULARITIES:
            supported = ", ".join(sorted(SUPPORTED_TIMESTAMP_GRANULARITIES))
            raise AudioError(
                "audio.transcription.timestamp_granularities entries must be "
                f"one of: {supported}"
            )
        if granularity not in normalized:
            normalized.append(granularity)
    billing = as_mapping(audio.get("billing"), field="audio.billing")
    usd_per_minute = billing.get(
        "transcription_usd_per_minute",
        DEFAULT_OPENAI_TRANSCRIPTION_USD_PER_MINUTE,
    )
    if isinstance(usd_per_minute, bool) or not isinstance(
        usd_per_minute,
        (int, float),
    ):
        raise AudioError("audio.billing.transcription_usd_per_minute must be a number")
    if usd_per_minute < 0:
        raise AudioError(
            "audio.billing.transcription_usd_per_minute must not be negative"
        )
    return TranscriptionSettings(
        model=model,
        timestamp_granularities=tuple(normalized),
        usd_per_minute=float(usd_per_minute),
    )


def script_path_from_manifest(spec: dict[str, Any]) -> Path:
    script = spec.get("script")
    if not isinstance(script, str) or not script:
        raise AudioError("recording config field 'script' must be a non-empty string")
    return relative_path(script)


def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def narration_config_path(
    recording_id: str,
    spec: dict[str, Any] | None = None,
) -> Path:
    if spec is not None:
        config = spec.get("_studio_config")
        if isinstance(config, dict):
            return (
                studio_data_dir_from_config(config)
                / "generated"
                / "narration"
                / f"{recording_id}.yaml"
            )
    return (
        studio_data_dir_from_config(None)
        / "generated"
        / "narration"
        / f"{recording_id}.yaml"
    )


def yaml_block_scalar(text: str, *, indent: int) -> str:
    prefix = " " * indent
    wrapped = textwrap.wrap(text, width=78 - indent) or [""]
    return "\n".join(f"{prefix}{line}" for line in wrapped)


def narration_yaml_text(
    recording_id: str,
    script_path: Path,
    source_digest: str,
    script_narration: ScriptNarration,
) -> str:
    lines = [
        "# @package narration",
        "# Generated by OmegaFlow action=sync_narration.",
        "# Source of truth: edit the Markdown script, then rerun the sync action.",
        "",
        f"source_script: {json.dumps(display_path(script_path))}",
        f"source_sha256: {json.dumps(source_digest)}",
        "generated: true",
        "scene:",
        f"  id: {json.dumps(recording_id)}",
        f"  title: {json.dumps(script_narration.scene_title)}",
        "beats:",
    ]
    for segment in script_narration.segments:
        lines.extend(
            [
                f"  - id: {json.dumps(segment.segment_id)}",
                f"    heading: {json.dumps(segment.heading)}",
                "    text: >-",
                yaml_block_scalar(segment.text, indent=6),
            ]
        )
    return "\n".join(lines) + "\n"


def recording_beat_ids(spec: dict[str, Any]) -> list[str]:
    beats = spec.get("beats")
    if not isinstance(beats, list):
        return []
    ids: list[str] = []
    for beat in beats:
        if isinstance(beat, dict) and isinstance(beat.get("id"), str):
            ids.append(beat["id"])
    return ids


def validate_script_narration_against_recording(
    script_narration: ScriptNarration,
    spec: dict[str, Any],
) -> None:
    known = {"overview", *recording_beat_ids(spec)}
    missing = [
        segment.segment_id
        for segment in script_narration.segments
        if segment.segment_id not in known
    ]
    if missing:
        raise AudioError(
            "narration script references unknown beat id(s): " + ", ".join(missing)
        )


def sync_narration_config(
    spec: dict[str, Any], *, output_path: Path | None = None
) -> Path:
    recording_id = require_string(spec, "_recording_id", field="recording")
    script_path = script_path_from_manifest(spec)
    if not script_path.exists():
        raise AudioError(f"script not found: {script_path}")
    source_digest = sha256_file(script_path)
    script_narration = extract_script_narration(script_path.read_text(encoding="utf-8"))
    if not script_narration.segments:
        raise AudioError(f"no Narration blocks found in {script_path}")
    validate_script_narration_against_recording(script_narration, spec)
    target = output_path or narration_config_path(recording_id, spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        narration_yaml_text(recording_id, script_path, source_digest, script_narration),
        encoding="utf-8",
    )
    return target


def narration_mapping_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    narration = spec.get("narration")
    if not isinstance(narration, dict) or not narration:
        raise AudioError(
            "recording narration is missing from the Markdown recording script"
        )
    return narration


def load_narration_segments(spec: dict[str, Any]) -> list[NarrationSegment]:
    narration = narration_mapping_from_spec(spec)
    source_script = narration.get("source_script")
    source_digest = narration.get("source_sha256")
    if isinstance(source_script, str) and isinstance(source_digest, str):
        script_path = relative_path(source_script)
        if not script_path.exists():
            raise AudioError(f"narration source script not found: {source_script}")
        actual_digest = sha256_file(script_path)
        if actual_digest != source_digest:
            raise AudioError(
                "recording narration source hash is stale; rebuild from the "
                "Markdown recording script"
            )

    beats = narration.get("beats")
    if not isinstance(beats, list) or not beats:
        raise AudioError("recording narration config must contain non-empty beats")
    segments: list[NarrationSegment] = []
    seen: set[str] = set()
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            raise AudioError(f"narration.beats.{index} must be a mapping")
        segment_id = beat.get("id")
        heading = beat.get("heading")
        text = beat.get("text")
        if not isinstance(segment_id, str) or not segment_id:
            raise AudioError(f"narration.beats.{index}.id must be a non-empty string")
        if segment_id in seen:
            raise AudioError(f"duplicate narration beat id: {segment_id}")
        seen.add(segment_id)
        if not isinstance(heading, str) or not heading:
            raise AudioError(
                f"narration.beats.{index}.heading must be a non-empty string"
            )
        if not isinstance(text, str) or not text.strip():
            raise AudioError(f"narration.beats.{index}.text must be a non-empty string")
        segments.append(
            NarrationSegment(
                segment_id=segment_id,
                heading=heading,
                text=normalize_narration_text(text),
            )
        )
    return segments


def segment_cache_key(segment: NarrationSegment, settings: AudioSettings) -> str:
    payload = {
        "provider": settings.provider,
        "model": settings.model,
        "voice": settings.voice,
        "format": settings.format,
        "instructions": settings.instructions,
        "text": normalize_narration_text(segment.text),
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:16]


def narration_take_cache_key(
    take: NarrationTakePlan,
    settings: AudioSettings,
    *,
    timing_settings: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "provider": settings.provider,
        "model": settings.model,
        "voice": settings.voice,
        "format": settings.format,
        "instructions": settings.instructions,
        "timing_settings": dict(timing_settings or {}),
        "ordered_beat_ids": [member.beat_id for member in take.members],
        "members": [
            {
                "beat_id": member.beat_id,
                "text": member.text,
                "text_start": member.text_start,
                "text_end": member.text_end,
            }
            for member in take.members
        ],
        "synthesis_text": take.synthesis_text,
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def narration_take_filename_id(take_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", take_id).strip("-.")
    if value:
        return value
    return sha256(take_id.encode("utf-8")).hexdigest()[:12]


def plan_narration_take_audio(
    recording_id: str,
    takes: tuple[NarrationTakePlan, ...],
    settings: AudioSettings,
    *,
    timing_settings: Mapping[str, Any] | None = None,
) -> list[NarrationTakeAudioPlanItem]:
    recording_dir = settings.cache_dir / recording_id
    items: list[NarrationTakeAudioPlanItem] = []
    for take in takes:
        cache_key = narration_take_cache_key(
            take, settings, timing_settings=timing_settings
        )
        safe_id = narration_take_filename_id(take.id)
        items.append(
            NarrationTakeAudioPlanItem(
                take=take,
                cache_key=cache_key,
                output_path=recording_dir
                / f"{safe_id}-{cache_key}.{settings.format}",
                index_path=recording_dir / f"{safe_id}.take.json",
            )
        )
    return items


def narration_take_index_payload(item: NarrationTakeAudioPlanItem) -> dict[str, Any]:
    return {
        "version": 1,
        "take_id": item.take.id,
        "explicit": item.take.explicit,
        "ordered_beat_ids": [member.beat_id for member in item.take.members],
        "cache_key": item.cache_key,
    }


def narration_take_review_warning(
    item: NarrationTakeAudioPlanItem,
    previous: object,
) -> dict[str, Any] | None:
    if not item.take.explicit or not isinstance(previous, dict):
        return None
    if previous.get("take_id") != item.take.id:
        return None
    old_order = previous.get("ordered_beat_ids")
    new_order = [member.beat_id for member in item.take.members]
    if old_order == new_order:
        return None
    if not isinstance(old_order, list) or not all(
        isinstance(beat_id, str) for beat_id in old_order
    ):
        return None
    return {
        "code": "NARRATION_TAKE_REVIEW",
        "take_id": item.take.id,
        "previous_beat_ids": old_order,
        "current_beat_ids": new_order,
    }


def load_narration_take_index(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_narration_take_index(item: NarrationTakeAudioPlanItem) -> None:
    item.index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = item.index_path.with_suffix(item.index_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(narration_take_index_payload(item), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(item.index_path)


def _structured_payload(value: object) -> dict[str, Any]:
    payload = OmegaConf.to_container(
        OmegaConf.structured(value), resolve=True, enum_to_str=True
    )
    if not isinstance(payload, dict):
        raise AudioError("generated audio payload must be a mapping")
    return payload


def _timestamp_source_ms(
    text_offset: int,
    words: list[NarrationTimestampWordV1],
    *,
    duration_ms: int,
) -> int:
    if not words:
        raise AudioError("cannot resolve narration marker without word timestamps")
    if text_offset <= words[0].text_start:
        return words[0].start_ms
    for word in words:
        if text_offset <= word.text_end:
            width = word.text_end - word.text_start
            if width <= 0:
                return word.start_ms
            fraction = (text_offset - word.text_start) / width
            fraction = min(1.0, max(0.0, fraction))
            return round(word.start_ms + fraction * (word.end_ms - word.start_ms))
    return duration_ms


def _wait_timestamp_source_ms(
    text_offset: int,
    words: list[NarrationTimestampWordV1],
    *,
    synthesis_text: str,
    duration_ms: int,
) -> int:
    """Resolve waits inside inter-word silence, away from either word boundary."""
    previous_word: NarrationTimestampWordV1 | None = None
    for word in words:
        if word.text_start < text_offset:
            previous_word = word
            continue
        separator = synthesis_text[text_offset : word.text_start]
        if all(character.isspace() for character in separator):
            if previous_word is None:
                return 0
            return previous_word.end_ms + (
                word.start_ms - previous_word.end_ms
            ) // 2
    trailing_text = synthesis_text[text_offset:]
    if all(character.isspace() for character in trailing_text):
        return duration_ms
    return _timestamp_source_ms(text_offset, words, duration_ms=duration_ms)


def narration_timestamp_sidecar_payload(
    take: NarrationTakePlan,
    *,
    duration_ms: int,
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise AudioError("narration take duration must be an integer")
    if duration_ms < 0:
        raise AudioError("narration take duration must be non-negative")
    typed_words: list[NarrationTimestampWordV1] = []
    previous_text_end = 0
    previous_time_end = 0
    for index, word in enumerate(words):
        if not isinstance(word, dict):
            raise AudioError(f"narration timestamp word {index} must be a mapping")
        try:
            text = word["text"]
            text_start = word["text_start"]
            text_end = word["text_end"]
            start_ms = word["start_ms"]
            end_ms = word["end_ms"]
            if not isinstance(text, str) or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (text_start, text_end, start_ms, end_ms)
            ):
                raise TypeError
            typed = NarrationTimestampWordV1(
                text=text,
                text_start=text_start,
                text_end=text_end,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        except (KeyError, TypeError) as exc:
            raise AudioError(f"invalid narration timestamp word {index}") from exc
        if (
            typed.text_start < previous_text_end
            or typed.text_end <= typed.text_start
            or typed.end_ms <= typed.start_ms
            or typed.start_ms < previous_time_end
            or typed.end_ms > duration_ms
            or typed.text_end > len(take.synthesis_text)
        ):
            raise AudioError(f"invalid narration timestamp word ordering at {index}")
        if take.synthesis_text[typed.text_start : typed.text_end] != typed.text:
            raise AudioError(f"narration timestamp word {index} does not match take text")
        typed_words.append(typed)
        previous_text_end = typed.text_end
        previous_time_end = typed.end_ms

    members = [
        NarrationTimestampMemberV1(
            beat_id=member.beat_id,
            text_start=member.text_start,
            text_end=member.text_end,
            source_start_ms=(
                0
                if index == 0
                else _timestamp_source_ms(
                    member.text_start, typed_words, duration_ms=duration_ms
                )
            ),
            source_end_ms=(
                duration_ms
                if index + 1 == len(take.members)
                else _timestamp_source_ms(
                    member.text_end, typed_words, duration_ms=duration_ms
                )
            ),
        )
        for index, member in enumerate(take.members)
    ]
    anchors = [
        NarrationTimestampAnchorV1(
            beat_id=anchor.beat_id,
            id=anchor.id,
            text_offset=anchor.text_offset,
            source_ms=_timestamp_source_ms(
                anchor.text_offset, typed_words, duration_ms=duration_ms
            ),
        )
        for anchor in take.anchors
    ]
    waits = [
        NarrationTimestampWaitV1(
            beat_id=wait.beat_id,
            target=wait.target,
            text_offset=wait.text_offset,
            source_ms=_wait_timestamp_source_ms(
                wait.text_offset,
                typed_words,
                synthesis_text=take.synthesis_text,
                duration_ms=duration_ms,
            ),
            gap_ms=wait.gap_ms,
        )
        for wait in take.waits
    ]
    return _structured_payload(
        NarrationTimestampSidecarV1(
            take_id=take.id,
            duration_ms=duration_ms,
            members=members,
            words=typed_words,
            anchors=anchors,
            waits=waits,
        )
    )


def narration_audio_metadata_v3_payload(
    plan: RecordingPlan,
    *,
    take_audio_paths: Mapping[str, str],
    take_audio_sha256: Mapping[str, str],
    take_durations_ms: Mapping[str, int],
    timestamp_paths: Mapping[str, str],
) -> dict[str, Any]:
    takes: list[NarrationAudioTakeV3] = []
    source_offset = 0
    for take in plan.narration_takes:
        try:
            audio_path = take_audio_paths[take.id]
            audio_sha256 = take_audio_sha256[take.id]
            duration = take_durations_ms[take.id]
            timestamp_path = timestamp_paths[take.id]
        except KeyError as exc:
            raise AudioError(f"missing audio metadata for narration take {take.id!r}") from exc
        if not isinstance(audio_path, str) or not audio_path:
            raise AudioError(f"invalid audio path for narration take {take.id!r}")
        if not isinstance(audio_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", audio_sha256
        ):
            raise AudioError(f"invalid audio hash for narration take {take.id!r}")
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise AudioError(
                f"audio duration for narration take {take.id!r} must be an integer"
            )
        if duration < 0:
            raise AudioError(f"negative audio duration for narration take {take.id!r}")
        if not isinstance(timestamp_path, str) or not timestamp_path:
            raise AudioError(f"invalid timestamp path for narration take {take.id!r}")
        takes.append(
            NarrationAudioTakeV3(
                id=take.id,
                src=audio_path,
                sha256=audio_sha256,
                source_start_ms=source_offset,
                source_end_ms=source_offset + duration,
                timestamps=timestamp_path,
                members=[
                    NarrationAudioMemberV2(
                        beat_id=member.beat_id,
                        text=member.text,
                        text_start=member.text_start,
                        text_end=member.text_end,
                    )
                    for member in take.members
                ],
            )
        )
        source_offset += duration
    return _structured_payload(
        NarrationAudioMetadataV3(
            recording=plan.id,
            duration_ms=source_offset,
            takes=takes,
        )
    )


def plan_audio(
    recording_id: str,
    segments: list[NarrationSegment],
    settings: AudioSettings,
) -> list[AudioPlanItem]:
    items: list[AudioPlanItem] = []
    recording_dir = settings.cache_dir / recording_id
    for segment in segments:
        cache_key = segment_cache_key(segment, settings)
        filename = f"{segment.segment_id}-{cache_key}.{settings.format}"
        items.append(
            AudioPlanItem(
                segment=segment,
                cache_key=cache_key,
                output_path=recording_dir / filename,
            )
        )
    return items


def reusable_cache_path(item: AudioPlanItem, settings: AudioSettings) -> Path | None:
    for candidate in item.output_path.parent.glob(
        f"*-{item.cache_key}.{settings.format}"
    ):
        if candidate != item.output_path:
            return candidate
    return None


def audio_items_requiring_synthesis(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    *,
    force: bool = False,
) -> list[AudioPlanItem]:
    if force:
        return list(plan)
    needed: list[AudioPlanItem] = []
    for item in plan:
        if item.output_path.exists():
            continue
        if reusable_cache_path(item, settings) is not None:
            continue
        needed.append(item)
    return needed


def audio_items_requiring_materialization(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    *,
    force: bool = False,
) -> list[AudioPlanItem]:
    if force:
        return list(plan)
    needed: list[AudioPlanItem] = []
    for item in plan:
        if item.output_path.exists():
            continue
        if reusable_cache_path(item, settings) is not None:
            needed.append(item)
    return needed


def timestamp_items_requiring_generation(
    plan: list[AudioPlanItem],
    *,
    transcription: TranscriptionSettings | None = None,
    force: bool = False,
) -> list[AudioPlanItem]:
    if force:
        return list(plan)
    needed: list[AudioPlanItem] = []
    for item in plan:
        timeline_path = timeline_path_for(item)
        if not timeline_path.exists() or not timestamp_sidecar_matches_item(
            timeline_path,
            item,
            transcription=transcription,
        ):
            needed.append(item)
    return needed


def openai_tts_billable_characters(
    segment: NarrationSegment,
    settings: AudioSettings,
) -> int:
    return len(segment.text) + len(settings.instructions or "")


def estimate_openai_tts_billing(
    items: list[AudioPlanItem],
    settings: AudioSettings,
) -> AudioBillingSummary:
    characters = sum(
        openai_tts_billable_characters(item.segment, settings) for item in items
    )
    cost = characters * settings.tts_usd_per_1m_characters / 1_000_000
    return AudioBillingSummary(
        generated_segments=len(items),
        billable_characters=characters,
        estimated_cost_usd=cost,
    )


def estimate_openai_transcription_billing(
    items: list[AudioPlanItem],
    transcription: TranscriptionSettings,
    *,
    ffprobe: str = "ffprobe",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AudioTranscriptionBillingSummary:
    seconds = sum(
        audio_duration_seconds(item.output_path, ffprobe=ffprobe, run=run)
        for item in items
    )
    cost = seconds * transcription.usd_per_minute / 60
    return AudioTranscriptionBillingSummary(
        generated_timestamp_files=len(items),
        audio_seconds=seconds,
        estimated_cost_usd=cost,
    )


def format_usd(value: float) -> str:
    if value == 0:
        return "$0.00"
    if value < 0.01:
        return f"${value:.6f}"
    return f"${value:.2f}"


def print_openai_tts_billing_summary(summary: AudioBillingSummary) -> None:
    info_line(
        "OpenAI TTS estimated cost this run: "
        f"{format_usd(summary.estimated_cost_usd)}"
    )


def print_openai_transcription_billing_summary(
    summary: AudioTranscriptionBillingSummary,
) -> None:
    info_line(
        "OpenAI transcription estimated cost this run: "
        f"{format_usd(summary.estimated_cost_usd)}"
    )


def audio_ready_summary(
    *,
    generated_count: int,
    reused_count: int,
    generated_timestamp_count: int | None,
    estimated_tts_cost: str | None,
) -> str:
    parts: list[str] = []
    if generated_count:
        parts.append(f"{generated_count} generated segments")
    if reused_count:
        parts.append(f"{reused_count} reused segments")
    if generated_timestamp_count:
        parts.append(f"{generated_timestamp_count} generated timestamp files")
    if estimated_tts_cost is not None:
        parts.append(f"estimated TTS cost {estimated_tts_cost}")
    return "audio updated: " + ", ".join(parts)


def published_audio_summary(
    *,
    audio_path: Path,
    timestamp_count: int,
) -> str:
    return (
        f"published audio ready: {audio_path.name}, metadata, "
        f"{timestamp_count} timestamp files"
    )


def print_paths_or_summary(
    *,
    verbose: bool,
    path_label: str,
    summary_label: str,
    paths: list[Path],
    current: int | None = None,
    total: int | None = None,
) -> None:
    if verbose:
        for path in paths:
            pass_line(f"{path_label}: {display_path(path)}")
    elif paths:
        pass_line(f"{summary_label}: {len(paths)}", current=current, total=total)


def openai_speech_bytes(
    segment: NarrationSegment,
    settings: AudioSettings,
    *,
    environ: dict[str, str] | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> bytes:
    env = audio_environment(settings, environ)
    api_key = env.get(settings.env)
    if not api_key:
        raise AudioError(f"missing OpenAI API key environment variable: {settings.env}")

    payload: dict[str, Any] = {
        "model": settings.model,
        "input": segment.text,
        "voice": settings.voice,
        "response_format": settings.format,
    }
    if settings.instructions:
        payload["instructions"] = settings.instructions
    request = urllib.request.Request(
        OPENAI_SPEECH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.fp is None:
            detail = exc.msg
        else:
            detail = exc.read().decode("utf-8", errors="replace")
        raise AudioError(
            f"OpenAI speech request failed: HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AudioError(f"OpenAI speech request failed: {exc.reason}") from exc


def generate_audio(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    *,
    force: bool = False,
    synthesize: Callable[
        [NarrationSegment, AudioSettings], bytes
    ] = openai_speech_bytes,
) -> list[Path]:
    written: list[Path] = []
    for item in plan:
        if not force:
            if item.output_path.exists():
                written.append(item.output_path)
                continue
            existing = reusable_cache_path(item, settings)
            if existing is not None:
                item.output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existing, item.output_path)
                written.append(item.output_path)
                continue
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        audio_bytes = synthesize(item.segment, settings)
        if not audio_bytes:
            raise AudioError(
                f"audio provider returned no data for {item.segment.segment_id}"
            )
        item.output_path.write_bytes(audio_bytes)
        written.append(item.output_path)
    return written


def output_audio_path(
    spec: dict[str, Any], recording_id: str, settings: AudioSettings
) -> Path:
    outputs = as_mapping(spec.get("outputs"), field="outputs")
    configured = outputs.get("audio")
    if configured is None:
        asset_dir = outputs.get("asset_dir")
        if not isinstance(asset_dir, str) or not asset_dir:
            raise AudioError("outputs.asset_dir must be a non-empty string")
        return relative_path(asset_dir) / f"audio.{settings.format}"
    if not isinstance(configured, str) or not configured:
        raise AudioError("outputs.audio must be a non-empty string")
    return relative_path(configured)


def output_audio_metadata_path(spec: dict[str, Any], output_path: Path) -> Path:
    outputs = as_mapping(spec.get("outputs"), field="outputs")
    configured = outputs.get("audio_metadata")
    if configured is None:
        return output_path.with_suffix(".json")
    if not isinstance(configured, str) or not configured:
        raise AudioError("outputs.audio_metadata must be a non-empty string")
    return relative_path(configured)


def ffmpeg_concat_file_entry(path: Path) -> str:
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'\n"


def publish_audio(
    plan: list[AudioPlanItem],
    output_path: Path,
    *,
    ffmpeg: str = "ffmpeg",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    if not plan:
        raise AudioError("no audio segments to publish")
    for item in plan:
        if not item.output_path.exists():
            raise AudioError(
                "audio segment is missing; run action=generate first: "
                f"{display_path(item.output_path)}"
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_file = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".ffconcat", delete=False
    )
    concat_path = Path(concat_file.name)
    try:
        with concat_file:
            for item in plan:
                concat_file.write(ffmpeg_concat_file_entry(item.output_path))
        result = run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        concat_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if not detail:
            detail = f"exit code {result.returncode}"
        raise AudioError(f"ffmpeg failed while publishing audio: {detail}")
    return output_path


def published_timestamp_path(metadata_path: Path, item: AudioPlanItem) -> Path:
    return metadata_path.with_name(
        f"{metadata_path.stem}.{item.segment.segment_id}.timestamps.json"
    )


def publish_timestamp_sidecars(
    plan: list[AudioPlanItem],
    metadata_path: Path,
) -> dict[str, Path]:
    published: dict[str, Path] = {}
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    for item in plan:
        source = timeline_path_for(item)
        if not source.exists():
            continue
        target = published_timestamp_path(metadata_path, item)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise AudioError(f"audio timestamp metadata must be a mapping: {source}")
        compact = compact_timestamp_payload(payload)
        target.write_text(
            json.dumps(compact, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        published[item.segment.segment_id] = target
    return published


def audio_duration_seconds(
    path: Path,
    *,
    ffprobe: str = "ffprobe",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> float:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if not detail:
            detail = f"exit code {result.returncode}"
        raise AudioError(f"ffprobe failed while reading audio duration: {detail}")
    value = result.stdout.strip()
    try:
        duration = float(value)
    except ValueError as exc:
        raise AudioError(
            f"ffprobe returned invalid duration for {path}: {value}"
        ) from exc
    if duration < 0:
        raise AudioError(f"ffprobe returned negative duration for {path}: {value}")
    return duration


def audio_metadata_payload(
    recording_id: str,
    plan: list[AudioPlanItem],
    output_path: Path,
    *,
    guide_by_segment_id: dict[str, dict[str, Any]] | None = None,
    anchors_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    waits_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    pause_after_by_segment_id: dict[str, float] | None = None,
    timestamps_by_segment_id: dict[str, Path] | None = None,
    ffprobe: str = "ffprobe",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    offset = 0.0
    segments: list[dict[str, Any]] = []
    for item in plan:
        duration = audio_duration_seconds(item.output_path, ffprobe=ffprobe, run=run)
        segment = {
            "id": item.segment.segment_id,
            "heading": item.segment.heading,
            "text": item.segment.text,
            "audio": display_path(item.output_path),
            "offset": round(offset, 3),
            "duration": round(duration, 3),
        }
        if anchors_by_segment_id and item.segment.segment_id in anchors_by_segment_id:
            segment["anchors"] = anchors_by_segment_id[item.segment.segment_id]
        if waits_by_segment_id and item.segment.segment_id in waits_by_segment_id:
            segment["waits"] = waits_by_segment_id[item.segment.segment_id]
        if guide_by_segment_id and item.segment.segment_id in guide_by_segment_id:
            segment["guide"] = guide_by_segment_id[item.segment.segment_id]
        if (
            pause_after_by_segment_id
            and item.segment.segment_id in pause_after_by_segment_id
        ):
            segment["pause_after"] = round(
                pause_after_by_segment_id[item.segment.segment_id],
                3,
            )
        if (
            timestamps_by_segment_id
            and item.segment.segment_id in timestamps_by_segment_id
        ):
            segment["timestamps"] = display_path(
                timestamps_by_segment_id[item.segment.segment_id]
            )
        segments.append(segment)
        offset += duration
    return {
        "recording": recording_id,
        "audio": display_path(output_path),
        "duration": round(
            audio_duration_seconds(output_path, ffprobe=ffprobe, run=run),
            3,
        ),
        "segments": segments,
    }


def guide_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw_commands = value.get("commands")
    if not isinstance(raw_commands, list):
        return None
    commands = [
        normalize_narration_text(command)
        for command in raw_commands
        if isinstance(command, str) and command.strip()
    ]
    if not commands:
        return None
    payload: dict[str, Any] = {"commands": commands}
    success_hint = value.get("success_hint")
    if isinstance(success_hint, str) and success_hint.strip():
        payload["success_hint"] = normalize_narration_text(success_hint)
    return payload


def guide_by_segment_id_from_spec(
    spec: dict[str, Any],
    plan: list[AudioPlanItem],
) -> dict[str, dict[str, Any]]:
    beats = spec.get("beats")
    if not isinstance(beats, list) or not plan:
        return {}
    segment_offset = 1 if plan[0].segment.segment_id == "overview" else 0
    guides: dict[str, dict[str, Any]] = {}
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        segment_index = index + segment_offset
        if segment_index >= len(plan):
            break
        guide = guide_payload(beat.get("guide"))
        if guide is not None:
            guides[plan[segment_index].segment.segment_id] = guide
    return guides


def anchors_by_segment_id_from_spec(
    spec: dict[str, Any],
    plan: list[AudioPlanItem],
) -> dict[str, list[dict[str, Any]]]:
    narration = narration_mapping_from_spec(spec)
    beats = narration.get("beats")
    if not isinstance(beats, list):
        return {}
    plan_by_id = {item.segment.segment_id for item in plan}
    anchors: dict[str, list[dict[str, Any]]] = {}
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        segment_id = beat.get("id")
        raw_anchors = beat.get("anchors")
        if (
            not isinstance(segment_id, str)
            or segment_id not in plan_by_id
            or not isinstance(raw_anchors, list)
        ):
            continue
        clean_anchors = []
        for raw_anchor in raw_anchors:
            if not isinstance(raw_anchor, dict):
                continue
            anchor_id = raw_anchor.get("id")
            marker = raw_anchor.get("marker")
            text_offset = raw_anchor.get("text_offset")
            if (
                isinstance(anchor_id, str)
                and anchor_id
                and isinstance(marker, str)
                and isinstance(text_offset, int)
            ):
                clean_anchors.append(
                    {
                        "id": anchor_id,
                        "marker": marker,
                        "text_offset": text_offset,
                    }
                )
        if clean_anchors:
            anchors[segment_id] = clean_anchors
    return anchors


def waits_by_segment_id_from_spec(
    spec: dict[str, Any],
    plan: list[AudioPlanItem],
) -> dict[str, list[dict[str, Any]]]:
    narration = narration_mapping_from_spec(spec)
    beats = narration.get("beats")
    if not isinstance(beats, list):
        return {}
    plan_by_id = {item.segment.segment_id for item in plan}
    waits: dict[str, list[dict[str, Any]]] = {}
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        segment_id = beat.get("id")
        raw_waits = beat.get("waits")
        if (
            not isinstance(segment_id, str)
            or segment_id not in plan_by_id
            or not isinstance(raw_waits, list)
        ):
            continue
        clean_waits = []
        for raw_wait in raw_waits:
            if not isinstance(raw_wait, dict):
                continue
            target = raw_wait.get("target")
            marker = raw_wait.get("marker")
            text_offset = raw_wait.get("text_offset")
            gap_seconds = raw_wait.get("gap_seconds")
            if (
                isinstance(target, str)
                and target
                and isinstance(marker, str)
                and isinstance(text_offset, int)
                and isinstance(gap_seconds, (int, float))
                and gap_seconds >= 0
            ):
                clean_waits.append(
                    {
                        "target": target,
                        "marker": marker,
                        "text_offset": text_offset,
                        "gap_seconds": round(float(gap_seconds), 3),
                    }
                )
        if clean_waits:
            waits[segment_id] = clean_waits
    return waits


def pause_after_by_segment_id_from_spec(
    spec: dict[str, Any],
    plan: list[AudioPlanItem],
) -> dict[str, float]:
    narration = narration_mapping_from_spec(spec)
    beats = narration.get("beats")
    if not isinstance(beats, list) or not plan:
        return {}
    plan_by_id = {item.segment.segment_id for item in plan}
    pauses: dict[str, float] = {}
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        segment_id = beat.get("id")
        pause_after = beat.get("viewer_hold")
        if (
            isinstance(segment_id, str)
            and segment_id in plan_by_id
            and isinstance(pause_after, (int, float))
            and pause_after > 0
        ):
            pauses[segment_id] = float(pause_after)
    return pauses


def format_browse_timestamp(seconds: float) -> str:
    total_seconds = int(round(max(0.0, seconds)))
    minutes, remainder = divmod(total_seconds, 60)
    return f"{minutes:02d}m{remainder:02d}s"


def refresh_ordered_audio_browse_links(
    plan: list[AudioPlanItem],
    segments: list[dict[str, Any]],
    *,
    browse_dir: Path | None = None,
) -> Path | None:
    if not plan:
        return None
    if len(plan) != len(segments):
        raise AudioError("audio browse link count does not match audio plan")
    target_dir = browse_dir or plan[0].output_path.parent / "ordered"
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in target_dir.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()

    for item, segment in zip(plan, segments, strict=True):
        offset = segment.get("offset")
        duration = segment.get("duration")
        if not isinstance(offset, (int, float)) or not isinstance(
            duration, (int, float)
        ):
            raise AudioError("audio metadata segment is missing numeric timing")
        start = format_browse_timestamp(float(offset))
        end = format_browse_timestamp(float(offset) + float(duration))
        link = target_dir / f"{start}-{end}-{item.output_path.name}"
        target = Path("..") / item.output_path.name
        try:
            link.symlink_to(target)
        except OSError:
            shutil.copy2(item.output_path, link)
    return target_dir


def publish_audio_metadata(
    recording_id: str,
    plan: list[AudioPlanItem],
    output_path: Path,
    metadata_path: Path,
    *,
    guide_by_segment_id: dict[str, dict[str, Any]] | None = None,
    anchors_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    waits_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    pause_after_by_segment_id: dict[str, float] | None = None,
    timestamps_by_segment_id: dict[str, Path] | None = None,
    ffprobe: str = "ffprobe",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    payload = audio_metadata_payload(
        recording_id,
        plan,
        output_path,
        guide_by_segment_id=guide_by_segment_id,
        anchors_by_segment_id=anchors_by_segment_id,
        waits_by_segment_id=waits_by_segment_id,
        pause_after_by_segment_id=pause_after_by_segment_id,
        timestamps_by_segment_id=timestamps_by_segment_id,
        ffprobe=ffprobe,
        run=run,
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    refresh_ordered_audio_browse_links(plan, payload["segments"])
    return metadata_path


def published_audio_metadata_mismatch(
    plan: list[AudioPlanItem],
    metadata_path: Path,
    *,
    anchors_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    waits_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    pause_after_by_segment_id: dict[str, float] | None = None,
    allow_missing_segment_audio: bool = False,
) -> str | None:
    if not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return "metadata root is not a JSON object"
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return "metadata segments field is missing or invalid"
    if len(segments) != len(plan):
        return f"segment count is {len(segments)}, expected {len(plan)}"

    def wait_metadata_matches(
        actual_value: object,
        expected_value: object,
    ) -> bool:
        if not isinstance(actual_value, list) or not isinstance(expected_value, list):
            return actual_value == expected_value
        if len(actual_value) != len(expected_value):
            return False
        for actual_wait, expected_wait in zip(
            actual_value, expected_value, strict=True
        ):
            if not isinstance(actual_wait, dict) or not isinstance(expected_wait, dict):
                return actual_wait == expected_wait
            for key, value in expected_wait.items():
                if actual_wait.get(key) != value:
                    return False
        return True

    for index, (item, segment) in enumerate(zip(plan, segments, strict=True)):
        if not isinstance(segment, dict):
            return f"segment {index + 1} is not an object"
        expected = {
            "id": item.segment.segment_id,
            "heading": item.segment.heading,
            "text": item.segment.text,
        }
        if not allow_missing_segment_audio or "audio" in segment:
            expected["audio"] = display_path(item.output_path)
        for field, expected_value in expected.items():
            actual_value = segment.get(field)
            if actual_value != expected_value:
                segment_id = item.segment.segment_id
                return (
                    f"segment {segment_id!r} field {field!r} is stale "
                    f"(metadata has {actual_value!r}, expected {expected_value!r})"
                )
        metadata_fields: list[
            tuple[str, dict[str, list[dict[str, Any]]] | dict[str, float] | None]
        ] = [
            ("anchors", anchors_by_segment_id),
            ("waits", waits_by_segment_id),
            ("pause_after", pause_after_by_segment_id),
        ]
        for field, expected_by_segment_id in metadata_fields:
            if expected_by_segment_id is None:
                continue
            segment_id = item.segment.segment_id
            expected_value = expected_by_segment_id.get(segment_id)
            if field == "pause_after" and isinstance(expected_value, (int, float)):
                expected_value = round(float(expected_value), 3)
            actual_value = segment.get(field)
            matches = (
                wait_metadata_matches(actual_value, expected_value)
                if field == "waits"
                else actual_value == expected_value
            )
            if not matches:
                return (
                    f"segment {segment_id!r} field {field!r} is stale "
                    f"(metadata has {actual_value!r}, expected {expected_value!r})"
                )
    return None


def validate_published_audio_metadata(
    plan: list[AudioPlanItem],
    metadata_path: Path,
    *,
    anchors_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    waits_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    pause_after_by_segment_id: dict[str, float] | None = None,
    allow_missing_segment_audio: bool = False,
) -> None:
    mismatch = published_audio_metadata_mismatch(
        plan,
        metadata_path,
        anchors_by_segment_id=anchors_by_segment_id,
        waits_by_segment_id=waits_by_segment_id,
        pause_after_by_segment_id=pause_after_by_segment_id,
        allow_missing_segment_audio=allow_missing_segment_audio,
    )
    if mismatch is None:
        return
    raise AudioError(
        "published audio metadata is stale: "
        f"{display_path(metadata_path)}: {mismatch}; "
        "run audio_generate and audio_publish"
    )


def published_audio_is_fresh(
    plan: list[AudioPlanItem],
    output_path: Path,
    metadata_path: Path,
    *,
    anchors_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    waits_by_segment_id: dict[str, list[dict[str, Any]]] | None = None,
    pause_after_by_segment_id: dict[str, float] | None = None,
) -> bool:
    if not output_path.exists() or not metadata_path.exists():
        return False
    if (
        published_audio_metadata_mismatch(
            plan,
            metadata_path,
            anchors_by_segment_id=anchors_by_segment_id,
            waits_by_segment_id=waits_by_segment_id,
            pause_after_by_segment_id=pause_after_by_segment_id,
        )
        is not None
    ):
        return False
    try:
        output_mtime = output_path.stat().st_mtime_ns
        metadata_mtime = metadata_path.stat().st_mtime_ns
    except OSError:
        return False
    published_mtime = min(output_mtime, metadata_mtime)
    for item in plan:
        inputs = [item.output_path]
        timestamp_path = timeline_path_for(item)
        if timestamp_path.exists():
            inputs.append(timestamp_path)
        for path in inputs:
            if not path.exists():
                return False
            try:
                if path.stat().st_mtime_ns > published_mtime:
                    return False
            except OSError:
                return False
    return True


def multipart_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "")
        .replace("\n", "")
    )


def encode_multipart_form(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
    *,
    boundary: str = "omegaflow-boundary",
) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            "Content-Disposition: form-data; "
            f'name="{multipart_escape(name)}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, filename, content_type, data in files:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            "Content-Disposition: form-data; "
            f'name="{multipart_escape(name)}"; '
            f'filename="{multipart_escape(filename)}"\r\n'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def audio_content_type(path: Path) -> str:
    if path.suffix == ".mp3":
        return "audio/mpeg"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def timeline_path_for(item: AudioPlanItem) -> Path:
    return item.output_path.with_suffix(".timeline.json")


def openai_transcription_json(
    item: AudioPlanItem,
    settings: AudioSettings,
    transcription: TranscriptionSettings,
    *,
    environ: dict[str, str] | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    env = audio_environment(settings, environ)
    api_key = env.get(settings.env)
    if not api_key:
        raise AudioError(f"missing OpenAI API key environment variable: {settings.env}")
    if not item.output_path.exists():
        raise AudioError(f"audio file not found: {item.output_path}")

    fields = [
        ("model", transcription.model),
        ("response_format", "verbose_json"),
    ]
    for granularity in transcription.timestamp_granularities:
        fields.append(("timestamp_granularities[]", granularity))
    body, content_type = encode_multipart_form(
        fields,
        [
            (
                "file",
                item.output_path.name,
                audio_content_type(item.output_path),
                item.output_path.read_bytes(),
            )
        ],
    )
    request = urllib.request.Request(
        OPENAI_TRANSCRIPTIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.fp is None:
            detail = exc.msg
        else:
            detail = exc.read().decode("utf-8", errors="replace")
        raise AudioError(
            f"OpenAI transcription request failed: HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AudioError(f"OpenAI transcription request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AudioError("OpenAI transcription response was not JSON") from exc
    if not isinstance(data, dict):
        raise AudioError("OpenAI transcription response must be a JSON object")
    return data


def timeline_payload(
    recording_id: str,
    item: AudioPlanItem,
    transcription: TranscriptionSettings,
    response: dict[str, Any],
) -> dict[str, Any]:
    return compact_timestamp_payload(
        {
            "recording": recording_id,
            "segment": item.segment.segment_id,
            "source_text": item.segment.text,
            "timestamp_granularities": list(transcription.timestamp_granularities),
            "transcript": response.get("text"),
            "transcription_model": transcription.model,
            "duration": response.get("duration"),
            "words": response.get("words", []),
        }
    )


def compact_timestamp_word(word: object) -> dict[str, Any] | None:
    if not isinstance(word, dict):
        return None
    raw_word = word.get("word")
    start = word.get("start")
    end = word.get("end")
    if (
        not isinstance(raw_word, str)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
    ):
        return None
    return {
        "end": round(float(end), 3),
        "start": round(float(start), 3),
        "word": raw_word,
    }


def compact_timestamp_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "recording",
        "segment",
        "source_text",
        "transcript",
        "transcription_model",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            compact[key] = value
    timestamp_granularities = payload.get("timestamp_granularities")
    if (
        isinstance(timestamp_granularities, list)
        and timestamp_granularities
        and all(isinstance(item, str) for item in timestamp_granularities)
    ):
        compact["timestamp_granularities"] = timestamp_granularities
    duration = payload.get("duration")
    if isinstance(duration, (int, float)):
        compact["duration"] = round(float(duration), 3)
    words = payload.get("words")
    compact_words: list[dict[str, Any]] = []
    if isinstance(words, list):
        for word in words:
            compact_word = compact_timestamp_word(word)
            if compact_word is not None:
                compact_words.append(compact_word)
    compact["words"] = compact_words
    return compact


def timestamp_sidecar_matches_item(
    path: Path,
    item: AudioPlanItem,
    *,
    transcription: TranscriptionSettings | None = None,
) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("source_text") != item.segment.text:
        return False
    if transcription is None:
        return True
    return payload.get("transcription_model") == transcription.model and payload.get(
        "timestamp_granularities"
    ) == list(transcription.timestamp_granularities)


def generate_timestamps(
    recording_id: str,
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    transcription: TranscriptionSettings,
    *,
    force: bool = False,
    transcribe: Callable[
        [AudioPlanItem, AudioSettings, TranscriptionSettings], dict[str, Any]
    ] = openai_transcription_json,
) -> list[Path]:
    written: list[Path] = []
    for item in plan:
        timeline_path = timeline_path_for(item)
        if (
            timeline_path.exists()
            and not force
            and timestamp_sidecar_matches_item(
                timeline_path,
                item,
                transcription=transcription,
            )
        ):
            written.append(timeline_path)
            continue
        response = transcribe(item, settings, transcription)
        payload = timeline_payload(recording_id, item, transcription, response)
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(timeline_path)
    return written


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(project_root()))
    except ValueError:
        return str(path)


def audio_plan_status(
    item: AudioPlanItem,
    settings: AudioSettings,
) -> tuple[str, Path | None]:
    if item.output_path.exists():
        return "cached", None
    reusable = reusable_cache_path(item, settings)
    if reusable is not None:
        return "reusable", reusable
    return "missing", None


def audio_plan_item_payload(
    item: AudioPlanItem,
    settings: AudioSettings,
) -> dict[str, Any]:
    status, reusable = audio_plan_status(item, settings)
    payload = {
        "segment": item.segment.segment_id,
        "heading": item.segment.heading,
        "status": status,
        "cache_key": item.cache_key,
        "output": display_path(item.output_path),
        "timeline": display_path(timeline_path_for(item)),
        "text": item.segment.text,
    }
    if reusable is not None:
        payload["reuses"] = display_path(reusable)
    return payload


def shortened_text(text: str, *, width: int = 110) -> str:
    return textwrap.shorten(
        normalize_narration_text(text), width=width, placeholder="..."
    )


def print_plan_json(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    published_audio: Path,
    published_metadata: Path,
) -> None:
    for item in plan:
        print(json.dumps(audio_plan_item_payload(item, settings), sort_keys=True))
    print(
        json.dumps(
            {
                "published_audio": display_path(published_audio),
                "published_audio_metadata": display_path(published_metadata),
            },
            sort_keys=True,
        )
    )


def print_plan_text(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    published_audio: Path,
    published_metadata: Path,
) -> None:
    payloads = [audio_plan_item_payload(item, settings) for item in plan]
    counts = {
        "cached": sum(1 for payload in payloads if payload["status"] == "cached"),
        "reusable": sum(1 for payload in payloads if payload["status"] == "reusable"),
        "missing": sum(1 for payload in payloads if payload["status"] == "missing"),
    }
    print("Audio dry run")
    print(f"  segments: {len(plan)}")
    print(
        "  cache: "
        f"{counts['cached']} cached, "
        f"{counts['reusable']} reusable, "
        f"{counts['missing']} missing"
    )
    print(f"  published audio: {display_path(published_audio)}")
    print(f"  metadata: {display_path(published_metadata)}")
    if not plan:
        return
    print()
    print("Segments:")
    for item, payload in zip(plan, payloads, strict=True):
        status = str(payload["status"])
        print(f"  {status:<8} {item.segment.segment_id} - {item.segment.heading}")
        print(f"           audio: {payload['output']}")
        if "reuses" in payload:
            print(f"           reuses: {payload['reuses']}")
        print(f"           text: {shortened_text(item.segment.text)}")


def print_plan(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    published_audio: Path,
    published_metadata: Path,
    *,
    output_format: str = "text",
) -> None:
    if output_format == "json":
        print_plan_json(plan, settings, published_audio, published_metadata)
        return
    if output_format == "text":
        print_plan_text(plan, settings, published_audio, published_metadata)
        return
    raise AudioError("output_format must be 'text' or 'json'")


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
        load_configured_env_file(config)
        spec = load_recording_spec_from_hydra_cfg(cfg)
        action = config.get("step") or config.get("action", "generate")
        if action == "build":
            action = "generate"
        if action not in {
            "generate",
            "check",
            "dry_run",
            "publish",
            "sync_narration",
        }:
            raise AudioError(
                "action must be 'generate', 'check', 'dry_run', 'publish', "
                "or 'sync_narration'"
            )
        force = config.get("force", False)
        timestamps = config.get("timestamps", False)
        output_format = config.get("output_format", "text")
        output_override = config.get("output")
        verbose = config.get("verbose", False)
        if not isinstance(force, bool):
            raise AudioError("force must be a boolean")
        if not isinstance(timestamps, bool):
            raise AudioError("timestamps must be a boolean")
        if not isinstance(output_format, str):
            raise AudioError("output_format must be a string")
        if output_override is not None and not isinstance(output_override, str):
            raise AudioError("output must be a string or null")
        if not isinstance(verbose, bool):
            raise AudioError("verbose must be a boolean")
        recording_id = require_string(spec, "_recording_id", field="recording")
        previous_renderer = AUDIO_PROGRESS.renderer
        AUDIO_PROGRESS.renderer = (
            LogProgressRenderer() if verbose else ProgressBarRenderer()
        )

        def restore_renderer() -> bool:
            was_progress_bar = isinstance(AUDIO_PROGRESS.renderer, ProgressBarRenderer)
            if isinstance(AUDIO_PROGRESS.renderer, ProgressBarRenderer):
                AUDIO_PROGRESS.renderer.finish(replay=False)
            AUDIO_PROGRESS.renderer = previous_renderer
            return was_progress_bar

        def finish(
            code: int,
            *,
            summaries: list[StatusSummary] | None = None,
        ) -> int:
            was_progress_bar = restore_renderer()
            if code == 0 and was_progress_bar and summaries:
                for status, message, color in summaries:
                    status_line(status, message, color=color)
            return code

        if action == "sync_narration":
            try:
                path = sync_narration_config(spec)
                pass_line(f"synced narration: {display_path(path)}", current=1, total=1)
                return finish(
                    0,
                    summaries=[
                        (
                            "pass",
                            f"synced narration: {display_path(path)}",
                            ANSI_GREEN_BOLD,
                        )
                    ],
                )
            except BaseException:
                restore_renderer()
                raise
        try:
            settings = audio_settings(spec)
            transcription = transcription_settings(spec)
            segments = load_narration_segments(spec)
            plan = plan_audio(recording_id, segments, settings)
            guide_by_segment_id = guide_by_segment_id_from_spec(spec, plan)
            anchors_by_segment_id = anchors_by_segment_id_from_spec(spec, plan)
            waits_by_segment_id = waits_by_segment_id_from_spec(spec, plan)
            pause_after_by_segment_id = pause_after_by_segment_id_from_spec(spec, plan)
            published_audio = (
                relative_path(output_override)
                if output_override
                else output_audio_path(spec, recording_id, settings)
            )
            published_metadata = (
                published_audio.with_suffix(".json")
                if output_override
                else output_audio_metadata_path(spec, published_audio)
            )
            if action == "dry_run":
                print_plan(
                    plan,
                    settings,
                    published_audio,
                    published_metadata,
                    output_format=output_format,
                )
                return finish(0)
            if action == "check":
                validate_published_audio_metadata(
                    plan,
                    published_metadata,
                    anchors_by_segment_id=anchors_by_segment_id,
                    waits_by_segment_id=waits_by_segment_id,
                    pause_after_by_segment_id=pause_after_by_segment_id,
                    allow_missing_segment_audio=True,
                )
                state = "enabled" if settings.enabled else "disabled"
                pass_line(
                    f"{recording_id} audio {state}; "
                    f"{len(plan)} narration segment(s), provider {settings.provider}, "
                    f"transcription {transcription.model}",
                    current=1,
                    total=1,
                )
                return finish(
                    0,
                    summaries=[
                        (
                            "pass",
                            f"{recording_id} audio {state}; "
                            f"{len(plan)} narration segment(s), "
                            f"provider {settings.provider}, "
                            f"transcription {transcription.model}",
                            ANSI_GREEN_BOLD,
                        )
                    ],
                )
            if action == "publish":
                path = publish_audio(plan, published_audio)
                timestamps_by_segment_id = publish_timestamp_sidecars(
                    plan,
                    published_metadata,
                )
                metadata_path = publish_audio_metadata(
                    recording_id,
                    plan,
                    published_audio,
                    published_metadata,
                    guide_by_segment_id=guide_by_segment_id,
                    anchors_by_segment_id=anchors_by_segment_id,
                    waits_by_segment_id=waits_by_segment_id,
                    pause_after_by_segment_id=pause_after_by_segment_id,
                    timestamps_by_segment_id=timestamps_by_segment_id,
                )
                total_steps = 3
                pass_line(
                    f"published audio: {display_path(path)}",
                    current=1,
                    total=total_steps,
                )
                pass_line(
                    f"published audio metadata: {display_path(metadata_path)}",
                    current=2,
                    total=total_steps,
                )
                print_paths_or_summary(
                    verbose=verbose,
                    path_label="published audio timestamps",
                    summary_label="published audio timestamps",
                    paths=list(timestamps_by_segment_id.values()),
                    current=3,
                    total=total_steps,
                )
                return finish(
                    0,
                    summaries=[
                        (
                            "pass",
                            published_audio_summary(
                                audio_path=path,
                                timestamp_count=len(timestamps_by_segment_id),
                            ),
                            ANSI_GREEN_BOLD,
                        )
                    ],
                )
            if not settings.enabled:
                raise AudioError("audio is disabled in the recording config")
            synthesized_items = audio_items_requiring_synthesis(
                plan,
                settings,
                force=force,
            )
            materialized_items = audio_items_requiring_materialization(
                plan,
                settings,
                force=force,
            )
            timestamp_items = (
                timestamp_items_requiring_generation(
                    plan,
                    transcription=transcription,
                    force=force,
                )
                if timestamps
                else []
            )
            total_steps = 1 + (1 if synthesized_items else 0) + (1 if timestamps else 0)
            current_step = 1
            paths = generate_audio(plan, settings, force=force)
            print_paths_or_summary(
                verbose=verbose,
                path_label="audio",
                summary_label="audio segments ready",
                paths=paths,
                current=current_step,
                total=total_steps,
            )
            if settings.provider == "openai" and synthesized_items:
                current_step += 1
                billing = estimate_openai_tts_billing(synthesized_items, settings)
                info_line(
                    "OpenAI TTS estimated cost this run: "
                    f"{format_usd(billing.estimated_cost_usd)}",
                    current=current_step,
                    total=total_steps,
                )
            else:
                billing = None
            timeline_paths: list[Path] | None = None
            if timestamps:
                current_step += 1
                timeline_paths = generate_timestamps(
                    recording_id,
                    plan,
                    settings,
                    transcription,
                    force=force,
                )
                print_paths_or_summary(
                    verbose=verbose,
                    path_label="timeline",
                    summary_label="audio timestamps ready",
                    paths=timeline_paths,
                    current=current_step,
                    total=total_steps,
                )
                if settings.provider == "openai" and timestamp_items:
                    transcription_billing = estimate_openai_transcription_billing(
                        timestamp_items,
                        transcription,
                    )
                    info_line(
                        "OpenAI transcription estimated cost this run: "
                        f"{format_usd(transcription_billing.estimated_cost_usd)}",
                        current=current_step,
                        total=total_steps,
                    )
                    if billing is not None:
                        total_cost = (
                            billing.estimated_cost_usd
                            + transcription_billing.estimated_cost_usd
                        )
                        info_line(
                            "OpenAI total estimated cost this run: "
                            f"{format_usd(total_cost)}",
                            current=current_step,
                            total=total_steps,
                        )
            estimated_tts_cost = (
                format_usd(billing.estimated_cost_usd) if billing is not None else None
            )
            generated_count = len(synthesized_items)
            reused_count = len(materialized_items)
            generated_timestamp_count = len(timestamp_items) if timestamps else None
            summaries = []
            if generated_count or reused_count or generated_timestamp_count:
                summaries.append(
                    (
                        "pass",
                        audio_ready_summary(
                            generated_count=generated_count,
                            reused_count=reused_count,
                            generated_timestamp_count=generated_timestamp_count,
                            estimated_tts_cost=estimated_tts_cost,
                        ),
                        ANSI_GREEN_BOLD,
                    )
                )
            return finish(
                0,
                summaries=summaries,
            )
        except BaseException:
            restore_renderer()
            raise
    except StudioConfigError as exc:
        raise AudioError(str(exc)) from exc


@hydra.main(
    version_base=None,
    config_path=str(CONFIG_DIR),
    config_name=STUDIO_CONFIG_NAME,
)
def main(cfg: DictConfig) -> None:
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
