#!/usr/bin/env python3
"""Deterministic standard-library server for the Tiny Canvas tutorial app."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
RECORDING_DIR = APP_DIR.parent
STATE_DIR = RECORDING_DIR.parent / ".omegaflow" / "tutorial" / "sunset-beach"
ARTWORK = STATE_DIR / "artwork.svg"
EXPORT = STATE_DIR / "sunset-beach.svg"
DRAFT = APP_DIR / "draft.svg"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(content)
    temporary.replace(path)


class TinyCanvasHandler(BaseHTTPRequestHandler):
    server_version = "TinyCanvas/1"

    def send_bytes(self, content: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_bytes(b'{"status":"ready"}\n', "application/json")
            return
        if path == "/api/artwork":
            self.send_bytes(ARTWORK.read_bytes(), "image/svg+xml")
            return
        if path in STATIC_FILES:
            relative_path, content_type = STATIC_FILES[path]
            self.send_bytes((APP_DIR / relative_path).read_bytes(), content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/artwork", "/api/export"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid SVG payload")
            return
        content = self.rfile.read(length)
        if b"<svg" not in content[:500]:
            self.send_error(HTTPStatus.BAD_REQUEST, "expected SVG")
            return
        write_atomic(ARTWORK if path == "/api/artwork" else EXPORT, content)
        self.send_bytes(b'{"status":"ok"}\n', "application/json")

    def log_message(self, format: str, *args: object) -> None:
        return


def ensure_artwork() -> None:
    if not ARTWORK.exists():
        write_atomic(ARTWORK, DRAFT.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ensure_artwork()
    server = ThreadingHTTPServer((args.host, args.port), TinyCanvasHandler)
    host, port = server.server_address
    print(f"Tiny Canvas ready at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
