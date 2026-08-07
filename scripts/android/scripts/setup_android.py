#!/usr/bin/env python3
"""Install Google's latest Android Platform Tools into a user-local directory."""

from __future__ import annotations

import argparse
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


URLS = {
    "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "Windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=Path.home() / ".local" / "share" / "xhs-publishing",
    )
    args = parser.parse_args()

    system = platform.system()
    if system not in URLS:
        raise SystemExit(f"Unsupported platform: {system}")

    install_dir = args.install_dir.expanduser().resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / "platform-tools"

    with tempfile.TemporaryDirectory(prefix="xhs-platform-tools-") as temp_dir:
        archive = Path(temp_dir) / "platform-tools.zip"
        print(f"Downloading official Platform Tools from {URLS[system]}")
        urllib.request.urlretrieve(URLS[system], archive)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temp_dir)
        extracted = Path(temp_dir) / "platform-tools"
        if not (extracted / ("adb.exe" if system == "Windows" else "adb")).exists():
            raise SystemExit("Downloaded archive does not contain adb")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), target)

    adb = target / ("adb.exe" if system == "Windows" else "adb")
    print(f"Installed: {adb}")
    print(f"Set XHS_ADB={adb} or add {target} to PATH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
