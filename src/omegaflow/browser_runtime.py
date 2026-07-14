"""Pinned browser runtime metadata and preflight helpers."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
from dataclasses import dataclass
from collections.abc import Callable
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


@dataclass(frozen=True)
class BrowserMediaRuntime:
    ffmpeg: str
    ffprobe: str
    webp_encoder: str
    vp8_encoder: str | None


def actionable_playwright_error(message: str) -> str:
    """Turn Playwright launch failures into stable installation guidance."""

    lower = message.lower()
    if "executable doesn't exist" in lower or "browser was not found" in lower:
        return (
            "pinned Chromium is not installed; run "
            "`python -m playwright install chromium`"
        )
    platform_markers = (
        "missing dependencies",
        "missing libraries",
        "error while loading shared libraries",
        "host system is missing",
    )
    if any(marker in lower for marker in platform_markers):
        return (
            "pinned Chromium cannot start because platform libraries are missing; "
            "run `python -m playwright install-deps chromium` (or install the "
            "equivalent packages for this platform)"
        )
    if "no usable sandbox" in lower or "sandbox" in lower and "failed" in lower:
        return (
            "pinned Chromium cannot start with the host sandbox configuration; "
            "use a supported container/host setup rather than disabling the sandbox"
        )
    return message


def require_browser_media_runtime(
    *,
    require_vp8: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> BrowserMediaRuntime:
    """Require the codecs used by stable states and dynamic fragments."""

    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")
    missing = [name for name, value in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if value is None]
    if missing:
        raise BrowserRuntimeError(
            "browser presentation media requires "
            + " and ".join(missing)
            + "; install an ffmpeg build with libwebp"
            + (" and libvpx" if require_vp8 else "")
        )
    result = run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BrowserRuntimeError(
            "could not inspect ffmpeg encoders; verify the ffmpeg installation"
        )
    encoders = result.stdout + "\n" + result.stderr
    if not re_search_encoder(encoders, "libwebp"):
        raise BrowserRuntimeError(
            "browser state publication requires an ffmpeg build with the libwebp encoder"
        )
    vp8 = "libvpx" if re_search_encoder(encoders, "libvpx") else None
    if require_vp8 and vp8 is None:
        raise BrowserRuntimeError(
            "captured browser motion requires an ffmpeg build with the libvpx VP8 encoder"
        )
    probe = run(
        [ffprobe, "-version"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        raise BrowserRuntimeError(
            "browser presentation validation requires a working ffprobe executable"
        )
    return BrowserMediaRuntime(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        webp_encoder="libwebp",
        vp8_encoder=vp8,
    )


def re_search_encoder(output: str, name: str) -> bool:
    return any(
        line.strip().split(maxsplit=2)[1:2] == [name]
        for line in output.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )


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
