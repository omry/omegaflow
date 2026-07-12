import json
import importlib.util
import os
import shlex
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from omegaflow import __version__
from omegaflow import audio
from omegaflow import record
from omegaflow import retime_cast
from omegaflow import studio
from omegaflow import studio_config as studio_config_module
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
    recording_spec_from_config,
    studio_directive_blocks,
    studio_run_dir,
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
    assert __version__ == "0.4.0"


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


def test_published_audio_check_allows_sanitized_segment_paths(tmp_path) -> None:
    plan = [
        audio.AudioPlanItem(
            segment=audio.NarrationSegment(
                segment_id="intro",
                heading="Introduction",
                text="Hello.",
            ),
            cache_key="key",
            output_path=tmp_path / "cache" / "intro.mp3",
        )
    ]
    metadata_path = tmp_path / "published" / "audio.json"
    metadata_path.parent.mkdir()
    metadata_path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "intro",
                        "heading": "Introduction",
                        "text": "Hello.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    audio.validate_published_audio_metadata(
        plan,
        metadata_path,
        allow_missing_segment_audio=True,
    )
    with pytest.raises(audio.AudioError, match="field 'audio' is stale"):
        audio.validate_published_audio_metadata(plan, metadata_path)


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
    monkeypatch.delenv("OMEGAFLOW_PROJECT_ROOT", raising=False)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir.name == "conf"
    assert layout.config_dir.parent.name == "omegaflow"
    assert layout.recording_script_dir == recordings_dir


def test_empty_workspace_uses_bundled_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMEGAFLOW_PROJECT_ROOT", raising=False)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir.name == "conf"
    assert layout.config_dir.parent.name == "omegaflow"
    assert layout.data_dir == tmp_path / "recordings" / ".omegaflow"
    assert layout.recording_script_dir == tmp_path / "recordings"


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
    run_dir.mkdir(parents=True)
    (run_dir / "recording.cast").write_text(
        '{"version": 2}\n[1.25, "o", "ok"]\n',
        encoding="utf-8",
    )
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
    paths = studio.artifact_paths(spec)
    assert paths["cast"] == output_dir / "recording.cast"
    assert paths["retimed_cast"] == output_dir / "recording.retimed.cast"
    assert paths["audio"] == output_dir / "audio.mp3"
    assert paths["audio_metadata"] == output_dir / "audio.json"


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
    run_dir.mkdir(parents=True)
    (run_dir / "recording.cast").write_text(
        '{"version": 2}\n[1.25, "o", "ok"]\n',
        encoding="utf-8",
    )

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
    run_dir.mkdir(parents=True)
    (run_dir / "recording.cast").write_text(
        '{"version": 2}\n[1.25, "o", "ok"]\n',
        encoding="utf-8",
    )

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
    run_dir.mkdir(parents=True)
    (run_dir / "recording.cast").write_text('{"version": 2}\n', encoding="utf-8")

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


def test_build_audio_generates_missing_segments_before_publish(monkeypatch) -> None:
    cfg = OmegaConf.create({"output_format": "text", "verbose": False})
    calls: list[tuple[str, bool, bool]] = []

    monkeypatch.setattr(
        studio,
        "audio_segments_need_materialization",
        lambda _cfg: True,
    )
    monkeypatch.setattr(studio, "build_audio_stats", lambda *args, **kwargs: None)

    def fake_run_step(
        label,
        runner,
        cfg,
        step,
        *,
        quiet=False,
        show_step=True,
        config_overrides=None,
    ):
        calls.append((step, quiet, show_step))
        return ""

    monkeypatch.setattr(studio, "run_step", fake_run_step)

    studio.run_build_audio_actions(
        cfg,
        generate_needed=False,
        publish_needed=True,
        output=Path("voiceover.mp3"),
    )

    assert calls == [
        ("generate", False, False),
        ("publish", False, False),
    ]


