"""Private file-based handoff between a blocking CLI and browser capture."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


BROWSER_HANDOFF_ROOT_ENV = "OMEGAFLOW_BROWSER_HANDOFF_ROOT"
BROWSER_HANDOFF_ID_ENV = "OMEGAFLOW_BROWSER_HANDOFF_ID"
BROWSER_HANDOFF_MARKER = "OmegaFlowBrowserHandoff"
_HANDOFF_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")


def _validated_id(value: str) -> str:
    if not _HANDOFF_ID_RE.fullmatch(value):
        raise ValueError("browser handoff id must be identifier-like")
    return value


def _secure_directory(path: Path, *, create: bool) -> None:
    if path.is_symlink():
        raise RuntimeError(f"browser handoff directory must not be a symlink: {path}")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError(f"browser handoff directory is missing: {path}")
    path.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"browser handoff directory is not private: {path}")


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, (json.dumps(dict(payload), separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)


class BrowserHandoffBroker:
    """Recorder-side owner of private handoff channels."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().absolute()

    def _channel(self, handoff_id: str) -> Path:
        return self.root / _validated_id(handoff_id)

    def prepare(self, handoff_id: str) -> None:
        _validated_id(handoff_id)
        _secure_directory(self.root, create=True)
        channel = self._channel(handoff_id)
        if channel.is_symlink():
            raise RuntimeError(
                f"browser handoff channel must not be a symlink: {channel}"
            )
        channel.mkdir(mode=0o700)
        _secure_directory(channel, create=False)

    def ready_url(self, handoff_id: str) -> str | None:
        channel = self._channel(handoff_id)
        _secure_directory(channel, create=False)
        if not (channel / "ready.json").is_file():
            return None
        request_path = channel / "request.json"
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("browser handoff request is unreadable") from exc
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url:
            raise RuntimeError("browser handoff request has no URL")
        return url

    def close(self, handoff_id: str) -> None:
        channel = self._channel(handoff_id)
        _secure_directory(channel, create=False)
        path = channel / "closed.json"
        if not path.exists():
            _write_exclusive(path, {"closed": True})


class BrokeredBrowserSession:
    """Watch-side browser session whose lifetime is owned by the recorder."""

    def __init__(self, *, channel: Path, handoff_id: str, url: str) -> None:
        self.channel = channel
        self.handoff_id = handoff_id
        self._announced = False
        _secure_directory(channel, create=False)
        _write_exclusive(channel / "request.json", {"url": url})

    @classmethod
    def from_environment(
        cls,
        url: str,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> BrokeredBrowserSession | None:
        values = os.environ if environment is None else environment
        root = values.get(BROWSER_HANDOFF_ROOT_ENV)
        handoff_id = values.get(BROWSER_HANDOFF_ID_ENV)
        if not root and not handoff_id:
            return None
        if not root or not handoff_id:
            raise RuntimeError("browser handoff environment is incomplete")
        validated = _validated_id(handoff_id)
        return cls(
            channel=Path(root).expanduser().absolute() / validated,
            handoff_id=validated,
            url=url,
        )

    def is_open(self) -> bool:
        if not self._announced:
            _write_exclusive(self.channel / "ready.json", {"ready": True})
            sys.stdout.write(
                f"\x1b]1337;{BROWSER_HANDOFF_MARKER};{self.handoff_id};ready\x07"
            )
            sys.stdout.flush()
            self._announced = True
        return not (self.channel / "closed.json").is_file()

    def close(self) -> None:
        return None
