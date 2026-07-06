import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from omegaflow_studio import __version__
from omegaflow_studio import record
from omegaflow_studio import retime_cast
from omegaflow_studio import studio
from omegaflow_studio.record import collect_run_jobs
from omegaflow_studio.studio_config import (
    CONFIG_DIR,
    RECORDING_SCRIPT_DIR,
    discover_project_layout,
    studio_run_dir,
)


def test_version_is_available() -> None:
    assert __version__ == "0.1.0"


def test_studio_paths_use_studio_project_directory() -> None:
    assert CONFIG_DIR.parts[-2:] == ("studio", "conf")
    assert RECORDING_SCRIPT_DIR.parts[-2:] == ("studio", "recordings")


def test_discovers_media_project_directory(tmp_path, monkeypatch) -> None:
    media_conf = tmp_path / "media" / "conf"
    media_conf.mkdir(parents=True)
    (media_conf / "config.yaml").write_text("action: build\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_RECORDING_DIR", raising=False)
    monkeypatch.delenv("OMEGAFLOW_STUDIO_DATA_DIR", raising=False)

    layout = discover_project_layout()

    assert layout.root == tmp_path
    assert layout.config_dir == media_conf
    assert layout.recording_script_dir == tmp_path / "media" / "recording-scripts"


def test_studio_run_dir_uses_studio_directory() -> None:
    assert (
        studio_run_dir("studio", "build", "record", False, "demo", "20260705-010203")
        == "studio/runs/demo/20260705-010203"
    )
    assert (
        studio_run_dir("studio", "inspect", None, False, "demo", "20260705-010203")
        == "studio/runs/.scratch/inspect/demo/20260705-010203"
    )


def test_studio_run_dir_routes_missing_recording_to_scratch() -> None:
    assert (
        studio_run_dir("media", "build", None, False, None, "20260705-010203")
        == "media/runs/.scratch/build/unselected/20260705-010203"
    )


def test_studio_run_dir_keeps_legacy_signature() -> None:
    assert (
        studio_run_dir("inspect", None, False, "demo", "20260705-010203")
        == "studio/runs/.scratch/inspect/demo/20260705-010203"
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
                    {"path": "media/recording-scripts/demo.yaml"},
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
        {"path": "media/recording-scripts/demo.yaml"}
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
