"""Build a single-file BotDesk executable with PyInstaller.

Run from the project folder:

    python packaging/build_exe.py

The executable is written to packaging/dist/ (botdesk.exe on Windows). PyInstaller
builds for the operating system it runs on, so run this on each platform you need.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    stylesheet = ROOT / "botdesk" / "ui" / "botdesk.tcss"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", "botdesk",
        "--paths", str(ROOT),
        "--collect-submodules", "botdesk",
        "--collect-data", "textual",
        "--add-data", f"{stylesheet}{os.pathsep}botdesk/ui",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE),
        str(HERE / "launcher.py"),
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