def test_retime_action_passes_audio_metadata_override(monkeypatch) -> None:
    cfg = OmegaConf.create({"output_format": "text"})
    calls: list[dict[str, object]] = []

    def fake_run_step(
        label,
        runner,
        cfg,
        step,
        *,
        quiet=False,
        show_step=True,
        config_overrides=None,
    ):
        calls.append(config_overrides or {})
        return ""

    monkeypatch.setattr(studio, "run_step", fake_run_step)

    studio.run_retime_action(
        cfg,
        "retime",
        cast=Path("run.cast"),
        timeline=Path("run.timeline.jsonl"),
        audio_metadata=Path("run.json"),
        output=Path("run.retimed.cast"),
    )

    assert calls == [
        {
            "cast": Path("run.cast"),
            "timeline": Path("run.timeline.jsonl"),
            "audio_metadata": Path("run.json"),
            "output": Path("run.retimed.cast"),
        }
    ]


def test_retime_tool_uses_audio_metadata_override(tmp_path, monkeypatch) -> None:
    stale_metadata = tmp_path / "published.json"
    run_metadata = tmp_path / "run.json"
    output = tmp_path / "run.retimed.cast"
    requested_metadata: list[Path | None] = []

    monkeypatch.setattr(
        retime_cast,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: {
            "_recording_id": "demo",
            "outputs": {
                "cast": str(tmp_path / "published.cast"),
                "audio_metadata": str(stale_metadata),
            },
        },
    )
    monkeypatch.setattr(
        retime_cast,
        "read_audio_segment_timings",
        lambda path: requested_metadata.append(path) or {},
    )
    monkeypatch.setattr(
        retime_cast,
        "read_cast",
        lambda _path: ({"version": 3, "term": {"cols": 80, "rows": 24}}, ["event"]),
    )
    monkeypatch.setattr(retime_cast, "read_timeline", lambda _path: [])
    monkeypatch.setattr(retime_cast, "retime_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        retime_cast,
        "write_cast",
        lambda path, _header, _events: path.write_text(
            '{"version": 3}\n', encoding="utf-8"
        ),
    )
    monkeypatch.setattr(retime_cast, "pass_line", lambda _message: None)

    result = retime_cast.run_tool_from_hydra_cfg(
        OmegaConf.create(
            {
                "action": "retime",
                "cast": str(tmp_path / "run.cast"),
                "timeline": str(tmp_path / "run.timeline.jsonl"),
                "audio_metadata": str(run_metadata),
                "output": str(output),
            }
        )
    )

    assert result == 0
    assert requested_metadata == [run_metadata]
    assert output.exists()


def test_audio_dry_run_output_override_uses_run_local_metadata(
    tmp_path, monkeypatch, capsys
) -> None:
    run_audio = tmp_path / "runs" / "demo" / "audio" / "demo.mp3"
    public_audio = tmp_path / "public" / "demo" / "audio.mp3"
    public_metadata = tmp_path / "public" / "demo" / "audio.json"
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "outputs": {
            "dir": str(tmp_path / "public"),
            "asset_dir": str(tmp_path / "public" / "demo"),
            "cast": str(tmp_path / "public" / "demo" / "recording.cast"),
            "audio": str(public_audio),
            "audio_metadata": str(public_metadata),
        },
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
            "cache_dir": str(tmp_path / "cache" / "audio"),
        },
        "narration": {
            "beats": [
                {
                    "id": "intro",
                    "heading": "Intro",
                    "text": "Say hello.",
                }
            ]
        },
    }
    monkeypatch.setattr(audio, "load_recording_spec_from_hydra_cfg", lambda _cfg: spec)

    status = audio.run_tool_from_hydra_cfg(
        OmegaConf.create(
            {
                "action": "dry_run",
                "output": str(run_audio),
                "output_format": "json",
                "timestamps": True,
                "force": False,
                "verbose": False,
                "load_env_file": False,
            }
        )
    )

    assert status == 0
    payloads = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    assert payloads[-1]["published_audio"] == str(run_audio)
    assert payloads[-1]["published_audio_metadata"] == str(run_audio.with_suffix(".json"))
    assert payloads[-1]["published_audio_metadata"] != str(public_metadata)


