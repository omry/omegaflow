import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from contextlib import contextmanager
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
from omegaflow.recording_plan import normalize_recording_plan
from omegaflow.studio_config import (
    CONFIG_DIR,
    RECORDING_SCRIPT_DIR,
    STUDIO_CONFIG_NAME,
    StudioConfigError,
    compose_studio_config,
    discover_project_layout,
    list_recording_ids,
    load_configured_env_file,
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


def test_build_completion_detail_includes_video_length() -> None:
    assert studio.format_build_completion_detail(
        8.25,
        video_duration_ms=64_200,
    ) == "8.2s · video length 1m 4.2s"


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
    final_render = output.rsplit("\x1b8", 2)[-2]
    final_progress_line = next(
        line for line in final_render.splitlines() if "progress" in line
    )
    assert "[████████████████████████████] 1/1" in final_progress_line
    assert final_progress_line.endswith("1/1 · completed in 2.3s")
    assert output.endswith("\x1b8\x1b[1E\x1b[2K\r")


def test_build_progress_anchors_redraw_without_changing_terminal_input() -> None:
    termios = pytest.importorskip("termios")
    master_fd, slave_fd = os.openpty()
    try:
        with os.fdopen(
            os.dup(slave_fd), "w", encoding="utf-8"
        ) as output_stream:
            original_lflag = termios.tcgetattr(slave_fd)[3]
            assert original_lflag & termios.ECHO
            progress = studio.BuildProgress(
                total=1,
                stream=output_stream,
                interactive=True,
                color=False,
                heartbeat_interval=10.0,
            )

            progress.begin("Recording workflow")

            active_lflag = termios.tcgetattr(slave_fd)[3]
            assert active_lflag == original_lflag
            os.set_blocking(master_fd, False)
            initial_output = bytearray()
            while True:
                try:
                    initial_output.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    break
            assert b"\x1b7" in initial_output

            os.write(master_fd, b"\n")
            time.sleep(0.01)

            assert b"\n" in os.read(master_fd, 4096)

            progress.advance("Video ready")
            progress.finish()

            redraw_output = os.read(master_fd, 4096)
            assert b"\x1b8" in redraw_output
            assert termios.tcgetattr(slave_fd)[3] == original_lflag
    finally:
        os.close(master_fd)
        os.close(slave_fd)


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

    assert stream.getvalue().endswith("\x1b8\x1b[2M")


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


def test_private_watch_capture_does_not_reuse_canonical_run(
    tmp_path,
    monkeypatch,
) -> None:
    plan = studio.normalized_recording_plan(
        {
            "id": "demo",
            "beats": [{"id": "hello", "actions": [{"run": "printf hello"}]}],
        }
    )
    canonical_run = tmp_path / "runs/demo/canonical"
    private_run = tmp_path / "runs/.scratch/watch/demo/hello/private"
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: canonical_run,
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "capture_is_fresh",
        lambda *_args: pytest.fail("private capture must not inspect canonical runs"),
    )
    captured: list[Path] = []

    def fake_capture(_spec, _plan, run_dir, *, headed, on_progress):
        captured.append(run_dir)

    monkeypatch.setattr(
        studio.presentation_build,
        "capture_recording",
        fake_capture,
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "write_capture_fingerprint",
        lambda *_args: None,
    )

    result = studio.run_build_record_action(
        OmegaConf.create(
            {"force": False, "verbose": False, "output_format": "json"}
        ),
        {
            "_recording_id": "demo",
            "_hydra_output_dir": str(private_run),
        },
        plan,
        reuse_latest=False,
    )

    assert result == private_run
    assert captured == [private_run]


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
    run_dir = tmp_path / "failed-run"
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "_hydra_output_dir": str(run_dir),
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

    def fail_capture(_cfg, _spec, _plan, *, progress, reuse_latest):
        assert reuse_latest is True
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
    timing = json.loads((run_dir / "build-timing.json").read_text(encoding="utf-8"))
    assert timing["version"] == 1
    assert timing["recording"] == "demo"
    assert timing["status"] == "failed"
    assert isinstance(timing["duration_ms"], int)
    assert timing["duration_ms"] >= 0
    assert [stage["name"] for stage in timing["stages"]] == [
        "resolve_publish_targets",
        "capture",
    ]
    assert timing["stages"][0]["status"] == "completed"
    assert timing["stages"][1]["status"] == "failed"


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


def test_capture_failure_message_collapses_terminal_progress_redraws() -> None:
    report = {
        "output": (
            "\x1b[3F\x1b7\x1b[2Kprogress [░░░░] 0/5\r\n"
            "\x1b[2Kcurrent Recording workflow\r\n"
            "\x1b8\x1b[2Kprogress [████] 5/5\r\n"
            "\x1b[2Kcurrent Video ready\r\n"
            "\x1b8\x1b[2Kprogress [████] 5/5 · completed in 0.8s "
            "· video length 10.5s\r\n"
            "\x1b8\x1b[1E\x1b[2Kpublish html: updated\r\n"
        ),
        "recording_id": "demo",
        "run_id": "20260731-140558",
    }

    message = studio.capture_failure_message(RuntimeError("cleanup failed"), report)

    assert "\x1b" not in message
    assert "\r" not in message
    assert "current Recording workflow" not in message
    assert "current Video ready" not in message
    assert message.count("progress ") == 1
    assert (
        "progress [████] 5/5 · completed in 0.8s · video length 10.5s"
        in message
    )
    assert "publish html: updated" in message


def test_capture_failure_message_keeps_browser_cause_without_terminal_output() -> None:
    pane_error = studio.CapturePaneStreamError(
        "player",
        studio.RecordingMedium.browser,
        RuntimeError(
            "BROWSER_UNSUPPORTED_MOTION: beat 'review', action 'play': "
            "could not align the initial browser frame"
        ),
    )
    error = CaptureFailed(
        primary=CaptureFailureDetail("capture concurrent pane streams", pane_error),
        cleanup=(),
    )

    message = studio.capture_failure_message(
        error,
        {
            "output": "nested build progress\nnested watch server stopped\n",
            "recording_id": "tutorial",
            "run_id": "20260731-200837",
        },
    )

    assert "capture pane stream 'player' failed" in message
    assert "could not align the initial browser frame" in message
    assert "nested build progress" not in message
    assert "nested watch server stopped" not in message
    assert message.endswith(
        "Run: omegaflow recording=tutorial action=output "
        "run_id=20260731-200837"
    )


