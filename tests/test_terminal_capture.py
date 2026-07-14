from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import omegaflow.terminal_capture as terminal_capture
from omegaflow import presentation_build
from omegaflow.capture import CaptureContext, CaptureCoordinator, CaptureFailed
from omegaflow.record import asciinema_command
from omegaflow.recording_plan import normalize_recording_plan
from omegaflow.terminal_capture import (
    PersistentTerminalRunner,
    TerminalControlSession,
)


def test_persistent_terminal_protocol_preserves_state_and_marks_hidden_intervals(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "persistent-terminal",
            "setup": [
                {
                    "name": "prepare state",
                    "run": "mkdir -p shared; export SETUP_VALUE=ready",
                }
            ],
            "beats": [
                {
                    "id": "create",
                    "actions": [
                        {
                            "run": (
                                "cd shared; export BEAT_VALUE=persisted; "
                                "printf '%s\\n' \"$SETUP_VALUE\" > state.txt"
                            )
                        }
                    ],
                    "checks": [
                        {"name": "created", "run": "test -f state.txt"}
                    ],
                },
                {
                    "id": "verify",
                    "actions": [
                        {
                            "run": (
                                f"test \"$PWD\" = \"{tmp_path / 'shared'}\"; "
                                "test \"$BEAT_VALUE\" = persisted; "
                                "test \"$(cat state.txt)\" = ready"
                            )
                        }
                    ],
                },
            ],
            "cleanup": [
                {
                    "name": "record cleanup state",
                    "run": "printf cleaned > cleanup.txt",
                }
            ],
        }
    )
    runner = PersistentTerminalRunner(record_cast=False, timeout_seconds=5.0)
    coordinator = CaptureCoordinator(terminal_runner_factory=lambda: runner)

    result = coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    assert [beat.beat_id for beat in result.beats] == ["create", "verify"]
    assert (tmp_path / "shared" / "cleanup.txt").read_text(encoding="utf-8") == (
        "cleaned"
    )
    events = [
        json.loads(line)
        for line in (tmp_path / "run" / "capture" / "terminal.timeline.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    phases = [event["phase"] for event in events]
    assert phases == [
        "session_start",
        "hidden_start",
        "hidden_end",
        "beat_start",
        "beat_end",
        "hidden_start",
        "hidden_end",
        "beat_start",
        "beat_end",
        "hidden_start",
        "hidden_end",
        "session_end",
    ]
    assert [
        event.get("op") for event in events if event["phase"] == "hidden_start"
    ] == ["setup", "checks", "cleanup"]
    assert not (tmp_path / "run" / "capture" / ".terminal-control").exists()


def test_headed_terminal_session_inherits_interactive_stdio(
    tmp_path: Path, monkeypatch
) -> None:
    context = CaptureContext.create(tmp_path / "run", workspace=tmp_path)
    observed: dict[str, object] = {}

    class FakeProcess:
        pass

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(terminal_capture.subprocess, "Popen", fake_popen)
    session = TerminalControlSession(context, headless=False)
    try:
        session.start()

        assert "--headless" not in observed["command"]
        assert observed["stdin"] is None
        assert observed["stdout"] is None
        assert observed["stderr"] is None
    finally:
        for fd in (session._request_fd, session._response_fd):
            if fd is not None:
                terminal_capture.os.close(fd)
        session._request_fd = None
        session._response_fd = None
        session._process = None
        session._remove_control_streams()


def test_terminal_run_file_executes_in_the_persistent_shell(tmp_path: Path) -> None:
    script = tmp_path / "state.sh"
    script.write_text("export FROM_FILE=yes\ncd nested\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    plan = normalize_recording_plan(
        {
            "id": "run-file",
            "beats": [
                {"id": "source", "actions": [{"run_file": "state.sh"}]},
                {
                    "id": "verify",
                    "actions": [
                        {
                            "run": (
                                "test \"$FROM_FILE\" = yes; "
                                f"test \"$PWD\" = \"{tmp_path / 'nested'}\""
                            )
                        }
                    ],
                },
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=5.0
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)


def test_terminal_run_file_snapshot_survives_cleanup_mutation(
    tmp_path: Path,
) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    script = tmp_path / "recorded-command.sh"
    original_command = "printf 'original command output\\n'\n"
    mutated_command = "printf 'mutated command output\\n'\n"
    script.write_text(original_command, encoding="utf-8")
    spec = {
        "id": "immutable-run-file",
        "_project_root": str(tmp_path),
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False},
        "beats": [
            {
                "id": "recorded",
                "actions": [{"run_file": str(script)}],
            }
        ],
        "cleanup": [
            {
                "run": (
                    "printf %s "
                    f"{shlex.quote(mutated_command)} > {shlex.quote(str(script))}"
                )
            }
        ],
    }
    plan = normalize_recording_plan(spec)
    run_dir = tmp_path / "run"

    presentation_build.capture_recording(spec, plan, run_dir)
    result = presentation_build.compile_presentation_bundle(spec, plan, run_dir)

    assert script.read_text(encoding="utf-8") == mutated_command
    actions = json.loads(
        (run_dir / "capture/terminal-beats/recorded.actions.json").read_text(
            encoding="utf-8"
        )
    )
    assert actions["actions"][0]["command"] == original_command
    assert "original command output" in _cast_output(
        (result.bundle_dir / "beats/recorded.cast").read_text(encoding="utf-8")
    )
    assert "mutated command output" not in _cast_output(
        (result.bundle_dir / "beats/recorded.cast").read_text(encoding="utf-8")
    )


def test_terminal_failure_preserves_postmortem_artifacts(
    tmp_path: Path,
) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    spec = {
        "id": "failed-terminal",
        "_project_root": str(tmp_path),
        "environment": {"working_directory": str(tmp_path)},
        "style": {"color": False},
        "beats": [
            {
                "id": "failure",
                "actions": [
                    {
                        "run": (
                            "printf 'failure stdout\\n'; "
                            "printf 'failure stderr\\n' >&2; exit 7"
                        )
                    }
                ],
            }
        ],
    }
    plan = normalize_recording_plan(spec)
    run_dir = tmp_path / "failed-run"

    with pytest.raises(CaptureFailed, match="capture beat failure"):
        presentation_build.capture_recording(spec, plan, run_dir)

    for name in ("failure.json", "failed.cast", "stdout", "stderr", "progress", "enter"):
        assert (run_dir / name).is_file(), name
    assert (run_dir / "enter").stat().st_mode & 0o111
    assert "failure stdout" in (run_dir / "stdout").read_text(encoding="utf-8")
    assert "failure stderr" in (run_dir / "stderr").read_text(encoding="utf-8")
    failure = json.loads((run_dir / "failure.json").read_text(encoding="utf-8"))
    assert failure["kind"] == "capture"
    assert failure["id"] == "capture beat failure"
    assert failure["postmortem_path"] == str(run_dir / "enter")
    assert "terminal step exited 7, expected 0" in failure["message"]
    assert "terminal step exited 7, expected 0" in failure["stderr"]


def test_persistent_terminal_preserves_user_shell_state(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    plan = normalize_recording_plan(
        {
            "id": "shell-state",
            "beats": [
                {
                    "id": "define",
                    "actions": [
                        {
                            "run": "\n".join(
                                (
                                    "cd child",
                                    "export DEMO_EXPORTED=ready",
                                    "DEMO_LOCAL=local",
                                    "demo_function() { printf 'function-ok\\n'; }",
                                    "shopt -s expand_aliases extglob",
                                    'alias demo_alias="printf \'alias-ok\\n\'"',
                                    "set -f",
                                    "trap 'printf user-trap > user-trap.txt' EXIT",
                                )
                            )
                        }
                    ],
                },
                {
                    "id": "verify",
                    "actions": [
                        {
                            "run": "\n".join(
                                (
                                    'test "$DEMO_EXPORTED" = ready',
                                    'test "$DEMO_LOCAL" = local',
                                    "demo_function",
                                    "demo_alias",
                                    "[[ $- == *f* ]]",
                                    "shopt -q expand_aliases",
                                    "[[ foobar == +(foo|bar) ]]",
                                )
                            ),
                            "expect": {
                                "output_contains": ["function-ok", "alias-ok"]
                            },
                        }
                    ],
                },
            ],
        }
    )

    CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=2.0
        )
    ).capture(plan, tmp_path / "run", workspace=tmp_path)

    assert (tmp_path / "child" / "user-trap.txt").read_text(
        encoding="utf-8"
    ) == "user-trap"


def test_persistent_terminal_reports_expected_exit_without_hanging(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "expected-exit",
            "beats": [
                {
                    "id": "exit",
                    "actions": [
                        {"run": "exit 7", "expect": {"exit_code": 7}}
                    ],
                }
            ],
        }
    )
    started = time.monotonic()

    CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=2.0
        )
    ).capture(plan, tmp_path / "run", workspace=tmp_path)

    assert time.monotonic() - started < 3.0


