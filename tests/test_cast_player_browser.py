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


@pytest.mark.parametrize(
    ("commands", "copy_label"),
    [
        (["python -m pip install omegaflow"], "Copy command"),
        (
            [
                "omegaflow recording=quickstart action=build",
                "omegaflow recording=quickstart action=watch",
            ],
            "Copy commands",
        ),
    ],
)
def test_guided_checkpoint_renders_authored_commands(
    tmp_path: Path,
    commands: list[str],
    copy_label: str,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presentation"]["guided"] = True
    manifest["beats"][0]["guide"] = {
        "commands": commands,
        "summary": "Install the package before continuing.",
        "success_hint": "Install OmegaFlow.",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#play").click()
        page.locator("#guide:not([hidden])").wait_for(timeout=3000)

        assert page.locator("#guide-command").text_content() == "\n".join(commands)
        assert page.locator("#guide-copy").is_visible()
        assert page.locator("#guide-copy").text_content() == copy_label
        assert page.locator("#guide-summary").text_content() == (
            "Install the package before continuing."
        )
        assert page.locator("#guide-continue").text_content() == "Finish"

        page.evaluate(
            "commands => Object.defineProperty(navigator, 'clipboard', {"
            "configurable: true, value: {writeText: text => {"
            "window.__copiedGuideCommands = text; return Promise.resolve();"
            "}}})",
            commands,
        )
        page.locator("#guide-copy").click()
        page.wait_for_function(
            "expected => window.__copiedGuideCommands === expected",
            arg="\n".join(commands),
        )
        assert page.locator("#guide-copy").text_content() == "Copied"
        page.wait_for_function(
            "expected => document.querySelector('#guide-copy').textContent === expected",
            arg=copy_label,
            timeout=3000,
        )

        page.locator("#guide").click(position={"x": 10, "y": 10})
        continue_button = page.locator("#guide-continue")
        play_button = page.locator("#play")
        assert continue_button.get_attribute("data-resume-hint") == "true"
        assert play_button.get_attribute("data-resume-hint") == "true"
        assert continue_button.evaluate(
            "element => getComputedStyle(element).outlineStyle"
        ) == "solid"
        assert play_button.evaluate(
            "element => getComputedStyle(element).outlineStyle"
        ) == "solid"

        continue_button.click()
        assert continue_button.get_attribute("data-resume-hint") is None
        assert play_button.get_attribute("data-resume-hint") is None
        browser.close()


def test_guided_scrubber_click_only_snaps_after_crossing_checkpoint(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presentation"]["guided"] = True
    manifest["recording"]["duration_ms"] = 2400
    next_payload = json.loads((tmp_path / "beats/browser.browser.json").read_text())
    next_payload["beat_id"] = "second"
    (tmp_path / "beats/second.browser.json").write_text(
        json.dumps(next_payload), encoding="utf-8"
    )
    manifest["beats"].append(
        {
            "id": "second",
            "heading": "Second step",
            "renderer": "browser",
            "offset_ms": 1200,
            "duration_ms": 1200,
            "payload": "beats/second.browser.json",
            "guide": {"success_hint": "Second step complete."},
            "transition_in": "cut",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        progress = page.locator("#progress")

        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "element => { element.value = String(1100 / 2400 * 1000); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        assert page.locator("#guide").is_hidden()
        assert 450 <= int(progress.input_value()) <= 465

        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "element => { element.value = String(1300 / 2400 * 1000); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        page.locator("#guide:not([hidden])").wait_for(timeout=1000)
        assert int(progress.input_value()) == 500
        browser.close()


def test_guided_checkpoint_holds_outgoing_beat_before_transition(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recording"]["duration_ms"] = 2400
    manifest["presentation"]["guided"] = True
    manifest["renderers"]["terminal"] = {"payload_version": 1}

    (tmp_path / "beats/outgoing.cast").write_text(
        '{"version":3,"term":{"cols":80,"rows":24}}\n'
        '[0.0,"o","outgoing terminal beat"]\n',
        encoding="utf-8",
    )
    manifest["beats"][0] = {
        "id": "outgoing",
        "heading": "Outgoing terminal step",
        "renderer": "terminal",
        "offset_ms": 0,
        "duration_ms": 1200,
        "payload": "beats/outgoing.cast",
        "guide": {"commands": ["continue"]},
        "transition_in": None,
    }

    next_payload = json.loads(
        (tmp_path / "beats/browser.browser.json").read_text(encoding="utf-8")
    )
    next_payload["beat_id"] = "next"
    next_payload["initial_display_url"] = "https://public.example/next"
    next_payload["events"] = []
    (tmp_path / "beats/next.browser.json").write_text(
        json.dumps(next_payload), encoding="utf-8"
    )
    manifest["beats"].append(
        {
            "id": "next",
            "heading": "Next step",
            "renderer": "browser",
            "offset_ms": 1200,
            "duration_ms": 1200,
            "payload": "beats/next.browser.json",
            "guide": None,
            "transition_in": "cut",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        progress = page.locator("#progress")
        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "element => { element.value = '500'; "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        page.locator("#guide:not([hidden])").wait_for(timeout=3000)

        assert page.locator("#terminal").is_visible()
        assert page.locator("#terminal").text_content() == (
            "outgoing terminal beat"
        )
        assert page.locator("#browser-stage").is_hidden()
        boundary_markers = page.locator(
            '.section-marker[data-start="1.2"]'
        )
        assert boundary_markers.count() == 1

        page.locator("#guide-continue").click()
        page.locator("#browser-stage").wait_for(state="visible", timeout=1000)
        assert page.locator(".browser-chrome-url:visible").text_content() == (
            "https://public.example/next"
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


def test_player_toolbar_highlight_clears_on_control_click_or_next_beat(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recording"]["duration_ms"] = 2400
    manifest["presentation"]["guided"] = True
    manifest["beats"][0]["player"] = {
        "highlight": {"control": "guided", "start_ms": 300, "end_ms": 1200}
    }
    second_payload_path = tmp_path / "beats/next.browser.json"
    second_payload = json.loads(
        (tmp_path / "beats/browser.browser.json").read_text(encoding="utf-8")
    )
    second_payload["beat_id"] = "next"
    second_payload_path.write_text(json.dumps(second_payload), encoding="utf-8")
    manifest["beats"].append(
        {
            **manifest["beats"][0],
            "id": "next",
            "heading": "Next step",
            "offset_ms": 1200,
            "payload": "beats/next.browser.json",
            "player": None,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        player_url = (
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.goto(player_url)
        page.wait_for_function("!document.querySelector('#play').disabled")
        guided = page.locator("#guided")
        assert guided.get_attribute("data-highlighted") is None

        page.locator("#play").click()
        page.wait_for_function(
            "document.querySelector('#guided').hasAttribute('data-highlighted')"
        )
        assert guided.evaluate("element => getComputedStyle(element).outlineStyle") == (
            "solid"
        )
        assert guided.evaluate(
            "element => getComputedStyle(element, '::after').content"
        ) not in {"none", "normal"}
        guided.click()
        assert guided.get_attribute("data-highlighted") is None

        page.reload()
        page.wait_for_function("!document.querySelector('#play').disabled")
        guided = page.locator("#guided")
        assert guided.get_attribute("data-highlighted") is None
        page.locator("#play").click()
        page.wait_for_function(
            "document.querySelector('#guided').hasAttribute('data-highlighted')"
        )
        page.locator("#guide:not([hidden])").wait_for(timeout=3000)
        assert guided.get_attribute("data-highlighted") == "true"
        checkpoint_clock = page.locator("#clock").text_content()
        guided.click()
        assert guided.get_attribute("aria-pressed") == "false"
        assert page.locator("#guide").is_visible()
        assert page.locator("#play").get_attribute("aria-label") == "Continue"
        page.wait_for_timeout(150)
        assert page.locator("#clock").text_content() == checkpoint_clock
        page.locator("#guide-continue").click()
        page.wait_for_function(
            "!document.querySelector('#guided').hasAttribute('data-highlighted')"
        )
        assert guided.get_attribute("data-highlighted") is None
        browser.close()


def test_toolbar_controls_show_deterministic_tooltips(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        rate = page.locator("#rate")

        rate.hover()
        page.wait_for_function(
            "getComputedStyle(document.querySelector('#rate'), '::before').opacity === '1'"
        )

        assert rate.get_attribute("data-tooltip") == (
            "Playback speed: 1× (left-click next, right-click previous)"
        )
        assert rate.get_attribute("title") is None
        assert rate.evaluate(
            "element => getComputedStyle(element, '::before').opacity"
        ) == "1"
        browser.close()


def test_hovering_each_scrubber_section_shows_its_heading(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recording"]["duration_ms"] = 2400
    next_payload = json.loads((tmp_path / "beats/browser.browser.json").read_text())
    next_payload["beat_id"] = "controls"
    (tmp_path / "beats/controls.browser.json").write_text(
        json.dumps(next_payload), encoding="utf-8"
    )
    manifest["beats"].append(
        {
            "id": "controls",
            "heading": "Control Playback",
            "renderer": "browser",
            "offset_ms": 1200,
            "duration_ms": 1200,
            "payload": "beats/controls.browser.json",
            "guide": None,
            "transition_in": "cut",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")

        for test_id, heading in (
            ("section-region-browser", "Browser step"),
            ("section-region-controls", "Control Playback"),
        ):
            region = page.get_by_test_id(test_id)
            bounds = region.bounding_box()
            assert bounds is not None
            page.mouse.move(
                bounds["x"] + bounds["width"] / 2,
                bounds["y"] + bounds["height"] / 2,
            )
            tooltip = page.locator("#section-tooltip:not([hidden])")
            tooltip.wait_for(timeout=1000)
            assert tooltip.text_content() == heading

        browser.close()


@pytest.mark.parametrize("width", [390, 320])
def test_embedded_transport_stays_compact_on_short_mobile_viewports(
    tmp_path: Path, width: int
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": width, "height": 240},
            is_mobile=True,
            has_touch=True,
        )
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&embed=1&layout=wide-browser"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")

        header = page.locator(".bar").bounding_box()
        status = page.locator(".status").bounding_box()
        stage = page.locator(".stage").bounding_box()
        assert header is not None and status is not None and stage is not None
        assert header["height"] <= 40
        assert status["height"] <= 58
        assert stage["height"] >= 140
        for control in ("#play", "#restart", "#rate", "#mute", "#progress"):
            assert page.locator(control).is_visible()

        page.evaluate(
            """() => {
              const narration = document.querySelector('#narration');
              narration.innerHTML = Array.from({length: 32}, (_, index) => (
                `<span class="narration-word ${index === 27 ? 'current' : 'future'}">word${index}</span>`
              )).join(' ');
              window.updateNarrationScroll({animate: true});
            }"""
        )
        narration_box = page.locator("#narration").bounding_box()
        current_word_box = page.locator(".narration-word.current").bounding_box()
        assert narration_box is not None and current_word_box is not None
        assert current_word_box["x"] >= narration_box["x"] - 1
        assert current_word_box["x"] + current_word_box["width"] <= (
            narration_box["x"] + narration_box["width"] + 1
        )
        assert current_word_box["y"] >= narration_box["y"] - 1
        assert current_word_box["y"] + current_word_box["height"] <= (
            narration_box["y"] + narration_box["height"] + 1
        )
        browser.close()


def test_narration_bar_compacts_when_the_player_not_the_viewport_is_short(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&embed=1&layout=wide-browser"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#player").evaluate(
            "element => { element.style.height = '240px'; }"
        )

        header = page.locator(".bar").bounding_box()
        assert header is not None
        assert header["height"] <= 40
        browser.close()


def test_completed_progress_track_remains_visible(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 240})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&embed=1&layout=wide-browser"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#play").click()
        page.wait_for_function(
            "document.querySelector('#progress').value === '1000'"
        )

        state = page.locator("#progress").evaluate(
            """element => ({
              complete: element.parentElement.dataset.complete,
              position: element.style.getPropertyValue('--position'),
              width: element.getBoundingClientRect().width,
              height: element.getBoundingClientRect().height,
              display: getComputedStyle(element).display,
              visibility: getComputedStyle(element).visibility,
              opacity: getComputedStyle(element).opacity,
            })"""
        )
        assert state["complete"] == "true"
        assert state["position"] == "100%"
        assert state["display"] == "block"
        assert state["visibility"] == "visible"
        assert state["opacity"] == "1"
        assert state["width"] > 100
        assert state["height"] > 0
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


def test_homepage_quickstart_checkpoint_holds_terminal_before_browser() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    static_root = REPO_ROOT / "website" / "static"
    manifest = (
        "/omegaflow-videos/quickstart-demo/presentation/"
        "recording.presentation.json"
    )
    manifest_data = json.loads(
        (static_root / manifest.removeprefix("/")).read_text(encoding="utf-8")
    )
    build_beat = next(
        beat for beat in manifest_data["beats"] if beat["id"] == "build"
    )
    build_checkpoint_ms = build_beat["offset_ms"] + build_beat["duration_ms"]
    assert any(
        interval["presentation_start_ms"] < build_checkpoint_ms
        < interval["presentation_end_ms"]
        for interval in manifest_data["audio"]["intervals"]
    )

    with player_site(static_root) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        page.goto(
            f"{base_url}/cast-player.html?manifest={manifest}"
            "&embed=1&layout=wide-browser"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        progress = page.locator("#progress")
        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "(element, value) => { element.value = String(value); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }",
            round(
                (build_checkpoint_ms - 3500)
                / manifest_data["recording"]["duration_ms"]
                * 1000
            ),
        )
        page.locator("#play").click()
        page.locator("#guide:not([hidden])").wait_for(timeout=5000)
        assert page.locator("#guide-title").text_content() == (
            "Checkpoint: Build the Video"
        )
        assert page.locator("#guide-command").text_content() == (
            "omegaflow recording=quickstart action=build\n"
            "omegaflow recording=quickstart action=watch"
        )
        assert page.locator("#guide-continue").text_content() == "Continue"
        assert page.locator("#terminal").is_visible()
        assert page.locator("#browser-stage").is_hidden()

        page.locator("#guide-continue").click()
        page.locator("#browser-stage").wait_for(state="visible", timeout=1500)
        browser.close()


def test_homepage_quickstart_bundle_loads_paused_browser_preview_at_end() -> None:
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
        assert page.locator("#guided").get_attribute("aria-pressed") == "true"
        assert page.locator("#guided").get_attribute("aria-label") == "Guided mode on"
        assert page.locator("#guided").get_attribute("data-highlighted") is None
        manifest_data = json.loads(
            (static_root / manifest.removeprefix("/")).read_text(encoding="utf-8")
        )
        install_beat = next(
            beat for beat in manifest_data["beats"] if beat["id"] == "install"
        )
        assert install_beat["guide"] == {
            "commands": ["python -m pip install omegaflow"],
            "summary": None,
            "success_hint": (
                "OmegaFlow is installed and the omegaflow command is available."
            ),
        }
        intro_beat = manifest_data["beats"][0]
        intro_highlight = intro_beat["player"]["highlight"]
        cue_seek_ms = (
            intro_beat["offset_ms"] + intro_highlight["start_ms"] + 100
        )
        cue_progress_value = round(
            cue_seek_ms / manifest_data["recording"]["duration_ms"] * 1000
        )
        progress = page.locator("#progress")
        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "(element, value) => { element.value = String(value); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }",
            cue_progress_value,
        )
        assert page.locator("#guided").get_attribute("data-highlighted") == "true"
        assert page.locator("#guided").evaluate(
            "element => getComputedStyle(element).outlineStyle"
        ) == "solid"
        browser_beat = next(
            beat for beat in manifest_data["beats"] if beat["renderer"] == "browser"
        )
        browser_payload = json.loads(
            (static_root / "omegaflow-videos/quickstart-demo/presentation"
             / browser_beat["payload"]).read_text(encoding="utf-8")
        )
        assert browser_beat["player"] is None
        assert browser_payload["initial_pointer"] == {
            "x": 576.0,
            "y": 180.0,
            "visible": False,
        }
        pointer_visibility = [
            (event["action_id"], event["visible"])
            for event in browser_payload["events"]
            if event["kind"] == "pointer_visibility"
        ]
        assert pointer_visibility == [
            ("show_pointer", True),
            ("hide_pointer", False),
        ]
        speed_clicks = [
            event
            for event in browser_payload["events"]
            if event["kind"] == "click"
            and event["action_id"] in {"increase_speed", "restore_speed"}
        ]
        assert [event["button"] for event in speed_clicks] == ["left", "right"]
        first_visual_ms = min(
            event["at_ms"]
            for event in browser_payload["events"]
            if event["kind"] in {"state", "clip", "scroll"}
        )
        assert first_visual_ms >= 350
        assert not any(
            event["kind"] == "clip" for event in browser_payload["events"]
        )
        second_preview = next(
            event
            for event in browser_payload["events"]
            if event["kind"] == "state"
            and event["action_id"] == "preview_playback_section"
        )
        preview_seek_ms = (
            browser_beat["offset_ms"] + second_preview["at_ms"] + 100
        )
        progress_value = round(
            preview_seek_ms / manifest_data["recording"]["duration_ms"] * 1000
        )
        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "(element, value) => { element.value = String(value); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }",
            progress_value,
        )
        assert page.locator("#guided").get_attribute("data-highlighted") is None
        visible_state = page.locator(".browser-state-primary:not([hidden])")
        assert urlparse(visible_state.get_attribute("src")).path.endswith(
            "/omegaflow-videos/quickstart-demo/presentation/"
            + manifest_data["assets"][second_preview["asset"]]["path"]
        )
        diagnostics = page.evaluate(
            "() => window.__omegaflowMediaDiagnostics"
        )
        assert diagnostics is None

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
            playback_src = take.get("playback_src", take["src"])
            assert any(path.endswith("/" + playback_src) for path in requested_paths)
        assert failed_requests == []
        assert bad_responses == []
        browser.close()
