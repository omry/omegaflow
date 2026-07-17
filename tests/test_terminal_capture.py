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
    extract_terminal_beat_casts,
)


def test_handoff_marker_ends_visible_terminal_beat_before_watch_process_exits(
    tmp_path: Path,
) -> None:
    cast_path = tmp_path / "terminal.cast"
    cast_path.write_text(
        "\n".join(
            [
                json.dumps({"version": 3, "width": 80, "height": 24}),
                json.dumps(
                    [
                        0.1,
                        "o",
                        "\x1b]1337;OmegaFlow;1;beat;start;watch\x07"
                        "\x1b]1337;OmegaFlowAction;watch;watch_command;start\x07"
                        "$ omegaflow recording=demo action=watch\r\n",
                    ]
                ),
                json.dumps(
                    [
                        0.2,
                        "o",
                        "pass  serving local watch server: http://127.0.0.1:43123/\r\n"
                        "info  opened isolated system browser; close it or press Ctrl-C to stop\r\n"
                        "\x1b]1337;OmegaFlowBrowserHandoff;watch_command;ready\x07",
                    ]
                ),
                json.dumps(
                    [
                        2.0,
                        "o",
                        "info  stopped local watch server\r\n"
                        "\x1b]1337;OmegaFlowAction;watch;watch_command;end\x07"
                        "\x1b]1337;OmegaFlow;1;beat;end;watch\x07",
                    ]
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    extract_terminal_beat_casts(
        cast_path,
        tmp_path / "beats",
        expected_beat_ids=("watch",),
    )

    output = (tmp_path / "beats" / "watch.cast").read_text(encoding="utf-8")
    actions = json.loads(
        (tmp_path / "beats" / "watch.actions.json").read_text(encoding="utf-8")
    )
    assert "serving local watch server" in output
    assert "opened isolated system browser" in output
    assert "stopped local watch server" not in output
    assert actions["actions"] == [
        {
            "id": "watch_command",
            "start_ms": 0,
            "end_ms": 200,
            "duration_ms": 200,
        }
    ]


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


def test_terminal_commands_report_live_action_boundaries(tmp_path: Path) -> None:
    plan = normalize_recording_plan(
        {
            "id": "progress",
            "beats": [
                {
                    "id": "commands",
                    "actions": [
                        {
                            "commands": [
                                {"id": "one", "run": "printf one"},
                                {"id": "two", "run": "printf two"},
                            ]
                        }
                    ],
                }
            ],
        }
    )
    progress: list[tuple[str, str, int, int]] = []

    CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=5.0
        )
    ).capture(
        plan,
        tmp_path / "run",
        workspace=tmp_path,
        on_progress=lambda state, action, current, total: progress.append(
            (state, action.action_id, current, total)
        ),
    )

    assert progress == [
        ("started", "one", 0, 2),
        ("completed", "one", 1, 2),
        ("started", "two", 1, 2),
        ("completed", "two", 2, 2),
    ]


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


def test_persistent_terminal_applies_recorded_output_policy(tmp_path: Path) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    plan = normalize_recording_plan(
        {
            "id": "output-policy",
            "beats": [
                {
                    "id": "replace",
                    "actions": [
                        {
                            "run": "printf 'private real output\\n'",
                            "display": "generate replaceable output",
                            "output": {"replace": "public replacement output"},
                            "expect": {"output_contains": ["private real output"]},
                        }
                    ],
                },
                {
                    "id": "suppress",
                    "actions": [
                        {
                            "run": "printf 'suppressed real output\\n'",
                            "display": "generate suppressible output",
                            "output": "suppress",
                            "expect": {"output_contains": ["suppressed real output"]},
                        }
                    ],
                },
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=True, title="Output policy test", timeout_seconds=5.0
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    beat_dir = tmp_path / "run" / "capture" / "terminal-beats"
    replaced = (beat_dir / "replace.cast").read_text(encoding="utf-8")
    suppressed = (beat_dir / "suppress.cast").read_text(encoding="utf-8")
    assert "public replacement output" in replaced
    assert "private real output" not in replaced
    assert "suppressed real output" not in suppressed
    assert _cast_output(replaced).endswith("public replacement output\r\n$ ")
    assert _cast_output(suppressed).endswith(
        "$ generate suppressible output\r\n$ "
    )


def test_persistent_terminal_reuses_visible_prompt_between_commands(
    tmp_path: Path,
) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    plan = normalize_recording_plan(
        {
            "id": "prompt-state",
            "beats": [
                {
                    "id": "commands",
                    "actions": [
                        {
                            "commands": [
                                {
                                    "run": "printf 'first output\\n'",
                                    "display": "first command",
                                },
                                {
                                    "run": "printf 'second output\\n'",
                                    "display": "second command",
                                    "show_prompt_after": False,
                                },
                            ]
                        }
                    ],
                },
                {
                    "id": "fresh-beat",
                    "actions": [
                        {
                            "run": "printf 'fresh output\\n'",
                            "display": "fresh command",
                        }
                    ],
                },
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=True, title="Prompt state test", timeout_seconds=5.0
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    beat_dir = tmp_path / "run" / "capture" / "terminal-beats"
    commands = _cast_output(
        (beat_dir / "commands.cast").read_text(encoding="utf-8")
    )
    fresh = _cast_output(
        (beat_dir / "fresh-beat.cast").read_text(encoding="utf-8")
    )
    assert commands == (
        "$ first command\r\n"
        "first output\r\n"
        "$ second command\r\n"
        "second output\r\n"
    )
    assert fresh == "$ fresh command\r\nfresh output\r\n$ "


def test_persistent_terminal_colors_prompt_and_command_when_enabled(
    tmp_path: Path,
) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    plan = normalize_recording_plan(
        {
            "id": "colored-prompt",
            "beats": [
                {
                    "id": "command",
                    "actions": [
                        {
                            "run": "printf 'colored output\\n'",
                            "display": "colored command",
                        }
                    ],
                }
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=True,
            title="Colored prompt test",
            color=True,
            timeout_seconds=5.0,
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    output = _cast_output(
        (
            tmp_path
            / "run"
            / "capture"
            / "terminal-beats"
            / "command.cast"
        ).read_text(encoding="utf-8")
    )
    assert (
        "\x1b[32;1m$\x1b[0m \x1b[1mcolored command\x1b[0m\r\n"
        in output
    )
    assert output.endswith("\x1b[32;1m$\x1b[0m ")


def test_terminal_beat_keeps_prompt_visible_while_command_waits_and_types(
    tmp_path: Path,
) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    plan = normalize_recording_plan(
        {
            "id": "typed-command",
            "beats": [
                {
                    "id": "typed",
                    "actions": [
                        {
                            "commands": [
                                {
                                    "id": "command",
                                    "run": "printf 'finished\\n'",
                                    "display": "abcde",
                                    "pre_command_pause": 0.2,
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=True,
            typing=True,
            typing_min_delay=0.02,
            typing_max_delay=0.02,
            typing_space_delay=0,
            typing_punctuation_delay=0,
            typing_newline_delay=0,
            post_enter_pause=0,
            post_command_pause=0,
            timeout_seconds=5.0,
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    beat_dir = tmp_path / "run" / "capture" / "terminal-beats"
    source = beat_dir / "typed.cast"
    action_payload = json.loads(
        (beat_dir / "typed.actions.json").read_text(encoding="utf-8")
    )
    interval = action_payload["actions"][0]
    destination = tmp_path / "published.cast"
    presentation_build.materialize_terminal_beat(
        source,
        destination,
        duration_ms=2_000,
        captured_action_intervals_ms={
            "command": (interval["start_ms"], interval["end_ms"])
        },
        action_starts_ms={"command": 700},
    )

    absolute_ms = 0
    visible_events: list[tuple[int, str]] = []
    for line in destination.read_text(encoding="utf-8").splitlines()[1:]:
        event = json.loads(line)
        absolute_ms += round(float(event[0]) * 1000)
        if event[1] == "o" and event[2]:
            visible_events.append((absolute_ms, event[2]))

    assert visible_events[0][0] <= 10
    assert visible_events[0][1] == "$ "
    typed_events: list[tuple[int, str]] = []
    for at_ms, text in visible_events[1:]:
        if "\n" in text:
            break
        typed_events.append((at_ms, text))
    assert typed_events[0][0] >= 900
    assert "".join(text for _, text in typed_events).startswith("abcde")
    assert len(typed_events) >= 3
    assert all(text != "abcde" for _, text in typed_events)


def test_terminal_beat_follow_along_streams_output_during_command(
    tmp_path: Path,
) -> None:
    if shutil.which(asciinema_command()) is None:
        pytest.skip("asciinema is unavailable")
    plan = normalize_recording_plan(
        {
            "id": "follow-output",
            "beats": [
                {
                    "id": "follow",
                    "actions": [
                        {
                            "commands": [
                                {
                                    "id": "count",
                                    "run": (
                                        "printf '1\\n'; sleep 0.25; "
                                        "printf '2\\n'; sleep 0.25; "
                                        "printf '3\\n'"
                                    ),
                                    "display": "count slowly",
                                    "follow_along": True,
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=True,
            typing=False,
            post_enter_pause=0,
            post_command_pause=0,
            timeout_seconds=5.0,
        )
    )

    coordinator.capture(plan, tmp_path / "run", workspace=tmp_path)

    source = tmp_path / "run/capture/terminal-beats/follow.cast"
    absolute_ms = 0
    output_times: dict[str, int] = {}
    for line in source.read_text(encoding="utf-8").splitlines()[1:]:
        event = json.loads(line)
        absolute_ms += round(float(event[0]) * 1000)
        if event[1] != "o":
            continue
        text = str(event[2]).strip()
        if text in {"1", "2", "3"}:
            output_times[text] = absolute_ms

    assert list(output_times) == ["1", "2", "3"]
    assert output_times["2"] - output_times["1"] >= 150
    assert output_times["3"] - output_times["2"] >= 150


def _cast_output(cast: str) -> str:
    return "".join(json.loads(line)[2] for line in cast.splitlines()[1:])


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
