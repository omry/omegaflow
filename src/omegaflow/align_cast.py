#!/usr/bin/env python3
"""Align an asciinema cast with a recording config.

This is a proof-of-concept analyzer. It uses visible captions and prompted
command lines, so it can tell whether a cast still resembles the config, but
it is not a durable sync format.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hydra
from omegaconf import DictConfig

from . import retime_cast
from .studio_config import (
    CONFIG_DIR,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    container_from_hydra_cfg,
    load_recording_spec,
    load_recording_spec_from_hydra_cfg,
    project_root,
)
from .terminal_style import (
    ANSI_CYAN_BOLD,
    ANSI_GREEN_BOLD,
    ANSI_YELLOW_BOLD,
    print_status,
)


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class AlignmentError(RuntimeError):
    pass


def pass_line(message: str) -> None:
    print_status("pass", message, color=ANSI_GREEN_BOLD)


def warn_line(message: str) -> None:
    print_status("warn", message, color=ANSI_YELLOW_BOLD)


def info_line(message: str) -> None:
    print_status("info", message, color=ANSI_CYAN_BOLD)


@dataclass(frozen=True)
class CastLine:
    time: float
    text: str
    is_caption: bool


@dataclass(frozen=True)
class ObservedCommand:
    time: float
    beat_caption: str | None
    text: str


@dataclass(frozen=True)
class ExpectedCommand:
    beat_id: str
    beat_caption: str | None
    action_index: int
    text: str


def load_manifest(
    recording_id: str, overrides: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    try:
        return load_recording_spec(recording_id, overrides)
    except StudioConfigError as exc:
        raise AlignmentError(str(exc)) from exc


def as_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return project_root() / candidate


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(project_root()))
    except ValueError:
        return str(path)


def cast_path_from_manifest(spec: dict[str, Any]) -> Path:
    outputs = as_mapping(spec.get("outputs"))
    configured_retimed = outputs.get("retimed_cast")
    if isinstance(configured_retimed, str) and configured_retimed:
        return relative_path(configured_retimed)
    cast = outputs.get("cast")
    if not isinstance(cast, str) or not cast:
        raise AlignmentError(
            "recording config outputs.retimed_cast or outputs.cast must be a non-empty string"
        )
    cast_path = relative_path(cast)
    return retime_cast.output_path_from_manifest(spec, cast_path)


def clean_terminal_text(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def is_colored_caption_line(text: str) -> bool:
    return "\x1b[36;1m# " in text and clean_terminal_text(text).strip().startswith("# ")


def read_cast_lines(path: Path) -> list[CastLine]:
    if not path.exists():
        raise AlignmentError(f"cast file not found: {path}")

    lines: list[CastLine] = []
    current: list[str] = []
    current_raw: list[str] = []
    current_time = 0.0
    absolute_time = 0.0

    with path.open(encoding="utf-8") as handle:
        header = handle.readline()
        if not header:
            raise AlignmentError(f"cast file is empty: {path}")
        try:
            json.loads(header)
        except json.JSONDecodeError as exc:
            raise AlignmentError(f"invalid asciinema header in {path}") from exc

        for raw in handle:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AlignmentError(f"invalid asciinema event in {path}") from exc
            if (
                not isinstance(event, list)
                or len(event) != 3
                or not isinstance(event[0], (int, float))
            ):
                continue
            delay, event_type, payload = event
            absolute_time += float(delay)
            if event_type != "o" or not isinstance(payload, str):
                continue
            for char in payload.replace("\r\n", "\n").replace("\r", "\n"):
                if not current:
                    current_time = absolute_time
                current_raw.append(char)
                if char == "\n":
                    raw_line = "".join(current_raw)
                    lines.append(
                        CastLine(
                            current_time,
                            clean_terminal_text(raw_line).rstrip("\n"),
                            is_colored_caption_line(raw_line),
                        )
                    )
                    current = []
                    current_raw = []
                else:
                    current.append(char)
        if current:
            raw_line = "".join(current_raw)
            lines.append(
                CastLine(
                    current_time,
                    clean_terminal_text(raw_line),
                    is_colored_caption_line(raw_line),
                )
            )
    return lines


def terminal_command_lines(text: str) -> list[str]:
    result: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        result.append(line.strip())
    return result


def expected_captions(spec: dict[str, Any]) -> list[tuple[str, str]]:
    captions: list[tuple[str, str]] = []
    for beat in as_list(spec.get("beats")):
        if not isinstance(beat, dict):
            continue
        beat_id = beat.get("id")
        caption = beat.get("caption")
        if isinstance(beat_id, str) and isinstance(caption, str) and caption:
            captions.append((beat_id, caption))
    return captions


def expected_commands(spec: dict[str, Any]) -> list[ExpectedCommand]:
    commands: list[ExpectedCommand] = []
    for beat in as_list(spec.get("beats")):
        if not isinstance(beat, dict):
            continue
        beat_id = beat.get("id")
        caption = beat.get("caption")
        if not isinstance(beat_id, str):
            continue
        beat_caption = caption if isinstance(caption, str) else None
        for index, action in enumerate(as_list(beat.get("actions")), start=1):
            if not isinstance(action, dict):
                continue
            action_commands = action.get("commands")
            if isinstance(action_commands, list):
                for action_command in action_commands:
                    if not isinstance(action_command, dict):
                        continue
                    display = action_command.get("display", action_command.get("run"))
                    if not isinstance(display, str):
                        continue
                    for command in terminal_command_lines(display):
                        commands.append(
                            ExpectedCommand(
                                beat_id=beat_id,
                                beat_caption=beat_caption,
                                action_index=index,
                                text=command,
                            )
                        )
                continue
            display = action.get("display", action.get("run"))
            if isinstance(display, str):
                for command in terminal_command_lines(display):
                    commands.append(
                        ExpectedCommand(
                            beat_id=beat_id,
                            beat_caption=beat_caption,
                            action_index=index,
                            text=command,
                        )
                    )
    return commands


def observed_captions(lines: list[CastLine]) -> list[tuple[float, str]]:
    captions: list[tuple[float, str]] = []
    for line in lines:
        text = line.text.strip()
        if line.is_caption and text.startswith("# "):
            captions.append((line.time, text[2:]))
    return captions


def observed_commands(lines: list[CastLine]) -> list[ObservedCommand]:
    commands: list[ObservedCommand] = []
    current_caption: str | None = None
    previous_command: ObservedCommand | None = None
    for line in lines:
        text = line.text.rstrip()
        stripped = text.strip()
        if line.is_caption and stripped.startswith("# "):
            current_caption = stripped[2:]
            previous_command = None
            continue
        if text.startswith("$ "):
            previous_command = ObservedCommand(
                time=line.time,
                beat_caption=current_caption,
                text=text[2:].strip(),
            )
            commands.append(previous_command)
            continue
        if not stripped:
            continue
        if previous_command is not None and text.startswith("  ") and stripped:
            previous_command = ObservedCommand(
                time=line.time,
                beat_caption=current_caption,
                text=stripped,
            )
            commands.append(previous_command)
            continue
        previous_command = None
    return commands


@dataclass(frozen=True)
class AlignmentReport:
    text: str
    aligned: bool
    matched_captions: int
    expected_captions: int
    matched_commands: int
    expected_commands: int
    observed_captions: int
    observed_commands: int
    mismatch_lines: tuple[str, ...]

    def summary_text(self) -> str:
        status = "ok" if self.aligned else "review required"
        return (
            f"{status}: alignment "
            f"captions {self.matched_captions}/{self.expected_captions}, "
            f"commands {self.matched_commands}/{self.expected_commands}"
        )


def render_report(spec: dict[str, Any], cast_path: Path) -> AlignmentReport:
    lines = read_cast_lines(cast_path)
    expected_caps = expected_captions(spec)
    observed_caps = observed_captions(lines)
    expected_cmds = expected_commands(spec)
    observed_cmds = observed_commands(lines)

    report: list[str] = [
        f"config: {display_path(Path(spec['_manifest_path']))}",
        f"cast: {display_path(cast_path)}",
        "",
        "Captions",
    ]
    mismatch_lines: list[str] = []

    matched_captions = 0
    for index, (beat_id, caption) in enumerate(expected_caps):
        observed = observed_caps[index] if index < len(observed_caps) else None
        if observed is not None and observed[1] == caption:
            matched_captions += 1
            report.append(f"  ok  {observed[0]:7.3f}s  {beat_id}: {caption}")
        elif observed is None:
            report.append(f"  miss {'':7}   {beat_id}: {caption}")
            mismatch_lines.append(f"caption missing {beat_id}: {caption}")
        else:
            report.append(f"  diff {observed[0]:7.3f}s  {beat_id}: {caption}")
            report.append(f"       observed: {observed[1]}")
            mismatch_lines.append(f"caption diff {beat_id}: expected {caption}")
            mismatch_lines.append(f"caption diff {beat_id}: observed {observed[1]}")
    for observed in observed_caps[len(expected_caps) :]:
        report.append(f"  extra {observed[0]:7.3f}s  {observed[1]}")
        mismatch_lines.append(f"caption extra: {observed[1]}")

    report.extend(["", "Commands"])
    matched_commands = 0
    for index, expected in enumerate(expected_cmds):
        observed = observed_cmds[index] if index < len(observed_cmds) else None
        if observed is None:
            report.append(
                f"  miss          {expected.beat_id}.{expected.action_index}: {expected.text}"
            )
            mismatch_lines.append(
                f"command missing {expected.beat_id}.{expected.action_index}: "
                f"{expected.text}"
            )
            continue
        if observed.text == expected.text:
            matched_commands += 1
            report.append(
                f"  ok  {observed.time:7.3f}s  "
                f"{expected.beat_id}.{expected.action_index}: {expected.text}"
            )
        else:
            report.append(
                f"  diff {observed.time:7.3f}s  "
                f"{expected.beat_id}.{expected.action_index}: {expected.text}"
            )
            report.append(f"       observed: {observed.text}")
            mismatch_lines.append(
                f"command diff {expected.beat_id}.{expected.action_index}: "
                f"expected {expected.text}"
            )
            mismatch_lines.append(
                f"command diff {expected.beat_id}.{expected.action_index}: "
                f"observed {observed.text}"
            )
    for observed in observed_cmds[len(expected_cmds) :]:
        report.append(f"  extra {observed.time:7.3f}s  {observed.text}")
        mismatch_lines.append(f"command extra: {observed.text}")

    report.extend(
        [
            "",
            "Summary",
            f"  captions: {matched_captions}/{len(expected_caps)} matched",
            f"  commands: {matched_commands}/{len(expected_cmds)} matched",
        ]
    )
    aligned = (
        matched_captions == len(expected_caps)
        and len(observed_caps) == len(expected_caps)
        and matched_commands == len(expected_cmds)
        and len(observed_cmds) == len(expected_cmds)
    )
    if aligned:
        report.extend(["", "Review", "  aligned: no manual review required"])
    else:
        report.extend(
            [
                "",
                "Review",
                "  misaligned: manual review required",
                "  Check whether the recording should be regenerated, the config",
                "  should be updated, or the movie script no longer matches the",
                "  versioned workflow.",
            ]
        )
    report.extend(
        [
            "",
            "Limitations",
            "  This POC aligns visible text only. It can drift if captions, prompts,",
            "  terminal wrapping, ANSI output, or command text changes. A production",
            "  retiming pipeline should emit a sidecar timeline with beat/action/phase",
            "  boundaries during capture.",
        ]
    )
    return AlignmentReport(
        text="\n".join(report),
        aligned=aligned,
        matched_captions=matched_captions,
        expected_captions=len(expected_caps),
        matched_commands=matched_commands,
        expected_commands=len(expected_cmds),
        observed_captions=len(observed_caps),
        observed_commands=len(observed_cmds),
        mismatch_lines=tuple(mismatch_lines),
    )


def print_mismatch_lines(report: AlignmentReport, *, limit: int = 12) -> None:
    if not report.mismatch_lines:
        return
    info_line("alignment mismatches:")
    shown = report.mismatch_lines[:limit]
    for line in shown:
        info_line(line)
    remaining = len(report.mismatch_lines) - len(shown)
    if remaining > 0:
        warn_line(
            f"{remaining} more alignment mismatches hidden; rerun with verbose=true"
        )


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
        spec = load_recording_spec_from_hydra_cfg(cfg)
        action = config.get("step") or config.get("action", "align")
        if action == "build":
            action = "align"
        if action not in {"align", "check"}:
            raise AlignmentError("action must be 'align' or 'check'")
        cast_override = config.get("cast")
        allow_mismatch = config.get("allow_mismatch", False)
        verbose = config.get("verbose", False)
        if cast_override is not None and not isinstance(cast_override, str):
            raise AlignmentError("cast must be a string or null")
        if not isinstance(allow_mismatch, bool):
            raise AlignmentError("allow_mismatch must be a boolean")
        if not isinstance(verbose, bool):
            raise AlignmentError("verbose must be a boolean")
        cast_path = (
            relative_path(cast_override)
            if cast_override
            else cast_path_from_manifest(spec)
        )
        report = render_report(spec, cast_path)
        if verbose:
            print(report.text)
        elif report.aligned:
            pass_line(
                "alignment "
                f"captions {report.matched_captions}/{report.expected_captions}, "
                f"commands {report.matched_commands}/{report.expected_commands}"
            )
        else:
            warn_line(
                "alignment "
                f"captions {report.matched_captions}/{report.expected_captions}, "
                f"commands {report.matched_commands}/{report.expected_commands}"
            )
            print_mismatch_lines(report)
            warn_line("rerun with verbose=true for full alignment report")
        if report.aligned or allow_mismatch:
            return 0
        return 2
    except StudioConfigError as exc:
        raise AlignmentError(str(exc)) from exc


@hydra.main(
    version_base=None,
    config_path=str(CONFIG_DIR),
    config_name=STUDIO_CONFIG_NAME,
)
def main(cfg: DictConfig) -> None:
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except AlignmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
