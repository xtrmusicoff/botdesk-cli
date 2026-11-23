"""Where BotDesk keeps its data."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Return the data folder, creating it if needed.

    Default: the `data` folder next to the `botdesk` package. Override with
    the BOTDESK_HOME environment variable.
    """
    env = os.environ.get("BOTDESK_HOME")
    path = Path(env).expanduser() if env else PROJECT_DIR / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
