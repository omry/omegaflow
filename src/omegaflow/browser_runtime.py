"""Pinned browser runtime metadata and preflight helpers."""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLAYWRIGHT_PACKAGE_VERSION = "1.61.0"
CHROMIUM_REVISION = "1228"
CHROMIUM_BROWSER_VERSION = "149.0.7827.55"


class BrowserRuntimeError(RuntimeError):
    """Raised when the pinned browser runtime is unavailable or inconsistent."""


@dataclass(frozen=True)
class BrowserRuntime:
    playwright_version: str
    chromium_revision: str
    chromium_version: str
    executable_path: Path | None = None


def _playwright_browser_manifest() -> dict[str, Any]:
    try:
        package_root = Path(importlib.metadata.distribution("playwright").locate_file(""))
    except importlib.metadata.PackageNotFoundError as exc:
        raise BrowserRuntimeError(
            "browser recording requires the 'browser' extra: "
            "install OmegaFlow with `pip install 'omegaflow[browser]'`"
        ) from exc
    path = package_root / "playwright" / "driver" / "package" / "browsers.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserRuntimeError("could not read Playwright browser metadata") from exc
    if not isinstance(value, dict):
        raise BrowserRuntimeError("Playwright browser metadata is invalid")
    return value


def pinned_browser_runtime() -> BrowserRuntime:
    try:
        installed_version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError as exc:
        raise BrowserRuntimeError(
            "browser recording requires the 'browser' extra: "
            "install OmegaFlow with `pip install 'omegaflow[browser]'`"
        ) from exc
    if installed_version != PLAYWRIGHT_PACKAGE_VERSION:
        raise BrowserRuntimeError(
            "browser recording requires Playwright "
            f"{PLAYWRIGHT_PACKAGE_VERSION}, found {installed_version}"
        )
    browsers = _playwright_browser_manifest().get("browsers")
    if not isinstance(browsers, list):
        raise BrowserRuntimeError("Playwright browser metadata has no browser list")
    chromium = next(
        (
            browser
            for browser in browsers
            if isinstance(browser, dict) and browser.get("name") == "chromium"
        ),
        None,
    )
    if chromium is None:
        raise BrowserRuntimeError("Playwright browser metadata has no Chromium entry")
    revision = str(chromium.get("revision", ""))
    version = str(chromium.get("browserVersion", ""))
    if revision != CHROMIUM_REVISION or version != CHROMIUM_BROWSER_VERSION:
        raise BrowserRuntimeError(
            "Playwright browser metadata does not match OmegaFlow's pinned Chromium: "
            f"expected revision {CHROMIUM_REVISION} ({CHROMIUM_BROWSER_VERSION}), "
            f"found revision {revision} ({version})"
        )
    return BrowserRuntime(
        playwright_version=installed_version,
        chromium_revision=revision,
        chromium_version=version,
    )
