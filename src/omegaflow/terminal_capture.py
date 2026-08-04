"""Persistent terminal capture runner and private control protocol."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import select
import shlex
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .capture import BeatCapture, CaptureContext
from .record import RecordingError, asciinema_command, command_output_config
from .recording_plan import (
    OuterBeatPlan,
    FrozenMapping,
    TerminalActionPlan,
    TerminalCheckPlan,
    terminal_action_id,
)
from .studio_config import RecordingMedium


CONTROL_STREAM_MODE = 0o600
CONTROL_TIMEOUT_SECONDS = 30.0
SHUTDOWN_GRACE_SECONDS = 5.0
TERMINAL_BOUNDARY_OUTPUT_BYTE_LIMIT = 8 * 1024 * 1024
CONTROL_OPERATIONS = frozenset({"setup", "beat", "checks", "cleanup", "shutdown"})
TERMINAL_MARKER_RE = re.compile(
    r"\x1b\]1337;OmegaFlow;(?P<seq>[0-9]+);"
    r"(?P<op>setup|beat|checks|cleanup);(?P<phase>start|end);"
    r"(?P<beat>[A-Za-z0-9_-]*)\x07"
)
TERMINAL_ACTION_MARKER_RE = re.compile(
    r"\x1b\]1337;OmegaFlowAction;(?P<beat>[A-Za-z0-9_-]+);"
    r"(?P<action>[A-Za-z0-9_-]+);(?P<phase>start|end)\x07"
)
TERMINAL_COMMAND_MARKER_RE = re.compile(
    r"\x1b\]1337;OmegaFlowCommand;(?P<beat>[A-Za-z0-9_-]+);"
    r"(?P<action>[A-Za-z0-9_-]+);"
    r"(?P<phase>typing_start|typing_end|output_start|output_end)\x07"
)
TERMINAL_PRESENTATION_SNAPSHOT_FIELDS = (
    "display",
    "color",
    "typing",
    "typing_min_delay",
    "typing_max_delay",
    "typing_space_delay",
    "typing_punctuation_delay",
    "typing_newline_delay",
    "typing_seed",
    "pre_command_pause",
    "pre_enter_pause",
    "post_enter_pause",
    "post_command_pause",
)
TERMINAL_BROWSER_HANDOFF_MARKER_RE = re.compile(
    r"\x1b\]1337;OmegaFlowBrowserHandoff;"
    r"(?P<action>[A-Za-z0-9_-]+);ready\x07"
)
TERMINAL_ANY_MARKER_RE = re.compile(
    r"\x1b\]1337;(?:"
    r"OmegaFlow;[0-9]+;(?:setup|beat|checks|cleanup);(?:start|end);[A-Za-z0-9_-]*"
    r"|OmegaFlowAction;[A-Za-z0-9_-]+;[A-Za-z0-9_-]+;(?:start|end)"
    r"|OmegaFlowCommand;[A-Za-z0-9_-]+;[A-Za-z0-9_-]+;"
    r"(?:typing_start|typing_end|output_start|output_end)"
    r"|OmegaFlowBrowserHandoff;[A-Za-z0-9_-]+;ready"
    r")\x07"
)


class TerminalCaptureError(RuntimeError):
    """Raised when the persistent terminal session or its protocol fails."""


class TerminalLifecycleStepError(TerminalCaptureError):
    """A named setup or cleanup step failed in the terminal session."""

    def __init__(
        self,
        operation: str,
        step_name: str,
        step_index: int,
        error: TerminalCaptureError,
        run_file: str | None = None,
    ) -> None:
        self.operation = operation
        self.step_name = step_name
        self.step_index = step_index
        self.error = error
        self.run_file = run_file
        super().__init__(f"{operation} step {step_name!r} failed: {error}")


@dataclass(frozen=True)
class TerminalPresentationDefaults:
    """Resolved recording-wide defaults used to materialize terminal actions."""

    color: bool
    typing: bool
    typing_min_delay: float
    typing_max_delay: float
    typing_space_delay: float
    typing_punctuation_delay: float
    typing_newline_delay: float
    typing_seed: int
    post_enter_pause: float
    post_command_pause: float


def terminal_typing_delays(
    text: str,
    *,
    minimum: float,
    maximum: float,
    space: float,
    punctuation: float,
    newline: float,
    seed: int,
) -> tuple[float, ...]:
    """Return the deterministic delay following each non-final typed character."""

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    rng = random.Random(seed ^ int.from_bytes(digest[:8], "big"))
    delays: list[float] = []
    for index, char in enumerate(text):
        if index == len(text) - 1:
            break
        delay = rng.uniform(minimum, maximum)
        if char == "\n":
            delay += newline + rng.uniform(0.0, newline / 2)
        elif char.isspace():
            delay += rng.uniform(0.0, space)
        elif char in "|;&":
            delay += punctuation + rng.uniform(0.0, punctuation)
        elif char == "\\":
            delay += newline / 2
        elif char in ",.:=/\"'{}[]()":
            delay += rng.uniform(0.0, punctuation)
        if char in " -_/" and rng.random() < 0.08:
            delay += rng.uniform(0.04, 0.12)
        delays.append(delay)
    return tuple(delays)


def _session_script() -> str:
    return r'''#!/usr/bin/env bash
set +e
trap '' PIPE
exec 8<>"$OMEGAFLOW_REQUEST_STREAM"
exec 9<>"$OMEGAFLOW_RESPONSE_STREAM"
: >"$OMEGAFLOW_TERMINAL_STDOUT"
: >"$OMEGAFLOW_TERMINAL_STDERR"
OMEGAFLOW_PROMPT_VISIBLE=0
OMEGAFLOW_USER_COMMAND_SEQ=0
OMEGAFLOW_LAST_COMMAND_CWD=""
OMEGAFLOW_ACTIVE_STATUS_PIPE=""
OMEGAFLOW_ACTIVE_STATUS_RESULT=""
OMEGAFLOW_ACTIVE_CWD_RESULT=""
OMEGAFLOW_ACTIVE_PTY_READY=""
OMEGAFLOW_ACTIVE_PTY_OPENED=""
OMEGAFLOW_ACTIVE_SECRET_LEAK=""
OMEGAFLOW_ACTIVE_INPUT_ERROR=""
OMEGAFLOW_ACTIVE_RELAY_PID=""
OMEGAFLOW_ACTIVE_STATUS_MONITOR_PID=""
OMEGAFLOW_ACTIVE_INPUT_SEQ=0
OMEGAFLOW_ACTIVE_STDOUT_START=""
OMEGAFLOW_ACTIVE_STDERR_START=""
OMEGAFLOW_VISIBLE=0
: "${OMEGAFLOW_COLOR:=0}"
: "${OMEGAFLOW_TYPING:=0}"
: "${OMEGAFLOW_TYPING_MIN_DELAY:=0.012}"
: "${OMEGAFLOW_TYPING_MAX_DELAY:=0.045}"
: "${OMEGAFLOW_TYPING_SPACE_DELAY:=0.025}"
: "${OMEGAFLOW_TYPING_PUNCTUATION_DELAY:=0.05}"
: "${OMEGAFLOW_TYPING_NEWLINE_DELAY:=0.16}"
: "${OMEGAFLOW_TYPING_SEED:=17}"
: "${OMEGAFLOW_POST_ENTER_PAUSE:=0}"
: "${OMEGAFLOW_POST_COMMAND_PAUSE:=0}"
OMEGAFLOW_USER_SHELL_INPUT="$TMPDIR/omegaflow-user-shell.input.pipe"
OMEGAFLOW_USER_SHELL_DEAD="$TMPDIR/omegaflow-user-shell.dead"
OMEGAFLOW_USER_SHELL_PGID="$TMPDIR/omegaflow-user-shell.pgid"
rm -f "$OMEGAFLOW_USER_SHELL_INPUT" "$OMEGAFLOW_USER_SHELL_DEAD" "$OMEGAFLOW_USER_SHELL_PGID"
mkfifo "$OMEGAFLOW_USER_SHELL_INPUT"

"$OMEGAFLOW_PYTHON" - "$OMEGAFLOW_USER_SHELL_INPUT" "$OMEGAFLOW_USER_SHELL_DEAD" "$OMEGAFLOW_USER_SHELL_PGID" <<'PY' &
import subprocess
import sys
from pathlib import Path

input_path, dead_path, pgid_path = sys.argv[1:]
with open(input_path, "rb") as shell_input:
    process = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc"],
        stdin=shell_input,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        process_group=0,
    )
    Path(pgid_path).write_text(f"{process.pid}\n", encoding="utf-8")
    status = process.wait()
Path(dead_path).write_text(f"{status}\n", encoding="utf-8")
PY
OMEGAFLOW_USER_SHELL_MONITOR_PID=$!
exec 7>"$OMEGAFLOW_USER_SHELL_INPUT"

omegaflow_cleanup_user_shell() {
  local ignored_status=$?
  trap - EXIT
  set +e
  if [[ -n "$OMEGAFLOW_ACTIVE_RELAY_PID" ]]; then
    kill -TERM "$OMEGAFLOW_ACTIVE_RELAY_PID" 2>/dev/null || true
    wait "$OMEGAFLOW_ACTIVE_RELAY_PID" 2>/dev/null || true
  fi
  exec 7>&- 2>/dev/null || true
  local wait_index
  for ((wait_index = 0; wait_index < 20; wait_index += 1)); do
    [[ -f "$OMEGAFLOW_USER_SHELL_DEAD" ]] && break
    sleep 0.05
  done
  if [[ -f "$OMEGAFLOW_USER_SHELL_PGID" ]]; then
    local user_shell_pgid
    IFS= read -r user_shell_pgid <"$OMEGAFLOW_USER_SHELL_PGID" || user_shell_pgid=""
    [[ -n "$user_shell_pgid" ]] && kill -TERM -- "-$user_shell_pgid" 2>/dev/null || true
    sleep 0.1
    [[ -n "$user_shell_pgid" ]] && kill -KILL -- "-$user_shell_pgid" 2>/dev/null || true
  fi
  wait "$OMEGAFLOW_USER_SHELL_MONITOR_PID" 2>/dev/null || true
  rm -f "$OMEGAFLOW_USER_SHELL_INPUT" "$OMEGAFLOW_USER_SHELL_DEAD" "$OMEGAFLOW_USER_SHELL_PGID"
  return "$ignored_status"
}
trap omegaflow_cleanup_user_shell EXIT

omegaflow_reset_active_user_command() {
  OMEGAFLOW_ACTIVE_STATUS_PIPE=""
  OMEGAFLOW_ACTIVE_STATUS_RESULT=""
  OMEGAFLOW_ACTIVE_CWD_RESULT=""
  OMEGAFLOW_ACTIVE_PTY_READY=""
  OMEGAFLOW_ACTIVE_PTY_OPENED=""
  OMEGAFLOW_ACTIVE_SECRET_LEAK=""
  OMEGAFLOW_ACTIVE_INPUT_ERROR=""
  OMEGAFLOW_ACTIVE_RELAY_PID=""
  OMEGAFLOW_ACTIVE_STATUS_MONITOR_PID=""
  OMEGAFLOW_ACTIVE_INPUT_SEQ=0
  OMEGAFLOW_ACTIVE_STDOUT_START=""
  OMEGAFLOW_ACTIVE_STDERR_START=""
}

omegaflow_wait_active_input_response() {
  local response_path="${OMEGAFLOW_ACTIVE_STATUS_PIPE}.input-response-${OMEGAFLOW_ACTIVE_INPUT_SEQ}"
  while [[ ! -s "$response_path" ]]; do
    if [[ -n "$OMEGAFLOW_ACTIVE_RELAY_PID" ]] && \
      ! kill -0 "$OMEGAFLOW_ACTIVE_RELAY_PID" 2>/dev/null; then
      printf '%s\n' 'continued terminal input relay exited before responding' \
        >"$OMEGAFLOW_ACTIVE_INPUT_ERROR"
      return 125
    fi
    sleep 0.01
  done
  local response_status
  response_status=$("$OMEGAFLOW_PYTHON" - "$response_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    response = json.load(handle)
print(response.get("status", "error"))
PY
  )
  [[ "$response_status" == "suspended" || "$response_status" == "completed" ]]
}

omegaflow_finalize_active_user_command() {
  if [[ -n "$OMEGAFLOW_ACTIVE_STATUS_MONITOR_PID" ]]; then
    wait "$OMEGAFLOW_ACTIVE_STATUS_MONITOR_PID" 2>/dev/null || true
  fi
  if [[ -n "$OMEGAFLOW_ACTIVE_RELAY_PID" ]]; then
    wait "$OMEGAFLOW_ACTIVE_RELAY_PID" 2>/dev/null || true
  fi
  local status=125
  if [[ -f "$OMEGAFLOW_ACTIVE_STATUS_RESULT" ]]; then
    IFS= read -r status <"$OMEGAFLOW_ACTIVE_STATUS_RESULT" || status=125
  fi
  if [[ -f "$OMEGAFLOW_ACTIVE_SECRET_LEAK" ]]; then
    printf '%s\n' 'recording secret appeared in terminal command output' \
      >>"$OMEGAFLOW_TERMINAL_STDERR"
    status=125
  fi
  if [[ -f "$OMEGAFLOW_ACTIVE_INPUT_ERROR" ]]; then
    printf '%s' 'terminal input failed: ' >>"$OMEGAFLOW_TERMINAL_STDERR"
    cat "$OMEGAFLOW_ACTIVE_INPUT_ERROR" >>"$OMEGAFLOW_TERMINAL_STDERR"
    status=125
  fi
  OMEGAFLOW_LAST_COMMAND_CWD=""
  if [[ -f "$OMEGAFLOW_ACTIVE_CWD_RESULT" ]]; then
    IFS= read -r OMEGAFLOW_LAST_COMMAND_CWD \
      <"$OMEGAFLOW_ACTIVE_CWD_RESULT" || OMEGAFLOW_LAST_COMMAND_CWD=""
  fi
  rm -f \
    "$OMEGAFLOW_ACTIVE_STATUS_PIPE" "$OMEGAFLOW_ACTIVE_STATUS_RESULT" \
    "$OMEGAFLOW_ACTIVE_CWD_RESULT" "$OMEGAFLOW_ACTIVE_PTY_READY" \
    "$OMEGAFLOW_ACTIVE_PTY_OPENED" "$OMEGAFLOW_ACTIVE_SECRET_LEAK" \
    "$OMEGAFLOW_ACTIVE_INPUT_ERROR" \
    "${OMEGAFLOW_ACTIVE_STATUS_PIPE}.input-request-"* \
    "${OMEGAFLOW_ACTIVE_STATUS_PIPE}.input-response-"*
  omegaflow_reset_active_user_command
  return "$status"
}

omegaflow_continue_user_command() {
  local input_json="$1"
  local suspend="$2"
  if [[ -z "$OMEGAFLOW_ACTIVE_STATUS_PIPE" ]]; then
    printf '%s\n' 'continue_from has no active terminal command' \
      >"$OMEGAFLOW_TERMINAL_STDERR"
    return 125
  fi
  OMEGAFLOW_ACTIVE_INPUT_SEQ=$((OMEGAFLOW_ACTIVE_INPUT_SEQ + 1))
  local request_path="${OMEGAFLOW_ACTIVE_STATUS_PIPE}.input-request-${OMEGAFLOW_ACTIVE_INPUT_SEQ}"
  "$OMEGAFLOW_PYTHON" - \
    "$request_path" "$input_json" "$suspend" <<'PY'
import json
import os
import sys

path, input_json, suspend = sys.argv[1:]
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "actions": json.loads(input_json),
            "finish": suspend != "true",
        },
        handle,
        separators=(",", ":"),
    )
os.replace(temporary, path)
PY
  if ! omegaflow_wait_active_input_response; then
    omegaflow_finalize_active_user_command
    return 125
  fi
  if [[ "$suspend" == "true" ]]; then
    return 0
  fi
  omegaflow_finalize_active_user_command
}

omegaflow_run_user_command() {
  local command="$1"
  local stdout_target="$2"
  local stderr_target="$3"
  local timing="${4:-presentation}"
  local encoded_environment="${5:-}"
  local input_json="${6:-[]}"
  local suspend="${7:-false}"
  if [[ -n "$OMEGAFLOW_ACTIVE_STATUS_PIPE" ]]; then
    printf '%s\n' 'terminal command started before continued command completed' \
      >"$OMEGAFLOW_TERMINAL_STDERR"
    return 125
  fi
  OMEGAFLOW_USER_COMMAND_SEQ=$((OMEGAFLOW_USER_COMMAND_SEQ + 1))
  local status_pipe="$TMPDIR/omegaflow-user-command-${OMEGAFLOW_USER_COMMAND_SEQ}.pipe"
  local status_result="${status_pipe}.result"
  local cwd_result="${status_pipe}.cwd"
  rm -f "$status_pipe" "$status_result" "$cwd_result"
  mkfifo "$status_pipe"
  "$OMEGAFLOW_PYTHON" - "$status_pipe" "$status_result" "$OMEGAFLOW_USER_SHELL_DEAD" <<'PY' &
import os
import sys
import time

fifo_path, result_path, dead_path = sys.argv[1:]
fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
buffer = b""
status = "125"
try:
    while True:
        try:
            chunk = os.read(fd, 128)
        except BlockingIOError:
            chunk = b""
        if chunk:
            buffer += chunk
            if b"\n" in buffer:
                status = buffer.split(b"\n", 1)[0].decode("ascii", "replace")
                break
        if os.path.exists(dead_path):
            try:
                dead_status = open(dead_path, encoding="utf-8").read().strip()
            except OSError:
                dead_status = ""
            if dead_status:
                status = dead_status
                break
        time.sleep(0.02)
finally:
    os.close(fd)
with open(result_path, "w", encoding="utf-8") as handle:
    handle.write(status + "\n")
PY
  local status_monitor_pid=$!
  local encoded_command
  encoded_command=$("$OMEGAFLOW_PYTHON" - "$command" <<'PY'
import base64
import sys

print(base64.b64encode(sys.argv[1].encode()).decode())
PY
  )
  if [[ -n "$encoded_environment" ]]; then
    encoded_command=$("$OMEGAFLOW_PYTHON" - \
      "$encoded_command" "$encoded_environment" <<'PY'
import base64
import json
import shlex
import sys

command = base64.b64decode(sys.argv[1]).decode()
environment = json.loads(base64.b64decode(sys.argv[2]).decode())
lines = ["__omegaflow_scoped_command() {"]
for index, (name, value) in enumerate(environment.items()):
    lines.extend(
        [
            f'  local __omegaflow_had_{index}="${{{name}+x}}"',
            f'  local __omegaflow_old_{index}="${{{name}-}}"',
            f"  export {name}={shlex.quote(value)}",
        ]
    )
lines.extend([f"  eval {shlex.quote(command)}", "  local __omegaflow_status=$?"])
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
        "__omegaflow_scoped_status=$?",
        "unset -f __omegaflow_scoped_command",
        '(exit "$__omegaflow_scoped_status")',
    ]
)
wrapped = "\n".join(lines)
print(base64.b64encode(wrapped.encode()).decode())
PY
    )
  fi
  local decoder='import base64,sys;print(base64.b64decode(sys.argv[1]).decode(),end="")'
  local relay_pid=""
  local pty_ready="${status_pipe}.pty-ready"
  local pty_opened="${status_pipe}.pty-opened"
  local secret_leak="${status_pipe}.secret-leak"
  local input_error="${status_pipe}.input-error"
  OMEGAFLOW_ACTIVE_STATUS_PIPE="$status_pipe"
  OMEGAFLOW_ACTIVE_STATUS_RESULT="$status_result"
  OMEGAFLOW_ACTIVE_CWD_RESULT="$cwd_result"
  OMEGAFLOW_ACTIVE_PTY_READY="$pty_ready"
  OMEGAFLOW_ACTIVE_PTY_OPENED="$pty_opened"
  OMEGAFLOW_ACTIVE_SECRET_LEAK="$secret_leak"
  OMEGAFLOW_ACTIVE_INPUT_ERROR="$input_error"
  OMEGAFLOW_ACTIVE_STATUS_MONITOR_PID="$status_monitor_pid"
  OMEGAFLOW_ACTIVE_INPUT_SEQ=1
  if [[ "$timing" == "realtime" ]]; then
    rm -f "$pty_ready" "$pty_opened" "$secret_leak" "$input_error"
    "$OMEGAFLOW_PYTHON" - \
      "$pty_ready" "$pty_opened" "$stdout_target" \
      "$encoded_environment" "$secret_leak" "$input_json" "$input_error" \
      "$status_pipe" "$suspend" <<'PY' &
import base64
import errno
import fcntl
import json
import os
import pty
import re
import select
import struct
import sys
import termios
import time
from pathlib import Path

(
    ready_path,
    opened_path,
    log_path,
    encoded_environment,
    leak_path,
    input_json,
    input_error_path,
    control_root,
    suspend,
) = sys.argv[1:]
environment = (
    json.loads(base64.b64decode(encoded_environment).decode())
    if encoded_environment
    else {}
)
actions = json.loads(input_json)
finish_after_actions = suspend != "true"
secrets = tuple(
    value.encode()
    for value in environment.values()
    if isinstance(value, str) and value
)
key_bytes = {
    "enter": b"\r",
    "tab": b"\t",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "delete": b"\x1b[3~",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "page_up": b"\x1b[5~",
    "page_down": b"\x1b[6~",
}
ansi_pattern = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)


def visible_terminal_text(raw):
    text = []
    raw_ends = []
    cursor = 0
    for match in ansi_pattern.finditer(raw):
        for index in range(cursor, match.start()):
            text.append(raw[index])
            raw_ends.append(index + 1)
        cursor = match.end()
    for index in range(cursor, len(raw)):
        text.append(raw[index])
        raw_ends.append(index + 1)
    if len(text) > 65536:
        text = text[-65536:]
        raw_ends = raw_ends[-65536:]
    return "".join(text), raw_ends


master_fd, slave_fd = pty.openpty()
try:
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except OSError:
        size = os.terminal_size((80, 24))
    fcntl.ioctl(
        slave_fd,
        termios.TIOCSWINSZ,
        struct.pack("HHHH", size.lines, size.columns, 0, 0),
    )
    Path(ready_path).write_text(os.ttyname(slave_fd), encoding="utf-8")
    deadline = time.monotonic() + 10
    while not os.path.exists(opened_path):
        if time.monotonic() >= deadline:
            raise TimeoutError("realtime command did not open its terminal")
        time.sleep(0.01)
    os.close(slave_fd)
    slave_fd = -1
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    state = {"pending": b"", "leaked": False}
    visible_raw = ""
    action_index = 0
    action_started = time.monotonic()
    next_send = action_started
    pending_text = ""
    with open(log_path, "ab", buffering=0) as log:
        def emit(data):
            if not data:
                return
            log.write(data)
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

        def redact(data, *, final=False):
            state["pending"] += data
            output = bytearray()
            while state["pending"]:
                matches = []
                for secret in secrets:
                    index = state["pending"].find(secret)
                    if index >= 0:
                        matches.append((index, secret))
                if matches:
                    index, secret = min(matches, key=lambda item: item[0])
                    output.extend(state["pending"][:index])
                    output.extend(b"[REDACTED]")
                    state["pending"] = state["pending"][index + len(secret) :]
                    state["leaked"] = True
                    continue
                if final or not secrets:
                    output.extend(state["pending"])
                    state["pending"] = b""
                    break
                keep = 0
                for secret in secrets:
                    maximum = min(len(state["pending"]), len(secret) - 1)
                    for length in range(maximum, 0, -1):
                        if state["pending"].endswith(secret[:length]):
                            keep = max(keep, length)
                            break
                if len(state["pending"]) > keep:
                    output.extend(state["pending"][: len(state["pending"]) - keep])
                    state["pending"] = state["pending"][
                        len(state["pending"]) - keep :
                    ]
                break
            return bytes(output)

        command_closed = False
        input_seq = 1

        def respond(status, error=None):
            response_path = f"{control_root}.input-response-{input_seq}"
            payload = {"status": status}
            if error is not None:
                payload["error"] = str(error)
            temporary = response_path + ".tmp"
            Path(temporary).write_text(
                json.dumps(payload, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, response_path)

        while True:
            if not command_closed:
                readable, _, _ = select.select([master_fd], [], [], 0.01)
                if readable:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            command_closed = True
                            chunk = b""
                        else:
                            raise
                    if not chunk:
                        command_closed = True
                    else:
                        emit(redact(chunk))
                        visible_raw = (
                            visible_raw + chunk.decode("utf-8", errors="replace")
                        )[-131072:]
            else:
                time.sleep(0.01)

            now = time.monotonic()
            if action_index < len(actions):
                if command_closed:
                    raise RuntimeError(
                        f"terminal command exited before input step "
                        f"{action_index + 1} completed"
                    )
                if now < next_send:
                    continue
                action = actions[action_index]
                if action.get("wait_for") is not None:
                    timeout = action.get("timeout")
                    timeout = 10.0 if timeout is None else float(timeout)
                    visible_text, raw_ends = visible_terminal_text(visible_raw)
                    match_at = visible_text.find(action["wait_for"])
                    if match_at >= 0:
                        match_end = match_at + len(action["wait_for"])
                        visible_raw = visible_raw[raw_ends[match_end - 1] :]
                        action_index += 1
                        action_started = now
                    elif now - action_started >= timeout:
                        raise TimeoutError(
                            f"terminal input step {action_index + 1} timed out "
                            "waiting for terminal output"
                        )
                elif action.get("pause") is not None:
                    next_send = now + float(action["pause"])
                    action_index += 1
                    action_started = next_send
                elif action.get("text") is not None:
                    if not pending_text:
                        visible_raw = ""
                        pending_text = action["text"]
                    char, pending_text = pending_text[0], pending_text[1:]
                    os.write(
                        master_fd,
                        b"\r" if char == "\n" else char.encode("utf-8"),
                    )
                    interval = action.get("interval")
                    next_send = now + (
                        0.035 if interval is None else float(interval)
                    )
                    if not pending_text:
                        action_index += 1
                        action_started = now
                elif action.get("key") is not None:
                    visible_raw = ""
                    os.write(master_fd, key_bytes[action["key"]])
                    action_index += 1
                    action_started = now
                elif action.get("control") is not None:
                    visible_raw = ""
                    os.write(
                        master_fd,
                        bytes([ord(action["control"].lower()) & 0x1F]),
                    )
                    action_index += 1
                    action_started = now
                continue

            if finish_after_actions:
                if not command_closed:
                    continue
                respond("completed")
                break
            if command_closed:
                raise RuntimeError(
                    "terminal command exited before its continuation"
                )
            respond("suspended")
            input_seq += 1
            request_path = f"{control_root}.input-request-{input_seq}"
            while not os.path.exists(request_path):
                readable, _, _ = select.select([master_fd], [], [], 0.01)
                if readable:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            command_closed = True
                            chunk = b""
                        else:
                            raise
                    if not chunk:
                        command_closed = True
                    else:
                        emit(redact(chunk))
                        visible_raw = (
                            visible_raw + chunk.decode("utf-8", errors="replace")
                        )[-131072:]
                if command_closed:
                    break
            if not os.path.exists(request_path):
                continue
            request = json.loads(Path(request_path).read_text(encoding="utf-8"))
            actions = request["actions"]
            finish_after_actions = bool(request["finish"])
            action_index = 0
            action_started = time.monotonic()
            next_send = action_started
            pending_text = ""
            visible_raw = ""
        emit(redact(b"", final=True))
    if state["leaked"]:
        Path(leak_path).write_text("redacted\n", encoding="utf-8")
except Exception as exc:
    if "state" in locals() and state["leaked"]:
        Path(leak_path).write_text("redacted\n", encoding="utf-8")
    Path(input_error_path).write_text(f"{exc}\n", encoding="utf-8")
    if "input_seq" in locals():
        try:
            respond("error", exc)
        except Exception:
            pass
    raise
finally:
    if slave_fd >= 0:
        os.close(slave_fd)
    os.close(master_fd)
PY
    relay_pid=$!
    OMEGAFLOW_ACTIVE_RELAY_PID="$relay_pid"
    local ready_index
    for ((ready_index = 0; ready_index < 1000; ready_index += 1)); do
      [[ -s "$pty_ready" ]] && break
      kill -0 "$relay_pid" 2>/dev/null || break
      sleep 0.01
    done
    if [[ ! -s "$pty_ready" ]]; then
      kill "$relay_pid" 2>/dev/null || true
      wait "$relay_pid" 2>/dev/null || true
      kill "$status_monitor_pid" 2>/dev/null || true
      wait "$status_monitor_pid" 2>/dev/null || true
      rm -f \
        "$status_pipe" "$status_result" "$pty_ready" "$pty_opened" \
        "$input_error" "$cwd_result"
      omegaflow_reset_active_user_command
      return 125
    fi
    local pty_slave
    IFS= read -r pty_slave <"$pty_ready"
    printf 'exec 6<>%q\n: >%q\neval "$(%q -c %q %q)" <&6 >&6 2>&1\n__omegaflow_status=$?\npwd -P >%q\nprintf "%%s\\n" "$__omegaflow_status" >%q\nexec 6>&-\n' \
      "$pty_slave" "$pty_opened" "$OMEGAFLOW_PYTHON" "$decoder" \
      "$encoded_command" "$cwd_result" "$status_pipe" >&7 || true
    if ! omegaflow_wait_active_input_response; then
      omegaflow_finalize_active_user_command
      return 125
    fi
    if [[ "$suspend" == "true" ]]; then
      return 0
    fi
  else
    printf 'eval "$(%q -c %q %q)" >>%q 2>>%q\n__omegaflow_status=$?\npwd -P >%q\nprintf "%%s\\n" "$__omegaflow_status" >%q\n' \
      "$OMEGAFLOW_PYTHON" "$decoder" "$encoded_command" \
      "$stdout_target" "$stderr_target" "$cwd_result" "$status_pipe" \
      >&7 || true
  fi
  omegaflow_finalize_active_user_command
}

omegaflow_validate_no_secret_output() {
  local encoded_environment="$1"
  local stdout_target="$2"
  local stderr_target="$3"
  local stdout_start="$4"
  local stderr_start="$5"
  [[ -z "$encoded_environment" ]] && return 0
  "$OMEGAFLOW_PYTHON" - \
    "$encoded_environment" "$stdout_target" "$stderr_target" \
    "$stdout_start" "$stderr_start" <<'PY'
import base64
import json
import sys

encoded, stdout_path, stderr_path, stdout_start, stderr_start = sys.argv[1:]
environment = json.loads(base64.b64decode(encoded).decode())
secrets = tuple(
    value.encode()
    for value in environment.values()
    if isinstance(value, str) and value
)
paths = ((stdout_path, int(stdout_start)), (stderr_path, int(stderr_start)))
leaked = False
for path, offset in paths:
    with open(path, "rb+") as handle:
        handle.seek(offset)
        output = handle.read()
        if any(secret in output for secret in secrets):
            leaked = True
            handle.truncate(offset)
if leaked:
    with open(stderr_path, "ab") as handle:
        handle.write(b"recording secret appeared in terminal command output\n")
    raise SystemExit(1)
PY
}

omegaflow_emit_range() {
  "$OMEGAFLOW_PYTHON" - "$1" "$2" <<'PY'
import sys

path, offset = sys.argv[1], int(sys.argv[2])
with open(path, "rb") as handle:
    handle.seek(offset)
    sys.stdout.buffer.write(handle.read())
PY
}

omegaflow_prompt_text() {
  printf '$'
}

omegaflow_print_prompt() {
  local prompt
  prompt="$(omegaflow_prompt_text)"
  if [[ "$OMEGAFLOW_COLOR" == "1" ]]; then
    printf '\033[32;1m%s\033[0m ' "$prompt"
  else
    printf '%s ' "$prompt"
  fi
}

omegaflow_type_text() {
  local text="$1"
  local timing="$2"
  if [[ "$OMEGAFLOW_VISIBLE" != "1" || "$OMEGAFLOW_TYPING" != "1" || "$timing" != "realtime" ]]; then
    printf '%s' "$text"
    return
  fi
  "$OMEGAFLOW_PYTHON" - "$text" <<'PY'
import hashlib
import os
import random
import sys
import time

text = sys.argv[1]
minimum = float(os.environ["OMEGAFLOW_TYPING_MIN_DELAY"])
maximum = float(os.environ["OMEGAFLOW_TYPING_MAX_DELAY"])
space = float(os.environ["OMEGAFLOW_TYPING_SPACE_DELAY"])
punctuation = float(os.environ["OMEGAFLOW_TYPING_PUNCTUATION_DELAY"])
newline = float(os.environ["OMEGAFLOW_TYPING_NEWLINE_DELAY"])
seed = int(os.environ["OMEGAFLOW_TYPING_SEED"])
digest = hashlib.sha256(text.encode("utf-8")).digest()
rng = random.Random(seed ^ int.from_bytes(digest[:8], "big"))

for index, char in enumerate(text):
    sys.stdout.write(char)
    sys.stdout.flush()
    if index == len(text) - 1:
        continue
    delay = rng.uniform(minimum, maximum)
    if char == "\n":
        delay += newline + rng.uniform(0.0, newline / 2)
    elif char.isspace():
        delay += rng.uniform(0.0, space)
    elif char in "|;&":
        delay += punctuation + rng.uniform(0.0, punctuation)
    elif char == "\\":
        delay += newline / 2
    elif char in ",.:=/\"'{}[]()":
        delay += rng.uniform(0.0, punctuation)
    if char in " -_/" and rng.random() < 0.08:
        delay += rng.uniform(0.04, 0.12)
    time.sleep(delay)
PY
}

omegaflow_pause() {
  local duration="$1"
  local timing="$2"
  if [[ "$OMEGAFLOW_VISIBLE" == "1" && "$timing" == "realtime" && -n "$duration" && "$duration" != "0" ]]; then
    sleep "$duration"
  fi
}

omegaflow_print_command() {
  local display="$1"
  local pre_enter_pause="$2"
  local timing="$3"
  if [[ "$OMEGAFLOW_COLOR" == "1" ]]; then
    printf '\033[1m'
  fi
  omegaflow_type_text "$display" "$timing"
  omegaflow_pause "$pre_enter_pause" "$timing"
  if [[ "$OMEGAFLOW_COLOR" == "1" ]]; then
    printf '\033[0m'
  fi
  printf '\n'
}

omegaflow_begin_beat() {
  omegaflow_print_prompt
  OMEGAFLOW_PROMPT_VISIBLE=1
}

omegaflow_command_marker() {
  local phase="$1"
  printf '\033]1337;OmegaFlowCommand;%s;%s;%s\007' \
    "$OMEGAFLOW_ACTIVE_BEAT_ID" "$OMEGAFLOW_ACTIVE_ACTION_ID" "$phase"
}

omegaflow_validate_step() {
  "$OMEGAFLOW_PYTHON" - "$@" <<'PY'
import json
import os
import re
import sys

status = int(sys.argv[1])
expect = json.loads(sys.argv[2])
stdout_path, stderr_path = sys.argv[3], sys.argv[4]
stdout_offset, stderr_offset = int(sys.argv[5]), int(sys.argv[6])
with open(stdout_path, "rb") as handle:
    handle.seek(stdout_offset)
    stdout = handle.read().decode("utf-8", errors="replace")
with open(stderr_path, "rb") as handle:
    handle.seek(stderr_offset)
    stderr = handle.read().decode("utf-8", errors="replace")
combined = stdout + stderr

def fail(message):
    print(message, file=sys.stderr)
    with open(stderr_path, "a", encoding="utf-8") as handle:
        handle.write(message + "\\n")
    raise SystemExit(1)

expected_exit = expect.get("exit_code", 0)
if status != expected_exit:
    fail(f"terminal step exited {status}, expected {expected_exit}")
for text in expect.get("output_contains", []):
    if text not in combined:
        fail(f"terminal step output is missing text: {text}")
for pattern in expect.get("output_regex", []):
    if re.search(pattern, combined) is None:
        fail(f"terminal step output does not match: {pattern}")
for value in expect.get("file_exists", []):
    path = os.path.expanduser(os.path.expandvars(value))
    if not os.path.exists(path):
        fail(f"terminal step file is missing: {path}")
PY
}

omegaflow_validate_produces() {
  local producer_id="$1"
  local produces_json="$2"
  local command_cwd="$3"
  [[ "$produces_json" == "{}" ]] && return 0
  "$OMEGAFLOW_PYTHON" - \
    "$producer_id" "$produces_json" "$command_cwd" \
    "$OMEGAFLOW_PRODUCED_OUTPUTS" \
    "$OMEGAFLOW_TERMINAL_STDERR" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

producer_id, produces_json, command_cwd, log_path, stderr_path = sys.argv[1:]
produces = json.loads(produces_json)


def fail(message):
    print(message, file=sys.stderr)
    with open(stderr_path, "a", encoding="utf-8") as handle:
        handle.write(message + "\n")
    raise SystemExit(1)


def path_hash(path):
    if path.is_file():
        return "file", hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(b"directory\0")
    entries = sorted(
        path.rglob("*"),
        key=lambda item: item.relative_to(path).as_posix(),
    )
    for entry in entries:
        relative = entry.relative_to(path).as_posix().encode("utf-8")
        if entry.is_symlink():
            digest.update(b"link\0" + relative + b"\0")
            digest.update(os.readlink(entry).encode("utf-8") + b"\0")
        elif entry.is_dir():
            digest.update(b"dir\0" + relative + b"\0")
        elif entry.is_file():
            digest.update(b"file\0" + relative + b"\0")
            digest.update(entry.read_bytes())
            digest.update(b"\0")
    return "directory", digest.hexdigest()


records = []
for name, configured in produces.items():
    configured_path = Path(os.path.expandvars(configured)).expanduser()
    if configured_path.is_absolute():
        path = configured_path
    elif command_cwd:
        path = Path(command_cwd) / configured_path
    else:
        fail(
            f"terminal producer {producer_id!r} could not resolve the current "
            f"working directory for {name!r}: {configured_path}"
        )
    if not path.exists():
        fail(
            f"terminal producer {producer_id!r} did not create "
            f"{name!r}: {configured_path}"
        )
    if not path.is_file() and not path.is_dir():
        fail(
            f"terminal producer {producer_id!r} output {name!r} "
            f"is not a file or directory: {configured_path}"
        )
    kind, sha256 = path_hash(path)
    records.append(
        {
            "producer": producer_id,
            "output": name,
            "path": str(path.resolve()),
            "kind": kind,
            "sha256": sha256,
        }
    )
with open(log_path, "a", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
PY
}

omegaflow_run_step() {
  local command="$1"
  local expect="$2"
  local display="$3"
  local output_mode="$4"
  local replacement_output="$5"
  local show_prompt_after="$6"
  local timing="$7"
  local pre_command_pause="$8"
  local pre_enter_pause="$9"
  local post_enter_pause="${10}"
  local post_command_pause="${11}"
  local encoded_environment="${12}"
  local input_json="${13}"
  local producer_id="${14}"
  local produces_json="${15}"
  local continue_from="${16}"
  local suspend="${17}"
  local stdout_start
  local stderr_start
  local status
  local output_streamed=false
  stdout_start="$(wc -c <"$OMEGAFLOW_TERMINAL_STDOUT")"
  stderr_start="$(wc -c <"$OMEGAFLOW_TERMINAL_STDERR")"
  if [[ -n "$continue_from" ]]; then
    stdout_start="$OMEGAFLOW_ACTIVE_STDOUT_START"
    stderr_start="$OMEGAFLOW_ACTIVE_STDERR_START"
    omegaflow_continue_user_command "$input_json" "$suspend"
    status=$?
    output_streamed=true
  else
    if [[ "$OMEGAFLOW_PROMPT_VISIBLE" -ne 1 ]]; then
      omegaflow_print_prompt
    fi
    omegaflow_pause "$pre_command_pause" "$timing"
    omegaflow_command_marker typing_start
    omegaflow_print_command "$display" "$pre_enter_pause" "$timing"
    omegaflow_command_marker typing_end
    OMEGAFLOW_PROMPT_VISIBLE=0
  fi
  if [[ -z "$continue_from" && "$timing" == "realtime" && "$output_mode" == "real" ]]; then
    OMEGAFLOW_ACTIVE_STDOUT_START="$stdout_start"
    OMEGAFLOW_ACTIVE_STDERR_START="$stderr_start"
    omegaflow_run_user_command \
      "$command" "$OMEGAFLOW_TERMINAL_STDOUT" "$OMEGAFLOW_TERMINAL_STDERR" \
      realtime "$encoded_environment" "$input_json" "$suspend"
    status=$?
    output_streamed=true
  elif [[ -z "$continue_from" ]]; then
    omegaflow_run_user_command \
      "$command" "$OMEGAFLOW_TERMINAL_STDOUT" "$OMEGAFLOW_TERMINAL_STDERR" \
      presentation "$encoded_environment"
    status=$?
  fi
  if [[ "$suspend" == "true" ]]; then
    omegaflow_command_marker output_start
    omegaflow_command_marker output_end
    return "$status"
  fi
  if [[ -z "$continue_from" ]] && ! omegaflow_validate_no_secret_output \
    "$encoded_environment" \
    "$OMEGAFLOW_TERMINAL_STDOUT" "$OMEGAFLOW_TERMINAL_STDERR" \
    "$stdout_start" "$stderr_start"; then
    status=125
  fi
  if [[ -z "$post_enter_pause" ]]; then
    post_enter_pause="$OMEGAFLOW_POST_ENTER_PAUSE"
  fi
  omegaflow_pause "$post_enter_pause" "$timing"
  omegaflow_command_marker output_start
  if [[ "$output_mode" == "real" && "$output_streamed" != "true" ]]; then
    omegaflow_emit_range "$OMEGAFLOW_TERMINAL_STDOUT" "$stdout_start"
    omegaflow_emit_range "$OMEGAFLOW_TERMINAL_STDERR" "$stderr_start" >&2
  elif [[ "$output_mode" == "replace" && -n "$replacement_output" ]]; then
    printf '%s' "$replacement_output"
    if [[ "$replacement_output" != *$'\n' ]]; then
      printf '\n'
    fi
  fi
  if [[ "$show_prompt_after" == "true" ]]; then
    omegaflow_print_prompt
    OMEGAFLOW_PROMPT_VISIBLE=1
  fi
  omegaflow_command_marker output_end
  if [[ -z "$post_command_pause" ]]; then
    post_command_pause="$OMEGAFLOW_POST_COMMAND_PAUSE"
  fi
  omegaflow_pause "$post_command_pause" "$timing"
  omegaflow_validate_step "$status" "$expect" "$OMEGAFLOW_TERMINAL_STDOUT" "$OMEGAFLOW_TERMINAL_STDERR" "$stdout_start" "$stderr_start" || return $?
  omegaflow_validate_produces \
    "$producer_id" "$produces_json" "$OMEGAFLOW_LAST_COMMAND_CWD"
}

omegaflow_run_group() {
  local script="$1"
  local expect="$2"
  local stdout_start
  local stderr_start
  local status
  stdout_start="$(wc -c <"$OMEGAFLOW_TERMINAL_STDOUT")"
  stderr_start="$(wc -c <"$OMEGAFLOW_TERMINAL_STDERR")"
  eval "$script"
  status=$?
  omegaflow_validate_step "$status" "$expect" "$OMEGAFLOW_TERMINAL_STDOUT" "$OMEGAFLOW_TERMINAL_STDERR" "$stdout_start" "$stderr_start"
}

omegaflow_run_marked() {
  local beat_id="$1"
  local action_id="$2"
  local script="$3"
  local action_gate
  local status
  printf '{"seq":%s,"status":"action_ready","action_id":"%s"}\n' "$seq" "$action_id" >&9
  IFS= read -r action_gate <&8
  if [[ "$action_gate" != "continue:$seq:$action_id" ]]; then
    return 125
  fi
  printf '{"seq":%s,"status":"action_started","action_id":"%s"}\n' "$seq" "$action_id" >&9
  printf '\033]1337;OmegaFlowAction;%s;%s;start\007' "$beat_id" "$action_id"
  OMEGAFLOW_ACTIVE_BEAT_ID="$beat_id"
  OMEGAFLOW_ACTIVE_ACTION_ID="$action_id"
  eval "$script"
  status=$?
  OMEGAFLOW_ACTIVE_BEAT_ID=""
  OMEGAFLOW_ACTIVE_ACTION_ID=""
  printf '\033]1337;OmegaFlowAction;%s;%s;end\007' "$beat_id" "$action_id"
  if [[ "$status" -eq 0 ]]; then
    printf '{"seq":%s,"status":"action_completed","action_id":"%s"}\n' "$seq" "$action_id" >&9
  fi
  return "$status"
}

while IFS= read -r request <&8; do
  parsed=$("$OMEGAFLOW_PYTHON" - "$request" <<'PY'
import json
import shlex
import sys

allowed = {"setup", "beat", "checks", "cleanup", "shutdown"}
try:
    value = json.loads(sys.argv[1])
    seq = value["seq"]
    op = value["op"]
    beat_id = value.get("beat_id", "")
    script = value.get("script", "")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ValueError("seq must be a positive integer")
    if op not in allowed:
        raise ValueError("unsupported operation")
    if not isinstance(beat_id, str) or not isinstance(script, str):
        raise ValueError("beat_id and script must be strings")
except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
print("seq=" + shlex.quote(str(seq)))
print("op=" + shlex.quote(op))
print("beat_id=" + shlex.quote(beat_id))
print("script=" + shlex.quote(script))
PY
  )
  parse_status=$?
  if [[ "$parse_status" -ne 0 ]]; then
    printf '{"seq":-1,"status":"failed","error":"malformed request"}\n' >&9
    continue
  fi
  eval "$parsed"
  printf '{"seq":%s,"status":"started"}\n' "$seq" >&9
  if [[ "$op" == "shutdown" ]]; then
    printf '{"seq":%s,"status":"completed"}\n' "$seq" >&9
    break
  fi
  OMEGAFLOW_PROMPT_VISIBLE=0
  if [[ "$op" == "beat" ]]; then
    OMEGAFLOW_VISIBLE=1
  else
    OMEGAFLOW_VISIBLE=0
  fi
  printf '\033]1337;OmegaFlow;%s;%s;start;%s\007' "$seq" "$op" "$beat_id"
  eval "$script"
  status=$?
  printf '\033]1337;OmegaFlow;%s;%s;end;%s\007' "$seq" "$op" "$beat_id"
  if [[ "$status" -eq 0 ]]; then
    printf '{"seq":%s,"status":"completed"}\n' "$seq" >&9
  else
    printf '{"seq":%s,"status":"failed","error":"exit %s"}\n' "$seq" "$status" >&9
  fi
done
'''


class TerminalControlSession:
    """One request-at-a-time JSON protocol over private named pipes."""

    def __init__(
        self,
        context: CaptureContext,
        *,
        record_cast: bool = True,
        title: str = "OmegaFlow recording",
        window_size: str = "100x28",
        idle_time_limit: float | None = None,
        headless: bool = True,
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
        timeout_seconds: float = CONTROL_TIMEOUT_SECONDS,
    ) -> None:
        self.context = context
        self.record_cast = record_cast
        self.title = title
        self.window_size = window_size
        self.idle_time_limit = idle_time_limit
        self.headless = headless
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
        self.control_dir = context.runner_capture / ".terminal-control"
        self.request_path = self.control_dir / "requests.jsonl"
        self.response_path = self.control_dir / "responses.jsonl"
        self.script_path = self.control_dir / "session.sh"
        self.cast_path = context.runner_capture / "terminal.cast"
        self.output_path = context.runner_capture / "terminal.output.log"
        self.timeline_path = context.runner_capture / "terminal.timeline.jsonl"
        self._process: subprocess.Popen[bytes] | None = None
        self._request_fd: int | None = None
        self._response_fd: int | None = None
        self._response_buffer = b""
        self._seq = 0
        self._start_ns = 0
        self._closed = False

    @property
    def started(self) -> bool:
        return self._process is not None

    def start(self) -> None:
        if self.started:
            return
        if self._closed:
            raise TerminalCaptureError("terminal control session is already closed")
        self.control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.control_dir.chmod(0o700)
        for path in (self.request_path, self.response_path):
            if path.exists() or path.is_symlink():
                raise TerminalCaptureError(f"terminal control stream already exists: {path}")
            os.mkfifo(path, CONTROL_STREAM_MODE)
            path.chmod(CONTROL_STREAM_MODE)
        self.script_path.write_text(_session_script(), encoding="utf-8")
        self.script_path.chmod(0o700)
        self.timeline_path.write_text("", encoding="utf-8")
        environment = dict(self.context.environment)
        environment.update(
            {
                "OMEGAFLOW_REQUEST_STREAM": str(self.request_path),
                "OMEGAFLOW_RESPONSE_STREAM": str(self.response_path),
                "OMEGAFLOW_PYTHON": sys.executable,
                "OMEGAFLOW_COLOR": "1" if self.color else "0",
                "OMEGAFLOW_TYPING": "1" if self.typing else "0",
                "OMEGAFLOW_TYPING_MIN_DELAY": str(self.typing_min_delay),
                "OMEGAFLOW_TYPING_MAX_DELAY": str(self.typing_max_delay),
                "OMEGAFLOW_TYPING_SPACE_DELAY": str(self.typing_space_delay),
                "OMEGAFLOW_TYPING_PUNCTUATION_DELAY": str(
                    self.typing_punctuation_delay
                ),
                "OMEGAFLOW_TYPING_NEWLINE_DELAY": str(self.typing_newline_delay),
                "OMEGAFLOW_TYPING_SEED": str(self.typing_seed),
                "OMEGAFLOW_POST_ENTER_PAUSE": str(self.post_enter_pause),
                "OMEGAFLOW_POST_COMMAND_PAUSE": str(self.post_command_pause),
                "OMEGAFLOW_TERMINAL_STDOUT": str(
                    self.context.runner_capture / "terminal.stdout.log"
                ),
                "OMEGAFLOW_TERMINAL_STDERR": str(
                    self.context.runner_capture / "terminal.stderr.log"
                ),
                "OMEGAFLOW_PRODUCED_OUTPUTS": str(
                    self.context.runner_capture / "produced-outputs.jsonl"
                ),
            }
        )
        command: list[str]
        output_handle: Any = None
        if self.record_cast:
            command = [
                asciinema_command(),
                "record",
                "--quiet",
                "--overwrite",
                "--return",
            ]
            if self.headless:
                command.append("--headless")
            command.extend(
                [
                    "--window-size",
                    self.window_size,
                    "--title",
                    self.title,
                ]
            )
            if self.idle_time_limit is not None:
                command.extend(["--idle-time-limit", str(self.idle_time_limit)])
            command.extend(
                [
                    "--command",
                    f"bash {shlex.quote(str(self.script_path))}",
                    str(self.cast_path),
                ]
            )
            stdin: Any = subprocess.DEVNULL if self.headless else None
            stdout: Any = subprocess.DEVNULL if self.headless else None
            stderr: Any = subprocess.STDOUT if self.headless else None
        else:
            command = ["bash", str(self.script_path)]
            self.output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            output_handle = self.output_path.open("wb")
            stdin = subprocess.DEVNULL
            stdout = output_handle
            stderr = subprocess.STDOUT
        try:
            self._process = subprocess.Popen(
                command,
                cwd=self.context.working_directory,
                env=environment,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._remove_control_streams()
            raise TerminalCaptureError(
                f"could not start persistent terminal session: {exc}"
            ) from exc
        finally:
            if output_handle is not None:
                output_handle.close()
        try:
            self._request_fd = os.open(self.request_path, os.O_RDWR)
            self._response_fd = os.open(self.response_path, os.O_RDWR)
        except OSError as exc:
            self._terminate_process()
            self._remove_control_streams()
            raise TerminalCaptureError("could not open terminal control streams") from exc
        self._start_ns = time.monotonic_ns()
        self._append_event("session_start")

    def execute(
        self,
        op: str,
        *,
        script: str = "",
        beat_id: str = "",
        wait_indefinitely: bool = False,
        on_progress: Callable[[str, str], None] | None = None,
        before_action: Callable[[str], None] | None = None,
    ) -> tuple[int, int]:
        if op not in CONTROL_OPERATIONS or op == "shutdown":
            raise ValueError(f"invalid terminal execution operation: {op}")
        if not self.started or self._closed:
            raise TerminalCaptureError("terminal control session is not running")
        self._seq += 1
        seq = self._seq
        request = json.dumps(
            {"seq": seq, "op": op, "beat_id": beat_id, "script": script},
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        start_ms = self._elapsed_ms()
        phase = "beat" if op == "beat" else "hidden"
        self._append_event(f"{phase}_start", seq=seq, op=op, beat_id=beat_id)
        self._write_request(request)
        started = self._read_response(seq)
        if started.get("status") != "started":
            raise TerminalCaptureError(f"terminal request {seq} did not start")
        completed = self._read_response(
            seq,
            wait_indefinitely=wait_indefinitely,
            on_progress=on_progress,
            before_action=before_action,
        )
        end_ms = self._elapsed_ms()
        status = completed.get("status")
        self._append_event(
            f"{phase}_end",
            seq=seq,
            op=op,
            beat_id=beat_id,
            status=status,
        )
        if status != "completed":
            error = completed.get("error", "unknown terminal failure")
            stderr_path = self.context.runner_capture / "terminal.stderr.log"
            try:
                stderr_text = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                stderr_text = ""
            input_errors = re.findall(
                r"^terminal input failed: (.+)$",
                stderr_text,
                flags=re.MULTILINE,
            )
            if input_errors:
                error = input_errors[-1]
            produced_output_errors = re.findall(
                r"^(terminal producer .+ (?:did not create|is not) .+)$",
                stderr_text,
                flags=re.MULTILINE,
            )
            if produced_output_errors:
                error = produced_output_errors[-1]
            if "recording secret appeared in terminal command output" in stderr_text:
                error = "recording secret appeared in terminal command output"
            raise TerminalCaptureError(
                f"terminal {op} request {seq} failed for {beat_id or '<recording>'}: {error}"
            )
        return start_ms, end_ms

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            self._remove_control_streams()
            return
        shutdown_error: BaseException | None = None
        if process.poll() is None:
            try:
                self._seq += 1
                seq = self._seq
                self._write_request(
                    json.dumps(
                        {"seq": seq, "op": "shutdown", "beat_id": "", "script": ""},
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                if self._read_response(seq).get("status") != "started":
                    raise TerminalCaptureError("terminal shutdown did not start")
                if self._read_response(seq).get("status") != "completed":
                    raise TerminalCaptureError("terminal shutdown did not complete")
            except BaseException as exc:
                shutdown_error = exc
        try:
            process.wait(timeout=SHUTDOWN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._terminate_process()
            shutdown_error = shutdown_error or TerminalCaptureError(
                "terminal session exceeded shutdown grace period"
            )
        finally:
            self._append_event("session_end", returncode=process.poll())
            for fd in (self._request_fd, self._response_fd):
                if fd is not None:
                    os.close(fd)
            self._request_fd = None
            self._response_fd = None
            self._remove_control_streams()
        if shutdown_error is not None:
            raise TerminalCaptureError("persistent terminal shutdown failed") from shutdown_error
        if process.returncode != 0:
            raise TerminalCaptureError(
                f"persistent terminal session exited with status {process.returncode}"
            )

    def cancel(self) -> None:
        """Terminate the persistent shell without competing for its response stream."""

        self._terminate_process()

    def _write_request(self, request: bytes) -> None:
        if self._request_fd is None:
            raise TerminalCaptureError("terminal request stream is unavailable")
        try:
            os.write(self._request_fd, request)
        except OSError as exc:
            raise TerminalCaptureError("could not write terminal request") from exc

    def _read_response(
        self,
        seq: int,
        *,
        wait_indefinitely: bool = False,
        on_progress: Callable[[str, str], None] | None = None,
        before_action: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            line, separator, remainder = self._response_buffer.partition(b"\n")
            if separator:
                self._response_buffer = remainder
                try:
                    response = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TerminalCaptureError("terminal response is malformed") from exc
                if not isinstance(response, dict) or response.get("seq") != seq:
                    raise TerminalCaptureError(
                        f"terminal response sequence mismatch for request {seq}"
                    )
                status = response.get("status")
                if status == "action_ready":
                    action_id = response.get("action_id")
                    if not isinstance(action_id, str) or not action_id:
                        raise TerminalCaptureError(
                            "terminal action response is missing its action id"
                        )
                    if before_action is not None:
                        before_action(action_id)
                    self._write_request(
                        f"continue:{seq}:{action_id}\n".encode("utf-8")
                    )
                    deadline = time.monotonic() + self.timeout_seconds
                    continue
                if status in {"action_started", "action_completed"}:
                    action_id = response.get("action_id")
                    if not isinstance(action_id, str) or not action_id:
                        raise TerminalCaptureError(
                            "terminal action response is missing its action id"
                        )
                    if on_progress is not None:
                        on_progress(status.removeprefix("action_"), action_id)
                    continue
                return response
            process = self._process
            if process is None or process.poll() is not None:
                raise TerminalCaptureError("terminal session exited before responding")
            remaining = deadline - time.monotonic()
            if not wait_indefinitely and remaining <= 0:
                raise TerminalCaptureError(f"terminal request {seq} timed out")
            if self._response_fd is None:
                raise TerminalCaptureError("terminal response stream is unavailable")
            wait_seconds = 0.25 if wait_indefinitely else remaining
            readable, _, _ = select.select(
                [self._response_fd], [], [], wait_seconds
            )
            if not readable:
                if wait_indefinitely:
                    continue
                raise TerminalCaptureError(f"terminal request {seq} timed out")
            chunk = os.read(self._response_fd, 65536)
            if chunk:
                self._response_buffer += chunk

    def _elapsed_ms(self) -> int:
        return round((time.monotonic_ns() - self._start_ns) / 1_000_000)

    def _append_event(self, phase: str, **values: Any) -> None:
        event = {"time_ms": self._elapsed_ms() if self._start_ns else 0, "phase": phase}
        event.update({key: value for key, value in values.items() if value not in {None, ""}})
        with self.timeline_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")

    def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _remove_control_streams(self) -> None:
        for path in (self.request_path, self.response_path, self.script_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            self.control_dir.rmdir()
        except (FileNotFoundError, OSError):
            pass


class PersistentTerminalRunner:
    """Capture terminal beats in one shell and one optional asciinema process."""

    def __init__(
        self,
        *,
        record_cast: bool = True,
        title: str = "OmegaFlow recording",
        window_size: str = "100x28",
        idle_time_limit: float | None = None,
        headless: bool = True,
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
        timeout_seconds: float = CONTROL_TIMEOUT_SECONDS,
        delegated_environment: Mapping[str, str] | None = None,
        secret_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.record_cast = record_cast
        self.title = title
        self.window_size = window_size
        self.idle_time_limit = idle_time_limit
        self.headless = headless
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
        self.session: TerminalControlSession | None = None
        self._captured_beat_ids: list[str] = []
        self._command_snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        self._continuation_boundary_beat_ids: set[str] = set()

    def start(self, context: CaptureContext) -> None:
        if self.session is not None:
            return
        self.context = context
        session = TerminalControlSession(
            context,
            record_cast=self.record_cast,
            title=self.title,
            window_size=self.window_size,
            idle_time_limit=self.idle_time_limit,
            headless=self.headless,
            color=self.color,
            typing=self.typing,
            typing_min_delay=self.typing_min_delay,
            typing_max_delay=self.typing_max_delay,
            typing_space_delay=self.typing_space_delay,
            typing_punctuation_delay=self.typing_punctuation_delay,
            typing_newline_delay=self.typing_newline_delay,
            typing_seed=self.typing_seed,
            post_enter_pause=self.post_enter_pause,
            post_command_pause=self.post_command_pause,
            timeout_seconds=self.timeout_seconds,
        )
        session.start()
        self.session = session

    def run_setup(self, steps: Iterable[TerminalCheckPlan]) -> None:
        self._execute_steps("setup", steps)

    def run_cleanup(self, steps: Iterable[TerminalCheckPlan]) -> None:
        self._execute_steps("cleanup", steps)

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
        actions = tuple(
            action for action in beat.actions if isinstance(action, TerminalActionPlan)
        )
        checks = tuple(
            check for check in beat.checks if isinstance(check, TerminalCheckPlan)
        )
        self._captured_beat_ids.append(beat.id)
        if any(
            command.get("_continuation_boundary")
            for action in actions
            for command in (action.config.get("commands") or ())
        ):
            self._continuation_boundary_beat_ids.add(beat.id)
        command_snapshots = resolve_terminal_command_snapshots(
            actions,
            working_directory=self.context.working_directory,
            defaults=TerminalPresentationDefaults(
                color=self.color,
                typing=self.typing,
                typing_min_delay=self.typing_min_delay,
                typing_max_delay=self.typing_max_delay,
                typing_space_delay=self.typing_space_delay,
                typing_punctuation_delay=self.typing_punctuation_delay,
                typing_newline_delay=self.typing_newline_delay,
                typing_seed=self.typing_seed,
                post_enter_pause=self.post_enter_pause,
                post_command_pause=self.post_command_pause,
            ),
        )
        self._command_snapshots[beat.id] = command_snapshots
        start_ms, end_ms = session.execute(
            "beat",
            beat_id=beat.id,
            script=_beat_script(
                beat.id,
                actions,
                self.context,
                command_snapshots=command_snapshots,
                delegated_environment=self.delegated_environment,
                secret_environment=self.secret_environment,
            ),
            wait_indefinitely=_beat_has_browser_handoff(actions),
            on_progress=on_progress,
            before_action=before_action,
        )
        if checks:
            session.execute(
                "checks",
                beat_id=beat.id,
                script=_steps_script(
                    (check.config for check in checks),
                    self.context,
                    secret_environment=self.secret_environment,
                ),
            )
        beat_dir = self.context.paths.capture / "terminal-beats"
        beat_cast = beat_dir / f"{beat.id}.cast"
        action_timing = (
            beat_dir / f"{beat.id}.actions.json"
        )
        return BeatCapture(
            beat_id=beat.id,
            artifacts=(beat_cast, action_timing, session.timeline_path),
            metadata={"capture_start_ms": start_ms, "capture_end_ms": end_ms},
        )

    def close(self) -> None:
        session = self.session
        if session is None:
            return
        try:
            session.close()
            if session.record_cast:
                extract_terminal_beat_casts(
                    session.cast_path,
                    self.context.paths.capture / "terminal-beats",
                    expected_beat_ids=tuple(self._captured_beat_ids),
                    command_snapshots=self._command_snapshots,
                    boundary_state_beat_ids=self._continuation_boundary_beat_ids,
                )
        finally:
            self.session = None

    def cancel_capture(self) -> None:
        session = self.session
        if session is not None:
            session.cancel()

    def _execute_steps(self, op: str, steps: Iterable[TerminalCheckPlan]) -> None:
        configs = tuple(step.config for step in steps)
        if not configs:
            return
        session = self._require_session()
        for index, config in enumerate(configs, start=1):
            value = _thaw(config)
            configured_name = value.get("name")
            step_name = (
                configured_name
                if isinstance(configured_name, str) and configured_name
                else f"{op} step {index}"
            )
            try:
                session.execute(
                    op,
                    script=_steps_script(
                        (config,),
                        self.context,
                        secret_environment=self.secret_environment,
                    ),
                )
            except TerminalCaptureError as exc:
                raise TerminalLifecycleStepError(
                    op,
                    step_name,
                    index,
                    exc,
                    run_file=(
                        value.get("run_file")
                        if isinstance(value.get("run_file"), str)
                        else None
                    ),
                ) from exc

    def _require_session(self) -> TerminalControlSession:
        if self.session is None:
            raise TerminalCaptureError("persistent terminal runner is not started")
        return self.session


def _steps_script(
    configs: Iterable[FrozenMapping],
    context: CaptureContext | None,
    *,
    secret_environment: Mapping[str, str] | None = None,
) -> str:
    if context is None:
        raise TerminalCaptureError("terminal capture context is unavailable")
    commands: list[str] = []
    for config in configs:
        value = _thaw(config)
        command_entries = value.get("commands")
        if command_entries:
            command_script = " && ".join(
                _validated_step_script(
                    command,
                    context,
                    secret_environment=secret_environment,
                )
                for command in command_entries
            )
            commands.append(
                _validated_group_script(
                    command_script,
                    value.get("expect", {}),
                )
            )
        else:
            commands.append(
                _validated_step_script(
                    value,
                    context,
                    secret_environment=secret_environment,
                )
            )
    return " && ".join(commands)


def _beat_script(
    beat_id: str,
    actions: Iterable[TerminalActionPlan],
    context: CaptureContext | None,
    *,
    command_snapshots: Mapping[str, Mapping[str, Any]],
    delegated_environment: Mapping[str, str],
    secret_environment: Mapping[str, str],
) -> str:
    if context is None:
        raise TerminalCaptureError("terminal capture context is unavailable")
    steps: list[str] = []
    starts_with_continuation = False
    found_first_command = False
    for action_index, action in enumerate(actions):
        value = _thaw(action.config)
        command_entries = value.get("commands")
        if command_entries:
            commands: list[str] = []
            for command_index, command in enumerate(command_entries):
                if not found_first_command:
                    starts_with_continuation = bool(command.get("continue_from"))
                    found_first_command = True
                action_id = terminal_action_id(
                    action_index, command_index, command
                )
                commands.append(
                    _marked_step_script(
                        beat_id,
                        action_id,
                        _validated_step_script(
                            command,
                            context,
                            snapshot=command_snapshots[action_id],
                            delegated_environment=delegated_environment,
                            secret_environment=secret_environment,
                        ),
                    )
                )
            steps.append(
                _validated_group_script(" && ".join(commands), value.get("expect", {}))
            )
        else:
            if not found_first_command:
                starts_with_continuation = bool(value.get("continue_from"))
                found_first_command = True
            action_id = terminal_action_id(action_index, None)
            steps.append(
                _marked_step_script(
                    beat_id,
                    action_id,
                    _validated_step_script(
                        value,
                        context,
                        snapshot=command_snapshots[action_id],
                        delegated_environment=delegated_environment,
                        secret_environment=secret_environment,
                    ),
                )
            )
    if not steps:
        return "omegaflow_begin_beat"
    if starts_with_continuation:
        return " && ".join(steps)
    return "omegaflow_begin_beat && " + " && ".join(steps)


def _beat_has_browser_handoff(actions: Iterable[TerminalActionPlan]) -> bool:
    return any(
        command.get("browser_handoff")
        for action in actions
        for command in (action.config.get("commands") or ())
    )


def resolve_terminal_command_snapshots(
    actions: Iterable[TerminalActionPlan],
    *,
    working_directory: Path,
    defaults: TerminalPresentationDefaults,
) -> dict[str, dict[str, Any]]:
    """Resolve command execution and presentation settings from current config."""

    snapshots: dict[str, dict[str, Any]] = {}
    for action_index, action in enumerate(actions):
        value = _thaw(action.config)
        command_entries = value.get("commands")
        if command_entries:
            commands = enumerate(command_entries)
        else:
            commands = ((None, value),)
        for command_index, command in commands:
            action_id = terminal_action_id(action_index, command_index, command)
            continue_from = command.get("continue_from")
            command_text = (
                ""
                if continue_from
                else _step_command_in_directory(command, working_directory)
            )
            display = "" if continue_from else _step_display(command, command_text)
            timing = command.get("timing", "presentation")
            if timing not in {"presentation", "realtime"}:
                raise TerminalCaptureError(
                    "terminal step timing must be presentation or realtime"
                )
            snapshot: dict[str, Any] = {
                "command": command_text,
                "display": display,
                "timing": timing,
                "color": defaults.color,
                "typing": defaults.typing,
                "typing_min_delay": defaults.typing_min_delay,
                "typing_max_delay": defaults.typing_max_delay,
                "typing_space_delay": defaults.typing_space_delay,
                "typing_punctuation_delay": defaults.typing_punctuation_delay,
                "typing_newline_delay": defaults.typing_newline_delay,
                "typing_seed": defaults.typing_seed,
                "pre_command_pause": _resolved_pause(
                    command, "pre_command_pause", 0.0
                ),
                "pre_enter_pause": _resolved_pause(
                    command, "pre_enter_pause", 0.0
                ),
                "post_enter_pause": _resolved_pause(
                    command,
                    "post_enter_pause",
                    defaults.post_enter_pause,
                ),
                "post_command_pause": _resolved_pause(
                    command,
                    "post_command_pause",
                    defaults.post_command_pause,
                ),
                "show_prompt_after": command.get("show_prompt_after", True),
            }
            if timing == "presentation":
                typing_seconds = (
                    sum(
                        terminal_typing_delays(
                            display,
                            minimum=defaults.typing_min_delay,
                            maximum=defaults.typing_max_delay,
                            space=defaults.typing_space_delay,
                            punctuation=defaults.typing_punctuation_delay,
                            newline=defaults.typing_newline_delay,
                            seed=defaults.typing_seed,
                        )
                    )
                    if defaults.typing
                    else 0.0
                )
                snapshot["presentation_duration_ms"] = round(
                    1000
                    * (
                        snapshot["pre_command_pause"]
                        + typing_seconds
                        + snapshot["pre_enter_pause"]
                        + snapshot["post_enter_pause"]
                        + snapshot["post_command_pause"]
                    )
                )
            snapshots[action_id] = snapshot
    return snapshots


def _marked_step_script(beat_id: str, action_id: str, script: str) -> str:
    return "omegaflow_run_marked " + " ".join(
        shlex.quote(value) for value in (beat_id, action_id, script)
    )


def _validated_step_script(
    step: Mapping[str, Any],
    context: CaptureContext,
    *,
    snapshot: Mapping[str, Any] | None = None,
    delegated_environment: Mapping[str, str] | None = None,
    secret_environment: Mapping[str, str] | None = None,
) -> str:
    continue_from = step.get("continue_from")
    command = (
        snapshot["command"]
        if snapshot is not None
        else "" if continue_from else _step_command(step, context)
    )
    if step.get("browser_handoff"):
        handoff_id = step.get("id")
        if not isinstance(handoff_id, str) or not handoff_id:
            raise TerminalCaptureError("browser handoff command requires an id")
        command = (
            "(export OMEGAFLOW_BROWSER_HANDOFF_ID="
            + shlex.quote(handoff_id)
            + "; "
            + command
            + ")"
        )
    display = (
        snapshot["display"]
        if snapshot is not None
        else _step_display(step, command)
    )
    expect = step.get("_continuation_expect", step.get("expect", {}))
    if not isinstance(expect, dict):
        raise TerminalCaptureError("terminal step expect must be a mapping")
    _validate_expect(expect)
    try:
        output = command_output_config(dict(step), field="terminal step")
    except RecordingError as exc:
        raise TerminalCaptureError(str(exc)) from exc
    show_prompt_after = (
        snapshot["show_prompt_after"]
        if snapshot is not None
        else step.get("show_prompt_after", True)
    )
    if not isinstance(show_prompt_after, bool):
        raise TerminalCaptureError("terminal step show_prompt_after must be a boolean")
    timing = (
        snapshot["timing"]
        if snapshot is not None
        else step.get("timing", "presentation")
    )
    if timing not in {"presentation", "realtime"}:
        raise TerminalCaptureError(
            "terminal step timing must be presentation or realtime"
        )
    pause_fields = (
        "pre_command_pause",
        "pre_enter_pause",
        "post_enter_pause",
        "post_command_pause",
    )
    pauses = (
        tuple(str(snapshot[field]) for field in pause_fields)
        if snapshot is not None
        else tuple(_optional_pause(step, field) for field in pause_fields)
    )
    requested_environment = step.get("with_env", [])
    if not isinstance(requested_environment, (list, tuple)) or any(
        not isinstance(name, str) or not name for name in requested_environment
    ):
        raise TerminalCaptureError(
            "terminal step with_env must be a list of non-empty strings"
        )
    available_environment = delegated_environment or {}
    missing = [
        name for name in requested_environment if name not in available_environment
    ]
    if missing:
        raise TerminalCaptureError(
            "terminal step could not resolve scoped environment name "
            + repr(missing[0])
        )
    scoped_environment = dict(secret_environment or {})
    scoped_environment.update(
        {name: available_environment[name] for name in requested_environment}
    )
    encoded_environment = (
        base64.b64encode(
            json.dumps(
                scoped_environment,
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        if scoped_environment
        else ""
    )
    input_steps = step.get("input", [])
    if not isinstance(input_steps, (list, tuple)):
        raise TerminalCaptureError("terminal step input must be a list")
    if input_steps and timing != "realtime":
        raise TerminalCaptureError("terminal step input requires realtime timing")
    if input_steps and output["mode"] != "real":
        raise TerminalCaptureError("terminal step input requires output: real")
    input_json = json.dumps(_thaw(input_steps), separators=(",", ":"))
    produces = step.get("_continuation_produces", step.get("produces", {}))
    if not isinstance(produces, Mapping):
        raise TerminalCaptureError("terminal step produces must be a mapping")
    producer_id = step.get(
        "_continuation_producer_id",
        step.get("id", ""),
    )
    suspend = bool(step.get("_suspend_for_continuation"))
    return "omegaflow_run_step " + " ".join(
        shlex.quote(value)
        for value in (
            command,
            json.dumps(expect, separators=(",", ":")),
            display,
            output["mode"],
            output["replace"],
            "true" if show_prompt_after else "false",
            timing,
            *pauses,
            encoded_environment,
            input_json,
            str(producer_id or ""),
            json.dumps(_thaw(produces), separators=(",", ":")),
            str(continue_from or ""),
            "true" if suspend else "false",
        )
    )


def _optional_pause(step: Mapping[str, Any], field: str) -> str:
    value = step.get(field)
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise TerminalCaptureError(f"terminal step {field} must be non-negative")
    return str(value)


def _resolved_pause(
    step: Mapping[str, Any],
    field: str,
    default: float,
) -> float:
    value = step.get(field, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise TerminalCaptureError(f"terminal step {field} must be non-negative")
    return float(value)


def _step_display(step: Mapping[str, Any], command: str) -> str:
    display = step.get("display")
    if display is None:
        display = command
    if not isinstance(display, str) or not display:
        raise TerminalCaptureError("terminal step display must be a non-empty string")
    return display


def _validated_group_script(script: str, expect: object) -> str:
    if not isinstance(expect, dict):
        raise TerminalCaptureError("terminal action expect must be a mapping")
    _validate_expect(expect)
    return "omegaflow_run_group " + shlex.quote(script) + " " + shlex.quote(
        json.dumps(expect, separators=(",", ":"))
    )


def _validate_expect(expect: Mapping[str, Any]) -> None:
    unknown = set(expect) - {
        "exit_code",
        "output_contains",
        "output_regex",
        "file_exists",
    }
    if unknown:
        raise TerminalCaptureError(
            "terminal expect has unknown fields: " + ", ".join(sorted(unknown))
        )
    exit_code = expect.get("exit_code", 0)
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise TerminalCaptureError("terminal expect.exit_code must be an integer")
    for field in ("output_contains", "output_regex", "file_exists"):
        values = expect.get(field, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise TerminalCaptureError(
                f"terminal expect.{field} must be a list of non-empty strings"
            )
        if field == "output_regex":
            for value in values:
                try:
                    re.compile(value)
                except re.error as exc:
                    raise TerminalCaptureError(
                        f"terminal expect.output_regex is invalid: {exc}"
                    ) from exc


def _step_command(step: Mapping[str, Any], context: CaptureContext) -> str:
    return _step_command_in_directory(step, context.working_directory)


def _step_command_in_directory(
    step: Mapping[str, Any], working_directory: Path
) -> str:
    run = step.get("run")
    run_file = step.get("run_file")
    if isinstance(run, str) and run:
        return run
    if isinstance(run_file, str) and run_file:
        path = Path(run_file).expanduser()
        if not path.is_absolute():
            path = working_directory / path
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TerminalCaptureError(f"could not read terminal run_file: {path}") from exc
    raise TerminalCaptureError("terminal step must contain run or run_file")


def _thaw(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def extract_terminal_beat_casts(
    cast_path: Path,
    output_dir: Path,
    *,
    expected_beat_ids: tuple[str, ...],
    command_snapshots: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    boundary_state_beat_ids: Iterable[str] = (),
) -> dict[str, Path]:
    """Split the physical persistent cast into beat-local, zero-based casts."""

    try:
        lines = cast_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TerminalCaptureError(f"could not read terminal cast: {cast_path}") from exc
    if not lines:
        raise TerminalCaptureError(f"terminal cast is empty: {cast_path}")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise TerminalCaptureError(f"terminal cast header is invalid: {cast_path}") from exc
    if not isinstance(header, dict):
        raise TerminalCaptureError(f"terminal cast header is not a mapping: {cast_path}")

    expected = list(expected_beat_ids)
    expected_set = set(expected)
    boundary_state_set = set(boundary_state_beat_ids)
    if len(expected_set) != len(expected):
        raise TerminalCaptureError("terminal beat extraction received duplicate beat ids")
    unknown_boundaries = boundary_state_set - expected_set
    if unknown_boundaries:
        raise TerminalCaptureError(
            "terminal state boundaries reference unknown beat ids: "
            + repr(sorted(unknown_boundaries))
        )
    captured: dict[str, list[list[Any]]] = {}
    action_timings: dict[str, list[dict[str, Any]]] = {}
    previous_times: dict[str, float] = {}
    beat_origins: dict[str, float] = {}
    active: str | None = None
    active_action: tuple[str, str, float, int] | None = None
    active_command_boundaries: dict[str, int] = {}
    ended_handoffs: set[tuple[str, str]] = set()
    boundary_output: dict[str, list[str]] = {}
    output_history: list[str] = []
    output_history_bytes = 0
    absolute_time = 0.0

    def append_output_history(chunk: str) -> None:
        nonlocal output_history_bytes
        if not boundary_state_set:
            return
        output_history_bytes += len(chunk.encode("utf-8"))
        if output_history_bytes > TERMINAL_BOUNDARY_OUTPUT_BYTE_LIMIT:
            raise TerminalCaptureError(
                "terminal boundary output exceeds "
                f"{TERMINAL_BOUNDARY_OUTPUT_BYTE_LIMIT} bytes"
            )
        output_history.append(chunk)

    def append_event(beat_id: str, timestamp: float, kind: Any, data: Any) -> None:
        previous = previous_times[beat_id]
        delta = round(max(0.0, timestamp - previous), 6)
        captured[beat_id].append([delta, kind, data])
        previous_times[beat_id] = timestamp

    def handle_beat_marker(match: re.Match[str]) -> None:
        nonlocal active
        op = match.group("op")
        phase = match.group("phase")
        beat_id = match.group("beat")
        if op != "beat":
            if active is not None:
                raise TerminalCaptureError(
                    f"hidden terminal interval overlaps beat {active!r}"
                )
            return
        if beat_id not in expected_set:
            raise TerminalCaptureError(
                f"terminal cast contains unexpected beat marker {beat_id!r}"
            )
        if phase == "start":
            if active is not None or beat_id in captured:
                raise TerminalCaptureError(
                    f"terminal beat {beat_id!r} has duplicate or nested start marker"
                )
            active = beat_id
            if beat_id in boundary_state_set:
                if not output_history:
                    raise TerminalCaptureError(
                        f"terminal beat {beat_id!r} has no prior terminal state"
                    )
                boundary_output[beat_id] = list(output_history)
            captured[beat_id] = []
            action_timings[beat_id] = []
            previous_times[beat_id] = absolute_time
            beat_origins[beat_id] = absolute_time
            return
        if active is None and any(item[0] == beat_id for item in ended_handoffs):
            return
        if active != beat_id:
            raise TerminalCaptureError(
                f"terminal beat {beat_id!r} end marker does not match active beat"
            )
        if active_action is not None:
            raise TerminalCaptureError(
                f"terminal action {active_action[1]!r} has no end marker"
            )
        active = None

    def handle_action_marker(match: re.Match[str]) -> None:
        nonlocal active_action, active_command_boundaries
        beat_id = match.group("beat")
        action_id = match.group("action")
        phase = match.group("phase")
        if (beat_id, action_id) in ended_handoffs and phase == "end":
            return
        if active != beat_id:
            raise TerminalCaptureError(
                f"terminal action marker {beat_id!r}/{action_id!r} is outside its beat"
            )
        if phase == "start":
            if active_action is not None:
                raise TerminalCaptureError(
                    f"terminal action {action_id!r} starts inside {active_action[1]!r}"
                )
            if any(item["id"] == action_id for item in action_timings[beat_id]):
                raise TerminalCaptureError(
                    f"terminal action {action_id!r} has a duplicate start marker"
                )
            active_action = (
                beat_id,
                action_id,
                absolute_time,
                len(captured[beat_id]),
            )
            active_command_boundaries = {}
            return
        if active_action is None or active_action[:2] != (beat_id, action_id):
            raise TerminalCaptureError(
                f"terminal action {beat_id!r}/{action_id!r} end marker does not match"
            )
        finish_active_action()

    def handle_command_marker(match: re.Match[str]) -> None:
        beat_id = match.group("beat")
        action_id = match.group("action")
        phase = match.group("phase")
        if (beat_id, action_id) in ended_handoffs:
            return
        if active_action is None or active_action[:2] != (beat_id, action_id):
            raise TerminalCaptureError(
                f"terminal command marker {beat_id!r}/{action_id!r} "
                "is outside its action"
            )
        expected_phases = (
            "typing_start",
            "typing_end",
            "output_start",
            "output_end",
        )
        if phase in active_command_boundaries:
            raise TerminalCaptureError(
                f"terminal command marker {beat_id!r}/{action_id!r}/{phase!r} "
                "is duplicated"
            )
        phase_index = expected_phases.index(phase)
        if any(
            expected_phases.index(previous) > phase_index
            for previous in active_command_boundaries
        ):
            raise TerminalCaptureError(
                f"terminal command markers for {beat_id!r}/{action_id!r} "
                "are out of order"
            )
        active_command_boundaries[phase] = len(captured[beat_id])

    def finish_active_action() -> None:
        nonlocal active_action, active_command_boundaries
        if active_action is None:
            raise TerminalCaptureError("terminal action has no active interval")
        beat_id, action_id, start, event_start = active_action
        origin = beat_origins[beat_id]
        start_ms = round((start - origin) * 1000)
        end_ms = round((absolute_time - origin) * 1000)
        action_timings[beat_id].append(
            {
                "id": action_id,
                "capture_start_ms": start_ms,
                "capture_end_ms": end_ms,
                "event_indexes": {
                    "action_start": event_start,
                    **active_command_boundaries,
                    "action_end": len(captured[beat_id]),
                },
            }
        )
        active_action = None
        active_command_boundaries = {}

    def handle_handoff_marker(match: re.Match[str]) -> None:
        nonlocal active
        action_id = match.group("action")
        if active is None or active_action is None:
            raise TerminalCaptureError(
                f"browser handoff {action_id!r} is outside a terminal action"
            )
        beat_id = active
        if active_action[:2] != (beat_id, action_id):
            raise TerminalCaptureError(
                f"browser handoff {action_id!r} does not match active terminal action"
            )
        finish_active_action()
        ended_handoffs.add((beat_id, action_id))
        active = None

    for line_number, line in enumerate(lines[1:], 2):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TerminalCaptureError(
                f"invalid terminal cast event at {cast_path}:{line_number}"
            ) from exc
        if (
            not isinstance(event, list)
            or len(event) != 3
            or isinstance(event[0], bool)
            or not isinstance(event[0], (int, float))
        ):
            raise TerminalCaptureError(
                f"invalid terminal cast event at {cast_path}:{line_number}"
            )
        absolute_time += float(event[0])
        kind = event[1]
        data = event[2]
        if kind != "o" or not isinstance(data, str):
            if active is not None:
                append_event(active, absolute_time, kind, data)
            continue
        cursor = 0
        for marker in TERMINAL_ANY_MARKER_RE.finditer(data):
            prefix = data[cursor : marker.start()]
            if prefix:
                if active is not None:
                    append_event(active, absolute_time, kind, prefix)
                append_output_history(prefix)
            beat_marker = TERMINAL_MARKER_RE.fullmatch(marker.group(0))
            if beat_marker is not None:
                handle_beat_marker(beat_marker)
            else:
                action_marker = TERMINAL_ACTION_MARKER_RE.fullmatch(marker.group(0))
                if action_marker is not None:
                    handle_action_marker(action_marker)
                else:
                    command_marker = TERMINAL_COMMAND_MARKER_RE.fullmatch(
                        marker.group(0)
                    )
                    if command_marker is not None:
                        handle_command_marker(command_marker)
                    else:
                        handoff_marker = (
                            TERMINAL_BROWSER_HANDOFF_MARKER_RE.fullmatch(
                                marker.group(0)
                            )
                        )
                        if handoff_marker is None:
                            raise TerminalCaptureError(
                                "terminal cast marker is malformed"
                            )
                        handle_handoff_marker(handoff_marker)
            cursor = marker.end()
        suffix = data[cursor:]
        if suffix:
            if active is not None:
                append_event(active, absolute_time, kind, suffix)
            append_output_history(suffix)
    if active is not None:
        raise TerminalCaptureError(f"terminal beat {active!r} has no end marker")
    if list(captured) != expected:
        raise TerminalCaptureError(
            "terminal beat markers do not match source order: "
            f"expected {expected!r}, found {list(captured)!r}"
        )

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    outputs: dict[str, Path] = {}
    for beat_id in expected:
        output = output_dir / f"{beat_id}.cast"
        beat_header = dict(header)
        if beat_id in boundary_output:
            beat_header["omegaflow_boundary_output"] = boundary_output[beat_id]
        header_line = json.dumps(beat_header, separators=(",", ":"))
        event_lines = [json.dumps(event, separators=(",", ":")) for event in captured[beat_id]]
        output.write_text(
            "\n".join([header_line, *event_lines]) + "\n",
            encoding="utf-8",
        )
        timing_output = output_dir / f"{beat_id}.actions.json"
        timing_actions = action_timings[beat_id]
        if command_snapshots is not None:
            beat_snapshots = command_snapshots.get(beat_id, {})
            timing_ids = {item["id"] for item in timing_actions}
            if set(beat_snapshots) != timing_ids:
                raise TerminalCaptureError(
                    f"terminal command snapshots do not match beat {beat_id!r} actions"
                )
            timing_actions = [
                {
                    **item,
                    "timing": beat_snapshots[item["id"]]["timing"],
                    "presentation_snapshot": {
                        field: beat_snapshots[item["id"]][field]
                        for field in TERMINAL_PRESENTATION_SNAPSHOT_FIELDS
                    },
                }
                for item in timing_actions
            ]
        timing_output.write_text(
            json.dumps(
                {
                    "version": 1,
                    "beat_id": beat_id,
                    "actions": timing_actions,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs[beat_id] = output
    return outputs
