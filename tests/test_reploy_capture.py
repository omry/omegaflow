from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from omegaconf import open_dict

from omegaflow.controller_run import ControllerRunManifest
from omegaflow.reploy_blueprint import Endpoint
from omegaflow.reploy_capture import (
    PreparedReployRun,
    ReployCaptureError,
    capture_recording_via_reploy,
    prepare_reploy_run,
    run_prepared_reploy_session,
)
from omegaflow.studio_config import compose_studio_hydra_config
from omegaflow.workload_runtime import RuntimeManifest


FIXTURES = Path(__file__).parent / "fixtures" / "reploy-controlled-session-v1"


def _manifest() -> ControllerRunManifest:
    return ControllerRunManifest(
        "demo",
        {"id": "demo", "beats": []},
        80,
        24,
        "omegaflow-terminal",
        "omegaflow-telemetry",
        (),
        (),
    )


def _prepared(tmp_path: Path) -> PreparedReployRun:
    root = tmp_path / "reploy"
    controller = root / "deployments" / "controller"
    workload = root / "deployments" / "workload"
    evidence = root / "host"
    controller.mkdir(parents=True)
    workload.mkdir()
    evidence.mkdir()
    runtime = RuntimeManifest(
        "omegaflow-runtime-manifest-v1",
        "0.9.0",
        "0" * 40,
        "omegaflow-envoy-telemetry-v1",
        "awsh-v1",
        "linux",
        "amd64",
        "go1.25.0",
        (),
    )
    return PreparedReployRun(
        root,
        runtime,
        root / "blueprints" / "controller.blueprint.yaml",
        root / "blueprints" / "workload.blueprint.yaml",
        controller,
        workload,
        root / "input" / "controller" / "run-manifest.json",
        root / "output",
        evidence,
    )


def test_public_controlled_session_command_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path)
    success = (FIXTURES / "run-results.jsonl").read_bytes().splitlines()[1] + b"\n"
    observed: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=success, stderr=b"host details\n")

    monkeypatch.setattr(subprocess, "run", run)
    manifest = replace(_manifest(), application_endpoint_ids=("web",))
    result = run_prepared_reploy_session(
        prepared, manifest, reploy_command="/usr/bin/reploy"
    )

    assert result.ok
    assert observed == [
        [
            "/usr/bin/reploy",
            "controlled-session",
            "run",
            "--controller-dir",
            str(prepared.controller_deployment),
            "--workload-dir",
            str(prepared.workload_deployment),
            "--columns",
            "80",
            "--rows",
            "24",
            "--output-dir",
            str(prepared.output_dir),
            "--endpoint",
            "omegaflow-terminal",
            "--endpoint",
            "omegaflow-telemetry",
            "--endpoint",
            "web",
            "--",
            "omegaflow_controller_run",
        ]
    ]
    assert (prepared.host_evidence_dir / "controlled-session.stderr.log").read_bytes() == b"host details\n"


def test_prepare_stages_runtime_and_builds_both_resolved_blueprints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = compose_studio_hydra_config(
        None, (f"studio.run_dir={run_dir}",)
    )
    runtime = RuntimeManifest(
        "omegaflow-runtime-manifest-v1",
        "0.9.0",
        "0" * 40,
        "omegaflow-envoy-telemetry-v1",
        "awsh-v1",
        "linux",
        "amd64",
        "go1.25.0",
        (),
    )

    def stage_runtime(destination: Path) -> RuntimeManifest:
        destination.mkdir(parents=True)
        return runtime

    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr("omegaflow.reploy_capture.stage_workload_runtime", stage_runtime)
    monkeypatch.setattr(subprocess, "run", run)
    prepared = prepare_reploy_run(
        config,
        run_dir,
        _manifest(),
        reploy_command="/usr/bin/reploy",
    )

    assert prepared.runtime is runtime
    assert prepared.controller_blueprint.is_file()
    assert prepared.workload_blueprint.is_file()
    assert prepared.controller_manifest.is_file()
    assert [command[1] for command in commands] == ["stage", "build", "stage", "build"]
    assert str(prepared.controller_deployment) in commands[0]
    assert str(prepared.workload_deployment) in commands[2]


