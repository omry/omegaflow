"""Synthetic manifest/integrity controls; these do not qualify any Bash build."""
import copy
import json
import os
from pathlib import Path
import stat

import pytest

from omegaflow import __version__, bash_builds
from omegaflow.runtime_artifact import capture_artifact
import test_bash_builds as fixtures


@pytest.fixture
def runtime_fixture(tmp_path, monkeypatch):
    root = tmp_path / "installed" / "runtime" / "linux-amd64"
    data = {p.removeprefix("/omegaflow-runtime/"): b"synthetic " + p.encode()
            for p in fixtures.ASSETS}
    data.update({"bin/envoy": b"synthetic Envoy,never executed", "etc/inputrc": b""})
    table = fixtures.table()
    proof = next(iter(table["builds"].values()))["qualification"]
    for name, value in data.items():
        path = "/omegaflow-runtime/" + name
        if name != "bin/envoy":
            proof["adapter_inputs"][path] = bash_builds.sha(value)
        proof["inputs"][path] = bash_builds.sha(value)
    bash_builds.validate_table(table)
    table_bytes = bash_builds.canonical(table) + b"\n"
    monkeypatch.setattr(bash_builds, "_TABLE", table_bytes[:-1].decode())
    data["etc/bash-builds.json"] = table_bytes
    for name, value in data.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        path.chmod(0o755 if name.startswith("bin/") else 0o644)
    manifest = {"schema": "omegaflow-runtime-manifest-v1", "version": __version__,
                "source_revision": "a" * 40, "go_toolchain": bash_builds.GO_TOOLCHAIN,
                "target": {"os": "linux", "arch": "amd64"},
                "schemas": {"telemetry": "omegaflow-envoy-telemetry-v1",
                            "private": "awsh-v1", "helper": "awsh-helper-v1"},
                "files": [{"path": name, "size": len(value), "executable": name.startswith("bin/"),
                           "sha256": bash_builds.sha(value)} for name, value in sorted(data.items())]}
    (root / "manifest.json").write_bytes(bash_builds.canonical(manifest) + b"\n")
    yield root, manifest, data
    for directory, _, _ in os.walk(tmp_path):
        Path(directory).chmod(0o700)


def write_manifest(root, manifest):
    (root / "manifest.json").write_bytes(bash_builds.canonical(manifest) + b"\n")


def test_installer_write_bits_are_not_manifest_changes(runtime_fixture):
    root, manifest, data = runtime_fixture
    before = (root / "manifest.json").read_bytes()
    with capture_artifact(root, source_revision="a" * 40) as artifact:
        assert artifact.manifest == manifest and artifact.payload == data
        assert artifact.manifest_bytes == before
    with pytest.raises(ValueError, match="mode"):
        with capture_artifact(root, readonly=True):
            pass
    for path in root.rglob("*"):
        path.chmod(0o555 if path.is_dir() or path.parent.name == "bin" else 0o444)
    root.chmod(0o555)
    with capture_artifact(root, readonly=True) as artifact:
        assert artifact.manifest_bytes == before


@pytest.mark.parametrize("fault", ["version", "revision", "target", "toolchain", "schema",
    "schemas", "extra-key", "files-type", "duplicate-path", "absolute", "traversal", "dot",
    "backslash", "size-bool", "negative-size", "digest", "executable", "unknown-path"])
def test_invalid_manifest(runtime_fixture, fault):
    root, manifest, _ = runtime_fixture
    if fault in {"version", "go_toolchain", "schema"}:
        manifest[fault] = "wrong"
    elif fault == "revision":
        manifest["source_revision"] = "short"
    elif fault == "target":
        manifest["target"]["arch"] = "arm64"
    elif fault == "toolchain":
        manifest["go_toolchain"] = "go0.0.0"
    elif fault == "schemas":
        manifest["schemas"]["helper"] = "different"
    elif fault == "extra-key":
        manifest["extra"] = True
    elif fault == "files-type":
        manifest["files"] = {}
    elif fault == "duplicate-path":
        manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    else:
        item = manifest["files"][0]
        if fault in {"absolute", "traversal", "dot", "backslash", "unknown-path"}:
            item["path"] = {"absolute": "/bin/awsh", "traversal": "../awsh", "dot": "bin/./awsh",
                            "backslash": "bin\\awsh", "unknown-path": "other/awsh"}[fault]
        elif fault == "size-bool":
            item["size"] = True
        elif fault == "negative-size":
            item["size"] = -1
        elif fault == "digest":
            item["sha256"] = "e" * 64
        else:
            item["executable"] = False
    write_manifest(root, manifest)
    with pytest.raises(ValueError):
        with capture_artifact(root):
            pytest.fail("invalid manifest was exposed")


