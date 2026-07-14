from __future__ import annotations

import socket
import shlex
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from omegaflow.capture import BeatCapture, CaptureContext, CaptureCoordinator
from omegaflow.recording_plan import BeatPlan, normalize_recording_plan
from omegaflow.terminal_capture import PersistentTerminalRunner


def unused_loopback_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


class LocalServiceBrowserRunner:
    def __init__(self, port: int) -> None:
        self.port = port
        self.context: CaptureContext | None = None
        self.closed = False

    def start(self, context: CaptureContext) -> None:
        self.context = context

    def capture_beat(self, beat: BeatPlan) -> BeatCapture:
        assert self.context is not None
        body: str | None = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/state.txt", timeout=0.2
                ) as response:
                    body = response.read().decode("utf-8")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.02)
        if body is None:
            raise RuntimeError("local fixture service did not become ready")
        (self.context.workspace / "browser-produced-state.txt").write_text(
            f"browser-consumed:{body}", encoding="utf-8"
        )
        return BeatCapture(beat_id=beat.id)

    def close(self) -> None:
        self.closed = True


def test_mixed_capture_shares_files_and_loopback_service(tmp_path: Path) -> None:
    port = unused_loopback_port()
    python = shlex.quote(sys.executable)
    plan = normalize_recording_plan(
        {
            "id": "mixed-service",
            "browser": {},
            "beats": [
                {
                    "id": "start-service",
                    "actions": [
                        {
                                "run": (
                                    "printf terminal-ready > state.txt; "
                                    f"{python} -m http.server \"$PORT\" --bind 127.0.0.1 "
                                    ">server.log 2>&1 & export SERVER_PID=$!"
                                )
                        }
                    ],
                },
                {"id": "browser-consume", "medium": "browser", "actions": []},
                {
                    "id": "terminal-verify",
                    "actions": [
                        {
                            "run": (
                                "test \"$(cat browser-produced-state.txt)\" = "
                                "browser-consumed:terminal-ready"
                            )
                        }
                    ],
                },
            ],
            "cleanup": [
                {"run": "kill \"$SERVER_PID\"; wait \"$SERVER_PID\" 2>/dev/null || true"}
            ],
        }
    )
    browser = LocalServiceBrowserRunner(port)
    coordinator = CaptureCoordinator(
        terminal_runner_factory=lambda: PersistentTerminalRunner(
            record_cast=False, timeout_seconds=5.0
        ),
        browser_runner_factory=lambda: browser,
    )

    result = coordinator.capture(
        plan,
        tmp_path / "run",
        workspace=tmp_path,
        environment={"PORT": str(port)},
    )

    assert [beat.beat_id for beat in result.beats] == [
        "start-service",
        "browser-consume",
        "terminal-verify",
    ]
    assert (tmp_path / "browser-produced-state.txt").read_text(
        encoding="utf-8"
    ) == "browser-consumed:terminal-ready"
    assert browser.closed
