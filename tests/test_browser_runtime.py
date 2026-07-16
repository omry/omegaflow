from __future__ import annotations

import importlib.metadata
import subprocess

import pytest

from omegaflow.browser_runtime import (
    CHROMIUM_BROWSER_VERSION,
    CHROMIUM_REVISION,
    PLAYWRIGHT_PACKAGE_VERSION,
    BrowserRuntimeError,
    actionable_playwright_error,
    pinned_browser_runtime,
    require_browser_media_runtime,
)


def test_browser_runtime_matches_pinned_playwright_metadata() -> None:
    try:
        importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("Playwright is an optional browser-recording dependency")

    runtime = pinned_browser_runtime()

    assert runtime.playwright_version == PLAYWRIGHT_PACKAGE_VERSION
    assert runtime.chromium_revision == CHROMIUM_REVISION
    assert runtime.chromium_version == CHROMIUM_BROWSER_VERSION


def test_playwright_launch_errors_have_actionable_remedies() -> None:
    assert "playwright install chromium" in actionable_playwright_error(
        "Executable doesn't exist at /missing/chrome"
    )
    assert "playwright install-deps chromium" in actionable_playwright_error(
        "Host system is missing dependencies to run browsers"
    )


def test_media_runtime_reports_missing_tools_and_codecs() -> None:
    with pytest.raises(BrowserRuntimeError, match="ffprobe"):
        require_browser_media_runtime(
            which=lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
        )

    def run_without_h264(*args, **kwargs):
        command = args[0]
        output = " V..... libwebp WebP image\n" if "-encoders" in command else "ffprobe"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    with pytest.raises(BrowserRuntimeError, match="libx264"):
        require_browser_media_runtime(
            require_h264=True,
            which=lambda name: f"/usr/bin/{name}",
            run=run_without_h264,
        )


def test_installed_media_runtime_has_selected_encoders() -> None:
    runtime = require_browser_media_runtime(require_h264=True)

    assert runtime.webp_encoder == "libwebp"
    assert runtime.h264_encoder == "libx264"
