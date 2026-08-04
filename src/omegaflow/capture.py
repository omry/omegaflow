"""Media-neutral capture lifecycle primitives.

The coordinator built on this module owns a private, recording-scoped run
directory. Terminal and browser runners share the recording workspace and
authored environment while receiving private artifact, diagnostic, and
temporary namespaces.
"""

from __future__ import annotations

import shutil
import stat
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from types import MappingProxyType
from typing import Any, Callable, Protocol, runtime_checkable

from .browser_handoff import BROWSER_HANDOFF_ROOT_ENV, BrowserHandoffBroker
from .recording_plan import (
    OuterBeatPlan,
    BrowserActionPlan,
    CapturedPaneBeatPlan,
    JoinPlan,
    RecordingPlan,
    StreamKind,
    TerminalActionPlan,
    capture_runner_beat,
    captured_pane_beats,
    pane_action_id,
    terminal_action_id,
)
from .studio_config import RecordingMedium


PRIVATE_DIRECTORY_MODE = 0o700


class CaptureSetupError(RuntimeError):
    """Raised when the private staged run directory cannot be prepared."""


class CapturePaneStreamError(CaptureSetupError):
    """Raised when one explicit pane stream fails during concurrent capture."""

    def __init__(
        self,
        pane_id: str,
        medium: RecordingMedium | None,
        error: BaseException,
    ) -> None:
        self.pane_id = pane_id
        self.medium = medium
        self.error = error
        detail = str(error).strip()
        message = f"capture pane stream {pane_id!r} failed"
        if detail:
            message += f": {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class CapturePaths:
    """Private paths allocated for one capture run."""

    run: Path
    capture: Path
    diagnostics: Path
    temporary: Path

    def private_path(self, *parts: str) -> Path:
        """Return a path below the run directory without allowing traversal."""

        if not parts:
            return self.run
        relative = Path(*parts)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise CaptureSetupError("private artifact path must stay below the run directory")
        candidate = self.run.joinpath(relative)
        try:
            candidate.relative_to(self.run)
        except ValueError as exc:
            raise CaptureSetupError(
                "private artifact path must stay below the run directory"
            ) from exc
        return candidate


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise CaptureSetupError(f"private capture directory must not be a symlink: {path}")
    try:
        path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise CaptureSetupError(f"could not create private capture directory: {path}") from exc
    if not path.is_dir():
        raise CaptureSetupError(f"private capture path is not a directory: {path}")
    try:
        path.chmod(PRIVATE_DIRECTORY_MODE)
    except OSError as exc:
        raise CaptureSetupError(f"could not secure private capture directory: {path}") from exc
    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077:
        raise CaptureSetupError(
            f"private capture directory has group or public permissions: {path}"
        )


def prepare_capture_paths(run_dir: Path) -> CapturePaths:
    """Create the private directories used while a run is being assembled."""

    run = run_dir.expanduser().absolute()
    paths = CapturePaths(
        run=run,
        capture=run / "capture",
        diagnostics=run / "diagnostics",
        temporary=run / ".tmp",
    )
    for path in (paths.run, paths.capture, paths.diagnostics, paths.temporary):
        _prepare_private_directory(path)
    return paths


