from __future__ import annotations

import importlib.util
import platform as platform_module
import sys
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


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version != "standard":
            return

        root = Path(self.root)
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

        build_data["tag"] = tag
        build_data["pure_python"] = False
