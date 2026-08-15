from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
from pathlib import Path

import pytest

from omegaflow.workload_runtime import (
    AWSH_SCHEMA,
    MANIFEST_SCHEMA,
    TELEMETRY_SCHEMA,
    WorkloadRuntimeError,
    _stage_runtime,
    decode_manifest,
    validate_runtime_source,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def runtime_fixture(root: Path, *, architecture: str = "amd64") -> Path:
    payloads = {
        "bin/awsh": (b"#!/bin/sh\n", 0o555),
        "bin/envoy": (b"envoy-binary", 0o555),
        "libexec/awsh-driver.bash": (b"driver", 0o444),
    }
    files = []
    for relative, (content, mode) in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
        files.append(
            {
                "path": relative,
                "size": len(content),
                "executable": bool(mode & 0o111),
                "sha256": digest(content),
            }
        )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "omegaflow_version": "0.9.0",
        "source_revision": "a" * 40,
        "telemetry_schema": TELEMETRY_SCHEMA,
        "awsh_schema": AWSH_SCHEMA,
        "os": "linux",
        "architecture": architecture,
        "go_version": "go version go1.25.1 linux/amd64",
        "files": sorted(files, key=lambda item: item["path"]),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest) + "\n",
        encoding="utf-8",
    )
    return root


def test_runtime_manifest_rejects_duplicate_and_unknown_fields() -> None:
    duplicate = b'{"schema":"a","schema":"b"}'
    with pytest.raises(WorkloadRuntimeError, match="duplicate field"):
        decode_manifest(duplicate)

    value = {
        "schema": MANIFEST_SCHEMA,
        "omegaflow_version": "0.9.0",
        "source_revision": "a" * 40,
        "telemetry_schema": TELEMETRY_SCHEMA,
        "awsh_schema": AWSH_SCHEMA,
        "os": "linux",
        "architecture": "amd64",
        "go_version": "go1.25",
        "files": [],
        "extra": True,
    }
    with pytest.raises(WorkloadRuntimeError, match="unknown extra"):
        decode_manifest(json.dumps(value).encode())


def test_runtime_source_validation_detects_tampering_and_extra_files(
    tmp_path: Path,
) -> None:
    source = runtime_fixture(tmp_path / "runtime")
    manifest = validate_runtime_source(source, expected_platform="linux-amd64")
    assert [item.path for item in manifest.files] == [
        "bin/awsh",
        "bin/envoy",
        "libexec/awsh-driver.bash",
    ]

    (source / "bin" / "envoy").chmod(0o755)
    (source / "bin" / "envoy").write_bytes(b"tampered")
    (source / "bin" / "envoy").chmod(0o555)
    with pytest.raises(WorkloadRuntimeError, match="size mismatch"):
        validate_runtime_source(source)

    source = runtime_fixture(tmp_path / "extra")
    (source / "bin" / "extra").write_text("extra", encoding="utf-8")
    with pytest.raises(WorkloadRuntimeError, match="additional bin/extra"):
        validate_runtime_source(source)


def test_runtime_source_rejects_symlinks_and_mode_changes(tmp_path: Path) -> None:
    source = runtime_fixture(tmp_path / "symlink")
    (source / "bin" / "link").symlink_to("envoy")
    with pytest.raises(WorkloadRuntimeError, match="symlink"):
        validate_runtime_source(source)

    source = runtime_fixture(tmp_path / "mode")
    (source / "bin" / "envoy").chmod(0o744)
    with pytest.raises(WorkloadRuntimeError, match="executable mode mismatch"):
        validate_runtime_source(source)


def test_stage_runtime_creates_fresh_nonwritable_verified_tree(tmp_path: Path) -> None:
    source = runtime_fixture(tmp_path / "source")
    (source / "bin" / "awsh").chmod(0o755)
    (source / "bin" / "envoy").chmod(0o755)
    (source / "libexec" / "awsh-driver.bash").chmod(0o644)
    destination = tmp_path / "run" / "reploy" / "input" / "runtime"

    manifest = _stage_runtime(source, destination, platform="linux-amd64")

    assert manifest.platform == "linux-amd64"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o555
    assert stat.S_IMODE((destination / "bin" / "envoy").stat().st_mode) == 0o555
    assert stat.S_IMODE((destination / "manifest.json").stat().st_mode) == 0o444
    with pytest.raises(WorkloadRuntimeError, match="must not exist"):
        _stage_runtime(source, destination, platform="linux-amd64")


def test_runtime_builder_writes_sorted_manifest_and_production_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    prototype = root / "docs" / "future" / "prototype" / "awsh"
    prototype.mkdir(parents=True)
    (prototype / "awsh").write_text("#!/bin/sh\n", encoding="utf-8")
    (prototype / "awsh-driver.bash").write_text("driver\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tools" / "build_workload_runtime.py"
    spec = importlib.util.spec_from_file_location("test_build_workload_runtime", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fake_go(_root: Path, *args: str, env=None) -> str:
        if args == ("env", "GOVERSION"):
            return "go1.25.1"
        output = Path(args[args.index("-o") + 1])
        output.write_bytes(b"envoy")
        return ""

    monkeypatch.setattr(module, "run_go", fake_go)
    output = tmp_path / "runtime"
    module.build_runtime(
        root,
        "amd64",
        output=output,
        version="0.9.0",
        source_revision="b" * 40,
    )

    manifest = validate_runtime_source(output, expected_platform="linux-amd64")
    assert [item.path for item in manifest.files] == [
        "bin/awsh",
        "bin/envoy",
        "libexec/awsh-driver.bash",
    ]
