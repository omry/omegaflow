"""Recording-local application secret resolution."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from .service_environment import (
    ALLOWED_SERVICE_ENVIRONMENT_NAMES,
    ServiceEnvironmentError,
    read_environment_file,
)


APPLICATION_SECRET_FILE = "app.secret.env"
ENVIRONMENT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ApplicationEnvironmentError(RuntimeError):
    """A recording application secret could not be resolved safely."""


RepositoryCheck = Callable[[Path, Path], None]


def application_secret_names(spec: Mapping[str, object]) -> tuple[str, ...]:
    """Return validated application-secret names from a recording spec."""

    environment = spec.get("environment", {})
    if not isinstance(environment, Mapping):
        raise ApplicationEnvironmentError("environment must be a mapping")
    configured = environment.get("secrets", [])
    if not isinstance(configured, list):
        raise ApplicationEnvironmentError(
            "environment.secrets must be a list of environment variable names"
        )
    names: list[str] = []
    for value in configured:
        if not isinstance(value, str) or ENVIRONMENT_NAME_RE.fullmatch(value) is None:
            raise ApplicationEnvironmentError(
                "environment.secrets must contain valid environment variable names"
            )
        if value in names:
            raise ApplicationEnvironmentError(
                f"environment.secrets contains duplicate name {value!r}"
            )
        if value in ALLOWED_SERVICE_ENVIRONMENT_NAMES:
            raise ApplicationEnvironmentError(
                f"environment.secrets name {value!r} is an OmegaFlow service "
                "secret; use the scoped service environment instead"
            )
        names.append(value)
    return tuple(names)


def application_secret_path(spec: Mapping[str, object]) -> Path:
    """Return the recording-local application-secret path."""

    script_dir = spec.get("_script_dir")
    if not isinstance(script_dir, str) or not script_dir:
        raise ApplicationEnvironmentError(
            "recording application secrets require a recording script directory"
        )
    return Path(script_dir).expanduser().resolve() / APPLICATION_SECRET_FILE


def _find_repository_marker(root: Path, marker_name: str) -> Path | None:
    current = root.resolve()
    for candidate in (current, *current.parents):
        marker = candidate / marker_name
        if marker_name == ".git":
            if marker.is_file() or (marker / "HEAD").exists():
                return marker
        elif marker.exists():
            return marker
    return None


def _run_repository_command(
    command: list[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=None if environment is None else dict(environment),
        )
    except OSError as exc:
        raise ApplicationEnvironmentError(
            f"could not inspect application secret repository state: {exc}"
        ) from exc


def _git_repository_check(root: Path, path: Path) -> bool:
    if _find_repository_marker(root, ".git") is None:
        return False
    git = shutil.which("git")
    if git is None:
        raise ApplicationEnvironmentError(
            "could not inspect application secret repository state: "
            "Git executable is unavailable"
        )
    repository = _run_repository_command(
        [git, "-C", str(root), "rev-parse", "--show-toplevel"]
    )
    if repository.returncode != 0:
        raise ApplicationEnvironmentError(
            repository.stderr.strip() or "Git repository lookup failed"
        )
    repository_root = Path(repository.stdout.strip()).resolve()
    try:
        relative = path.resolve().relative_to(repository_root).as_posix()
    except ValueError:
        return True
    tracked = _run_repository_command(
        [
            git,
            "-C",
            str(repository_root),
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
        ]
    )
    if tracked.returncode == 0:
        raise ApplicationEnvironmentError(
            f"recording application secret {path} is tracked or staged"
        )
    if tracked.returncode != 1:
        raise ApplicationEnvironmentError(
            tracked.stderr.strip() or "Git tracked-file lookup failed"
        )
    ignored = _run_repository_command(
        [
            git,
            "-C",
            str(repository_root),
            "check-ignore",
            "--no-index",
            "-q",
            "--",
            relative,
        ]
    )
    if ignored.returncode == 0:
        return True
    if ignored.returncode == 1:
        raise ApplicationEnvironmentError(
            f"recording application secret {path} is not ignored"
        )
    raise ApplicationEnvironmentError(
        ignored.stderr.strip() or "Git ignore lookup failed"
    )


def _sapling_repository_check(root: Path, path: Path) -> bool:
    if _find_repository_marker(root, ".sl") is None:
        return False
    sapling = shutil.which("sl")
    if sapling is None:
        raise ApplicationEnvironmentError(
            "could not inspect application secret repository state: "
            "Sapling executable is unavailable"
        )
    environment = dict(os.environ)
    environment["CHGDISABLE"] = "1"
    repository = _run_repository_command(
        [sapling, "--cwd", str(root), "root"],
        environment=environment,
    )
    if repository.returncode != 0:
        raise ApplicationEnvironmentError(
            repository.stderr.strip() or "Sapling repository lookup failed"
        )
    repository_root = Path(repository.stdout.strip()).resolve()
    try:
        relative = path.resolve().relative_to(repository_root).as_posix()
    except ValueError:
        return True
    tracked = _run_repository_command(
        [sapling, "--cwd", str(repository_root), "files", relative],
        environment=environment,
    )
    if tracked.returncode == 0 and tracked.stdout.strip():
        raise ApplicationEnvironmentError(
            f"recording application secret {path} is tracked or staged"
        )
    if tracked.returncode not in {0, 1}:
        raise ApplicationEnvironmentError(
            tracked.stderr.strip() or "Sapling tracked-file lookup failed"
        )
    ignored = _run_repository_command(
        [
            sapling,
            "--cwd",
            str(repository_root),
            "status",
            "-i",
            "-n",
            relative,
        ],
        environment=environment,
    )
    if ignored.returncode != 0:
        raise ApplicationEnvironmentError(
            ignored.stderr.strip() or "Sapling ignore lookup failed"
        )
    if relative not in ignored.stdout.splitlines():
        raise ApplicationEnvironmentError(
            f"recording application secret {path} is not ignored"
        )
    return True


def _validate_application_secret_path(path: Path) -> None:
    if path.parent.is_symlink():
        raise ApplicationEnvironmentError(
            f"recording application secret directory {path.parent} is a symbolic link"
        )
    if path.is_symlink():
        raise ApplicationEnvironmentError(
            f"recording application secret {path} must not be a symbolic link"
        )
    if not path.is_file():
        raise ApplicationEnvironmentError(
            f"recording application secret {path} is not a file"
        )


def validate_application_secret_repository(root: Path, path: Path) -> None:
    """Reject a local secret file that is not safely ignored."""

    _git_repository_check(root, path)
    _sapling_repository_check(root, path)


def resolve_application_environment(
    spec: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
    _repository_check: RepositoryCheck = validate_application_secret_repository,
) -> dict[str, str]:
    """Resolve each declared application secret from exactly one source."""

    names = application_secret_names(spec)
    if not names:
        return {}
    parent = os.environ if environ is None else environ
    path = application_secret_path(spec)
    file_values: dict[str, str] = {}
    if path.exists() or path.is_symlink():
        _validate_application_secret_path(path)
        _repository_check(path.parent, path)
        try:
            file_values = read_environment_file(path)
        except ServiceEnvironmentError as exc:
            raise ApplicationEnvironmentError(str(exc)) from exc
    undeclared = sorted(set(file_values) - set(names))
    if undeclared:
        raise ApplicationEnvironmentError(
            f"{path} contains undeclared application secret {undeclared[0]!r}"
        )
    resolved: dict[str, str] = {}
    for name in names:
        host_value = parent.get(name)
        local_value = file_values.get(name)
        has_host = isinstance(host_value, str) and bool(host_value)
        has_local = isinstance(local_value, str) and bool(local_value)
        if has_host and has_local:
            raise ApplicationEnvironmentError(
                f"recording application secret {name!r} is set in both the "
                f"host environment and {APPLICATION_SECRET_FILE}"
            )
        if not has_host and not has_local:
            raise ApplicationEnvironmentError(
                f"missing recording application secret {name!r}; set exactly "
                f"one source in the host environment or {path}"
            )
        resolved[name] = host_value if has_host else local_value  # type: ignore[assignment]
    return resolved
