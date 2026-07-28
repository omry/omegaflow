from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from omegaconf import OmegaConf
from omegaconf.errors import ValidationError

from omegaflow.application_environment import (
    ApplicationEnvironmentError,
    resolve_application_environment,
)
from omegaflow.studio_config import (
    RecordingEnvironmentConfig,
    recording_from_script,
)


def _spec(recording_dir: Path, *names: str) -> dict[str, object]:
    return {
        "_script_dir": str(recording_dir),
        "environment": {"secrets": list(names)},
    }


def test_application_environment_resolves_only_declared_local_values(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    (recording_dir / "app.secret.env").write_text(
        "APP_TOKEN=local-token\n",
        encoding="utf-8",
    )

    resolved = resolve_application_environment(
        _spec(recording_dir, "APP_TOKEN"),
        environ={"UNDECLARED_HOST_VALUE": "must-not-pass"},
        _repository_check=lambda _root, _path: None,
    )

    assert resolved == {"APP_TOKEN": "local-token"}


def test_application_environment_accepts_a_declared_fileless_ci_value(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)

    resolved = resolve_application_environment(
        _spec(recording_dir, "APP_TOKEN"),
        environ={"APP_TOKEN": "ci-token", "OTHER": "must-not-pass"},
        _repository_check=lambda _root, _path: None,
    )

    assert resolved == {"APP_TOKEN": "ci-token"}


def test_application_environment_rejects_ambiguous_sources(tmp_path: Path) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    (recording_dir / "app.secret.env").write_text(
        "APP_TOKEN=local-token\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ApplicationEnvironmentError,
        match="APP_TOKEN.*both the host environment and app.secret.env",
    ):
        resolve_application_environment(
            _spec(recording_dir, "APP_TOKEN"),
            environ={"APP_TOKEN": "ci-token"},
            _repository_check=lambda _root, _path: None,
        )


def test_application_environment_rejects_missing_values(tmp_path: Path) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)

    with pytest.raises(
        ApplicationEnvironmentError,
        match="missing recording application secret 'APP_TOKEN'",
    ):
        resolve_application_environment(
            _spec(recording_dir, "APP_TOKEN"),
            environ={},
            _repository_check=lambda _root, _path: None,
        )


def test_application_environment_rejects_undeclared_file_entries(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    (recording_dir / "app.secret.env").write_text(
        "APP_TOKEN=local-token\nUNDECLARED=private\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ApplicationEnvironmentError,
        match="undeclared application secret 'UNDECLARED'",
    ):
        resolve_application_environment(
            _spec(recording_dir, "APP_TOKEN"),
            environ={},
            _repository_check=lambda _root, _path: None,
        )


def test_application_environment_rejects_service_environment_names(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)

    with pytest.raises(
        ApplicationEnvironmentError,
        match="OPENAI_OMEGAFLOW_API_KEY.*OmegaFlow service secret",
    ):
        resolve_application_environment(
            _spec(recording_dir, "OPENAI_OMEGAFLOW_API_KEY"),
            environ={"OPENAI_OMEGAFLOW_API_KEY": "service-token"},
            _repository_check=lambda _root, _path: None,
        )


def test_application_environment_rejects_a_symlinked_secret_file(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    outside = tmp_path / "outside.env"
    outside.write_text("APP_TOKEN=outside\n", encoding="utf-8")
    (recording_dir / "app.secret.env").symlink_to(outside)

    with pytest.raises(
        ApplicationEnvironmentError,
        match="app.secret.env.*symbolic link",
    ):
        resolve_application_environment(
            _spec(recording_dir, "APP_TOKEN"),
            environ={},
            _repository_check=lambda _root, _path: None,
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_application_environment_requires_the_local_file_to_be_ignored(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    secret_file = recording_dir / "app.secret.env"
    secret_file.write_text("APP_TOKEN=local-token\n", encoding="utf-8")

    with pytest.raises(
        ApplicationEnvironmentError,
        match="app.secret.env.*is not ignored",
    ):
        resolve_application_environment(
            _spec(recording_dir, "APP_TOKEN"),
            environ={},
        )

    (tmp_path / "recordings" / ".gitignore").write_text(
        "**/app.secret.env\n",
        encoding="utf-8",
    )
    assert resolve_application_environment(
        _spec(recording_dir, "APP_TOKEN"),
        environ={},
    ) == {"APP_TOKEN": "local-token"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_application_environment_rejects_a_tracked_local_file(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    recording_dir = tmp_path / "recordings" / "demo"
    recording_dir.mkdir(parents=True)
    secret_file = recording_dir / "app.secret.env"
    secret_file.write_text("APP_TOKEN=local-token\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "**/app.secret.env\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "-f", "recordings/demo/app.secret.env"],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(
        ApplicationEnvironmentError,
        match="app.secret.env.*tracked or staged",
    ):
        resolve_application_environment(
            _spec(recording_dir, "APP_TOKEN"),
            environ={},
        )


def test_application_environment_names_are_typed_configuration() -> None:
    configured = OmegaConf.merge(
        OmegaConf.structured(RecordingEnvironmentConfig),
        {"secrets": ["APP_TOKEN"]},
    )

    assert configured.secrets == ["APP_TOKEN"]
    with pytest.raises(ValidationError):
        OmegaConf.merge(
            OmegaConf.structured(RecordingEnvironmentConfig),
            {"secrets": "APP_TOKEN"},
        )


def test_recording_frontmatter_accepts_declared_application_secrets(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recordings"
    script_dir = recording_dir / "demo"
    script_dir.mkdir(parents=True)
    (script_dir / "index.md").write_text(
        """\
---
kind: video
id: demo
environment:
  secrets:
  - APP_TOKEN
audio:
  enabled: false
---

```studio-directive
scene: Application environment
```

```studio-directive
beat:
  id: probe
  heading: Probe
  narration: Probe the application environment.
  actions:
  - run: "true"
```
""",
        encoding="utf-8",
    )

    spec = recording_from_script("demo", recording_dir=recording_dir)

    assert spec["environment"]["secrets"] == ["APP_TOKEN"]
