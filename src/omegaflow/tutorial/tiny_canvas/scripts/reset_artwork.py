#!/usr/bin/env python3
"""Prepare editable runtime state from the packaged example artwork."""

from pathlib import Path
import shutil


RECORDING_DIR = Path(__file__).resolve().parents[1]
SOURCE = RECORDING_DIR / "example.svg"
STATE_DIR = RECORDING_DIR.parent / ".omegaflow" / "tutorial" / "sunset-beach"
DESTINATION = STATE_DIR / "sunset-study.svg"


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for artwork in STATE_DIR.glob("*.svg"):
        artwork.unlink()
    shutil.copyfile(SOURCE, DESTINATION)
    print("Prepared the example artwork.")


if __name__ == "__main__":
    main()
