from __future__ import annotations

import json
import shutil
import tempfile
import threading
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from omegaflow import studio


REPO_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def watch_server(
    root: Path,
    *,
    pages: dict[str, bytes] | None = None,
    recordings: dict[str, dict[str, object]] | None = None,
):
    snapshot_artifacts: dict[str, dict[str, Path]] = {}
    with tempfile.TemporaryDirectory(prefix="omegaflow-watch-test-") as snapshot_root:
        handler = partial(
            studio.StudioWatchRequestHandler,
            artifacts={},
            directory=str(root),
            pages=pages,
            recordings=recordings,
            snapshot_artifacts=snapshot_artifacts,
            snapshot_lock=threading.RLock(),
            snapshot_directory=Path(snapshot_root),
        )
        server = studio.http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def test_recording_watch_route_refreshes_to_latest_immutable_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    player_root = tmp_path / "player"
    player_root.mkdir()
    (player_root / "cast-player.html").write_text(
        '<script src="cast-player-core.js"></script>',
        encoding="utf-8",
    )
    (player_root / "cast-player-core.js").write_text(
        "window.playerLoaded = true;",
        encoding="utf-8",
    )

    def recording_run(name: str, label: str) -> Path:
        run_dir = tmp_path / "runs" / name
        presentation = run_dir / "presentation"
        (presentation / "beats").mkdir(parents=True)
        (presentation / "recording.presentation.json").write_text(
            f'{{"build":"{label}"}}\n',
            encoding="utf-8",
        )
        (presentation / "beats" / "terminal.cast").write_text(
            f"{label}\n",
            encoding="utf-8",
        )
        return run_dir

    first_run = recording_run("first", "first")
    second_run = recording_run("second", "second")
    latest = [first_run]
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: latest[0],
    )

    with watch_server(
        player_root,
        recordings={"tutorial/beat": {"_recording_id": "tutorial/beat"}},
    ) as base_url:
        player_url = f"{base_url}/watch/tutorial/beat/"
        assert urlopen(f"{base_url}/watch/tutorial/beat").url == player_url
        assert urlopen(player_url).read().decode() == (
            '<script src="cast-player-core.js"></script>'
        )
        assert urlopen(f"{player_url}cast-player-core.js").read().decode() == (
            "window.playerLoaded = true;"
        )

        with urlopen(f"{player_url}recording.presentation.json") as response:
            first_snapshot_url = response.url
            assert response.read() == b'{"build":"first"}\n'
        assert "/__studio_snapshots__/" in first_snapshot_url

        (first_run / "presentation" / "recording.presentation.json").write_text(
            '{"build":"first-rebuilt"}\n',
            encoding="utf-8",
        )
        (first_run / "presentation" / "beats" / "terminal.cast").write_text(
            "first-rebuilt\n",
            encoding="utf-8",
        )
        with urlopen(f"{player_url}recording.presentation.json") as response:
            rebuilt_snapshot_url = response.url
            assert response.read() == b'{"build":"first-rebuilt"}\n'
        assert rebuilt_snapshot_url != first_snapshot_url
        assert urlopen(first_snapshot_url).read() == b'{"build":"first"}\n'

        latest[0] = second_run
        with urlopen(f"{player_url}recording.presentation.json") as response:
            second_snapshot_url = response.url
            assert response.read() == b'{"build":"second"}\n'
        assert second_snapshot_url != first_snapshot_url

        shutil.rmtree(first_run)
        assert urlopen(first_snapshot_url).read() == b'{"build":"first"}\n'
        first_asset_url = first_snapshot_url.replace(
            "recording.presentation.json",
            "beats/terminal.cast",
        )
        assert urlopen(first_asset_url).read() == b"first\n"


