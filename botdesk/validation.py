"""Input validation and safe file names."""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

COMMAND_NAME_RE = re.compile(r"[a-z0-9_]{1,32}")
WINDOW_MIN = 30
WINDOW_MAX = 86400
WINDOW_ERROR = f"Enter a whole number of seconds from {WINDOW_MIN} to {WINDOW_MAX}."
MIN_PASSWORD_LENGTH = 8
TELEGRAM_TEXT_LIMIT = 4096
TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")


def command_name_error(name: str, existing: set[str]) -> str | None:
    """Return an error message, or None if the name is valid."""
    if not COMMAND_NAME_RE.fullmatch(name):
        return "Use 1 to 32 lowercase letters, digits or underscores."
    if name in existing:
        return f"/{name} already exists."
    return None


def parse_window(text: str) -> int:
    """Parse the live chat window in seconds. Raises ValueError with a message."""
    value = text.strip()
    if not value.isascii() or not value.isdigit():
        raise ValueError(WINDOW_ERROR)
    seconds = int(value)
    if not WINDOW_MIN <= seconds <= WINDOW_MAX:
        raise ValueError(WINDOW_ERROR)
    return seconds


def looks_like_token(text: str) -> bool:
    return bool(TOKEN_RE.fullmatch(text.strip()))


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
_MAX_NAME = 150


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """Strip path separators and characters invalid on Windows."""
    clean = _INVALID_CHARS.sub("", name).strip().rstrip(". ")
    if not clean:
        clean = fallback
    if clean.split(".")[0].upper() in _RESERVED:
        clean = "_" + clean
    if len(clean) > _MAX_NAME:
        suffix = Path(clean).suffix
        if len(suffix) > 20:
            suffix = ""
        clean = clean[: _MAX_NAME - len(suffix)].rstrip(". ") + suffix
    return clean


def reserve_unique_file(folder: Path, name: str) -> tuple[Path, BinaryIO]:
    """Create a new, empty file in folder; add _1, _2, ... if the name exists.

    Returns the path and the open binary handle. Creation is exclusive, so an
    existing file is never overwritten.
    """
    path = folder / name
    stem, suffix = path.stem, path.suffix
    counter = 0
    while True:
        try:
            return path, open(path, "xb")
        except FileExistsError:
            counter += 1
            path = folder / f"{stem}_{counter}{suffix}"
