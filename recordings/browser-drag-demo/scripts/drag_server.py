from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PAGE = b"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tiny Canvas</title>
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
    width: min(900px, calc(100vw - 48px));
  }
  h1 { margin: 0 0 8px; font-size: 28px; }
  p { margin: 0 0 18px; color: #c9c5d8; }
  svg {
    display: block;
    width: 100%;
    height: auto;
    border: 1px solid #5e6480;
    border-radius: 18px;
    background: #181d2c;
    box-shadow: 0 20px 70px rgb(0 0 0 / 35%);
  }
  [data-testid="sun"] { cursor: grab; }
  [data-testid="sun-destination"] {
    fill: #fff4a8;
    fill-opacity: .12;
    stroke: #ffd561;
    stroke-dasharray: 10 8;
    stroke-width: 3;
  }
  [data-testid="status"] {
    margin-top: 14px;
    color: #8df0ae;
    font-weight: 700;
  }
</style>
<main>
  <h1>Sunset postcard</h1>
  <p>Canvas ready</p>
  <svg viewBox="0 0 900 470" role="img" aria-label="Sunset beach artwork">
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
        <stop stop-color="#7568e8"/>
        <stop offset=".58" stop-color="#ec7f91"/>
        <stop offset="1" stop-color="#ffca77"/>
      </linearGradient>
      <linearGradient id="sea" x1="0" y1="0" x2="1" y2="0">
        <stop stop-color="#2572a8"/>
        <stop offset="1" stop-color="#52b3bb"/>
      </linearGradient>
    </defs>
    <rect width="900" height="300" fill="url(#sky)"/>
    <rect y="300" width="900" height="115" fill="url(#sea)"/>
    <path d="M0 390 Q210 355 430 398 T900 382 V470 H0Z" fill="#efca82"/>
    <rect data-testid="sun-destination" x="635" y="70" width="150" height="150" rx="22"/>
    <g data-testid="sun" transform="translate(170 155)">
      <circle r="55" fill="#ffd25d" stroke="#fff1aa" stroke-width="8"/>
      <circle r="22" fill="#fff0a0" opacity=".7"/>
    </g>
    <path d="M790 455 Q755 330 790 235" fill="none" stroke="#39291f" stroke-width="18"/>
    <path d="M790 237 q-70 0 -105 -48 q76 -15 115 23" fill="#225d43"/>
    <path d="M790 237 q58 -38 112 4 q-58 31 -109 20" fill="#2f7950"/>
    <path d="M790 239 q-18 -76 23 -112 q31 63 -5 116" fill="#28704a"/>
    <path d="M787 245 q-86 20 -119 73 q78 1 127 -47" fill="#1f6242"/>
    <path d="M794 245 q82 11 119 67 q-77 10 -124 -40" fill="#378759"/>
    <path d="M789 240 q-60 -50 -61 -103 q63 28 77 98" fill="#327f54"/>
  </svg>
  <div data-testid="status" aria-live="polite">Ready to drag</div>
</main>
<script>
  const svg = document.querySelector('svg');
  const sun = document.querySelector('[data-testid="sun"]');
  const destination = document.querySelector('[data-testid="sun-destination"]');
  const status = document.querySelector('[data-testid="status"]');
  let dragging = false;

  function svgPoint(event) {
    const point = new DOMPoint(event.clientX, event.clientY);
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }

  sun.addEventListener('pointerdown', (event) => {
    dragging = true;
    sun.style.cursor = 'grabbing';
    event.preventDefault();
  });

  addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const point = svgPoint(event);
    sun.setAttribute('transform', `translate(${point.x} ${point.y})`);
  });

  addEventListener('pointerup', (event) => {
    if (!dragging) return;
    dragging = false;
    sun.style.cursor = 'grab';
    const target = destination.getBoundingClientRect();
    const inside = (
      event.clientX >= target.left && event.clientX <= target.right &&
      event.clientY >= target.top && event.clientY <= target.bottom
    );
    if (inside) {
      const x = Number(destination.getAttribute('x'));
      const y = Number(destination.getAttribute('y'));
      const width = Number(destination.getAttribute('width'));
      const height = Number(destination.getAttribute('height'));
      sun.setAttribute('transform', `translate(${x + width / 2} ${y + height / 2})`);
      status.textContent = 'Sun moved';
    } else {
      sun.setAttribute('transform', 'translate(170 155)');
      status.textContent = 'Ready to drag';
    }
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
    Path(".drag-server-ready").touch()
    server.serve_forever()


if __name__ == "__main__":
    main()
