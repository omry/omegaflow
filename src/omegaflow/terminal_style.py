"""Shared terminal color helpers for OmegaFlow tools."""

from __future__ import annotations

import os
import sys
from typing import Any

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_BLUE_BOLD = "\033[34;1m"
ANSI_RED_BOLD = "\033[31;1m"
ANSI_GREEN_BOLD = "\033[32;1m"
ANSI_YELLOW_BOLD = "\033[33;1m"
ANSI_CYAN_BOLD = "\033[36;1m"


def color_enabled(stream: Any = sys.stdout) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    color = os.environ.get("FORCE_COLOR") or os.environ.get("OMEGAFLOW_COLOR")
    if color and color.lower() not in {"0", "false", "no", "never"}:
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def color_text(text: str, color: str, *, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = color_enabled()
    if not enabled:
        return text
    return f"{color}{text}{ANSI_RESET}"


def formatted_status(
    status: str,
    message: str,
    *,
    color: str,
    enabled: bool | None = None,
) -> str:
    return f"{color_text(f'{status:<5}', color, enabled=enabled)} {message}"


def print_status(status: str, message: str, *, color: str) -> None:
    print(formatted_status(status, message, color=color))
