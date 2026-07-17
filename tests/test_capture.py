from __future__ import annotations

import stat
import threading
import time
from pathlib import Path

import pytest

import omegaflow.capture as capture_module
from omegaflow.capture import (
    BeatCapture,
    CaptureCoordinator,
    CaptureContext,
    CaptureFailed,
    CaptureFailureCollector,
    CaptureSetupError,
    capture_action_items,
    prepare_capture_paths,
)
from omegaflow.browser_handoff import (
    BROWSER_HANDOFF_ID_ENV,
    BrokeredBrowserSession,
)
from omegaflow.recording_plan import normalize_recording_plan
from omegaflow.recording_plan import RecordingPlan


def test_capture_context_creates_private_staged_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = CaptureContext.create(
        tmp_path / "runs" / "demo",
        workspace=workspace,
        environment={"EXISTING": "value"},
    )

    assert context.workspace == workspace.resolve()
    assert context.working_directory == workspace.resolve()
    assert context.environment["EXISTING"] == "value"
    assert context.environment["OMEGAFLOW_RUN_DIR"] == str(context.paths.run)
    assert context.environment["TMPDIR"] == str(context.paths.temporary)
    for path in (
        context.paths.run,
        context.paths.capture,
        context.paths.diagnostics,
        context.paths.temporary,
    ):
        assert path.is_dir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700

    with pytest.raises(TypeError):
        context.environment["MUTATED"] = "no"  # type: ignore[index]


def test_capture_context_keeps_explicit_working_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workdir = workspace / "project"
    workdir.mkdir(parents=True)

    context = CaptureContext.create(
        tmp_path / "run",
        workspace=workspace,
        working_directory=workdir,
        environment={},
    )

    assert context.working_directory == workdir.resolve()
    assert "OMEGAFLOW_WORKSPACE" not in context.environment
    assert "OMEGAFLOW_WORKDIR" not in context.environment


