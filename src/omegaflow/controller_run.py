"""Trusted controller entrypoint for a public Reploy controlled session."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import select
import shlex
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .envoy_session import EnvoyTerminalSession
from .reploy_protocol import (
    BrokerReady,
    ClientError,
    Opened,
    Ready,
    ReployDiagnostic,
    ReployEndpoint,
    ReployLifecycleResult,
    Terminated,
    Terminating,
    WorkloadExit,
    WorkloadOutputsFinalized,
    decode_client_event,
    encode_client_request,
)
from .recording_plan import (
    RecordingPlan,
    RecordingPlanError,
    declared_recording_source_inputs,
    normalize_recording_plan,
)


MANIFEST_SCHEMA = "omegaflow-controller-run-v1"
MAX_MANIFEST_BYTES = 8 << 20
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ControllerRunError(RuntimeError):
    """The trusted controller could not complete its Reploy lifecycle."""


@dataclass(frozen=True)
class ControllerAsset:
    name: str
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ControllerRunManifest:
    recording_id: str
    recording_plan: dict[str, Any]
    columns: int
    rows: int
    terminal_endpoint_id: str
    telemetry_endpoint_id: str
    application_endpoint_ids: tuple[str, ...]
    assets: tuple[ControllerAsset, ...]


@dataclass(frozen=True)
class ControllerContext:
    manifest: ControllerRunManifest
    opened: Opened
    output_dir: Path

    def endpoint(self, endpoint_id: str, *, scheme: str | None = None) -> ReployEndpoint:
        matches = [item for item in self.opened.endpoints if item.id == endpoint_id]
        if len(matches) != 1:
            raise ControllerRunError(
                f"Reploy granted {len(matches)} endpoints named {endpoint_id!r}"
            )
        endpoint = matches[0]
        if scheme is not None and endpoint.scheme != scheme:
            raise ControllerRunError(
                f"endpoint {endpoint_id!r} uses {endpoint.scheme!r}, expected {scheme!r}"
            )
        return endpoint

    def open_envoy_session(
        self,
        *,
        title: str = "OmegaFlow recording",
        output_dir: Path | None = None,
    ) -> EnvoyTerminalSession:
        terminal = self.endpoint(self.manifest.terminal_endpoint_id, scheme="tcp")
        telemetry = self.endpoint(self.manifest.telemetry_endpoint_id, scheme="tcp")
        return EnvoyTerminalSession(
            (terminal.host, terminal.port),
            (telemetry.host, telemetry.port),
            output_dir or self.output_dir / "capture" / "runners" / "terminal",
            session_id=self.manifest.recording_id,
            columns=self.opened.columns,
            rows=self.opened.rows,
            title=title,
        )


def load_controller_manifest(
    path: Path,
    *,
    assets_root: Path | None = None,
) -> ControllerRunManifest:
    """Load and fully validate the bounded, controller-only run input."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ControllerRunError(f"could not read controller run manifest: {exc}") from exc
    if not payload or len(payload) > MAX_MANIFEST_BYTES or b"\x00" in payload:
        raise ControllerRunError("controller run manifest is empty, oversized, or contains NUL")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ControllerRunError(f"controller run manifest repeats field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerRunError(f"controller run manifest is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ControllerRunError("controller run manifest must be an object")
    required = {
        "schema",
        "recording_id",
        "recording_plan",
        "terminal",
        "envoy_endpoints",
        "application_endpoint_ids",
        "assets",
    }
    if set(value) != required:
        raise ControllerRunError("controller run manifest fields do not match v1")
    if value["schema"] != MANIFEST_SCHEMA:
        raise ControllerRunError("unsupported controller run manifest schema")
    recording_id = _identifier(value["recording_id"], "recording_id")
    plan = value["recording_plan"]
    if not isinstance(plan, dict) or plan.get("id") != recording_id:
        raise ControllerRunError("recording_plan must be an object matching recording_id")
    try:
        normalized_plan = normalize_recording_plan(plan)
    except RecordingPlanError as exc:
        raise ControllerRunError(f"recording_plan is invalid: {exc}") from exc
    _validate_controller_recording_capabilities(plan, normalized_plan)
    terminal = _exact_object(value["terminal"], {"columns", "rows"}, "terminal")
    columns = _integer(terminal["columns"], "terminal.columns", 1, 1000)
    rows = _integer(terminal["rows"], "terminal.rows", 1, 1000)
    endpoints = _exact_object(
        value["envoy_endpoints"], {"terminal", "telemetry"}, "envoy_endpoints"
    )
    terminal_id = _identifier(endpoints["terminal"], "envoy_endpoints.terminal")
    telemetry_id = _identifier(endpoints["telemetry"], "envoy_endpoints.telemetry")
    if terminal_id == telemetry_id:
        raise ControllerRunError("Envoy endpoint ids must be distinct")
    application_ids = value["application_endpoint_ids"]
    if not isinstance(application_ids, list):
        raise ControllerRunError("application_endpoint_ids must be an array")
    decoded_application_ids = tuple(
        _identifier(item, "application_endpoint_ids item") for item in application_ids
    )
    if len(set(decoded_application_ids)) != len(decoded_application_ids):
        raise ControllerRunError("application_endpoint_ids contains duplicates")
    if {terminal_id, telemetry_id} & set(decoded_application_ids):
        raise ControllerRunError("application endpoint ids overlap Envoy endpoint ids")
    if normalized_plan.browser is not None:
        endpoint_id = normalized_plan.browser.get("endpoint_id")
        if not isinstance(endpoint_id, str) or endpoint_id not in decoded_application_ids:
            raise ControllerRunError(
                "browser.endpoint_id must select one declared application endpoint"
            )
        if normalized_plan.browser.get("base_url") not in (None, ""):
            raise ControllerRunError(
                "controller browser base_url must come from opened.endpoints"
            )
    raw_assets = value["assets"]
    if not isinstance(raw_assets, list):
        raise ControllerRunError("assets must be an array")
    assets = tuple(_asset(item) for item in raw_assets)
    if len({item.name for item in assets}) != len(assets):
        raise ControllerRunError("assets contains duplicate names")
    root = assets_root if assets_root is not None else path.parent / "assets"
    for asset in assets:
        _validate_asset(root, asset)
    return ControllerRunManifest(
        recording_id,
        plan,
        columns,
        rows,
        terminal_id,
        telemetry_id,
        decoded_application_ids,
        assets,
    )


def write_controller_manifest(
    path: Path,
    manifest: ControllerRunManifest,
) -> None:
    body = {
        "schema": MANIFEST_SCHEMA,
        "recording_id": manifest.recording_id,
        "recording_plan": manifest.recording_plan,
        "terminal": {"columns": manifest.columns, "rows": manifest.rows},
        "envoy_endpoints": {
            "terminal": manifest.terminal_endpoint_id,
            "telemetry": manifest.telemetry_endpoint_id,
        },
        "application_endpoint_ids": list(manifest.application_endpoint_ids),
        "assets": [
            {"name": item.name, "path": item.path, "size": item.size, "sha256": item.sha256}
            for item in manifest.assets
        ],
    }
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ControllerRunError("controller run manifest exceeds 8 MiB")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)


