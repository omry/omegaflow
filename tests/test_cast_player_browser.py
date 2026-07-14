from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return


@contextmanager
def player_site(root: Path):
    handler = lambda *args, **kwargs: QuietStaticHandler(  # noqa: E731
        *args, directory=root, **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def write_browser_player_fixture(root: Path) -> None:
    shutil.copy2(
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html",
        root / "cast-player.html",
    )
    shutil.copy2(
        REPO_ROOT / "src/omegaflow/player/static/cast-player-core.js",
        root / "cast-player-core.js",
    )
    (root / "beats").mkdir()
    image = (
        "data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='240'%3E"
        "%3Crect width='400' height='240' fill='%23252d3d'/%3E%3C/svg%3E"
    )
    payload = {
        "payload_version": 1,
        "beat_id": "browser",
        "duration_ms": 1200,
        "viewport": {"width": 400, "height": 240, "device_scale_factor": 1},
        "initial_state": "initial",
        "initial_pointer": {"x": 20, "y": 20, "visible": True},
        "initial_display_url": "https://public.example/demo",
        "animation_policies": {"pointer": "pointer-v1", "typing": "natural-v1"},
        "events": [
            {
                "kind": "pointer_move",
                "action_id": "move",
                "at_ms": 100,
                "end_ms": 500,
                "start": {"x": 20, "y": 20},
                "end": {"x": 200, "y": 120},
                "curve": {"x1": 60, "y1": 20, "x2": 160, "y2": 100},
            },
            {
                "kind": "display_url",
                "action_id": "move",
                "at_ms": 500,
                "end_ms": 500,
                "value": "https://public.example/finished",
            },
        ],
    }
    (root / "beats/browser.browser.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    manifest = {
        "manifest_version": 1,
        "recording": {
            "id": "browser-player",
            "title": "Browser player",
            "duration_ms": 1200,
        },
        "renderers": {"browser": {"payload_version": 1}},
        "presentation": {
            "browser": {
                "window": {"mode": "framed", "theme": "kde-breeze", "title": "Demo"},
                "chrome": {"mode": "full"},
            }
        },
        "assets": {
            "initial": {
                "path": image,
                "media_type": "image/webp",
                "sha256": "0" * 64,
                "bytes": 0,
            }
        },
        "beats": [
            {
                "id": "browser",
                "heading": "Browser step",
                "renderer": "browser",
                "offset_ms": 0,
                "duration_ms": 1200,
                "payload": "beats/browser.browser.json",
                "guide": {"success_hint": "The browser step is complete."},
                "transition_in": "window-open",
            }
        ],
    }
    (root / "recording.presentation.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


@pytest.mark.parametrize("viewport", [(1280, 800), (390, 844)])
def test_standalone_browser_player_on_desktop_and_emulated_mobile(
    tmp_path: Path, viewport: tuple[int, int]
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": viewport[0], "height": viewport[1]},
            is_mobile=viewport[0] < 500,
            has_touch=viewport[0] < 500,
        )
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.locator("#play").wait_for(state="visible")
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator(".browser-window[data-mode='framed']").wait_for()

        assert page.locator(".browser-chrome[data-mode='full']").is_visible()
        assert page.locator(".browser-chrome-url").text_content() == (
            "https://public.example/demo"
        )
        viewport_box = page.locator(".browser-viewport").bounding_box()
        stage_box = page.locator("#browser-stage").bounding_box()
        assert viewport_box is not None and stage_box is not None
        assert viewport_box["width"] <= stage_box["width"] + 1
        assert viewport_box["height"] <= stage_box["height"] + 1

        page.locator("#rate").click()
        assert page.locator("#rate").text_content() == "1.25×"
        page.locator("#play").click()
        page.wait_for_function("Number(document.querySelector('#progress').value) > 0")

        page.locator("#guided").click()
        page.locator("#progress").evaluate(
            "element => { element.value = '990'; "
            "element.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        page.locator("#play").click()
        page.locator("#guide:not([hidden])").wait_for(timeout=3000)
        assert page.locator("#guide-copy").is_hidden()
        assert page.locator("#guide-hint").text_content() == (
            "The browser step is complete."
        )
        assert page.locator(".browser-chrome-url").text_content() == (
            "https://public.example/finished"
        )
        browser.close()
