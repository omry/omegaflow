"""Typed Reploy blueprint configuration and materialization.

The dataclasses in this module mirror Reploy's public schema-1 YAML syntax.
They intentionally contain no Hydra-specific metadata; Hydra and OmegaConf use
them as structured configuration, while Reploy remains the semantic validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from omegaconf import DictConfig, OmegaConf


@dataclass
class Compatibility:
    platforms: list[str] = field(default_factory=list)


@dataclass
class BlueprintMetadata:
    schema: int = 1
    version: str = ""
    requires_reploy: str = ""
    compatibility: Compatibility = field(default_factory=Compatibility)


@dataclass
class ExecutableExport:
    executable: str = ""


@dataclass
class Base:
    image: str = ""
    exports: dict[str, ExecutableExport] = field(default_factory=dict)


@dataclass
class APTPackageRequest:
    package: str = ""
    exports: dict[str, ExecutableExport] = field(default_factory=dict)


@dataclass
class EnvironmentPackages:
    os: list[APTPackageRequest | str] = field(default_factory=list)


@dataclass
class CommandRequirement:
    command: str = ""
    version: str = ""
    supplier: str = ""


@dataclass
class PythonPackages:
    interpreter: CommandRequirement | None = None
    requirements: list[str] = field(default_factory=list)


@dataclass
class ApplicationPackages:
    os: list[APTPackageRequest | str] = field(default_factory=list)
    python: PythonPackages | None = None


@dataclass
class ApplicationOption:
    description: str = ""
    packages: ApplicationPackages = field(default_factory=ApplicationPackages)


@dataclass
class Executable:
    source: str = ""
    binary: str = ""
    order: list[str] = field(default_factory=list)
    argv_prefix: list[str] = field(default_factory=list)
    argv_suffix: list[str] = field(default_factory=list)


@dataclass
class Application:
    packages: ApplicationPackages = field(default_factory=ApplicationPackages)
    options: dict[str, ApplicationOption] = field(default_factory=dict)
    executables: dict[str, Executable] = field(default_factory=dict)


@dataclass
class RuntimeNetwork:
    public: str = ""
    local: str = ""
    ambiguous: str = ""


@dataclass
class EnvironmentRuntime:
    user: str = ""
    network: RuntimeNetwork = field(default_factory=RuntimeNetwork)


@dataclass
class Terminal:
    color_env: str = ""


@dataclass
class Mount:
    target: str = ""
    writable: bool | str = False
    update_policy: str = ""


@dataclass
class Command:
    executable: str = ""
    trigger: list[str] = field(default_factory=list)
    native_command: bool | str = False
    deployed_command: bool | str = False
    forward_flags: list[str] = field(default_factory=list)
    argv: list[str] = field(default_factory=list)
    order: list[str] = field(default_factory=list)


@dataclass
class Readiness:
    path: str = ""
    timeout: str = ""
    interval: str = ""
    tls_verify: bool | str = False


@dataclass
class Endpoint:
    scheme: str = ""
    port: int | str = 0
    readiness: Readiness | None = None


@dataclass
class Requirements:
    endpoints: list[str] = field(default_factory=list)


@dataclass
class Action:
    environment: list[str] = field(default_factory=list)


@dataclass
class Step:
    requires: Requirements = field(default_factory=Requirements)
    actions: list[Action] = field(default_factory=list)


@dataclass
class RuntimeEvents:
    before_start: list[Step] = field(default_factory=list)
    after_start: list[Step] = field(default_factory=list)
    before_stop: list[Step] = field(default_factory=list)
    after_stop: list[Step] = field(default_factory=list)


@dataclass
class Workload:
    command: str = ""
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    runtime: RuntimeEvents = field(default_factory=RuntimeEvents)


@dataclass
class InstallTarget:
    default_path: str = ""
    default_paths: dict[str, str] = field(default_factory=dict)


@dataclass
class SystemAccount:
    user: str = ""
    group: str = ""
    on_missing: str = ""


@dataclass
class SystemInstall:
    account: SystemAccount = field(default_factory=SystemAccount)


@dataclass
class InstallSuccess:
    lines: list[str] = field(default_factory=list)


@dataclass
class Install:
    target: InstallTarget = field(default_factory=InstallTarget)
    system: SystemInstall = field(default_factory=SystemInstall)
    after_install: list[Step] = field(default_factory=list)
    success: InstallSuccess = field(default_factory=InstallSuccess)


@dataclass
class Environment:
    id: str = ""
    control_script: str = ""
    vars: dict[str, Any] = field(default_factory=dict)
    base: Base = field(default_factory=Base)
    packages: EnvironmentPackages = field(default_factory=EnvironmentPackages)
    applications: dict[str, Application] = field(default_factory=dict)
    allow_concurrent: str = ""
    runtime: EnvironmentRuntime = field(default_factory=EnvironmentRuntime)
    terminal: Terminal = field(default_factory=Terminal)
    install: Install = field(default_factory=Install)
    mounts: dict[str, Mount] = field(default_factory=dict)
    commands: dict[str, Command] = field(default_factory=dict)
    workload: Workload | None = None


@dataclass
class DockerMount:
    extends: str = ""
    mode: str = ""
    source: str = ""
    name: str = ""


@dataclass
class Bind:
    address: str = ""


@dataclass
class Publication:
    address: str = ""
    staging: int | bool | str = False
    deployed: int | bool | str = False


@dataclass
class DockerEndpoint:
    extends: str = ""
    bind: Bind = field(default_factory=Bind)
    publish: Publication = field(default_factory=Publication)


@dataclass
class DockerWorkload:
    restart: str = ""
    endpoints: dict[str, DockerEndpoint] = field(default_factory=dict)


@dataclass
class Docker:
    mounts: dict[str, DockerMount] = field(default_factory=dict)
    workload: DockerWorkload | None = None


@dataclass
class Blueprint:
    blueprint: BlueprintMetadata = field(default_factory=BlueprintMetadata)
    environment: Environment = field(default_factory=Environment)
    docker: Docker = field(default_factory=Docker)


@dataclass
class ReployConfig:
    controller: Blueprint = field(default_factory=Blueprint)
    workload: Blueprint = field(default_factory=Blueprint)


RESERVED_WORKLOAD_PATHS = (
    PurePosixPath("/omegaflow-runtime"),
    PurePosixPath("/run/omegaflow"),
)


class ReployBlueprintError(RuntimeError):
    pass


def _without_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _without_empty(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := _without_empty(item)) is not None]
    return value


def blueprint_mapping(config: Blueprint | DictConfig) -> dict[str, Any]:
    """Resolve a typed blueprint into native Reploy YAML data."""

    node = config if isinstance(config, DictConfig) else OmegaConf.structured(config)
    value = OmegaConf.to_container(node, resolve=True, enum_to_str=True)
    if not isinstance(value, dict):
        raise ReployBlueprintError("Reploy blueprint must resolve to a mapping")
    cleaned = _without_empty(value)
    if not isinstance(cleaned, dict):
        raise ReployBlueprintError("Reploy blueprint must resolve to a mapping")
    return cleaned


def blueprint_yaml(config: Blueprint | DictConfig) -> str:
    """Serialize one final typed blueprint without changing its content."""

    return yaml.safe_dump(
        blueprint_mapping(config),
        sort_keys=False,
        allow_unicode=True,
    )


def validate_workload_reserved_paths(
    config: Blueprint | DictConfig,
    *,
    expected_runtime_source: Path,
) -> None:
    """Require OmegaFlow's mounts and reject application overlap with them."""

    data = blueprint_mapping(config)
    environment = data.get("environment", {})
    mounts = environment.get("mounts", {}) if isinstance(environment, dict) else {}
    runtime_mount = mounts.get("omegaflow_runtime")
    expected_environment_mount = {
        "target": "/omegaflow-runtime",
        "writable": False,
        "update_policy": "unmanaged",
    }
    if runtime_mount != expected_environment_mount:
        raise ReployBlueprintError(
            "workload environment mount 'omegaflow_runtime' must be the "
            "protected read-only OmegaFlow runtime mount"
        )
    run_mount = mounts.get("omegaflow_run")
    expected_run_mount = {
        "target": "/run/omegaflow",
        "writable": True,
        "update_policy": "replace",
    }
    if run_mount != expected_run_mount:
        raise ReployBlueprintError(
            "workload environment mount 'omegaflow_run' must be the protected "
            "writable OmegaFlow run mount"
        )

    docker = data.get("docker", {})
    docker_mounts = docker.get("mounts", {}) if isinstance(docker, dict) else {}
    runtime_docker_mount = docker_mounts.get("omegaflow_runtime")
    expected_docker_mount = {
        "extends": "environment.mounts.omegaflow_runtime",
        "mode": "bind",
        "source": str(expected_runtime_source),
    }
    if runtime_docker_mount != expected_docker_mount:
        raise ReployBlueprintError(
            "workload Docker mount 'omegaflow_runtime' must bind the verified "
            "staged OmegaFlow runtime"
        )
    run_docker_mount = docker_mounts.get("omegaflow_run")
    expected_run_docker_mount = {
        "extends": "environment.mounts.omegaflow_run",
        "mode": "tmpfs",
    }
    if run_docker_mount != expected_run_docker_mount:
        raise ReployBlueprintError(
            "workload Docker mount 'omegaflow_run' must provide the protected "
            "ephemeral OmegaFlow run tmpfs"
        )
    for name, mount in docker_mounts.items():
        if not isinstance(mount, dict):
            continue
        for reserved_name in ("omegaflow_runtime", "omegaflow_run"):
            if (
                name != reserved_name
                and mount.get("extends") == f"environment.mounts.{reserved_name}"
            ):
                raise ReployBlueprintError(
                    f"workload Docker mount {name!r} also extends the reserved "
                    f"OmegaFlow mount {reserved_name!r}"
                )

    allowed = {
        "omegaflow_runtime": PurePosixPath("/omegaflow-runtime"),
        "omegaflow_run": PurePosixPath("/run/omegaflow"),
    }
    for name, mount in mounts.items():
        if not isinstance(mount, dict) or not isinstance(mount.get("target"), str):
            continue
        target = PurePosixPath(mount["target"])
        for reserved in RESERVED_WORKLOAD_PATHS:
            overlaps = (
                target == reserved
                or target in reserved.parents
                or reserved in target.parents
            )
            if not overlaps:
                continue
            if allowed.get(name) == target:
                break
            raise ReployBlueprintError(
                f"workload mount {name!r} at {target} overlaps reserved path {reserved}"
            )


def write_reploy_blueprints(config: DictConfig, run_dir: Path) -> tuple[Path, Path]:
    """Retain both resolved blueprints beside fresh deployment locations."""

    root = run_dir / "reploy"
    blueprints = root / "blueprints"
    deployments = root / "deployments"
    expected_runtime_source = Path(str(config.studio.run_dir)) / "reploy/input/runtime"
    validate_workload_reserved_paths(
        config.reploy.workload,
        expected_runtime_source=expected_runtime_source,
    )
    if blueprints.exists() or deployments.exists():
        raise ReployBlueprintError("Reploy run materialization must start fresh")
    blueprints.mkdir(mode=0o700, parents=True)
    deployments.mkdir(mode=0o700)
    controller = blueprints / "controller.blueprint.yaml"
    workload = blueprints / "workload.blueprint.yaml"
    controller.write_text(blueprint_yaml(config.reploy.controller), encoding="utf-8")
    workload.write_text(blueprint_yaml(config.reploy.workload), encoding="utf-8")
    return controller, workload
