#!/usr/bin/env python3
"""Testing-only split-screen frontend for the awsh prototype."""

from __future__ import annotations

import argparse
import errno
import glob
import os
import pty
import readline
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path
from typing import BinaryIO


AWSH = Path(__file__).with_name("awsh")
SCHEMA = "awsh-v1"
REQUEST_FD = 20
RESULT_FD = 21
POST_COMPLETION_QUIET_SECONDS = 0.05
POST_COMPLETION_DRAIN_SECONDS = 0.25
EVENT_FIELDS = {
    "ready": ("pid", "cwd"),
    "started": ("operation_id",),
    "completed": ("operation_id", "status", "cwd"),
    "protocol_error": ("code", "message"),
    "closed": ("reason", "cwd"),
}


class ProtocolError(RuntimeError):
    """The private awsh result stream was malformed."""


class EventDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._fields: list[str] = []
        self._closed = False

    def feed(self, chunk: bytes) -> list[dict[str, str]]:
        self._buffer.extend(chunk)
        while b"\0" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\0")
            self._buffer = bytearray(remainder)
            try:
                self._fields.append(raw.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise ProtocolError("result field is not valid UTF-8") from exc

        events: list[dict[str, str]] = []
        while len(self._fields) >= 2:
            schema, kind = self._fields[:2]
            if schema != SCHEMA:
                raise ProtocolError(f"unexpected result schema: {schema!r}")
            names = EVENT_FIELDS.get(kind)
            if names is None:
                raise ProtocolError(f"unexpected result kind: {kind!r}")
            field_count = 2 + len(names)
            if len(self._fields) < field_count:
                break
            values = self._fields[2:field_count]
            del self._fields[:field_count]
            event = {"type": kind, **dict(zip(names, values, strict=True))}
            events.append(event)
            if kind == "closed":
                self._closed = True
        return events

    def finish(self) -> None:
        if self._buffer or self._fields:
            raise ProtocolError("result stream ended inside an event")
        if not self._closed:
            raise ProtocolError("result stream ended before a closed event")


class AwshSession:
    def __init__(self) -> None:
        request_read, request_write = os.pipe()
        result_read, result_write = os.pipe()
        pid, terminal_master = pty.fork()
        if pid == 0:
            try:
                os.close(request_write)
                os.close(result_read)
                os.dup2(request_read, REQUEST_FD)
                os.dup2(result_write, RESULT_FD)
                if request_read not in {REQUEST_FD, RESULT_FD}:
                    os.close(request_read)
                if result_write not in {REQUEST_FD, RESULT_FD}:
                    os.close(result_write)
                os.execv(
                    AWSH,
                    [
                        str(AWSH),
                        "--request-fd",
                        str(REQUEST_FD),
                        "--result-fd",
                        str(RESULT_FD),
                    ],
                )
            except BaseException:
                os._exit(127)

        os.close(request_read)
        os.close(result_write)
        self.pid = pid
        self.request_write = request_write
        self.result_read = result_read
        self.terminal_master = terminal_master
        self.result_open = True
        self.terminal_open = True
        self._waited = False
        self._decoder = EventDecoder()

    @property
    def readable_fds(self) -> list[int]:
        result = []
        if self.terminal_open:
            result.append(self.terminal_master)
        if self.result_open:
            result.append(self.result_read)
        return result

    def send(self, kind: str, *fields: str) -> None:
        payload = b"\0".join(
            value.encode("utf-8") for value in (SCHEMA, kind, *fields)
        ) + b"\0"
        while payload:
            written = os.write(self.request_write, payload)
            payload = payload[written:]

    def read_terminal(self) -> bytes:
        try:
            chunk = os.read(self.terminal_master, 65536)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            chunk = b""
        if not chunk:
            self.terminal_open = False
        return chunk

    def read_events(self) -> list[dict[str, str]]:
        chunk = os.read(self.result_read, 65536)
        if chunk:
            return self._decoder.feed(chunk)
        self.result_open = False
        self._decoder.finish()
        return []

    def resize(self, source_fd: int) -> None:
        size = termios.tcgetwinsize(source_fd)
        termios.tcsetwinsize(self.terminal_master, size)

    def wait(self, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while True:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self._waited = True
                return os.waitstatus_to_exitcode(status)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

    def close(self) -> None:
        for descriptor in (
            self.request_write,
            self.result_read,
            self.terminal_master,
        ):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if self._waited:
            return
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if self.wait(0.5) is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.wait(0.5)


class EventLog:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._started = time.monotonic()

    def write(self, event: dict[str, str]) -> None:
        elapsed = time.monotonic() - self._started
        details = " ".join(
            f"{name}={shlex.quote(value)}"
            for name, value in event.items()
            if name != "type"
        )
        line = f"{elapsed:8.3f}  {event['type']:<14} {details}\n"
        self._stream.write(line.encode("utf-8"))
        self._stream.flush()

    def diagnostic(self, message: str) -> None:
        elapsed = time.monotonic() - self._started
        self._stream.write(f"{elapsed:8.3f}  wrapper        {message}\n".encode())
        self._stream.flush()

    def request(self, operation_id: str, source: str) -> None:
        elapsed = time.monotonic() - self._started
        details = (
            f"operation_id={shlex.quote(operation_id)} "
            f"source={shlex.quote(source)}"
        )
        self._stream.write(f"{elapsed:8.3f}  request        {details}\n".encode())
        self._stream.flush()


def _write_all(descriptor: int, payload: bytes) -> None:
    while payload:
        written = os.write(descriptor, payload)
        payload = payload[written:]


def _complete_path(text: str, state: int, cwd: str) -> str | None:
    expanded = os.path.expanduser(text)
    relative = not os.path.isabs(expanded)
    pattern = os.path.join(cwd, expanded) if relative else expanded
    matches = sorted(glob.glob(pattern + "*"))
    candidates = []
    for match in matches:
        if text.startswith("~"):
            home = str(Path.home())
            candidate = "~" + match[len(home) :]
        elif relative:
            candidate = os.path.relpath(match, cwd)
            if text.startswith("." + os.sep) and not candidate.startswith("."):
                candidate = "." + os.sep + candidate
        else:
            candidate = match
        if os.path.isdir(match):
            candidate += os.sep
        candidates.append(candidate)
    return candidates[state] if state < len(candidates) else None


def _relay_descriptors(
    session: AwshSession, input_fd: int, completed: bool
) -> list[int]:
    descriptors = list(session.readable_fds)
    if not completed:
        descriptors.append(input_fd)
    return descriptors


def _completion_drain_finished(
    completed: bool,
    quiet_since: float | None,
    drain_deadline: float | None,
    now: float,
) -> bool:
    if not completed:
        return False
    if drain_deadline is not None and now >= drain_deadline:
        return True
    return (
        quiet_since is not None
        and now - quiet_since >= POST_COMPLETION_QUIET_SECONDS
    )


def _prompt(cwd: str) -> str:
    home = str(Path.home())
    if cwd == home or cwd.startswith(home + os.sep):
        shown = "~" + cwd[len(home) :]
    else:
        shown = cwd
    return f"awsh:{shown}$ "


def _read_ready(session: AwshSession, event_log: EventLog) -> str:
    while session.result_open:
        readable, _, _ = select.select(session.readable_fds, [], [], 2.0)
        if not readable:
            raise TimeoutError("awsh did not emit ready")
        for descriptor in readable:
            if descriptor == session.terminal_master:
                _write_all(sys.stdout.fileno(), session.read_terminal())
                continue
            for event in session.read_events():
                event_log.write(event)
                if event["type"] == "ready":
                    return event["cwd"]
    raise EOFError("awsh result stream closed before ready")


def _relay_operation(
    session: AwshSession,
    event_log: EventLog,
    operation_id: str,
    cwd: str,
) -> tuple[str, bool]:
    input_fd = sys.stdin.fileno()
    output_fd = sys.stdout.fileno()
    previous_settings = termios.tcgetattr(input_fd)
    completed = False
    closed = False
    quiet_since: float | None = None
    drain_deadline: float | None = None

    try:
        tty.setraw(input_fd)
        session.resize(input_fd)
        while session.result_open and not closed:
            now = time.monotonic()
            if completed and drain_deadline is None:
                drain_deadline = now + POST_COMPLETION_DRAIN_SECONDS
            if _completion_drain_finished(
                completed, quiet_since, drain_deadline, now
            ):
                break
            descriptors = _relay_descriptors(session, input_fd, completed)
            timeout = POST_COMPLETION_QUIET_SECONDS
            if drain_deadline is not None:
                timeout = min(timeout, max(0.0, drain_deadline - now))
            readable, _, _ = select.select(descriptors, [], [], timeout)
            session.resize(input_fd)
            if not readable and completed:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= 0.05:
                    break
                continue
            if readable:
                quiet_since = None
            for descriptor in readable:
                if descriptor == input_fd:
                    if completed:
                        continue
                    chunk = os.read(input_fd, 4096)
                    if chunk and session.terminal_open:
                        _write_all(session.terminal_master, chunk)
                elif descriptor == session.terminal_master:
                    chunk = session.read_terminal()
                    if chunk:
                        _write_all(output_fd, chunk)
                else:
                    for event in session.read_events():
                        event_log.write(event)
                        if event["type"] == "completed":
                            cwd = event["cwd"]
                            if event["operation_id"] == operation_id:
                                completed = True
                        elif event["type"] == "closed":
                            cwd = event["cwd"]
                            closed = True
        return cwd, closed or not session.result_open
    finally:
        termios.tcsetattr(input_fd, termios.TCSADRAIN, previous_settings)


def _shutdown(session: AwshSession, event_log: EventLog) -> None:
    if not session.result_open:
        raise ProtocolError("awsh result stream closed before shutdown")
    try:
        session.send("shutdown")
    except OSError as exc:
        raise ProtocolError("could not send shutdown request") from exc
    deadline = time.monotonic() + 1.0
    while session.result_open and time.monotonic() < deadline:
        readable, _, _ = select.select(session.readable_fds, [], [], 0.1)
        for descriptor in readable:
            if descriptor == session.terminal_master:
                chunk = session.read_terminal()
                if chunk:
                    _write_all(sys.stdout.fileno(), chunk)
            else:
                for event in session.read_events():
                    event_log.write(event)
                    if event["type"] == "closed":
                        return
    raise TimeoutError("awsh did not acknowledge shutdown")


def run_controller(event_path: Path, done_path: Path) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("awsh demo controller requires a terminal", file=sys.stderr)
        return 64

    readline.parse_and_bind("set editing-mode emacs")
    readline.parse_and_bind("tab: complete")
    readline.set_completer_delims(" \t\n;|&()<>")
    session: AwshSession | None = None
    try:
        with event_path.open("ab", buffering=0) as event_stream:
            event_log = EventLog(event_stream)
            session = AwshSession()
            cwd = _read_ready(session, event_log)
            readline.set_completer(
                lambda text, state: _complete_path(text, state, cwd)
            )
            operation_number = 0
            print("testing console: Enter submits; Ctrl-D exits; Ctrl-C interrupts")

            while session.result_open:
                try:
                    source = input(_prompt(cwd))
                except KeyboardInterrupt:
                    print()
                    continue
                except EOFError:
                    print()
                    _shutdown(session, event_log)
                    break
                if not source.strip():
                    continue

                operation_number += 1
                operation_id = f"demo-{operation_number}"
                try:
                    event_log.request(operation_id, source)
                    session.send("execute", operation_id, source)
                except OSError as exc:
                    event_log.diagnostic(f"request write failed: {exc}")
                    break
                cwd, closed = _relay_operation(
                    session, event_log, operation_id, cwd
                )
                if closed:
                    break
            return_code = session.wait(0.5)
            if return_code is None:
                raise TimeoutError("awsh did not exit after closing")
            return return_code
    except (EOFError, OSError, ProtocolError, TimeoutError) as exc:
        print(f"awsh demo: {exc}", file=sys.stderr)
        return 74
    finally:
        if session is not None:
            session.close()
        done_path.touch()


def watch_events(event_path: Path, done_path: Path) -> int:
    with event_path.open("r", encoding="utf-8") as stream:
        while True:
            line = stream.readline()
            if line:
                print(line, end="", flush=True)
                continue
            if done_path.exists():
                remainder = stream.read()
                if remainder:
                    print(remainder, end="", flush=True)
                return 0
            time.sleep(0.05)


def launch_split_screen() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("awsh demo requires an interactive terminal", file=sys.stderr)
        return 64
    if shutil.which("tmux") is None:
        print("awsh demo requires tmux", file=sys.stderr)
        return 69

    script = Path(__file__).resolve()
    session_name = f"awsh-demo-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="awsh-demo-") as temporary_directory:
        event_path = Path(temporary_directory, "events.log")
        done_path = Path(temporary_directory, "done")
        event_path.touch()
        controller = shlex.join(
            [
                sys.executable,
                str(script),
                "--controller",
                str(event_path),
                str(done_path),
            ]
        )
        viewer = shlex.join(
            [
                sys.executable,
                str(script),
                "--events",
                str(event_path),
                str(done_path),
            ]
        )
        try:
            subprocess.run(
                [
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-c",
                    os.getcwd(),
                    controller,
                ],
                check=True,
            )
            subprocess.run(
                [
                    "tmux",
                    "split-window",
                    "-h",
                    "-l",
                    "34%",
                    "-t",
                    f"{session_name}:0",
                    "-c",
                    os.getcwd(),
                    viewer,
                ],
                check=True,
            )
            subprocess.run(
                ["tmux", "set-option", "-t", session_name, "status", "off"],
                check=True,
            )
            subprocess.run(
                [
                    "tmux",
                    "set-window-option",
                    "-t",
                    session_name,
                    "pane-border-status",
                    "top",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "tmux",
                    "set-window-option",
                    "-t",
                    session_name,
                    "window-style",
                    "fg=colour245,bg=colour235",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "tmux",
                    "set-window-option",
                    "-t",
                    session_name,
                    "window-active-style",
                    "fg=default,bg=default",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "tmux",
                    "select-pane",
                    "-t",
                    f"{session_name}:0.0",
                    "-T",
                    "shell / PTY",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "tmux",
                    "select-pane",
                    "-t",
                    f"{session_name}:0.1",
                    "-T",
                    "events",
                ],
                check=True,
            )
            subprocess.run(
                ["tmux", "select-pane", "-t", f"{session_name}:0.0"],
                check=True,
            )
            environment = os.environ.copy()
            environment.pop("TMUX", None)
            completed = subprocess.run(
                ["tmux", "attach-session", "-t", session_name], env=environment
            )
            return completed.returncode
        except subprocess.CalledProcessError as exc:
            print(f"awsh demo: tmux setup failed: {exc}", file=sys.stderr)
            return 74
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open the testing-only awsh split-screen console."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--controller",
        nargs=2,
        metavar=("EVENT_LOG", "DONE_FILE"),
        help=argparse.SUPPRESS,
    )
    modes.add_argument(
        "--events",
        nargs=2,
        metavar=("EVENT_LOG", "DONE_FILE"),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.controller:
        return run_controller(*(Path(value) for value in arguments.controller))
    if arguments.events:
        return watch_events(*(Path(value) for value in arguments.events))
    return launch_split_screen()


if __name__ == "__main__":
    raise SystemExit(main())
