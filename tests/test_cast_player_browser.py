from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

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


def test_embedded_wide_browser_layout_resizes_the_complete_window(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 500})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&embed=1&layout=wide-browser"
        )
        page.locator(".browser-window[data-mode='framed']").wait_for()

        player = page.locator("#player")
        assert player.get_attribute("data-embedded") == "true"
        assert player.get_attribute("data-layout") == "wide-browser"
        initial = page.locator(".browser-window-layout").bounding_box()
        assert initial is not None

        page.set_viewport_size({"width": 1200, "height": 750})
        page.wait_for_function(
            "minimum => document.querySelector('.browser-window-layout')"
            ".getBoundingClientRect().width > minimum",
            arg=initial["width"] * 1.4,
        )
        resized = page.locator(".browser-window-layout").bounding_box()
        viewport = page.locator(".browser-viewport").bounding_box()
        assert resized is not None and viewport is not None
        assert resized["width"] > initial["width"] * 1.4
        assert viewport["width"] > 0
        browser.close()


def test_homepage_quickstart_bundle_loads_browser_beat_at_end() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    static_root = REPO_ROOT / "website" / "static"
    manifest = (
        "/omegaflow-videos/quickstart-demo/presentation/"
        "recording.presentation.json"
    )

    with player_site(static_root) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        presentation_requests: list[str] = []
        failed_requests: list[str] = []
        bad_responses: list[str] = []
        page.on(
            "request",
            lambda request: presentation_requests.append(request.url)
            if "/omegaflow-videos/quickstart-demo/presentation/" in request.url
            else None,
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(request.url)
            if not urlparse(request.url).path.endswith(".mp3")
            else None,
        )
        page.on(
            "response",
            lambda response: bad_responses.append(
                f"{response.status} {response.url}"
            )
            if response.status >= 400
            else None,
        )
        page.goto(
            f"{base_url}/cast-player.html?manifest={manifest}"
            "&embed=1&layout=wide-browser"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#progress").evaluate(
            "element => { element.value = '1000'; "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        page.locator(".browser-window").wait_for()

        elapsed, total = page.locator("#clock").text_content().split(" / ")
        assert elapsed == total
        assert page.locator("#browser-stage").is_visible()
        assert page.locator("#terminal").text_content() != "Failed to fetch"
        audio_metadata = json.loads(
            (
                static_root
                / "omegaflow-videos/quickstart-demo/presentation/audio.json"
            ).read_text(encoding="utf-8")
        )
        requested_paths = {urlparse(url).path for url in presentation_requests}
        assert any(path.endswith("/recording.presentation.json") for path in requested_paths)
        assert any(path.endswith("/audio.json") for path in requested_paths)
        for take in audio_metadata["takes"]:
            assert take["sha256"] in take["src"]
            assert any(path.endswith("/" + take["src"]) for path in requested_paths)
        assert failed_requests == []
        assert bad_responses == []
        browser.close()
