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
    package = source / "src/omegaflow"
    package.mkdir(parents=True)
    for name in ("__init__.py", "runtime_artifact.py"):
        shutil.copyfile(ROOT / "src/omegaflow" / name, package / name)
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
    fixtures.tool.generate(table, source)
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
        subprocess.run([sys.executable, str(source / "tools/bash_builds.py"), "generate"], check=True)
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


def load_build_tool(source, monkeypatch):
    monkeypatch.syspath_prepend(str(source / "tools"))
    spec = importlib.util.spec_from_file_location("artifact_build_tool", source / "tools/build_envoy_runtime.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


@pytest.fixture
def qualified_artifact(checkout, tmp_path, monkeypatch):
    """Synthetic receipt binds a real built artifact; this does not qualify Bash."""
    source, assets, key = checkout
    artifact = tmp_path / "runtime"
    result = build(source, assets, key, artifact)
    assert result.returncode == 0, result.stderr
    tool = load_build_tool(source, monkeypatch)
    table = fixtures.tool.read(source / "runtime/envoy/bash-builds.json")
    receipt = table["builds"][key]["qualification"]
    for name, data in tool.inventory(artifact).items():
        path = tool.PREFIX + name
        if name not in {"manifest.json", "etc/bash-builds.json", "bin/envoy"}:
            receipt["adapter_inputs"][path] = fixtures.tool.sha(data)
            receipt["inputs"][receipt["assets"][path]] = fixtures.tool.sha(data)
    receipt["inputs"]["/omegaflow-runtime/bin/envoy"] = fixtures.tool.sha(
        (artifact / "bin/envoy").read_bytes())
    fixtures.tool.validate_table(table)
    (source / "runtime/envoy/bash-builds.json").write_bytes(fixtures.tool.canonical(table) + b"\n")
    fixtures.tool.generate(table, source)
    manifest = json.loads((artifact / "manifest.json").read_bytes())
    replace_artifact_file(artifact, manifest, "etc/bash-builds.json", fixtures.tool.canonical(table) + b"\n")
    assert tool.verify_qualified(artifact, REVISION) == manifest
    return tool, artifact, manifest, source, table, key


def replace_artifact_file(artifact, manifest, name, data):
    """Rebind metadata too, so receipt tests cannot pass from a digest failure."""
    path = artifact / name
    path.chmod(0o644)
    path.write_bytes(data)
    path.chmod(0o555 if name.startswith("bin/") else 0o444)
    item = next(item for item in manifest["files"] if item["path"] == name)
    item.update(size=len(data), sha256=fixtures.tool.sha(data))
    path = artifact / "manifest.json"
    path.chmod(0o644)
    path.write_bytes(fixtures.tool.canonical(manifest) + b"\n")
    path.chmod(0o444)


@pytest.mark.parametrize("fault", ["missing", "extra", "bytes", "writable-file", "executable-data",
    "writable-directory", "manifest-mode", "symlink", "directory-symlink", "fifo", "duplicate-path",
    "duplicate-field", "unknown-field", "traversal", "absolute-path", "size", "revision", "platform",
    "toolchain", "schema", "version", "architecture", "stale-consumer", "stale-envoy", "stale-awsh"])
def test_qualified_guard_rejects_drift_without_writing(qualified_artifact, tmp_path, fault):
    tool, artifact, manifest, source, table, key = qualified_artifact
    path = artifact / "etc/inputrc"
    if fault in {"missing", "symlink", "fifo"}:
        path.parent.chmod(0o755)
        path.unlink()
        if fault == "symlink":
            target = tmp_path / "aliased-inputrc"
            target.write_bytes(b"")
            path.symlink_to(target)
        elif fault == "fifo":
            os.mkfifo(path)
        path.parent.chmod(0o555)
    elif fault == "directory-symlink":
        artifact.chmod(0o755)
        directory = artifact / "etc"
        directory.chmod(0o755)
        directory.rename(tmp_path / "aliased-etc")
        (tmp_path / "aliased-etc").chmod(0o555)
        directory.symlink_to(tmp_path / "aliased-etc", target_is_directory=True)
        artifact.chmod(0o555)
    elif fault == "extra":
        artifact.chmod(0o755)
        (artifact / "extra").write_bytes(b"unlisted")
        artifact.chmod(0o555)
    elif fault == "bytes":
        path.chmod(0o644)
        path.write_bytes(b"changed")
        path.chmod(0o444)
    elif fault in {"writable-file", "executable-data"}:
        path.chmod(0o644 if fault == "writable-file" else 0o555)
    elif fault == "writable-directory":
        path.parent.chmod(0o755)
    elif fault == "manifest-mode":
        (artifact / "manifest.json").chmod(0o644)
    elif fault in {"stale-consumer", "stale-envoy", "stale-awsh"}:
        relative = {"stale-consumer": "src/omegaflow/bash_builds.py",
                    "stale-envoy": "runtime/envoy/bashbuild/envoy_generated.go",
                    "stale-awsh": "runtime/envoy/bashbuild/awsh_generated.go"}[fault]
        (source / relative).write_text("stale generated consumer\n")
    else:
        if fault == "duplicate-path":
            manifest["files"].append(manifest["files"][0].copy())
        elif fault == "unknown-field":
            manifest["unexpected"] = True
        elif fault in {"traversal", "absolute-path"}:
            manifest["files"][0]["path"] = "etc/../inputrc" if fault == "traversal" else "/etc/inputrc"
        elif fault == "size":
            manifest["files"][0]["size"] += 1
        elif fault == "revision":
            manifest["source_revision"] = "b" * 40
        elif fault == "platform":
            manifest["target"]["os"] = "darwin"
        elif fault == "architecture":
            manifest["target"]["arch"] = "arm64"
        elif fault == "toolchain":
            manifest["go_toolchain"] = "go0.0.0"
        elif fault == "schema":
            manifest["schemas"]["helper"] = "future"
        elif fault == "version":
            manifest["version"] = "wrong-package-version"
        data = fixtures.tool.canonical(manifest) + b"\n"
        if fault == "duplicate-field":
            data = data.replace(b'{', b'{"version":"duplicate",', 1)
        path = artifact / "manifest.json"
        path.chmod(0o644)
        path.write_bytes(data)
        path.chmod(0o444)
    before = (artifact / "manifest.json").read_bytes()
    with pytest.raises((ValueError, OSError, KeyError)):
        tool.verify_qualified(artifact, REVISION)
    assert (artifact / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["old-awsh", "old-envoy", "old-assets", "unqualified", "empty", "different-table",
    "missing-envoy-proof", "wrong-envoy-proof"])
def test_coherent_manifest_does_not_replace_qualification(qualified_artifact, fault):
    tool, artifact, manifest, source, table, key = qualified_artifact
    if fault == "old-awsh":
        replace_artifact_file(artifact, manifest, "bin/awsh", b"other executable")
    elif fault == "old-envoy":
        replace_artifact_file(artifact, manifest, "bin/envoy", b"other executable")
    elif fault == "old-assets":
        replace_artifact_file(artifact, manifest, "lib/locale/C.UTF-8/LC_CTYPE", b"other locale")
    else:
        if fault == "unqualified":
            table["builds"][key]["qualification"] = None
        elif fault == "empty":
            table["builds"].clear()
        elif fault == "different-table":
            table["builds"][key]["candidate"]["release"] = "different candidate"
            receipt = table["builds"][key]["qualification"]
            receipt["candidate_sha256"] = fixtures.tool.sha(fixtures.tool.canonical(table["builds"][key]["candidate"]))
        elif fault == "missing-envoy-proof":
            del table["builds"][key]["qualification"]["inputs"]["/omegaflow-runtime/bin/envoy"]
        else:
            table["builds"][key]["qualification"]["inputs"]["/omegaflow-runtime/bin/envoy"] = "0" * 64
        if fault != "different-table":
            (source / "runtime/envoy/bash-builds.json").write_bytes(fixtures.tool.canonical(table) + b"\n")
            fixtures.tool.generate(table, source)
        replace_artifact_file(artifact, manifest, "etc/bash-builds.json",
            fixtures.tool.canonical({"version": 1, "builds": fixtures.tool.qualified(table)}) + b"\n")
    tool.read_artifact(artifact, REVISION)  # All manifest checks still pass.
    with pytest.raises(ValueError):
        tool.verify_qualified(artifact, REVISION)


def test_qualified_guard_is_read_only_and_requires_canonical_root(qualified_artifact, tmp_path):
    tool, artifact, manifest, *_ = qualified_artifact
    before = tool.inventory(artifact)
    assert tool.verify_qualified(artifact, REVISION) == manifest
    assert tool.inventory(artifact) == before
    alias = tmp_path / "alias"
    alias.symlink_to(artifact, target_is_directory=True)
    with pytest.raises(ValueError, match="noncanonical"):
        tool.verify_qualified(alias, REVISION)


def test_packaged_qualification_and_client_staging_preserve_payload(qualified_artifact, tmp_path, monkeypatch):
    tool, artifact, manifest, *_ = qualified_artifact
    original = tool.inventory(artifact)
    spec = importlib.util.spec_from_file_location("packaged_qualification_runner",
        ROOT / "tests/envoy-bash-qualification/run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    commands = []

    def build(command, **kwargs):
        commands.append(command)
        if command[0] == "go":
            assert command[:2] == ["go", "test"], "packaged Awsh must never be rebuilt"
            Path(command[command.index("-o") + 1]).write_bytes(b"test probe only")
        else:
            assert command[:2] == ["docker", "build"]
            Path(command[command.index("--iidfile") + 1]).write_text("sha256:" + "a" * 64)

    monkeypatch.setattr(runner.subprocess, "run", build)
    context = tmp_path / "qualification"
    runner.stage(context, artifact)
    assert tool.inventory(context / "runtime") == original
    assert (context / "qualification/bin/awsh-real").read_bytes() == original["bin/awsh"]
    dockerfile = (context / "Dockerfile").read_text()
    assert "cp -a /usr/share/terminfo" not in dockerfile
    assert "cp -a /usr/lib/locale" not in dockerfile
    assert len(commands) == len(runner.PACKAGES)
    spec = importlib.util.spec_from_file_location("packaged_client_staging",
        ROOT / "tests/envoy-session-startup/staging.py")
    staging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staging)
    monkeypatch.setattr(staging, "artifact_tool", lambda: tool)
    iid, staged = staging.stage_artifact(tmp_path, "client", "sha256:" + "b" * 64, artifact)
    assert iid == "sha256:" + "a" * 64
    assert tool.inventory(staged / "runtime") == original
    assert tool.inventory(artifact) == original
    assert len(commands) == len(runner.PACKAGES) + 1
    assert not (staged / "manifest.py").exists()
    assert not (staged / "bash-builds.json").exists()


def test_verification_cli_needs_no_build_arguments_and_rejects_mixing(qualified_artifact, monkeypatch):
    tool, artifact, *_ = qualified_artifact
    monkeypatch.setattr(sys, "argv", ["build-runtime", "--verify-qualified", str(artifact),
                                      "--source-revision", REVISION])
    tool.main()
    monkeypatch.setattr(sys, "argv", ["build-runtime", "--verify-qualified", str(artifact),
                                      "--source-revision", REVISION, "--arch", "amd64"])
    with pytest.raises(SystemExit) as error:
        tool.main()
    assert error.value.code == 2
    monkeypatch.setattr(sys, "argv", ["build-runtime", "--output", str(artifact.parent / "new"),
                                      "--source-revision", REVISION])
    with pytest.raises(SystemExit) as error:
        tool.main()
    assert error.value.code == 2


def test_builder_packages_only_qualified_projection(checkout, tmp_path, monkeypatch):
    source, assets, key = checkout
    table = fixtures.tool.read(source / "runtime/envoy/bash-builds.json")
    candidate = table["builds"][key]["candidate"].copy()
    candidate["executable_sha256"] = "c" * 64
    table["builds"]["c" * 64] = {"candidate": candidate, "qualification": None}
    (source / "runtime/envoy/bash-builds.json").write_bytes(fixtures.tool.canonical(table) + b"\n")
    fixtures.tool.generate(table, source)
    tool = load_build_tool(source, monkeypatch)
    output = tmp_path / "projected-runtime"
    monkeypatch.setattr(sys, "argv", ["build-runtime", "--output", str(output), "--assets", str(assets),
                                      "--bash-sha256", key, "--arch", "amd64", "--source-revision", REVISION])
    tool.main()
    manifest, payload = tool.read_artifact(output, REVISION)
    assert set(fixtures.tool.decode(payload["etc/bash-builds.json"])["builds"]) == {key}
    assert payload["etc/bash-builds.json"] == (source / "runtime/envoy/etc/bash-builds.json").read_bytes()