def test_capture_entrypoint_uses_composed_config_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    config = compose_studio_hydra_config(
        None, (f"studio.run_dir={run_dir}",)
    )
    prepared = _prepared(tmp_path)
    expected = object()
    observed: list[tuple[object, object, object]] = []

    def prepare(
        received_config: object,
        received_run_dir: object,
        manifest: object,
        **_: object,
    ) -> PreparedReployRun:
        observed.append((received_config, received_run_dir, manifest))
        return prepared

    monkeypatch.setattr("omegaflow.reploy_capture.prepare_reploy_run", prepare)
    monkeypatch.setattr(
        "omegaflow.reploy_capture.run_prepared_reploy_session",
        lambda *_args, **_kwargs: expected,
    )
    result = capture_recording_via_reploy(
        config,
        {"id": "demo", "capture": {"window_size": "90x30"}, "beats": []},
        run_dir,
        reploy_command="reploy",
    )

    assert result.prepared is prepared
    assert result.run_result is expected
    assert observed[0][0] is config
    assert observed[0][1] == run_dir
    manifest = observed[0][2]
    assert manifest.columns == 90
    assert manifest.rows == 30


def test_capture_entrypoint_rejects_enabled_narration_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = compose_studio_hydra_config(None)
    staged = False

    def prepare(*_args: object, **_kwargs: object) -> PreparedReployRun:
        nonlocal staged
        staged = True
        return _prepared(tmp_path)

    monkeypatch.setattr("omegaflow.reploy_capture.prepare_reploy_run", prepare)

    with pytest.raises(ReployCaptureError, match="does not yet support enabled narration"):
        capture_recording_via_reploy(
            config,
            {
                "id": "narrated-demo",
                "audio": {"enabled": True},
                "beats": [
                    {
                        "id": "intro",
                        "narration": "Introduce the demo.",
                        "actions": [{"run": "true"}],
                    }
                ],
            },
            tmp_path / "run",
            reploy_command="reploy",
        )

    assert staged is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("storage_state_env", "BROWSER_STORAGE_STATE"),
        ("storage_state_path", ".private/browser-state.json"),
    ),
)
def test_capture_entrypoint_rejects_browser_auth_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    config = compose_studio_hydra_config(None)
    with open_dict(config.reploy.workload.environment.workload.endpoints):
        config.reploy.workload.environment.workload.endpoints["web"] = Endpoint(
            scheme="http", port=8080
        )
    staged = False

    def prepare(*_args: object, **_kwargs: object) -> PreparedReployRun:
        nonlocal staged
        staged = True
        return _prepared(tmp_path)

    monkeypatch.setattr("omegaflow.reploy_capture.prepare_reploy_run", prepare)

    with pytest.raises(ReployCaptureError, match="browser authentication inputs"):
        capture_recording_via_reploy(
            config,
            {
                "id": "authenticated-browser-demo",
                "browser": {
                    "endpoint_id": "web",
                    "auth": {field: value},
                },
                "beats": [
                    {
                        "id": "browser",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/private"}}
                        ],
                    }
                ],
            },
            tmp_path / "run",
            reploy_command="reploy",
        )

    assert staged is False


