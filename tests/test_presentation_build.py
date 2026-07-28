from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import sys
import wave
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import omegaflow.presentation_build as presentation_build
import omegaflow.studio as studio
from omegaflow import __version__
from omegaflow import audio as audio_module
from omegaflow.capture import CaptureContext
from omegaflow.presentation_build import (
    _auth_state_sha256,
    _capture_environment,
    _source_words_with_timing,
    _visualization_highlights,
    capture_recording,
    compile_presentation_bundle,
    PresentationAudioArtifacts,
    prepare_narration_audio,
    public_bundle_dir,
    publish_bundle,
    validate_run_bundle,
    write_capture_fingerprint,
)
from omegaflow.presentation_compiler import (
    CompiledBeatTiming,
    CompiledRecordingTiming,
)
from omegaflow.recording_plan import (
    NarrationTakeMemberPlan,
    NarrationTakePlan,
    NarrationTakeWaitPlan,
    normalize_recording_plan,
)


def png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content))
            + kind
            + content
            + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes((*color, 255)) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )


def state(path: Path, *, color: tuple[int, int, int]) -> dict[str, object]:
    content = png(1440, 900, color)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": path.relative_to(path.parents[2]).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "media_type": "image/png",
        "width": 1440,
        "height": 900,
        "bytes": len(content),
    }


