from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from omegaflow.capture import CaptureContext
from omegaflow.envoy_session import EnvoyOperationResult, EnvoySessionError
from omegaflow.envoy_terminal_capture import EnvoyPersistentTerminalRunner
from omegaflow.presentation_build import _load_terminal_actions
from omegaflow.recording_plan import (
    capture_runner_beat,
    captured_pane_beats,
    normalize_recording_plan,
)
from omegaflow.terminal_capture import TerminalCaptureError


class FakeSession:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.cast_path = output_dir / "terminal.cast"
        self.raw_path = output_dir / "terminal.output.log"
        self.timeline_path = output_dir / "terminal.timeline.jsonl"
        self.elapsed_ms = 0
        self.cast_event_count = 0
        self.mode = "real"
        self.replacement = ""
        self.closed = False

    @property
    def raw_offset(self) -> int:
        return self.raw_path.stat().st_size

    def start(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cast_path.write_text(
            '{"version":3,"term":{"cols":80,"rows":24}}\n', encoding="utf-8"
        )
        self.raw_path.write_bytes(b"")
        self.timeline_path.write_text("", encoding="utf-8")

    def cast_checkpoint(self) -> tuple[int, int]:
        return self.cast_path.stat().st_size, self.cast_event_count

    def present(self, text: str, **_: object) -> None:
        self._cast(text)

    def begin_operation_output(self, mode: str, replacement: str = "") -> None:
        self.mode, self.replacement = mode, replacement

    def end_operation_output(self) -> None:
        if self.mode == "replace":
            self._cast(self.replacement)
        self.mode, self.replacement = "real", ""

    def execute(self, operation_id: str, source: str, **_: object) -> EnvoyOperationResult:
        start = self.raw_path.stat().st_size
        output = ("ran:" + source + "\n").encode()
        with self.raw_path.open("ab") as handle:
            handle.write(output)
        if self.mode == "real":
            self._cast(output.decode())
        self.elapsed_ms += 5
        return EnvoyOperationResult(
            operation_id, 0, "/work", start, start + len(output)
        )

    def read_output_range(self, start: int, through: int) -> bytes:
        with self.raw_path.open("rb") as handle:
            handle.seek(start)
            return handle.read(through - start)

    def write_cast_slice(self, start: int, through: int, destination: Path) -> None:
        with self.cast_path.open("rb") as handle:
            header = handle.readline()
            handle.seek(start)
            payload = handle.read(through - start)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(header + payload)

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.closed = True

    def _cast(self, text: str) -> None:
        with self.cast_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps([0, "o", text], separators=(",", ":")) + "\n")
        self.cast_event_count += 1
        self.elapsed_ms += 1


class RealtimeFakeSession(FakeSession):
    def __init__(self, output_dir: Path) -> None:
        super().__init__(output_dir)
        self.state = SimpleNamespace(phase="idle")
        self.ready = SimpleNamespace(cwd="/work")
        self._finish = threading.Event()
        self._cancelled = False
        self._artifact_lock = threading.Lock()

    def execute(self, operation_id: str, source: str, **_: object) -> EnvoyOperationResult:
        start = self.raw_offset
        self.state.phase = "running"
        self.emit(b"ready\n")
        assert self._finish.wait(2)
        if not self._cancelled:
            self.emit(b"done\n")
        self.state.phase = "idle"
        return EnvoyOperationResult(
            operation_id,
            130 if self._cancelled else 0,
            "/work",
            start,
            self.raw_offset,
            cancelled=self._cancelled,
        )

    def send_input(self, payload: bytes) -> None:
        if payload == b"b":
            self._finish.set()

    def cancel(self, _operation_id: str, _reason: str) -> None:
        self._cancelled = True
        self._finish.set()

    def emit(self, payload: bytes) -> None:
        with self._artifact_lock:
            with self.raw_path.open("ab") as handle:
                handle.write(payload)
            if self.mode == "real":
                self._cast(payload.decode())


class BashProbeSession(FakeSession):
    def execute(self, operation_id: str, source: str, **_: object) -> EnvoyOperationResult:
        completed = subprocess.run(
            ["/usr/bin/bash", "-c", "__probe() { " + source + "; }; __probe"],
            capture_output=True,
            check=False,
        )
        start = self.raw_offset
        with self.raw_path.open("ab") as handle:
            handle.write(completed.stdout)
        return EnvoyOperationResult(
            operation_id,
            completed.returncode,
            "/work",
            start,
            start + len(completed.stdout),
        )


