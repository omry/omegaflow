#!/usr/bin/env python3
"""Vendor an asciinema release binary for platform wheel builds."""

from __future__ import annotations

import argparse
import hashlib
import stat
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ASCIINEMA_VERSION = "3.2.1"


@dataclass(frozen=True)
class AsciinemaAsset:
    name: str
    sha256: str


ASSETS: dict[str, AsciinemaAsset] = {
    "linux-x86_64": AsciinemaAsset(
        name="asciinema-x86_64-unknown-linux-gnu",
        sha256="1b405bbda565b33c3c4718de67fedc3535580603c0694b1ff3fb04f363430a20",
    ),
    "linux-aarch64": AsciinemaAsset(
        name="asciinema-aarch64-unknown-linux-gnu",
        sha256="b516a6d896844c0ffbc96e0a55afe4cbcc79216abde0fc64fdda4e39bee421ea",
    ),
    "macos-x86_64": AsciinemaAsset(
        name="asciinema-x86_64-apple-darwin",
        sha256="1b388af0e1566ab19deea663b0ce64730ad46ade2825fadd43cc88f0bd28140a",
    ),
    "macos-aarch64": AsciinemaAsset(
        name="asciinema-aarch64-apple-darwin",
        sha256="1f0c76da7855601df93e5dccdf69b7c683b81beff1411e38b3802de1f5fc7a1c",
    ),
}


def asset_url(asset: AsciinemaAsset) -> str:
    return (
        "https://github.com/asciinema/asciinema/releases/download/"
        f"v{ASCIINEMA_VERSION}/{asset.name}"
    )


def sha256_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vendor(platform: str, *, output: Path) -> None:
    try:
        asset = ASSETS[platform]
    except KeyError as exc:
        choices = ", ".join(sorted(ASSETS))
        raise SystemExit(
            f"unsupported platform {platform!r}; choose one of: {choices}"
        ) from exc
    if not asset.sha256:
        raise SystemExit(
            f"no checksum recorded for {platform}; add the v{ASCIINEMA_VERSION} "
            "release asset checksum before publishing this wheel"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".download")
    urllib.request.urlretrieve(asset_url(asset), temporary)
    actual = sha256_digest(temporary)
    if actual != asset.sha256:
        temporary.unlink(missing_ok=True)
        raise SystemExit(
            f"checksum mismatch for {asset.name}: expected {asset.sha256}, got {actual}"
        )
    temporary.replace(output)
    mode = output.stat().st_mode
    output.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    output.with_suffix(".platform").write_text(platform + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "platform",
        choices=sorted(ASSETS),
        help="target platform asset to vendor",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/omegaflow_studio/bin/asciinema"),
        help="where to write the packaged recorder binary",
    )
    args = parser.parse_args(argv)
    vendor(args.platform, output=args.output)
    print(f"vendored asciinema {ASCIINEMA_VERSION}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
