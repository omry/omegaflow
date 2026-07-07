#!/usr/bin/env python3
"""Frontend CLI for OmegaFlow."""

from __future__ import annotations

import hashlib
import html
import http.server
import io
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, unquote, urlparse

import hydra
from omegaconf import DictConfig, OmegaConf

from . import align_cast
from . import audio
from . import record
from . import retime_cast
from . import studio_config as studio_config_module
from .studio_config import (
    CONFIG_DIR,
    STUDIO_CONFIG_NAME,
    StudioAction,
    StudioConfigError,
    StudioStep,
    container_from_hydra_cfg,
    is_valid_recording_id,
    list_recording_ids,
    load_configured_env_file,
    load_recording_spec_from_hydra_cfg,
    recording_script_dir_from_config,
    recording_spec_from_config,
)
from .terminal_style import (
    ANSI_CYAN_BOLD,
    ANSI_DIM,
    ANSI_GREEN_BOLD,
    ANSI_RED_BOLD,
    ANSI_YELLOW_BOLD,
    color_enabled,
    color_text,
)


class StudioError(RuntimeError):
    pass


RECORDING_FINGERPRINT_VERSION = 2
FINGERPRINT_PRIVATE_SPEC_KEYS = {"_overrides", "_recording_id"}
CAPTURE_FINGERPRINT_IGNORED_KEYS = {
    "audio",
    "audio_metadata",
    "guide",
    "narration",
    "publish",
    "retimed_cast",
    "surfaces",
}


ToolRunner = Callable[[Any], int]


def text_output_enabled(cfg: DictConfig) -> bool:
    return OmegaConf.select(cfg, "output_format", default="text") != "json"


def status_line(
    status: str,
    message: str,
    *,
    color: str,
    detail: str | None = None,
) -> None:
    enabled = color_enabled()
    label = color_text(f"{status:<5}", color, enabled=enabled)
    line = f"{label} {message}"
    if detail:
        line += color_text(f" ({detail})", ANSI_DIM, enabled=enabled)
    print(line, flush=True)


def step_line(message: str) -> None:
    status_line("step", message, color=ANSI_CYAN_BOLD)


def skip_line(message: str, *, detail: str | None = None) -> None:
    status_line("skip", message, color=ANSI_YELLOW_BOLD, detail=detail)


def pass_line(message: str) -> None:
    status_line("pass", message, color=ANSI_GREEN_BOLD)


def fail_line(message: str) -> None:
    status_line("fail", message, color=ANSI_RED_BOLD)


def info_line(message: str) -> None:
    status_line("info", message, color=ANSI_CYAN_BOLD)


BUILD_STEPS = [
    {
        "action": "record",
        "kind": "compile",
        "description": "record a fast terminal baseline and timeline sidecar",
    },
    {
        "action": "audio_generate",
        "kind": "compile",
        "description": "generate or reuse cached TTS fragments for each beat",
    },
    {
        "action": "audio_publish",
        "kind": "link",
        "description": "concatenate voiceover and write audio timing metadata",
    },
    {
        "action": "retime",
        "kind": "optimize",
        "description": "create the watchable cast using terminal and audio timing",
    },
    {
        "action": "publish_surface",
        "kind": "link",
        "description": "embed the finished recording in the selected publish surface",
    },
    {
        "action": "align_check",
        "kind": "validate",
        "description": "verify visible captions and commands still match the script",
    },
]

PUBLIC_ACTIONS = [action.value for action in StudioAction]

RECORD_ACTIONS = {
    "record": "record",
    "record_check": "check",
    "record_dry_run": "dry_run",
    "dry_run": "dry_run",
    "session": "session",
    "list": "list",
    "runs": "runs",
    "play": "play",
    "inspect": "inspect",
    "output": "output",
}
AUDIO_ACTIONS = {
    "sync_narration": "sync_narration",
    "audio_check": "check",
    "audio_dry_run": "dry_run",
    "audio_generate": "generate",
    "audio_publish": "publish",
    "generate": "generate",
    "publish": "publish",
}
RETIME_ACTIONS = {
    "retime": "retime",
    "retime_check": "check",
}
ALIGN_ACTIONS = {
    "align": "align",
    "align_check": "check",
}
WATCH_ARTIFACT_PREFIX = "/__studio_artifacts__/"
WATCH_HOST = "127.0.0.1"
TEXT_BROWSER_COMMANDS = {
    "elinks",
    "links",
    "links2",
    "lynx",
    "w3m",
}


def cfg_with_step(cfg: DictConfig, step: str, **overrides: object) -> DictConfig:
    data = OmegaConf.to_container(cfg, resolve=False, enum_to_str=True)
    if not isinstance(data, dict):
        raise StudioError("composed Hydra config must be a mapping")
    data["step"] = step
    for key, value in overrides.items():
        data[key] = str(value) if isinstance(value, Path) else value
    return OmegaConf.create(data)


def run_step(
    label: str,
    runner: ToolRunner,
    cfg: DictConfig,
    step: str,
    *,
    quiet: bool = False,
    config_overrides: dict[str, object] | None = None,
) -> str:
    if text_output_enabled(cfg) and not quiet:
        step_line(label)
    captured = io.StringIO()
    try:
        if quiet:
            with redirect_stdout(captured):
                result = runner(cfg_with_step(cfg, step, **(config_overrides or {})))
        else:
            result = runner(cfg_with_step(cfg, step, **(config_overrides or {})))
    except BaseException:
        output = captured.getvalue()
        if output:
            print(output, end="")
        raise
    output = captured.getvalue()
    if result != 0:
        if output:
            print(output, end="")
        raise StudioError(f"{label} failed with exit code {result}")
    return output


def run_record_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    spec: dict[str, Any] | None = None
    config = container_from_hydra_cfg(cfg)
    output_format = config.get("output_format", "text")
    verbose = bool_config(config, "verbose")
    if action == "record":
        spec = recording_spec_from_config(config, recording_id=None, overrides=())
    run_step(
        label or action.replace("_", " "), record.run_tool_from_hydra_cfg, cfg, action
    )
    if spec is not None:
        fingerprint_path = write_recording_fingerprint(spec)
        if output_format != "json" and verbose:
            pass_line(f"wrote recording fingerprint: {display_path(fingerprint_path)}")


def run_build_record_action(cfg: DictConfig, spec: dict[str, Any]) -> Path:
    config = container_from_hydra_cfg(cfg)
    output_format = config.get("output_format", "text")
    verbose = bool_config(config, "verbose")
    if not bool_config(config, "force"):
        reason = recording_skip_reason(spec)
        if reason is None:
            run_dir = latest_successful_recording_run_dir(spec)
            if run_dir is None:
                raise StudioError("recording was fresh but no successful run was found")
            if output_format != "json":
                cast_path = run_artifact_paths(run_dir, spec)["cast"]
                skip_line(
                    "record baseline cast",
                    detail=f"{display_path(cast_path)} is fresh",
                )
            return run_dir
        if output_format != "json" and verbose:
            info_line(f"record baseline cast: {reason}")
    run_dir = current_recording_run_dir(spec)
    paths = run_artifact_paths(run_dir, spec)
    run_step(
        "record baseline cast",
        record.run_tool_from_hydra_cfg,
        cfg,
        "record",
        config_overrides={"output": paths["cast"]},
    )
    return run_dir


def run_audio_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(label or f"audio {action}", audio.run_tool_from_hydra_cfg, cfg, action)


def status_message_from_output(output: str, status: str) -> str | None:
    prefix = f"{status:<5} "
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