def test_capture_entrypoint_selects_declared_browser_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    config = compose_studio_hydra_config(None, (f"studio.run_dir={run_dir}",))
    with open_dict(config.reploy.workload.environment.workload.endpoints):
        config.reploy.workload.environment.workload.endpoints["web"] = Endpoint(
            scheme="http", port=8080
        )
    prepared = _prepared(tmp_path)
    observed: list[ControllerRunManifest] = []

    monkeypatch.setattr(
        "omegaflow.reploy_capture.prepare_reploy_run",
        lambda _config, _run_dir, manifest, **_kwargs: (
            observed.append(manifest) or prepared
        ),
    )
    monkeypatch.setattr(
        "omegaflow.reploy_capture.run_prepared_reploy_session",
        lambda *_args, **_kwargs: object(),
    )

    capture_recording_via_reploy(
        config,
        {
            "id": "browser-demo",
            "browser": {"endpoint_id": "web"},
            "beats": [
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [{"id": "open", "open_page": {"url": "/demo"}}],
                }
            ],
        },
        run_dir,
        reploy_command="reploy",
    )

    assert observed[0].application_endpoint_ids == ("web",)


def test_capture_entrypoint_rejects_undeclared_or_host_browser_url(
    tmp_path: Path,
) -> None:
    config = compose_studio_hydra_config(None)
    spec = {
        "id": "browser-demo",
        "browser": {"endpoint_id": "web"},
        "beats": [
            {
                "id": "browser",
                "medium": "browser",
                "actions": [{"id": "open", "open_page": {"url": "/"}}],
            }
        ],
    }
    with pytest.raises(ReployCaptureError, match="not declared"):
        capture_recording_via_reploy(config, spec, tmp_path / "run", reploy_command="reploy")

    with open_dict(config.reploy.workload.environment.workload.endpoints):
        config.reploy.workload.environment.workload.endpoints["web"] = Endpoint(
            scheme="http", port=8080
        )
    spec["browser"] = {
        "endpoint_id": "web",
        "base_url": "http://substituted.invalid",
    }
    with pytest.raises(ReployCaptureError, match="opened.endpoints"):
        capture_recording_via_reploy(config, spec, tmp_path / "run", reploy_command="reploy")


def test_capture_entrypoint_compiles_run_files_and_removes_host_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    source_dir = tmp_path / "recording"
    source_dir.mkdir()
    (source_dir / "command.sh").write_text("printf portable\n", encoding="utf-8")
    config = compose_studio_hydra_config(None, (f"studio.run_dir={run_dir}",))
    prepared = _prepared(tmp_path)
    observed: list[ControllerRunManifest] = []

    def prepare(
        _config: object,
        _run_dir: object,
        manifest: ControllerRunManifest,
        **_: object,
    ) -> PreparedReployRun:
        observed.append(manifest)
        return prepared

    monkeypatch.setattr("omegaflow.reploy_capture.prepare_reploy_run", prepare)
    monkeypatch.setattr(
        "omegaflow.reploy_capture.run_prepared_reploy_session",
        lambda *_args, **_kwargs: object(),
    )
    capture_recording_via_reploy(
        config,
        {
            "id": "demo",
            "_script_dir": str(source_dir),
            "_host_only": object(),
            "beats": [
                {
                    "id": "one",
                    "actions": [{"run_file": "command.sh"}],
                }
            ],
        },
        run_dir,
        reploy_command="reploy",
    )

    payload = observed[0].recording_plan
    assert all(not key.startswith("_") for key in payload)
    command = payload["beats"][0]["actions"][0]
    assert command["run"] == "printf portable\n"
    assert "run_file" not in command


def test_structured_failure_overrides_zero_host_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path)
    failed = (FIXTURES / "run-results.jsonl").read_bytes().splitlines()[0] + b"\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(
            command, 0, stdout=failed, stderr=b""
        ),
    )

    with pytest.raises(ReployCaptureError, match="admission rejected"):
        run_prepared_reploy_session(
            prepared, _manifest(), reploy_command="/usr/bin/reploy"
        )


def test_rejects_nonexclusive_host_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path)
    success = (FIXTURES / "run-results.jsonl").read_bytes().splitlines()[1]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(
            command, 0, stdout=b"progress\n" + success + b"\n", stderr=b""
        ),
    )

    with pytest.raises(ReployCaptureError, match="exactly one"):
        run_prepared_reploy_session(
            prepared, _manifest(), reploy_command="/usr/bin/reploy"
        )
