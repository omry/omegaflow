from __future__ import annotations

import struct
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_website_uses_canonical_logo_and_camera_mascot() -> None:
    design_dir = REPO_ROOT / "docs/design"
    image_dir = REPO_ROOT / "website/static/img"

    assert (image_dir / "omegaflow-logo.svg").read_bytes() == (
        design_dir / "logo.svg"
    ).read_bytes()
    assert (image_dir / "favicon.svg").read_bytes() == (
        design_dir / "logo.svg"
    ).read_bytes()
    assert (image_dir / "omegaflow-mascot-camera.svg").read_bytes() == (
        design_dir / "mascot-camera.svg"
    ).read_bytes()


def test_social_card_uses_night_studio_palette() -> None:
    social_card = (
        REPO_ROOT / "website/static/img/omegaflow-social.svg"
    ).read_text(encoding="utf-8")

    assert 'width="1200" height="630"' in social_card
    assert "#8b7cff" in social_card
    assert "#ffc247" in social_card
    assert "Rebuildable terminal demos." in social_card

    png = (REPO_ROOT / "website/static/img/omegaflow-social.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1200, 630)
