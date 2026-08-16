"""Persistent terminal runner backed by one trusted Envoy session."""

from __future__ import annotations

import json
import re
import shlex
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable

from .capture import BeatCapture, CaptureContext
from .envoy_session import EnvoyOperationResult, EnvoySessionError, EnvoyTerminalSession
from .record import RecordingError, command_output_config
from .recording_plan import (
    OuterBeatPlan,
    TerminalActionPlan,
    TerminalCheckPlan,
    terminal_action_id,
)
from .studio_config import RecordingMedium
from .terminal_capture import (
    TERMINAL_FAILURE_OUTPUT_MAX_BYTES,
    TERMINAL_PRESENTATION_SNAPSHOT_FIELDS,
    TerminalCaptureError,
    TerminalLifecycleStepError,
    TerminalPresentationDefaults,
    _step_command,
    _thaw,
    _validate_expect,
    resolve_terminal_command_snapshots,
    terminal_typing_delays,
)


SessionFactory = Callable[[CaptureContext], EnvoyTerminalSession]


class EnvoyPersistentTerminalRunner:
    """Interpret the terminal plan on a persistent workload-side Bash."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        color: bool = False,
        typing: bool = False,
        typing_min_delay: float = 0.012,
        typing_max_delay: float = 0.045,
        typing_space_delay: float = 0.025,
        typing_punctuation_delay: float = 0.05,
        typing_newline_delay: float = 0.16,
        typing_seed: int = 17,
        post_enter_pause: float = 0.0,
        post_command_pause: float = 0.0,
        timeout_seconds: float = 30.0,
        delegated_environment: Mapping[str, str] | None = None,
        secret_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.color = color
        self.typing = typing
        self.typing_min_delay = typing_min_delay
        self.typing_max_delay = typing_max_delay
        self.typing_space_delay = typing_space_delay
        self.typing_punctuation_delay = typing_punctuation_delay
        self.typing_newline_delay = typing_newline_delay
        self.typing_seed = typing_seed
        self.post_enter_pause = post_enter_pause
        self.post_command_pause = post_command_pause
        self.timeout_seconds = timeout_seconds
        self.delegated_environment = dict(delegated_environment or {})
        self.secret_environment = dict(secret_environment or {})
        self.context: CaptureContext | None = None
        self.session: EnvoyTerminalSession | None = None
        self._operation_sequence = 0
        self._active_thread: threading.Thread | None = None
        self._active_result: list[EnvoyOperationResult] = []
        self._active_failure: list[BaseException] = []
        self._active_operation_id: str | None = None
        self._active_producer_id: str | None = None
        self._active_output_start = 0
        self._active_validation_command: Mapping[str, Any] | None = None
        self._next_cast_start = 0
        self._next_cast_event = 0

    def start(self, context: CaptureContext) -> None:
        if self.session is not None:
            return
        self.context = context
        session = self.session_factory(context)
        try:
            session.start()
        except EnvoySessionError as exc:
            raise TerminalCaptureError(f"could not start Envoy terminal: {exc}") from exc
        self.session = session
        self._next_cast_start, self._next_cast_event = session.cast_checkpoint()

    def run_setup(self, steps: Iterable[TerminalCheckPlan]) -> None:
        self._run_hidden_steps("setup", steps)

    def run_cleanup(self, steps: Iterable[TerminalCheckPlan]) -> None:
        self._run_hidden_steps("cleanup", steps)

    def capture_beat(
        self,
        beat: OuterBeatPlan,
        *,
        on_progress: Callable[[str, str], None] | None = None,
        before_action: Callable[[str], None] | None = None,
    ) -> BeatCapture:
        if beat.medium is not RecordingMedium.terminal:
            raise TerminalCaptureError(
                f"terminal runner cannot capture {beat.medium.value} beat {beat.id!r}"
            )
        session = self._require_session()
        context = self._require_context()
        actions = tuple(
            item for item in beat.actions if isinstance(item, TerminalActionPlan)
        )
        checks = tuple(item for item in beat.checks if isinstance(item, TerminalCheckPlan))
        snapshots = resolve_terminal_command_snapshots(
            actions,
            working_directory=context.working_directory,
            defaults=self._presentation_defaults(),
        )
        cast_start, event_start = self._next_cast_start, self._next_cast_event
        beat_started = session.elapsed_ms
        timings: list[dict[str, Any]] = []
        prompt_visible = False
        for action_index, action in enumerate(actions):
            value = _thaw(action.config)
            entries = value.get("commands")
            commands = list(enumerate(entries)) if entries else [(None, value)]
            group_output_start = session.raw_offset
            group_status: int | None = None
            group_suspended = False
            for command_index, command in commands:
                action_id = terminal_action_id(action_index, command_index, command)
                if command.get("browser_handoff"):
                    raise TerminalCaptureError(
                        "browser handoff belongs to the next Reploy integration slice"
                    )
                snapshot = snapshots[action_id]
                action_start_ms = session.elapsed_ms - beat_started
                relative_action_start = session.cast_event_count - event_start
                if on_progress is not None:
                    on_progress("started", action_id)
                continuation = bool(command.get("continue_from"))
                if not continuation:
                    if not prompt_visible:
                        session.present(self._prompt(), phase="prompt")
                    self._pause(snapshot["pre_command_pause"])
                    typing_start = session.cast_event_count - event_start
                    self._present_command(snapshot)
                    typing_end = session.cast_event_count - event_start
                    self._pause(snapshot["pre_enter_pause"])
                    session.present("\n", phase="displayed_command")
                    self._pause(snapshot["post_enter_pause"])
                else:
                    typing_start = session.cast_event_count - event_start
                    typing_end = typing_start
                output_start = session.cast_event_count - event_start
                result = self._execute_command(
                    command,
                    snapshot,
                    on_gate=(
                        None
                        if before_action is None
                        else lambda _gate, action_id=action_id: before_action(action_id)
                    ),
                )
                group_status = result.status
                group_suspended = group_suspended or result.suspended
                output_end = session.cast_event_count - event_start
                if not result.suspended:
                    self._validate_result(command, result)
                    self._pause(snapshot["post_command_pause"])
                    prompt_visible = bool(snapshot["show_prompt_after"])
                    if prompt_visible:
                        session.present(self._prompt(), phase="prompt")
                else:
                    prompt_visible = False
                if on_progress is not None:
                    on_progress("completed", action_id)
                timings.append(
                    {
                        "id": action_id,
                        "timing": snapshot["timing"],
                        "capture_start_ms": action_start_ms,
                        "capture_end_ms": session.elapsed_ms - beat_started,
                        "event_indexes": {
                            "action_start": relative_action_start,
                            "typing_start": typing_start,
                            "typing_end": typing_end,
                            "output_start": output_start,
                            "output_end": output_end,
                            "action_end": session.cast_event_count - event_start,
                        },
                        "presentation_snapshot": {
                            field: snapshot[field]
                            for field in TERMINAL_PRESENTATION_SNAPSHOT_FIELDS
                        },
                    }
                )
            group_expect = value.get("expect", {}) if entries else {}
            if entries and group_expect and not group_suspended:
                self._validate_group_expect(
                    group_expect,
                    group_output_start,
                    session.raw_offset,
                    group_status,
                )
        if checks:
            self._run_hidden_steps("checks", checks)
        beat_ended = session.elapsed_ms
        cast_end, _ = session.cast_checkpoint()
        self._next_cast_start = cast_end
        self._next_cast_event = session.cast_event_count
        beat_dir = context.paths.capture / "terminal-beats"
        beat_cast = beat_dir / f"{beat.id}.cast"
        action_timing = beat_dir / f"{beat.id}.actions.json"
        session.write_cast_slice(cast_start, cast_end, beat_cast)
        action_timing.write_text(
            json.dumps(
                {
                    "version": 1,
                    "beat_id": beat.id,
                    "actions": timings,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return BeatCapture(
            beat.id,
            (beat_cast, action_timing, session.timeline_path),
            {"capture_start_ms": beat_started, "capture_end_ms": beat_ended},
        )

    def close(self) -> None:
        session = self.session
        if session is None:
            return
        finalization_error: BaseException | None = None
        try:
            if self._active_operation_id is not None:
                try:
                    session.cancel(self._active_operation_id, "recording-complete")
                    if self._active_thread is not None:
                        self._active_thread.join(6)
                        if self._active_thread.is_alive():
                            raise EnvoySessionError(
                                "open terminal operation did not stop during shutdown"
                            )
                    command = self._active_validation_command
                    result = self._finish_active()
                    if command is not None:
                        self._validate_result(command, result, validate_exit=False)
                except BaseException as exc:
                    finalization_error = exc
                    self._clear_active()
            try:
                session.close()
            except EnvoySessionError as exc:
                if finalization_error is not None:
                    raise TerminalCaptureError(
                        f"open terminal finalization failed: {finalization_error}; "
                        f"persistent Envoy shutdown also failed: {exc}"
                    ) from finalization_error
                raise TerminalCaptureError(
                    f"persistent Envoy shutdown failed: {exc}"
                ) from exc
            if finalization_error is not None:
                if isinstance(finalization_error, TerminalCaptureError):
                    raise finalization_error
                raise TerminalCaptureError(
                    f"open terminal finalization failed: {finalization_error}"
                ) from finalization_error
        finally:
            self.session = None

    def cancel_capture(self) -> None:
        session = self.session
        if session is not None:
            session.abort()

    def _run_hidden_steps(self, operation: str, steps: Iterable[TerminalCheckPlan]) -> None:
        for index, step in enumerate(steps, 1):
            value = _thaw(step.config)
            name = value.get("name") or f"{operation} step {index}"
            try:
                result = self._execute_command(value, None)
                self._validate_result(value, result)
            except TerminalCaptureError as exc:
                raise TerminalLifecycleStepError(operation, str(name), index, exc) from exc

    def _execute_command(
        self,
        command: Mapping[str, Any],
        snapshot: Mapping[str, Any] | None,
        *,
        on_gate: Callable[[str], None] | None = None,
    ) -> EnvoyOperationResult:
        session = self._require_session()
        context = self._require_context()
        source = snapshot["command"] if snapshot is not None else _step_command(command, context)
        continue_from = command.get("continue_from")
        if continue_from:
            if not isinstance(continue_from, str):
                raise TerminalCaptureError("continue_from must be an identifier")
            if command.get("_finalize_open_at_recording_end"):
                self._active_validation_command = dict(command)
            try:
                return self._continue_realtime(command, continue_from)
            except EnvoySessionError as exc:
                raise TerminalCaptureError(
                    f"Envoy continuation {continue_from!r} failed: {exc}"
                ) from exc
        requested = command.get("with_env", [])
        if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
            raise TerminalCaptureError("terminal step with_env must be a string list")
        missing = [item for item in requested if item not in self.delegated_environment]
        if missing:
            raise TerminalCaptureError(
                f"terminal step could not resolve environment {missing[0]!r}"
            )
        environment = dict(self.secret_environment)
        environment.update({name: self.delegated_environment[name] for name in requested})
        if environment:
            source = _scoped_environment_source(source, environment)
        try:
            output = command_output_config(dict(command), field="terminal step")
        except RecordingError as exc:
            raise TerminalCaptureError(str(exc)) from exc
        mode = "hidden" if output["mode"] == "suppress" else output["mode"]
        session.begin_operation_output(mode, output["replace"])
        self._operation_sequence += 1
        operation_id = f"op-{self._operation_sequence}"
        input_steps = command.get("input", [])
        try:
            if input_steps:
                result = self._execute_realtime(
                    operation_id,
                    source,
                    input_steps,
                    producer_id=str(command.get("id") or operation_id),
                    suspend=bool(
                        command.get("_suspend_for_continuation")
                        or command.get("_finalize_open_at_recording_end")
                    ),
                    on_gate=on_gate,
                )
                if result.suspended and command.get("_finalize_open_at_recording_end"):
                    self._active_validation_command = dict(command)
            else:
                result = session.execute(
                    operation_id,
                    source,
                    timeout=self.timeout_seconds,
                    on_gate=on_gate,
                )
        except EnvoySessionError as exc:
            raise TerminalCaptureError(f"Envoy operation {operation_id} failed: {exc}") from exc
        finally:
            session.end_operation_output()
        return result

    def _execute_realtime(
        self,
        operation_id: str,
        source: str,
        input_steps: object,
        *,
        producer_id: str,
        suspend: bool,
        on_gate: Callable[[str], None] | None,
    ) -> EnvoyOperationResult:
        if not isinstance(input_steps, list):
            raise TerminalCaptureError("terminal step input must be a list")
        session = self._require_session()
        if self._active_thread is not None:
            raise TerminalCaptureError("a realtime terminal operation is already active")
        result: list[EnvoyOperationResult] = []
        failure: list[BaseException] = []

        def execute() -> None:
            try:
                result.append(
                    session.execute(
                        operation_id,
                        source,
                        timeout=self.timeout_seconds,
                        on_gate=on_gate,
                    )
                )
            except BaseException as exc:
                failure.append(exc)

        worker = threading.Thread(target=execute, daemon=True)
        self._active_thread = worker
        self._active_result = result
        self._active_failure = failure
        self._active_operation_id = operation_id
        self._active_producer_id = producer_id
        self._active_output_start = session.raw_offset
        worker.start()
        deadline = time.monotonic() + self.timeout_seconds
        while session.state.phase == "starting" and time.monotonic() < deadline:
            time.sleep(0.005)
        self._apply_input_steps(input_steps, self._active_output_start, deadline)
        if suspend:
            if not worker.is_alive():
                return self._finish_active()
            ready = session.ready
            return EnvoyOperationResult(
                operation_id,
                0,
                ready.cwd if ready is not None else "/",
                self._active_output_start,
                session.raw_offset,
                suspended=True,
            )
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            session.cancel(operation_id, "operation-timeout")
            worker.join(6)
        return self._finish_active()

    def _continue_realtime(
        self,
        command: Mapping[str, Any],
        continue_from: str,
    ) -> EnvoyOperationResult:
        if self._active_thread is None or self._active_operation_id is None:
            raise TerminalCaptureError("continue_from has no active terminal operation")
        if continue_from != self._active_producer_id:
            raise TerminalCaptureError("continue_from does not match the active producer")
        input_steps = command.get("input", [])
        if not isinstance(input_steps, list):
            raise TerminalCaptureError("terminal step input must be a list")
        deadline = time.monotonic() + self.timeout_seconds
        self._apply_input_steps(input_steps, self._active_output_start, deadline)
        if command.get("_suspend_for_continuation") or command.get(
            "_finalize_open_at_recording_end"
        ):
            if not self._active_thread.is_alive():
                return self._finish_active()
            ready = self._require_session().ready
            return EnvoyOperationResult(
                self._active_operation_id,
                0,
                ready.cwd if ready is not None else "/",
                self._active_output_start,
                self._require_session().raw_offset,
                suspended=True,
            )
        self._active_thread.join(max(0.0, deadline - time.monotonic()))
        if self._active_thread.is_alive():
            self._require_session().cancel(self._active_operation_id, "operation-timeout")
            self._active_thread.join(6)
        return self._finish_active()

    def _apply_input_steps(
        self,
        input_steps: list[object],
        start: int,
        deadline: float,
    ) -> None:
        session = self._require_session()
        for item in input_steps:
            if not isinstance(item, Mapping):
                raise TerminalCaptureError("terminal input item must be a mapping")
            actions = [
                name
                for name in ("pause", "wait_for", "text", "control", "key")
                if item.get(name) is not None
            ]
            if len(actions) != 1:
                raise TerminalCaptureError(
                    "terminal input item must contain exactly one input action"
                )
            action = actions[0]
            item_deadline = deadline
            configured_timeout = item.get("timeout")
            if configured_timeout is not None:
                item_deadline = min(
                    deadline,
                    time.monotonic() + float(configured_timeout),
                )
            if action == "pause":
                self._pause(float(item["pause"]))
            elif action == "wait_for":
                self._wait_for_output(str(item["wait_for"]), start, item_deadline)
            elif action == "text":
                configured_interval = item.get("interval")
                interval = (
                    0.035
                    if configured_interval is None
                    else float(configured_interval)
                )
                for character in str(item["text"]):
                    session.send_input(b"\r" if character == "\n" else character.encode())
                    self._pause(interval)
            elif action == "control":
                control = str(item["control"]).lower()
                if len(control) != 1 or not "a" <= control <= "z":
                    raise TerminalCaptureError("terminal control input must be one letter")
                session.send_input(bytes([ord(control) & 0x1F]))
            else:
                keys = {
                    "enter": b"\r", "tab": b"\t", "escape": b"\x1b",
                    "backspace": b"\x7f", "up": b"\x1b[A", "down": b"\x1b[B",
                    "right": b"\x1b[C", "left": b"\x1b[D",
                    "delete": b"\x1b[3~", "home": b"\x1b[H", "end": b"\x1b[F",
                    "page_up": b"\x1b[5~", "page_down": b"\x1b[6~",
                }
                key = str(item["key"])
                if key not in keys:
                    raise TerminalCaptureError(f"unsupported terminal key {key!r}")
                session.send_input(keys[key])

    def _finish_active(self) -> EnvoyOperationResult:
        if self._active_failure:
            failure = self._active_failure[0]
            self._clear_active()
            raise EnvoySessionError(str(failure)) from failure
        if not self._active_result:
            self._clear_active()
            raise EnvoySessionError("realtime operation did not complete")
        result = self._active_result[0]
        self._clear_active()
        return result

    def _clear_active(self) -> None:
        self._active_thread = None
        self._active_result = []
        self._active_failure = []
        self._active_operation_id = None
        self._active_producer_id = None
        self._active_output_start = 0
        self._active_validation_command = None

    def _wait_for_output(self, text: str, start: int, deadline: float) -> None:
        session = self._require_session()
        while time.monotonic() < deadline:
            through = session.raw_offset
            if text in session.read_output_range(start, through).decode("utf-8", "replace"):
                return
            time.sleep(0.01)
        raise TerminalCaptureError(f"timed out waiting for terminal output {text!r}")

    def _validate_result(
        self,
        command: Mapping[str, Any],
        result: EnvoyOperationResult,
        *,
        validate_exit: bool = True,
    ) -> None:
        if result.failure_code is not None:
            raise TerminalCaptureError(
                result.failure_message or result.failure_code,
                failure_kind=result.failure_code,
            )
        expect = command.get("_continuation_expect", command.get("expect", {}))
        if not isinstance(expect, Mapping):
            raise TerminalCaptureError("terminal step expect must be a mapping")
        _validate_expect(expect)
        expected_status = expect.get("exit_code", 0)
        output = self._require_session().read_output_range(
            result.output_start, result.output_through
        ).decode("utf-8", "replace")
        if any(value and value in output for value in self.secret_environment.values()):
            raise TerminalCaptureError(
                "recording secret appeared in terminal command output",
                failure_kind="secret",
                exit_code=result.status,
            )
        failures = []
        if validate_exit and result.status != expected_status:
            failures.append(f"terminal step exited {result.status}, expected {expected_status}")
        for text in expect.get("output_contains", []):
            if text not in output:
                failures.append(f"terminal step output is missing text: {text}")
        for pattern in expect.get("output_regex", []):
            if re.search(pattern, output) is None:
                failures.append(f"terminal step output does not match: {pattern}")
        for configured in expect.get("file_exists", []):
            if not self._probe_file_exists(configured):
                failures.append(f"terminal step file is missing: {configured}")
        if failures:
            tail = output[-TERMINAL_FAILURE_OUTPUT_MAX_BYTES:]
            raise TerminalCaptureError(
                failures[0],
                output=tail or None,
                output_truncated=len(output) > len(tail),
                exit_code=result.status,
            )
        self._record_produced_outputs(command)

    def _probe_file_exists(self, configured: str) -> bool:
        session = self._require_session()
        self._operation_sequence += 1
        operation_id = f"probe-{self._operation_sequence}"
        source = "test -e " + shlex.quote(configured)
        session.begin_operation_output("hidden")
        try:
            result = session.execute(
                operation_id,
                source,
                timeout=self.timeout_seconds,
            )
        except EnvoySessionError as exc:
            raise TerminalCaptureError(f"file expectation probe failed: {exc}") from exc
        finally:
            session.end_operation_output()
        return result.failure_code is None and result.status == 0

    def _record_produced_outputs(self, command: Mapping[str, Any]) -> None:
        produces = command.get("_continuation_produces", command.get("produces", {}))
        if not isinstance(produces, Mapping):
            raise TerminalCaptureError("terminal step produces must be a mapping")
        if not produces:
            return
        producer = command.get("_continuation_producer_id", command.get("id", ""))
        if not isinstance(producer, str) or not producer:
            raise TerminalCaptureError("terminal producer requires an id")
        records = []
        for name, configured in produces.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(configured, str)
                or not configured
                or "\n" in configured
            ):
                raise TerminalCaptureError("terminal produces entries must be non-empty strings")
            record = self._probe_produced_output(producer, name, configured)
            records.append(record)
        path = self._require_context().paths.capture / "produced-outputs.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _probe_produced_output(
        self,
        producer: str,
        name: str,
        configured: str,
    ) -> dict[str, str]:
        session = self._require_session()
        self._operation_sequence += 1
        operation_id = f"artifact-{self._operation_sequence}"
        path = shlex.quote(configured)
        source = (
            "__omegaflow_hash_directory() ("
            "set -o pipefail; cd -- \"$1\" || return; "
            "{ printf 'directory\\0'; "
            "LC_ALL=C find -P . -mindepth 1 -print0 | LC_ALL=C sort -z | "
            "while IFS= read -r -d '' __omegaflow_entry; do "
            "__omegaflow_relative=${__omegaflow_entry#./}; "
            "if [[ -L \"$__omegaflow_entry\" ]]; then "
            "printf 'link\\0%s\\0' \"$__omegaflow_relative\"; "
            "readlink -z -- \"$__omegaflow_entry\"; "
            "elif [[ -d \"$__omegaflow_entry\" ]]; then "
            "printf 'dir\\0%s\\0' \"$__omegaflow_relative\"; "
            "elif [[ -f \"$__omegaflow_entry\" ]]; then "
            "printf 'file\\0%s\\0' \"$__omegaflow_relative\"; "
            "cat -- \"$__omegaflow_entry\"; printf '\\0'; "
            "fi; done; } | sha256sum | cut -d' ' -f1); "
            f"__omegaflow_path={path}; "
            'if [[ -f "$__omegaflow_path" ]]; then '
            "printf 'file\\0'; realpath -e -z -- \"$__omegaflow_path\"; "
            'sha256sum -- "$__omegaflow_path" | cut -d" " -f1; '
            'elif [[ -d "$__omegaflow_path" ]]; then '
            "printf 'directory\\0'; realpath -e -z -- \"$__omegaflow_path\"; "
            '__omegaflow_hash_directory "$__omegaflow_path"; '
            'else printf "missing\\n"; __omegaflow_status=44; fi; '
            '__omegaflow_status=${__omegaflow_status:-$?}; '
            'unset -f __omegaflow_hash_directory; '
            'unset __omegaflow_path; '
            'if [[ "$__omegaflow_status" -eq 0 ]]; then '
            'unset __omegaflow_status; return 0; '
            'else unset __omegaflow_status; return 44; fi'
        )
        session.begin_operation_output("hidden")
        try:
            result = session.execute(operation_id, source, timeout=self.timeout_seconds)
        except EnvoySessionError as exc:
            raise TerminalCaptureError(f"produced output probe failed: {exc}") from exc
        finally:
            session.end_operation_output()
        payload = session.read_output_range(
            result.output_start, result.output_through
        )
        fields = payload.split(b"\x00")
        if result.status != 0 or len(fields) != 3:
            raise TerminalCaptureError(
                f"terminal producer {producer!r} did not create {name!r}: {configured}",
                failure_kind="produces",
                exit_code=result.status,
            )
        try:
            kind = fields[0].decode("ascii")
            resolved = fields[1].decode("utf-8", "strict")
            digest = fields[2].decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise TerminalCaptureError(
                "produced output probe returned invalid text"
            ) from exc
        if kind not in {"file", "directory"}:
            raise TerminalCaptureError("produced output probe returned an invalid kind")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise TerminalCaptureError("produced output probe returned an invalid digest")
        return {
            "producer": producer,
            "output": name,
            "path": resolved,
            "kind": kind,
            "sha256": digest,
        }

    def _validate_group_expect(
        self,
        expect: object,
        start: int,
        through: int,
        status: int | None,
    ) -> None:
        if not isinstance(expect, Mapping):
            raise TerminalCaptureError("terminal action expect must be a mapping")
        _validate_expect(expect)
        output = self._require_session().read_output_range(start, through).decode(
            "utf-8", "replace"
        )
        for text in expect.get("output_contains", []):
            if text not in output:
                raise TerminalCaptureError(
                    f"terminal action output is missing text: {text}", output=output
                )
        for pattern in expect.get("output_regex", []):
            if re.search(pattern, output) is None:
                raise TerminalCaptureError(
                    f"terminal action output does not match: {pattern}", output=output
                )
        expected_status = expect.get("exit_code", 0)
        if status != expected_status:
            raise TerminalCaptureError(
                f"terminal action exited {status}, expected {expected_status}"
            )
        for configured in expect.get("file_exists", []):
            if not self._probe_file_exists(configured):
                raise TerminalCaptureError(f"terminal action file is missing: {configured}")

    def _present_command(self, snapshot: Mapping[str, Any]) -> None:
        session = self._require_session()
        display = str(snapshot["display"])
        if self.typing and snapshot["timing"] == "presentation":
            delays = terminal_typing_delays(
                display,
                minimum=float(snapshot["typing_min_delay"]),
                maximum=float(snapshot["typing_max_delay"]),
                space=float(snapshot["typing_space_delay"]),
                punctuation=float(snapshot["typing_punctuation_delay"]),
                newline=float(snapshot["typing_newline_delay"]),
                seed=int(snapshot["typing_seed"]),
            )
            for index, character in enumerate(display):
                session.present(character, phase="displayed_command")
                if index < len(delays):
                    self._pause(delays[index])
        else:
            session.present(display, phase="displayed_command")

    def _prompt(self) -> str:
        return "\x1b[32;1m$\x1b[0m " if self.color else "$ "

    def _pause(self, duration: float) -> None:
        if duration > 0:
            time.sleep(duration)

    def _presentation_defaults(self) -> TerminalPresentationDefaults:
        return TerminalPresentationDefaults(
            self.color,
            self.typing,
            self.typing_min_delay,
            self.typing_max_delay,
            self.typing_space_delay,
            self.typing_punctuation_delay,
            self.typing_newline_delay,
            self.typing_seed,
            self.post_enter_pause,
            self.post_command_pause,
        )

    def _require_session(self) -> EnvoyTerminalSession:
        if self.session is None:
            raise TerminalCaptureError("persistent Envoy runner is not started")
        return self.session

    def _require_context(self) -> CaptureContext:
        if self.context is None:
            raise TerminalCaptureError("terminal capture context is unavailable")
        return self.context


def _scoped_environment_source(source: str, environment: Mapping[str, str]) -> str:
    lines = ["__omegaflow_scoped_command() {"]
    for index, (name, value) in enumerate(environment.items()):
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
            or not isinstance(value, str)
            or "\x00" in value
        ):
            raise TerminalCaptureError(f"invalid delegated environment name {name!r}")
        lines.extend(
            [
                f'  local __omegaflow_had_{index}="${{{name}+x}}"',
                f'  local __omegaflow_old_{index}="${{{name}-}}"',
                f"  export {name}={shlex.quote(value)}",
            ]
        )
    lines.extend([f"  eval {shlex.quote(source)}", "  local __omegaflow_status=$?"])
    for index, name in reversed(list(enumerate(environment))):
        lines.extend(
            [
                f'  if [[ -n "$__omegaflow_had_{index}" ]]; then',
                f'    export {name}="$__omegaflow_old_{index}"',
                "  else",
                f"    unset {name}",
                "  fi",
            ]
        )
    lines.extend(
        [
            '  return "$__omegaflow_status"',
            "}",
            "__omegaflow_scoped_command",
            "__omegaflow_status=$?",
            "unset -f __omegaflow_scoped_command",
            'return "$__omegaflow_status"',
        ]
    )
    return "\n".join(lines)
