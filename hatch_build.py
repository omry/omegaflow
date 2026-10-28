from __future__ import annotations

import importlib.util
from contextlib import ExitStack
import hashlib
import os
import platform as platform_module
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


def runtime_artifact(root: Path, stack: ExitStack) -> Path | None:
    """Build-only bridge; installed runtime selection never reads this option."""
    value = os.environ.get("OMEGAFLOW_RUNTIME_ARTIFACT")
    if value is None:
        return None
    if not value or not Path(value).is_absolute():
        raise ValueError("OMEGAFLOW_RUNTIME_ARTIFACT must name an absolute existing artifact")
    # This hook runs in the source build environment, before the package exists.
    sys.path.insert(0, str(root / "src"))
    try:
        from omegaflow.runtime_artifact import capture_artifact
    finally:
        sys.path.pop(0)
    if (root / ".sl").is_dir():
        command = ["sl", "log", "-r", ".", "-T", "{node}"]
    elif (root / ".git").exists():
        command = ["git", "rev-parse", "HEAD"]
    else:
        raise ValueError("artifact wheel requires its exact source checkout revision")
    revision = subprocess.check_output(command, cwd=root, text=True,
                                       env=os.environ | {"CHGDISABLE": "1"}).strip()
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    artifact = Path(value)
    captured = stack.enter_context(capture_artifact(
        artifact, version=version, source_revision=revision, readonly=True))
    print("OmegaFlow runtime manifest sha256=" + hashlib.sha256(captured.manifest_bytes).hexdigest())
    return artifact


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version != "standard":
            return

        root = Path(self.root)
        self._runtime_stack = ExitStack()
        try:
            CustomBuildHook._initialize_wheel(self, root, build_data)
        except BaseException:
            self._runtime_stack.close()
            raise

    def _initialize_wheel(self, root: Path, build_data: dict[str, Any]) -> None:
        runtime = runtime_artifact(root, self._runtime_stack)
        if runtime is not None and current_build_platform() != "linux-x86_64":
            raise ValueError("Linux/amd64 runtime requires a Linux/amd64 wheel build")
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
        if runtime is not None:
            if platform != "linux-x86_64":
                raise ValueError("runtime and bundled recorder architectures differ")
            build_data.setdefault("force_include", {})[str(runtime)] = "omegaflow/runtime/linux-amd64"
        try:
            tag = WHEEL_TAGS[platform]
        except KeyError as exc:
            raise RuntimeError(
                f"unsupported bundled asciinema platform: {platform}"
            ) from exc

        build_data["tag"] = tag
        build_data["pure_python"] = False

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        stack = getattr(self, "_runtime_stack", None)
        if stack is not None:
            stack.close()  # Recheck the original bound artifact after Hatch copied it.
