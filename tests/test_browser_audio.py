from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from omegaflow.browser_audio import (
    MAX_CAPTURED_AUDIO_BYTES,
    stop_page_audio_capture,
)


class ResultPage:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def evaluate(self, _expression: str) -> dict[str, Any]:
        return self.result


def test_page_audio_capture_rejects_oversized_payload_before_persistence(
    tmp_path: Path,
) -> None:
    content = b"x" * (MAX_CAPTURED_AUDIO_BYTES + 1)
    page = ResultPage(
        {
            "data": base64.b64encode(content).decode(),
            "mime_type": "audio/webm;codecs=opus",
        }
    )

    with pytest.raises(RuntimeError, match="exceeds the size budget"):
        stop_page_audio_capture(
            page,
            fragments_dir=tmp_path,
            source_start_ms=0,
            source_end_ms=100,
        )

    assert list(tmp_path.iterdir()) == []
