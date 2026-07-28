"""Typed normalization for terminal, browser, and mixed recording specs."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
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
    OuterBeatTransitionKind,
    PaneActionConfig,
    PaneBeatConfig,
    PaneConfig,
    PaneKind,
    PaneLayoutConfig,
    PaneTitleConfig,
    PaneTransitionConfig,
    PaneTransitionKind,
    PlayerToolbarControl,
    PlayerToolbarHighlightConfig,
    RecordingActionConfig,
    RecordingCheckConfig,
    RecordingExpectationConfig,
    RecordingMedium,
    RecordingPresentationConfig,
    RecordingRequirementsConfig,
    RecordingStepConfig,
    TerminalActionConfig,
    StudioConfigError,
    BeatEffectConfig,
    narration_text_and_anchors,
)
from .service_environment import (
    ALLOWED_SERVICE_ENVIRONMENT_NAMES,
)


class RecordingPlanError(StudioConfigError):
    """Raised when a recording cannot be normalized into a typed plan."""


PRESENTATION_PANE_LIMIT = 64
PRESENTATION_ITEM_LIMIT = 100_000


class StreamKind(str, Enum):
    narration = "narration"
    pane = "pane"


class EventEndpoint(str, Enum):
    started = "started"
    ended = "ended"


T = TypeVar("T")

ACTION_KINDS = (
    "open_page",
    "click",
    "move_pointer",
    "drag",
    "set_pointer",
    "fill",
    "type_text",
    "press",
    "scroll",
    "wait_for",
)


def _validate_plan_id(value: str, *, field_name: str) -> None:
    if not ACTION_ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field_name} {value!r}")


TARGET_FAMILIES = ("role", "label", "placeholder", "text", "test_id", "css", "xpath")
CONDITION_KINDS = ("visible", "hidden", "url", "response")
CHECK_KINDS = ("url", "visible", "hidden", "text", "value", "count", "response")
URL_MATCH_KINDS = ("equals", "contains", "matches")
ACTION_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
ANCHOR_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_-]*@\Z")
CSS_LENGTH_RE = re.compile(
    r"(?:0|(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em|%))\Z"
)
TERMINAL_INPUT_OPERATIONS = ("wait_for", "text", "key", "control", "pause")
TERMINAL_INPUT_KEYS = frozenset(
    {
        "enter",
        "tab",
        "escape",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "page_up",
        "page_down",
    }
)
TERMINAL_STEP_FIELDS = {item.name for item in fields(RecordingStepConfig)}
TERMINAL_ACTION_FIELDS = {item.name for item in fields(TerminalActionConfig)}
BROWSER_ACTION_FIELDS = {item.name for item in fields(BrowserActionConfig)}
BROWSER_CHECK_FIELDS = {item.name for item in fields(BrowserCheckConfig)}
BROWSER_ACTION_ONLY_FIELDS = BROWSER_ACTION_FIELDS - {"after", "timing"}
BROWSER_CHECK_ONLY_FIELDS = BROWSER_CHECK_FIELDS - {"name"}
TERMINAL_ACTION_ONLY_FIELDS = TERMINAL_ACTION_FIELDS - {"after", "timing"}
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


def _resolved_action_timing(
    value: object,
    *,
    field: str,
    default: str = "presentation",
) -> str:
    if value is None:
        value = default
    if isinstance(value, Enum):
        value = value.value
    if value not in {"presentation", "realtime"}:
        raise RecordingPlanError(f"{field} must be presentation or realtime")
    return str(value)


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


def validate_terminal_input_step(value: object, *, field: str) -> None:
    mapping = _mapping(value, field=field)
    operations = [
        name for name in TERMINAL_INPUT_OPERATIONS if mapping.get(name) is not None
    ]
    if len(operations) != 1:
        raise RecordingPlanError(f"{field} must define exactly one operation")
    operation = operations[0]
    allowed = {operation}
    if operation == "wait_for":
        allowed.add("timeout")
    elif operation == "text":
        allowed.add("interval")
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        name = unexpected[0]
        if name == "timeout":
            raise RecordingPlanError(
                f"{field}.timeout is only valid with wait_for"
            )
        if name == "interval":
            raise RecordingPlanError(
                f"{field}.interval is only valid with text"
            )
        raise RecordingPlanError(
            f"{field} has fields invalid for {operation}: {', '.join(unexpected)}"
        )
    if operation in {"wait_for", "text"} and (
        not isinstance(mapping[operation], str) or not mapping[operation]
    ):
        raise RecordingPlanError(
            f"{field}.{operation} must be a non-empty string"
        )
    if operation == "key" and mapping[operation] not in TERMINAL_INPUT_KEYS:
        raise RecordingPlanError(f"{field}.key is an unsupported key")
    if operation == "control" and (
        not isinstance(mapping[operation], str)
        or re.fullmatch(r"[A-Za-z]", mapping[operation]) is None
    ):
        raise RecordingPlanError(
            f"{field}.control must be a single ASCII letter"
        )
    if operation == "pause":
        _optional_non_negative_number(mapping[operation], field=f"{field}.pause")
    if "timeout" in mapping:
        _optional_non_negative_number(mapping["timeout"], field=f"{field}.timeout")
    if "interval" in mapping:
        _optional_non_negative_number(
            mapping["interval"], field=f"{field}.interval"
        )


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
    browser_handoff = mapping.get("browser_handoff", False)
    if not isinstance(browser_handoff, bool):
        handoff_mapping = _mapping(
            browser_handoff,
            field=f"{field}.browser_handoff",
        )
        if set(handoff_mapping) != {"target"}:
            raise RecordingPlanError(
                f"{field}.browser_handoff must contain exactly one target"
            )
        target = handoff_mapping["target"]
        if not isinstance(target, str) or not ACTION_ID_RE.fullmatch(target):
            raise RecordingPlanError(
                f"{field}.browser_handoff.target must be identifier-like"
            )
    if "show_prompt_after" in mapping and not isinstance(
        mapping["show_prompt_after"], bool
    ):
        raise RecordingPlanError(
            f"{field}.show_prompt_after must be a boolean"
        )
    with_env = mapping.get("with_env", [])
    if not isinstance(with_env, list) or any(
        not isinstance(name, str) or not name for name in with_env
    ):
        raise RecordingPlanError(
            f"{field}.with_env must be a list of non-empty strings"
        )
    duplicates = sorted(
        {name for name in with_env if with_env.count(name) > 1}
    )
    if duplicates:
        raise RecordingPlanError(
            f"{field}.with_env contains duplicate names: {', '.join(duplicates)}"
        )
    unsupported = sorted(set(with_env) - ALLOWED_SERVICE_ENVIRONMENT_NAMES)
    if unsupported:
        raise RecordingPlanError(
            f"{field}.with_env name {unsupported[0]!r} is not an allowlisted "
            "OmegaFlow service environment name"
        )
    timing = _resolved_action_timing(
        mapping.get("timing"),
        field=f"{field}.timing",
    )
    input_steps = mapping.get("input", [])
    if not isinstance(input_steps, list):
        raise RecordingPlanError(f"{field}.input must be a list")
    if input_steps and timing != "realtime":
        raise RecordingPlanError(f"{field}.input requires timing: realtime")
    if (
        input_steps
        and mapping.get("output") is not None
        and mapping.get("output") != "real"
    ):
        raise RecordingPlanError(f"{field}.input requires output: real")
    for index, input_step in enumerate(input_steps):
        validate_terminal_input_step(
            input_step,
            field=f"{field}.input.{index}",
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


def validate_terminal_step(
    value: object,
    *,
    field: str,
    action: bool = False,
) -> RecordingStepConfig:
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
    schema = TerminalActionConfig if action else RecordingStepConfig
    return _typed(mapping, schema, field=field)


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
        "timing",
        "audio",
        "until",
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
    elif kind == "drag":
        unknown_drag_fields = sorted(set(payload) - {"from", "to"})
        if unknown_drag_fields:
            raise RecordingPlanError(
                f"{field}.drag has unknown fields: "
                + ", ".join(unknown_drag_fields)
            )
        if not {"from", "to"} <= set(payload):
            raise RecordingPlanError(
                f"{field}.drag must contain from and to"
            )
        for endpoint_name in ("from", "to"):
            endpoint_field = f"{field}.drag.{endpoint_name}"
            endpoint = _mapping(payload[endpoint_name], field=endpoint_field)
            unexpected_endpoint = sorted(set(endpoint) - {"target", "position"})
            if unexpected_endpoint:
                raise RecordingPlanError(
                    f"{endpoint_field} has unknown fields: "
                    + ", ".join(unexpected_endpoint)
                )
            validate_target(
                endpoint.get("target"),
                field=f"{endpoint_field}.target",
            )
            if endpoint.get("position") is not None:
                _normalized_point(
                    endpoint["position"],
                    field=f"{endpoint_field}.position",
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
    elif kind in {"fill", "type_text"}:
        validate_target(payload.get("target"), field=f"{field}.{kind}.target")
        content_kind = _one_present(payload, ("text", "secret"), field=f"{field}.{kind}")
        if content_kind == "text":
            if not isinstance(payload["text"], str):
                raise RecordingPlanError(f"{field}.{kind}.text must be a string")
        else:
            _validate_secret(payload["secret"], field=f"{field}.{kind}.secret")
        if kind == "type_text" and payload.get("interval_ms") is not None:
            _positive_int(
                payload["interval_ms"],
                field=f"{field}.type_text.interval_ms",
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

    timing = _resolved_action_timing(
        mapping.get("timing"),
        field=f"{field}.timing",
    )
    audio = mapping.get("audio")
    if audio not in {None, "capture"}:
        raise RecordingPlanError(f"{field}.audio must be capture")
    if audio == "capture":
        if timing != "realtime":
            raise RecordingPlanError(
                f"{field}.audio capture requires timing: realtime"
            )
        if kind in {"open_page", "set_pointer"}:
            raise RecordingPlanError(
                f"{field}.{kind} does not support audio capture"
            )
    until = mapping.get("until")
    if until is not None:
        if kind == "wait_for":
            raise RecordingPlanError(f"{field}.wait_for cannot also define until")
        if timing != "realtime":
            raise RecordingPlanError(f"{field}.until requires timing: realtime")
        validate_condition(until, field=f"{field}.until")
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
    if mapping.get("transition") == "captured":
        raise RecordingPlanError(
            f"{field}.transition must be cut or fade; use timing: realtime "
            "to preserve observed motion"
        )
    if mapping.get("transition") not in {None, "cut", "fade"}:
        raise RecordingPlanError(f"{field}.transition must be cut or fade")
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


def _terminal_action_with_timing_default(
    value: object,
    *,
    default_timing: str,
    field: str,
) -> dict[str, Any]:
    action = dict(_mapping(value, field=field))
    action_timing = _resolved_action_timing(
        action.get("timing"),
        field=f"{field}.timing",
        default=default_timing,
    )
    action["timing"] = action_timing
    commands = action.get("commands")
    if commands is None:
        return action
    if not isinstance(commands, list):
        return action
    normalized_commands: list[dict[str, Any]] = []
    for command_index, raw_command in enumerate(commands):
        command_field = f"{field}.commands.{command_index}"
        command = dict(_mapping(raw_command, field=command_field))
        command["timing"] = _resolved_action_timing(
            command.get("timing"),
            field=f"{command_field}.timing",
            default=action_timing,
        )
        normalized_commands.append(command)
    action["commands"] = normalized_commands
    return action


def normalize_beat_actions(
    beat: dict[str, Any],
    *,
    index: int,
    default_timing: str | None = None,
) -> NormalizedBeatActions:
    field = f"beats.{index}"
    beat_timing = _resolved_action_timing(
        beat.get("timing"),
        field=f"{field}.timing",
        default=default_timing or "presentation",
    )
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
            if (
                action_mapping.get("with_env") is not None
                and action_mapping.get("commands") is None
            ):
                raise RecordingPlanError(
                    f"{field}.actions.{action_index}.with_env is supported only "
                    "on entries inside commands"
                )
            browser_kinds = [
                kind for kind in ACTION_KINDS if action_mapping.get(kind) is not None
            ]
            if browser_kinds:
                raise RecordingPlanError(
                    f"{field}.actions.{action_index} browser action "
                    f"{browser_kinds[0]} is invalid for a terminal beat"
                )
        for check_index, check in enumerate(checks):
            check_mapping = _mapping(
                check, field=f"{field}.checks.{check_index}"
            )
            if check_mapping.get("timing") is not None:
                raise RecordingPlanError(
                    f"{field}.checks.{check_index}.timing is valid only for actions"
                )
        return NormalizedBeatActions(
            terminal_actions=tuple(
                validate_terminal_step(
                    _project_envelope(
                        _terminal_action_with_timing_default(
                            action,
                            default_timing=beat_timing,
                            field=f"{field}.actions.{action_index}",
                        ),
                        fields_to_keep=TERMINAL_ACTION_FIELDS,
                        fields_to_reject=BROWSER_ACTION_ONLY_FIELDS,
                        field=f"{field}.actions.{action_index}",
                    ),
                    field=f"{field}.actions.{action_index}",
                    action=True,
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
                    {
                        **_mapping(
                            action,
                            field=f"{field}.actions.{action_index}",
                        ),
                        "timing": _resolved_action_timing(
                            _mapping(
                                action,
                                field=f"{field}.actions.{action_index}",
                            ).get("timing"),
                            field=f"{field}.actions.{action_index}.timing",
                            default=beat_timing,
                        ),
                    },
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


def _text_highlights(
    beat: dict[str, Any],
    *,
    index: int,
    anchors: tuple[NarrationAnchorPlan, ...],
    pane_kinds: Mapping[str, PaneKind],
    default_pane_id: str | None,
) -> tuple[TextHighlightEffectPlan, ...]:
    raw_effects = beat.get("effects", [])
    if not isinstance(raw_effects, list):
        raise RecordingPlanError(f"beats.{index}.effects must be a list")

    anchor_offsets = {anchor.id: anchor.text_offset for anchor in anchors}
    highlights: list[TextHighlightEffectPlan] = []
    for effect_index, raw_effect in enumerate(raw_effects):
        field = f"beats.{index}.effects.{effect_index}"
        effect_mapping = _mapping(raw_effect, field=field)
        _one_present(effect_mapping, ("highlight",), field=field)
        effect = _typed(effect_mapping, BeatEffectConfig, field=field)
        if effect.highlight is None:  # pragma: no cover - guarded by _one_present
            raise RecordingPlanError(f"{field} must contain exactly one of: highlight")
        highlight = effect.highlight
        highlight_field = f"{field}.highlight"
        pane_id = highlight.pane if highlight.pane is not None else default_pane_id
        if pane_id is None:
            raise RecordingPlanError(
                f"{highlight_field}.pane is required for a multi-pane beat"
            )
        if pane_id not in pane_kinds:
            raise RecordingPlanError(
                f"{highlight_field}.pane references unknown pane {pane_id!r}"
            )
        if pane_kinds[pane_id] not in {PaneKind.terminal, PaneKind.visualization}:
            raise RecordingPlanError(
                f"{highlight_field}.pane {pane_id!r} does not expose a text surface"
            )
        targets = _text_highlight_targets(
            highlight.targets,
            field=highlight_field,
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
            TextHighlightEffectPlan(
                pane_id=pane_id,
                targets=tuple(targets),
                color=highlight.color.value,
                start_anchor=start_id,
                end_anchor=end_id,
            )
        )
    return tuple(highlights)


def _text_highlight_targets(
    values: list[object],
    *,
    field: str,
) -> tuple[TextHighlightTargetPlan, ...]:
    if not values:
        raise RecordingPlanError(f"{field}.targets must be non-empty")
    targets: list[TextHighlightTargetPlan] = []
    for target_index, target in enumerate(values):
        target_field = f"{field}.targets.{target_index}"
        matchers = [
            (kind, pattern)
            for kind, pattern in (
                ("text", getattr(target, "text", None)),
                ("regex", getattr(target, "regex", None)),
            )
            if pattern
        ]
        if len(matchers) != 1:
            raise RecordingPlanError(
                f"{target_field} must contain exactly one of: text, regex"
            )
        kind, pattern = matchers[0]
        if kind == "regex":
            if len(pattern) > 256:
                raise RecordingPlanError(
                    f"{target_field}.regex must be at most 256 characters"
                )
            escapes = re.findall(r"\\([A-Za-z])", pattern)
            if (
                re.search(r"(?:[*+?]|\})\+", pattern)
                or any(escape not in "dDsSwWbBnrtfvxu" for escape in escapes)
                or re.search(r"\\[1-9]", pattern)
                or re.search(r"\(\?(?:[=!]|<[=!]|>|P=|\(|#)", pattern)
            ):
                raise RecordingPlanError(
                    f"{target_field}.regex uses unsupported syntax"
                )
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise RecordingPlanError(
                    f"{target_field}.regex is invalid: {exc}"
                ) from exc
            for probe in ("", "a", " ", "\n", "ab"):
                match = compiled.search(probe)
                if match is not None and match.start() == match.end():
                    raise RecordingPlanError(
                        f"{target_field}.regex must not match empty text"
                    )
        targets.append(
            TextHighlightTargetPlan(
                kind=kind,
                pattern=pattern,
                occurrence=_positive_int(
                    getattr(target, "occurrence", None),
                    field=f"{target_field}.occurrence",
                ),
            )
        )
    return tuple(targets)


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
    start_join: JoinPlan | None = None


@dataclass(frozen=True)
class BrowserActionPlan:
    id: str
    kind: str
    config: FrozenMapping
    start_join: JoinPlan | None = None


@dataclass(frozen=True)
class TerminalCheckPlan:
    config: FrozenMapping


@dataclass(frozen=True)
class TextHighlightTargetPlan:
    kind: str
    pattern: str
    occurrence: int


@dataclass(frozen=True)
class TextHighlightEffectPlan:
    pane_id: str
    targets: tuple[TextHighlightTargetPlan, ...]
    color: str
    start_anchor: str
    end_anchor: str


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
class VisualizationActionPlan:
    id: str
    language: str
    text: str

    def __post_init__(self) -> None:
        _validate_plan_id(self.id, field_name="visualization action id")
        if not self.language or len(self.language) > 64:
            raise ValueError("visualization language must be 1 to 64 characters")
        if not self.text or len(self.text) > 100_000:
            raise ValueError(
                "visualization text must be 1 to 100000 characters"
            )


@dataclass(frozen=True)
class StreamRef:
    kind: StreamKind
    id: str

    def __post_init__(self) -> None:
        if not ACTION_ID_RE.fullmatch(self.id):
            raise ValueError(f"invalid stream id {self.id!r}")


@dataclass(frozen=True)
class StreamPosition:
    action_id: str | None = None
    pane_beat_id: str | None = None
    endpoint: EventEndpoint = EventEndpoint.started

    def __post_init__(self) -> None:
        if self.action_id is not None and not ACTION_ID_RE.fullmatch(self.action_id):
            raise ValueError(f"invalid stream position action id {self.action_id!r}")
        if self.pane_beat_id is not None and not ACTION_ID_RE.fullmatch(
            self.pane_beat_id
        ):
            raise ValueError(
                f"invalid stream position pane beat id {self.pane_beat_id!r}"
            )


@dataclass(frozen=True)
class EventRef:
    stream: StreamRef
    action_id: str
    endpoint: EventEndpoint
    pane_beat_id: str | None = None

    def __post_init__(self) -> None:
        if not ACTION_ID_RE.fullmatch(self.action_id):
            raise ValueError(f"invalid event action id {self.action_id!r}")
        if self.stream.kind is StreamKind.narration:
            if self.pane_beat_id is not None:
                raise ValueError("narration event cannot have a pane beat id")
        elif self.pane_beat_id is None or not ACTION_ID_RE.fullmatch(
            self.pane_beat_id
        ):
            raise ValueError("pane event requires a valid pane beat id")

    @property
    def qualified_id(self) -> str:
        components = [self.stream.id]
        if self.pane_beat_id is not None:
            components.append(self.pane_beat_id)
        components.extend((self.action_id, self.endpoint.value))
        return ".".join(components)


@dataclass(frozen=True)
class JoinPlan:
    waiting_stream: StreamRef
    waiting_position: StreamPosition
    event: EventRef
    gap_ms: int = 0

    def __post_init__(self) -> None:
        if self.gap_ms < 0:
            raise ValueError("join gap must be non-negative")
        if self.waiting_stream == self.event.stream:
            raise ValueError("join must reference another stream")
        if (
            self.waiting_stream.kind is StreamKind.narration
            and self.waiting_position.pane_beat_id is not None
        ):
            raise ValueError("narration position cannot have a pane beat id")
        if (
            self.waiting_stream.kind is StreamKind.pane
            and self.waiting_position.pane_beat_id is None
        ):
            raise ValueError("pane position requires a pane beat id")


@dataclass(frozen=True)
class NarrationSegmentPlan:
    id: str
    beat_id: str
    text_start: int
    text_end: int

    def __post_init__(self) -> None:
        _validate_plan_id(self.id, field_name="narration segment id")
        _validate_plan_id(self.beat_id, field_name="narration segment beat id")
        if self.text_start < 0 or self.text_end < self.text_start:
            raise ValueError("invalid narration segment range")


@dataclass(frozen=True)
class NarrationStreamPlan:
    id: str
    segments: tuple[NarrationSegmentPlan, ...]

    def __post_init__(self) -> None:
        _validate_plan_id(self.id, field_name="narration stream id")
        segment_ids = [segment.id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("duplicate narration segment id")


@dataclass(frozen=True)
class PaneTitlePlan:
    visible: bool = True
    text: str | None = None
    alignment_x: str = "right"
    alignment_y: str = "top"
    position_x: str = "0.25rem"
    position_y: str = "0.25rem"

    def __post_init__(self) -> None:
        if self.text is not None and not self.text.strip():
            raise ValueError("pane title must be non-empty")
        for name in ("position_x", "position_y"):
            if not CSS_LENGTH_RE.fullmatch(getattr(self, name)):
                raise ValueError(f"pane title {name} must be a non-negative CSS length")


@dataclass(frozen=True)
class PanePlan:
    id: str
    kind: PaneKind
    title: PaneTitlePlan = field(default_factory=PaneTitlePlan)

    def __post_init__(self) -> None:
        _validate_plan_id(self.id, field_name="pane id")


@dataclass(frozen=True)
class PaneLayoutPlan:
    areas: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PaneTransitionPlan:
    kind: PaneTransitionKind = PaneTransitionKind.cut
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("pane transition duration must be non-negative")


@dataclass(frozen=True)
class OuterBeatTransitionPlan:
    kind: OuterBeatTransitionKind = OuterBeatTransitionKind.cut
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("outer beat transition duration must be non-negative")


@dataclass(frozen=True)
class PanePresentationPlan:
    browser_pointer_visible: bool | None = None
    browser_window: FrozenMapping | None = None
    browser_chrome: FrozenMapping | None = None


@dataclass(frozen=True)
class TerminalPaneRecordingPlan:
    actions: tuple[TerminalActionPlan, ...]
    checks: tuple[TerminalCheckPlan, ...]


@dataclass(frozen=True)
class BrowserPaneRecordingPlan:
    actions: tuple[BrowserActionPlan, ...]
    checks: tuple[BrowserCheckPlan, ...]


@dataclass(frozen=True)
class VisualizationPaneRecordingPlan:
    action: VisualizationActionPlan


PaneRecordingPlan = (
    TerminalPaneRecordingPlan
    | BrowserPaneRecordingPlan
    | VisualizationPaneRecordingPlan
)


@dataclass(frozen=True)
class PaneBeatPlan:
    id: str
    start_join: JoinPlan | None
    recording: PaneRecordingPlan
    presentation: PanePresentationPlan
    transition: PaneTransitionPlan

    @property
    def actions(
        self,
    ) -> tuple[
        TerminalActionPlan | BrowserActionPlan | VisualizationActionPlan, ...
    ]:
        if isinstance(self.recording, VisualizationPaneRecordingPlan):
            return (self.recording.action,)
        return self.recording.actions

    @property
    def checks(self) -> tuple[TerminalCheckPlan | BrowserCheckPlan, ...]:
        if isinstance(self.recording, VisualizationPaneRecordingPlan):
            return ()
        return self.recording.checks

    def __post_init__(self) -> None:
        _validate_plan_id(self.id, field_name="pane beat id")


@dataclass(frozen=True)
class OuterPaneTrackPlan:
    pane_id: str
    kind: PaneKind
    beats: tuple[PaneBeatPlan, ...]

    def __post_init__(self) -> None:
        _validate_plan_id(self.pane_id, field_name="pane id")
        if not self.beats:
            raise ValueError("pane track must contain at least one pane beat")
        beat_ids = [beat.id for beat in self.beats]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError(f"duplicate pane beat id in pane {self.pane_id!r}")


@dataclass(frozen=True)
class OuterBeatPlan:
    id: str
    heading: str
    caption: str
    narration_text: str
    explicit_narration_take: str | None
    viewer_hold_ms: int
    player_highlight: PlayerToolbarHighlightPlan | None
    guide: FrozenMapping | None
    anchors: tuple[NarrationAnchorPlan, ...]
    waits: tuple[NarrationWaitPlan, ...]
    effects: tuple[TextHighlightEffectPlan, ...]
    pane_tracks: tuple[OuterPaneTrackPlan, ...]
    layout: PaneLayoutPlan
    transition: OuterBeatTransitionPlan = field(
        default_factory=OuterBeatTransitionPlan
    )

    @property
    def pane_track(self) -> OuterPaneTrackPlan:
        if len(self.pane_tracks) != 1 or len(self.pane_tracks[0].beats) != 1:
            raise RecordingPlanError(
                f"outer beat {self.id!r} is not a single-pane shorthand beat"
            )
        return self.pane_tracks[0]

    @property
    def pane_beat(self) -> PaneBeatPlan:
        return self.pane_track.beats[0]

    @property
    def medium(self) -> RecordingMedium:
        return RecordingMedium(self.pane_track.kind.value)

    @property
    def browser_pointer_visible(self) -> bool | None:
        return self.pane_beat.presentation.browser_pointer_visible

    @property
    def browser_window(self) -> FrozenMapping | None:
        return self.pane_beat.presentation.browser_window

    @property
    def browser_chrome(self) -> FrozenMapping | None:
        return self.pane_beat.presentation.browser_chrome

    @property
    def actions(
        self,
    ) -> tuple[
        TerminalActionPlan | BrowserActionPlan | VisualizationActionPlan, ...
    ]:
        return self.pane_beat.actions

    @property
    def checks(self) -> tuple[TerminalCheckPlan | BrowserCheckPlan, ...]:
        return self.pane_beat.checks


@dataclass(frozen=True)
class RecordingPlan:
    id: str
    title: str | None
    browser: FrozenMapping | None
    presentation: FrozenMapping
    setup: tuple[TerminalCheckPlan, ...]
    panes: tuple[PanePlan, ...]
    beats: tuple[OuterBeatPlan, ...]
    cleanup: tuple[TerminalCheckPlan, ...]
    narration_stream: NarrationStreamPlan
    narration_takes: tuple[NarrationTakePlan, ...]
    browser_handoffs: tuple[BrowserHandoffPlan, ...] = ()


@dataclass(frozen=True)
class BrowserHandoffPlan:
    id: str
    producer_outer_beat_id: str
    producer_pane_id: str
    producer_pane_beat_id: str
    target_pane_id: str
    consumer_outer_beat_id: str
    consumer_pane_beat_id: str
    consumer_action_id: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("browser handoff id", self.id),
            ("producer outer beat id", self.producer_outer_beat_id),
            ("producer pane id", self.producer_pane_id),
            ("producer pane beat id", self.producer_pane_beat_id),
            ("target pane id", self.target_pane_id),
            ("consumer outer beat id", self.consumer_outer_beat_id),
            ("consumer pane beat id", self.consumer_pane_beat_id),
            ("consumer action id", self.consumer_action_id),
        ):
            _validate_plan_id(value, field_name=field_name)


@dataclass(frozen=True)
class CapturedPaneBeatPlan:
    outer_beat_id: str
    pane_id: str
    capture_id: str
    kind: PaneKind
    beat: PaneBeatPlan


def captured_pane_beats(plan: RecordingPlan) -> tuple[CapturedPaneBeatPlan, ...]:
    """Return captured pane beats in outer-beat and track source order."""

    result: list[CapturedPaneBeatPlan] = []
    seen_capture_ids: set[str] = set()
    explicit = bool(plan.panes)
    for outer in plan.beats:
        for track in outer.pane_tracks:
            if track.kind is PaneKind.visualization:
                continue
            for pane_beat in track.beats:
                capture_id = (
                    f"{outer.id}--{track.pane_id}--{pane_beat.id}"
                    if explicit
                    else outer.id
                )
                if capture_id in seen_capture_ids:
                    raise RecordingPlanError(
                        f"generated capture id {capture_id!r} is not unique"
                    )
                seen_capture_ids.add(capture_id)
                result.append(
                    CapturedPaneBeatPlan(
                        outer_beat_id=outer.id,
                        pane_id=track.pane_id,
                        capture_id=capture_id,
                        kind=track.kind,
                        beat=pane_beat,
                    )
                )
    return tuple(result)


def capture_runner_beat(
    plan: RecordingPlan,
    capture: CapturedPaneBeatPlan,
) -> OuterBeatPlan:
    """Project one captured pane beat onto the existing runner contract."""

    outer = next(beat for beat in plan.beats if beat.id == capture.outer_beat_id)
    return replace(
        outer,
        id=capture.capture_id,
        narration_text="",
        explicit_narration_take=None,
        viewer_hold_ms=0,
        player_highlight=None,
        guide=None,
        anchors=(),
        waits=(),
        effects=(),
        pane_tracks=(
            OuterPaneTrackPlan(
                pane_id=capture.pane_id,
                kind=capture.kind,
                beats=(capture.beat,),
            ),
        ),
        layout=PaneLayoutPlan(areas=((capture.pane_id,),)),
    )


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
    beats: tuple[OuterBeatPlan, ...],
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
    grouped: dict[str, list[OuterBeatPlan]] = {}
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


def plan_narration_stream(
    beats: tuple[OuterBeatPlan, ...],
    *,
    narration_id: str = "voiceover",
) -> NarrationStreamPlan:
    """Return the logical narration stream independently of physical TTS takes."""

    if not isinstance(narration_id, str) or not ACTION_ID_RE.fullmatch(
        narration_id
    ):
        raise RecordingPlanError(f"narration stream id {narration_id!r} is invalid")
    segments: list[NarrationSegmentPlan] = []
    seen_ids: set[str] = set()
    for beat in beats:
        for index, anchor in enumerate(beat.anchors):
            if anchor.id in seen_ids:
                raise RecordingPlanError(
                    f"duplicate narration segment id {anchor.id!r}"
                )
            seen_ids.add(anchor.id)
            text_end = (
                beat.anchors[index + 1].text_offset
                if index + 1 < len(beat.anchors)
                else len(beat.narration_text)
            )
            segments.append(
                NarrationSegmentPlan(
                    id=anchor.id,
                    beat_id=beat.id,
                    text_start=anchor.text_offset,
                    text_end=text_end,
                )
            )
    return NarrationStreamPlan(id=narration_id, segments=tuple(segments))


def _declared_panes(
    spec: dict[str, Any],
) -> tuple[tuple[PanePlan, ...], dict[str, PaneKind]]:
    raw_panes = spec.get("panes", [])
    if not isinstance(raw_panes, list):
        raise RecordingPlanError("panes must be a list")
    plans: list[PanePlan] = []
    kinds: dict[str, PaneKind] = {}
    for index, raw_pane in enumerate(raw_panes):
        field = f"panes.{index}"
        pane = _typed(_mapping(raw_pane, field=field), PaneConfig, field=field)
        if pane.title is None:
            title = PaneTitleConfig()
        elif pane.title == "hidden":
            title = PaneTitleConfig(visible=False)
        elif isinstance(pane.title, str):
            title = PaneTitleConfig(text=pane.title)
        else:
            title = pane.title
        try:
            plan = PanePlan(
                id=pane.id,
                kind=pane.kind,
                title=PaneTitlePlan(
                    visible=title.visible,
                    text=title.text,
                    alignment_x=title.alignment_x.value,
                    alignment_y=title.alignment_y.value,
                    position_x=title.position_x,
                    position_y=title.position_y,
                ),
            )
        except ValueError as exc:
            raise RecordingPlanError(f"{field}: {exc}") from exc
        if plan.id in kinds:
            raise RecordingPlanError(f"duplicate pane id {plan.id!r}")
        plans.append(plan)
        kinds[plan.id] = plan.kind
    return tuple(plans), kinds


def _pane_transition(
    value: object,
    *,
    field: str,
) -> PaneTransitionPlan:
    mapping = {} if value is None else _mapping(value, field=field)
    transition = _typed(mapping, PaneTransitionConfig, field=field)
    if transition.kind is PaneTransitionKind.cut and transition.duration_ms:
        raise RecordingPlanError(
            f"{field}.duration_ms must be zero for a cut transition"
        )
    if transition.kind is PaneTransitionKind.fade and transition.duration_ms <= 0:
        raise RecordingPlanError(
            f"{field}.duration_ms must be positive for a fade transition"
        )
    return PaneTransitionPlan(
        kind=transition.kind,
        duration_ms=transition.duration_ms,
    )


def _visualization_pane_beat(
    raw: dict[str, Any],
    *,
    field: str,
) -> PaneBeatPlan:
    pane_beat = _typed(raw, PaneBeatConfig, field=field)
    if not ACTION_ID_RE.fullmatch(pane_beat.id):
        raise RecordingPlanError(f"{field}.id is invalid")
    if pane_beat.checks:
        raise RecordingPlanError(
            f"{field} visualization beats cannot contain checks"
        )
    if (
        pane_beat.pointer is not None
        or pane_beat.window is not None
        or pane_beat.chrome is not None
    ):
        raise RecordingPlanError(
            f"{field} visualization beats cannot contain browser presentation fields"
        )
    if len(pane_beat.actions) != 1:
        raise RecordingPlanError(
            f"{field}.actions must contain exactly one visualization show action"
        )
    action = pane_beat.actions[0]
    action_field = f"{field}.actions.0"
    raw_action = _mapping(raw.get("actions", [None])[0], field=action_field)
    present = [
        name
        for name in (*ACTION_KINDS, *TERMINAL_ACTION_ONLY_FIELDS, "show")
        if name in raw_action and not _is_injected_default(raw_action[name])
    ]
    if present != ["show"]:
        raise RecordingPlanError(
            f"{action_field} must contain exactly one visualization show action"
        )
    if not ACTION_ID_RE.fullmatch(action.id):
        raise RecordingPlanError(f"{action_field}.id is invalid")
    if action.show is None:  # pragma: no cover - guarded above
        raise RecordingPlanError(f"{action_field}.show is required")
    try:
        action_plan = VisualizationActionPlan(
            id=action.id,
            language=action.show.language,
            text=action.show.text,
        )
    except ValueError as exc:
        raise RecordingPlanError(f"{action_field}: {exc}") from exc
    return PaneBeatPlan(
        id=pane_beat.id,
        start_join=None,
        recording=VisualizationPaneRecordingPlan(action=action_plan),
        presentation=PanePresentationPlan(),
        transition=_pane_transition(
            raw.get("transition"),
            field=f"{field}.transition",
        ),
    )


def _terminal_pane_beat(
    raw: dict[str, Any],
    *,
    field: str,
    beat_index: int,
    source_dir: Path | None,
    narration_id: str,
    pane_id: str,
    pane_kinds: Mapping[str, PaneKind],
    default_timing: str,
) -> PaneBeatPlan:
    pane_beat = _typed(raw, PaneBeatConfig, field=field)
    if not ACTION_ID_RE.fullmatch(pane_beat.id):
        raise RecordingPlanError(f"{field}.id is invalid")
    if (
        pane_beat.pointer is not None
        or pane_beat.window is not None
        or pane_beat.chrome is not None
    ):
        raise RecordingPlanError(
            f"{field} terminal beats cannot contain browser presentation fields"
        )
    transformed_actions: list[dict[str, Any]] = []
    action_after: list[object] = []
    action_ids: list[str] = []
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raise RecordingPlanError(f"{field}.actions must be a list")
    for action_index, raw_action in enumerate(raw_actions):
        action_field = f"{field}.actions.{action_index}"
        action = dict(_mapping(raw_action, field=action_field))
        action_id = action.pop("id", None)
        if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
            raise RecordingPlanError(f"{action_field}.id is invalid")
        action_ids.append(action_id)
        action_after.append(action.pop("after", None))
        if action.get("commands") is not None:
            raise RecordingPlanError(
                f"{action_field}.commands is not supported in explicit pane beats; "
                "use one identified action per command"
            )
        if action.get("browser_handoff"):
            action["show_prompt_after"] = False
        action["id"] = action_id
        transformed_actions.append({"commands": [action]})
    normalized = normalize_beat_actions(
        {
            "medium": RecordingMedium.terminal.value,
            "actions": transformed_actions,
            "checks": raw.get("checks", []),
        },
        index=beat_index,
        default_timing=_resolved_action_timing(
            raw.get("timing"),
            field=f"{field}.timing",
            default=default_timing,
        ),
    )
    actions = tuple(
        TerminalActionPlan(
            config=freeze_value(
                _resolve_terminal_run_files(action, source_dir=source_dir)
            ),
            start_join=_cross_stream_join(
                action_after[action_index],
                field=f"{field}.actions.{action_index}.after",
                narration_id=narration_id,
                pane_kinds=pane_kinds,
                waiting_pane_id=pane_id,
                waiting_pane_beat_id=pane_beat.id,
                waiting_action_id=action_id,
            ),
        )
        for action_index, (action, action_id) in enumerate(
            zip(normalized.terminal_actions, action_ids, strict=True)
        )
    )
    checks = tuple(
        TerminalCheckPlan(
            config=freeze_value(
                _resolve_terminal_run_files(check, source_dir=source_dir)
            )
        )
        for check in normalized.terminal_checks
    )
    return PaneBeatPlan(
        id=pane_beat.id,
        start_join=None,
        recording=TerminalPaneRecordingPlan(actions=actions, checks=checks),
        presentation=PanePresentationPlan(),
        transition=_pane_transition(
            raw.get("transition"),
            field=f"{field}.transition",
        ),
    )


def _browser_pane_beat(
    raw: dict[str, Any],
    *,
    field: str,
    beat_index: int,
    narration_id: str,
    pane_id: str,
    pane_kinds: Mapping[str, PaneKind],
    browser_config: BrowserRecordingConfig | None,
    default_chrome_mode: str,
    default_timing: str,
) -> PaneBeatPlan:
    pane_beat = _typed(raw, PaneBeatConfig, field=field)
    if not ACTION_ID_RE.fullmatch(pane_beat.id):
        raise RecordingPlanError(f"{field}.id is invalid")
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raise RecordingPlanError(f"{field}.actions must be a list")
    stripped_actions: list[dict[str, Any]] = []
    action_after: list[object] = []
    for action_index, raw_action in enumerate(raw_actions):
        action_field = f"{field}.actions.{action_index}"
        action = dict(_mapping(raw_action, field=action_field))
        action_after.append(action.pop("after", None))
        stripped_actions.append(action)
    normalized = normalize_beat_actions(
        {
            "medium": RecordingMedium.browser.value,
            "actions": stripped_actions,
            "checks": raw.get("checks", []),
        },
        index=beat_index,
        default_timing=_resolved_action_timing(
            raw.get("timing"),
            field=f"{field}.timing",
            default=default_timing,
        ),
    )
    actions = tuple(
        BrowserActionPlan(
            id=action.id,
            kind=_browser_action_kind(action),
            config=freeze_value(action),
            start_join=_cross_stream_join(
                action_after[action_index],
                field=f"{field}.actions.{action_index}.after",
                narration_id=narration_id,
                pane_kinds=pane_kinds,
                waiting_pane_id=pane_id,
                waiting_pane_beat_id=pane_beat.id,
                waiting_action_id=action.id,
            ),
        )
        for action_index, action in enumerate(normalized.browser_actions)
    )
    checks = tuple(
        BrowserCheckPlan(
            name=check.name,
            kind=_browser_check_kind(check),
            config=freeze_value(check),
        )
        for check in normalized.browser_checks
    )
    window = (
        None
        if pane_beat.window is None
        else freeze_value(pane_beat.window)
    )
    chrome = (
        None
        if pane_beat.chrome is None
        else freeze_value(pane_beat.chrome)
    )
    effective_chrome_mode = (
        default_chrome_mode
        if pane_beat.chrome is None
        else pane_beat.chrome.mode
    )
    for action in actions:
        if action.kind != "open_page":
            continue
        payload = action.config["open_page"]
        capture_url = payload.get("url")
        if (
            capture_url is not None
            and not urlsplit(capture_url).scheme
            and (browser_config is None or not browser_config.base_url)
        ):
            raise RecordingPlanError(
                f"relative open_page URL in {action.id!r} requires browser.base_url"
            )
        if effective_chrome_mode == "full" and payload.get("display_url") is None:
            raise RecordingPlanError(
                f"open_page {action.id!r} requires display_url with full chrome"
            )
    return PaneBeatPlan(
        id=pane_beat.id,
        start_join=None,
        recording=BrowserPaneRecordingPlan(actions=actions, checks=checks),
        presentation=PanePresentationPlan(
            browser_pointer_visible=(
                None if pane_beat.pointer is None else pane_beat.pointer.visible
            ),
            browser_window=window,
            browser_chrome=chrome,
        ),
        transition=_pane_transition(
            raw.get("transition"),
            field=f"{field}.transition",
        ),
    )


def _event_ref(
    value: object,
    *,
    field: str,
    narration_id: str,
    pane_kinds: Mapping[str, PaneKind],
) -> EventRef:
    if not isinstance(value, str):
        raise RecordingPlanError(f"{field} must be a fully qualified event")
    parts = value.split(".")
    try:
        if len(parts) == 3 and parts[0] == narration_id:
            return EventRef(
                stream=StreamRef(StreamKind.narration, narration_id),
                action_id=parts[1],
                endpoint=EventEndpoint(parts[2]),
            )
        if len(parts) == 4 and parts[0] in pane_kinds:
            return EventRef(
                stream=StreamRef(StreamKind.pane, parts[0]),
                pane_beat_id=parts[1],
                action_id=parts[2],
                endpoint=EventEndpoint(parts[3]),
            )
    except ValueError as exc:
        raise RecordingPlanError(f"{field}: {exc}") from exc
    raise RecordingPlanError(
        f"{field} must be a fully qualified narration or pane action event"
    )


def _cross_stream_join(
    value: object,
    *,
    field: str,
    narration_id: str,
    pane_kinds: Mapping[str, PaneKind],
    waiting_pane_id: str,
    waiting_pane_beat_id: str,
    waiting_action_id: str | None,
) -> JoinPlan | None:
    if value is None:
        return None
    try:
        return JoinPlan(
            waiting_stream=StreamRef(StreamKind.pane, waiting_pane_id),
            waiting_position=StreamPosition(
                pane_beat_id=waiting_pane_beat_id,
                action_id=waiting_action_id,
            ),
            event=_event_ref(
                value,
                field=field,
                narration_id=narration_id,
                pane_kinds=pane_kinds,
            ),
        )
    except ValueError as exc:
        raise RecordingPlanError(f"{field}: {exc}") from exc


def _explicit_outer_beat(
    beat: dict[str, Any],
    *,
    index: int,
    pane_kinds: Mapping[str, PaneKind],
    narration_id: str,
    narration_entry: dict[str, Any] | None,
    audio_enabled: bool,
    source_dir: Path | None,
    browser_config: BrowserRecordingConfig | None,
    default_browser_chrome_mode: str,
) -> OuterBeatPlan:
    outer_timing = _resolved_action_timing(
        beat.get("timing"),
        field=f"beats.{index}.timing",
    )
    forbidden = [
        name
        for name in (
            "medium",
            "actions",
            "checks",
            "pointer",
            "window",
            "chrome",
        )
        if name in beat and not _is_injected_default(beat[name])
    ]
    if forbidden:
        raise RecordingPlanError(
            f"beats.{index} cannot mix explicit panes with single-pane fields: "
            f"{', '.join(forbidden)}"
        )
    raw_tracks = _mapping(beat.get("panes"), field=f"beats.{index}.panes")
    if not raw_tracks:
        raise RecordingPlanError(f"beats.{index}.panes must be non-empty")
    unknown_tracks = sorted(set(raw_tracks) - set(pane_kinds))
    if unknown_tracks:
        raise RecordingPlanError(
            f"beats.{index}.panes references unknown pane(s): "
            f"{', '.join(unknown_tracks)}"
        )
    layout_mapping = _mapping(beat.get("layout"), field=f"beats.{index}.layout")
    layout = _typed(
        layout_mapping,
        PaneLayoutConfig,
        field=f"beats.{index}.layout",
    )
    if not layout.areas or not layout.areas[0]:
        raise RecordingPlanError(f"beats.{index}.layout.areas must be non-empty")
    width = len(layout.areas[0])
    if any(not row or len(row) != width for row in layout.areas):
        raise RecordingPlanError(
            f"beats.{index}.layout.areas must be a non-empty rectangular grid"
        )
    layout_ids = {pane_id for row in layout.areas for pane_id in row}
    if layout_ids != set(raw_tracks):
        raise RecordingPlanError(
            f"beats.{index}.layout and panes must reference the same pane ids"
        )

    narration_text, anchors, waits = _beat_narration(beat, narration_entry)
    if waits:
        raise RecordingPlanError(
            f"beats.{index} narration waits are not supported in explicit "
            "multi-pane beats"
        )
    anchor_ids = {anchor.id for anchor in anchors}
    tracks: list[OuterPaneTrackPlan] = []
    track_order = list(dict.fromkeys(pane_id for row in layout.areas for pane_id in row))
    for pane_id in track_order:
        raw_pane_beats = raw_tracks[pane_id]
        track_field = f"beats.{index}.panes.{pane_id}"
        if not isinstance(raw_pane_beats, list) or not raw_pane_beats:
            raise RecordingPlanError(f"{track_field} must be a non-empty list")
        kind = pane_kinds[pane_id]
        pane_beats: list[PaneBeatPlan] = []
        for pane_beat_index, raw_pane_beat_value in enumerate(raw_pane_beats):
            pane_beat_field = f"{track_field}.{pane_beat_index}"
            raw_pane_beat = _mapping(
                raw_pane_beat_value,
                field=pane_beat_field,
            )
            if kind is PaneKind.visualization:
                pane_beat = _visualization_pane_beat(
                    raw_pane_beat,
                    field=pane_beat_field,
                )
            elif kind is PaneKind.terminal:
                pane_beat = _terminal_pane_beat(
                    raw_pane_beat,
                    field=pane_beat_field,
                    beat_index=index,
                    source_dir=source_dir,
                    narration_id=narration_id,
                    pane_id=pane_id,
                    pane_kinds=pane_kinds,
                    default_timing=outer_timing,
                )
            elif kind is PaneKind.browser:
                pane_beat = _browser_pane_beat(
                    raw_pane_beat,
                    field=pane_beat_field,
                    beat_index=index,
                    narration_id=narration_id,
                    pane_id=pane_id,
                    pane_kinds=pane_kinds,
                    browser_config=browser_config,
                    default_chrome_mode=default_browser_chrome_mode,
                    default_timing=outer_timing,
                )
            else:  # pragma: no cover - guarded by PaneKind
                raise RecordingPlanError(f"{track_field} pane kind is unsupported")
            if (
                pane_beat_index == 0
                and pane_beat.transition != PaneTransitionPlan()
            ):
                raise RecordingPlanError(
                    f"{pane_beat_field}.transition is only valid between pane beats"
                )
            start_join = _cross_stream_join(
                raw_pane_beat.get("after"),
                field=f"{pane_beat_field}.after",
                narration_id=narration_id,
                pane_kinds=pane_kinds,
                waiting_pane_id=pane_id,
                waiting_pane_beat_id=pane_beat.id,
                waiting_action_id=None,
            )
            pane_beats.append(replace(pane_beat, start_join=start_join))
        tracks.append(
            OuterPaneTrackPlan(
                pane_id=pane_id,
                kind=kind,
                beats=tuple(pane_beats),
            )
        )

    effects = _text_highlights(
        beat,
        index=index,
        anchors=anchors,
        pane_kinds={
            pane_id: pane_kinds[pane_id]
            for pane_id in raw_tracks
        },
        default_pane_id=None,
    )
    if effects and not audio_enabled:
        raise RecordingPlanError(
            f"beats.{index} text highlight effects require audio.enabled=true"
        )
    if (
        any(
            join is not None
            and join.event.stream.kind is StreamKind.narration
            for track in tracks
            for pane_beat in track.beats
            for join in (
                pane_beat.start_join,
                *(getattr(action, "start_join", None) for action in pane_beat.actions),
            )
        )
        and not audio_enabled
    ):
        raise RecordingPlanError(
            f"beats.{index} narration joins require audio.enabled=true"
        )
    player_highlight = _player_toolbar_highlight(
        beat,
        index=index,
        anchors=anchors,
    )
    if player_highlight is not None and not audio_enabled:
        raise RecordingPlanError(
            f"beats.{index}.player.highlight requires audio.enabled=true"
        )
    viewer_hold = beat.get("viewer_hold")
    if viewer_hold is None and narration_entry is not None:
        viewer_hold = narration_entry.get("viewer_hold")
    if viewer_hold is None:
        viewer_hold_ms = 0
    elif isinstance(viewer_hold, bool) or not isinstance(viewer_hold, (int, float)):
        raise RecordingPlanError(f"beat {beat['id']!r} viewer_hold must be a number")
    elif viewer_hold < 0:
        raise RecordingPlanError(
            f"beat {beat['id']!r} viewer_hold must be non-negative"
        )
    else:
        viewer_hold_ms = round(float(viewer_hold) * 1000)
    explicit_take = beat.get("narration_take")
    if explicit_take is not None and (
        not isinstance(explicit_take, str) or not ACTION_ID_RE.fullmatch(explicit_take)
    ):
        raise RecordingPlanError(f"beat {beat['id']!r} narration_take is invalid")
    heading = beat.get("heading")
    if not heading and narration_entry is not None:
        heading = narration_entry.get("heading", "")
    if heading is None:
        heading = ""
    if not isinstance(heading, str):
        raise RecordingPlanError(f"beat {beat['id']!r} heading must be a string")
    caption = beat.get("caption", "")
    if caption is None:
        caption = ""
    if not isinstance(caption, str):
        raise RecordingPlanError(f"beat {beat['id']!r} caption must be a string")
    guide_value = beat.get("guide")
    guide = freeze_value(guide_value) if isinstance(guide_value, dict) else None
    return OuterBeatPlan(
        id=beat["id"],
        heading=heading,
        caption=caption,
        narration_text=narration_text,
        explicit_narration_take=explicit_take,
        viewer_hold_ms=viewer_hold_ms,
        player_highlight=player_highlight,
        guide=guide,
        anchors=anchors,
        waits=(),
        effects=effects,
        pane_tracks=tuple(tracks),
        layout=PaneLayoutPlan(
            areas=tuple(tuple(row) for row in layout.areas)
        ),
    )


def pane_action_id(
    action: TerminalActionPlan | BrowserActionPlan | VisualizationActionPlan,
    action_index: int,
) -> str:
    """Return the authored action identity used by pane events and joins."""

    if isinstance(action, (BrowserActionPlan, VisualizationActionPlan)):
        return action.id
    commands = action.config.get("commands")
    if commands:
        command = commands[0]
        action_id = command.get("id")
        if isinstance(action_id, str) and action_id:
            return action_id
    return terminal_action_id(action_index, None, action.config)


def _pane_action_event_id(
    pane_id: str,
    pane_beat_id: str,
    action_id: str,
    endpoint: EventEndpoint,
) -> str:
    return f"{pane_id}.{pane_beat_id}.{action_id}.{endpoint.value}"


def _pane_joins(pane_beat: PaneBeatPlan) -> tuple[JoinPlan, ...]:
    return tuple(
        join
        for join in (
            pane_beat.start_join,
            *(getattr(action, "start_join", None) for action in pane_beat.actions),
        )
        if join is not None
    )


def _validate_explicit_event_graph(
    beats: tuple[OuterBeatPlan, ...],
    *,
    pane_kinds: Mapping[str, PaneKind],
    narration_event_ids: set[str],
    browser_handoffs: tuple[BrowserHandoffPlan, ...],
) -> None:
    event_ids = set(narration_event_ids)
    action_nodes: dict[tuple[str, str, str], tuple[str, str]] = {}
    pane_sequences: dict[str, list[tuple[str, str]]] = {
        pane_id: [] for pane_id in pane_kinds
    }
    pane_beat_sequences: dict[
        tuple[str, str, str], list[tuple[str, str]]
    ] = {}
    joins: list[JoinPlan] = []
    first_browser_actions: dict[str, BrowserActionPlan] = {}

    for outer in beats:
        for track in outer.pane_tracks:
            for pane_beat in track.beats:
                joins.extend(_pane_joins(pane_beat))
                for action_index, action in enumerate(pane_beat.actions):
                    action_id = pane_action_id(action, action_index)
                    key = (track.pane_id, pane_beat.id, action_id)
                    if key in action_nodes:
                        raise RecordingPlanError(
                            "duplicate pane action event identity "
                            f"{track.pane_id}.{pane_beat.id}.{action_id}"
                        )
                    started = _pane_action_event_id(
                        track.pane_id,
                        pane_beat.id,
                        action_id,
                        EventEndpoint.started,
                    )
                    ended = _pane_action_event_id(
                        track.pane_id,
                        pane_beat.id,
                        action_id,
                        EventEndpoint.ended,
                    )
                    action_nodes[key] = (started, ended)
                    pane_sequences[track.pane_id].append((started, ended))
                    pane_beat_sequences.setdefault(
                        (outer.id, track.pane_id, pane_beat.id), []
                    ).append((started, ended))
                    event_ids.update((started, ended))
                    if (
                        track.kind is PaneKind.browser
                        and track.pane_id not in first_browser_actions
                        and isinstance(action, BrowserActionPlan)
                    ):
                        first_browser_actions[track.pane_id] = action

    for pane_id, action in first_browser_actions.items():
        if action.kind != "open_page":
            raise RecordingPlanError(
                f"the first browser action in pane {pane_id!r} must be open_page"
            )

    for join in joins:
        if join.event.qualified_id not in event_ids:
            raise RecordingPlanError(
                f"unknown event {join.event.qualified_id!r}"
            )

    captured = {
        pane_id
        for pane_id, kind in pane_kinds.items()
        if kind in {PaneKind.terminal, PaneKind.browser}
    }
    adjacency: dict[str, set[str]] = {}

    def edge(before: str, after: str) -> None:
        adjacency.setdefault(before, set()).add(after)
        adjacency.setdefault(after, set())

    for pane_id in captured:
        previous_end: str | None = None
        for started, ended in pane_sequences[pane_id]:
            edge(started, ended)
            if previous_end is not None:
                edge(previous_end, started)
            previous_end = ended

    for outer in beats:
        for track in outer.pane_tracks:
            if track.pane_id not in captured:
                continue
            for pane_beat in track.beats:
                actions = tuple(
                    (pane_action_id(action, action_index), action)
                    for action_index, action in enumerate(pane_beat.actions)
                )
                if (
                    pane_beat.start_join is not None
                    and pane_beat.start_join.event.stream.kind is StreamKind.pane
                    and pane_beat.start_join.event.stream.id in captured
                    and actions
                ):
                    edge(
                        pane_beat.start_join.event.qualified_id,
                        _pane_action_event_id(
                            track.pane_id,
                            pane_beat.id,
                            actions[0][0],
                            EventEndpoint.started,
                        ),
                    )
                for action_id, action in actions:
                    join = getattr(action, "start_join", None)
                    if (
                        join is None
                        or join.event.stream.kind is not StreamKind.pane
                        or join.event.stream.id not in captured
                    ):
                        continue
                    edge(
                        join.event.qualified_id,
                        _pane_action_event_id(
                            track.pane_id,
                            pane_beat.id,
                            action_id,
                            EventEndpoint.started,
                        ),
                    )

    for handoff in browser_handoffs:
        producer_prefix = (
            f"{handoff.producer_pane_id}."
            f"{handoff.producer_pane_beat_id}.{handoff.id}"
        )
        consumer_prefix = (
            f"{handoff.target_pane_id}."
            f"{handoff.consumer_pane_beat_id}."
            f"{handoff.consumer_action_id}"
        )
        consumer_sequence = pane_beat_sequences[
            (
                handoff.consumer_outer_beat_id,
                handoff.target_pane_id,
                handoff.consumer_pane_beat_id,
            )
        ]
        edge(f"{producer_prefix}.started", f"{consumer_prefix}.started")
        edge(consumer_sequence[-1][1], f"{producer_prefix}.ended")

    indegree = {node: 0 for node in adjacency}
    for successors in adjacency.values():
        for successor in successors:
            indegree[successor] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for successor in adjacency[node]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if visited != len(adjacency):
        cycle_nodes = sorted(node for node, degree in indegree.items() if degree)
        raise RecordingPlanError(
            "capture dependency cycle: " + " -> ".join(cycle_nodes)
        )


def normalize_recording_plan(spec: dict[str, Any]) -> RecordingPlan:
    """Validate cross-references and return a deeply immutable execution plan."""

    validate_recording_modalities(spec)
    recording_id = spec.get("id")
    if not isinstance(recording_id, str) or not recording_id:
        raise RecordingPlanError("recording.id must be a non-empty string")
    title = spec.get("title")
    if title is not None and not isinstance(title, str):
        raise RecordingPlanError("recording.title must be a string")

    pane_plans, pane_kinds = _declared_panes(spec)
    raw_beats = spec.get("beats", [])
    if not isinstance(raw_beats, list):
        raise RecordingPlanError("beats must be a list")
    narration_by_beat = _narration_by_beat(spec)
    narration_mapping = spec.get("narration", {})
    narration_id = (
        narration_mapping.get("id", "voiceover")
        if isinstance(narration_mapping, dict)
        else "voiceover"
    )
    seen_beat_ids: set[str] = set()
    seen_browser_action_ids: set[str] = set()
    first_browser_action_seen = False
    beat_plans: list[OuterBeatPlan] = []
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
        if pane_plans:
            if not beat.get("panes"):
                raise RecordingPlanError(
                    f"beats.{index} must define explicit panes because the recording "
                    "declares pane streams"
                )
            beat_plans.append(
                _explicit_outer_beat(
                    beat,
                    index=index,
                    pane_kinds=pane_kinds,
                    narration_id=narration_id,
                    narration_entry=narration_by_beat.get(beat_id),
                    audio_enabled=audio_enabled,
                    source_dir=source_dir,
                    browser_config=browser_config,
                    default_browser_chrome_mode=(
                        presentation.browser.chrome.mode
                    ),
                )
            )
            continue
        if beat.get("panes") or beat.get("layout"):
            raise RecordingPlanError(
                f"beats.{index} explicit panes require top-level pane declarations"
            )
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
        text_highlights = _text_highlights(
            beat,
            index=index,
            anchors=anchors,
            pane_kinds={"main": PaneKind(medium.value)},
            default_pane_id="main",
        )
        player_highlight = _player_toolbar_highlight(
            beat,
            index=index,
            anchors=anchors,
        )
        if text_highlights and not audio_enabled:
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
        if medium is RecordingMedium.browser:
            recording: PaneRecordingPlan = BrowserPaneRecordingPlan(
                actions=tuple(
                    action
                    for action in actions
                    if isinstance(action, BrowserActionPlan)
                ),
                checks=tuple(
                    check
                    for check in checks
                    if isinstance(check, BrowserCheckPlan)
                ),
            )
        else:
            recording = TerminalPaneRecordingPlan(
                actions=tuple(
                    action
                    for action in actions
                    if isinstance(action, TerminalActionPlan)
                ),
                checks=tuple(
                    check
                    for check in checks
                    if isinstance(check, TerminalCheckPlan)
                ),
            )
        pane_beat = PaneBeatPlan(
            id=beat_id,
            start_join=None,
            recording=recording,
            presentation=PanePresentationPlan(
                browser_pointer_visible=(
                    None if pointer_config is None else pointer_config.visible
                ),
                browser_window=(
                    None if window_config is None else freeze_value(window_config)
                ),
                browser_chrome=(
                    None if chrome_config is None else freeze_value(chrome_config)
                ),
            ),
            transition=PaneTransitionPlan(),
        )
        beat_plans.append(
            OuterBeatPlan(
                id=beat_id,
                heading=heading,
                caption=caption,
                narration_text=narration_text,
                explicit_narration_take=explicit_take,
                viewer_hold_ms=viewer_hold_ms,
                player_highlight=player_highlight,
                guide=guide,
                anchors=anchors,
                waits=waits,
                effects=text_highlights,
                pane_tracks=(
                    OuterPaneTrackPlan(
                        pane_id="main",
                        kind=PaneKind(medium.value),
                        beats=(pane_beat,),
                    ),
                ),
                layout=PaneLayoutPlan(areas=(("main",),)),
            )
        )

    unknown_narration_beats = set(narration_by_beat) - seen_beat_ids
    if unknown_narration_beats:
        unknown = ", ".join(sorted(unknown_narration_beats))
        raise RecordingPlanError(
            f"internal narration references unknown beat(s): {unknown}"
        )

    frozen_beats = tuple(beat_plans)
    narration_stream = plan_narration_stream(
        frozen_beats,
        narration_id=narration_id,
    )
    narration_event_ids = {
        f"{narration_stream.id}.{segment.id}.{endpoint.value}"
        for segment in narration_stream.segments
        for endpoint in EventEndpoint
    }
    browser_handoffs = _plan_browser_handoffs(frozen_beats)
    if pane_plans:
        used_pane_ids = {
            track.pane_id
            for beat in frozen_beats
            for track in beat.pane_tracks
        }
        unused_pane_ids = sorted(set(pane_kinds) - used_pane_ids)
        if unused_pane_ids:
            raise RecordingPlanError(
                "declared panes are not used: " + ", ".join(unused_pane_ids)
            )
        pane_beat_ids = {pane_id: set() for pane_id in pane_kinds}
        for beat in frozen_beats:
            for track in beat.pane_tracks:
                for pane_beat in track.beats:
                    if pane_beat.id in pane_beat_ids[track.pane_id]:
                        raise RecordingPlanError(
                            f"duplicate pane beat id {pane_beat.id!r} in pane "
                            f"{track.pane_id!r} across recording"
                        )
                    pane_beat_ids[track.pane_id].add(pane_beat.id)
        _validate_explicit_event_graph(
            frozen_beats,
            pane_kinds=pane_kinds,
            narration_event_ids=narration_event_ids,
            browser_handoffs=browser_handoffs,
        )
    plan = RecordingPlan(
        id=recording_id,
        title=title,
        browser=freeze_value(browser_config) if browser_config is not None else None,
        presentation=freeze_value(presentation),
        setup=lifecycle_steps["setup"],
        panes=pane_plans,
        beats=frozen_beats,
        cleanup=lifecycle_steps["cleanup"],
        narration_stream=narration_stream,
        narration_takes=plan_narration_takes(frozen_beats),
        browser_handoffs=browser_handoffs,
    )
    _validate_recording_plan_limits(plan)
    return plan


def _validate_recording_plan_limits(plan: RecordingPlan) -> None:
    pane_count = len(plan.panes) or 1
    if pane_count > PRESENTATION_PANE_LIMIT:
        raise RecordingPlanError(
            f"recording panes exceeds {PRESENTATION_PANE_LIMIT} entries"
        )

    item_count = (
        len(plan.setup)
        + len(plan.cleanup)
        + len(plan.beats)
        + len(plan.narration_stream.segments)
    )
    for beat in plan.beats:
        item_count += sum(len(row) for row in beat.layout.areas)
        item_count += len(beat.anchors) + len(beat.waits) + len(beat.pane_tracks)
        for track in beat.pane_tracks:
            item_count += len(track.beats)
            for pane_beat in track.beats:
                item_count += (
                    len(pane_beat.actions)
                    + len(pane_beat.checks)
                )
                for action in pane_beat.actions:
                    if not isinstance(action, TerminalActionPlan):
                        continue
                    commands = action.config.get("commands") or ()
                    item_count += len(commands)
                    for command in commands:
                        item_count += len(command.get("input") or ())
    if item_count > PRESENTATION_ITEM_LIMIT:
        raise RecordingPlanError(
            f"recording aggregate structure exceeds {PRESENTATION_ITEM_LIMIT} entries"
        )


def _plan_browser_handoffs(
    beats: tuple[OuterBeatPlan, ...],
) -> tuple[BrowserHandoffPlan, ...]:
    browser_pane_ids = sorted(
        {
            track.pane_id
            for beat in beats
            for track in beat.pane_tracks
            if track.kind is PaneKind.browser
        }
    )
    all_pane_ids = {
        track.pane_id
        for beat in beats
        for track in beat.pane_tracks
    }
    consumers: dict[
        str,
        list[tuple[str, str, str, str]],
    ] = {}
    producers: list[
        tuple[str, str, str, int, int, Mapping[str, Any]]
    ] = []

    for beat in beats:
        for track in beat.pane_tracks:
            for pane_beat in track.beats:
                for action_index, action in enumerate(pane_beat.actions):
                    if isinstance(action, BrowserActionPlan):
                        if action.kind != "open_page":
                            continue
                        handoff_id = action.config["open_page"].get("handoff")
                        if handoff_id is not None:
                            consumers.setdefault(str(handoff_id), []).append(
                                (
                                    beat.id,
                                    track.pane_id,
                                    pane_beat.id,
                                    action.id,
                                )
                            )
                        continue
                    if not isinstance(action, TerminalActionPlan):
                        continue
                    commands = action.config.get("commands") or ()
                    for command_index, command in enumerate(commands):
                        if command.get("browser_handoff"):
                            producers.append(
                                (
                                    beat.id,
                                    track.pane_id,
                                    pane_beat.id,
                                    action_index,
                                    command_index,
                                    command,
                                )
                            )

    producer_ids: set[str] = set()
    planned: list[BrowserHandoffPlan] = []
    consumed_actions: set[tuple[str, str, str, str]] = set()
    for (
        outer_beat_id,
        producer_pane_id,
        producer_pane_beat_id,
        action_index,
        command_index,
        command,
    ) in producers:
        command_id = command.get("id")
        if not isinstance(command_id, str) or not command_id:
            raise RecordingPlanError(
                "browser_handoff command requires an explicit id"
            )
        if command_id in producer_ids:
            raise RecordingPlanError(
                f"duplicate browser_handoff id {command_id!r}"
            )
        producer_ids.add(command_id)
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
            raise RecordingPlanError(
                "browser_handoff command requires real output"
            )

        producer_track = next(
            track
            for beat in beats
            if beat.id == outer_beat_id
            for track in beat.pane_tracks
            if track.pane_id == producer_pane_id
        )
        producer_pane_beat = next(
            pane_beat
            for pane_beat in producer_track.beats
            if pane_beat.id == producer_pane_beat_id
        )
        final_action = producer_pane_beat.actions[-1]
        final_commands = (
            final_action.config.get("commands")
            if isinstance(final_action, TerminalActionPlan)
            else None
        )
        if (
            action_index != len(producer_pane_beat.actions) - 1
            or not final_commands
            or command_index != len(final_commands) - 1
        ):
            raise RecordingPlanError(
                "browser_handoff command must be the last command in its "
                "terminal pane beat"
            )

        raw_handoff = command["browser_handoff"]
        if raw_handoff is True:
            if len(browser_pane_ids) != 1:
                targets = ", ".join(browser_pane_ids) or "none"
                raise RecordingPlanError(
                    "browser_handoff target is ambiguous; specify target "
                    f"explicitly (eligible browser panes: {targets})"
                )
            target_pane_id = browser_pane_ids[0]
        elif isinstance(raw_handoff, Mapping):
            target = raw_handoff.get("target")
            if not isinstance(target, str) or not target:
                raise RecordingPlanError(
                    "browser_handoff.target must be a non-empty pane id"
                )
            target_pane_id = target
        else:  # pragma: no cover - rejected by structured command validation
            raise RecordingPlanError(
                "browser_handoff must be true or contain a target"
            )

        if target_pane_id not in all_pane_ids:
            raise RecordingPlanError(
                f"browser_handoff target {target_pane_id!r} is not a declared pane"
            )
        if target_pane_id not in browser_pane_ids:
            raise RecordingPlanError(
                f"browser_handoff target {target_pane_id!r} is not a browser pane"
            )
        matching_consumers = [
            consumer
            for consumer in consumers.get(command_id, ())
            if consumer[1] == target_pane_id
        ]
        if len(matching_consumers) != 1:
            raise RecordingPlanError(
                f"browser_handoff {command_id!r} target {target_pane_id!r} "
                "does not consume exactly one matching open_page action"
            )
        consumer = matching_consumers[0]
        consumed_actions.add(consumer)
        planned.append(
            BrowserHandoffPlan(
                id=command_id,
                producer_outer_beat_id=outer_beat_id,
                producer_pane_id=producer_pane_id,
                producer_pane_beat_id=producer_pane_beat_id,
                target_pane_id=target_pane_id,
                consumer_outer_beat_id=consumer[0],
                consumer_pane_beat_id=consumer[2],
                consumer_action_id=consumer[3],
            )
        )

    for handoff_id, handoff_consumers in consumers.items():
        for consumer in handoff_consumers:
            if consumer not in consumed_actions:
                raise RecordingPlanError(
                    f"open_page handoff {handoff_id!r} has no matching "
                    "browser_handoff producer targeting its pane"
                )
    return tuple(planned)
