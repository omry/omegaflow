from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PAGE = b"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browser timing parity</title>
<style>
  :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
  body {
    display: grid;
    min-height: 100vh;
    margin: 0;
    place-items: center;
    background: #111522;
    color: #f7f4ff;
  }
  main {
    box-sizing: border-box;
    width: min(620px, calc(100vw - 48px));
    padding: 34px;
    border: 1px solid #555d78;
    border-radius: 22px;
    background: #181d2c;
    box-shadow: 0 22px 70px rgb(0 0 0 / 35%);
  }
  .eyebrow {
    margin: 0 0 8px;
    color: #a99aff;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: .12em;
    text-transform: uppercase;
  }
  h1 { margin: 0 0 10px; font-size: 30px; }
  p { margin: 0 0 24px; color: #c9c5d8; }
  .track {
    height: 22px;
    overflow: hidden;
    border-radius: 999px;
    background: #0c0f18;
    box-shadow: inset 0 0 0 1px #343a50;
  }
  .progress {
    width: 0;
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, #8875f5, #ffd15c);
  }
  .value {
    margin: 12px 0 24px;
    color: #fff3c8;
    font: 700 22px ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  button {
    padding: 12px 18px;
    border: 1px solid #7769c8;
    border-radius: 10px;
    background: #292942;
    color: white;
    font: inherit;
    font-weight: 750;
  }
  [data-testid="complete"] {
    display: none;
    margin: 18px 0 0;
    color: #8df0ae;
    font-weight: 800;
  }
</style>
<main>
  <div class="eyebrow" data-testid="mode"></div>
  <h1>Browser action</h1>
  <p data-testid="ready"></p>
  <div class="track"><div class="progress" data-testid="progress"></div></div>
  <div class="value" data-testid="value">0%</div>
  <button type="button" data-testid="start">Run action</button>
  <div data-testid="complete">Complete</div>
</main>
<script>
  const mode = new URLSearchParams(location.search).get('mode') === 'realtime'
    ? 'realtime'
    : 'presentation';
  const progress = document.querySelector('[data-testid="progress"]');
  const value = document.querySelector('[data-testid="value"]');
  const complete = document.querySelector('[data-testid="complete"]');
  const start = document.querySelector('[data-testid="start"]');
  document.querySelector('[data-testid="mode"]').textContent = `${mode} timing`;
  document.querySelector('[data-testid="ready"]').textContent =
    `${mode[0].toUpperCase()}${mode.slice(1)} ready`;

  function render(amount) {
    const percent = Math.round(amount * 100);
    progress.style.width = `${percent}%`;
    value.textContent = `${percent}%`;
  }

  function finish() {
    render(1);
    complete.style.display = 'block';
    start.disabled = true;
  }

  start.addEventListener('click', () => {
    if (mode === 'presentation') {
      finish();
      return;
    }
    start.disabled = true;
    const duration = 3000;
    const started = performance.now();
    function frame(now) {
      const amount = Math.min(1, (now - started) / duration);
      render(amount);
      if (amount < 1) {
        requestAnimationFrame(frame);
      } else {
        complete.style.display = 'block';
      }
    }
    requestAnimationFrame(frame);
  });
</script>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] not in {"/", "/index.html"}:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    Path(".timing-server-ready").touch()
    server.serve_forever()


if __name__ == "__main__":
    main()
