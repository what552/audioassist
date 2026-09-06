"""QWEN-02 MLX adapter, lifecycle, local-model, and packaging tests."""
from __future__ import annotations

from importlib.metadata import distribution
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import MagicMock, patch

import pytest
import numpy as np

from src.asr_qwen_mlx import MLXQwenASREngine


@pytest.fixture(autouse=True)
def local_test_audio():
    with patch("src.asr_qwen_mlx.load_local_audio", return_value=np.zeros(16000, dtype=np.float32)):
        yield


def _mlx_modules():
    mlx_package = ModuleType("mlx")
    mx = ModuleType("mlx.core")
    mx.float16 = "mlx-float16"
    mx.clear_cache = MagicMock()
    mlx_package.core = mx

    package = ModuleType("mlx_qwen3_asr")
    package.Session = MagicMock()
    package.ForcedAligner = MagicMock()
    return mlx_package, mx, package


def test_load_uses_explicit_local_paths_and_full_precision():
    mlx_package, mx, package = _mlx_modules()
    with patch.dict(
        sys.modules,
        {"mlx": mlx_package, "mlx.core": mx, "mlx_qwen3_asr": package},
    ):
        engine = MLXQwenASREngine(
            model_path="/models/qwen",
            aligner_path="/models/aligner",
            with_timestamps=True,
        )
        engine.load()

    package.Session.assert_called_once_with(
        model="/models/qwen", dtype="mlx-float16"
    )
    package.ForcedAligner.assert_called_once_with(
        "/models/aligner", dtype="mlx-float16", backend="mlx"
    )


def test_realtime_load_omits_forced_aligner():
    mlx_package, mx, package = _mlx_modules()
    with patch.dict(
        sys.modules,
        {"mlx": mlx_package, "mlx.core": mx, "mlx_qwen3_asr": package},
    ):
        engine = MLXQwenASREngine(
            model_path="/models/qwen",
            aligner_path=None,
            with_timestamps=False,
        )
        engine.load()

    package.ForcedAligner.assert_not_called()


def test_failed_aligner_load_releases_partial_session():
    mlx_package, mx, package = _mlx_modules()
    package.Session.return_value = SimpleNamespace(
        model=object(), tokenizer=object(), config=object()
    )
    package.ForcedAligner.side_effect = RuntimeError("aligner failed")
    engine = MLXQwenASREngine(
        model_path="/models/qwen",
        aligner_path="/models/aligner",
        with_timestamps=True,
    )

    with patch.dict(
        sys.modules,
        {"mlx": mlx_package, "mlx.core": mx, "mlx_qwen3_asr": package},
    ), pytest.raises(RuntimeError, match="aligner failed"):
        engine.load()

    assert engine._session is None
    assert engine._aligner is None
    assert engine._mx is None
    mx.clear_cache.assert_called_once()


def test_transcribe_maps_language_and_word_segments(caplog):
    result = SimpleNamespace(
        text="hello world",
        language="English",
        segments=[
            {"text": "hello", "start": 0.1, "end": 0.5},
            {"text": " world", "start": 0.5, "end": 1.0},
        ],
        truncated=False,
        chunks=[{"language": "English", "finish_reason": "eos", "truncated": False,
                 "generated_tokens": 5, "max_new_tokens": 1024}],
    )
    session = MagicMock()
    session.transcribe.return_value = result
    aligner = object()
    engine = MLXQwenASREngine(
        model_path="/models/qwen",
        aligner_path="/models/aligner",
        with_timestamps=True,
    )
    engine._session = session
    engine._aligner = aligner

    converted = engine.transcribe("/audio/sample.wav")

    assert converted.text == "hello world"
    assert converted.language == "English"
    assert engine.last_truncated is False
    assert [(word.word, word.start, word.end) for word in converted.words] == [
        ("hello", 0.1, 0.5),
        (" world", 0.5, 1.0),
    ]
    assert session.transcribe.call_count == 1
    np.testing.assert_array_equal(session.transcribe.call_args.args[0], np.zeros(16000))
    assert session.transcribe.call_args.kwargs == {
        "return_timestamps": True, "forced_aligner": aligner,
        "return_chunks": True, "max_new_tokens": 1024,
    }
    assert engine.last_chunk_diagnostics[0]["generated_tokens"] == 5


