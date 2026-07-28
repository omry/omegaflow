#!/usr/bin/env python3
"""Deterministic standard-library server for the Tiny Canvas tutorial app."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


APP_DIR = Path(__file__).resolve().parent
RECORDING_DIR = APP_DIR.parent
STATE_DIR = RECORDING_DIR.parent / ".omegaflow" / "tutorial" / "sunset-beach"
DRAFT = APP_DIR / "draft.svg"
SVG_FILENAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.svg")


def filename_for_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")
    return f"{slug or 'untitled-artwork'}.svg"


ARTWORK = STATE_DIR / filename_for_title("Sunset Study")
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
        if path.startswith("/files/"):
            filename = path.removeprefix("/files/")
            if not SVG_FILENAME.fullmatch(filename):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            svg = STATE_DIR / filename
            if not svg.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "artwork file is missing")
                return
            self.send_bytes(svg.read_bytes(), "image/svg+xml")
            return
        if path in STATIC_FILES:
            relative_path, content_type = STATIC_FILES[path]
            self.send_bytes((APP_DIR / relative_path).read_bytes(), content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/export":
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
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid SVG")
            return
        title = next(
            (
                element.text.strip()
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "title"
                and element.text
                and element.text.strip()
            ),
            "Untitled artwork",
        )
        filename = filename_for_title(title)
        write_atomic(STATE_DIR / filename, content)
        response = json.dumps({"status": "ok", "filename": filename}).encode("utf-8")
        self.send_bytes(response + b"\n", "application/json")

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
