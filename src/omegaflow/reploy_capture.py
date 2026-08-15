"""Host-side materialization and public Reploy controlled-session launch."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from omegaconf import DictConfig

from . import record
from .controller_run import ControllerRunManifest, write_controller_manifest
from .reploy_blueprint import write_reploy_blueprints
from .reploy_protocol import ReployRunResult, decode_run_result
from .recording_plan import RecordingPlanError, normalize_recording_plan
from .workload_runtime import RuntimeManifest, stage_workload_runtime


class ReployCaptureError(RuntimeError):
    """Host materialization or authoritative Reploy execution failed."""


_ENDPOINT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True)
class PreparedReployRun:
    root: Path
    runtime: RuntimeManifest
    controller_blueprint: Path
    workload_blueprint: Path
    controller_deployment: Path
    workload_deployment: Path
    controller_manifest: Path
    output_dir: Path
    host_evidence_dir: Path


@dataclass(frozen=True)
class ReployCaptureResult:
    prepared: PreparedReployRun
    run_result: ReployRunResult


def capture_recording_via_reploy(
    config: DictConfig,
    spec: Mapping[str, Any],
    run_dir: Path,
    *,
    reploy_command: str | Path | None = None,
) -> ReployCaptureResult:
    """Execute one already-composed recording through public Reploy APIs."""

    recording_id = spec.get("id")
    if not isinstance(recording_id, str) or not recording_id:
        raise ReployCaptureError("recording id must be a non-empty string")
    capture = spec.get("capture", {})
    if not isinstance(capture, Mapping):
        raise ReployCaptureError("recording capture config must be a mapping")
    window_size = capture.get("window_size", "100x28")
    if not isinstance(window_size, str):
        raise ReployCaptureError("capture.window_size must be COLUMNSxROWS")
    match = re.fullmatch(r"([1-9][0-9]{0,3})x([1-9][0-9]{0,3})", window_size)
    if match is None:
        raise ReployCaptureError("capture.window_size must be COLUMNSxROWS")
    columns, rows = (int(match.group(1)), int(match.group(2)))
    if columns > 1000 or rows > 1000:
        raise ReployCaptureError("capture.window_size exceeds Envoy limits")
    try:
        plan = normalize_recording_plan(dict(spec))
    except RecordingPlanError as exc:
        raise ReployCaptureError(f"recording plan is invalid: {exc}") from exc
    _validate_reploy_capture_capabilities(spec, plan)
    plan_payload = _portable_recording_plan(spec)
    try:
        normalize_recording_plan(plan_payload)
    except RecordingPlanError as exc:  # pragma: no cover - compilation invariant
        raise ReployCaptureError(f"compiled recording plan is invalid: {exc}") from exc
    application_endpoint_ids = _browser_application_endpoint_ids(config, spec, plan)
    manifest = ControllerRunManifest(
        recording_id,
        plan_payload,
        columns,
        rows,
        "omegaflow-terminal",
        "omegaflow-telemetry",
        application_endpoint_ids,
        (),
    )
    prepared = prepare_reploy_run(
        config,
        run_dir,
        manifest,
        reploy_command=reploy_command,
    )
    result = run_prepared_reploy_session(
        prepared,
        manifest,
        reploy_command=reploy_command,
    )
    return ReployCaptureResult(prepared, result)


def _validate_reploy_capture_capabilities(spec: Mapping[str, Any], plan: Any) -> None:
    audio_config = spec.get("audio")
    narration_enabled = (
        isinstance(audio_config, Mapping) and audio_config.get("enabled") is True
    )
    if narration_enabled and plan.narration_takes:
        raise ReployCaptureError(
            "Reploy capture does not yet support enabled narration; secret-dependent "
            "narration requires an approved staging or delegation boundary"
        )


def _browser_application_endpoint_ids(
    config: DictConfig,
    spec: Mapping[str, Any],
    plan: Any,
) -> tuple[str, ...]:
    if plan.browser is None:
        return ()
    browser = spec.get("browser")
    if not isinstance(browser, Mapping):
        raise ReployCaptureError("browser config must be a mapping")
    endpoint_id = browser.get("endpoint_id")
    if not isinstance(endpoint_id, str) or not _ENDPOINT_ID_RE.fullmatch(endpoint_id):
        raise ReployCaptureError(
            "Reploy browser capture requires a valid browser.endpoint_id"
        )
    if browser.get("base_url") not in (None, ""):
        raise ReployCaptureError(
            "Reploy browser capture derives browser.base_url from opened.endpoints; "
            "configure browser.endpoint_id instead"
        )
    endpoints = config.reploy.workload.environment.workload.endpoints
    if endpoint_id not in endpoints:
        raise ReployCaptureError(
            f"browser endpoint {endpoint_id!r} is not declared by the workload blueprint"
        )
    endpoint = endpoints[endpoint_id]
    if endpoint.scheme not in {"http", "https"}:
        raise ReployCaptureError(
            f"browser endpoint {endpoint_id!r} must use http or https"
        )
    return (endpoint_id,)


def prepare_reploy_run(
    config: DictConfig,
    run_dir: Path,
    controller_manifest: ControllerRunManifest,
    *,
    reploy_command: str | Path | None = None,
) -> PreparedReployRun:
    """Stage trusted inputs, retain blueprints, and build two fresh deployments."""

    reploy = _reploy_command(reploy_command)
    root = run_dir / "reploy"
    runtime_path = root / "input" / "runtime"
    controller_input = root / "input" / "controller"
    manifest_path = controller_input / "run-manifest.json"
    if root.exists():
        raise ReployCaptureError(f"Reploy run root must not exist: {root}")
    runtime = stage_workload_runtime(runtime_path)
    write_controller_manifest(manifest_path, controller_manifest)
    controller_blueprint, workload_blueprint = write_reploy_blueprints(config, run_dir)
    controller_deployment = root / "deployments" / "controller"
    workload_deployment = root / "deployments" / "workload"
    host_evidence = root / "host"
    host_evidence.mkdir(mode=0o700)
    _run_logged(
        [reploy, "stage", str(controller_blueprint), "--dir", str(controller_deployment)],
        host_evidence / "controller-stage",
    )
    _run_logged(
        [reploy, "build", "--dir", str(controller_deployment)],
        host_evidence / "controller-build",
    )
    _run_logged(
        [reploy, "stage", str(workload_blueprint), "--dir", str(workload_deployment)],
        host_evidence / "workload-stage",
    )
    _run_logged(
        [reploy, "build", "--dir", str(workload_deployment)],
        host_evidence / "workload-build",
    )
    return PreparedReployRun(
        root,
        runtime,
        controller_blueprint,
        workload_blueprint,
        controller_deployment,
        workload_deployment,
        manifest_path,
        root / "output",
        host_evidence,
    )


def run_prepared_reploy_session(
    prepared: PreparedReployRun,
    manifest: ControllerRunManifest,
    *,
    reploy_command: str | Path | None = None,
) -> ReployRunResult:
    """Invoke only Reploy's public host command and validate its exact result."""

    reploy = _reploy_command(reploy_command)
    if prepared.output_dir.exists():
        raise ReployCaptureError("Reploy controller output directory must start absent")
    command = [
        reploy,
        "controlled-session",
        "run",
        "--controller-dir",
        str(prepared.controller_deployment),
        "--workload-dir",
        str(prepared.workload_deployment),
        "--columns",
        str(manifest.columns),
        "--rows",
        str(manifest.rows),
        "--output-dir",
        str(prepared.output_dir),
    ]
    for endpoint_id in (
        manifest.terminal_endpoint_id,
        manifest.telemetry_endpoint_id,
        *manifest.application_endpoint_ids,
    ):
        command.extend(["--endpoint", endpoint_id])
    command.extend(["--", "omegaflow_controller_run"])
    completed = subprocess.run(command, capture_output=True, check=False)
    stdout_path = prepared.host_evidence_dir / "controlled-session.stdout.json"
    stderr_path = prepared.host_evidence_dir / "controlled-session.stderr.log"
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise ReployCaptureError(
            "Reploy controlled-session stdout must contain exactly one result object"
        )
    try:
        result = decode_run_result(lines[0])
    except ValueError as exc:
        raise ReployCaptureError(f"invalid Reploy controlled-session result: {exc}") from exc
    _validate_run_result(result, process_status=completed.returncode)
    return result


