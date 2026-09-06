"""Internal Qwen backend selection and construction.

The public AudioAssist engine value remains ``qwen``.  This module owns the
private backend choice so pipelines and capture adapters do not depend on a
particular inference framework.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import platform
import sys
from typing import Protocol, runtime_checkable

from .types import TranscriptResult


QWEN_BACKEND_ENV = "AUDIOASSIST_QWEN_BACKEND"
_VALID_OVERRIDES = {
    "auto",
    "mlx",
    "pytorch-cpu",
    "pytorch-cuda",
    "pytorch-mps",
}


@runtime_checkable
class QwenASREngineProtocol(Protocol):
    """Application-owned interface implemented by every Qwen backend."""

    backend_name: str

    def load(self) -> None: ...

    def transcribe(self, audio_path: str) -> TranscriptResult: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class QwenBackendConfig:
    name: str
    device: str
    dtype_name: str


def select_qwen_backend(
    override: str | None = None,
    *,
    torch_module=None,
    sys_platform: str | None = None,
    machine: str | None = None,
) -> QwenBackendConfig:
    """Select a supported Qwen backend without importing an adapter itself.

    Automatic selection intentionally keeps the existing CUDA/CPU behavior.
    ``mlx`` and ``pytorch-mps`` remain explicit overrides until QWEN-03.
    """
    choice = (override or os.getenv(QWEN_BACKEND_ENV, "auto")).strip().lower()
    if choice not in _VALID_OVERRIDES:
        valid = ", ".join(sorted(_VALID_OVERRIDES))
        raise ValueError(
            f"Invalid {QWEN_BACKEND_ENV} value {choice!r}; expected one of: {valid}"
        )

    if choice == "mlx":
        current_platform = sys_platform or sys.platform
        current_machine = machine or platform.machine()
        if current_platform != "darwin" or current_machine != "arm64":
            raise RuntimeError("Qwen MLX backend requires Apple Silicon macOS")
        return QwenBackendConfig("mlx", "metal", "float16")

    if torch_module is None:
        import torch as torch_module

    if choice == "auto":
        choice = "pytorch-cuda" if torch_module.cuda.is_available() else "pytorch-cpu"

    if choice == "pytorch-cuda":
        if not torch_module.cuda.is_available():
            raise RuntimeError("Qwen PyTorch CUDA backend requested, but CUDA is unavailable")
        return QwenBackendConfig(choice, "cuda", "float16")

    if choice == "pytorch-mps":
        current_platform = sys_platform or sys.platform
        current_machine = machine or platform.machine()
        mps = getattr(getattr(torch_module, "backends", None), "mps", None)
        if (
            current_platform != "darwin"
            or current_machine != "arm64"
            or mps is None
            or not mps.is_available()
        ):
            raise RuntimeError(
                "Qwen PyTorch MPS diagnostic backend requires Apple Silicon "
                "and an available PyTorch MPS runtime"
            )
        # qwen-asr 0.0.6 / torch 2.10 can terminate early on MPS fp16.
        # Keep ASR and alignment fp32, with bounded chunks in the adapter.
        return QwenBackendConfig(choice, "mps", "float32")

    return QwenBackendConfig("pytorch-cpu", "cpu", "float32")


def create_qwen_engine(
    *,
    model_path: str,
    aligner_path: str | None,
    with_timestamps: bool,
    backend_override: str | None = None,
) -> QwenASREngineProtocol:
    """Create the selected Qwen adapter using validated local directories."""
    if not model_path or not os.path.isdir(model_path):
        raise ValueError("Qwen model_path must be an existing local directory")
    if with_timestamps and (not aligner_path or not os.path.isdir(aligner_path)):
        raise ValueError(
            "Qwen timestamps require an existing local Forced Aligner directory"
        )

    config = select_qwen_backend(backend_override)

    if config.name == "mlx":
        from .asr_qwen_mlx import MLXQwenASREngine

        return MLXQwenASREngine(
            with_timestamps=with_timestamps,
            model_path=model_path,
            aligner_path=aligner_path,
        )

    # Lazy import keeps backend-specific modules out of non-Qwen startup paths.
    from .asr import ASREngine

    return ASREngine(
        with_timestamps=with_timestamps,
        model_path=model_path,
        aligner_path=aligner_path,
        backend=config,
    )
