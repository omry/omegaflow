from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from tools.release import (
    ReleaseMetadata,
    ReleaseValidationError,
    changelog_entry,
    pypi_version_exists,
    require_unpublished_version,
    resolve_release,
    write_github_output,
)


class Response:
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def release_files(tmp_path: Path, version: str = "0.9.0") -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nname = "omegaflow"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        f"# Changelog\n\n## {version} (2026-07-15)\n",
        encoding="utf-8",
    )
    return pyproject, changelog


def test_tag_release_matches_package_version_and_changelog(tmp_path: Path) -> None:
    pyproject, changelog = release_files(tmp_path)

    metadata = resolve_release(
        event_name="push",
        ref_name="v0.9.0",
        input_version="",
        pyproject_path=pyproject,
        changelog_path=changelog,
    )

    assert metadata == ReleaseMetadata(version="0.9.0", tag="v0.9.0")


def test_tag_release_rejects_version_mismatch(tmp_path: Path) -> None:
    pyproject, changelog = release_files(tmp_path)

    with pytest.raises(ReleaseValidationError, match="expected 'v0.9.0'"):
        resolve_release(
            event_name="push",
            ref_name="v0.8.0",
            input_version="",
            pyproject_path=pyproject,
            changelog_path=changelog,
        )


def test_manual_release_requires_matching_version(tmp_path: Path) -> None:
    pyproject, changelog = release_files(tmp_path)

    with pytest.raises(ReleaseValidationError, match="requested version '0.8.0'"):
        resolve_release(
            event_name="workflow_dispatch",
            ref_name="main",
            input_version="0.8.0",
            pyproject_path=pyproject,
            changelog_path=changelog,
        )


def test_manual_release_requires_matching_existing_tag(tmp_path: Path) -> None:
    pyproject, changelog = release_files(tmp_path)

    with pytest.raises(ReleaseValidationError, match="select the existing 'v0.9.0'"):
        resolve_release(
            event_name="workflow_dispatch",
            ref_name="main",
            input_version="0.9.0",
            pyproject_path=pyproject,
            changelog_path=changelog,
        )


def test_release_requires_generated_changelog_entry(tmp_path: Path) -> None:
    pyproject, changelog = release_files(tmp_path)
    changelog.write_text("# Changelog\n", encoding="utf-8")

    with pytest.raises(ReleaseValidationError, match="no release heading"):
        resolve_release(
            event_name="workflow_dispatch",
            ref_name="v0.9.0",
            input_version="0.9.0",
            pyproject_path=pyproject,
            changelog_path=changelog,
        )


def test_release_rejects_unsupported_event(tmp_path: Path) -> None:
    pyproject, changelog = release_files(tmp_path)

    with pytest.raises(ReleaseValidationError, match="unsupported release event"):
        resolve_release(
            event_name="schedule",
            ref_name="main",
            input_version="0.9.0",
            pyproject_path=pyproject,
            changelog_path=changelog,
        )


def test_github_output_contains_version_and_tag(tmp_path: Path) -> None:
    output = tmp_path / "github-output"

    write_github_output(
        output,
        ReleaseMetadata(version="0.9.0", tag="v0.9.0"),
    )

    assert output.read_text(encoding="utf-8") == "version=0.9.0\ntag=v0.9.0\n"


def test_changelog_entry_extracts_only_requested_release(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## 0.9.0 (2026-07-15)\n\n"
        "### Features\n\n- Current.\n\n"
        "## 0.4.0 (2026-06-01)\n\n- Previous.\n",
        encoding="utf-8",
    )

    assert changelog_entry(changelog, "0.9.0") == (
        "## 0.9.0 (2026-07-15)\n\n### Features\n\n- Current.\n"
    )


def test_pypi_guard_rejects_existing_version(tmp_path: Path) -> None:
    pyproject, _ = release_files(tmp_path)

    with pytest.raises(ReleaseValidationError, match="already exists on PyPI"):
        require_unpublished_version(
            pyproject,
            "0.9.0",
            opener=lambda url, timeout: Response(),
        )


def test_pypi_guard_accepts_missing_version(tmp_path: Path) -> None:
    pyproject, _ = release_files(tmp_path)

    def missing(url: str, timeout: int) -> Response:
        raise HTTPError(url, 404, "Not Found", {}, None)

    require_unpublished_version(pyproject, "0.9.0", opener=missing)


def test_pypi_guard_rejects_inconclusive_check() -> None:
    def unavailable(url: str, timeout: int) -> Response:
        raise URLError("offline")

    with pytest.raises(ReleaseValidationError, match="could not check"):
        pypi_version_exists("omegaflow", "0.9.0", opener=unavailable)