def _validate_run_result(result: ReployRunResult, *, process_status: int) -> None:
    failures: list[str] = []
    if process_status != 0:
        failures.append(f"host process exited {process_status}")
    if not result.ok:
        failures.append(result.error or "structured result is unsuccessful")
    if result.result_delivered is not True:
        failures.append("terminated result was not delivered")
    if result.result_acknowledged is not True:
        failures.append("terminated result was not acknowledged")
    if result.controller_status is None or (
        result.controller_status.kind != "exited" or result.controller_status.code != 0
    ):
        failures.append("controller did not exit successfully")
    if result.controller_output is None or result.controller_output.kind != "directory-retained":
        failures.append("controller output directory was not retained")
    if result.delivery_tail_cleanup_status is None or (
        result.delivery_tail_cleanup_status.kind != "succeeded"
    ):
        failures.append("delivery-tail cleanup did not succeed")
    if result.delivery_tail_recovery_action != "none":
        failures.append("delivery-tail recovery action is required")
    session = result.session_result
    if session is None:
        failures.append("authoritative session result is missing")
    else:
        if session.controller_finalization_status.kind != "completed":
            failures.append("controller finalization did not complete")
        if session.workload_output_finalization_status.kind != "drained":
            failures.append("workload output did not drain")
        if session.cleanup_status.kind != "succeeded":
            failures.append("session cleanup did not succeed")
        if session.recovery_action != "none":
            failures.append("session recovery action is required")
    if failures:
        raise ReployCaptureError("Reploy controlled session failed: " + "; ".join(failures))


