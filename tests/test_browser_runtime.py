from __future__ import annotations

import importlib.metadata

import pytest

from omegaflow.browser_runtime import (
    CHROMIUM_BROWSER_VERSION,
    CHROMIUM_REVISION,
    PLAYWRIGHT_PACKAGE_VERSION,
    pinned_browser_runtime,
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
