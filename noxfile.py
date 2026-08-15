from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import tomllib
import zipfile

import nox


nox.options.sessions = ["ci"]

RELEASE_PLATFORMS = (
    "linux-x86_64",
    "linux-aarch64",
    "macos-x86_64",
    "macos-aarch64",
)
VENDORED_RECORDER_PATHS = (
    Path("src/omegaflow/bin/asciinema"),
    Path("src/omegaflow/bin/asciinema.platform"),
)
GENERATED_RUNTIME_PATHS = (
    Path("src/omegaflow/_runtime"),
)
SOURCE_REVISION_PATH = Path("src/omegaflow/_source_revision")


@nox.session(venv_backend="none")
def tests(session: nox.Session) -> None:
    session.run(sys.executable, "-m", "pytest", *session.posargs)


@nox.session(venv_backend="none")
def ci(session: nox.Session) -> None:
    """Run the repository checks required by the Python CI job."""
    session.run(sys.executable, "-m", "pytest", *session.posargs)
    session.run(
        sys.executable,
        "website/scripts/update_recording_schema_docs.py",
        "--check",
    )
    validate_release_notes(session)


@nox.session(venv_backend="none")
def schema_docs(session: nox.Session) -> None:
    session.run(
        sys.executable,
        "website/scripts/update_recording_schema_docs.py",
        "--check",
    )


@nox.session(venv_backend="none")
def release_notes(session: nox.Session) -> None:
    version = session.posargs[0] if session.posargs else None
    validate_release_notes(session, version=version)


@nox.session(venv_backend="none")
def package(session: nox.Session) -> None:
    dist_dir = Path("dist")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    preserve_source_revision = SOURCE_REVISION_PATH.is_file()
    clean_vendored_recorder()
    clean_generated_runtime(preserve_source_revision=preserve_source_revision)
    session.run(sys.executable, "-m", "build", "--sdist", "--no-isolation")
    try:
        for platform in RELEASE_PLATFORMS:
            session.run(sys.executable, "tools/vendor_asciinema.py", platform)
            session.run(sys.executable, "-m", "build", "--wheel", "--no-isolation")
        verify_release_artifacts()
    finally:
        clean_vendored_recorder()
        clean_generated_runtime(preserve_source_revision=preserve_source_revision)


@nox.session(venv_backend="none")
def website(session: nox.Session) -> None:
    session.run(
        sys.executable,
        "website/scripts/update_recording_schema_docs.py",
        "--check",
    )
    session.run(
        "pnpm",
        "--dir",
        "website",
        "install",
        "--frozen-lockfile",
        external=True,
    )
    session.run("pnpm", "--dir", "website", "build", external=True)


def clean_vendored_recorder() -> None:
    for path in VENDORED_RECORDER_PATHS:
        path.unlink(missing_ok=True)


def clean_generated_runtime(*, preserve_source_revision: bool = False) -> None:
    for path in GENERATED_RUNTIME_PATHS:
        if path.is_dir():
            for item in sorted(
                path.rglob("*"),
                key=lambda value: len(value.parts),
                reverse=True,
            ):
                if item.is_symlink():
                    continue
                item.chmod(0o700 if item.is_dir() else 0o600)
            path.chmod(0o700)
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    if not preserve_source_revision:
        SOURCE_REVISION_PATH.unlink(missing_ok=True)


def validate_release_notes(
    session: nox.Session,
    *,
    version: str | None = None,
) -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    session.run(
        sys.executable,
        "-m",
        "towncrier",
        "build",
        "--draft",
        "--version",
        version or pyproject["project"]["version"],
    )


def verify_release_artifacts() -> None:
    artifacts = {path.name for path in Path("dist").iterdir() if path.is_file()}
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package = pyproject["project"]["name"].replace("-", "_")
    version = pyproject["project"]["version"]
    expected = {
        f"{package}-{version}.tar.gz",
        f"{package}-{version}-py3-none-manylinux_2_35_x86_64.whl",
        f"{package}-{version}-py3-none-manylinux_2_35_aarch64.whl",
        f"{package}-{version}-py3-none-macosx_10_12_x86_64.whl",
        f"{package}-{version}-py3-none-macosx_11_0_arm64.whl",
    }
    missing = expected - artifacts
    unexpected_pure_wheels = {
        artifact for artifact in artifacts if artifact.endswith("-py3-none-any.whl")
    }
    if missing or unexpected_pure_wheels:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if unexpected_pure_wheels:
            details.append(
                "unexpected pure wheels: "
                + ", ".join(sorted(unexpected_pure_wheels))
            )
        raise RuntimeError("invalid release artifacts; " + "; ".join(details))
    for path in Path("dist").glob("*.whl"):
        verify_workload_runtime_artifact(path)


def verify_workload_runtime_artifact(path: Path) -> None:
    architecture = "arm64" if "aarch64" in path.name or "arm64" in path.name else "amd64"
    root = f"omegaflow/_runtime/linux-{architecture}"
    expected = {
        f"{root}/manifest.json",
        f"{root}/bin/envoy",
        f"{root}/bin/awsh",
        f"{root}/libexec/awsh-driver.bash",
    }
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        missing = expected - names
        other_runtimes = {
            name
            for name in names
            if name.startswith("omegaflow/_runtime/") and not name.startswith(root + "/")
        }
        if missing or other_runtimes:
            raise RuntimeError(
                f"invalid workload runtime in {path.name}: "
                f"missing={sorted(missing)}, unexpected={sorted(other_runtimes)}"
            )
        manifest = json.loads(wheel.read(f"{root}/manifest.json"))
        if manifest.get("os") != "linux" or manifest.get("architecture") != architecture:
            raise RuntimeError(f"invalid workload runtime platform in {path.name}")