def test_friendly_watch_route_loads_the_player_in_a_browser(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    run_dir = tmp_path / "runs" / "hello"
    presentation = run_dir / "presentation"
    beats = presentation / "beats"
    beats.mkdir(parents=True)
    (beats / "terminal.cast").write_text(
        '\n'.join(
            (
                json.dumps(
                    {
                        "version": 2,
                        "width": 80,
                        "height": 24,
                        "timestamp": 0,
                        "env": {"TERM": "xterm-256color"},
                    }
                ),
                json.dumps([0.0, "o", "hello\r\n"]),
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest = {
        "manifest_version": 1,
        "recording": {
            "id": "hello",
            "title": "Friendly hello",
            "duration_ms": 1000,
        },
        "renderers": {"terminal": {"payload_version": 1}},
        "presentation": {"guided": False},
        "assets": {},
        "beats": [
            {
                "id": "hello",
                "heading": "Hello",
                "renderer": "terminal",
                "offset_ms": 0,
                "duration_ms": 1000,
                "payload": "beats/terminal.cast",
                "guide": None,
                "player": None,
                "transition_in": "cut",
            }
        ],
    }
    (presentation / "recording.presentation.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        studio,
        "latest_successful_recording_run_dir",
        lambda _spec: run_dir,
    )
    static_root = REPO_ROOT / "src" / "omegaflow" / "player" / "static"

    with (
        watch_server(
            static_root,
            recordings={"hello": {"_recording_id": "hello"}},
        ) as base_url,
        sync_api.sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 500})
        snapshot_requests: list[str] = []
        page.on(
            "request",
            lambda request: snapshot_requests.append(request.url)
            if "/__studio_snapshots__/" in request.url
            else None,
        )
        page.goto(f"{base_url}/watch/hello/")
        page.wait_for_function("!document.querySelector('#play').disabled")

        assert page.url == f"{base_url}/watch/hello/"
        assert page.locator("#narration").text_content() == "Friendly hello"
        assert any(
            url.endswith("/recording.presentation.json")
            for url in snapshot_requests
        )
        assert any(url.endswith("/beats/terminal.cast") for url in snapshot_requests)
        browser.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bytes=2-5", (2, 5)),
        ("bytes=7-", (7, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=7-99", (7, 9)),
    ],
)
def test_parse_http_byte_range(value: str, expected: tuple[int, int]) -> None:
    assert studio.parse_http_byte_range(value, size=10) == expected


@pytest.mark.parametrize("value", ["items=0-1", "bytes=", "bytes=9-2", "bytes=10-"])
def test_parse_http_byte_range_rejects_invalid_requests(value: str) -> None:
    with pytest.raises(ValueError):
        studio.parse_http_byte_range(value, size=10)


def test_watch_server_supports_media_byte_ranges(tmp_path: Path) -> None:
    (tmp_path / "audio.mp3").write_bytes(b"0123456789")

    with watch_server(tmp_path) as base_url:
        with urlopen(f"{base_url}/audio.mp3") as response:
            assert response.status == 200
            assert response.headers["Accept-Ranges"] == "bytes"
            assert response.read() == b"0123456789"

        request = Request(
            f"{base_url}/audio.mp3",
            headers={"Range": "bytes=2-5"},
        )
        with urlopen(request) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.headers["Content-Length"] == "4"
            assert response.read() == b"2345"

        invalid = Request(
            f"{base_url}/audio.mp3",
            headers={"Range": "bytes=20-"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(invalid)
        assert caught.value.code == 416
        assert caught.value.headers["Content-Range"] == "bytes */10"


def test_collection_watch_page_filters_a_bounded_scrolling_list(
    tmp_path: Path,
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    members = [
        {
            "id": f"tutorial/chapter-{index}",
            "title": f"Chapter {index}",
            "description": (
                "Publish the finished video."
                if index == 30
                else f"Learn topic {index}."
            ),
            "url": f"/watch/{index}",
        }
        for index in range(1, 31)
    ]
    page_html = studio.render_collection_watch_page(
        {"id": "tutorial", "title": "Tutorial"},
        members,
    ).encode("utf-8")

    with (
        watch_server(
            tmp_path,
            pages={"/collection.html": page_html},
        ) as base_url,
        sync_api.sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 480})
        page.goto(f"{base_url}/collection.html")

        cards = page.locator("[data-video-card]")
        assert cards.count() == 30
        metrics = page.locator(".video-list").evaluate(
            "element => ({client: element.clientHeight, scroll: element.scrollHeight})"
        )
        assert metrics["scroll"] > metrics["client"]
        assert page.evaluate("document.body.scrollHeight <= window.innerHeight")

        page.locator("#video-search").fill("publish")
        assert page.locator("[data-video-card]:visible").count() == 1
        assert page.locator("#result-count").text_content() == "1 of 30 videos"
        assert "Chapter 30" in page.locator("[data-video-card]:visible").text_content()

        page.locator("#video-search").fill("not present")
        assert page.locator("[data-video-card]:visible").count() == 0
        assert page.locator("#empty-state").is_visible()

        browser.close()
