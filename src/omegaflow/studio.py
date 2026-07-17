#!/usr/bin/env python3
"""Frontend CLI for OmegaFlow."""

from __future__ import annotations

import difflib
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
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, unquote, urlparse

import hydra
from omegaconf import DictConfig, OmegaConf

from . import audio
from . import record
from . import presentation_build
from . import studio_config as studio_config_module
from .recording_plan import RecordingPlanError, normalize_recording_plan
from .studio_config import (
    CONFIG_DIR,
    STUDIO_CONFIG_NAME,
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
    recording_script_dir_from_config,
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
) -> Path:
    config = container_from_hydra_cfg(cfg)
    verbose = bool_config(config, "verbose")
    if not bool_config(config, "force"):
        latest = latest_successful_recording_run_dir(spec)
        if latest is not None and presentation_build.capture_is_fresh(
            spec, plan, latest
        ):
            if text_output_enabled(cfg):
                skip_line(
                    "capture recording",
                    detail=f"{display_path(latest)} is fresh",
                )
            return latest
        if latest is not None and text_output_enabled(cfg) and verbose:
            info_line("capture recording: capture fingerprint changed")
    run_dir = current_recording_run_dir(spec)
    if text_output_enabled(cfg):
        step_line("capture recording")
    presentation_build.capture_recording(
        spec,
        plan,
        run_dir,
        headed=bool_config(config, "headed"),
    )
    presentation_build.write_capture_fingerprint(spec, plan, run_dir)
    if text_output_enabled(cfg):
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
This beat runs one self-contained command and checks its output.

When you enable audio, narration anchors can synchronize words and commands.
For example, put `@run_demo@` in the narration, set the command's `after` field
to `"@run_demo@"`, and add `@wait:show_message@` where narration should wait for
the command to finish. The starter keeps audio disabled, so its runnable beat
uses plain narration.

