"""Build one reproducible runtime candidate from explicit qualified asset inputs.

The release caller supplies its source revision. This writes the existing Go
runtimebundle.Manifest format. C4.2 still owns qualification of packaged bytes;
building a candidate does not qualify its newly built Awsh.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import subprocess
import tomllib

import bash_builds


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "runtime/envoy"
TABLE = MODULE / "bash-builds.json"
PREFIX = "/omegaflow-runtime/"


def regular_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"nonregular build input: {path}")
        return stream.read()


def inventory(root: Path) -> dict[str, bytes]:
    """Reject symlinks and special files, including directory aliases."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"missing regular input tree: {root}")
    result = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError("symlinked build input directory")
        for name in files:
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = regular_bytes(path)
    return result


def source_identity() -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in inventory(MODULE).items()}


def read_artifact(root: Path, source_revision: str | None = None) -> tuple[dict, dict[str, bytes]]:
    """The shared schema also reads candidates before their first qualification."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from omegaflow.runtime_artifact import capture_artifact
    finally:
        sys.path.pop(0)
    with capture_artifact(root, version=None, target=None,
                          source_revision=source_revision, qualified=False, readonly=True,
                          go_toolchain="go" + regular_bytes(MODULE / ".go-version").decode().strip()) as artifact:
        return artifact.manifest, artifact.payload


def verify_qualified(root: Path, source_revision: str) -> dict:
    bash_builds.require(root.is_absolute() and root == root.resolve(strict=True), "noncanonical runtime root")
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from omegaflow.runtime_artifact import capture_artifact
    finally:
        sys.path.pop(0)
    table = bash_builds.validate_table(bash_builds.decode(regular_bytes(TABLE)))
    bash_builds.generate(table, ROOT, check=True)
    expected = bash_builds.canonical({"version": 1, "builds": bash_builds.qualified(table)}) + b"\n"
    version = tomllib.loads(regular_bytes(ROOT / "pyproject.toml").decode())["project"]["version"]
    with capture_artifact(root, source_revision=source_revision, version=version, table=expected,
                          go_toolchain="go" + regular_bytes(MODULE / ".go-version").decode().strip(),
                          readonly=True) as artifact:
        return artifact.manifest


def build(output: Path, assets: Path, bash_sha256: str, arch: str, source_revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("source revision must be one complete lowercase commit identity")
    if arch not in {"amd64", "arm64"}:
        raise ValueError("unsupported runtime architecture")
    output = output.parent.resolve(strict=True) / output.name
    if output.is_relative_to(MODULE.resolve()):
        raise ValueError("build output must be outside the runtime source module")
    if assets.absolute() != assets.resolve(strict=True):
        raise ValueError("asset root must be canonical and may not be a symlink")
    before = source_identity()
    version_bytes = regular_bytes(ROOT / "pyproject.toml")
    table_bytes = regular_bytes(TABLE)
    table = bash_builds.validate_table(bash_builds.decode(table_bytes))
    bash_builds.generate(table, ROOT, check=True)
    entry = table["builds"].get(bash_sha256)
    if entry is None or entry["qualification"] is None:
        raise ValueError("an existing qualified Bash profile must select the trusted inputs")
    if entry["candidate"]["target"]["arch"] != arch:
        raise ValueError("qualified assets do not establish this target architecture")
    inputs = entry["qualification"]["adapter_inputs"]
    trusted = {}
    for prefix in ("share/terminfo/", "lib/locale/"):
        expected = {name.removeprefix(PREFIX): digest for name, digest in inputs.items()
                    if name.startswith(PREFIX + prefix)}
        observed = {prefix + name: data for name, data in inventory(assets / prefix).items()}
        if set(observed) != set(expected):
            raise ValueError("missing or extra trusted asset input")
        for name, data in observed.items():
            if hashlib.sha256(data).hexdigest() != expected[name]:
                raise ValueError(f"qualified asset bytes changed: {name}")
        trusted.update(observed)
    terminal = [name for name in trusted if name.startswith("share/terminfo/")
                and name.endswith("/xterm-256color")]
    if len(terminal) != 1:
        raise ValueError("one exact xterm-256color entry is required")
    # Qualification may have used a larger terminfo database. The artifact has
    # the fixed selected terminal and the complete selected locale tree.
    payload = {name: data for name, data in trusted.items()
               if name == terminal[0] or name.startswith("lib/locale/")}
    for name in ("etc/awsh-bashrc", "etc/inputrc"):
        data = regular_bytes(MODULE / name)
        if hashlib.sha256(data).hexdigest() != inputs[PREFIX + name]:
            raise ValueError(f"qualified fixed input changed: {name}")
        payload[name] = data
    if payload["etc/inputrc"]:
        raise ValueError("the fixed inputrc must be empty")
    payload["etc/bash-builds.json"] = bash_builds.canonical({"version": 1, "builds": bash_builds.qualified(table)}) + b"\n"
    pin = regular_bytes(MODULE / ".go-version").decode("ascii").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", pin):
        raise ValueError("one exact Go patch version must be pinned")
    env = os.environ | {"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": arch,
                        "GOAMD64": "v1", "GOARM64": "v8.0", "GOEXPERIMENT": "",
                        "GOFLAGS": "", "GOENV": "off", "GOFIPS140": "off", "GOWORK": "off",
                        "GOTOOLCHAIN": "local", "GOPROXY": "off"}
    toolchain = subprocess.check_output(["go", "env", "GOVERSION"], cwd=MODULE,
                                        env=env, text=True).strip()
    if toolchain != "go" + pin:
        raise ValueError(f"runtime build requires go{pin},got {toolchain}")
    output.mkdir(mode=0o700)  # Never overwrite, resume or clean another output.
    (output / "bin").mkdir()
    for name, package in (("envoy", "omegaflow-envoy"), ("awsh", "awsh")):
        subprocess.run(["go", "build", "-mod=readonly", "-trimpath", "-buildvcs=false",
                        "-ldflags=-s -w -buildid=", "-o", str(output / "bin" / name),
                        "./cmd/" + package], cwd=MODULE, env=env, check=True)
    for name, data in payload.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o444)
    for name in ("envoy", "awsh"):
        (output / "bin" / name).chmod(0o555)
    if (source_identity() != before or regular_bytes(TABLE) != table_bytes
            or regular_bytes(ROOT / "pyproject.toml") != version_bytes):
        raise ValueError("source or declared input changed during the build")
    files = [{"path": name, "size": len(data), "executable": name.startswith("bin/"),
              "sha256": hashlib.sha256(data).hexdigest()}
             for name, data in sorted(inventory(output).items())]
    version = tomllib.loads(version_bytes.decode("utf-8"))["project"]["version"]
    manifest = {"schema": "omegaflow-runtime-manifest-v1", "version": version,
                "source_revision": source_revision, "go_toolchain": toolchain,
                "target": {"os": "linux", "arch": arch},
                "schemas": {"telemetry": "omegaflow-envoy-telemetry-v1",
                            "private": "awsh-v1", "helper": "awsh-helper-v1"}, "files": files}
    # No manifest exists for an incomplete candidate. C5 owns installed-source
    # staging; these are the immutable release build outputs themselves.
    target = output / "manifest.json"
    target.write_bytes(bash_builds.canonical(manifest) + b"\n")
    target.chmod(0o444)
    for directory, _, _ in os.walk(output, topdown=False):
        Path(directory).chmod(0o555)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify-qualified", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--bash-sha256")
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    if args.verify_qualified is not None:
        if any(x is not None for x in (args.assets, args.bash_sha256, args.arch)):
            parser.error("build arguments cannot accompany --verify-qualified")
        verify_qualified(args.verify_qualified, args.source_revision)
    else:
        if any(x is None for x in (args.assets, args.bash_sha256, args.arch)):
            parser.error("--assets, --bash-sha256 and --arch are required for a build")
        build(args.output, args.assets, args.bash_sha256, args.arch, args.source_revision)


if __name__ == "__main__":
    main()