class _Broker:
    def __init__(self, command: str, output_dir: Path) -> None:
        self.stderr_path = output_dir / "reploy-client.stderr.log"
        self.events_path = output_dir / "reploy-client.events.jsonl"
        self._stderr = self.stderr_path.open("wb")
        self._events = self.events_path.open("wb")
        self.process = subprocess.Popen(
            [command, "client"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
        )
        if self.process.stdin is None or self.process.stdout is None:  # pragma: no cover
            raise ControllerRunError("session client pipes were not created")
        self.input = self.process.stdin
        self.output = self.process.stdout
        self.buffer = b""

    def read(self, timeout: float) -> Any:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControllerRunError("timed out waiting for Reploy client event")
            readable, _, _ = select.select([self.output.fileno()], [], [], remaining)
            if not readable:
                raise ControllerRunError("timed out waiting for Reploy client event")
            chunk = os.read(self.output.fileno(), 65536)
            if not chunk:
                raise ControllerRunError("Reploy session client closed its event stream")
            self.buffer += chunk
            if len(self.buffer) > (1 << 20):
                raise ControllerRunError("Reploy session client event is oversized")
        line, self.buffer = self.buffer.split(b"\n", 1)
        self._events.write(line + b"\n")
        self._events.flush()
        try:
            return decode_client_event(line)
        except ValueError as exc:
            raise ControllerRunError(f"invalid Reploy client event: {exc}") from exc

    def write(self, kind: str, **values: Any) -> None:
        try:
            self.input.write(encode_client_request(kind, **values))
            self.input.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ControllerRunError(f"could not send Reploy client request {kind!r}") from exc

    def finish(self, timeout: float = 10.0) -> None:
        self.input.close()
        try:
            status = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.process.kill()
            self.process.wait()
            raise ControllerRunError("Reploy session client did not exit") from exc
        finally:
            self._events.close()
            self._stderr.close()
        if status != 0:
            raise ControllerRunError(f"Reploy session client exited with status {status}")

    def abort(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        self._events.close()
        self._stderr.close()


class _Attachment:
    def __init__(self, command: str, socket_path: str, output_dir: Path) -> None:
        self.stdout_path = output_dir / "bootstrap.stdout.log"
        self.stderr_path = output_dir / "bootstrap.stderr.log"
        self._stdout = self.stdout_path.open("wb")
        self._stderr = self.stderr_path.open("wb")
        self.process = subprocess.Popen(
            [command, "attach", "--socket", socket_path],
            stdin=subprocess.PIPE,
            stdout=self._stdout,
            stderr=self._stderr,
        )
        if self.process.stdin is None:  # pragma: no cover
            raise ControllerRunError("attachment input pipe was not created")
        self.input = self.process.stdin

    def bootstrap(self, manifest: ControllerRunManifest) -> None:
        command = [
            "exec",
            "/omegaflow-runtime/bin/envoy",
            "--columns",
            str(manifest.columns),
            "--rows",
            str(manifest.rows),
        ]
        line = " ".join(shlex.quote(item) for item in command) + "\n"
        try:
            self.input.write(line.encode("utf-8"))
            self.input.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ControllerRunError("could not bootstrap the workload Envoy") from exc

    def close(self, timeout: float = 10.0) -> None:
        try:
            self.input.close()
        except OSError:
            pass
        try:
            status = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.process.kill()
            self.process.wait()
            raise ControllerRunError("bootstrap attachment did not exit") from exc
        finally:
            self._stdout.close()
            self._stderr.close()
        if status != 0:
            raise ControllerRunError(f"bootstrap attachment exited with status {status}")

    def abort(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        self._stdout.close()
        self._stderr.close()


def run_controller_session(
    manifest: ControllerRunManifest,
    output_dir: Path,
    capture: Callable[[ControllerContext], None],
    *,
    session_client: str = "reploy-session-client",
    startup_timeout: float = 10.0,
    lifecycle_timeout: float = 30.0,
) -> ReployLifecycleResult:
    """Drive the public client contract around one controller capture callback."""

    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise ControllerRunError("REPLOY_OUTPUT_DIR must be an empty directory")
    else:
        output_dir.mkdir(mode=0o700, parents=True)
    broker = _Broker(session_client, output_dir)
    attachment: _Attachment | None = None
    terminated: Terminated | None = None
    capture_error: BaseException | None = None
    try:
        event = broker.read(startup_timeout)
        if not isinstance(event, BrokerReady):
            raise ControllerRunError(f"first Reploy event was {event.type!r}")
        attachment = _Attachment(session_client, event.terminal_socket, output_dir)
        attachment.bootstrap(manifest)
        opened = broker.read(startup_timeout)
        if not isinstance(opened, Opened):
            raise ControllerRunError(f"Reploy event was {opened.type!r}, expected 'opened'")
        required = {"complete", "terminate"}
        if not required.issubset(opened.operations):
            raise ControllerRunError("Reploy session does not grant required lifecycle operations")
        if (opened.columns, opened.rows) != (manifest.columns, manifest.rows):
            raise ControllerRunError("Reploy terminal dimensions do not match the run manifest")
        ready = broker.read(startup_timeout)
        if not isinstance(ready, Ready):
            raise ControllerRunError(f"Reploy event was {ready.type!r}, expected 'ready'")
        try:
            capture(ControllerContext(manifest, opened, output_dir))
        except BaseException as exc:
            capture_error = exc
            broker.write("terminate")
        finalization: WorkloadOutputsFinalized | None = None
        deadline = time.monotonic() + lifecycle_timeout
        while finalization is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControllerRunError(
                    "timed out waiting for workload output finalization"
                )
            event = broker.read(remaining)
            if isinstance(event, (ReployDiagnostic, WorkloadExit, Terminating)):
                continue
            if isinstance(event, ClientError):
                raise ControllerRunError(
                    f"Reploy session client error {event.code!r}: {event.message}"
                )
            if isinstance(event, WorkloadOutputsFinalized):
                finalization = event
                break
            raise ControllerRunError(
                f"unexpected Reploy event {event.type!r} before output finalization"
            )
        broker.write("complete")
        event = broker.read(lifecycle_timeout)
        if not isinstance(event, Terminated):
            raise ControllerRunError(f"Reploy event was {event.type!r}, expected 'terminated'")
        terminated = event
        (output_dir / "reploy-terminated.json").write_text(
            json.dumps(_lifecycle_json(event.result), separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        client_error: ControllerRunError | None = None
        try:
            broker.write("acknowledge-terminated")
            broker.finish()
        except ControllerRunError as exc:
            client_error = exc
        attachment_error: ControllerRunError | None = None
        try:
            attachment.close()
        except ControllerRunError as exc:
            attachment_error = exc
        if capture_error is not None:
            detail = f"capture failed: {capture_error}"
            if finalization.status != "drained":
                detail += (
                    "; workload output finalization also failed: "
                    f"{finalization.reason}"
                )
            if client_error is not None:
                detail += f"; Reploy session client also failed: {client_error}"
            if attachment_error is not None:
                detail += f"; bootstrap attachment also failed: {attachment_error}"
            raise ControllerRunError(detail) from capture_error
        if finalization.status != "drained":
            detail = f"Reploy workload output finalization failed: {finalization.reason}"
            if client_error is not None:
                detail += f"; session client also failed: {client_error}"
            if attachment_error is not None:
                detail += f"; bootstrap attachment also failed: {attachment_error}"
            raise ControllerRunError(detail)
        if client_error is not None:
            detail = str(client_error)
            if attachment_error is not None:
                detail += f"; bootstrap attachment also failed: {attachment_error}"
            raise ControllerRunError(detail) from client_error
        if attachment_error is not None:
            raise attachment_error
        return event.result
    finally:
        broker.abort()
        if attachment is not None:
            attachment.abort()
        if terminated is None and capture_error is not None:
            (output_dir / "controller-error.txt").write_text(
                f"{capture_error}\n", encoding="utf-8"
            )


def controller_main(
    manifest_path: Path = Path("/omegaflow-input/run-manifest.json"),
    *,
    output_dir: Path | None = None,
    capture: Callable[[ControllerContext], None] | None = None,
) -> int:
    """Run the bounded internal controller command."""

    destination = output_dir
    if destination is None:
        configured = os.environ.get("REPLOY_OUTPUT_DIR")
        if not configured:
            raise ControllerRunError("REPLOY_OUTPUT_DIR is required")
        destination = Path(configured)
    resolved_destination = destination.resolve()
    resolved_input = manifest_path.parent.resolve()
    if (
        resolved_destination == resolved_input
        or resolved_destination in resolved_input.parents
        or resolved_input in resolved_destination.parents
    ):
        raise ControllerRunError("controller input and output directories overlap")
    try:
        manifest = load_controller_manifest(manifest_path)
        if capture is None:
            capture = capture_controller_recording
        run_controller_session(manifest, destination, capture)
        return 0
    except BaseException as exc:
        try:
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            (destination / "controller-error.json").write_text(
                json.dumps(
                    {
                        "schema": "omegaflow-controller-error-v1",
                        "type": type(exc).__name__,
                        "message": str(exc)[:4096],
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        raise


def capture_controller_recording(context: ControllerContext) -> None:
    """Capture and finalize a publication candidate inside the controller."""

    from . import presentation_build
    from .browser_capture import PersistentBrowserRunner
    from .envoy_terminal_capture import EnvoyPersistentTerminalRunner
    from .recording_plan import (
        BrowserActionPlan,
        capture_runner_beat,
        captured_pane_beats,
    )

    spec = context.manifest.recording_plan
    try:
        plan = normalize_recording_plan(spec)
    except RecordingPlanError as exc:  # already validated; protects API callers
        raise ControllerRunError(f"recording_plan is invalid: {exc}") from exc
    _validate_controller_recording_capabilities(spec, plan)
    captured = captured_pane_beats(plan)
    terminal_panes = {item.pane_id for item in captured if item.kind.value == "terminal"}
    if len(terminal_panes) > 1:
        raise ControllerRunError("Reploy migration currently supports one terminal pane")
    browser_panes = {item.pane_id for item in captured if item.kind.value == "browser"}
    if len(browser_panes) > 1:
        raise ControllerRunError("Reploy migration currently supports one browser pane")

    browser_config: dict[str, Any] | None = None
    if plan.browser is not None:
        if plan.browser_handoffs:
            raise ControllerRunError(
                "Reploy browser capture does not accept workload-selected handoff URLs"
            )
        browser_config = dict(plan.browser)
        endpoint_id = browser_config.pop("endpoint_id", None)
        if not isinstance(endpoint_id, str):  # protected by manifest validation
            raise ControllerRunError("browser.endpoint_id is required")
        endpoint = context.endpoint(endpoint_id)
        if endpoint.scheme not in {"http", "https"}:
            raise ControllerRunError(
                f"browser endpoint {endpoint_id!r} must use http or https"
            )
        browser_config["base_url"] = _endpoint_base_url(endpoint)
        for item in captured:
            if item.kind.value != "browser":
                continue
            beat = capture_runner_beat(plan, item)
            for action in beat.actions:
                if not isinstance(action, BrowserActionPlan) or action.kind != "open_page":
                    continue
                payload = action.config["open_page"]
                capture_url = payload.get("url")
                if capture_url is not None:
                    _validate_relative_browser_url(capture_url)
                if payload.get("handoff") is not None:
                    raise ControllerRunError(
                        "Reploy browser capture does not accept workload-selected handoff URLs"
                    )

    capture_config = spec.get("capture", {})
    if not isinstance(capture_config, dict):
        raise ControllerRunError("recording capture config must be a mapping")
    options = presentation_build._terminal_capture_options(spec)
    _, environment = presentation_build._capture_environment(spec)

    def terminal_runner(_pane_id: str | None) -> EnvoyPersistentTerminalRunner:
        return EnvoyPersistentTerminalRunner(
            lambda capture_context: context.open_envoy_session(
                title=plan.title or plan.id,
                output_dir=capture_context.runner_capture,
            ),
            color=environment.get("NO_COLOR") is None,
            typing=bool(options["typing"]),
            typing_min_delay=float(options.get("typing_min_delay", 0.012)),
            typing_max_delay=float(options.get("typing_max_delay", 0.045)),
            typing_space_delay=float(options.get("typing_space_delay", 0.025)),
            typing_punctuation_delay=float(options.get("typing_punctuation_delay", 0.05)),
            typing_newline_delay=float(options.get("typing_newline_delay", 0.16)),
            typing_seed=int(options.get("typing_seed", 17)),
            post_enter_pause=float(options.get("post_enter_pause", 0.0)),
            post_command_pause=float(options.get("post_command_pause", 0.0)),
            timeout_seconds=float(capture_config.get("timeout", 30.0)),
        )

    presentation_build.capture_recording(
        spec,
        plan,
        context.output_dir,
        headed=False,
        terminal_runner_factory=terminal_runner,
        browser_runner_factory=(
            None
            if browser_config is None
            else lambda: PersistentBrowserRunner(browser_config, headless=True)
        ),
    )
    audio_artifacts = presentation_build.prepare_narration_audio(
        spec,
        plan,
        context.output_dir,
    )
    presentation_build.compile_presentation_bundle(
        spec,
        plan,
        context.output_dir,
        audio_artifacts=audio_artifacts,
    )


def _validate_controller_recording_capabilities(
    spec: Mapping[str, Any], plan: RecordingPlan
) -> None:
    if declared_recording_source_inputs(spec):
        raise ControllerRunError(
            "Reploy controller does not accept path-based recording inputs without "
            "an approved controller-asset staging boundary"
        )
    audio_config = spec.get("audio")
    if (
        isinstance(audio_config, Mapping)
        and audio_config.get("enabled") is True
        and plan.narration_takes
    ):
        raise ControllerRunError(
            "Reploy controller does not accept enabled narration without an approved "
            "staging or secret-delegation boundary"
        )
    browser_config = spec.get("browser")
    auth_config = (
        browser_config.get("auth") if isinstance(browser_config, Mapping) else None
    )
    if isinstance(auth_config, Mapping) and any(
        auth_config.get(name) not in (None, "")
        for name in ("storage_state_env", "storage_state_path")
    ):
        raise ControllerRunError(
            "Reploy controller does not accept browser authentication inputs without "
            "an approved asset or secret-delegation boundary"
        )


def _endpoint_base_url(endpoint: ReployEndpoint) -> str:
    host = endpoint.host
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise ControllerRunError(
                f"endpoint {endpoint.id!r} has an invalid host"
            ) from exc
        rendered_host = f"[{host}]"
    elif re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", host):
        rendered_host = host
    else:
        raise ControllerRunError(f"endpoint {endpoint.id!r} has an invalid host")
    return f"{endpoint.scheme}://{rendered_host}:{endpoint.port}/"


def _validate_relative_browser_url(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ControllerRunError("Reploy browser open_page URLs must be non-empty")
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ControllerRunError(
            "Reploy browser open_page URLs must be relative to the granted endpoint"
        )


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ControllerRunError(f"{name} is not a valid identifier")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ControllerRunError(f"{name} must be between {minimum} and {maximum}")
    return value


def _exact_object(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ControllerRunError(f"{name} fields do not match v1")
    return value


def _asset(value: Any) -> ControllerAsset:
    item = _exact_object(value, {"name", "path", "size", "sha256"}, "asset")
    name = _identifier(item["name"], "asset.name")
    path = item["path"]
    if not isinstance(path, str):
        raise ControllerRunError("asset.path must be a string")
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or "." in pure.parts:
        raise ControllerRunError("asset.path must be a normalized relative path")
    size = _integer(item["size"], "asset.size", 0, 1 << 30)
    digest = item["sha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ControllerRunError("asset.sha256 must be lowercase SHA-256")
    return ControllerAsset(name, path, size, digest)


def _validate_asset(root: Path, asset: ControllerAsset) -> None:
    try:
        root_stat = root.lstat()
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ControllerRunError(
            "controller assets root is not a contained directory"
        ) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise ControllerRunError(
            "controller assets root is not a contained directory"
        )
    root = resolved_root
    candidate = root / asset.path
    try:
        candidate_stat = candidate.lstat()
        path = candidate.resolve(strict=True)
    except OSError as exc:
        raise ControllerRunError(
            f"controller asset {asset.name!r} is not a contained regular file"
        ) from exc
    if (
        stat.S_ISLNK(candidate_stat.st_mode)
        or not stat.S_ISREG(candidate_stat.st_mode)
        or root not in path.parents
    ):
        raise ControllerRunError(
            f"controller asset {asset.name!r} is not a contained regular file"
        )
    if path.stat().st_size != asset.size:
        raise ControllerRunError(f"controller asset {asset.name!r} size does not match")
    if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
        raise ControllerRunError(f"controller asset {asset.name!r} hash does not match")


def _status_json(value: Any) -> dict[str, Any]:
    result = {"kind": value.kind}
    for name in ("code", "reason", "message"):
        item = getattr(value, name)
        if item is not None:
            result[name] = item
    return result


def _lifecycle_json(value: ReployLifecycleResult) -> dict[str, Any]:
    return {
        "cause": value.cause,
        "workload_status": _status_json(value.workload_status),
        "workload_output_finalization_status": _status_json(
            value.workload_output_finalization_status
        ),
        "runtime_observation_status": _status_json(value.runtime_observation_status),
        "controller_finalization_status": _status_json(value.controller_finalization_status),
        "cleanup_status": _status_json(value.cleanup_status),
        "recovery_action": value.recovery_action,
    }