@dataclass(frozen=True)
class CaptureContext:
    """Immutable recording state with an optional private runner scope."""

    paths: CapturePaths
    workspace: Path
    working_directory: Path
    environment: Mapping[str, str]
    runner_id: str | None = None

    @property
    def runner_capture(self) -> Path:
        if self.runner_id is None:
            return self.paths.capture
        return self.paths.capture / "runners" / self.runner_id

    @property
    def runner_diagnostics(self) -> Path:
        if self.runner_id is None:
            return self.paths.diagnostics
        return self.paths.diagnostics / "runners" / self.runner_id

    @property
    def runner_temporary(self) -> Path:
        if self.runner_id is None:
            return self.paths.temporary
        return self.paths.temporary / "runners" / self.runner_id

    @classmethod
    def create(
        cls,
        run_dir: Path,
        *,
        workspace: Path,
        working_directory: Path | None = None,
        environment: Mapping[str, str | None] | None = None,
    ) -> CaptureContext:
        paths = prepare_capture_paths(run_dir)
        resolved_workspace = workspace.expanduser().resolve()
        if not resolved_workspace.is_dir():
            raise CaptureSetupError(
                f"capture workspace is not a directory: {resolved_workspace}"
            )
        resolved_working_directory = (
            working_directory.expanduser().resolve()
            if working_directory is not None
            else resolved_workspace
        )
        if not resolved_working_directory.is_dir():
            raise CaptureSetupError(
                "capture working directory is not a directory: "
                f"{resolved_working_directory}"
            )
        resolved_environment: dict[str, str] = {}
        if environment is not None:
            for key, value in environment.items():
                if value is None:
                    resolved_environment.pop(key, None)
                else:
                    resolved_environment[key] = value
        private_home = paths.temporary / "home"
        _prepare_private_directory(private_home)
        resolved_environment.update(
            {
                "HOME": str(private_home),
                "OMEGAFLOW_RUN_DIR": str(paths.run),
                "TMPDIR": str(paths.temporary),
                BROWSER_HANDOFF_ROOT_ENV: str(paths.temporary / "browser-handoffs"),
            }
        )
        return cls(
            paths=paths,
            workspace=resolved_workspace,
            working_directory=resolved_working_directory,
            environment=MappingProxyType(resolved_environment),
        )

    def for_runner(self, runner_id: str) -> CaptureContext:
        """Return a pane-runner scope while retaining shared recording state."""

        relative = Path(runner_id)
        if (
            not runner_id
            or relative.is_absolute()
            or len(relative.parts) != 1
            or relative.parts[0] in {"", ".", ".."}
        ):
            raise CaptureSetupError(f"invalid capture runner id: {runner_id!r}")
        runner_environment = dict(self.environment)
        runner_temporary = self.paths.temporary / "runners" / runner_id
        runner_environment.update(
            {
                "HOME": str(runner_temporary / "home"),
                "TMPDIR": str(runner_temporary),
            }
        )
        scoped = CaptureContext(
            paths=self.paths,
            workspace=self.workspace,
            working_directory=self.working_directory,
            environment=MappingProxyType(runner_environment),
            runner_id=runner_id,
        )
        for path in (
            scoped.runner_capture,
            scoped.runner_diagnostics,
            scoped.runner_temporary,
            Path(scoped.environment["HOME"]),
        ):
            _prepare_private_directory(path)
        return scoped


@dataclass(frozen=True)
class BeatCapture:
    """Media-neutral result returned after one beat has been captured."""

    beat_id: str
    artifacts: tuple[Path, ...] = ()
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class CaptureRunner(Protocol):
    """Persistent, source-ordered capture runner contract."""

    def start(self, context: CaptureContext) -> None: ...

    def capture_beat(
        self,
        beat: OuterBeatPlan,
        *,
        on_progress: RunnerProgressCallback | None = None,
        before_action: RunnerActionGate | None = None,
    ) -> BeatCapture: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class CaptureFailureDetail:
    """One primary or cleanup failure with its lifecycle operation."""

    operation: str
    error: BaseException

    def describe(self) -> str:
        return f"{self.operation}: {type(self.error).__name__}: {self.error}"


class CaptureFailed(RuntimeError):
    """Aggregate capture failure that keeps cleanup errors secondary."""

    def __init__(
        self,
        *,
        primary: CaptureFailureDetail | None,
        cleanup: tuple[CaptureFailureDetail, ...],
    ) -> None:
        if primary is None and not cleanup:
            raise ValueError("CaptureFailed requires at least one failure")
        self.primary = primary
        self.cleanup = cleanup
        parts = []
        if primary is not None:
            parts.append(f"capture failed during {primary.describe()}")
        if cleanup:
            details = "; ".join(item.describe() for item in cleanup)
            prefix = "cleanup also failed" if primary is not None else "cleanup failed"
            parts.append(f"{prefix}: {details}")
        super().__init__("; ".join(parts))


