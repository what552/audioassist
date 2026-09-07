"""Shared persisted preferences; no UI or inference-runtime imports at startup."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading

from .asr_factory import QWEN_BACKEND_ENV, select_qwen_backend

_lock = threading.RLock()
BACKENDS = ("auto", "pytorch-cpu", "pytorch-mps", "mlx", "pytorch-cuda")


def read_config(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("Application configuration must be a JSON object")
    return data


def update_config(path: str, update) -> None:
    """Serialize in-process settings writes and atomically replace the file."""
    with _lock:
        data = read_config(path)
        update(data)
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".config-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate_backend(value: str) -> str:
    if not isinstance(value, str) or value not in BACKENDS:
        raise ValueError("Unknown Qwen backend; choose auto, pytorch-cpu, pytorch-mps, mlx or pytorch-cuda")
    return value


def default_backend(path: str | None) -> str:
    """Snapshot a job preference. Explicit environment overrides remain supported."""
    override = os.getenv(QWEN_BACKEND_ENV)
    return validate_backend(override.strip().lower() if override is not None
                            else read_config(path).get("qwen_backend", "auto"))


def backend_options() -> list[dict]:
    labels = {"auto": "Auto (CPU on Mac)", "pytorch-cpu": "CPU",
              "pytorch-mps": "MPS — Experimental", "mlx": "MLX — Experimental",
              "pytorch-cuda": "CUDA"}
    options = []
    for value in BACKENDS:
        reason = ""
        try:
            select_qwen_backend(value)
            if value == "mlx" and importlib.util.find_spec("mlx_qwen3_asr") is None:
                raise RuntimeError("MLX Qwen runtime is not installed")
        except Exception as exc:
            reason = str(exc)
        options.append({"value": value, "label": labels[value],
                        "available": not reason, "reason": reason})
    return options


def get_backend_settings(path: str | None) -> dict:
    saved = validate_backend(read_config(path).get("qwen_backend", "auto"))
    effective = default_backend(path)
    return {"saved": saved, "effective": effective,
            "environment_override": os.getenv(QWEN_BACKEND_ENV) is not None,
            "options": backend_options()}


def save_backend_settings(path: str, value: str) -> None:
    validate_backend(value)
    option = next(item for item in backend_options() if item["value"] == value)
    if not option["available"]:
        raise ValueError(option["reason"])
    update_config(path, lambda data: data.update(qwen_backend=value))
