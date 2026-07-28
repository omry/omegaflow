#!/usr/bin/env python3
"""Inspect the semantic objects in the editable tutorial artwork."""

from pathlib import Path
import xml.etree.ElementTree as ET


RECORDING_DIR = Path(__file__).resolve().parents[1]
ARTWORK = (
    RECORDING_DIR.parent
    / ".omegaflow"
    / "tutorial"
    / "sunset-beach"
    / "artwork.svg"
)
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"


def element_by_id(root: ET.Element, element_id: str) -> ET.Element | None:
    return next(
        (element for element in root.iter() if element.get("id") == element_id),
        None,
    )


def main() -> None:
    root = ET.parse(ARTWORK).getroot()
    title = element_by_id(root, "svg-title")
    required = ("sun", "coconut-tree", "sunset-target", "tree-target")
    missing = [item for item in required if element_by_id(root, item) is None]
    leaves = [
        element
        for element in root.iter(f"{SVG_NAMESPACE}path")
        if element.get("class") == "palm-leaf"
    ]
    if title is None or title.text != "Sunset Study":
        raise SystemExit("Unexpected draft title.")
    if missing:
        raise SystemExit(f"Missing semantic objects: {', '.join(missing)}")
    if len(leaves) < 6:
        raise SystemExit("The coconut tree is missing leaves.")

    print("Tiny Canvas draft")
    print(f"Title: {title.text}")
    print("Objects: sun, coconut-tree")
    print("Status: ready")


if __name__ == "__main__":
    main()
