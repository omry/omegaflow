#!/usr/bin/env python3
"""Validate and expose release metadata for publication workflows."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Callable, ContextManager
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


class ReleaseValidationError(ValueError):
    """Raised when release inputs do not describe the checked-out project."""


@dataclass(frozen=True)
class ReleaseMetadata:
    version: str
    tag: str


def project_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ReleaseValidationError("pyproject.toml has no project version")
    return version


def project_name(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    name = data.get("project", {}).get("name")
    if not isinstance(name, str) or not name.strip():
        raise ReleaseValidationError("pyproject.toml has no project name")
    return name


def pypi_version_exists(
    project: str,
    version: str,
    *,
    opener: Callable[..., ContextManager[object]] = urlopen,
) -> bool:
    url = (
        "https://pypi.org/pypi/"
        f"{quote(project, safe='')}/{quote(version, safe='')}/json"
    )
    try:
        with opener(url, timeout=15):
            return True
    except HTTPError as error:
        if error.code == 404:
            return False
        raise ReleaseValidationError(
            f"PyPI returned HTTP {error.code} while checking {project} {version}"
        ) from error
    except URLError as error:
        raise ReleaseValidationError(
            f"could not check whether {project} {version} exists on PyPI: "
            f"{error.reason}"
        ) from error


def require_unpublished_version(
    pyproject_path: Path,
    version: str,
    *,
    opener: Callable[..., ContextManager[object]] = urlopen,
) -> None:
    name = project_name(pyproject_path)
    if pypi_version_exists(name, version, opener=opener):
        raise ReleaseValidationError(
            f"{name} {version} already exists on PyPI; refusing to rebuild it"
        )


def validate_changelog(changelog_path: Path, version: str) -> None:
    changelog_entry(changelog_path, version)


def changelog_entry(changelog_path: Path, version: str) -> str:
    heading = f"## {version} "
    lines = changelog_path.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(heading)),
        None,
    )
    if start is None:
        raise ReleaseValidationError(
            f"CHANGELOG.md has no release heading for {version}"
        )
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start=start + 1)
            if line.startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip() + "\n"


def resolve_release(
    *,
    event_name: str,
    ref_name: str,
    input_version: str,
    pyproject_path: Path,
    changelog_path: Path,
) -> ReleaseMetadata:
    version = project_version(pyproject_path)
    tag = f"v{version}"

    if event_name == "push":
        if ref_name != tag:
            raise ReleaseValidationError(
                f"release tag {ref_name!r} does not match package version {version!r}; "
                f"expected {tag!r}"
            )
    elif event_name == "workflow_dispatch":
        if input_version != version:
            raise ReleaseValidationError(
                f"requested version {input_version!r} does not match package version "
                f"{version!r}"
            )
        if ref_name != tag:
            raise ReleaseValidationError(
                f"manual release ref {ref_name!r} does not match package version "
                f"{version!r}; select the existing {tag!r} tag"
            )
    else:
        raise ReleaseValidationError(f"unsupported release event {event_name!r}")

    validate_changelog(changelog_path, version)
    return ReleaseMetadata(version=version, tag=tag)


def write_github_output(path: Path, metadata: ReleaseMetadata) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"version={metadata.version}\n")
        output.write(f"tag={metadata.tag}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref-name", default="")
    parser.add_argument("--input-version", default="")
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--release-notes-output", type=Path)
    parser.add_argument(
        "--check-pypi",
        action="store_true",
        help="fail if the resolved version already exists on PyPI",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = resolve_release(
            event_name=args.event_name,
            ref_name=args.ref_name,
            input_version=args.input_version,
            pyproject_path=args.pyproject,
            changelog_path=args.changelog,
        )
        if args.check_pypi:
            require_unpublished_version(args.pyproject, metadata.version)
    except (OSError, ReleaseValidationError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(f"release validation failed: {error}") from error

    if args.github_output is not None:
        write_github_output(args.github_output, metadata)
    if args.release_notes_output is not None:
        args.release_notes_output.write_text(
            changelog_entry(args.changelog, metadata.version),
            encoding="utf-8",
        )
    print(f"release {metadata.tag} matches package metadata and changelog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