@pytest.mark.parametrize("command", ["if then", "set -e; false; printf should-not-run"])
def test_persistent_terminal_shell_failure_is_bounded(
    tmp_path: Path, command: str
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "bounded-failure",
            "beats": [
                {"id": "failure", "actions": [{"run": command}]},
            ],
        }
    )
    started = time.monotonic()

    with pytest.raises(Exception, match="exit 1"):
        CaptureCoordinator(
            terminal_runner_factory=lambda: PersistentTerminalRunner(
                record_cast=False, timeout_seconds=2.0
            )
        ).capture(plan, tmp_path / "run", workspace=tmp_path)

    assert time.monotonic() - started < 3.0
    assert "should-not-run" not in (
        tmp_path / "run" / "capture" / "terminal.stdout.log"
    ).read_text(encoding="utf-8")


def test_persistent_terminal_background_process_cleanup_is_bounded(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "background-cleanup",
            "beats": [
                {
                    "id": "background",
                    "actions": [
                        {
                            "run": (
                                "nohup sleep 10 >/dev/null 2>&1 & "
                                "echo $! > background.pid"
                            )
                        }
                    ],
                }
            ],
        }
    )
    started = time.monotonic()

    CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=2.0
        )
    ).capture(plan, tmp_path / "run", workspace=tmp_path)

    assert time.monotonic() - started < 3.0
    background_pid = int((tmp_path / "background.pid").read_text(encoding="utf-8"))
    assert subprocess.run(
        ["kill", "-0", str(background_pid)], capture_output=True, check=False
    ).returncode != 0