@dataclass(frozen=True)
class BuildAudioStats:
    generated_segments: int
    reused_segments: int
    generated_timestamp_files: int
    tts_billable_characters: int | None
    transcription_audio_seconds: float | None
    estimated_tts_cost: str | None
    estimated_transcription_cost: str | None
    estimated_openai_cost: str | None
    transcription_cost_unavailable: bool = False
    timestamp_items: tuple[audio.AudioPlanItem, ...] = ()


def count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural or singular + 's'}"


def build_audio_stats(
    cfg: DictConfig, *, generate_needed: bool
) -> BuildAudioStats | None:
    if not generate_needed:
        return None
    config = container_from_hydra_cfg(cfg)
    try:
        spec = recording_spec_from_config(config, recording_id=None, overrides=())
        settings = audio.audio_settings(spec)
        if not settings.enabled:
            return None
        transcription = audio.transcription_settings(spec)
        segments = audio.load_narration_segments(spec)
        recording_id = audio.require_string(spec, "_recording_id", field="recording")
        plan = audio.plan_audio(recording_id, segments, settings)
    except (StudioConfigError, audio.AudioError):
        return None

    force = bool_config(config, "force")
    synthesized_items = audio.audio_items_requiring_synthesis(
        plan,
        settings,
        force=force,
    )
    materialized_items = [
        item
        for item in audio.audio_items_requiring_materialization(
            plan,
            settings,
            force=force,
        )
        if item not in synthesized_items
    ]
    timestamps = bool_config(config, "timestamps", default=True)
    timestamp_items = (
        audio.timestamp_items_requiring_generation(
            plan,
            transcription=transcription,
            force=force,
        )
        if timestamps
        else []
    )
    billing = (
        audio.estimate_openai_tts_billing(synthesized_items, settings)
        if settings.provider == "openai"
        else None
    )
    return BuildAudioStats(
        generated_segments=len(synthesized_items),
        reused_segments=len(materialized_items),
        generated_timestamp_files=len(timestamp_items),
        tts_billable_characters=(
            billing.billable_characters if billing is not None else None
        ),
        transcription_audio_seconds=None,
        estimated_tts_cost=(
            audio.format_usd(billing.estimated_cost_usd)
            if billing is not None
            else None
        ),
        estimated_transcription_cost=None,
        estimated_openai_cost=(
            audio.format_usd(billing.estimated_cost_usd)
            if billing is not None
            else None
        ),
        timestamp_items=tuple(timestamp_items),
    )


def build_audio_stats_with_transcription_cost(
    cfg: DictConfig,
    stats: BuildAudioStats,
) -> BuildAudioStats:
    if not stats.timestamp_items:
        return stats
    config = container_from_hydra_cfg(cfg)
    try:
        spec = recording_spec_from_config(config, recording_id=None, overrides=())
        settings = audio.audio_settings(spec)
        transcription = audio.transcription_settings(spec)
        billing = audio.estimate_openai_transcription_billing(
            list(stats.timestamp_items),
            transcription,
        )
    except (StudioConfigError, audio.AudioError):
        return replace(stats, transcription_cost_unavailable=True)

    tts_cost = None
    if stats.estimated_tts_cost is not None:
        if stats.tts_billable_characters is not None:
            tts_cost = (
                stats.tts_billable_characters
                * settings.tts_usd_per_1m_characters
                / 1_000_000
            )
    total_cost = billing.estimated_cost_usd + (tts_cost or 0)
    return replace(
        stats,
        transcription_audio_seconds=billing.audio_seconds,
        estimated_transcription_cost=audio.format_usd(billing.estimated_cost_usd),
        estimated_openai_cost=audio.format_usd(total_cost),
    )


def build_audio_stats_message(stats: BuildAudioStats) -> str | None:
    parts: list[str] = []
    if stats.generated_segments:
        parts.append(count_phrase(stats.generated_segments, "generated TTS segment"))
    if stats.reused_segments:
        parts.append(count_phrase(stats.reused_segments, "reused cached segment"))
    if stats.generated_timestamp_files:
        parts.append(
            count_phrase(stats.generated_timestamp_files, "generated timestamp file")
        )
    if stats.tts_billable_characters is not None:
        parts.append(f"{stats.tts_billable_characters} TTS chars")
    if stats.transcription_audio_seconds is not None:
        parts.append(f"{stats.transcription_audio_seconds:.1f}s transcribed audio")
    if (
        stats.estimated_tts_cost is not None
        and stats.estimated_transcription_cost is not None
        and stats.estimated_openai_cost is not None
    ):
        parts.append(
            "estimated OpenAI cost "
            f"TTS {stats.estimated_tts_cost}, "
            f"transcription {stats.estimated_transcription_cost}, "
            f"total {stats.estimated_openai_cost}"
        )
    elif stats.estimated_tts_cost is not None:
        parts.append(f"estimated TTS cost {stats.estimated_tts_cost}")
    elif stats.estimated_transcription_cost is not None:
        parts.append(
            f"estimated transcription cost {stats.estimated_transcription_cost}"
        )
    if stats.transcription_cost_unavailable:
        parts.append("transcription cost unavailable")
    if not parts:
        return None
    return "audio updated: " + ", ".join(parts)


def run_build_audio_actions(
    cfg: DictConfig,
    *,
    generate_needed: bool,
    publish_needed: bool,
    output: Path | None = None,
) -> None:
    if not generate_needed and not publish_needed:
        return
    config = container_from_hydra_cfg(cfg)
    verbose = bool_config(config, "verbose")
    if verbose:
        if generate_needed:
            run_audio_action(cfg, "generate", "generate audio")
        if publish_needed:
            run_step(
                "publish audio",
                audio.run_tool_from_hydra_cfg,
                cfg,
                "publish",
                config_overrides={"output": output} if output is not None else None,
            )
        return

    if text_output_enabled(cfg):
        step_line("audio")
    stats = build_audio_stats(cfg, generate_needed=generate_needed)
    generate_output = ""
    publish_output = ""
    if generate_needed:
        generate_output = run_step(
            "audio",
            audio.run_tool_from_hydra_cfg,
            cfg,
            "generate",
            quiet=True,
        )
        if stats is not None:
            stats = build_audio_stats_with_transcription_cost(cfg, stats)
    if publish_needed:
        publish_output = run_step(
            "audio",
            audio.run_tool_from_hydra_cfg,
            cfg,
            "publish",
            quiet=True,
            config_overrides={"output": output} if output is not None else None,
        )
    if text_output_enabled(cfg):
        message = build_audio_stats_message(stats) if stats is not None else None
        if message is None:
            message = status_message_from_output(generate_output, "pass")
        if message is None:
            message = status_message_from_output(publish_output, "pass")
        pass_line(message or "audio ready")


def audio_generate_needs_work(cfg: DictConfig) -> bool:
    config = container_from_hydra_cfg(cfg)
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    try:
        settings = audio.audio_settings(spec)
        if not settings.enabled:
            return False
        if bool_config(config, "force"):
            return True
        transcription = audio.transcription_settings(spec)
        segments = audio.load_narration_segments(spec)
        recording_id = audio.require_string(spec, "_recording_id", field="recording")
        plan = audio.plan_audio(recording_id, segments, settings)
    except audio.AudioError:
        return True
    if audio.audio_items_requiring_synthesis(plan, settings):
        return True
    if audio.audio_items_requiring_materialization(plan, settings):
        return True
    timestamps = bool_config(config, "timestamps", default=True)
    return bool(
        timestamps
        and audio.timestamp_items_requiring_generation(
            plan,
            transcription=transcription,
            force=False,
        )
    )


