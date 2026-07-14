"""Media-neutral capture lifecycle primitives.

The coordinator built on this module owns a private, recording-scoped run
directory.  Terminal and browser runners receive the same immutable context so
they observe the same working directory, environment, and temporary storage.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Protocol, runtime_checkable

from .recording_plan import BeatPlan
from .recording_plan import RecordingPlan
from .studio_config import RecordingMedium


PRIVATE_DIRECTORY_MODE = 0o700


class CaptureSetupError(RuntimeError):
    """Raised when the private staged run directory cannot be prepared."""


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
    """Immutable recording-scoped state shared by every capture runner."""

    paths: CapturePaths
    workspace: Path
    working_directory: Path
    environment: Mapping[str, str]

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
        resolved_environment = dict(os.environ)
        if environment is not None:
            for key, value in environment.items():
                if value is None:
                    resolved_environment.pop(key, None)
                else:
                    resolved_environment[key] = value
        resolved_environment.update(
            {
                "OMEGAFLOW_RUN_DIR": str(paths.run),
                "OMEGAFLOW_WORKSPACE": str(resolved_workspace),
                "OMEGAFLOW_WORKDIR": str(resolved_working_directory),
                "TMPDIR": str(paths.temporary),
            }
        )
        return cls(
            paths=paths,
            workspace=resolved_workspace,
            working_directory=resolved_working_directory,
            environment=MappingProxyType(resolved_environment),
        )


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

    def capture_beat(self, beat: BeatPlan) -> BeatCapture: ...

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


CaptureRunnerFactory = Callable[[], CaptureRunner]


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
        browser_runner_factory: CaptureRunnerFactory | None = None,
    ) -> None:
        self._runner_factories = {
            RecordingMedium.terminal: terminal_runner_factory,
            RecordingMedium.browser: browser_runner_factory,
        }

    def capture(
        self,
        plan: RecordingPlan,
        run_dir: Path,
        *,
        workspace: Path,
        working_directory: Path | None = None,
        environment: Mapping[str, str | None] | None = None,
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

        try:
            if plan.setup or plan.cleanup:
                terminal_runner = ensure_runner(RecordingMedium.terminal)
                if plan.setup:
                    operation = "project setup"
                    run_setup = getattr(terminal_runner, "run_setup", None)
                    if not callable(run_setup):
                        raise CaptureSetupError(
                            "terminal capture runner does not support project setup"
                        )
                    run_setup(plan.setup)
            for beat in plan.beats:
                runner = ensure_runner(beat.medium)
                operation = f"capture beat {beat.id}"
                capture = runner.capture_beat(beat)
                if capture.beat_id != beat.id:
                    raise RuntimeError(
                        f"{beat.medium.value} runner returned beat {capture.beat_id!r} "
                        f"while capturing {beat.id!r}"
                    )
                captures.append(capture)
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
