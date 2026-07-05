from omegaflow_studio import __version__
from omegaflow_studio.studio_config import (
    CONFIG_DIR,
    RECORDING_SCRIPT_DIR,
    studio_run_dir,
)


def test_version_is_available() -> None:
    assert __version__ == "0.1.0"


def test_studio_paths_use_studio_project_directory() -> None:
    assert CONFIG_DIR.parts[-2:] == ("studio", "conf")
    assert RECORDING_SCRIPT_DIR.parts[-2:] == ("studio", "recordings")


def test_studio_run_dir_uses_studio_directory() -> None:
    assert (
        studio_run_dir("build", "record", False, "demo", "20260705-010203")
        == "studio/runs/demo/20260705-010203"
    )
    assert (
        studio_run_dir("inspect", None, False, "demo", "20260705-010203")
        == "studio/studio-runs/inspect/demo/20260705-010203"
    )