def _reploy_command(value: str | Path | None) -> str:
    if value is not None:
        return str(value)
    found = shutil.which("reploy")
    if found is None:
        raise ReployCaptureError("reploy executable is not installed")
    return found


def _run_logged(command: list[str], prefix: Path) -> None:
    try:
        completed = subprocess.run(command, capture_output=True, check=False)
    except OSError as exc:
        rendered = " ".join(command[:2])
        raise ReployCaptureError(f"could not execute {rendered}: {exc}") from exc
    prefix.with_suffix(".stdout.log").write_bytes(completed.stdout)
    prefix.with_suffix(".stderr.log").write_bytes(completed.stderr)
    if completed.returncode != 0:
        rendered = " ".join(command[:2])
        raise ReployCaptureError(
            f"{rendered} failed with status {completed.returncode}; "
            f"see {prefix.with_suffix('.stderr.log')}"
        )


def _portable_recording_plan(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Compile host-only recording inputs into bounded controller JSON data."""

    source_spec = dict(spec)

    def visit(value: Any) -> Any:
        if isinstance(value, Mapping):
            result = {
                str(key): visit(item)
                for key, item in value.items()
                if not str(key).startswith("_") and key != "run_file"
            }
            run_file = value.get("run_file")
            if run_file is not None:
                if not isinstance(run_file, str) or not run_file:
                    raise ReployCaptureError("recording run_file must be a non-empty string")
                if value.get("run") is not None:
                    raise ReployCaptureError("recording step cannot define run and run_file")
                path = record.run_file_path(run_file, source_spec)
                try:
                    result["run"] = path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise ReployCaptureError(
                        f"could not compile recording run_file {path}: {exc}"
                    ) from exc
            return result
        if isinstance(value, (list, tuple)):
            return [visit(item) for item in value]
        return value

    compiled = visit(spec)
    if not isinstance(compiled, dict):  # pragma: no cover - top-level type contract
        raise ReployCaptureError("recording plan must be a mapping")
    try:
        return json.loads(json.dumps(compiled))
    except (TypeError, ValueError) as exc:
        raise ReployCaptureError("compiled recording plan is not JSON serializable") from exc
