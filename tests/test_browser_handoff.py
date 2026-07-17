from __future__ import annotations

from pathlib import Path

import pytest

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
