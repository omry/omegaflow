"""Typed normalization for terminal, browser, and mixed recording specs."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .studio_config import (
    BeatPlayerConfig,
    BrowserActionConfig,
    BrowserCheckConfig,
    BrowserChromePresentationConfig,
    BrowserConditionConfig,
    BrowserPointerPresentationConfig,
    BrowserRecordingConfig,
    BrowserTargetConfig,
    BrowserUrlMatcherConfig,
    BrowserWindowModeConfig,
    PlayerToolbarControl,
    PlayerToolbarHighlightConfig,
    RecordingActionConfig,
    RecordingCheckConfig,
    RecordingExpectationConfig,
    RecordingMedium,
    RecordingPresentationConfig,
    RecordingRequirementsConfig,
    RecordingStepConfig,
    StudioConfigError,
    TerminalEffectConfig,
    narration_text_and_anchors,
)


class RecordingPlanError(StudioConfigError):
    """Raised when a recording cannot be normalized into a typed plan."""


T = TypeVar("T")

ACTION_KINDS = (
    "open_page",
    "click",
    "move_pointer",
    "set_pointer",
    "fill",
    "type_keys",
    "press",
    "scroll",
    "wait_for",
)
TARGET_FAMILIES = ("role", "label", "placeholder", "text", "test_id", "css", "xpath")
CONDITION_KINDS = ("visible", "hidden", "url", "response")
CHECK_KINDS = ("url", "visible", "hidden", "text", "value", "count", "response")
URL_MATCH_KINDS = ("equals", "contains", "matches")
ACTION_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
ANCHOR_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_-]*@\Z")
TERMINAL_STEP_FIELDS = {item.name for item in fields(RecordingStepConfig)}
BROWSER_ACTION_FIELDS = {item.name for item in fields(BrowserActionConfig)}
BROWSER_CHECK_FIELDS = {item.name for item in fields(BrowserCheckConfig)}
BROWSER_ACTION_ONLY_FIELDS = BROWSER_ACTION_FIELDS - {"after"}
BROWSER_CHECK_ONLY_FIELDS = BROWSER_CHECK_FIELDS - {"name"}
TERMINAL_ACTION_ONLY_FIELDS = TERMINAL_STEP_FIELDS - {"after"}
TERMINAL_CHECK_ONLY_FIELDS = TERMINAL_STEP_FIELDS - {"name"}


def terminal_action_id(
    action_index: int,
    command_index: int | None,
    command: Mapping[str, Any] | None = None,
) -> str:
    """Return the stable ID shared by terminal capture and compilation."""

    if command_index is None:
        return f"__step_{action_index}"
    explicit = command.get("id") if command is not None else None
    return str(explicit or f"__step_{action_index}_command_{command_index}")


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecordingPlanError(f"{field} must be a mapping")
    return value


def _typed(value: dict[str, Any], schema: type[T], *, field: str) -> T:
    try:
        config = OmegaConf.merge(OmegaConf.structured(schema), value)
        result = OmegaConf.to_object(config)
    except (OmegaConfBaseException, TypeError, ValueError) as exc:
        raise RecordingPlanError(f"invalid {field}: {exc}") from exc
    if not isinstance(result, schema):
        raise RecordingPlanError(f"invalid {field}: expected {schema.__name__}")
    return result


def _one_present(mapping: dict[str, Any], names: tuple[str, ...], *, field: str) -> str:
    present = [name for name in names if mapping.get(name) is not None]
    if len(present) != 1:
        choices = ", ".join(names)
        raise RecordingPlanError(f"{field} must contain exactly one of: {choices}")
    return present[0]


def _positive_int(value: object, *, field: str, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecordingPlanError(f"{field} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise RecordingPlanError(f"{field} must be {qualifier}")
    return value


def _normalized_point(value: object, *, field: str) -> dict[str, Any]:
    point = _mapping(value, field=field)
    if set(point) != {"x", "y"}:
        raise RecordingPlanError(f"{field} must contain x and y")
    if any(
        isinstance(point[axis], bool)
        or not isinstance(point[axis], (int, float))
        for axis in ("x", "y")
    ):
        raise RecordingPlanError(
            f"{field} values must be numbers between 0 and 1"
        )
    if any(not 0 <= float(point[axis]) <= 1 for axis in ("x", "y")):
        raise RecordingPlanError(f"{field} values must be between 0 and 1")
    return point


def _expectation_mapping(value: object, *, field: str) -> dict[str, Any]:
    if isinstance(value, RecordingExpectationConfig):
        return asdict(value)
    return _mapping(value, field=field)


def validate_terminal_expectation(
    value: object,
    *,
    field: str,
) -> RecordingExpectationConfig:
    mapping = _expectation_mapping(value, field=field)
    allowed = {"exit_code", "output_contains", "output_regex", "file_exists"}
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        raise RecordingPlanError(
            f"{field} has unknown fields: {', '.join(unexpected)}"
        )
    exit_code = mapping.get("exit_code", 0)
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise RecordingPlanError(f"{field}.exit_code must be an integer")
    for name in ("output_contains", "output_regex", "file_exists"):
        values = mapping.get(name, [])
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            raise RecordingPlanError(
                f"{field}.{name} must be a list of non-empty strings"
            )
        if name == "output_regex":
            for pattern in values:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise RecordingPlanError(
                        f"{field}.output_regex is invalid: {exc}"
                    ) from exc
    return _typed(mapping, RecordingExpectationConfig, field=field)


def validate_terminal_output(value: object, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value not in {"real", "suppress"}:
            raise RecordingPlanError(f"{field} must be one of: real, suppress")
        return
    mapping = _mapping(value, field=field)
    if set(mapping) != {"replace"}:
        raise RecordingPlanError(f"{field} mapping must contain only: replace")
    if not isinstance(mapping["replace"], str):
        raise RecordingPlanError(f"{field}.replace must be a string")


def _optional_non_negative_number(value: object, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RecordingPlanError(f"{field} must be a non-negative number")


def validate_terminal_command(value: object, *, field: str) -> None:
    mapping = _mapping(value, field=field)
    has_run = isinstance(mapping.get("run"), str) and bool(mapping["run"])
    has_run_file = isinstance(mapping.get("run_file"), str) and bool(
        mapping["run_file"]
    )
    if has_run == has_run_file:
        raise RecordingPlanError(
            f"{field} must define exactly one of run or run_file"
        )
    command_id = mapping.get("id")
    if command_id not in {None, ""} and (
        not isinstance(command_id, str) or not ACTION_ID_RE.fullmatch(command_id)
    ):
        raise RecordingPlanError(f"{field}.id must be identifier-like")
    display = mapping.get("display")
    if display is not None and (not isinstance(display, str) or not display):
        raise RecordingPlanError(f"{field}.display must be a non-empty string")
    after = mapping.get("after")
    if after is not None and (not isinstance(after, str) or not ANCHOR_RE.fullmatch(after)):
        raise RecordingPlanError(f"{field}.after must contain exactly one @anchor@")
    for name in ("browser_handoff", "show_prompt_after"):
        if name in mapping and not isinstance(mapping[name], bool):
            raise RecordingPlanError(f"{field}.{name} must be a boolean")
    if mapping.get("timing", "presentation") not in {"presentation", "realtime"}:
        raise RecordingPlanError(
            f"{field}.timing must be presentation or realtime"
        )
    for name in (
        "pre_command_pause",
        "pre_enter_pause",
        "post_enter_pause",
        "post_command_pause",
    ):
        _optional_non_negative_number(mapping.get(name), field=f"{field}.{name}")
    validate_terminal_output(mapping.get("output"), field=f"{field}.output")
    validate_terminal_expectation(mapping.get("expect", {}), field=f"{field}.expect")


def validate_terminal_step(value: object, *, field: str) -> RecordingStepConfig:
    mapping = _mapping(value, field=field)
    commands = mapping.get("commands")
    if commands is None:
        validate_terminal_command(mapping, field=field)
    else:
        if not isinstance(commands, list) or not commands:
            raise RecordingPlanError(f"{field}.commands must be a non-empty list")
        if any(mapping.get(name) is not None for name in ("run", "run_file", "display")):
            raise RecordingPlanError(
                f"{field} must use commands or run/run_file/display, not both"
            )
        for index, command in enumerate(commands):
            validate_terminal_command(command, field=f"{field}.commands.{index}")
        validate_terminal_expectation(
            mapping.get("expect", {}), field=f"{field}.expect"
        )
        validate_terminal_output(mapping.get("output"), field=f"{field}.output")
    name = mapping.get("name")
    if name is not None and (not isinstance(name, str) or not name):
        raise RecordingPlanError(f"{field}.name must be a non-empty string")
    progress = mapping.get("progress", [])
    if not isinstance(progress, list) or any(
        not isinstance(item, str) or not item for item in progress
    ):
        raise RecordingPlanError(f"{field}.progress must be a list of non-empty strings")
    return _typed(mapping, RecordingStepConfig, field=field)


def validate_requirements(value: object, *, field: str = "requirements") -> None:
    mapping = _mapping(value, field=field)
    unexpected = sorted(set(mapping) - {"commands"})
    if unexpected:
        raise RecordingPlanError(
            f"{field} has unknown fields: {', '.join(unexpected)}"
        )
    commands = mapping.get("commands", [])
    if not isinstance(commands, list) or any(
        not isinstance(command, str) or not command for command in commands
    ):
        raise RecordingPlanError(
            f"{field}.commands must be a list of non-empty strings"
        )
    _typed(mapping, RecordingRequirementsConfig, field=field)


def validate_parameters(value: object, *, field: str = "parameters") -> None:
    mapping = _mapping(value, field=field)
    for name, parameter in mapping.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", name
        ):
            raise RecordingPlanError(f"{field} keys must be shell-safe names")
        if isinstance(parameter, dict):
            if set(parameter) != {"default"}:
                raise RecordingPlanError(
                    f"{field}.{name} mapping must contain only: default"
                )
            parameter = parameter["default"]
        if not isinstance(parameter, (str, int, float, bool)):
            raise RecordingPlanError(f"{field}.{name} must define a scalar default")


def _is_injected_default(value: object) -> bool:
    if value is None or value == "" or value is False:
        return True
    if value == [] or value == {}:
        return True
    if isinstance(value, RecordingExpectationConfig):
        value = asdict(value)
    if isinstance(value, dict) and set(value) == {
        "exit_code",
        "output_contains",
        "output_regex",
        "file_exists",
    }:
        return value == {
            "exit_code": 0,
            "output_contains": [],
            "output_regex": [],
            "file_exists": [],
        }
    return False


def _project_envelope(
    mapping: dict[str, Any],
    *,
    fields_to_keep: set[str],
    fields_to_reject: set[str],
    field: str,
) -> dict[str, Any]:
    unexpected = sorted(
        name
        for name in fields_to_reject
        if name in mapping and not _is_injected_default(mapping[name])
    )
    if unexpected:
        raise RecordingPlanError(
            f"{field} has fields invalid for this beat medium: {', '.join(unexpected)}"
        )
    return {name: value for name, value in mapping.items() if name in fields_to_keep}


def validate_target(value: object, *, field: str) -> BrowserTargetConfig:
    mapping = _mapping(value, field=field)
    family = _one_present(mapping, TARGET_FAMILIES, field=field)
    allowed = {family}
    if family == "role":
        allowed.add("name")
    if family in {"role", "label", "placeholder", "text"}:
        allowed.add("exact")
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        raise RecordingPlanError(
            f"{field} has fields invalid for {family}: {', '.join(unexpected)}"
        )
    selected = mapping.get(family)
    if not isinstance(selected, str) or not selected:
        raise RecordingPlanError(f"{field}.{family} must be a non-empty string")
    if "name" in mapping and mapping["name"] is not None and not isinstance(
        mapping["name"], str
    ):
        raise RecordingPlanError(f"{field}.name must be a string")
    return _typed(mapping, BrowserTargetConfig, field=field)


def validate_url_matcher(
    value: object,
    *,
    field: str,
    response: bool = False,
) -> None:
    mapping = _mapping(value, field=field)
    kind = _one_present(mapping, URL_MATCH_KINDS, field=field)
    allowed = set(URL_MATCH_KINDS)
    if response:
        allowed.update(("method", "status"))
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        raise RecordingPlanError(f"{field} has unknown fields: {', '.join(unexpected)}")
    matcher = mapping[kind]
    if not isinstance(matcher, str) or not matcher:
        raise RecordingPlanError(f"{field}.{kind} must be a non-empty string")
    if kind == "matches":
        try:
            re.compile(matcher)
        except re.error as exc:
            raise RecordingPlanError(f"{field}.matches is invalid: {exc}") from exc
    if response:
        method = mapping.get("method")
        if method is not None and (not isinstance(method, str) or not method):
            raise RecordingPlanError(f"{field}.method must be a non-empty string")
        status = mapping.get("status")
        if status is not None:
            status_value = _positive_int(status, field=f"{field}.status")
            if status_value > 599:
                raise RecordingPlanError(f"{field}.status must be at most 599")


def validate_condition(value: object, *, field: str) -> BrowserConditionConfig:
    mapping = _mapping(value, field=field)
    kind = _one_present(mapping, CONDITION_KINDS, field=field)
    unexpected = sorted(set(mapping) - {kind, "timeout_ms"})
    if unexpected:
        raise RecordingPlanError(f"{field} has unknown fields: {', '.join(unexpected)}")
    if kind in {"visible", "hidden"}:
        validate_target(mapping[kind], field=f"{field}.{kind}")
    elif kind == "url":
        validate_url_matcher(mapping[kind], field=f"{field}.url")
    else:
        validate_url_matcher(mapping[kind], field=f"{field}.response", response=True)
    if mapping.get("timeout_ms") is not None:
        _positive_int(mapping["timeout_ms"], field=f"{field}.timeout_ms")
    return _typed(mapping, BrowserConditionConfig, field=field)


def _validate_capture_url(value: object, *, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise RecordingPlanError(f"{field} must be a non-empty string")
    if value == "about:blank" or not urlsplit(value).scheme:
        return
    if urlsplit(value).scheme not in {"http", "https"}:
        raise RecordingPlanError(f"{field} must be relative, HTTP(S), or about:blank")


def validate_display_url(
    value: object,
    *,
    field: str,
    allow_handoff: bool = False,
) -> None:
    if not isinstance(value, str) or not value:
        raise RecordingPlanError(f"{field} must be a non-empty string")
    if value == "about:blank":
        return
    if value == "$handoff":
        if allow_handoff:
            return
        raise RecordingPlanError(f"{field} does not support $handoff")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RecordingPlanError(f"{field} must be absolute HTTP(S) or about:blank")
    if parsed.username is not None or parsed.password is not None:
        raise RecordingPlanError(f"{field} must not contain user information")


def _validate_secret(value: object, *, field: str) -> None:
    mapping = _mapping(value, field=field)
    unexpected = sorted(set(mapping) - {"env", "presentation", "placeholder"})
    if unexpected:
        raise RecordingPlanError(f"{field} has unknown fields: {', '.join(unexpected)}")
    env = mapping.get("env")
    if not isinstance(env, str) or not env:
        raise RecordingPlanError(f"{field}.env must be a non-empty string")
    presentation = mapping.get("presentation", "masked")
    if presentation not in {"masked", "placeholder", "omitted"}:
        raise RecordingPlanError(
            f"{field}.presentation must be masked, placeholder, or omitted"
        )
    placeholder = mapping.get("placeholder")
    if presentation == "placeholder" and (
        not isinstance(placeholder, str) or not placeholder
    ):
        raise RecordingPlanError(
            f"{field}.placeholder is required for placeholder presentation"
        )


def validate_browser_action(value: object, *, field: str) -> BrowserActionConfig:
    mapping = _mapping(value, field=field)
    kind = _one_present(mapping, ACTION_KINDS, field=field)
    allowed = {
        "id",
        kind,
        "after",
        "hold_before_ms",
        "hold_after_ms",
        "transition",
        "display_url_after",
    }
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        raise RecordingPlanError(f"{field} has unknown fields: {', '.join(unexpected)}")

    action_id = mapping.get("id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise RecordingPlanError(
            f"{field}.id must start with a letter and contain letters, digits, _ or -"
        )
    payload = _mapping(mapping[kind], field=f"{field}.{kind}")

    if kind == "open_page":
        source = _one_present(payload, ("url", "handoff"), field=f"{field}.open_page")
        if source == "url":
            _validate_capture_url(payload.get("url"), field=f"{field}.open_page.url")
        else:
            handoff = payload.get("handoff")
            if not isinstance(handoff, str) or not ACTION_ID_RE.fullmatch(handoff):
                raise RecordingPlanError(
                    f"{field}.open_page.handoff must be identifier-like"
                )
        display_url = payload.get("display_url")
        if display_url is not None:
            validate_display_url(
                display_url,
                field=f"{field}.open_page.display_url",
                allow_handoff=source == "handoff",
            )
            if display_url == "$handoff" and source != "handoff":
                raise RecordingPlanError(
                    f"{field}.open_page.display_url $handoff requires a handoff source"
                )
        if payload.get("lifecycle", "domcontentloaded") not in {
            "domcontentloaded",
            "load",
        }:
            raise RecordingPlanError(
                f"{field}.open_page.lifecycle must be domcontentloaded or load"
            )
        if payload.get("loading", "hide") not in {"hide", "show"}:
            raise RecordingPlanError(f"{field}.open_page.loading must be hide or show")
        if payload.get("ready") is not None:
            validate_condition(payload["ready"], field=f"{field}.open_page.ready")
        if payload.get("timeout_ms") is not None:
            _positive_int(payload["timeout_ms"], field=f"{field}.open_page.timeout_ms")
    elif kind == "move_pointer":
        destination = _one_present(
            payload,
            ("viewport", "target"),
            field=f"{field}.move_pointer",
        )
        if destination == "target":
            validate_target(
                payload["target"],
                field=f"{field}.move_pointer.target",
            )
            if payload.get("position") is not None:
                _normalized_point(
                    payload["position"],
                    field=f"{field}.move_pointer.position",
                )
        else:
            if payload.get("position") is not None:
                raise RecordingPlanError(
                    f"{field}.move_pointer.position requires a target"
                )
            _normalized_point(
                payload["viewport"],
                field=f"{field}.move_pointer.viewport",
            )
    elif kind == "set_pointer":
        if set(payload) != {"visible"}:
            raise RecordingPlanError(
                f"{field}.set_pointer must contain only visible"
            )
        if not isinstance(payload["visible"], bool):
            raise RecordingPlanError(
                f"{field}.set_pointer.visible must be boolean"
            )
    elif kind == "click":
        validate_target(payload.get("target"), field=f"{field}.click.target")
        if payload.get("button", "left") not in {"left", "middle", "right"}:
            raise RecordingPlanError(f"{field}.click.button is invalid")
        position = payload.get("position", "center")
        if position != "center":
            position_mapping = _mapping(position, field=f"{field}.click.position")
            if set(position_mapping) != {"x", "y"}:
                raise RecordingPlanError(
                    f"{field}.click.position must be center or contain x and y"
                )
            if any(
                isinstance(position_mapping[axis], bool)
                or not isinstance(position_mapping[axis], (int, float))
                for axis in ("x", "y")
            ):
                raise RecordingPlanError(f"{field}.click.position values must be numbers")
    elif kind in {"fill", "type_keys"}:
        validate_target(payload.get("target"), field=f"{field}.{kind}.target")
        content_kind = _one_present(payload, ("text", "secret"), field=f"{field}.{kind}")
        if content_kind == "text":
            if not isinstance(payload["text"], str):
                raise RecordingPlanError(f"{field}.{kind}.text must be a string")
        else:
            _validate_secret(payload["secret"], field=f"{field}.{kind}.secret")
        if kind == "type_keys" and payload.get("capture_delay_ms") is not None:
            _positive_int(
                payload["capture_delay_ms"],
                field=f"{field}.type_keys.capture_delay_ms",
                allow_zero=True,
            )
    elif kind == "press":
        key = payload.get("key")
        if not isinstance(key, str) or not key:
            raise RecordingPlanError(f"{field}.press.key must be a non-empty string")
        if payload.get("target") is not None:
            validate_target(payload["target"], field=f"{field}.press.target")
    elif kind == "scroll":
        destination = _one_present(payload, ("target", "by", "to"), field=f"{field}.scroll")
        if destination == "target":
            validate_target(payload["target"], field=f"{field}.scroll.target")
            if payload.get("container") is not None:
                raise RecordingPlanError(
                    f"{field}.scroll.container is valid only with by or to"
                )
        else:
            offset = _mapping(payload[destination], field=f"{field}.scroll.{destination}")
            if set(offset) != {"x", "y"} or any(
                isinstance(offset[axis], bool) or not isinstance(offset[axis], int)
                for axis in ("x", "y")
            ):
                raise RecordingPlanError(
                    f"{field}.scroll.{destination} must contain integer x and y"
                )
            if payload.get("container") is not None:
                validate_target(payload["container"], field=f"{field}.scroll.container")
    else:
        validate_condition(payload, field=f"{field}.wait_for")

    after = mapping.get("after")
    if after is not None and (not isinstance(after, str) or not ANCHOR_RE.fullmatch(after)):
        raise RecordingPlanError(f"{field}.after must contain exactly one @anchor@")
    for hold_field in ("hold_before_ms", "hold_after_ms"):
        if mapping.get(hold_field) is not None:
            _positive_int(
                mapping[hold_field],
                field=f"{field}.{hold_field}",
                allow_zero=True,
            )
    if mapping.get("transition") not in {None, "cut", "fade", "captured"}:
        raise RecordingPlanError(f"{field}.transition must be cut, fade, or captured")
    if mapping.get("display_url_after") is not None:
        validate_display_url(
            mapping["display_url_after"], field=f"{field}.display_url_after"
        )
    return _typed(mapping, BrowserActionConfig, field=field)


def validate_browser_check(value: object, *, field: str) -> BrowserCheckConfig:
    mapping = _mapping(value, field=field)
    name = mapping.get("name")
    if not isinstance(name, str) or not name:
        raise RecordingPlanError(f"{field}.name must be a non-empty string")
    kind = _one_present(mapping, CHECK_KINDS, field=field)
    unexpected = sorted(set(mapping) - {"name", kind})
    if unexpected:
        raise RecordingPlanError(f"{field} has unknown fields: {', '.join(unexpected)}")
    payload = mapping[kind]
    if kind == "url":
        validate_url_matcher(payload, field=f"{field}.url")
    elif kind in {"visible", "hidden"}:
        validate_target(payload, field=f"{field}.{kind}")
    elif kind in {"text", "value"}:
        check = _mapping(payload, field=f"{field}.{kind}")
        validate_target(check.get("target"), field=f"{field}.{kind}.target")
        matcher = {key: value for key, value in check.items() if key != "target"}
        validate_url_matcher(matcher, field=f"{field}.{kind}")
    elif kind == "count":
        check = _mapping(payload, field=f"{field}.count")
        validate_target(check.get("target"), field=f"{field}.count.target")
        unexpected_count = sorted(set(check) - {"target", "equals"})
        if unexpected_count:
            raise RecordingPlanError(
                f"{field}.count has unknown fields: {', '.join(unexpected_count)}"
            )
        _positive_int(
            check.get("equals"), field=f"{field}.count.equals", allow_zero=True
        )
    else:
        validate_url_matcher(payload, field=f"{field}.response", response=True)
    return _typed(mapping, BrowserCheckConfig, field=field)


def validate_browser_config(value: object, *, field: str = "browser") -> BrowserRecordingConfig:
    mapping = _mapping(value, field=field)
    config = _typed(mapping, BrowserRecordingConfig, field=field)
    if config.profile != "desktop-v1":
        raise RecordingPlanError(f"{field}.profile must be desktop-v1")
    if config.viewport is not None:
        width = config.viewport.width
        height = config.viewport.height
        if (width is None) != (height is None):
            raise RecordingPlanError(f"{field}.viewport width and height are required together")
        if width is not None:
            _positive_int(width, field=f"{field}.viewport.width")
            _positive_int(height, field=f"{field}.viewport.height")
        scale = config.viewport.device_scale_factor
        if scale is not None and (isinstance(scale, bool) or scale <= 0):
            raise RecordingPlanError(
                f"{field}.viewport.device_scale_factor must be positive"
            )
    if config.context is not None:
        if config.context.color_scheme not in {None, "light", "dark", "no-preference"}:
            raise RecordingPlanError(f"{field}.context.color_scheme is invalid")
        if config.context.reduced_motion not in {None, "reduce", "no-preference"}:
            raise RecordingPlanError(f"{field}.context.reduced_motion is invalid")
    if config.auth.storage_state_env and config.auth.storage_state_path:
        raise RecordingPlanError(
            f"{field}.auth storage_state_env and storage_state_path are mutually exclusive"
        )
    _positive_int(config.timeouts.action_ms, field=f"{field}.timeouts.action_ms")
    _positive_int(config.timeouts.readiness_ms, field=f"{field}.timeouts.readiness_ms")
    for index, redaction in enumerate(mapping.get("redactions", [])):
        redaction_mapping = _mapping(redaction, field=f"{field}.redactions.{index}")
        if set(redaction_mapping) != {"target"}:
            raise RecordingPlanError(
                f"{field}.redactions.{index} must contain only target"
            )
        validate_target(
            redaction_mapping.get("target"), field=f"{field}.redactions.{index}.target"
        )
    return config


def validate_presentation_config(
    value: object,
    *,
    field: str = "presentation",
) -> RecordingPresentationConfig:
    mapping = _mapping(value, field=field)
    config = _typed(mapping, RecordingPresentationConfig, field=field)
    browser = config.browser
    if browser.window.mode not in {"none", "framed"}:
        raise RecordingPlanError(f"{field}.browser.window.mode must be none or framed")
    if browser.window.opening_transition not in {"cut", "fade", "window-open"}:
        raise RecordingPlanError(f"{field}.browser.window.opening_transition is invalid")
    if browser.chrome.mode not in {"hidden", "minimal", "full"}:
        raise RecordingPlanError(f"{field}.browser.chrome.mode is invalid")
    if browser.transitions.default not in {"cut", "fade"}:
        raise RecordingPlanError(f"{field}.browser.transitions.default is invalid")
    if browser.typing.policy != "natural-v1":
        raise RecordingPlanError(f"{field}.browser.typing.policy must be natural-v1")
    return config


@dataclass(frozen=True)
class NormalizedBeatActions:
    terminal_actions: tuple[RecordingStepConfig, ...] = ()
    terminal_checks: tuple[RecordingStepConfig, ...] = ()
    browser_actions: tuple[BrowserActionConfig, ...] = ()
    browser_checks: tuple[BrowserCheckConfig, ...] = ()


def _recording_source_dir(spec: dict[str, Any]) -> Path | None:
    value = spec.get("_script_dir")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        root = spec.get("_project_root")
        if isinstance(root, str) and root:
            path = Path(root).expanduser() / path
    return path.resolve()


def _resolve_terminal_run_files(
    step: RecordingStepConfig,
    *,
    source_dir: Path | None,
) -> RecordingStepConfig:
    if source_dir is None:
        return step

    def resolved(value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = source_dir / path
        return str(path.resolve())

    commands = step.commands
    if commands is not None:
        commands = [replace(command, run_file=resolved(command.run_file)) for command in commands]
    return replace(step, run_file=resolved(step.run_file), commands=commands)


def normalize_beat_actions(
    beat: dict[str, Any],
    *,
    index: int,
) -> NormalizedBeatActions:
    field = f"beats.{index}"
    raw_medium = beat.get("medium", RecordingMedium.terminal.value)
    try:
        medium = RecordingMedium(raw_medium)
    except (TypeError, ValueError) as exc:
        raise RecordingPlanError(f"{field}.medium must be terminal or browser") from exc
    actions = beat.get("actions", [])
    checks = beat.get("checks", [])
    if not isinstance(actions, list):
        raise RecordingPlanError(f"{field}.actions must be a list")
    if not isinstance(checks, list):
        raise RecordingPlanError(f"{field}.checks must be a list")
    if medium is RecordingMedium.terminal:
        for action_index, action in enumerate(actions):
            action_mapping = _mapping(
                action, field=f"{field}.actions.{action_index}"
            )
            browser_kinds = [
                kind for kind in ACTION_KINDS if action_mapping.get(kind) is not None
            ]
            if browser_kinds:
                raise RecordingPlanError(
                    f"{field}.actions.{action_index} browser action "
                    f"{browser_kinds[0]} is invalid for a terminal beat"
                )
        return NormalizedBeatActions(
            terminal_actions=tuple(
                validate_terminal_step(
                    _project_envelope(
                        _mapping(action, field=f"{field}.actions.{action_index}"),
                        fields_to_keep=TERMINAL_STEP_FIELDS,
                        fields_to_reject=BROWSER_ACTION_ONLY_FIELDS,
                        field=f"{field}.actions.{action_index}",
                    ),
                    field=f"{field}.actions.{action_index}",
                )
                for action_index, action in enumerate(actions)
            ),
            terminal_checks=tuple(
                validate_terminal_step(
                    _project_envelope(
                        _mapping(check, field=f"{field}.checks.{check_index}"),
                        fields_to_keep=TERMINAL_STEP_FIELDS,
                        fields_to_reject=BROWSER_CHECK_ONLY_FIELDS,
                        field=f"{field}.checks.{check_index}",
                    ),
                    field=f"{field}.checks.{check_index}",
                )
                for check_index, check in enumerate(checks)
            ),
        )
    return NormalizedBeatActions(
        browser_actions=tuple(
            validate_browser_action(
                _project_envelope(
                    _mapping(action, field=f"{field}.actions.{action_index}"),
                    fields_to_keep=BROWSER_ACTION_FIELDS,
                    fields_to_reject=TERMINAL_ACTION_ONLY_FIELDS,
                    field=f"{field}.actions.{action_index}",
                ),
                field=f"{field}.actions.{action_index}",
            )
            for action_index, action in enumerate(actions)
        ),
        browser_checks=tuple(
            validate_browser_check(
                _project_envelope(
                    _mapping(check, field=f"{field}.checks.{check_index}"),
                    fields_to_keep=BROWSER_CHECK_FIELDS,
                    fields_to_reject=TERMINAL_CHECK_ONLY_FIELDS,
                    field=f"{field}.checks.{check_index}",
                ),
                field=f"{field}.checks.{check_index}",
            )
            for check_index, check in enumerate(checks)
        ),
    )


def validate_beat_pointer(
    beat: dict[str, Any],
    *,
    index: int,
    medium: RecordingMedium,
) -> BrowserPointerPresentationConfig | None:
    value = beat.get("pointer")
    if value is None:
        return None
    if medium is not RecordingMedium.browser:
        raise RecordingPlanError(f"beats.{index}.pointer is invalid for terminal beats")
    return _typed(
        _mapping(value, field=f"beats.{index}.pointer"),
        BrowserPointerPresentationConfig,
        field=f"beats.{index}.pointer",
    )


def validate_beat_browser_presentation(
    beat: dict[str, Any],
    *,
    index: int,
    medium: RecordingMedium,
) -> tuple[BrowserWindowModeConfig | None, BrowserChromePresentationConfig | None]:
    window_value = beat.get("window")
    chrome_value = beat.get("chrome")
    if window_value is None and chrome_value is None:
        return None, None
    if medium is not RecordingMedium.browser:
        field = "window" if window_value is not None else "chrome"
        raise RecordingPlanError(
            f"beats.{index}.{field} is invalid for terminal beats"
        )
    window = (
        None
        if window_value is None
        else _typed(
            _mapping(window_value, field=f"beats.{index}.window"),
            BrowserWindowModeConfig,
            field=f"beats.{index}.window",
        )
    )
    chrome = (
        None
        if chrome_value is None
        else _typed(
            _mapping(chrome_value, field=f"beats.{index}.chrome"),
            BrowserChromePresentationConfig,
            field=f"beats.{index}.chrome",
        )
    )
    if window is not None and window.mode not in {"none", "framed"}:
        raise RecordingPlanError(f"beats.{index}.window.mode must be none or framed")
    if chrome is not None and chrome.mode not in {"hidden", "minimal", "full"}:
        raise RecordingPlanError(f"beats.{index}.chrome.mode is invalid")
    return window, chrome


def _terminal_text_highlights(
    beat: dict[str, Any],
    *,
    index: int,
    medium: RecordingMedium,
    anchors: tuple[NarrationAnchorPlan, ...],
) -> tuple[TerminalTextHighlightPlan, ...]:
    raw_effects = beat.get("effects", [])
    if not isinstance(raw_effects, list):
        raise RecordingPlanError(f"beats.{index}.effects must be a list")
    if raw_effects and medium is not RecordingMedium.terminal:
        raise RecordingPlanError(
            f"beats.{index}.effects are invalid for browser beats"
        )

    anchor_offsets = {anchor.id: anchor.text_offset for anchor in anchors}
    highlights: list[TerminalTextHighlightPlan] = []
    for effect_index, raw_effect in enumerate(raw_effects):
        field = f"beats.{index}.effects.{effect_index}"
        effect_mapping = _mapping(raw_effect, field=field)
        _one_present(effect_mapping, ("highlight",), field=field)
        effect = _typed(effect_mapping, TerminalEffectConfig, field=field)
        if effect.highlight is None:  # pragma: no cover - guarded by _one_present
            raise RecordingPlanError(f"{field} must contain exactly one of: highlight")
        highlight = effect.highlight
        highlight_field = f"{field}.highlight"
        if not highlight.text:
            raise RecordingPlanError(f"{highlight_field}.text must be non-empty")
        occurrence = _positive_int(
            highlight.occurrence,
            field=f"{highlight_field}.occurrence",
        )
        for boundary, reference in (("start", highlight.start), ("end", highlight.end)):
            if not ANCHOR_RE.fullmatch(reference):
                raise RecordingPlanError(
                    f"{highlight_field}.{boundary} must be a narration anchor"
                )
            if reference[1:-1] not in anchor_offsets:
                raise RecordingPlanError(
                    f"{highlight_field} references unknown {boundary} anchor {reference}"
                )
        start_id = highlight.start[1:-1]
        end_id = highlight.end[1:-1]
        if anchor_offsets[start_id] >= anchor_offsets[end_id]:
            raise RecordingPlanError(
                f"{highlight_field} start anchor {highlight.start} must precede "
                f"end anchor {highlight.end}"
            )
        highlights.append(
            TerminalTextHighlightPlan(
                text=highlight.text,
                start_anchor=start_id,
                end_anchor=end_id,
                occurrence=occurrence,
            )
        )
    return tuple(highlights)


def _player_toolbar_highlight(
    beat: dict[str, Any],
    *,
    index: int,
    anchors: tuple[NarrationAnchorPlan, ...],
) -> PlayerToolbarHighlightPlan | None:
    player = beat.get("player")
    if not isinstance(player, dict):
        return None
    highlight = player.get("highlight")
    if not isinstance(highlight, dict):
        return None
    anchor_offsets = {anchor.id: anchor.text_offset for anchor in anchors}
    start_reference = highlight["start"]
    start_anchor = start_reference[1:-1]
    if start_anchor not in anchor_offsets:
        raise RecordingPlanError(
            f"beats.{index}.player.highlight references unknown start anchor "
            f"{start_reference}"
        )
    end_reference = highlight.get("end")
    end_anchor = None if end_reference is None else end_reference[1:-1]
    if end_anchor is not None:
        if end_anchor not in anchor_offsets:
            raise RecordingPlanError(
                f"beats.{index}.player.highlight references unknown end anchor "
                f"{end_reference}"
            )
        if anchor_offsets[start_anchor] >= anchor_offsets[end_anchor]:
            raise RecordingPlanError(
                f"beats.{index}.player.highlight start anchor must precede end anchor"
            )
    return PlayerToolbarHighlightPlan(
        control=PlayerToolbarControl(highlight["control"]).value,
        start_anchor=start_anchor,
        end_anchor=end_anchor,
    )


def validate_recording_modalities(spec: dict[str, Any]) -> None:
    validate_requirements(spec.get("requirements", {}))
    validate_parameters(spec.get("parameters", {}))
    for lifecycle in ("setup", "cleanup"):
        steps = spec.get(lifecycle, [])
        if not isinstance(steps, list):
            raise RecordingPlanError(f"{lifecycle} must be a list")
        for index, step in enumerate(steps):
            validate_terminal_step(step, field=f"{lifecycle}.{index}")
    beats = spec.get("beats", [])
    if not isinstance(beats, list):
        raise RecordingPlanError("beats must be a list")
    has_browser = False
    for index, value in enumerate(beats):
        beat = _mapping(value, field=f"beats.{index}")
        raw_medium = beat.get("medium", RecordingMedium.terminal.value)
        try:
            medium = RecordingMedium(raw_medium)
        except (TypeError, ValueError) as exc:
            raise RecordingPlanError(
                f"beats.{index}.medium must be terminal or browser"
            ) from exc
        if medium is RecordingMedium.browser:
            has_browser = True
        validate_beat_pointer(beat, index=index, medium=medium)
        validate_beat_browser_presentation(beat, index=index, medium=medium)
        player = beat.get("player")
        if player is not None:
            player_mapping = _mapping(player, field=f"beats.{index}.player")
            unexpected = sorted(
                set(player_mapping) - {item.name for item in fields(BeatPlayerConfig)}
            )
            if unexpected:
                raise RecordingPlanError(
                    f"beats.{index}.player has unknown fields: {', '.join(unexpected)}"
                )
            highlight = _mapping(
                player_mapping.get("highlight"),
                field=f"beats.{index}.player.highlight",
            )
            unexpected = sorted(
                set(highlight)
                - {item.name for item in fields(PlayerToolbarHighlightConfig)}
            )
            if unexpected:
                raise RecordingPlanError(
                    f"beats.{index}.player.highlight has unknown fields: "
                    f"{', '.join(unexpected)}"
                )
            try:
                PlayerToolbarControl(highlight.get("control"))
            except (TypeError, ValueError) as exc:
                raise RecordingPlanError(
                    f"beats.{index}.player.highlight.control is invalid"
                ) from exc
            for boundary in ("start", "end"):
                reference = highlight.get(boundary)
                if boundary == "end" and reference is None:
                    continue
                if not isinstance(reference, str) or not ANCHOR_RE.fullmatch(reference):
                    raise RecordingPlanError(
                        f"beats.{index}.player.highlight.{boundary} must be a "
                        "narration anchor"
                    )
        normalize_beat_actions(beat, index=index)
        guide = beat.get("guide")
        if guide is not None:
            guide_mapping = _mapping(guide, field=f"beats.{index}.guide")
        else:
            guide_mapping = None
        if medium is RecordingMedium.browser and guide_mapping is not None:
            commands = guide_mapping.get("commands", [])
            if commands:
                raise RecordingPlanError(
                    f"beats.{index}.guide.commands is invalid for browser beats"
                )
    browser = spec.get("browser")
    if has_browser and browser is None:
        raise RecordingPlanError("browser configuration is required for browser beats")
    if browser is not None:
        validate_browser_config(browser)
    presentation = spec.get("presentation", {})
    validate_presentation_config(presentation)


@dataclass(frozen=True)
class FrozenMapping(Mapping[str, Any]):
    """Small immutable mapping used by the execution plan."""

    entries: tuple[tuple[str, Any], ...] = ()

    def __getitem__(self, key: str) -> Any:
        for item_key, value in self.entries:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def freeze_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return FrozenMapping(
            tuple((item.name, freeze_value(getattr(value, item.name))) for item in fields(value))
        )
    if isinstance(value, dict):
        return FrozenMapping(
            tuple((str(key), freeze_value(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    return value


@dataclass(frozen=True)
class NarrationAnchorPlan:
    id: str
    text_offset: int


@dataclass(frozen=True)
class NarrationWaitPlan:
    target: str
    text_offset: int
    gap_ms: int


@dataclass(frozen=True)
class NarrationTakeAnchorPlan:
    beat_id: str
    id: str
    text_offset: int


@dataclass(frozen=True)
class NarrationTakeWaitPlan:
    beat_id: str
    target: str
    text_offset: int
    gap_ms: int


@dataclass(frozen=True)
class NarrationTakeMemberPlan:
    beat_id: str
    text: str
    text_start: int
    text_end: int


@dataclass(frozen=True)
class NarrationTakePlan:
    id: str
    explicit: bool
    members: tuple[NarrationTakeMemberPlan, ...]
    synthesis_text: str
    anchors: tuple[NarrationTakeAnchorPlan, ...]
    waits: tuple[NarrationTakeWaitPlan, ...]


@dataclass(frozen=True)
class TerminalActionPlan:
    config: FrozenMapping


@dataclass(frozen=True)
class BrowserActionPlan:
    id: str
    kind: str
    config: FrozenMapping


@dataclass(frozen=True)
class TerminalCheckPlan:
    config: FrozenMapping


@dataclass(frozen=True)
class TerminalTextHighlightPlan:
    text: str
    start_anchor: str
    end_anchor: str
    occurrence: int


@dataclass(frozen=True)
class PlayerToolbarHighlightPlan:
    control: str
    start_anchor: str
    end_anchor: str | None


@dataclass(frozen=True)
class BrowserCheckPlan:
    name: str
    kind: str
    config: FrozenMapping


@dataclass(frozen=True)
class BeatPlan:
    id: str
    medium: RecordingMedium
    heading: str
    caption: str
    narration_text: str
    explicit_narration_take: str | None
    viewer_hold_ms: int
    browser_pointer_visible: bool | None
    browser_window: FrozenMapping | None
    browser_chrome: FrozenMapping | None
    player_highlight: PlayerToolbarHighlightPlan | None
    guide: FrozenMapping | None
    anchors: tuple[NarrationAnchorPlan, ...]
    waits: tuple[NarrationWaitPlan, ...]
    terminal_highlights: tuple[TerminalTextHighlightPlan, ...]
    actions: tuple[TerminalActionPlan | BrowserActionPlan, ...]
    checks: tuple[TerminalCheckPlan | BrowserCheckPlan, ...]


@dataclass(frozen=True)
class RecordingPlan:
    id: str
    title: str | None
    browser: FrozenMapping | None
    presentation: FrozenMapping
    setup: tuple[TerminalCheckPlan, ...]
    beats: tuple[BeatPlan, ...]
    cleanup: tuple[TerminalCheckPlan, ...]
    narration_takes: tuple[NarrationTakePlan, ...]


def _browser_action_kind(action: BrowserActionConfig) -> str:
    kinds = [kind for kind in ACTION_KINDS if getattr(action, kind) is not None]
    if len(kinds) != 1:
        raise RecordingPlanError("normalized browser action does not have one kind")
    return kinds[0]


def _browser_check_kind(check: BrowserCheckConfig) -> str:
    kinds = [kind for kind in CHECK_KINDS if getattr(check, kind) is not None]
    if len(kinds) != 1:
        raise RecordingPlanError("normalized browser check does not have one kind")
    return kinds[0]


def _narration_by_beat(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    narration = spec.get("narration")
    if narration is None:
        return {}
    if not isinstance(narration, dict):
        raise RecordingPlanError("internal narration must be a mapping")
    if "beats" not in narration:
        return {}
    values = narration.get("beats")
    if not isinstance(values, list):
        raise RecordingPlanError("internal narration.beats must be a list")
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise RecordingPlanError(
                f"internal narration.beats.{index} must be a mapping"
            )
        beat_id = value.get("id")
        if not isinstance(beat_id, str) or not beat_id:
            raise RecordingPlanError(
                f"internal narration.beats.{index}.id must be a non-empty string"
            )
        if beat_id in result:
            raise RecordingPlanError(
                f"duplicate internal narration entry for beat {beat_id!r}"
            )
        result[beat_id] = value
    return result


def _beat_narration(
    beat: dict[str, Any], narration: dict[str, Any] | None
) -> tuple[str, tuple[NarrationAnchorPlan, ...], tuple[NarrationWaitPlan, ...]]:
    if narration is not None:
        text = narration.get("text", "")
        raw_anchors = narration.get("anchors", [])
        raw_waits = narration.get("waits", [])
    else:
        raw_text = beat.get("narration", "")
        if not isinstance(raw_text, str):
            raise RecordingPlanError(f"beat {beat.get('id')!r} narration must be a string")
        text, raw_anchors, raw_waits = narration_text_and_anchors(raw_text)
    if not isinstance(text, str):
        raise RecordingPlanError(f"beat {beat.get('id')!r} narration text must be a string")
    if not isinstance(raw_anchors, list):
        raise RecordingPlanError(f"beat {beat.get('id')!r} anchors must be a list")
    if not isinstance(raw_waits, list):
        raise RecordingPlanError(f"beat {beat.get('id')!r} waits must be a list")
    anchors: list[NarrationAnchorPlan] = []
    anchor_ids: set[str] = set()
    for index, value in enumerate(raw_anchors):
        mapping = _mapping(value, field=f"beat {beat.get('id')}.anchors.{index}")
        anchor_id = mapping.get("id")
        offset = mapping.get("text_offset")
        if not isinstance(anchor_id, str) or not ACTION_ID_RE.fullmatch(anchor_id):
            raise RecordingPlanError(f"invalid narration anchor in beat {beat.get('id')}")
        if anchor_id in anchor_ids:
            raise RecordingPlanError(f"duplicate narration anchor @{anchor_id}@")
        anchor_ids.add(anchor_id)
        text_offset = _positive_int(
            offset,
            field=f"beat {beat.get('id')}.anchors.{index}.text_offset",
            allow_zero=True,
        )
        if text_offset > len(text):
            raise RecordingPlanError(
                f"narration anchor @{anchor_id}@ is outside beat {beat.get('id')!r} text"
            )
        anchors.append(NarrationAnchorPlan(id=anchor_id, text_offset=text_offset))
    waits: list[NarrationWaitPlan] = []
    for index, value in enumerate(raw_waits):
        mapping = _mapping(value, field=f"beat {beat.get('id')}.waits.{index}")
        target = mapping.get("target")
        offset = mapping.get("text_offset")
        gap_seconds = mapping.get("gap_seconds", 0.0)
        if not isinstance(target, str) or not ACTION_ID_RE.fullmatch(target):
            raise RecordingPlanError(f"invalid narration wait target in beat {beat.get('id')}")
        if isinstance(gap_seconds, bool) or not isinstance(gap_seconds, (int, float)):
            raise RecordingPlanError(f"invalid narration wait gap in beat {beat.get('id')}")
        if gap_seconds < 0:
            raise RecordingPlanError(
                f"narration wait gap in beat {beat.get('id')!r} must be non-negative"
            )
        text_offset = _positive_int(
            offset,
            field=f"beat {beat.get('id')}.waits.{index}.text_offset",
            allow_zero=True,
        )
        if text_offset > len(text):
            raise RecordingPlanError(
                f"narration wait for {target!r} is outside beat {beat.get('id')!r} text"
            )
        waits.append(
            NarrationWaitPlan(
                target=target,
                text_offset=text_offset,
                gap_ms=round(float(gap_seconds) * 1000),
            )
        )
    return text, tuple(anchors), tuple(waits)


def _terminal_reference_ids(
    actions: tuple[RecordingStepConfig, ...], *, beat_id: str
) -> tuple[set[str], list[str]]:
    ids: set[str] = set()
    anchor_refs: list[str] = []
    for action in actions:
        if action.after:
            anchor_refs.append(action.after)
        for command in action.commands or []:
            if command.id:
                if command.id in ids:
                    raise RecordingPlanError(
                        f"duplicate terminal command id {command.id!r} in beat {beat_id!r}"
                    )
                ids.add(command.id)
            if command.after:
                anchor_refs.append(command.after)
    return ids, anchor_refs


def plan_narration_takes(
    beats: tuple[BeatPlan, ...],
) -> tuple[NarrationTakePlan, ...]:
    resolved_ids: list[str | None] = []
    for beat in beats:
        if not beat.narration_text:
            if beat.explicit_narration_take is not None:
                raise RecordingPlanError(
                    f"beat {beat.id!r} has narration_take but no narration"
                )
            resolved_ids.append(None)
        else:
            resolved_ids.append(beat.explicit_narration_take or f"__beat__:{beat.id}")

    closed: set[str] = set()
    active: str | None = None
    for take_id in resolved_ids:
        if take_id == active:
            continue
        if active is not None:
            closed.add(active)
        if take_id is not None and take_id in closed:
            raise RecordingPlanError(
                f"narration take {take_id!r} is fragmented; members must be contiguous"
            )
        active = take_id

    ordered_ids: list[str] = []
    grouped: dict[str, list[BeatPlan]] = {}
    for beat, take_id in zip(beats, resolved_ids, strict=True):
        if take_id is None:
            continue
        if take_id not in grouped:
            ordered_ids.append(take_id)
            grouped[take_id] = []
        grouped[take_id].append(beat)

    takes: list[NarrationTakePlan] = []
    for take_id in ordered_ids:
        member_beats = grouped[take_id]
        synthesis_text = " ".join(beat.narration_text for beat in member_beats)
        members: list[NarrationTakeMemberPlan] = []
        anchors: list[NarrationTakeAnchorPlan] = []
        waits: list[NarrationTakeWaitPlan] = []
        offset = 0
        for index, beat in enumerate(member_beats):
            start = offset
            end = start + len(beat.narration_text)
            members.append(
                NarrationTakeMemberPlan(
                    beat_id=beat.id,
                    text=beat.narration_text,
                    text_start=start,
                    text_end=end,
                )
            )
            anchors.extend(
                NarrationTakeAnchorPlan(
                    beat_id=beat.id,
                    id=anchor.id,
                    text_offset=start + anchor.text_offset,
                )
                for anchor in beat.anchors
            )
            waits.extend(
                NarrationTakeWaitPlan(
                    beat_id=beat.id,
                    target=wait.target,
                    text_offset=start + wait.text_offset,
                    gap_ms=wait.gap_ms,
                )
                for wait in beat.waits
            )
            offset = end + (1 if index + 1 < len(member_beats) else 0)
        takes.append(
            NarrationTakePlan(
                id=take_id,
                explicit=not take_id.startswith("__beat__:"),
                members=tuple(members),
                synthesis_text=synthesis_text,
                anchors=tuple(anchors),
                waits=tuple(waits),
            )
        )
    return tuple(takes)


def normalize_recording_plan(spec: dict[str, Any]) -> RecordingPlan:
    """Validate cross-references and return a deeply immutable execution plan."""

    validate_recording_modalities(spec)
    recording_id = spec.get("id")
    if not isinstance(recording_id, str) or not recording_id:
        raise RecordingPlanError("recording.id must be a non-empty string")
    title = spec.get("title")
    if title is not None and not isinstance(title, str):
        raise RecordingPlanError("recording.title must be a string")

    raw_beats = spec.get("beats", [])
    if not isinstance(raw_beats, list):
        raise RecordingPlanError("beats must be a list")
    narration_by_beat = _narration_by_beat(spec)
    seen_beat_ids: set[str] = set()
    seen_browser_action_ids: set[str] = set()
    first_browser_action_seen = False
    beat_plans: list[BeatPlan] = []
    browser_mapping = spec.get("browser")
    browser_config = (
        validate_browser_config(browser_mapping) if browser_mapping is not None else None
    )
    presentation = validate_presentation_config(spec.get("presentation", {}))
    audio = spec.get("audio", {})
    audio_enabled = isinstance(audio, dict) and audio.get("enabled") is True
    source_dir = _recording_source_dir(spec)
    lifecycle_steps: dict[str, tuple[TerminalCheckPlan, ...]] = {}
    for lifecycle in ("setup", "cleanup"):
        raw_steps = spec.get(lifecycle, [])
        if not isinstance(raw_steps, list):
            raise RecordingPlanError(f"{lifecycle} must be a list")
        lifecycle_steps[lifecycle] = tuple(
            TerminalCheckPlan(
                config=freeze_value(
                    _resolve_terminal_run_files(
                        _typed(
                            _mapping(step, field=f"{lifecycle}.{index}"),
                            RecordingStepConfig,
                            field=f"{lifecycle}.{index}",
                        ),
                        source_dir=source_dir,
                    )
                )
            )
            for index, step in enumerate(raw_steps)
        )

    for index, raw_beat in enumerate(raw_beats):
        beat = _mapping(raw_beat, field=f"beats.{index}")
        beat_id = beat.get("id")
        if not isinstance(beat_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", beat_id
        ):
            raise RecordingPlanError(f"beats.{index}.id is invalid")
        if beat_id in seen_beat_ids:
            raise RecordingPlanError(f"duplicate beat id {beat_id!r}")
        seen_beat_ids.add(beat_id)
        try:
            medium = RecordingMedium(beat.get("medium", RecordingMedium.terminal.value))
        except (TypeError, ValueError) as exc:
            raise RecordingPlanError(f"beats.{index}.medium is invalid") from exc
        pointer_config = validate_beat_pointer(beat, index=index, medium=medium)
        window_config, chrome_config = validate_beat_browser_presentation(
            beat,
            index=index,
            medium=medium,
        )
        normalized = normalize_beat_actions(beat, index=index)
        if medium is RecordingMedium.terminal:
            normalized = replace(
                normalized,
                terminal_actions=tuple(
                    _resolve_terminal_run_files(action, source_dir=source_dir)
                    for action in normalized.terminal_actions
                ),
                terminal_checks=tuple(
                    _resolve_terminal_run_files(check, source_dir=source_dir)
                    for check in normalized.terminal_checks
                ),
            )
        narration_entry = narration_by_beat.get(beat_id)
        narration_text, anchors, waits = _beat_narration(beat, narration_entry)
        anchor_ids = {anchor.id for anchor in anchors}
        terminal_highlights = _terminal_text_highlights(
            beat,
            index=index,
            medium=medium,
            anchors=anchors,
        )
        player_highlight = _player_toolbar_highlight(
            beat,
            index=index,
            anchors=anchors,
        )
        if terminal_highlights and not audio_enabled:
            raise RecordingPlanError(
                f"beats.{index}.effects.highlight requires audio.enabled=true"
            )
        if player_highlight is not None and not audio_enabled:
            raise RecordingPlanError(
                f"beats.{index}.player.highlight requires audio.enabled=true"
            )

        if medium is RecordingMedium.browser:
            browser_actions = tuple(
                BrowserActionPlan(
                    id=action.id,
                    kind=_browser_action_kind(action),
                    config=freeze_value(action),
                )
                for action in normalized.browser_actions
            )
            checks: tuple[TerminalCheckPlan | BrowserCheckPlan, ...] = tuple(
                BrowserCheckPlan(
                    name=check.name,
                    kind=_browser_check_kind(check),
                    config=freeze_value(check),
                )
                for check in normalized.browser_checks
            )
            actions = browser_actions
            action_ids = {action.id for action in browser_actions}
            for action in browser_actions:
                if action.id in seen_browser_action_ids:
                    raise RecordingPlanError(
                        f"duplicate browser action id {action.id!r} across recording"
                    )
                seen_browser_action_ids.add(action.id)
                after = action.config.get("after")
                if after is not None and after[1:-1] not in anchor_ids:
                    raise RecordingPlanError(
                        f"browser action {action.id!r} references unknown anchor {after}"
                    )
            if browser_actions:
                first = browser_actions[0]
                if not first_browser_action_seen:
                    if first.kind != "open_page":
                        raise RecordingPlanError(
                            "the first browser action in a recording must be open_page"
                        )
                    first_browser_action_seen = True
                if first.kind == "wait_for" and first.config["wait_for"].get(
                    "response"
                ) is not None:
                    raise RecordingPlanError(
                        f"wait_for.response cannot be first in browser beat {beat_id!r}"
                    )
            for action in browser_actions:
                if action.kind == "open_page":
                    payload = action.config["open_page"]
                    capture_url = payload.get("url")
                    if capture_url is not None and not urlsplit(capture_url).scheme and (
                        browser_config is None or not browser_config.base_url
                    ):
                        raise RecordingPlanError(
                            f"relative open_page URL in {action.id!r} requires browser.base_url"
                        )
                    effective_chrome_mode = (
                        presentation.browser.chrome.mode
                        if chrome_config is None
                        else chrome_config.mode
                    )
                    if (
                        effective_chrome_mode == "full"
                        and payload.get("display_url") is None
                    ):
                        raise RecordingPlanError(
                            f"open_page {action.id!r} requires display_url with full chrome"
                        )
            wait_targets = action_ids
            anchor_refs: list[str] = []
        else:
            actions = tuple(
                TerminalActionPlan(config=freeze_value(action))
                for action in normalized.terminal_actions
            )
            checks = tuple(
                TerminalCheckPlan(config=freeze_value(check))
                for check in normalized.terminal_checks
            )
            wait_targets, anchor_refs = _terminal_reference_ids(
                normalized.terminal_actions, beat_id=beat_id
            )
            for anchor_ref in anchor_refs:
                if not ANCHOR_RE.fullmatch(anchor_ref) or anchor_ref[1:-1] not in anchor_ids:
                    raise RecordingPlanError(
                        f"terminal action in beat {beat_id!r} references unknown anchor {anchor_ref!r}"
                    )
        for wait in waits:
            if wait.target not in wait_targets:
                raise RecordingPlanError(
                    f"narration wait in beat {beat_id!r} references unknown action or command {wait.target!r}"
                )

        viewer_hold = beat.get("viewer_hold")
        if viewer_hold is None and narration_entry is not None:
            viewer_hold = narration_entry.get("viewer_hold")
        if viewer_hold is None:
            viewer_hold_ms = 0
        elif isinstance(viewer_hold, bool) or not isinstance(viewer_hold, (int, float)):
            raise RecordingPlanError(f"beat {beat_id!r} viewer_hold must be a number")
        elif viewer_hold < 0:
            raise RecordingPlanError(f"beat {beat_id!r} viewer_hold must be non-negative")
        else:
            viewer_hold_ms = round(float(viewer_hold) * 1000)

        guide_value = beat.get("guide")
        guide = freeze_value(guide_value) if isinstance(guide_value, dict) else None
        explicit_take = beat.get("narration_take")
        if explicit_take is not None and (
            not isinstance(explicit_take, str) or not ACTION_ID_RE.fullmatch(explicit_take)
        ):
            raise RecordingPlanError(f"beat {beat_id!r} narration_take is invalid")
        heading = beat.get("heading")
        if not heading and narration_entry is not None:
            heading = narration_entry.get("heading", "")
        if heading is None:
            heading = ""
        if not isinstance(heading, str):
            raise RecordingPlanError(f"beat {beat_id!r} heading must be a string")
        caption = beat.get("caption", "")
        if caption is None:
            caption = ""
        if not isinstance(caption, str):
            raise RecordingPlanError(f"beat {beat_id!r} caption must be a string")
        beat_plans.append(
            BeatPlan(
                id=beat_id,
                medium=medium,
                heading=heading,
                caption=caption,
                narration_text=narration_text,
                explicit_narration_take=explicit_take,
                viewer_hold_ms=viewer_hold_ms,
                browser_pointer_visible=(
                    None if pointer_config is None else pointer_config.visible
                ),
                browser_window=(
                    None if window_config is None else freeze_value(window_config)
                ),
                browser_chrome=(
                    None if chrome_config is None else freeze_value(chrome_config)
                ),
                player_highlight=player_highlight,
                guide=guide,
                anchors=anchors,
                waits=waits,
                terminal_highlights=terminal_highlights,
                actions=actions,
                checks=checks,
            )
        )

    unknown_narration_beats = set(narration_by_beat) - seen_beat_ids
    if unknown_narration_beats:
        unknown = ", ".join(sorted(unknown_narration_beats))
        raise RecordingPlanError(
            f"internal narration references unknown beat(s): {unknown}"
        )

    frozen_beats = tuple(beat_plans)
    _validate_browser_handoffs(frozen_beats)
    return RecordingPlan(
        id=recording_id,
        title=title,
        browser=freeze_value(browser_config) if browser_config is not None else None,
        presentation=freeze_value(presentation),
        setup=lifecycle_steps["setup"],
        beats=frozen_beats,
        cleanup=lifecycle_steps["cleanup"],
        narration_takes=plan_narration_takes(frozen_beats),
    )


def _validate_browser_handoffs(beats: tuple[BeatPlan, ...]) -> None:
    for beat_index, beat in enumerate(beats):
        if beat.medium is not RecordingMedium.terminal:
            continue
        handoffs: list[tuple[int, int, Mapping[str, Any]]] = []
        for action_index, action in enumerate(beat.actions):
            if not isinstance(action, TerminalActionPlan):
                continue
            commands = action.config.get("commands") or ()
            for command_index, command in enumerate(commands):
                if command.get("browser_handoff"):
                    handoffs.append((action_index, command_index, command))
        if not handoffs:
            continue
        if len(handoffs) != 1:
            raise RecordingPlanError(
                f"terminal beat {beat.id!r} must contain at most one browser_handoff"
            )
        action_index, command_index, command = handoffs[0]
        command_id = command.get("id")
        if not isinstance(command_id, str) or not command_id:
            raise RecordingPlanError("browser_handoff command requires an explicit id")
        if command.get("timing") != "realtime":
            raise RecordingPlanError(
                "browser_handoff command requires timing: realtime"
            )
        if command.get("show_prompt_after") is not False:
            raise RecordingPlanError(
                "browser_handoff command requires show_prompt_after: false"
            )
        output = command.get("output")
        if output is not None and output != "real":
            raise RecordingPlanError("browser_handoff command requires real output")
        final_action = beat.actions[-1]
        final_commands = (
            final_action.config.get("commands")
            if isinstance(final_action, TerminalActionPlan)
            else None
        )
        if (
            action_index != len(beat.actions) - 1
            or not final_commands
            or command_index != len(final_commands) - 1
        ):
            raise RecordingPlanError(
                "browser_handoff command must be the last command in its terminal beat"
            )
        if beat_index + 1 >= len(beats):
            raise RecordingPlanError(
                f"browser_handoff {command_id!r} has no following browser beat"
            )
        consumer = beats[beat_index + 1]
        if consumer.medium is not RecordingMedium.browser or not consumer.actions:
            raise RecordingPlanError(
                f"following beat does not consume browser_handoff {command_id!r}"
            )
        first = consumer.actions[0]
        open_page = (
            first.config.get("open_page")
            if isinstance(first, BrowserActionPlan) and first.kind == "open_page"
            else None
        )
        if not open_page or open_page.get("handoff") != command_id:
            raise RecordingPlanError(
                f"following browser beat does not consume browser_handoff {command_id!r}"
            )

    for beat_index, beat in enumerate(beats):
        if beat.medium is not RecordingMedium.browser or not beat.actions:
            continue
        first = beat.actions[0]
        if not isinstance(first, BrowserActionPlan) or first.kind != "open_page":
            continue
        handoff_id = first.config["open_page"].get("handoff")
        if handoff_id is None:
            continue
        if beat_index == 0 or beats[beat_index - 1].medium is not RecordingMedium.terminal:
            raise RecordingPlanError(
                f"open_page handoff {handoff_id!r} has no preceding terminal command"
            )
        producer_ids = {
            command.get("id")
            for action in beats[beat_index - 1].actions
            if isinstance(action, TerminalActionPlan)
            for command in (action.config.get("commands") or ())
            if command.get("browser_handoff")
        }
        if handoff_id not in producer_ids:
            raise RecordingPlanError(
                f"open_page handoff {handoff_id!r} has no preceding terminal command"
            )
