import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from omegaflow import __version__
from omegaflow import audio
from omegaflow import record
from omegaflow import studio
from omegaflow import studio_config as studio_config_module
from omegaflow.capture import (
    CaptureCoordinator,
    CaptureFailed,
    CaptureFailureDetail,
)
from omegaflow.record import collect_run_jobs
from omegaflow.studio_config import (
    CONFIG_DIR,
    RECORDING_SCRIPT_DIR,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    compose_studio_config,
    discover_project_layout,
    list_recording_ids,
    recording_from_script,
    recording_script_dir_from_config,
    recording_spec_from_config,
    studio_data_dir_from_config,
    studio_directive_blocks,
    studio_run_dir,
)
from omegaflow.tool_progress import ProgressBarRenderer
from omegaflow.terminal_style import ANSI_GREEN_BOLD, ANSI_RESET
from omegaflow.terminal_capture import (
    PersistentTerminalRunner,
    TerminalCaptureError,
    TerminalLifecycleStepError,
)


def write_successful_presentation_run(
    run_dir: Path, *, duration_ms: int = 1_250
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recording.fingerprint.json").write_text("{}\n", encoding="utf-8")
    presentation = run_dir / "presentation"
    presentation.mkdir()
    (presentation / "recording.presentation.json").write_text(
        json.dumps({"recording": {"duration_ms": duration_ms}}) + "\n",
        encoding="utf-8",
    )


def load_custom_build_hook():
    return load_hatch_build_module().CustomBuildHook


def load_hatch_build_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "omegaflow_hatch_build", root / "hatch_build.py"
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load hatch_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_is_available() -> None:
    assert __version__ == "0.9.0"


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_build_progress_renders_determinate_interactive_work() -> None:
    stream = TtyBuffer()
    progress = studio.BuildProgress(
        total=4,
        stream=stream,
        interactive=True,
        color=False,
    )

    progress.begin("Recording workflow (2 beats)")
    progress.update("Record: Install OmegaFlow")
    progress.advance("Recorded: Install OmegaFlow")
    progress.advance("Recorded: Build the video")
    progress.begin("Preparing narration (1 take)")
    progress.advance("Generated narration")
    progress.advance("Video ready")
    progress.finish()

    output = stream.getvalue()
    assert "0/4" in output
    assert "1/4" in output
    assert "4/4" in output
    assert "Record: Install OmegaFlow" in output
    assert "Preparing narration (1 take)" in output


def test_build_progress_keeps_a_long_first_action_visibly_active() -> None:
    stream = TtyBuffer()
    progress = studio.BuildProgress(
        total=4,
        stream=stream,
        interactive=True,
        color=False,
        heartbeat_interval=0.01,
    )

    progress.begin("Recording workflow (1 action)")
    initial_output = stream.getvalue()
    deadline = time.monotonic() + 0.5
    while len(stream.getvalue()) == len(initial_output) and time.monotonic() < deadline:
        time.sleep(0.005)
    active_output = stream.getvalue()
    progress.finish()

    assert len(active_output) > len(initial_output)
    assert "0/4" in active_output
    assert "1/4" not in active_output
    assert " · 0." not in active_output
    assert "▓" in active_output
    assert active_output.count("Recording workflow (1 action)") >= 2


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0.0, "2/4"),
        (2.9, "2/4"),
        (3.0, "2/4 · 3s"),
        (3.9, "2/4 · 3s"),
        (4.0, "2/4 · 4s"),
        (float("inf"), "2/4"),
        (float("nan"), "2/4"),
    ],
)
def test_build_progress_only_times_sustained_activities(
    elapsed: float, expected: str
) -> None:
    assert ProgressBarRenderer._detail(
        current=2,
        total=4,
        active=True,
        activity_elapsed=elapsed,
    ) == expected


def test_build_progress_activity_indicator_uses_an_intensity_gradient() -> None:
    renderer = ProgressBarRenderer(interactive=True, enabled=False)

    bar = renderer._bar(
        current=0,
        total=4,
        width=12,
        enabled=False,
        active=True,
        activity_step=5,
    )

    assert "▒▓▒" in bar


def test_build_progress_activity_position_does_not_jump_on_real_advance() -> None:
    renderer = ProgressBarRenderer(interactive=True, enabled=False)

    before = renderer._bar(
        current=0,
        total=4,
        width=20,
        enabled=False,
        active=True,
        activity_step=8,
    )
    after = renderer._bar(
        current=1,
        total=4,
        width=20,
        enabled=False,
        active=True,
        activity_step=8,
    )

    assert before.index("▓") == after.index("▓")


def test_build_progress_activity_keeps_moving_after_progress_advances() -> None:
    renderer = ProgressBarRenderer(interactive=True, enabled=False)

    renderer._bar(
        current=0,
        total=4,
        width=20,
        enabled=False,
        active=True,
        activity_step=2,
    )
    at_boundary = renderer._bar(
        current=1,
        total=4,
        width=20,
        enabled=False,
        active=True,
        activity_step=2,
    )
    next_heartbeat = renderer._bar(
        current=1,
        total=4,
        width=20,
        enabled=False,
        active=True,
        activity_step=3,
    )

    assert at_boundary.index("▓") == 1 + 5
    assert next_heartbeat.index("▓") == at_boundary.index("▓") + 1


def test_build_progress_activity_phase_survives_message_changes() -> None:
    stream = TtyBuffer()
    progress = studio.BuildProgress(
        total=4,
        stream=stream,
        interactive=True,
        color=False,
    )
    progress.begin("Recording workflow")
    progress._activity_step = 8

    progress.advance("Recorded first action")
    progress.finish()

    assert progress._activity_step == 8


def test_build_progress_retains_the_completed_interactive_bar() -> None:
    stream = TtyBuffer()
    progress = studio.BuildProgress(
        total=1,
        stream=stream,
        interactive=True,
        color=False,
    )
    progress.begin("Recording workflow")
    progress.advance("Video ready")

    progress.finish(completion="completed in 2.3s")

    output = stream.getvalue()
    final_render = output.rsplit("\x1b[2F", 1)[-1]
    final_progress_line = next(
        line for line in final_render.splitlines() if "progress" in line
    )
    assert "[████████████████████████████] 1/1" in final_progress_line
    assert final_progress_line.endswith("1/1 · completed in 2.3s")
    assert output.endswith("\x1b[1F\x1b[2K\r")


def test_build_progress_clears_an_incomplete_interactive_bar() -> None:
    stream = TtyBuffer()
    progress = studio.BuildProgress(
        total=2,
        stream=stream,
        interactive=True,
        color=False,
    )
    progress.begin("Recording workflow")

    progress.finish()

    assert stream.getvalue().endswith("\x1b[2F\x1b[2M")


def test_build_progress_keeps_the_progress_line_within_narrow_terminals() -> None:
    stream = TtyBuffer()
    renderer = ProgressBarRenderer(
        stream=stream,
        columns=20,
        interactive=True,
        enabled=False,
    )

    renderer.emit(
        {
            "message": "Recording",
            "status": "step",
            "current": 0,
            "total": 4,
            "active": True,
            "activity_elapsed": 123.4,
            "activity_step": 1,
        }
    )

    progress_line = next(
        line.removeprefix("\x1b[2K")
        for line in stream.getvalue().splitlines()
        if "progress" in line
    )
    assert len(progress_line) <= 20
    assert "0/4" in progress_line
    assert "123.4s" not in progress_line


def test_build_progress_does_not_retain_transient_heartbeat_history() -> None:
    renderer = ProgressBarRenderer(interactive=False, enabled=False)
    renderer.emit(
        {"message": "Recording", "status": "step", "current": 0, "total": 4}
    )

    for step in range(100):
        renderer.emit(
            {
                "message": "Recording",
                "status": "step",
                "current": 0,
                "total": 4,
                "active": True,
                "activity_elapsed": step / 4,
                "activity_step": step,
                "transient": True,
            }
        )

    assert len(renderer._events) == 1


def test_build_progress_keeps_noninteractive_logs_concise() -> None:
    stream = io.StringIO()
    progress = studio.BuildProgress(
        total=4,
        stream=stream,
        interactive=False,
        color=False,
    )

    progress.begin("Recording workflow (2 beats)")
    progress.update("Record: Install OmegaFlow")
    progress.advance("Recorded: Install OmegaFlow")
    progress.begin("Preparing narration (1 take)")
    progress.advance("Generated narration")
    progress.begin("Assembling video")
    progress.advance("Compiled internal manifest")
    progress.finish()

    assert stream.getvalue().splitlines() == [
        "step  Recording workflow (2 beats)",
        "step  Preparing narration (1 take)",
        "step  Assembling video",
    ]


def test_cached_recording_advances_build_progress_without_internal_lines(
    tmp_path, monkeypatch, capsys
) -> None:
    plan = studio.normalized_recording_plan(
        {
            "id": "demo",
            "beats": [{"id": "hello", "actions": [{"run": "printf hello"}]}],
        }
    )
    run_dir = tmp_path / "cached-run"
    monkeypatch.setattr(
        studio, "latest_successful_recording_run_dir", lambda _spec: run_dir
    )
    monkeypatch.setattr(
        studio.presentation_build, "capture_is_fresh", lambda *_args: True
    )
    stream = io.StringIO()
    progress = studio.BuildProgress(
        total=2,
        stream=stream,
        interactive=False,
        color=False,
    )

    result = studio.run_build_record_action(
        OmegaConf.create(
            {"force": False, "verbose": False, "output_format": "text"}
        ),
        {"_recording_id": "demo"},
        plan,
        progress=progress,
    )

    assert result == run_dir
    assert progress.current == 1
    assert stream.getvalue().splitlines() == ["step  Recording workflow (1 action)"]
    assert capsys.readouterr().out == ""


def test_forced_recording_reports_each_captured_action(
    tmp_path, monkeypatch, capsys
) -> None:
    plan = studio.normalized_recording_plan(
        {
            "id": "demo",
            "beats": [
                {
                    "id": "one",
                    "heading": "First",
                    "actions": [
                        {
                            "commands": [
                                {"id": "prepare", "run": "prepare", "display": "Prepare"}
                            ]
                        }
                    ],
                },
                {
                    "id": "two",
                    "heading": "Second",
                    "actions": [
                        {
                            "commands": [
                                {"id": "verify", "run": "verify", "display": "Verify"}
                            ]
                        }
                    ],
                },
            ],
        }
    )
    run_dir = tmp_path / "forced-run"
    monkeypatch.setattr(studio, "current_recording_run_dir", lambda _spec: run_dir)
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: pytest.fail("forced capture must not inspect cached runs"),
    )

    def fake_capture(_spec, capture_plan, target, *, headed, on_progress):
        assert target == run_dir
        assert headed is False
        actions = studio.capture_action_items(capture_plan)
        for current, action in enumerate(actions):
            on_progress("started", action, current, len(actions))
            on_progress("completed", action, current + 1, len(actions))

    monkeypatch.setattr(studio.presentation_build, "capture_recording", fake_capture)
    monkeypatch.setattr(
        studio.presentation_build, "write_capture_fingerprint", lambda *_args: None
    )
    stream = TtyBuffer()
    progress = studio.BuildProgress(
        total=3,
        stream=stream,
        interactive=True,
        color=False,
    )

    result = studio.run_build_record_action(
        OmegaConf.create(
            {"force": True, "verbose": False, "output_format": "text"}
        ),
        {"_recording_id": "demo"},
        plan,
        progress=progress,
    )

    assert result == run_dir
    assert progress.current == 2
    assert "Record: First · Prepare" in stream.getvalue()
    assert "Record: Second · Verify" in stream.getvalue()
    assert capsys.readouterr().out == ""


def test_failed_build_clears_progress_and_reports_failure(
    tmp_path, monkeypatch, capsys
) -> None:
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "audio": {"enabled": False},
        "beats": [{"id": "hello", "actions": []}],
    }
    plan = studio.normalized_recording_plan(spec)
    cfg = OmegaConf.create(
        {
            "recording": "demo",
            "force": False,
            "verbose": False,
            "output_format": "text",
        }
    )
    monkeypatch.setattr(studio, "build_publish_surface_names", lambda *_args: [])

    def fail_capture(_cfg, _spec, _plan, *, progress):
        progress.begin("Recording workflow (1 beat)")
        progress.update("Record: Broken beat")
        raise studio.StudioError("broken capture")

    monkeypatch.setattr(studio, "run_build_record_action", fail_capture)

    with pytest.raises(studio.StudioError, match="broken capture"):
        studio.run_manifest_build(cfg, dict(cfg), spec, plan)

    output = capsys.readouterr().out
    assert "step  Recording workflow (1 beat)" in output
    assert "fail  build failed after" in output
    assert "build completed" not in output


def test_capture_failure_message_surfaces_stderr_and_recovery_command() -> None:
    setup_error = TerminalLifecycleStepError(
        "setup",
        "prepare isolated demo environment",
        1,
        TerminalCaptureError(
            "terminal setup request 1 failed for <recording>: exit 1"
        ),
        run_file=(
            "/workspace/recordings/quickstart-demo/"
            "scripts/setup-demo-environment.sh"
        ),
    )
    cleanup_error = TerminalLifecycleStepError(
        "cleanup",
        "remove demo project",
        1,
        TerminalCaptureError(
            "terminal cleanup request 2 failed for <recording>: exit 1"
        ),
        run_file=(
            "/workspace/recordings/quickstart-demo/"
            "scripts/cleanup-demo-project.sh"
        ),
    )
    error = CaptureFailed(
        primary=CaptureFailureDetail("project setup", setup_error),
        cleanup=(CaptureFailureDetail("project cleanup", cleanup_error),),
    )
    report = {
        "recording_id": "quickstart-demo",
        "run_id": "20260720-221308",
        "working_directory": "/workspace",
        "stderr": (
            "/bin/bash: line 4: BASH_SOURCE[0]: unbound variable\n"
            "repository environment is missing: //.venv/bin/python\n"
            "terminal step exited 1, expected 0\n"
        ),
    }

    assert studio.capture_failure_message(error, report) == (
        "Setup step 'prepare isolated demo environment' failed (exit 1) "
        "while running '/workspace/recordings/quickstart-demo/"
        "scripts/setup-demo-environment.sh'\n"
        "  /bin/bash: line 4: BASH_SOURCE[0]: unbound variable\n"
        "  repository environment is missing: //.venv/bin/python\n"
        "warning: cleanup step 'remove demo project' also failed while running "
        "'/workspace/recordings/quickstart-demo/"
        "scripts/cleanup-demo-project.sh'\n"
        "Run: omegaflow recording=quickstart-demo action=output "
        "run_id=20260720-221308"
    )