def test_transcribe_recovers_empty_aggregate_language_from_chunks():
    result = SimpleNamespace(
        text="hello",
        language="",
        segments=[{"text": "hello", "start": 0.1, "end": 0.5}],
        chunks=[{"language": "Chinese", "generated_tokens": 5, "max_new_tokens": 1024,
                 "finish_reason": "eos", "truncated": False}],
        truncated=False,
    )
    session = MagicMock()
    session.transcribe.return_value = result
    engine = MLXQwenASREngine(
        model_path="/models/qwen",
        aligner_path="/models/aligner",
        with_timestamps=True,
    )
    engine._session = session

    converted = engine.transcribe("/audio/sample.wav")

    assert converted.language == "Chinese"


def test_close_clears_session_aligner_holders_and_mlx_cache():
    mx = SimpleNamespace(clear_cache=MagicMock())
    session = SimpleNamespace(model=object(), tokenizer=object(), config=object())
    aligner_backend = SimpleNamespace(
        model=object(), tokenizer=object(), config=object()
    )
    aligner = SimpleNamespace(_backend=aligner_backend)
    model_holder = SimpleNamespace(clear=MagicMock())
    tokenizer_holder = SimpleNamespace(clear=MagicMock())
    load_models = ModuleType("mlx_qwen3_asr.load_models")
    load_models._ModelHolder = model_holder
    tokenizer = ModuleType("mlx_qwen3_asr.tokenizer")
    tokenizer._TokenizerHolder = tokenizer_holder

    engine = MLXQwenASREngine(
        model_path="/models/qwen",
        aligner_path="/models/aligner",
        with_timestamps=True,
    )
    engine._session = session
    engine._aligner = aligner
    engine._mx = mx

    with patch.dict(
        sys.modules,
        {
            "mlx_qwen3_asr.load_models": load_models,
            "mlx_qwen3_asr.tokenizer": tokenizer,
        },
    ):
        engine.close()

    assert engine._session is None
    assert engine._aligner is None
    assert session.model is None
    assert session.tokenizer is None
    assert session.config is None
    assert aligner._backend is None
    assert aligner_backend.model is None
    model_holder.clear.assert_called_once()
    tokenizer_holder.clear.assert_called_once()
    mx.clear_cache.assert_called_once()


def test_close_logs_traceback_when_aligner_backend_cannot_be_released(caplog):
    class StubbornAligner:
        _backend = SimpleNamespace(model=None, tokenizer=None, config=None)

        def __setattr__(self, name, value):
            if name == "_backend":
                raise RuntimeError("read-only backend")
            object.__setattr__(self, name, value)

    model_holder = SimpleNamespace(clear=MagicMock())
    tokenizer_holder = SimpleNamespace(clear=MagicMock())
    load_models = ModuleType("mlx_qwen3_asr.load_models")
    load_models._ModelHolder = model_holder
    tokenizer = ModuleType("mlx_qwen3_asr.tokenizer")
    tokenizer._TokenizerHolder = tokenizer_holder
    engine = MLXQwenASREngine(
        model_path="/models/qwen",
        aligner_path="/models/aligner",
        with_timestamps=True,
    )
    engine._aligner = StubbornAligner()
    caplog.set_level(logging.DEBUG, logger="src.asr_qwen_mlx")

    with patch.dict(
        sys.modules,
        {
            "mlx_qwen3_asr.load_models": load_models,
            "mlx_qwen3_asr.tokenizer": tokenizer,
        },
    ):
        engine.close()

    record = next(
        item for item in caplog.records
        if "release MLX aligner backend" in item.getMessage()
    )
    assert record.exc_info is not None


def test_adapter_requires_aligner_for_timestamps():
    with pytest.raises(ValueError, match="aligner_path"):
        MLXQwenASREngine(
            model_path="/models/qwen",
            aligner_path=None,
            with_timestamps=True,
        )


def test_pinned_package_assets_exist():
    installed_files = {
        str(path) for path in (distribution("mlx-qwen3-asr").files or [])
    }
    assert "mlx_qwen3_asr/assets/mel_filters.npz" in installed_files
    assert "mlx_qwen3_asr/assets/korean_dict_jieba.dict" in installed_files


def test_pyinstaller_collects_focused_mlx_qwen_resources():
    spec = (Path(__file__).resolve().parents[1] / "AudioAssist.spec").read_text(
        encoding="utf-8"
    )
    assert 'collect_data_files("mlx_qwen3_asr")' in spec
    assert 'copy_metadata("mlx-qwen3-asr")' in spec
    assert 'collect_submodules("mlx_qwen3_asr")' not in spec
