from __future__ import annotations

import importlib.metadata
import shutil
import site
import sys
from pathlib import Path


def copy_distribution(name: str, destination_root: Path) -> None:
    distribution = importlib.metadata.distribution(name)
    for installed_path in distribution.files or ():
        if ".." in installed_path.parts or installed_path.is_absolute():
            continue
        source = Path(distribution.locate_file(installed_path))
        destination = destination_root / installed_path
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: seed-tutorial-venv.py VENV")
    venv = Path(sys.argv[1]).resolve()
    site_packages = Path(
        next(
            path
            for path in site.getsitepackages([str(venv)])
            if path.startswith(str(venv))
        )
    )
    for distribution in (
        "hatchling",
        "hydra-core",
        "omegaconf",
        "packaging",
        "pathspec",
        "playwright",
        "pluggy",
        "pyee",
        "Pygments",
        "PyYAML",
        "greenlet",
        "trove-classifiers",
    ):
        copy_distribution(distribution, site_packages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