def test_envoy_runner_writes_direct_beat_cast_and_action_ranges(tmp_path: Path) -> None:
    plan = normalize_recording_plan(
        {
            "id": "demo",
            "beats": [
                {
                    "id": "one",
                    "actions": [
                        {
                            "run": "printf hello",
                            "expect": {"output_contains": ["printf hello"]},
                        }
                    ],
                }
            ],
        }
    )
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = FakeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(lambda _context: fake)
    runner.start(context)
    result = runner.capture_beat(plan.beats[0])
    runner.close()

    cast = result.artifacts[0].read_text(encoding="utf-8")
    actions = json.loads(result.artifacts[1].read_text(encoding="utf-8"))
    assert "$ printf hello\nran:printf hello\n$ " in "".join(
        json.loads(line)[2] for line in cast.splitlines()[1:]
    )
    assert actions["version"] == 1
    assert actions["beat_id"] == "one"
    assert actions["actions"][0]["id"] == "__step_0"
    assert actions["actions"][0]["event_indexes"]["output_start"] == 3
    loaded = _load_terminal_actions(
        result.artifacts[1],
        beat_id="one",
        expected_action_ids=("__step_0",),
    )
    assert loaded["__step_0"].timing == "presentation"
    assert loaded["__step_0"].presentation_snapshot["display"] == "printf hello"
    assert fake.closed


def test_envoy_runner_replacement_output_does_not_change_raw_log(tmp_path: Path) -> None:
    plan = normalize_recording_plan(
        {
            "id": "demo",
            "beats": [
                {
                    "id": "one",
                    "actions": [
                        {"run": "printf secret", "output": {"replace": "shown\n"}}
                    ],
                }
            ],
        }
    )
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = FakeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(lambda _context: fake)
    runner.start(context)
    result = runner.capture_beat(plan.beats[0])
    runner.close()

    cast = result.artifacts[0].read_text(encoding="utf-8")
    assert "shown" in cast
    assert "ran:printf secret" not in cast
    assert b"ran:printf secret" in fake.raw_path.read_bytes()


def test_envoy_runner_rejects_secret_output(tmp_path: Path) -> None:
    class SecretFakeSession(FakeSession):
        def execute(
            self, operation_id: str, _source: str, **_: object
        ) -> EnvoyOperationResult:
            start = self.raw_offset
            output = b"leaked-token\n"
            with self.raw_path.open("ab") as handle:
                handle.write(output)
            if self.mode == "real":
                self._cast(output.decode())
            return EnvoyOperationResult(
                operation_id,
                0,
                "/work",
                start,
                start + len(output),
            )

    plan = normalize_recording_plan(
        {
            "id": "demo",
            "beats": [{"id": "one", "actions": [{"run": "true"}]}],
        }
    )
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = SecretFakeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(
        lambda _context: fake,
        secret_environment={"TOKEN": "leaked-token"},
    )
    runner.start(context)

    with pytest.raises(TerminalCaptureError) as exc_info:
        runner.capture_beat(plan.beats[0])

    assert exc_info.value.failure_kind == "secret"
    runner.cancel_capture()


