from __future__ import annotations

import hashlib
import json
import shutil
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def pane_title(text: str | None = None) -> dict[str, object]:
    return {
        "visible": True,
        "text": text,
        "alignment_x": "right",
        "alignment_y": "top",
        "position_x": "0.25rem",
        "position_y": "0.25rem",
    }


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


def copy_player_assets(root: Path) -> None:
    static_root = REPO_ROOT / "src/omegaflow/player/static"
    for name in (
        "cast-player.html",
        "cast-player-core.js",
        "re2js-2.8.5.umd.js",
    ):
        shutil.copy2(static_root / name, root / name)


def write_browser_player_fixture(root: Path) -> None:
    copy_player_assets(root)
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
        "signatures": "signatures.json",
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
            }
        },
        "panes": [{"id": "main", "title": pane_title(), "renderer": "browser"}],
        "beats": [
            {
                "id": "browser",
                "heading": "Browser step",
                "offset_ms": 0,
                "duration_ms": 1200,
                "layout": {"areas": [["main"]]},
                "pane_tracks": [
                    {
                        "pane_id": "main",
                        "initial": "first",
                        "beats": [
                            {
                                "id": "browser",
                                "offset_ms": 0,
                                "duration_ms": 1200,
                                "payload": "beats/browser.browser.json",
                                "transition": {"kind": "cut", "duration_ms": 0},
                            }
                        ],
                    }
                ],
                "guide": {"success_hint": "The browser step is complete."},
                "transition_in": "window-open",
            }
        ],
    }
    (root / "recording.presentation.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / "signatures.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "beats/browser.browser.json": {
                        "sha256": hashlib.sha256(
                            (root / "beats/browser.browser.json").read_bytes()
                        ).hexdigest(),
                        "bytes": (root / "beats/browser.browser.json").stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def write_terminal_player_fixture(root: Path) -> None:
    copy_player_assets(root)
    (root / "beats").mkdir()
    payload_path = root / "beats/nano.cast"
    shutil.copy2(
        REPO_ROOT / "tests/fixtures/nano-character-sets.cast",
        payload_path,
    )
    manifest = {
        "manifest_version": 1,
        "signatures": "signatures.json",
        "recording": {
            "id": "nano-player",
            "title": "Nano terminal playback",
            "duration_ms": 1100,
        },
        "renderers": {"terminal": {"payload_version": 1}},
        "presentation": {"browser": None, "guided": False},
        "assets": {},
        "panes": [{"id": "main", "title": pane_title(), "renderer": "terminal"}],
        "beats": [
            {
                "id": "nano",
                "heading": "Edit artwork",
                "offset_ms": 0,
                "duration_ms": 1100,
                "layout": {"areas": [["main"]]},
                "pane_tracks": [
                    {
                        "pane_id": "main",
                        "initial": "first",
                        "beats": [
                            {
                                "id": "nano",
                                "offset_ms": 0,
                                "duration_ms": 1100,
                                "payload": "beats/nano.cast",
                                "transition": {"kind": "cut", "duration_ms": 0},
                            }
                        ],
                    }
                ],
                "guide": None,
                "transition_in": None,
            }
        ],
    }
    (root / "recording.presentation.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    payload = payload_path.read_bytes()
    (root / "signatures.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "beats/nano.cast": {
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def write_visualization_player_fixture(root: Path) -> None:
    shutil.copy2(
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html",
        root / "cast-player.html",
    )
    shutil.copy2(
        REPO_ROOT / "src/omegaflow/player/static/cast-player-core.js",
        root / "cast-player-core.js",
    )
    (root / "beats").mkdir()
    text = 'title: "<script>alert(1)</script>"\nstatus: ready\n'
    payload = {
        "payload_version": 1,
        "beat_id": "definition",
        "duration_ms": 1000,
        "language": "yaml",
        "text": text,
        "highlights": [
            {
                "start": 43,
                "end": 48,
                "color": "brand",
                "start_ms": 0,
                "end_ms": 900,
            },
        ],
        "tokens": [
            {"start": 0, "end": 5, "kind": "key"},
            {"start": 7, "end": 34, "kind": "string"},
            {"start": 35, "end": 41, "kind": "key"},
            {"start": 43, "end": 48, "kind": "keyword"},
        ],
    }
    payload_path = root / "beats/definition.visualization.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = {
        "manifest_version": 1,
        "signatures": "signatures.json",
        "recording": {
            "id": "visualization-player",
            "title": "Visualization player",
            "duration_ms": 1000,
        },
        "renderers": {"visualization": {"payload_version": 1}},
        "presentation": {
            "guided": False,
            "pane_chrome": {"style": "framed"},
        },
        "assets": {},
        "panes": [
            {
                "id": "definition",
                "title": {
                    "visible": True,
                    "text": "Beat definition",
                    "alignment_x": "right",
                    "alignment_y": "top",
                    "position_x": "0.25rem",
                    "position_y": "0.25rem",
                },
                "renderer": "visualization",
            }
        ],
        "beats": [
            {
                "id": "explain",
                "heading": "Explain a beat",
                "offset_ms": 0,
                "duration_ms": 1000,
                "layout": {"areas": [["definition"]]},
                "pane_tracks": [
                    {
                        "pane_id": "definition",
                        "initial": "first",
                        "beats": [
                            {
                                "id": "definition",
                                "offset_ms": 0,
                                "duration_ms": 1000,
                                "payload": "beats/definition.visualization.json",
                                "transition": {"kind": "cut", "duration_ms": 0},
                            }
                        ],
                    }
                ],
                "guide": None,
                "player": None,
                "transition_in": "cut",
            }
        ],
    }
    (root / "recording.presentation.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / "signatures.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "beats/definition.visualization.json": {
                        "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                        "bytes": payload_path.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_visualization_player_renders_escaped_syntax_tokens(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_visualization_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        host = page.locator(".visualization-renderer-host:not([hidden])")
        host.wait_for(state="visible")

        assert host.text_content() == (
            'title: "<script>alert(1)</script>"\nstatus: ready\n'
        )
        assert host.locator("script").count() == 0
        assert host.locator("[data-token-kind='key']").all_inner_texts() == [
            "title",
            "status",
        ]
        assert host.locator("[data-token-kind='string']").inner_text() == (
            '"<script>alert(1)</script>"'
        )
        assert host.locator("[data-language='yaml']").count() == 1
        highlighted = host.locator("[data-highlight-color='brand']")
        assert highlighted.inner_text() == "ready"
        assert "visualization-text-highlight-brand" in (
            highlighted.get_attribute("class") or ""
        )
        browser.close()


def test_pane_chrome_can_be_disabled(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_visualization_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presentation"]["pane_chrome"]["style"] = "none"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        host = page.locator(".visualization-renderer-host:not([hidden])")
        host.wait_for(state="visible")

        assert page.locator(".stage").get_attribute("data-pane-chrome") == "none"
        assert host.evaluate(
            "element => getComputedStyle(element).borderTopWidth"
        ) == "0px"
        browser.close()


def test_pane_title_position_is_relative_to_selected_frame_edges(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_visualization_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    title = manifest["panes"][0]["title"]
    title.update(
        {
            "alignment_x": "right",
            "position_x": "0.8rem",
            "alignment_y": "bottom",
            "position_y": "0.7rem",
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
        host = page.locator(".visualization-renderer-host:not([hidden])")
        host.wait_for(state="visible")

        assert host.get_attribute("data-title-alignment-x") == "right"
        assert host.get_attribute("data-title-alignment-y") == "bottom"
        assert host.evaluate(
            "element => getComputedStyle(element, '::before').right"
        ) == "12.8px"
        assert host.evaluate(
            "element => getComputedStyle(element, '::before').bottom"
        ) == "11.2px"
        browser.close()


def test_pane_title_can_be_hidden(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_visualization_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["panes"][0]["title"]["visible"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        host = page.locator(".visualization-renderer-host:not([hidden])")
        host.wait_for(state="visible")

        assert host.get_attribute("data-pane-title-visible") == "false"
        assert host.evaluate(
            "element => getComputedStyle(element, '::before').display"
        ) == "none"
        browser.close()


def test_checked_in_visualization_fixture_composes_with_terminal() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    manifest = (
        "/tests/fixtures/visualization-player/recording.presentation.json"
    )

    with player_site(REPO_ROOT) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 650})
        page.goto(
            f"{base_url}/src/omegaflow/player/static/cast-player.html"
            f"?manifest={manifest}"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#play").click()
        page.locator("#playback-cover").wait_for(state="hidden")
        visualization = page.locator(
            ".visualization-renderer-host:not([hidden])"
        )
        terminal = page.locator(".terminal-renderer-host:not([hidden])")

        assert 'regex: "Renderer: .*"' in visualization.inner_text()
        assert "Renderer: ready" in terminal.inner_text()
        visualization_box = visualization.bounding_box()
        terminal_box = terminal.bounding_box()
        assert visualization_box is not None and terminal_box is not None
        assert (
            visualization_box["x"] + visualization_box["width"]
            <= terminal_box["x"] + 1
        )
        assert visualization.get_attribute("data-pane-label") == "Beat definition"
        assert terminal.get_attribute("data-pane-label") == "Status"
        assert visualization.evaluate(
            "element => getComputedStyle(element).borderTopWidth"
        ) == "1px"
        assert terminal.evaluate(
            "element => getComputedStyle(element).borderTopWidth"
        ) == "1px"
        assert visualization.evaluate(
            "element => getComputedStyle(element).borderRadius"
        ) != "0px"
        assert terminal.evaluate(
            "element => getComputedStyle(element).borderRadius"
        ) != "0px"
        assert visualization.evaluate(
            "element => getComputedStyle(element, '::before').right"
        ) == "4px"
        assert page.locator(".stage").get_attribute("data-pane-chrome") == "framed"
        assert page.locator("#play").get_attribute("aria-label") == "Pause"
        visualization.click()
        assert page.locator("#play").get_attribute("aria-label") == "Play"
        browser.close()


def test_tiny_canvas_artwork_fits_short_browser_viewport(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    app_root = REPO_ROOT / "src/omegaflow/tutorial/tiny_canvas/app"
    shutil.copy2(app_root / "styles.css", tmp_path / "styles.css")
    artwork = (app_root / "draft.svg").read_text(encoding="utf-8")
    (tmp_path / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head><link rel="stylesheet" href="/styles.css"></head>
<body>
  <header>
    <div><p class="eyebrow">OmegaFlow tutorial app</p><h1>Tiny Canvas</h1></div>
    <p id="status">Ready</p>
  </header>
  <main>
    <aside><label>Artwork title</label><input value="Sunset Study"></aside>
    <section class="canvas-shell"><div id="canvas">{artwork}</div></section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 700})
        page.goto(base_url)

        canvas = page.locator("#canvas").bounding_box()
        artwork_box = page.locator("#canvas svg").bounding_box()
        assert canvas is not None and artwork_box is not None
        assert canvas["y"] + canvas["height"] <= 700
        assert artwork_box["y"] + artwork_box["height"] <= 700
        assert abs(canvas["width"] - canvas["height"]) <= 1
        assert abs(artwork_box["width"] - artwork_box["height"]) <= 1
        browser.close()


def test_tiny_canvas_save_button_shows_saved_feedback(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    app_root = REPO_ROOT / "src/omegaflow/tutorial/tiny_canvas/app"
    for name in ("app.js", "index.html", "styles.css"):
        shutil.copy2(app_root / name, tmp_path / name)
    artwork = (app_root / "draft.svg").read_text(encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route(
            "**/api/artwork",
            lambda route: route.fulfill(
                status=200,
                content_type="image/svg+xml",
                body=artwork,
            ),
        )
        page.route(
            "**/api/export",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"filename":"coconut-sunset.svg"}',
            ),
        )
        page.goto(base_url)
        sync_api.expect(page.get_by_test_id("status")).to_have_text("Ready")
        page.get_by_test_id("artwork-title").fill("Coconut Sunset")

        button = page.get_by_test_id("export-artwork")
        button.click()

        sync_api.expect(button).to_have_text("Saved coconut-sunset.svg ✓")
        sync_api.expect(button).to_have_attribute("data-state", "saved")
        page.wait_for_timeout(200)
        assert button.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(113, 228, 155)"
        browser.close()


def test_browser_player_fixture_has_valid_payload_signature(tmp_path: Path) -> None:
    write_browser_player_fixture(tmp_path)

    payload = (tmp_path / "beats/browser.browser.json").read_bytes()
    signature = json.loads(
        (tmp_path / "signatures.json").read_text(encoding="utf-8")
    )["files"]["beats/browser.browser.json"]

    assert signature == {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def test_generated_player_replays_nano_without_control_sequence_artifacts(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_terminal_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#progress").evaluate(
            "element => { "
            "element.dispatchEvent(new Event('pointerdown', {bubbles: true})); "
            "element.value = '500'; "
            "element.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        terminal_host = page.locator(".terminal-renderer-host:not([hidden])")
        page.wait_for_function(
            "document.querySelector('.terminal-renderer-host:not([hidden])')"
            ".textContent.includes('GNU nano 7.2')"
        )

        terminal_text = terminal_host.text_content()
        assert terminal_text is not None
        assert "Help" in terminal_text
        assert "Write Out" in terminal_text
        assert "Read File" in terminal_text
        assert "\x1b" not in terminal_text
        assert "(B" not in terminal_text
        assert ")0" not in terminal_text
        page.wait_for_function(
            "parseFloat(getComputedStyle(document.querySelector("
            "'.terminal-renderer-host:not([hidden])')).fontSize) > 15"
        )
        geometry = terminal_host.evaluate(
            """element => {
              const box = element.getBoundingClientRect();
              const range = document.createRange();
              range.selectNodeContents(element);
              const content = range.getBoundingClientRect();
              return {
                boxHeight: box.height,
                contentHeight: content.height,
                fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
              };
            }"""
        )
        assert geometry["fontSize"] > 15
        assert geometry["contentHeight"] <= geometry["boxHeight"]
        assert geometry["contentHeight"] >= geometry["boxHeight"] * 0.8

        page.locator("#progress").evaluate(
            "element => { element.value = '1090'; "
            "element.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        page.wait_for_function(
            "document.querySelector('.terminal-renderer-host:not([hidden])')"
            ".textContent === ''"
        )
        browser.close()


def test_terminal_player_wraps_printable_output_at_captured_columns(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_terminal_player_fixture(tmp_path)
    payload_path = tmp_path / "beats" / "nano.cast"
    payload_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "version": 3,
                        "term": {"cols": 10, "rows": 4},
                        "timestamp": 0,
                    }
                ),
                json.dumps([0.0, "o", "1234567890ABC"]),
                "",
            )
        ),
        encoding="utf-8",
    )
    payload = payload_path.read_bytes()
    (tmp_path / "signatures.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "beats/nano.cast": {
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 640, "height": 360})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator("#progress").evaluate(
            "element => { element.value = '500'; "
            "element.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        terminal_host = page.locator(".terminal-renderer-host:not([hidden])")
        page.wait_for_function(
            "document.querySelector('.terminal-renderer-host:not([hidden])')"
            ".textContent.includes('ABC')"
        )

        assert terminal_host.text_content().rstrip() == "1234567890\nABC"
        browser.close()


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
        stage_box = page.locator(
            ".browser-renderer-host:not([hidden])"
        ).bounding_box()
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


def test_player_composes_terminal_and_browser_fixture_panes_side_by_side(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    terminal_path = tmp_path / "beats/terminal.cast"
    terminal_path.write_text(
        '{"version":3,"term":{"cols":80,"rows":24}}\n'
        '[0.0,"o","terminal pane"]\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["renderers"]["terminal"] = {"payload_version": 1}
    manifest["panes"][0]["id"] = "auto"
    manifest["beats"][0]["pane_tracks"][0]["pane_id"] = "auto"
    manifest["panes"].insert(0, {"id": "terminal", "renderer": "terminal"})
    manifest["beats"][0]["layout"] = {"areas": [["terminal", "auto"]]}
    manifest["beats"][0]["pane_tracks"].insert(
        0,
        {
            "pane_id": "terminal",
            "initial": "first",
            "beats": [
                {
                    "id": "terminal",
                    "offset_ms": 0,
                    "duration_ms": 1200,
                    "payload": "beats/terminal.cast",
                    "transition": {"kind": "cut", "duration_ms": 0},
                }
            ],
        },
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    signatures_path = tmp_path / "signatures.json"
    signatures = json.loads(signatures_path.read_text(encoding="utf-8"))
    signatures["files"]["beats/terminal.cast"] = {
        "sha256": hashlib.sha256(terminal_path.read_bytes()).hexdigest(),
        "bytes": terminal_path.stat().st_size,
    }
    signatures_path.write_text(json.dumps(signatures), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        terminal_host = page.locator(
            ".terminal-renderer-host:not([hidden])"
        )
        assert "terminal pane" in terminal_host.inner_text()
        terminal_box = terminal_host.bounding_box()
        browser_box = page.locator(
            ".browser-renderer-host:not([hidden])"
        ).bounding_box()
        assert terminal_box is not None and browser_box is not None
        assert terminal_box["x"] + terminal_box["width"] <= browser_box["x"] + 1
        assert abs(terminal_box["width"] - browser_box["width"]) <= 2
        assert page.locator(".stage").evaluate(
            "element => element.style.gridTemplateAreas"
        ) == '"pane-1 pane-2"'
        assert terminal_host.evaluate("element => element.style.gridArea") == "pane-1"
        assert page.locator(
            ".browser-renderer-host:not([hidden])"
        ).evaluate("element => element.style.gridArea") == "pane-2"
        browser.close()


def test_player_composes_sequential_terminal_pane_beats_during_fade(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    first_path = tmp_path / "beats/first.cast"
    second_path = tmp_path / "beats/second.cast"
    first_path.write_text(
        '{"version":3,"term":{"cols":80,"rows":24}}\n'
        '[0.0,"o","first terminal pane"]\n',
        encoding="utf-8",
    )
    second_path.write_text(
        '{"version":3,"term":{"cols":80,"rows":24}}\n'
        '[0.0,"o","second terminal pane"]\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recording"]["duration_ms"] = 1200
    manifest["renderers"] = {"terminal": {"payload_version": 1}}
    manifest["panes"] = [{"id": "terminal", "renderer": "terminal"}]
    manifest["beats"] = [
        {
            "id": "terminal-sequence",
            "heading": "Terminal sequence",
            "offset_ms": 0,
            "duration_ms": 1200,
            "layout": {"areas": [["terminal"]]},
            "pane_tracks": [
                {
                    "pane_id": "terminal",
                    "initial": "first",
                    "beats": [
                        {
                            "id": "first",
                            "offset_ms": 0,
                            "duration_ms": 700,
                            "payload": "beats/first.cast",
                            "transition": {"kind": "cut", "duration_ms": 0},
                        },
                        {
                            "id": "second",
                            "offset_ms": 700,
                            "duration_ms": 500,
                            "payload": "beats/second.cast",
                            "transition": {"kind": "fade", "duration_ms": 200},
                        },
                    ],
                }
            ],
            "transition_in": "cut",
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    signatures_path = tmp_path / "signatures.json"
    signatures = json.loads(signatures_path.read_text(encoding="utf-8"))
    signatures["files"] = {
        f"beats/{path.name}": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in (first_path, second_path)
    }
    signatures_path.write_text(json.dumps(signatures), encoding="utf-8")

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
            "element => { element.value = String(800 / 1200 * 1000); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); "
            "element.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        page.wait_for_timeout(200)
        terminal_states = page.locator(".terminal-renderer-host").evaluate_all(
            "hosts => hosts.map(host => ({"
            "hidden: host.hidden, opacity: host.style.opacity, "
            "text: host.textContent}))"
        )
        assert len([state for state in terminal_states if not state["hidden"]]) == 2, (
            terminal_states,
            page.locator("#terminal").text_content(),
        )
        hosts = page.locator(".terminal-renderer-host:not([hidden])")
        assert hosts.nth(0).inner_text().strip() == "first terminal pane"
        assert hosts.nth(1).inner_text().strip() == "second terminal pane"
        assert float(hosts.nth(0).evaluate("element => element.style.opacity")) == 1
        assert 0.49 <= float(
            hosts.nth(1).evaluate("element => element.style.opacity")
        ) <= 0.51
        browser.close()


def test_browser_player_hides_disabled_window_decorations(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presentation"]["browser"]["window"]["mode"] = "none"
    manifest["presentation"]["browser"]["chrome"]["mode"] = "hidden"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        page.locator(".browser-window[data-mode='none']").wait_for()

        assert page.locator(".browser-window-titlebar").is_hidden()
        assert page.locator(".browser-chrome[data-mode='hidden']").is_hidden()
        assert page.locator(".browser-window-titlebar").evaluate(
            "element => getComputedStyle(element).display"
        ) == "none"
        assert page.locator(".browser-chrome").evaluate(
            "element => getComputedStyle(element).display"
        ) == "none"
        browser.close()


def test_player_hides_narration_behind_a_logo_cover_until_playback_starts(
    tmp_path: Path,
) -> None:
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

        assert page.locator("#player").get_attribute("data-playback-started") == "false"
        assert page.locator("#narration").evaluate(
            "element => getComputedStyle(element).visibility"
        ) == "hidden"
        assert page.locator("#playback-cover").evaluate(
            "element => getComputedStyle(element).opacity"
        ) == "1"
        logo = page.locator("#playback-cover .playback-cover-logo")
        assert logo.is_visible()
        logo_box = logo.bounding_box()
        stage_box = page.locator(".stage").bounding_box()
        assert logo_box is not None and stage_box is not None
        assert 96 <= logo_box["width"] <= 145

        page.set_viewport_size({"width": 900, "height": 360})
        compact_logo_box = logo.bounding_box()
        compact_stage_box = page.locator(".stage").bounding_box()
        assert compact_logo_box is not None and compact_stage_box is not None
        assert compact_logo_box["width"] <= compact_stage_box["height"] * 0.42 + 1
        assert compact_logo_box["width"] < logo_box["width"]

        page.locator("#play").click()
        page.wait_for_function(
            "document.querySelector('#player').dataset.playbackStarted === 'true'"
        )
        page.wait_for_function(
            "getComputedStyle(document.querySelector('#playback-cover')).opacity === '0'"
        )

        assert page.locator("#narration").evaluate(
            "element => getComputedStyle(element).visibility"
        ) == "visible"
        playback_feedback = page.locator("#playback-flash")
        assert playback_feedback.get_attribute("data-visible") is None

        page.locator("#play").click()
        page.locator("#playback-flash[data-feedback='pause']").wait_for()
        assert playback_feedback.locator(".playback-mark").count() == 1
        assert playback_feedback.locator(".playback-mark-pause").evaluate(
            "element => getComputedStyle(element).opacity"
        ) == "1"
        assert playback_feedback.locator(".playback-mark-play").evaluate(
            "element => getComputedStyle(element).opacity"
        ) == "0"
        assert playback_feedback.evaluate(
            "element => getComputedStyle(element).borderTopWidth"
        ) == "0px"
        assert playback_feedback.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgba(0, 0, 0, 0)"

        page.locator("#play").click()
        page.locator("#playback-flash[data-feedback='play']").wait_for()
        assert playback_feedback.locator(".playback-mark-play").evaluate(
            "element => getComputedStyle(element).opacity"
        ) == "1"

        countdown_page = browser.new_page(viewport={"width": 900, "height": 600})
        countdown_page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&autoplay=countdown"
        )
        countdown_page.wait_for_function(
            "!document.querySelector('#play').disabled"
        )
        assert countdown_page.locator("#player").get_attribute(
            "data-autoplay-countdown"
        ) == "true"
        assert countdown_page.locator("#playback-cover").is_hidden()
        browser.close()


def test_player_reveals_startup_load_errors_instead_of_covering_them(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/missing.presentation.json"
        )
        page.wait_for_function(
            "document.querySelector('#voice').getAttribute('aria-label') === "
            "'recording unavailable'"
        )

        assert page.locator("#terminal").text_content() == (
            "could not load presentation manifest: 404"
        )
        assert page.locator("#terminal").is_visible()
        assert page.locator("#playback-cover").is_hidden()
        assert page.locator("#play").is_disabled()
        browser.close()


def test_player_starts_at_the_requested_manifest_beat(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first = manifest["beats"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "second"
    second["heading"] = "Second step"
    second["offset_ms"] = 1200
    second["pane_tracks"][0]["beats"][0]["id"] = "second"
    manifest["beats"].append(second)
    manifest["recording"]["duration_ms"] = 2400
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&beat=second"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")

        assert page.locator("#clock").text_content() == "0:01 / 0:02"
        assert int(page.locator("#progress").input_value()) == 500
        assert page.locator("#play").get_attribute("aria-label") == "Play"
        browser.close()


def test_player_rejects_an_unknown_requested_manifest_beat(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json&beat=missing"
        )
        page.wait_for_function(
            "document.querySelector('#voice').getAttribute('aria-label') === "
            "'recording unavailable'"
        )

        assert page.locator("#terminal").text_content() == (
            "unknown beat \"missing\"; valid beat ids: browser"
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
            "offset_ms": 1200,
            "duration_ms": 1200,
            "layout": {"areas": [["main"]]},
            "pane_tracks": [
                {
                    "pane_id": "main",
                    "initial": "first",
                    "beats": [
                        {
                            "id": "second",
                            "offset_ms": 0,
                            "duration_ms": 1200,
                            "payload": "beats/second.browser.json",
                            "transition": {"kind": "cut", "duration_ms": 0},
                        }
                    ],
                }
            ],
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
    manifest["panes"] = [
        {"id": "terminal", "renderer": "terminal"},
        {"id": "browser", "renderer": "browser"},
    ]

    (tmp_path / "beats/outgoing.cast").write_text(
        '{"version":3,"term":{"cols":80,"rows":24}}\n'
        '[0.0,"o","outgoing terminal beat"]\n',
        encoding="utf-8",
    )
    manifest["beats"][0] = {
        "id": "outgoing",
        "heading": "Outgoing terminal step",
        "offset_ms": 0,
        "duration_ms": 1200,
        "layout": {"areas": [["terminal"]]},
        "pane_tracks": [
            {
                "pane_id": "terminal",
                "initial": "first",
                "beats": [
                    {
                        "id": "outgoing",
                        "offset_ms": 0,
                        "duration_ms": 1200,
                        "payload": "beats/outgoing.cast",
                        "transition": {"kind": "cut", "duration_ms": 0},
                    }
                ],
            }
        ],
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
            "offset_ms": 1200,
            "duration_ms": 1200,
            "layout": {"areas": [["browser"]]},
            "pane_tracks": [
                {
                    "pane_id": "browser",
                    "initial": "first",
                    "beats": [
                        {
                            "id": "next",
                            "offset_ms": 0,
                            "duration_ms": 1200,
                            "payload": "beats/next.browser.json",
                            "transition": {"kind": "cut", "duration_ms": 0},
                        }
                    ],
                }
            ],
            "guide": None,
            "transition_in": "cut",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    signature_paths = (
        "beats/browser.browser.json",
        "beats/outgoing.cast",
        "beats/next.browser.json",
    )
    signatures = {
        relative_path: {
            "sha256": hashlib.sha256(
                (tmp_path / relative_path).read_bytes()
            ).hexdigest(),
            "bytes": (tmp_path / relative_path).stat().st_size,
        }
        for relative_path in signature_paths
    }
    (tmp_path / "signatures.json").write_text(
        json.dumps({"version": 1, "files": signatures}),
        encoding="utf-8",
    )

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

        terminal_host = page.locator(".terminal-renderer-host:not([hidden])")
        assert terminal_host.is_visible()
        assert terminal_host.text_content().rstrip() == (
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
    next_beat = json.loads(json.dumps(manifest["beats"][0]))
    next_beat.update(
        id="next",
        heading="Next step",
        offset_ms=1200,
        player=None,
    )
    next_pane_beat = next_beat["pane_tracks"][0]["beats"][0]
    next_pane_beat.update(id="next", payload="beats/next.browser.json")
    manifest["beats"].append(next_beat)
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
        geometry = rate.evaluate(
            """element => {
              const style = getComputedStyle(element, '::before');
              const probe = document.createElement('span');
              probe.textContent = element.dataset.tooltip;
              Object.assign(probe.style, {
                position: 'fixed',
                visibility: 'hidden',
                whiteSpace: 'nowrap',
                fontFamily: style.fontFamily,
                fontSize: style.fontSize,
                fontWeight: style.fontWeight,
                letterSpacing: style.letterSpacing,
              });
              document.body.append(probe);
              const textWidth = probe.getBoundingClientRect().width;
              probe.remove();
              return {
                backgroundWidth: parseFloat(style.width),
                requiredWidth: textWidth
                  + parseFloat(style.paddingLeft)
                  + parseFloat(style.paddingRight)
                  + parseFloat(style.borderLeftWidth)
                  + parseFloat(style.borderRightWidth),
              };
            }"""
        )
        assert geometry["backgroundWidth"] >= geometry["requiredWidth"] - 1
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
            "offset_ms": 1200,
            "duration_ms": 1200,
            "layout": {"areas": [["main"]]},
            "pane_tracks": [
                {
                    "pane_id": "main",
                    "initial": "first",
                    "beats": [
                        {
                            "id": "controls",
                            "offset_ms": 0,
                            "duration_ms": 1200,
                            "payload": "beats/controls.browser.json",
                            "transition": {"kind": "cut", "duration_ms": 0},
                        }
                    ],
                }
            ],
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
            assert page.evaluate(
                """() => {
                  const tooltip = document.querySelector('#section-tooltip');
                  const cover = document.querySelector('#playback-cover');
                  tooltip.style.pointerEvents = 'auto';
                  cover.style.pointerEvents = 'auto';
                  const tooltipRect = tooltip.getBoundingClientRect();
                  const coverRect = cover.getBoundingClientRect();
                  const overlapTop = Math.max(tooltipRect.top, coverRect.top);
                  const overlapBottom = Math.min(tooltipRect.bottom, coverRect.bottom);
                  if (overlapBottom <= overlapTop) {
                    return false;
                  }
                  const point = {
                    x: tooltipRect.left + tooltipRect.width / 2,
                    y: overlapTop + (overlapBottom - overlapTop) / 2,
                  };
                  return document.elementFromPoint(point.x, point.y) === tooltip;
                }"""
            )

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
                `<span class="narration-word ${
                  index < 2 ? 'past' : index === 2 ? 'current' : 'future'
                }">word${index}</span>`
              )).join(' ');
              window.updateNarrationScroll({animate: true});
            }"""
        )
        assert page.locator("#narration").evaluate(
            "element => getComputedStyle(element).whiteSpace"
        ) == "nowrap"
        assert page.locator("#narration").evaluate("element => element.scrollLeft") == 0

        page.evaluate(
            """() => {
              const words = document.querySelectorAll('.narration-word');
              words.forEach((word, index) => {
                word.className = `narration-word ${
                  index < 18 ? 'past' : index === 18 ? 'current' : 'future'
                }`;
              });
              window.updateNarrationScroll({animate: true});
            }"""
        )
        advanced_scroll = page.locator("#narration").evaluate(
            "element => element.scrollLeft"
        )
        narration_box = page.locator("#narration").bounding_box()
        current_word_box = page.locator(".narration-word.current").bounding_box()
        assert narration_box is not None and current_word_box is not None
        assert advanced_scroll > 0
        assert current_word_box["x"] == pytest.approx(narration_box["x"], abs=1)

        page.evaluate(
            """() => {
              const words = document.querySelectorAll('.narration-word');
              words.forEach((word, index) => {
                word.className = `narration-word ${
                  index < 31 ? 'past' : index === 31 ? 'current' : 'future'
                }`;
              });
              window.updateNarrationScroll({animate: true});
            }"""
        )
        maximum_scroll = page.locator("#narration").evaluate(
            "element => element.scrollWidth - element.clientWidth"
        )
        assert page.locator("#narration").evaluate(
            "element => element.scrollLeft"
        ) == maximum_scroll
        browser.close()


def test_browser_beat_can_hide_recording_window_and_chrome(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    write_browser_player_fixture(tmp_path)
    manifest_path = tmp_path / "recording.presentation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hidden_browser = {
        "window": {"mode": "none", "theme": "kde-breeze", "title": None},
        "chrome": {"mode": "hidden"},
    }
    manifest["beats"][0]["pane_tracks"][0]["beats"][0]["browser"] = hidden_browser
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with player_site(tmp_path) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 500})
        page.goto(
            f"{base_url}/cast-player.html?manifest="
            f"{base_url}/recording.presentation.json"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")

        assert page.locator(".browser-window").get_attribute("data-mode") == "none"
        assert page.locator(".browser-window-titlebar").is_hidden()
        assert page.locator(".browser-chrome").get_attribute("data-mode") == "hidden"
        assert page.locator(".browser-chrome").is_hidden()
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
            "omegaflow recording=test-video action=build\n"
            "omegaflow recording=test-video action=watch"
        )
        assert page.locator("#guide-continue").text_content() == "Continue"
        assert page.locator(
            ".terminal-renderer-host:not([hidden])"
        ).is_visible()
        assert page.locator("#browser-stage").is_hidden()

        page.locator("#guide-continue").click()
        page.locator("#browser-stage").wait_for(state="visible", timeout=1500)
        browser.close()


def test_homepage_quickstart_narration_does_not_advance_during_audio_wait() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    static_root = REPO_ROOT / "website" / "static"
    manifest = (
        "/omegaflow-videos/quickstart-demo/presentation/"
        "recording.presentation.json"
    )
    manifest_data = json.loads(
        (static_root / manifest.removeprefix("/")).read_text(encoding="utf-8")
    )
    intervals = manifest_data["audio"]["intervals"]
    wait_before_bootstrap_summary = next(
        (left, right)
        for left, right in zip(intervals, intervals[1:], strict=True)
        if left["source_end_ms"] == right["source_start_ms"]
        and right["presentation_start_ms"] - left["presentation_end_ms"] > 500
    )
    left, right = wait_before_bootstrap_summary
    wait_midpoint_ms = (
        left["presentation_end_ms"] + right["presentation_start_ms"]
    ) / 2

    with player_site(static_root) as base_url, sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        page.goto(
            f"{base_url}/cast-player.html?manifest={manifest}"
            "&embed=1&layout=wide-browser"
        )
        page.wait_for_function("!document.querySelector('#play').disabled")
        progress_value = round(
            wait_midpoint_ms / manifest_data["recording"]["duration_ms"] * 1000
        )
        progress = page.locator("#progress")
        progress.dispatch_event("pointerdown")
        progress.evaluate(
            "(element, value) => { element.value = String(value); "
            "element.dispatchEvent(new Event('input', {bubbles: true})); }",
            progress_value,
        )

        assert page.locator(".narration-word.current").all_text_contents() == []
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
        pane_renderers = {
            pane["id"]: pane["renderer"] for pane in manifest_data["panes"]
        }
        browser_outer_beat = next(
            beat
            for beat in manifest_data["beats"]
            if any(
                pane_renderers[track["pane_id"]] == "browser"
                for track in beat["pane_tracks"]
            )
        )
        browser_track = next(
            track
            for track in browser_outer_beat["pane_tracks"]
            if pane_renderers[track["pane_id"]] == "browser"
        )
        browser_beat = browser_track["beats"][0]
        browser_payload = json.loads(
            (static_root / "omegaflow-videos/quickstart-demo/presentation"
             / browser_beat["payload"]).read_text(encoding="utf-8")
        )
        assert browser_outer_beat["player"] is None
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
            browser_outer_beat["offset_ms"]
            + browser_beat["offset_ms"]
            + second_preview["at_ms"]
            + 100
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
        signatures = json.loads(
            (
                static_root
                / "omegaflow-videos/quickstart-demo/presentation/signatures.json"
            ).read_text(encoding="utf-8")
        )["files"]
        requested_paths = {urlparse(url).path for url in presentation_requests}
        assert any(path.endswith("/recording.presentation.json") for path in requested_paths)
        assert any(path.endswith("/signatures.json") for path in requested_paths)
        assert any(path.endswith("/audio.json") for path in requested_paths)
        presentation_prefix = (
            "/omegaflow-videos/quickstart-demo/presentation/"
        )
        signed_requests = {
            parsed.path.split(presentation_prefix, 1)[1]: parse_qs(parsed.query).get("v")
            for parsed in map(urlparse, presentation_requests)
            if presentation_prefix in parsed.path
            and parsed.path.split(presentation_prefix, 1)[1].startswith(
                ("beats/", "media/", "timestamps/", "audio/")
            )
        }
        assert any(path.startswith("beats/") for path in signed_requests)
        assert any(path.startswith("media/") for path in signed_requests)
        assert any(path.startswith("timestamps/") for path in signed_requests)
        for path, query_signature in signed_requests.items():
            assert query_signature == [signatures[path]["sha256"]]
        for take in audio_metadata["takes"]:
            playback_src = take.get("playback_src", take["src"])
            expected_signature = signatures[playback_src]["sha256"]
            assert any(
                urlparse(url).path.endswith("/" + playback_src)
                and parse_qs(urlparse(url).query).get("v") == [expected_signature]
                for url in presentation_requests
            )
        assert failed_requests == []
        assert bad_responses == []
        browser.close()
