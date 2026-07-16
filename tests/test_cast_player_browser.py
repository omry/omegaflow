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


def test_playback_completion_renders_the_exact_final_browser_state(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    payload_path = tmp_path / "beats/browser.browser.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["events"].append(
        {
            "kind": "display_url",
            "action_id": "complete",
            "at_ms": 1200,
            "end_ms": 1200,
            "value": "https://public.example/complete",
        }
    )
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 500})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#play").click()
        page.wait_for_function(
            "document.querySelector('#clock').textContent.trim() === '0:01 / 0:01'"
        )
        page.wait_for_function(
            "document.querySelector('.browser-chrome-url').textContent === "
            "'https://public.example/complete'"
        )

        assert page.locator(".browser-chrome-url").text_content() == (
            "https://public.example/complete"
        )
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
        manifest_data = json.loads(
            (static_root / manifest.removeprefix("/")).read_text(encoding="utf-8")
        )
        browser_beat = next(
            beat for beat in manifest_data["beats"] if beat["renderer"] == "browser"
        )
        browser_payload = json.loads(
            (static_root / "omegaflow-videos/quickstart-demo/presentation"
             / browser_beat["payload"]).read_text(encoding="utf-8")
        )
        first_clip = next(
            event for event in browser_payload["events"] if event["kind"] == "clip"
        )
        clip_start_ms = browser_beat["offset_ms"] + first_clip["at_ms"] + 100
        progress_value = round(
            clip_start_ms / manifest_data["recording"]["duration_ms"] * 1000
        )
        page.locator("#progress").evaluate(
            "(element, value) => { element.value = String(value); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }",
            progress_value,
        )
        active_clip = page.locator(".browser-clip:not([hidden])")
        active_clip.wait_for()
        active_clip.evaluate(
            "clip => { "
            "clip.__testMediaEvents = {play: 0, pause: 0, seeking: 0}; "
            "for (const name of ['play', 'pause', 'seeking']) { "
            "clip.addEventListener(name, () => clip.__testMediaEvents[name] += 1); "
            "} }"
        )
        captured_clips = page.locator(".browser-clip")
        assert captured_clips.count() == 2
        continuation_clip = captured_clips.nth(1)
        continuation_clip.evaluate(
            "clip => { "
            "clip.__testMediaEvents = {play: 0, pause: 0, seeking: 0}; "
            "clip.__testHiddenMutations = 0; "
            "new MutationObserver(records => { "
            "clip.__testHiddenMutations += records.length; "
            "}).observe(clip, {attributes: true, attributeFilter: ['hidden']}); "
            "for (const name of ['play', 'pause', 'seeking']) { "
            "clip.addEventListener(name, () => clip.__testMediaEvents[name] += 1); "
            "} }"
        )
        initial_clip_time = active_clip.evaluate("clip => clip.currentTime")
        page.locator("#play").click()
        page.wait_for_function(
            "initial => document.querySelector('.browser-clip:not([hidden])')"
            ".currentTime > initial + 0.2",
            arg=initial_clip_time,
        )
        clip_playback = active_clip.evaluate(
            "clip => ({paused: clip.paused, currentTime: clip.currentTime, "
            "events: clip.__testMediaEvents})"
        )
        assert clip_playback["paused"] is False
        assert clip_playback["events"]["play"] == 1
        assert clip_playback["events"]["pause"] == 0
        assert clip_playback["events"]["seeking"] <= 1
        page.wait_for_function(
            "() => { const clips = document.querySelectorAll('.browser-clip'); "
            "return clips.length === 2 && !clips[1].hidden && "
            "clips[1].currentTime > 0.5; }",
            timeout=4000,
        )
        continuation_playback = continuation_clip.evaluate(
            "clip => ({hidden: clip.hidden, paused: clip.paused, "
            "currentTime: clip.currentTime, events: {...clip.__testMediaEvents}, "
            "hiddenMutations: clip.__testHiddenMutations})"
        )
        assert continuation_playback["hidden"] is False
        assert continuation_playback["paused"] is False
        assert continuation_playback["events"]["play"] == 1
        assert continuation_playback["events"]["pause"] == 0
        assert continuation_playback["events"]["seeking"] <= 1
        assert continuation_playback["hiddenMutations"] <= 1
        early_frame = continuation_clip.screenshot()
        page.wait_for_function(
            "() => document.querySelectorAll('.browser-clip')[1].currentTime > 2.5",
            timeout=4000,
        )
        later_frame = continuation_clip.screenshot()
        assert later_frame != early_frame

        final_clip = next(
            event for event in reversed(browser_payload["events"])
            if event["kind"] == "clip"
        )
        final_clip_element = continuation_clip.element_handle()
        assert final_clip_element is not None
        page.wait_for_function(
            "() => document.querySelectorAll('.browser-clip')[1].hidden",
            timeout=9000,
        )
        completed_clip = final_clip_element.evaluate(
            "clip => ({hidden: clip.hidden, paused: clip.paused, "
            "events: {...clip.__testMediaEvents}, "
            "hiddenMutations: clip.__testHiddenMutations})"
        )
        page.wait_for_timeout(400)
        held_clip = final_clip_element.evaluate(
            "clip => ({hidden: clip.hidden, paused: clip.paused, "
            "events: {...clip.__testMediaEvents}, "
            "hiddenMutations: clip.__testHiddenMutations})"
        )
        final_clip_index = browser_payload["events"].index(final_clip)
        final_state = browser_payload["events"][final_clip_index + 1]
        assert final_state["kind"] == "state"
        final_state_path = manifest_data["assets"][final_state["asset"]]["path"]
        visible_state = page.locator(".browser-state-primary:not([hidden])")
        assert urlparse(visible_state.get_attribute("src")).path.endswith(
            "/omegaflow-videos/quickstart-demo/presentation/" + final_state_path
        )
        assert completed_clip["hidden"] is True
        assert completed_clip["paused"] is True
        assert held_clip == completed_clip
        assert completed_clip["events"]["play"] == 1
        assert completed_clip["events"]["pause"] <= 1
        diagnostics = page.evaluate(
            "() => window.__omegaflowMediaDiagnostics"
        )
        beat_diagnostics = [
            clip
            for clip in diagnostics["clips"]
            if clip["beatId"] == browser_beat["id"]
        ]
        assert diagnostics["version"] == 1
        assert len(beat_diagnostics) == 2
        assert all(clip["sampleCount"] > 0 for clip in beat_diagnostics)
        attempted = [clip for clip in beat_diagnostics if clip["playAttempts"]]
        assert attempted, [
            (clip["assetId"], clip["sampleCount"], clip["playAttempts"])
            for clip in beat_diagnostics
        ]
        assert all(
            clip["playResolutions"] == clip["playAttempts"]
            for clip in attempted
        ), [
            (clip["playAttempts"], clip["playResolutions"])
            for clip in attempted
        ]
        assert all(not clip["playRejections"] for clip in beat_diagnostics)
        assert beat_diagnostics[-1]["last"]["hidden"] is True
        assert completed_clip["events"]["seeking"] <= 2
        assert completed_clip["hiddenMutations"] <= 2

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
