#!/usr/bin/env python3
"""Open a Tiny Canvas file in the editor or directly in a browser."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import time
import webbrowser

from omegaflow.browser_handoff import BrokeredBrowserSession


SVG_FILENAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.svg")
EDITABLE_FILE = "sunset-study.svg"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", action="store_true")
    parser.add_argument("file")
    args = parser.parse_args()

    filename = Path(args.file).name
    if args.file != filename or not SVG_FILENAME.fullmatch(filename):
        raise SystemExit(f"Unknown Tiny Canvas file: {filename}")
    if not args.view and filename != EDITABLE_FILE:
        raise SystemExit(
            f"Tiny Canvas edits {EDITABLE_FILE} and saves a title-named file."
        )

    base_url = os.environ.get("TINY_CANVAS_URL", "http://127.0.0.1:8765")
    path = f"/files/{filename}" if args.view else "/"
    url = f"{base_url.rstrip('/')}{path}"
    session = BrokeredBrowserSession.from_environment(url)
    if session is None:
        if not webbrowser.open(url):
            raise SystemExit("Could not open Tiny Canvas.")
        return

    while session.is_open():
        time.sleep(0.05)


if __name__ == "__main__":
    main()
