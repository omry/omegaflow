from __future__ import annotations

import importlib.util
import os
import platform as platform_module
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


WHEEL_TAGS = {
    "linux-x86_64": "py3-none-manylinux_2_35_x86_64",
    "linux-aarch64": "py3-none-manylinux_2_35_aarch64",
    "macos-x86_64": "py3-none-macosx_10_12_x86_64",
    "macos-aarch64": "py3-none-macosx_11_0_arm64",
}


def current_build_platform() -> str | None:
    system = platform_module.system().lower()
    machine = platform_module.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        arch = "aarch64"
    else:
        return None

    if system == "linux":
        return f"linux-{arch}"
    if system == "darwin":
        return f"macos-{arch}"
    return None


def vendor_asciinema(root: Path, platform: str, *, output: Path) -> None:
    vendor_script = root / "tools" / "vendor_asciinema.py"
    spec = importlib.util.spec_from_file_location(
        "omegaflow_vendor_asciinema", vendor_script
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {vendor_script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.vendor(platform, output=output)


def build_workload_runtime(
    root: Path,
    architecture: str,
    *,
    output: Path,
    version: str,
    source_revision: str,
) -> None:
    build_script = root / "tools" / "build_workload_runtime.py"
    spec = importlib.util.spec_from_file_location(
        "omegaflow_build_workload_runtime", build_script
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {build_script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.build_runtime(
        root,
        architecture,
        output=output,
        version=version,
        source_revision=source_revision,
    )


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def resolve_source_revision(root: Path) -> str:
    def validate(value: str) -> str:
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise RuntimeError(
                "OmegaFlow source revision must be 40 lowercase hexadecimal characters"
            )
        return value

    for variable in ("OMEGAFLOW_SOURCE_REVISION", "GITHUB_SHA"):
        value = os.environ.get(variable, "").strip()
        if value:
            return validate(value)
    recorded = root / "src" / "omegaflow" / "_source_revision"
    if recorded.is_file():
        value = recorded.read_text(encoding="utf-8").strip()
        if value:
            return validate(value)
    for command in (
        ["git", "rev-parse", "HEAD"],
        ["sl", "log", "-r", ".", "-T", "{node}"],
    ):
        try:
            result = subprocess.run(
                command,
                cwd=root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        value = result.stdout.strip()
        if value:
            return validate(value)
    raise RuntimeError(
        "could not determine OmegaFlow source revision; set "
        "OMEGAFLOW_SOURCE_REVISION"
    )


def workload_architecture(platform: str) -> str:
    if platform.endswith("-x86_64"):
        return "amd64"
    if platform.endswith("-aarch64"):
        return "arm64"
    raise RuntimeError(f"unsupported workload runtime platform: {platform}")


def remove_generated_runtime(path: Path) -> None:
    if not path.exists():
        return
    for item in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if item.is_symlink():
            continue
        item.chmod(0o700 if item.is_dir() else 0o600)
    path.chmod(0o700)
    shutil.rmtree(path)


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version != "standard":
            return

        root = Path(self.root)
        if self.target_name == "sdist":
            revision_path = root / "src" / "omegaflow" / "_source_revision"
            if not revision_path.exists():
                revision_path.write_text(resolve_source_revision(root) + "\n", encoding="utf-8")
                self._generated_revision = revision_path
            return
        if self.target_name != "wheel":
            return

        bin_dir = root / "src" / "omegaflow" / "bin"
        bundled_recorder = bin_dir / "asciinema"
        if not bundled_recorder.is_file():
            platform = current_build_platform()
            if platform is None:
                return
            vendor_asciinema(root, platform, output=bundled_recorder)

        platform_file = bin_dir / "asciinema.platform"
        if not platform_file.is_file():
            raise RuntimeError(
                "bundled asciinema is missing src/omegaflow/bin/"
                "asciinema.platform; run tools/vendor_asciinema.py before "
                "building a platform wheel"
            )
        platform = platform_file.read_text(encoding="utf-8").strip()
        try:
            tag = WHEEL_TAGS[platform]
        except KeyError as exc:
            raise RuntimeError(
                f"unsupported bundled asciinema platform: {platform}"
            ) from exc

        if (root / "runtime" / "envoy" / "go.mod").is_file():
            architecture = workload_architecture(platform)
            runtime_root = root / "src" / "omegaflow" / "_runtime"
            output = runtime_root / f"linux-{architecture}"
            build_workload_runtime(
                root,
                architecture,
                output=output,
                version=project_version(root),
                source_revision=resolve_source_revision(root),
            )
            self._generated_runtime = runtime_root

        build_data["tag"] = tag
        build_data["pure_python"] = False

    def finalize(
        self,
        version: str,
        build_data: dict[str, Any],
        artifact_path: str,
    ) -> None:
        generated_runtime = getattr(self, "_generated_runtime", None)
        if generated_runtime is not None:
            remove_generated_runtime(generated_runtime)
        generated_revision = getattr(self, "_generated_revision", None)
        if generated_revision is not None:
            generated_revision.unlink(missing_ok=True)