def audio_publish_needs_work(cfg: DictConfig, *, output: Path | None = None) -> bool:
    config = container_from_hydra_cfg(cfg)
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    try:
        settings = audio.audio_settings(spec)
        if not settings.enabled:
            return False
        if bool_config(config, "force"):
            return True
        segments = audio.load_narration_segments(spec)
        recording_id = audio.require_string(spec, "_recording_id", field="recording")
        plan = audio.plan_audio(recording_id, segments, settings)
        anchors_by_segment_id = audio.anchors_by_segment_id_from_spec(spec, plan)
        waits_by_segment_id = audio.waits_by_segment_id_from_spec(spec, plan)
        pause_after_by_segment_id = audio.pause_after_by_segment_id_from_spec(
            spec,
            plan,
        )
        published_audio = output or audio.output_audio_path(
            spec, recording_id, settings
        )
        published_metadata = audio.output_audio_metadata_path(spec, published_audio)
    except audio.AudioError:
        return True
    return not audio.published_audio_is_fresh(
        plan,
        published_audio,
        published_metadata,
        anchors_by_segment_id=anchors_by_segment_id,
        waits_by_segment_id=waits_by_segment_id,
        pause_after_by_segment_id=pause_after_by_segment_id,
    )


def retime_needs_work(
    cfg: DictConfig, *, paths: dict[str, Path] | None = None
) -> bool:
    config = container_from_hydra_cfg(cfg)
    if bool_config(config, "force"):
        return True
    try:
        if paths is None:
            spec = recording_spec_from_config(config, recording_id=None, overrides=())
            paths = artifact_paths(spec)
        retime_cast.require_fresh_retimed_cast(
            cast_path=paths["cast"],
            timeline_path=paths["timeline"],
            output_path=paths["retimed_cast"],
            audio_metadata_path=paths["audio_metadata"],
        )
    except (StudioError, retime_cast.RetimeError):
        return True
    return False


def staged_retimed_cast_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.build-{os.getpid()}.tmp")


def run_retime_action(
    cfg: DictConfig,
    action: str,
    label: str | None = None,
    *,
    cast: Path | None = None,
    timeline: Path | None = None,
    output: Path | None = None,
    quiet: bool = False,
) -> str:
    config_overrides = {}
    if cast is not None:
        config_overrides["cast"] = cast
    if timeline is not None:
        config_overrides["timeline"] = timeline
    if output is not None:
        config_overrides["output"] = output
    return run_step(
        label or f"retime {action}",
        retime_cast.run_tool_from_hydra_cfg,
        cfg,
        action,
        quiet=quiet,
        config_overrides=config_overrides or None,
    )


def run_align_action(
    cfg: DictConfig,
    action: str,
    label: str | None = None,
    *,
    cast: Path | None = None,
) -> None:
    config_overrides = {"cast": cast} if cast is not None else None
    run_step(
        label or f"align {action}",
        align_cast.run_tool_from_hydra_cfg,
        cfg,
        action,
        config_overrides=config_overrides,
    )


def bool_config(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise StudioError(f"{key} must be a boolean")
    return value


def action_help() -> str:
    actions = ", ".join(PUBLIC_ACTIONS)
    return (
        f"user-facing actions: {actions}\n"
        "omit action for the default build; use dry_run=true to preview the build graph"
    )


def enum_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (StudioAction, StudioStep)):
        return value.value
    return None


def validate_action(value: object) -> str:
    if value is None:
        return StudioAction.build.value
    normalized = enum_value(value)
    if normalized is None:
        raise StudioError("action must be a string\n" + action_help())
    if not normalized:
        raise StudioError("action cannot be empty\n" + action_help())
    if normalized not in PUBLIC_ACTIONS:
        raise StudioError(f"unknown action: {normalized}\n" + action_help())
    return normalized


def validate_step(value: object) -> str | None:
    if value is None:
        return None
    normalized = enum_value(value)
    if normalized is None:
        raise StudioError("step must be a string")
    if not normalized:
        raise StudioError("step cannot be empty")
    step_values = [step.value for step in StudioStep]
    if normalized not in step_values:
        raise StudioError(
            "unknown internal step: "
            f"{normalized}\ninternal steps: {', '.join(step_values)}"
        )
    return normalized


def display_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        return str(candidate.relative_to(retime_cast.REPO_ROOT))
    except ValueError:
        return str(candidate)


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def recording_id_from_value(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def print_available_recording_scripts(
    *,
    selected_required: bool,
    config: dict[str, Any] | None = None,
) -> int:
    recording_dir = recording_script_dir_from_config(config)
    recording_ids = list_recording_ids(recording_dir)
    if selected_required:
        print("No recording script selected.")
    if recording_ids:
        print("Available recording scripts:")
        for recording_id in recording_ids:
            print(f"  {recording_id}")
        if selected_required:
            print()
            print(f"Run with: studio recording={recording_ids[0]}")
    else:
        print(f"No recording scripts found in {recording_dir}.")
    return 1 if selected_required else 0


BOOTSTRAP_WORKSPACE_CONFIG = """\
capture:
  window_size: 80x20
  headless: true
  baseline_compressed: true
style:
  color: true
  typing: true
audio:
  enabled: false
  provider: openai
  env: OPENAI_API_KEY
  model: gpt-4o-mini-tts
  voice: marin
  format: mp3
"""


def bootstrap_recording_text(recording_id: str, title: str) -> str:
    return f"""\
---
id: {recording_id}
title: {title}
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${{outputs.dir}}/${{id}}.html
---

# {title}

This Markdown file is the source for one generated terminal video.

The YAML header names the recording, chooses output paths, and declares where
the finished video can be published. The prose explains the walkthrough for
readers and future maintainers. The fenced `studio-directive` blocks tell
OmegaFlow what to record.

```yaml studio-directive
scene: {title}
```

The scene is the title shown by the player. Beats are the steps in the video.
This beat runs a small shell script kept in this video's `scripts/` directory.

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Run the support script and verify the terminal output.
  caption: A one-command terminal recording.
  actions:
  - commands:
    - run_file: scripts/hello.sh
      display: bash scripts/hello.sh
      expect:
        output_contains:
        - hello from {recording_id}
```

Publish surfaces in the header let the same recording write a standalone HTML
page. Add a docs surface when you want the build to update a documentation page.
"""


def bootstrap_support_script_text(recording_id: str) -> str:
    return f"""\
#!/usr/bin/env bash
set -euo pipefail

printf 'hello from {recording_id}\\n'
"""


def bootstrap_workspace_path(config: dict[str, Any]) -> Path:
    value = optional_string(config.get("workspace"))
    if value is None:
        return recording_script_dir_from_config(config)
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return studio_config_module.PROJECT_ROOT / path


def write_bootstrap_file(
    path: Path,
    text: str,
    *,
    executable: bool = False,
    force: bool = False,
) -> str:
    existed = path.exists()
    if existed and not force:
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)
    return "updated" if existed else "created"


def run_bootstrap(config: dict[str, Any]) -> int:
    workspace = bootstrap_workspace_path(config)
    recording_id = recording_id_from_value(config.get("recording")) or "quickstart"
    if not is_valid_recording_id(recording_id):
        raise StudioError(
            "bootstrap recording id must be a lowercase kebab-case path"
        )
    title = recording_id.rsplit("/", 1)[-1].replace("-", " ").title()
    force = bool_config(config, "force")
    dry_run = bool_config(config, "dry_run")

    writes = [
        (workspace / "config.yaml", BOOTSTRAP_WORKSPACE_CONFIG, False),
        (
            workspace / recording_id / "omegaflow.md",
            bootstrap_recording_text(recording_id, title),
            False,
        ),
        (
            workspace / recording_id / "scripts" / "hello.sh",
            bootstrap_support_script_text(recording_id),
            True,
        ),
    ]

    print(f"workspace {display_path(workspace)}")
    if dry_run:
        print("dry run")
    for path, text, executable in writes:
        if dry_run:
            status = "would update" if path.exists() else "would create"
            print(f"{status:>12} {display_path(path)}")
        else:
            status = write_bootstrap_file(
                path,
                text,
                executable=executable,
                force=force,
            )
            print(f"{status:>7} {display_path(path)}")
    print()
    print(f"next    studio recording={recording_id} action=build")
    return 0


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StudioError(f"{field} must be a mapping")
    return value


