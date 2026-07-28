#!/usr/bin/env python3
"""Restore the editable artwork from the immutable tutorial draft."""

from pathlib import Path
import shutil


RECORDING_DIR = Path(__file__).resolve().parents[1]
SOURCE = RECORDING_DIR / "app" / "draft.svg"
STATE_DIR = RECORDING_DIR.parent / ".omegaflow" / "tutorial" / "sunset-beach"
DESTINATION = STATE_DIR / "artwork.svg"


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, DESTINATION)
    print("Restored the Tiny Canvas draft.")


if __name__ == "__main__":
    main()
