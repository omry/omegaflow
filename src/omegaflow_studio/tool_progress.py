"""Structured progress events shared by OmegaFlow tools."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .terminal_style import (
    ANSI_CYAN_BOLD,
    ANSI_DIM,
    ANSI_GREEN_BOLD,
    ANSI_RED_BOLD,
    ANSI_YELLOW_BOLD,
    color_enabled,
    color_text,
    formatted_status,
)


PROGRESS_PIPE_ENV = "OMEGAFLOW_STUDIO_PROGRESS_PIPE"
STATUS_COLORS = {
    "check": ANSI_CYAN_BOLD,
    "cmd": ANSI_GREEN_BOLD,
    "fail": ANSI_RED_BOLD,
    "info": ANSI_CYAN_BOLD,
    "pass": ANSI_GREEN_BOLD,
    "skip": ANSI_YELLOW_BOLD,
    "step": ANSI_CYAN_BOLD,
    "warn": ANSI_YELLOW_BOLD,
}


def progress_pipes_supported() -> bool:
    return hasattr(os, "mkfifo")


def write_progress_event(
    event: dict[str, Any],
    *,
    pipe_path: str | Path | None = None,
) -> bool:
    raw_path = str(pipe_path or os.environ.get(PROGRESS_PIPE_ENV, ""))
    if not raw_path:
        return False
    line = json.dumps(event, sort_keys=True) + "\n"
    try:
        fd = os.open(raw_path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, line.encode("utf-8"))
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


class ProgressPipeReporter:
    def __init__(
        self,
        path: Path,
        *,
        enabled: bool,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._anchor_fd: int | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        if not progress_pipes_supported():
            raise RuntimeError("progress pipes require os.mkfifo support")
        if self.path.exists():
            self.path.unlink()
        try:
            os.mkfifo(self.path)
            self._anchor_fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            if self.path.exists():
                self.path.unlink()
            raise
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._close_anchor()
        self._unblock_reader()
        self._thread.join(timeout=1.0)
        if self.path.exists():
            self.path.unlink()

    def _close_anchor(self) -> None:
        if self._anchor_fd is None:
            return
        try:
            os.close(self._anchor_fd)
        except OSError:
            pass
        self._anchor_fd = None

    def _unblock_reader(self) -> None:
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return
        try:
            os.close(fd)
        except OSError:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if self._stop.is_set():
                            break
                        self._emit_line(line)
            except OSError:
                return

    def _emit_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            self.on_event(event)


def status_color(status: str) -> str:
    return STATUS_COLORS.get(status, ANSI_CYAN_BOLD)


class LogProgressRenderer:
    def __init__(
        self,
        *,
        stream: Any | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.stream = stream
        self.enabled = enabled

    def emit(self, event: dict[str, Any]) -> None:
        if event.get("phase") != "status":
            return
        status = event.get("status")
        message = event.get("message")
        if not isinstance(status, str) or not isinstance(message, str):
            return
        color = event.get("_color")
        if not isinstance(color, str):
            color = status_color(status)
        enabled = event.get("_enabled", self.enabled)
        if not isinstance(enabled, bool):
            enabled = self.enabled
        print(
            formatted_status(
                status,
                message,
                color=color,
                enabled=enabled,
            ),
            file=self.stream or sys.stdout,
            flush=True,
        )


class ProgressBarRenderer:
    def __init__(
        self,
        *,
        stream: Any | None = None,
        width: int = 28,
        columns: int | None = None,
        interactive: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.stream = stream
        self.width = width
        self.columns = columns
        self.interactive = interactive
        self.enabled = enabled
        self._rendered = False
        self._event_count = 0
        self._events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        if not isinstance(message, str) or not message:
            return
        status = event.get("status")
        if not isinstance(status, str) or not status:
            status = "step"
        current = event.get("current")
        total = event.get("total")
        current_int = current if isinstance(current, int) and current >= 0 else None
        total_int = total if isinstance(total, int) and total > 0 else None
        self._event_count += 1
        self._events.append(dict(event))
        if not self._interactive():
            return
        self._render(
            message=message,
            status=status,
            current=current_int,
            total=total_int,
        )

    def finish(self, *, replay: bool = False) -> None:
        stream = self.stream or sys.stdout
        if self._rendered:
            stream.write("\x1b[2F\x1b[2M")
            self._rendered = False
        if replay:
            renderer = LogProgressRenderer(stream=stream, enabled=self.enabled)
            for event in self._events:
                renderer.emit(event)
        stream.flush()

    def _interactive(self) -> bool:
        if self.interactive is not None:
            return self.interactive
        stream = self.stream or sys.stdout
        isatty = getattr(stream, "isatty", None)
        return bool(isatty and isatty())

    def _render(
        self,
        *,
        message: str,
        status: str,
        current: int | None,
        total: int | None,
    ) -> None:
        stream = self.stream or sys.stdout
        enabled = color_enabled(stream) if self.enabled is None else self.enabled
        if self._rendered:
            stream.write("\x1b[2F")
        columns = self._terminal_columns()
        detail = self._detail(current=current, total=total)
        bar_width = self._bar_width(columns=columns, detail=detail)
        bar = self._bar(
            current=current,
            total=total,
            width=bar_width,
            enabled=enabled,
        )
        label = color_text("progress", ANSI_CYAN_BOLD, enabled=enabled)
        detail_text = f" {detail}" if detail else ""
        current_label = color_text("current ", ANSI_DIM, enabled=enabled)
        message = self._truncate(message, max(1, columns - len("current ")))
        message_text = color_text(message, status_color(status), enabled=enabled)
        stream.write(f"\r\x1b[2K{label} {bar}{detail_text}\n")
        stream.write(f"\r\x1b[2K{current_label}{message_text}\n")
        stream.flush()
        self._rendered = True

    def _terminal_columns(self) -> int:
        if self.columns is not None:
            return max(20, self.columns)
        return max(20, shutil.get_terminal_size(fallback=(80, 24)).columns)

    def _bar_width(self, *, columns: int, detail: str) -> int:
        fixed_width = len("progress ") + len("[]")
        if detail:
            fixed_width += 1 + len(detail)
        return min(self.width, max(4, columns - fixed_width))

    def _bar(
        self,
        *,
        current: int | None,
        total: int | None,
        width: int,
        enabled: bool,
    ) -> str:
        if current is not None and total is not None:
            clamped = min(max(current, 0), total)
            filled = round(width * clamped / total)
        else:
            filled = self._event_count % (width + 1)
            if filled == 0:
                filled = 1
        filled_text = "█" * filled
        empty_text = "░" * (width - filled)
        text = f"[{filled_text}{empty_text}]"
        return color_text(text, ANSI_GREEN_BOLD, enabled=enabled)

    @staticmethod
    def _detail(*, current: int | None, total: int | None) -> str:
        if current is not None and total is not None:
            return f"{min(current, total)}/{total}"
        return ""

    @staticmethod
    def _truncate(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 3:
            return "." * width
        return text[: width - 3] + "..."


class ToolProgress:
    def __init__(
        self,
        tool: str,
        *,
        renderer: LogProgressRenderer | ProgressBarRenderer | None = None,
    ) -> None:
        self.tool = tool
        self.renderer = renderer or LogProgressRenderer()

    @contextmanager
    def use_renderer(
        self,
        renderer: LogProgressRenderer | ProgressBarRenderer,
    ) -> Iterator[None]:
        previous = self.renderer
        self.renderer = renderer
        try:
            yield
        finally:
            self.renderer = previous

    def status(
        self,
        status: str,
        message: str,
        *,
        color: str,
        enabled: bool | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        event = {
            "message": message,
            "phase": "status",
            "status": status,
            "tool": self.tool,
        }
        if current is not None:
            event["current"] = current
        if total is not None:
            event["total"] = total
        if write_progress_event(event):
            return
        event["_color"] = color
        event["_enabled"] = enabled
        self.renderer.emit(event)
