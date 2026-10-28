"""Installed-source staging controls; actual wheel acceptance also runs separately."""
from contextlib import contextmanager
import json
from pathlib import Path
import stat

import pytest

from omegaflow import runtime_staging
from omegaflow.runtime_artifact import Artifact, capture_artifact
from test_runtime_artifact import runtime_fixture


def installed(monkeypatch, root):
    monkeypatch.setattr(runtime_staging, "files", lambda package: root.parent.parent)


def test_private_manifest_last_read_only_stage_and_cleanup(runtime_fixture, monkeypatch):
    source, manifest, payload = runtime_fixture
    installed(monkeypatch, source)
    original = {str(p.relative_to(source)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in source.rglob("*") if p.is_file()}
    with runtime_staging.stage_runtime() as root:
        parent = root.parent
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
        assert root != source and source not in root.parents
        with capture_artifact(root, readonly=True) as artifact:
            assert artifact.manifest == manifest and artifact.payload == payload
            assert artifact.manifest_bytes == original["manifest.json"][0]
        for name in payload:
            assert stat.S_IMODE((root / name).stat().st_mode) == (0o555 if name.startswith("bin/") else 0o444)
    assert not parent.exists()
    assert original == {str(p.relative_to(source)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                        for p in source.rglob("*") if p.is_file()}


def test_missing_installed_resources_fail_without_source_override(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_staging, "files", lambda package: tmp_path)
    monkeypatch.setenv("OMEGAFLOW_RUNTIME_ARTIFACT", "/must/not/be/read/by/runtime")
    with pytest.raises(ValueError, match="runtime artifact"):
        with runtime_staging.stage_runtime():
            pytest.fail("missing installed runtime was exposed")
    with pytest.raises(TypeError):
        runtime_staging.stage_runtime(source=tmp_path).__enter__()


@pytest.mark.parametrize("fault", ["before-tree", "before-manifest", "during-copy", "consumer-error"])
def test_owned_partial_cleanup_and_source_preservation(runtime_fixture, monkeypatch, tmp_path, fault):
    source, _, payload = runtime_fixture
    installed(monkeypatch, source)
    parents = []
    original_temp = runtime_staging.tempfile.mkdtemp
    def mkdtemp(**kwargs):
        parent = original_temp(dir=tmp_path, **kwargs)
        parents.append(Path(parent))
        return parent
    monkeypatch.setattr(runtime_staging.tempfile, "mkdtemp", mkdtemp)
    original_mkdir = Path.mkdir
    def failing_mkdir(path, *args, **kwargs):
        if fault == "before-tree" and path.name == "runtime" and path.parent in parents:
            raise OSError("injected stage allocation failure")
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    original_open = Path.open
    def failing_open(path, mode="r", *args, **kwargs):
        if mode == "xb" and (fault == "before-manifest" and path.name == "manifest.json" or
                             fault == "during-copy" and path.name == "inputrc"):
            if path.name == "manifest.json":
                assert all((path.parent / name).is_file() for name in payload)
            raise OSError("injected stage write failure")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises((ValueError, RuntimeError), match="injected"):
        with runtime_staging.stage_runtime():
            if fault == "consumer-error":
                raise RuntimeError("injected consumer failure")
            pytest.fail("failed stage was exposed")
    assert parents and all(not p.exists() for p in parents)
    assert payload == {name: (source / name).read_bytes() for name in payload}


def test_mutation_during_copy_is_rejected_before_exposure(runtime_fixture, monkeypatch):
    source, _, _ = runtime_fixture
    installed(monkeypatch, source)
    verify = Artifact.verify
    count = 0
    def mutate(artifact):
        nonlocal count
        count += 1
        if count == 2:
            (source / "bin/envoy").write_bytes(b"mutated during copy")
        return verify(artifact)
    monkeypatch.setattr(Artifact, "verify", mutate)
    with pytest.raises(ValueError, match="mutated|changed"):
        with runtime_staging.stage_runtime():
            pytest.fail("mutated source was exposed")


def test_completed_stage_cannot_publish_a_different_valid_manifest(runtime_fixture, monkeypatch):
    source, _, payload = runtime_fixture
    installed(monkeypatch, source)
    original = (source / "manifest.json").read_bytes()
    @contextmanager
    def corrupt_before_final_validation(root, **kwargs):
        if root != source:
            path = root / "manifest.json"
            value = json.loads(path.read_bytes())
            value["source_revision"] = "b" * 40
            path.chmod(0o644)
            path.write_text(json.dumps(value))
            path.chmod(0o444)
        with capture_artifact(root, **kwargs) as artifact:
            yield artifact
    monkeypatch.setattr(runtime_staging, "capture_artifact", corrupt_before_final_validation)
    with pytest.raises(ValueError, match="differs from installed source"):
        with runtime_staging.stage_runtime():
            pytest.fail("a different manifest was published")
    assert (source / "manifest.json").read_bytes() == original
    assert payload == {name: (source / name).read_bytes() for name in payload}


@pytest.mark.parametrize("replace_parent", [True, False])
def test_parent_replacement_preserves_other_data_and_cleans_owned_payload(
    runtime_fixture, monkeypatch, tmp_path, replace_parent,
):
    source, _, payload = runtime_fixture
    installed(monkeypatch, source)
    original = (source / "manifest.json").read_bytes()
    allocate = runtime_staging.tempfile.mkdtemp
    monkeypatch.setattr(runtime_staging.tempfile, "mkdtemp",
                        lambda **kwargs: allocate(dir=tmp_path, **kwargs))
    moved = tmp_path / "moved-owned-parent"
    with pytest.raises(ValueError, match="replaced"):
        with runtime_staging.stage_runtime() as root:
            parent = root.parent
            parent.rename(moved)
            if replace_parent:
                parent.mkdir(mode=0o700)
                marker = parent / "unrelated.txt"
                marker.write_text("must survive cleanup of another directory")
    if replace_parent:
        assert marker.read_text() == "must survive cleanup of another directory"
    assert not (moved / "runtime").exists()
    assert (source / "manifest.json").read_bytes() == original
    assert payload == {name: (source / name).read_bytes() for name in payload}


@pytest.mark.parametrize("consumer_error", [False, True])
def test_allocation_cleanup_removes_all_contents_without_following_links(
    runtime_fixture, monkeypatch, tmp_path, consumer_error,
):
    source, _, payload = runtime_fixture
    installed(monkeypatch, source)
    outside = tmp_path / "outside"
    outside.write_bytes(b"must survive")
    allocate = runtime_staging.tempfile.mkdtemp
    monkeypatch.setattr(runtime_staging.tempfile, "mkdtemp",
                        lambda **kwargs: allocate(dir=tmp_path, **kwargs))

    outcome = (pytest.raises(RuntimeError, match="consumer failure") if consumer_error
               else pytest.raises(ValueError, match="runtime path replaced or mutated"))
    with outcome:
        with runtime_staging.stage_runtime() as root:
            parent = root.parent
            root.rename(parent / "renamed-runtime")
            replacement = parent / "runtime"
            replacement.mkdir(mode=0o700)
            (replacement / "replacement-marker").write_text("disposable")
            (parent / "extra-file").write_text("disposable")
            extra_dir = parent / "extra-dir"
            extra_dir.mkdir(mode=0o700)
            (extra_dir / "nested").write_text("disposable")
            (parent / "outside-link").symlink_to(outside)
            if consumer_error:
                raise RuntimeError("consumer failure")

    assert not parent.exists()
    assert outside.read_bytes() == b"must survive"
    assert payload == {name: (source / name).read_bytes() for name in payload}
