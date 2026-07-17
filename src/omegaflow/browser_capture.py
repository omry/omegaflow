"""Persistent Playwright browser capture runner."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from .browser_runtime import (
    BrowserRuntimeError,
    actionable_playwright_error,
    pinned_browser_runtime,
)
from .browser_visuals import BrowserVisualCapture, BrowserVisualError
from .capture import BeatCapture, CaptureContext
from .recording_plan import (
    BeatPlan,
    BrowserActionPlan,
    BrowserCheckPlan,
    FrozenMapping,
)
from .studio_config import RecordingMedium


DESKTOP_VIEWPORT_WIDTH = 1440
DESKTOP_VIEWPORT_HEIGHT = 900
DESKTOP_DEVICE_SCALE_FACTOR = 1.0
DESKTOP_LOCALE = "en-US"
DESKTOP_TIMEZONE = "UTC"
DESKTOP_COLOR_SCHEME = "light"
DESKTOP_REDUCED_MOTION = "reduce"


class BrowserCaptureError(RuntimeError):
    """Browser capture failure with a stable user-facing code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class BrowserAuthentication:
    """Resolved private storage state identity."""

    storage_state: Mapping[str, Any] | None
    content_sha256: str | None


@dataclass(frozen=True)
class ResolvedBrowserProfile:
    """Complete deterministic values materialized from desktop-v1."""

    name: str
    browser_engine: str
    browser_revision: str
    browser_version: str
    headless: bool
    viewport_width: int
    viewport_height: int
    screen_width: int
    screen_height: int
    device_scale_factor: float
    locale: str
    timezone: str
    color_scheme: str
    reduced_motion: str
    permissions: tuple[str, ...]
    user_agent: str
    is_mobile: bool
    has_touch: bool
    audio_muted: bool
    auth_state_sha256: str | None


@dataclass(frozen=True)
class ResponseObservation:
    """Private in-memory response fact used by waits and checks."""

    seq: int
    url: str
    method: str
    status: int


@dataclass(frozen=True)
class BrowserWarning:
    code: str
    beat_id: str
    action_id: str | None = None