def artifact_paths(spec: dict[str, Any]) -> dict[str, Path]:
    recording_id = str(spec["_recording_id"])
    cast_path = retime_cast.cast_path_from_manifest(spec)
    timeline_path = retime_cast.timeline_path_for_cast(cast_path)
    retimed_path = retime_cast.output_path_from_manifest(spec, cast_path)
    settings = audio.audio_settings(spec)
    audio_path = audio.output_audio_path(spec, recording_id, settings)
    audio_metadata_path = audio.output_audio_metadata_path(spec, audio_path)
    narration_path = audio.narration_config_path(recording_id, spec)
    audio_cache = settings.cache_dir / recording_id
    return {
        "cast": cast_path,
        "timeline": timeline_path,
        "retimed_cast": retimed_path,
        "audio": audio_path,
        "audio_metadata": audio_metadata_path,
        "narration_config": narration_path,
        "audio_cache": audio_cache,
    }


def current_recording_run_dir(spec: dict[str, Any]) -> Path:
    return record.run_artifact_dir(spec)


def latest_successful_recording_run_dir(spec: dict[str, Any]) -> Path | None:
    recording_id = str(spec["_recording_id"])
    try:
        return record.find_latest_run_dir(
            recording_id,
            artifact="success",
            data_dir=record.recording_data_dir(spec),
        )
    except record.RecordingError:
        return None


def run_artifact_paths(run_dir: Path, spec: dict[str, Any]) -> dict[str, Path]:
    recording_id = str(spec["_recording_id"])
    settings = audio.audio_settings(spec)
    audio_path = run_dir / "audio" / f"{recording_id}.{settings.format}"
    return {
        "cast": run_dir / "recording.cast",
        "timeline": run_dir / "recording.timeline.jsonl",
        "recording_fingerprint": run_dir / "recording.fingerprint.json",
        "retimed_cast": run_dir / "recording.retimed.cast",
        "audio": audio_path,
        "audio_metadata": audio_path.with_suffix(".json"),
        "narration_config": audio.narration_config_path(recording_id, spec),
        "audio_cache": settings.cache_dir / recording_id,
    }


def latest_run_artifact_paths(spec: dict[str, Any]) -> dict[str, Path] | None:
    run_dir = latest_successful_recording_run_dir(spec)
    if run_dir is None:
        return None
    return run_artifact_paths(run_dir, spec)


def remove_unused_empty_run_dir(spec: dict[str, Any], *, used_run_dir: Path) -> None:
    current_run_dir = current_recording_run_dir(spec)
    if current_run_dir.resolve() == used_run_dir.resolve():
        return
    try:
        current_run_dir.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return


def recording_fingerprint_path(cast_path: Path) -> Path:
    return cast_path.with_suffix(".recording.json")


def publish_artifact_paths(spec: dict[str, Any]) -> dict[str, Path]:
    return artifact_paths(spec) | {
        "recording_fingerprint": recording_fingerprint_path(
            retime_cast.cast_path_from_manifest(spec)
        ),
    }


def clean_artifact_paths(spec: dict[str, Any]) -> dict[str, Path]:
    cast_path = retime_cast.cast_path_from_manifest(spec)
    return {
        "baseline_cast": cast_path,
        "timeline": retime_cast.timeline_path_for_cast(cast_path),
        "recording_fingerprint": recording_fingerprint_path(cast_path),
        "retimed_cast": retime_cast.output_path_from_manifest(spec, cast_path),
    }


def normalize_fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not (
                str(key).startswith("_")
                and str(key) not in FINGERPRINT_PRIVATE_SPEC_KEYS
            )
        }
    if isinstance(value, list):
        return [normalize_fingerprint_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_fingerprint_value(item) for item in value]
    if isinstance(value, Path):
        return display_path(value)
    return value


def capture_fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): capture_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in CAPTURE_FINGERPRINT_IGNORED_KEYS
            and not (
                str(key).startswith("_")
                and str(key) not in FINGERPRINT_PRIVATE_SPEC_KEYS
            )
        }
    if isinstance(value, list):
        return [capture_fingerprint_value(item) for item in value]
    if isinstance(value, tuple):
        return [capture_fingerprint_value(item) for item in value]
    if isinstance(value, Path):
        return display_path(value)
    return value


def collect_run_file_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            if key == "run_file" and isinstance(item, str) and item:
                paths.append(item)
            else:
                paths.extend(collect_run_file_values(item))
        return paths
    if isinstance(value, list):
        paths = []
        for item in value:
            paths.extend(collect_run_file_values(item))
        return paths
    return []


