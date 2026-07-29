#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "reference-state.json"
READY = ROOT / ".reference-server-ready"


def read_state() -> str:
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "not-ready"
    state = value.get("state") if isinstance(value, dict) else None
    return state if isinstance(state, str) else "not-ready"


def write_state(value: str) -> None:
    STATE.write_text(json.dumps({"state": value}) + "\n", encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/advance":
            write_state("browser-updated")
            self._send("text/plain; charset=utf-8", b"browser-updated")
            return
        if path != "/":
            self.send_error(404)
            return
        state = read_state()
        body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>OmegaFlow Browser Reference</title>
    <style>
      :root {{ color-scheme: light; font-family: Inter, system-ui, sans-serif; }}
      body {{ margin: 0; background: #eef3fb; color: #17233a; }}
      main {{ width: 680px; margin: 96px auto; padding: 40px; background: white;
              border: 1px solid #cfdaea; border-radius: 18px; box-shadow: 0 18px 48px #16305b24; }}
      h1 {{ margin-top: 0; }}
      #state {{ display: inline-block; padding: 8px 12px; background: #e7eefb;
                border-radius: 8px; font-weight: 700; }}
      label {{ display: grid; gap: 8px; margin: 28px 0; font-weight: 650; }}
      input {{ padding: 12px 14px; border: 1px solid #9eacc2; border-radius: 8px; font: inherit; }}
      button {{ padding: 11px 18px; border: 0; border-radius: 8px; color: white;
                background: #2764d8; font: inherit; font-weight: 700; cursor: pointer; }}
    </style>
  </head>
  <body>
    <main>
      <h1>Shared capture environment</h1>
      <p>Current state: <span id="state" data-testid="state">{state}</span></p>
      <label>Project name <input aria-label="Project name" value=""></label>
      <button type="button">Advance</button>
    </main>
    <script>
      document.querySelector('button').addEventListener('click', async () => {{
        const response = await fetch('/advance');
        document.querySelector('#state').textContent = await response.text();
      }});
    </script>
  </body>
</html>""".encode("utf-8")
        self._send("text/html; charset=utf-8", body)

    def _send(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    READY.write_text("ready\n", encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        READY.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
