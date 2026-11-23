"""Where BotDesk keeps its data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    """The per-user application data folder for this platform."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "BotDesk"


def data_dir() -> Path:
    """Return the data folder, creating it if needed.

    Default: a BotDesk folder in the user's application data directory.
    Override with the BOTDESK_HOME environment variable.
    """
    env = os.environ.get("BOTDESK_HOME")
    path = Path(env).expanduser() if env else default_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
