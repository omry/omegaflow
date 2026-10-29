"""Controls for installed-stage evidence; real native acceptance is separate."""
from contextlib import contextmanager
import asyncio
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryFile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def coordinator():
    return load("tests/envoy-session-startup/installed.py", "installed_stage_proof")


@pytest.fixture
def client_runner():
    return load("tests/envoy-session-client/run.py", "installed_client_runner")


@pytest.mark.parametrize("mode", [0o444, 0o555])
def test_corruption_changes_only_disposable_copy(tmp_path, coordinator, client_runner, mode):
    source = tmp_path / "source"
    source.mkdir()
    (source / "asset").write_bytes(b"accepted")
    (source / "asset").chmod(mode)
    (source / "retained").write_bytes(b"untouched")
    before = coordinator.snapshot(source)
    copy = tmp_path / "copy"
    client_runner.corrupt_copy(source, copy, "asset")
    assert coordinator.snapshot(source) == before
    assert (copy / "asset").read_bytes() != b"accepted"
    assert (copy / "asset").stat().st_mode & 0o777 == mode
    assert (copy / "retained").read_bytes() == b"untouched"


def test_stage_identity_detects_changes_and_rejects_links(tmp_path, coordinator):
    root = tmp_path / "stage"
    root.mkdir()
    (root / "asset").write_bytes(b"accepted")
    before = coordinator.snapshot(root)
    (root / "asset").chmod(0o444)
    assert coordinator.snapshot(root) != before
    (root / "link").symlink_to(root / "asset")
    with pytest.raises(ValueError, match="nonregular"):
        coordinator.snapshot(root)
    assert (root / "asset").read_bytes() == b"accepted"


def test_checkout_modules_cannot_claim_installed_acceptance(coordinator):
    with pytest.raises(ValueError, match="exact installed module"):
        coordinator.installed_modules()


def test_asset_controls_require_manifested_artifact(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tests/envoy-session-client/run.py"),
                             "--qualification", str(tmp_path), "--output", str(tmp_path / "out"),
                             "--asset-controls"], capture_output=True, text=True)
    assert result.returncode == 2 and "--asset-controls requires --runtime" in result.stderr
    assert not (tmp_path / "out").exists()


def test_failed_native_command_retains_identity_and_disposes_stage(tmp_path, monkeypatch, coordinator):
    # A unit boundary peer exercises the coordinator's failure path only.
    # It supplies no installed-source or native acceptance evidence.
    prefix = tmp_path / "prefix"
    source = prefix / "omegaflow/runtime/linux-amd64"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"files": [
        {"path": f"asset-{i}"} for i in range(18)]}))
    for i in range(18):
        (source / f"asset-{i}").write_bytes(b"accepted")
    parent = tmp_path / "owned"

    @contextmanager
    def stage():
        parent.mkdir(mode=0o700)
        shutil.copytree(source, parent / "runtime")
        try:
            yield parent / "runtime"
        finally:
            shutil.rmtree(parent)

    monkeypatch.setattr(coordinator, "installed_modules", lambda: {})
    monkeypatch.setattr(coordinator.sys, "prefix", str(prefix))
    monkeypatch.setattr(coordinator, "files", lambda _: prefix / "omegaflow")
    monkeypatch.setattr(coordinator.runtime_staging, "stage_runtime", stage)
    monkeypatch.setattr(coordinator.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=7))
    output = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["installed.py", "--python", sys.executable,
        "--acquisition", str(tmp_path / "acquisition.json"), "--qualification", str(tmp_path),
        "--output", str(output)])
    with pytest.raises(RuntimeError, match="native stage acceptance failed"):
        coordinator.main()
    report = json.loads((output / "installed-stage.json").read_text())
    assert report["status"] == "failed" and report["commands"][0]["returncode"] == 7
    assert report["stage_before"] == report["stage_after"]
    assert report["source_unchanged"] and report["allocation_removed"]
    assert not parent.exists()


@pytest.mark.parametrize("payload", [b"", b"UNEXPECTED PRE-READY TERMINAL OUTPUT\n"])
def test_refusal_proxy_counts_output_hidden_by_startup_buffer(payload):
    # A unit peer exposes the transport's startup buffer. Native acceptance
    # separately exercises the production validator and its actual diagnostic.
    native = load("tests/envoy-session-client/native.py", "installed_native_refusal")
    peers = load("tests/test_envoy_transport.py", "installed_transport_peers")

    async def run():
        async with peers.Peer() as peer:
            proxy = native.ChannelDelay()
            proxy.hold_terminal_eof = True
            try:
                addresses = await proxy.start(peer.addresses["terminal"], peer.addresses["telemetry"])
                with TemporaryFile() as raw:
                    owner = await native.EnvoyTransport.connect(
                        *addresses, session_id=peers.SESSION_ID,
                        bootstrap_written_at=asyncio.get_running_loop().time(),
                        raw_log=raw, telemetry_log=io.BytesIO())
                    waiter = asyncio.create_task(owner.wait_ready())
                    try:
                        await peer.write("terminal", payload)
                        await peer.eof("terminal")
                        await asyncio.wait_for(proxy.terminal_eof_seen.wait(), 2)
                        if payload:
                            await peers.until(lambda: len(owner._early) == len(payload))
                        await peer.write("telemetry", peers.diagnostic(severity="fatal"))
                        await peer.eof("telemetry")
                        with pytest.raises(native.EnvoyTransportError):
                            await asyncio.wait_for(waiter, 2)
                        assert owner.raw_offset == 0 and peers.raw_bytes(raw) == b""
                        assert proxy.terminal_received_bytes == len(payload)
                    finally:
                        await owner.close()
                        await asyncio.gather(waiter, return_exceptions=True)
            finally:
                await proxy.close()

    asyncio.run(asyncio.wait_for(run(), 5))