def test_copy_capture_logs_aggregates_nonempty_runner_output(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    client = run_dir / "capture/runners/client"
    server = run_dir / "capture/runners/server"
    client.mkdir(parents=True)
    server.mkdir(parents=True)
    (client / "terminal.stdout.log").write_text(
        "PING from client\n", encoding="utf-8"
    )
    (server / "terminal.stdout.log").write_text(
        "PONG from server\n", encoding="utf-8"
    )
    (client / "terminal.stderr.log").write_text("", encoding="utf-8")
    (server / "terminal.stderr.log").write_text("", encoding="utf-8")

    stdout, stderr, _ = presentation_build._copy_capture_logs(run_dir)

    assert stdout.read_text(encoding="utf-8") == (
        "=== pane client ===\n"
        "PING from client\n"
        "\n"
        "=== pane server ===\n"
        "PONG from server\n"
    )
    assert stderr.read_text(encoding="utf-8") == ""


def test_capture_failure_preserves_each_terminal_runner_cast(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    for pane_id in ("client", "server"):
        runner_dir = run_dir / "capture/runners" / pane_id
        runner_dir.mkdir(parents=True)
        (runner_dir / "terminal.cast").write_text(
            f"{pane_id} cast\n", encoding="utf-8"
        )
        (runner_dir / "terminal.timeline.jsonl").write_text(
            f"{pane_id} timeline\n", encoding="utf-8"
        )

    presentation_build._preserve_capture_diagnostics(
        {"id": "two-terminals"},
        run_dir,
        RuntimeError("capture failed"),
        working_directory=tmp_path,
    )

    assert (run_dir / "failed-client.cast").read_text(
        encoding="utf-8"
    ) == "client cast\n"
    assert (run_dir / "failed-server.cast").read_text(
        encoding="utf-8"
    ) == "server cast\n"
    assert (run_dir / "failed.cast").read_text(
        encoding="utf-8"
    ) == "client cast\n"


def test_materialized_wait_is_silence_between_complete_audio_fragments(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    sample_rate = 24_000
    source_samples = [
        round(
            8_000
            * math.sin(
                2 * math.pi * (440 if index < sample_rate // 2 else 880)
                * index
                / sample_rate
            )
        )
        for index in range(sample_rate)
    ]
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(struct.pack(f"<{len(source_samples)}h", *source_samples))

    output = tmp_path / "with-wait.wav"
    presentation_build._materialize_waited_audio(  # pyright: ignore[reportPrivateUsage]
        source,
        output,
        source_start_ms=0,
        playback_start_ms=0,
        intervals=(
            presentation_build.PresentationAudioIntervalV1(
                presentation_start_ms=0,
                presentation_end_ms=500,
                source_start_ms=0,
                source_end_ms=500,
            ),
            presentation_build.PresentationAudioIntervalV1(
                presentation_start_ms=1000,
                presentation_end_ms=1500,
                source_start_ms=500,
                source_end_ms=1000,
            ),
        ),
        ffmpeg=ffmpeg,
    )

    with wave.open(str(output), "rb") as stream:
        assert stream.getframerate() == sample_rate
        samples = struct.unpack(
            f"<{stream.getnframes()}h", stream.readframes(stream.getnframes())
        )
    assert len(samples) == 3 * sample_rate // 2
    assert max(abs(value) for value in samples[sample_rate // 2 : sample_rate]) == 0
    assert samples[sample_rate:] == tuple(source_samples[sample_rate // 2 :])


def write_mixed_capture(run_dir: Path) -> None:
    capture = run_dir / "capture"
    beats = capture / "terminal-beats"
    beats.mkdir(parents=True)
    for beat_id, output in (("prepare", "ready\n"), ("verify", "done\n")):
        (beats / f"{beat_id}.cast").write_text(
            json.dumps({"version": 3, "width": 80, "height": 20})
            + "\n"
            + json.dumps([0.1, "o", output])
            + "\n",
            encoding="utf-8",
        )
        (beats / f"{beat_id}.actions.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "beat_id": beat_id,
                    "actions": [
                        {
                            "id": "__step_0",
                            "start_ms": 0,
                            "end_ms": 100,
                            "duration_ms": 100,
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
    initial = state(capture / "states" / "initial.png", color=(245, 245, 245))
    opened = state(capture / "states" / "opened.png", color=(20, 80, 160))
    records = [
        {
            "capture_version": 1,
            "seq": 1,
            "type": "run_start",
            "profile": {
                "viewport_width": 1440,
                "viewport_height": 900,
                "device_scale_factor": 1.0,
            },
            "initial_state": initial,
        },
        {"capture_version": 1, "seq": 2, "type": "beat_start", "beat_id": "web"},
        {
            "capture_version": 1,
            "seq": 3,
            "type": "action",
            "beat_id": "web",
            "action_id": "open",
            "kind": "open_page",
            "completion": {"kind": "navigation"},
            "visual": {"kind": "state", "state": opened},
        },
        {"capture_version": 1, "seq": 4, "type": "beat_end", "beat_id": "web"},
        {"capture_version": 1, "seq": 5, "type": "run_end", "status": "completed"},
    ]
    (capture / "browser.capture.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_capture_recording_propagates_headed_override_to_both_runners(
    tmp_path: Path, monkeypatch
) -> None:
    spec = {
        "id": "headed-mixed",
        "_project_root": str(tmp_path),
        "environment": {"working_directory": str(tmp_path)},
        "capture": {"headless": True, "timeout": 50},
        "style": {
            "typing": True,
            "typing_min_delay": 0.02,
            "typing_max_delay": 0.06,
            "typing_space_delay": 0.03,
            "typing_punctuation_delay": 0.05,
            "typing_newline_delay": 0.12,
            "typing_seed": 5,
        },
        "timing": {"post_enter_pause": 0.25, "post_command_pause": 0.55},
        "browser": {},
        "beats": [
            {"id": "terminal", "actions": [{"run": "printf terminal"}]},
            {
                "id": "browser",
                "medium": "browser",
                "actions": [{"id": "open", "open_page": {"url": "about:blank"}}],
            },
        ],
    }
    plan = normalize_recording_plan(spec)
    observed: dict[str, object] = {}

    class FakeTerminalRunner:
        def __init__(self, **kwargs) -> None:
            observed["terminal"] = kwargs

    class FakeBrowserRunner:
        def __init__(self, browser, **kwargs) -> None:
            observed["browser_plan"] = browser
            observed["browser"] = kwargs

    class FakeCoordinator:
        def __init__(self, *, terminal_runner_factory, browser_runner_factory) -> None:
            self.terminal_runner_factory = terminal_runner_factory
            self.browser_runner_factory = browser_runner_factory

        def capture(self, *_args, **_kwargs):
            self.terminal_runner_factory()
            assert self.browser_runner_factory is not None
            self.browser_runner_factory()
            return object()

    monkeypatch.setattr(presentation_build, "PersistentTerminalRunner", FakeTerminalRunner)
    monkeypatch.setattr(presentation_build, "PersistentBrowserRunner", FakeBrowserRunner)
    monkeypatch.setattr(presentation_build, "CaptureCoordinator", FakeCoordinator)

    result = capture_recording(spec, plan, tmp_path / "run", headed=True)

    assert result is not None
    assert observed["terminal"]["headless"] is False
    assert observed["terminal"]["color"] is True
    assert observed["terminal"]["typing"] is True
    assert observed["terminal"]["typing_min_delay"] == 0.02
    assert observed["terminal"]["typing_max_delay"] == 0.06
    assert observed["terminal"]["typing_space_delay"] == 0.03
    assert observed["terminal"]["typing_punctuation_delay"] == 0.05
    assert observed["terminal"]["typing_newline_delay"] == 0.12
    assert observed["terminal"]["typing_seed"] == 5
    assert observed["terminal"]["post_enter_pause"] == 0.25
    assert observed["terminal"]["post_command_pause"] == 0.55
    assert observed["terminal"]["timeout_seconds"] == 50
    assert observed["browser"]["headless"] is False
    assert observed["browser_plan"] == plan.browser


def test_capture_environment_applies_color_and_removes_no_color(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    spec = {
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": True},
    }

    working_directory, environment = _capture_environment(spec)
    context = CaptureContext.create(
        tmp_path / "run",
        workspace=tmp_path,
        working_directory=working_directory,
        environment=environment,
    )

    assert context.environment["CLICOLOR_FORCE"] == "1"
    assert context.environment["FORCE_COLOR"] == "1"
    assert context.environment["PY_COLORS"] == "1"
    assert context.environment["TERM"] == "xterm-256color"
    assert "NO_COLOR" not in context.environment


def test_capture_environment_constructs_deterministic_command_environment(
    tmp_path: Path, monkeypatch
) -> None:
    configured_bin = tmp_path / "tools"
    configured_bin.mkdir()
    monkeypatch.setenv("PATH", "/host-only/bin")
    monkeypatch.setenv("HOST_ONLY_APPLICATION_VALUE", "must-not-leak")

    working_directory, environment = _capture_environment(
        {
            "environment": {
                "working_directory": str(tmp_path),
                "path_prepend": [str(configured_bin)],
                "variables": {
                    "EXPLICIT_APPLICATION_VALUE": "visible",
                    "OMEGAFLOW_VERSION": "forged",
                },
            }
        }
    )
    context = CaptureContext.create(
        tmp_path / "run",
        workspace=tmp_path,
        working_directory=working_directory,
        environment=environment,
    )

    assert context.environment["EXPLICIT_APPLICATION_VALUE"] == "visible"
    assert context.environment["OMEGAFLOW_VERSION"] == __version__
    assert context.environment["PATH"].split(os.pathsep) == [
        str(configured_bin.resolve()),
        str(Path(sys.executable).parent),
        *os.defpath.split(os.pathsep),
    ]
    assert "HOST_ONLY_APPLICATION_VALUE" not in context.environment
    assert "/host-only/bin" not in context.environment["PATH"]


def test_browser_auth_fingerprint_uses_explicit_recording_environment(
    tmp_path: Path, monkeypatch
) -> None:
    configured = tmp_path / "configured-auth.json"
    configured.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    host = tmp_path / "host-auth.json"
    host.write_text('{"host": "must-not-be-used"}', encoding="utf-8")
    monkeypatch.setenv("BROWSER_AUTH_STATE", str(host))

    digest = _auth_state_sha256(
        {
            "environment": {
                "working_directory": str(tmp_path),
                "variables": {"BROWSER_AUTH_STATE": configured.name},
            },
            "browser": {"auth": {"storage_state_env": "BROWSER_AUTH_STATE"}},
        }
    )

    assert digest == hashlib.sha256(configured.read_bytes()).hexdigest()


def test_omegaflow_version_participates_in_capture_freshness(monkeypatch) -> None:
    spec = {
        "id": "versioned-environment",
        "beats": [
            {
                "id": "probe",
                "narration": "Probe the environment.",
                "actions": [{"run": "true"}],
            }
        ],
    }
    plan = normalize_recording_plan(spec)

    monkeypatch.setattr(presentation_build, "__version__", "1.0")
    first = presentation_build.artifact_fingerprints(spec, plan)
    monkeypatch.setattr(presentation_build, "__version__", "2.0")
    second = presentation_build.artifact_fingerprints(spec, plan)

    assert first.capture_fingerprint != second.capture_fingerprint


def test_capture_with_delegated_environment_is_never_reused(
    tmp_path: Path, monkeypatch
) -> None:
    spec = {
        "id": "delegated-environment",
        "beats": [
            {
                "id": "build",
                "actions": [
                    {
                        "commands": [
                            {
                                "run": "true",
                                "with_env": ["OPENAI_OMEGAFLOW_API_KEY"],
                            }
                        ]
                    }
                ],
            }
        ],
    }
    plan = normalize_recording_plan(spec)
    monkeypatch.setattr(
        presentation_build,
        "read_fingerprint",
        lambda _run_dir: {"version": 1},
    )
    monkeypatch.setattr(
        presentation_build,
        "capture_artifacts_exist",
        lambda _plan, _run_dir: True,
    )

    assert not presentation_build.capture_is_fresh(spec, plan, tmp_path)


def test_capture_environment_disables_color(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("PY_COLORS", "1")
    working_directory, environment = _capture_environment(
        {
            "environment": {"working_directory": str(tmp_path)},
            "style": {"color": False},
        }
    )
    context = CaptureContext.create(
        tmp_path / "run",
        workspace=tmp_path,
        working_directory=working_directory,
        environment=environment,
    )

    assert context.environment["NO_COLOR"] == "1"
    assert "CLICOLOR_FORCE" not in context.environment
    assert "FORCE_COLOR" not in context.environment
    assert "PY_COLORS" not in context.environment


def test_capture_environment_rejects_private_service_names() -> None:
    with pytest.raises(
        presentation_build.PresentationBuildError, match="use with_env"
    ):
        _capture_environment(
            {
                "environment": {
                    "variables": {
                        "OPENAI_OMEGAFLOW_API_KEY": "must-not-be-recorded"
                    }
                }
            }
        )


def test_mixed_capture_compiles_validates_and_publishes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    output_dir = tmp_path / "public"
    spec = {
        "id": "mixed",
        "title": "Mixed demo",
        "outputs": {"asset_dir": str(output_dir)},
        "browser": {},
        "presentation": {
            "pane_chrome": {"style": "none"},
            "browser": {
                "window": {"mode": "framed", "title": "Default"},
                "chrome": {"mode": "full"},
            }
        },
        "audio": {"enabled": False},
        "beats": [
            {
                "id": "prepare",
                "actions": [{"run": "printf ready"}],
                "guide": {
                    "commands": ["python -m pip install omegaflow"],
                    "summary": "Install the package before continuing.",
                    "success_hint": "Install OmegaFlow.",
                },
            },
            {
                "id": "web",
                "medium": "browser",
                "window": {"mode": "none"},
                "chrome": {"mode": "hidden"},
                "actions": [
                    {
                        "id": "open",
                        "open_page": {
                            "url": "about:blank",
                            "display_url": "https://demo.example/",
                        },
                    }
                ],
            },
            {"id": "verify", "actions": [{"run": "printf done"}]},
        ],
    }
    plan = normalize_recording_plan(spec)
    write_mixed_capture(run_dir)
    write_capture_fingerprint(spec, plan, run_dir)

    result = compile_presentation_bundle(spec, plan, run_dir)

    manifest = validate_run_bundle(spec, run_dir)
    pane_renderers = {
        pane["id"]: pane["renderer"] for pane in manifest["panes"]
    }
    assert [
        pane_renderers[beat["pane_tracks"][0]["pane_id"]]
        for beat in manifest["beats"]
    ] == [
        "terminal",
        "browser",
        "terminal",
    ]
    assert result.manifest == run_dir / "presentation/recording.presentation.json"
    assert manifest["manifest_version"] == 1
    assert manifest["signatures"] == "signatures.json"
    assert manifest["presentation"]["pane_chrome"] == {"style": "none"}
    signatures = json.loads(
        (result.bundle_dir / "signatures.json").read_text(encoding="utf-8")
    )
    assert signatures["version"] == 1
    assert set(signatures["files"]) == {
        path.relative_to(result.bundle_dir).as_posix()
        for path in result.bundle_dir.rglob("*")
        if path.is_file() and path.name != "signatures.json"
    }
    media_paths = sorted(
        asset["path"] for asset in manifest["assets"].values()
    )
    assert media_paths == [
        "media/browser-state-001.webp",
        "media/browser-state-002.webp",
    ]
    assert all(set(asset) == {"media_type", "path"} for asset in manifest["assets"].values())
    assert manifest["presentation"]["browser"] == {
        "window": {
            "mode": "framed",
            "theme": "kde-breeze",
            "title": "Default",
            "opening_transition": "cut",
        },
        "chrome": {"mode": "full"},
    }
    assert manifest["beats"][1]["pane_tracks"][0]["beats"][0]["browser"] == {
        "window": {
            "mode": "none",
            "theme": "kde-breeze",
            "title": "Default",
            "opening_transition": "cut",
        },
        "chrome": {"mode": "hidden"},
    }
    assert manifest["beats"][0]["guide"] == {
        "commands": ["python -m pip install omegaflow"],
        "summary": "Install the package before continuing.",
        "success_hint": "Install OmegaFlow.",
    }
    assert not any(
        "capture" in path.relative_to(result.bundle_dir).parts
        for path in result.bundle_dir.rglob("*")
    )

    destination = publish_bundle(spec, run_dir)

    assert destination == public_bundle_dir(spec)
    assert (destination / "recording.presentation.json").is_file()
    assert list((destination / "media").glob("*.webp"))


def test_visualization_and_terminal_authoring_compiles_end_to_end(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    terminal_dir = run_dir / "capture" / "terminal-beats"
    terminal_dir.mkdir(parents=True)
    capture_id = "compose--terminal--status"
    (terminal_dir / f"{capture_id}.cast").write_text(
        json.dumps({"version": 3, "width": 80, "height": 20})
        + "\n"
        + json.dumps([0.1, "o", "Renderer: ready\n"])
        + "\n",
        encoding="utf-8",
    )
    (terminal_dir / f"{capture_id}.actions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "beat_id": capture_id,
                "actions": [
                    {
                        "id": "run-status",
                        "start_ms": 0,
                        "end_ms": 100,
                        "duration_ms": 100,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = {
        "id": "visualization-terminal",
        "audio": {"enabled": False},
        "panes": [
            {"id": "definition", "kind": "visualization"},
            {"id": "terminal", "kind": "terminal"},
        ],
        "beats": [
            {
                "id": "compose",
                "heading": "Explain the definition",
                "layout": {"areas": [["definition"], ["terminal"]]},
                "panes": {
                    "definition": [
                        {
                            "id": "source",
                            "actions": [
                                {
                                    "id": "show-source",
                                    "show": {
                                        "language": "yaml",
                                        "text": (
                                            "effects:\n"
                                            "- highlight:\n"
                                            '    regex: "Renderer: .*"\n'
                                        ),
                                    },
                                }
                            ],
                        }
                    ],
                    "terminal": [
                        {
                            "id": "status",
                            "actions": [
                                {
                                    "id": "run-status",
                                    "run": "printf 'Renderer: ready\\n'",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    }
    plan = normalize_recording_plan(spec)

    result = compile_presentation_bundle(spec, plan, run_dir)
    manifest = validate_run_bundle(spec, run_dir)

    assert manifest["panes"] == [
        {
            "id": "definition",
            "renderer": "visualization",
            "title": {
                "visible": True,
                "text": None,
                "alignment_x": "right",
                "alignment_y": "top",
                "position_x": "0.25rem",
                "position_y": "0.25rem",
            },
        },
        {
            "id": "terminal",
            "renderer": "terminal",
            "title": {
                "visible": True,
                "text": None,
                "alignment_x": "right",
                "alignment_y": "top",
                "position_x": "0.25rem",
                "position_y": "0.25rem",
            },
        },
    ]
    assert manifest["beats"][0]["layout"] == {
        "areas": [["definition"], ["terminal"]]
    }
    tracks = manifest["beats"][0]["pane_tracks"]
    assert [track["pane_id"] for track in tracks] == [
        "definition",
        "terminal",
    ]
    visualization_path = (
        result.bundle_dir / tracks[0]["beats"][0]["payload"]
    )
    visualization = json.loads(
        visualization_path.read_text(encoding="utf-8")
    )
    assert visualization["language"] == "yaml"
    assert visualization["text"].startswith("effects:\n")
    assert any(token["kind"] == "key" for token in visualization["tokens"])
    assert visualization["highlights"] == []
    assert (
        result.bundle_dir / tracks[1]["beats"][0]["payload"]
    ).read_text(encoding="utf-8").endswith(
        json.dumps([0.1, "o", "Renderer: ready\n"], separators=(",", ":"))
        + "\n"
    )


def test_terminal_and_browser_panes_compile_browser_presentation_overrides(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    capture_dir = run_dir / "capture"
    browser_capture_dir = capture_dir / "runners" / "browser"
    terminal_dir = capture_dir / "terminal-beats"
    terminal_dir.mkdir(parents=True)
    terminal_capture_id = "compose--terminal--session"
    (terminal_dir / f"{terminal_capture_id}.cast").write_text(
        json.dumps({"version": 3, "width": 80, "height": 20})
        + "\n"
        + json.dumps([0.1, "o", "Terminal ready\n"])
        + "\n",
        encoding="utf-8",
    )
    (terminal_dir / f"{terminal_capture_id}.actions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "beat_id": terminal_capture_id,
                "actions": [
                    {
                        "id": "run-app",
                        "start_ms": 0,
                        "end_ms": 100,
                        "duration_ms": 100,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    initial_path = browser_capture_dir / "states" / "initial.png"
    initial = state(
        initial_path,
        color=(245, 245, 245),
    )
    initial["path"] = initial_path.relative_to(run_dir).as_posix()
    opened_path = browser_capture_dir / "states" / "opened.png"
    opened = state(
        opened_path,
        color=(20, 80, 160),
    )
    opened["path"] = opened_path.relative_to(run_dir).as_posix()
    browser_capture_id = "compose--browser--interaction"
    records = [
        {
            "capture_version": 1,
            "seq": 1,
            "type": "run_start",
            "profile": {
                "viewport_width": 1440,
                "viewport_height": 900,
                "device_scale_factor": 1.0,
            },
            "initial_state": initial,
        },
        {
            "capture_version": 1,
            "seq": 2,
            "type": "beat_start",
            "beat_id": browser_capture_id,
        },
        {
            "capture_version": 1,
            "seq": 3,
            "type": "action",
            "beat_id": browser_capture_id,
            "action_id": "open-app",
            "kind": "open_page",
            "completion": {"kind": "navigation"},
            "visual": {"kind": "state", "state": opened},
        },
        {
            "capture_version": 1,
            "seq": 4,
            "type": "beat_end",
            "beat_id": browser_capture_id,
        },
        {
            "capture_version": 1,
            "seq": 5,
            "type": "run_end",
            "status": "completed",
        },
    ]
    (browser_capture_dir / "browser.capture.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    spec = {
        "id": "terminal-browser",
        "browser": {},
        "presentation": {
            "browser": {
                "window": {
                    "mode": "framed",
                    "title": "Default",
                    "opening_transition": "window-open",
                },
                "chrome": {"mode": "full"},
            }
        },
        "audio": {"enabled": False},
        "panes": [
            {"id": "terminal", "kind": "terminal"},
            {"id": "browser", "kind": "browser"},
        ],
        "beats": [
            {
                "id": "compose",
                "layout": {"areas": [["terminal", "browser"]]},
                "panes": {
                    "terminal": [
                        {
                            "id": "session",
                            "actions": [
                                {
                                    "id": "run-app",
                                    "run": "printf 'Terminal ready\\n'",
                                }
                            ],
                        }
                    ],
                    "browser": [
                        {
                            "id": "interaction",
                            "window": {"mode": "none"},
                            "chrome": {"mode": "hidden"},
                            "actions": [
                                {
                                    "id": "open-app",
                                    "open_page": {"url": "about:blank"},
                                    "hold_after_ms": 100,
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    }
    plan = normalize_recording_plan(spec)

    compile_presentation_bundle(spec, plan, run_dir)
    manifest = validate_run_bundle(spec, run_dir)

    browser_beat = manifest["beats"][0]["pane_tracks"][1]["beats"][0]
    assert browser_beat["browser"] == {
        "window": {
            "mode": "none",
            "theme": "kde-breeze",
            "title": "Default",
            "opening_transition": "window-open",
        },
        "chrome": {"mode": "hidden"},
    }


def test_two_browser_panes_compile_from_isolated_capture_logs(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    spec = {
        "id": "two-browsers",
        "browser": {},
        "audio": {"enabled": False},
        "panes": [
            {"id": "left", "kind": "browser"},
            {"id": "right", "kind": "browser"},
        ],
        "beats": [
            {
                "id": "compare",
                "layout": {"areas": [["left", "right"]]},
                "panes": {
                    "left": [
                        {
                            "id": "first",
                            "actions": [
                                {
                                    "id": "open-left",
                                    "open_page": {"url": "about:blank"},
                                    "hold_after_ms": 100,
                                }
                            ],
                        }
                    ],
                    "right": [
                        {
                            "id": "second",
                            "actions": [
                                {
                                    "id": "open-right",
                                    "open_page": {"url": "about:blank"},
                                    "hold_after_ms": 100,
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    }
    plan = normalize_recording_plan(spec)

    for index, (pane_id, pane_beat_id, action_id) in enumerate(
        (
            ("left", "first", "open-left"),
            ("right", "second", "open-right"),
        ),
        start=1,
    ):
        capture_dir = run_dir / "capture" / "runners" / pane_id
        capture_id = f"compare--{pane_id}--{pane_beat_id}"
        initial_path = capture_dir / "states" / "initial.png"
        initial = state(
            initial_path,
            color=(245, 245, 245),
        )
        initial["path"] = initial_path.relative_to(run_dir).as_posix()
        opened_path = capture_dir / "states" / "opened.png"
        opened = state(
            opened_path,
            color=(20 * index, 80, 160),
        )
        opened["path"] = opened_path.relative_to(run_dir).as_posix()
        records = [
            {
                "capture_version": 1,
                "seq": 1,
                "type": "run_start",
                "profile": {
                    "viewport_width": 1440,
                    "viewport_height": 900,
                    "device_scale_factor": 1.0,
                },
                "initial_state": initial,
            },
            {
                "capture_version": 1,
                "seq": 2,
                "type": "beat_start",
                "beat_id": capture_id,
            },
            {
                "capture_version": 1,
                "seq": 3,
                "type": "action",
                "beat_id": capture_id,
                "action_id": action_id,
                "kind": "open_page",
                "completion": {"kind": "navigation"},
                "visual": {"kind": "state", "state": opened},
            },
            {
                "capture_version": 1,
                "seq": 4,
                "type": "beat_end",
                "beat_id": capture_id,
            },
            {
                "capture_version": 1,
                "seq": 5,
                "type": "run_end",
                "status": "completed",
            },
        ]
        (capture_dir / "browser.capture.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    compile_presentation_bundle(spec, plan, run_dir)
    manifest = validate_run_bundle(spec, run_dir)

    tracks = manifest["beats"][0]["pane_tracks"]
    assert [track["pane_id"] for track in tracks] == ["left", "right"]
    assert [pane["renderer"] for pane in manifest["panes"]] == [
        "browser",
        "browser",
    ]
    assert all(
        (run_dir / "presentation" / track["beats"][0]["payload"]).is_file()
        for track in tracks
    )


def test_browser_capture_paths_report_each_scoped_pane_log(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "two-browsers",
            "browser": {},
            "panes": [
                {"id": "left", "kind": "browser"},
                {"id": "right", "kind": "browser"},
            ],
            "beats": [
                {
                    "id": "compare",
                    "layout": {"areas": [["left", "right"]]},
                    "panes": {
                        "left": [
                            {
                                "id": "first",
                                "actions": [
                                    {
                                        "id": "open-left",
                                        "open_page": {"url": "about:blank"},
                                    }
                                ],
                            }
                        ],
                        "right": [
                            {
                                "id": "second",
                                "actions": [
                                    {
                                        "id": "open-right",
                                        "open_page": {"url": "about:blank"},
                                    }
                                ],
                            }
                        ],
                    },
                }
            ],
        }
    )

    assert presentation_build.browser_capture_paths(plan, tmp_path / "run") == (
        tmp_path / "run/capture/runners/left/browser.capture.jsonl",
        tmp_path / "run/capture/runners/right/browser.capture.jsonl",
    )


def test_container_highlight_compiles_to_timed_visualization_ranges() -> None:
    spec = {
        "id": "visualization-highlight",
        "audio": {"enabled": True},
        "panes": [
            {"id": "definition", "kind": "visualization"},
            {"id": "terminal", "kind": "terminal"},
        ],
        "beats": [
            {
                "id": "explain",
                "narration": "@start@ Explain. @end@ Done.",
                "effects": [
                    {
                        "highlight": {
                            "pane": "definition",
                            "targets": [
                                {"text": "@ready@", "occurrence": 1},
                                {"text": "@ready@", "occurrence": 2},
                            ],
                            "start": "@start@",
                            "end": "@end@",
                        },
                    }
                ],
                "layout": {"areas": [["definition"], ["terminal"]]},
                "panes": {
                    "definition": [
                        {
                            "id": "source",
                            "actions": [
                                {
                                    "id": "show-source",
                                    "show": {
                                        "language": "yaml",
                                        "text": (
                                            "narration: '@ready@ Explain.'\n"
                                            "start: '@ready@'\n"
                                        ),
                                    },
                                }
                            ],
                        }
                    ],
                    "terminal": [
                        {
                            "id": "status",
                            "actions": [
                                {"id": "run-status", "run": "printf ready"},
                            ],
                        }
                    ],
                },
            }
        ],
    }
    beat = normalize_recording_plan(spec).beats[0]
    beat_timing = CompiledBeatTiming(id="explain", offset_ms=1000, duration_ms=2000)
    timing = CompiledRecordingTiming(
        duration_ms=3000,
        beats=(beat_timing,),
        actions=(),
        pane_beats=(),
        anchor_times_ms={
            ("explain", "start"): 1200,
            ("explain", "end"): 1800,
        },
        audio_intervals=(),
    )

    highlights = _visualization_highlights(
        beat,
        pane_id="definition",
        text="narration: '@ready@ Explain.'\nstart: '@ready@'\n",
        timing=timing,
        beat_timing=beat_timing,
        pane_offset_ms=0,
        pane_end_ms=2000,
        transition_ms=0,
    )

    assert [
        (item.start, item.end, item.start_ms, item.end_ms)
        for item in highlights
    ] == [
        (12, 19, 200, 800),
        (38, 45, 200, 800),
    ]


def test_sequential_visualization_beats_compile_at_narration_events(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    terminal_dir = run_dir / "capture" / "terminal-beats"
    terminal_dir.mkdir(parents=True)
    capture_id = "compose--terminal--status"
    (terminal_dir / f"{capture_id}.cast").write_text(
        json.dumps({"version": 3, "width": 80, "height": 20})
        + "\n"
        + json.dumps([0.1, "o", "Renderer: ready\n"])
        + "\n",
        encoding="utf-8",
    )
    (terminal_dir / f"{capture_id}.actions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "beat_id": capture_id,
                "actions": [
                    {
                        "id": "run-status",
                        "start_ms": 0,
                        "end_ms": 100,
                        "duration_ms": 100,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = {
        "id": "sequential-visualization",
        "audio": {"enabled": True},
        "narration": {"id": "voiceover"},
        "panes": [
            {"id": "definition", "kind": "visualization"},
            {"id": "terminal", "kind": "terminal"},
        ],
        "beats": [
            {
                "id": "compose",
                "narration": (
                    "Inspect the exact target. "
                    "@regex@ Inspect the regular expression. "
                    "@combined@ Combine both targets."
                ),
                "layout": {"areas": [["definition"], ["terminal"]]},
                "panes": {
                    "definition": [
                        {
                            "id": "exact",
                            "actions": [
                                {
                                    "id": "show-exact",
                                    "show": {
                                        "language": "yaml",
                                        "text": '- text: "ready"\n',
                                    },
                                }
                            ],
                        },
                        {
                            "id": "regex",
                            "after": "voiceover.regex.started",
                            "actions": [
                                {
                                    "id": "show-regex",
                                    "show": {
                                        "language": "yaml",
                                        "text": "- regex: 'Elapsed: .*'\n",
                                    },
                                }
                            ],
                        },
                        {
                            "id": "combined",
                            "after": "voiceover.combined.started",
                            "actions": [
                                {
                                    "id": "show-combined",
                                    "show": {
                                        "language": "yaml",
                                        "text": (
                                            '- text: "ready"\n'
                                            "- regex: 'Elapsed: .*'\n"
                                        ),
                                    },
                                }
                            ],
                        },
                    ],
                    "terminal": [
                        {
                            "id": "status",
                            "actions": [
                                {
                                    "id": "run-status",
                                    "run": "printf ready",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    }
    plan = normalize_recording_plan(spec)
    take = plan.narration_takes[0]
    timestamp = run_dir / "audio-source" / "timestamps.json"
    timestamp.parent.mkdir(parents=True)
    timestamp.write_text(
        json.dumps(
            {
                "version": 1,
                "take_id": take.id,
                "duration_ms": 1400,
                "members": [
                    {
                        "beat_id": "compose",
                        "text_start": take.members[0].text_start,
                        "text_end": take.members[0].text_end,
                        "source_start_ms": 0,
                        "source_end_ms": 1400,
                    }
                ],
                "words": [],
                "anchors": [
                    {
                        "beat_id": anchor.beat_id,
                        "id": anchor.id,
                        "text_offset": anchor.text_offset,
                        "source_ms": source_ms,
                    }
                    for anchor, source_ms in zip(
                        take.anchors,
                        (400, 900),
                        strict=True,
                    )
                ],
                "waits": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_audio = run_dir / "audio-source" / "take.mp3"
    source_audio.write_bytes(b"fake-audio")
    metadata = run_dir / "audio-source" / "audio.json"
    metadata.write_text(
        json.dumps(
            audio_module.narration_audio_metadata_v1_payload(
                plan,
                take_audio_paths={take.id: "audio/take.mp3"},
                take_durations_ms={take.id: 1400},
                timestamp_paths={take.id: f"timestamps/{timestamp.name}"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = PresentationAudioArtifacts(
        metadata=metadata,
        timestamps={take.id: timestamp},
        take_audio={take.id: source_audio},
    )

    result = compile_presentation_bundle(
        spec,
        plan,
        run_dir,
        audio_artifacts=artifacts,
    )
    manifest = validate_run_bundle(spec, run_dir)

    definition_beats = manifest["beats"][0]["pane_tracks"][0]["beats"]
    assert [
        (beat["id"], beat["offset_ms"], beat["duration_ms"])
        for beat in definition_beats
    ] == [
        ("exact", 0, 400),
        ("regex", 400, 500),
        ("combined", 900, 500),
    ]
    assert all(
        (result.bundle_dir / beat["payload"]).is_file()
        for beat in definition_beats
    )


def test_prepare_narration_audio_writes_cross_beat_v1_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    spec = {
        "id": "narrated",
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "cache_dir": str(tmp_path / "cache"),
            "format": "mp3",
        },
        "browser": {},
        "beats": [
            {
                "id": "terminal",
                "narration_take": "joined",
                "narration": "First, prepare the state,",
                "actions": [{"run": "printf ready"}],
            },
            {
                "id": "browser",
                "medium": "browser",
                "narration_take": "joined",
                "narration": "then open it in the browser.",
                "actions": [
                    {"id": "open", "open_page": {"url": "about:blank"}}
                ],
            },
        ],
    }
    plan = normalize_recording_plan(spec)

    def fake_generate_audio(items, _settings, *, force=False, on_activity=None):
        del force, on_activity
        for item in items:
            item.output_path.parent.mkdir(parents=True, exist_ok=True)
            item.output_path.write_bytes(b"take-audio")

    def fake_generate_timestamps(
        _recording_id, items, _settings, _transcription, *, force=False
    ):
        del force
        for item in items:
            path = studio.audio.timeline_path_for(item)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    audio_module.timeline_payload(
                        _recording_id,
                        item,
                        _transcription,
                        {
                            "text": item.segment.text,
                            "duration": 2.0,
                            "words": [],
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(studio.audio, "generate_audio", fake_generate_audio)
    monkeypatch.setattr(studio.audio, "generate_timestamps", fake_generate_timestamps)
    monkeypatch.setattr(studio.audio, "audio_duration_seconds", lambda _path: 2.0)

    artifacts = prepare_narration_audio(spec, plan, tmp_path / "run")

    assert artifacts is not None
    metadata = json.loads(artifacts.metadata.read_text(encoding="utf-8"))
    assert metadata["version"] == 1
    assert metadata["takes"][0]["src"] == "audio/joined.mp3"
    assert "sha256" not in metadata["takes"][0]
    assert [member["beat_id"] for member in metadata["takes"][0]["members"]] == [
        "terminal",
        "browser",
    ]
    assert set(artifacts.timestamps) == {"joined"}
    assert artifacts.tts_billing is not None
    assert artifacts.tts_billing.generated_segments == 1
    assert artifacts.tts_billing.billable_characters == len(
        plan.narration_takes[0].synthesis_text
    )
    assert artifacts.transcription_billing is not None
    assert artifacts.transcription_billing.generated_timestamp_files == 1
    assert artifacts.transcription_billing.audio_seconds == 2.0

    reused = prepare_narration_audio(spec, plan, tmp_path / "reused-run")

    assert reused is not None
    assert reused.tts_billing is None
    assert reused.transcription_billing is None


def test_prepare_narration_audio_reports_each_slow_operation(
    tmp_path: Path, monkeypatch
) -> None:
    spec = {
        "id": "narrated",
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "cache_dir": str(tmp_path / "cache"),
            "format": "mp3",
        },
        "beats": [
            {
                "id": "hello",
                "heading": "Say hello",
                "narration": "Say hello.",
                "actions": [],
            }
        ],
    }
    plan = normalize_recording_plan(spec)

    def fake_generate_audio(items, _settings, *, force=False, on_activity=None):
        del force
        assert len(items) == 1
        item = items[0]
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        item.output_path.write_bytes(b"take-audio")
        if on_activity is not None:
            on_activity(1024, 2.5)
            on_activity(1536, 2.5)
            on_activity(2048, 3.9)
        return [item.output_path]

    def fake_generate_timestamps(
        _recording_id, items, _settings, _transcription, *, force=False
    ):
        del force
        assert len(items) == 1
        item = items[0]
        path = studio.audio.timeline_path_for(item)
        path.write_text('{"words": []}\n', encoding="utf-8")
        return [path]

    monkeypatch.setattr(studio.audio, "generate_audio", fake_generate_audio)
    monkeypatch.setattr(studio.audio, "generate_timestamps", fake_generate_timestamps)
    monkeypatch.setattr(studio.audio, "audio_duration_seconds", lambda _path: 1.0)
    progress: list[tuple[str, int, int]] = []

    artifacts = prepare_narration_audio(
        spec,
        plan,
        tmp_path / "run",
        on_progress=lambda message, current, total: progress.append(
            (message, current, total)
        ),
    )

    assert artifacts is not None
    assert "NARRATION_TIMING_LOW_CONFIDENCE" in artifacts.warnings
    assert progress == [
        ("Generate narration: Say hello", 0, 3),
        ("Generate narration: Say hello · 1.0 KiB received", 0, 3),
        ("Generate narration: Say hello · 1.5 KiB received", 0, 3),
        ("Generate narration: Say hello · 3s · 2.0 KiB received", 0, 3),
        ("Generate narration: Say hello", 1, 3),
        ("Time narration: Say hello", 1, 3),
        ("Time narration: Say hello", 2, 3),
        ("Prepare narration: Say hello", 2, 3),
        ("Prepare narration: Say hello", 3, 3),
    ]


def test_openai_speech_stream_reports_received_audio_chunks(tmp_path: Path) -> None:
    settings = audio_module.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_API_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        cache_dir=tmp_path,
    )
    segment = audio_module.NarrationSegment(
        segment_id="take",
        heading="Take",
        text="Hello world",
    )
    reads = iter((b"abc", b"defg", b""))
    request_payload: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int = -1) -> bytes:
            assert size == 8 * 1024
            return next(reads)

    def urlopen(request, *, timeout):
        assert timeout == 120
        request_payload.update(json.loads(request.data))
        return Response()

    activity: list[tuple[int, float]] = []
    content = audio_module.openai_speech_bytes(
        segment,
        settings,
        environ={"OPENAI_API_KEY": "secret"},
        urlopen=urlopen,
        on_activity=lambda received, elapsed: activity.append((received, elapsed)),
    )

    assert content == b"abcdefg"
    assert request_payload["stream_format"] == "audio"
    assert [received for received, _elapsed in activity] == [3, 7]
    assert all(elapsed >= 0 for _received, elapsed in activity)


def test_source_words_repair_zero_duration_transcription_timestamps() -> None:
    words = _source_words_with_timing(
        "First second",
        [
            {"word": "First", "start": 0.0, "end": 0.4},
            {"word": "second", "start": 0.4, "end": 0.4},
        ],
        duration_ms=1000,
    )

    assert words[1]["start_ms"] == 400
    assert words[1]["end_ms"] == 401


@pytest.mark.parametrize(
    ("text", "raw_words", "duration_ms", "expected"),
    [
        (
            "A ready-to-watch video. When",
            [
                {"word": "A", "start": 0.0, "end": 0.1},
                {"word": "ready", "start": 0.2, "end": 0.4},
                {"word": "to", "start": 0.4, "end": 0.5},
                {"word": "watch", "start": 0.5, "end": 0.8},
                {"word": "video", "start": 0.9, "end": 1.2},
                {"word": "When", "start": 1.8, "end": 2.0},
            ],
            2500,
            [(0, 100), (200, 800), (900, 1200), (1800, 2000)],
        ),
        (
            "Quick start works",
            [
                {"word": "Quickstart", "start": 0.1, "end": 0.7},
                {"word": "works", "start": 0.8, "end": 1.1},
            ],
            2500,
            [(100, 400), (400, 700), (800, 1100)],
        ),
        (
            "A ready-to-watch two-beat video. When",
            [
                {"word": "A", "start": 3.44, "end": 3.68},
                {"word": "ready", "start": 3.68, "end": 3.86},
                {"word": "to", "start": 3.86, "end": 4.0},
                {"word": "watch", "start": 4.0, "end": 4.28},
                {"word": "2", "start": 4.28, "end": 4.72},
                {"word": "beat", "start": 4.72, "end": 4.72},
                {"word": "video", "start": 4.72, "end": 5.08},
                {"word": "When", "start": 5.78, "end": 5.86},
            ],
            6000,
            [
                (3440, 3680),
                (3680, 4280),
                (4280, 4720),
                (4720, 5080),
                (5780, 5860),
            ],
        ),
    ],
)
def test_source_words_preserve_timings_across_tokenization_differences(
    text: str,
    raw_words: list[dict[str, object]],
    duration_ms: int,
    expected: list[tuple[int, int]],
) -> None:
    words = _source_words_with_timing(text, raw_words, duration_ms=duration_ms)

    assert [(word["start_ms"], word["end_ms"]) for word in words] == expected
    assert all(word["timing_confidence"] == "high" for word in words)


def test_source_words_trace_numeric_equivalence_to_raw_tokens() -> None:
    words = _source_words_with_timing(
        "two-beat video. When",
        [
            {"word": "2", "start": 4.28, "end": 4.72},
            {"word": "beat", "start": 4.72, "end": 4.72},
            {"word": "video", "start": 4.72, "end": 5.08},
            {"word": "When", "start": 5.78, "end": 5.86},
        ],
        duration_ms=6000,
    )

    assert words[0]["timing_source"] == "transcription"
    assert words[0]["timing_confidence"] == "high"
    assert (words[0]["raw_word_start"], words[0]["raw_word_end"]) == (0, 2)


def test_authored_wait_uses_silence_before_when_with_numeric_asr_token() -> None:
    text = "A ready-to-watch two-beat video. When"
    wait_offset = text.index("When")
    take = NarrationTakePlan(
        id="take",
        explicit=True,
        members=(
            NarrationTakeMemberPlan(
                beat_id="beat", text=text, text_start=0, text_end=len(text)
            ),
        ),
        synthesis_text=text,
        anchors=(),
        waits=(
            NarrationTakeWaitPlan(
                beat_id="beat",
                target="build_command",
                text_offset=wait_offset,
                gap_ms=200,
            ),
        ),
    )
    words = _source_words_with_timing(
        text,
        [
            {"word": "A", "start": 3.44, "end": 3.68},
            {"word": "ready", "start": 3.68, "end": 3.86},
            {"word": "to", "start": 3.86, "end": 4.0},
            {"word": "watch", "start": 4.0, "end": 4.28},
            {"word": "2", "start": 4.28, "end": 4.72},
            {"word": "beat", "start": 4.72, "end": 4.72},
            {"word": "video", "start": 4.72, "end": 5.08},
            {"word": "When", "start": 5.78, "end": 5.86},
        ],
        duration_ms=6000,
    )

    sidecar = audio_module.narration_timestamp_sidecar_payload(
        take, duration_ms=6000, words=words
    )

    assert words[-2]["text"] == "video."
    assert (words[-2]["end_ms"], words[-1]["start_ms"]) == (5080, 5780)
    assert sidecar["words"][2]["timing_source"] == "transcription"
    assert sidecar["words"][2]["timing_confidence"] == "high"
    assert (
        sidecar["words"][2]["raw_word_start"],
        sidecar["words"][2]["raw_word_end"],
    ) == (
        4,
        6,
    )
    assert sidecar["waits"][0]["source_ms"] == 5430


@pytest.mark.parametrize(
    ("raw_words", "mismatch_source"),
    [
        (
            [
                {"word": "Alpha", "start": 0.0, "end": 0.2},
                {"word": "different", "start": 0.3, "end": 0.7},
                {"word": "video", "start": 0.8, "end": 1.1},
                {"word": "When", "start": 1.5, "end": 1.7},
            ],
            "interpolated",
        ),
        (
            [
                {"word": "Alpha", "start": 0.0, "end": 0.2},
                {"word": "video", "start": 0.8, "end": 1.1},
                {"word": "When", "start": 1.5, "end": 1.7},
            ],
            "interpolated",
        ),
        (
            [
                {"word": "Alpha", "start": 0.0, "end": 0.2},
                {"word": "unexpected", "start": 0.3, "end": 0.7},
                {"word": "misrecognized", "start": 0.7, "end": 0.8},
                {"word": "video", "start": 0.8, "end": 1.1},
                {"word": "When", "start": 1.5, "end": 1.7},
            ],
            "transcription",
        ),
    ],
)
def test_source_word_mismatch_does_not_discard_later_transcription_timing(
    raw_words: list[dict[str, object]],
    mismatch_source: str,
) -> None:
    words = _source_words_with_timing(
        "Alpha misrecognized video. When", raw_words, duration_ms=2000
    )

    assert words[1]["timing_source"] == mismatch_source
    assert words[1]["timing_confidence"] == (
        "low" if mismatch_source == "interpolated" else "high"
    )
    assert (words[2]["start_ms"], words[2]["end_ms"]) == (800, 1100)
    assert words[2]["timing_confidence"] == "high"
    assert (words[3]["start_ms"], words[3]["end_ms"]) == (1500, 1700)


def test_watch_serves_run_local_manifest_reference_graph(
    tmp_path: Path, monkeypatch
) -> None:
    bundle = tmp_path / "presentation"
    (bundle / "beats").mkdir(parents=True)
    manifest = bundle / "recording.presentation.json"
    payload = bundle / "beats/web.browser.json"
    manifest.write_text("{}\n", encoding="utf-8")
    payload.write_text("{}\n", encoding="utf-8")
    spec = {
        "id": "browser",
        "_recording_id": "browser",
        "browser": {},
        "beats": [
            {
                "id": "web",
                "medium": "browser",
                "actions": [
                    {"id": "open", "open_page": {"url": "about:blank"}}
                ],
            }
        ],
    }
    url, artifacts = studio.watch_player_url_path(spec, run_dir=tmp_path)

    assert parse_qs(urlparse(url).query)["manifest"] == [
        "/__studio_artifacts__/recording.presentation.json"
    ]
    assert artifacts == {
        "recording.presentation.json": manifest.resolve(),
        "beats/web.browser.json": payload.resolve(),
    }


def test_clean_removes_public_presentation_but_retains_private_run(
    tmp_path: Path, monkeypatch
) -> None:
    asset_dir = tmp_path / "public"
    bundle = asset_dir / "presentation"
    bundle.mkdir(parents=True)
    (bundle / "recording.presentation.json").write_text("{}\n", encoding="utf-8")
    private_run = tmp_path / "runs/run-1"
    private_run.mkdir(parents=True)
    spec = {
        "id": "browser",
        "_recording_id": "browser",
        "outputs": {
            "asset_dir": str(asset_dir),
            "cast": str(asset_dir / "recording.cast"),
        },
    }
    monkeypatch.setattr(studio, "recording_spec_from_config", lambda *args, **kwargs: spec)

    removed = studio.clean_recording_outputs({})

    assert bundle in removed
    assert not bundle.exists()
    assert private_run.is_dir()