```yaml studio-directive
beat:
  id: show-message
  heading: Run The Quickstart
  narration: Run one inline command and verify its terminal output.
  caption: A self-contained command with an expected output check.
  actions:
  - commands:
    - id: show_message
      follow_along: true
      run: for n in 3 2 1; do printf '%s\\n' "$n"; sleep 1; done; printf 'Hello World!\\n'
      expect:
        output_contains:
        - Hello World!
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
    print(f"next    omegaflow recording={recording_id}")
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
        **kwargs: Any,
    ) -> None:
        self.artifacts = artifacts
        super().__init__(*args, directory=directory, **kwargs)

    def translate_path(self, path: str) -> str:
        request_path = urlparse(path).path
        if request_path.startswith(WATCH_ARTIFACT_PREFIX):
            relative = unquote(request_path[len(WATCH_ARTIFACT_PREFIX) :])
            artifact = self.artifacts.get(relative)
            if artifact is None:
                artifact = self.artifacts.get(Path(relative).stem)
            if artifact is not None:
                return str(artifact)
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self) -> Any:
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


def publish_surface(
    config: dict[str, Any],
    *,
    surface_name: str | None = None,
    presentation_run_dir: Path | None = None,
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
    try:
        presentation_build.validate_run_bundle(spec, run_dir)
        presentation_build.publish_bundle(spec, run_dir)
    except presentation_build.PresentationBuildError as exc:
        raise StudioError(str(exc)) from exc
    publish_paths = {"manifest": presentation_build.public_manifest_path(spec)}
    _surface_name, surface = selected
    surface_type = optional_string(surface.get("type"))
    file_name = optional_string(surface.get("file"))
    if not surface_type:
        raise StudioError("publish surface type must be a non-empty string")
    if not file_name:
        raise StudioError("publish surface file must be a non-empty string")
    path = record.relative_path(file_name)

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
    surface_name: str | None = None,
    presentation_run_dir: Path | None = None,
) -> None:
    config = container_from_hydra_cfg(cfg)
    path = publish_surface(
        config,
        surface_name=surface_name,
        presentation_run_dir=presentation_run_dir,
    )
    if path is not None and text_output_enabled(cfg):
        step_line("publish surface")
        pass_line(f"wrote publish surface: {display_path(path)}")


def watch_player_url_path(
    spec: dict[str, Any],
    *,
    run_dir: Path | None = None,
    autoplay_countdown: bool = False,
) -> tuple[str, dict[str, Path]]:
    resolved_run_dir = run_dir or latest_successful_recording_run_dir(spec)
    if resolved_run_dir is None:
        raise StudioError(
            "no successful recording run found; run omegaflow action=build first"
        )
    paths = presentation_build.run_paths(resolved_run_dir)
    bundle = paths["presentation"]
    manifest = paths["manifest"]
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
    manifest_url = WATCH_ARTIFACT_PREFIX + quote(
        presentation_build.MANIFEST_FILE, safe="/"
    )
    query = {"manifest": manifest_url}
    if autoplay_countdown:
        query["autoplay"] = "countdown"
    return "/cast-player.html?" + urlencode(query), artifacts


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
    managed_browser: bool = False,
    open_browser: bool = True,
) -> int:
    static_root = Path(__file__).with_name("player") / "static"

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


def run_watch(cfg: DictConfig, config: dict[str, Any]) -> int:
    run_id = config.get("run_id")
    recording_id = recording_id_from_value(config.get("recording"))
    if run_id is not None or recording_id is None:
        raise StudioError("watch requires a recording id and does not accept run_id")

    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    url_path, artifacts = watch_player_url_path(spec, autoplay_countdown=True)
    open_browser = bool_config(config, "open", True)
    return run_watch_server(
        cfg,
        url_path,
        artifacts,
        managed_browser=open_browser,
        open_browser=open_browser,
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
    print()
    print(color_text("next", ANSI_CYAN_BOLD) + " follow-up command")
    print(
        "  "
        + color_text("watch  ", ANSI_GREEN_BOLD)
        + " "
        + studio_tool_command(recording_id, "action=watch")
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


def run_manifest_build(
    cfg: DictConfig,
    config: dict[str, Any],
    spec: dict[str, Any],
    plan: Any,
) -> int:
    started = time.monotonic()
    success = False
    try:
        run_dir = run_build_record_action(cfg, spec, plan)
        if text_output_enabled(cfg):
            step_line("prepare narration takes")
        audio_artifacts = presentation_build.prepare_narration_audio(
            spec,
            plan,
            run_dir,
            force=bool_config(config, "force"),
        )
        if text_output_enabled(cfg):
            if audio_artifacts is None:
                skip_line("prepare narration takes", detail="audio is disabled or empty")
            else:
                pass_line(f"prepared {len(audio_artifacts.timestamps)} narration take(s)")
            step_line("compile presentation")
        result = presentation_build.compile_presentation_bundle(
            spec,
            plan,
            run_dir,
            audio_artifacts=audio_artifacts,
        )
        if text_output_enabled(cfg):
            pass_line(f"wrote presentation: {display_path(result.manifest)}")
            for warning in result.warnings:
                info_line(f"{warning}: review recommended")
        for surface_name in build_publish_surface_names(config, spec):
            run_publish_surface(
                cfg,
                surface_name=surface_name,
                presentation_run_dir=run_dir,
            )
        remove_unused_empty_run_dir(spec, used_run_dir=run_dir)
        garbage_collect_recording_runs(
            spec,
            current_run_dir=run_dir,
            report=text_output_enabled(cfg),
        )
        success = True
    except presentation_build.PresentationBuildError as exc:
        raise StudioError(str(exc)) from exc
    finally:
        print_build_elapsed(cfg, time.monotonic() - started, success=success)
    print_success_followups(cfg)
    return 0


def run_build(cfg: DictConfig) -> int:
    config = container_from_hydra_cfg(cfg)
    spec = load_recording_spec_from_hydra_cfg(cfg)
    plan = normalized_recording_plan(spec)
    return run_manifest_build(cfg, config, spec, plan)


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
    if action == "watch":
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