def test_capture_failure_preserves_primary_error_when_report_is_invalid(
    tmp_path, monkeypatch
) -> None:
    plan = studio.normalized_recording_plan(
        {
            "id": "demo",
            "beats": [{"id": "broken", "actions": []}],
        }
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "failure.json").write_bytes(b"\xff")
    original = RuntimeError("capture exploded")
    monkeypatch.setattr(studio, "current_recording_run_dir", lambda _spec: run_dir)

    def fail_capture(*_args, **_kwargs):
        raise original

    monkeypatch.setattr(studio.presentation_build, "capture_recording", fail_capture)

    with pytest.raises(RuntimeError, match="capture exploded") as caught:
        studio.run_build_record_action(
            OmegaConf.create(
                {
                    "force": True,
                    "headed": False,
                    "verbose": False,
                    "output_format": "text",
                }
            ),
            {"_recording_id": "demo"},
            plan,
        )

    assert caught.value is original


def test_manifest_build_folds_internal_steps_into_concise_progress(
    tmp_path, monkeypatch, capsys
) -> None:
    output_events: list[str] = []
    progress_type = studio.BuildProgress

    class TrackingBuildProgress(progress_type):
        def finish(self, *, completion: str | None = None) -> None:
            output_events.append("progress finished")
            super().finish(completion=completion)

    original_info_line = studio.info_line

    def tracking_info_line(message: str) -> None:
        if "estimated cost this build" in message:
            output_events.append("billing printed")
        original_info_line(message)

    monkeypatch.setattr(studio, "BuildProgress", TrackingBuildProgress)
    monkeypatch.setattr(studio, "info_line", tracking_info_line)
    website_surface = tmp_path / "website.md"
    website_surface.write_text(
        "<!-- studio:demo:start -->\nold\n<!-- studio:demo:end -->\n",
        encoding="utf-8",
    )
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "audio": {"enabled": True},
        "publish": {
            "surfaces": {
                "website": {
                    "type": "docusaurus_mdx",
                    "file": str(website_surface),
                    "placeholder": "demo",
                },
                "standalone": {
                    "type": "standalone_html",
                    "file": "standalone.html",
                },
            }
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
    plan = studio.normalized_recording_plan(spec)
    cfg = OmegaConf.create(
        {
            "recording": "demo",
            "force": False,
            "verbose": False,
            "output_format": "text",
        }
    )
    run_dir = tmp_path / "run"
    monkeypatch.setattr(
        studio, "latest_successful_recording_run_dir", lambda _spec: run_dir
    )
    monkeypatch.setattr(
        studio.presentation_build, "capture_is_fresh", lambda *_args: True
    )

    def fake_audio(_spec, _plan, _run_dir, *, force, on_progress):
        assert force is False
        for current, message in enumerate(
            ("Generate narration", "Time narration", "Prepare narration")
        ):
            on_progress(message, current, 3)
            on_progress(message, current + 1, 3)
        return SimpleNamespace(
            timestamps={"take": tmp_path / "take.json"},
            tts_billing=audio.AudioBillingSummary(
                generated_segments=1,
                billable_characters=100,
                estimated_cost_usd=0.0015,
            ),
            transcription_billing=audio.AudioTranscriptionBillingSummary(
                generated_timestamp_files=1,
                audio_seconds=5.0,
                estimated_cost_usd=0.0005,
            ),
        )

    monkeypatch.setattr(
        studio.presentation_build, "prepare_narration_audio", fake_audio
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "compile_presentation_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest=run_dir / "presentation" / "recording.presentation.json",
            warnings=(),
        ),
    )
    monkeypatch.setattr(
        studio,
        "build_publish_surface_names",
        lambda *_args: ["website", "standalone"],
    )
    published_bundles: list[tuple[dict[str, object], Path]] = []
    monkeypatch.setattr(
        studio,
        "publish_presentation_bundle",
        lambda bundle_spec, bundle_run_dir: published_bundles.append(
            (bundle_spec, bundle_run_dir)
        ),
    )
    published: list[tuple[str | None, bool, bool]] = []

    def fake_publish(
        _cfg,
        *,
        surface_name=None,
        presentation_run_dir=None,
        publish_bundle_assets=True,
        report=True,
    ):
        assert presentation_run_dir == run_dir
        published.append((surface_name, publish_bundle_assets, report))
        return studio.PublishSurfaceOutcome(
            path=tmp_path / f"{surface_name}.html",
            updated=surface_name == "website",
        )

    monkeypatch.setattr(studio, "run_publish_surface", fake_publish)
    monkeypatch.setattr(studio, "remove_unused_empty_run_dir", lambda *_a, **_k: None)
    monkeypatch.setattr(
        studio, "garbage_collect_recording_runs", lambda *_a, **_k: []
    )

    assert studio.run_manifest_build(cfg, dict(cfg), spec, plan) == 0

    output = capsys.readouterr().out
    assert "step  Recording workflow (0 actions)" in output
    assert "step  Preparing narration (1 take)" in output
    assert (
        "info  OpenAI narration estimated cost this build: $0.002000 "
        "(TTS $0.001500 + transcription $0.000500)"
        in output
    )
    assert "step  Assembling video" in output
    assert "pass  build completed after" in output
    assert "capture recording" not in output
    assert "compile presentation" not in output
    assert "publish surface" not in output
    assert "wrote presentation" not in output
    assert "publish  website (Docusaurus): updated — rebuild required" in output
    assert str(tmp_path / "website.html") not in output
    assert (
        "publish  standalone (Standalone HTML): unchanged — "
        f"{tmp_path / 'standalone.html'}"
        in output
    )
    assert published_bundles == [(spec, run_dir)]
    assert published == [
        ("website", False, False),
        ("standalone", False, False),
    ]
    assert output_events == ["progress finished", "billing printed"]


def test_narration_billing_message_colors_only_dollar_amounts() -> None:
    artifacts = SimpleNamespace(
        tts_billing=audio.AudioBillingSummary(
            generated_segments=1,
            billable_characters=100,
            estimated_cost_usd=0.0015,
        ),
        transcription_billing=audio.AudioTranscriptionBillingSummary(
            generated_timestamp_files=1,
            audio_seconds=5.0,
            estimated_cost_usd=0.0005,
        ),
    )

    assert studio.narration_billing_message(artifacts, color=True) == (
        "OpenAI narration estimated cost this build: "
        f"{ANSI_GREEN_BOLD}$0.002000{ANSI_RESET} "
        f"(TTS {ANSI_GREEN_BOLD}$0.001500{ANSI_RESET} + transcription "
        f"{ANSI_GREEN_BOLD}$0.000500{ANSI_RESET})"
    )


@pytest.mark.parametrize(
    ("invalid_surface", "message"),
    [
        (
            {"type": "unsupported", "file": "invalid.html"},
            "unsupported publish surface type",
        ),
        (
            {"type": "docusaurus_mdx", "file": "invalid.md"},
            "docusaurus_mdx surfaces require a placeholder",
        ),
        (
            {"type": "plain_html", "file": "invalid.html"},
            "plain_html surfaces require a placeholder",
        ),
    ],
)
def test_manifest_build_validates_all_surfaces_before_publishing(
    monkeypatch, invalid_surface, message
) -> None:
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "audio": {"enabled": False},
        "publish": {
            "surfaces": {
                "valid": {
                    "type": "standalone_html",
                    "file": "valid.html",
                },
                "invalid": {
                    **invalid_surface,
                },
            }
        },
        "beats": [{"id": "hello", "actions": []}],
    }
    plan = studio.normalized_recording_plan(spec)
    cfg = OmegaConf.create(
        {
            "recording": "demo",
            "force": False,
            "verbose": False,
            "output_format": "text",
        }
    )
    monkeypatch.setattr(
        studio,
        "build_publish_surface_names",
        lambda *_args: ["valid", "invalid"],
    )
    capture_called = False
    publish_called = False

    def capture(*_args, **_kwargs):
        nonlocal capture_called
        capture_called = True

    def publish(*_args, **_kwargs):
        nonlocal publish_called
        publish_called = True

    monkeypatch.setattr(studio, "run_build_record_action", capture)
    monkeypatch.setattr(studio, "publish_presentation_bundle", publish)

    with pytest.raises(studio.StudioError, match=message):
        studio.run_manifest_build(cfg, dict(cfg), spec, plan)

    assert capture_called is False
    assert publish_called is False


def test_command_output_replace_selects_replacement_mode() -> None:
    assert record.command_output_config(
        {"output": {"replace": "concise output"}}, field="actions.0"
    ) == {"mode": "replace", "replace": "concise output"}


@pytest.mark.parametrize(
    "output",
    [
        {"mode": "fake", "text": "legacy output"},
        {"text": "ambiguous output"},
        "fake",
    ],
)
def test_command_output_rejects_old_fake_forms(output: object) -> None:
    with pytest.raises(record.RecordingError):
        record.command_output_config({"output": output}, field="actions.0")


def test_package_installs_omegaflow_command() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "omegaflow"
    assert pyproject["project"]["scripts"] == {
        "omegaflow": "omegaflow.studio:main"
    }
    assert pyproject["tool"]["hatch"]["build"]["hooks"]["custom"] == {
        "path": "hatch_build.py"
    }
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"] == [
        "/src/omegaflow/bin/asciinema",
        "/src/omegaflow/bin/asciinema.platform",
    ]


def test_asciinema_command_prefers_configured_path(tmp_path) -> None:
    configured = tmp_path / "asciinema"

    assert (
        record.asciinema_command(
            {"studio": {"asciinema_path": str(configured)}}
        )
        == str(configured)
    )