def fingerprint_dependency_paths(spec: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    manifest_path = optional_string(spec.get("_manifest_path"))
    if manifest_path is not None:
        paths.append(retime_cast.relative_path(manifest_path))
    for value in collect_run_file_values(spec):
        paths.append(record.run_file_path(value, spec))
    paths.extend(
        [
            Path(__file__),
            Path(record.__file__),
            Path(studio_config_module.__file__),
        ]
    )
    return sorted(set(paths), key=lambda path: display_path(path) or str(path))


def file_fingerprint_entry(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": display_path(path)}
    if not path.exists():
        entry["missing"] = True
        return entry
    if not path.is_file():
        entry["type"] = "non-file"
        return entry
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entry


def recording_fingerprint_payload(spec: dict[str, Any]) -> dict[str, Any]:
    spec_payload = capture_fingerprint_value(spec)
    dependencies = [
        file_fingerprint_entry(path) for path in fingerprint_dependency_paths(spec)
    ]
    fingerprint_input = {
        "version": RECORDING_FINGERPRINT_VERSION,
        "spec": spec_payload,
        "dependencies": dependencies,
    }
    encoded = json.dumps(
        fingerprint_input, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return {
        "version": RECORDING_FINGERPRINT_VERSION,
        "recording": str(spec.get("_recording_id") or spec.get("id") or ""),
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "spec_sha256": hashlib.sha256(
            json.dumps(
                spec_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest(),
        "dependencies": dependencies,
    }


def dependency_missing(payload: dict[str, Any]) -> str | None:
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        if dependency.get("missing"):
            return str(dependency.get("path") or "unknown dependency")
    return None


def read_recording_fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise StudioError(
            f"invalid recording fingerprint JSON: {display_path(path)}"
        ) from exc
    if not isinstance(payload, dict):
        raise StudioError(
            f"recording fingerprint must be a mapping: {display_path(path)}"
        )
    return payload


def write_recording_fingerprint(
    spec: dict[str, Any], *, fingerprint_path: Path | None = None
) -> Path:
    if fingerprint_path is None:
        cast_path = retime_cast.cast_path_from_manifest(spec)
        fingerprint_path = recording_fingerprint_path(cast_path)
    payload = recording_fingerprint_payload(spec)
    missing = dependency_missing(payload)
    if missing is not None:
        raise StudioError(f"recording fingerprint dependency is missing: {missing}")
    fingerprint_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return fingerprint_path


def write_publish_recording_fingerprint(source: Path, destination: Path) -> Path:
    payload = read_recording_fingerprint(source)
    if payload is None:
        raise StudioError(f"recording fingerprint is missing: {display_path(source)}")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        raise StudioError(f"recording fingerprint dependencies must be a list: {source}")
    payload = dict(payload)
    payload["dependencies"] = [
        dependency
        for dependency in dependencies
        if isinstance(dependency, dict)
        and isinstance(dependency.get("path"), str)
        and not Path(dependency["path"]).is_absolute()
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def recording_skip_reason(spec: dict[str, Any]) -> str | None:
    paths = latest_run_artifact_paths(spec)
    if paths is None:
        return "successful recording run is missing"
    for name in ("cast", "timeline"):
        path = paths[name]
        if not path.exists():
            return f"{name} is missing"
    fingerprint_path = paths["recording_fingerprint"]
    try:
        stored = read_recording_fingerprint(fingerprint_path)
    except StudioError as exc:
        return str(exc)
    if stored is None:
        return "recording fingerprint is missing"
    current = recording_fingerprint_payload(spec)
    missing = dependency_missing(current)
    if missing is not None:
        return f"recording dependency is missing: {missing}"
    if stored.get("version") != RECORDING_FINGERPRINT_VERSION:
        return "recording fingerprint version changed"
    if stored.get("fingerprint") != current["fingerprint"]:
        return "recording fingerprint changed"
    return None


def publish_config(spec: dict[str, Any]) -> dict[str, Any]:
    publish = spec.get("publish")
    if publish is None:
        return {}
    return as_mapping(publish, field="publish")


def selected_surface_name(config: dict[str, Any], spec: dict[str, Any]) -> str | None:
    surface_override = config.get("surface")
    if surface_override is not None:
        if not isinstance(surface_override, str) or not surface_override:
            raise StudioError("surface must be a non-empty string or null")
        return surface_override
    publish = publish_config(spec)
    default = publish.get("default")
    if default is None:
        return None
    if not isinstance(default, str) or not default:
        raise StudioError("publish.default must be a non-empty string")
    return default


def build_publish_surface_names(
    config: dict[str, Any], spec: dict[str, Any]
) -> list[str]:
    surface_override = config.get("surface")
    if surface_override is not None:
        selected = selected_surface_name(config, spec)
        return [selected] if selected is not None else []
    publish = publish_config(spec)
    on_build = publish.get("on_build", True)
    if not isinstance(on_build, bool):
        raise StudioError("publish.on_build must be a boolean")
    if not on_build:
        return []
    configured = publish.get("build_surfaces")
    if configured is None:
        selected = selected_surface_name(config, spec)
        return [selected] if selected is not None else []
    if not isinstance(configured, list):
        raise StudioError("publish.build_surfaces must be a list")
    names: list[str] = []
    for item in configured:
        if not isinstance(item, str) or not item:
            raise StudioError(
                "publish.build_surfaces entries must be non-empty strings"
            )
        names.append(item)
    return names


def selected_surface(
    config: dict[str, Any],
    spec: dict[str, Any],
    *,
    surface_name: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    surface_name = surface_name or selected_surface_name(config, spec)
    if surface_name is None:
        return None
    publish = publish_config(spec)
    surfaces = as_mapping(publish.get("surfaces"), field="publish.surfaces")
    surface = surfaces.get(surface_name)
    if not isinstance(surface, dict):
        raise StudioError(f"publish surface not found: {surface_name}")
    return surface_name, surface


def build_plan(cfg: DictConfig, config: dict[str, Any]) -> dict[str, Any]:
    spec = load_recording_spec_from_hydra_cfg(cfg)
    paths = run_artifact_paths(current_recording_run_dir(spec), spec)
    publish_paths = publish_artifact_paths(spec)
    manifest_path = optional_string(spec.get("_manifest_path"))
    script_path = optional_string(spec.get("script"))
    surface_info: list[dict[str, Any]] = []
    for surface_name in build_publish_surface_names(config, spec):
        surface = selected_surface(config, spec, surface_name=surface_name)
        if surface is None:
            continue
        _surface_name, surface_config = surface
        surface_info.append(
            {
                "name": surface_name,
                "type": optional_string(surface_config.get("type")),
                "file": display_path(optional_string(surface_config.get("file"))),
                "placeholder": optional_string(surface_config.get("placeholder")),
            }
        )

    return {
        "recording": str(spec["_recording_id"]),
        "title": optional_string(spec.get("title")),
        "inputs": {
            "recording_script": display_path(script_path),
            "recording_source": display_path(manifest_path),
        },
        "outputs": {
            "baseline_cast": display_path(paths["cast"]),
            "timeline": display_path(paths["timeline"]),
            "recording_fingerprint": display_path(paths["recording_fingerprint"]),
            "audio_fragments": display_path(paths["audio_cache"] / "*.mp3"),
            "voiceover": display_path(paths["audio"]),
            "audio_metadata": display_path(paths["audio_metadata"]),
            "retimed_cast": display_path(paths["retimed_cast"]),
        },
        "publish": {
            "on_build": bool(publish_config(spec).get("on_build", True)),
            "surfaces": surface_info,
            "targets": {
                "baseline_cast": display_path(publish_paths["cast"]),
                "timeline": display_path(publish_paths["timeline"]),
                "recording_fingerprint": display_path(
                    publish_paths["recording_fingerprint"]
                ),
                "voiceover": display_path(publish_paths["audio"]),
                "audio_metadata": display_path(publish_paths["audio_metadata"]),
                "retimed_cast": display_path(publish_paths["retimed_cast"]),
            },
        },
        "steps": BUILD_STEPS,
    }


def print_build_plan(plan: dict[str, Any]) -> None:
    title = plan.get("title") or plan["recording"]
    print(f"Build dry run: {title}")
    print()
    print("Inputs:")
    for name, value in plan["inputs"].items():
        print(f"  {name}: {value}")
    print()
    print("Outputs:")
    for name, value in plan["outputs"].items():
        print(f"  {name}: {value}")
    publish = plan.get("publish")
    if isinstance(publish, dict) and publish.get("surfaces"):
        print()
        print("Publish surfaces:")
        for surface in publish["surfaces"]:
            print(f"  {surface['name']}:")
            print(f"    type: {surface['type']}")
            print(f"    file: {surface['file']}")
            if surface.get("placeholder"):
                print(f"    placeholder: {surface['placeholder']}")
        print()
        print("Publish targets:")
        for name, value in publish["targets"].items():
            print(f"  {name}: {value}")
    print()
    print("Pipeline:")
    for index, step in enumerate(plan["steps"], 1):
        print(
            f"  {index}. {step['action']} " f"({step['kind']}): {step['description']}"
        )
    print()
    print("No commands were run.")


def run_build_dry_run(cfg: DictConfig, config: dict[str, Any]) -> int:
    plan = build_plan(cfg, config)
    if config.get("output_format") == "json":
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print_build_plan(plan)
    return 0


def clean_recording_outputs(config: dict[str, Any]) -> list[Path]:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    removed: list[Path] = []
    for path in clean_artifact_paths(spec).values():
        if not path.exists():
            continue
        if path.is_dir():
            raise StudioError(f"refusing to remove directory: {display_path(path)}")
        path.unlink()
        removed.append(path)
    return removed


def run_clean(config: dict[str, Any]) -> int:
    removed = clean_recording_outputs(config)
    removed_display = [display_path(path) for path in removed]
    if config.get("output_format") == "json":
        print(
            json.dumps(
                {
                    "removed": removed_display,
                    "retained": [
                        "audio",
                        "audio_metadata",
                        "audio_cache",
                        "recording_runs",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    step_line("clean recording outputs")
    if removed_display:
        for path in removed_display:
            pass_line(f"removed {path}")
    else:
        skip_line("nothing to clean")
    info_line("retained audio outputs, audio metadata, audio cache, and recording runs")
    return 0


def site_url(path: Path) -> str:
    static_root = retime_cast.REPO_ROOT / "website" / "static"
    try:
        return "/" + path.relative_to(static_root).as_posix()
    except ValueError:
        return display_path(path) or str(path)


def first_narration_text(spec: dict[str, Any], segment_id: str | None) -> str | None:
    if not segment_id:
        return None
    narration = spec.get("narration")
    if not isinstance(narration, dict):
        return None
    beats = narration.get("beats")
    if not isinstance(beats, list):
        return None
    for beat in beats:
        if not isinstance(beat, dict) or beat.get("id") != segment_id:
            continue
        text = beat.get("text")
        return text if isinstance(text, str) and text else None
    return None


def player_params(
    spec: dict[str, Any],
    surface: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, str]:
    title = optional_string(spec.get("title")) or str(spec["_recording_id"])
    intro_segment = optional_string(surface.get("intro_segment"))
    intro = optional_string(surface.get("intro")) or first_narration_text(
        spec,
        intro_segment,
    )
    params = {
        "title": title,
        "src": site_url(paths["retimed_cast"]),
    }
    if audio.audio_settings(spec).enabled:
        params["audio"] = site_url(paths["audio"])
        params["audioMeta"] = site_url(paths["audio_metadata"])
    if intro:
        params["intro"] = intro
    if intro_segment:
        params["introSegment"] = intro_segment
    return params


def render_docusaurus_mdx(
    spec: dict[str, Any],
    surface: dict[str, Any],
    paths: dict[str, Path] | None = None,
) -> str:
    component = optional_string(surface.get("component")) or "TerminalCast"
    params = player_params(spec, surface, paths or artifact_paths(spec))
    lines = [f"<{component}"]
    for key, value in params.items():
        lines.append(f"  {key}={json.dumps(value)}")
    lines.append("/>")
    return "\n".join(lines)


def ensure_mdx_component_import(
    text: str,
    *,
    component: str,
    import_path: str,
) -> str:
    import_line = f"import {component} from {json.dumps(import_path)};"
    if import_line in text:
        return text
    if f"import {component} " in text:
        return text
    if text.startswith("---\n"):
        end_index = text.find("\n---\n", 4)
        if end_index >= 0:
            insert_index = end_index + len("\n---\n")
            return text[:insert_index] + "\n" + import_line + "\n" + text[insert_index:]
    return import_line + "\n\n" + text


def render_html_embed(
    spec: dict[str, Any],
    surface: dict[str, Any],
    paths: dict[str, Path] | None = None,
) -> str:
    params = player_params(spec, surface, paths or artifact_paths(spec))
    attributes = {
        "title": params["title"],
        "src": params["src"],
        "player": "/cast-player.html",
    }
    if "audio" in params:
        attributes["audio"] = params["audio"]
    if "audioMeta" in params:
        attributes["audio-meta"] = params["audioMeta"]
    if "intro" in params:
        attributes["intro"] = params["intro"]
    if "introSegment" in params:
        attributes["intro-segment"] = params["introSegment"]
    rendered_attributes = " ".join(
        f'{key}="{html.escape(value, quote=True)}"' for key, value in attributes.items()
    )
    return (
        '<script src="/cast-player-embed.js"></script>\n'
        f"<cast-player-embed {rendered_attributes}></cast-player-embed>"
    )


def render_standalone_html(
    spec: dict[str, Any],
    surface: dict[str, Any],
    paths: dict[str, Path] | None = None,
) -> str:
    title = optional_string(spec.get("title")) or str(spec["_recording_id"])
    embed = render_html_embed(spec, surface, paths)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"    <title>{html.escape(title)}</title>\n"
        "    <style>\n"
        "      body { margin: 0; background: #11131a; }\n"
        "      iframe { width: 100vw; height: 100vh; border: 0; display: block; }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        f"    {embed}\n"
        "  </body>\n"
        "</html>\n"
    )


class StudioWatchRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        artifacts: dict[str, Path],
        directory: str,
        **kwargs: Any,
    ) -> None:
        self.artifacts = artifacts
        super().__init__(*args, directory=directory, **kwargs)

    def translate_path(self, path: str) -> str:
        request_path = urlparse(path).path
        if request_path.startswith(WATCH_ARTIFACT_PREFIX):
            artifact_name = Path(unquote(request_path)).stem
            artifact = self.artifacts.get(artifact_name)
            if artifact is not None:
                return str(artifact)
        return super().translate_path(path)

    def log_message(self, format: str, *args: object) -> None:
        return


def replace_placeholder(text: str, placeholder: str, replacement: str) -> str:
    start = f"<!-- studio:{placeholder}:start -->"
    end = f"<!-- studio:{placeholder}:end -->"
    start_index = text.find(start)
    end_index = text.find(end)
    if start_index < 0 or end_index < 0 or end_index < start_index:
        raise StudioError(f"placeholder {placeholder!r} not found")
    return (
        text[: start_index + len(start)]
        + "\n"
        + replacement.rstrip()
        + "\n"
        + text[end_index:]
    )


def copy_publish_artifact(source: Path, destination: Path) -> Path | None:
    if not source.exists():
        raise StudioError(f"publish artifact is missing: {display_path(source)}")
    if source.resolve() == destination.resolve():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def publish_artifacts_from_run(
    spec: dict[str, Any],
    source_paths: dict[str, Path],
) -> list[Path]:
    targets = publish_artifact_paths(spec)
    required = [
        "cast",
        "timeline",
        "recording_fingerprint",
        "retimed_cast",
    ]
    if audio.audio_settings(spec).enabled:
        required.extend(["audio", "audio_metadata"])
    written: list[Path] = []
    for name in required:
        if name in {"audio_metadata", "recording_fingerprint"}:
            continue
        path = copy_publish_artifact(source_paths[name], targets[name])
        if path is not None:
            written.append(path)
    if "recording_fingerprint" in required:
        written.append(
            write_publish_recording_fingerprint(
                source_paths["recording_fingerprint"],
                targets["recording_fingerprint"],
            )
        )
    if "audio_metadata" in required:
        metadata = json.loads(source_paths["audio_metadata"].read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise StudioError(
                "publish audio metadata must be a mapping: "
                f"{display_path(source_paths['audio_metadata'])}"
            )
        metadata["audio"] = display_path(targets["audio"])
        segments = metadata.get("segments")
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                timestamp = segment.get("timestamps")
                if not isinstance(timestamp, str) or not timestamp:
                    continue
                source_timestamp = retime_cast.relative_path(timestamp)
                target_timestamp = targets["audio_metadata"].with_name(
                    source_timestamp.name
                )
                copied = copy_publish_artifact(source_timestamp, target_timestamp)
                if copied is not None:
                    written.append(copied)
                segment["timestamps"] = display_path(target_timestamp)
        targets["audio_metadata"].parent.mkdir(parents=True, exist_ok=True)
        targets["audio_metadata"].write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        shutil.copystat(source_paths["audio_metadata"], targets["audio_metadata"])
        written.append(targets["audio_metadata"])
    return written


def publish_surface(
    config: dict[str, Any],
    *,
    source_paths: dict[str, Path] | None = None,
    surface_name: str | None = None,
    validation_retimed_cast: Path | None = None,
) -> Path | None:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    selected = selected_surface(config, spec, surface_name=surface_name)
    if selected is None:
        return None
    paths = source_paths or artifact_paths(spec)
    publish_paths = publish_artifact_paths(spec)
    try:
        retime_cast.require_fresh_retimed_cast(
            cast_path=paths["cast"],
            timeline_path=paths["timeline"],
            output_path=validation_retimed_cast or paths["retimed_cast"],
            audio_metadata_path=paths["audio_metadata"],
        )
    except retime_cast.RetimeError as exc:
        raise StudioError(str(exc)) from exc
    if source_paths is not None:
        publish_artifacts_from_run(spec, source_paths)
    _surface_name, surface = selected
    surface_type = optional_string(surface.get("type"))
    file_name = optional_string(surface.get("file"))
    if not surface_type:
        raise StudioError("publish surface type must be a non-empty string")
    if not file_name:
        raise StudioError("publish surface file must be a non-empty string")
    path = retime_cast.relative_path(file_name)

    if surface_type == "docusaurus_mdx":
        placeholder = optional_string(surface.get("placeholder"))
        if not placeholder:
            raise StudioError("docusaurus_mdx surfaces require a placeholder")
        original = path.read_text(encoding="utf-8")
        component = optional_string(surface.get("component")) or "TerminalCast"
        import_path = (
            optional_string(surface.get("component_import"))
            or f"@site/src/components/{component}"
        )
        rendered = render_docusaurus_mdx(spec, surface, publish_paths)
        updated = ensure_mdx_component_import(
            replace_placeholder(original, placeholder, rendered),
            component=component,
            import_path=import_path,
        )
        if updated == original:
            return None
        path.write_text(updated, encoding="utf-8")
        return path
    if surface_type == "plain_html":
        placeholder = optional_string(surface.get("placeholder"))
        if not placeholder:
            raise StudioError("plain_html surfaces require a placeholder")
        original = path.read_text(encoding="utf-8")
        rendered = render_html_embed(spec, surface, publish_paths)
        updated = replace_placeholder(original, placeholder, rendered)
        if updated == original:
            return None
        path.write_text(updated, encoding="utf-8")
        return path
    if surface_type == "standalone_html":
        rendered = render_standalone_html(spec, surface, publish_paths)
        if path.exists() and path.read_text(encoding="utf-8") == rendered:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return path
    raise StudioError(f"unsupported publish surface type: {surface_type}")


def run_publish_surface(
    cfg: DictConfig,
    *,
    source_paths: dict[str, Path] | None = None,
    surface_name: str | None = None,
    validation_retimed_cast: Path | None = None,
) -> None:
    config = container_from_hydra_cfg(cfg)
    path = publish_surface(
        config,
        source_paths=source_paths,
        surface_name=surface_name,
        validation_retimed_cast=validation_retimed_cast,
    )
    if path is not None and text_output_enabled(cfg):
        step_line("publish surface")
        pass_line(f"wrote publish surface: {display_path(path)}")


def run_play(cfg: DictConfig, config: dict[str, Any]) -> int:
    cast_override = config.get("cast")
    run_id = config.get("run_id")
    recording_id = recording_id_from_value(config.get("recording"))
    if cast_override is not None or run_id is not None or recording_id is None:
        run_record_action(cfg, "play", "play")
        return 0

    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    paths = latest_run_artifact_paths(spec)
    if paths is None:
        raise StudioError(
            "no successful recording run found; run studio action=build first"
        )
    cast_path = paths["retimed_cast"]
    if not cast_path.exists():
        raise StudioError(
            f"retimed cast not found: {display_path(cast_path)}; "
            "run studio action=build first"
        )
    if text_output_enabled(cfg):
        step_line(f"play retimed cast: {display_path(cast_path)}")
    record.check_asciinema()
    return subprocess.run(
        ["asciinema", "play", str(cast_path)],
        cwd=retime_cast.REPO_ROOT,
        check=False,
    ).returncode


def watch_artifact_url(path: Path, key: str, artifacts: dict[str, Path]) -> str:
    static_root = (retime_cast.REPO_ROOT / "website" / "static").resolve()
    resolved = path.resolve()
    try:
        return "/" + quote(resolved.relative_to(static_root).as_posix(), safe="/")
    except ValueError:
        artifacts[key] = resolved
        suffix = quote(resolved.suffix)
        return f"{WATCH_ARTIFACT_PREFIX}{quote(key)}{suffix}"


def watch_player_url_path(spec: dict[str, Any]) -> tuple[str, dict[str, Path]]:
    paths = latest_run_artifact_paths(spec)
    if paths is None:
        raise StudioError(
            "no successful recording run found; run studio action=build first"
        )
    required = {
        "cast": paths["retimed_cast"],
    }
    if audio.audio_settings(spec).enabled:
        required["audio"] = paths["audio"]
        required["audioMeta"] = paths["audio_metadata"]
    missing = [display_path(path) for path in required.values() if not path.exists()]
    if missing:
        formatted = ", ".join(path for path in missing if path is not None)
        raise StudioError(
            f"watch artifacts not found: {formatted}; "
            "run studio action=build first"
        )

    artifacts: dict[str, Path] = {}
    params = {
        "title": optional_string(spec.get("title")) or str(spec["_recording_id"]),
        "cast": watch_artifact_url(paths["retimed_cast"], "cast", artifacts),
    }
    if audio.audio_settings(spec).enabled:
        params["audio"] = watch_artifact_url(paths["audio"], "audio", artifacts)
        params["audioMeta"] = watch_artifact_url(
            paths["audio_metadata"],
            "audioMeta",
            artifacts,
        )
    return "/cast-player.html?" + urlencode(params), artifacts


def configured_browser_command_name() -> str | None:
    browser = os.environ.get("BROWSER")
    if not browser:
        return None
    first_entry = browser.split(os.pathsep, 1)[0].strip()
    if not first_entry:
        return None
    try:
        command = shlex.split(first_entry)[0]
    except ValueError:
        return None
    return Path(command).name.lower()


def should_auto_open_browser() -> bool:
    browser_command = configured_browser_command_name()
    if browser_command in TEXT_BROWSER_COMMANDS:
        return False
    if browser_command is not None:
        return True
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def running_under_wsl() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return False
    release = release.lower()
    return "microsoft" in release or "wsl" in release


def quote_powershell_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def open_wsl_host_browser(url: str) -> bool:
    wslview = shutil.which("wslview")
    if wslview is not None:
        subprocess.Popen(
            [wslview, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    powershell = shutil.which("powershell.exe")
    if powershell is not None:
        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Start-Process {quote_powershell_string(url)}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    return False


def open_watch_url(url: str) -> bool:
    if running_under_wsl():
        return open_wsl_host_browser(url)
    if not should_auto_open_browser():
        return False
    return webbrowser.open(url)


def run_watch_server(
    cfg: DictConfig,
    url_path: str,
    artifacts: dict[str, Path],
) -> int:
    static_root = retime_cast.REPO_ROOT / "website" / "static"

    def handler_factory(*args: Any, **kwargs: Any) -> StudioWatchRequestHandler:
        return StudioWatchRequestHandler(
            *args,
            artifacts=artifacts,
            directory=str(static_root),
            **kwargs,
        )

    with http.server.ThreadingHTTPServer((WATCH_HOST, 0), handler_factory) as server:
        url = f"http://{WATCH_HOST}:{server.server_port}{url_path}"
        if text_output_enabled(cfg):
            step_line("watch recording")
            pass_line(f"serving local player: {url}")
        opened = open_watch_url(url)
        if text_output_enabled(cfg):
            if opened:
                info_line("opened browser; press Ctrl-C to stop")
            else:
                info_line("open the URL in a browser; press Ctrl-C to stop")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            if text_output_enabled(cfg):
                info_line("stopped local player")
    return 0


def run_watch(cfg: DictConfig, config: dict[str, Any]) -> int:
    cast_override = config.get("cast")
    run_id = config.get("run_id")
    recording_id = recording_id_from_value(config.get("recording"))
    if cast_override is not None or run_id is not None or recording_id is None:
        raise StudioError(
            "watch requires a recording id and built recording artifacts; "
            "use action=play for preserved run casts"
        )

    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    url_path, artifacts = watch_player_url_path(spec)
    return run_watch_server(cfg, url_path, artifacts)


def studio_tool_command(recording_id: str, *overrides: str) -> str:
    parts = ["studio", f"recording={recording_id}", *overrides]
    return " ".join(shlex.quote(part) for part in parts)


def print_success_followups(cfg: DictConfig) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") == "json":
        return
    recording_value = OmegaConf.select(cfg, "recording")
    recording_id = recording_id_from_value(recording_value)
    if not isinstance(recording_id, str) or not recording_id:
        return
    print()
    print(color_text("next", ANSI_CYAN_BOLD) + " follow-up commands")
    print(
        "  "
        + color_text("play   ", ANSI_GREEN_BOLD)
        + " "
        + studio_tool_command(recording_id, "action=play")
    )
    print(
        "  "
        + color_text("watch  ", ANSI_GREEN_BOLD)
        + " "
        + studio_tool_command(recording_id, "action=watch")
    )
    print(
        "  "
        + color_text("inspect", ANSI_GREEN_BOLD)
        + " "
        + studio_tool_command(recording_id, "action=inspect")
    )


def format_elapsed(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minute_remainder = divmod(minutes, 60)
    return f"{int(hours)}h {int(minute_remainder)}m {remainder:.1f}s"


def print_build_elapsed(cfg: DictConfig, seconds: float, *, success: bool) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") == "json":
        return
    if success:
        pass_line(f"build completed after {format_elapsed(seconds)}")
    else:
        fail_line(f"build failed after {format_elapsed(seconds)}")


def run_build(cfg: DictConfig) -> int:
    started = time.monotonic()
    success = False
    config = container_from_hydra_cfg(cfg)
    spec = load_recording_spec_from_hydra_cfg(cfg)
    staged_retimed: Path | None = None
    try:
        run_dir = run_build_record_action(cfg, spec)
        paths = run_artifact_paths(run_dir, spec)
        generate_needed = audio_generate_needs_work(cfg)
        publish_needed = audio_publish_needs_work(cfg, output=paths["audio"])
        run_build_audio_actions(
            cfg,
            generate_needed=generate_needed,
            publish_needed=publish_needed,
            output=paths["audio"],
        )
        if retime_needs_work(cfg, paths=paths):
            retimed_path = paths["retimed_cast"]
            staged_retimed = staged_retimed_cast_path(retimed_path)
            if staged_retimed.exists():
                staged_retimed.unlink()
            verbose = bool_config(config, "verbose")
            if text_output_enabled(cfg) and not verbose:
                step_line("retime cast")
            run_retime_action(
                cfg,
                "retime",
                "retime cast",
                cast=paths["cast"],
                timeline=paths["timeline"],
                output=staged_retimed,
                quiet=not verbose,
            )
            if not staged_retimed.exists():
                raise StudioError(
                    "retime cast did not write expected output: "
                    f"{display_path(staged_retimed)}"
                )
            run_align_action(cfg, "check", "check alignment", cast=staged_retimed)
            staged_retimed.replace(retimed_path)
            staged_retimed = None
            if text_output_enabled(cfg) and not verbose:
                pass_line(f"wrote retimed cast: {display_path(retimed_path)}")
        else:
            run_align_action(cfg, "check", "check alignment", cast=paths["retimed_cast"])
        fingerprint_path = write_recording_fingerprint(
            spec,
            fingerprint_path=paths["recording_fingerprint"],
        )
        surface_names = build_publish_surface_names(config, spec)
        for surface_name in surface_names:
            run_publish_surface(
                cfg,
                source_paths=paths,
                surface_name=surface_name,
            )
        if text_output_enabled(cfg) and bool_config(config, "verbose"):
            pass_line(f"wrote recording fingerprint: {display_path(fingerprint_path)}")
        remove_unused_empty_run_dir(spec, used_run_dir=run_dir)
        success = True
    finally:
        if staged_retimed is not None and staged_retimed.exists():
            staged_retimed.unlink()
        print_build_elapsed(cfg, time.monotonic() - started, success=success)
    print_success_followups(cfg)
    return 0


def run_check(cfg: DictConfig) -> int:
    run_record_action(cfg, "check", "check recording")
    run_audio_action(cfg, "check", "check audio")
    run_retime_action(cfg, "check", "check retime")
    run_align_action(cfg, "check", "check alignment")
    return 0


def run_internal_step(cfg: DictConfig, config: dict[str, Any], step: str) -> int:
    if step in RECORD_ACTIONS:
        action = (
            "dry_run"
            if step == "record" and bool_config(config, "dry_run")
            else RECORD_ACTIONS[step]
        )
        run_record_action(cfg, action, step.replace("_", " "))
        return 0
    if step in AUDIO_ACTIONS:
        run_audio_action(cfg, AUDIO_ACTIONS[step], step.replace("_", " "))
        return 0
    if step in RETIME_ACTIONS:
        run_retime_action(cfg, RETIME_ACTIONS[step], step.replace("_", " "))
        return 0
    if step in ALIGN_ACTIONS:
        run_align_action(cfg, ALIGN_ACTIONS[step], step.replace("_", " "))
        return 0
    raise StudioError(f"unknown internal step: {step}")


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
    except StudioConfigError as exc:
        raise StudioError(str(exc)) from exc
    action = validate_action(config.get("action", "build"))
    step = validate_step(config.get("step"))

    if step is None and action == "list":
        return print_available_recording_scripts(
            selected_required=False,
            config=config,
        )

    if step is None and action == "bootstrap":
        return run_bootstrap(config)

    recording_required = step is not None or action in {
        "build",
        "check",
        "clean",
        "watch",
    }
    if recording_required and recording_id_from_value(config.get("recording")) is None:
        return print_available_recording_scripts(
            selected_required=True,
            config=config,
        )

    if step is None and action == "build" and bool_config(config, "dry_run"):
        return run_build_dry_run(cfg, config)

    if step is None and action == "clean":
        return run_clean(config)

    try:
        load_configured_env_file(config)
    except StudioConfigError as exc:
        raise StudioError(str(exc)) from exc

    if step is not None:
        return run_internal_step(cfg, config, step)

    if action == "build":
        return run_build(cfg)
    if action == "check":
        return run_check(cfg)
    if action == "play":
        return run_play(cfg, config)
    if action == "watch":
        return run_watch(cfg, config)

    if action in RECORD_ACTIONS:
        run_record_action(cfg, RECORD_ACTIONS[action], str(action).replace("_", " "))
        return 0

    raise StudioError(f"unknown studio action: {action}")


@hydra.main(
    version_base=None,
    config_path=str(CONFIG_DIR),
    config_name=STUDIO_CONFIG_NAME,
)
def main(cfg: DictConfig) -> None:
    use_color = record.host_color_enabled(sys.stderr)
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except record.RecordingInterrupted as exc:
        print(
            color_text(
                f"interrupted: {exc}",
                ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130) from exc
    except KeyboardInterrupt:
        print(
            color_text(
                "interrupted: studio run cancelled by user",
                ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as exc:
        print(
            color_text("error:", ANSI_RED_BOLD, enabled=use_color) + f" {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
