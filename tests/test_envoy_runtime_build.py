"""Build/manifest tests with synthetic asset evidence; no Bash qualification claim."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import test_bash_builds as fixtures


ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


@pytest.fixture
def checkout(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "runtime/envoy", source / "runtime/envoy")
    (source / "tools").mkdir()
    for name in ("bash_builds.py", "build_envoy_runtime.py"):
        shutil.copyfile(ROOT / "tools" / name, source / "tools" / name)
    shutil.copyfile(ROOT / "pyproject.toml", source / "pyproject.toml")
    cases = source / "tests/fixtures/envoy-protocol-v1/cases.jsonl"
    cases.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests/fixtures/envoy-protocol-v1/cases.jsonl", cases)
    assets = tmp_path / "assets"
    data = {
        "/omegaflow-runtime/bin/awsh": b"synthetic old qualification input;not executed",
        "/omegaflow-runtime/etc/awsh-bashrc": (source / "runtime/envoy/etc/awsh-bashrc").read_bytes(),
        "/omegaflow-runtime/etc/inputrc": b"",
        "/omegaflow-runtime/share/terminfo/x/xterm-256color": b"synthetic terminal",
        "/omegaflow-runtime/lib/locale/C.UTF-8/LC_CTYPE": b"synthetic locale",
    }
    table = fixtures.table()
    key, entry = next(iter(table["builds"].items()))
    receipt = entry["qualification"]
    for name, value in data.items():
        receipt["adapter_inputs"][name] = hashlib.sha256(value).hexdigest()
        receipt["inputs"][receipt["assets"][name]] = receipt["adapter_inputs"][name]
        if "/share/" in name or "/lib/" in name:
            path = assets / name.removeprefix("/omegaflow-runtime/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
    fixtures.tool.validate_table(table)
    (source / "runtime/envoy/bash-builds.json").write_bytes(fixtures.tool.canonical(table) + b"\n")
    yield source, assets, key
    # Outputs are deliberately read-only. Restore only this test's owned dirs.
    for directory, _, _ in os.walk(tmp_path):
        Path(directory).chmod(0o700)


def build(source, assets, key, output, *, arch="amd64", revision=REVISION, env=None):
    return subprocess.run([sys.executable, str(source / "tools/build_envoy_runtime.py"),
                           "--output", str(output), "--assets", str(assets),
                           "--bash-sha256", key, "--arch", arch,
                           "--source-revision", revision],
                          text=True, capture_output=True, env=env)


def test_independent_source_paths_produce_identical_binaries_and_manifest(checkout, tmp_path):
    source, assets, key = checkout
    second = tmp_path / "another-source-path"
    shutil.copytree(source, second)
    outputs = [tmp_path / "first", tmp_path / "second"]
    for tree, output, fips in zip((source, second), outputs, ("off", "latest"), strict=True):
        result = build(tree, assets, key, output,
                       env=os.environ | {"GOFLAGS": "-this-must-not-be-inherited", "GOAMD64": "v3",
                                          "GOFIPS140": fips})
        assert result.returncode == 0, result.stderr
    one = {str(p.relative_to(outputs[0])): p.read_bytes() for p in outputs[0].rglob("*") if p.is_file()}
    two = {str(p.relative_to(outputs[1])): p.read_bytes() for p in outputs[1].rglob("*") if p.is_file()}
    assert one == two
    manifest = json.loads(one["manifest.json"])
    assert manifest["source_revision"] == REVISION
    assert manifest["go_toolchain"] == "go" + (source / "runtime/envoy/.go-version").read_text().strip()
    assert {x["path"] for x in manifest["files"]} == set(one) - {"manifest.json"}
    result = subprocess.run(["go", "test", "./runtimebundle", "-run", "^TestBuiltRuntimeCandidate$", "-count=1"],
                            cwd=source / "runtime/envoy", text=True, capture_output=True,
                            env=os.environ | {"OMEGAFLOW_TEST_BUILT_RUNTIME": str(outputs[0]), "GOTOOLCHAIN": "local"})
    assert result.returncode == 0, result.stdout + result.stderr
    # This is the real production command, not a packaged Go test executable.
    result = subprocess.run([str(outputs[0] / "bin/envoy")], text=True, capture_output=True)
    assert result.returncode == 2 and "Envoy requires" in result.stderr
    assert subprocess.check_output(["go", "tool", "buildid", str(outputs[0] / "bin/envoy")]).strip() == b""


@pytest.mark.parametrize("fault", ["missing", "extra", "changed", "symlink", "directory-symlink", "fifo"])
def test_reject_asset_drift_without_a_manifest(checkout, tmp_path, fault):
    source, assets, key = checkout
    terminal = assets / "share/terminfo/x/xterm-256color"
    if fault == "missing":
        terminal.unlink()
    elif fault == "extra":
        terminal.with_name("extra").write_bytes(b"not in the selected profile")
    elif fault == "changed":
        terminal.write_bytes(b"drift")
    elif fault == "symlink":
        data = terminal.read_bytes()
        terminal.unlink()
        target = tmp_path / "alias-target"
        target.write_bytes(data)
        terminal.symlink_to(target)
    elif fault == "directory-symlink":
        directory = terminal.parent
        moved = tmp_path / "moved-terminal-tree"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        terminal.unlink()
        os.mkfifo(terminal)
    output = tmp_path / "output"
    result = build(source, assets, key, output)
    assert result.returncode != 0
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("fault", ["toolchain", "architecture", "unqualified", "revision", "existing-output"])
def test_reject_wrong_identity_or_existing_output(checkout, tmp_path, fault):
    source, assets, key = checkout
    output = tmp_path / "output"
    if fault == "toolchain":
        (source / "runtime/envoy/.go-version").write_text("0.0.0\n")
    elif fault == "unqualified":
        path = source / "runtime/envoy/bash-builds.json"
        table = json.loads(path.read_bytes())
        table["builds"][key]["qualification"] = None
        path.write_text(json.dumps(table))
    elif fault == "existing-output":
        output.mkdir()
        (output / "keep").write_bytes(b"owned by an earlier build")
    result = build(source, assets, key, output,
                   arch="arm64" if fault == "architecture" else "amd64",
                   revision="bad" if fault == "revision" else REVISION)
    assert result.returncode != 0
    assert not (output / "manifest.json").exists()
    if fault == "existing-output":
        assert (output / "keep").read_bytes() == b"owned by an earlier build"
    if fault == "toolchain":
        assert "requires go0.0.0" in result.stderr


def test_source_change_during_real_build_cannot_publish_manifest(checkout, tmp_path, monkeypatch):
    source, assets, key = checkout
    monkeypatch.syspath_prepend(str(source / "tools"))
    spec = importlib.util.spec_from_file_location("runtime_build_drift", source / "tools/build_envoy_runtime.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    real_run = tool.subprocess.run
    changed = False

    def race(argv, **kwargs):
        nonlocal changed
        result = real_run(argv, **kwargs)
        if not changed and argv[:2] == ["go", "build"]:
            path = source / "runtime/envoy/go.mod"
            path.write_bytes(path.read_bytes() + b"\n")
            changed = True
        return result

    monkeypatch.setattr(tool.subprocess, "run", race)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="source or declared input changed"):
        tool.build(output, assets, key, "amd64", REVISION)
    assert changed and not (output / "manifest.json").exists()