def test_prepare_capture_paths_rejects_symlink_and_file(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(CaptureSetupError, match="symlink"):
        prepare_capture_paths(link)

    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(CaptureSetupError, match="could not create"):
        prepare_capture_paths(file_path)


def test_private_path_rejects_escape(tmp_path: Path) -> None:
    paths = prepare_capture_paths(tmp_path / "run")

    assert paths.private_path("capture", "state.png") == (
        paths.run / "capture" / "state.png"
    )
    with pytest.raises(CaptureSetupError, match="stay below"):
        paths.private_path("..", "secret")
    with pytest.raises(CaptureSetupError, match="stay below"):
        paths.private_path(str(tmp_path / "outside"))


def test_failure_collector_retains_primary_and_all_cleanup_failures() -> None:
    primary = ValueError("beat failed")
    browser_close = RuntimeError("browser close failed")
    cleanup = OSError("project cleanup failed")
    collector = CaptureFailureCollector()

    collector.record_primary("capture beat create", primary)
    collector.record_cleanup("close browser", browser_close)
    collector.record_cleanup("project cleanup", cleanup)

    with pytest.raises(CaptureFailed) as caught:
        collector.raise_if_failed()

    assert caught.value.primary is not None
    assert caught.value.primary.error is primary
    assert [detail.error for detail in caught.value.cleanup] == [
        browser_close,
        cleanup,
    ]
    assert caught.value.__cause__ is primary
    assert "cleanup also failed" in str(caught.value)


def test_later_primary_failure_cannot_replace_first_failure() -> None:
    collector = CaptureFailureCollector()
    first = ValueError("first")
    later = RuntimeError("later")

    collector.record_primary("setup", first)
    collector.record_primary("unexpected later operation", later)

    assert collector.primary is not None
    assert collector.primary.error is first
    assert [detail.error for detail in collector.cleanup] == [later]


def test_cleanup_failure_alone_makes_capture_unsuccessful() -> None:
    collector = CaptureFailureCollector()
    cleanup = RuntimeError("cleanup failed")
    collector.record_cleanup("project cleanup", cleanup)

    with pytest.raises(CaptureFailed) as caught:
        collector.raise_if_failed()

    assert caught.value.primary is None
    assert caught.value.cleanup[0].error is cleanup
    assert caught.value.__cause__ is cleanup


def test_empty_failure_collector_does_not_raise() -> None:
    CaptureFailureCollector().raise_if_failed()


class FakeRunner:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls
        self.context: CaptureContext | None = None

    def start(self, context: CaptureContext) -> None:
        self.context = context
        self.calls.append(f"{self.name}:start")

    def capture_beat(self, beat: object) -> BeatCapture:
        beat_id = getattr(beat, "id")
        self.calls.append(f"{self.name}:beat:{beat_id}")
        return BeatCapture(beat_id=beat_id)

    def close(self) -> None:
        self.calls.append(f"{self.name}:close")


def mixed_plan() -> RecordingPlan:
    return normalize_recording_plan(
        {
            "id": "mixed",
            "browser": {},
            "beats": [
                {"id": "terminal-one", "actions": [{"run": "prepare"}]},
                {"id": "browser", "medium": "browser", "actions": []},
                {"id": "terminal-two", "actions": [{"run": "verify"}]},
            ],
        }
    )


def test_coordinator_dispatches_beats_in_source_order_with_shared_context(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    terminal = FakeRunner("terminal", calls)
    browser = FakeRunner("browser", calls)
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: terminal,
        browser_runner_factory=lambda: browser,
    )

    result = coordinator.capture(
        mixed_plan(),
        tmp_path / "run",
        workspace=tmp_path,
        environment={"SHARED": "yes"},
    )

    assert calls == [
        "terminal:start",
        "terminal:beat:terminal-one",
        "browser:start",
        "browser:beat:browser",
        "terminal:beat:terminal-two",
        "browser:close",
        "terminal:close",
    ]
    assert terminal.context is browser.context is result.context
    assert result.context.environment["SHARED"] == "yes"
    assert [beat.beat_id for beat in result.beats] == [
        "terminal-one",
        "browser",
        "terminal-two",
    ]
    assert not result.context.paths.temporary.exists()


def test_capture_action_items_flatten_terminal_commands_and_browser_actions() -> None:
    plan = normalize_recording_plan(
        {
            "id": "actions",
            "browser": {},
            "beats": [
                {
                    "id": "terminal",
                    "heading": "Run commands",
                    "actions": [
                        {
                            "commands": [
                                {"id": "prepare", "run": "prepare", "display": "Prepare"},
                                {"id": "build", "run": "build", "display": "Build"},
                            ]
                        }
                    ],
                },
                {
                    "id": "browser",
                    "medium": "browser",
                    "heading": "Use browser",
                    "actions": [
                        {"id": "open_player", "open_page": {"url": "https://example.com"}},
                        {"id": "wait_done", "wait_for": {"url": {"contains": "example"}}},
                    ],
                },
            ],
        }
    )

    assert [
        (item.beat_id, item.action_id, item.label)
        for item in capture_action_items(plan)
    ] == [
        ("terminal", "prepare", "Prepare"),
        ("terminal", "build", "Build"),
        ("browser", "open_player", "Open player"),
        ("browser", "wait_done", "Wait done"),
    ]


def test_coordinator_reports_started_and_completed_actions(tmp_path: Path) -> None:
    calls: list[str] = []
    progress: list[tuple[str, str, int, int]] = []

    class ProgressRunner(FakeRunner):
        def capture_beat(self, beat, *, on_progress=None) -> BeatCapture:
            action_ids: list[str] = []
            for action_index, action in enumerate(beat.actions):
                browser_id = getattr(action, "id", None)
                if browser_id is not None:
                    action_ids.append(browser_id)
                    continue
                commands = action.config.get("commands")
                if commands:
                    action_ids.extend(
                        command.get("id")
                        or f"__step_{action_index}_command_{command_index}"
                        for command_index, command in enumerate(commands)
                    )
                else:
                    action_ids.append(f"__step_{action_index}")
            for action_id in action_ids:
                if on_progress is not None:
                    on_progress("started", action_id)
                    on_progress("completed", action_id)
            return super().capture_beat(beat)

    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: ProgressRunner("terminal", calls),
        browser_runner_factory=lambda: ProgressRunner("browser", calls),
    )

    coordinator.capture(
        mixed_plan(),
        tmp_path / "run",
        workspace=tmp_path,
        on_progress=lambda state, action, current, total: progress.append(
            (state, action.action_id, current, total)
        ),
    )

    assert progress == [
        ("started", "__step_0", 0, 2),
        ("completed", "__step_0", 1, 2),
        ("started", "__step_0", 1, 2),
        ("completed", "__step_0", 2, 2),
    ]