def test_audio_publish_needs_work_output_override_checks_run_local_metadata(
    tmp_path, monkeypatch
) -> None:
    run_audio = tmp_path / "runs" / "demo" / "audio" / "demo.mp3"
    public_metadata = tmp_path / "public" / "demo" / "audio.json"
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "outputs": {
            "dir": str(tmp_path / "public"),
            "asset_dir": str(tmp_path / "public" / "demo"),
            "cast": str(tmp_path / "public" / "demo" / "recording.cast"),
            "audio": str(tmp_path / "public" / "demo" / "audio.mp3"),
            "audio_metadata": str(public_metadata),
        },
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
            "cache_dir": str(tmp_path / "cache" / "audio"),
        },
        "narration": {
            "beats": [
                {
                    "id": "intro",
                    "heading": "Intro",
                    "text": "Say hello.",
                }
            ]
        },
    }
    captured: dict[str, Path] = {}
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda _config, recording_id, overrides: spec,
    )

    def fake_published_audio_is_fresh(
        _plan,
        output_path,
        metadata_path,
        **_kwargs,
    ):
        captured["audio"] = output_path
        captured["metadata"] = metadata_path
        return True

    monkeypatch.setattr(audio, "published_audio_is_fresh", fake_published_audio_is_fresh)

    needs_work = studio.audio_publish_needs_work(
        OmegaConf.create({"force": False}),
        output=run_audio,
    )

    assert needs_work is False
    assert captured["audio"] == run_audio
    assert captured["metadata"] == run_audio.with_suffix(".json")
    assert captured["metadata"] != public_metadata


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
    assert spec["outputs"]["cast"] == "site/videos/hello/recording.cast"
    assert spec["outputs"]["audio"] == "site/videos/hello/audio.mp3"
    assert spec["outputs"]["audio_metadata"] == "site/videos/hello/audio.json"
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
    assert spec["outputs"]["cast"] == "preview/videos/hello/recording.cast"


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
    assert studio.artifact_paths(alpha)["cast"] == Path.cwd() / (
        "site/videos/alpha/recording.cast"
    )
    assert studio.artifact_paths(beta)["cast"] == Path.cwd() / (
        "site/videos/beta/recording.cast"
    )
    assert studio.artifact_paths(alpha)["cast"] != studio.artifact_paths(beta)["cast"]
    assert studio.artifact_paths(alpha)["audio"] != studio.artifact_paths(beta)["audio"]


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


def test_run_file_resolves_from_recording_script_dir(tmp_path) -> None:
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
        "capture": {"baseline_compressed": True},
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

    script = record.render_session_script(spec)
    dependencies = studio.fingerprint_dependency_paths(spec)

    assert "setup from recording script dir" in script
    assert "action from recording script dir" in script
    assert setup_script in dependencies
    assert action_script in dependencies


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
    support_script = workspace / "demo-recording" / "scripts" / "hello.sh"

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
        "dry_run": False,
    }
    assert "id:" not in shared_config
    assert "title:" not in shared_config
    assert "id: demo-recording" in recording
    assert "type: standalone_html" in recording
    assert "cast:" not in recording
    assert "file: ${outputs.asset_dir}/index.html" in recording
    assert "This Markdown file is the source for one generated terminal video." in recording
    assert "fenced `studio-directive` blocks tell" in recording
    assert "run_file: scripts/hello.sh" in recording
    assert "output_contains:" in recording
    assert "- hello from demo-recording" in recording
    assert support_script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert support_script.stat().st_mode & 0o111


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
    assert "next    omegaflow recording=quickstart\n" in output
    assert "action=build" not in output
    recording = (workspace / "quickstart" / "index.md").read_text(
        encoding="utf-8"
    )
    support_script = workspace / "quickstart" / "scripts" / "hello.sh"

    assert "id: quickstart" in recording
    assert "title: Quickstart" in recording
    assert "- hello from quickstart" in recording
    assert support_script.stat().st_mode & 0o111


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
    assert "recordings/quickstart/scripts/hello.sh" in output
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
    assert "+    - run_file: scripts/hello.sh" in output
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
    support_script = (
        workspace / "tutorial" / "recording-file" / "scripts" / "hello.sh"
    )

    assert "id: tutorial/recording-file" in recording
    assert "title: Recording File" in recording
    assert "- hello from tutorial/recording-file" in recording
    assert support_script.stat().st_mode & 0o111


def test_interrupted_recording_message_is_terse(tmp_path) -> None:
    message = record.format_interrupted_recording(
        tmp_path / "recording.cast",
        [tmp_path / ".recording.cast.recording-123.cast"],
    )

    assert message == "recording cancelled by user\noutput was not updated."
    assert str(tmp_path) not in message
    assert "removed staged recording artifacts" not in message


