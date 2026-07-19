from __future__ import annotations

import threading
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from omegaflow import studio


@contextmanager
def watch_server(root: Path, *, pages: dict[str, bytes] | None = None):
    handler = partial(
        studio.StudioWatchRequestHandler,
        artifacts={},
        directory=str(root),
        pages=pages,
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