def test_coordinator_does_not_complete_failed_beat(tmp_path: Path) -> None:
    progress: list[tuple[str, str, int, int]] = []

    class FailingRunner(FakeRunner):
        def capture_beat(self, beat: object) -> BeatCapture:
            raise RuntimeError("capture failed")

    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: FailingRunner("terminal", []),
    )

    with pytest.raises(CaptureFailed, match="capture failed"):
        coordinator.capture(
            normalize_recording_plan(
                {"id": "broken", "beats": [{"id": "broken", "actions": []}]}
            ),
            tmp_path / "run",
            workspace=tmp_path,
            on_progress=lambda state, action, current, total: progress.append(
                (state, action.action_id, current, total)
            ),
        )

    assert progress == []


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([('queued', '__step_0')], "invalid action state 'queued'"),
        ([('started', 'missing')], "unknown action 'broken'/'missing'"),
        ([('completed', '__step_0')], "before starting it"),
        (
            [('started', '__step_0'), ('started', '__step_0')],
            "started action 'broken'/'__step_0' twice",
        ),
        (
            [
                ('started', '__step_0'),
                ('completed', '__step_0'),
                ('completed', '__step_0'),
            ],
            "completed action 'broken'/'__step_0' twice",
        ),
    ],
)
def test_coordinator_rejects_malformed_runner_progress(
    tmp_path: Path,
    events: list[tuple[str, str]],
    message: str,
) -> None:
    class MalformedProgressRunner(FakeRunner):
        def capture_beat(self, beat, *, on_progress=None) -> BeatCapture:
            assert on_progress is not None
            for event in events:
                on_progress(*event)
            return super().capture_beat(beat)

    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: MalformedProgressRunner("terminal", []),
    )

    with pytest.raises(CaptureFailed) as caught:
        coordinator.capture(
            normalize_recording_plan(
                {
                    "id": "broken",
                    "beats": [{"id": "broken", "actions": [{"run": "true"}]}],
                }
            ),
            tmp_path / "run",
            workspace=tmp_path,
            on_progress=lambda *_args: None,
        )

    assert isinstance(caught.value.__cause__, CaptureSetupError)
    assert message in str(caught.value.__cause__)


def test_coordinator_hands_blocking_terminal_watch_to_browser_runner(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class HandoffTerminal(FakeRunner):
        def capture_beat(self, beat: object) -> BeatCapture:
            assert self.context is not None
            environment = dict(self.context.environment)
            environment[BROWSER_HANDOFF_ID_ENV] = "watch_command"
            session = BrokeredBrowserSession.from_environment(
                "http://127.0.0.1:43123/cast-player.html?manifest=demo",
                environment=environment,
            )
            assert session is not None
            self.calls.append("terminal:watch-blocked")
            while session.is_open():
                time.sleep(0.005)
            self.calls.append("terminal:watch-released")
            return BeatCapture(beat_id=getattr(beat, "id"))

        def cancel_capture(self) -> None:
            self.calls.append("terminal:cancel-capture")

    class HandoffBrowser(FakeRunner):
        def set_handoff_url(self, handoff_id: str, url: str) -> None:
            self.calls.append(f"browser:handoff:{handoff_id}:{url}")

        def capture_beat(self, beat: object) -> BeatCapture:
            self.calls.append(f"browser:beat:{getattr(beat, 'id')}")
            return BeatCapture(beat_id=getattr(beat, "id"))

    plan = normalize_recording_plan(
        {
            "id": "handoff",
            "browser": {},
            "beats": [
                {
                    "id": "watch",
                    "actions": [
                        {
                            "commands": [
                                {
                                    "id": "watch_command",
                                    "run": "watch",
                                    "browser_handoff": True,
                                    "follow_along": True,
                                    "show_prompt_after": False,
                                }
                            ]
                        }
                    ],
                },
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {"handoff": "watch_command"},
                        }
                    ],
                },
            ],
        }
    )
    terminal = HandoffTerminal("terminal", calls)
    browser = HandoffBrowser("browser", calls)

    result = CaptureCoordinator(
        terminal_runner_factory=lambda: terminal,
        browser_runner_factory=lambda: browser,
    ).capture(plan, tmp_path / "run", workspace=tmp_path)

    assert [beat.beat_id for beat in result.beats] == ["watch", "browser"]
    assert calls.index("terminal:watch-blocked") < calls.index("browser:beat:browser")
    assert calls.index("browser:beat:browser") < calls.index("terminal:watch-released")
    assert any(
        call.startswith(
            "browser:handoff:watch_command:http://127.0.0.1:43123/"
        )
        for call in calls
    )


