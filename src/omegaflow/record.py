#!/usr/bin/env python3
"""Validate recording inputs and inspect preserved OmegaFlow runs."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import hydra
from omegaconf import DictConfig

from .studio_config import (
    CONFIG_DIR,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    container_from_hydra_cfg,
    is_valid_recording_id,
    list_recording_ids,
    load_recording_spec,
    load_recording_spec_from_hydra_cfg,
    project_root,
    recording_script_dir_from_config,
    studio_data_dir_from_config,
)
from .terminal_style import (
    ANSI_RED_BOLD,
    ANSI_YELLOW_BOLD,
    color_text,
    color_enabled as terminal_color_enabled,
)


RUN_ID_DATETIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_SINCE_UNITS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
}


class RecordingError(RuntimeError):
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


def host_color_enabled(stream: Any = sys.stderr) -> bool:
    return terminal_color_enabled(stream)


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
    return project_root() / candidate


def run_file_path(run_file: str, spec: dict[str, Any] | None = None) -> Path:
    candidate = Path(run_file)
    if candidate.is_absolute():
        return candidate
    search_roots: list[Path] = []
    if spec is not None:
        script_dir = spec.get("_script_dir")
        if isinstance(script_dir, str) and script_dir:
            search_roots.append(relative_path(script_dir))
    search_roots.append(project_root())
    for root in search_roots:
        path = root / candidate
        if path.exists():
            return path
    return search_roots[0] / candidate


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(project_root()))
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
        if raw_output not in {"real", "suppress"}:
            raise RecordingError(f"{field}.output must be one of: real, suppress")
        mode = raw_output
        text = None
    elif isinstance(raw_output, dict):
        if set(raw_output) != {"replace"}:
            raise RecordingError(f"{field}.output mapping must contain only: replace")
        mode = "replace"
        text = raw_output.get("replace")
    else:
        raise RecordingError(f"{field}.output must be a string or mapping")
    if mode == "replace":
        if not isinstance(text, str):
            raise RecordingError(f"{field}.output.replace must be a string")
    return {"mode": mode, "replace": text or ""}


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
        browser_handoff = raw_command.get("browser_handoff", False)
        if not isinstance(browser_handoff, bool):
            raise RecordingError(
                f"{field}.{index}.commands.{command_index}.browser_handoff must be a boolean"
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
                "browser_handoff": browser_handoff,
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
    require_string(outputs, "asset_dir")
    capture = as_mapping(spec.get("capture"), field="capture")
    window_size = capture.get("window_size", "100x28")
    if not isinstance(window_size, str) or not re.fullmatch(r"\d+x\d+", window_size):
        raise RecordingError("capture.window_size must look like COLSxROWS")
    idle_time_limit = capture.get("idle_time_limit")
    if idle_time_limit is not None and (
        not isinstance(idle_time_limit, (int, float)) or idle_time_limit <= 0
    ):
        raise RecordingError("capture.idle_time_limit must be a positive number")
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


def copy_run_artifact(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def postmortem_entrypoint_text(*, run_dir: str, workdir: str, venv: str) -> str:
    run_id = Path(run_dir).name
    prompt_name = f"omegaflow:{run_id}"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"run_dir={shlex.quote(run_dir)}",
        f"workdir={shlex.quote(workdir)}",
        f"venv={shlex.quote(venv)}",
        f"export OMEGAFLOW_RUN_ID={shlex.quote(run_id)}",
        "export OMEGAFLOW_POSTMORTEM=1",
        'export OMEGAFLOW_RUN_DIR="$run_dir"',
        'export OMEGAFLOW_VENV="$venv"',
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
        f"omegaflow recording={require_string(spec, 'id')} " "step=record"
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


def recording_data_dir(spec: dict[str, Any] | None) -> Path:
    if spec is not None:
        config = spec.get("_studio_config")
        if isinstance(config, dict):
            return studio_data_dir_from_config(config)
    return studio_data_dir_from_config(None)


def recording_runs_dir(spec: dict[str, Any]) -> Path:
    recording_id = require_string(spec, "id")
    hydra_output_dir = relative_path(require_string(spec, "_hydra_output_dir"))
    if hydra_output_dir.parent.name == recording_id and (
        hydra_output_dir.parent.parent.name == "runs"
    ):
        return hydra_output_dir.parent
    return recording_data_dir(spec) / "runs" / recording_id


def all_recording_runs_root(data_dir: Path | None = None) -> Path:
    return (data_dir or studio_data_dir_from_config(None)) / "runs"


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
    fingerprint = run_dir / "recording.fingerprint.json"
    manifest = run_dir / "presentation" / "recording.presentation.json"
    if artifact == "inspect":
        return (run_dir / "enter").exists()
    if artifact == "output":
        return (run_dir / "failure.json").exists()
    if artifact == "success":
        return fingerprint.exists() and not (run_dir / "failure.json").exists()
    if artifact == "preserved":
        return (
            (run_dir / "enter").exists()
            or (run_dir / "failure.json").exists()
            or (run_dir / "failed.cast").exists()
            or fingerprint.exists()
            or manifest.exists()
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


def presentation_duration_seconds(path: Path) -> float | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        duration_ms = value["recording"]["duration_ms"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    return float(duration_ms) / 1000


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
    fingerprint = run_dir / "recording.fingerprint.json"
    manifest = run_dir / "presentation" / "recording.presentation.json"
    failed_cast = run_dir / "failed.cast"
    failure = read_failure_report(run_dir / "failure.json")
    if fingerprint.exists() and failure is None:
        length_seconds = presentation_duration_seconds(manifest)
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
    return subprocess.run([str(entrypoint)], cwd=project_root(), check=False).returncode


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
    run_id = config.get("run_id")
    if run_id is not None and not isinstance(run_id, str):
        raise RecordingError("run_id must be a string or null")
    spec = spec_from_hydra_cfg(cfg)
    if action == "inspect":
        if not recording_was_explicit(spec):
            spec = None
        return inspect_run(spec, run_id=run_id, data_dir=data_dir)
    if action == "output":
        if not recording_was_explicit(spec):
            spec = None
        return output_run(spec, run_id=run_id, data_dir=data_dir)
    raise RecordingError(
        f"unsupported record-tool action: {action}; use the omegaflow command"
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
