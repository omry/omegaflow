"""Validate and stage the installed OmegaFlow workload runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform as platform_module
import shutil
import stat
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any


MANIFEST_SCHEMA = "omegaflow-runtime-manifest-v1"
TELEMETRY_SCHEMA = "omegaflow-envoy-telemetry-v1"
AWSH_SCHEMA = "awsh-v1"
MAX_MANIFEST_BYTES = 1024 * 1024
ALLOWED_SOURCE_MODES = {False: {0o444, 0o644}, True: {0o555, 0o755}}


class WorkloadRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeFile:
    path: str
    size: int
    executable: bool
    sha256: str


@dataclass(frozen=True)
class RuntimeManifest:
    schema: str
    omegaflow_version: str
    source_revision: str
    telemetry_schema: str
    awsh_schema: str
    os: str
    architecture: str
    go_version: str
    files: tuple[RuntimeFile, ...]

    @property
    def platform(self) -> str:
        return f"{self.os}-{self.architecture}"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkloadRuntimeError(f"runtime manifest has duplicate field {key!r}")
        result[key] = value
    return result


def _exact_fields(value: dict[str, Any], expected: set[str], *, field: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise WorkloadRuntimeError(f"invalid {field}: {'; '.join(details)}")


def _required_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkloadRuntimeError(f"{field} must be a non-empty string")
    return value


def _runtime_path(value: Any) -> str:
    text = _required_string(value, field="files[].path")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or path.as_posix() != text
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] not in {"bin", "libexec"}
    ):
        raise WorkloadRuntimeError(f"invalid runtime payload path: {text!r}")
    return text


def decode_manifest(data: bytes) -> RuntimeManifest:
    if len(data) > MAX_MANIFEST_BYTES:
        raise WorkloadRuntimeError("runtime manifest exceeds 1 MiB")
    try:
        value = json.loads(data, object_pairs_hook=_strict_object)
    except WorkloadRuntimeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkloadRuntimeError(f"invalid runtime manifest JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkloadRuntimeError("runtime manifest must be a JSON object")
    expected = {
        "schema",
        "omegaflow_version",
        "source_revision",
        "telemetry_schema",
        "awsh_schema",
        "os",
        "architecture",
        "go_version",
        "files",
    }
    _exact_fields(value, expected, field="runtime manifest")
    if value["schema"] != MANIFEST_SCHEMA:
        raise WorkloadRuntimeError(f"unsupported runtime manifest schema: {value['schema']!r}")
    if value["telemetry_schema"] != TELEMETRY_SCHEMA:
        raise WorkloadRuntimeError("runtime telemetry schema does not match OmegaFlow")
    if value["awsh_schema"] != AWSH_SCHEMA:
        raise WorkloadRuntimeError("runtime awsh schema does not match OmegaFlow")
    if value["os"] != "linux" or value["architecture"] not in {"amd64", "arm64"}:
        raise WorkloadRuntimeError("runtime manifest platform is unsupported")
    raw_files = value["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise WorkloadRuntimeError("runtime manifest files must be a non-empty list")
    files: list[RuntimeFile] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_files):
        if not isinstance(raw, dict):
            raise WorkloadRuntimeError(f"files[{index}] must be an object")
        _exact_fields(
            raw,
            {"path", "size", "executable", "sha256"},
            field=f"files[{index}]",
        )
        path = _runtime_path(raw["path"])
        if path in seen:
            raise WorkloadRuntimeError(f"duplicate runtime payload path: {path}")
        seen.add(path)
        size = raw["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise WorkloadRuntimeError(f"files[{index}].size must be a non-negative integer")
        executable = raw["executable"]
        if not isinstance(executable, bool):
            raise WorkloadRuntimeError(f"files[{index}].executable must be a boolean")
        digest = raw["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise WorkloadRuntimeError(f"files[{index}].sha256 must be lowercase SHA-256")
        files.append(
            RuntimeFile(
                path=path,
                size=size,
                executable=executable,
                sha256=digest,
            )
        )
    if tuple(item.path for item in files) != tuple(sorted(item.path for item in files)):
        raise WorkloadRuntimeError("runtime manifest files must be sorted by path")
    source_revision = _required_string(value["source_revision"], field="source_revision")
    if len(source_revision) != 40 or any(
        character not in "0123456789abcdef" for character in source_revision
    ):
        raise WorkloadRuntimeError("source_revision must be a lowercase 40-character revision")
    return RuntimeManifest(
        schema=value["schema"],
        omegaflow_version=_required_string(value["omegaflow_version"], field="omegaflow_version"),
        source_revision=source_revision,
        telemetry_schema=value["telemetry_schema"],
        awsh_schema=value["awsh_schema"],
        os=value["os"],
        architecture=value["architecture"],
        go_version=_required_string(value["go_version"], field="go_version"),
        files=tuple(files),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WorkloadRuntimeError(f"could not open regular runtime file {path}") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise WorkloadRuntimeError(f"runtime file is no longer regular: {path}")
    return descriptor, info


def _read_regular_bytes(path: Path, *, limit: int) -> bytes:
    descriptor, info = _open_regular(path)
    if info.st_size > limit:
        os.close(descriptor)
        raise WorkloadRuntimeError(f"runtime file exceeds its size limit: {path}")
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(limit + 1)


def _copy_verified_file(source: Path, target: Path, item: RuntimeFile) -> None:
    descriptor, info = _open_regular(source)
    mode = stat.S_IMODE(info.st_mode)
    if mode not in ALLOWED_SOURCE_MODES[item.executable] or info.st_size != item.size:
        os.close(descriptor)
        raise WorkloadRuntimeError(f"runtime payload changed while staging: {item.path}")
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as input_handle, target.open("xb") as output_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                digest.update(chunk)
                output_handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != item.sha256:
        target.unlink(missing_ok=True)
        raise WorkloadRuntimeError(f"runtime digest changed while staging: {item.path}")


def _remove_staged_tree(path: Path) -> None:
    if not path.exists():
        return
    for item in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if item.is_symlink():
            continue
        item.chmod(0o700 if item.is_dir() else 0o600)
    path.chmod(0o700)
    shutil.rmtree(path)


def validate_runtime_source(
    source: Path,
    *,
    expected_platform: str | None = None,
) -> RuntimeManifest:
    source = source.absolute()
    manifest_path = source / "manifest.json"
    try:
        manifest_stat = manifest_path.lstat()
    except FileNotFoundError as exc:
        raise WorkloadRuntimeError(f"runtime manifest is missing: {manifest_path}") from exc
    if not stat.S_ISREG(manifest_stat.st_mode) or manifest_path.is_symlink():
        raise WorkloadRuntimeError("runtime manifest must be a regular file")
    manifest = decode_manifest(
        _read_regular_bytes(manifest_path, limit=MAX_MANIFEST_BYTES)
    )
    if expected_platform is not None and manifest.platform != expected_platform:
        raise WorkloadRuntimeError(
            f"runtime platform {manifest.platform} does not match {expected_platform}"
        )

    actual: set[str] = set()
    for path in source.rglob("*"):
        relative = path.relative_to(source).as_posix()
        info = path.lstat()
        if path.is_symlink():
            raise WorkloadRuntimeError(f"runtime contains symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            if relative not in {"bin", "libexec"}:
                raise WorkloadRuntimeError(f"runtime contains unexpected directory: {relative}")
            continue
        if not stat.S_ISREG(info.st_mode):
            raise WorkloadRuntimeError(f"runtime contains special file: {relative}")
        if relative != "manifest.json":
            actual.add(relative)
    expected = {item.path for item in manifest.files}
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("additional " + ", ".join(sorted(extra)))
        raise WorkloadRuntimeError("runtime payload does not match manifest: " + "; ".join(details))

    for item in manifest.files:
        path = source / item.path
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if mode not in ALLOWED_SOURCE_MODES[item.executable]:
            raise WorkloadRuntimeError(
                f"runtime executable mode mismatch for {item.path}: got {mode:04o}"
            )
        if info.st_size != item.size:
            raise WorkloadRuntimeError(f"runtime size mismatch for {item.path}")
        if _sha256(path) != item.sha256:
            raise WorkloadRuntimeError(f"runtime digest mismatch for {item.path}")
    return manifest


def host_workload_platform() -> str:
    machine = platform_module.machine().lower()
    if machine in {"x86_64", "amd64"}:
        architecture = "amd64"
    elif machine in {"aarch64", "arm64"}:
        architecture = "arm64"
    else:
        raise WorkloadRuntimeError(f"unsupported workload architecture: {machine}")
    return f"linux-{architecture}"


def installed_runtime_source(platform: str | None = None) -> Path:
    selected = platform or host_workload_platform()
    root = resources.files("omegaflow").joinpath("_runtime", selected)
    try:
        path = Path(root)
    except TypeError as exc:
        raise WorkloadRuntimeError("installed runtime is not filesystem-backed") from exc
    if not path.is_dir():
        raise WorkloadRuntimeError(
            f"installed OmegaFlow distribution has no workload runtime for {selected}"
        )
    return path


def _stage_runtime(source: Path, destination: Path, *, platform: str) -> RuntimeManifest:
    manifest = validate_runtime_source(source, expected_platform=platform)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise WorkloadRuntimeError(f"runtime destination must not exist: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        for directory in (temporary / "bin", temporary / "libexec"):
            directory.mkdir(mode=0o700)
        for item in manifest.files:
            target = temporary / item.path
            _copy_verified_file(source / item.path, target, item)
            target.chmod(0o555 if item.executable else 0o444)
        manifest_target = temporary / "manifest.json"
        manifest_bytes = _read_regular_bytes(
            source / "manifest.json",
            limit=MAX_MANIFEST_BYTES,
        )
        if decode_manifest(manifest_bytes) != manifest:
            raise WorkloadRuntimeError("runtime manifest changed while staging")
        manifest_target.write_bytes(manifest_bytes)
        manifest_target.chmod(0o444)
        for directory in (temporary / "bin", temporary / "libexec", temporary):
            directory.chmod(0o555)
        temporary.rename(destination)
    except Exception:
        _remove_staged_tree(temporary)
        raise
    return validate_runtime_source(destination, expected_platform=platform)


def stage_workload_runtime(destination: Path, *, platform: str | None = None) -> RuntimeManifest:
    """Stage the exact installed runtime into one fresh non-writable directory."""

    selected = platform or host_workload_platform()
    return _stage_runtime(installed_runtime_source(selected), destination, platform=selected)
