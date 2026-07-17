from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import omegaflow.presentation_build as presentation_build
import omegaflow.studio as studio
from omegaflow import audio as audio_module
from omegaflow.capture import CaptureContext
from omegaflow.presentation_build import (
    _capture_environment,
    _source_words_with_timing,
    capture_recording,
    compile_presentation_bundle,
    prepare_narration_audio,
    public_bundle_dir,
    publish_bundle,
    validate_run_bundle,
    write_capture_fingerprint,
)
from omegaflow.recording_plan import normalize_recording_plan


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
        "capture": {"headless": True},
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


def test_mixed_capture_compiles_validates_and_publishes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    output_dir = tmp_path / "public"
    spec = {
        "id": "mixed",
        "title": "Mixed demo",
        "outputs": {"asset_dir": str(output_dir)},
        "browser": {},
        "audio": {"enabled": False},
        "beats": [
            {"id": "prepare", "actions": [{"run": "printf ready"}]},
            {
                "id": "web",
                "medium": "browser",
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
    assert [beat["renderer"] for beat in manifest["beats"]] == [
        "terminal",
        "browser",
        "terminal",
    ]
    assert result.manifest == run_dir / "presentation/recording.presentation.json"
    assert not any(
        "capture" in path.relative_to(result.bundle_dir).parts
        for path in result.bundle_dir.rglob("*")
    )

    destination = publish_bundle(spec, run_dir)

    assert destination == public_bundle_dir(spec)
    assert (destination / "recording.presentation.json").is_file()
    assert list((destination / "media").glob("*.webp"))


def test_prepare_narration_audio_writes_cross_beat_v3_metadata(
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
            path.write_text('{"words": []}\n', encoding="utf-8")

    monkeypatch.setattr(studio.audio, "generate_audio", fake_generate_audio)
    monkeypatch.setattr(studio.audio, "generate_timestamps", fake_generate_timestamps)
    monkeypatch.setattr(studio.audio, "audio_duration_seconds", lambda _path: 2.0)

    artifacts = prepare_narration_audio(spec, plan, tmp_path / "run")

    assert artifacts is not None
    metadata = json.loads(artifacts.metadata.read_text(encoding="utf-8"))
    assert metadata["version"] == 3
    assert metadata["takes"][0]["src"].startswith("audio/joined-")
    assert metadata["takes"][0]["sha256"] in metadata["takes"][0]["src"]
    assert [member["beat_id"] for member in metadata["takes"][0]["members"]] == [
        "terminal",
        "browser",
    ]
    assert set(artifacts.timestamps) == {"joined"}


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
            on_activity(1536, 2.5)
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

    prepare_narration_audio(
        spec,
        plan,
        tmp_path / "run",
        on_progress=lambda message, current, total: progress.append(
            (message, current, total)
        ),
    )

    assert progress == [
        ("Generate narration: Say hello", 0, 3),
        ("Generate narration: Say hello · 2.5s · 1.5 KiB received", 0, 3),
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
