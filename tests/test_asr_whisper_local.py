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


def test_mlx_transcribe_converts_nested_word_timestamps():
    transcribe = MagicMock(return_value={
        "text": " hello world ",
        "language": "en",
        "segments": [
            {"words": [
                {"word": " hello", "start": 0.1, "end": 0.4},
                {"word": " world", "start": 0.4, "end": 0.9},
            ]},
        ],
    })
    engine = WhisperASREngine(model_path="/models/local")
    engine._model = "/models/local"
    engine._mlx_whisper = SimpleNamespace(transcribe=transcribe)

    result = engine._transcribe_mlx("/audio/input.wav")

    assert result.text == "hello world"
    assert result.language == "en"
    assert [(word.word, word.start, word.end) for word in result.words] == [
        (" hello", 0.1, 0.4),
        (" world", 0.4, 0.9),
    ]


def test_faster_whisper_transcribe_converts_generator_and_word_timestamps():
    segments = iter([
        SimpleNamespace(
            text=" Hello",
            words=[SimpleNamespace(word=" Hello", start=0.2, end=0.6)],
        ),
        SimpleNamespace(
            text=" world",
            words=[SimpleNamespace(word=" world", start=0.6, end=1.0)],
        ),
    ])
    model = MagicMock()
    model.transcribe.return_value = (segments, SimpleNamespace(language="en"))
    engine = WhisperASREngine(model_path="/models/local")
    engine._backend = "faster-whisper"
    engine._model = model

    result = engine._transcribe_faster_whisper("/audio/input.wav")

    assert result.text == "Hello world"
    assert result.language == "en"
    assert [(word.word, word.start, word.end) for word in result.words] == [
        (" Hello", 0.2, 0.6),
        (" world", 0.6, 1.0),
    ]
    model.transcribe.assert_called_once_with(
        "/audio/input.wav",
        word_timestamps=True,
        language=None,
        beam_size=5,
        vad_filter=True,
    )
