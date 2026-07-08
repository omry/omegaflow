#!/usr/bin/env python3
"""Record OmegaFlow casts from Hydra-composed configs."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import stat
import string
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import hydra
from omegaconf import DictConfig

from .studio_config import (
    CONFIG_DIR,
    PROJECT_DATA_DIR,
    PROJECT_ROOT,
    RECORDING_SCRIPT_DIR,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    container_from_hydra_cfg,
    is_valid_recording_id,
    list_recording_ids,
    load_recording_spec,
    load_recording_spec_from_hydra_cfg,
    recording_script_dir_from_config,
    recording_script_path,
    studio_data_dir_from_config,
)
from .terminal_style import (
    ANSI_CYAN_BOLD,
    ANSI_GREEN_BOLD,
    ANSI_RED_BOLD,
    ANSI_BLUE_BOLD,
    ANSI_YELLOW_BOLD,
    color_text,
    color_enabled as terminal_color_enabled,
    formatted_status,
)
from .tool_progress import (
    ProgressBarRenderer,
    ProgressPipeReporter,
    PROGRESS_PIPE_ENV,
)


REPO_ROOT = PROJECT_ROOT
RUN_ID_DATETIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_SINCE_UNITS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
}


class RecordingError(RuntimeError):
    pass


class RecordingInterrupted(RuntimeError):
    pass


def configured_asciinema_path(source: dict[str, Any] | None) -> str | None:
    if not isinstance(source, dict):
        return None
    config = source.get("_studio_config", source)
    if not isinstance(config, dict):
        return None
    studio = config.get("studio", {})
    if not isinstance(studio, dict):
        return None
    value = studio.get("asciinema_path")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if path.is_absolute():
        return str(path)
    return str(relative_path(str(path)))


def bundled_asciinema_path() -> str | None:
    candidate = Path(__file__).resolve().parent / "bin" / "asciinema"
    if candidate.is_file():
        return str(candidate)
    return None


def asciinema_command(source: dict[str, Any] | None = None) -> str:
    configured = configured_asciinema_path(source)
    if configured is not None:
        return configured
    bundled = bundled_asciinema_path()
    if bundled is not None:
        return bundled
    return "asciinema"


class RecordingSuspendGuard:
    def __init__(self) -> None:
        self.signal_number = getattr(signal, "SIGTSTP", None)
        self.previous_handler: Any = None
        self.enabled = False

    def __enter__(self) -> None:
        if self.signal_number is None:
            return
        try:
            self.previous_handler = signal.getsignal(self.signal_number)
            signal.signal(self.signal_number, signal.SIG_IGN)
        except (OSError, RuntimeError, ValueError):
            return
        self.enabled = True

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if not self.enabled or self.signal_number is None:
            return
        signal.signal(self.signal_number, self.previous_handler)


INTERRUPT_RETURNCODES = {
    128 + signal.SIGINT,
    128 + signal.SIGTERM,
    -signal.SIGINT,
    -signal.SIGTERM,
}

HIDDEN_INTERVAL_JITTER_TOLERANCE_SECONDS = 0.25
ANSI_CONTROL_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
REPLACEMENT_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def host_color_enabled(stream: Any = sys.stderr) -> bool:
    return terminal_color_enabled(stream)


def status_line(
    status: str,
    message: str,
    *,
    color: str,
    enabled: bool,
) -> str:
    return formatted_status(status, message, color=color, enabled=enabled)


def progress_status_line(message: str, *, color: bool) -> str:
    if message.startswith("setup: "):
        return status_line("step", message, color=ANSI_CYAN_BOLD, enabled=color)
    if message.startswith("stage "):
        return status_line("step", message, color=ANSI_CYAN_BOLD, enabled=color)
    if message.startswith("running: "):
        return status_line(
            "cmd",
            message.removeprefix("running: "),
            color=ANSI_GREEN_BOLD,
            enabled=color,
        )
    if message.startswith("check: "):
        return status_line(
            "check",
            message.removeprefix("check: "),
            color=ANSI_BLUE_BOLD,
            enabled=color,
        )
    return status_line("info", message, color=ANSI_CYAN_BOLD, enabled=color)


def load_manifest(
    recording_id: str, overrides: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    try:
        return load_recording_spec(recording_id, overrides)
    except StudioConfigError as exc:
        raise RecordingError(str(exc)) from exc


def require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RecordingError(
            f"recording config field {key!r} must be a non-empty string"
        )
    return value


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RecordingError(f"recording config field {field!r} must be a mapping")
    return value


def as_list(value: object, *, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RecordingError(f"recording config field {field!r} must be a list")
    return value


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def optional_non_negative_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or value < 0:
        raise RecordingError(f"{field} must be a non-negative number")
    return float(value)


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def run_file_path(run_file: str, spec: dict[str, Any] | None = None) -> Path:
    candidate = Path(run_file)
    if candidate.is_absolute():
        return candidate
    search_roots: list[Path] = []
    if spec is not None:
        script_dir = spec.get("_script_dir")
        if isinstance(script_dir, str) and script_dir:
            search_roots.append(relative_path(script_dir))
    search_roots.append(REPO_ROOT)
    for root in search_roots:
        path = root / candidate
        if path.exists():
            return path
    return search_roots[0] / candidate


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def step_command_text(
    step: dict[str, Any],
    index: int,
    *,
    field: str,
    spec: dict[str, Any] | None = None,
) -> str:
    has_run = "run" in step and step.get("run") is not None
    has_run_file = "run_file" in step and step.get("run_file") is not None
    if has_run and has_run_file:
        raise RecordingError(
            f"{field}.{index} must use either run or run_file, not both"
        )
    if not has_run and not has_run_file:
        raise RecordingError(f"{field}.{index} must define run or run_file")
    if has_run:
        return require_string(step, "run")

    run_file = step.get("run_file")
    if not isinstance(run_file, str) or not run_file:
        raise RecordingError(f"{field}.{index}.run_file must be a non-empty string")
    path = run_file_path(run_file, spec)
    if not path.is_file():
        raise RecordingError(f"{field}.{index}.run_file does not exist: {path}")
    try:
        command = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordingError(
            f"failed to read {field}.{index}.run_file: {path}"
        ) from exc
    if not command.strip():
        raise RecordingError(f"{field}.{index}.run_file is empty: {path}")
    return command


def command_output_config(
    command: dict[str, Any],
    *,
    field: str,
) -> dict[str, str]:
    raw_output = command.get("output")
    if raw_output is None:
        mode = "real"
        text = None
    elif isinstance(raw_output, str):
        if raw_output not in {"real", "suppress", "fake"}:
            raise RecordingError(f"{field}.output must be one of: real, suppress, fake")
        mode = raw_output
        text = None
    elif isinstance(raw_output, dict):
        mode_value = raw_output.get("mode", "real")
        if not isinstance(mode_value, str) or mode_value not in {
            "real",
            "suppress",
            "fake",
        }:
            raise RecordingError(
                f"{field}.output.mode must be one of: real, suppress, fake"
            )
        mode = mode_value
        text = raw_output.get("text")
    else:
        raise RecordingError(f"{field}.output must be a string or mapping")
    if mode == "fake":
        if not isinstance(text, str):
            raise RecordingError(
                f"{field}.output.text must be a string for fake output"
            )
    elif text is not None and not isinstance(text, str):
        raise RecordingError(f"{field}.output.text must be a string")
    return {"mode": mode, "text": text or ""}


def command_timing_mode(command: dict[str, Any], *, field: str) -> str:
    raw_timing = command.get("timing", "presentation")
    if not isinstance(raw_timing, str) or raw_timing not in {
        "presentation",
        "realtime",
    }:
        raise RecordingError(
            f"{field}.timing must be one of: presentation, realtime"
        )
    return raw_timing


def action_command_entries(
    action: dict[str, Any],
    index: int,
    *,
    field: str,
    spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    raw_commands = action.get("commands")
    if raw_commands is None:
        return None
    if any(action.get(key) is not None for key in ("run", "run_file", "display")):
        raise RecordingError(
            f"{field}.{index} must use commands or run/run_file/display, not both"
        )
    commands = as_list(raw_commands, field=f"{field}.{index}.commands")
    if not commands:
        raise RecordingError(f"{field}.{index}.commands must not be empty")
    entries: list[dict[str, Any]] = []
    for command_index, raw_command in enumerate(commands, start=1):
        if not isinstance(raw_command, dict):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index} must be a mapping"
            )
        command_id = raw_command.get("id", "")
        if command_id is None:
            command_id = ""
        if not isinstance(command_id, str):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.id must be a string"
            )
        if command_id and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", command_id):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.id must be identifier-like"
            )
        run = step_command_text(
            raw_command,
            command_index,
            field=f"{field}.{index}.commands",
            spec=spec,
        )
        display = raw_command.get("display", run)
        if not isinstance(display, str) or not display:
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.display must be a non-empty string"
            )
        after = raw_command.get("after", "")
        if after is None:
            after = ""
        if not isinstance(after, str):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.after must be a string"
            )
        if after and not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_-]*@", after):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.after must use @anchor@ syntax"
            )
        follow_along = raw_command.get("follow_along", False)
        if not isinstance(follow_along, bool):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.follow_along must be a boolean"
            )
        show_prompt_after = raw_command.get("show_prompt_after", True)
        if not isinstance(show_prompt_after, bool):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.show_prompt_after must be a boolean"
            )
        output = command_output_config(
            raw_command,
            field=f"{field}.{index}.commands.{command_index}",
        )
        timing = command_timing_mode(
            raw_command,
            field=f"{field}.{index}.commands.{command_index}",
        )
        post_enter_pause = optional_non_negative_number(
            raw_command.get("post_enter_pause"),
            field=f"{field}.{index}.commands.{command_index}.post_enter_pause",
        )
        post_command_pause = optional_non_negative_number(
            raw_command.get("post_command_pause"),
            field=f"{field}.{index}.commands.{command_index}.post_command_pause",
        )
        pre_command_pause = optional_non_negative_number(
            raw_command.get("pre_command_pause"),
            field=f"{field}.{index}.commands.{command_index}.pre_command_pause",
        )
        pre_enter_pause = optional_non_negative_number(
            raw_command.get("pre_enter_pause"),
            field=f"{field}.{index}.commands.{command_index}.pre_enter_pause",
        )
        entries.append(
            {
                "id": command_id,
                "run": run,
                "display": display,
                "after": after,
                "follow_along": follow_along,
                "show_prompt_after": show_prompt_after,
                "output": output,
                "timing": timing,
                "pre_command_pause": pre_command_pause,
                "pre_enter_pause": pre_enter_pause,
                "post_enter_pause": post_enter_pause,
                "post_command_pause": post_command_pause,
            }
        )
    return entries


def setup_command_text(
    step: dict[str, Any],
    index: int,
    *,
    spec: dict[str, Any] | None = None,
) -> str:
    return step_command_text(step, index, field="setup", spec=spec)


def cleanup_command_text(
    step: dict[str, Any],
    index: int,
    *,
    spec: dict[str, Any] | None = None,
) -> str:
    return step_command_text(step, index, field="cleanup", spec=spec)


def check_asciinema(source: dict[str, Any] | None = None) -> str:
    command = asciinema_command(source)
    try:
        result = subprocess.run(
            [command, "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = f"configured at {command}" if command != "asciinema" else "on PATH"
        raise RecordingError(
            "asciinema 3.x is required and was not found "
            f"{detail}; install asciinema 3.x or use a platform-specific "
            "OmegaFlow wheel with a bundled recorder"
        ) from exc
    version = result.stdout.strip()
    match = re.search(r"\b(\d+)\.", version)
    if match is None or int(match.group(1)) < 3:
        raise RecordingError(f"asciinema 3.x is required, found: {version}")
    return version


def check_required_commands(spec: dict[str, Any]) -> None:
    requirements = as_mapping(spec.get("requirements"), field="requirements")
    search_path = os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")]
    )
    for command in as_list(requirements.get("commands"), field="requirements.commands"):
        if not isinstance(command, str) or not command:
            raise RecordingError(
                "requirements.commands values must be non-empty strings"
            )
        if shutil.which(command, path=search_path) is None:
            raise RecordingError(f"required command not found on PATH: {command}")


def require_non_negative_number(
    mapping: dict[str, Any], key: str, default: float
) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, (int, float)) or value < 0:
        raise RecordingError(f"style.{key} must be a non-negative number")
    return float(value)


def require_positive_number(mapping: dict[str, Any], key: str, default: float) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, (int, float)) or value <= 0:
        raise RecordingError(f"style.{key} must be a positive number")
    return float(value)


def require_integer(mapping: dict[str, Any], key: str, default: int) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int):
        raise RecordingError(f"style.{key} must be an integer")
    return value


def failure_summary_config(spec: dict[str, Any]) -> dict[str, Any]:
    summary = as_mapping(spec.get("failure_summary"), field="failure_summary")
    raw_rules = summary.get("terminal_animations", [])
    rules = as_list(raw_rules, field="failure_summary.terminal_animations")
    terminal_animations: list[dict[str, Any]] = []
    for index, raw_rule in enumerate(rules, start=1):
        field = f"failure_summary.terminal_animations.{index}"
        if not isinstance(raw_rule, dict):
            raise RecordingError(f"{field} must be a mapping")
        regex = raw_rule.get("regex")
        replacement = raw_rule.get("replacement")
        if not isinstance(regex, str) or not regex:
            raise RecordingError(f"{field}.regex must be a non-empty string")
        try:
            pattern = re.compile(regex)
        except re.error as exc:
            raise RecordingError(f"{field}.regex is invalid: {exc}") from exc
        if not isinstance(replacement, str) or not replacement:
            raise RecordingError(f"{field}.replacement must be a non-empty string")
        try:
            replacement_fields = list(string.Formatter().parse(replacement))
        except ValueError as exc:
            raise RecordingError(f"{field}.replacement is invalid: {exc}") from exc
        for _, placeholder, format_spec, conversion in replacement_fields:
            if placeholder is None:
                continue
            if format_spec or conversion:
                raise RecordingError(
                    f"{field}.replacement placeholders do not support format specs"
                )
            if not REPLACEMENT_FIELD_RE.fullmatch(placeholder):
                raise RecordingError(
                    f"{field}.replacement placeholder {placeholder!r} must be a "
                    "named regex capture"
                )
            if placeholder not in pattern.groupindex:
                raise RecordingError(
                    f"{field}.replacement references unknown regex capture "
                    f"{placeholder!r}"
                )
        terminal_animations.append(
            {
                "regex": regex,
                "replacement": replacement,
            }
        )
    return {"terminal_animations": terminal_animations}


def validate_manifest(spec: dict[str, Any]) -> None:
    recording_id = require_string(spec, "id")
    if not is_valid_recording_id(recording_id):
        raise RecordingError("recording id must be a lowercase kebab-case path")
    require_string(spec, "title")
    outputs = as_mapping(spec.get("outputs"), field="outputs")
    require_string(outputs, "cast")
    capture = as_mapping(spec.get("capture"), field="capture")
    window_size = capture.get("window_size", "100x28")
    if not isinstance(window_size, str) or not re.fullmatch(r"\d+x\d+", window_size):
        raise RecordingError("capture.window_size must look like COLSxROWS")
    idle_time_limit = capture.get("idle_time_limit")
    if idle_time_limit is not None and (
        not isinstance(idle_time_limit, (int, float)) or idle_time_limit <= 0
    ):
        raise RecordingError("capture.idle_time_limit must be a positive number")
    baseline_compressed = capture.get("baseline_compressed", False)
    if not isinstance(baseline_compressed, bool):
        raise RecordingError("capture.baseline_compressed must be a boolean")
    parameters = spec.get("parameters")
    if parameters is not None:
        parameters = as_mapping(parameters, field="parameters")
        for key, value in parameters.items():
            if not isinstance(key, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", key
            ):
                raise RecordingError("parameters keys must be shell-safe names")
            if not isinstance(value, (str, int, float, bool)):
                raise RecordingError(f"parameters.{key} must be a scalar value")
    hydra_output_dir = spec.get("_hydra_output_dir")
    if not isinstance(hydra_output_dir, str) or not hydra_output_dir:
        raise RecordingError("Hydra output directory is required for recording")
    keep_output_dir = spec.get("_keep_hydra_output_dir", False)
    if not isinstance(keep_output_dir, bool):
        raise RecordingError("_keep_hydra_output_dir must be a boolean")
    setup = as_list(spec.get("setup"), field="setup")
    for index, step in enumerate(setup, start=1):
        if not isinstance(step, dict):
            raise RecordingError("each setup step must be a mapping")
        step_command_text(step, index, field="setup", spec=spec)
        name = step.get("name")
        if name is not None and (not isinstance(name, str) or not name):
            raise RecordingError(f"setup.{index}.name must be a non-empty string")
    cleanup = as_list(spec.get("cleanup"), field="cleanup")
    for index, step in enumerate(cleanup, start=1):
        if not isinstance(step, dict):
            raise RecordingError("each cleanup step must be a mapping")
        step_command_text(step, index, field="cleanup", spec=spec)
        name = step.get("name")
        if name is not None and (not isinstance(name, str) or not name):
            raise RecordingError(f"cleanup.{index}.name must be a non-empty string")
    beats = as_list(spec.get("beats"), field="beats")
    if not beats:
        raise RecordingError("recording config must contain at least one beat")
    for beat in beats:
        if not isinstance(beat, dict):
            raise RecordingError("each beat must be a mapping")
        require_string(beat, "id")
        actions = as_list(beat.get("actions"), field=f"beats.{beat['id']}.actions")
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                raise RecordingError(f"beat {beat['id']} action must be a mapping")
            entries = action_command_entries(
                action,
                index,
                field=f"beats.{beat['id']}.actions",
                spec=spec,
            )
            if entries is None:
                step_command_text(
                    action,
                    index,
                    field=f"beats.{beat['id']}.actions",
                    spec=spec,
                )
        checks = as_list(beat.get("checks"), field=f"beats.{beat['id']}.checks")
        for index, check in enumerate(checks, start=1):
            if not isinstance(check, dict):
                raise RecordingError(f"beat {beat['id']} check must be a mapping")
            step_command_text(
                check,
                index,
                field=f"beats.{beat['id']}.checks",
                spec=spec,
            )
            name = check.get("name")
            if name is not None and (not isinstance(name, str) or not name):
                raise RecordingError(
                    f"beat {beat['id']} check name must be a non-empty string"
                )


def shell_expect_args(expect: dict[str, Any]) -> list[str]:
    args: list[str] = []
    exit_code = expect.get("exit_code", 0)
    if not isinstance(exit_code, int):
        raise RecordingError("expect.exit_code must be an integer")
    args.extend(["exit", str(exit_code)])
    for field, gate_name in [
        ("output_contains", "contains"),
        ("output_regex", "regex"),
        ("file_exists", "file"),
    ]:
        for value in as_list(expect.get(field), field=f"expect.{field}"):
            if not isinstance(value, str) or not value:
                raise RecordingError(f"expect.{field} values must be non-empty strings")
            args.extend([gate_name, value])
    return args


def action_progress_labels(action: dict[str, Any], *, field: str) -> list[str]:
    labels: list[str] = []
    for value in as_list(action.get("progress"), field=f"{field}.progress"):
        if not isinstance(value, str) or not value:
            raise RecordingError(f"{field}.progress values must be non-empty strings")
        labels.append(value)
    return labels


def timeline_path_for_cast(cast_path: Path) -> Path:
    return cast_path.with_suffix(".timeline.jsonl")


def failure_path_for_cast(cast_path: Path) -> Path:
    return cast_path.with_suffix(".failure.json")


def staged_cast_path_for(cast_path: Path) -> Path:
    token = f"recording-{os.getpid()}"
    for index in range(100):
        suffix = "" if index == 0 else f"-{index}"
        candidate = cast_path.with_name(f".{cast_path.name}.{token}{suffix}.cast")
        if not candidate.exists():
            return candidate
    raise RecordingError(f"could not allocate staged recording path beside {cast_path}")


def remove_recording_artifacts(paths: list[Path]) -> list[Path]:
    removed: list[Path] = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
            else:
                continue
        except OSError as exc:
            raise RecordingError(
                f"failed to remove interrupted recording artifact: {path}"
            ) from exc
        removed.append(path)
    return removed


def run_artifact_dir(spec: dict[str, Any]) -> Path:
    return relative_path(require_string(spec, "_hydra_output_dir"))


def copy_run_artifact(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def preserve_successful_run_artifacts(
    spec: dict[str, Any], *, cast_path: Path, timeline_path: Path
) -> None:
    run_dir = run_artifact_dir(spec)
    copy_run_artifact(cast_path, run_dir / "recording.cast")
    copy_run_artifact(timeline_path, run_dir / "recording.timeline.jsonl")


def preserve_failed_run_artifacts(
    spec: dict[str, Any], *, cast_path: Path, timeline_path: Path
) -> None:
    run_dir = run_artifact_dir(spec)
    copy_run_artifact(cast_path, run_dir / "failed.cast")
    copy_run_artifact(timeline_path, run_dir / "failed.timeline.jsonl")


def format_interrupted_recording(cast_path: Path, removed: list[Path]) -> str:
    return "recording cancelled by user\noutput was not updated."


def recording_was_interrupted(returncode: int) -> bool:
    return returncode in INTERRUPT_RETURNCODES


def postmortem_entrypoint_text(*, run_dir: str, workdir: str, venv: str) -> str:
    run_id = Path(run_dir).name
    prompt_name = f"omegaflow:{run_id}"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"run_dir={shlex.quote(run_dir)}",
        f"workdir={shlex.quote(workdir)}",
        f"venv={shlex.quote(venv)}",
        f"export OMEGAFLOW_STUDIO_RUN_ID={shlex.quote(run_id)}",
        "export OMEGAFLOW_STUDIO_POSTMORTEM=1",
        'export OMEGAFLOW_STUDIO_RUN_DIR="$run_dir"',
        'export OMEGAFLOW_STUDIO_WORKDIR="$workdir"',
        'export OMEGAFLOW_STUDIO_VENV="$venv"',
        'cd "$workdir"',
        'if [[ -n "$venv" && -f "$venv/bin/activate" ]]; then',
        '  . "$venv/bin/activate"',
        'elif [[ -n "$venv" ]]; then',
        "  printf 'warning: venv activate script not found: %s\\n' \"$venv/bin/activate\" >&2",
        "fi",
        "printf 'OmegaFlow postmortem shell\\n'",
        "printf '  run dir: %s\\n' \"$run_dir\"",
        "printf '  workdir: %s\\n' \"$workdir\"",
        'if [[ -n "$venv" ]]; then',
        "  printf '  venv: %s\\n' \"$venv\"",
        "fi",
        'prompt_dir="$run_dir/shell"',
        'mkdir -p "$prompt_dir"',
        'shell_path="${SHELL:-/bin/sh}"',
        'shell_name="$(basename "$shell_path")"',
        'case "$shell_name" in',
        "  zsh)",
        '    zsh_dir="$prompt_dir/zsh"',
        '    mkdir -p "$zsh_dir"',
        "    cat > \"$zsh_dir/.zshrc\" <<'EOF'",
        f"PROMPT='%F{{cyan}}[{prompt_name}]%f %~ %# '",
        "RPROMPT=''",
        "EOF",
        '    ZDOTDIR="$zsh_dir" exec "$shell_path" -i',
        "    ;;",
        "  bash)",
        '    bashrc="$prompt_dir/bashrc"',
        "    cat > \"$bashrc\" <<'EOF'",
        f"PS1='\\[\\033[36m\\][{prompt_name}]\\[\\033[0m\\] \\w \\$ '",
        "EOF",
        '    exec "$shell_path" --rcfile "$bashrc" -i',
        "    ;;",
        "  *)",
        f"    PS1='[{prompt_name}] $ '",
        "    export PS1",
        '    exec "$shell_path" -i',
        "    ;;",
        "esac",
    ]
    return "\n".join(lines) + "\n"


def write_postmortem_entrypoint(
    path: Path, *, run_dir: str, workdir: str, venv: str
) -> None:
    path.write_text(
        postmortem_entrypoint_text(run_dir=run_dir, workdir=workdir, venv=venv),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    metadata = {
        "entrypoint": str(path),
        "run_dir": run_dir,
        "venv": venv,
        "workdir": workdir,
    }
    path.with_suffix(".json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def refresh_postmortem_entrypoint(path: Path) -> None:
    metadata_path = path.with_suffix(".json")
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecordingError(f"invalid postmortem metadata: {metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise RecordingError(f"postmortem metadata must be a mapping: {metadata_path}")
    run_dir = metadata.get("run_dir")
    workdir = metadata.get("workdir")
    venv = metadata.get("venv", "")
    if not isinstance(run_dir, str) or not run_dir:
        raise RecordingError(f"postmortem metadata missing run_dir: {metadata_path}")
    if not isinstance(workdir, str) or not workdir:
        raise RecordingError(f"postmortem metadata missing workdir: {metadata_path}")
    if not isinstance(venv, str):
        raise RecordingError(
            f"postmortem metadata field venv must be a string: {metadata_path}"
        )
    write_postmortem_entrypoint(path, run_dir=run_dir, workdir=workdir, venv=venv)


def beat_progress_index(spec: dict[str, Any]) -> tuple[dict[str, int], int]:
    beats = [
        beat
        for beat in as_list(spec.get("beats"), field="beats")
        if isinstance(beat, dict) and isinstance(beat.get("id"), str)
    ]
    return {str(beat["id"]): index for index, beat in enumerate(beats, 1)}, len(beats)


def progress_message_for_event(
    event: dict[str, Any],
    *,
    beat_indexes: dict[str, int],
    beat_count: int,
) -> str | None:
    phase = event.get("phase")
    beat = event.get("beat")
    if phase == "check_start" and beat == "__setup__":
        check = event.get("check") or event.get("check_id") or "setup"
        return f"setup: {check}"
    if phase == "caption_start":
        caption = event.get("caption")
        if not isinstance(caption, str) or not caption:
            return None
        if isinstance(beat, str) and beat in beat_indexes:
            return f"stage {beat_indexes[beat]}/{beat_count}: {caption}"
        return f"stage: {caption}"
    if phase == "command_run_start":
        label = event.get("progress")
        if isinstance(label, str) and label:
            return f"running: {label}"
        display = event.get("display")
        if isinstance(display, str) and display:
            first_line = display.strip().splitlines()[0] if display.strip() else ""
            if first_line:
                return f"running: {first_line}"
        command = event.get("command")
        if not isinstance(command, str) or not command:
            return None
        first_line = command.strip().splitlines()[0] if command.strip() else ""
        if first_line:
            return f"running: {first_line}"
    if phase == "check_start" and beat != "__setup__":
        check = event.get("check") or event.get("check_id")
        if isinstance(check, str) and check:
            return f"check: {check}"
    return None


class ProgressEventPrinter:
    def __init__(self, spec: dict[str, Any], *, color: bool) -> None:
        self.color = color
        self.beat_indexes, self.beat_count = beat_progress_index(spec)

    def emit(self, event: dict[str, Any]) -> None:
        message = progress_message_for_event(
            event,
            beat_indexes=self.beat_indexes,
            beat_count=self.beat_count,
        )
        if message is None:
            return
        print(
            progress_status_line(message, color=self.color),
            file=sys.stderr,
            flush=True,
        )


class RecordProgressBar:
    def __init__(self, spec: dict[str, Any], *, color: bool) -> None:
        self.beat_indexes, self.beat_count = beat_progress_index(spec)
        self.renderer = ProgressBarRenderer(stream=sys.stderr, enabled=color)
        self.current = 0

    def emit(self, event: dict[str, Any]) -> None:
        message = progress_message_for_event(
            event,
            beat_indexes=self.beat_indexes,
            beat_count=self.beat_count,
        )
        if message is None:
            return
        status = "step"
        phase = event.get("phase")
        beat = event.get("beat")
        if phase == "caption_start" and isinstance(beat, str):
            self.current = self.beat_indexes.get(beat, self.current)
            message = message.removeprefix(f"stage {self.current}/{self.beat_count}: ")
        elif phase == "command_run_start":
            status = "cmd"
            message = message.removeprefix("running: ")
        elif phase == "check_start" and beat != "__setup__":
            status = "check"
            message = message.removeprefix("check: ")
        self.renderer.emit(
            {
                "current": self.current,
                "message": message,
                "phase": "status",
                "status": status,
                "total": self.beat_count,
            }
        )

    def finish(self) -> None:
        self.renderer.finish()


def read_timeline_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordingError(
                f"invalid timeline event in {path}:{line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise RecordingError(
                f"timeline event must be a mapping: {path}:{line_number}"
            )
        events.append(event)
    return events


def read_failure_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecordingError(f"invalid failure report: {path}") from exc
    if not isinstance(report, dict):
        raise RecordingError(f"failure report must be a mapping: {path}")
    return report


def failure_run_identity(report: dict[str, Any]) -> tuple[str | None, str | None]:
    recording_id = report.get("recording_id")
    run_id = report.get("run_id")
    if (
        isinstance(recording_id, str)
        and recording_id
        and isinstance(run_id, str)
        and run_id
    ):
        return recording_id, run_id
    run_dir = report.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir:
        return (
            recording_id if isinstance(recording_id, str) and recording_id else None,
            run_id if isinstance(run_id, str) and run_id else None,
        )
    path = Path(run_dir)
    if not isinstance(run_id, str) or not run_id:
        run_id = path.name
    if not isinstance(recording_id, str) or not recording_id:
        recording_id = path.parent.name if path.parent.name else None
    return recording_id, run_id


def recording_tool_command(
    *, recording_id: str | None = None, action: str, run_id: str
) -> str:
    parts = ["python -m omegaflow_studio.record"]
    if recording_id:
        parts.append(f"recording={recording_id}")
    parts.extend([f"action={action}", f"run_id={run_id}"])
    return " ".join(shlex.quote(part) for part in parts)


def append_run_action_hints(
    lines: list[str], report: dict[str, Any], *, color: bool = False
) -> None:
    recording_id, run_id = failure_run_identity(report)
    if not run_id:
        return
    lines.append(color_text(f"run_id: {run_id}", ANSI_CYAN_BOLD, enabled=color))
    lines.append(color_text("Inspect run with:", ANSI_CYAN_BOLD, enabled=color))
    lines.append(
        "  "
        + recording_tool_command(
            recording_id=recording_id, action="inspect", run_id=run_id
        )
    )
    lines.append(color_text("Play run with:", ANSI_CYAN_BOLD, enabled=color))
    lines.append("  " + recording_tool_command(action="play", run_id=run_id))
    lines.append(color_text("View output with:", ANSI_CYAN_BOLD, enabled=color))
    lines.append(
        "  "
        + recording_tool_command(
            recording_id=recording_id, action="output", run_id=run_id
        )
    )


def check_intervals_from_timeline(
    events: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    starts: dict[str, float] = {}
    intervals: list[tuple[float, float]] = []
    for event in events:
        phase = event.get("phase")
        check_id = event.get("check_id")
        timestamp = event.get("time")
        if (
            phase not in {"check_start", "check_end"}
            or not isinstance(check_id, str)
            or not isinstance(timestamp, (int, float))
        ):
            continue
        if phase == "check_start":
            starts[check_id] = float(timestamp)
            continue
        start = starts.pop(check_id, None)
        if start is not None and timestamp > start:
            intervals.append((start, float(timestamp)))
    return merge_intervals(intervals)


def unfinished_check_from_timeline(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    starts: dict[str, dict[str, Any]] = {}
    for event in events:
        phase = event.get("phase")
        check_id = event.get("check_id")
        if phase not in {"check_start", "check_end"} or not isinstance(check_id, str):
            continue
        if phase == "check_start":
            starts[check_id] = event
        else:
            starts.pop(check_id, None)
    if not starts:
        return None
    return list(starts.values())[-1]


def raw_index_for_plain_start(raw_line: str, plain_start: int) -> int | None:
    if plain_start == 0:
        return 0
    raw_index = 0
    plain_index = 0
    while raw_index < len(raw_line):
        match = ANSI_CONTROL_RE.match(raw_line, raw_index)
        if match is not None:
            raw_index = match.end()
            continue
        if plain_index == plain_start:
            return raw_index
        raw_index += 1
        plain_index += 1
    return len(raw_line) if plain_index == plain_start else None


def raw_index_for_plain_end(raw_line: str, plain_end: int) -> int | None:
    raw_index = 0
    plain_index = 0
    while raw_index < len(raw_line) and plain_index < plain_end:
        match = ANSI_CONTROL_RE.match(raw_line, raw_index)
        if match is not None:
            raw_index = match.end()
            continue
        raw_index += 1
        plain_index += 1
    if plain_index != plain_end:
        return None
    while raw_index < len(raw_line):
        match = ANSI_CONTROL_RE.match(raw_line, raw_index)
        if match is None:
            break
        raw_index = match.end()
    return raw_index


def raw_span_for_plain_span(
    raw_line: str, plain_start: int, plain_end: int
) -> str | None:
    raw_start = raw_index_for_plain_start(raw_line, plain_start)
    raw_end = raw_index_for_plain_end(raw_line, plain_end)
    if raw_start is None or raw_end is None or raw_start > raw_end:
        return None
    return raw_line[raw_start:raw_end]


def expand_terminal_animation_replacement(
    raw_line: str,
    plain_match: re.Match[str],
    replacement: str,
) -> str:
    result = replacement
    for name, value in plain_match.groupdict().items():
        if value is None:
            continue
        plain_start, plain_end = plain_match.span(name)
        raw_value = raw_span_for_plain_span(raw_line, plain_start, plain_end)
        replacement_value = raw_value if raw_value is not None else value
        result = result.replace(f"{{{name}}}", replacement_value)
    return result


def terminal_animation_rule_for_line(
    line: str, rules: list[dict[str, Any]]
) -> tuple[int, str] | None:
    plain = ANSI_CONTROL_RE.sub("", line)
    for index, rule in enumerate(rules):
        regex = rule.get("regex")
        replacement = rule.get("replacement")
        if not isinstance(regex, str) or not isinstance(replacement, str):
            continue
        try:
            pattern = re.compile(regex)
        except re.error:
            continue
        match = pattern.fullmatch(plain)
        if match is not None:
            return index, expand_terminal_animation_replacement(
                line,
                match,
                replacement,
            )
    return None


def collapse_terminal_animation_lines(text: str, rules: list[dict[str, Any]]) -> str:
    lines = text.splitlines(keepends=True)
    collapsed: list[str] = []
    pending: list[str] = []
    pending_rule: int | None = None
    pending_replacement: str | None = None

    def flush_pending() -> None:
        nonlocal pending, pending_rule, pending_replacement
        if not pending:
            return
        if len(pending) >= 3 and pending_replacement is not None:
            newline = "\n" if pending[-1].endswith("\n") else ""
            collapsed.append(f"{pending_replacement}{newline}")
        else:
            collapsed.extend(pending)
        pending = []
        pending_rule = None
        pending_replacement = None

    for line in lines:
        stripped = line.rstrip("\n")
        match = terminal_animation_rule_for_line(stripped, rules)
        if match is None:
            flush_pending()
            collapsed.append(line)
            continue

        rule_index, replacement = match
        if pending and rule_index != pending_rule:
            flush_pending()
        pending.append(line)
        pending_rule = rule_index
        pending_replacement = replacement

    flush_pending()
    return "".join(collapsed)


def format_recording_failure(
    *,
    returncode: int,
    command: list[str],
    cast_path: Path,
    timeline_path: Path,
    failure_path: Path,
    color: bool = False,
) -> str:
    lines = [
        color_text(
            f"asciinema recording failed with exit code {returncode}",
            ANSI_RED_BOLD,
            enabled=color,
        ),
        f"cast: {cast_path}",
    ]
    report = read_failure_report(failure_path)
    if report is not None:
        kind = report.get("kind", "step")
        name = report.get("name")
        step_id = report.get("id")
        label = str(kind)
        if isinstance(name, str) and name:
            label = f"{label} {name!r}"
        if isinstance(step_id, str) and step_id:
            label = f"{label} ({step_id})"
        lines.append(
            color_text(f"session failed during {label}", ANSI_RED_BOLD, enabled=color)
        )
        message = report.get("message")
        if isinstance(message, str) and message:
            lines.append(color_text(f"reason: {message}", ANSI_RED_BOLD, enabled=color))
        stderr = report.get("stderr")
        if isinstance(stderr, str) and stderr:
            failure_summary = report.get("failure_summary")
            terminal_animations = (
                failure_summary.get("terminal_animations")
                if isinstance(failure_summary, dict)
                else None
            )
            if isinstance(terminal_animations, list):
                stderr = collapse_terminal_animation_lines(stderr, terminal_animations)
            label = "stderr"
            if report.get("stderr_truncated"):
                label = "stderr (last 12000 chars)"
            lines.append(
                color_text(f"--- {label} ---", ANSI_YELLOW_BOLD, enabled=color)
            )
            lines.append(stderr.rstrip())
            lines.append(
                color_text("--- end stderr ---", ANSI_YELLOW_BOLD, enabled=color)
            )
        append_run_action_hints(lines, report, color=color)
        return "\n".join(lines)

    events = read_timeline_events(timeline_path)
    unfinished = unfinished_check_from_timeline(events)
    if unfinished is not None:
        check = unfinished.get("check")
        check_id = unfinished.get("check_id")
        beat = unfinished.get("beat")
        if isinstance(check, str) and check:
            lines.append(
                color_text(
                    f"last hidden step started: {check}",
                    ANSI_YELLOW_BOLD,
                    enabled=color,
                )
            )
        if isinstance(beat, str) and isinstance(check_id, str):
            lines.append(f"timeline marker: beat={beat} check_id={check_id}")
        lines.append(
            color_text(
                "no failure sidecar was written; the session may have exited before "
                "the recorder could capture hidden-step output",
                ANSI_YELLOW_BOLD,
                enabled=color,
            )
        )
    else:
        lines.append(
            color_text(
                "no session failure sidecar was written",
                ANSI_YELLOW_BOLD,
                enabled=color,
            )
        )
    lines.append("command: " + " ".join(shlex.quote(part) for part in command))
    return "\n".join(lines)


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
    return merged


def removed_time_before(
    timestamp: float, intervals: list[tuple[float, float]]
) -> float:
    removed = 0.0
    for start, end in intervals:
        if timestamp <= start:
            break
        removed += max(0.0, min(timestamp, end) - start)
    return removed


def timestamp_in_interval(
    timestamp: float, intervals: list[tuple[float, float]]
) -> bool:
    return any(start < timestamp < end for start, end in intervals)


def first_output_timestamp(raw_lines: list[str]) -> float | None:
    absolute_time = 0.0
    for line in raw_lines[1:]:
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(event, list)
            or len(event) != 3
            or not isinstance(event[0], (int, float))
        ):
            continue
        absolute_time += float(event[0])
        if event[1] == "o" and isinstance(event[2], str) and event[2]:
            return absolute_time
    return None


def output_timestamps(raw_lines: list[str]) -> list[float]:
    timestamps: list[float] = []
    absolute_time = 0.0
    for line in raw_lines[1:]:
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return timestamps
        if (
            not isinstance(event, list)
            or len(event) != 3
            or not isinstance(event[0], (int, float))
        ):
            continue
        absolute_time += float(event[0])
        if event[1] == "o" and isinstance(event[2], str) and event[2]:
            timestamps.append(absolute_time)
    return timestamps


def clip_leading_interval_at_first_output(
    intervals: list[tuple[float, float]], first_output: float | None
) -> list[tuple[float, float]]:
    if first_output is None:
        return intervals
    clipped: list[tuple[float, float]] = []
    clipped_leading = False
    for start, end in intervals:
        if not clipped_leading and start < first_output < end:
            clipped.append((start, first_output))
            clipped_leading = True
        else:
            clipped.append((start, end))
    return merge_intervals(clipped)


def drop_jitter_intervals_with_visible_output(
    intervals: list[tuple[float, float]], timestamps: list[float]
) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    for start, end in intervals:
        duration = end - start
        if duration <= HIDDEN_INTERVAL_JITTER_TOLERANCE_SECONDS and any(
            start < timestamp < end for timestamp in timestamps
        ):
            continue
        kept.append((start, end))
    return kept


def strip_cast_intervals(cast_path: Path, intervals: list[tuple[float, float]]) -> None:
    intervals = merge_intervals(intervals)
    if not intervals:
        return

    raw_lines = cast_path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise RecordingError(f"cast file is empty: {cast_path}")
    intervals = clip_leading_interval_at_first_output(
        intervals, first_output_timestamp(raw_lines)
    )
    intervals = drop_jitter_intervals_with_visible_output(
        intervals, output_timestamps(raw_lines)
    )

    output_lines = [raw_lines[0]]
    absolute_time = 0.0
    previous_adjusted_time = 0.0
    for line_number, line in enumerate(raw_lines[1:], 2):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordingError(
                f"invalid asciinema event in {cast_path}:{line_number}"
            ) from exc
        if (
            not isinstance(event, list)
            or len(event) != 3
            or not isinstance(event[0], (int, float))
        ):
            output_lines.append(line)
            continue
        absolute_time += float(event[0])
        if (
            event[1] == "o"
            and isinstance(event[2], str)
            and event[2]
            and timestamp_in_interval(absolute_time, intervals)
        ):
            raise RecordingError(
                "visible cast output overlaps hidden check interval at "
                f"{absolute_time:.3f}s in {cast_path}"
            )
        adjusted_time = absolute_time - removed_time_before(absolute_time, intervals)
        event[0] = round(max(0.0, adjusted_time - previous_adjusted_time), 6)
        previous_adjusted_time = adjusted_time
        output_lines.append(json.dumps(event, separators=(",", ":")))

    cast_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def normalize_cast_header(cast_path: Path, spec: dict[str, Any]) -> None:
    raw_lines = cast_path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise RecordingError(f"cast file is empty: {cast_path}")
    try:
        header = json.loads(raw_lines[0])
    except json.JSONDecodeError as exc:
        raise RecordingError(f"invalid asciinema header in {cast_path}") from exc
    if not isinstance(header, dict):
        raise RecordingError(f"asciinema header must be a mapping: {cast_path}")

    header["command"] = (
        f"omegaflow recording={require_string(spec, 'id')} " "step=session"
    )
    header.pop("env", None)

    capture = as_mapping(spec.get("capture"), field="capture")
    if capture.get("idle_time_limit") is None:
        header.pop("idle_time_limit", None)
    else:
        header["idle_time_limit"] = capture["idle_time_limit"]

    output_lines = [json.dumps(header, separators=(",", ":"))]
    output_lines.extend(raw_lines[1:])
    cast_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


CONTROL_OVERRIDE_PREFIXES = (
    "action=",
    "step=",
    "output=",
    "cast=",
    "timeline=",
    "headed=",
    "force=",
    "timestamps=",
    "allow_mismatch=",
    "run_id=",
)


def is_control_override(override: object) -> bool:
    text = str(override)
    return any(text.startswith(prefix) for prefix in CONTROL_OVERRIDE_PREFIXES)


def session_overrides_from_spec(spec: dict[str, Any]) -> list[str]:
    overrides = spec.get("_overrides", [])
    if not isinstance(overrides, list):
        overrides = []
    result = [
        str(override)
        for override in overrides
        if not is_control_override(override)
        and not str(override).startswith("hydra.run.dir=")
        and not str(override).startswith("recording=")
    ]
    result.insert(0, f"recording={require_string(spec, 'id')}")
    result.append("step=session")
    hydra_output_dir = require_string(spec, "_hydra_output_dir")
    result.append(f"hydra.run.dir={hydra_output_dir}")
    return result


def validate_session_overrides(overrides: list[str]) -> None:
    command = [
        sys.executable,
        "-m",
        "omegaflow_studio.record",
        *overrides,
        "--cfg",
        "job",
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").strip()
    if not detail:
        detail = f"exit code {result.returncode}"
    raise RecordingError(f"invalid recording session config: {detail}")


def has_recording_config(spec: dict[str, Any]) -> bool:
    manifest = spec.get("_manifest_path")
    if isinstance(manifest, str) and manifest:
        return relative_path(manifest).exists()
    recording_dir = spec.get("_recording_dir")
    if isinstance(recording_dir, str) and recording_dir:
        recording_id = require_string(spec, "id")
        return recording_script_path(recording_id, Path(recording_dir)).exists()
    recording_id = require_string(spec, "id")
    return recording_script_path(recording_id, RECORDING_SCRIPT_DIR).exists()


def render_session_script(spec: dict[str, Any]) -> str:
    environment = as_mapping(spec.get("environment"), field="environment")
    working_directory = environment.get("working_directory", ".")
    if not isinstance(working_directory, str):
        raise RecordingError("environment.working_directory must be a string")
    workdir = relative_path(working_directory)

    path_prepend = as_list(
        environment.get("path_prepend"), field="environment.path_prepend"
    )
    path_entries = [str(relative_path(str(entry))) for entry in path_prepend]
    path_entries.append(str(Path(sys.executable).parent))
    path_prefix = ":".join(path_entries)
    environment_variables = as_mapping(
        environment.get("variables"), field="environment.variables"
    )
    style = as_mapping(spec.get("style"), field="style")
    color = bool(style.get("color", True))
    typing = bool(style.get("typing", True))
    typing_min_delay = require_positive_number(style, "typing_min_delay", 0.012)
    typing_max_delay = require_positive_number(style, "typing_max_delay", 0.045)
    if typing_min_delay > typing_max_delay:
        raise RecordingError(
            "style.typing_min_delay must be less than or equal to style.typing_max_delay"
        )
    typing_space_delay = require_non_negative_number(style, "typing_space_delay", 0.025)
    typing_punctuation_delay = require_non_negative_number(
        style, "typing_punctuation_delay", 0.05
    )
    typing_newline_delay = require_non_negative_number(
        style, "typing_newline_delay", 0.16
    )
    typing_seed = require_integer(style, "typing_seed", 17)
    failure_summary = failure_summary_config(spec)
    capture = as_mapping(spec.get("capture"), field="capture")
    baseline_compressed = bool(capture.get("baseline_compressed", False))
    session_typing = typing and not baseline_compressed
    parameters = as_mapping(spec.get("parameters", {}), field="parameters")
    hydra_output_dir = require_string(spec, "_hydra_output_dir")
    keep_hydra_output_dir = bool(spec.get("_keep_hydra_output_dir", False))

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shell_quote(workdir)}",
    ]
    if path_prefix:
        lines.append(f'export PATH={shell_quote(path_prefix)}:"$PATH"')
    for key, value in sorted(environment_variables.items()):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RecordingError(
                "environment.variables keys must be shell-safe variable names"
            )
        if not isinstance(value, (str, int, float, bool)):
            raise RecordingError(f"environment.variables.{key} must be a scalar value")
        lines.append(f"export {key}={shell_quote(value)}")
    lines.extend(
        [
            f"recording_color={shell_quote(1 if color else 0)}",
            f"recording_id={shell_quote(require_string(spec, 'id'))}",
            f"recording_python={shell_quote(sys.executable)}",
            f"recording_baseline_compressed={shell_quote(1 if baseline_compressed else 0)}",
            f"recording_typing={shell_quote(1 if session_typing else 0)}",
            f"recording_typing_min_delay={shell_quote(typing_min_delay)}",
            f"recording_typing_max_delay={shell_quote(typing_max_delay)}",
            f"recording_typing_space_delay={shell_quote(typing_space_delay)}",
            f"recording_typing_punctuation_delay={shell_quote(typing_punctuation_delay)}",
            f"recording_typing_newline_delay={shell_quote(typing_newline_delay)}",
            f"recording_typing_seed={shell_quote(typing_seed)}",
            f"export OMEGAFLOW_STUDIO_FAILURE_SUMMARY={shell_quote(json.dumps(failure_summary, separators=(',', ':')))}",
            'if [[ "$recording_color" == 1 ]]; then',
            "  export CLICOLOR_FORCE=1",
            "  export FORCE_COLOR=1",
            "  export PY_COLORS=1",
            "  export TERM=xterm-256color",
            "  unset NO_COLOR",
            "else",
            "  export NO_COLOR=1",
            "fi",
            "export recording_typing_min_delay",
            "export recording_typing_max_delay",
            "export recording_typing_space_delay",
            "export recording_typing_punctuation_delay",
            "export recording_typing_newline_delay",
            "export recording_typing_seed",
            'recording_timeline_path="${OMEGAFLOW_STUDIO_TIMELINE:-}"',
            f'recording_progress_pipe_path="${{{PROGRESS_PIPE_ENV}:-}}"',
            'recording_failure_path="${OMEGAFLOW_STUDIO_FAILURE:-}"',
            'recording_start_epoch="$("$recording_python" - <<\'PY\'',
            "import time",
            "print(time.time())",
            "PY",
            ')"',
            'if [[ -n "$recording_timeline_path" ]]; then',
            '  : > "$recording_timeline_path"',
            "fi",
            "unset VIRTUAL_ENV",
            "unset VIRTUAL_ENV_PROMPT",
        ]
    )
    for key, value in sorted(parameters.items()):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RecordingError("parameters keys must be shell-safe names")
        if not isinstance(value, (str, int, float, bool)):
            raise RecordingError(f"parameters.{key} must be a scalar value")
        lines.append(f"recording_param_{key}={shell_quote(value)}")
        lines.append(f"export recording_param_{key}")
    run_dir_path = relative_path(hydra_output_dir)
    postmortem_path = run_dir_path / "enter"
    lines.extend(
        [
            f"recording_run_dir={shell_quote(run_dir_path)}",
            f"recording_tmp={shell_quote(run_dir_path)}",
            f"recording_postmortem_path={shell_quote(postmortem_path)}",
            'recording_run_id="$(basename "$recording_run_dir")"',
            'recording_run_failure_path="$recording_run_dir/failure.json"',
            'recording_stdout_path="$recording_run_dir/stdout"',
            'recording_stderr_path="$recording_run_dir/stderr"',
            'recording_progress_path="$recording_run_dir/progress"',
            f"recording_keep_hydra_output_dir={shell_quote(1 if keep_hydra_output_dir else 0)}",
            'mkdir -p "$recording_tmp"',
            ': > "$recording_stdout_path"',
            ': > "$recording_stderr_path"',
            ': > "$recording_progress_path"',
            "log_stage_marker() {",
            '  local phase="$1"',
            '  local beat_id="$2"',
            '  local index="$3"',
            '  local total="$4"',
            '  printf "::: stage %s/%s %s beat=%s\\n" "$index" "$total" "$phase" "$beat_id" >>"$recording_progress_path"',
            "}",
            "recording_write_postmortem_entrypoint() {",
            '  local workdir="${1:-$PWD}"',
            '  local venv="${2:-}"',
            '  "$recording_python" - "$recording_postmortem_path" "$workdir" "$venv" "$recording_run_dir" <<\'PY\'',
            "import json",
            "import shlex",
            "import stat",
            "import sys",
            "from pathlib import Path",
            "",
            "path = Path(sys.argv[1])",
            "workdir = sys.argv[2]",
            "venv = sys.argv[3]",
            "run_dir = sys.argv[4]",
            "run_id = Path(run_dir).name",
            "prompt_name = f'omegaflow:{run_id}'",
            "lines = [",
            "    '#!/usr/bin/env bash',",
            "    'set -euo pipefail',",
            "    f'run_dir={shlex.quote(run_dir)}',",
            "    f'workdir={shlex.quote(workdir)}',",
            "    f'venv={shlex.quote(venv)}',",
            "    f'export OMEGAFLOW_STUDIO_RUN_ID={shlex.quote(run_id)}',",
            "    'export OMEGAFLOW_STUDIO_POSTMORTEM=1',",
            "    'export OMEGAFLOW_STUDIO_RUN_DIR=\"$run_dir\"',",
            "    'export OMEGAFLOW_STUDIO_WORKDIR=\"$workdir\"',",
            "    'export OMEGAFLOW_STUDIO_VENV=\"$venv\"',",
            "    'cd \"$workdir\"',",
            '    \'if [[ -n "$venv" && -f "$venv/bin/activate" ]]; then\',',
            "    '  . \"$venv/bin/activate\"',",
            "    'elif [[ -n \"$venv\" ]]; then',",
            '    "  printf \'warning: venv activate script not found: %s\\\\n\' \\"$venv/bin/activate\\" >&2",',
            "    'fi',",
            "    \"printf 'OmegaFlow postmortem shell\\\\n'\",",
            '    "printf \'  run dir: %s\\\\n\' \\"$run_dir\\"",',
            '    "printf \'  workdir: %s\\\\n\' \\"$workdir\\"",',
            "    'if [[ -n \"$venv\" ]]; then',",
            '    "  printf \'  venv: %s\\\\n\' \\"$venv\\"",',
            "    'fi',",
            "    'prompt_dir=\"$run_dir/shell\"',",
            "    'mkdir -p \"$prompt_dir\"',",
            "    'shell_path=\"${SHELL:-/bin/sh}\"',",
            '    \'shell_name="$(basename "$shell_path")"\',',
            "    'case \"$shell_name\" in',",
            "    '  zsh)',",
            "    '    zsh_dir=\"$prompt_dir/zsh\"',",
            "    '    mkdir -p \"$zsh_dir\"',",
            "    '    cat > \"$zsh_dir/.zshrc\" <<\\'EOF\\'',",
            "    f\"PROMPT='%F{{cyan}}[{prompt_name}]%f %~ %# '\",",
            "    \"RPROMPT=''\",",
            "    'EOF',",
            '    \'    ZDOTDIR="$zsh_dir" exec "$shell_path" -i\',',
            "    '    ;;',",
            "    '  bash)',",
            "    '    bashrc=\"$prompt_dir/bashrc\"',",
            "    '    cat > \"$bashrc\" <<\\'EOF\\'',",
            "    f\"PS1='\\\\[\\\\033[36m\\\\][{prompt_name}]\\\\[\\\\033[0m\\\\] \\\\w \\\\$ '\",",
            "    'EOF',",
            '    \'    exec "$shell_path" --rcfile "$bashrc" -i\',',
            "    '    ;;',",
            "    '  *)',",
            "    f\"    PS1='[{prompt_name}] $ '\",",
            "    '    export PS1',",
            "    '    exec \"$shell_path\" -i',",
            "    '    ;;',",
            "    'esac',",
            "]",
            "path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')",
            "path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)",
            "metadata = {'run_dir': run_dir, 'workdir': workdir, 'venv': venv, 'entrypoint': str(path)}",
            "path.with_suffix('.json').write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
            "PY",
            "}",
            'recording_write_postmortem_entrypoint "$PWD" ""',
            'if [[ "$recording_keep_hydra_output_dir" == 1 ]]; then',
            "  cleanup_paths=()",
            "else",
            '  cleanup_paths=("$recording_tmp")',
            "fi",
            "cleanup_names=()",
            "cleanup_commands=()",
            "register_script_cleanup() {",
            '  cleanup_names+=("$1")',
            '  cleanup_commands+=("$2")',
            "}",
        ]
    )
    for index, step in enumerate(
        as_list(spec.get("cleanup"), field="cleanup"), start=1
    ):
        command = cleanup_command_text(step, index, spec=spec)
        cleanup_name = step.get("name", f"cleanup step {index}")
        if not isinstance(cleanup_name, str) or not cleanup_name:
            raise RecordingError(f"cleanup.{index}.name must be a non-empty string")
        lines.append(
            "register_script_cleanup "
            f"{shell_quote(cleanup_name)} "
            f"{shell_quote(command)}"
        )
    lines.extend(
        [
            "cleanup_pids=()",
            "run_script_cleanups() {",
            "  local index",
            "  local cleanup_name",
            "  local cleanup_command",
            "  local status",
            "  local cleanup_status=0",
            "  local marker",
            '  for index in "${!cleanup_commands[@]}"; do',
            '    cleanup_name="${cleanup_names[$index]}"',
            '    cleanup_command="${cleanup_commands[$index]}"',
            '    marker="::: cleanup cleanup_$((index + 1))"',
            '    printf "%s start %s\\n" "$marker" "$cleanup_name" >>"$recording_progress_path"',
            "    set +e",
            '    ( eval "$cleanup_command" ) >>"$recording_stdout_path" 2>>"$recording_stderr_path"',
            "    status=$?",
            "    set -e",
            '    printf "%s end status=%s\\n" "$marker" "$status" >>"$recording_progress_path"',
            '    if [[ "$status" -ne 0 ]]; then',
            '      [[ "$cleanup_status" -eq 0 ]] && cleanup_status="$status"',
            '      record_failure cleanup "cleanup_$((index + 1))" "$cleanup_name" "exited $status, expected 0" "$recording_stdout_path" "$recording_stderr_path" "$recording_progress_path"',
            "    fi",
            "  done",
            '  return "$cleanup_status"',
            "}",
            "cleanup() {",
            "  local exit_status=$?",
            "  trap - EXIT",
            "  set +e",
            "  local cleanup_status=0",
            "  run_script_cleanups",
            "  cleanup_status=$?",
            "  local pid",
            '  for pid in "${cleanup_pids[@]}"; do',
            '    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then',
            '      kill "$pid" 2>/dev/null || true',
            '      wait "$pid" 2>/dev/null || true',
            "    fi",
            "  done",
            "  local path",
            '  for path in "${cleanup_paths[@]}"; do',
            '    if [[ -n "$path" ]]; then',
            '      rm -rf "$path" || cleanup_status=1',
            "    fi",
            "  done",
            '  if [[ "$exit_status" -eq 0 && "$cleanup_status" -ne 0 ]]; then',
            '    exit "$cleanup_status"',
            "  fi",
            '  exit "$exit_status"',
            "}",
            "trap cleanup EXIT",
            "",
            "record_failure() {",
            '  local kind="$1"',
            '  local step_id="$2"',
            '  local step_name="$3"',
            '  local message="$4"',
            '  local output_path="${5:-}"',
            '  local stderr_path="${6:-}"',
            '  local progress_path="${7:-}"',
            '  "$recording_python" - "$recording_failure_path" "$recording_run_failure_path" "$kind" "$step_id" "$step_name" "$message" "$output_path" "$stderr_path" "$progress_path" "$recording_run_dir" "$recording_postmortem_path" "$recording_id" "$recording_run_id" <<\'PY\'',
            "import json",
            "import os",
            "import sys",
            "from pathlib import Path",
            "",
            "sidecar_path, run_failure_path, kind, step_id, step_name, message, output_path, stderr_path, progress_path, run_dir, postmortem_path, recording_id, run_id = sys.argv[1:]",
            "max_chars = 12000",
            "",
            "def read_capped(text_path):",
            "    if not text_path:",
            "        return '', False",
            "    try:",
            "        with open(text_path, 'r', encoding='utf-8', errors='replace') as handle:",
            "            text = handle.read()",
            "    except OSError as exc:",
            "        return f'<unable to read captured output: {exc}>', False",
            "    truncated = len(text) > max_chars",
            "    if truncated:",
            "        text = text[-max_chars:]",
            "    return text, truncated",
            "",
            "output, output_truncated = read_capped(output_path)",
            "stderr, stderr_truncated = read_capped(stderr_path)",
            "progress, progress_truncated = read_capped(progress_path)",
            "report = {",
            "    'kind': kind,",
            "    'id': step_id,",
            "    'name': step_name,",
            "    'message': message,",
            "    'output': output,",
            "    'output_path': output_path,",
            "    'output_truncated': output_truncated,",
            "    'stderr': stderr,",
            "    'stderr_path': stderr_path,",
            "    'stderr_truncated': stderr_truncated,",
            "    'progress': progress,",
            "    'progress_path': progress_path,",
            "    'progress_truncated': progress_truncated,",
            "    'run_dir': run_dir,",
            "    'postmortem_path': postmortem_path,",
            "    'recording_id': recording_id,",
            "    'run_id': run_id,",
            "    'failure_summary': json.loads(os.environ.get('OMEGAFLOW_STUDIO_FAILURE_SUMMARY', '{}')),",
            "}",
            "for report_path in [sidecar_path, run_failure_path]:",
            "    if not report_path:",
            "        continue",
            "    path = Path(report_path)",
            "    path.parent.mkdir(parents=True, exist_ok=True)",
            "    with path.open('w', encoding='utf-8') as handle:",
            "        json.dump(report, handle, indent=2, sort_keys=True)",
            "        handle.write('\\n')",
            "PY",
            "}",
            "",
            "fail_gate() {",
            '  local action_id="$1"',
            '  local message="$2"',
            '  record_failure action "$action_id" "$action_id" "$message" "$recording_stdout_path" "$recording_stderr_path" "$recording_progress_path"',
            '  if [[ "$recording_color" == 1 ]]; then',
            '    printf \'\\n\\033[31;1mrecording gate failed:\\033[0m %s %s\\n\' "$action_id" "$message" >&2',
            "  else",
            '    printf \'\\nrecording gate failed: %s %s\\n\' "$action_id" "$message" >&2',
            "  fi",
            "  exit 1",
            "}",
            "",
            "fail_check() {",
            '  local check_id="$1"',
            '  local check_name="$2"',
            '  local message="$3"',
            '  local output_path="$4"',
            '  local stderr_path="${5:-}"',
            '  record_failure check "$check_id" "$check_name" "$message" "$output_path" "$stderr_path" "$recording_progress_path"',
            '  if [[ "$recording_color" == 1 ]]; then',
            '    printf \'\\n\\033[31;1mrecording check failed:\\033[0m %s %s\\n\' "$check_name" "$message" >&2',
            "  else",
            '    printf \'\\nrecording check failed: %s %s\\n\' "$check_name" "$message" >&2',
            "  fi",
            '  if [[ -s "$stderr_path" ]]; then',
            "    printf -- '--- stderr ---\\n' >&2",
            '    "$recording_python" - "$stderr_path" <<\'PY\'',
            "import sys",
            "",
            "path = sys.argv[1]",
            "max_chars = 12000",
            "with open(path, 'r', encoding='utf-8', errors='replace') as handle:",
            "    text = handle.read()",
            "if len(text) > max_chars:",
            "    print(f'<stderr truncated to last {max_chars} chars>', file=sys.stderr)",
            "    text = text[-max_chars:]",
            "print(text, end='' if text.endswith('\\n') else '\\n', file=sys.stderr)",
            "PY",
            "    printf -- '--- end stderr ---\\n' >&2",
            "  fi",
            "  exit 1",
            "}",
            "",
            "timeline_event() {",
            '  local phase="$1"',
            '  local beat_id="$2"',
            '  local check_id="$3"',
            '  local check_name="$4"',
            "  shift 4",
            '  [[ -z "$recording_timeline_path" && -z "$recording_progress_pipe_path" ]] && return',
            '  "$recording_python" - "$recording_timeline_path" "$recording_progress_pipe_path" "$recording_start_epoch" "$phase" "$beat_id" "$check_id" "$check_name" "$@" <<\'PY\'',
            "import json",
            "import os",
            "import re",
            "import sys",
            "import time",
            "",
            "timeline_path, progress_pipe_path, start, phase, beat_id, check_id, check_name, *pairs = sys.argv[1:]",
            "event = {",
            "    'time': round(time.time() - float(start), 3),",
            "    'phase': phase,",
            "}",
            "if beat_id:",
            "    event['beat'] = beat_id",
            "if check_id:",
            "    event['check_id'] = check_id",
            "if check_name:",
            "    event['check'] = check_name",
            "if len(pairs) % 2:",
            "    raise SystemExit('timeline key/value arguments must be paired')",
            "for index in range(0, len(pairs), 2):",
            "    key = pairs[index]",
            "    value = pairs[index + 1]",
            "    if value.startswith(('{', '[', '\"')) or re.fullmatch(r'-?\\d+(\\.\\d+)?', value):",
            "        try:",
            "            event[key] = json.loads(value)",
            "        except json.JSONDecodeError:",
            "            event[key] = value",
            "    else:",
            "        event[key] = value",
            "line = json.dumps(event, sort_keys=True) + '\\n'",
            "if timeline_path:",
            "    with open(timeline_path, 'a', encoding='utf-8') as handle:",
            "        handle.write(line)",
            "if progress_pipe_path:",
            "    try:",
            "        fd = os.open(progress_pipe_path, os.O_WRONLY | os.O_NONBLOCK)",
            "    except OSError:",
            "        pass",
            "    else:",
            "        try:",
            "            os.write(fd, line.encode('utf-8'))",
            "        finally:",
            "            os.close(fd)",
            "PY",
            "}",
            "",
            "print_caption() {",
            '  if [[ "$recording_color" == 1 ]]; then',
            "    printf '\\n\\033[36;1m# %s\\033[0m\\n\\n' \"$1\"",
            "  else",
            "    printf '\\n# %s\\n\\n' \"$1\"",
            "  fi",
            "}",
            "",
            "type_text() {",
            '  local text="$1"',
            '  if [[ "$recording_typing" != 1 ]]; then',
            '    printf "%s" "$text"',
            "    return",
            "  fi",
            '  "$recording_python" - "$text" <<\'PY\'',
            "import hashlib",
            "import os",
            "import random",
            "import sys",
            "import time",
            "",
            "text = sys.argv[1]",
            "minimum = float(os.environ['recording_typing_min_delay'])",
            "maximum = float(os.environ['recording_typing_max_delay'])",
            "space = float(os.environ['recording_typing_space_delay'])",
            "punctuation = float(os.environ['recording_typing_punctuation_delay'])",
            "newline = float(os.environ['recording_typing_newline_delay'])",
            "seed = int(os.environ['recording_typing_seed'])",
            "digest = hashlib.sha256(text.encode('utf-8')).digest()",
            "text_seed = int.from_bytes(digest[:8], 'big')",
            "rng = random.Random(seed ^ text_seed)",
            "",
            "for index, char in enumerate(text):",
            "    sys.stdout.write(char)",
            "    sys.stdout.flush()",
            "    if index == len(text) - 1:",
            "        continue",
            "    delay = rng.uniform(minimum, maximum)",
            "    if char == '\\n':",
            "        delay += newline + rng.uniform(0.0, newline / 2)",
            "    elif char.isspace():",
            "        delay += rng.uniform(0.0, space)",
            "    elif char in '|;&':",
            "        delay += punctuation + rng.uniform(0.0, punctuation)",
            "    elif char == '\\\\':",
            "        delay += newline / 2",
            "    elif char in ',.:=/\"\\'{}[]()':",
            "        delay += rng.uniform(0.0, punctuation)",
            "    if char in ' -_/' and rng.random() < 0.08:",
            "        delay += rng.uniform(0.04, 0.12)",
            "    time.sleep(delay)",
            "PY",
            "}",
            "",
            "recording_prompt_text() {",
            '  local venv_name=""',
            '  if [[ -n "${VIRTUAL_ENV:-}" ]]; then',
            '    venv_name="$(basename "$VIRTUAL_ENV")"',
            "  fi",
            '  if [[ -n "$venv_name" ]]; then',
            '    printf "(%s) $" "$venv_name"',
            "  else",
            "    printf '$'",
            "  fi",
            "}",
            "",
            "print_idle_prompt() {",
            "  local prompt",
            '  prompt="$(recording_prompt_text)"',
            '  if [[ "$recording_color" == 1 ]]; then',
            '    printf "\\033[32;1m%s\\033[0m " "$prompt"',
            "  else",
            '    printf "%s " "$prompt"',
            "  fi",
            "}",
            "",
            "print_command_line() {",
            '  local line="$1"',
            '  local continuation="$2"',
            "  local prompt",
            '  prompt="$(recording_prompt_text)"',
            '  if [[ "$recording_color" == 1 ]]; then',
            '    if [[ "$continuation" == 1 ]]; then',
            "      printf '  \\033[1m'",
            "    else",
            '      printf "\\033[32;1m%s\\033[0m \\033[1m" "$prompt"',
            "    fi",
            '    type_text "$line"',
            "    printf '\\033[0m\\n'",
            "  else",
            '    if [[ "$continuation" == 1 ]]; then',
            "      printf '  '",
            "    else",
            '      printf "%s " "$prompt"',
            "    fi",
            '    type_text "$line"',
            "    printf '\\n'",
            "  fi",
            "}",
            "",
            "print_command() {",
            '  local command="$1"',
            "  local line",
            "  local continuation=0",
            '  while IFS= read -r line || [[ -n "$line" ]]; do',
            '    if [[ -z "$line" ]]; then',
            "      printf '\\n'",
            "      continuation=0",
            "      continue",
            "    fi",
            '    if [[ "$continuation" == 1 || "$line" =~ ^[[:space:]] ]]; then',
            '      print_command_line "$line" 1',
            "    else",
            '      print_command_line "$line" 0',
            "    fi",
            '    if [[ "$line" == *\\\\ ]]; then',
            "      continuation=1",
            "    else",
            "      continuation=0",
            "    fi",
            '  done <<< "$command"',
            "}",
            "",
            "split_commands() {",
            '  local text="$1"',
            '  local target_name="$2"',
            "  local line",
            "  local chunk=''",
            '  local -n target="$target_name"',
            "  target=()",
            '  while IFS= read -r line || [[ -n "$line" ]]; do',
            '    if [[ -z "$line" && -z "$chunk" ]]; then',
            "      continue",
            "    fi",
            '    if [[ -n "$chunk" ]]; then',
            "      chunk+=$'\\n'",
            "    fi",
            '    chunk+="$line"',
            '    if [[ "$line" == *\\\\ ]]; then',
            "      continue",
            "    fi",
            '    target+=("$chunk")',
            "    chunk=''",
            '  done <<< "$text"',
            '  if [[ -n "$chunk" ]]; then',
            '    target+=("$chunk")',
            "  fi",
            "}",
            "",
            "load_progress_labels() {",
            '  local labels_json="$1"',
            '  local target_name="$2"',
            '  local -n target="$target_name"',
            "  target=()",
            '  [[ -n "$labels_json" ]] || return',
            "  while IFS= read -r label; do",
            '    target+=("$label")',
            '  done < <("$recording_python" - "$labels_json" <<\'PY\'',
            "import json",
            "import sys",
            "",
            "for value in json.loads(sys.argv[1]):",
            "    print(value)",
            "PY",
            "  )",
            "}",
            "",
            "command_json_count() {",
            '  local commands_json="$1"',
            '  "$recording_python" - "$commands_json" <<\'PY\'',
            "import json",
            "import sys",
            "",
            "print(len(json.loads(sys.argv[1])))",
            "PY",
            "}",
            "",
            "command_json_field() {",
            '  local commands_json="$1"',
            '  local command_index="$2"',
            '  local field="$3"',
            '  "$recording_python" - "$commands_json" "$command_index" "$field" <<\'PY\'',
            "import json",
            "import sys",
            "",
            "commands = json.loads(sys.argv[1])",
            "index = int(sys.argv[2])",
            "field = sys.argv[3]",
            "value = commands[index]",
            "for part in field.split('.'):",
            "    value = value.get(part, '') if isinstance(value, dict) else ''",
            "if isinstance(value, bool):",
            "    print('true' if value else 'false')",
            "elif value is None:",
            "    print('')",
            "else:",
            "    print(str(value), end='')",
            "PY",
            "}",
            "",
            "run_visible_command_chunk() {",
            '  local action_id="$1"',
            '  local marker="$2"',
            '  local command_chunk="$3"',
            '  local chunk_id="$4"',
            '  local output_mode="${5:-real}"',
            '  local fake_output="${6:-}"',
            '  local timing="${7:-presentation}"',
            '  if [[ "$output_mode" == suppress || "$output_mode" == fake ]]; then',
            '    eval "$command_chunk" >>"$recording_stdout_path" 2>>"$recording_stderr_path" </dev/null',
            "    local status=$?",
            '    if [[ "$output_mode" == fake && -n "$fake_output" ]]; then',
            '      printf "%s" "$fake_output"',
            '      [[ "$fake_output" == *$\'\\n\' ]] || printf "\\n"',
            "    fi",
            '    return "$status"',
            "  fi",
            '  local stdout_pipe="$recording_tmp/${action_id}.${chunk_id}.stdout.pipe"',
            '  local stderr_pipe="$recording_tmp/${action_id}.${chunk_id}.stderr.pipe"',
            '  rm -f "$stdout_pipe" "$stderr_pipe"',
            '  mkfifo "$stdout_pipe" "$stderr_pipe"',
            '  "$recording_python" - "$stdout_pipe" "$stderr_pipe" "$recording_stdout_path" "$recording_stderr_path" "$timing" <<\'PY\' &',
            "import os",
            "import re",
            "import sys",
            "import threading",
            "",
            "stdout_pipe, stderr_pipe, stdout_path, stderr_path, timing = sys.argv[1:]",
            "skip_patterns = (",
            "    re.compile(rb'^Installing collected packages:'),",
            "    re.compile(rb'^Successfully installed '),",
            ")",
            "",
            "def display_line(line):",
            "    clean = re.sub(rb'\\x1b\\[[0-9;]*m', b'', line)",
            "    return not any(pattern.match(clean) for pattern in skip_patterns)",
            "",
            "def pump_stream(pipe_path, log_path, display):",
            "    fd = os.open(pipe_path, os.O_RDONLY)",
            "    try:",
            "        with open(log_path, 'ab', buffering=0) as output:",
            "            while True:",
            "                chunk = os.read(fd, 4096)",
            "                if not chunk:",
            "                    break",
            "                output.write(chunk)",
            "                display.buffer.write(chunk)",
            "                display.buffer.flush()",
            "    finally:",
            "        os.close(fd)",
            "",
            "def pump_lines(pipe_path, log_path, display):",
            "    with open(pipe_path, 'rb') as pipe, open(log_path, 'ab') as output:",
            "        for line in iter(pipe.readline, b''):",
            "            output.write(line)",
            "            output.flush()",
            "            if display_line(line):",
            "                display.buffer.write(line)",
            "                display.buffer.flush()",
            "",
            "pump = pump_stream if timing == 'realtime' else pump_lines",
            "threads = [",
            "    threading.Thread(target=pump, args=(stdout_pipe, stdout_path, sys.stdout)),",
            "    threading.Thread(target=pump, args=(stderr_pipe, stderr_path, sys.stderr)),",
            "]",
            "for thread in threads:",
            "    thread.start()",
            "for thread in threads:",
            "    thread.join()",
            "PY",
            "  local filter_pid=$!",
            '  eval "$command_chunk" >"$stdout_pipe" 2>"$stderr_pipe" </dev/null',
            "  local status=$?",
            '  wait "$filter_pid" 2>/dev/null || true',
            '  rm -f "$stdout_pipe" "$stderr_pipe"',
            '  return "$status"',
            "}",
            "",
            "free_port() {",
            "  \"$recording_python\" - <<'PY'",
            "import socket",
            "with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:",
            "    sock.bind(('127.0.0.1', 0))",
            "    print(sock.getsockname()[1])",
            "PY",
            "}",
            "",
            "expand_path() {",
            '  local raw="$1"',
            '  eval "printf \'%s\' \\"$raw\\""',
            "}",
            "",
            "output_contains_text() {",
            '  local output_path="$1"',
            '  local expected="$2"',
            '  grep -F -- "$expected" "$output_path" >/dev/null && return 0',
            '  "$recording_python" - "$output_path" "$expected" <<\'PY\'',
            "import re",
            "import sys",
            "from pathlib import Path",
            "",
            "path, expected = sys.argv[1:]",
            "text = Path(path).read_text(encoding='utf-8', errors='replace')",
            "plain = re.sub(r'\\x1b\\[[0-?]*[ -/]*[@-~]', '', text)",
            "raise SystemExit(0 if expected in plain else 1)",
            "PY",
            "}",
            "",
            "output_contains_text_from_offsets() {",
            '  local stdout_path="$1"',
            '  local stderr_path="$2"',
            '  local stdout_offset="$3"',
            '  local stderr_offset="$4"',
            '  local expected="$5"',
            '  "$recording_python" - "$stdout_path" "$stderr_path" "$stdout_offset" "$stderr_offset" "$expected" <<\'PY\'',
            "import re",
            "import sys",
            "from pathlib import Path",
            "",
            "stdout_path, stderr_path, stdout_offset, stderr_offset, expected = sys.argv[1:]",
            "",
            "def segment(path, offset):",
            "    try:",
            "        with Path(path).open('rb') as handle:",
            "            handle.seek(int(offset))",
            "            data = handle.read()",
            "    except OSError:",
            "        return ''",
            "    text = data.decode('utf-8', errors='replace')",
            "    return re.sub(r'\\x1b\\[[0-?]*[ -/]*[@-~]', '', text)",
            "",
            "plain = segment(stdout_path, stdout_offset) + segment(stderr_path, stderr_offset)",
            "raise SystemExit(0 if expected in plain else 1)",
            "PY",
            "}",
            "",
            "output_matches_regex() {",
            '  local output_path="$1"',
            '  local expected="$2"',
            '  grep -E -- "$expected" "$output_path" >/dev/null && return 0',
            '  "$recording_python" - "$output_path" "$expected" <<\'PY\'',
            "import re",
            "import sys",
            "from pathlib import Path",
            "",
            "path, expected = sys.argv[1:]",
            "text = Path(path).read_text(encoding='utf-8', errors='replace')",
            "plain = re.sub(r'\\x1b\\[[0-?]*[ -/]*[@-~]', '', text)",
            "raise SystemExit(0 if re.search(expected, plain, re.MULTILINE) else 1)",
            "PY",
            "}",
            "",
            "output_matches_regex_from_offsets() {",
            '  local stdout_path="$1"',
            '  local stderr_path="$2"',
            '  local stdout_offset="$3"',
            '  local stderr_offset="$4"',
            '  local expected="$5"',
            '  "$recording_python" - "$stdout_path" "$stderr_path" "$stdout_offset" "$stderr_offset" "$expected" <<\'PY\'',
            "import re",
            "import sys",
            "from pathlib import Path",
            "",
            "stdout_path, stderr_path, stdout_offset, stderr_offset, expected = sys.argv[1:]",
            "",
            "def segment(path, offset):",
            "    try:",
            "        with Path(path).open('rb') as handle:",
            "            handle.seek(int(offset))",
            "            data = handle.read()",
            "    except OSError:",
            "        return ''",
            "    text = data.decode('utf-8', errors='replace')",
            "    return re.sub(r'\\x1b\\[[0-?]*[ -/]*[@-~]', '', text)",
            "",
            "plain = segment(stdout_path, stdout_offset) + segment(stderr_path, stderr_offset)",
            "raise SystemExit(0 if re.search(expected, plain, re.MULTILINE) else 1)",
            "PY",
            "}",
            "",
            "run_action() {",
            '  local beat_id="$1"',
            '  local action_id="$2"',
            '  local display_command="$3"',
            '  local command="$4"',
            '  local progress_json="$5"',
            '  local after_anchor="$6"',
            "  shift 6",
            '  local marker="::: action ${action_id}"',
            '  printf "%s start beat=%s\\n" "$marker" "$beat_id" >>"$recording_progress_path"',
            "  local stdout_start",
            "  local stderr_start",
            '  stdout_start="$(wc -c <"$recording_stdout_path")"',
            '  stderr_start="$(wc -c <"$recording_stderr_path")"',
            "  local display_chunks=()",
            "  local command_chunks=()",
            "  local progress_labels=()",
            '  split_commands "$display_command" display_chunks',
            '  split_commands "$command" command_chunks',
            '  load_progress_labels "$progress_json" progress_labels',
            '  timeline_event action_start "$beat_id" "" "" action_id "$action_id"',
            "  set +e",
            "  local status=0",
            "  if [[ ${#display_chunks[@]} -gt 0 && ${#display_chunks[@]} -eq ${#command_chunks[@]} ]]; then",
            "    local index",
            '    for index in "${!command_chunks[@]}"; do',
            '      timeline_event command_prompt_start "$beat_id" "" "" action_id "$action_id" chunk_index "$index" command "${display_chunks[$index]}" prompt "$(recording_prompt_text)" color "$recording_color" after "$after_anchor"',
            '      timeline_event command_prompt_end "$beat_id" "" "" action_id "$action_id" chunk_index "$index" command "${display_chunks[$index]}" prompt "$(recording_prompt_text)" color "$recording_color" after "$after_anchor"',
            '      timeline_event command_run_start "$beat_id" "" "" action_id "$action_id" chunk_index "$index" command "${command_chunks[$index]}" display "${display_chunks[$index]}" progress "${progress_labels[$index]:-}" after "$after_anchor"',
            '      run_visible_command_chunk "$action_id" "$marker" "${command_chunks[$index]}" "$index"',
            "      status=$?",
            '      timeline_event command_run_end "$beat_id" "" "" action_id "$action_id" chunk_index "$index" status "$status" after "$after_anchor"',
            '      [[ "$status" -eq 0 ]] || break',
            "    done",
            "  else",
            '    timeline_event command_prompt_start "$beat_id" "" "" action_id "$action_id" chunk_index fallback command "$display_command" prompt "$(recording_prompt_text)" color "$recording_color" after "$after_anchor"',
            '    timeline_event command_prompt_end "$beat_id" "" "" action_id "$action_id" chunk_index fallback command "$display_command" prompt "$(recording_prompt_text)" color "$recording_color" after "$after_anchor"',
            '    timeline_event command_run_start "$beat_id" "" "" action_id "$action_id" chunk_index fallback command "$command" display "$display_command" progress "${progress_labels[0]:-}" after "$after_anchor"',
            '    run_visible_command_chunk "$action_id" "$marker" "$command" fallback',
            "    status=$?",
            '    timeline_event command_run_end "$beat_id" "" "" action_id "$action_id" chunk_index fallback status "$status" after "$after_anchor"',
            "  fi",
            "  set -e",
            "  local expected_exit=0",
            '  local gate_args=("$@")',
            "  local gate",
            "  local value",
            "  local gate_index",
            "  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do",
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    [[ "$gate" == exit ]] && expected_exit="$value"',
            "  done",
            '  [[ "$status" -eq "$expected_exit" ]] || fail_gate "$action_id" "exited $status, expected $expected_exit"',
            "  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do",
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    case "$gate" in',
            "      exit)",
            "        ;;",
            "      contains)",
            '        output_contains_text_from_offsets "$recording_stdout_path" "$recording_stderr_path" "$stdout_start" "$stderr_start" "$value" || fail_gate "$action_id" "missing text: $value"',
            "        ;;",
            "      regex)",
            '        output_matches_regex_from_offsets "$recording_stdout_path" "$recording_stderr_path" "$stdout_start" "$stderr_start" "$value" || fail_gate "$action_id" "missing regex: $value"',
            "        ;;",
            "      file)",
            "        local expanded",
            '        expanded="$(expand_path "$value")"',
            '        [[ -e "$expanded" ]] || fail_gate "$action_id" "missing file: $expanded"',
            "        ;;",
            "      *)",
            '        fail_gate "$action_id" "unknown gate: $gate"',
            "        ;;",
            "    esac",
            "  done",
            '  printf "%s end status=%s\\n" "$marker" "$status" >>"$recording_progress_path"',
            '  timeline_event action_end "$beat_id" "" "" action_id "$action_id" status "$status"',
            "  printf '\\n'",
            "}",
            "",
            "run_action_commands() {",
            '  local beat_id="$1"',
            '  local action_id="$2"',
            '  local commands_json="$3"',
            '  local progress_json="$4"',
            "  shift 4",
            '  local marker="::: action ${action_id}"',
            '  printf "%s start beat=%s\\n" "$marker" "$beat_id" >>"$recording_progress_path"',
            "  local stdout_start",
            "  local stderr_start",
            '  stdout_start="$(wc -c <"$recording_stdout_path")"',
            '  stderr_start="$(wc -c <"$recording_stderr_path")"',
            "  local progress_labels=()",
            '  load_progress_labels "$progress_json" progress_labels',
            '  timeline_event action_start "$beat_id" "" "" action_id "$action_id"',
            "  set +e",
            "  local status=0",
            "  local command_count",
            '  command_count="$(command_json_count "$commands_json")"',
            "  local index",
            "  for ((index = 0; index < command_count; index += 1)); do",
            "    local display_command",
            "    local command",
            "    local follow_along",
            "    local show_prompt_after",
            "    local after_anchor",
            "    local command_id",
            "    local output_mode",
            "    local fake_output",
            "    local timing",
            "    local pre_command_pause",
            "    local pre_enter_pause",
            "    local post_enter_pause",
            "    local post_command_pause",
            '    display_command="$(command_json_field "$commands_json" "$index" display)"',
            '    command="$(command_json_field "$commands_json" "$index" run)"',
            '    command_id="$(command_json_field "$commands_json" "$index" id)"',
            '    after_anchor="$(command_json_field "$commands_json" "$index" after)"',
            '    follow_along="$(command_json_field "$commands_json" "$index" follow_along)"',
            '    show_prompt_after="$(command_json_field "$commands_json" "$index" show_prompt_after)"',
            '    output_mode="$(command_json_field "$commands_json" "$index" output.mode)"',
            '    fake_output="$(command_json_field "$commands_json" "$index" output.text)"',
            '    timing="$(command_json_field "$commands_json" "$index" timing)"',
            '    pre_command_pause="$(command_json_field "$commands_json" "$index" pre_command_pause)"',
            '    pre_enter_pause="$(command_json_field "$commands_json" "$index" pre_enter_pause)"',
            '    post_enter_pause="$(command_json_field "$commands_json" "$index" post_enter_pause)"',
            '    post_command_pause="$(command_json_field "$commands_json" "$index" post_command_pause)"',
            '    timeline_event command_prompt_start "$beat_id" "" "" action_id "$action_id" command_id "$command_id" chunk_index "$index" command "$display_command" prompt "$(recording_prompt_text)" color "$recording_color" follow_along "$follow_along" show_prompt_after "$show_prompt_after" after "$after_anchor" timing "$timing" pre_command_pause "$pre_command_pause" pre_enter_pause "$pre_enter_pause" post_enter_pause "$post_enter_pause" post_command_pause "$post_command_pause"',
            '    timeline_event command_prompt_end "$beat_id" "" "" action_id "$action_id" command_id "$command_id" chunk_index "$index" command "$display_command" prompt "$(recording_prompt_text)" color "$recording_color" follow_along "$follow_along" show_prompt_after "$show_prompt_after" after "$after_anchor" pre_command_pause "$pre_command_pause" pre_enter_pause "$pre_enter_pause" post_enter_pause "$post_enter_pause" post_command_pause "$post_command_pause"',
            '    timeline_event command_run_start "$beat_id" "" "" action_id "$action_id" command_id "$command_id" chunk_index "$index" command "$command" display "$display_command" progress "${progress_labels[$index]:-}" follow_along "$follow_along" output_mode "$output_mode" fake_output "$fake_output" after "$after_anchor" timing "$timing" pre_command_pause "$pre_command_pause" pre_enter_pause "$pre_enter_pause" post_enter_pause "$post_enter_pause" post_command_pause "$post_command_pause"',
            '    run_visible_command_chunk "$action_id" "$marker" "$command" "$index" "$output_mode" "$fake_output" "$timing"',
            "    status=$?",
            '    timeline_event command_run_end "$beat_id" "" "" action_id "$action_id" command_id "$command_id" chunk_index "$index" status "$status" follow_along "$follow_along" after "$after_anchor"',
            '    [[ "$status" -eq 0 ]] || break',
            "  done",
            "  set -e",
            "  local expected_exit=0",
            '  local gate_args=("$@")',
            "  local gate",
            "  local value",
            "  local gate_index",
            "  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do",
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    [[ "$gate" == exit ]] && expected_exit="$value"',
            "  done",
            '  [[ "$status" -eq "$expected_exit" ]] || fail_gate "$action_id" "exited $status, expected $expected_exit"',
            "  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do",
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    case "$gate" in',
            "      exit)",
            "        ;;",
            "      contains)",
            '        output_contains_text_from_offsets "$recording_stdout_path" "$recording_stderr_path" "$stdout_start" "$stderr_start" "$value" || fail_gate "$action_id" "missing text: $value"',
            "        ;;",
            "      regex)",
            '        output_matches_regex_from_offsets "$recording_stdout_path" "$recording_stderr_path" "$stdout_start" "$stderr_start" "$value" || fail_gate "$action_id" "missing regex: $value"',
            "        ;;",
            "      file)",
            "        local expanded",
            '        expanded="$(expand_path "$value")"',
            '        [[ -e "$expanded" ]] || fail_gate "$action_id" "missing file: $expanded"',
            "        ;;",
            "      *)",
            '        fail_gate "$action_id" "unknown gate: $gate"',
            "        ;;",
            "    esac",
            "  done",
            '  printf "%s end status=%s\\n" "$marker" "$status" >>"$recording_progress_path"',
            '  timeline_event action_end "$beat_id" "" "" action_id "$action_id" status "$status"',
            "  printf '\\n'",
            "}",
            "",
            "run_check() {",
            '  local beat_id="$1"',
            '  local check_id="$2"',
            '  local check_name="$3"',
            '  local command="$4"',
            "  shift 4",
            '  local marker="::: check ${check_id}"',
            '  timeline_event check_start "$beat_id" "$check_id" "$check_name"',
            '  printf "%s start beat=%s name=%s\\n" "$marker" "$beat_id" "$check_name" >>"$recording_progress_path"',
            "  local stdout_start",
            "  local stderr_start",
            '  stdout_start="$(wc -c <"$recording_stdout_path")"',
            '  stderr_start="$(wc -c <"$recording_stderr_path")"',
            "  set +e",
            '  if [[ "$beat_id" == "__setup__" ]]; then',
            '    eval "$command" >>"$recording_stdout_path" 2>>"$recording_stderr_path"',
            "  else",
            '    ( eval "$command" ) >>"$recording_stdout_path" 2>>"$recording_stderr_path"',
            "  fi",
            "  local status=$?",
            "  set -e",
            "  local expected_exit=0",
            '  local gate_args=("$@")',
            "  local gate",
            "  local value",
            "  local gate_index",
            "  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do",
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    [[ "$gate" == exit ]] && expected_exit="$value"',
            "  done",
            '  [[ "$status" -eq "$expected_exit" ]] || fail_check "$check_id" "$check_name" "exited $status, expected $expected_exit" "$recording_stdout_path" "$recording_stderr_path"',
            "  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do",
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    case "$gate" in',
            "      exit)",
            "        ;;",
            "      contains)",
            '        output_contains_text_from_offsets "$recording_stdout_path" "$recording_stderr_path" "$stdout_start" "$stderr_start" "$value" || fail_check "$check_id" "$check_name" "missing text: $value" "$recording_stdout_path" "$recording_stderr_path"',
            "        ;;",
            "      regex)",
            '        output_matches_regex_from_offsets "$recording_stdout_path" "$recording_stderr_path" "$stdout_start" "$stderr_start" "$value" || fail_check "$check_id" "$check_name" "missing regex: $value" "$recording_stdout_path" "$recording_stderr_path"',
            "        ;;",
            "      file)",
            "        local expanded",
            '        expanded="$(expand_path "$value")"',
            '        [[ -e "$expanded" ]] || fail_check "$check_id" "$check_name" "missing file: $expanded" "$recording_stdout_path" "$recording_stderr_path"',
            "        ;;",
            "      *)",
            '        fail_check "$check_id" "$check_name" "unknown gate: $gate" "$recording_stdout_path" "$recording_stderr_path"',
            "        ;;",
            "    esac",
            "  done",
            '  printf "%s end status=%s\\n" "$marker" "$status" >>"$recording_progress_path"',
            '  timeline_event check_end "$beat_id" "$check_id" "$check_name"',
            "}",
            "",
            "hold() {",
            '  local beat_id="$1"',
            '  local seconds="$2"',
            '  timeline_event hold_start "$beat_id" "" "" seconds "$seconds"',
            '  if [[ "$recording_baseline_compressed" != 1 ]]; then',
            '    sleep "$seconds"',
            "  fi",
            '  timeline_event hold_end "$beat_id" "" "" seconds "$seconds"',
            "}",
            "",
        ]
    )

    setup = as_list(spec.get("setup"), field="setup")
    for index, step in enumerate(setup, start=1):
        command = setup_command_text(step, index, spec=spec)
        check_name = step.get("name", f"setup step {index}")
        if not isinstance(check_name, str) or not check_name:
            raise RecordingError(f"setup.{index}.name must be a non-empty string")
        expect = as_mapping(step.get("expect"), field=f"setup.{index}.expect")
        gate_args = [shell_quote(value) for value in shell_expect_args(expect)]
        lines.append(
            "run_check "
            f"{shell_quote('__setup__')} "
            f"{shell_quote(f'setup_check_{index}')} "
            f"{shell_quote(check_name)} "
            f"{shell_quote(command)} " + " ".join(gate_args)
        )
    if setup:
        lines.append("")

    beats = as_list(spec.get("beats"), field="beats")
    total_beats = len(beats)
    for beat_index, beat in enumerate(beats, start=1):
        beat_id = require_string(beat, "id")
        safe_beat_id = re.sub(r"[^A-Za-z0-9_]", "_", beat_id)
        caption = beat.get("caption")
        lines.append(
            "log_stage_marker "
            f"{shell_quote('start')} "
            f"{shell_quote(beat_id)} "
            f"{shell_quote(beat_index)} "
            f"{shell_quote(total_beats)}"
        )
        lines.append(f"timeline_event beat_start {shell_quote(beat_id)} '' ''")
        if isinstance(caption, str) and caption:
            lines.append(
                f"timeline_event caption_start {shell_quote(beat_id)} '' '' "
                f"caption {shell_quote(caption)}"
            )
            lines.append(f"print_caption {shell_quote(caption)}")
            lines.append(
                f"timeline_event caption_end {shell_quote(beat_id)} '' '' "
                f"caption {shell_quote(caption)}"
            )
        actions = as_list(beat.get("actions"), field=f"beats.{beat_id}.actions")
        for index, action in enumerate(actions, start=1):
            command_entries = action_command_entries(
                action,
                index,
                field=f"beats.{beat_id}.actions",
                spec=spec,
            )
            expect = as_mapping(
                action.get("expect"), field=f"beats.{beat_id}.actions.{index}.expect"
            )
            gate_args = [shell_quote(value) for value in shell_expect_args(expect)]
            progress_labels = action_progress_labels(
                action, field=f"beats.{beat_id}.actions.{index}"
            )
            progress_json = json.dumps(progress_labels)
            action_id = f"{safe_beat_id}_{index}"
            if command_entries is not None:
                lines.append(
                    "run_action_commands "
                    f"{shell_quote(beat_id)} "
                    f"{shell_quote(action_id)} "
                    f"{shell_quote(json.dumps(command_entries))} "
                    f"{shell_quote(progress_json)} " + " ".join(gate_args)
                )
            else:
                command = step_command_text(
                    action,
                    index,
                    field=f"beats.{beat_id}.actions",
                    spec=spec,
                )
                display_command = action.get("display", command)
                if not isinstance(display_command, str) or not display_command:
                    raise RecordingError(
                        f"beats.{beat_id}.actions.{index}.display must be a non-empty string"
                    )
                after_anchor = action.get("after", "")
                if after_anchor is None:
                    after_anchor = ""
                if not isinstance(after_anchor, str):
                    raise RecordingError(
                        f"beats.{beat_id}.actions.{index}.after must be a string"
                    )
                if after_anchor and not re.fullmatch(
                    r"@[A-Za-z][A-Za-z0-9_-]*@", after_anchor
                ):
                    raise RecordingError(
                        f"beats.{beat_id}.actions.{index}.after must use @anchor@ syntax"
                    )
                lines.append(
                    "run_action "
                    f"{shell_quote(beat_id)} "
                    f"{shell_quote(action_id)} "
                    f"{shell_quote(display_command)} "
                    f"{shell_quote(command)} "
                    f"{shell_quote(progress_json)} "
                    f"{shell_quote(after_anchor)} " + " ".join(gate_args)
                )
        checks = as_list(beat.get("checks"), field=f"beats.{beat_id}.checks")
        for index, check in enumerate(checks, start=1):
            command = step_command_text(
                check,
                index,
                field=f"beats.{beat_id}.checks",
                spec=spec,
            )
            check_name = check.get("name", f"{beat_id} check {index}")
            if not isinstance(check_name, str) or not check_name:
                raise RecordingError(
                    f"beats.{beat_id}.checks.{index}.name must be a non-empty string"
                )
            expect = as_mapping(
                check.get("expect"), field=f"beats.{beat_id}.checks.{index}.expect"
            )
            gate_args = [shell_quote(value) for value in shell_expect_args(expect)]
            check_id = f"{safe_beat_id}_check_{index}"
            lines.append(
                "run_check "
                f"{shell_quote(beat_id)} "
                f"{shell_quote(check_id)} "
                f"{shell_quote(check_name)} "
                f"{shell_quote(command)} " + " ".join(gate_args)
            )
        viewer_hold = beat.get("viewer_hold")
        if viewer_hold is not None:
            if not isinstance(viewer_hold, (int, float)):
                raise RecordingError(f"beat {beat_id} viewer_hold must be numeric")
            lines.append(f"hold {shell_quote(beat_id)} {shell_quote(viewer_hold)}")
        lines.append(f"timeline_event beat_end {shell_quote(beat_id)} '' ''")
        lines.append(
            "log_stage_marker "
            f"{shell_quote('end')} "
            f"{shell_quote(beat_id)} "
            f"{shell_quote(beat_index)} "
            f"{shell_quote(total_beats)}"
        )
        lines.append("")
    return "\n".join(lines)


def record(
    spec: dict[str, Any],
    *,
    dry_run: bool,
    check_only: bool,
    output_override: str | None,
    headed: bool = False,
    verbose: bool = False,
) -> int:
    use_color = host_color_enabled(sys.stderr)
    validate_manifest(spec)
    check_required_commands(spec)
    asciinema_command_path = asciinema_command(spec)
    asciinema_version = check_asciinema(spec)
    session_overrides = session_overrides_from_spec(spec)
    if has_recording_config(spec):
        validate_session_overrides(session_overrides)
    script_text = render_session_script(spec)
    if dry_run:
        print(script_text)
        return 0
    if check_only:
        print(
            color_text(
                f"ok: {spec['id']} ({asciinema_version})",
                ANSI_GREEN_BOLD,
                enabled=use_color,
            )
        )
        return 0

    outputs = as_mapping(spec.get("outputs"), field="outputs")
    cast_path = relative_path(output_override or require_string(outputs, "cast"))
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path = timeline_path_for_cast(cast_path)
    failure_path = failure_path_for_cast(cast_path)
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    if failure_path.exists():
        failure_path.unlink()
    staged_cast_path = staged_cast_path_for(cast_path)
    staged_timeline_path = timeline_path_for_cast(staged_cast_path)
    staged_failure_path = failure_path_for_cast(staged_cast_path)
    staged_progress_pipe_path = staged_timeline_path.with_suffix(".progress.pipe")
    staged_paths = [
        staged_cast_path,
        staged_timeline_path,
        staged_failure_path,
        staged_progress_pipe_path,
    ]

    capture = as_mapping(spec.get("capture"), field="capture")
    window_size = str(capture.get("window_size", "100x28"))
    idle_time_limit = capture.get("idle_time_limit")
    headless = bool(capture.get("headless", True)) and not headed

    session_python = Path(sys.executable)
    session_args = [
        shlex.quote(str(session_python)),
        "-m",
        "omegaflow_studio.record",
    ]
    session_args.extend(shlex.quote(override) for override in session_overrides)
    runner_command = " ".join(
        [
            "env",
            "OMEGAFLOW_STUDIO_TIMELINE=" + shlex.quote(str(staged_timeline_path)),
            f"{PROGRESS_PIPE_ENV}=" + shlex.quote(str(staged_progress_pipe_path)),
            "OMEGAFLOW_STUDIO_FAILURE=" + shlex.quote(str(staged_failure_path)),
            *session_args,
        ]
    )
    command = [
        asciinema_command_path,
        "record",
        "--quiet",
        "--overwrite",
        "--return",
        "--window-size",
        window_size,
        "--title",
        require_string(spec, "title"),
    ]
    if idle_time_limit is not None:
        command.extend(["--idle-time-limit", str(idle_time_limit)])
    if headless:
        command.append("--headless")
    command.extend(["--command", runner_command, str(staged_cast_path)])
    progress_printer = (
        ProgressEventPrinter(spec, color=use_color)
        if verbose
        else RecordProgressBar(spec, color=use_color)
    )
    progress = ProgressPipeReporter(
        staged_progress_pipe_path,
        enabled=headless,
        on_event=progress_printer.emit,
    )
    record_started = time.monotonic()
    try:
        progress.start()
        with RecordingSuspendGuard():
            result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    except KeyboardInterrupt as exc:
        removed = remove_recording_artifacts(staged_paths)
        raise RecordingInterrupted(
            format_interrupted_recording(cast_path, removed)
        ) from exc
    finally:
        progress.stop()
        if isinstance(progress_printer, RecordProgressBar):
            progress_printer.finish()
    if recording_was_interrupted(result.returncode):
        removed = remove_recording_artifacts(staged_paths)
        raise RecordingInterrupted(format_interrupted_recording(cast_path, removed))
    if result.returncode != 0:
        try:
            preserve_failed_run_artifacts(
                spec,
                cast_path=staged_cast_path,
                timeline_path=staged_timeline_path,
            )
            raise RecordingError(
                format_recording_failure(
                    returncode=result.returncode,
                    command=command,
                    cast_path=cast_path,
                    timeline_path=staged_timeline_path,
                    failure_path=staged_failure_path,
                    color=use_color,
                )
            )
        finally:
            remove_recording_artifacts(staged_paths)
    try:
        intervals = check_intervals_from_timeline(
            read_timeline_events(staged_timeline_path)
        )
        strip_cast_intervals(staged_cast_path, intervals)
        normalize_cast_header(staged_cast_path, spec)
        preserve_successful_run_artifacts(
            spec,
            cast_path=staged_cast_path,
            timeline_path=staged_timeline_path,
        )
        staged_timeline_path.replace(timeline_path)
        staged_cast_path.replace(cast_path)
    except KeyboardInterrupt as exc:
        removed = remove_recording_artifacts(staged_paths)
        raise RecordingInterrupted(
            format_interrupted_recording(cast_path, removed)
        ) from exc
    except Exception:
        preserve_failed_run_artifacts(
            spec,
            cast_path=staged_cast_path,
            timeline_path=staged_timeline_path,
        )
        remove_recording_artifacts(staged_paths)
        raise
    print(
        status_line(
            "pass",
            (
                f"recorded baseline cast: {display_path(cast_path)} "
                f"({format_elapsed(time.monotonic() - record_started)})"
            ),
            color=ANSI_GREEN_BOLD,
            enabled=use_color,
        )
    )
    postmortem_path = run_artifact_dir(spec) / "enter"
    if verbose and postmortem_path.exists():
        run_id = postmortem_path.parent.name
        print(
            status_line(
                "info",
                f"run id: {run_id}",
                color=ANSI_CYAN_BOLD,
                enabled=use_color,
            )
        )
        print(
            status_line(
                "next",
                "inspect run",
                color=ANSI_CYAN_BOLD,
                enabled=use_color,
            )
        )
        print(
            "  "
            + recording_tool_command(
                recording_id=require_string(spec, "id"),
                action="inspect",
                run_id=run_id,
            )
        )
        print(
            status_line(
                "next",
                "play run",
                color=ANSI_CYAN_BOLD,
                enabled=use_color,
            )
        )
        print("  " + recording_tool_command(action="play", run_id=run_id))
    return 0


def recording_data_dir(spec: dict[str, Any] | None) -> Path:
    if spec is not None:
        config = spec.get("_studio_config")
        if isinstance(config, dict):
            return studio_data_dir_from_config(config)
    return PROJECT_DATA_DIR


def recording_runs_dir(spec: dict[str, Any]) -> Path:
    recording_id = require_string(spec, "id")
    hydra_output_dir = relative_path(require_string(spec, "_hydra_output_dir"))
    if hydra_output_dir.parent.name == recording_id and (
        hydra_output_dir.parent.parent.name == "runs"
    ):
        return hydra_output_dir.parent
    return recording_data_dir(spec) / "runs" / recording_id


def all_recording_runs_root(data_dir: Path | None = None) -> Path:
    return (data_dir or PROJECT_DATA_DIR) / "runs"


def validate_run_id(run_id: str) -> None:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise RecordingError("run_id must be a run id, not a path")


def run_dir_for_id(spec: dict[str, Any], run_id: str) -> Path:
    validate_run_id(run_id)
    run_dir = recording_runs_dir(spec) / run_id
    if not run_dir.is_dir():
        raise RecordingError(f"recording run not found: {run_dir}")
    return run_dir


def find_run_dir_by_id(run_id: str, *, data_dir: Path | None = None) -> Path:
    validate_run_id(run_id)
    runs_root = all_recording_runs_root(data_dir)
    matches = sorted(
        path
        for path in runs_root.rglob(run_id)
        if path.is_dir() and path.parent != runs_root
    )
    if not matches:
        raise RecordingError(f"recording run not found for run_id: {run_id}")
    if len(matches) > 1:
        candidates = ", ".join(
            path.parent.relative_to(runs_root).as_posix() for path in matches
        )
        raise RecordingError(
            f"run_id {run_id} is ambiguous across recordings: {candidates}; "
            "add recording=<id>"
        )
    return matches[0]


def run_dir_has_artifact(run_dir: Path, artifact: str) -> bool:
    if artifact == "inspect":
        return (run_dir / "enter").exists()
    if artifact == "output":
        return (run_dir / "failure.json").exists()
    if artifact == "play":
        return (run_dir / "recording.cast").exists() or (
            run_dir / "failed.cast"
        ).exists()
    if artifact == "success":
        return (run_dir / "recording.cast").exists() and not (
            run_dir / "failure.json"
        ).exists()
    if artifact == "preserved":
        return (
            (run_dir / "enter").exists()
            or (run_dir / "failure.json").exists()
            or (run_dir / "recording.cast").exists()
            or (run_dir / "failed.cast").exists()
        )
    raise RecordingError(f"unknown run artifact filter: {artifact}")


def find_latest_run_dir(
    recording_id: str | None = None,
    *,
    artifact: str = "preserved",
    data_dir: Path | None = None,
) -> Path:
    runs_root = all_recording_runs_root(data_dir)
    if not runs_root.is_dir():
        raise RecordingError(f"no preserved runs found under: {runs_root}")
    if recording_id is None:
        run_dirs = sorted(path for path in runs_root.rglob("*") if path.is_dir())
    else:
        recording_dir = runs_root / recording_id
        run_dirs = (
            sorted(path for path in recording_dir.iterdir() if path.is_dir())
            if recording_dir.is_dir()
            else []
        )

    candidates: list[tuple[str, int, str, Path]] = []
    for run_dir in run_dirs:
        if not run_dir_has_artifact(run_dir, artifact):
            continue
        recording_name = run_dir.parent.relative_to(runs_root).as_posix()
        candidates.append(
            (
                run_dir.name,
                run_dir.stat().st_mtime_ns,
                recording_name,
                run_dir,
            )
        )
    if not candidates:
        scope = f" for recording: {recording_id}" if recording_id else ""
        raise RecordingError(
            f"no preserved runs with {artifact} artifacts found{scope} "
            f"under: {runs_root}"
        )
    return max(candidates)[3]


def run_dir_for_optional_id(
    spec: dict[str, Any] | None,
    run_id: str | None,
    *,
    artifact: str = "preserved",
    data_dir: Path | None = None,
) -> Path:
    resolved_data_dir = data_dir or recording_data_dir(spec)
    if run_id:
        return (
            run_dir_for_id(spec, run_id)
            if spec is not None
            else find_run_dir_by_id(run_id, data_dir=resolved_data_dir)
        )
    recording_id = require_string(spec, "id") if spec is not None else None
    return find_latest_run_dir(
        recording_id,
        artifact=artifact,
        data_dir=resolved_data_dir,
    )


def recording_was_explicit(spec: dict[str, Any]) -> bool:
    overrides = spec.get("_overrides", [])
    if not isinstance(overrides, list):
        return False
    return any(str(override).startswith("recording=") for override in overrides)


def run_cast_path(run_dir: Path) -> Path | None:
    for name in ["recording.cast", "failed.cast"]:
        path = run_dir / name
        if path.exists():
            return path
    return None


def cast_duration_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    total = 0.0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(event, list)
            or not event
            or not isinstance(event[0], (int, float))
        ):
            return None
        total += float(event[0])
    return total


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    rounded = int(round(seconds))
    minutes, remainder = divmod(rounded, 60)
    return f"{minutes}:{remainder:02d}"


def format_elapsed(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minute_remainder = divmod(minutes, 60)
    return f"{int(hours)}h {int(minute_remainder)}m {remainder:.1f}s"


def parse_run_id_timestamp(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, RUN_ID_DATETIME_FORMAT)
    except ValueError:
        return None


def age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds()))


def format_age(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def parse_runs_since(value: object) -> timedelta | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecordingError("runs_since must be a duration string or null")
    text = value.strip().lower()
    if text in {"", "all", "none", "null"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", text)
    if match is None:
        raise RecordingError("runs_since must look like 30m, 2h, 1d, or be null/all")
    amount = float(match.group(1))
    unit = match.group(2)
    return timedelta(seconds=amount * RUN_SINCE_UNITS[unit])


def parse_runs_limit(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RecordingError("runs_limit must be a positive integer or null")
    return value


def failure_reason(report: dict[str, Any] | None) -> str:
    if report is None:
        return "failed cast preserved"
    message = report.get("message")
    if not isinstance(message, str) or not message:
        message = "failed"
    step = report.get("step_name") or report.get("step_id")
    if isinstance(step, str) and step and step != message:
        return f"{step}: {message}"
    return message


def run_job_from_dir(
    recording_id: str, run_dir: Path, *, now: datetime | None = None
) -> dict[str, Any] | None:
    if now is None:
        now = datetime.now()
    timestamp = parse_run_id_timestamp(run_dir.name)
    age_value = age_seconds(timestamp, now)
    success_cast = run_dir / "recording.cast"
    failed_cast = run_dir / "failed.cast"
    failure = read_failure_report(run_dir / "failure.json")
    if success_cast.exists() and failure is None:
        length_seconds = cast_duration_seconds(success_cast)
        return {
            "job_id": run_dir.name,
            "age": format_age(age_value),
            "age_seconds": age_value,
            "type": recording_id,
            "result": "success",
            "length": format_duration(length_seconds),
            "length_seconds": length_seconds,
            "reason": None,
        }
    if failure is not None or failed_cast.exists():
        return {
            "job_id": run_dir.name,
            "age": format_age(age_value),
            "age_seconds": age_value,
            "type": recording_id,
            "result": "failed",
            "length": None,
            "length_seconds": None,
            "reason": failure_reason(failure),
        }
    return None


def collect_run_jobs(
    recording_id: str | None = None,
    *,
    since: timedelta | None = None,
    limit: int | None = 10,
    now: datetime | None = None,
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    runs_root = all_recording_runs_root(data_dir)
    if not runs_root.is_dir():
        return []
    if now is None:
        now = datetime.now()
    cutoff = now - since if since is not None else None
    if recording_id is None:
        run_dirs = sorted(path for path in runs_root.rglob("*") if path.is_dir())
    else:
        recording_dir = runs_root / recording_id
        run_dirs = (
            sorted(path for path in recording_dir.iterdir() if path.is_dir())
            if recording_dir.is_dir()
            else []
        )

    candidates: list[tuple[int, str, str, Path]] = []
    for run_dir in run_dirs:
        run_timestamp = parse_run_id_timestamp(run_dir.name)
        if cutoff is not None and (run_timestamp is None or run_timestamp < cutoff):
            continue
        recording_name = run_dir.parent.relative_to(runs_root).as_posix()
        candidates.append(
            (
                run_dir.stat().st_mtime_ns,
                run_dir.name,
                recording_name,
                run_dir,
            )
        )
    jobs: list[dict[str, Any]] = []
    for _mtime, _run_id, candidate_recording_id, run_dir in sorted(
        candidates,
        key=lambda item: (item[1], item[2]),
        reverse=True,
    ):
        job = run_job_from_dir(candidate_recording_id, run_dir, now=now)
        if job is not None:
            jobs.append(job)
            if limit is not None and len(jobs) >= limit:
                break
    return jobs


def format_run_jobs_table(jobs: list[dict[str, Any]]) -> str:
    columns = ["job_id", "age", "type", "result", "length", "reason"]
    rows = [
        {
            "job_id": str(job.get("job_id") or ""),
            "age": str(job.get("age") or "-"),
            "type": str(job.get("type") or ""),
            "result": str(job.get("result") or ""),
            "length": str(job.get("length") or "-"),
            "reason": str(job.get("reason") or "-"),
        }
        for job in jobs
    ]
    widths = {
        column: max(
            len(column),
            *(len(row[column]) for row in rows),
        )
        for column in columns
    }
    lines = [
        "  ".join(column.ljust(widths[column]) for column in columns),
        "  ".join("-" * widths[column] for column in columns),
    ]
    lines.extend(
        "  ".join(row[column].ljust(widths[column]) for column in columns)
        for row in rows
    )
    return "\n".join(lines)


def list_run_jobs(
    *,
    recording_id: str | None = None,
    output_format: str = "text",
    since: timedelta | None = None,
    limit: int | None = 10,
    now: datetime | None = None,
    data_dir: Path | None = None,
) -> int:
    jobs = collect_run_jobs(
        recording_id,
        since=since,
        limit=limit,
        now=now,
        data_dir=data_dir,
    )
    if output_format == "json":
        print(json.dumps(jobs, indent=2, sort_keys=True))
    else:
        print(format_run_jobs_table(jobs))
    return 0


def inspect_run(
    spec: dict[str, Any] | None,
    *,
    run_id: str | None,
    data_dir: Path | None = None,
) -> int:
    run_dir = run_dir_for_optional_id(
        spec,
        run_id,
        artifact="inspect",
        data_dir=data_dir,
    )
    entrypoint = run_dir / "enter"
    if not entrypoint.exists():
        raise RecordingError(f"postmortem entrypoint not found: {entrypoint}")
    refresh_postmortem_entrypoint(entrypoint)
    return subprocess.run([str(entrypoint)], cwd=REPO_ROOT, check=False).returncode


def failure_output_path(run_dir: Path) -> Path | None:
    report = read_failure_report(run_dir / "failure.json")
    if report is None:
        return None
    output_path = report.get("output_path")
    if not isinstance(output_path, str) or not output_path:
        return None
    path = Path(output_path)
    if not path.is_absolute():
        path = run_dir / path
    return path


def page_or_print(path: Path) -> int:
    if not path.exists():
        raise RecordingError(f"captured output file not found: {path}")
    if sys.stdout.isatty():
        pager = shlex.split(os.environ.get("PAGER", "less")) or ["less"]
        try:
            return subprocess.run([*pager, str(path)], check=False).returncode
        except OSError as exc:
            raise RecordingError(f"failed to run pager {pager[0]!r}") from exc
    sys.stdout.write(path.read_text(encoding="utf-8", errors="replace"))
    return 0


def output_run(
    spec: dict[str, Any] | None,
    *,
    run_id: str | None,
    data_dir: Path | None = None,
) -> int:
    run_dir = run_dir_for_optional_id(
        spec,
        run_id,
        artifact="output",
        data_dir=data_dir,
    )
    output_path = failure_output_path(run_dir)
    if output_path is None:
        raise RecordingError(f"no captured failure output found in run: {run_dir}")
    return page_or_print(output_path)


def play_recording(
    spec: dict[str, Any] | None,
    *,
    run_id: str | None,
    cast_override: str | None,
    data_dir: Path | None = None,
) -> int:
    if cast_override:
        cast_path = relative_path(cast_override)
    else:
        run_dir = run_dir_for_optional_id(
            spec,
            run_id,
            artifact="play",
            data_dir=data_dir,
        )
        cast_path = run_cast_path(run_dir)
        if cast_path is None:
            raise RecordingError(f"no preserved cast found in run: {run_dir}")
    if not cast_path.exists():
        raise RecordingError(f"cast not found: {cast_path}")
    asciinema_command_path = asciinema_command(spec)
    check_asciinema(spec)
    return subprocess.run(
        [asciinema_command_path, "play", str(cast_path)],
        cwd=REPO_ROOT,
        check=False,
    ).returncode


def spec_from_hydra_cfg(cfg: Any) -> dict[str, Any]:
    try:
        return load_recording_spec_from_hydra_cfg(cfg)
    except StudioConfigError as exc:
        raise RecordingError(str(exc)) from exc


def control_config_from_hydra_cfg(cfg: Any) -> dict[str, Any]:
    try:
        config = container_from_hydra_cfg(cfg)
    except StudioConfigError as exc:
        raise RecordingError(str(exc)) from exc
    return config


def tool_step(config: dict[str, Any], default: str) -> str:
    step = config.get("step")
    if isinstance(step, str) and step:
        return step
    action = config.get("action")
    if action in {None, "build"}:
        return default
    if isinstance(action, str) and action:
        return action
    return default


def run_tool_from_hydra_cfg(cfg: Any) -> int:
    config = control_config_from_hydra_cfg(cfg)
    data_dir = studio_data_dir_from_config(config)
    action = tool_step(config, "record")
    if action == "list":
        return list_recordings(config)
    if action == "runs":
        output_format = config.get("output_format", "text")
        if not isinstance(output_format, str):
            raise RecordingError("output_format must be a string")
        since = parse_runs_since(config.get("runs_since"))
        limit = parse_runs_limit(config.get("runs_limit"))
        requested_recording = config.get("recording")
        recording_id = None
        if isinstance(requested_recording, str) and requested_recording:
            spec = spec_from_hydra_cfg(cfg)
            recording_id = require_string(spec, "id")
        return list_run_jobs(
            recording_id=recording_id,
            output_format=output_format,
            since=since,
            limit=limit,
            data_dir=data_dir,
        )
    cast_override = config.get("cast")
    if cast_override is not None and not isinstance(cast_override, str):
        raise RecordingError("cast must be a string or null")
    run_id = config.get("run_id")
    if run_id is not None and not isinstance(run_id, str):
        raise RecordingError("run_id must be a string or null")
    if action == "play":
        spec = spec_from_hydra_cfg(cfg)
        if not cast_override and not recording_was_explicit(spec):
            spec = None
        return play_recording(
            spec,
            run_id=run_id,
            cast_override=cast_override,
            data_dir=data_dir,
        )
    spec = spec_from_hydra_cfg(cfg)
    if action == "inspect":
        if not recording_was_explicit(spec):
            spec = None
        return inspect_run(spec, run_id=run_id, data_dir=data_dir)
    if action == "output":
        if not recording_was_explicit(spec):
            spec = None
        return output_run(spec, run_id=run_id, data_dir=data_dir)
    if action == "session":
        validate_manifest(spec)
        check_required_commands(spec)
        script_text = render_session_script(spec)
        result = subprocess.run(
            ["bash"], input=script_text, cwd=REPO_ROOT, text=True, check=False
        )
        return result.returncode
    if action not in {"record", "check", "dry_run"}:
        raise RecordingError(f"unknown action: {action}")
    output = config.get("output")
    if output is not None and not isinstance(output, str):
        raise RecordingError("output must be a string or null")
    headed = config.get("headed", False)
    if not isinstance(headed, bool):
        raise RecordingError("headed must be a boolean")
    verbose = config.get("verbose", False)
    if not isinstance(verbose, bool):
        raise RecordingError("verbose must be a boolean")
    return record(
        spec,
        dry_run=action == "dry_run",
        check_only=action == "check",
        output_override=output,
        headed=headed,
        verbose=verbose,
    )


def list_recordings(config: dict[str, Any] | None = None) -> int:
    recording_dir = recording_script_dir_from_config(config)
    for recording_id in list_recording_ids(recording_dir):
        print(recording_id)
    return 0


@hydra.main(
    version_base=None,
    config_path=str(CONFIG_DIR),
    config_name=STUDIO_CONFIG_NAME,
)
def main(cfg: DictConfig) -> None:
    use_color = host_color_enabled(sys.stderr)
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except RecordingInterrupted as exc:
        print(
            color_text(f"interrupted: {exc}", ANSI_YELLOW_BOLD, enabled=use_color),
            file=sys.stderr,
        )
        raise SystemExit(130) from exc
    except RecordingError as exc:
        print(
            color_text("error:", ANSI_RED_BOLD, enabled=use_color) + f" {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError as exc:
        print(
            color_text(
                f"error: command failed with exit code {exc.returncode}: {exc.cmd}",
                ANSI_RED_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode) from exc
    except KeyboardInterrupt:
        print(
            color_text(
                "interrupted: recording cancelled by user",
                ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130)


if __name__ == "__main__":
    main()
