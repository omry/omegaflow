from __future__ import annotations

import sys
import time


def write_status(step: int, *, redraw: bool) -> None:
    if redraw:
        sys.stdout.write("\r\033[1A")
    minutes, seconds = divmod(step, 60)
    sys.stdout.write("\033[2KElapsed since start of video:\r\n")
    sys.stdout.write(f"\033[2K{minutes:02d}:{seconds:02d}")
    sys.stdout.flush()


def main() -> None:
    total = 43
    sys.stdout.write("Project: sunset.svg\r\nRenderer: ready\r\n")
    for step in range(1, total + 1):
        write_status(step, redraw=step > 1)
        time.sleep(1)
    sys.stdout.write("\r\n")


if __name__ == "__main__":
    main()
