"""Whisper backends must consume validated local model paths when provided."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.asr_whisper import MLX_MODELS, WhisperASREngine


def test_mlx_loader_uses_explicit_local_model_path():
    fake_module = SimpleNamespace()
    engine = WhisperASREngine(size="turbo", model_path="/models/whisper-turbo")

    with patch.dict(sys.modules, {"mlx_whisper": fake_module}):
        engine._load_mlx()

    assert engine._model == "/models/whisper-turbo"
    assert engine._mlx_whisper is fake_module


def test_mlx_loader_keeps_catalog_fallback_without_local_path():
    engine = WhisperASREngine(size="turbo")

    with patch.dict(sys.modules, {"mlx_whisper": SimpleNamespace()}):
        engine._load_mlx()

    assert engine._model == MLX_MODELS["turbo"]


def test_faster_whisper_loader_uses_explicit_local_model_path():
    whisper_model = MagicMock()
    engine = WhisperASREngine(size="large", model_path="/models/whisper-large")

    with patch.dict(
        sys.modules,
        {"faster_whisper": SimpleNamespace(WhisperModel=whisper_model)},
    ):
        engine._load_faster_whisper()

    whisper_model.assert_called_once_with(
        "/models/whisper-large", device="cpu", compute_type="int8"
    )


def test_mlx_transcribe_uses_loaded_local_path():
    transcribe = MagicMock(return_value={"text": " hello ", "language": "en", "segments": []})
    engine = WhisperASREngine(model_path="/models/local")
    engine._model = "/models/local"
    engine._mlx_whisper = SimpleNamespace(transcribe=transcribe)

    result = engine._transcribe_mlx("/audio/input.wav")

    assert result.text == "hello"
    transcribe.assert_called_once()
    assert transcribe.call_args.kwargs["path_or_hf_repo"] == "/models/local"
