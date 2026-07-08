from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


WHEEL_TAGS = {
    "linux-x86_64": "py3-none-manylinux_2_35_x86_64",
    "linux-aarch64": "py3-none-manylinux_2_35_aarch64",
    "macos-x86_64": "py3-none-macosx_10_12_x86_64",
    "macos-aarch64": "py3-none-macosx_11_0_arm64",
}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version != "standard":
            return

        bin_dir = Path(self.root) / "src" / "omegaflow_studio" / "bin"
        bundled_recorder = bin_dir / "asciinema"
        if not bundled_recorder.is_file():
            return

        platform_file = bin_dir / "asciinema.platform"
        if not platform_file.is_file():
            raise RuntimeError(
                "bundled asciinema is missing src/omegaflow_studio/bin/"
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