def test_coordinator_cancels_blocking_terminal_handoff_when_capture_is_interrupted(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []
    released = threading.Event()

    class BlockingTerminal(FakeRunner):
        def capture_beat(self, beat: object) -> BeatCapture:
            self.calls.append("terminal:watch-blocked")
            if not released.wait(timeout=1):
                raise AssertionError("blocking terminal capture was not cancelled")
            self.calls.append("terminal:watch-cancelled")
            raise RuntimeError("terminal capture cancelled")

        def cancel_capture(self) -> None:
            self.calls.append("terminal:cancel-capture")
            released.set()

    class HandoffBrowser(FakeRunner):
        def set_handoff_url(self, handoff_id: str, url: str) -> None:
            raise AssertionError("handoff URL should not be delivered after interruption")

    plan = normalize_recording_plan(
        {
            "id": "handoff-interrupted",
            "browser": {},
            "beats": [
                {
                    "id": "watch",
                    "actions": [
                        {
                            "commands": [
                                {
                                    "id": "watch_command",
                                    "run": "watch",
                                    "browser_handoff": True,
                                    "follow_along": True,
                                    "show_prompt_after": False,
                                }
                            ]
                        }
                    ],
                },
                {
                    "id": "browser",
                    "medium": "browser",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {"handoff": "watch_command"},
                        }
                    ],
                },
            ],
        }
    )
    terminal = BlockingTerminal("terminal", calls)
    browser = HandoffBrowser("browser", calls)
    monkeypatch.setattr(
        capture_module,
        "_wait_for_browser_handoff",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(CaptureFailed) as caught:
        CaptureCoordinator(
            terminal_runner_factory=lambda: terminal,
            browser_runner_factory=lambda: browser,
        ).capture(plan, tmp_path / "run", workspace=tmp_path)

    assert isinstance(caught.value.primary.error, KeyboardInterrupt)
    assert "terminal:cancel-capture" in calls
    assert "terminal:watch-cancelled" in calls


def test_coordinator_closes_started_runners_after_primary_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FailingBrowser(FakeRunner):
        def capture_beat(self, beat: object) -> BeatCapture:
            beat_id = getattr(beat, "id")
            self.calls.append(f"{self.name}:beat:{beat_id}")
            raise RuntimeError("browser failed")

    terminal = FakeRunner("terminal", calls)
    browser = FailingBrowser("browser", calls)
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: terminal,
        browser_runner_factory=lambda: browser,
    )

    with pytest.raises(CaptureFailed, match="capture beat browser") as caught:
        coordinator.capture(
            mixed_plan(),
            tmp_path / "run",
            workspace=tmp_path,
        )

    assert isinstance(caught.value.primary.error, RuntimeError)  # type: ignore[union-attr]
    assert calls[-2:] == ["browser:close", "terminal:close"]
    assert not (tmp_path / "run" / ".tmp").exists()


class FaultRunner(FakeRunner):
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        failures: set[str],
    ) -> None:
        super().__init__(name, calls)
        self.failures = failures

    def _call(self, operation: str) -> None:
        value = f"{self.name}:{operation}"
        self.calls.append(value)
        if value in self.failures:
            raise RuntimeError(value)

    def start(self, context: CaptureContext) -> None:
        self.context = context
        self._call("start")

    def run_setup(self, _steps: object) -> None:
        self._call("setup")

    def run_cleanup(self, _steps: object) -> None:
        self._call("cleanup")

    def capture_beat(self, beat: object) -> BeatCapture:
        beat_id = getattr(beat, "id")
        self._call(f"beat:{beat_id}")
        if getattr(beat, "checks"):
            self._call(f"checks:{beat_id}")
        return BeatCapture(beat_id=beat_id)

    def close(self) -> None:
        self._call("close")


