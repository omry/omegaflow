#!/usr/bin/env python3
"""Frontend CLI for OmegaFlow."""

from __future__ import annotations

import difflib
import hashlib
import html
import http.server
import io
import json
import ntpath
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, unquote, urlparse

import hydra
from omegaconf import DictConfig, OmegaConf

from . import audio
from . import record
from . import presentation_build
from . import studio_config as studio_config_module
from .capture import CaptureActionItem, capture_action_items
from .recording_plan import RecordingPlanError, normalize_recording_plan
from .studio_config import (
    CONFIG_DIR,
    STUDIO_CONFIG_NAME,
    RecordingSourceKind,
    StudioAction,
    StudioConfigError,
    StudioStep,
    configure_project_root,
    container_from_hydra_cfg,
    is_valid_recording_id,
    list_recording_ids,
    load_configured_env_file,
    load_recording_spec_from_hydra_cfg,
    project_config_searchpath_override,
    recording_collection_from_script,
    recording_script_dir_from_config,
    recording_source_kind,
    recording_spec_from_config,
    studio_data_dir_from_config,
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
from .tool_progress import LogProgressRenderer, ProgressBarRenderer


class StudioError(RuntimeError):
    pass


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


class BuildProgress:
    """One concise progress surface for the complete video build."""

    def __init__(
        self,
        *,
        total: int,
        stream: Any | None = None,
        interactive: bool | None = None,
        color: bool | None = None,
        active: bool = True,
        heartbeat_interval: float = 0.25,
    ) -> None:
        if total <= 0:
            raise ValueError("build progress total must be positive")
        if heartbeat_interval <= 0:
            raise ValueError("build progress heartbeat interval must be positive")
        self.total = total
        self.current = 0
        self.stream = stream or sys.stdout
        self.active = active
        self.heartbeat_interval = heartbeat_interval
        if interactive is None:
            isatty = getattr(self.stream, "isatty", None)
            interactive = bool(isatty and isatty())
        self.interactive = interactive
        self._bar = ProgressBarRenderer(
            stream=self.stream,
            interactive=interactive,
            enabled=color,
        )
        self._log = LogProgressRenderer(stream=self.stream, enabled=color)
        self._finished = False
        self._lock = threading.RLock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._active_message: str | None = None
        self._active_since = 0.0
        self._activity_step = 0

    def _event(self, message: str, *, active: bool = False) -> dict[str, Any]:
        event: dict[str, Any] = {
            "phase": "status",
            "status": "step",
            "message": message,
            "current": self.current,
            "total": self.total,
        }
        if active:
            event["active"] = True
            event["activity_elapsed"] = max(
                0.0, time.monotonic() - self._active_since
            )
            event["activity_step"] = self._activity_step
        return event

    def _activate(self, message: str) -> None:
        if message != self._active_message:
            self._active_message = message
            self._active_since = time.monotonic()
        if self.interactive and self._heartbeat_thread is None:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat,
                name="omegaflow-build-progress",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def _heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval):
            with self._lock:
                if self._finished or not self.active or self._active_message is None:
                    continue
                self._activity_step += 1
                event = self._event(self._active_message, active=True)
                event["transient"] = True
                self._bar.emit(event)

    def begin(self, message: str) -> None:
        with self._lock:
            if not self.active:
                return
            self._activate(message)
            event = self._event(message, active=True)
            if self.interactive:
                self._bar.emit(event)
            else:
                self._log.emit(event)

    def update(self, message: str) -> None:
        with self._lock:
            if self.active and self.interactive:
                self._activate(message)
                self._bar.emit(self._event(message, active=True))

    def advance(self, message: str, *, units: int = 1) -> None:
        if units < 0:
            raise ValueError("build progress units must be non-negative")
        with self._lock:
            self.current = min(self.total, self.current + units)
            self.update(message)

    def finish(self, *, completion: str | None = None) -> None:
        heartbeat_thread: threading.Thread | None
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._heartbeat_stop.set()
            heartbeat_thread = self._heartbeat_thread
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=max(1.0, self.heartbeat_interval * 2))
        with self._lock:
            if self.active and self.interactive:
                retain = self.current >= self.total
                if retain and completion:
                    event = self._event(
                        self._active_message or "Video ready",
                        active=False,
                    )
                    event["completion"] = completion
                    self._bar.emit(event)
                self._bar.finish(
                    replay=False,
                    retain=retain,
                )


PRESENTATION_BUILD_STEPS = [
    {
        "action": "record",
        "kind": "capture",
        "description": "capture terminal and browser beats in one persistent environment",
    },
    {
        "action": "audio_generate",
        "kind": "compile",
        "description": "generate or reuse narration-take audio and timestamps",
    },
    {
        "action": "presentation_compile",
        "kind": "compile",
        "description": "solve global timing and materialize beat-local presentation payloads",
    },
    {
        "action": "presentation_validate",
        "kind": "validate",
        "description": "validate the manifest, assets, hashes, and public-safety boundary",
    },
    {
        "action": "publish_surface",
        "kind": "link",
        "description": "atomically publish the bundle and selected embed surfaces",
    },
]

PUBLIC_ACTIONS = [action.value for action in StudioAction]

RECORD_ACTIONS = {
    "record": "record",
    "record_check": "check",
    "list": "list",
    "runs": "runs",
    "inspect": "inspect",
    "output": "output",
}
WATCH_ARTIFACT_PREFIX = "/__studio_artifacts__/"
WATCH_SNAPSHOT_PREFIX = "/__studio_snapshots__/"
WATCH_ROUTE_PREFIX = "/watch/"
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


def cfg_for_recording(cfg: DictConfig, recording_id: str) -> DictConfig:
    data = OmegaConf.to_container(cfg, resolve=False, enum_to_str=True)
    if not isinstance(data, dict):
        raise StudioError("composed Hydra config must be a mapping")
    data["recording"] = recording_id
    data["step"] = None
    return OmegaConf.create(data)


def run_step(
    label: str,
    runner: ToolRunner,
    cfg: DictConfig,
    step: str,
    *,
    quiet: bool = False,
    show_step: bool = True,
    config_overrides: dict[str, object] | None = None,
) -> str:
    if text_output_enabled(cfg) and not quiet and show_step:
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
    verbose = bool_config(config, "verbose")
    if action == "check":
        spec = recording_spec_from_config(config, recording_id=None, overrides=())
        normalized_recording_plan(spec)
        try:
            record.validate_manifest(spec)
            record.check_required_commands(spec)
            asciinema_version = record.check_asciinema(spec)
        except record.RecordingError as exc:
            raise StudioError(str(exc)) from exc
        if text_output_enabled(cfg):
            pass_line(f"recording source is valid: {spec['id']} ({asciinema_version})")
        return
    if action == "record":
        spec = recording_spec_from_config(config, recording_id=None, overrides=())
        plan = normalized_recording_plan(spec)
        run_dir = current_recording_run_dir(spec)
        if text_output_enabled(cfg):
            step_line("capture recording")
        presentation_build.capture_recording(
            spec,
            plan,
            run_dir,
            headed=bool_config(config, "headed"),
        )
        fingerprint_path = presentation_build.write_capture_fingerprint(
            spec, plan, run_dir
        )
        if text_output_enabled(cfg):
            pass_line(f"captured recording: {display_path(run_dir)}")
            if verbose:
                pass_line(
                    "wrote recording fingerprint: "
                    f"{display_path(fingerprint_path)}"
                )
        return
    run_step(
        label or action.replace("_", " "), record.run_tool_from_hydra_cfg, cfg, action
    )


def normalized_recording_plan(spec: dict[str, Any]) -> Any:
    try:
        return normalize_recording_plan(spec)
    except RecordingPlanError as exc:
        raise StudioError(str(exc)) from exc


