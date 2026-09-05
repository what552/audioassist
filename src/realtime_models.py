"""Resolve realtime ASR selections to validated local model directories."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .model_errors import ModelNotReadyError
from .model_manager import ModelManager


_DEFAULT_MODEL_IDS = {
    "qwen": "qwen3-asr-1.7b",
    "whisper": "whisper-large-v3-turbo",
}


@dataclass(frozen=True)
class RealtimeModel:
    engine: str
    model_id: str
    local_path: str


def resolve_realtime_model(
    *,
    engine: str = "qwen",
    model_id: str | None = None,
    manager: ModelManager | None = None,
) -> RealtimeModel:
    """Return the selected realtime ASR model without downloading anything."""
    if engine not in _DEFAULT_MODEL_IDS:
        raise ValueError(f"Unsupported realtime ASR engine: {engine!r}")

    manager = manager or ModelManager()
    selected_id = model_id or _DEFAULT_MODEL_IDS[engine]
    info = manager.get_model(selected_id)
    if info is None or info.role != "asr":
        raise ValueError(f"Unknown realtime ASR model: {selected_id!r}")

    selected_engine = "whisper" if info.engine == "mlx-whisper" else info.engine
    if selected_engine not in _DEFAULT_MODEL_IDS:
        raise ValueError(
            f"Unsupported realtime ASR model engine: {info.engine!r}"
        )

    if not manager.is_downloaded(selected_id):
        raise ModelNotReadyError(selected_id, "model_not_downloaded")
    local_path = manager.local_path(selected_id)
    if not local_path or not os.path.isdir(local_path):
        raise ModelNotReadyError(selected_id, "model_incomplete")

    return RealtimeModel(
        engine=selected_engine,
        model_id=selected_id,
        local_path=local_path,
    )
