"""Tests for app.API.get_setup_status()."""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch

from app import API
import src.model_manager as _mm_module
from src.model_manager import _DIARIZER_REQUIRED_FILES


def _create_diarizer_files(directory: str) -> None:
    for rel in _DIARIZER_REQUIRED_FILES:
        full = os.path.join(directory, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    models_dir = str(tmp_path / "models")
    os.makedirs(models_dir)

    # Patch ModelManager so any ModelManager() call uses our tmp models_dir
    # and never reads the developer's real HF cache.
    _OrigMM = _mm_module.ModelManager

    class _TestManager(_OrigMM):
        def __init__(self, *args, **kwargs):
            super().__init__(models_dir=models_dir)
        def _hf_cache_path(self, model_id):
            return None

    monkeypatch.setattr(_mm_module, "ModelManager", _TestManager)
    return API(), models_dir


class TestGetSetupStatus:
    def test_both_not_ready_when_nothing_downloaded(self, env):
        api, _ = env
        status = api.get_setup_status()
        assert status["asr_ready"] is False
        assert status["diarizer_ready"] is False

    def test_asr_ready_when_recommended_asr_downloaded(self, env):
        api, models_dir = env
        asr_dir = os.path.join(models_dir, "qwen3-asr-1.7b")
        os.makedirs(asr_dir)
        open(os.path.join(asr_dir, "config.json"), "w").close()

        status = api.get_setup_status()
        assert status["asr_ready"] is True
        assert status["diarizer_ready"] is False

    def test_diarizer_ready_when_recommended_diarizer_downloaded(self, env):
        api, models_dir = env
        diar_dir = os.path.join(models_dir, "pyannote-diarization-community-1")
        os.makedirs(diar_dir)
        _create_diarizer_files(diar_dir)

        status = api.get_setup_status()
        assert status["asr_ready"] is False
        assert status["diarizer_ready"] is True

    def test_both_ready_when_both_downloaded(self, env):
        api, models_dir = env
        # ASR
        asr_dir = os.path.join(models_dir, "qwen3-asr-1.7b")
        os.makedirs(asr_dir)
        open(os.path.join(asr_dir, "config.json"), "w").close()
        # Diarizer
        diar_dir = os.path.join(models_dir, "pyannote-diarization-community-1")
        os.makedirs(diar_dir)
        _create_diarizer_files(diar_dir)

        status = api.get_setup_status()
        assert status["asr_ready"] is True
        assert status["diarizer_ready"] is True

    def test_returns_dict_with_expected_keys(self, env):
        api, _ = env
        status = api.get_setup_status()
        assert "asr_ready" in status
        assert "diarizer_ready" in status
        assert "runtime" in status

    def test_runtime_status_forwarded(self, env):
        api, _ = env
        fake_runtime = {
            "severity": "warning",
            "message": "Detected NVIDIA GPU but torch is CPU-only.",
            "needs_cuda_torch": True,
        }
        with patch("src.runtime_env.get_runtime_status", return_value=fake_runtime):
            status = api.get_setup_status()
        assert status["runtime"] == fake_runtime