@pytest.mark.parametrize("fault", ["extra", "empty-directory", "missing", "changed", "symlink",
    "directory-link", "hardlink", "manifest-hardlink", "fifo", "socket", "setuid", "sticky-directory",
    "executable-data", "nonexecutable-bin", "executable-manifest", "duplicate-field"])
def test_entire_source_inventory_and_types(runtime_fixture, tmp_path, fault):
    root, _, _ = runtime_fixture
    path = root / "etc/inputrc"
    if fault == "extra":
        (root / "etc/extra").write_bytes(b"")
    elif fault == "empty-directory":
        (root / "extra").mkdir()
    elif fault in {"missing", "changed"}:
        path.unlink() if fault == "missing" else path.write_bytes(b"changed")
    elif fault == "symlink":
        path.unlink()
        path.symlink_to(root / "bin/envoy")
    elif fault == "directory-link":
        moved = tmp_path / "moved"
        (root / "etc").rename(moved)
        (root / "etc").symlink_to(moved, target_is_directory=True)
    elif fault in {"hardlink", "manifest-hardlink"}:
        os.link(path if fault == "hardlink" else root / "manifest.json", tmp_path / "alias")
    elif fault in {"fifo", "socket"}:
        path.unlink()
        if fault == "fifo":
            os.mkfifo(path)
        else:
            import socket
            sock = socket.socket(socket.AF_UNIX)
            try:
                sock.bind(str(path))
            finally:
                sock.close()
    elif fault == "setuid":
        (root / "bin/envoy").chmod(0o4755)
    elif fault == "sticky-directory":
        (root / "etc").chmod(0o1755)
    elif fault == "executable-data":
        path.chmod(0o755)
    elif fault == "nonexecutable-bin":
        (root / "bin/envoy").chmod(0o644)
    elif fault == "executable-manifest":
        (root / "manifest.json").chmod(0o755)
    else:
        path = root / "manifest.json"
        path.write_bytes(path.read_bytes().replace(b'{', b'{"schema":"duplicate",', 1))
    with pytest.raises(ValueError):
        with capture_artifact(root):
            pytest.fail("invalid source was exposed")


@pytest.mark.parametrize("level", ["root", "ancestor"])
def test_never_follow_source_ancestor_links(runtime_fixture, tmp_path, level):
    root, _, _ = runtime_fixture
    if level == "root":
        source = tmp_path / "alias"
        source.symlink_to(root, target_is_directory=True)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(root.parent, target_is_directory=True)
        source = alias / root.name
    with pytest.raises(ValueError):
        with capture_artifact(source):
            pytest.fail("source link was followed")


@pytest.mark.parametrize("fault", ["file-mutation", "file-replacement", "root-replacement", "ancestor-replacement"])
def test_capture_stays_bound_until_consumer_finishes(runtime_fixture, fault):
    root, _, _ = runtime_fixture
    with pytest.raises(ValueError, match="replaced|mutated|changed"):
        with capture_artifact(root):
            if fault == "file-mutation":
                (root / "bin/envoy").write_bytes(b"changed")
            elif fault == "file-replacement":
                path = root / "bin/envoy"
                replacement = path.with_name("new")
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(stat.S_IMODE(path.stat().st_mode))
                replacement.replace(path)
            else:
                path = root if fault == "root-replacement" else root.parent
                path.rename(path.with_name("old"))
                path.mkdir()
