"""Failures in evidence collection must prevent native support admission."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


probe = load("qualification_probe", "tests/envoy-bash-qualification/probe.py")
tool = load("qualification_tool", "tools/bash_builds.py")
runner = load("qualification_runner", "tests/envoy-bash-qualification/run.py")


def test_every_native_case_requires_actual_assertions():
    mapping = json.loads((ROOT / "tests/envoy-bash-qualification/case-map.json").read_text())
    required = tool.case_ids()
    assert len(required) == 258
    proofs = {selector: {"observed": True} for selectors in mapping.values() for selector in selectors}
    assert probe.collect_cases(mapping, required, proofs) == dict.fromkeys(required, True)
    for selector in proofs:
        incomplete = proofs.copy()
        del incomplete[selector]
        with pytest.raises(ValueError, match="missing actual assertion"):
            probe.collect_cases(mapping, required, incomplete)
    for case in required:
        incomplete = mapping.copy()
        del incomplete[case]
        with pytest.raises(ValueError, match="incomplete/extra"):
            probe.collect_cases(incomplete, required, proofs)


@pytest.mark.parametrize("selectors", [[], ["unobserved"], ["observed", "observed"], [1]])
def test_invalid_or_unobserved_selectors_reject(selectors):
    with pytest.raises(ValueError):
        probe.collect_cases({"case": selectors}, ["case"], {"observed": {}})


@pytest.mark.parametrize("output", ["PASS\n", "--- SKIP: TestRequired (0.00s)\nPASS\n"])
def test_empty_or_skipped_native_suite_cannot_supply_proof(tmp_path, monkeypatch, output):
    def run(*args, **kwargs):
        kwargs["stdout"].write(output)

    monkeypatch.setattr(probe.subprocess, "run", run)
    with pytest.raises(ValueError):
        probe.successful_go("launch", "^TestRequired$", tmp_path, {})


def test_injected_failure_restores_original_input_and_mode(tmp_path):
    path = tmp_path / "asset"
    path.write_bytes(b"bound original")
    path.chmod(0o444)
    with pytest.raises(RuntimeError, match="injected"):
        with probe.fault(path, b"fault"):
            assert path.read_bytes() == b"fault"
            raise RuntimeError("injected")
    assert path.read_bytes() == b"bound original"
    assert path.stat().st_mode & 0o777 == 0o444


def test_staged_proof_excludes_table_outputs_but_binds_schema(tmp_path, monkeypatch):
    def build(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_bytes(b"isolated test executable")

    monkeypatch.setattr(runner.subprocess, "run", build)
    runner.stage(tmp_path)
    runtime = tmp_path / "qualification/repo/runtime/envoy"
    assert not (runtime / "bash-builds.json").exists()
    assert not (runtime / "etc/bash-builds.json").exists()
    assert (runtime / "bashbuild/table_generated.go").read_bytes() == (
        ROOT / "runtime/envoy/bashbuild/table_generated.go"
    ).read_bytes()
    assert (tmp_path / "runtime/etc/awsh-bashrc").read_bytes() == (
        ROOT / "runtime/envoy/etc/awsh-bashrc"
    ).read_bytes()


def test_fault_replaces_executing_inode_and_preserves_placement_symlink(tmp_path):
    binary = tmp_path / "python"
    shutil.copyfile(Path(sys.executable).resolve(), binary)
    binary.chmod(0o555)
    placement = tmp_path / "selected"
    placement.symlink_to(binary.name)
    original = binary.read_bytes()
    child = subprocess.Popen([str(placement), "-c", "import time; time.sleep(60)"])
    try:
        with probe.fault(placement, b"#!/bin/sh\nexit 1\n"):
            assert placement.is_symlink()
            assert binary.read_bytes() == b"#!/bin/sh\nexit 1\n"
        assert placement.is_symlink()
        assert binary.read_bytes() == original
        assert binary.stat().st_mode & 0o777 == 0o555
    finally:
        child.kill()
        child.wait(timeout=5)