def test_cleanup_only_failure_does_not_repeat_primary_cleanup_as_warning() -> None:
    error = CaptureFailed(
        primary=None,
        cleanup=(
            CaptureFailureDetail(
                "close terminal runner",
                TerminalCaptureError("terminal extraction failed"),
            ),
        ),
    )

    message = studio.capture_failure_message(
        error,
        {
            "output": "captured context\n",
            "recording_id": "demo",
            "run_id": "20260731-140558",
        },
    )

    assert message == (
        "Cleanup failed\n"
        "  terminal extraction failed\n"
        "  captured context\n"
        "Run: omegaflow recording=demo action=output run_id=20260731-140558"
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
            duration_ms=1400,
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
    assert "info  OpenAI narration estimated cost this build: < $0.01" in output
    assert "step  Assembling video" in output
    assert "pass  build completed after" in output
    assert " · video length 1.4s" in output
    assert "capture recording" not in output
    assert "compile presentation" not in output
    assert "publish surface" not in output
    assert "wrote presentation" not in output
    assert "omegaflow recording=demo action=watch" not in output
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
    timing = json.loads((run_dir / "build-timing.json").read_text(encoding="utf-8"))
    assert timing["version"] == 1
    assert timing["recording"] == "demo"
    assert timing["status"] == "completed"
    assert isinstance(timing["duration_ms"], int)
    assert timing["duration_ms"] >= 0
    assert [stage["name"] for stage in timing["stages"]] == [
        "resolve_publish_targets",
        "capture",
        "narration",
        "assemble",
        "publish_bundle",
        "publish_surface",
        "publish_surface",
        "finalize",
    ]
    capture = timing["stages"][1]
    assert capture["details"] == {"action_count": 0, "reused": True}
    narration = timing["stages"][2]
    assert narration["details"] == {
        "enabled": True,
        "take_count": 1,
        "generated_tts_takes": 1,
        "generated_timestamp_files": 1,
    }
    assert [
        stage["details"]["surface"] for stage in timing["stages"][5:7]
    ] == ["website", "standalone"]


def test_narration_billing_message_colors_only_dollar_amounts() -> None:
    artifacts = SimpleNamespace(
        tts_billing=audio.AudioBillingSummary(
            generated_segments=1,
            billable_characters=100,
            estimated_cost_usd=0.004275,
        ),
        transcription_billing=audio.AudioTranscriptionBillingSummary(
            generated_timestamp_files=1,
            audio_seconds=5.0,
            estimated_cost_usd=0.001846,
        ),
    )

    assert studio.narration_billing_message(artifacts, color=True) == (
        "OpenAI narration estimated cost this build: "
        f"{ANSI_GREEN_BOLD}< $0.01{ANSI_RESET}"
    )


@pytest.mark.parametrize(
    ("tts_cost", "transcription_cost", "expected"),
    [
        (0.007, 0.003, "$0.01"),
        (0.075, 0.025, "$0.10"),
        (0.087, 0.034, "$0.12 (TTS $0.09 + transcription $0.03)"),
    ],
)
def test_narration_billing_message_rounds_to_cents_and_limits_breakdown(
    tts_cost: float,
    transcription_cost: float,
    expected: str,
) -> None:
    artifacts = SimpleNamespace(
        tts_billing=audio.AudioBillingSummary(
            generated_segments=1,
            billable_characters=100,
            estimated_cost_usd=tts_cost,
        ),
        transcription_billing=audio.AudioTranscriptionBillingSummary(
            generated_timestamp_files=1,
            audio_seconds=5.0,
            estimated_cost_usd=transcription_cost,
        ),
    )

    assert studio.narration_billing_message(artifacts, color=False) == (
        f"OpenAI narration estimated cost this build: {expected}"
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


def test_asciinema_command_uses_parent_resolved_path_inside_isolated_capture(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        record,
        "bundled_asciinema_path",
        lambda: "/bundle/asciinema",
    )
    monkeypatch.setenv(record.ASCIINEMA_PATH_ENV, "/host-tools/asciinema")
    monkeypatch.setattr(record.shutil, "which", lambda _command: None)

    assert record.asciinema_command({"studio": {}}) == "/host-tools/asciinema"


def test_asciinema_command_resolves_host_fallback_before_environment_isolation(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(record, "bundled_asciinema_path", lambda: None)
    monkeypatch.setattr(
        record.shutil,
        "which",
        lambda command: "tools/asciinema" if command == "asciinema" else None,
    )

    assert (
        record.asciinema_command({"studio": {}})
        == str(tmp_path / "tools/asciinema")
    )


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


def test_required_commands_use_the_recorded_command_path(
    tmp_path: Path, monkeypatch
) -> None:
    configured_bin = tmp_path / "configured-bin"
    configured_bin.mkdir()
    configured_tool = configured_bin / "configured-tool"
    configured_tool.write_text("#!/bin/sh\n", encoding="utf-8")
    configured_tool.chmod(0o755)
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    host_tool = host_bin / "host-only-tool"
    host_tool.write_text("#!/bin/sh\n", encoding="utf-8")
    host_tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(host_bin))

    record.check_required_commands(
        {
            "environment": {"path_prepend": [str(configured_bin)]},
            "requirements": {"commands": ["configured-tool"]},
        }
    )

    with pytest.raises(record.RecordingError, match="host-only-tool"):
        record.check_required_commands(
            {"requirements": {"commands": ["host-only-tool"]}}
        )


def test_studio_run_dir_uses_data_directory() -> None:
    assert (
        studio_run_dir(
            "recordings/.omegaflow",
            "build",
            "capture",
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


def test_studio_config_does_not_load_a_process_env_file_by_default(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MUST_NOT_LOAD=secret\n", encoding="utf-8")
    monkeypatch.delenv("MUST_NOT_LOAD", raising=False)

    config = compose_studio_config(None, ())

    assert config["load_env_file"] is False
    assert config["env_file"] is None
    assert load_configured_env_file({}) == {}
    assert "MUST_NOT_LOAD" not in os.environ


def test_public_steps_are_capture_and_narration() -> None:
    assert [step.value for step in studio_config_module.StudioStep] == [
        "capture",
        "narration",
    ]


@pytest.mark.parametrize(
    "legacy_step",
    [
        "record",
        "record_check",
        "record_dry_run",
        "dry_run",
        "sync_narration",
        "publish",
    ],
)
def test_legacy_steps_are_rejected(legacy_step) -> None:
    with pytest.raises(studio.StudioError, match=rf"unknown step: {legacy_step}"):
        studio.validate_step(legacy_step)


def test_capture_step_dispatches_capture_only(monkeypatch) -> None:
    config = compose_studio_config(
        None,
        ("recording=demo", "step=capture"),
    )
    dispatched: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        studio,
        "run_record_action",
        lambda _cfg, action, label=None: dispatched.append((action, label)),
    )

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert dispatched == [("capture", "capture")]


def test_step_rejects_non_build_action() -> None:
    config = compose_studio_config(
        None,
        ("recording=demo", "action=watch", "step=narration"),
    )

    with pytest.raises(
        studio.StudioError,
        match=r"step can only be combined with action=build",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))


def test_beat_target_rejects_non_watch_action() -> None:
    config = compose_studio_config(
        None,
        ("recording=demo", "action=build", "beat=intro"),
    )

    with pytest.raises(
        studio.StudioError,
        match=r"beat can only be combined with action=watch",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))


def test_build_plan_lists_each_scoped_browser_capture_log(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    spec = {
        "id": "two-browsers",
        "_recording_id": "two-browsers",
        "_hydra_output_dir": str(run_dir),
        "outputs": {"asset_dir": str(tmp_path / "public")},
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
    monkeypatch.setattr(
        studio,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: spec,
    )

    plan = studio.build_plan(OmegaConf.create({}), {})

    assert plan["outputs"]["browser_capture_logs"] == [
        str(run_dir / "capture/runners/left/browser.capture.jsonl"),
        str(run_dir / "capture/runners/right/browser.capture.jsonl"),
    ]
    assert "browser_capture_log" not in plan["outputs"]


def test_build_plan_lists_declared_source_dependencies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    dependency = recording_dir / "example.svg"
    dependency.write_text("<svg/>", encoding="utf-8")
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "_project_root": str(tmp_path),
        "_script_dir": str(recording_dir),
        "_hydra_output_dir": str(tmp_path / "run"),
        "outputs": {"asset_dir": str(tmp_path / "public")},
        "beats": [
            {
                "id": "inspect",
                "actions": [
                    {"run": "true", "inputs": ["example.svg"]},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        studio,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: spec,
    )

    plan = studio.build_plan(OmegaConf.create({}), {})

    assert plan["inputs"]["source_dependencies"] == [
        str(dependency),
    ]


def test_capture_step_uses_the_hydra_run_directory(monkeypatch, tmp_path) -> None:
    run_dir = tmp_path / "capture-run"
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "_hydra_output_dir": str(run_dir),
        "beats": [
            {
                "id": "intro",
                "heading": "Introduction",
                "actions": [],
            }
        ],
    }
    cfg = OmegaConf.create(
        {
            "recording": "demo",
            "step": "capture",
            "output_format": "text",
            "verbose": False,
            "headed": False,
        }
    )
    monkeypatch.setattr(
        studio,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: spec,
    )
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: pytest.fail(
            "capture step bypassed the Hydra recording loader"
        ),
    )
    captured: dict[str, object] = {}

    def fake_capture(capture_spec, plan, target, *, headed):
        captured.update(
            spec=capture_spec,
            plan=plan,
            target=target,
            headed=headed,
        )

    monkeypatch.setattr(
        studio.presentation_build,
        "capture_recording",
        fake_capture,
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "write_capture_fingerprint",
        lambda _spec, _plan, target: target / "recording.fingerprint.json",
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "prepare_narration_audio",
        lambda *_args, **_kwargs: pytest.fail("capture step prepared narration"),
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "compile_presentation_bundle",
        lambda *_args, **_kwargs: pytest.fail("capture step assembled video"),
    )

    assert studio.run_tool_from_hydra_cfg(cfg) == 0

    assert captured["spec"] is spec
    assert captured["target"] == run_dir
    assert captured["headed"] is False


def test_narration_step_prepares_build_narration_without_capture(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    recording_dir = tmp_path / "recordings" / "narrated"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Narrated
---

```yaml studio-directive
config:
  audio:
    enabled: true
```

```yaml studio-directive
beat:
  id: intro
  heading: Introduction
  narration: Hello from the narration step.
  actions:
  - commands:
    - run: "true"
```
""".lstrip(),
        encoding="utf-8",
    )
    config = compose_studio_config(
        None,
        ("recording=narrated", "action=build", "step=narration"),
    )
    scratch_dir = (
        tmp_path
        / "recordings"
        / ".omegaflow"
        / "runs"
        / ".scratch"
        / "narration"
        / "narrated"
        / "test-run"
    )
    spec = recording_spec_from_config(
        config,
        recording_id=None,
        overrides=(),
        hydra_output_dir=str(scratch_dir),
    )
    monkeypatch.setattr(
        studio,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: spec,
    )
    prepared: dict[str, object] = {}

    def fake_prepare(spec, plan, run_dir, *, force, on_progress):
        prepared.update(
            spec=spec,
            plan=plan,
            run_dir=run_dir,
            force=force,
        )
        assert on_progress is not None
        for current in range(1, 4):
            on_progress("Prepare narration: Introduction", current, 3)
        return None

    monkeypatch.setattr(
        studio.presentation_build,
        "prepare_narration_audio",
        fake_prepare,
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "capture_recording",
        lambda *_args, **_kwargs: pytest.fail("narration step captured recording"),
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "compile_presentation_bundle",
        lambda *_args, **_kwargs: pytest.fail("narration step assembled video"),
    )

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert prepared["spec"]["id"] == "narrated"
    assert len(prepared["plan"].narration_takes) == 1
    assert prepared["force"] is False
    assert "/.scratch/narration/narrated/" in str(prepared["run_dir"])
    output = capsys.readouterr().out
    assert "Preparing narration (1 take)" in output
    assert "narration ready: 1 take" in output


def test_check_missing_capture_points_to_capture_step(monkeypatch) -> None:
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "beats": [
            {
                "id": "intro",
                "heading": "Introduction",
                "actions": [],
            }
        ],
    }
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: None,
    )

    with pytest.raises(
        studio.StudioError,
        match=(
            r"no complete capture found; run omegaflow recording=demo "
            r"step=capture first"
        ),
    ):
        studio.run_check(OmegaConf.create({}))


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
title: Hello Video
---

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
title: Tutorial Recording File
---

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


@pytest.mark.parametrize("kind", ["video", "collection"])
def test_recording_source_rejects_authored_id(tmp_path, kind: str) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    collection_fields = "members:\n  - child\n" if kind == "collection" else ""
    (recording_dir / "index.md").write_text(
        f"""\
---
kind: {kind}
id: other
{collection_fields}---
""",
        encoding="utf-8",
    )

    with pytest.raises(StudioConfigError, match=r"Key 'id' not in"):
        if kind == "collection":
            studio_config_module.recording_collection_from_script(
                "hello",
                recording_dir=recordings_dir,
            )
        else:
            recording_from_script("hello", recording_dir=recordings_dir)


def test_recording_source_kind_defaults_to_video_for_compatibility(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

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
title: Hello Video
description: Learn how to make a narrated terminal video.
---

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


def test_recording_beat_without_narration_is_a_valid_silent_beat(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  actions:
  - commands:
    - run: printf hello
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_from_script("hello", recording_dir=recordings_dir)
    plan = normalize_recording_plan(spec)

    assert spec["beats"][0]["id"] == "hello"
    assert spec["narration"]["beats"] == []
    assert [beat.id for beat in plan.beats] == ["hello"]
    assert plan.beats[0].narration_text == ""
    assert plan.beats[0].actions[0].config["commands"][0]["run"] == "printf hello"
    assert plan.narration_stream.segments == ()
    assert plan.narration_takes == ()


def test_recording_beat_with_explicit_empty_narration_reports_public_config_error(
    tmp_path,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: ""
```
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(
        StudioConfigError,
        match=r"studio-directive beat\.narration must be a non-empty string",
    ):
        recording_from_script("hello", recording_dir=recordings_dir)


def test_recording_collection_preserves_declared_member_order(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    collection_dir = recordings_dir / "tutorial"
    collection_dir.mkdir(parents=True)
    (collection_dir / "index.md").write_text(
        """
---
kind: collection
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
title: {member}
---

```yaml studio-directive
config:
  audio:
    enabled: false
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
    built: list[str] = []

    def fake_run_build(member_cfg):
        built.append(OmegaConf.select(member_cfg, "recording"))
        return 0

    monkeypatch.setattr(studio, "run_build", fake_run_build)

    assert studio.run_collection_build(cfg, config) == 0

    assert built == members
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


def test_tool_rejects_beat_target_for_a_collection(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings" / "tutorial"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "index.md").write_text(
        """
---
kind: collection
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
        "beat": "intro",
        "output_format": "text",
        "load_env_file": False,
        "studio": {
            "recording_dir": "recordings",
            "data_dir": "recordings/.omegaflow",
        },
    }
    monkeypatch.setattr(
        studio,
        "run_collection_watch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("collection watch started")
        ),
    )

    with pytest.raises(
        studio.StudioError,
        match=(
            r"beat cannot target a recording collection; "
            r"select a collection member"
        ),
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))


def test_tool_rejects_single_video_actions_for_a_collection(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings" / "tutorial"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "index.md").write_text(
        """
---
kind: collection
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
title: Demo
---

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


def test_text_highlight_anchor_timing_requires_audio_enabled(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "demo"
    recording_dir.mkdir(parents=True)
    (recordings_dir / "config.yaml").write_text("audio:\n  enabled: false\n", encoding="utf-8")
    (recording_dir / "index.md").write_text(
        """\
---
title: Demo
---

```yaml studio-directive
beat:
  id: hello
  heading: Hello
  narration: "@highlight_start@ Project settings. @highlight_end@"
  effects:
  - highlight:
      targets:
      - text: .omegaflow/config.yaml
      start: "@highlight_start@"
      end: "@highlight_end@"
```
""",
        encoding="utf-8",
    )

    with pytest.raises(
        StudioConfigError,
        match=r"text highlight in beat 'hello'",
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


def test_audio_env_file_is_recording_local_config_without_global_mutation(
    tmp_path, monkeypatch
) -> None:
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
    assert os.environ["OPENAI_RECORDING_KEY"] == "process-secret"
    assert audio.audio_environment(settings, None)["OPENAI_RECORDING_KEY"] == (
        "file-secret"
    )


def test_audio_uses_private_omegaflow_service_environment_without_mutation(
    tmp_path, monkeypatch
) -> None:
    secret_name = "OPENAI_OMEGAFLOW_API_KEY"
    private_dir = tmp_path / ".omegaflow"
    private_dir.mkdir()
    private_file = private_dir / "omegaflow-secret.env"
    private_file.write_text(f"{secret_name}=file-secret\n", encoding="utf-8")
    private_file.chmod(0o600)
    monkeypatch.delenv(secret_name, raising=False)
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env=secret_name,
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        cache_dir=tmp_path / "cache",
        project_root=tmp_path,
    )

    resolved = audio.audio_environment(settings, None)

    assert resolved[secret_name] == "file-secret"
    assert secret_name not in os.environ


@pytest.mark.parametrize(
    "recording_id",
    [
        "quickstart-demo",
        "internal/browser-narration-smoke",
    ],
)
def test_repository_recordings_use_the_private_tts_service_environment(
    recording_id,
) -> None:
    config = compose_studio_config(None, (f"recording={recording_id}",))
    spec = recording_spec_from_config(
        config,
        recording_id=None,
        overrides=(),
        hydra_output_dir=f"/tmp/omegaflow-test-{recording_id}",
    )

    settings = audio.audio_settings(spec)

    assert settings.env == "OPENAI_OMEGAFLOW_API_KEY"
    assert settings.env_file is None


def test_recording_config_directive_overrides_workspace_defaults(
    tmp_path, monkeypatch
) -> None:
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
title: Hello Video
---

# Hello Video

```yaml studio-directive
config:
  audio:
    enabled: true
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
title: Hello Video
---

```yaml studio-directive
config:
  capture:
    headless: true
    window_size: 80x20
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
title: Hello Video
---

```yaml studio-directive
config:
  outputs:
    dir: site/videos
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
title: Hello Video
---

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
title: Hello Video
---

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


def test_rec_narration_id_is_applied_before_narration_is_generated(tmp_path) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
```
""".lstrip(),
        encoding="utf-8",
    )

    spec = recording_from_script(
        "hello",
        recording_dir=recordings_dir,
        overrides={"narration": {"id": "guide"}},
    )

    assert spec["narration"]["id"] == "guide"
    assert spec["narration"]["scene"] == {
        "id": "hello",
        "title": "Hello Video",
    }
    assert [beat["id"] for beat in spec["narration"]["beats"]] == ["hello"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scene", {"title": "Override"}),
        ("beats", []),
    ],
)
def test_rec_rejects_generated_narration_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        "---\ntitle: Hello Video\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(StudioConfigError, match=field):
        recording_from_script(
            "hello",
            recording_dir=recordings_dir,
            overrides={"narration": {field: value}},
        )


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
kind: video
title: Hello Video
---

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
title: {recording_id.title()}
---

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
title: Hello Video
---

```yaml studio-directive
config:
  capture:
    typo_window_size: 80x20
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
title: Hello Video
---

```yaml studio-directive
config:
  retime:
    post_command_pause: 0.1
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


def test_recording_schema_validates_beat_directive_command_fields(
    tmp_path, monkeypatch
) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "hello").mkdir()
    (recordings_dir / "hello" / "index.md").write_text(
        """
---
title: Hello Video
---

```yaml studio-directive
beat:
  id: configured
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - id: say-hello
      run: printf 'hello\\n'
      display: echo hello
      timing: realtime
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
title: Hello Video
---

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - run: printf 'hello\\n'
      disaply: echo hello
```
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
title: Hello Video
---

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - commands:
    - run: printf 'hello\\n'
      retime: realtime
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


@pytest.mark.parametrize("value", ["null", "[]", "true"])
def test_studio_directive_config_requires_a_mapping(value: str) -> None:
    script = f"""
```yaml studio-directive
config: {value}
```
""".lstrip()

    with pytest.raises(
        StudioConfigError,
        match=r"studio-directive block near line 1\.config must be a mapping",
    ):
        studio_directive_blocks(script)


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


def test_studio_directive_type_error_is_concise_and_names_the_field() -> None:
    script = """
```yaml studio-directive
beat:
  id: hello
  medium: shell
```
""".lstrip()

    with pytest.raises(StudioConfigError) as exc_info:
        studio_directive_blocks(script)

    assert str(exc_info.value) == (
        "invalid studio-directive block near line 1:\n"
        "  beat.medium: Invalid value 'shell', expected one of "
        "[terminal, browser]"
    )
    assert "reference_type" not in str(exc_info.value)
    assert "object_type" not in str(exc_info.value)


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


def test_studio_directive_schema_accepts_realtime_browser_audio_capture() -> None:
    script = """
```yaml studio-directive
beat:
  id: browser
  medium: browser
  heading: Play preview
  narration: Play the preview.
  actions:
  - id: play
    timing: realtime
    audio: capture
    click:
      target: {role: button, name: Play}
```
""".lstrip()

    action = studio_directive_blocks(script)[0]["beat"]["actions"][0]

    assert action["timing"] == "realtime"
    assert action["audio"] == "capture"


@pytest.mark.parametrize("generated_field", ["script", "studio"])
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
{generated_field}: {{}}
---

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


def test_recording_config_directive_accepts_a_typed_narration_stream_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

```yaml studio-directive
config:
  narration:
    id: guide
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  actions:
  - run: printf hello
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    spec = recording_from_script("hello")
    plan = normalize_recording_plan(spec)

    assert spec["narration"]["id"] == "guide"
    assert plan.narration_stream.id == "guide"


def test_studio_directive_accepts_pane_title_shortcuts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
panes:
- id: automatic
  kind: visualization
- id: explicit
  kind: visualization
  title: Live output
- id: untitled
  kind: visualization
  title: hidden
- id: positioned
  kind: visualization
  title:
    text: Preview
    alignment_x: right
    alignment_y: bottom
    position_x: 0.4rem
    position_y: 0.3rem
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

    spec = recording_from_script("hello")

    assert [pane.get("title") for pane in spec["panes"]] == [
        None,
        "Live output",
        "hidden",
        {
            "text": "Preview",
            "alignment_x": "right",
            "alignment_y": "bottom",
            "position_x": "0.4rem",
            "position_y": "0.3rem",
        },
    ]


def test_studio_directive_accepts_targeted_browser_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

```yaml studio-directive
config:
  browser: {}
```

```yaml studio-directive
panes:
- id: terminal
  kind: terminal
- id: preview
  kind: browser
```

```yaml studio-directive
beat:
  id: hello
  heading: Open the preview
  narration: Open the generated player.
  layout:
    areas:
    - [terminal, preview]
  panes:
    terminal:
    - id: session
      actions:
      - id: watch
        run: omegaflow recording=demo action=watch
        browser_handoff:
          target: preview
        timing: realtime
    preview:
    - id: player
      actions:
      - id: open
        open_page:
          handoff: watch
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        studio_config_module,
        "RECORDING_SCRIPT_DIR",
        recordings_dir,
    )

    plan = normalize_recording_plan(recording_from_script("hello"))

    assert len(plan.browser_handoffs) == 1
    assert plan.browser_handoffs[0].target_pane_id == "preview"


def test_studio_directive_accepts_realtime_input_on_explicit_terminal_pane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
panes:
- id: terminal
  kind: terminal
```

```yaml studio-directive
beat:
  id: edit
  heading: Edit a file
  layout:
    areas:
    - [terminal]
  panes:
    terminal:
    - id: editor
      actions:
      - id: edit-file
        run: nano example.txt
        timing: realtime
        input:
        - wait_for: GNU nano
          timeout: 2
        - text: updated
          interval: 0.01
        - {control: x}
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        studio_config_module,
        "RECORDING_SCRIPT_DIR",
        recordings_dir,
    )

    plan = normalize_recording_plan(recording_from_script("hello"))

    action = plan.beats[0].pane_tracks[0].beats[0].actions[0]
    input_steps = action.config["commands"][0]["input"]
    assert input_steps[0]["wait_for"] == "GNU nano"
    assert input_steps[0]["timeout"] == 2
    assert input_steps[1]["text"] == "updated"
    assert input_steps[1]["interval"] == 0.01
    assert input_steps[2]["control"] == "x"


def test_recording_frontmatter_rejects_pane_declarations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
panes:
- id: terminal
  kind: terminal
---

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

    with pytest.raises(StudioConfigError, match="panes"):
        recording_from_script("hello")


def test_recordings_config_rejects_pane_declarations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recordings_dir / "config.yaml").write_text(
        "panes:\n- id: terminal\n  kind: terminal\n",
        encoding="utf-8",
    )
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

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

    with pytest.raises(StudioConfigError, match="panes"):
        recording_from_script("hello")


def test_rec_override_rejects_pane_declarations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

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

    with pytest.raises(
        StudioConfigError,
        match=r"rec cannot override recording identity/generated fields: panes",
    ):
        recording_from_script(
            "hello",
            overrides={"panes": [{"id": "terminal", "kind": "terminal"}]},
        )


def test_studio_directive_rejects_duplicate_pane_declarations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
panes:
- id: terminal
  kind: terminal
```

```yaml studio-directive
panes:
- id: other
  kind: terminal
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

    with pytest.raises(StudioConfigError, match="duplicate studio-directive panes"):
        recording_from_script("hello")


def test_studio_directive_rejects_empty_pane_declaration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
panes: []
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

    with pytest.raises(
        StudioConfigError,
        match="studio-directive panes must be a non-empty list",
    ):
        recording_from_script("hello")


@pytest.mark.parametrize("pane_block", ["same", "later"])
def test_studio_directive_requires_panes_before_every_beat_declaration(
    tmp_path: Path,
    monkeypatch,
    pane_block: str,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    beat_value = """
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
"""
    pane_declaration = """
panes:
- id: terminal
  kind: terminal
"""
    pane_source = (
        pane_declaration + "```\n"
        if pane_block == "same"
        else "```\n\n```yaml studio-directive\n" + pane_declaration + "```\n"
    )
    (recording_dir / "index.md").write_text(
        (
            """
---
kind: video
title: Hello Video
---

```yaml studio-directive
"""
            + beat_value
            + pane_source
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(
        StudioConfigError,
        match="studio-directive panes must appear before any beat declaration",
    ):
        recording_from_script("hello")


def test_studio_directive_requires_panes_before_empty_beat_declaration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        f"""
---
kind: video
title: Hello Video
---

```yaml studio-directive
beat: null
panes:
- id: terminal
  kind: terminal
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(
        StudioConfigError,
        match="studio-directive panes must appear before any beat declaration",
    ):
        recording_from_script("hello")


def test_explicit_multi_pane_beat_requires_pane_declaration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
kind: video
title: Hello Video
---

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  layout:
    areas:
    - [left, right]
  panes:
    left:
    - id: left
      actions:
      - id: show-left
        show:
          text: Left
    right:
    - id: right
      actions:
      - id: show-right
        show:
          text: Right
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(
        StudioConfigError,
        match="explicit multi-pane beat hello requires a preceding panes declaration",
    ):
        recording_from_script("hello")


@pytest.mark.parametrize("structure", ["panes", "beat"])
def test_studio_directive_requires_config_before_structure(
    tmp_path: Path,
    monkeypatch,
    structure: str,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    structure_block = (
        """panes:
- id: terminal
  kind: terminal
"""
        if structure == "panes"
        else """beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
"""
    )
    (recording_dir / "index.md").write_text(
        f"""
---
title: Hello Video
---

```yaml studio-directive
{structure_block.rstrip()}
```

```yaml studio-directive
config:
  audio:
    enabled: false
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(
        StudioConfigError,
        match="studio-directive config must appear before panes or beat",
    ):
        recording_from_script("hello")


def test_studio_directive_rejects_duplicate_config_declarations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

```yaml studio-directive
config:
  audio:
    enabled: false
```

```yaml studio-directive
config:
  capture:
    headless: true
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(StudioConfigError, match="duplicate studio-directive config"):
        recording_from_script("hello")


@pytest.mark.parametrize("removed_key", ["scene", "beats"])
def test_studio_directive_rejects_removed_structure_keys(removed_key: str) -> None:
    value = "{id: hello, title: Hello}" if removed_key == "scene" else "[]"
    script = f"""
```yaml studio-directive
{removed_key}: {value}
```
""".lstrip()

    with pytest.raises(StudioConfigError, match=removed_key):
        studio_directive_blocks(script)


def test_recording_frontmatter_rejects_production_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
capture:
  headless: true
---
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(StudioConfigError, match="capture"):
        recording_from_script("hello")


def test_recording_frontmatter_requires_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        "---\nkind: video\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    with pytest.raises(StudioConfigError, match="non-empty title"):
        recording_from_script("hello")


def test_recording_narration_scene_is_generated_from_directory_and_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

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

    spec = recording_from_script("hello")

    assert spec["narration"]["scene"] == {"id": "hello", "title": "Hello Video"}


def test_studio_directive_panes_build_a_multi_pane_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "hello"
    recording_dir.mkdir(parents=True)
    (recording_dir / "index.md").write_text(
        """
---
title: Hello Video
---

```yaml studio-directive
config:
  audio:
    enabled: false
```

```yaml studio-directive
panes:
- id: definition
  kind: visualization
- id: terminal
  kind: terminal
beat:
  id: hello
  heading: Say Hello
  narration: Print one line.
  layout:
    areas:
    - [definition]
    - [terminal]
  panes:
    definition:
    - id: source
      actions:
      - id: show-source
        show:
          language: yaml
          text: "run: printf hello"
    terminal:
    - id: output
      actions:
      - id: run-command
        run: printf hello
```
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config_module, "RECORDING_SCRIPT_DIR", recordings_dir)

    spec = recording_from_script("hello")
    plan = normalize_recording_plan(spec)

    assert [(pane.id, pane.kind.value) for pane in plan.panes] == [
        ("definition", "visualization"),
        ("terminal", "terminal"),
    ]


def test_repository_video_recordings_use_current_source_structure() -> None:
    recordings_dir = Path(__file__).resolve().parents[1] / "recordings"

    for recording_id in list_recording_ids(recordings_dir):
        source_path = recordings_dir / recording_id / "index.md"
        source = source_path.read_text(encoding="utf-8")
        frontmatter, body = studio_config_module.split_frontmatter(
            source,
            source=source_path,
        )
        if frontmatter.get("kind") == "collection":
            continue

        assert set(frontmatter) <= {"kind", "title", "description"}, source_path
        blocks = studio_directive_blocks(body, resolve=False)
        assert all(len(block) == 1 for block in blocks), source_path
        directive_names = [next(iter(block)) for block in blocks]
        expected_names = (
            (["config"] if "config" in directive_names else [])
            + (["panes"] if "panes" in directive_names else [])
            + ["beat"] * directive_names.count("beat")
        )
        assert directive_names == expected_names, source_path
        assert "beat" in directive_names, source_path

        recording_from_script(recording_id, recording_dir=recordings_dir)


def test_terminal_highlight_demo_combines_exact_and_multiline_regex_targets() -> None:
    recordings_dir = Path(__file__).resolve().parents[1] / "recordings"
    spec = recording_from_script(
        "reference/terminal-highlights",
        recording_dir=recordings_dir,
    )

    plan = normalize_recording_plan(spec)
    demonstrated_narration = (
        "Highlight will start @exact_start@ now, and will end now.@exact_end@"
    )
    source = (
        recordings_dir / "reference" / "terminal-highlights" / "index.md"
    ).read_text(encoding="utf-8")
    assert source.count(demonstrated_narration) == 2

    assert [(pane.id, pane.kind.value) for pane in plan.panes] == [
        ("definition", "visualization"),
        ("terminal", "terminal"),
    ]
    assert len(plan.beats) == 1
    beat = plan.beats[0]
    assert [track.pane_id for track in beat.pane_tracks] == [
        "definition",
        "terminal",
    ]
    definition_beats = beat.pane_tracks[0].beats
    assert [pane_beat.id for pane_beat in definition_beats] == [
        "exact-overview",
        "regex-target",
        "combined-targets",
    ]
    assert definition_beats[0].start_join is None
    assert [
        pane_beat.start_join.event.qualified_id
        for pane_beat in definition_beats[1:]
        if pane_beat.start_join is not None
    ] == [
        "voiceover.regex_start.started",
        "voiceover.combined_start.started",
    ]
    definition_effects = [
        highlight
        for highlight in beat.effects
        if highlight.pane_id == "definition"
    ]
    assert len(definition_effects) == 3
    assert all(highlight.color == "brand" for highlight in definition_effects)
    assert [
        [(target.pattern, target.occurrence) for target in highlight.targets]
        for highlight in definition_effects
    ] == [
        [("@exact_start@", 1), ("@exact_start@", 2)],
        [("@exact_end@", 1), ("@exact_end@", 2)],
        [("now, and will end now.", 1)],
    ]
    terminal_beat = beat.pane_tracks[1].beats[0]
    terminal_effects = [
        highlight
        for highlight in beat.effects
        if highlight.pane_id == "terminal"
    ]
    assert [
        [(target.kind, target.pattern) for target in highlight.targets]
        for highlight in terminal_effects
    ] == [
        [("text", "Renderer: ready")],
        [("regex", r"Elapsed since start of video:\n.*")],
        [
            ("text", "Renderer: ready"),
            ("regex", r"Elapsed since start of video:\n.*"),
        ],
    ]
    assert all(
        target.occurrence == 1
        for highlight in terminal_effects
        for target in highlight.targets
    )
    assert (
        terminal_beat.actions[0].config["commands"][0]["timing"]
        == "realtime"
    )


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
            "inputs": [],
            "produces": {},
            "output": None,
            "expect": {
                "exit_code": 0,
                "output_contains": [],
                "output_regex": [],
                "file_exists": [],
            },
            "id": None,
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
        "@quickstart_script_start@ a test video script you can run immediately. "
        "@quickstart_script_end@"
        in bootstrap_beat["narration"]
    )
    assert bootstrap_beat["effects"] == [
        {
            "highlight": {
                "targets": [{"text": ".omegaflow/config.yaml"}],
                "start": "@project_settings_start@",
                "end": "@project_settings_end@",
            }
        },
        {
            "highlight": {
                "targets": [{"text": "recordings/config.yaml"}],
                "start": "@recording_defaults_start@",
                "end": "@recording_defaults_end@",
            }
        },
        {
            "highlight": {
                "targets": [{"text": "recordings/test-video/index.md"}],
                "start": "@quickstart_script_start@",
                "end": "@quickstart_script_end@",
            }
        },
    ]
    assert beats_by_id["build"]["narration_take"] == "build-and-browser"
    assert beats_by_id["build"]["guide"]["commands"] == [
        "omegaflow recording=test-video action=build",
        "omegaflow recording=test-video action=watch",
    ]
    assert beats_by_id["install"]["guide"]["success_hint"] == (
        "OmegaFlow is installed and the omegaflow command is available."
    )
    assert beats_by_id["bootstrap"]["guide"]["success_hint"] == (
        "The recording workspace contains project settings, recording defaults, "
        "and the test video script."
    )
    assert beats_by_id["build"]["heading"] == "Build the Video"
    assert [command["id"] for command in build_commands] == [
        "build_command",
        "watch_command",
    ]
    assert bootstrap_beat["actions"][0]["commands"][0]["run"] == (
        'cd "$HOMEPAGE_DEMO_ROOT" && '
        'omegaflow project_root="$HOMEPAGE_DEMO_ROOT" bootstrap=project'
    )
    assert build_commands[0]["run"] == (
        "omegaflow recording=test-video action=build force=true"
    )
    assert build_commands[0]["timing"] == "realtime"
    assert "follow_along" not in build_commands[0]
    assert build_commands[1]["display"] == (
        "omegaflow recording=test-video action=watch"
    )
    assert build_commands[1]["after"] == "@watch@"
    assert build_commands[1]["browser_handoff"] is True
    assert build_commands[1]["timing"] == "realtime"
    assert "follow_along" not in build_commands[1]
    assert build_commands[1]["show_prompt_after"] is False
    assert build_commands[1]["run"] == (
        "omegaflow recording=test-video action=watch watch_port=43123 "
        "autoplay=false"
    )
    assert build_commands[1].get("output") is None
    assert browser_beat["narration_take"] == "build-and-browser"
    assert browser_beat["heading"] == "Explore the Player"
    assert browser_beat["guide"] == {
        "summary": "This beat demonstrated beat previews and playback speed.",
        "success_hint": "To learn more, explore the guides or read the docs.",
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

    generated = studio.bootstrap_recording_text("test-video", "Test Video")
    assert "kind: video" in generated
    generated_beats = [
        block["beat"]
        for block in studio_directive_blocks(generated, resolve=False)
        if "beat" in block
    ]
    assert [beat["id"] for beat in generated_beats] == [
        "first-video-beat",
        "second-video-beat",
    ]
    assert generated_beats[0]["heading"] == "First Video Beat"
    assert generated_beats[0]["narration"] == (
        "This is the first beat in the generated test video."
    )
    assert generated_beats[0]["viewer_hold"] == 3
    assert generated_beats[1]["heading"] == "Second Video Beat"
    assert generated_beats[1]["narration"] == (
        "This is the second beat in the generated test video."
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
                                {
                                    "run": (
                                        'printf "%s\\n" "$HOMEPAGE_DEMO_ROOT" '
                                        '> "$OMEGAFLOW_RUN_DIR/demo-root.txt" && '
                                        'cd "$HOMEPAGE_DEMO_ROOT" && '
                                        "omegaflow "
                                        'project_root="$HOMEPAGE_DEMO_ROOT" '
                                        "bootstrap=project "
                                        '> "$OMEGAFLOW_RUN_DIR/bootstrap-output.txt"'
                                    )
                                },
                            ]
                        }
                    ],
                }
            ],
            "cleanup": recording["cleanup"],
        }
    )
    repository = tmp_path / "repository"
    (repository / ".sl").mkdir(parents=True)
    run_dir = repository / "recordings" / ".omegaflow" / "runs" / "test"
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False,
            timeout_seconds=60.0,
        )
    )

    coordinator.capture(
        plan,
        run_dir,
        workspace=root,
        working_directory=root,
        environment={
            "OMEGAFLOW_TEST_ROOT": str(root),
            "PATH": os.environ.get("PATH", ""),
        },
    )

    bootstrap_output = (run_dir / "bootstrap-output.txt").read_text(encoding="utf-8")
    demo_root = Path(
        (run_dir / "demo-root.txt").read_text(encoding="utf-8").strip()
    )
    assert "could not verify whether" not in bootstrap_output
    assert not demo_root.is_relative_to(repository)
    assert not demo_root.exists()
    assert not list((run_dir / ".tmp").glob("omegaflow-quickstart-env.*"))
    assert not list((run_dir / ".tmp").glob("omegaflow-quickstart-demo.*"))


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


def test_bootstrap_creates_composable_project_workspace(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "force": False,
        }
    )

    assert status == 0
    tool_config = (tmp_path / ".omegaflow" / "config.yaml").read_text(
        encoding="utf-8"
    )
    shared_config = (workspace / "config.yaml").read_text(encoding="utf-8")
    recording = (workspace / "test-video" / "index.md").read_text(
        encoding="utf-8"
    )
    support_dir = workspace / "test-video" / "scripts"

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
    assert "\nid:" not in recording
    assert "type: standalone_html" in recording
    assert "cast:" not in recording
    assert "file: ${outputs.asset_dir}/index.html" in recording
    assert "This Markdown file is the source for one generated terminal video." in recording
    assert "header contains its metadata" in recording
    assert "fenced `studio-directive` blocks configure" in recording
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


def test_project_bootstrap_is_a_typed_operation_with_default_build_preserved() -> None:
    bootstrap = compose_studio_config(None, ("bootstrap=project",))
    default = compose_studio_config(None, ())

    assert bootstrap["bootstrap"] == "project"
    assert bootstrap["action"] is None
    assert default["bootstrap"] is None
    assert default["action"] is None
    assert studio.validate_action(default["action"]) == "build"


def test_project_bootstrap_creates_the_minimal_project_tree(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(None, ("bootstrap=project",))

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    files = sorted(
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert files == [
        ".omegaflow/.gitignore",
        ".omegaflow/config.yaml",
        ".omegaflow/omegaflow-secret.env",
        "recordings/.gitignore",
        "recordings/config.yaml",
        "recordings/test-video/index.md",
    ]
    assert (tmp_path / ".omegaflow" / ".gitignore").read_text(
        encoding="utf-8"
    ) == "/omegaflow-secret.env\n"
    secret_file = tmp_path / ".omegaflow" / "omegaflow-secret.env"
    assert secret_file.read_text(
        encoding="utf-8"
    ) == "# OPENAI_OMEGAFLOW_API_KEY=\n"
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "recordings" / ".gitignore").read_text(
        encoding="utf-8"
    ) == "**/app.secret.env\n"
    recording = (
        tmp_path / "recordings" / "test-video" / "index.md"
    ).read_text(encoding="utf-8")
    assert "\nid:" not in recording
    assert "title: Test Video" in recording
    assert "quickstart" not in recording.lower()


def test_project_bootstrap_anchors_tool_files_at_project_root(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(
        None,
        ("bootstrap=project", "workspace=media/recordings"),
    )

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert (tmp_path / ".omegaflow" / "config.yaml").is_file()
    assert not (tmp_path / "media" / ".omegaflow").exists()
    tool_config = (tmp_path / ".omegaflow" / "config.yaml").read_text(
        encoding="utf-8"
    )
    assert "recording_dir: media/recordings" in tool_config
    assert "data_dir: media/recordings/.omegaflow" in tool_config


def test_project_bootstrap_adds_secret_rule_to_existing_tool_ignore(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool_dir = tmp_path / ".omegaflow"
    tool_dir.mkdir()
    (tool_dir / ".gitignore").write_text("# user rules\n", encoding="utf-8")
    config = compose_studio_config(None, ("bootstrap=project",))

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    assert (tool_dir / ".gitignore").read_text(encoding="utf-8") == (
        "# user rules\n/omegaflow-secret.env\n"
    )
    assert (tool_dir / "config.yaml").exists()
    assert (tool_dir / "omegaflow-secret.env").exists()
    assert (tmp_path / "recordings").exists()


def test_project_bootstrap_refuses_tracked_secret_target_before_writing(
    tmp_path, monkeypatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for tracked-secret validation")
    monkeypatch.chdir(tmp_path)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    tool_dir = tmp_path / ".omegaflow"
    tool_dir.mkdir()
    (tool_dir / ".gitignore").write_text(
        studio.BOOTSTRAP_TOOL_GITIGNORE,
        encoding="utf-8",
    )
    secret_file = tool_dir / "omegaflow-secret.env"
    secret_file.write_text("tracked-value\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", ".omegaflow/omegaflow-secret.env"],
        cwd=tmp_path,
        check=True,
    )
    config = compose_studio_config(
        None,
        ("bootstrap=project", "force=true"),
    )

    with pytest.raises(
        studio.StudioError,
        match=r"omegaflow-secret\.env.*tracked or staged",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert secret_file.read_text(encoding="utf-8") == "tracked-value\n"
    assert not (tool_dir / "config.yaml").exists()
    assert not (tmp_path / "recordings").exists()


def test_project_bootstrap_refuses_sapling_tracked_secret_target_before_writing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sl").mkdir()
    tool_dir = tmp_path / ".omegaflow"
    tool_dir.mkdir()
    (tool_dir / ".gitignore").write_text(
        studio.BOOTSTRAP_TOOL_GITIGNORE,
        encoding="utf-8",
    )
    secret_file = tool_dir / "omegaflow-secret.env"
    secret_file.write_text("tracked-value\n", encoding="utf-8")

    monkeypatch.setattr(
        studio.shutil,
        "which",
        lambda command: "/usr/bin/sl" if command == "sl" else None,
    )

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        if command[-1] == "root":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{tmp_path}\n",
                stderr="",
            )
        assert command[-2:] == [
            "files",
            ".omegaflow/omegaflow-secret.env",
        ]
        assert env is not None
        assert env["CHGDISABLE"] == "1"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=".omegaflow/omegaflow-secret.env\n",
            stderr="",
        )

    monkeypatch.setattr(studio.subprocess, "run", fake_run)
    config = compose_studio_config(
        None,
        ("bootstrap=project", "force=true"),
    )

    with pytest.raises(
        studio.StudioError,
        match=r"omegaflow-secret\.env.*tracked or staged",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert secret_file.read_text(encoding="utf-8") == "tracked-value\n"
    assert not (tool_dir / "config.yaml").exists()
    assert not (tmp_path / "recordings").exists()


def test_project_bootstrap_warns_and_continues_when_repository_cannot_be_inspected(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sl").mkdir()
    tool_dir = tmp_path / ".omegaflow"
    tool_dir.mkdir()
    (tool_dir / ".gitignore").write_text(
        studio.BOOTSTRAP_TOOL_GITIGNORE,
        encoding="utf-8",
    )
    monkeypatch.setattr(studio.shutil, "which", lambda _command: None)
    config = compose_studio_config(None, ("bootstrap=project",))

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    output = capsys.readouterr().out
    assert "warn " in output
    assert "could not verify whether .omegaflow/omegaflow-secret.env" in output
    assert "Sapling executable is unavailable" in output
    assert "continuing without VCS verification" in output
    assert (tool_dir / "omegaflow-secret.env").is_file()
    assert (tool_dir / "config.yaml").is_file()
    assert (tmp_path / "recordings" / "test-video" / "index.md").is_file()


def test_sapling_untracked_secret_is_safe_to_bootstrap(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / ".sl").mkdir()
    secret_file = tmp_path / ".omegaflow" / "omegaflow-secret.env"
    monkeypatch.setattr(
        studio.shutil,
        "which",
        lambda command: "/usr/bin/sl" if command == "sl" else None,
    )

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if command[-1] == "root":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{tmp_path}\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(studio.subprocess, "run", fake_run)

    assert studio.vcs_tracks_path(tmp_path, secret_file) is False


def test_project_bootstrap_refuses_symlinked_secret_target_before_writing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool_dir = tmp_path / ".omegaflow"
    tool_dir.mkdir()
    (tool_dir / ".gitignore").write_text(
        studio.BOOTSTRAP_TOOL_GITIGNORE,
        encoding="utf-8",
    )
    outside = tmp_path / "outside.env"
    outside.write_text("outside-value\n", encoding="utf-8")
    (tool_dir / "omegaflow-secret.env").symlink_to(outside)
    config = compose_studio_config(
        None,
        ("bootstrap=project", "force=true"),
    )

    with pytest.raises(
        studio.StudioError,
        match=r"omegaflow-secret\.env.*symbolic link",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert outside.read_text(encoding="utf-8") == "outside-value\n"
    assert not (tool_dir / "config.yaml").exists()
    assert not (tmp_path / "recordings").exists()


@pytest.mark.parametrize(
    "relative_target",
    [
        ".omegaflow/config.yaml",
        "recordings/config.yaml",
        "recordings/test-video/index.md",
    ],
)
@pytest.mark.parametrize("dry_run", [None, "diff"])
def test_project_bootstrap_refuses_symlinked_generated_target_before_writing(
    tmp_path,
    monkeypatch,
    relative_target,
    dry_run,
) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside_text = "studio:\n  recording_dir: recordings\n"
    outside.write_text(outside_text, encoding="utf-8")
    target = tmp_path / relative_target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)
    overrides = ["bootstrap=project", "force=true"]
    if dry_run is not None:
        overrides.append(f"dry_run={dry_run}")
    config = compose_studio_config(None, tuple(overrides))

    with pytest.raises(
        studio.StudioError,
        match="symbolic link",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert outside.read_text(encoding="utf-8") == outside_text
    assert not (tmp_path / ".omegaflow" / "omegaflow-secret.env").exists()


def test_bootstrap_private_file_is_restricted_when_created(
    tmp_path, monkeypatch
) -> None:
    observed_modes: list[int] = []
    real_open = os.open

    def observing_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
    ) -> int:
        fd = real_open(path, flags, mode)
        observed_modes.append(os.fstat(fd).st_mode & 0o777)
        return fd

    monkeypatch.setattr(studio.os, "open", observing_open)
    secret_file = tmp_path / "omegaflow-secret.env"

    assert studio.write_bootstrap_file(
        secret_file,
        studio.BOOTSTRAP_TOOL_SECRET_ENV,
        mode=0o600,
    ) == "created"

    assert observed_modes == [0o600]
    assert secret_file.stat().st_mode & 0o777 == 0o600


def test_project_bootstrap_rejects_action_before_writing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(
        None,
        ("bootstrap=project", "action=build"),
    )

    with pytest.raises(
        studio.StudioError,
        match="bootstrap and action are mutually exclusive",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert list(tmp_path.iterdir()) == []


def test_project_bootstrap_preserves_existing_files_until_forced(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(None, ("bootstrap=project",))
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0
    capsys.readouterr()

    secret_file = tmp_path / ".omegaflow" / "omegaflow-secret.env"
    tool_ignore = tmp_path / ".omegaflow" / ".gitignore"
    recordings_ignore = tmp_path / "recordings" / ".gitignore"
    recording_file = tmp_path / "recordings" / "test-video" / "index.md"
    secret_file.write_text(
        "OPENAI_OMEGAFLOW_API_KEY=user-owned-secret\n",
        encoding="utf-8",
    )
    secret_file.chmod(0o640)
    tool_ignore.write_text(
        "# user tool rule\n/omegaflow-secret.env\n",
        encoding="utf-8",
    )
    recordings_ignore.write_text(
        "# user recording rule\n**/app.secret.env\n",
        encoding="utf-8",
    )
    recording_file.write_text("user-owned recording\n", encoding="utf-8")

    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0
    output = capsys.readouterr().out
    assert secret_file.read_text(encoding="utf-8") == (
        "OPENAI_OMEGAFLOW_API_KEY=user-owned-secret\n"
    )
    assert secret_file.stat().st_mode & 0o777 == 0o640
    assert recording_file.read_text(encoding="utf-8") == "user-owned recording\n"
    assert "exists .omegaflow/omegaflow-secret.env" in output
    assert "exists recordings/test-video/index.md" in output

    forced = compose_studio_config(
        None,
        ("bootstrap=project", "force=true"),
    )
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(forced)) == 0
    output = capsys.readouterr().out
    assert secret_file.read_text(
        encoding="utf-8"
    ) == "OPENAI_OMEGAFLOW_API_KEY=user-owned-secret\n"
    assert secret_file.stat().st_mode & 0o777 == 0o640
    assert tool_ignore.read_text(encoding="utf-8") == (
        "# user tool rule\n/omegaflow-secret.env\n"
    )
    assert recordings_ignore.read_text(encoding="utf-8") == (
        "# user recording rule\n**/app.secret.env\n"
    )
    assert "\nid:" not in recording_file.read_text(encoding="utf-8")
    assert "exists .omegaflow/omegaflow-secret.env" in output
    assert "updated recordings/test-video/index.md" in output


def test_action_bootstrap_is_not_a_compatibility_alias() -> None:
    with pytest.raises(StudioConfigError):
        compose_studio_config(None, ("action=bootstrap",))


def test_bootstrap_default_recording_is_test_video(tmp_path, capsys) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "force": False,
        }
    )
    output = capsys.readouterr().out

    assert status == 0
    assert "next    " not in output
    recording = (workspace / "test-video" / "index.md").read_text(
        encoding="utf-8"
    )
    support_dir = workspace / "test-video" / "scripts"

    assert "\nid:" not in recording
    assert "title: Test Video" in recording
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
    assert "Bootstrap dry run: test-video" in output
    assert "Recording workspace:" in output
    assert "Files:" in output
    assert "create" in output
    assert ".omegaflow/config.yaml" in output
    assert "recordings/config.yaml" in output
    assert "recordings/test-video/index.md" in output
    assert "recordings/test-video/scripts/hello.sh" not in output
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
    assert "Bootstrap dry run diff: test-video" in output
    assert "--- /dev/null" in output
    assert f"+++ {tmp_path}/.omegaflow/config.yaml" in output
    assert "+studio:" in output
    assert "+  recording_dir: recordings" in output
    assert "+  data_dir: recordings/.omegaflow" in output
    assert "+id:" not in output
    assert '+      run: "# First video beat"' in output
    assert '+      run: "# Second video beat"' in output
    assert "No files were written." in output
    assert not (tmp_path / ".omegaflow").exists()
    assert not workspace.exists()


def test_bootstrap_dry_run_diff_does_not_disclose_existing_secret(
    tmp_path, capsys
) -> None:
    workspace = tmp_path / "recordings"
    tool_dir = tmp_path / ".omegaflow"
    tool_dir.mkdir()
    (tool_dir / ".gitignore").write_text(
        studio.BOOTSTRAP_TOOL_GITIGNORE,
        encoding="utf-8",
    )
    secret_file = tool_dir / "omegaflow-secret.env"
    secret_file.write_text(
        "OPENAI_OMEGAFLOW_API_KEY=TOP-SECRET-SENTINEL\n",
        encoding="utf-8",
    )
    secret_file.chmod(0o600)

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "dry_run": "diff",
            "force": False,
        }
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "TOP-SECRET-SENTINEL" not in output
    assert "omegaflow-secret.env (private content hidden)" in output
    assert secret_file.read_text(encoding="utf-8") == (
        "OPENAI_OMEGAFLOW_API_KEY=TOP-SECRET-SENTINEL\n"
    )


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


def test_project_bootstrap_rejects_recording_before_writing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(
        None,
        ("bootstrap=project", "recording=custom"),
    )

    with pytest.raises(
        studio.StudioError,
        match="bootstrap does not accept recording",
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert list(tmp_path.iterdir()) == []


def test_tutorial_bootstrap_requires_an_existing_project_before_writing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = compose_studio_config(None, ("bootstrap=tutorial",))

    with pytest.raises(
        studio.StudioError,
        match=(
            r"bootstrap=tutorial requires an OmegaFlow project; "
            r"run `omegaflow bootstrap=project` first"
        ),
    ):
        studio.run_tool_from_hydra_cfg(OmegaConf.create(config))

    assert list(tmp_path.iterdir()) == []


def test_tutorial_bootstrap_materializes_packaged_tiny_canvas_workspace(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    project = compose_studio_config(None, ("bootstrap=project",))
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(project)) == 0
    capsys.readouterr()

    tutorial = compose_studio_config(None, ("bootstrap=tutorial",))
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(tutorial)) == 0

    tutorial_root = tmp_path / "recordings" / "sunset-beach"
    files = sorted(
        path.relative_to(tutorial_root).as_posix()
        for path in tutorial_root.rglob("*")
        if path.is_file()
    )
    assert files == [
        ".nanorc",
        "app/app.js",
        "app/index.html",
        "app/server.py",
        "app/styles.css",
        "example.svg",
        "index.md",
        "scripts/inspect_artwork.py",
        "scripts/reset_artwork.py",
        "scripts/tiny_canvas.py",
    ]

    recording = (tutorial_root / "index.md").read_text(encoding="utf-8")
    assert "\nid:" not in recording
    assert "title: Refine a Sunset Beach Poster" in recording
    assert "id: inspect-draft" in recording
    assert "medium: terminal" in recording
    assert "name: prepare the example artwork" in recording
    assert "base_url:" not in recording
    assert "presentation:" not in recording
    assert "publish:" not in recording
    assert "narration:" not in recording
    assert (
        "run: python recordings/sunset-beach/scripts/reset_artwork.py" in recording
    )
    assert (
        "run: python recordings/sunset-beach/scripts/inspect_artwork.py" in recording
    )

    starter_plan = normalize_recording_plan(
        recording_from_script(
            "sunset-beach",
            recording_dir=tmp_path / "recordings",
        )
    )
    assert [beat.id for beat in starter_plan.beats] == ["inspect-draft"]
    starter_beat = starter_plan.beats[0]
    assert starter_beat.medium.value == "terminal"
    assert (
        starter_beat.actions[0].config["commands"][0]["run"]
        == "python recordings/sunset-beach/scripts/inspect_artwork.py"
    )
    assert starter_plan.narration_stream.segments == ()
    assert starter_plan.narration_takes == ()

    application = (tutorial_root / "app" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "Tiny Canvas" in application
    assert 'id="artwork-title"' in application
    assert 'data-testid="artwork-title"' in application
    assert 'id="canvas"' in application
    assert 'id="save-artwork"' not in application
    assert 'id="export-artwork"' in application
    assert 'data-testid="export-artwork"' in application
    app_script = (tutorial_root / "app" / "app.js").read_text(encoding="utf-8")
    assert "function filenameForTitle(title)" in app_script
    assert "Save as ${filenameForTitle(titleInput.value)}" in app_script
    assert "Saved ${result.filename}" in app_script
    server = (tutorial_root / "app" / "server.py").read_text(encoding="utf-8")
    assert "filename_for_title" in server
    assert 'ARTWORK = STATE_DIR / filename_for_title("Sunset Study")' in server
    launcher = (tutorial_root / "scripts" / "tiny_canvas.py").read_text(
        encoding="utf-8"
    )
    assert '"--view"' in launcher
    assert 'f"/files/{filename}"' in launcher

    example = (tutorial_root / "example.svg").read_text(encoding="utf-8")
    assert 'id="sun"' in example
    assert 'data-testid="sun"' in example
    assert 'id="sun-glasses"' in example
    assert 'id="sun-smile"' in example
    assert 'id="sea"' in example
    assert example.index('id="sun"') < example.index('id="sea"')
    assert 'id="coconut-tree"' in example
    assert 'data-testid="coconut-tree"' in example
    assert 'id="sunset-target"' in example
    assert 'data-testid="sunset-target"' in example
    assert 'cx="405" cy="390"' in example
    assert 'id="tree-target"' in example
    assert 'data-testid="tree-target"' in example
    assert 'cx="565" cy="425"' in example
    assert example.count('class="palm-leaf"') >= 6
    assert not (tutorial_root / "sunset-study.svg").exists()

    output = capsys.readouterr().out
    created = [
        line.removeprefix("created ")
        for line in output.splitlines()
        if line.startswith("created ")
    ]
    assert created == [
        f"recordings/sunset-beach/{relative_path}" for relative_path in files
    ]
    assert "next    " not in output


def test_tiny_canvas_filename_is_derived_from_the_artwork_title() -> None:
    from omegaflow.tutorial.tiny_canvas.app.server import filename_for_title

    assert filename_for_title("Sunset Study") == "sunset-study.svg"
    assert filename_for_title("Coconut Sunset") == "coconut-sunset.svg"
    assert filename_for_title("  Étude: Sea & Sky  ") == "etude-sea-sky.svg"


def test_tutorial_bootstrap_preserves_user_owned_files_until_forced(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    for mode in ("project", "tutorial"):
        config = compose_studio_config(None, (f"bootstrap={mode}",))
        assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0
    capsys.readouterr()

    tutorial_root = tmp_path / "recordings" / "sunset-beach"
    recording = tutorial_root / "index.md"
    application = tutorial_root / "app" / "index.html"
    recording.write_text("user recording\n", encoding="utf-8")
    application.write_text("user application\n", encoding="utf-8")

    tutorial = compose_studio_config(None, ("bootstrap=tutorial",))
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(tutorial)) == 0
    output = capsys.readouterr().out

    assert recording.read_text(encoding="utf-8") == "user recording\n"
    assert application.read_text(encoding="utf-8") == "user application\n"
    assert "exists recordings/sunset-beach/index.md" in output
    assert "exists recordings/sunset-beach/app/index.html" in output

    forced = compose_studio_config(
        None,
        ("bootstrap=tutorial", "force=true"),
    )
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(forced)) == 0
    output = capsys.readouterr().out

    assert "\nid:" not in recording.read_text(encoding="utf-8")
    assert "Tiny Canvas" in application.read_text(encoding="utf-8")
    assert "updated recordings/sunset-beach/index.md" in output
    assert "updated recordings/sunset-beach/app/index.html" in output


def test_tutorial_bootstrap_uses_the_configured_recording_path(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project = compose_studio_config(
        None,
        ("bootstrap=project", "workspace=media/recordings"),
    )
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(project)) == 0

    tutorial = compose_studio_config(None, ("bootstrap=tutorial",))
    assert studio.run_tool_from_hydra_cfg(OmegaConf.create(tutorial)) == 0

    recording = (
        tmp_path / "media" / "recordings" / "sunset-beach" / "index.md"
    ).read_text(encoding="utf-8")
    assert (
        "run: python media/recordings/sunset-beach/scripts/reset_artwork.py"
        in recording
    )
    assert "{{ tutorial_path }}" not in recording


def test_tutorial_runtime_state_does_not_invalidate_the_recording_source(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for mode in ("project", "tutorial"):
        config = compose_studio_config(None, (f"bootstrap={mode}",))
        assert studio.run_tool_from_hydra_cfg(OmegaConf.create(config)) == 0

    recording_root = tmp_path / "recordings" / "sunset-beach"
    roots = (tmp_path / "recordings" / "config.yaml", recording_root)
    before = studio.watch_source_fingerprint(roots)

    subprocess.run(
        [
            sys.executable,
            str(recording_root / "scripts" / "reset_artwork.py"),
        ],
        check=True,
    )

    assert studio.watch_source_fingerprint(roots) == before
    assert not (recording_root / "artwork.svg").exists()
    assert (
        tmp_path
        / "recordings"
        / ".omegaflow"
        / "tutorial"
        / "sunset-beach"
        / "sunset-study.svg"
    ).is_file()


def test_complete_tiny_canvas_tutorial_has_linear_terminal_browser_flow(
    tmp_path: Path,
) -> None:
    recordings_dir = tmp_path / "recordings"
    recording_dir = recordings_dir / "sunset-beach"
    recording_dir.mkdir(parents=True)
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "tutorial"
        / "sunset-beach-complete.md"
    )
    shutil.copyfile(fixture, recording_dir / "index.md")

    plan = normalize_recording_plan(
        recording_from_script("sunset-beach", recording_dir=recordings_dir)
    )

    assert plan.panes == ()
    assert [beat.id for beat in plan.beats] == ["inspect-draft", "edit-artwork"]
    launch, edit = plan.beats
    assert launch.layout.areas == (("main",),)
    assert edit.layout.areas == (("main",),)
    launch_tracks = {track.pane_id: track for track in launch.pane_tracks}
    edit_tracks = {track.pane_id: track for track in edit.pane_tracks}
    assert [item.id for item in launch_tracks["main"].beats] == ["inspect-draft"]
    assert len(launch_tracks["main"].beats[0].actions) == 1
    assert [item.id for item in edit_tracks["main"].beats] == ["edit-artwork"]
    assert len(plan.setup) == 2
    assert (
        edit_tracks["main"].beats[0].actions[0].config["open_page"]["handoff"]
        == "open-editor"
    )
    browser_actions = edit_tracks["main"].beats[0].actions
    assert [action.kind for action in browser_actions[:2]] == [
        "open_page",
        "type_text",
    ]
    assert browser_actions[1].id == "rename-artwork"
    assert (
        browser_actions[1].config["type_text"]["target"]["test_id"]
        == "artwork-title"
    )
    assert browser_actions[1].config["type_text"]["text"] == "Coconut Sunset"
    assert browser_actions[1].config["type_text"]["interval_ms"] == 90
    assert browser_actions[2].kind == "drag"
    assert (
        browser_actions[2].config["drag"]["from"]["target"]["test_id"] == "sun"
    )
    assert browser_actions[3].kind == "drag"
    assert (
        browser_actions[3].config["drag"]["from"]["target"]["test_id"]
        == "coconut-tree"
    )
    assert [handoff.target_pane_id for handoff in plan.browser_handoffs] == [
        "main",
    ]
    assert (
        launch_tracks["main"]
        .beats[0]
        .actions[-1]
        .config["commands"][-1]["browser_handoff"]
        is True
    )
    assert plan.presentation["guided"] is True
    assert (
        launch.guide["summary"]
        == "The Tiny Canvas workflow is ready to validate and publish."
    )
    assert edit.guide is None
    assert edit.narration_text.startswith("Rename the poster Coconut Sunset.")
    assert [anchor.id for anchor in edit.anchors] == [
        "rename",
        "sun",
        "tree",
        "save",
    ]
    assert browser_actions[1].config["after"] == "@rename@"
    assert browser_actions[2].config["after"] == "@sun@"
    assert browser_actions[3].config["after"] == "@tree@"
    assert browser_actions[4].config["after"] == "@save@"


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


def test_watch_presentation_artifacts_uses_selected_prefix_run(
    tmp_path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "runs/.scratch/watch/hello/prepare/20260730-010203"
    manifest = run_dir / "presentation/recording.presentation.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: pytest.fail("canonical recording run should not be selected"),
    )

    bundle, artifacts = studio.watch_presentation_artifacts(
        {
            "_recording_id": "hello",
            "_watch_run_dir": str(run_dir),
        }
    )

    assert bundle == manifest.parent.resolve()
    assert artifacts == {
        "recording.presentation.json": manifest.resolve(),
    }


def test_watch_recording_url_path_targets_a_named_beat() -> None:
    assert studio.watch_recording_url_path(
        "tutorial/beat",
        beat_id="highlight",
        autoplay_countdown=True,
    ) == "/watch/tutorial/beat/?beat=highlight&autoplay=countdown"


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
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: "plan")
    monkeypatch.setattr(
        studio,
        "watch_plan_freshness",
        lambda _spec, _plan: studio.WatchPlanFreshness(
            "fresh", "fresh", Path("/run")
        ),
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


@pytest.mark.parametrize("fresh", [True, False])
def test_run_watch_checks_freshness_before_serving(
    monkeypatch,
    capsys,
    fresh: bool,
) -> None:
    events: list[str] = []
    spec = {"id": "hello", "_recording_id": "hello", "beats": []}
    plan = object()
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): spec,
    )
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: plan)

    def fake_freshness(_spec, checked_plan) -> studio.WatchPlanFreshness:
        assert checked_plan is plan
        events.append("freshness")
        state = "fresh" if fresh else "stale"
        return studio.WatchPlanFreshness(state, state, Path("/run"))

    monkeypatch.setattr(
        studio,
        "watch_plan_freshness",
        fake_freshness,
    )

    def fake_rebuild(_cfg, recording_id, *, beat_id=None):
        assert recording_id == "hello"
        assert beat_id is None
        events.append("rebuild")
        return Path("/rebuilt")

    monkeypatch.setattr(studio, "run_watch_rebuild", fake_rebuild)

    def fake_artifacts(_spec, *, run_dir=None):
        events.append("artifacts")
        return Path("/presentation"), {}

    monkeypatch.setattr(studio, "watch_presentation_artifacts", fake_artifacts)

    @contextmanager
    def fake_rebuilds(*_args, **_kwargs):
        yield

    monkeypatch.setattr(studio, "watch_recording_rebuilds", fake_rebuilds)
    monkeypatch.setattr(studio, "run_watch_server", lambda *_args, **_kwargs: 0)

    assert (
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {"recording": "hello", "open": False},
        )
        == 0
    )
    assert events == (
        ["freshness", "artifacts"]
        if fresh
        else ["freshness", "rebuild", "artifacts"]
    )
    output = capsys.readouterr().out
    state = "fresh" if fresh else "stale"
    assert (
        f"watch freshness: capture={state}, presentation={state}"
        in output
    )
    assert ("watch build plan" in output) is not fresh


def test_watch_freshness_rejects_presentation_only_source_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_spec = {
        "id": "hello",
        "beats": [
            {
                "id": "intro",
                "viewer_hold": 1,
                "actions": [{"run": "printf hello"}],
            }
        ],
    }
    changed_spec = {
        **original_spec,
        "beats": [
            {
                **original_spec["beats"][0],
                "viewer_hold": 2,
            }
        ],
    }
    original_plan = studio.normalized_recording_plan(original_spec)
    changed_plan = studio.normalized_recording_plan(changed_spec)
    run_dir = tmp_path / "run"
    manifest = run_dir / "presentation" / "recording.presentation.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"beats": [{"id": "intro"}]}) + "\n",
        encoding="utf-8",
    )
    fingerprint = studio.presentation_build.artifact_fingerprints(
        original_spec,
        original_plan,
    )
    (run_dir / "recording.fingerprint.json").write_text(
        json.dumps(
            {
                "version": 1,
                **fingerprint.payload(),
                "presentation_source_fingerprint": (
                    fingerprint.presentation_fingerprint
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "capture_is_fresh",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        studio.presentation_build,
        "capture_artifacts_exist",
        lambda *_args: True,
    )

    original_freshness = studio.watch_plan_freshness(
        original_spec,
        original_plan,
        run_dir=run_dir,
    )
    changed_freshness = studio.watch_plan_freshness(
        changed_spec,
        changed_plan,
        run_dir=run_dir,
    )
    assert original_freshness.capture == "fresh"
    assert original_freshness.presentation == "fresh"
    assert original_freshness.ready
    assert changed_freshness.capture == "fresh"
    assert changed_freshness.presentation == "stale"
    assert not changed_freshness.ready


def test_run_watch_targets_a_named_source_beat(monkeypatch) -> None:
    requested: dict[str, object] = {}
    spec = {
        "id": "hello",
        "_recording_id": "hello",
        "beats": [
            {"id": "intro", "actions": []},
            {"id": "highlight", "actions": []},
        ],
    }
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): spec,
    )
    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        lambda _spec, *, run_dir=None: (Path("/presentation"), {}),
    )
    monkeypatch.setattr(
        studio,
        "watch_plan_freshness",
        lambda _spec, _plan: studio.WatchPlanFreshness(
            "fresh", "fresh", Path("/run")
        ),
    )

    def fake_run_watch_server(_cfg, url, _artifacts, **_kwargs):
        requested["url"] = url
        return 0

    monkeypatch.setattr(studio, "run_watch_server", fake_run_watch_server)

    assert (
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {
                "recording": "hello",
                "beat": "highlight",
                "autoplay": False,
            },
        )
        == 0
    )
    assert requested["url"] == "/watch/hello/?beat=highlight"


def test_recording_plan_through_beat_keeps_selected_prefix() -> None:
    plan = studio.normalized_recording_plan(
        {
            "id": "hello",
            "audio": {"enabled": True},
            "beats": [
                {
                    "id": "intro",
                    "narration": "Intro.",
                    "actions": [{"run": "printf intro"}],
                },
                {
                    "id": "prepare",
                    "narration": "Prepare.",
                    "actions": [{"run": "printf prepare"}],
                },
                {
                    "id": "publish",
                    "narration": "Publish.",
                    "actions": [{"run": "printf publish"}],
                },
            ],
        }
    )

    selected = studio.recording_plan_through_beat(plan, "prepare")

    assert [beat.id for beat in selected.beats] == ["intro", "prepare"]
    assert [
        member.beat_id
        for take in selected.narration_takes
        for member in take.members
    ] == [
        "intro",
        "prepare",
    ]


def test_run_watch_builds_missing_selected_beat_prefix(monkeypatch) -> None:
    requested: dict[str, object] = {}
    spec = {
        "id": "hello",
        "_recording_id": "hello",
        "beats": [
            {"id": "intro", "actions": []},
            {"id": "prepare", "actions": []},
            {"id": "publish", "actions": []},
        ],
    }
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): spec,
    )
    monkeypatch.setattr(
        studio,
        "watch_plan_freshness",
        lambda _spec, _plan: studio.WatchPlanFreshness(
            "stale", "stale", Path("/run")
        ),
    )
    monkeypatch.setattr(
        studio,
        "watch_plan_has_fresh_presentation",
        lambda _spec, _plan: False,
    )

    def fake_rebuild(_cfg, recording_id, *, beat_id=None):
        requested["rebuild"] = (recording_id, beat_id)
        return Path("/watch-prefix")

    monkeypatch.setattr(studio, "run_watch_rebuild", fake_rebuild)
    monkeypatch.setattr(
        studio,
        "watch_presentation_artifacts",
        lambda _spec, *, run_dir=None: (Path("/presentation"), {}),
    )

    @contextmanager
    def fake_rebuilds(
        _cfg,
        _config,
        recording_ids,
        *,
        target_beats=None,
        target_specs=None,
        source_specs=None,
    ):
        requested["target_beats"] = target_beats
        requested["target_specs"] = target_specs
        requested["source_specs"] = source_specs
        assert recording_ids == ("hello",)
        yield

    monkeypatch.setattr(studio, "watch_recording_rebuilds", fake_rebuilds)
    monkeypatch.setattr(studio, "run_watch_server", lambda *_args, **_kwargs: 0)

    assert (
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {
                "recording": "hello",
                "beat": "prepare",
                "autoplay": False,
            },
        )
        == 0
    )
    assert requested["rebuild"] == ("hello", "prepare")
    assert requested["target_beats"] == {"hello": "prepare"}
    assert requested["target_specs"]["hello"]["_watch_run_dir"] == "/watch-prefix"
    assert requested["source_specs"] == {"hello": spec}


def test_run_watch_rejects_unknown_source_beat_with_valid_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {
            "_recording_id": "hello",
            "beats": [
                {"id": "intro", "actions": []},
                {"id": "highlight", "actions": []},
            ],
        },
    )

    with pytest.raises(
        studio.StudioError,
        match=(
            r"unknown beat 'missing' for recording hello; "
            r"valid beat ids: intro, highlight"
        ),
    ):
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {"recording": "hello", "beat": "missing"},
        )


def test_run_watch_does_not_treat_nested_pane_beats_as_watch_targets(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {
            "_recording_id": "hello",
            "beats": [
                {
                    "id": "presentation",
                    "panes": [
                        {
                            "pane": "main",
                            "beats": [{"id": "nested", "actions": []}],
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(
        studio.StudioError,
        match=(
            r"unknown beat 'nested' for recording hello; "
            r"valid beat ids: presentation"
        ),
    ):
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {"recording": "hello", "beat": "nested"},
        )


@pytest.mark.parametrize("value", ["", 1, True, ["intro"]])
def test_run_watch_rejects_invalid_beat_id(value, monkeypatch) -> None:
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id=None, overrides=(): {
            "_recording_id": "hello",
            "beats": [{"id": "intro", "actions": []}],
        },
    )

    with pytest.raises(
        studio.StudioError,
        match="beat must be a non-empty string or null",
    ):
        studio.run_watch(
            OmegaConf.create({"output_format": "text"}),
            {"recording": "hello", "beat": value},
        )


@pytest.mark.parametrize("changed_file", ["index.md", "scripts/action.sh"])
@pytest.mark.parametrize("target_beat", [None, "prepare"])
def test_watch_rebuilds_after_recording_source_changes(
    tmp_path,
    monkeypatch,
    changed_file,
    target_beat,
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
    rebuilt: list[tuple[str, str | None]] = []
    target_spec: dict[str, object] = {}

    class ChangingEvent:
        def __init__(self) -> None:
            self.wait_count = 0

        def wait(self, _timeout) -> bool:
            self.wait_count += 1
            if self.wait_count == 1:
                (recording_source / changed_file).write_text("changed\n")
            return stop_event.is_set()

    def fake_rebuild(_cfg, recording_id, *, beat_id=None) -> Path:
        rebuilt.append((recording_id, beat_id))
        stop_event.set()
        return Path("/rebuilt-prefix")

    monkeypatch.setattr(studio, "run_watch_rebuild", fake_rebuild)

    studio.run_watch_rebuild_loop(
        OmegaConf.create(config),
        config,
        ("hello",),
        ChangingEvent(),
        poll_interval=0.001,
        quiet_interval=0.001,
        target_beats=(
            None if target_beat is None else {"hello": target_beat}
        ),
        target_specs=(
            None if target_beat is None else {"hello": target_spec}
        ),
    )

    assert rebuilt == [("hello", target_beat)]
    if target_beat is not None:
        assert target_spec["_watch_run_dir"] == "/rebuilt-prefix"


def test_watch_rebuilds_after_declared_external_input_changes(
    tmp_path,
    monkeypatch,
) -> None:
    recording_dir = tmp_path / "recordings"
    recording_source = recording_dir / "hello"
    recording_source.mkdir(parents=True)
    (recording_source / "index.md").write_text("initial narration\n")
    dependency = tmp_path / "shared" / "example.svg"
    dependency.parent.mkdir()
    dependency.write_text("initial\n", encoding="utf-8")
    config = {
        "recording": "hello",
        "project_root": str(tmp_path),
        "studio": {"recording_dir": str(recording_dir)},
    }
    spec = {
        "_project_root": str(tmp_path),
        "_script_dir": str(recording_source),
        "beats": [
            {
                "id": "inspect",
                "actions": [
                    {"run": "true", "inputs": ["project://shared/example.svg"]}
                ],
            }
        ],
    }
    stop_event = threading.Event()
    rebuilt: list[str] = []

    class ChangingEvent:
        def __init__(self) -> None:
            self.wait_count = 0

        def wait(self, _timeout) -> bool:
            self.wait_count += 1
            if self.wait_count == 1:
                dependency.write_text("changed\n", encoding="utf-8")
            return stop_event.is_set()

    def fake_rebuild(_cfg, recording_id, *, beat_id=None) -> Path:
        assert beat_id is None
        rebuilt.append(recording_id)
        stop_event.set()
        return Path("/rebuilt-prefix")

    monkeypatch.setattr(studio, "run_watch_rebuild", fake_rebuild)
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )

    studio.run_watch_rebuild_loop(
        OmegaConf.create(config),
        config,
        ("hello",),
        ChangingEvent(),
        poll_interval=0.001,
        quiet_interval=0.001,
        source_specs={"hello": spec},
    )

    assert rebuilt == ["hello"]


def test_watch_waits_for_2_5_seconds_of_source_quiet_before_rebuilding(
    tmp_path,
    monkeypatch,
) -> None:
    recording_source = tmp_path / "recordings" / "hello"
    recording_source.mkdir(parents=True)
    source = recording_source / "index.md"
    source.write_text("initial\n", encoding="utf-8")
    config = {
        "recording": "hello",
        "studio": {"recording_dir": str(tmp_path / "recordings")},
    }
    rebuilt_at_wait: list[int] = []

    class EditingEvent:
        def __init__(self) -> None:
            self.wait_count = 0

        def wait(self, _timeout) -> bool:
            self.wait_count += 1
            if self.wait_count == 1:
                source.write_text("first edit\n", encoding="utf-8")
            elif self.wait_count == 3:
                source.write_text("second edit\n", encoding="utf-8")
            return bool(rebuilt_at_wait)

    editing = EditingEvent()

    def fake_rebuild(_cfg, _recording_id) -> int:
        rebuilt_at_wait.append(editing.wait_count)
        return 0

    monkeypatch.setattr(studio, "run_watch_rebuild", fake_rebuild)

    studio.run_watch_rebuild_loop(
        OmegaConf.create(config),
        config,
        ("hello",),
        editing,
        poll_interval=1.0,
        quiet_interval=2.5,
    )

    assert rebuilt_at_wait == [6]


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
        return {
            "_recording_id": "hello",
            "_hydra_output_dir": hydra_output_dir,
        }

    monkeypatch.setattr(studio, "recording_spec_from_config", fake_recording_spec)
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: "plan")

    def fake_manifest_build(
        build_cfg,
        config,
        spec,
        plan,
        *,
        publish_surfaces=True,
        garbage_collect_runs=True,
        reuse_latest_capture=True,
    ) -> int:
        observed.update(
            build_cfg=build_cfg,
            build_config=config,
            spec=spec,
            plan=plan,
            publish_surfaces=publish_surfaces,
            garbage_collect_runs=garbage_collect_runs,
            reuse_latest_capture=reuse_latest_capture,
        )
        return 0

    monkeypatch.setattr(studio, "run_manifest_build", fake_manifest_build)

    assert studio.run_watch_rebuild(cfg, "hello") == observed["run_dir"]
    assert observed["config"]["action"] == "build"
    assert observed["run_dir"].parent == (
        tmp_path / "recordings/.omegaflow/runs/hello"
    )
    assert observed["publish_surfaces"] is False
    assert observed["garbage_collect_runs"] is True
    assert observed["reuse_latest_capture"] is True


def test_watch_rebuild_selected_prefix_uses_private_scratch_run(
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
        _config,
        *,
        recording_id=None,
        overrides=(),
        hydra_output_dir=None,
    ):
        return {
            "_recording_id": "hello",
            "_hydra_output_dir": hydra_output_dir,
        }

    monkeypatch.setattr(studio, "recording_spec_from_config", fake_recording_spec)
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: "full")
    monkeypatch.setattr(
        studio,
        "recording_plan_through_beat",
        lambda plan, beat_id: (plan, beat_id),
    )
    monkeypatch.setattr(
        studio,
        "latest_watch_build_run_dir",
        lambda _config, _recording_id, _beat_id: None,
    )

    def fake_manifest_build(
        _build_cfg,
        _config,
        spec,
        plan,
        *,
        publish_surfaces=True,
        garbage_collect_runs=True,
        reuse_latest_capture=True,
    ) -> int:
        observed.update(
            run_dir=Path(spec["_hydra_output_dir"]),
            plan=plan,
            publish_surfaces=publish_surfaces,
            garbage_collect_runs=garbage_collect_runs,
            reuse_latest_capture=reuse_latest_capture,
        )
        return 0

    monkeypatch.setattr(studio, "run_manifest_build", fake_manifest_build)

    run_dir = studio.run_watch_rebuild(cfg, "hello", beat_id="prepare")

    assert run_dir == observed["run_dir"]
    assert (
        tmp_path
        / "recordings/.omegaflow/runs/.scratch/watch/hello/prepare"
    ) in run_dir.parents
    assert observed["plan"] == ("full", "prepare")
    assert observed["publish_surfaces"] is False
    assert observed["garbage_collect_runs"] is False
    assert observed["reuse_latest_capture"] is False


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
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: "plan")
    monkeypatch.setattr(
        studio,
        "watch_plan_freshness",
        lambda _spec, _plan: studio.WatchPlanFreshness(
            "fresh", "fresh", Path("/run")
        ),
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
    monkeypatch.setattr(studio, "normalized_recording_plan", lambda _spec: "plan")
    monkeypatch.setattr(
        studio,
        "watch_plan_freshness",
        lambda _spec, _plan: studio.WatchPlanFreshness(
            "fresh", "fresh", Path("/run")
        ),
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
