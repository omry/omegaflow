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
def watch_server(root: Path):
    handler = partial(
        studio.StudioWatchRequestHandler,
        artifacts={},
        directory=str(root),
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
