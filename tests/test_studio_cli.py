import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from omegaflow_studio import __version__
from omegaflow_studio import audio
from omegaflow_studio import record
from omegaflow_studio import retime_cast
from omegaflow_studio import studio
from omegaflow_studio import studio_config as studio_config_module
from omegaflow_studio.record import collect_run_jobs
from omegaflow_studio.studio_config import (
    CONFIG_DIR,
    RECORDING_SCRIPT_DIR,
    StudioConfigError,
    discover_project_layout,
    recording_from_script,
    studio_directive_blocks,
    studio_run_dir,
)


def test_version_is_available() -> None:
    assert __version__ == "0.1.0"


def test_studio_paths_use_canonical_recordings_workspace() -> None:
    assert CONFIG_DIR.parts[-2:] == ("omegaflow_studio", "conf")
    assert RECORDING_SCRIPT_DIR.parts[-1:] == ("recordings",)


def test_discovers_recordings_project_directory(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "config.yaml").write_text("audio:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_RECORDING_DIR", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_DATA_DIR", raising=False)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir.name == "conf"
    assert layout.config_dir.parent.name == "omegaflow_studio"
    assert layout.recording_script_dir == recordings_dir


def test_empty_workspace_uses_bundled_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_RECORDING_DIR", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_DATA_DIR", raising=False)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir.name == "conf"
    assert layout.config_dir.parent.name == "omegaflow_studio"
    assert layout.data_dir == tmp_path / ".omegaflow"
    assert layout.recording_script_dir == tmp_path / "recordings"


def test_studio_run_dir_uses_data_directory() -> None:
    assert (
        studio_run_dir(".omegaflow", "build", "record", False, "demo", "20260705-010203")
        == ".omegaflow/runs/demo/20260705-010203"
    )
    assert (
        studio_run_dir(".omegaflow", "inspect", None, False, "demo", "20260705-010203")
        == ".omegaflow/runs/.scratch/inspect/demo/20260705-010203"
    )


def test_studio_run_dir_routes_missing_recording_to_scratch() -> None:
    assert (
        studio_run_dir(".omegaflow", "build", None, False, None, "20260705-010203")
        == ".omegaflow/runs/.scratch/build/unselected/20260705-010203"
    )


def test_collect_run_jobs_uses_project_data_dir(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "media"
    run_dir = data_dir / "runs" / "demo" / "20260705-010203"
    run_dir.mkdir(parents=True)
    (run_dir / "recording.cast").write_text(
        '{"version": 2}\n[1.25, "o", "ok"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(record, "PROJECT_DATA_DIR", data_dir)

    jobs = collect_run_jobs(now=datetime(2026, 7, 5, 1, 3, 3))

    assert [job["job_id"] for job in jobs] == ["20260705-010203"]
    assert jobs[0]["type"] == "demo"
    assert jobs[0]["result"] == "success"


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


def test_recording_frontmatter_overrides_recordings_config(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "config.yaml").write_text(
        """
audio:
  enabled: false
  provider: openai
  env: SHARED_KEY
style:
  color: false
""".lstrip(),
        encoding="utf-8",
    )
    (recordings_dir / "hello.md").write_text(
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
    assert spec["style"]["color"] is False
    assert spec["beats"][0]["id"] == "hello"


def test_recordings_config_rejects_identity_fields(tmp_path, monkeypatch) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    (recordings_dir / "config.yaml").write_text(
        "title: Shared Title\n",
        encoding="utf-8",
    )
    (recordings_dir / "hello.md").write_text(
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


def test_bootstrap_creates_recording_workspace(tmp_path) -> None:
    workspace = tmp_path / "recordings"

    status = studio.run_bootstrap(
        {
            "workspace": str(workspace),
            "recording": "demo-recording",
            "force": False,
        }
    )

    assert status == 0
    shared_config = (workspace / "config.yaml").read_text(encoding="utf-8")
    recording = (workspace / "demo-recording.md").read_text(encoding="utf-8")
    support_script = workspace / "demo-recording" / "hello.sh"

    assert "id:" not in shared_config
    assert "title:" not in shared_config
    assert "id: demo-recording" in recording
    assert "type: standalone_html" in recording
    assert "run_file: demo-recording/hello.sh" in recording
    assert support_script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert support_script.stat().st_mode & 0o111


def minimal_recording_spec(run_dir) -> dict[str, object]:
    return {
        "id": "demo",
        "_recording_id": "demo",
        "_hydra_output_dir": str(run_dir),
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


def test_recording_skip_reason_uses_latest_successful_run(
    tmp_path, monkeypatch
) -> None:
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
    monkeypatch.setattr(record, "PROJECT_DATA_DIR", data_dir)
    spec = minimal_recording_spec(run_dir)
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
                    {"path": "recordings/demo.md"},
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
    assert metadata["segments"][0]["timestamps"] == str(target_timestamp)
    assert target_timestamp.read_text(encoding="utf-8") == '{"words": []}\n'
    fingerprint = json.loads(
        target_cast.with_suffix(".recording.json").read_text(encoding="utf-8")
    )
    assert fingerprint["dependencies"] == [
        {"path": "recordings/demo.md"}
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
