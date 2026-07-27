from __future__ import annotations

import os
from pathlib import Path

import pytest

from omegaflow.audio import AudioSettings, NarrationSegment, openai_speech_bytes
from omegaflow.service_environment import (
    ServiceEnvironmentError,
    resolve_service_environment,
)


SECRET_NAME = "OPENAI_OMEGAFLOW_API_KEY"


def write_service_environment(root: Path, value: str) -> Path:
    directory = root / ".omegaflow"
    directory.mkdir()
    path = directory / "omegaflow-secret.env"
    path.write_text(f"{SECRET_NAME}={value}\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_service_environment_reads_private_project_file_without_mutating_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_service_environment(tmp_path, "file-secret")
    monkeypatch.delenv(SECRET_NAME, raising=False)

    resolved = resolve_service_environment((SECRET_NAME,), root=tmp_path)

    assert resolved == {SECRET_NAME: "file-secret"}
    assert SECRET_NAME not in os.environ


def test_service_environment_prefers_explicit_parent_value_for_ci(
    tmp_path: Path,
) -> None:
    write_service_environment(tmp_path, "file-secret")

    resolved = resolve_service_environment(
        (SECRET_NAME,),
        root=tmp_path,
        environ={SECRET_NAME: "ci-secret"},
    )

    assert resolved == {SECRET_NAME: "ci-secret"}


def test_service_environment_rejects_public_file_permissions(tmp_path: Path) -> None:
    path = write_service_environment(tmp_path, "file-secret")
    path.chmod(0o644)

    with pytest.raises(ServiceEnvironmentError, match="mode 0600"):
        resolve_service_environment((SECRET_NAME,), root=tmp_path, environ={})


def test_service_environment_rejects_non_allowlisted_names(tmp_path: Path) -> None:
    with pytest.raises(ServiceEnvironmentError, match="not an allowlisted"):
        resolve_service_environment(("UNRELATED_SECRET",), root=tmp_path, environ={})


def test_tts_receives_project_service_value_without_global_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_service_environment(tmp_path, "file-secret")
    monkeypatch.delenv(SECRET_NAME, raising=False)
    settings = AudioSettings(
        enabled=True,
        provider="openai",
        env=SECRET_NAME,
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        cache_dir=tmp_path / "cache",
        project_root=tmp_path,
    )

    class Response:
        def __init__(self) -> None:
            self.chunks = iter((b"audio", b""))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int = -1) -> bytes:
            return next(self.chunks)

    response = Response()

    def urlopen(request, *, timeout):
        assert timeout == 120
        assert request.headers["Authorization"] == "Bearer file-secret"
        return response

    content = openai_speech_bytes(
        NarrationSegment("intro", "Intro", "Hello"),
        settings,
        urlopen=urlopen,
    )

    assert content == b"audio"
    assert SECRET_NAME not in os.environ