def test_success_followups_show_user_facing_actions(capsys) -> None:
    cfg = OmegaConf.create(
        {
            "recording": "quickstart-demo",
            "output_format": "text",
        }
    )

    studio.print_success_followups(cfg)

    output = capsys.readouterr().out
    assert "omegaflow recording=quickstart-demo action=play" in output
    assert "omegaflow recording=quickstart-demo action=watch" in output
    assert "action=inspect" not in output


def minimal_recording_spec(run_dir, *, data_dir: Path | None = None) -> dict[str, object]:
    config: dict[str, object] = {}
    if data_dir is not None:
        config["studio"] = {"data_dir": str(data_dir)}
    return {
        "id": "demo",
        "_recording_id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_studio_config": config,
        "outputs": {"cast": "website/static/casts/demo.cast"},
        "audio": {
            "enabled": False,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
        },
    }


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
        artifact = "recording.cast" if index < 3 else "failure.json"
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
    assert "run gc: removed 2 old run(s)" in capsys.readouterr().out


def test_run_gc_dry_run_reports_without_removing(tmp_path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "media"
    runs_dir = data_dir / "runs" / "demo"
    old_run = runs_dir / "20260705-010201"
    current = runs_dir / "20260705-010202"
    for run_dir in [old_run, current]:
        run_dir.mkdir(parents=True)
        (run_dir / "recording.cast").write_text("{}\n", encoding="utf-8")
    now = 2_000_000_000.0
    monkeypatch.setattr(studio.time, "time", lambda: now)
    os.utime(old_run, (now - 31 * 86400,) * 2)
    spec = minimal_recording_spec(current, data_dir=data_dir)
    spec["_studio_config"]["studio"]["run_gc"] = {"dry_run": True}

    assert studio.garbage_collect_recording_runs(spec, current_run_dir=current) == [
        old_run
    ]
    assert old_run.is_dir()
    assert "would remove 1 old run(s) (dry run)" in capsys.readouterr().out


def test_run_gc_can_be_disabled(tmp_path) -> None:
    data_dir = tmp_path / "media"
    old_run = data_dir / "runs" / "demo" / "20260705-010201"
    old_run.mkdir(parents=True)
    spec = minimal_recording_spec(old_run, data_dir=data_dir)
    spec["_studio_config"]["studio"]["run_gc"] = {"enabled": False}

    assert studio.garbage_collect_recording_runs(spec, current_run_dir=old_run) == []
    assert old_run.is_dir()


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
    assert "removed 0 old run(s)" in captured.out


def test_recording_skip_reason_uses_latest_successful_run(tmp_path) -> None:
    data_dir = tmp_path / "media"
    run_dir = data_dir / "runs" / "demo" / "20260705-010203"
    run_dir.mkdir(parents=True)
    (run_dir / "recording.cast").write_text(
        '{"version": 2}\n[1.25, "o", "ok"]\n',
        encoding="utf-8",
    )
    (run_dir / "recording.timeline.jsonl").write_text(
        '{"time": 0, "phase": "start"}\n',
        encoding="utf-8",
    )
    spec = minimal_recording_spec(run_dir, data_dir=data_dir)
    studio.write_recording_fingerprint(
        spec,
        fingerprint_path=run_dir / "recording.fingerprint.json",
    )

    assert studio.recording_skip_reason(spec) is None

    (run_dir / "recording.cast").unlink()

    assert studio.recording_skip_reason(spec) == "successful recording run is missing"


def test_publish_artifacts_from_run_rewrites_public_metadata(tmp_path) -> None:
    run_dir = tmp_path / "media" / "runs" / "demo" / "20260705-010203"
    target_dir = tmp_path / "published"
    target_cast = target_dir / "demo.cast"
    target_audio = target_dir / "demo.mp3"
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "_hydra_output_dir": str(run_dir),
        "outputs": {
            "cast": str(target_cast),
            "audio": str(target_audio),
        },
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
        },
    }
    source_paths = studio.run_artifact_paths(run_dir, spec)
    source_paths["cast"].parent.mkdir(parents=True)
    source_paths["cast"].write_text('{"version": 2}\n', encoding="utf-8")
    source_paths["timeline"].write_text('{"time": 0}\n', encoding="utf-8")
    source_paths["retimed_cast"].write_text('{"version": 2}\n', encoding="utf-8")
    source_paths["audio"].parent.mkdir(parents=True)
    source_paths["audio"].write_bytes(b"mp3")
    source_timestamp = source_paths["audio_metadata"].with_name(
        "demo.intro.timestamps.json"
    )
    source_timestamp.write_text('{"words": []}\n', encoding="utf-8")
    source_paths["audio_metadata"].write_text(
        json.dumps(
            {
                "audio": str(source_paths["audio"]),
                "segments": [
                    {
                        "id": "intro",
                        "audio": str(source_paths["audio"]),
                        "timestamps": str(source_timestamp),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_paths["recording_fingerprint"].write_text(
        json.dumps(
            {
                "dependencies": [
                    {"path": "recordings/demo/index.md"},
                    {"path": str(tmp_path / "absolute-input.txt")},
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )

    written = studio.publish_artifacts_from_run(spec, source_paths)

    target_metadata = target_audio.with_suffix(".json")
    target_timestamp = target_metadata.with_name(source_timestamp.name)
    assert target_cast.read_text(encoding="utf-8") == '{"version": 2}\n'
    assert target_cast.with_suffix(".timeline.jsonl").read_text(
        encoding="utf-8"
    ) == '{"time": 0}\n'
    assert target_cast.with_name("demo.retimed.cast").read_text(
        encoding="utf-8"
    ) == '{"version": 2}\n'
    assert target_audio.read_bytes() == b"mp3"
    metadata = json.loads(target_metadata.read_text(encoding="utf-8"))
    assert metadata["audio"] == str(target_audio)
    assert "audio" not in metadata["segments"][0]
    assert metadata["segments"][0]["timestamps"] == str(target_timestamp)
    assert target_timestamp.read_text(encoding="utf-8") == '{"words": []}\n'
    fingerprint = json.loads(
        target_cast.with_suffix(".recording.json").read_text(encoding="utf-8")
    )
    assert fingerprint["dependencies"] == [
        {"path": "recordings/demo/index.md"}
    ]
    assert target_audio in written
    assert target_metadata in written


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


def test_watch_player_url_path_allows_silent_recordings(tmp_path, monkeypatch) -> None:
    retimed_cast = tmp_path / "runs" / "hello" / "recording.retimed.cast"
    retimed_cast.parent.mkdir(parents=True)
    retimed_cast.write_text('{"version": 3}\n', encoding="utf-8")
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
    paths = {
        "retimed_cast": retimed_cast,
        "audio": tmp_path / "missing.mp3",
        "audio_metadata": tmp_path / "missing.json",
    }
    monkeypatch.setattr(studio, "latest_run_artifact_paths", lambda _spec: paths)

    url_path, artifacts = studio.watch_player_url_path(spec)

    assert "cast=" in url_path
    assert "audio=" not in url_path
    assert "audioMeta=" not in url_path
    assert artifacts == {"cast": retimed_cast.resolve()}


def test_watch_server_reports_local_watch_server(monkeypatch, capsys) -> None:
    class FakeServer:
        server_port = 51234

        def __init__(self, _address, _handler_factory) -> None:
            pass

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
        "/cast-player.html?cast=/__watch_artifact__/cast",
        {"cast": Path("recording.cast")},
    )
    output = capsys.readouterr().out

    assert status == 0
    assert "serving local watch server: http://127.0.0.1:51234/" in output
    assert "opened browser; press Ctrl-C to stop" in output
    assert "stopped local watch server" in output


def test_session_cleanup_failure_fails_run(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "demo" / "20260705-010203"
    spec = {
        "id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_keep_hydra_output_dir": True,
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False, "typing": False},
        "capture": {"baseline_compressed": True},
        "cleanup": [
            {
                "name": "Cleanup fails",
                "run": "echo cleanup failed >&2; exit 7",
            }
        ],
        "beats": [{"id": "done"}],
    }
    script_path = tmp_path / "session.sh"
    script_path.write_text(record.render_session_script(spec), encoding="utf-8")

    result = subprocess.run(
        ["bash", str(script_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7
    failure = json.loads((run_dir / "failure.json").read_text(encoding="utf-8"))
    assert failure["kind"] == "cleanup"
    assert failure["id"] == "cleanup_1"
    assert failure["name"] == "Cleanup fails"
    assert failure["message"] == "exited 7, expected 0"
    assert "cleanup failed" in failure["stderr"]


def test_session_uses_persistent_shell_without_replacing_omegaflow_cleanup(
    tmp_path,
) -> None:
    run_dir = tmp_path / "runs" / "demo" / "20260705-010203"
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    spec = {
        "id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_keep_hydra_output_dir": True,
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False, "typing": False},
        "capture": {"baseline_compressed": True},
        "cleanup": [
            {
                "name": "prove controller cleanup ran",
                "run": f"touch {tmp_path / 'controller-cleanup'}",
            }
        ],
        "beats": [
            {
                "id": "hello",
                "actions": [
                    {
                        "commands": [
                            {
                                "run": """
cd child
export DEMO_STATE=ready
demo_function() { printf 'function-ok\\n'; }
shopt -s expand_aliases
shopt -s extglob
alias demo_alias="printf 'alias-ok\\n'"
set -f
trap 'printf user-trap > user-trap.txt' EXIT
""".strip(),
                                "display": "cd child",
                                "timing": "realtime",
                            },
                            {
                                "run": """
printf '%s %s\\n' "$PWD" "$DEMO_STATE"
demo_function
demo_alias
[[ $- == *f* ]]
shopt -q expand_aliases
[[ foobar == +(foo|bar) ]]
""".strip(),
                                "display": "echo state",
                                "timing": "realtime",
                            }
                        ]
                    }
                ],
            }
        ],
    }
    script_path = tmp_path / "session.sh"
    script = record.render_session_script(spec)
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(script_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert "local -n" not in script
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    output = (run_dir / "stdout").read_text(encoding="utf-8")
    assert f"{child_dir} ready" in output
    assert "function-ok" in output
    assert "alias-ok" in output
    assert (child_dir / "user-trap.txt").read_text(encoding="utf-8") == "user-trap"
    assert (tmp_path / "controller-cleanup").is_file()


@pytest.mark.parametrize(
    "command",
    [
        "exit 7",
        "if then",
        "set -e; false; printf should-not-run",
    ],
)
def test_persistent_shell_failure_is_bounded_and_preserves_errexit(
    tmp_path, command
) -> None:
    run_dir = tmp_path / "runs" / "demo" / "20260705-010203"
    spec = {
        "id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_keep_hydra_output_dir": True,
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False, "typing": False},
        "capture": {"baseline_compressed": True},
        "beats": [
            {
                "id": "failure",
                "actions": [
                    {"commands": [{"run": command, "timing": "realtime"}]}
                ],
            }
        ],
    }
    script_path = tmp_path / "session.sh"
    script_path.write_text(record.render_session_script(spec), encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(script_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
    assert "should-not-run" not in (run_dir / "stdout").read_text(encoding="utf-8")
    failure = json.loads((run_dir / "failure.json").read_text(encoding="utf-8"))
    if command == "exit 7":
        assert failure["message"] == "exited 7, expected 0"


@pytest.mark.parametrize(
    "command",
    [
        "nohup sleep 10 >/dev/null 2>&1 & echo $! > background.pid",
        "trap 'sleep 10' EXIT",
    ],
)
def test_persistent_shell_background_and_exit_trap_cleanup_is_bounded(
    tmp_path, command
) -> None:
    run_dir = tmp_path / "runs" / "demo" / "20260705-010203"
    spec = {
        "id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_keep_hydra_output_dir": True,
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False, "typing": False},
        "capture": {"baseline_compressed": True},
        "beats": [
            {
                "id": "bounded",
                "actions": [
                    {"commands": [{"run": command, "timing": "realtime"}]}
                ],
            }
        ],
    }
    script_path = tmp_path / "session.sh"
    script_path.write_text(record.render_session_script(spec), encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(script_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=3,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    if "background.pid" in command:
        background_pid = int((tmp_path / "background.pid").read_text().strip())
        assert subprocess.run(
            ["kill", "-0", str(background_pid)],
            capture_output=True,
            check=False,
        ).returncode != 0


def test_new_and_nested_bash_shells_clear_parent_shell_local_state(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "demo" / "20260705-010203"
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    fresh_shell_assertions = """
[[ -z ${DEMO_LOCAL+x} ]]
[[ $DEMO_EXPORTED == exported ]]
[[ $PWD == */child ]]
! declare -F demo_function >/dev/null
! alias demo_alias >/dev/null 2>&1
! shopt -q extglob
[[ -z $(trap -p EXIT) ]]
""".strip()
    nested_command = (
        "/bin/bash --noprofile --norc -c "
        + shlex.quote(fresh_shell_assertions + "\nprintf nested-shell-ok\\n")
    )
    new_shell_command = (
        fresh_shell_assertions
        + "\n"
        + nested_command
        + "\nprintf new-shell-ok\\n"
    )
    spec = {
        "id": "demo",
        "_hydra_output_dir": str(run_dir),
        "_keep_hydra_output_dir": True,
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False, "typing": False},
        "capture": {"baseline_compressed": True},
        "beats": [
            {
                "id": "shells",
                "actions": [
                    {
                        "commands": [
                            {
                                "run": """
cd child
DEMO_LOCAL=parent
export DEMO_EXPORTED=exported
demo_function() { :; }
alias demo_alias=:
shopt -s extglob
trap : EXIT
""".strip(),
                                "timing": "realtime",
                            },
                            {
                                "run": "/bin/bash --noprofile --norc -c "
                                + shlex.quote(new_shell_command),
                                "timing": "realtime",
                            },
                        ]
                    }
                ],
            }
        ],
    }
    script_path = tmp_path / "session.sh"
    script_path.write_text(record.render_session_script(spec), encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(script_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    output = (run_dir / "stdout").read_text(encoding="utf-8")
    assert "new-shell-ok" in output
    assert "nested-shell-ok" in output


def test_environment_path_prepend_takes_precedence(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "demo"
    spec = {
        "id": "demo",
        "_hydra_output_dir": str(run_dir),
        "environment": {
            "working_directory": str(tmp_path),
            "path_prepend": ["tools/bin"],
        },
        "style": {"color": False, "typing": False},
        "capture": {"baseline_compressed": True},
        "beats": [{"id": "done"}],
    }

    script = record.render_session_script(spec)

    path_line = next(line for line in script.splitlines() if line.startswith("export PATH="))
    assert path_line.index("tools/bin") < path_line.index(str(Path(sys.executable).parent))


def test_require_fresh_retimed_cast_rejects_unmaterialized_waits(tmp_path) -> None:
    cast = tmp_path / "demo.cast"
    timeline = tmp_path / "demo.timeline.jsonl"
    retimed = tmp_path / "demo.retimed.cast"
    metadata = tmp_path / "demo.json"
    cast.write_text('{"version": 3}\n', encoding="utf-8")
    timeline.write_text('{"time": 0}\n', encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "intro",
                        "waits": [
                            {
                                "marker": "@wait:server@",
                                "target": "server",
                                "text_offset": 4,
                                "gap_seconds": 0.0,
                            }
                        ],
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    retimed.write_text('{"version": 3}\n', encoding="utf-8")

    try:
        retime_cast.require_fresh_retimed_cast(
            cast_path=cast,
            timeline_path=timeline,
            output_path=retimed,
            audio_metadata_path=metadata,
        )
    except retime_cast.RetimeError as exc:
        assert "has not been materialized by retime" in str(exc)
    else:
        raise AssertionError("expected unmaterialized wait to fail freshness check")


def test_adjusted_audio_seconds_pauses_inline_wait_until_command_finishes() -> None:
    timing = retime_cast.AudioSegmentTiming(
        segment_id="hello",
        duration=8.0,
        anchor_seconds={"@run_demo@": 2.0},
        waits=(
            retime_cast.AudioWaitTiming(
                target="run_demo",
                marker="@wait:run_demo+300ms@",
                seconds=4.0,
                gap_seconds=0.3,
            ),
        ),
    )
    wait_windows: dict[str, dict[str, object]] = {}

    before_wait = retime_cast.adjusted_audio_seconds(
        base_seconds=2.0,
        timing=timing,
        command_end_by_id={},
        beat_start=10.0,
        beat_id="hello",
        wait_windows=wait_windows,
    )
    after_wait = retime_cast.adjusted_audio_seconds(
        base_seconds=8.0,
        timing=timing,
        command_end_by_id={"run_demo": 18.0},
        beat_start=10.0,
        beat_id="hello",
        wait_windows=wait_windows,
    )

    assert before_wait == 2.0
    assert after_wait == 12.3
    assert wait_windows["@wait:run_demo+300ms@"]["presentation_start"] == 14.0
    assert wait_windows["@wait:run_demo+300ms@"]["presentation_end"] == 18.3


def test_adjusted_audio_seconds_can_start_command_at_same_second_as_wait() -> None:
    timing = retime_cast.AudioSegmentTiming(
        segment_id="hello",
        duration=8.0,
        anchor_seconds={"@run_demo@": 2.0},
        waits=(
            retime_cast.AudioWaitTiming(
                target="run_demo",
                marker="@wait:run_demo+300ms@",
                seconds=2.0,
                gap_seconds=0.3,
            ),
        ),
    )

    command_start = retime_cast.adjusted_audio_seconds(
        base_seconds=2.0,
        timing=timing,
        command_end_by_id={},
        beat_start=10.0,
        beat_id="hello",
        include_boundary_waits=False,
    )
    after_wait = retime_cast.adjusted_audio_seconds(
        base_seconds=8.0,
        timing=timing,
        command_end_by_id={"run_demo": 15.0},
        beat_start=10.0,
        beat_id="hello",
    )

    assert command_start == 2.0
    assert after_wait == 11.3


def test_wait_marker_between_words_resumes_from_previous_word_end() -> None:
    text = "Create a virtual environment first. Then install OmegaFlow."
    spans = retime_cast.timestamp_word_spans(
        text=text,
        words=[
            {"word": "Create", "start": 0.0, "end": 0.3},
            {"word": "a", "start": 0.36, "end": 0.42},
            {"word": "virtual", "start": 0.5, "end": 0.86},
            {"word": "environment", "start": 0.92, "end": 1.4},
            {"word": "first", "start": 1.5, "end": 1.78},
            {"word": "Then", "start": 2.2, "end": 2.42},
            {"word": "install", "start": 2.44, "end": 2.8},
            {"word": "OmegaFlow", "start": 2.84, "end": 3.3},
        ],
        timestamp_path=Path("timestamps.json"),
    )
    marker_offset = len("Create a virtual environment first.")

    wait_seconds = retime_cast.marker_seconds_from_offset(
        marker="@wait:env_command+300ms@",
        text_offset=marker_offset,
        spans=spans,
        segment_duration=4.0,
        text=text,
        segment_id="install",
        prefer_previous_word_end=True,
    )
    anchor_seconds = retime_cast.marker_seconds_from_offset(
        marker="@install@",
        text_offset=marker_offset,
        spans=spans,
        segment_duration=4.0,
        text=text,
        segment_id="install",
    )

    assert wait_seconds == 1.78
    assert anchor_seconds == 2.2


def test_timestamp_word_spans_tolerates_missing_word_end() -> None:
    spans = retime_cast.timestamp_word_spans(
        text="Hello world.",
        words=[
            {"word": "Hello", "start": 0.0},
            {"word": "world", "start": 0.4},
        ],
        timestamp_path=Path("timestamps.json"),
    )

    assert spans == [
        (0, 5, 0.0, 0.0),
        (6, 11, 0.4, 0.4),
    ]


def replacement_presentation_command(
    *, replacement_output: str
) -> retime_cast.PresentationCommand:
    interval = retime_cast.TimelineInterval(
        start=0.0,
        end=0.0,
        start_event={},
        end_event={},
    )
    return retime_cast.PresentationCommand(
        key=("beat", "action", "command"),
        prompt_interval=interval,
        run_interval=interval,
        prompt_payload="$ command",
        output_mode="replace",
        replacement_output=replacement_output,
        timing="presentation",
        output_span=retime_cast.OutputSpan(events=()),
    )


def test_replacement_output_uses_terminal_line_endings() -> None:
    scheduled: list[retime_cast.ScheduledEvent] = []

    retime_cast.schedule_command_output(
        scheduled=scheduled,
        command=replacement_presentation_command(replacement_output="one\ntwo\n"),
        output_start=1.0,
        order=2.0,
    )

    assert scheduled[0].payload == "one\r\ntwo\r\n"


def test_replacement_output_preserves_existing_terminal_line_endings() -> None:
    scheduled: list[retime_cast.ScheduledEvent] = []

    retime_cast.schedule_command_output(
        scheduled=scheduled,
        command=replacement_presentation_command(
            replacement_output="one\r\ntwo\r\n"
        ),
        output_start=1.0,
        order=2.0,
    )

    assert scheduled[0].payload == "one\r\ntwo\r\n"
