"""Private, explicitly scoped environment values used by OmegaFlow itself."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path

from .studio_config import StudioConfigError, dotenv_entry


SERVICE_ENVIRONMENT_PATH = Path(".omegaflow/omegaflow-secret.env")
ALLOWED_SERVICE_ENVIRONMENT_NAMES = frozenset({"OPENAI_OMEGAFLOW_API_KEY"})


class ServiceEnvironmentError(RuntimeError):
    """A private OmegaFlow service environment could not be resolved safely."""


def read_environment_file(path: Path) -> dict[str, str]:
    """Parse an env file without changing the process environment."""

    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ServiceEnvironmentError(
            f"could not read private environment file: {path}"
        ) from exc
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        try:
            entry = dotenv_entry(line, path=path, line_number=line_number)
        except StudioConfigError as exc:
            raise ServiceEnvironmentError(str(exc)) from exc
        if entry is not None:
            name, value = entry
            values[name] = value
    return values


def _private_service_file(root: Path) -> tuple[Path, dict[str, str]]:
    path = root / SERVICE_ENVIRONMENT_PATH
    if path.is_symlink():
        raise ServiceEnvironmentError(
            f"private OmegaFlow environment must not be a symbolic link: {path}"
        )
    if not path.exists():
        return path, {}
    if not path.is_file():
        raise ServiceEnvironmentError(
            f"private OmegaFlow environment is not a file: {path}"
        )
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ServiceEnvironmentError(
            f"could not inspect private OmegaFlow environment: {path}"
        ) from exc
    if mode != 0o600:
        raise ServiceEnvironmentError(
            f"private OmegaFlow environment must have mode 0600: {path}"
        )
    return path, read_environment_file(path)


def validate_service_environment_names(names: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name:
            raise ServiceEnvironmentError(
                "scoped environment names must be non-empty strings"
            )
        if name not in ALLOWED_SERVICE_ENVIRONMENT_NAMES:
            raise ServiceEnvironmentError(
                f"{name!r} is not an allowlisted OmegaFlow service environment name"
            )
        if name not in normalized:
            normalized.append(name)
    return tuple(normalized)


def resolve_service_environment(
    names: Iterable[str],
    *,
    root: Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve allowlisted values from CI/parent env, then the private project file."""

    requested = validate_service_environment_names(names)
    if not requested:
        return {}
    parent = os.environ if environ is None else environ
    resolved = {
        name: parent[name]
        for name in requested
        if isinstance(parent.get(name), str) and parent[name]
    }
    missing = tuple(name for name in requested if name not in resolved)
    path: Path | None = None
    if missing:
        path, file_values = _private_service_file(root.expanduser().resolve())
        for name in missing:
            value = file_values.get(name)
            if value:
                resolved[name] = value
    still_missing = [name for name in requested if name not in resolved]
    if still_missing:
        source = path or root.expanduser().resolve() / SERVICE_ENVIRONMENT_PATH
        raise ServiceEnvironmentError(
            "missing scoped OmegaFlow service environment value "
            + ", ".join(repr(name) for name in still_missing)
            + f"; set it in the parent environment or {source}"
        )
    return {name: resolved[name] for name in requested}