def run_build_record_action(
    cfg: DictConfig,
    spec: dict[str, Any],
    plan: Any,
    *,
    progress: BuildProgress | None = None,
) -> Path:
    config = container_from_hydra_cfg(cfg)
    verbose = bool_config(config, "verbose")
    action_count = len(capture_action_items(plan))
    if progress is not None:
        noun = "action" if action_count == 1 else "actions"
        progress.begin(f"Recording workflow ({action_count} {noun})")
    if not bool_config(config, "force"):
        latest = latest_successful_recording_run_dir(spec)
        if latest is not None and presentation_build.capture_is_fresh(
            spec, plan, latest
        ):
            if progress is not None:
                progress.advance(
                    "Reused recorded workflow",
                    units=action_count,
                )
            elif text_output_enabled(cfg):
                skip_line(
                    "capture recording",
                    detail=f"{display_path(latest)} is fresh",
                )
            return latest
        if latest is not None and text_output_enabled(cfg) and verbose:
            info_line("capture recording: capture fingerprint changed")
    run_dir = current_recording_run_dir(spec)
    if progress is None and text_output_enabled(cfg):
        step_line("capture recording")

    def on_capture_progress(
        state: str,
        action: CaptureActionItem,
        _current: int,
        _total: int,
    ) -> None:
        if progress is None:
            return
        beat_label = action.beat_heading or action.beat_id
        message = f"Record: {beat_label} · {action.label}"
        if state == "completed":
            progress.advance(message)
        else:
            progress.update(message)

    presentation_build.capture_recording(
        spec,
        plan,
        run_dir,
        headed=bool_config(config, "headed"),
        on_progress=on_capture_progress if progress is not None else None,
    )
    presentation_build.write_capture_fingerprint(spec, plan, run_dir)
    if progress is None and text_output_enabled(cfg):
        pass_line(f"captured recording: {display_path(run_dir)}")
    return run_dir


def run_audio_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(label or f"audio {action}", audio.run_tool_from_hydra_cfg, cfg, action)