def test_persistent_terminal_cleanup_can_stop_a_background_job(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "explicit-background-cleanup",
            "beats": [
                {
                    "id": "background",
                    "actions": [
                        {
                            "run": (
                                "sleep 10 & export SERVER_PID=$!; "
                                "printf '%s' \"$SERVER_PID\" > background.pid"
                            )
                        }
                    ],
                }
            ],
            "cleanup": [
                {
                    "run": (
                        "kill \"$SERVER_PID\"; "
                        "wait \"$SERVER_PID\" 2>/dev/null || true"
                    )
                }
            ],
        }
    )

    CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=2.0
        )
    ).capture(plan, tmp_path / "run", workspace=tmp_path)

    background_pid = int((tmp_path / "background.pid").read_text(encoding="utf-8"))
    assert subprocess.run(
        ["kill", "-0", str(background_pid)], capture_output=True, check=False
    ).returncode != 0


def test_terminal_protocol_runs_inside_one_asciinema_capture(tmp_path: Path) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    plan = normalize_recording_plan(
        {
            "id": "cast",
            "setup": [{"run": "printf 'hidden-setup\\n'"}],
            "beats": [
                {
                    "id": "one",
                    "actions": [{"run": "printf 'one\\n'"}],
                    "checks": [{"run": "printf 'hidden-check\\n'"}],
                },
                {"id": "two", "actions": [{"run": "printf 'two\\n'"}]},
            ],
            "cleanup": [{"run": "printf 'hidden-cleanup\\n'"}],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=True, title="Persistent terminal test", timeout_seconds=5.0
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    cast_path = tmp_path / "run" / "capture" / "terminal.cast"
    assert cast_path.is_file()
    assert cast_path.stat().st_size > 0
    beat_dir = cast_path.parent / "terminal-beats"
    one = (beat_dir / "one.cast").read_text(encoding="utf-8")
    two = (beat_dir / "two.cast").read_text(encoding="utf-8")
    one_actions = json.loads(
        (beat_dir / "one.actions.json").read_text(encoding="utf-8")
    )
    two_actions = json.loads(
        (beat_dir / "two.actions.json").read_text(encoding="utf-8")
    )
    assert "one" in one and "two" not in one
    assert "two" in two and "one" not in two
    one_output = "".join(
        json.loads(line)[2] for line in one.splitlines()[1:]
    )
    assert "$ printf 'one\\n'" in one_output
    assert [item["id"] for item in one_actions["actions"]] == ["__step_0"]
    assert [item["id"] for item in two_actions["actions"]] == ["__step_0"]
    assert (
        one_actions["actions"][0]["end_ms"]
        >= one_actions["actions"][0]["start_ms"]
    )
    for value in (one, two):
        assert "hidden-setup" not in value
        assert "hidden-check" not in value
        assert "hidden-cleanup" not in value
        assert "OmegaFlow;" not in value
        assert "OmegaFlowAction;" not in value
        first_event = json.loads(value.splitlines()[1])
        assert first_event[0] >= 0


def test_persistent_terminal_honors_exit_output_regex_and_file_gates(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "terminal-gates",
            "beats": [
                {
                    "id": "gates",
                    "actions": [
                        {
                            "run": "printf 'gate value 42\\n'; touch created.txt; false",
                            "expect": {
                                "exit_code": 1,
                                "output_contains": ["gate value"],
                                "output_regex": [r"value [0-9]+"],
                                "file_exists": ["created.txt"],
                            },
                        },
                        {
                            "commands": [
                                {"run": "printf 'group one\\n'"},
                                {"run": "printf 'group two\\n'"},
                            ],
                            "expect": {
                                "output_contains": ["group one", "group two"]
                            },
                        },
                    ],
                }
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=5.0
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    assert (tmp_path / "created.txt").is_file()
