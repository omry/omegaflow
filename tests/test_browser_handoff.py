from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

import omegaflow.browser_handoff as browser_handoff_module
from omegaflow.browser_handoff import (
    BROWSER_HANDOFF_ID_ENV,
    BROWSER_HANDOFF_ROOT_ENV,
    BrowserHandoffBroker,
    BrokeredBrowserSession,
)


def test_brokered_browser_session_publishes_url_only_when_watch_is_ready(
    tmp_path: Path, capsys
) -> None:
    broker = BrowserHandoffBroker(tmp_path / "handoffs")
    broker.prepare("watch_command")
    session = BrokeredBrowserSession.from_environment(
        "http://127.0.0.1:43123/cast-player.html?manifest=demo",
        environment={
            BROWSER_HANDOFF_ROOT_ENV: str(broker.root),
            BROWSER_HANDOFF_ID_ENV: "watch_command",
        },
    )

    assert session is not None
    assert broker.ready_url("watch_command") is None
    assert session.is_open() is True
    assert broker.ready_url("watch_command") == (
        "http://127.0.0.1:43123/cast-player.html?manifest=demo"
    )
    assert "OmegaFlowBrowserHandoff;watch_command;ready" in capsys.readouterr().out

    broker.close("watch_command")
    assert session.is_open() is False
    session.close()


def test_browser_handoff_rejects_invalid_ids_and_symlinked_channels(
    tmp_path: Path,
) -> None:
    broker = BrowserHandoffBroker(tmp_path / "handoffs")
    with pytest.raises(ValueError, match="handoff id"):
        broker.prepare("../escape")

    broker.root.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.mkdir()
    (broker.root / "watch_command").symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        broker.prepare("watch_command")


def test_browser_handoff_close_is_idempotent_under_concurrent_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    broker = BrowserHandoffBroker(tmp_path / "handoffs")
    broker.prepare("watch_command")
    barrier = Barrier(2)
    write_exclusive = browser_handoff_module._write_exclusive

    def synchronized_write(
        path: Path,
        payload: Mapping[str, Any],
    ) -> None:
        barrier.wait()
        write_exclusive(path, payload)

    monkeypatch.setattr(
        browser_handoff_module,
        "_write_exclusive",
        synchronized_write,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(broker.close, "watch_command")
            for _ in range(2)
        ]
        for future in futures:
            future.result()
