# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Windows onedir desktop builds.

Expected environment:
  AUDIOASSIST_FFMPEG_DIR=C:\\path\\to\\ffmpeg\\bin
"""
from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


ROOT = Path(SPECPATH).resolve().parents[1]
ENTRY = ROOT / "run.py"
UI_DIR = ROOT / "ui"


def _safe_collect_submodules(package_name: str) -> list[str]:
    try:
        return collect_submodules(package_name)
    except Exception:
        return []


def _safe_collect_data(package_name: str) -> list[tuple[str, str]]:
    try:
        return collect_data_files(package_name)
    except Exception:
        return []


def _safe_collect_dynamic_libs(package_name: str) -> list[tuple[str, str]]:
    try:
        return collect_dynamic_libs(package_name)
    except Exception:
        return []


def _safe_copy_metadata(dist_name: str) -> list[tuple[str, str]]:
    try:
        return copy_metadata(dist_name)
    except Exception:
        return []


def _ffmpeg_binaries() -> list[tuple[str, str]]:
    ffmpeg_dir = os.environ.get("AUDIOASSIST_FFMPEG_DIR", "").strip()
    if not ffmpeg_dir:
        raise SystemExit(
            "AUDIOASSIST_FFMPEG_DIR is required. Point it to a directory "
            "containing ffmpeg.exe and ffprobe.exe."
        )

    base = Path(ffmpeg_dir).expanduser().resolve()
    binaries: list[tuple[str, str]] = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        path = base / name
        if not path.is_file():
            raise SystemExit(f"Missing required FFmpeg binary: {path}")
        binaries.append((str(path), "ffmpeg-bin"))
    return binaries


datas = [(str(UI_DIR), "ui")]
for package_name in ("webview", "_sounddevice_data", "pyannote", "qwen_asr", "silero_vad"):
    datas += _safe_collect_data(package_name)
for dist_name in ("pywebview", "qwen_asr", "huggingface_hub", "openai", "faster_whisper"):
    datas += _safe_copy_metadata(dist_name)


binaries = _ffmpeg_binaries()
for package_name in ("torch", "torchaudio", "soundfile", "ctranslate2"):
    binaries += _safe_collect_dynamic_libs(package_name)


hiddenimports: list[str] = []
for package_name in (
    "webview",
    "pyannote",
    "qwen_asr",
    "silero_vad",
    "faster_whisper",
):
    hiddenimports += _safe_collect_submodules(package_name)


excludes = [
    "playwright",
    "pytest",
    "pytest_playwright",
]


a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AudioAssist",
)