class SecretRegistry:
    """In-memory exact-value registry for private diagnostic scrubbing."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    @property
    def values(self) -> frozenset[str]:
        return frozenset(self._values)

    def register(self, value: str) -> None:
        if value:
            self._values.add(value)

    def register_storage_state(self, value: Mapping[str, Any] | None) -> None:
        if value is None:
            return
        cookies = value.get("cookies", [])
        if isinstance(cookies, list):
            for cookie in cookies:
                if isinstance(cookie, dict) and isinstance(cookie.get("value"), str):
                    self.register(cookie["value"])
        origins = value.get("origins", [])
        if isinstance(origins, list):
            for origin in origins:
                if not isinstance(origin, dict):
                    continue
                storage = origin.get("localStorage", [])
                if not isinstance(storage, list):
                    continue
                for item in storage:
                    if isinstance(item, dict) and isinstance(item.get("value"), str):
                        self.register(item["value"])

    def scrub(self, text: str) -> str:
        result = text
        for value in sorted(self._values, key=len, reverse=True):
            result = result.replace(value, "[REDACTED]")
        return result


def _thaw(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BrowserCaptureError("BROWSER_SCHEMA", f"{field} must be a mapping")
    return value


def _target_description(target: Mapping[str, Any], family: str) -> str:
    parts = [f"{family}={target[family]!r}"]
    if family == "role" and target.get("name") is not None:
        parts.append(f"name={target['name']!r}")
    if target.get("exact") is True:
        parts.append("exact=true")
    return ", ".join(parts)


def _target_context(beat_id: str, action_id: str | None) -> str:
    if beat_id and action_id:
        return f"browser beat {beat_id!r}, action {action_id!r}"
    if beat_id:
        return f"browser beat {beat_id!r}"
    if action_id:
        return f"browser action {action_id!r}"
    return "browser target"


def resolve_browser_authentication(
    config: Mapping[str, Any], context: CaptureContext
) -> BrowserAuthentication:
    auth = _mapping(config.get("auth", {}), field="browser.auth")
    env_name = auth.get("storage_state_env")
    configured_path = auth.get("storage_state_path")
    if env_name and configured_path:
        raise BrowserCaptureError(
            "BROWSER_SCHEMA",
            "browser.auth storage_state_env and storage_state_path are mutually exclusive",
        )
    if env_name:
        if not isinstance(env_name, str):
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser.auth.storage_state_env must be a string"
            )
        configured_path = context.environment.get(env_name)
        if not configured_path:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                f"browser authentication environment variable {env_name!r} is not set",
            )
    if configured_path is None:
        return BrowserAuthentication(storage_state=None, content_sha256=None)
    if not isinstance(configured_path, str) or not configured_path:
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "browser auth storage-state path must be non-empty"
        )
    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = context.working_directory / path
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "could not read browser storage state"
        ) from exc
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "browser storage state is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "browser storage state must be a mapping"
        )
    return BrowserAuthentication(
        storage_state=value,
        content_sha256=hashlib.sha256(content).hexdigest(),
    )


class PersistentBrowserRunner:
    """Own one pinned Chromium browser, context, and page for a recording."""

    def __init__(
        self,
        browser_config: Mapping[str, Any],
        *,
        headless: bool = True,
    ) -> None:
        self.browser_config = _thaw(browser_config)
        self.headless = headless
        self.capture_context: CaptureContext | None = None
        self.profile: ResolvedBrowserProfile | None = None
        self.authentication: BrowserAuthentication | None = None
        self.playwright: Any = None
        self.browser: Any = None
        self.browser_context: Any = None
        self.page: Any = None
        self.video: Any = None
        self.visuals: BrowserVisualCapture | None = None
        self.initial_visual_state: dict[str, Any] | None = None
        self.responses: list[ResponseObservation] = []
        self.warnings: list[BrowserWarning] = []
        self.secrets = SecretRegistry()
        self.capture_log_path: Path | None = None
        self.console_log_path: Path | None = None
        self.network_log_path: Path | None = None
        self.page_error_log_path: Path | None = None
        self._capture_seq = 0
        self._diagnostic_seq = 0
        self._warned_external_origins: set[str] = set()
        self._active_secret_redactions: tuple[Mapping[str, Any], ...] = ()
        self._current_beat_id = ""
        self._current_action_id: str | None = None
        self._video_origin_ns = 0
        self._beat_start_ns = 0
        self._closed = False
        self._close_succeeded = False
        self._completed = False
        self._capture_failed = False
        self._handoff_urls: dict[str, str] = {}

    def set_handoff_url(self, handoff_id: str, url: str) -> None:
        if not handoff_id or handoff_id in self._handoff_urls:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                f"browser handoff {handoff_id!r} is invalid or duplicated",
            )
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                f"browser handoff {handoff_id!r} has an invalid URL",
            )
        self._handoff_urls[handoff_id] = url

    def start(self, context: CaptureContext) -> None:
        if self.page is not None:
            return
        if self._closed:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser capture runner is already closed"
            )
        try:
            runtime = pinned_browser_runtime()
        except BrowserRuntimeError as exc:
            raise BrowserCaptureError("BROWSER_SCHEMA", str(exc)) from exc
        config = _mapping(self.browser_config, field="browser")
        if config.get("profile", "desktop-v1") != "desktop-v1":
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser.profile must be desktop-v1"
            )
        resolved = _resolved_context_values(config)
        authentication = resolve_browser_authentication(config, context)
        fragments = context.paths.capture / "fragments"
        _prepare_private_browser_directory(fragments)

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                "browser recording requires the browser extra: "
                "install OmegaFlow with `pip install 'omegaflow[browser]'`",
            ) from exc

        self.capture_context = context
        self.authentication = authentication
        self.secrets.register_storage_state(authentication.storage_state)
        self.capture_log_path = context.paths.capture / "browser.capture.jsonl"
        self.console_log_path = context.paths.diagnostics / "console.jsonl"
        self.network_log_path = context.paths.diagnostics / "network.jsonl"
        self.page_error_log_path = context.paths.diagnostics / "page-errors.jsonl"
        for path in (
            self.capture_log_path,
            self.console_log_path,
            self.network_log_path,
            self.page_error_log_path,
        ):
            if path.exists() or path.is_symlink():
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA",
                    f"private browser artifact already exists: {path.name}",
                )
            path.open("x", encoding="utf-8").close()
            path.chmod(0o600)
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--autoplay-policy=no-user-gesture-required",
                    "--mute-audio",
                ],
                env=dict(context.environment),
            )
            self.browser_context = self.browser.new_context(
                viewport={
                    "width": resolved["viewport_width"],
                    "height": resolved["viewport_height"],
                },
                screen={
                    "width": resolved["viewport_width"],
                    "height": resolved["viewport_height"],
                },
                device_scale_factor=resolved["device_scale_factor"],
                locale=resolved["locale"],
                timezone_id=resolved["timezone"],
                color_scheme=resolved["color_scheme"],
                reduced_motion=resolved["reduced_motion"],
                permissions=list(resolved["permissions"]),
                is_mobile=False,
                has_touch=False,
                storage_state=authentication.storage_state,
                record_video_dir=str(fragments),
                record_video_size={
                    "width": math.ceil(
                        resolved["viewport_width"] * resolved["device_scale_factor"]
                    ),
                    "height": math.ceil(
                        resolved["viewport_height"] * resolved["device_scale_factor"]
                    ),
                },
            )
            self.page = self.browser_context.new_page()
            self.video = self.page.video
            self._calibrate_video_origin()
            self.page.on("response", self._observe_response)
            self.page.on("console", self._observe_console)
            self.page.on("pageerror", self._observe_page_error)
            self.page.on("requestfailed", self._observe_request_failure)
            timeouts = _mapping(config.get("timeouts", {}), field="browser.timeouts")
            self.page.set_default_timeout(int(timeouts.get("action_ms", 10_000)))
            self.page.set_default_navigation_timeout(
                int(timeouts.get("readiness_ms", 15_000))
            )
            user_agent = self.page.evaluate("navigator.userAgent")
            if not isinstance(user_agent, str) or not user_agent:
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", "pinned browser returned an invalid user agent"
                )
            self.profile = ResolvedBrowserProfile(
                name="desktop-v1",
                browser_engine="chromium",
                browser_revision=runtime.chromium_revision,
                browser_version=runtime.chromium_version,
                headless=self.headless,
                viewport_width=resolved["viewport_width"],
                viewport_height=resolved["viewport_height"],
                screen_width=resolved["viewport_width"],
                screen_height=resolved["viewport_height"],
                device_scale_factor=resolved["device_scale_factor"],
                locale=resolved["locale"],
                timezone=resolved["timezone"],
                color_scheme=resolved["color_scheme"],
                reduced_motion=resolved["reduced_motion"],
                permissions=resolved["permissions"],
                user_agent=user_agent,
                is_mobile=False,
                has_touch=False,
                audio_muted=True,
                auth_state_sha256=authentication.content_sha256,
            )
            redactions = tuple(
                _mapping(redaction, field="browser.redactions")["target"]
                for redaction in config.get("redactions", [])
            )
            self.visuals = BrowserVisualCapture(
                self.page,
                run_dir=context.paths.run,
                states_dir=context.paths.capture / "states",
                fragments_dir=fragments,
                diagnostics_dir=context.paths.diagnostics / "stability",
                redaction_targets=redactions,
                locator_factory=self._locator_without_count,
            )
            pristine = self.page.evaluate(
                """() => location.href === 'about:blank' &&
                  document.body !== null && document.body.childElementCount === 0 &&
                  document.body.textContent === ''"""
            )
            if pristine is not True:
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", "initial browser page is not pristine"
                )
            self.initial_visual_state = self.visuals.capture_unredacted_state_once()
            self._append_capture(
                "run_start",
                profile=asdict(self.profile),
                initial_state=self.initial_visual_state,
            )
        except BrowserCaptureError:
            self._close_resources()
            raise
        except BrowserVisualError as exc:
            self._close_resources()
            message = str(exc).split(": ", 1)[-1]
            raise BrowserCaptureError(exc.code, message) from exc
        except PlaywrightError as exc:
            message = self._sanitize(str(exc))
            self._close_resources()
            message = actionable_playwright_error(message)
            raise BrowserCaptureError("BROWSER_SCHEMA", message) from exc
        except BaseException as exc:
            message = self._sanitize(str(exc))
            self._close_resources()
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", f"browser startup failed: {message}"
            ) from exc

    def capture_beat(
        self,
        beat: BeatPlan,
        *,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> BeatCapture:
        try:
            return self._capture_browser_beat(beat, on_progress=on_progress)
        except BaseException:
            self._capture_failed = True
            raise

    def _capture_browser_beat(
        self,
        beat: BeatPlan,
        *,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> BeatCapture:
        if beat.medium is not RecordingMedium.browser:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                f"browser runner cannot capture {beat.medium.value} beat {beat.id!r}",
            )
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        beat_response_start = len(self.responses)
        previous_action_response_start: int | None = None
        actions: list[dict[str, Any]] = []
        self._current_beat_id = beat.id
        self._beat_start_ns = time.monotonic_ns()
        self._append_capture("beat_start", beat_id=beat.id)
        for raw_action in beat.actions:
            if not isinstance(raw_action, BrowserActionPlan):
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", f"browser beat {beat.id!r} has a terminal action"
                )
            action_response_start = len(self.responses)
            if on_progress is not None:
                on_progress("started", raw_action.id)
            actions.append(
                self._execute_action(
                    beat.id,
                    raw_action,
                    previous_action_response_start=previous_action_response_start,
                )
            )
            self._append_capture("action", beat_id=beat.id, **actions[-1])
            if on_progress is not None:
                on_progress("completed", raw_action.id)
            previous_action_response_start = action_response_start
        checks: list[dict[str, Any]] = []
        for raw_check in beat.checks:
            if not isinstance(raw_check, BrowserCheckPlan):
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", f"browser beat {beat.id!r} has a terminal check"
                )
            checks.append(
                self._execute_check(
                    beat.id,
                    raw_check,
                    response_start=beat_response_start,
                )
            )
            self._append_capture("check", beat_id=beat.id, **checks[-1])
        self._append_capture("beat_end", beat_id=beat.id)
        self._current_beat_id = ""
        self._beat_start_ns = 0
        return BeatCapture(
            beat_id=beat.id,
            artifacts=(self.capture_log_path,) if self.capture_log_path else (),
            metadata={
                "actions": tuple(actions),
                "checks": tuple(checks),
                "runner_initial_state": self.initial_visual_state,
            },
        )

    def _execute_action(
        self,
        beat_id: str,
        action: BrowserActionPlan,
        *,
        previous_action_response_start: int | None,
    ) -> dict[str, Any]:
        if self.page is None or self.capture_context is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        config = _mapping(_thaw(action.config), field=f"action {action.id}")
        payload = _mapping(config.get(action.kind), field=f"action {action.id}.{action.kind}")
        started_ms = self._beat_elapsed_ms()
        video_started_ms = self._video_elapsed_ms()
        self._current_action_id = action.id
        target_fact: dict[str, Any] | None = None
        completion: dict[str, Any] = {"kind": "action"}
        before_state: dict[str, Any] | None = None
        visual: dict[str, Any]
        extra_redactions = self._action_redactions(action.kind, payload)
        document_origin = self._document_time_origin()
        try:
            if action.kind not in {"open_page", "wait_for"}:
                before_state = self._require_visuals().capture_state_once(
                    action_id=action.id,
                    extra_redactions=extra_redactions,
                )
            if action.kind == "open_page":
                completion = self._open_page(
                    payload, beat_id=beat_id, action_id=action.id
                )
            elif action.kind == "click":
                locator, target_fact = self._strict_target(
                    payload.get("target"), beat_id=beat_id, action_id=action.id
                )
                button = payload.get("button", "left")
                position = payload.get("position", "center")
                options: dict[str, Any] = {"button": button}
                if isinstance(position, dict):
                    relative_x = float(position["x"])
                    relative_y = float(position["y"])
                    if not (
                        0 <= relative_x <= target_fact["bounds"]["width"]
                        and 0 <= relative_y <= target_fact["bounds"]["height"]
                    ):
                        raise BrowserCaptureError(
                            "BROWSER_SCHEMA",
                            f"click position for action {action.id!r} is outside its target",
                        )
                    options["position"] = {
                        "x": relative_x,
                        "y": relative_y,
                    }
                    target_fact["point"] = {
                        "x": target_fact["bounds"]["x"] + relative_x,
                        "y": target_fact["bounds"]["y"] + relative_y,
                    }
                locator.click(**options)
            elif action.kind == "move_pointer":
                target = payload.get("target")
                if target is not None:
                    _, target_fact = self._strict_target(
                        target,
                        beat_id=beat_id,
                        action_id=action.id,
                    )
                else:
                    viewport_position = _mapping(
                        payload.get("viewport"),
                        field=f"action {action.id}.move_pointer.viewport",
                    )
                    viewport = self.page.viewport_size
                    if not isinstance(viewport, dict):
                        raise BrowserCaptureError(
                            "BROWSER_SCHEMA", "browser viewport is unavailable"
                        )
                    x = min(
                        float(viewport["width"] - 1),
                        float(viewport["width"]) * float(viewport_position["x"]),
                    )
                    y = min(
                        float(viewport["height"] - 1),
                        float(viewport["height"]) * float(viewport_position["y"]),
                    )
                    target_fact = {"point": {"x": x, "y": y}}
                point = _mapping(
                    target_fact.get("point"),
                    field=f"action {action.id}.move_pointer point",
                )
                self.page.mouse.move(float(point["x"]), float(point["y"]))
            elif action.kind in {"fill", "type_keys"}:
                locator, target_fact = self._strict_target(
                    payload.get("target"), beat_id=beat_id, action_id=action.id
                )
                target_fact["text_overlay"] = self._text_overlay_fact(
                    locator,
                    allow_password=payload.get("secret") is not None,
                )
                value, presentation = self._input_value(payload)
                if action.kind == "fill":
                    locator.fill(value)
                else:
                    delay = payload.get("capture_delay_ms")
                    locator.press_sequentially(value, delay=0 if delay is None else delay)
                completion["input"] = presentation
            elif action.kind == "press":
                target = payload.get("target")
                if target is not None:
                    locator, target_fact = self._strict_target(
                        target, beat_id=beat_id, action_id=action.id
                    )
                    locator.focus()
                self.page.keyboard.press(payload["key"])
            elif action.kind == "scroll":
                target_fact = self._scroll(
                    payload, beat_id=beat_id, action_id=action.id
                )
            elif action.kind == "wait_for":
                response_start = previous_action_response_start
                if payload.get("response") is not None and response_start is None:
                    raise BrowserCaptureError(
                        "BROWSER_SCHEMA",
                        f"wait_for.response action {action.id!r} has no preceding action",
                    )
                completion = self._wait_condition(
                    payload,
                    response_start=len(self.responses)
                    if response_start is None
                    else response_start,
                    beat_id=beat_id,
                    action_id=action.id,
                )
            else:
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", f"unsupported browser action kind {action.kind!r}"
                )
            self._wait_for_render_assets()
            if self._document_time_origin() != document_origin:
                self._active_secret_redactions = ()
                extra_redactions = self._current_secret_redaction(
                    action.kind, payload
                )
            execution_ended_ms = self._beat_elapsed_ms()
            explicit_dynamic = config.get("transition") == "captured"
            force_dynamic = explicit_dynamic or (
                action.kind == "open_page" and payload.get("loading", "hide") == "show"
            ) or (
                action.kind == "scroll"
                and (
                    target_fact is None
                    or not isinstance(target_fact.get("scroll"), dict)
                    or target_fact["scroll"].get("eligible") is not True
                )
            )
            visual = self._require_visuals().observe(
                beat_id=beat_id,
                action_id=action.id,
                video_start_ms=video_started_ms,
                video_end_ms=self._video_elapsed_ms,
                start_state_path=(
                    self._require_visuals().run_dir / before_state["path"]
                    if before_state is not None
                    else None
                ),
                extra_redactions=extra_redactions,
                force_dynamic=force_dynamic,
                explicit_dynamic=explicit_dynamic,
            )
            current_secret = self._current_secret_redaction(action.kind, payload)
            if current_secret:
                active = [dict(target) for target in self._active_secret_redactions]
                active.extend(
                    dict(target) for target in current_secret if target not in active
                )
                self._active_secret_redactions = tuple(active)
        except BrowserVisualError as exc:
            raise BrowserCaptureError(exc.code, str(exc).split(": ", 1)[-1]) from exc
        except BrowserCaptureError:
            raise
        except BaseException as exc:
            if _is_playwright_timeout(exc):
                raise BrowserCaptureError(
                    "BROWSER_READINESS_TIMEOUT",
                    f"browser action {action.id!r} timed out",
                ) from exc
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                f"browser action {action.id!r} failed: {self._sanitize(str(exc))}",
            ) from exc
        result: dict[str, Any] = {
            "action_id": action.id,
            "kind": action.kind,
            "execution": {"start_ms": started_ms, "end_ms": execution_ended_ms},
            "completion": completion,
            "visual": visual,
        }
        if before_state is not None:
            result["before_state"] = before_state
        if target_fact is not None:
            result["target"] = target_fact
        self._current_action_id = None
        return result

    def _open_page(
        self, payload: Mapping[str, Any], *, beat_id: str, action_id: str
    ) -> dict[str, Any]:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        handoff_id = payload.get("handoff")
        capture_url = payload.get("url")
        if handoff_id is not None:
            capture_url = self._handoff_urls.pop(str(handoff_id), None)
            if capture_url is None:
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA",
                    f"browser handoff {handoff_id!r} has no captured URL",
                )
        if not isinstance(capture_url, str) or not capture_url:
            raise BrowserCaptureError("BROWSER_SCHEMA", "open_page.url must be non-empty")
        base_url = self.browser_config.get("base_url")
        if not urlsplit(capture_url).scheme:
            if not isinstance(base_url, str) or not base_url:
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", "relative open_page URL requires browser.base_url"
                )
            capture_url = urljoin(base_url, capture_url)
        lifecycle = payload.get("lifecycle", "domcontentloaded")
        if lifecycle not in {"domcontentloaded", "load"}:
            raise BrowserCaptureError("BROWSER_SCHEMA", "open_page.lifecycle is invalid")
        timeout = self._timeout(payload, readiness=True)
        response_start = len(self.responses)
        self.page.goto(capture_url, wait_until=lifecycle, timeout=timeout)
        ready = payload.get("ready")
        completion: dict[str, Any] = {"kind": "navigation", "lifecycle": lifecycle}
        if ready is not None:
            completion = self._wait_condition(
                _mapping(ready, field="open_page.ready"),
                response_start=response_start,
                beat_id=beat_id,
                action_id=action_id,
            )
        completion["url"] = capture_url
        completion["lifecycle"] = lifecycle
        return completion

    def _strict_target(
        self,
        value: object,
        *,
        beat_id: str,
        action_id: str | None,
        wait_state: str = "attached",
        timeout_ms: int | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        target = _mapping(value, field="browser target")
        family = next(
            (
                name
                for name in ("role", "label", "placeholder", "text", "test_id", "css", "xpath")
                if target.get(name) is not None
            ),
            None,
        )
        if family is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser target has no locator")
        description = _target_description(target, family)
        context = _target_context(beat_id, action_id)
        exact = bool(target.get("exact", False))
        if family == "role":
            options: dict[str, Any] = {"exact": exact}
            if target.get("name") is not None:
                options["name"] = target["name"]
            locator = self.page.get_by_role(target["role"], **options)
        elif family == "label":
            locator = self.page.get_by_label(target["label"], exact=exact)
        elif family == "placeholder":
            locator = self.page.get_by_placeholder(target["placeholder"], exact=exact)
        elif family == "text":
            locator = self.page.get_by_text(target["text"], exact=exact)
        elif family == "test_id":
            locator = self.page.get_by_test_id(target["test_id"])
        elif family == "css":
            locator = self.page.locator(target["css"])
            self._emit_warning("FRAGILE_BROWSER_SELECTOR", beat_id, action_id)
        else:
            locator = self.page.locator("xpath=" + target["xpath"])
            self._emit_warning("FRAGILE_BROWSER_SELECTOR", beat_id, action_id)
        try:
            locator.first.wait_for(
                state=wait_state,
                timeout=(
                    self._timeout({}, readiness=False)
                    if timeout_ms is None
                    else timeout_ms
                ),
            )
            count = locator.count()
        except BaseException as exc:
            if _is_playwright_timeout(exc):
                if timeout_ms is not None:
                    raise BrowserCaptureError(
                        "BROWSER_READINESS_TIMEOUT",
                        "browser readiness condition timed out",
                    ) from exc
                raise BrowserCaptureError(
                    "BROWSER_TARGET_COUNT",
                    f"{context}: expected exactly one element matching "
                    f"{description}; found 0",
                ) from exc
            raise
        if count != 1:
            raise BrowserCaptureError(
                "BROWSER_TARGET_COUNT",
                f"{context}: expected exactly one element matching "
                f"{description}; found {count}",
            )
        bounds = locator.bounding_box()
        if bounds is None:
            raise BrowserCaptureError(
                "BROWSER_TARGET_COUNT",
                f"{context}: element matching {description} has no visible bounds",
            )
        return locator, {
            "locator": target,
            "bounds": bounds,
            "point": {
                "x": bounds["x"] + bounds["width"] / 2,
                "y": bounds["y"] + bounds["height"] / 2,
            },
        }

    def _input_value(self, payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        text = payload.get("text")
        secret = payload.get("secret")
        if text is not None:
            if not isinstance(text, str):
                raise BrowserCaptureError("BROWSER_SCHEMA", "browser input text is invalid")
            return text, {"kind": "text", "text": text}
        secret = _mapping(secret, field="browser input secret")
        env_name = secret.get("env")
        if not isinstance(env_name, str) or not env_name:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser secret env is invalid")
        if self.capture_context is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        value = self.capture_context.environment.get(env_name)
        if value is None:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", f"browser secret environment variable {env_name!r} is not set"
            )
        self.secrets.register(value)
        presentation = secret.get("presentation", "masked")
        result = {"kind": "secret", "presentation": presentation}
        if presentation == "placeholder":
            result["placeholder"] = secret.get("placeholder")
        return value, result

    def _action_redactions(
        self, kind: str, payload: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        return (
            *self._active_secret_redactions,
            *self._current_secret_redaction(kind, payload),
        )

    def _current_secret_redaction(
        self, kind: str, payload: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        if kind not in {"fill", "type_keys"} or payload.get("secret") is None:
            return ()
        return (_mapping(payload.get("target"), field="browser secret target"),)

    def _document_time_origin(self) -> float:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        value = self.page.evaluate("performance.timeOrigin")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser document has no valid time origin"
            )
        return float(value)

    def _text_overlay_fact(
        self, locator: Any, *, allow_password: bool
    ) -> dict[str, Any]:
        try:
            fact = locator.evaluate(
                """element => {
                  const style = getComputedStyle(element);
                  const rect = element.getBoundingClientRect();
                  const number = (value, fallback = 0) => {
                    const parsed = Number.parseFloat(value);
                    return Number.isFinite(parsed) ? parsed : fallback;
                  };
                  const tag = element.tagName.toLowerCase();
                  const inputType = tag === 'input' ? element.type.toLowerCase() : '';
                  const supported = new Set([
                    'text', 'search', 'email', 'tel', 'url', 'password'
                  ]);
                  const initialEmpty = typeof element.value === 'string' &&
                    element.value.length === 0;
                  return {
                    eligible: tag === 'input' && supported.has(inputType) &&
                      initialEmpty && rect.width > 0 && rect.height > 0,
                    input_type: inputType,
                    style: {
                      font_family: style.fontFamily || 'sans-serif',
                      font_size: number(style.fontSize, 16),
                      font_weight: style.fontWeight || 'normal',
                      font_style: style.fontStyle || 'normal',
                      line_height: number(
                        style.lineHeight,
                        number(style.fontSize, 16) * 1.2
                      ),
                      letter_spacing: number(style.letterSpacing, 0),
                      color: style.color || 'rgb(0, 0, 0)',
                      text_align: style.textAlign || 'start',
                      padding_top: number(style.paddingTop),
                      padding_right: number(style.paddingRight),
                      padding_bottom: number(style.paddingBottom),
                      padding_left: number(style.paddingLeft),
                      clipping_rect: {
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height
                      },
                      selection_start: Number.isInteger(element.selectionStart)
                        ? element.selectionStart : null,
                      selection_end: Number.isInteger(element.selectionEnd)
                        ? element.selectionEnd : null,
                      caret_visible: false
                    }
                  };
                }"""
            )
        except BaseException as exc:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "could not capture browser text presentation facts"
            ) from exc
        if not isinstance(fact, dict) or not isinstance(fact.get("style"), dict):
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser text presentation facts are invalid"
            )
        if fact.get("input_type") == "password" and not allow_password:
            fact["eligible"] = False
        fact.pop("input_type", None)
        return fact

    def _require_visuals(self) -> BrowserVisualCapture:
        if self.visuals is None:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser visual capture is not started"
            )
        return self.visuals

    def _video_elapsed_ms(self) -> int:
        if not self._video_origin_ns:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser video is not started")
        return round((time.monotonic_ns() - self._video_origin_ns) / 1_000_000)

    def _calibrate_video_origin(self) -> None:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser page is not started")
        sample_started_ns = time.monotonic_ns()
        page_elapsed_ms = self.page.evaluate("performance.now()")
        sample_ended_ns = time.monotonic_ns()
        if (
            isinstance(page_elapsed_ms, bool)
            or not isinstance(page_elapsed_ms, (int, float))
            or page_elapsed_ms < 0
        ):
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser video has no valid time origin"
            )
        sample_midpoint_ns = (sample_started_ns + sample_ended_ns) // 2
        self._video_origin_ns = sample_midpoint_ns - round(
            float(page_elapsed_ms) * 1_000_000
        )

    def _scroll(
        self, payload: Mapping[str, Any], *, beat_id: str, action_id: str
    ) -> dict[str, Any] | None:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        if payload.get("target") is not None:
            locator, fact = self._strict_target(
                payload["target"], beat_id=beat_id, action_id=action_id
            )
            locator.scroll_into_view_if_needed()
            fact["scroll"] = {"eligible": False}
            return fact
        offset = payload.get("by") if payload.get("by") is not None else payload.get("to")
        offset = _mapping(offset, field="browser scroll offset")
        method = "scrollBy" if payload.get("by") is not None else "scrollTo"
        point = {"x": int(offset.get("x", 0)), "y": int(offset.get("y", 0))}
        container = payload.get("container")
        if container is None:
            start = self.page.evaluate("() => ({x: scrollX, y: scrollY})")
            self.page.evaluate(f"([x, y]) => window.{method}(x, y)", [point["x"], point["y"]])
            end = self.page.evaluate("() => ({x: scrollX, y: scrollY})")
            viewport = self.page.viewport_size
            if not isinstance(viewport, dict):
                raise BrowserCaptureError(
                    "BROWSER_SCHEMA", "browser viewport is unavailable"
                )
            return {
                "bounds": {
                    "x": 0,
                    "y": 0,
                    "width": viewport["width"],
                    "height": viewport["height"],
                },
                "point": {
                    "x": viewport["width"] / 2,
                    "y": viewport["height"] / 2,
                },
                "scroll": {"eligible": False, "start": start, "end": end},
            }
        locator, fact = self._strict_target(
            container, beat_id=beat_id, action_id=action_id
        )
        classification = locator.evaluate(
            """element => {
              const descendants = [element, ...element.querySelectorAll('*')];
              const hasMedia = descendants.some(node =>
                node.matches && node.matches('video, canvas')
              );
              const hasPositioned = descendants.some(node => {
                const position = getComputedStyle(node).position;
                return position === 'sticky' || position === 'fixed';
              });
              const hasAnimation = descendants.some(node =>
                typeof node.getAnimations === 'function' &&
                  node.getAnimations().length > 0
              );
              const hasScrollTimeline = descendants.some(node => {
                const style = getComputedStyle(node);
                return (style.scrollTimelineName &&
                    style.scrollTimelineName !== 'none') ||
                  (style.animationTimeline && style.animationTimeline !== 'auto');
              });
              const suspiciousVirtualization =
                element.hasAttribute('aria-rowcount') ||
                element.querySelector('[aria-rowindex]') !== null ||
                element.scrollHeight > Math.max(20000, element.clientHeight * 100);
              return {
                eligible: !hasMedia && !hasPositioned && !hasAnimation &&
                  !hasScrollTimeline && !suspiciousVirtualization,
                start: {x: element.scrollLeft, y: element.scrollTop}
              };
            }"""
        )
        locator.evaluate(f"(element, [x, y]) => element.{method}(x, y)", [point["x"], point["y"]])
        end = locator.evaluate(
            "element => ({x: element.scrollLeft, y: element.scrollTop})"
        )
        if not isinstance(classification, dict):
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "browser scroll classification is invalid"
            )
        classification["end"] = end
        fact["scroll"] = classification
        return fact

    def _wait_condition(
        self,
        condition: Mapping[str, Any],
        *,
        response_start: int,
        beat_id: str,
        action_id: str,
    ) -> dict[str, Any]:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        timeout = self._timeout(condition, readiness=True)
        try:
            if condition.get("visible") is not None:
                self._strict_target(
                    condition["visible"],
                    beat_id=beat_id,
                    action_id=action_id,
                    wait_state="visible",
                    timeout_ms=timeout,
                )
                return {"kind": "visible"}
            if condition.get("hidden") is not None:
                locator = self._locator_without_count(condition["hidden"])
                if locator.count() > 1:
                    raise BrowserCaptureError(
                        "BROWSER_TARGET_COUNT", "hidden target resolved to multiple elements"
                    )
                locator.wait_for(state="hidden", timeout=timeout)
                return {"kind": "hidden"}
            if condition.get("url") is not None:
                matcher = _mapping(condition["url"], field="URL condition")
                self._poll_until(lambda: _url_matches(matcher, self.page.url), timeout)
                return {"kind": "url"}
            if condition.get("response") is not None:
                matcher = _mapping(condition["response"], field="response condition")
                observed = self._wait_for_response(matcher, response_start, timeout)
                return {"kind": "response", "response_seq": observed.seq}
        except BrowserCaptureError:
            raise
        except BaseException as exc:
            if _is_playwright_timeout(exc):
                raise BrowserCaptureError(
                    "BROWSER_READINESS_TIMEOUT", "browser readiness condition timed out"
                ) from exc
            raise
        raise BrowserCaptureError("BROWSER_SCHEMA", "browser condition has no variant")

    def _execute_check(
        self,
        beat_id: str,
        check: BrowserCheckPlan,
        *,
        response_start: int,
    ) -> dict[str, Any]:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        config = _mapping(_thaw(check.config), field=f"check {check.name}")
        payload = config.get(check.kind)
        passed = False
        try:
            if check.kind == "url":
                passed = _url_matches(_mapping(payload, field="URL check"), self.page.url)
            elif check.kind in {"visible", "hidden"}:
                locator = self._locator_without_count(payload)
                count = locator.count()
                if count > 1:
                    raise BrowserCaptureError(
                        "BROWSER_TARGET_COUNT",
                        f"check {check.name!r} target resolved to {count} elements",
                    )
                passed = locator.is_visible() if check.kind == "visible" else count == 0 or locator.is_hidden()
            elif check.kind in {"text", "value"}:
                value = _mapping(payload, field=f"{check.kind} check")
                locator, _ = self._strict_target(
                    value.get("target"), beat_id=beat_id, action_id=None
                )
                actual = locator.text_content() if check.kind == "text" else locator.input_value()
                passed = _text_matches(value, actual or "")
            elif check.kind == "count":
                value = _mapping(payload, field="count check")
                locator = self._locator_without_count(value.get("target"))
                passed = locator.count() == value.get("equals")
            elif check.kind == "response":
                matcher = _mapping(payload, field="response check")
                passed = any(
                    _response_matches(matcher, response)
                    for response in self.responses[response_start:]
                )
        except BrowserCaptureError:
            raise
        except BaseException as exc:
            raise BrowserCaptureError(
                "BROWSER_CHECK_FAILED", f"browser check {check.name!r} failed: {exc}"
            ) from exc
        if not passed:
            raise BrowserCaptureError(
                "BROWSER_CHECK_FAILED", f"browser check {check.name!r} did not pass"
            )
        return {"name": check.name, "kind": check.kind, "passed": True}

    def _locator_without_count(self, value: object) -> Any:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        target = _mapping(value, field="browser target")
        family = next(
            (name for name in ("role", "label", "placeholder", "text", "test_id", "css", "xpath") if target.get(name) is not None),
            None,
        )
        if family == "role":
            options: dict[str, Any] = {"exact": bool(target.get("exact", False))}
            if target.get("name") is not None:
                options["name"] = target["name"]
            return self.page.get_by_role(target["role"], **options)
        if family == "label":
            return self.page.get_by_label(target["label"], exact=bool(target.get("exact", False)))
        if family == "placeholder":
            return self.page.get_by_placeholder(target["placeholder"], exact=bool(target.get("exact", False)))
        if family == "text":
            return self.page.get_by_text(target["text"], exact=bool(target.get("exact", False)))
        if family == "test_id":
            return self.page.get_by_test_id(target["test_id"])
        if family == "css":
            return self.page.locator(target["css"])
        if family == "xpath":
            return self.page.locator("xpath=" + target["xpath"])
        raise BrowserCaptureError("BROWSER_SCHEMA", "browser target has no locator")

    def _wait_for_render_assets(self) -> None:
        if self.page is None:
            return
        timeout = self._timeout({}, readiness=True)
        try:
            self.page.wait_for_function(
                """() => {
                  const fontsReady = !document.fonts || document.fonts.status === 'loaded';
                  const imagesReady = [...document.images].every((image) => {
                    const style = getComputedStyle(image);
                    const visible = style.display !== 'none' && style.visibility !== 'hidden' &&
                      image.getBoundingClientRect().width > 0 && image.getBoundingClientRect().height > 0;
                    return !visible || (image.complete && image.naturalWidth > 0);
                  });
                  return fontsReady && imagesReady;
                }""",
                timeout=timeout,
            )
        except BaseException as exc:
            if _is_playwright_timeout(exc):
                raise BrowserCaptureError(
                    "BROWSER_READINESS_TIMEOUT", "browser fonts or images did not become ready"
                ) from exc
            raise

    def _wait_for_response(
        self, matcher: Mapping[str, Any], start: int, timeout_ms: int
    ) -> ResponseObservation:
        result: ResponseObservation | None = None

        def matched() -> bool:
            nonlocal result
            result = next(
                (
                    response
                    for response in self.responses[start:]
                    if _response_matches(matcher, response)
                ),
                None,
            )
            return result is not None

        self._poll_until(matched, timeout_ms)
        if result is None:
            raise BrowserCaptureError(
                "BROWSER_READINESS_TIMEOUT", "browser response condition timed out"
            )
        return result

    def _poll_until(self, predicate: Any, timeout_ms: int) -> None:
        if self.page is None:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser runner is not started")
        deadline = time.monotonic() + timeout_ms / 1000
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrowserCaptureError(
                    "BROWSER_READINESS_TIMEOUT", "browser readiness condition timed out"
                )
            self.page.wait_for_timeout(min(50, max(1, round(remaining * 1000))))

    def _timeout(self, value: Mapping[str, Any], *, readiness: bool) -> int:
        override = value.get("timeout_ms")
        if override is not None:
            return int(override)
        timeouts = _mapping(self.browser_config.get("timeouts", {}), field="browser.timeouts")
        return int(timeouts.get("readiness_ms" if readiness else "action_ms", 15_000 if readiness else 10_000))

    def _observe_response(self, response: Any) -> None:
        try:
            observation = ResponseObservation(
                seq=len(self.responses) + 1,
                url=response.url,
                method=response.request.method.upper(),
                status=int(response.status),
            )
            self.responses.append(observation)
            origin = _safe_origin(observation.url)
            self._append_diagnostic(
                self.network_log_path,
                "response",
                origin=origin,
                method=observation.method,
                result=f"{observation.status // 100}xx",
            )
            self._observe_origin(origin)
        except BaseException:
            return

    def _observe_console(self, message: Any) -> None:
        try:
            self._append_diagnostic(
                self.console_log_path,
                "console",
                level=str(message.type),
                text=self._sanitize(str(message.text)),
            )
        except BaseException:
            return

    def _observe_page_error(self, error: Any) -> None:
        try:
            self._append_diagnostic(
                self.page_error_log_path,
                "page_error",
                text=self._sanitize(str(error)),
            )
        except BaseException:
            return

    def _observe_request_failure(self, request: Any) -> None:
        try:
            origin = _safe_origin(request.url)
            self._append_diagnostic(
                self.network_log_path,
                "request_failure",
                origin=origin,
                method=str(request.method).upper(),
                result=_failure_category(str(request.failure or "")),
            )
            self._observe_origin(origin)
        except BaseException:
            return

    def _observe_origin(self, origin: str) -> None:
        if not origin or _is_loopback_origin(origin):
            return
        if origin in self._warned_external_origins:
            return
        self._warned_external_origins.add(origin)
        self._emit_warning(
            "EXTERNAL_NETWORK_CAPTURE",
            self._current_beat_id,
            self._current_action_id,
        )

    def _emit_warning(
        self, code: str, beat_id: str, action_id: str | None
    ) -> None:
        warning = BrowserWarning(code, beat_id, action_id)
        if warning in self.warnings:
            return
        self.warnings.append(warning)
        values: dict[str, Any] = {"code": code}
        if beat_id:
            values["beat_id"] = beat_id
        if action_id:
            values["action_id"] = action_id
        self._append_capture("warning", **values)

    def _append_capture(self, record_type: str, **values: Any) -> None:
        path = self.capture_log_path
        if path is None:
            return
        self._capture_seq += 1
        record = {
            "capture_version": 1,
            "seq": self._capture_seq,
            "type": record_type,
            **values,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _append_diagnostic(
        self, path: Path | None, diagnostic_type: str, **values: Any
    ) -> None:
        if path is None:
            return
        self._diagnostic_seq += 1
        record = {
            "diagnostic_version": 1,
            "seq": self._diagnostic_seq,
            "type": diagnostic_type,
            "beat_id": self._current_beat_id or None,
            "action_id": self._current_action_id,
            **values,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _sanitize(self, text: str) -> str:
        value = self.secrets.scrub(text)
        context = self.capture_context
        if context is not None:
            for path in (
                context.paths.run,
                context.workspace,
                context.working_directory,
            ):
                value = value.replace(str(path), "[PRIVATE_PATH]")
        return value[:4000]

    def complete(self) -> None:
        if self._completed:
            return
        if self._capture_failed:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", "failed browser capture cannot be marked complete"
            )
        if not self._closed or not self._close_succeeded:
            raise BrowserCaptureError(
                "BROWSER_SCHEMA",
                "browser capture cannot complete before successful runner close",
            )
        self._append_capture("run_end", status="completed")
        self._completed = True

    def _beat_elapsed_ms(self) -> int:
        if not self._beat_start_ns:
            raise BrowserCaptureError("BROWSER_SCHEMA", "browser beat is not started")
        return round((time.monotonic_ns() - self._beat_start_ns) / 1_000_000)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors = self._close_resources()
        if errors:
            first_error = errors[0][1]
            if isinstance(first_error, BrowserVisualError):
                message = str(first_error).split(": ", 1)[-1]
                raise BrowserCaptureError(first_error.code, message) from first_error
            message = "; ".join(
                f"{operation}: {type(error).__name__}"
                for operation, error in errors
            )
            raise BrowserCaptureError(
                "BROWSER_SCHEMA", f"browser teardown failed: {message}"
            ) from errors[0][1]
        self._close_succeeded = True

    def _close_resources(self) -> list[tuple[str, BaseException]]:
        errors: list[tuple[str, BaseException]] = []
        page = self.page
        context = self.browser_context
        browser = self.browser
        playwright = self.playwright
        video = self.video
        visuals = self.visuals
        self.page = None
        self.browser_context = None
        self.browser = None
        self.playwright = None
        self.video = None
        self.visuals = None
        for name, resource in (("page", page), ("context", context)):
            if resource is None:
                continue
            try:
                resource.close()
            except BaseException as exc:
                errors.append((name, exc))
        if visuals is not None and visuals.dynamic_requests:
            try:
                if video is None:
                    raise BrowserVisualError(
                        "BROWSER_UNSUPPORTED_MOTION",
                        "Playwright did not provide a source video",
                    )
                source_video = Path(video.path())
                for asset in visuals.finalize_dynamic_fragments(source_video):
                    self._append_capture(
                        "diagnostic",
                        kind="dynamic_fragment",
                        beat_id=asset.beat_id,
                        action_id=asset.action_id,
                        path=asset.path.relative_to(visuals.run_dir).as_posix(),
                        sha256=asset.sha256,
                        media_type="video/mp4",
                        width=asset.width,
                        height=asset.height,
                        duration_ms=asset.duration_ms,
                        encoded_bytes=asset.encoded_bytes,
                        codec=asset.codec,
                        has_audio=asset.has_audio,
                        source_start_ms=asset.source_start_ms,
                        source_end_ms=asset.source_end_ms,
                    )
            except BaseException as exc:
                errors.append(("dynamic fragments", exc))
        for name, resource in (("browser", browser), ("playwright", playwright)):
            if resource is None:
                continue
            try:
                if name == "playwright":
                    resource.stop()
                else:
                    resource.close()
            except BaseException as exc:
                errors.append((name, exc))
        self.authentication = None
        self.capture_context = None
        self._active_secret_redactions = ()
        return errors


def _prepare_private_browser_directory(path: Path) -> None:
    if path.is_symlink():
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "private browser directory is a symlink"
        )
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "private browser path is not a directory"
        )
    path.chmod(0o700)
    if path.stat().st_mode & 0o077:
        raise BrowserCaptureError(
            "BROWSER_SCHEMA", "private browser directory is not private"
        )


def _resolved_context_values(config: Mapping[str, Any]) -> dict[str, Any]:
    viewport = config.get("viewport")
    viewport = {} if viewport is None else _mapping(viewport, field="browser.viewport")
    context = config.get("context")
    context = {} if context is None else _mapping(context, field="browser.context")
    width = viewport.get("width") or DESKTOP_VIEWPORT_WIDTH
    height = viewport.get("height") or DESKTOP_VIEWPORT_HEIGHT
    scale = viewport.get("device_scale_factor") or DESKTOP_DEVICE_SCALE_FACTOR
    permissions = context.get("permissions")
    if permissions is None:
        permissions = []
    return {
        "viewport_width": int(width),
        "viewport_height": int(height),
        "device_scale_factor": float(scale),
        "locale": context.get("locale") or DESKTOP_LOCALE,
        "timezone": context.get("timezone") or DESKTOP_TIMEZONE,
        "color_scheme": context.get("color_scheme") or DESKTOP_COLOR_SCHEME,
        "reduced_motion": context.get("reduced_motion") or DESKTOP_REDUCED_MOTION,
        "permissions": tuple(permissions),
    }


def _comparison_value(matcher: Mapping[str, Any], url: str) -> str:
    selected = next(
        (
            matcher[name]
            for name in ("equals", "contains", "matches")
            if matcher.get(name) is not None
        ),
        "",
    )
    if isinstance(selected, str) and selected.startswith("/"):
        parsed = urlsplit(url)
        return parsed.path + (("?" + parsed.query) if parsed.query else "")
    return url


def _text_matches(matcher: Mapping[str, Any], actual: str) -> bool:
    if matcher.get("equals") is not None:
        return actual == matcher["equals"]
    if matcher.get("contains") is not None:
        return matcher["contains"] in actual
    if matcher.get("matches") is not None:
        return re.search(matcher["matches"], actual) is not None
    raise BrowserCaptureError("BROWSER_SCHEMA", "matcher has no comparison")


def _url_matches(matcher: Mapping[str, Any], url: str) -> bool:
    return _text_matches(matcher, _comparison_value(matcher, url))


def _response_matches(
    matcher: Mapping[str, Any], response: ResponseObservation
) -> bool:
    method = matcher.get("method")
    if method is not None and response.method != str(method).upper():
        return False
    status = matcher.get("status")
    if status is not None and response.status != int(status):
        return False
    return _url_matches(matcher, response.url)


def _is_playwright_timeout(error: BaseException) -> bool:
    return type(error).__name__ == "TimeoutError" and type(error).__module__.startswith(
        "playwright"
    )


def _safe_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return ""
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}"


def _is_loopback_origin(origin: str) -> bool:
    host = urlsplit(origin).hostname
    return host in {"127.0.0.1", "::1", "localhost"}


def _failure_category(value: str) -> str:
    lowered = value.lower()
    if "abort" in lowered or "cancel" in lowered:
        return "aborted"
    if "timed" in lowered:
        return "timed_out"
    if "name" in lowered or "dns" in lowered:
        return "name_resolution"
    if "connection" in lowered or "refused" in lowered:
        return "connection"
    return "other"