class CaptureFailureCollector:
    """Collect one primary failure plus every teardown or cleanup failure."""

    def __init__(self) -> None:
        self._primary: CaptureFailureDetail | None = None
        self._cleanup: list[CaptureFailureDetail] = []

    @property
    def primary(self) -> CaptureFailureDetail | None:
        return self._primary

    @property
    def cleanup(self) -> tuple[CaptureFailureDetail, ...]:
        return tuple(self._cleanup)

    @property
    def failed(self) -> bool:
        return self._primary is not None or bool(self._cleanup)

    def record_primary(self, operation: str, error: BaseException) -> None:
        detail = _failure_detail(operation, error)
        if self._primary is None:
            self._primary = detail
        else:
            self._cleanup.append(detail)

    def record_cleanup(self, operation: str, error: BaseException) -> None:
        self._cleanup.append(_failure_detail(operation, error))

    def raise_if_failed(self) -> None:
        if not self.failed:
            return
        failure = CaptureFailed(primary=self._primary, cleanup=self.cleanup)
        if self._primary is not None:
            raise failure from self._primary.error
        raise failure from self._cleanup[0].error


def _failure_detail(operation: str, error: BaseException) -> CaptureFailureDetail:
    if not operation.strip():
        raise ValueError("capture failure operation must be non-empty")
    if not isinstance(error, BaseException):
        raise TypeError("capture failure must be an exception")
    return CaptureFailureDetail(operation=operation, error=error)


RunnerProgressCallback = Callable[[str, str], None]
RunnerActionGate = Callable[[str], None]
CaptureRunnerFactory = Callable[[], CaptureRunner]
PaneCaptureRunnerFactory = Callable[[str], CaptureRunner]


@dataclass(frozen=True)
class CaptureActionItem:
    """One executable recording action shown by build progress."""

    beat_id: str
    beat_heading: str
    action_id: str
    label: str
    pane_id: str | None = None
    pane_beat_id: str | None = None


CaptureProgressCallback = Callable[[str, CaptureActionItem, int, int], None]


def _action_label(value: Mapping[str, Any], fallback: str) -> str:
    display = value.get("display")
    if isinstance(display, str) and display:
        return display
    return fallback.replace("_", " ").replace("-", " ").capitalize()


def capture_action_items(plan: RecordingPlan) -> tuple[CaptureActionItem, ...]:
    """Flatten user-facing terminal commands and browser actions in source order."""

    items: list[CaptureActionItem] = []
    for captured in captured_pane_beats(plan):
        beat = capture_runner_beat(plan, captured)
        for action_index, action in enumerate(beat.actions):
            if isinstance(action, BrowserActionPlan):
                items.append(
                    CaptureActionItem(
                        beat_id=beat.id,
                        beat_heading=beat.heading,
                        action_id=action.id,
                        label=_action_label(action.config, action.id),
                        pane_id=captured.pane_id if plan.panes else None,
                        pane_beat_id=(
                            captured.beat.id if plan.panes else None
                        ),
                    )
                )
                continue
            if not isinstance(action, TerminalActionPlan):
                continue
            commands = action.config.get("commands")
            entries = enumerate(commands) if commands else ((None, action.config),)
            for command_index, command in entries:
                action_id = terminal_action_id(
                    action_index,
                    command_index,
                    command,
                )
                items.append(
                    CaptureActionItem(
                        beat_id=beat.id,
                        beat_heading=beat.heading,
                        action_id=action_id,
                        label=_action_label(command, action_id),
                        pane_id=captured.pane_id if plan.panes else None,
                        pane_beat_id=(
                            captured.beat.id if plan.panes else None
                        ),
                    )
                )
    return tuple(items)


@dataclass(frozen=True)
class CaptureResult:
    """Successful source-ordered capture results for one recording."""

    context: CaptureContext
    beats: tuple[BeatCapture, ...]


