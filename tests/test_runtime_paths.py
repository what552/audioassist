"""Tests for src.runtime_paths."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from src import runtime_paths


def test_bundle_root_defaults_to_repo_root():
    root = runtime_paths.bundle_root()
    assert (root / "run.py").exists()


def test_bundle_root_uses_meipass_when_present(tmp_path):
    with patch.object(sys, "_MEIPASS", str(tmp_path), create=True):
        assert runtime_paths.bundle_root() == Path(tmp_path)


def test_resolve_tool_path_prefers_env_override(tmp_path, monkeypatch):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    monkeypatch.setenv("AUDIOASSIST_FFMPEG_PATH", str(ffmpeg))

    assert runtime_paths.resolve_tool_path("ffmpeg") == str(ffmpeg)


def test_resolve_tool_path_prefers_bundled_binary(tmp_path):
    bundled_dir = tmp_path / "ffmpeg-bin"
    bundled_dir.mkdir()
    ffprobe = bundled_dir / "ffprobe.exe"
    ffprobe.write_bytes(b"")

    with patch.object(runtime_paths, "bundle_root", return_value=tmp_path), \
         patch("shutil.which", return_value=None):
        assert runtime_paths.resolve_tool_path("ffprobe") == str(ffprobe)


def test_resolve_tool_path_falls_back_to_system_path():
    with patch.object(runtime_paths, "bundle_root", return_value=Path("Z:/missing")), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        assert runtime_paths.resolve_tool_path("ffmpeg") == "/usr/bin/ffmpeg"