def bool_config(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise StudioError(f"{key} must be a boolean")
    return value


def bootstrap_dry_run_mode(value: object) -> str | None:
    if value is False or value is None:
        return None
    if value is True:
        return "files"
    if isinstance(value, str):
        normalized = value.lower()
        if normalized in {"false", "0", "no"}:
            return None
        if normalized in {"true", "1", "yes"}:
            return "files"
        if normalized == "diff":
            return "diff"
    raise StudioError("bootstrap dry_run must be true, false, or diff")


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
        return str(candidate.relative_to(studio_config_module.project_root()))
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
            print(f"Run with: omegaflow recording={recording_ids[0]}")
    else:
        print(f"No recording scripts found in {recording_dir}.")
    return 1 if selected_required else 0


BOOTSTRAP_WORKSPACE_CONFIG = """\
capture:
  window_size: 80x20
  headless: true
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


def bootstrap_project_root(workspace: Path) -> Path:
    return workspace.parent


def bootstrap_config_path_text(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def bootstrap_tool_config_text(workspace: Path) -> str:
    root = bootstrap_project_root(workspace)
    recording_dir = bootstrap_config_path_text(workspace, root=root)
    data_dir = bootstrap_config_path_text(workspace / ".omegaflow", root=root)
    return f"""\
studio:
  recording_dir: {recording_dir}
  data_dir: {data_dir}
  run_gc:
    enabled: true
    max_age_days: 30
    max_runs_per_recording: 10
    preserve_latest_failure: true
"""


def bootstrap_recording_text(recording_id: str, title: str) -> str:
    return f"""\
---
kind: video
id: {recording_id}
title: {title}
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${{outputs.asset_dir}}/index.html
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
The two short beats make the generated player's section navigation easy to see.

When you enable audio, narration anchors can synchronize words and commands.
For example, put `@run_demo@` in the narration, set the command's `after` field
to `"@run_demo@"`, and add `@wait:show_message@` where narration should wait for
the command to finish. The starter keeps audio disabled, so its runnable beat
uses plain narration.

```yaml studio-directive
beat:
  id: first-video-beat
  heading: First Video Beat
  narration: This is the first beat in the generated quickstart video.
  caption: The first beat in the quickstart video.
  viewer_hold: 3
  actions:
  - commands:
    - id: show_first_beat
      run: "# First video beat"
```

The second beat adds another visible section to the player timeline.

```yaml studio-directive
beat:
  id: second-video-beat
  heading: Second Video Beat
  narration: This is the second beat in the generated quickstart video.
  caption: The second beat in the quickstart video.
  viewer_hold: 4
  actions:
  - commands:
    - id: show_second_beat
      run: "# Second video beat"
```

Publish surfaces in the header let the same recording write a standalone HTML
page. Add a docs surface when you want the build to update a documentation page.
"""


def bootstrap_workspace_path(config: dict[str, Any]) -> Path:
    value = optional_string(config.get("workspace"))
    if value is None:
        return recording_script_dir_from_config(config)
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return studio_config_module.project_root(config) / path


def write_bootstrap_file(
    path: Path,
    text: str,
    *,
    force: bool = False,
) -> str:
    existed = path.exists()
    if existed and not force:
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "updated" if existed else "created"


def bootstrap_file_diff(path: Path, text: str) -> str:
    path_label = display_path(path)
    before_label = path_label if path_label.startswith("/") else f"a/{path_label}"
    after_label = path_label if path_label.startswith("/") else f"b/{path_label}"
    if path.exists():
        before = path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        before = []
        before_label = "/dev/null"
    after = text.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=before_label,
            tofile=after_label,
        )
    )


def colorize_unified_diff(diff: str, *, enabled: bool | None = None) -> str:
    lines = diff.splitlines(keepends=True)
    colored: list[str] = []
    for line in lines:
        if line.startswith(("--- ", "+++ ")):
            color = ANSI_YELLOW_BOLD
        elif line.startswith("@@"):
            color = ANSI_CYAN_BOLD
        elif line.startswith("+"):
            color = ANSI_GREEN_BOLD
        elif line.startswith("-"):
            color = ANSI_RED_BOLD
        else:
            color = ""
        colored.append(color_text(line, color, enabled=enabled) if color else line)
    return "".join(colored)


def run_bootstrap(config: dict[str, Any]) -> int:
    workspace = bootstrap_workspace_path(config)
    recording_id = recording_id_from_value(config.get("recording")) or "quickstart"
    if not is_valid_recording_id(recording_id):
        raise StudioError(
            "bootstrap recording id must be a lowercase kebab-case path"
        )
    title = recording_id.rsplit("/", 1)[-1].replace("-", " ").title()
    force = bool_config(config, "force")
    dry_run_mode = bootstrap_dry_run_mode(config.get("dry_run", False))

    writes = [
        (
            bootstrap_project_root(workspace) / ".omegaflow" / "config.yaml",
            bootstrap_tool_config_text(workspace),
        ),
        (workspace / "config.yaml", BOOTSTRAP_WORKSPACE_CONFIG),
        (
            workspace / recording_id / "index.md",
            bootstrap_recording_text(recording_id, title),
        ),
    ]

    if dry_run_mode is not None:
        if dry_run_mode == "diff":
            print(f"Bootstrap dry run diff: {recording_id}")
            print()
            print(f"Recording workspace: {display_path(workspace)}")
            print()
            use_color = color_enabled()
            for path, text in writes:
                diff = bootstrap_file_diff(path, text)
                if diff:
                    diff = colorize_unified_diff(diff, enabled=use_color)
                    print(diff, end="" if diff.endswith("\n") else "\n")
            print()
            print("No files were written.")
            return 0

        print(f"Bootstrap dry run: {recording_id}")
        print()
        print(f"Recording workspace: {display_path(workspace)}")
        print()
        print("Files:")
        for path, _text in writes:
            status = "update" if path.exists() else "create"
            print(f"  {status:>6} {display_path(path)}")
        print()
        print("No files were written.")
        return 0

    print(f"workspace {display_path(workspace)}")
    for path, text in writes:
        status = write_bootstrap_file(
            path,
            text,
            force=force,
        )
        print(f"{status:>7} {display_path(path)}")
    print()
    print(f"next    omegaflow recording={recording_id} action=build")
    return 0


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StudioError(f"{field} must be a mapping")
    return value


def current_recording_run_dir(spec: dict[str, Any]) -> Path:
    return record.relative_path(record.require_string(spec, "_hydra_output_dir"))


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


def garbage_collect_recording_runs(
    spec: dict[str, Any],
    *,
    current_run_dir: Path,
    report: bool = True,
    dry_run: bool = False,
) -> list[Path]:
    config = spec.get("_studio_config", {})
    if not isinstance(config, dict):
        raise StudioError("_studio_config must be a mapping")
    studio_config = config.get("studio", {})
    if not isinstance(studio_config, dict):
        raise StudioError("studio must be a mapping")
    run_gc = studio_config.get("run_gc", {})
    if not isinstance(run_gc, dict):
        raise StudioError("studio.run_gc must be a mapping")
    enabled = run_gc.get("enabled", True)
    if not isinstance(enabled, bool):
        raise StudioError("studio.run_gc.enabled must be a boolean")
    if not enabled:
        return []
    runs_dir = record.recording_runs_dir(spec)
    return garbage_collect_run_directory(
        runs_dir,
        run_gc=run_gc,
        current_run_dir=current_run_dir,
        report=report,
        dry_run=dry_run,
    )


def run_gc_policy(run_gc: dict[str, Any]) -> tuple[int, int, bool]:
    max_age_days = run_gc.get("max_age_days", 30)
    if (
        isinstance(max_age_days, bool)
        or not isinstance(max_age_days, int)
        or max_age_days < 1
    ):
        raise StudioError("studio.run_gc.max_age_days must be a positive integer")
    max_runs = run_gc.get("max_runs_per_recording", 10)
    if isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 1:
        raise StudioError(
            "studio.run_gc.max_runs_per_recording must be a positive integer"
        )
    preserve_failure = run_gc.get("preserve_latest_failure", True)
    if not isinstance(preserve_failure, bool):
        raise StudioError("studio.run_gc.preserve_latest_failure must be a boolean")
    return max_age_days, max_runs, preserve_failure


def garbage_collect_run_directory(
    runs_dir: Path,
    *,
    run_gc: dict[str, Any],
    current_run_dir: Path | None = None,
    report: bool = True,
    dry_run: bool = False,
) -> list[Path]:
    if not isinstance(dry_run, bool):
        raise StudioError("dry_run must be a boolean")
    max_age_days, max_runs, preserve_failure = run_gc_policy(run_gc)
    if not runs_dir.is_dir():
        return []
    run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
    newest_first = sorted(
        run_dirs,
        key=lambda path: (path.name, path.stat().st_mtime_ns),
        reverse=True,
    )
    protected: set[Path] = set()
    if current_run_dir is not None:
        protected.add(current_run_dir.resolve())
    if preserve_failure:
        latest_failure = next(
            (
                path
                for path in newest_first
                if (path / "failure.json").is_file()
                or (path / "failed.cast").is_file()
            ),
            None,
        )
        if latest_failure is not None:
            protected.add(latest_failure.resolve())

    retained = set(protected)
    for run_dir in newest_first:
        if len(retained) >= max_runs:
            break
        retained.add(run_dir.resolve())

    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    candidates = sorted(
        run_dir
        for run_dir in run_dirs
        if run_dir.resolve() not in protected
        and (
            run_dir.stat().st_mtime < cutoff
            or run_dir.resolve() not in retained
        )
    )

    action = "would remove" if dry_run else "removed"
    removed_count = 0
    for run_dir in candidates:
        if not dry_run:
            try:
                shutil.rmtree(run_dir)
            except OSError as exc:
                if report:
                    print(
                        f"run gc warning: could not remove {display_path(run_dir)}: "
                        f"{exc}",
                        file=sys.stderr,
                    )
                continue
        removed_count += 1
        if report:
            print(f"run gc {action}: {display_path(run_dir)}")
    if report and candidates:
        suffix = " (dry run)" if dry_run else ""
        print(f"run gc: {action} {removed_count} run(s){suffix}")
    return candidates


def run_gc_action(config: dict[str, Any]) -> int:
    studio_config = config.get("studio", {})
    if not isinstance(studio_config, dict):
        raise StudioError("studio must be a mapping")
    run_gc = studio_config.get("run_gc", {})
    if not isinstance(run_gc, dict):
        raise StudioError("studio.run_gc must be a mapping")
    enabled = run_gc.get("enabled", True)
    if not isinstance(enabled, bool):
        raise StudioError("studio.run_gc.enabled must be a boolean")
    if not enabled:
        info_line("run gc is disabled")
        return 0
    dry_run = bool_config(config, "dry_run")
    runs_root = studio_config_module.studio_data_dir_from_config(config) / "runs"
    recording_id = recording_id_from_value(config.get("recording"))
    if recording_id is not None:
        resolved_runs_root = runs_root.resolve()
        recording_dir = (runs_root / recording_id).resolve()
        try:
            recording_dir.relative_to(resolved_runs_root)
        except ValueError as exc:
            raise StudioError(
                "recording must resolve inside the configured runs directory"
            ) from exc
        recording_dirs = [recording_dir]
    elif runs_root.is_dir():
        recording_dirs = sorted(
            {
                path.parent
                for path in runs_root.rglob("*")
                if path.is_dir()
                and record.parse_run_id_timestamp(path.name) is not None
                and ".scratch" not in path.relative_to(runs_root).parts
            }
        )
    else:
        recording_dirs = []
    for recording_dir in recording_dirs:
        garbage_collect_run_directory(
            recording_dir,
            run_gc=run_gc,
            report=True,
            dry_run=dry_run,
        )
    if not recording_dirs:
        info_line("no recording runs to clean")
    return 0


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

    presentation_paths = presentation_build.run_paths(
        current_recording_run_dir(spec)
    )
    return {
        "recording": str(spec["_recording_id"]),
        "title": optional_string(spec.get("title")),
        "inputs": {
            "recording_script": display_path(script_path),
            "recording_source": display_path(manifest_path),
        },
        "outputs": {
            "private_capture": display_path(presentation_paths["capture"]),
            "browser_capture_log": display_path(
                presentation_paths["browser_capture"]
            ),
            "narration": display_path(presentation_paths["audio"]),
            "presentation_bundle": display_path(
                presentation_paths["presentation"]
            ),
            "presentation_manifest": display_path(
                presentation_paths["manifest"]
            ),
            "recording_fingerprint": display_path(
                presentation_paths["fingerprint"]
            ),
            "compilation_report": display_path(presentation_paths["report"]),
        },
        "publish": {
            "on_build": bool(publish_config(spec).get("on_build", True)),
            "surfaces": surface_info,
            "targets": {
                "presentation_bundle": display_path(
                    presentation_build.public_bundle_dir(spec)
                ),
                "presentation_manifest": display_path(
                    presentation_build.public_manifest_path(spec)
                ),
            },
        },
        "steps": PRESENTATION_BUILD_STEPS,
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
    bundle = presentation_build.public_bundle_dir(spec)
    if not bundle.exists():
        return []
    if not bundle.is_dir():
        raise StudioError(f"presentation output is not a directory: {display_path(bundle)}")
    shutil.rmtree(bundle)
    return [bundle]


def run_clean(config: dict[str, Any]) -> int:
    removed = clean_recording_outputs(config)
    removed_display = [display_path(path) for path in removed]
    if config.get("output_format") == "json":
        print(
            json.dumps(
                {
                    "removed": removed_display,
                    "retained": ["audio_cache", "recording_runs"],
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
    info_line("retained audio cache and recording runs")
    return 0


def site_url(path: Path) -> str:
    static_root = studio_config_module.project_root() / "website" / "static"
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
    params = {"title": title, "manifest": site_url(paths["manifest"])}
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
    params = player_params(
        spec,
        surface,
        paths or {"manifest": presentation_build.public_manifest_path(spec)},
    )
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
    params = player_params(
        spec,
        surface,
        paths or {"manifest": presentation_build.public_manifest_path(spec)},
    )
    attributes = {"title": params["title"], "player": "/cast-player.html"}
    attributes["manifest"] = params["manifest"]
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
        pages: dict[str, bytes] | None = None,
        recordings: dict[str, dict[str, Any]] | None = None,
        snapshot_artifacts: dict[str, dict[str, Path]] | None = None,
        snapshot_lock: threading.RLock | None = None,
        snapshot_directory: Path | None = None,
        **kwargs: Any,
    ) -> None:
        self.artifacts = artifacts
        self.pages = pages or {}
        self.recording_routes = {
            watch_recording_url_path(recording_id): spec
            for recording_id, spec in (recordings or {}).items()
        }
        self.snapshot_artifacts = (
            snapshot_artifacts if snapshot_artifacts is not None else {}
        )
        self.snapshot_lock = snapshot_lock or threading.RLock()
        self.snapshot_directory = snapshot_directory
        self.player_directory = Path(directory)
        super().__init__(*args, directory=directory, **kwargs)

    def recording_route(
        self,
        request_path: str,
    ) -> tuple[str, dict[str, Any]] | None:
        recording_routes = getattr(self, "recording_routes", {})
        for route in sorted(recording_routes, key=len, reverse=True):
            if request_path.startswith(route):
                return route, recording_routes[route]
        return None

    def redirect(self, location: str, *, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def redirect_to_latest_snapshot(self, spec: dict[str, Any]) -> None:
        bundle, artifacts = watch_presentation_artifacts(spec)
        token = watch_snapshot_token(artifacts)
        with self.snapshot_lock:
            if token not in self.snapshot_artifacts:
                snapshot_bundle = bundle
                if self.snapshot_directory is not None:
                    snapshot_bundle = self.snapshot_directory / token
                    if not snapshot_bundle.exists():
                        try:
                            shutil.copytree(
                                bundle,
                                snapshot_bundle,
                                copy_function=shutil.copy2,
                            )
                        except BaseException:
                            shutil.rmtree(snapshot_bundle, ignore_errors=True)
                            raise
                self.snapshot_artifacts[token] = {
                    path.relative_to(snapshot_bundle).as_posix(): path.resolve()
                    for path in snapshot_bundle.rglob("*")
                    if path.is_file()
                }
        location = (
            WATCH_SNAPSHOT_PREFIX
            + token
            + "/"
            + quote(presentation_build.MANIFEST_FILE, safe="/")
        )
        self.redirect(location, status=307)

    def translate_path(self, path: str) -> str:
        request_path = urlparse(path).path
        if request_path.startswith(WATCH_SNAPSHOT_PREFIX):
            relative = request_path[len(WATCH_SNAPSHOT_PREFIX) :]
            token, separator, artifact_name = relative.partition("/")
            if separator:
                with self.snapshot_lock:
                    artifact = self.snapshot_artifacts.get(token, {}).get(
                        unquote(artifact_name)
                    )
                if artifact is not None:
                    return str(artifact)
        if request_path.startswith(WATCH_ARTIFACT_PREFIX):
            relative = unquote(request_path[len(WATCH_ARTIFACT_PREFIX) :])
            artifact = self.artifacts.get(relative)
            if artifact is None:
                artifact = self.artifacts.get(Path(relative).stem)
            if artifact is not None:
                return str(artifact)
        recording_route = self.recording_route(request_path)
        if recording_route is not None:
            route, _spec = recording_route
            relative = request_path[len(route) :]
            if relative == "":
                return str(self.player_directory / "cast-player.html")
            if relative == "cast-player-core.js":
                return str(self.player_directory / "cast-player-core.js")
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self) -> Any:
        parsed_request = urlparse(self.path)
        request_path = parsed_request.path
        slash_routes = set(getattr(self, "recording_routes", {})) | {
            route for route in getattr(self, "pages", {}) if route.endswith("/")
        }
        if request_path + "/" in slash_routes:
            location = request_path + "/"
            if parsed_request.query:
                location += "?" + parsed_request.query
            self.redirect(location)
            return None
        page = getattr(self, "pages", {}).get(request_path)
        if page is not None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            return io.BytesIO(page)
        recording_route = self.recording_route(request_path)
        if recording_route is not None:
            route, spec = recording_route
            if request_path[len(route) :] == presentation_build.MANIFEST_FILE:
                try:
                    self.redirect_to_latest_snapshot(spec)
                except StudioError as exc:
                    self.send_error(404, str(exc))
                return None
        range_header = self.headers.get("Range")
        if range_header is None:
            return super().send_head()
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        try:
            source = open(path, "rb")  # noqa: SIM115
        except OSError:
            self.send_error(404, "File not found")
            return None
        size = os.fstat(source.fileno()).st_size
        try:
            start, end = parse_http_byte_range(range_header, size=size)
        except ValueError:
            source.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        self._response_byte_range = (start, end)
        self.send_response(206)
        self.send_header("Content-type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(os.fstat(source.fileno()).st_mtime))
        self.end_headers()
        return source

    def copyfile(self, source: Any, outputfile: Any) -> None:
        try:
            byte_range = getattr(self, "_response_byte_range", None)
            if byte_range is None:
                super().copyfile(source, outputfile)
                return
            start, end = byte_range
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers routinely cancel in-flight media requests while seeking,
            # reloading, or closing. The response has no remaining client.
            return

    def log_message(self, format: str, *args: object) -> None:
        return


def watch_snapshot_token(artifacts: Mapping[str, Path]) -> str:
    digest = hashlib.sha256()
    for name, path in sorted(artifacts.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        try:
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise StudioError(
                f"could not snapshot watch artifact: {display_path(path)}"
            ) from exc
        digest.update(b"\0")
    return digest.hexdigest()[:32]


def parse_http_byte_range(value: str, *, size: int) -> tuple[int, int]:
    if size <= 0 or not value.startswith("bytes="):
        raise ValueError("invalid byte range")
    requested = value[6:].strip()
    if not requested or "," in requested or "-" not in requested:
        raise ValueError("invalid byte range")
    raw_start, raw_end = requested.split("-", 1)
    if not raw_start:
        try:
            suffix = int(raw_end)
        except ValueError as exc:
            raise ValueError("invalid byte range") from exc
        if suffix <= 0:
            raise ValueError("invalid byte range")
        return max(0, size - suffix), size - 1
    try:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    except ValueError as exc:
        raise ValueError("invalid byte range") from exc
    if start < 0 or start >= size or end < start:
        raise ValueError("invalid byte range")
    return start, min(end, size - 1)


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


def resolve_publish_surface_definition(
    surface: dict[str, Any],
    *,
    preflight_target: bool,
) -> tuple[str, Path]:
    surface_type = optional_string(surface.get("type"))
    file_name = optional_string(surface.get("file"))
    if not surface_type:
        raise StudioError("publish surface type must be a non-empty string")
    publish_surface_type_label(surface_type)
    if not file_name:
        raise StudioError("publish surface file must be a non-empty string")
    path = record.relative_path(file_name)
    if surface_type in {"docusaurus_mdx", "plain_html"}:
        placeholder = optional_string(surface.get("placeholder"))
        if not placeholder:
            raise StudioError(f"{surface_type} surfaces require a placeholder")
        if preflight_target:
            try:
                original = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise StudioError(
                    f"could not read publish surface: {display_path(path)}"
                ) from exc
            replace_placeholder(original, placeholder, "")
    return surface_type, path


def publish_surface(
    config: dict[str, Any],
    *,
    surface_name: str | None = None,
    presentation_run_dir: Path | None = None,
    publish_bundle_assets: bool = True,
) -> Path | None:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    selected = selected_surface(config, spec, surface_name=surface_name)
    if selected is None:
        return None
    run_dir = presentation_run_dir or latest_successful_recording_run_dir(spec)
    if run_dir is None:
        raise StudioError(
            "no successful recording run found; run omegaflow action=build first"
        )
    _surface_name, surface = selected
    surface_type, path = resolve_publish_surface_definition(
        surface,
        preflight_target=True,
    )
    if publish_bundle_assets:
        publish_presentation_bundle(spec, run_dir)
    publish_paths = {"manifest": presentation_build.public_manifest_path(spec)}

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


def publish_presentation_bundle(spec: Mapping[str, Any], run_dir: Path) -> Path:
    try:
        return presentation_build.publish_bundle(spec, run_dir)
    except presentation_build.PresentationBuildError as exc:
        raise StudioError(str(exc)) from exc


@dataclass(frozen=True)
class PublishSurfaceOutcome:
    path: Path
    updated: bool


def run_publish_surface(
    cfg: DictConfig,
    *,
    surface_name: str | None = None,
    presentation_run_dir: Path | None = None,
    publish_bundle_assets: bool = True,
    report: bool = True,
) -> PublishSurfaceOutcome | None:
    config = container_from_hydra_cfg(cfg)
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    selected = selected_surface(config, spec, surface_name=surface_name)
    target_path: Path | None = None
    selected_name: str | None = None
    surface_type: str | None = None
    if selected is not None:
        selected_name, surface = selected
        surface_type, target_path = resolve_publish_surface_definition(
            surface,
            preflight_target=True,
        )
    updated_path = publish_surface(
        config,
        surface_name=surface_name,
        presentation_run_dir=presentation_run_dir,
        publish_bundle_assets=publish_bundle_assets,
    )
    if target_path is None:
        return None
    outcome = PublishSurfaceOutcome(
        path=updated_path or target_path,
        updated=updated_path is not None,
    )
    if report and text_output_enabled(cfg):
        assert surface_type is not None
        assert selected_name is not None
        print_publish_surfaces(
            cfg,
            [
                (
                    publish_surface_display_name(
                        selected_name,
                        surface_type,
                    ),
                    outcome,
                    surface_type == "docusaurus_mdx",
                )
            ],
        )
    return outcome


def watch_presentation_artifacts(
    spec: dict[str, Any],
    *,
    run_dir: Path | None = None,
) -> tuple[Path, dict[str, Path]]:
    resolved_run_dir = run_dir or latest_successful_recording_run_dir(spec)
    paths = (
        presentation_build.run_paths(resolved_run_dir)
        if resolved_run_dir is not None
        else None
    )
    bundle = paths["presentation"] if paths is not None else None
    manifest = paths["manifest"] if paths is not None else None
    if run_dir is None and (manifest is None or not manifest.is_file()):
        public_bundle = presentation_build.public_bundle_dir(spec)
        public_manifest = public_bundle / presentation_build.MANIFEST_FILE
        if public_manifest.is_file():
            bundle = public_bundle
            manifest = public_manifest
    if bundle is None or manifest is None:
        raise StudioError(
            "no successful recording run found; run omegaflow action=build first"
        )
    if not manifest.is_file():
        raise StudioError(
            f"presentation manifest not found: {display_path(manifest)}; "
            "run omegaflow action=build first"
        )
    artifacts = {
        path.relative_to(bundle).as_posix(): path.resolve()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    return bundle.resolve(), artifacts


def watch_player_url_path(
    spec: dict[str, Any],
    *,
    run_dir: Path | None = None,
    autoplay_countdown: bool = False,
) -> tuple[str, dict[str, Path]]:
    _bundle, artifacts = watch_presentation_artifacts(spec, run_dir=run_dir)
    manifest_url = WATCH_ARTIFACT_PREFIX + quote(
        presentation_build.MANIFEST_FILE, safe="/"
    )
    query = {"manifest": manifest_url}
    if autoplay_countdown:
        query["autoplay"] = "countdown"
    return "/cast-player.html?" + urlencode(query), artifacts


def watch_recording_url_path(
    recording_id: str,
    *,
    autoplay_countdown: bool = False,
) -> str:
    path = WATCH_ROUTE_PREFIX + quote(recording_id, safe="/") + "/"
    if not autoplay_countdown:
        return path
    return path + "?" + urlencode({"autoplay": "countdown"})


def collection_member_player_url(recording_id: str) -> str:
    return watch_recording_url_path(
        recording_id,
        autoplay_countdown=True,
    )


def render_collection_watch_page(
    collection: dict[str, Any],
    members: list[dict[str, str]],
) -> str:
    title = optional_string(collection.get("title")) or str(collection["id"])
    cards: list[str] = []
    number_width = max(2, len(str(len(members))))
    for index, member in enumerate(members, 1):
        member_title = optional_string(member.get("title")) or member["id"]
        description = optional_string(member.get("description")) or (
            f"Watch {member_title}."
        )
        search_text = " ".join((member["id"], member_title, description)).lower()
        cards.append(
            '        <a class="video-card" data-video-card="true" '
            f'data-search="{html.escape(search_text, quote=True)}" '
            f'href="{html.escape(member["url"], quote=True)}">\n'
            f'          <span class="video-number" aria-hidden="true">'
            f"{index:0{number_width}d}</span>\n"
            '          <span class="video-copy">\n'
            f"            <h2>{html.escape(member_title)}</h2>\n"
            f"            <p>{html.escape(description)}</p>\n"
            "          </span>\n"
            '          <span class="video-arrow" aria-hidden="true">&rarr;</span>\n'
            "        </a>"
        )
    rendered_cards = "\n".join(cards)
    count = len(members)
    count_label = f"{count} {'video' if count == 1 else 'videos'}"
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"    <title>{html.escape(title)} · OmegaFlow</title>\n"
        "    <style>\n"
        "      :root { color-scheme: dark; font-family: Inter, ui-sans-serif, "
        "system-ui, sans-serif; }\n"
        "      * { box-sizing: border-box; }\n"
        "      [hidden] { display: none !important; }\n"
        "      body { margin: 0; height: 100vh; height: 100dvh; overflow: hidden; "
        "background: #0d0f16; "
        "color: #f3f2f7; }\n"
        "      main { width: min(1040px, calc(100% - 40px)); height: 100%; "
        "margin: 0 auto; padding: clamp(28px, 6vh, 64px) 0; display: grid; "
        "grid-template-rows: auto auto minmax(0, 1fr); }\n"
        "      header { min-width: 0; }\n"
        "      .brand { color: #9b87ff; font-weight: 750; letter-spacing: .02em; }\n"
        "      h1 { margin: 8px 0 4px; font-size: clamp(2rem, 5vw, 3.25rem); "
        "line-height: 1.05; }\n"
        "      .intro { margin: 0 0 20px; color: #b9bbca; font-size: 1rem; }\n"
        "      .toolbar { display: grid; grid-template-columns: minmax(0, 1fr) auto; "
        "align-items: center; gap: 16px; margin-bottom: 12px; }\n"
        "      .search { display: flex; align-items: center; gap: 10px; min-width: 0; "
        "height: 42px; padding: 0 13px; border: 1px solid #3e4357; "
        "border-radius: 10px; background: #131620; color: #85899d; }\n"
        "      .search:focus-within { border-color: #9b87ff; "
        "box-shadow: 0 0 0 3px rgb(155 135 255 / 14%); }\n"
        "      .search input { min-width: 0; width: 100%; border: 0; outline: 0; "
        "background: transparent; color: #f3f2f7; font: inherit; }\n"
        "      .search input::placeholder { color: #85899d; }\n"
        "      .result-count { color: #9296a8; font-size: .9rem; white-space: nowrap; }\n"
        "      .video-list { min-height: 0; overflow: auto; overscroll-behavior: "
        "contain; display: grid; align-content: start; gap: 8px; padding: 1px 7px "
        "10px 1px; scrollbar-color: #4b5066 transparent; }\n"
        "      .video-card { display: grid; grid-template-columns: 2.5rem "
        "minmax(0, 1fr) auto; align-items: center; gap: 14px; padding: 13px 16px; "
        "border: 1px solid "
        "#3e4357; border-radius: 14px; color: inherit; text-decoration: none; "
        "background: #171a24; transition: border-color .15s, transform .15s, "
        "background .15s; }\n"
        "      .video-card:hover, .video-card:focus-visible { border-color: #9b87ff; "
        "background: #1d2030; transform: translateY(-1px); outline: none; }\n"
        "      .video-number { color: #8e7af7; font: 700 .82rem/1 ui-monospace, "
        "SFMono-Regular, Consolas, monospace; }\n"
        "      .video-copy { min-width: 0; }\n"
        "      .video-card h2 { margin: 0 0 3px; overflow: hidden; "
        "text-overflow: ellipsis; white-space: nowrap; font-size: 1.05rem; }\n"
        "      .video-card p { display: -webkit-box; margin: 0; overflow: hidden; "
        "color: #b9bbca; font-size: .92rem; line-height: 1.35; "
        "-webkit-box-orient: vertical; -webkit-line-clamp: 2; }\n"
        "      .video-arrow { color: #a998ff; font-size: 1.25rem; font-weight: 700; }\n"
        "      .empty-state { margin: 28px 0; color: #9296a8; text-align: center; }\n"
        "      @media (max-width: 520px) { main { width: min(100% - 24px, 1040px); "
        "padding: 24px 0; } .toolbar { gap: 10px; } .video-card { "
        "grid-template-columns: 2rem minmax(0, 1fr) auto; gap: 10px; "
        "padding: 12px; } }\n"
        "      @media (max-height: 560px) { main { padding: 16px 0; } "
        "h1 { font-size: 2rem; } .intro { margin-bottom: 12px; } }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        "    <main>\n"
        "      <header>\n"
        '        <div class="brand">Ω OmegaFlow</div>\n'
        f"        <h1>{html.escape(title)}</h1>\n"
        '        <p class="intro">Choose a video to watch.</p>\n'
        "      </header>\n"
        '      <div class="toolbar">\n'
        '        <label class="search">\n'
        '          <span aria-hidden="true">⌕</span>\n'
        '          <input id="video-search" type="search" '
        'placeholder="Filter videos…" aria-label="Filter videos" '
        'autocomplete="off" spellcheck="false" />\n'
        "        </label>\n"
        f'        <span class="result-count" id="result-count" aria-live="polite">'
        f"{count_label}</span>\n"
        "      </div>\n"
        '      <section class="video-list" aria-label="Videos">\n'
        f"{rendered_cards}\n"
        '        <p class="empty-state" id="empty-state" hidden>'
        "No videos match that search.</p>\n"
        "      </section>\n"
        "    </main>\n"
        "    <script>\n"
        "      const search = document.getElementById('video-search');\n"
        "      const count = document.getElementById('result-count');\n"
        "      const empty = document.getElementById('empty-state');\n"
        "      const cards = [...document.querySelectorAll('[data-video-card]')];\n"
        "      const filterVideos = () => {\n"
        "        const query = search.value.trim().toLowerCase();\n"
        "        let visible = 0;\n"
        "        for (const card of cards) {\n"
        "          const matches = !query || card.dataset.search.includes(query);\n"
        "          card.hidden = !matches;\n"
        "          if (matches) visible += 1;\n"
        "        }\n"
        "        const noun = visible === 1 ? 'video' : 'videos';\n"
        "        count.textContent = query && visible !== cards.length\n"
        "          ? `${visible} of ${cards.length} videos`\n"
        "          : `${visible} ${noun}`;\n"
        "        empty.hidden = visible !== 0;\n"
        "      };\n"
        "      search.addEventListener('input', filterVideos);\n"
        "      document.addEventListener('keydown', (event) => {\n"
        "        if (event.key === '/' && document.activeElement !== search) {\n"
        "          event.preventDefault();\n"
        "          search.focus();\n"
        "        } else if (event.key === 'Escape' && document.activeElement === search) {\n"
        "          search.value = '';\n"
        "          filterVideos();\n"
        "          search.blur();\n"
        "        }\n"
        "      });\n"
        "    </script>\n"
        "  </body>\n"
        "</html>\n"
    )


def collection_watch_routes(
    cfg: DictConfig,
    config: dict[str, Any],
) -> tuple[str, dict[str, bytes], dict[str, dict[str, Any]]]:
    collection, member_cfgs = load_collection_build(cfg, config)
    recordings: dict[str, dict[str, Any]] = {}
    rendered_members: list[dict[str, str]] = []
    for member_id, member_cfg in zip(
        collection["members"], member_cfgs, strict=True
    ):
        member_config = container_from_hydra_cfg(member_cfg)
        spec = recording_spec_from_config(
            member_config,
            recording_id=None,
            overrides=(),
        )
        try:
            watch_presentation_artifacts(spec)
        except StudioError as exc:
            raise StudioError(
                f"collection {collection['id']} member {member_id} cannot be "
                f"watched: {exc}; build it with "
                f"{studio_tool_command(member_id)}"
            ) from exc
        recordings[member_id] = spec
        rendered_members.append(
            {
                "id": member_id,
                "title": optional_string(spec.get("title")) or member_id,
                "description": optional_string(spec.get("description")) or "",
                "url": collection_member_player_url(member_id),
            }
        )
    page = render_collection_watch_page(collection, rendered_members).encode("utf-8")
    url_path = watch_recording_url_path(str(collection["id"]))
    return url_path, {url_path: page}, recordings


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


class ManagedSystemBrowser:
    def __init__(
        self,
        *,
        process: Any,
        profile_path: str,
        remove_profile: Callable[[str], None],
    ) -> None:
        self.process = process
        self.profile_path = profile_path
        self.remove_profile = remove_profile

    def is_open(self) -> bool:
        return bool(self.process is not None and self.process.poll() is None)

    def close(self) -> None:
        process = self.process
        profile_path = self.profile_path
        remove_profile = self.remove_profile
        self.process = None
        self.profile_path = ""
        self.remove_profile = lambda _path: None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except OSError:
                pass
        if profile_path:
            remove_profile(profile_path)


def native_system_chromium_executable() -> Path | None:
    configured = os.environ.get("BROWSER")
    if configured:
        first_entry = configured.split(os.pathsep, 1)[0].strip()
        try:
            command = shlex.split(first_entry)[0]
        except (IndexError, ValueError):
            command = ""
        if command:
            configured_path = Path(command).expanduser()
            resolved = None
            if configured_path.is_file():
                resolved = configured_path
            elif found := shutil.which(command):
                resolved = Path(found)
            if resolved is not None and any(
                name in resolved.name.lower()
                for name in ("chrome", "chromium", "edge", "brave")
            ):
                return resolved

    command_names = (
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "microsoft-edge-stable",
        "microsoft-edge",
        "brave-browser-stable",
        "brave-browser",
    )
    for command in command_names:
        if executable := shutil.which(command):
            return Path(executable)

    if sys.platform == "darwin":
        home = Path.home()
        candidates = (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            home
            / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            home
            / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            home / "Applications/Chromium.app/Contents/MacOS/Chromium",
            home
            / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)
    return None


def wsl_host_chromium_executable() -> Path | None:
    candidates = (
        Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def windows_temporary_directory() -> str:
    command = shutil.which("cmd.exe")
    if command is None:
        raise StudioError("watch under WSL requires Windows interoperability")
    try:
        completed = subprocess.run(
            [command, "/d", "/c", "echo", "%TEMP%"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise StudioError(f"could not query the Windows environment: {exc}") from exc
    value = completed.stdout.strip().strip("\r")
    if completed.returncode != 0 or not value or value == "%TEMP%":
        raise StudioError("could not resolve the Windows temporary directory")
    return value.rstrip("\\/")


def remove_windows_watch_profile(profile_path: str) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        return
    try:
        subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "Remove-Item -LiteralPath "
                    f"{quote_powershell_string(profile_path)} "
                    "-Recurse -Force -ErrorAction SilentlyContinue"
                ),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def launch_managed_wsl_host_browser(url: str) -> ManagedSystemBrowser:
    executable = wsl_host_chromium_executable()
    if executable is None:
        raise StudioError(
            "watch under WSL requires Google Chrome or Microsoft Edge on Windows"
        )
    profile_path = ntpath.join(
        windows_temporary_directory(),
        f"omegaflow-watch-{uuid.uuid4().hex}",
    )
    try:
        process = subprocess.Popen(
            [
                str(executable),
                f"--user-data-dir={profile_path}",
                "--autoplay-policy=no-user-gesture-required",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-mode",
                "--new-window",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        remove_windows_watch_profile(profile_path)
        raise StudioError(f"could not open Windows watch browser: {exc}") from exc
    return ManagedSystemBrowser(
        process=process,
        profile_path=profile_path,
        remove_profile=remove_windows_watch_profile,
    )


def remove_native_watch_profile(profile_path: str) -> None:
    shutil.rmtree(profile_path, ignore_errors=True)


def launch_managed_native_browser(url: str) -> ManagedSystemBrowser:
    executable = native_system_chromium_executable()
    if executable is None:
        raise StudioError(
            "watch requires an installed system Chrome, Chromium, Edge, or Brave browser"
        )
    profile_path = tempfile.mkdtemp(prefix="omegaflow-watch-")
    try:
        process = subprocess.Popen(
            [
                str(executable),
                f"--user-data-dir={profile_path}",
                "--autoplay-policy=no-user-gesture-required",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-mode",
                "--new-window",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        remove_native_watch_profile(profile_path)
        raise StudioError(f"could not open system watch browser: {exc}") from exc
    return ManagedSystemBrowser(
        process=process,
        profile_path=profile_path,
        remove_profile=remove_native_watch_profile,
    )


def launch_managed_watch_browser(
    url: str,
) -> Any:
    from .browser_handoff import BrokeredBrowserSession

    brokered = BrokeredBrowserSession.from_environment(url)
    if brokered is not None:
        return brokered
    if running_under_wsl():
        return launch_managed_wsl_host_browser(url)
    return launch_managed_native_browser(url)


def run_watch_server(
    cfg: DictConfig,
    url_path: str,
    artifacts: dict[str, Path],
    *,
    pages: dict[str, bytes] | None = None,
    recordings: dict[str, dict[str, Any]] | None = None,
    managed_browser: bool = False,
    open_browser: bool = True,
    port: int = 0,
) -> int:
    static_root = Path(__file__).with_name("player") / "static"
    snapshot_artifacts: dict[str, dict[str, Path]] = {}
    snapshot_lock = threading.RLock()
    snapshot_directory = tempfile.TemporaryDirectory(
        prefix="omegaflow-watch-snapshots-"
    )

    def handler_factory(*args: Any, **kwargs: Any) -> StudioWatchRequestHandler:
        return StudioWatchRequestHandler(
            *args,
            artifacts=artifacts,
            directory=str(static_root),
            pages=pages,
            recordings=recordings,
            snapshot_artifacts=snapshot_artifacts,
            snapshot_lock=snapshot_lock,
            snapshot_directory=Path(snapshot_directory.name),
            **kwargs,
        )

    try:
        watch_server = http.server.ThreadingHTTPServer(
            (WATCH_HOST, port), handler_factory
        )
    except OSError as exc:
        snapshot_directory.cleanup()
        requested = f"{WATCH_HOST}:{port}" if port else WATCH_HOST
        raise StudioError(
            f"could not start local watch server on {requested}: {exc}"
        ) from exc

    with snapshot_directory, watch_server as server:
        url = f"http://{WATCH_HOST}:{server.server_port}{url_path}"
        if text_output_enabled(cfg):
            step_line("watch recording")
            pass_line(f"serving local watch server: {url}")
        if managed_browser:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            browser_session = None
            try:
                browser_session = launch_managed_watch_browser(url)
                if text_output_enabled(cfg):
                    info_line(
                        "opened isolated system browser; close it or press Ctrl-C to stop"
                    )
                while browser_session.is_open():
                    time.sleep(0.2)
            except KeyboardInterrupt:
                pass
            finally:
                if browser_session is not None:
                    browser_session.close()
                server.shutdown()
                server_thread.join()
                if text_output_enabled(cfg):
                    info_line("stopped local watch server")
            return 0
        opened = open_watch_url(url) if open_browser else False
        if text_output_enabled(cfg):
            if opened:
                info_line("opened browser; press Ctrl-C to stop")
            else:
                info_line("open the URL in a browser; press Ctrl-C to stop")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            if text_output_enabled(cfg):
                info_line("stopped local watch server")
    return 0


def configured_watch_port(config: dict[str, Any]) -> int:
    value = config.get("watch_port")
    if value is None:
        return 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 65535
    ):
        raise StudioError(
            "watch_port must be an integer between 1 and 65535 or null"
        )
    return value


def run_watch(cfg: DictConfig, config: dict[str, Any]) -> int:
    run_id = config.get("run_id")
    recording_id = recording_id_from_value(config.get("recording"))
    if run_id is not None or recording_id is None:
        raise StudioError("watch requires a recording id and does not accept run_id")

    port = configured_watch_port(config)
    autoplay = bool_config(config, "autoplay", True)
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    watch_presentation_artifacts(spec)
    url_path = watch_recording_url_path(
        recording_id,
        autoplay_countdown=autoplay,
    )
    open_browser = bool_config(config, "open", True)
    return run_watch_server(
        cfg,
        url_path,
        {},
        recordings={recording_id: spec},
        managed_browser=open_browser,
        open_browser=open_browser,
        port=port,
    )


def run_collection_watch(cfg: DictConfig, config: dict[str, Any]) -> int:
    port = configured_watch_port(config)
    url_path, pages, recordings = collection_watch_routes(cfg, config)
    open_browser = bool_config(config, "open", True)
    return run_watch_server(
        cfg,
        url_path,
        {},
        pages=pages,
        recordings=recordings,
        managed_browser=open_browser,
        open_browser=open_browser,
        port=port,
    )


def studio_tool_command(recording_id: str, *overrides: str) -> str:
    parts = ["omegaflow", f"recording={recording_id}", *overrides]
    return " ".join(shlex.quote(part) for part in parts)


def print_success_followups(cfg: DictConfig) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") == "json":
        return
    recording_value = OmegaConf.select(cfg, "recording")
    recording_id = recording_id_from_value(recording_value)
    if not isinstance(recording_id, str) or not recording_id:
        return
    print(
        color_text("watch", ANSI_GREEN_BOLD)
        + "  "
        + studio_tool_command(recording_id, "action=watch")
    )


def print_publish_surfaces(
    cfg: DictConfig,
    surfaces: list[tuple[str, PublishSurfaceOutcome, bool]],
) -> None:
    if not text_output_enabled(cfg):
        return
    for name, outcome, rebuild_required in surfaces:
        enabled = color_enabled()
        result = "updated" if outcome.updated else "unchanged"
        result_color = ANSI_GREEN_BOLD if outcome.updated else ANSI_YELLOW_BOLD
        prefix = (
            color_text("publish", ANSI_GREEN_BOLD, enabled=enabled)
            + "  "
            + color_text(name, ANSI_CYAN_BOLD, enabled=enabled)
            + ": "
        )
        if rebuild_required:
            print(
                prefix
                + color_text(result, result_color, enabled=enabled)
                + " — "
                + color_text(
                    "rebuild required",
                    ANSI_YELLOW_BOLD,
                    enabled=enabled,
                )
            )
            continue
        print(
            prefix
            + color_text(result, result_color, enabled=enabled)
            + " — "
            + color_text(
                display_path(outcome.path),
                ANSI_DIM,
                enabled=enabled,
            )
        )


def publish_surface_type_label(surface_type: str) -> str:
    labels = {
        "docusaurus_mdx": "Docusaurus",
        "plain_html": "HTML",
        "standalone_html": "Standalone HTML",
    }
    try:
        return labels[surface_type]
    except KeyError as exc:
        raise StudioError(f"unsupported publish surface type: {surface_type}") from exc


def publish_surface_display_name(surface_name: str, surface_type: str) -> str:
    type_label = publish_surface_type_label(surface_type)
    normalized_name = surface_name.replace("_", " ").replace("-", " ").lower()
    if normalized_name == type_label.lower():
        return type_label
    return f"{surface_name} ({type_label})"


@dataclass(frozen=True)
class BuildPublishSurface:
    name: str
    label: str
    type: str


def resolve_build_publish_surfaces(
    config: dict[str, Any],
    spec: dict[str, Any],
    surface_names: list[str],
) -> list[BuildPublishSurface]:
    resolved: list[BuildPublishSurface] = []
    for surface_name in surface_names:
        selected = selected_surface(
            config,
            spec,
            surface_name=surface_name,
        )
        if selected is None:
            raise StudioError(f"publish surface not found: {surface_name}")
        _selected_name, surface = selected
        surface_type, _path = resolve_publish_surface_definition(
            surface,
            preflight_target=True,
        )
        resolved.append(
            BuildPublishSurface(
                name=surface_name,
                label=publish_surface_display_name(surface_name, surface_type),
                type=surface_type,
            )
        )
    return resolved


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


def load_collection_build(
    cfg: DictConfig,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[DictConfig]]:
    recording_id = recording_id_from_value(config.get("recording"))
    if recording_id is None:
        raise StudioError("recording collection id must be a non-empty string")
    recording_dir = recording_script_dir_from_config(config)
    try:
        collection = recording_collection_from_script(
            recording_id,
            recording_dir=recording_dir,
        )
    except StudioConfigError as exc:
        raise StudioError(str(exc)) from exc

    member_cfgs: list[DictConfig] = []
    for member in collection["members"]:
        try:
            kind = recording_source_kind(member, recording_dir=recording_dir)
        except StudioConfigError as exc:
            raise StudioError(
                f"collection {recording_id} member {member} is invalid: {exc}"
            ) from exc
        if kind is not RecordingSourceKind.video:
            raise StudioError(
                f"collection {recording_id} member {member} must be a video"
            )
        member_cfg = cfg_for_recording(cfg, member)
        member_config = container_from_hydra_cfg(member_cfg)
        try:
            spec = recording_spec_from_config(
                member_config,
                recording_id=None,
                overrides=(),
            )
            normalized_recording_plan(spec)
        except (StudioConfigError, StudioError) as exc:
            raise StudioError(
                f"collection {recording_id} member {member} is invalid: {exc}"
            ) from exc
        member_cfgs.append(member_cfg)
    return collection, member_cfgs


def print_collection_build_dry_run(collection: dict[str, Any]) -> None:
    title = collection.get("title") or collection["id"]
    print(f"Build collection dry run: {title}")
    print()
    print("Videos:")
    for index, member in enumerate(collection["members"], 1):
        print(f"  {index}. {member}")
    print()
    print("No videos were built.")


def run_collection_build(cfg: DictConfig, config: dict[str, Any]) -> int:
    collection, member_cfgs = load_collection_build(cfg, config)
    if bool_config(config, "dry_run"):
        if config.get("output_format") == "json":
            print(
                json.dumps(
                    {
                        "collection": collection["id"],
                        "title": collection.get("title"),
                        "members": collection["members"],
                        "dry_run": True,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        elif text_output_enabled(cfg):
            print_collection_build_dry_run(collection)
        return 0

    members = collection["members"]
    count = len(members)
    noun = "video" if count == 1 else "videos"
    title = collection.get("title") or collection["id"]
    if text_output_enabled(cfg):
        step_line(f"build collection: {title} ({count} {noun})")
    for index, (member, member_cfg) in enumerate(
        zip(members, member_cfgs, strict=True),
        1,
    ):
        if text_output_enabled(cfg):
            info_line(f"[{index}/{count}] {member}")
        try:
            run_build(member_cfg, show_followups=False)
        except StudioError as exc:
            raise StudioError(
                f"collection {collection['id']} failed at {member}: {exc}"
            ) from exc
    if text_output_enabled(cfg):
        pass_line(f"collection completed: {count} {noun}")
    return 0


def run_manifest_build(
    cfg: DictConfig,
    config: dict[str, Any],
    spec: dict[str, Any],
    plan: Any,
    *,
    show_followups: bool = True,
) -> int:
    started = time.monotonic()
    success = False
    surface_names = build_publish_surface_names(config, spec)
    raw_audio = spec.get("audio")
    audio_enabled = (
        isinstance(raw_audio, Mapping) and raw_audio.get("enabled") is True
    )
    narration_steps = 3 * len(plan.narration_takes) if audio_enabled else 0
    total_steps = (
        len(capture_action_items(plan))
        + narration_steps
        + 2
        + len(surface_names)
    )
    progress = BuildProgress(
        total=max(1, total_steps),
        interactive=False if bool_config(config, "verbose") else None,
        active=text_output_enabled(cfg),
    )
    warnings: tuple[str, ...] = ()
    published_surfaces: list[tuple[str, PublishSurfaceOutcome, bool]] = []
    try:
        publish_targets = resolve_build_publish_surfaces(
            config,
            spec,
            surface_names,
        )
        run_dir = run_build_record_action(cfg, spec, plan, progress=progress)
        narration_current = 0

        def on_narration_progress(message: str, current: int, _total: int) -> None:
            nonlocal narration_current
            if current > narration_current:
                progress.advance(message, units=current - narration_current)
                narration_current = current
            else:
                progress.update(message)

        if narration_steps:
            take_count = len(plan.narration_takes)
            noun = "take" if take_count == 1 else "takes"
            progress.begin(f"Preparing narration ({take_count} {noun})")
        audio_artifacts = presentation_build.prepare_narration_audio(
            spec,
            plan,
            run_dir,
            force=bool_config(config, "force"),
            on_progress=on_narration_progress if narration_steps else None,
        )
        progress.begin("Assembling video")
        result = presentation_build.compile_presentation_bundle(
            spec,
            plan,
            run_dir,
            audio_artifacts=audio_artifacts,
        )
        progress.advance("Assembled video")
        warnings = result.warnings
        if publish_targets:
            progress.update("Publish video")
            publish_presentation_bundle(spec, run_dir)
        for target in publish_targets:
            progress.update(f"Publish {target.label}")
            outcome = run_publish_surface(
                cfg,
                surface_name=target.name,
                presentation_run_dir=run_dir,
                publish_bundle_assets=False,
                report=False,
            )
            if outcome is None:
                raise StudioError(f"publish surface not found: {target.name}")
            published_surfaces.append(
                (
                    target.label,
                    outcome,
                    target.type == "docusaurus_mdx",
                )
            )
            result_label = "updated" if outcome.updated else "unchanged"
            progress.advance(f"{target.label}: {result_label}")
        progress.update("Finalize video")
        remove_unused_empty_run_dir(spec, used_run_dir=run_dir)
        garbage_collect_recording_runs(
            spec,
            current_run_dir=run_dir,
            report=text_output_enabled(cfg) and bool_config(config, "verbose"),
        )
        progress.advance("Video ready")
        success = True
    except presentation_build.PresentationBuildError as exc:
        raise StudioError(str(exc)) from exc
    finally:
        elapsed = time.monotonic() - started
        compact_success = success and progress.active and progress.interactive
        progress.finish(
            completion=(
                f"completed in {format_elapsed(elapsed)}"
                if compact_success
                else None
            )
        )
        if success:
            for warning in warnings:
                info_line(f"{warning}: review recommended")
        if not compact_success:
            print_build_elapsed(cfg, elapsed, success=success)
        if success:
            print_publish_surfaces(cfg, published_surfaces)
    if show_followups:
        print_success_followups(cfg)
    return 0


def run_build(cfg: DictConfig, *, show_followups: bool = True) -> int:
    config = container_from_hydra_cfg(cfg)
    spec = load_recording_spec_from_hydra_cfg(cfg)
    plan = normalized_recording_plan(spec)
    return run_manifest_build(
        cfg,
        config,
        spec,
        plan,
        show_followups=show_followups,
    )


def run_check(cfg: DictConfig) -> int:
    config = container_from_hydra_cfg(cfg)
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    plan = normalized_recording_plan(spec)
    run_dir = latest_successful_recording_run_dir(spec)
    if run_dir is None or not presentation_build.capture_artifacts_exist(
        plan, run_dir
    ):
        raise StudioError(
            "no complete capture found; run omegaflow action=record first"
        )
    if not presentation_build.capture_is_fresh(spec, plan, run_dir):
        raise StudioError("capture fingerprint is stale")
    manifest = presentation_build.run_paths(run_dir)["manifest"]
    if manifest.exists():
        try:
            presentation_build.validate_run_bundle(spec, run_dir)
        except Exception as exc:
            raise StudioError(f"presentation validation failed: {exc}") from exc
    if text_output_enabled(cfg):
        pass_line(f"recording is valid: {display_path(run_dir)}")
    return 0


def run_internal_step(cfg: DictConfig, config: dict[str, Any], step: str) -> int:
    if step in {"record_dry_run", "dry_run"} or (
        step == "record" and bool_config(config, "dry_run")
    ):
        return run_build_dry_run(cfg, config)
    if step == "record_check":
        return run_check(cfg)
    if step == "publish":
        run_publish_surface(cfg)
        return 0
    if step in RECORD_ACTIONS:
        run_record_action(cfg, RECORD_ACTIONS[step], step.replace("_", " "))
        return 0
    if step == "sync_narration":
        run_audio_action(cfg, "sync_narration", "sync narration")
        return 0
    raise StudioError(f"unknown internal step: {step}")


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
        configure_project_root(config)
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

    if step is None and action == "gc":
        return run_gc_action(config)

    selected_recording_id = recording_id_from_value(config.get("recording"))
    recording_required = step is not None or action in {
        "build",
        "check",
        "clean",
        "watch",
    }
    if recording_required and selected_recording_id is None:
        return print_available_recording_scripts(
            selected_required=True,
            config=config,
        )

    source_kind: RecordingSourceKind | None = None
    if step is None and selected_recording_id is not None:
        recording_dir = recording_script_dir_from_config(config)
        try:
            source_kind = recording_source_kind(
                selected_recording_id,
                recording_dir=recording_dir,
            )
        except StudioConfigError as exc:
            raise StudioError(str(exc)) from exc
        if source_kind is RecordingSourceKind.collection and action not in {
            "build",
            "watch",
        }:
            raise StudioError(
                f"recording={selected_recording_id} is a collection; "
                "only action=build and action=watch are supported"
            )

    if (
        step is None
        and action == "build"
        and source_kind is RecordingSourceKind.collection
        and bool_config(config, "dry_run")
    ):
        return run_collection_build(cfg, config)

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
        if source_kind is RecordingSourceKind.collection:
            return run_collection_build(cfg, config)
        return run_build(cfg)
    if action == "check":
        return run_check(cfg)
    if action == "watch":
        if source_kind is RecordingSourceKind.collection:
            return run_collection_watch(cfg, config)
        return run_watch(cfg, config)

    if action in RECORD_ACTIONS:
        run_record_action(cfg, RECORD_ACTIONS[action], str(action).replace("_", " "))
        return 0

    raise StudioError(f"unknown omegaflow action: {action}")


def normalize_cli_rec_overrides(argv: list[str]) -> list[str]:
    return [
        f"+{arg}" if arg.startswith("rec.") and "=" in arg else arg
        for arg in argv
    ]


def add_project_config_searchpath(argv: list[str]) -> list[str]:
    if not argv:
        return []
    searchpath_override = project_config_searchpath_override(argv[1:])
    if searchpath_override is None:
        return list(argv)
    return [argv[0], searchpath_override, *argv[1:]]


@hydra.main(
    version_base=None,
    config_path=str(CONFIG_DIR),
    config_name=STUDIO_CONFIG_NAME,
)
def hydra_main(cfg: DictConfig) -> None:
    use_color = record.host_color_enabled(sys.stderr)
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except KeyboardInterrupt:
        print(
            color_text(
                "interrupted: omegaflow run cancelled by user",
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


def main() -> None:
    normalized = normalize_cli_rec_overrides(sys.argv)
    sys.argv[:] = add_project_config_searchpath(normalized)
    hydra_main()


if __name__ == "__main__":
    main()