def lifecycle_plan() -> RecordingPlan:
    return normalize_recording_plan(
        {
            "id": "lifecycle",
            "browser": {},
            "setup": [{"run": "prepare"}],
            "beats": [
                {
                    "id": "terminal-one",
                    "actions": [{"run": "one"}],
                    "checks": [{"run": "check-one"}],
                },
                {"id": "browser", "medium": "browser", "actions": []},
                {"id": "terminal-two", "actions": [{"run": "two"}]},
            ],
            "cleanup": [{"run": "cleanup"}],
        }
    )


@pytest.mark.parametrize(
    ("failure", "required_calls", "forbidden_calls"),
    [
        (
            "terminal:start",
            ["terminal:start", "terminal:close"],
            ["terminal:setup", "terminal:cleanup", "browser:start"],
        ),
        (
            "terminal:setup",
            ["terminal:setup", "terminal:cleanup", "terminal:close"],
            ["terminal:beat:terminal-one", "browser:start"],
        ),
        (
            "terminal:beat:terminal-one",
            ["terminal:cleanup", "terminal:close"],
            ["browser:start"],
        ),
        (
            "terminal:checks:terminal-one",
            ["terminal:cleanup", "terminal:close"],
            ["browser:start"],
        ),
        (
            "browser:start",
            ["browser:start", "browser:close", "terminal:cleanup", "terminal:close"],
            ["browser:beat:browser", "terminal:beat:terminal-two"],
        ),
        (
            "browser:beat:browser",
            ["browser:close", "terminal:cleanup", "terminal:close"],
            ["terminal:beat:terminal-two"],
        ),
        (
            "terminal:beat:terminal-two",
            ["browser:close", "terminal:cleanup", "terminal:close"],
            [],
        ),
        (
            "browser:close",
            ["browser:close", "terminal:cleanup", "terminal:close"],
            [],
        ),
        (
            "terminal:cleanup",
            ["browser:close", "terminal:cleanup", "terminal:close"],
            [],
        ),
        (
            "terminal:close",
            ["browser:close", "terminal:cleanup", "terminal:close"],
            [],
        ),
    ],
)
def test_coordinator_fault_injection_always_closes_and_cleans_once(
    tmp_path: Path,
    failure: str,
    required_calls: list[str],
    forbidden_calls: list[str],
) -> None:
    calls: list[str] = []
    failures = {failure}
    terminal = FaultRunner("terminal", calls, failures=failures)
    browser = FaultRunner("browser", calls, failures=failures)
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: terminal,
        browser_runner_factory=lambda: browser,
    )

    with pytest.raises(CaptureFailed):
        coordinator.capture(lifecycle_plan(), tmp_path / failure, workspace=tmp_path)

    for call in required_calls:
        assert call in calls
    for call in forbidden_calls:
        assert call not in calls
    assert calls.count("terminal:cleanup") <= 1
    assert calls.count("browser:close") <= 1
    assert calls.count("terminal:close") == 1


def test_primary_failure_and_cleanup_failure_are_both_retained(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    failures = {"browser:beat:browser", "terminal:cleanup"}
    terminal = FaultRunner("terminal", calls, failures=failures)
    browser = FaultRunner("browser", calls, failures=failures)
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: terminal,
        browser_runner_factory=lambda: browser,
    )

    with pytest.raises(CaptureFailed) as caught:
        coordinator.capture(lifecycle_plan(), tmp_path / "run", workspace=tmp_path)

    assert caught.value.primary is not None
    assert "browser:beat:browser" in str(caught.value.primary.error)
    assert any("terminal:cleanup" in str(item.error) for item in caught.value.cleanup)
    assert calls[-3:] == ["browser:close", "terminal:cleanup", "terminal:close"]
