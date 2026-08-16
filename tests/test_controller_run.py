from __future__ import annotations

import hashlib
import json
import os
import stat
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from omegaflow.controller_run import (
    ControllerAsset,
    ControllerContext,
    ControllerRunError,
    ControllerRunManifest,
    capture_controller_recording,
    load_controller_manifest,
    run_controller_session,
    write_controller_manifest,
)
from omegaflow.reploy_protocol import Opened, ReployEndpoint


def _manifest(*, assets: tuple[ControllerAsset, ...] = ()) -> ControllerRunManifest:
    return ControllerRunManifest(
        recording_id="demo",
        recording_plan={"id": "demo", "beats": []},
        columns=80,
        rows=24,
        terminal_endpoint_id="omegaflow-terminal",
        telemetry_endpoint_id="omegaflow-telemetry",
        application_endpoint_ids=("web",),
        assets=assets,
    )


def test_controller_manifest_round_trips_and_checks_assets(tmp_path: Path) -> None:
    root = tmp_path / "input"
    assets = root / "assets"
    assets.mkdir(parents=True)
    payload = b"trusted input"
    (assets / "source.txt").write_bytes(payload)
    manifest = _manifest(
        assets=(
            ControllerAsset(
                "source",
                "source.txt",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )
    )
    path = root / "run-manifest.json"
    write_controller_manifest(path, manifest)

    assert load_controller_manifest(path) == manifest


def test_controller_manifest_rejects_path_escape_and_hash_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "input"
    (root / "assets").mkdir(parents=True)
    path = root / "run-manifest.json"
    write_controller_manifest(
        path,
        _manifest(assets=(ControllerAsset("source", "../source", 0, "0" * 64),)),
    )
    with pytest.raises(ControllerRunError, match="normalized relative"):
        load_controller_manifest(path)

    (root / "assets" / "source").write_bytes(b"data")
    write_controller_manifest(
        path,
        _manifest(assets=(ControllerAsset("source", "source", 4, "0" * 64),)),
    )
    with pytest.raises(ControllerRunError, match="hash does not match"):
        load_controller_manifest(path)


def test_controller_manifest_rejects_symlink_asset(tmp_path: Path) -> None:
    root = tmp_path / "input"
    assets = root / "assets"
    assets.mkdir(parents=True)
    payload = b"data"
    target = assets / "target"
    target.write_bytes(payload)
    (assets / "source").symlink_to(target.name)
    path = root / "run-manifest.json"
    write_controller_manifest(
        path,
        _manifest(
            assets=(
                ControllerAsset(
                    "source",
                    "source",
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                ),
            )
        ),
    )

    with pytest.raises(ControllerRunError, match="contained regular file"):
        load_controller_manifest(path)


def test_controller_manifest_rejects_symlinked_assets_root(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    payload = b"outside input"
    (external / "source").write_bytes(payload)
    (root / "assets").symlink_to(external, target_is_directory=True)
    path = root / "run-manifest.json"
    write_controller_manifest(
        path,
        _manifest(
            assets=(
                ControllerAsset(
                    "source",
                    "source",
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                ),
            )
        ),
    )

    with pytest.raises(ControllerRunError, match="assets root"):
        load_controller_manifest(path)


def _browser_manifest(*, url: str = "/demo") -> ControllerRunManifest:
    return ControllerRunManifest(
        recording_id="browser-demo",
        recording_plan={
            "id": "browser-demo",
            "browser": {"endpoint_id": "web"},
            "beats": [
                {
                    "id": "terminal",
                    "actions": [{"id": "prepare", "run": "printf ready"}],
                },
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [{"id": "open", "open_page": {"url": url}}],
                }
            ],
        },
        columns=80,
        rows=24,
        terminal_endpoint_id="omegaflow-terminal",
        telemetry_endpoint_id="omegaflow-telemetry",
        application_endpoint_ids=("web",),
        assets=(),
    )


def _browser_context(tmp_path: Path, *, url: str = "/demo") -> ControllerContext:
    return ControllerContext(
        _browser_manifest(url=url),
        Opened(
            operations=("complete", "terminate"),
            endpoints=(
                ReployEndpoint("omegaflow-terminal", "tcp", "workload", 47001),
                ReployEndpoint("omegaflow-telemetry", "tcp", "workload", 47002),
                ReployEndpoint("web", "http", "workload", 8080),
            ),
            columns=80,
            rows=24,
            output_finalization_timeout_milliseconds=30_000,
        ),
        tmp_path / "output",
    )


def test_controller_manifest_requires_selected_browser_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "run-manifest.json"
    write_controller_manifest(
        path,
        replace(_browser_manifest(), application_endpoint_ids=()),
    )

    with pytest.raises(ControllerRunError, match="browser.endpoint_id"):
        load_controller_manifest(path)


def test_controller_browser_uses_granted_endpoint_and_finalizes_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omegaflow.browser_capture as browser_capture
    import omegaflow.presentation_build as presentation_build

    observed: dict[str, object] = {}
    order: list[str] = []

    class FakeBrowserRunner:
        def __init__(self, browser_config: object, **kwargs: object) -> None:
            observed["browser_config"] = browser_config
            observed["browser_kwargs"] = kwargs

    def capture(*_args: object, **kwargs: object) -> None:
        order.append("capture")
        observed["capture_project_root"] = _args[0]["_project_root"]
        terminal_factory = kwargs["terminal_runner_factory"]
        factory = kwargs["browser_runner_factory"]
        assert callable(terminal_factory)
        assert callable(factory)
        observed["terminal_runner"] = terminal_factory(None)
        factory()

    def compile_bundle(*args: object, **kwargs: object) -> None:
        order.append("compile")
        observed["compile_project_root"] = args[0]["_project_root"]
        observed["audio_artifacts"] = kwargs["audio_artifacts"]

    monkeypatch.setattr(browser_capture, "PersistentBrowserRunner", FakeBrowserRunner)
    monkeypatch.setattr(presentation_build, "capture_recording", capture)
    monkeypatch.setattr(
        presentation_build,
        "prepare_narration_audio",
        lambda *_args, **_kwargs: order.append("narration") or "audio",
    )
    monkeypatch.setattr(
        presentation_build,
        "compile_presentation_bundle",
        compile_bundle,
    )

    capture_controller_recording(_browser_context(tmp_path))

    assert order == ["capture", "narration", "compile"]
    browser_config = observed["browser_config"]
    assert isinstance(browser_config, dict)
    assert browser_config["base_url"] == "http://workload:8080/"
    assert "endpoint_id" not in browser_config
    assert browser_config["auth"] == {
        "storage_state_env": None,
        "storage_state_path": None,
    }
    assert browser_config["timeouts"] == {
        "action_ms": 10_000,
        "readiness_ms": 15_000,
    }
    assert type(observed["terminal_runner"]).__name__ == "EnvoyPersistentTerminalRunner"
    assert observed["browser_kwargs"] == {"headless": True}
    assert observed["capture_project_root"] == "/omegaflow-input"
    assert observed["compile_project_root"] == "/omegaflow-input"
    assert observed["audio_artifacts"] == "audio"


@pytest.mark.parametrize(
    "url",
    (
        "https://substituted.invalid/demo",
        "//substituted.invalid/demo",
        r"\\substituted.invalid\demo",
    ),
)
def test_controller_browser_rejects_navigation_substitution(
    tmp_path: Path, url: str
) -> None:
    with pytest.raises(ControllerRunError, match="must be relative"):
        capture_controller_recording(_browser_context(tmp_path, url=url))


def test_controller_browser_rejects_malformed_granted_host(tmp_path: Path) -> None:
    context = ControllerContext(
        _browser_manifest(),
        replace(
            _browser_context(tmp_path).opened,
            endpoints=(ReployEndpoint("web", "http", "workload/other", 8080),),
        ),
        tmp_path / "output",
    )

    with pytest.raises(ControllerRunError, match="invalid host"):
        capture_controller_recording(context)


def test_controller_rejects_enabled_narration_without_delegation(
    tmp_path: Path,
) -> None:
    manifest = replace(
        _manifest(),
        recording_id="narrated-demo",
        recording_plan={
            "id": "narrated-demo",
            "audio": {"enabled": True},
            "beats": [
                {
                    "id": "intro",
                    "narration": "Introduce the demo.",
                    "actions": [{"run": "true"}],
                }
            ],
        },
        application_endpoint_ids=(),
    )
    context = ControllerContext(
        manifest,
        replace(_browser_context(tmp_path).opened, endpoints=()),
        tmp_path / "output",
    )
    manifest_path = tmp_path / "run-manifest.json"
    write_controller_manifest(manifest_path, manifest)

    with pytest.raises(ControllerRunError, match="does not accept enabled narration"):
        load_controller_manifest(manifest_path)
    with pytest.raises(ControllerRunError, match="does not accept enabled narration"):
        capture_controller_recording(context)


def test_controller_rejects_path_inputs_without_asset_staging(tmp_path: Path) -> None:
    manifest = replace(
        _manifest(),
        recording_id="input-demo",
        recording_plan={
            "id": "input-demo",
            "beats": [
                {
                    "id": "run",
                    "actions": [
                        {
                            "run": "cat settings.txt",
                            "inputs": ["settings.txt"],
                        }
                    ],
                }
            ],
        },
        application_endpoint_ids=(),
    )
    manifest_path = tmp_path / "run-manifest.json"
    write_controller_manifest(manifest_path, manifest)
    context = ControllerContext(
        manifest,
        replace(_browser_context(tmp_path).opened, endpoints=()),
        tmp_path / "output",
    )

    with pytest.raises(ControllerRunError, match="path-based recording inputs"):
        load_controller_manifest(manifest_path)
    with pytest.raises(ControllerRunError, match="path-based recording inputs"):
        capture_controller_recording(context)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("storage_state_env", "BROWSER_STORAGE_STATE"),
        ("storage_state_path", ".private/browser-state.json"),
    ),
)
def test_controller_rejects_browser_auth_without_delegation(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    manifest = _browser_manifest()
    manifest.recording_plan["browser"]["auth"] = {field: value}
    manifest_path = tmp_path / "run-manifest.json"
    write_controller_manifest(manifest_path, manifest)
    context = replace(_browser_context(tmp_path), manifest=manifest)

    with pytest.raises(ControllerRunError, match="browser authentication inputs"):
        load_controller_manifest(manifest_path)
    with pytest.raises(ControllerRunError, match="browser authentication inputs"):
        capture_controller_recording(context)


def _fake_session_client(tmp_path: Path) -> Path:
    path = tmp_path / "reploy-session-client"
    path.write_text(
        textwrap.dedent(
            f"""\
            #!{os.sys.executable}
            import json
            import os
            import pathlib
            import select
            import sys
            import time

            root = pathlib.Path(os.environ["OMEGAFLOW_FAKE_REPLOY"])
            scenario = os.environ.get("OMEGAFLOW_FAKE_SCENARIO", "success")

            def emit(kind, **values):
                print(json.dumps({{"schema": "reploy-controlled-session-client-v1", "type": kind, **values}}, separators=(",", ":")), flush=True)

            def wait_for(name):
                deadline = time.monotonic() + 3
                while not (root / name).exists():
                    if time.monotonic() >= deadline:
                        raise SystemExit(90)
                    time.sleep(0.005)

            if sys.argv[1] == "attach":
                (root / "attach.started").write_text("yes")
                command = sys.stdin.readline()
                (root / "bootstrap.txt").write_text(command)
                wait_for("capture.done")
                raise SystemExit(0)

            if sys.argv[1] != "client":
                raise SystemExit(2)
            if scenario == "startup-failure":
                emit("client-error", code="startup_failed", message="injected startup failure")
                raise SystemExit(7)
            emit("broker-ready", terminal_socket="/private/reploy.sock")
            wait_for("attach.started")
            emit("opened", operations=["input", "resize", "terminate", "complete"], endpoints=[
                {{"id": "omegaflow-terminal", "scheme": "tcp", "host": "workload", "port": 47001}},
                {{"id": "omegaflow-telemetry", "scheme": "tcp", "host": "workload", "port": 47002}},
                {{"id": "web", "scheme": "http", "host": "workload", "port": 8080}},
            ], columns=80, rows=24, output_finalization_timeout_milliseconds=30000)
            emit("ready")
            terminated_by_controller = False
            deadline = time.monotonic() + 3
            while not (root / "capture.done").exists():
                readable, _, _ = select.select([sys.stdin], [], [], 0.01)
                if readable:
                    request = json.loads(sys.stdin.readline())
                    assert request["type"] == "terminate"
                    terminated_by_controller = True
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit(91)
            emit("workload-exit", status={{"kind": "exited", "code": 0}})
            emit("terminating", cause="controller-request" if terminated_by_controller else "workload-exit")
            if scenario == "output-failure":
                emit("workload-outputs-finalized", status="failed", reason="injected drain failure")
                output_kind = "failed"
            else:
                emit("workload-outputs-finalized", status="drained")
                output_kind = "drained"
            request = json.loads(sys.stdin.readline())
            assert request["type"] == "complete"
            emit("terminated", result={{
                "cause": "workload-exit",
                "workload_status": {{"kind": "exited", "code": 0}},
                "workload_output_finalization_status": {{"kind": output_kind}},
                "runtime_observation_status": {{"kind": "maintained"}},
                "controller_finalization_status": {{"kind": "completed"}},
                "cleanup_status": {{"kind": "succeeded"}},
                "recovery_action": "none",
            }})
            if scenario == "ack-failure":
                raise SystemExit(8)
            request = json.loads(sys.stdin.readline())
            assert request["type"] == "acknowledge-terminated"
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_controller_drives_public_lifecycle_and_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    monkeypatch.setenv("OMEGAFLOW_FAKE_REPLOY", str(fake_root))
    client = _fake_session_client(tmp_path)
    output = tmp_path / "output"
    observed = []

    def capture(context: object) -> None:
        observed.append(context)
        (fake_root / "capture.done").write_text("done", encoding="utf-8")

    result = run_controller_session(
        _manifest(),
        output,
        capture,
        session_client=str(client),
        startup_timeout=2,
        lifecycle_timeout=2,
    )

    assert result.cleanup_status.kind == "succeeded"
    assert len(observed) == 1
    bootstrap = (fake_root / "bootstrap.txt").read_text(encoding="utf-8")
    assert bootstrap == (
        "exec /omegaflow-runtime/bin/envoy "
        "--terminal-listen 0.0.0.0:47001 "
        "--telemetry-listen 0.0.0.0:47002 "
        "--columns 80 --rows 24\n"
    )
    assert (output / "reploy-terminated.json").is_file()
    event_types = [
        json.loads(line)["type"]
        for line in (output / "reploy-client.events.jsonl").read_text().splitlines()
    ]
    assert event_types == [
        "broker-ready",
        "opened",
        "ready",
        "workload-exit",
        "terminating",
        "workload-outputs-finalized",
        "terminated",
    ]


def test_controller_requests_termination_after_capture_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    monkeypatch.setenv("OMEGAFLOW_FAKE_REPLOY", str(fake_root))
    client = _fake_session_client(tmp_path)

    with pytest.raises(ControllerRunError, match="capture failed"):
        run_controller_session(
            _manifest(),
            tmp_path / "output",
            lambda _context: (_ for _ in ()).throw(RuntimeError("injected capture failure")),
            session_client=str(client),
            startup_timeout=2,
            lifecycle_timeout=2,
        )

    assert (tmp_path / "output" / "reploy-terminated.json").is_file()


def test_controller_keeps_capture_failure_primary_when_acknowledgement_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    monkeypatch.setenv("OMEGAFLOW_FAKE_REPLOY", str(fake_root))
    monkeypatch.setenv("OMEGAFLOW_FAKE_SCENARIO", "ack-failure")
    client = _fake_session_client(tmp_path)

    with pytest.raises(ControllerRunError, match="capture failed: primary") as exc_info:
        run_controller_session(
            _manifest(),
            tmp_path / "output",
            lambda _context: (_ for _ in ()).throw(RuntimeError("primary")),
            session_client=str(client),
            startup_timeout=2,
            lifecycle_timeout=2,
        )

    assert "session client also failed" in str(exc_info.value)


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("startup-failure", "first Reploy event"),
        ("output-failure", "output finalization failed"),
        ("ack-failure", "exited with status 8"),
    ],
)
def test_controller_retains_public_lifecycle_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    message: str,
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    monkeypatch.setenv("OMEGAFLOW_FAKE_REPLOY", str(fake_root))
    monkeypatch.setenv("OMEGAFLOW_FAKE_SCENARIO", scenario)
    client = _fake_session_client(tmp_path)

    def capture(_context: object) -> None:
        (fake_root / "capture.done").write_text("done", encoding="utf-8")

    with pytest.raises(ControllerRunError, match=message):
        run_controller_session(
            _manifest(),
            tmp_path / "output",
            capture,
            session_client=str(client),
            startup_timeout=2,
            lifecycle_timeout=2,
        )