def test_asciinema_command_expands_configured_user_path(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/test-user")

    assert (
        record.asciinema_command({"studio": {"asciinema_path": "~/bin/asciinema"}})
        == "/home/test-user/bin/asciinema"
    )


def test_asciinema_command_prefers_bundled_path(monkeypatch) -> None:
    monkeypatch.setattr(record, "bundled_asciinema_path", lambda: "/bundle/asciinema")

    assert record.asciinema_command({"studio": {}}) == "/bundle/asciinema"


def test_check_asciinema_reports_missing_command(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    try:
        record.check_asciinema({"studio": {"asciinema_path": "/missing/asciinema"}})
    except record.RecordingError as exc:
        assert "asciinema 3.x is required" in str(exc)
        assert "configured at /missing/asciinema" in str(exc)
    else:
        raise AssertionError("expected missing asciinema to fail")


def test_check_asciinema_rejects_old_version(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["asciinema", "--version"],
            returncode=0,
            stdout="asciinema 2.4.0\n",
        )

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    try:
        record.check_asciinema()
    except record.RecordingError as exc:
        assert "asciinema 3.x is required, found: asciinema 2.4.0" in str(exc)
    else:
        raise AssertionError("expected old asciinema to fail")


def test_check_asciinema_accepts_version_3(monkeypatch) -> None:
    captured = {}

    def fake_run(args, **_kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="asciinema 3.2.1\n",
        )

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    assert record.check_asciinema({"studio": {"asciinema_path": "/opt/asciinema"}}) == (
        "asciinema 3.2.1"
    )
    assert captured["args"] == ["/opt/asciinema", "--version"]


def test_build_hook_marks_bundled_recorder_wheel_as_platform_specific(
    tmp_path,
) -> None:
    custom_build_hook = load_custom_build_hook()
    bundled = tmp_path / "src" / "omegaflow" / "bin" / "asciinema"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("fake recorder", encoding="utf-8")
    bundled.with_suffix(".platform").write_text("linux-x86_64\n", encoding="utf-8")
    build_data = {"tag": "py3-none-any", "pure_python": True}

    class Hook:
        root = str(tmp_path)
        target_name = "wheel"

    custom_build_hook.initialize(Hook(), "standard", build_data)

    assert build_data == {
        "tag": "py3-none-manylinux_2_35_x86_64",
        "pure_python": False,
    }


def test_build_hook_vendors_recorder_for_supported_source_wheel(
    monkeypatch,
    tmp_path,
) -> None:
    hatch_build = load_hatch_build_module()
    build_data = {"tag": "py3-none-any", "pure_python": True}

    def fake_vendor_asciinema(root, platform, *, output) -> None:
        assert root == tmp_path
        assert platform == "linux-x86_64"
        output.parent.mkdir(parents=True)
        output.write_text("fake recorder", encoding="utf-8")
        output.with_suffix(".platform").write_text(platform + "\n", encoding="utf-8")

    monkeypatch.setattr(hatch_build, "current_build_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(hatch_build, "vendor_asciinema", fake_vendor_asciinema)

    class Hook:
        root = str(tmp_path)
        target_name = "wheel"

    hatch_build.CustomBuildHook.initialize(Hook(), "standard", build_data)

    assert build_data == {
        "tag": "py3-none-manylinux_2_35_x86_64",
        "pure_python": False,
    }


def test_build_hook_keeps_unsupported_source_wheel_pure(
    monkeypatch,
    tmp_path,
) -> None:
    hatch_build = load_hatch_build_module()
    build_data = {"tag": "py3-none-any", "pure_python": True}
    monkeypatch.setattr(hatch_build, "current_build_platform", lambda: None)

    class Hook:
        root = str(tmp_path)
        target_name = "wheel"

    hatch_build.CustomBuildHook.initialize(Hook(), "standard", build_data)

    assert build_data == {"tag": "py3-none-any", "pure_python": True}


def test_build_hook_loads_dataclass_vendor_script(tmp_path) -> None:
    hatch_build = load_hatch_build_module()
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "vendor_asciinema.py").write_text(
        "\n".join(
            [
                "from dataclasses import dataclass",
                "",
                "@dataclass",
                "class Asset:",
                "    name: str",
                "",
                "def vendor(platform, *, output):",
                "    Asset(platform)",
                "    output.parent.mkdir(parents=True)",
                "    output.write_text('fake recorder')",
                "    output.with_suffix('.platform').write_text(platform + '\\n')",
            ]
        ),
        encoding="utf-8",
    )

    output = tmp_path / "src" / "omegaflow" / "bin" / "asciinema"
    hatch_build.vendor_asciinema(tmp_path, "linux-x86_64", output=output)

    assert output.read_text(encoding="utf-8") == "fake recorder"
    assert output.with_suffix(".platform").read_text(encoding="utf-8") == (
        "linux-x86_64\n"
    )


def test_build_hook_requires_bundled_recorder_platform_metadata(
    tmp_path,
) -> None:
    custom_build_hook = load_custom_build_hook()
    bundled = tmp_path / "src" / "omegaflow" / "bin" / "asciinema"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("fake recorder", encoding="utf-8")

    class Hook:
        root = str(tmp_path)
        target_name = "wheel"

    try:
        custom_build_hook.initialize(Hook(), "standard", {})
    except RuntimeError as exc:
        assert "asciinema.platform" in str(exc)
    else:
        raise AssertionError("expected missing platform metadata to fail")


def test_omegaflow_help_uses_product_name() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "omegaflow.studio", "--help"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "omegaflow is powered by Hydra." in result.stdout
    assert "studio is powered by Hydra." not in result.stdout


def test_recording_schema_docs_are_generated() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "website/scripts/update_recording_schema_docs.py",
            "--check",
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_studio_paths_use_canonical_recordings_workspace() -> None:
    assert CONFIG_DIR.parts[-2:] == ("omegaflow", "conf")
    assert STUDIO_CONFIG_NAME == "base-config"
    assert RECORDING_SCRIPT_DIR.parts[-1:] == ("recordings",)


def test_discovers_recordings_project_directory(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "config.yaml").write_text(
        "audio:\n  enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir.name == "conf"
    assert layout.config_dir.parent.name == "omegaflow"
    assert layout.recording_script_dir == recordings_dir


def test_discovers_project_config_from_nested_directory(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    nested = project / "docs" / "guide"
    nested.mkdir(parents=True)
    config_dir = project / ".omegaflow"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "studio:\n  recording_dir: demos\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(nested)

    layout = discover_project_layout()
    config = compose_studio_config(None, ())

    assert layout.root == project
    assert config["project_root"] == str(project)
    assert config["studio"]["recording_dir"] == "demos"


def test_empty_workspace_uses_bundled_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir.name == "conf"
    assert layout.config_dir.parent.name == "omegaflow"
    assert layout.data_dir == tmp_path / "recordings" / ".omegaflow"
    assert layout.recording_script_dir == tmp_path / "recordings"


def test_project_root_is_hydra_config_and_environment_does_not_override_it(
    tmp_path, monkeypatch
) -> None:
    ignored = tmp_path / "ignored"
    configured = tmp_path / "configured"
    config_dir = configured / ".omegaflow"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "studio:\n  recording_dir: demos\n  data_dir: .data\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMEGAFLOW_PROJECT_ROOT", str(ignored))

    config = compose_studio_config(
        None,
        overrides=(f"project_root={configured}",),
    )

    assert config["project_root"] == str(configured)
    assert recording_script_dir_from_config(config) == configured / "demos"
    assert studio_data_dir_from_config(config) == configured / ".data"
    assert discover_project_layout(start=configured).root == configured


def test_studio_run_dir_uses_data_directory() -> None:
    assert (
        studio_run_dir(
            "recordings/.omegaflow",
            "build",
            "record",
            False,
            "demo",
            "20260705-010203",
        )
        == "recordings/.omegaflow/runs/demo/20260705-010203"
    )
    assert (
        studio_run_dir(
            "recordings/.omegaflow",
            "inspect",
            None,
            False,
            "demo",
            "20260705-010203",
        )
        == "recordings/.omegaflow/runs/.scratch/inspect/demo/20260705-010203"
    )


def test_studio_run_dir_routes_missing_recording_to_scratch() -> None:
    assert (
        studio_run_dir(
            "recordings/.omegaflow",
            "build",
            None,
            False,
            None,
            "20260705-010203",
        )
        == "recordings/.omegaflow/runs/.scratch/build/unselected/20260705-010203"
    )


def test_studio_config_loads_cwd_local_config(tmp_path, monkeypatch) -> None:
    local_config_dir = tmp_path / ".omegaflow"
    local_config_dir.mkdir()
    (local_config_dir / "config.yaml").write_text(
        """
studio:
  recording_dir: demos
  data_dir: demos/.omegaflow
env_file: .env.studio
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = compose_studio_config(None, ())

    assert config["studio"]["recording_dir"] == "demos"
    assert config["studio"]["data_dir"] == "demos/.omegaflow"
    assert config["env_file"] == ".env.studio"


def test_runs_action_uses_config_data_dir(tmp_path, monkeypatch, capsys) -> None:
    local_config_dir = tmp_path / ".omegaflow"
    local_config_dir.mkdir()
    (local_config_dir / "config.yaml").write_text(
        """
studio:
  data_dir: custom-state
""".lstrip(),
        encoding="utf-8",
    )
    run_dir = tmp_path / "custom-state" / "runs" / "demo" / "20260705-010203"
    write_successful_presentation_run(run_dir)
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(None, ("action=runs", "output_format=json"))

    assert record.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    jobs = json.loads(capsys.readouterr().out)
    assert [job["job_id"] for job in jobs] == ["20260705-010203"]
    assert jobs[0]["type"] == "demo"


def test_studio_recording_dir_comes_from_config(tmp_path) -> None:
    recordings_dir = tmp_path / "docs" / "recordings"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
title: Hello Video
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_spec_from_config(
        {
            "recording": "hello",
            "studio": {
                "recording_dir": str(recordings_dir),
            },
        },
        recording_id=None,
        overrides=("studio.recording_dir=" + str(recordings_dir),),
    )

    assert spec["id"] == "hello"
    assert spec["_recording_dir"] == str(recordings_dir.resolve())
    assert spec["_manifest_path"] == str(recordings_dir / "hello" / "index.md")


def test_nested_recording_directories_are_listed_and_loaded(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "tutorial" / "recording-file"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
id: tutorial/recording-file
title: Tutorial Recording File
---

```yaml studio-directive
scene: Tutorial Recording File
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_spec_from_config(
        {
            "recording": "tutorial/recording-file",
            "studio": {
                "recording_dir": str(recordings_dir),
            },
        },
        recording_id=None,
        overrides=("recording=tutorial/recording-file",),
    )

    assert list_recording_ids(recordings_dir) == ["tutorial/recording-file"]
    assert spec["id"] == "tutorial/recording-file"
    assert spec["_manifest_path"] == str(recording_dir / "index.md")
    assert (
        spec["outputs"]["asset_dir"]
        == "recordings/.omegaflow/videos/tutorial/recording-file"
    )
    output_dir = (
        Path.cwd()
        / "recordings"
        / ".omegaflow"
        / "videos"
        / "tutorial"
        / "recording-file"
    )
    assert studio.presentation_build.public_bundle_dir(spec) == output_dir / "presentation"
    assert studio.presentation_build.public_manifest_path(spec) == (
        output_dir / "presentation" / "recording.presentation.json"
    )


def test_recording_source_kind_defaults_to_video_for_compatibility(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
id: hello
title: Hello Video
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_from_script("hello", recording_dir=recordings_dir)

    assert spec["kind"] == "video"


def test_recording_video_preserves_description(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
id: hello
title: Hello Video
description: Learn how to make a narrated terminal video.
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_from_script("hello", recording_dir=recordings_dir)

    assert spec["description"] == "Learn how to make a narrated terminal video."


def test_recording_collection_preserves_declared_member_order(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    collection_dir = recordings_dir / "tutorial"
    collection_dir.mkdir(parents=True)
    (collection_dir / "index.md").write_text(
        """
---
kind: collection
id: tutorial
title: Tutorial
members:
  - tutorial/recording-file
  - tutorial/beat
  - tutorial/publishing
---

# Tutorial
""".lstrip(),
        encoding="utf-8",
    )

    collection = studio_config_module.recording_collection_from_script(
        "tutorial",
        recording_dir=recordings_dir,
    )

    assert collection == {
        "kind": "collection",
        "id": "tutorial",
        "title": "Tutorial",
        "members": [
            "tutorial/recording-file",
            "tutorial/beat",
            "tutorial/publishing",
        ],
    }


def test_recording_collection_rejects_duplicate_members(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    collection_dir = recordings_dir / "tutorial"
    collection_dir.mkdir(parents=True)
    (collection_dir / "index.md").write_text(
        """
---
kind: collection
id: tutorial
members:
  - tutorial/beat
  - tutorial/beat
---
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(
        StudioConfigError,
        match="collection tutorial contains duplicate member: tutorial/beat",
    ):
        studio_config_module.recording_collection_from_script(
            "tutorial",
            recording_dir=recordings_dir,
        )


def test_collection_build_delegates_to_video_pipeline_in_member_order(
    tmp_path, monkeypatch, capsys
) -> None:
    recordings_dir = tmp_path / "recordings"
    collection_dir = recordings_dir / "tutorial"
    collection_dir.mkdir(parents=True)
    members = ["tutorial/recording-file", "tutorial/beat"]
    (collection_dir / "index.md").write_text(
        """
---
kind: collection
id: tutorial
title: Tutorial
members:
  - tutorial/recording-file
  - tutorial/beat
---
""".lstrip(),
        encoding="utf-8",
    )
    for member in members:
        member_dir = recordings_dir / member
        member_dir.mkdir(parents=True)
        (member_dir / "index.md").write_text(
            f"""
---
kind: video
id: {member}
title: {member}
audio:
  enabled: false
---

```yaml studio-directive
scene: {member}
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
    - commands:
        - run: printf 'hello\\n'
```
""".lstrip(),
            encoding="utf-8",
        )
    config = {
        "project_root": str(tmp_path),
        "recording": "tutorial",
        "action": "build",
        "output_format": "text",
        "dry_run": False,
        "force": True,
        "load_env_file": False,
        "rec": {},
        "script_params": {},
        "studio": {
            "recording_dir": "recordings",
            "data_dir": "recordings/.omegaflow",
        },
    }
    cfg = OmegaConf.create(config)
    built: list[tuple[str, bool]] = []

    def fake_run_build(member_cfg, *, show_followups=True):
        built.append((OmegaConf.select(member_cfg, "recording"), show_followups))
        return 0

    monkeypatch.setattr(studio, "run_build", fake_run_build)

    assert studio.run_collection_build(cfg, config) == 0

    assert built == [(members[0], False), (members[1], False)]
    output = capsys.readouterr().out
    assert "build collection: Tutorial (2 videos)" in output
    assert "[1/2] tutorial/recording-file" in output
    assert "[2/2] tutorial/beat" in output
    assert "collection completed: 2 videos" in output


def test_collection_build_dry_run_lists_members_without_building(
    tmp_path, monkeypatch, capsys
) -> None:
    config = {
        "recording": "tutorial",
        "output_format": "text",
        "dry_run": True,
    }
    cfg = OmegaConf.create(config)
    collection = {
        "kind": "collection",
        "id": "tutorial",
        "title": "Tutorial",
        "members": ["tutorial/recording-file", "tutorial/beat"],
    }
    monkeypatch.setattr(
        studio,
        "load_collection_build",
        lambda _cfg, _config: (collection, []),
    )
    monkeypatch.setattr(
        studio,
        "run_build",
        lambda *_args, **_kwargs: pytest.fail("dry run must not build videos"),
    )

    assert studio.run_collection_build(cfg, config) == 0

    output = capsys.readouterr().out
    assert "Build collection dry run: Tutorial" in output
    assert "1. tutorial/recording-file" in output
    assert "2. tutorial/beat" in output
    assert "No videos were built." in output


def test_collection_build_dry_run_supports_json_output(monkeypatch, capsys) -> None:
    config = {
        "recording": "tutorial",
        "output_format": "json",
        "dry_run": True,
    }
    cfg = OmegaConf.create(config)
    collection = {
        "kind": "collection",
        "id": "tutorial",
        "title": "Tutorial",
        "members": ["tutorial/recording-file", "tutorial/beat"],
    }
    monkeypatch.setattr(
        studio,
        "load_collection_build",
        lambda _cfg, _config: (collection, []),
    )

    assert studio.run_collection_build(cfg, config) == 0

    assert json.loads(capsys.readouterr().out) == {
        "collection": "tutorial",
        "dry_run": True,
        "members": ["tutorial/recording-file", "tutorial/beat"],
        "title": "Tutorial",
    }


def test_tool_dispatches_collection_to_collection_build(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings" / "tutorial"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "index.md").write_text(
        """
---
kind: collection
id: tutorial
members:
  - tutorial/beat
---
""".lstrip(),
        encoding="utf-8",
    )
    config = {
        "project_root": str(tmp_path),
        "recording": "tutorial",
        "action": "build",
        "output_format": "text",
        "dry_run": False,
        "load_env_file": False,
        "studio": {
            "recording_dir": "recordings",
            "data_dir": "recordings/.omegaflow",
        },
    }
    calls: list[str] = []
    monkeypatch.setattr(
        studio,
        "run_collection_build",
        lambda _cfg, _config: calls.append(_config["recording"]) or 0,
    )

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert calls == ["tutorial"]


def test_tool_dispatches_collection_to_collection_watch(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings" / "tutorial"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "index.md").write_text(
        """
---
kind: collection
id: tutorial
members:
  - tutorial/beat
---
""".lstrip(),
        encoding="utf-8",
    )
    config = {
        "project_root": str(tmp_path),
        "recording": "tutorial",
        "action": "watch",
        "output_format": "text",
        "load_env_file": False,
        "studio": {
            "recording_dir": "recordings",
            "data_dir": "recordings/.omegaflow",
        },
    }

    calls: list[str] = []
    monkeypatch.setattr(
        studio,
        "run_collection_watch",
        lambda _cfg, _config: calls.append(_config["recording"]) or 0,
    )

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert calls == ["tutorial"]


def test_tool_rejects_single_video_actions_for_a_collection(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings" / "tutorial"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "index.md").write_text(
        """
---
kind: collection
id: tutorial
members:
  - tutorial/beat
---
""".lstrip(),
        encoding="utf-8",
    )
    config = {
        "project_root": str(tmp_path),
        "recording": "tutorial",
        "action": "check",
        "output_format": "text",
        "load_env_file": False,
        "studio": {
            "recording_dir": "recordings",
            "data_dir": "recordings/.omegaflow",
        },
    }

    with pytest.raises(
        studio.StudioError,
        match=(
            "recording=tutorial is a collection; "
            "only action=build and action=watch are supported"
        ),
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))


def test_nested_recording_id_rejects_path_traversal(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    try:
        recording_from_script("../secret", recording_dir=recordings_dir)
    except StudioConfigError as exc:
        assert "lowercase kebab-case path" in str(exc)
    else:
        raise AssertionError("expected path traversal recording id to be rejected")


def test_narration_wait_marker_can_pause_before_more_spoken_text() -> None:
    text, anchors, waits = studio_config_module.narration_text_and_anchors(
        "Run the command. @install@ Then wait. "
        "@wait:install_command+300ms@ Now explain output."
    )

    assert text == "Run the command. Then wait. Now explain output."
    assert anchors == [
        {
            "id": "install",
            "marker": "@install@",
            "text_offset": len("Run the command."),
        }
    ]
    assert waits == [
        {
            "target": "install_command",
            "marker": "@wait:install_command+300ms@",
            "text_offset": len("Run the command. Then wait."),
            "gap_seconds": 0.3,
        }
    ]


def test_audio_timing_markers_require_audio_enabled(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "demo"
    recording_dir.mkdir(parents=True)
    (recordings_dir / "config.yaml").write_text(
        "audio:\n  enabled: false\n", encoding="utf-8"
    )
    (recording_dir / "index.md").write_text(
        """\
---
id: demo
title: Demo
---

```yaml studio-directive
scene: Demo
```

```yaml studio-directive
beat:
  id: hello
  heading: Hello
  narration: Talk first. @run_demo@ Then wait. @wait:run_demo+300ms@ Continue.
  actions:
  - commands:
    - id: run_demo
      run: echo hello
      after: "@run_demo@"
```
""",
        encoding="utf-8",
    )

    try:
        recording_spec_from_config(
            {"recording": "demo", "studio": {"recording_dir": str(recordings_dir)}},
            recording_id=None,
            overrides=(),
        )
    except StudioConfigError as exc:
        message = str(exc)
        assert "audio timing markers require audio.enabled: true" in message
        assert "narration wait markers in beat 'hello'" in message
        assert "command 'run_demo' after anchor '@run_demo@' in beat 'hello'" in message
    else:
        raise AssertionError("expected audio timing markers without audio to fail")


def test_terminal_highlight_anchor_timing_requires_audio_enabled(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "demo"
    recording_dir.mkdir(parents=True)
    (recordings_dir / "config.yaml").write_text("audio:\n  enabled: false\n", encoding="utf-8")
    (recording_dir / "index.md").write_text(
        """\
---
id: demo
title: Demo
---

```yaml studio-directive
scene: Demo
```

```yaml studio-directive
beat:
  id: hello
  heading: Hello
  narration: "@highlight_start@ Project settings. @highlight_end@"
  effects:
  - highlight:
      text: .omegaflow/config.yaml
      start: "@highlight_start@"
      end: "@highlight_end@"
```
""",
        encoding="utf-8",
    )

    with pytest.raises(
        StudioConfigError,
        match=r"terminal text highlight in beat 'hello'",
    ):
        recording_spec_from_config(
            {"recording": "demo", "studio": {"recording_dir": str(recordings_dir)}},
            recording_id=None,
            overrides=(),
        )


def test_studio_run_dir_uses_safe_placeholder_for_invalid_recording_id() -> None:
    run_dir = studio_config_module.studio_run_dir(
        "recordings/.omegaflow",
        "build",
        None,
        False,
        "../secret",
        "20260705-010203",
    )

    assert run_dir == "recordings/.omegaflow/runs/invalid-recording/20260705-010203"


def test_flat_recording_file_is_not_supported(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello.md").write_text(
        """
---
id: hello
title: Old Layout
---
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    assert list_recording_ids(recordings_dir) == []
    try:
        recording_from_script("hello")
    except StudioConfigError as exc:
        assert "recordings/hello/index.md" in str(exc)
    else:
        raise AssertionError("expected flat recording files to be unsupported")


def test_collect_run_jobs_uses_config_data_dir(tmp_path) -> None:
    data_dir = tmp_path / "media"
    run_dir = data_dir / "runs" / "demo" / "20260705-010203"
    write_successful_presentation_run(run_dir)

    jobs = collect_run_jobs(
        now=datetime(2026, 7, 5, 1, 3, 3),
        data_dir=data_dir,
    )

    assert [job["job_id"] for job in jobs] == ["20260705-010203"]
    assert jobs[0]["type"] == "demo"
    assert jobs[0]["result"] == "success"


def test_collect_run_jobs_handles_nested_recording_ids(tmp_path) -> None:
    data_dir = tmp_path / "media"
    run_dir = data_dir / "runs" / "tutorial" / "recording-file" / "20260705-010203"
    write_successful_presentation_run(run_dir)

    jobs = collect_run_jobs(
        now=datetime(2026, 7, 5, 1, 3, 3),
        data_dir=data_dir,
    )

    assert [job["job_id"] for job in jobs] == ["20260705-010203"]
    assert jobs[0]["type"] == "tutorial/recording-file"
    assert record.find_latest_run_dir(
        "tutorial/recording-file",
        artifact="success",
        data_dir=data_dir,
    ) == run_dir
    assert record.find_run_dir_by_id(
        "20260705-010203",
        data_dir=data_dir,
    ) == run_dir


def test_success_artifact_filter_excludes_failed_runs(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "demo" / "20260705-010203"
    write_successful_presentation_run(run_dir)

    assert record.run_dir_has_artifact(run_dir, "success")

    (run_dir / "failure.json").write_text('{"message": "boom"}\n', encoding="utf-8")

    assert not record.run_dir_has_artifact(run_dir, "success")
    assert record.run_dir_has_artifact(run_dir, "preserved")


def test_copy_run_artifact_allows_same_path(tmp_path) -> None:
    artifact = tmp_path / "recording.cast"
    artifact.write_text('{"version": 2}\n', encoding="utf-8")

    record.copy_run_artifact(artifact, artifact)

    assert artifact.read_text(encoding="utf-8") == '{"version": 2}\n'


def test_audio_env_file_is_recording_local_config(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env.audio"
    env_file.write_text("OPENAI_RECORDING_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_RECORDING_KEY", "process-secret")
    spec = {
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env_file": str(env_file),
            "env": "OPENAI_RECORDING_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
        },
    }

    settings = audio.audio_settings(spec)
    loaded = audio.load_audio_env_file(settings)

    assert settings.env_file == env_file
    assert settings.env == "OPENAI_RECORDING_KEY"
    assert loaded == {}
    assert os.environ["OPENAI_RECORDING_KEY"] == "process-secret"

    spec["audio"]["env_override"] = True
    settings = audio.audio_settings(spec)
    loaded = audio.load_audio_env_file(settings)

    assert loaded == {"OPENAI_RECORDING_KEY": "file-secret"}
    assert os.environ["OPENAI_RECORDING_KEY"] == "file-secret"


def test_recording_frontmatter_overrides_recordings_config(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "config.yaml").write_text(
        """
audio:
  enabled: false
  provider: openai
  env: SHARED_KEY
outputs:
  dir: site/videos
style:
  color: false
""".lstrip(),
        encoding="utf-8",
    )
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
title: Hello Video
audio:
  enabled: true
---

# Hello Video

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - run: printf 'hello\\n'
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    spec = recording_from_script("hello")

    assert spec["id"] == "hello"
    assert spec["title"] == "Hello Video"
    assert spec["audio"]["enabled"] is True
    assert spec["audio"]["provider"] == "openai"
    assert spec["audio"]["env"] == "SHARED_KEY"
    assert spec["outputs"]["dir"] == "site/videos"
    assert spec["outputs"]["asset_dir"] == "site/videos/hello"
    assert "cast" not in spec["outputs"]
    assert "retimed_cast" not in spec["outputs"]
    assert "audio" not in spec["outputs"]
    assert "audio_metadata" not in spec["outputs"]
    assert spec["style"]["color"] is False
    assert spec["beats"][0]["id"] == "hello"


def test_rec_from_tool_config_overrides_recording_spec(
    tmp_path,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
id: hello
title: Hello Video
capture:
  headless: true
  window_size: 80x20
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_spec_from_config(
        {
            "recording": "hello",
            "studio": {"recording_dir": str(recordings_dir)},
            "rec": {
                "capture": {
                    "headless": False,
                    "window_size": "120x32",
                },
            },
        },
        recording_id=None,
        overrides=("rec.capture.headless=false",),
    )

    assert spec["capture"]["headless"] is False
    assert spec["capture"]["window_size"] == "120x32"


def test_rec_overrides_are_applied_before_recording_interpolations(
    tmp_path,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
id: hello
title: Hello Video
outputs:
  dir: site/videos
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_spec_from_config(
        {
            "recording": "hello",
            "studio": {"recording_dir": str(recordings_dir)},
            "rec": {"outputs": {"dir": "preview/videos"}},
        },
        recording_id=None,
        overrides=("rec.outputs.dir=preview/videos",),
    )

    assert spec["outputs"]["dir"] == "preview/videos"
    assert spec["outputs"]["asset_dir"] == "preview/videos/hello"
    assert "cast" not in spec["outputs"]
    assert "retimed_cast" not in spec["outputs"]


def test_rec_rejects_non_mapping(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
id: hello
title: Hello Video
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    try:
        recording_spec_from_config(
            {
                "recording": "hello",
                "studio": {"recording_dir": str(recordings_dir)},
                "rec": "capture.headless=false",
            },
            recording_id=None,
            overrides=(),
        )
    except StudioConfigError as exc:
        assert "rec must be a mapping" in str(exc)
    else:
        raise AssertionError("expected StudioConfigError")


def test_rec_rejects_identity_and_generated_fields(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
id: hello
title: Hello Video
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    try:
        recording_spec_from_config(
            {
                "recording": "hello",
                "studio": {"recording_dir": str(recordings_dir)},
                "rec": {"id": "other", "script": "other/index.md"},
            },
            recording_id=None,
            overrides=(),
        )
    except StudioConfigError as exc:
        assert "rec cannot override recording identity/generated fields" in str(exc)
        assert "id" in str(exc)
        assert "script" in str(exc)
    else:
        raise AssertionError("expected StudioConfigError")


def test_compose_accepts_nested_rec_overrides() -> None:
    config = compose_studio_config(
        "quickstart-demo",
        overrides=("rec.capture.headless=false",),
    )

    assert config["recording"] == "quickstart-demo"
    assert config["rec"]["capture"]["headless"] is False


def test_compose_accepts_watch_open_override() -> None:
    config = compose_studio_config(
        "quickstart-demo",
        overrides=("action=watch", "open=false"),
    )

    assert config["action"] == "watch"
    assert config["open"] is False


def test_cli_rec_overrides_are_normalized_for_hydra() -> None:
    assert studio.normalize_cli_rec_overrides(
        [
            "omegaflow",
            "recording=quickstart-demo",
            "rec.capture.headless=false",
            "+rec.audio.enabled=false",
        ]
    ) == [
        "omegaflow",
        "recording=quickstart-demo",
        "+rec.capture.headless=false",
        "+rec.audio.enabled=false",
    ]


def test_cli_adds_selected_project_to_hydra_searchpath(tmp_path) -> None:
    argv = studio.add_project_config_searchpath(
        ["omegaflow", f"project_root={tmp_path}", "action=list"]
    )

    assert argv == [
        "omegaflow",
        f'hydra.searchpath=["file://{tmp_path.as_posix()}"]',
        f"project_root={tmp_path}",
        "action=list",
    ]


def test_cli_project_root_loads_selected_project_config(tmp_path) -> None:
    config_dir = tmp_path / ".omegaflow"
    recording_dir = tmp_path / "demos" / "demo"
    config_dir.mkdir()
    recording_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "studio:\n  recording_dir: demos\n",
        encoding="utf-8",
    )
    (recording_dir / "index.md").write_text(
        "---\nid: demo\ntitle: Demo\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "omegaflow.studio",
            f"project_root={tmp_path}",
            "action=list",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Available recording scripts:\n  demo\n" in result.stdout


def test_recordings_config_rejects_identity_fields(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "config.yaml").write_text(
        "title: Shared Title\n",
        encoding="utf-8",
    )
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    try:
        recording_from_script("hello")
    except StudioConfigError as exc:
        assert "cannot define recording identity fields: title" in str(exc)
    else:
        raise AssertionError("expected shared recording config identity to fail")


def test_shared_output_dir_derives_per_recording_asset_dirs(
    tmp_path, monkeypatch
) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "config.yaml").write_text(
        """
outputs:
  dir: site/videos
audio:
  enabled: false
""".lstrip(),
        encoding="utf-8",
    )
    for recording_id in ("alpha", "beta"):
        recording_dir = recordings_dir / recording_id
        recording_dir.mkdir()
        (recording_dir / "index.md").write_text(
            f"""
---
id: {recording_id}
title: {recording_id.title()}
---

```yaml studio-directive
scene: {recording_id.title()}
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
            encoding="utf-8",
        )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    alpha = recording_from_script("alpha")
    beta = recording_from_script("beta")
    alpha["_recording_id"] = "alpha"
    beta["_recording_id"] = "beta"

    assert alpha["outputs"]["asset_dir"] == "site/videos/alpha"
    assert beta["outputs"]["asset_dir"] == "site/videos/beta"
    assert studio.presentation_build.public_bundle_dir(alpha) == (
        Path.cwd() / "site/videos/alpha/presentation"
    )
    assert studio.presentation_build.public_bundle_dir(beta) == (
        Path.cwd() / "site/videos/beta/presentation"
    )
    assert studio.presentation_build.public_bundle_dir(
        alpha
    ) != studio.presentation_build.public_bundle_dir(beta)


def test_recording_schema_rejects_unknown_nested_config(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
capture:
  typo_window_size: 80x20
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    try:
        recording_from_script("hello")
    except StudioConfigError as exc:
        assert "typo_window_size" in str(exc)
    else:
        raise AssertionError("expected unknown nested recording config to fail")


def test_recording_schema_rejects_old_top_level_retime_config(
    tmp_path, monkeypatch
) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
retime:
  post_command_pause: 0.1
---

```yaml studio-directive
scene: Hello Video
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    try:
        recording_from_script("hello")
    except StudioConfigError as exc:
        assert "retime" in str(exc)
    else:
        raise AssertionError("expected old top-level retime config to fail")


def test_recording_schema_validates_frontmatter_command_fields(
    tmp_path, monkeypatch
) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
beats:
- id: configured
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - id: say-hello
      run: printf 'hello\\n'
      display: echo hello
      timing: realtime
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: narrated
  heading: Narrated
  narration: Narration text.
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    spec = recording_from_script("hello")

    configured = next(beat for beat in spec["beats"] if beat["id"] == "configured")
    command = configured["actions"][0]["commands"][0]
    assert command["run"] == "printf 'hello\\n'"
    assert command["display"] == "echo hello"
    assert command["timing"] == "realtime"


def test_recording_schema_rejects_unknown_command_field(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
beats:
- id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - run: printf 'hello\\n'
      disaply: echo hello
---
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    try:
        recording_from_script("hello")
    except StudioConfigError as exc:
        assert "disaply" in str(exc)
    else:
        raise AssertionError("expected unknown command field to fail")


def test_recording_schema_rejects_old_command_retime_field(
    tmp_path, monkeypatch
) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
id: hello
beats:
- id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - run: printf 'hello\\n'
      retime: realtime
---
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    try:
        recording_from_script("hello")
    except StudioConfigError as exc:
        assert "retime" in str(exc)
    else:
        raise AssertionError("expected old command retime field to fail")


def test_studio_directive_schema_rejects_unknown_top_level_key() -> None:
    script = """
```yaml studio-directive
wat: true
```
""".lstrip()

    try:
        studio_directive_blocks(script)
    except StudioConfigError as exc:
        assert "Key 'wat' not in 'StudioDirectiveBlock'" in str(exc)
    else:
        raise AssertionError("expected unknown directive key to fail")


def test_studio_directive_schema_rejects_unknown_nested_key() -> None:
    script = """
```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  surprise: nope
```
""".lstrip()

    try:
        studio_directive_blocks(script)
    except StudioConfigError as exc:
        assert "Key 'surprise' not in 'StudioDirectiveBeat'" in str(exc)
    else:
        raise AssertionError("expected unknown beat key to fail")


def test_studio_directive_schema_rejects_unknown_action_payload_key() -> None:
    script = """
```yaml studio-directive
beat:
  id: browser
  medium: browser
  heading: Open page
  narration: Open the player.
  actions:
  - id: open
    open_page:
      url: /
      typo_loading: show
```
""".lstrip()

    with pytest.raises(StudioConfigError, match="typo_loading"):
        studio_directive_blocks(script)


@pytest.mark.parametrize("generated_field", ["script", "narration", "studio"])
def test_recording_frontmatter_rejects_non_user_fields(
    tmp_path: Path,
    monkeypatch,
    generated_field: str,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        f"""
---
id: hello
{generated_field}: {{}}
---

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(StudioConfigError, match=generated_field):
        recording_from_script("hello")


def test_studio_directive_schema_does_not_inject_defaults() -> None:
    script = """
```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - run: printf 'hello\\n'
```
""".lstrip()

    block = studio_directive_blocks(script)[0]
    command = block["beat"]["actions"][0]["commands"][0]

    assert command == {"run": "printf 'hello\\n'"}


def test_quickstart_demo_uses_one_cross_medium_take_and_finishes_nested_player() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "recordings"
        / "quickstart-demo"
        / "index.md"
    ).read_text(encoding="utf-8")
    spec = recording_from_script(
        "quickstart-demo",
        recording_dir=Path(__file__).resolve().parents[1] / "recordings",
    )
    beats = [
        block["beat"]
        for block in studio_directive_blocks(source)
        if "beat" in block
    ]
    beats_by_id = {beat["id"]: beat for beat in beats}
    install_command = beats_by_id["install"]["actions"][0]["commands"][0]
    build_commands = beats_by_id["build"]["actions"][0]["commands"]
    browser_beat = beats_by_id["play-in-browser"]
    bootstrap_beat = beats_by_id["bootstrap"]
    actions = {action["id"]: action for action in browser_beat["actions"]}

    assert beats[0]["id"] == "introduction"
    assert (
        "With OmegaFlow, you can turn scripted terminal and browser workflows "
        "into narrated,"
        in beats_by_id["introduction"]["narration"]
    )
    assert (
        "These videos are organized into beats"
        in beats_by_id["introduction"]["narration"]
    )
    assert (
        "The demo runs in @guided_mode_start@ guided mode"
        in beats_by_id["introduction"]["narration"]
    )
    assert "pauses after each beat" in beats_by_id["introduction"]["narration"]
    assert "turn off Guided mode" in beats_by_id["introduction"]["narration"]
    assert beats_by_id["introduction"]["guide"] == {
        "summary": "Guided mode pauses after each beat.",
        "success_hint": "Continue when you are ready to install OmegaFlow."
    }
    assert beats_by_id["introduction"]["player"] == {
        "highlight": {"control": "guided", "start": "@guided_mode_start@"}
    }
    assert spec["setup"] == [
        {
            "run": None,
            "run_file": "scripts/setup-demo-environment.sh",
            "display": None,
            "after": None,
            "output": None,
            "expect": {
                "exit_code": 0,
                "output_contains": [],
                "output_regex": [],
                "file_exists": [],
            },
            "name": "prepare isolated demo environment",
            "progress": [],
            "commands": None,
        }
    ]
    assert install_command["run"] == (
        '"$HOMEPAGE_DEMO_VENV/bin/python" -m pip install '
        "--disable-pip-version-check --no-build-isolation --no-deps "
        '--editable "$HOMEPAGE_DEMO_REPO_ROOT"'
    )
    assert install_command["display"] == "python -m pip install omegaflow"
    assert install_command["output"] == {
        "replace": "Successfully installed omegaflow\n"
    }
    assert beats_by_id["install"]["narration"].startswith("Start by")
    assert "narration_take" not in beats_by_id["install"]
    assert "narration_take" not in beats_by_id["bootstrap"]
    assert all(
        not beats_by_id[beat_id]["narration"].startswith("OmegaFlow")
        for beat_id in ("bootstrap", "build")
    )
    assert "build next" not in beats_by_id["bootstrap"]["narration"]
    assert "This is a one-time setup" in bootstrap_beat["narration"]
    assert (
        "commit the generated files to version control"
        in bootstrap_beat["narration"]
    )
    assert "From your repository root, @bootstrap@ run" in bootstrap_beat["narration"]
    assert (
        "@project_settings_start@ project settings, @project_settings_end@"
        in bootstrap_beat["narration"]
    )
    assert (
        "@recording_defaults_start@ recording defaults, @recording_defaults_end@"
        in bootstrap_beat["narration"]
    )
    assert (
        "@quickstart_script_start@ a quickstart video script you can run immediately. "
        "@quickstart_script_end@"
        in bootstrap_beat["narration"]
    )
    assert bootstrap_beat["effects"] == [
        {
            "highlight": {
                "text": ".omegaflow/config.yaml",
                "start": "@project_settings_start@",
                "end": "@project_settings_end@",
            }
        },
        {
            "highlight": {
                "text": "recordings/config.yaml",
                "start": "@recording_defaults_start@",
                "end": "@recording_defaults_end@",
            }
        },
        {
            "highlight": {
                "text": "recordings/quickstart/index.md",
                "start": "@quickstart_script_start@",
                "end": "@quickstart_script_end@",
            }
        },
    ]
    assert beats_by_id["build"]["narration_take"] == "build-and-browser"
    assert beats_by_id["build"]["guide"]["commands"] == [
        "omegaflow recording=quickstart action=build",
        "omegaflow recording=quickstart action=watch",
    ]
    assert beats_by_id["install"]["guide"]["success_hint"] == (
        "OmegaFlow is installed and the omegaflow command is available."
    )
    assert beats_by_id["bootstrap"]["guide"]["success_hint"] == (
        "The recording workspace contains project settings, recording defaults, "
        "and the quickstart script."
    )
    assert beats_by_id["build"]["heading"] == "Build the Video"
    assert [command["id"] for command in build_commands] == [
        "build_command",
        "watch_command",
    ]
    assert bootstrap_beat["actions"][0]["commands"][0]["run"] == (
        'cd "$HOMEPAGE_DEMO_ROOT" && '
        'omegaflow project_root="$HOMEPAGE_DEMO_ROOT" action=bootstrap'
    )
    assert build_commands[0]["run"] == (
        "omegaflow recording=quickstart action=build force=true"
    )
    assert build_commands[0]["timing"] == "realtime"
    assert "follow_along" not in build_commands[0]
    assert build_commands[1]["display"] == (
        "omegaflow recording=quickstart action=watch"
    )
    assert build_commands[1]["after"] == "@watch@"
    assert build_commands[1]["browser_handoff"] is True
    assert build_commands[1]["timing"] == "realtime"
    assert "follow_along" not in build_commands[1]
    assert build_commands[1]["show_prompt_after"] is False
    assert build_commands[1]["run"] == (
        "omegaflow recording=quickstart action=watch watch_port=43123 "
        "autoplay=false"
    )
    assert build_commands[1].get("output") is None
    assert browser_beat["narration_take"] == "build-and-browser"
    assert browser_beat["heading"] == "Explore the Player"
    assert browser_beat["guide"] == {
        "summary": "This beat demonstrated beat previews and playback speed.",
        "success_hint": "To learn more, start the tutorial or read the docs.",
    }
    assert browser_beat["pointer"] == {"visible": False}
    assert "player" not in browser_beat
    assert browser_beat["narration"].startswith(
        "@open_player@ OmegaFlow scripts and records browser workflows"
    )
    assert "this script explores its player" in browser_beat["narration"]
    assert "OmegaFlow divides every video into beats" not in browser_beat["narration"]
    assert "@navigate_section@ First Video Beat" in browser_beat["narration"]
    assert "@playback_section@ Second Video Beat" in browser_beat["narration"]
    assert "Hover over either beat in the timeline" in browser_beat["narration"]
    assert all("two-section" not in beat["narration"] for beat in beats)
    assert "the watch command opens" in browser_beat["narration"]
    assert "A single OmegaFlow video" not in browser_beat["narration"]
    assert "one narration take" not in browser_beat["narration"]
    assert "@play_video@" not in browser_beat["narration"]
    assert "@wait:wait_for_playback" not in browser_beat["narration"]
    assert spec["browser"]["viewport"]["width"] == 1152
    assert spec["browser"]["viewport"]["height"] == 360
    assert spec["presentation"]["guided"] is True
    assert list(actions) == [
        "open_player",
        "show_pointer",
        "preview_navigation_section",
        "preview_playback_section",
        "point_at_speed",
        "increase_speed",
        "restore_speed",
        "hide_pointer",
    ]
    assert actions["open_player"]["open_page"]["handoff"] == "watch_command"
    assert actions["open_player"]["open_page"]["display_url"] == "$handoff"
    assert actions["open_player"]["hold_before_ms"] == 350
    speed_target = {"role": "button", "name": "Playback speed"}
    assert actions["show_pointer"]["set_pointer"] == {"visible": True}
    assert actions["show_pointer"]["after"] == "@show_pointer@"
    assert actions["preview_navigation_section"]["move_pointer"]["target"] == {
        "test_id": "section-region-first-video-beat"
    }
    assert actions["preview_navigation_section"]["move_pointer"]["position"] == {
        "x": 0.5,
        "y": 0.5,
    }
    assert actions["preview_navigation_section"]["after"] == "@navigate_section@"
    assert actions["preview_playback_section"]["move_pointer"]["target"] == {
        "test_id": "section-region-second-video-beat"
    }
    assert actions["preview_playback_section"]["move_pointer"]["position"] == {
        "x": 0.5,
        "y": 0.5,
    }
    assert actions["preview_playback_section"]["after"] == "@playback_section@"
    assert actions["point_at_speed"]["move_pointer"]["target"] == speed_target
    assert actions["point_at_speed"]["after"] == "@point_at_speed@"
    assert actions["increase_speed"]["click"]["target"] == speed_target
    assert actions["increase_speed"]["after"] == "@playback_speed_start@"
    assert actions["restore_speed"]["click"] == {
        "target": speed_target,
        "button": "right",
    }
    assert actions["restore_speed"]["after"] == "@playback_speed_end@"
    assert actions["hide_pointer"]["set_pointer"] == {"visible": False}
    assert actions["hide_pointer"].get("after") is None

    generated = studio.bootstrap_recording_text("quickstart", "Quickstart")
    assert "kind: video" in generated
    generated_beats = [
        block["beat"]
        for block in studio_directive_blocks(generated)
        if "beat" in block
    ]
    assert [beat["id"] for beat in generated_beats] == [
        "first-video-beat",
        "second-video-beat",
    ]
    assert generated_beats[0]["heading"] == "First Video Beat"
    assert generated_beats[0]["narration"] == (
        "This is the first beat in the generated quickstart video."
    )
    assert generated_beats[0]["viewer_hold"] == 3
    assert generated_beats[1]["heading"] == "Second Video Beat"
    assert generated_beats[1]["narration"] == (
        "This is the second beat in the generated quickstart video."
    )
    assert generated_beats[1]["viewer_hold"] == 4
    assert generated_beats[0]["actions"][0]["commands"][0] == {
        "id": "show_first_beat",
        "run": "# First video beat",
    }
    assert generated_beats[1]["actions"][0]["commands"][0] == {
        "id": "show_second_beat",
        "run": "# Second video beat",
    }


def test_quickstart_demo_installs_local_checkout_in_isolated_environment(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    recording = recording_from_script(
        "quickstart-demo",
        recording_dir=root / "recordings",
    )
    install_beat = next(
        beat for beat in recording["beats"] if beat["id"] == "install"
    )
    install_command = install_beat["actions"][0]["commands"][0]["run"]
    plan = studio.normalized_recording_plan(
        {
            "id": "quickstart-demo-install-smoke",
            "_script_dir": recording["_script_dir"],
            "setup": recording["setup"],
            "beats": [
                {
                    "id": "install",
                    "actions": [
                        {
                            "commands": [
                                {
                                    "run": (
                                        "if \"$HOMEPAGE_DEMO_VENV/bin/python\" "
                                        "-c 'import omegaflow' 2>/dev/null; then "
                                        "exit 91; fi"
                                    )
                                },
                                {"run": install_command},
                                {
                                    "run": (
                                        "\"$HOMEPAGE_DEMO_VENV/bin/python\" -c '"
                                        "import os, pathlib, omegaflow; "
                                        "root = pathlib.Path(os.environ[\"OMEGAFLOW_TEST_ROOT\"]); "
                                        "assert pathlib.Path(omegaflow.__file__).resolve()."
                                        "is_relative_to(root / \"src\")'"
                                    )
                                },
                                {"run": "omegaflow --help >/dev/null"},
                            ]
                        }
                    ],
                }
            ],
            "cleanup": recording["cleanup"],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False,
            timeout_seconds=60.0,
        )
    )

    coordinator.capture(
        plan,
        tmp_path / "run",
        workspace=root,
        working_directory=root,
        environment={
            "OMEGAFLOW_TEST_ROOT": str(root),
            "PATH": os.environ.get("PATH", ""),
        },
    )

    assert not list(
        (tmp_path / "run" / ".tmp").glob("omegaflow-quickstart-env.*")
    )
    assert not list(
        (tmp_path / "run" / ".tmp").glob("omegaflow-quickstart-demo.*")
    )


def test_run_file_dependencies_affect_capture_fingerprint(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    support_dir = recordings_dir / "hello"
    support_dir.mkdir(parents=True)
    setup_script = support_dir / "setup.sh"
    action_script = support_dir / "action.sh"
    setup_script.write_text("echo setup from recording script dir\n", encoding="utf-8")
    action_script.write_text("echo action from recording script dir\n", encoding="utf-8")
    spec = {
        "id": "hello",
        "_recording_id": "hello",
        "_script_dir": str(recordings_dir),
        "_hydra_output_dir": str(tmp_path / "runs" / "hello"),
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False, "typing": False},
        "capture": {},
        "setup": [{"run_file": "hello/setup.sh"}],
        "beats": [
            {
                "id": "hello",
                "actions": [
                    {
                        "commands": [
                            {
                                "run_file": "hello/action.sh",
                                "display": "bash hello/action.sh",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    plan = studio.normalized_recording_plan(spec)
    before = studio.presentation_build.artifact_fingerprints(spec, plan)
    action_script.write_text("echo changed\n", encoding="utf-8")
    after = studio.presentation_build.artifact_fingerprints(spec, plan)

    assert before.capture_fingerprint != after.capture_fingerprint


def test_bootstrap_creates_recording_workspace(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "recording": "demo-recording",
            "force": False,
        }
    )

    assert status == 0
    tool_config = (tmp_path / ".omegaflow" / "config.yaml").read_text(
        encoding="utf-8"
    )
    shared_config = (workspace / "config.yaml").read_text(encoding="utf-8")
    recording = (workspace / "demo-recording" / "index.md").read_text(
        encoding="utf-8"
    )
    support_dir = workspace / "demo-recording" / "scripts"

    assert "studio:" in tool_config
    assert "recording_dir: recordings" in tool_config
    assert "data_dir: recordings/.omegaflow" in tool_config
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(None, ())
    assert config["studio"]["recording_dir"] == "recordings"
    assert config["studio"]["data_dir"] == "recordings/.omegaflow"
    assert config["studio"]["run_gc"] == {
        "enabled": True,
        "max_age_days": 30,
        "max_runs_per_recording": 10,
        "preserve_latest_failure": True,
    }
    assert "id:" not in shared_config
    assert "title:" not in shared_config
    assert "id: demo-recording" in recording
    assert "type: standalone_html" in recording
    assert "cast:" not in recording
    assert "file: ${outputs.asset_dir}/index.html" in recording
    assert "This Markdown file is the source for one generated terminal video." in recording
    assert "fenced `studio-directive` blocks tell" in recording
    assert "id: first-video-beat" in recording
    assert 'run: "# First video beat"' in recording
    assert "id: second-video-beat" in recording
    assert 'run: "# Second video beat"' in recording
    assert "follow_along" not in recording
    assert "@run_demo@" in recording
    assert "@wait:show_message@" in recording
    assert "viewer_hold: 3" in recording
    assert "viewer_hold: 4" in recording
    assert not support_dir.exists()


def test_bootstrap_default_recording_is_quickstart(tmp_path, capsys) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "force": False,
        }
    )
    output = capsys.readouterr().out

    assert status == 0
    assert "next    omegaflow recording=quickstart action=build\n" in output
    recording = (workspace / "quickstart" / "index.md").read_text(
        encoding="utf-8"
    )
    support_dir = workspace / "quickstart" / "scripts"

    assert "id: quickstart" in recording
    assert "title: Quickstart" in recording
    assert "heading: First Video Beat" in recording
    assert "heading: Second Video Beat" in recording
    assert not support_dir.exists()


def test_bootstrap_dry_run_does_not_write(tmp_path, capsys) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "dry_run": True,
            "force": False,
        }
    )

    output = capsys.readouterr().out

    assert status == 0
    assert "Bootstrap dry run: quickstart" in output
    assert "Recording workspace:" in output
    assert "Files:" in output
    assert "create" in output
    assert ".omegaflow/config.yaml" in output
    assert "recordings/config.yaml" in output
    assert "recordings/quickstart/index.md" in output
    assert "recordings/quickstart/scripts/hello.sh" not in output
    assert "No files were written." in output
    assert not (tmp_path / ".omegaflow").exists()
    assert not workspace.exists()


def test_bootstrap_dry_run_diff_does_not_write(tmp_path, capsys) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "dry_run": "diff",
            "force": False,
        }
    )

    output = capsys.readouterr().out

    assert status == 0
    assert "Bootstrap dry run diff: quickstart" in output
    assert "--- /dev/null" in output
    assert f"+++ {tmp_path}/.omegaflow/config.yaml" in output
    assert "+studio:" in output
    assert "+  recording_dir: recordings" in output
    assert "+  data_dir: recordings/.omegaflow" in output
    assert "+id: quickstart" in output
    assert '+      run: "# First video beat"' in output
    assert '+      run: "# Second video beat"' in output
    assert "No files were written." in output
    assert not (tmp_path / ".omegaflow").exists()
    assert not workspace.exists()


def test_bootstrap_dry_run_diff_uses_color_when_enabled(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "dry_run": "diff",
            "force": False,
        }
    )

    output = capsys.readouterr().out

    assert status == 0
    assert "\033[33;1m+++ " in output
    assert "\033[32;1m+studio:" in output
    assert "\033[36;1m@@ " in output
    assert not (tmp_path / ".omegaflow").exists()
    assert not workspace.exists()


def test_bootstrap_dry_run_rejects_unknown_mode(tmp_path) -> None:
    try:
        studio.run_bootstrap(
            {
                "workspace": str(tmp_path / "recordings"),
                "dry_run": "verbose",
            }
        )
    except studio.StudioError as exc:
        assert "bootstrap dry_run must be true, false, or diff" in str(exc)
    else:
        raise AssertionError("expected unknown bootstrap dry_run mode to fail")


def test_bootstrap_creates_nested_recording_workspace(tmp_path) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "recording": "tutorial/recording-file",
            "force": False,
        }
    )

    assert status == 0
    recording = (
        workspace / "tutorial" / "recording-file" / "index.md"
    ).read_text(encoding="utf-8")
    support_dir = workspace / "tutorial" / "recording-file" / "scripts"

    assert "id: tutorial/recording-file" in recording
    assert "title: Recording File" in recording
    assert "heading: First Video Beat" in recording
    assert "heading: Second Video Beat" in recording
    assert not support_dir.exists()


def test_success_followups_show_user_facing_actions(capsys) -> None:
    cfg = OmegaConf.create(
        {
            "recording": "quickstart-demo",
            "output_format": "text",
        }
    )

    studio.print_success_followups(cfg)

    output = capsys.readouterr().out
    assert output.splitlines() == [
        "watch  omegaflow recording=quickstart-demo action=watch"
    ]
    assert "action=play" not in output
    assert "action=inspect" not in output


def test_play_is_not_a_public_action() -> None:
    assert "play" not in studio.PUBLIC_ACTIONS

    with pytest.raises(studio.StudioError, match="unknown action: play") as exc_info:
        studio.validate_action("play")

    help_line = str(exc_info.value).splitlines()[1]
    assert help_line.startswith("user-facing actions:")
    assert "play" not in help_line
    assert "watch" in help_line


def minimal_recording_spec(run_dir, *, data_dir: Path | None = None) -> dict[str, object]:
    config: dict[str, object] = {}
    if data_dir is not None:
        config["studio"] = {"data_dir": str(data_dir)}
    return {
        "id": "demo",
        "_recording_id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_studio_config": config,
        "outputs": {"asset_dir": "website/static/videos/demo"},
        "audio": {
            "enabled": False,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
        },
    }


def test_current_recording_run_dir_uses_hydra_output_dir(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "demo" / "2026-07-14_12-00-00"

    assert studio.current_recording_run_dir(minimal_recording_spec(run_dir)) == run_dir


def test_run_gc_removes_runs_older_than_max_age_and_protects_current(
    tmp_path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "media"
    runs_dir = data_dir / "runs" / "demo"
    run_dirs = [runs_dir / f"20260705-01020{index}" for index in range(6)]
    for run_dir in run_dirs:
        run_dir.mkdir(parents=True)
    now = 2_000_000_000.0
    monkeypatch.setattr(studio.time, "time", lambda: now)
    for index, run_dir in enumerate(run_dirs):
        artifact = "recording.fingerprint.json" if index < 3 else "failure.json"
        (run_dir / artifact).write_text("{}\n", encoding="utf-8")
        age_days = 31 if index in {0, 1, 3} else 29
        os.utime(run_dir, (now - age_days * 86400,) * 2)
    current = run_dirs[0]
    spec = minimal_recording_spec(current, data_dir=data_dir)

    removed = studio.garbage_collect_recording_runs(spec, current_run_dir=current)

    assert removed == [run_dirs[1], run_dirs[3]]
    assert current.is_dir()
    assert run_dirs[2].is_dir()
    assert run_dirs[4].is_dir()
    assert run_dirs[5].is_dir()
    assert "run gc: removed 2 run(s)" in capsys.readouterr().out


def test_run_gc_count_limit_protects_current_and_latest_failure(
    tmp_path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "media"
    runs_dir = data_dir / "runs" / "demo"
    run_dirs = [runs_dir / f"20260705-01020{index}" for index in range(6)]
    for index, run_dir in enumerate(run_dirs):
        run_dir.mkdir(parents=True)
        artifact = "failure.json" if index in {1, 3} else "recording.fingerprint.json"
        (run_dir / artifact).write_text("{}\n", encoding="utf-8")
    now = 2_000_000_000.0
    monkeypatch.setattr(studio.time, "time", lambda: now)
    for index, run_dir in enumerate(run_dirs):
        os.utime(run_dir, (now - (6 - index) * 60,) * 2)
    current = run_dirs[0]
    spec = minimal_recording_spec(current, data_dir=data_dir)
    spec["_studio_config"]["studio"]["run_gc"] = {
        "max_age_days": 30,
        "max_runs_per_recording": 3,
        "preserve_latest_failure": True,
    }

    assert studio.garbage_collect_recording_runs(
        spec, current_run_dir=current
    ) == [run_dirs[1], run_dirs[2], run_dirs[4]]
    assert current.is_dir()
    assert not run_dirs[1].exists()
    assert not run_dirs[2].exists()
    assert run_dirs[3].is_dir()
    assert not run_dirs[4].exists()
    assert run_dirs[5].is_dir()
    assert "run gc: removed 3 run(s)" in capsys.readouterr().out


def test_gc_action_dry_run_previews_count_cleanup_without_removing(
    tmp_path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "media"
    runs_dir = data_dir / "runs" / "demo"
    run_dirs = [runs_dir / f"20260705-01020{index}" for index in range(4)]
    for run_dir in run_dirs:
        run_dir.mkdir(parents=True)
        (run_dir / "recording.fingerprint.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(
        None,
        (
            "action=gc",
            "dry_run=true",
            f"studio.data_dir={data_dir}",
            "studio.run_gc.max_runs_per_recording=2",
        ),
    )

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert all(run_dir.is_dir() for run_dir in run_dirs)
    output = capsys.readouterr().out
    assert "run gc would remove" in output
    assert "run gc: would remove 2 run(s) (dry run)" in output


@pytest.mark.parametrize("recording", ["../../victim", "/tmp/victim"])
def test_gc_action_rejects_recording_paths_outside_runs_root(
    tmp_path, monkeypatch, recording
) -> None:
    data_dir = tmp_path / "media"
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(
        None,
        (
            "action=gc",
            "dry_run=true",
            f"studio.data_dir={data_dir}",
            f"recording={recording}",
        ),
    )

    with pytest.raises(
        studio.StudioError,
        match="recording must resolve inside the configured runs directory",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))


def test_gc_action_rejects_recording_symlink_outside_runs_root(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "media"
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    try:
        (runs_dir / "linked").symlink_to(victim, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(
        None,
        (
            "action=gc",
            "dry_run=true",
            f"studio.data_dir={data_dir}",
            "recording=linked",
        ),
    )

    with pytest.raises(
        studio.StudioError,
        match="recording must resolve inside the configured runs directory",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))


def test_run_gc_can_be_disabled(tmp_path) -> None:
    data_dir = tmp_path / "media"
    old_run = data_dir / "runs" / "demo" / "20260705-010201"
    old_run.mkdir(parents=True)
    spec = minimal_recording_spec(old_run, data_dir=data_dir)
    spec["_studio_config"]["studio"]["run_gc"] = {"enabled": False}

    assert studio.garbage_collect_recording_runs(spec, current_run_dir=old_run) == []
    assert old_run.is_dir()


@pytest.mark.parametrize(
    ("run_gc", "message"),
    [
        (
            {"max_runs_per_recording": 0},
            "max_runs_per_recording must be a positive integer",
        ),
        (
            {"preserve_latest_failure": "yes"},
            "preserve_latest_failure must be a boolean",
        ),
    ],
)
def test_run_gc_rejects_invalid_count_policy(
    tmp_path, run_gc, message
) -> None:
    current = tmp_path / "media" / "runs" / "demo" / "20260705-010202"
    current.mkdir(parents=True)
    spec = minimal_recording_spec(current, data_dir=tmp_path / "media")
    spec["_studio_config"]["studio"]["run_gc"] = run_gc

    with pytest.raises(studio.StudioError, match=message):
        studio.garbage_collect_recording_runs(spec, current_run_dir=current)


def test_run_gc_can_suppress_reporting(tmp_path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "media"
    old_run = data_dir / "runs" / "demo" / "20260705-010201"
    current = data_dir / "runs" / "demo" / "20260705-010202"
    for run_dir in [old_run, current]:
        run_dir.mkdir(parents=True)
    now = 2_000_000_000.0
    monkeypatch.setattr(studio.time, "time", lambda: now)
    os.utime(old_run, (now - 31 * 86400,) * 2)
    spec = minimal_recording_spec(current, data_dir=data_dir)

    studio.garbage_collect_recording_runs(
        spec, current_run_dir=current, report=False
    )

    assert not old_run.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_gc_deletion_failure_is_non_fatal(tmp_path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "media"
    old_run = data_dir / "runs" / "demo" / "20260705-010201"
    current = data_dir / "runs" / "demo" / "20260705-010202"
    for run_dir in [old_run, current]:
        run_dir.mkdir(parents=True)
    now = 2_000_000_000.0
    monkeypatch.setattr(studio.time, "time", lambda: now)
    monkeypatch.setattr(
        studio.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )
    os.utime(old_run, (now - 31 * 86400,) * 2)
    spec = minimal_recording_spec(current, data_dir=data_dir)

    studio.garbage_collect_recording_runs(spec, current_run_dir=current)

    assert old_run.is_dir()
    captured = capsys.readouterr()
    assert "could not remove" in captured.err
    assert "removed 0 run(s)" in captured.out


def test_build_publish_surface_names_are_config_driven() -> None:
    spec = {
        "publish": {
            "default": "docs",
            "on_build": True,
            "build_surfaces": ["docs", "standalone"],
            "surfaces": {"docs": {}, "standalone": {}},
        }
    }

    assert studio.build_publish_surface_names({}, spec) == ["docs", "standalone"]
    assert studio.build_publish_surface_names({"surface": "docs"}, spec) == ["docs"]


def test_build_publish_surface_names_can_disable_build_publish() -> None:
    spec = {
        "publish": {
            "default": "docs",
            "on_build": False,
            "surfaces": {"docs": {}},
        }
    }

    assert studio.build_publish_surface_names({}, spec) == []


def test_run_publish_surface_reports_the_target_as_unchanged_when_up_to_date(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "quick-start.md"
    target.write_text(
        "<!-- studio:demo:start -->\nold\n<!-- studio:demo:end -->\n",
        encoding="utf-8",
    )
    spec = {
        "publish": {
            "default": "docs",
            "surfaces": {
                "docs": {
                    "type": "docusaurus_mdx",
                    "file": str(target),
                    "placeholder": "demo",
                }
            },
        }
    }
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio, "publish_surface", lambda *_args, **_kwargs: None)

    result = studio.run_publish_surface(
        OmegaConf.create({"recording": "demo", "output_format": "text"}),
        surface_name="docs",
        report=False,
    )

    assert result == studio.PublishSurfaceOutcome(path=target, updated=False)


def test_run_publish_surface_reports_docusaurus_rebuild_requirement(
    tmp_path, monkeypatch, capsys
) -> None:
    target = tmp_path / "quick-start.md"
    target.write_text(
        "<!-- studio:demo:start -->\nold\n<!-- studio:demo:end -->\n",
        encoding="utf-8",
    )
    spec = {
        "publish": {
            "default": "docs",
            "surfaces": {
                "docs": {
                    "type": "docusaurus_mdx",
                    "file": str(target),
                    "placeholder": "demo",
                }
            },
        }
    }
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(
        studio,
        "publish_surface",
        lambda *_args, **_kwargs: target,
    )

    result = studio.run_publish_surface(
        OmegaConf.create({"recording": "demo", "output_format": "text"}),
        surface_name="docs",
    )

    assert result == studio.PublishSurfaceOutcome(path=target, updated=True)
    assert (
        capsys.readouterr().out
        == "publish  docs (Docusaurus): updated — rebuild required\n"
    )


def test_publish_surface_display_name_avoids_repeating_the_surface_type() -> None:
    assert (
        studio.publish_surface_display_name("docusaurus", "docusaurus_mdx")
        == "Docusaurus"
    )
    assert (
        studio.publish_surface_display_name("docs", "docusaurus_mdx")
        == "docs (Docusaurus)"
    )


def test_publish_surface_summary_colors_surface_outcome_and_path(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    path = tmp_path / "quick-start.md"

    studio.print_publish_surfaces(
        OmegaConf.create({"output_format": "text"}),
        [
            (
                "Docusaurus",
                studio.PublishSurfaceOutcome(path=path, updated=True),
                True,
            ),
            (
                "Standalone HTML",
                studio.PublishSurfaceOutcome(path=path, updated=False),
                False,
            ),
        ],
    )

    output = capsys.readouterr().out
    assert "\033[36;1mDocusaurus\033[0m" in output
    assert "\033[32;1mupdated\033[0m" in output
    assert "\033[33;1mrebuild required\033[0m" in output
    assert "\033[33;1munchanged\033[0m" in output
    assert f"\033[2m{path}\033[0m" in output


def test_watch_player_url_path_allows_silent_terminal_recordings(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "hello"
    bundle = run_dir / "presentation"
    beat = bundle / "beats" / "terminal.cast"
    beat.parent.mkdir(parents=True)
    manifest = bundle / "recording.presentation.json"
    manifest.write_text("{}\n", encoding="utf-8")
    beat.write_text('{"version": 3}\n', encoding="utf-8")
    spec = {
        "_recording_id": "hello",
        "title": "Hello",
        "audio": {
            "enabled": False,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
        },
    }
    url_path, artifacts = studio.watch_player_url_path(spec, run_dir=run_dir)
    countdown_url, _ = studio.watch_player_url_path(
        spec,
        run_dir=run_dir,
        autoplay_countdown=True,
    )

    assert "manifest=" in url_path
    assert "cast=" not in url_path
    assert "autoplay=" not in url_path
    assert "autoplay=countdown" in countdown_url
    assert artifacts == {
        "beats/terminal.cast": beat.resolve(),
        "recording.presentation.json": manifest.resolve(),
    }


def test_watch_player_url_path_falls_back_to_public_bundle(
    tmp_path, monkeypatch
) -> None:
    run_dir = tmp_path / "runs" / "hello"
    run_dir.mkdir(parents=True)
    bundle = tmp_path / "public" / "presentation"
    beat = bundle / "beats" / "terminal.cast"
    beat.parent.mkdir(parents=True)
    manifest = bundle / "recording.presentation.json"
    manifest.write_text("{}\n", encoding="utf-8")
    beat.write_text('{"version": 3}\n', encoding="utf-8")
    spec = {"_recording_id": "hello"}
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: run_dir,
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "public_bundle_dir",
        lambda _spec: bundle,
    )

    _url_path, artifacts = studio.watch_player_url_path(spec)

    assert artifacts == {
        "beats/terminal.cast": beat.resolve(),
        "recording.presentation.json": manifest.resolve(),
    }


def test_render_collection_watch_page_escapes_metadata_and_links_to_players() -> None:
    page = studio.render_collection_watch_page(
        {"id": "tutorial", "title": "Tutorial <Videos>"},
        [
            {
                "id": "tutorial/beat",
                "title": "Beats & narration",
                "description": "See how <actions> form a beat.",
                "url": "/watch/tutorial/beat/?autoplay=countdown",
            }
        ],
    )

    assert "Tutorial &lt;Videos&gt;" in page
    assert "Beats &amp; narration" in page
    assert "See how &lt;actions&gt; form a beat." in page
    assert 'href="/watch/tutorial/beat/?autoplay=countdown"' in page
    assert 'id="video-search"' in page
    assert 'data-search="tutorial/beat beats &amp; narration see how ' in page
    assert 'class="video-list"' in page
    assert 'id="empty-state"' in page
    assert "card.hidden = !matches" in page
    assert "1 video" in page


def test_collection_watch_page_renders_compact_ordered_rows_for_large_collections() -> None:
    members = [
        {
            "id": f"tutorial/chapter-{index}",
            "title": f"Chapter {index}",
            "description": f"Learn topic {index}.",
            "url": f"/watch/{index}",
        }
        for index in range(1, 16)
    ]

    page = studio.render_collection_watch_page(
        {"id": "tutorial", "title": "Tutorial"},
        members,
    )

    assert page.count('data-video-card="true"') == 15
    assert '<span class="video-number" aria-hidden="true">01</span>' in page
    assert '<span class="video-number" aria-hidden="true">15</span>' in page
    assert "15 videos" in page
    assert "overflow: auto" in page
    assert "Watch video" not in page


def test_collection_watch_routes_recording_members(monkeypatch) -> None:
    cfg = OmegaConf.create({"recording": "tutorial"})
    collection = {
        "kind": "collection",
        "id": "tutorial",
        "title": "Tutorial",
        "members": ["tutorial/recording-file", "tutorial/beat"],
    }
    member_cfgs = [
        OmegaConf.create({"recording": "tutorial/recording-file"}),
        OmegaConf.create({"recording": "tutorial/beat"}),
    ]
    monkeypatch.setattr(
        studio,
        "load_collection_build",
        lambda _cfg, _config: (collection, member_cfgs),
    )
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda config, recording_id=None, overrides=(): {
            "_recording_id": config["recording"],
            "title": config["recording"].rsplit("/", 1)[-1].title(),
            "description": f"Watch {config['recording']}",
        },
    )

    resolved: list[str] = []

    def fake_watch_presentation_artifacts(spec, *, run_dir=None):
        member = spec["_recording_id"]
        resolved.append(member)
        return Path(f"/{member}"), {}

    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        fake_watch_presentation_artifacts,
    )

    url_path, pages, recordings = studio.collection_watch_routes(
        cfg,
        {"recording": "tutorial"},
    )

    assert url_path == "/watch/tutorial/"
    assert resolved == ["tutorial/recording-file", "tutorial/beat"]
    assert set(recordings) == {
        "tutorial/recording-file",
        "tutorial/beat",
    }
    page = pages["/watch/tutorial/"].decode("utf-8")
    assert "Recording-File" in page
    assert "Watch tutorial/beat" in page
    assert 'href="/watch/tutorial/beat/?autoplay=countdown"' in page


def test_collection_watch_reports_member_without_a_build(monkeypatch) -> None:
    cfg = OmegaConf.create({"recording": "tutorial"})
    collection = {
        "kind": "collection",
        "id": "tutorial",
        "title": "Tutorial",
        "members": ["tutorial/beat"],
    }
    member_cfg = OmegaConf.create({"recording": "tutorial/beat"})
    monkeypatch.setattr(
        studio,
        "load_collection_build",
        lambda _cfg, _config: (collection, [member_cfg]),
    )
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {
            "_recording_id": "tutorial/beat",
            "title": "Beat",
        },
    )
    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            studio.StudioError("no successful recording run found")
        ),
    )

    with pytest.raises(
        studio.StudioError,
        match=(
            "collection tutorial member tutorial/beat cannot be watched: "
            "no successful recording run found; build it with "
            "omegaflow recording=tutorial/beat"
        ),
    ):
        studio.collection_watch_routes(cfg, {"recording": "tutorial"})


def test_watch_handler_serves_generated_page_from_memory() -> None:
    handler = studio.StudioWatchRequestHandler.__new__(
        studio.StudioWatchRequestHandler
    )
    handler.path = "/collection.html?ignored=true"
    handler.pages = {"/collection.html": b"<h1>Tutorial</h1>"}
    handler.headers = {}
    response: dict[str, object] = {"headers": []}
    handler.send_response = lambda status: response.update(status=status)
    handler.send_header = lambda name, value: response["headers"].append((name, value))
    handler.end_headers = lambda: response.update(ended=True)

    source = handler.send_head()

    assert source.read() == b"<h1>Tutorial</h1>"
    assert response == {
        "status": 200,
        "headers": [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", "17"),
        ],
        "ended": True,
    }


@pytest.mark.parametrize("error_type", [BrokenPipeError, ConnectionResetError])
@pytest.mark.parametrize("byte_range", [None, (0, 3)])
def test_watch_copyfile_ignores_disconnected_client(
    error_type: type[OSError],
    byte_range: tuple[int, int] | None,
) -> None:
    handler = studio.StudioWatchRequestHandler.__new__(
        studio.StudioWatchRequestHandler
    )
    if byte_range is not None:
        handler._response_byte_range = byte_range

    class DisconnectedOutput:
        def write(self, _chunk: bytes) -> None:
            raise error_type()

    handler.copyfile(io.BytesIO(b"data"), DisconnectedOutput())


def test_watch_copyfile_does_not_hide_unrelated_errors() -> None:
    handler = studio.StudioWatchRequestHandler.__new__(
        studio.StudioWatchRequestHandler
    )

    class InvalidOutput:
        def write(self, _chunk: bytes) -> None:
            raise RuntimeError("unexpected write failure")

    with pytest.raises(RuntimeError, match="unexpected write failure"):
        handler.copyfile(io.BytesIO(b"data"), InvalidOutput())


def test_run_watch_enables_countdown_autoplay(monkeypatch) -> None:
    requested: dict[str, object] = {}

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {"_recording_id": "hello"},
    )

    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        lambda _spec, *, run_dir=None: (Path("/presentation"), {}),
    )

    def fake_run_watch_server(
        _cfg,
        _url,
        _artifacts,
        *,
        recordings=None,
        managed_browser=False,
        open_browser=True,
        port=0,
    ):
        requested.update(
            url=_url,
            artifacts=_artifacts,
            recordings=recordings,
            managed_browser=managed_browser,
            open_browser=open_browser,
            port=port,
        )
        return 0

    monkeypatch.setattr(studio, "run_watch_server", fake_run_watch_server)

    status = studio.run_watch(
        OmegaConf.create({"output_format": "text"}),
        {"recording": "hello", "watch_port": 43123},
    )

    assert status == 0
    assert requested == {
        "url": "/watch/hello/?autoplay=countdown",
        "artifacts": {},
        "recordings": {"hello": {"_recording_id": "hello"}},
        "managed_browser": True,
        "open_browser": True,
        "port": 43123,
    }


@pytest.mark.parametrize("changed_file", ["index.md", "scripts/action.sh"])
def test_watch_rebuilds_after_recording_source_changes(
    tmp_path,
    monkeypatch,
    changed_file,
) -> None:
    recording_dir = tmp_path / "recordings"
    recording_source = recording_dir / "hello"
    script = recording_source / "scripts" / "action.sh"
    script.parent.mkdir(parents=True)
    (recording_source / "index.md").write_text("initial narration\n")
    script.write_text("echo initial\n")
    config = {
        "recording": "hello",
        "studio": {"recording_dir": str(recording_dir)},
    }
    stop_event = threading.Event()
    rebuilt: list[str] = []

    class ChangingEvent:
        def __init__(self) -> None:
            self.wait_count = 0

        def wait(self, _timeout) -> bool:
            self.wait_count += 1
            if self.wait_count == 1:
                (recording_source / changed_file).write_text("changed\n")
            return stop_event.is_set()

    def fake_rebuild(_cfg, recording_id) -> int:
        rebuilt.append(recording_id)
        stop_event.set()
        return 0

    monkeypatch.setattr(studio, "run_watch_rebuild", fake_rebuild)

    studio.run_watch_rebuild_loop(
        OmegaConf.create(config),
        config,
        ("hello",),
        ChangingEvent(),
        poll_interval=0.001,
    )

    assert rebuilt == ["hello"]


def test_watch_source_fingerprint_ignores_generated_cache_files(tmp_path) -> None:
    recording_source = tmp_path / "recordings" / "hello"
    recording_source.mkdir(parents=True)
    (recording_source / "index.md").write_text("narration\n")
    roots = (recording_source,)
    before = studio.watch_source_fingerprint(roots)

    cache = recording_source / "__pycache__"
    cache.mkdir()
    (cache / "action.pyc").write_bytes(b"generated")

    assert studio.watch_source_fingerprint(roots) == before


def test_watch_rebuild_uses_a_build_config_and_recording_run_dir(
    tmp_path,
    monkeypatch,
) -> None:
    cfg = OmegaConf.create(
        {
            "action": "watch",
            "recording": "hello",
            "project_root": str(tmp_path),
            "studio": {
                "data_dir": "recordings/.omegaflow",
                "recording_dir": "recordings",
            },
        }
    )
    observed: dict[str, object] = {}

    def fake_recording_spec(
        config,
        *,
        recording_id=None,
        overrides=(),
        hydra_output_dir=None,
    ):
        observed["config"] = config
        observed["run_dir"] = Path(hydra_output_dir)
        return {"_recording_id": "hello"}

    monkeypatch.setattr(studio, "recording_spec_from_config", fake_recording_spec)
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: "plan")

    def fake_manifest_build(
        build_cfg,
        config,
        spec,
        plan,
        *,
        show_followups=True,
    ) -> int:
        observed.update(
            build_cfg=build_cfg,
            build_config=config,
            spec=spec,
            plan=plan,
            show_followups=show_followups,
        )
        return 0

    monkeypatch.setattr(studio, "run_manifest_build", fake_manifest_build)

    assert studio.run_watch_rebuild(cfg, "hello") == 0
    assert observed["config"]["action"] == "build"
    assert observed["run_dir"].parent == (
        tmp_path / "recordings/.omegaflow/runs/hello"
    )
    assert observed["show_followups"] is False


def test_run_watch_can_disable_countdown_autoplay(monkeypatch) -> None:
    requested: dict[str, object] = {}
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {"_recording_id": "hello"},
    )
    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        lambda _spec, *, run_dir=None: (Path("/presentation"), {}),
    )

    def fake_run_watch_server(
        _cfg,
        _url,
        _artifacts,
        **kwargs,
    ):
        requested.update(url=_url, **kwargs)
        return 0

    monkeypatch.setattr(studio, "run_watch_server", fake_run_watch_server)

    status = studio.run_watch(
        OmegaConf.create({"output_format": "text"}),
        {"recording": "hello", "autoplay": False},
    )

    assert status == 0
    assert requested["url"] == "/watch/hello/"


@pytest.mark.parametrize("value", [0, "false", None])
def test_run_watch_rejects_invalid_autoplay(value, monkeypatch) -> None:
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {"_recording_id": "hello"},
    )

    with pytest.raises(studio.StudioError, match="autoplay must be a boolean"):
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {"recording": "hello", "autoplay": value},
        )


@pytest.mark.parametrize("value", [True, 0, -1, 65536, "43123"])
def test_run_watch_rejects_invalid_configured_port(monkeypatch, value) -> None:
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {"_recording_id": "hello"},
    )
    monkeypatch.setattr(
        studio,
        "run_watch_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("watch server started")
        ),
    )

    with pytest.raises(
        studio.StudioError,
        match="watch_port must be an integer between 1 and 65535 or null",
    ):
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {"recording": "hello", "watch_port": value},
        )


def test_run_watch_can_serve_without_opening_browser(monkeypatch) -> None:
    requested: dict[str, object] = {}

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {"_recording_id": "hello"},
    )
    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        lambda _spec, *, run_dir=None: (Path("/presentation"), {}),
    )

    def fake_run_watch_server(
        _cfg,
        _url,
        _artifacts,
        *,
        recordings=None,
        managed_browser=False,
        open_browser=True,
        port=0,
    ):
        requested.update(
            url=_url,
            artifacts=_artifacts,
            recordings=recordings,
            managed_browser=managed_browser,
            open_browser=open_browser,
            port=port,
        )
        return 0

    monkeypatch.setattr(studio, "run_watch_server", fake_run_watch_server)

    status = studio.run_watch(
        OmegaConf.create({"output_format": "text"}),
        {"recording": "hello", "open": False},
    )

    assert status == 0
    assert requested == {
        "url": "/watch/hello/?autoplay=countdown",
        "artifacts": {},
        "recordings": {"hello": {"_recording_id": "hello"}},
        "managed_browser": False,
        "open_browser": False,
        "port": 0,
    }


def test_run_collection_watch_can_serve_without_opening_browser(monkeypatch) -> None:
    requested: dict[str, object] = {}
    pages = {"/watch/tutorial/": b"<h1>Tutorial</h1>"}
    recordings = {"tutorial/beat": {"_recording_id": "tutorial/beat"}}
    monkeypatch.setattr(
        studio,
        "collection_watch_routes",
        lambda _cfg, _config: (
            "/watch/tutorial/",
            pages,
            recordings,
        ),
    )

    def fake_run_watch_server(
        _cfg,
        url_path,
        artifacts,
        *,
        pages=None,
        recordings=None,
        managed_browser=False,
        open_browser=True,
        port=0,
    ):
        requested.update(
            url_path=url_path,
            artifacts=artifacts,
            pages=pages,
            recordings=recordings,
            managed_browser=managed_browser,
            open_browser=open_browser,
            port=port,
        )
        return 0

    monkeypatch.setattr(studio, "run_watch_server", fake_run_watch_server)

    status = studio.run_collection_watch(
        OmegaConf.create({"output_format": "text"}),
        {"recording": "tutorial", "open": False, "watch_port": 43123},
    )

    assert status == 0
    assert requested == {
        "url_path": "/watch/tutorial/",
        "artifacts": {},
        "pages": pages,
        "recordings": recordings,
        "managed_browser": False,
        "open_browser": False,
        "port": 43123,
    }


def test_managed_watch_browser_uses_isolated_system_browser(monkeypatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(studio, "running_under_wsl", lambda: False)

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self) -> None:
            observed["terminated"] = True

        def wait(self, *, timeout):
            observed["wait_timeout"] = timeout
            return 0

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed["popen"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(
        studio,
        "native_system_chromium_executable",
        lambda: Path("/usr/bin/google-chrome"),
    )
    monkeypatch.setattr(
        studio.tempfile,
        "mkdtemp",
        lambda *, prefix: f"/tmp/{prefix}abc123",
    )
    monkeypatch.setattr(studio.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        studio,
        "remove_native_watch_profile",
        lambda path: observed.setdefault("removed_profile", path),
    )

    session = studio.launch_managed_watch_browser("http://127.0.0.1:1234/player")

    assert observed["command"] == [
        "/usr/bin/google-chrome",
        "--user-data-dir=/tmp/omegaflow-watch-abc123",
        "--autoplay-policy=no-user-gesture-required",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--new-window",
        "http://127.0.0.1:1234/player",
    ]
    assert observed["popen"] == {
        "stdout": studio.subprocess.DEVNULL,
        "stderr": studio.subprocess.DEVNULL,
    }
    assert session.is_open()
    session.close()
    assert observed["terminated"] is True
    assert observed["wait_timeout"] == 5
    assert observed["removed_profile"] == "/tmp/omegaflow-watch-abc123"


def test_managed_watch_browser_reports_missing_system_browser(monkeypatch) -> None:
    monkeypatch.setattr(studio, "running_under_wsl", lambda: False)
    monkeypatch.setattr(studio, "native_system_chromium_executable", lambda: None)

    with pytest.raises(
        studio.StudioError,
        match="installed system Chrome, Chromium, Edge, or Brave",
    ):
        studio.launch_managed_watch_browser("http://127.0.0.1:1234/player")


def test_managed_watch_browser_uses_isolated_windows_chrome_under_wsl(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self) -> None:
            observed["terminated"] = True

        def wait(self, *, timeout):
            observed["wait_timeout"] = timeout
            return 0

    process = FakeProcess()

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed["popen"] = kwargs
        return process

    monkeypatch.setattr(studio, "running_under_wsl", lambda: True)
    monkeypatch.setattr(
        studio,
        "wsl_host_chromium_executable",
        lambda: Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"),
    )
    monkeypatch.setattr(
        studio,
        "windows_temporary_directory",
        lambda: r"C:\Users\demo\AppData\Local\Temp",
    )
    monkeypatch.setattr(
        studio.uuid,
        "uuid4",
        lambda: type("Id", (), {"hex": "abc123"})(),
    )
    monkeypatch.setattr(studio.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        studio,
        "remove_windows_watch_profile",
        lambda path: observed.setdefault("removed_profile", path),
    )

    session = studio.launch_managed_watch_browser("http://127.0.0.1:1234/player")

    assert observed["command"] == [
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        r"--user-data-dir=C:\Users\demo\AppData\Local\Temp\omegaflow-watch-abc123",
        "--autoplay-policy=no-user-gesture-required",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--new-window",
        "http://127.0.0.1:1234/player",
    ]
    assert observed["popen"] == {
        "stdout": studio.subprocess.DEVNULL,
        "stderr": studio.subprocess.DEVNULL,
    }
    assert session.is_open()
    session.close()
    assert observed["terminated"] is True
    assert observed["wait_timeout"] == 5
    assert observed["removed_profile"] == (
        r"C:\Users\demo\AppData\Local\Temp\omegaflow-watch-abc123"
    )


def test_windows_watch_profile_cleanup_is_best_effort(monkeypatch) -> None:
    monkeypatch.setattr(studio.shutil, "which", lambda _command: "powershell.exe")
    monkeypatch.setattr(
        studio.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            studio.subprocess.TimeoutExpired("powershell.exe", 10)
        ),
    )

    studio.remove_windows_watch_profile(r"C:\Temp\omegaflow-watch-demo")


def test_managed_watch_server_stops_when_browser_closes(monkeypatch, capsys) -> None:
    observed: dict[str, object] = {}

    class FakeServer:
        server_port = 51234

        def __init__(self, _address, _handler_factory) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> bool:
            return False

        def serve_forever(self) -> None:
            observed["served"] = True

        def shutdown(self) -> None:
            observed["shutdown"] = True

    class FakeBrowserSession:
        def is_open(self) -> bool:
            return False

        def close(self) -> None:
            observed["browser_closed"] = True

    def fake_launch(url: str):
        observed["url"] = url
        return FakeBrowserSession()

    monkeypatch.setattr(studio.http.server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(studio, "launch_managed_watch_browser", fake_launch)

    status = studio.run_watch_server(
        OmegaConf.create({"output_format": "text"}),
        "/cast-player.html?manifest=demo&autoplay=countdown",
        {},
        managed_browser=True,
    )
    output = capsys.readouterr().out

    assert status == 0
    assert observed == {
        "served": True,
        "url": (
            "http://127.0.0.1:51234/"
            "cast-player.html?manifest=demo&autoplay=countdown"
        ),
        "browser_closed": True,
        "shutdown": True,
    }
    assert "opened isolated system browser" in output
    assert "stopped local watch server" in output


def test_managed_watch_browser_uses_capture_handoff_instead_of_system_browser(
    tmp_path: Path, monkeypatch
) -> None:
    from omegaflow.browser_handoff import (
        BROWSER_HANDOFF_ID_ENV,
        BROWSER_HANDOFF_ROOT_ENV,
        BrowserHandoffBroker,
    )

    broker = BrowserHandoffBroker(tmp_path / "handoffs")
    broker.prepare("watch_command")
    monkeypatch.setenv(BROWSER_HANDOFF_ROOT_ENV, str(broker.root))
    monkeypatch.setenv(BROWSER_HANDOFF_ID_ENV, "watch_command")
    monkeypatch.setattr(
        studio,
        "launch_managed_wsl_host_browser",
        lambda _url: (_ for _ in ()).throw(AssertionError("system browser opened")),
    )
    monkeypatch.setattr(
        studio,
        "launch_managed_native_browser",
        lambda _url: (_ for _ in ()).throw(AssertionError("system browser opened")),
    )

    session = studio.launch_managed_watch_browser(
        "http://127.0.0.1:43123/cast-player.html?manifest=demo"
    )

    assert session.is_open() is True
    assert broker.ready_url("watch_command") is not None
    broker.close("watch_command")
    assert session.is_open() is False


def test_watch_server_reports_local_watch_server(monkeypatch, capsys) -> None:
    observed: dict[str, object] = {}

    class FakeServer:
        server_port = 51234

        def __init__(self, address, _handler_factory) -> None:
            observed["address"] = address

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> bool:
            return False

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(studio.http.server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(studio, "open_watch_url", lambda _url: True)

    status = studio.run_watch_server(
        OmegaConf.create({"output_format": "text"}),
        "/cast-player.html?manifest=/__studio_artifacts__/recording.presentation.json",
        {"cast": Path("recording.cast")},
        port=51234,
    )
    output = capsys.readouterr().out

    assert status == 0
    assert observed == {"address": ("127.0.0.1", 51234)}
    assert "serving local watch server: http://127.0.0.1:51234/" in output
    assert "opened browser; press Ctrl-C to stop" in output
    assert "stopped local watch server" in output


def test_watch_server_reports_configured_port_collision(monkeypatch) -> None:
    def fail_to_bind(_address, _handler_factory):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(studio.http.server, "ThreadingHTTPServer", fail_to_bind)

    with pytest.raises(
        studio.StudioError,
        match=(
            r"could not start local watch server on 127\.0\.0\.1:43123: "
            r"\[Errno 98\] Address already in use"
        ),
    ):
        studio.run_watch_server(
            OmegaConf.create({"output_format": "text"}),
            "/cast-player.html?manifest=demo",
            {},
            port=43123,
        )


def test_watch_server_can_serve_without_calling_browser_opener(
    monkeypatch, capsys
) -> None:
    observed: dict[str, object] = {}

    class FakeServer:
        server_port = 51234

        def __init__(self, _address, _handler_factory) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> bool:
            return False

        def serve_forever(self) -> None:
            observed["served"] = True
            raise KeyboardInterrupt

    monkeypatch.setattr(studio.http.server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(
        studio,
        "open_watch_url",
        lambda _url: (_ for _ in ()).throw(AssertionError("browser opener called")),
    )

    status = studio.run_watch_server(
        OmegaConf.create({"output_format": "text"}),
        "/cast-player.html?manifest=/__studio_artifacts__/recording.presentation.json",
        {},
        open_browser=False,
    )
    output = capsys.readouterr().out

    assert status == 0
    assert observed == {"served": True}
    assert "open the URL in a browser; press Ctrl-C to stop" in output
    assert "stopped local watch server" in output
