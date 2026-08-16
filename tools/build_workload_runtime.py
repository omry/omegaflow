#!/usr/bin/env python3
"""Build one reproducible, manifest-locked OmegaFlow workload runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


MANIFEST_SCHEMA = "omegaflow-runtime-manifest-v1"
TELEMETRY_SCHEMA = "omegaflow-envoy-telemetry-v1"
AWSH_SCHEMA = "awsh-v1"
ARCHITECTURES = ("amd64", "arm64")


def remove_runtime_tree(path: Path) -> None:
    if not path.exists():
        return
    for item in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if item.is_symlink():
            continue
        item.chmod(0o700 if item.is_dir() else 0o600)
    path.chmod(0o700)
    shutil.rmtree(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_go(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["go", *args],
        cwd=root / "runtime" / "envoy",
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def build_runtime(
    root: Path,
    architecture: str,
    *,
    output: Path,
    version: str,
    source_revision: str,
) -> Path:
    if architecture not in ARCHITECTURES:
        raise ValueError(f"unsupported workload architecture: {architecture}")
    if output.exists():
        remove_runtime_tree(output)
    (output / "bin").mkdir(parents=True)
    (output / "libexec").mkdir()

    go_version = run_go(root, "env", "GOVERSION")
    environment = os.environ.copy()
    environment.update({"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": architecture})
    envoy = output / "bin" / "envoy"
    run_go(
        root,
        "build",
        "-trimpath",
        "-buildvcs=false",
        "-ldflags=-s -w -buildid=",
        "-o",
        str(envoy),
        "./cmd/omegaflow-envoy",
        env=environment,
    )

    prototype = root / "docs" / "future" / "prototype" / "awsh"
    shutil.copyfile(prototype / "awsh", output / "bin" / "awsh")
    shutil.copyfile(
        prototype / "awsh-driver.bash",
        output / "libexec" / "awsh-driver.bash",
    )
    for path in (envoy, output / "bin" / "awsh"):
        path.chmod(0o555)
    (output / "libexec" / "awsh-driver.bash").chmod(0o444)

    files = []
    for path in sorted((item for item in output.rglob("*") if item.is_file())):
        relative = path.relative_to(output).as_posix()
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "executable": bool(stat.S_IMODE(path.stat().st_mode) & 0o111),
                "sha256": sha256(path),
            }
        )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "omegaflow_version": version,
        "source_revision": source_revision,
        "telemetry_schema": TELEMETRY_SCHEMA,
        "awsh_schema": AWSH_SCHEMA,
        "os": "linux",
        "architecture": architecture,
        "go_version": go_version,
        "files": files,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o444)
    for directory in (output / "bin", output / "libexec", output):
        directory.chmod(0o555)
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=ARCHITECTURES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    manifest = build_runtime(
        root,
        args.architecture,
        output=args.output,
        version=args.version,
        source_revision=args.source_revision,
    )
    print(f"built linux-{args.architecture} workload runtime: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
