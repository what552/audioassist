"""Realtime model selection must resolve to validated local directories."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.model_errors import ModelNotReadyError
from src.realtime_models import resolve_realtime_model


def _manager(model_dir, *, downloaded=True, info=None):
    manager = MagicMock()
    manager.get_model.return_value = info or SimpleNamespace(
        id="qwen3-asr-1.7b", role="asr", engine="qwen"
    )
    manager.is_downloaded.return_value = downloaded
    manager.local_path.return_value = str(model_dir)
    return manager


def test_default_qwen_resolves_validated_local_path(tmp_path):
    model_dir = tmp_path / "qwen"
    model_dir.mkdir()
    manager = _manager(model_dir)

    resolved = resolve_realtime_model(manager=manager)

    assert resolved.engine == "qwen"
    assert resolved.model_id == "qwen3-asr-1.7b"
    assert resolved.local_path == str(model_dir)
    manager.is_downloaded.assert_called_once_with("qwen3-asr-1.7b")
    manager.download.assert_not_called()


def test_explicit_whisper_model_selects_whisper_backend(tmp_path):
    model_dir = tmp_path / "whisper"
    model_dir.mkdir()
    manager = _manager(
        model_dir,
        info=SimpleNamespace(
            id="whisper-medium", role="asr", engine="mlx-whisper"
        ),
    )

    resolved = resolve_realtime_model(
        engine="qwen", model_id="whisper-medium", manager=manager
    )

    assert resolved.engine == "whisper"
    assert resolved.model_id == "whisper-medium"
    assert resolved.local_path == str(model_dir)


def test_missing_model_fails_without_downloading(tmp_path):
    manager = _manager(tmp_path / "missing", downloaded=False)

    with pytest.raises(ModelNotReadyError) as exc_info:
        resolve_realtime_model(manager=manager)

    assert exc_info.value.reason == "model_not_downloaded"
    manager.download.assert_not_called()


def test_incomplete_model_path_fails_without_downloading(tmp_path):
    manager = _manager(tmp_path / "missing", downloaded=True)

    with pytest.raises(ModelNotReadyError) as exc_info:
        resolve_realtime_model(manager=manager)

    assert exc_info.value.reason == "model_incomplete"
    manager.download.assert_not_called()


@pytest.mark.parametrize(
    "info",
    [
        None,
        SimpleNamespace(id="aligner", role="aligner", engine="qwen"),
    ],
)
def test_unknown_or_non_asr_model_is_rejected(tmp_path, info):
    manager = _manager(tmp_path, info=info)
    if info is None:
        manager.get_model.return_value = None

    with pytest.raises(ValueError, match="Unknown realtime ASR model"):
        resolve_realtime_model(model_id="invalid", manager=manager)


def test_unknown_engine_is_rejected_before_model_lookup():
    manager = MagicMock()

    with pytest.raises(ValueError, match="Unsupported realtime ASR engine"):
        resolve_realtime_model(engine="remote", manager=manager)

    manager.get_model.assert_not_called()