def test_envoy_runner_continues_one_realtime_operation_without_losing_gap_output(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "demo",
            "panes": [{"id": "terminal", "kind": "terminal"}],
            "beats": [
                {
                    "id": "start",
                    "layout": {"areas": [["terminal"]]},
                    "panes": {
                        "terminal": [
                            {
                                "id": "start-pane",
                                "actions": [
                                    {
                                        "id": "producer",
                                        "run": "read value",
                                        "timing": "realtime",
                                        "input": [{"wait_for": "ready"}],
                                        "expect": {
                                            "output_contains": ["ready", "done"]
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                },
                {
                    "id": "finish",
                    "layout": {"areas": [["terminal"]]},
                    "panes": {
                        "terminal": [
                            {
                                "id": "finish-pane",
                                "actions": [
                                    {
                                        "id": "consumer",
                                        "continue_from": "producer",
                                        "timing": "realtime",
                                        "input": [
                                            {"text": "b", "interval": 0},
                                            {"wait_for": "done", "timeout": 1},
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                },
            ],
        }
    )
    captured = captured_pane_beats(plan)
    first = capture_runner_beat(plan, captured[0])
    second = capture_runner_beat(plan, captured[1])
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = RealtimeFakeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(lambda _context: fake, timeout_seconds=2)
    runner.start(context)

    first_capture = runner.capture_beat(first)
    fake.emit(b"between-beats\n")
    second_capture = runner.capture_beat(second)
    runner.close()

    first_cast = first_capture.artifacts[0].read_text(encoding="utf-8")
    second_cast = second_capture.artifacts[0].read_text(encoding="utf-8")
    second_output = "".join(
        json.loads(line)[2] for line in second_cast.splitlines()[1:]
    )
    assert "ready" in first_cast
    assert "done" not in first_cast
    assert "between-beats" in second_cast
    assert "done" in second_cast
    assert not second_output.startswith("$ \n")


def test_envoy_runner_directory_producer_hash_matches_native_contract(
    tmp_path: Path,
) -> None:
    produced = tmp_path / "produced"
    (produced / "nested").mkdir(parents=True)
    (produced / "a.txt").write_bytes(b"alpha")
    (produced / "nested" / "b.txt").write_bytes(b"beta")
    (produced / "link").symlink_to("a.txt")
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = BashProbeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(lambda _context: fake)
    runner.start(context)

    result = runner._probe_produced_output("producer", "bundle", str(produced))

    digest = hashlib.sha256(b"directory\0")
    for entry in sorted(
        produced.rglob("*"), key=lambda item: item.relative_to(produced).as_posix()
    ):
        relative = entry.relative_to(produced).as_posix().encode("utf-8")
        if entry.is_symlink():
            digest.update(b"link\0" + relative + b"\0")
            digest.update(os.readlink(entry).encode("utf-8") + b"\0")
        elif entry.is_dir():
            digest.update(b"dir\0" + relative + b"\0")
        elif entry.is_file():
            digest.update(b"file\0" + relative + b"\0")
            digest.update(entry.read_bytes())
            digest.update(b"\0")
    assert result == {
        "producer": "producer",
        "output": "bundle",
        "path": str(produced.resolve()),
        "kind": "directory",
        "sha256": digest.hexdigest(),
    }


def test_envoy_runner_finalizes_open_realtime_operation_during_close(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "demo",
            "panes": [{"id": "terminal", "kind": "terminal"}],
            "beats": [
                {
                    "id": "edit",
                    "layout": {"areas": [["terminal"]]},
                    "panes": {
                        "terminal": [
                            {
                                "id": "edit-pane",
                                "actions": [
                                    {
                                        "id": "editor",
                                        "run": "while :; do sleep 60; done",
                                        "timing": "realtime",
                                        "input": [{"wait_for": "ready", "timeout": 1}],
                                        "expect": {"output_contains": ["ready"]},
                                    }
                                ],
                            }
                        ]
                    },
                }
            ],
        }
    )
    captured = captured_pane_beats(plan)
    beat = capture_runner_beat(plan, captured[0])
    command = beat.actions[0].config["commands"][0]
    assert command["_finalize_open_at_recording_end"] is True
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = RealtimeFakeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(lambda _context: fake, timeout_seconds=2)
    runner.start(context)
    started = time.monotonic()

    capture = runner.capture_beat(beat)
    runner.close()

    assert time.monotonic() - started < 1
    output = "".join(
        json.loads(line)[2]
        for line in capture.artifacts[0].read_text(encoding="utf-8").splitlines()[1:]
    )
    assert "ready" in output
    assert not output.endswith("$ ")
    assert fake.closed


def test_envoy_runner_translates_continuation_session_failure(tmp_path: Path) -> None:
    class FailingRealtimeSession(RealtimeFakeSession):
        def execute(
            self, operation_id: str, _source: str, **_: object
        ) -> EnvoyOperationResult:
            self.state.phase = "running"
            self.emit(b"ready\n")
            assert self._finish.wait(2)
            self.state.phase = "idle"
            raise EnvoySessionError("injected continuation failure")

    plan = normalize_recording_plan(
        {
            "id": "demo",
            "panes": [{"id": "terminal", "kind": "terminal"}],
            "beats": [
                {
                    "id": "start",
                    "layout": {"areas": [["terminal"]]},
                    "panes": {
                        "terminal": [
                            {
                                "id": "start-pane",
                                "actions": [
                                    {
                                        "id": "producer",
                                        "run": "read value",
                                        "timing": "realtime",
                                        "input": [{"wait_for": "ready"}],
                                    }
                                ],
                            }
                        ]
                    },
                },
                {
                    "id": "finish",
                    "layout": {"areas": [["terminal"]]},
                    "panes": {
                        "terminal": [
                            {
                                "id": "finish-pane",
                                "actions": [
                                    {
                                        "id": "consumer",
                                        "continue_from": "producer",
                                        "timing": "realtime",
                                        "input": [
                                            {"text": "b", "interval": 0},
                                            {"pause": 0.05},
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                },
            ],
        }
    )
    captured = captured_pane_beats(plan)
    first = capture_runner_beat(plan, captured[0])
    second = capture_runner_beat(plan, captured[1])
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path).for_runner(
        "terminal"
    )
    fake = FailingRealtimeSession(context.runner_capture)
    runner = EnvoyPersistentTerminalRunner(lambda _context: fake, timeout_seconds=2)
    runner.start(context)
    runner.capture_beat(first)

    with pytest.raises(TerminalCaptureError, match="Envoy continuation.*injected"):
        runner.capture_beat(second)

    runner.cancel_capture()