class CaptureCoordinator:
    """Own the shared environment and persistent media runners for one run."""

    def __init__(
        self,
        *,
        terminal_runner_factory: CaptureRunnerFactory | None = None,
        terminal_pane_runner_factory: PaneCaptureRunnerFactory | None = None,
        browser_runner_factory: CaptureRunnerFactory | None = None,
    ) -> None:
        self._runner_factories = {
            RecordingMedium.terminal: terminal_runner_factory,
            RecordingMedium.browser: browser_runner_factory,
        }
        self._pane_runner_factories = {
            RecordingMedium.terminal: terminal_pane_runner_factory,
            RecordingMedium.browser: None,
        }

    def capture(
        self,
        plan: RecordingPlan,
        run_dir: Path,
        *,
        workspace: Path,
        working_directory: Path | None = None,
        environment: Mapping[str, str | None] | None = None,
        on_progress: CaptureProgressCallback | None = None,
    ) -> CaptureResult:
        context = CaptureContext.create(
            run_dir,
            workspace=workspace,
            working_directory=working_directory,
            environment=environment,
        )
        runners: dict[RecordingMedium, CaptureRunner] = {}
        start_attempted: list[RecordingMedium] = []
        started: set[RecordingMedium] = set()
        captures: list[BeatCapture] = []
        failures = CaptureFailureCollector()
        operation = "initialize capture"
        action_items = capture_action_items(plan)
        action_by_key = {
            (item.beat_id, item.action_id): item for item in action_items
        }
        completed_actions = 0
        total_actions = len(action_items)
        progress_lock = Lock()
        started_action_keys: set[tuple[str, str]] = set()
        completed_action_keys: set[tuple[str, str]] = set()

        def runner_progress(beat: OuterBeatPlan) -> RunnerProgressCallback | None:
            if on_progress is None:
                return None

            def report(state: str, action_id: str) -> None:
                nonlocal completed_actions
                key = (beat.id, action_id)
                item = action_by_key.get(key)
                if item is None:
                    raise CaptureSetupError(
                        f"runner reported unknown action {beat.id!r}/{action_id!r}"
                    )
                with progress_lock:
                    if state not in {"started", "completed"}:
                        raise CaptureSetupError(
                            f"runner reported invalid action state {state!r}"
                        )
                    if state == "started":
                        if key in started_action_keys:
                            raise CaptureSetupError(
                                f"runner started action {beat.id!r}/{action_id!r} twice"
                            )
                        started_action_keys.add(key)
                    if state == "completed":
                        if key not in started_action_keys:
                            raise CaptureSetupError(
                                "runner completed action "
                                f"{beat.id!r}/{action_id!r} before starting it"
                            )
                        if key in completed_action_keys:
                            raise CaptureSetupError(
                                f"runner completed action {beat.id!r}/{action_id!r} twice"
                            )
                        completed_action_keys.add(key)
                        completed_actions += 1
                    on_progress(state, item, completed_actions, total_actions)

            return report

        def ensure_runner(medium: RecordingMedium) -> CaptureRunner:
            nonlocal operation
            runner = runners.get(medium)
            if runner is not None:
                return runner
            operation = f"start {medium.value} runner"
            factory = self._runner_factories[medium]
            if factory is None:
                raise CaptureSetupError(
                    f"no {medium.value} capture runner is configured"
                )
            runner = factory()
            runners[medium] = runner
            start_attempted.append(medium)
            runner.start(context)
            started.add(medium)
            return runner

        def capture_beat(
            runner: CaptureRunner,
            beat: OuterBeatPlan,
            *,
            progress_callback: RunnerProgressCallback | None = None,
            before_action: RunnerActionGate | None = None,
        ) -> BeatCapture:
            if progress_callback is None:
                progress_callback = runner_progress(beat)
            kwargs: dict[str, Any] = {}
            if progress_callback is not None:
                kwargs["on_progress"] = progress_callback
            if before_action is not None:
                kwargs["before_action"] = before_action
            return runner.capture_beat(beat, **kwargs)

        def capture_explicit_panes() -> list[BeatCapture]:
            captured_beats = captured_pane_beats(plan)
            handoff_broker = BrowserHandoffBroker(
                Path(context.environment[BROWSER_HANDOFF_ROOT_ENV])
            )
            handoff_by_consumer = {
                (
                    handoff.consumer_outer_beat_id,
                    handoff.target_pane_id,
                    handoff.consumer_pane_beat_id,
                    handoff.consumer_action_id,
                ): handoff
                for handoff in plan.browser_handoffs
            }
            handoff_by_id = {
                handoff.id: handoff for handoff in plan.browser_handoffs
            }
            output_producers_by_consumer: dict[str, tuple[str, ...]] = {}
            for dependency in plan.output_dependencies:
                existing = output_producers_by_consumer.get(
                    dependency.consumer_event_id,
                    (),
                )
                output_producers_by_consumer[dependency.consumer_event_id] = (
                    *existing,
                    dependency.producer_event_id,
                )
            for handoff in plan.browser_handoffs:
                handoff_broker.prepare(handoff.id)
            streams: dict[str, list[CapturedPaneBeatPlan]] = {}
            medium_by_pane: dict[str, RecordingMedium] = {}
            for captured in captured_beats:
                streams.setdefault(captured.pane_id, []).append(captured)
                medium = RecordingMedium(captured.kind.value)
                medium_by_pane[captured.pane_id] = medium
            lifecycle_pane = next(
                (
                    pane_id
                    for pane_id, medium in medium_by_pane.items()
                    if medium is RecordingMedium.terminal
                ),
                None,
            )
            if (plan.setup or plan.cleanup) and lifecycle_pane is None:
                lifecycle_pane = "__recording_terminal__"
                streams[lifecycle_pane] = []
                medium_by_pane[lifecycle_pane] = RecordingMedium.terminal

            capture_events: dict[str, Event] = {}
            for captured in captured_beats:
                for action_index, action in enumerate(captured.beat.actions):
                    action_id = pane_action_id(action, action_index)
                    prefix = (
                        f"{captured.pane_id}.{captured.beat.id}.{action_id}"
                    )
                    capture_events[f"{prefix}.started"] = Event()
                    capture_events[f"{prefix}.ended"] = Event()

            failed = Event()
            allow_teardown = Event()
            setup_done = Event()
            if not plan.setup:
                setup_done.set()
            stream_capture_done = {
                pane_id: Event() for pane_id in streams
            }
            results: dict[str, BeatCapture] = {}
            results_lock = Lock()
            runners_by_pane: dict[str, CaptureRunner] = {}
            runners_lock = Lock()
            first_failure: list[tuple[str, BaseException]] = []
            failure_lock = Lock()

            def record_stream_failure(
                pane_id: str,
                error: BaseException,
            ) -> None:
                with failure_lock:
                    if not first_failure:
                        first_failure.append((pane_id, error))

            def close_handoff(handoff_id: str) -> None:
                try:
                    handoff_broker.close(handoff_id)
                except BaseException as exc:
                    record_stream_failure(
                        f"browser handoff {handoff_id}",
                        exc,
                    )
                    failed.set()

            def wait_for_join(join: JoinPlan | None) -> None:
                if (
                    join is None
                    or join.event.stream.kind is not StreamKind.pane
                ):
                    return
                event_id = join.event.qualified_id
                event = capture_events.get(event_id)
                if event is None:
                    raise CaptureSetupError(
                        f"capture join references unavailable event {event_id!r}"
                    )
                while not event.wait(0.05):
                    if failed.is_set():
                        raise CaptureSetupError(
                            f"capture join {event_id!r} was cancelled"
                        )

            def receive_handoff(
                handoff_id: str,
                runner: CaptureRunner,
            ) -> None:
                handoff = handoff_by_id[handoff_id]
                producer_ended = capture_events[
                    f"{handoff.producer_pane_id}."
                    f"{handoff.producer_pane_beat_id}.{handoff.id}.ended"
                ]
                while True:
                    url = handoff_broker.ready_url(handoff_id)
                    if url is not None:
                        break
                    if failed.is_set():
                        raise CaptureSetupError(
                            f"browser handoff {handoff_id!r} was cancelled"
                        )
                    if producer_ended.wait(0.05):
                        raise CaptureSetupError(
                            f"terminal action {handoff_id!r} exited without "
                            "opening a browser"
                        )
                set_handoff_url = getattr(runner, "set_handoff_url", None)
                if not callable(set_handoff_url):
                    raise CaptureSetupError(
                        f"target pane {handoff.target_pane_id!r} does not "
                        "support browser handoff"
                    )
                set_handoff_url(handoff_id, url)

            def capture_stream(
                pane_id: str,
                stream: list[CapturedPaneBeatPlan],
            ) -> None:
                medium = medium_by_pane[pane_id]
                factory = self._runner_factories[medium]
                if factory is None:
                    error = CaptureSetupError(
                        f"no {medium.value} capture runner is configured"
                    )
                    record_stream_failure(pane_id, error)
                    failed.set()
                    stream_capture_done[pane_id].set()
                    raise error
                runner: CaptureRunner | None = None
                primary: BaseException | None = None
                try:
                    pane_factory = self._pane_runner_factories[medium]
                    runner = pane_factory(pane_id) if pane_factory else factory()
                    with runners_lock:
                        runners_by_pane[pane_id] = runner
                    runner.start(context.for_runner(pane_id))
                    if pane_id == lifecycle_pane and plan.setup:
                        run_setup = getattr(runner, "run_setup", None)
                        if not callable(run_setup):
                            raise CaptureSetupError(
                                "terminal capture runner does not support "
                                "project setup"
                            )
                        run_setup(plan.setup)
                        setup_done.set()
                    elif plan.setup:
                        while not setup_done.wait(0.05):
                            if failed.is_set():
                                raise CaptureSetupError(
                                    "project setup failed before pane capture"
                                )
                    for captured in stream:
                        beat = capture_runner_beat(plan, captured)
                        wait_for_join(captured.beat.start_join)
                        authored_actions = {
                            pane_action_id(action, action_index): action
                            for action_index, action in enumerate(
                                captured.beat.actions
                            )
                        }
                        user_progress = runner_progress(beat)

                        def before_action(action_id: str) -> None:
                            try:
                                action = authored_actions[action_id]
                            except KeyError as exc:
                                raise CaptureSetupError(
                                    f"runner requested unknown action gate "
                                    f"{beat.id!r}/{action_id!r}"
                                ) from exc
                            wait_for_join(getattr(action, "start_join", None))
                            consumer_event_id = (
                                f"{captured.pane_id}.{captured.beat.id}.{action_id}"
                            )
                            for producer_event_id in output_producers_by_consumer.get(
                                consumer_event_id,
                                (),
                            ):
                                producer_ended = capture_events.get(
                                    f"{producer_event_id}.ended"
                                )
                                if producer_ended is None:
                                    raise CaptureSetupError(
                                        "output dependency references unavailable event "
                                        f"{producer_event_id!r}"
                                    )
                                while not producer_ended.wait(0.05):
                                    if failed.is_set():
                                        raise CaptureSetupError(
                                            "output dependency on "
                                            f"{producer_event_id!r} was cancelled"
                                        )
                            handoff = handoff_by_consumer.get(
                                (
                                    captured.outer_beat_id,
                                    captured.pane_id,
                                    captured.beat.id,
                                    action_id,
                                )
                            )
                            if handoff is not None:
                                receive_handoff(handoff.id, runner)

                        def report(state: str, action_id: str) -> None:
                            if state not in {"started", "completed"}:
                                raise CaptureSetupError(
                                    f"runner reported invalid action state {state!r}"
                                )
                            endpoint = (
                                "started" if state == "started" else "ended"
                            )
                            event_id = (
                                f"{captured.pane_id}.{captured.beat.id}."
                                f"{action_id}.{endpoint}"
                            )
                            event = capture_events.get(event_id)
                            if event is None:
                                raise CaptureSetupError(
                                    f"runner reported unknown capture event "
                                    f"{event_id!r}"
                            )
                            event.set()
                            if user_progress is not None:
                                user_progress(state, action_id)

                        capture = capture_beat(
                            runner,
                            beat,
                            progress_callback=report,
                            before_action=before_action,
                        )
                        _validate_beat_capture(capture, beat)
                        for handoff in plan.browser_handoffs:
                            if (
                                handoff.consumer_outer_beat_id,
                                handoff.target_pane_id,
                                handoff.consumer_pane_beat_id,
                            ) == (
                                captured.outer_beat_id,
                                captured.pane_id,
                                captured.beat.id,
                            ):
                                close_handoff(handoff.id)
                        for action_id in authored_actions:
                            prefix = (
                                f"{captured.pane_id}.{captured.beat.id}."
                                f"{action_id}"
                            )
                            if not all(
                                capture_events[f"{prefix}.{endpoint}"].is_set()
                                for endpoint in ("started", "ended")
                            ):
                                raise CaptureSetupError(
                                    f"runner did not report complete capture events "
                                    f"for {prefix!r}"
                                )
                        with results_lock:
                            results[captured.capture_id] = capture
                except BaseException as exc:
                    primary = exc
                    record_stream_failure(pane_id, exc)
                    failed.set()
                finally:
                    stream_capture_done[pane_id].set()
                    allow_teardown.wait()
                    if (
                        runner is not None
                        and pane_id == lifecycle_pane
                        and plan.cleanup
                    ):
                        try:
                            run_cleanup = getattr(runner, "run_cleanup", None)
                            if not callable(run_cleanup):
                                raise CaptureSetupError(
                                    "terminal capture runner does not support "
                                    "project cleanup"
                                )
                            run_cleanup(plan.cleanup)
                        except BaseException as exc:
                            if primary is None:
                                primary = exc
                    if runner is not None:
                        try:
                            runner.close()
                        except BaseException as exc:
                            if primary is None:
                                primary = exc
                        complete = getattr(runner, "complete", None)
                        if callable(complete):
                            try:
                                complete()
                            except BaseException as exc:
                                if primary is None:
                                    primary = exc
                if primary is not None:
                    record_stream_failure(pane_id, primary)
                    raise primary

            executor = ThreadPoolExecutor(max_workers=max(1, len(streams)))
            futures = {
                pane_id: executor.submit(capture_stream, pane_id, stream)
                for pane_id, stream in streams.items()
            }
            future_errors: list[BaseException] = []
            try:
                while not all(
                    event.wait(0.05)
                    for event in stream_capture_done.values()
                ):
                    if failed.is_set():
                        break
                if failed.is_set():
                    for handoff in plan.browser_handoffs:
                        close_handoff(handoff.id)
                    with runners_lock:
                        active_runners = tuple(runners_by_pane.values())
                    for runner in active_runners:
                        cancel = getattr(runner, "cancel_capture", None)
                        if callable(cancel):
                            cancel()
                allow_teardown.set()
                for pane_id, future in futures.items():
                    try:
                        future.result()
                    except BaseException as exc:
                        failed.set()
                        future_errors.append(exc)
            finally:
                for handoff in plan.browser_handoffs:
                    close_handoff(handoff.id)
                allow_teardown.set()
                executor.shutdown(wait=True, cancel_futures=True)
            if first_failure:
                failed_pane, error = first_failure[0]
                raise CapturePaneStreamError(
                    failed_pane,
                    medium_by_pane.get(failed_pane),
                    error,
                ) from error
            if future_errors:
                raise CaptureSetupError(
                    "capture pane stream failed"
                ) from future_errors[0]
            return [
                results[captured.capture_id] for captured in captured_beats
            ]

        try:
            if (plan.setup or plan.cleanup) and not plan.panes:
                terminal_runner = ensure_runner(RecordingMedium.terminal)
                if plan.setup:
                    operation = "project setup"
                    run_setup = getattr(terminal_runner, "run_setup", None)
                    if not callable(run_setup):
                        raise CaptureSetupError(
                            "terminal capture runner does not support project setup"
                        )
                    run_setup(plan.setup)
            if plan.panes:
                operation = "capture concurrent pane streams"
                captures.extend(capture_explicit_panes())
                beat_index = len(plan.beats)
            else:
                beat_index = 0
            while beat_index < len(plan.beats):
                beat = plan.beats[beat_index]
                runner = ensure_runner(beat.medium)
                operation = f"capture beat {beat.id}"
                handoff_id = _terminal_browser_handoff_id(beat)
                if handoff_id is not None:
                    browser_beat = plan.beats[beat_index + 1]
                    browser_runner = ensure_runner(RecordingMedium.browser)
                    cancel_terminal_capture = getattr(runner, "cancel_capture", None)
                    if not callable(cancel_terminal_capture):
                        raise CaptureSetupError(
                            "terminal capture runner does not support cancelling "
                            "a browser handoff"
                        )
                    broker = BrowserHandoffBroker(
                        Path(context.environment[BROWSER_HANDOFF_ROOT_ENV])
                    )
                    broker.prepare(handoff_id)
                    executor = ThreadPoolExecutor(max_workers=1)
                    try:
                        terminal_future = executor.submit(capture_beat, runner, beat)
                        try:
                            try:
                                url = _wait_for_browser_handoff(
                                    broker,
                                    handoff_id,
                                    terminal_future,
                                )
                                set_handoff_url = getattr(
                                    browser_runner, "set_handoff_url", None
                                )
                                if not callable(set_handoff_url):
                                    raise CaptureSetupError(
                                        "browser capture runner does not support browser "
                                        "handoff"
                                    )
                                set_handoff_url(handoff_id, url)
                                operation = f"capture beat {browser_beat.id}"
                                browser_capture = capture_beat(
                                    browser_runner, browser_beat
                                )
                            finally:
                                broker.close(handoff_id)
                            terminal_capture = terminal_future.result()
                        except BaseException:
                            if not terminal_future.done():
                                cancel_terminal_capture()
                            raise
                    finally:
                        executor.shutdown(wait=True, cancel_futures=True)
                    _validate_beat_capture(terminal_capture, beat)
                    _validate_beat_capture(browser_capture, browser_beat)
                    captures.extend((terminal_capture, browser_capture))
                    beat_index += 2
                    continue
                capture = capture_beat(runner, beat)
                if capture.beat_id != beat.id:
                    raise RuntimeError(
                        f"{beat.medium.value} runner returned beat {capture.beat_id!r} "
                        f"while capturing {beat.id!r}"
                    )
                captures.append(capture)
                beat_index += 1
        except BaseException as exc:
            failures.record_primary(operation, exc)
        finally:
            if RecordingMedium.browser in start_attempted:
                try:
                    runners[RecordingMedium.browser].close()
                except BaseException as exc:
                    failures.record_cleanup("close browser runner", exc)
            if RecordingMedium.terminal in started and plan.cleanup:
                terminal_runner = runners[RecordingMedium.terminal]
                try:
                    run_cleanup = getattr(terminal_runner, "run_cleanup", None)
                    if not callable(run_cleanup):
                        raise CaptureSetupError(
                            "terminal capture runner does not support project cleanup"
                        )
                    run_cleanup(plan.cleanup)
                except BaseException as exc:
                    failures.record_cleanup("project cleanup", exc)
            if RecordingMedium.terminal in start_attempted:
                try:
                    runners[RecordingMedium.terminal].close()
                except BaseException as exc:
                    failures.record_cleanup("close terminal runner", exc)
            try:
                shutil.rmtree(context.paths.temporary)
            except FileNotFoundError:
                pass
            except BaseException as exc:
                failures.record_cleanup("tear down recording environment", exc)
            if not failures.failed and RecordingMedium.browser in started:
                complete = getattr(runners[RecordingMedium.browser], "complete", None)
                if callable(complete):
                    try:
                        complete()
                    except BaseException as exc:
                        failures.record_cleanup("finalize browser capture log", exc)

        failures.raise_if_failed()
        return CaptureResult(context=context, beats=tuple(captures))


def _validate_beat_capture(capture: BeatCapture, beat: OuterBeatPlan) -> None:
    if capture.beat_id != beat.id:
        raise RuntimeError(
            f"{beat.medium.value} runner returned beat {capture.beat_id!r} "
            f"while capturing {beat.id!r}"
        )


def _terminal_browser_handoff_id(beat: OuterBeatPlan) -> str | None:
    if beat.medium is not RecordingMedium.terminal:
        return None
    for action in beat.actions:
        if not isinstance(action, TerminalActionPlan):
            continue
        for command in action.config.get("commands") or ():
            if command.get("browser_handoff"):
                command_id = command.get("id")
                return command_id if isinstance(command_id, str) else None
    return None


def _wait_for_browser_handoff(
    broker: BrowserHandoffBroker,
    handoff_id: str,
    terminal_future: Future[BeatCapture],
) -> str:
    while True:
        url = broker.ready_url(handoff_id)
        if url is not None:
            return url
        if terminal_future.done():
            terminal_future.result()
            raise RuntimeError(
                f"terminal command {handoff_id!r} exited without opening a browser"
            )
        time.sleep(0.01)
