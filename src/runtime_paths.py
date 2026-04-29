"""
Helpers for resolving resources and bundled binaries at runtime.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


_BUNDLED_BIN_DIR = "ffmpeg-bin"


def is_frozen_app() -> bool:
    """True when running from a frozen desktop bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """
    Return the directory that contains bundled resources.

    PyInstaller exposes ``sys._MEIPASS`` in frozen apps. During normal source
    execution, this falls back to the repository root.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _tool_filename(tool_name: str) -> str:
    if sys.platform.startswith("win"):
        return f"{tool_name}.exe"
    return tool_name


def bundled_tool_path(tool_name: str) -> Path | None:
    """Return the path to a bundled executable when present."""
    candidate = bundle_root() / _BUNDLED_BIN_DIR / _tool_filename(tool_name)
    if candidate.is_file():
        return candidate
    return None


def resolve_tool_path(tool_name: str) -> str:
    """
    Resolve an executable path in this order:

    1. explicit env override, e.g. ``AUDIOASSIST_FFMPEG_PATH``
    2. bundled binary inside the frozen app
    3. system PATH lookup
    4. bare tool name as last-resort fallback
    """
    env_name = f"AUDIOASSIST_{tool_name.upper()}_PATH"
    configured = os.environ.get(env_name, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)

    bundled = bundled_tool_path(tool_name)
    if bundled is not None:
        return str(bundled)

    return shutil.which(tool_name) or tool_name
