from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from omegaconf import OmegaConf, open_dict
from omegaconf.errors import ReadonlyConfigError

from omegaflow.reploy_blueprint import (
    Mount,
    ReployBlueprintError,
    blueprint_mapping,
    blueprint_yaml,
    write_reploy_blueprints,
)
from omegaflow.studio_config import compose_studio_hydra_config


def test_hydra_composes_complete_controller_and_workload_blueprints() -> None:
    config = compose_studio_hydra_config(None)

    assert config.reploy.controller.environment.id == "omegaflow-controller"
    assert OmegaConf.is_readonly(config.reploy.controller)
    assert config.reploy.workload.environment.id == "omegaflow-internal-demo"
    assert config.reploy.workload.environment.base.image == "debian:13"
    assert config.reploy.workload.environment.runtime.user == "omegaflow"
    assert set(config.reploy.workload.environment.workload.endpoints) == {
        "omegaflow-terminal",
        "omegaflow-telemetry",
    }
    assert list(config.reploy.workload.environment.commands.omegaflow_terminal.argv) == [
        "-c",
        'exec "$1" --terminal-listen "$2:$3" --telemetry-listen "$4:$5" '
        "--columns 80 --rows 24",
        "omegaflow-envoy",
        "/omegaflow-runtime/bin/envoy",
        "0.0.0.0",
        "47001",
        "0.0.0.0",
        "47002",
    ]
    assert (
        config.reploy.workload.docker.mounts.omegaflow_runtime.source
        == f"{config.studio.run_dir}/reploy/input/runtime"
    )


def test_controller_is_readonly_only_after_hydra_overrides_are_composed() -> None:
    config = compose_studio_hydra_config(
        None,
        ("reploy.controller.environment.runtime.user=controller-user",),
    )

    assert config.reploy.controller.environment.runtime.user == "controller-user"
    with pytest.raises(ReadonlyConfigError):
        config.reploy.controller.environment.id = "replacement"


def test_workload_remains_structurally_typed_and_user_overridable() -> None:
    config = compose_studio_hydra_config(
        None,
        (
            "reploy.workload.environment.runtime.user=demo-user",
            "reploy.workload.environment.workload.endpoints.omegaflow-terminal.port=48001",
        ),
    )

    assert config.reploy.workload.environment.runtime.user == "demo-user"
    assert (
        config.reploy.workload.environment.workload.endpoints[
            "omegaflow-terminal"
        ].port
        == 48001
    )
    assert config.reploy.workload.environment.commands.omegaflow_terminal.argv[5] == "48001"
    with pytest.raises(Exception):
        compose_studio_hydra_config(
            None,
            ("reploy.workload.environment.unknown_field=true",),
        )


def test_blueprint_serialization_is_native_reploy_yaml() -> None:
    config = compose_studio_hydra_config(None)
    data = blueprint_mapping(config.reploy.workload)
    rendered = yaml.safe_load(blueprint_yaml(config.reploy.workload))

    assert rendered == data
    assert rendered["blueprint"]["schema"] == 1
    assert rendered["environment"]["mounts"]["omegaflow_runtime"]["writable"] is False
    assert "readiness" not in rendered["environment"]["workload"]["endpoints"]["omegaflow-terminal"]


def test_write_reploy_blueprints_retains_both_and_requires_fresh_roots(
    tmp_path: Path,
) -> None:
    config = compose_studio_hydra_config(None)
    controller, workload = write_reploy_blueprints(config, tmp_path)

    controller_data = yaml.safe_load(controller.read_text(encoding="utf-8"))
    workload_data = yaml.safe_load(workload.read_text(encoding="utf-8"))
    assert controller_data["environment"]["id"] == "omegaflow-controller"
    assert workload_data["environment"]["id"] == "omegaflow-internal-demo"
    assert (tmp_path / "reploy" / "deployments").is_dir()
    assert not (tmp_path / "reploy" / "deployments" / "controller").exists()
    assert not (tmp_path / "reploy" / "deployments" / "workload").exists()
    with pytest.raises(ReployBlueprintError, match="must start fresh"):
        write_reploy_blueprints(config, tmp_path)


def test_workload_application_mount_cannot_overlap_reserved_runtime_paths(
    tmp_path: Path,
) -> None:
    config = compose_studio_hydra_config(None)
    with open_dict(config.reploy.workload.environment.mounts):
        config.reploy.workload.environment.mounts["application"] = Mount(
            target="/omegaflow-runtime/project",
            writable=True,
            update_policy="unmanaged",
        )

    with pytest.raises(ReployBlueprintError, match="overlaps reserved path"):
        write_reploy_blueprints(config, tmp_path)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (
            "reploy.workload.environment.mounts.omegaflow_runtime.writable",
            True,
            "protected read-only",
        ),
        (
            "reploy.workload.environment.mounts.omegaflow_runtime.update_policy",
            "replace",
            "protected read-only",
        ),
        (
            "reploy.workload.docker.mounts.omegaflow_runtime.mode",
            "tmpfs",
            "must bind the verified staged",
        ),
        (
            "reploy.workload.docker.mounts.omegaflow_runtime.source",
            "/tmp/unverified-runtime",
            "must bind the verified staged",
        ),
    ),
)
def test_reserved_runtime_mount_must_match_the_verified_staged_payload(
    tmp_path: Path,
    path: str,
    value: object,
    message: str,
) -> None:
    config = compose_studio_hydra_config(None)
    OmegaConf.update(config, path, value)

    with pytest.raises(ReployBlueprintError, match=message):
        write_reploy_blueprints(config, tmp_path)


def test_resolved_blueprints_pass_installed_reploy_validation(tmp_path: Path) -> None:
    reploy = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "reploy"
    if not reploy.is_file():
        pytest.skip("installed Reploy CLI is not available")
    config = compose_studio_hydra_config(None)
    for name in ("controller", "workload"):
        path = tmp_path / f"{name}.blueprint.yaml"
        path.write_text(blueprint_yaml(config.reploy[name]), encoding="utf-8")
        result = subprocess.run(
            [str(reploy), "validate", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
