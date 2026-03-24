"""Tests for F1: local-only model validation in pipeline.py"""
import pytest
from unittest.mock import MagicMock, patch

from src.pipeline import ModelNotReadyError, _validate_model_local


def _make_mm(is_downloaded=True, local_path=None):
    mm = MagicMock()
    mm.is_downloaded.return_value = is_downloaded
    mm.local_path.return_value = local_path
    return mm


def test_validate_model_local_returns_path(tmp_path):
    model_dir = tmp_path / "mymodel"
    model_dir.mkdir()
    mm = _make_mm(is_downloaded=True, local_path=str(model_dir))
    result = _validate_model_local(mm, "my-model-id")
    assert result == str(model_dir)


def test_run_raises_model_not_downloaded(tmp_path):
    """pipeline.run() should raise ModelNotReadyError when ASR model is not downloaded."""
    import os
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    mm = _make_mm(is_downloaded=False)

    with patch("src.pipeline.ModelManager", return_value=mm), \
         patch("src.pipeline.to_wav", return_value=(str(audio), False)), \
         patch("src.pipeline.split_to_chunks", return_value=[(str(audio), 0.0)]):
        from src.pipeline import run
        with pytest.raises(ModelNotReadyError) as exc_info:
            run(
                audio_path=str(audio),
                output_dir=str(tmp_path),
                engine="qwen",
            )
    assert exc_info.value.reason == "model_not_downloaded"
    assert "qwen3-asr-1.7b" in exc_info.value.model_id


def test_run_raises_model_incomplete(tmp_path):
    """pipeline.run() should raise ModelNotReadyError when model dir is missing."""
    import os
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    mm = _make_mm(is_downloaded=True, local_path=str(tmp_path / "nonexistent_dir"))

    with patch("src.pipeline.ModelManager", return_value=mm), \
         patch("src.pipeline.to_wav", return_value=(str(audio), False)), \
         patch("src.pipeline.split_to_chunks", return_value=[(str(audio), 0.0)]):
        from src.pipeline import run
        with pytest.raises(ModelNotReadyError) as exc_info:
            run(
                audio_path=str(audio),
                output_dir=str(tmp_path),
                engine="qwen",
            )
    assert exc_info.value.reason == "model_incomplete"


def test_validate_model_local_raises_not_downloaded():
    mm = _make_mm(is_downloaded=False)
    with pytest.raises(ModelNotReadyError) as exc_info:
        _validate_model_local(mm, "some-model")
    assert exc_info.value.reason == "model_not_downloaded"
    assert exc_info.value.model_id == "some-model"


def test_validate_model_local_raises_incomplete(tmp_path):
    mm = _make_mm(is_downloaded=True, local_path=str(tmp_path / "nonexistent"))
    with pytest.raises(ModelNotReadyError) as exc_info:
        _validate_model_local(mm, "some-model")
    assert exc_info.value.reason == "model_incomplete"


def test_model_not_ready_error_message():
    err = ModelNotReadyError("my-model", "model_not_downloaded")
    assert "my-model" in str(err)
    assert "not ready" in str(err)
    assert "Model Library" in str(err)
