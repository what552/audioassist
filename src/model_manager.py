"""
Model manager: catalog, download, delete, and select ASR models.
Designed to be called by both CLI and UI layers.
"""
from __future__ import annotations
import os
import json
import shutil
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from platformdirs import user_data_dir

logger = logging.getLogger(__name__)

APP_DATA_DIR = user_data_dir("TranscribeApp", appauthor=False)
DEFAULT_MODELS_DIR = os.path.join(APP_DATA_DIR, "models")
CONFIG_PATH = os.path.join(APP_DATA_DIR, "config.json")


# ── Model catalog ─────────────────────────────────────────────────────────────

@dataclass
class ModelInfo:
    id: str
    name: str
    description: str
    repo_id: str
    size_gb: float
    engine: str        # "qwen" | "mlx-whisper"
    role: str          # "asr" | "aligner"
    languages: list[str] = field(default_factory=list)
    recommended: bool = False


CATALOG: list[ModelInfo] = [
    ModelInfo(
        id="qwen3-asr-1.7b",
        name="Qwen3-ASR 1.7B",
        description="中文最强，支持30+语言及22种中国方言，字级时间戳需配合 ForcedAligner",
        repo_id="Qwen/Qwen3-ASR-1.7B",
        size_gb=3.5,
        engine="qwen",
        role="asr",
        languages=["zh", "en", "yue", "ja", "ko", "fr", "de", "es"],
        recommended=True,
    ),
    ModelInfo(
        id="qwen3-forced-aligner",
        name="Qwen3 ForcedAligner 0.6B",
        description="配合 Qwen3-ASR 使用，提供字级时间戳（11种语言）",
        repo_id="Qwen/Qwen3-ForcedAligner-0.6B",
        size_gb=1.2,
        engine="qwen",
        role="aligner",
        languages=["zh", "en", "ja", "ko", "fr", "de", "es", "ru", "ar", "hi", "pt"],
    ),
    ModelInfo(
        id="whisper-large-v3-turbo",
        name="Whisper Large v3 Turbo",
        description="Apple Silicon 专属，Neural Engine 加速，速度快8倍，适合实时转录",
        repo_id="mlx-community/whisper-large-v3-turbo",
        size_gb=0.8,
        engine="mlx-whisper",
        role="asr",
        languages=["zh", "en", "ja", "ko", "fr", "de", "es", "ru"],
        recommended=True,
    ),
    ModelInfo(
        id="whisper-large-v3",
        name="Whisper Large v3",
        description="Apple Silicon 专属，最高质量，比 turbo 慢但更准确",
        repo_id="mlx-community/whisper-large-v3-mlx",
        size_gb=3.1,
        engine="mlx-whisper",
        role="asr",
        languages=["zh", "en", "ja", "ko", "fr", "de", "es", "ru"],
    ),
    ModelInfo(
        id="whisper-medium",
        name="Whisper Medium",
        description="轻量版，速度最快，适合实时预览",
        repo_id="mlx-community/whisper-medium-mlx",
        size_gb=0.5,
        engine="mlx-whisper",
        role="asr",
        languages=["zh", "en", "ja", "ko", "fr", "de", "es"],
    ),
]


# ── ModelManager ──────────────────────────────────────────────────────────────

class ModelManager:
    def __init__(self, models_dir: str = DEFAULT_MODELS_DIR):
        self.models_dir = models_dir
        os.makedirs(models_dir, exist_ok=True)

    # ── Catalog ───────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        """Return catalog with download status for each model."""
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "size_gb": m.size_gb,
                "engine": m.engine,
                "role": m.role,
                "languages": m.languages,
                "recommended": m.recommended,
                "downloaded": self.is_downloaded(m.id),
                "local_path": self.local_path(m.id),
            }
            for m in CATALOG
        ]

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        return next((m for m in CATALOG if m.id == model_id), None)

    def local_path(self, model_id: str) -> str:
        return os.path.join(self.models_dir, model_id)

    def is_downloaded(self, model_id: str) -> bool:
        path = self.local_path(model_id)
        return os.path.isdir(path) and bool(os.listdir(path))

    # ── Download ──────────────────────────────────────────────────────────────

    def download(
        self,
        model_id: str,
        hf_endpoint: str = "https://hf-mirror.com",
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> str:
        """
        Download a model from HuggingFace (via mirror).

        Args:
            model_id: Model ID from catalog.
            hf_endpoint: HuggingFace endpoint URL.
            progress_callback: Called with (percent 0.0-1.0, message) during download.

        Returns:
            Local path to downloaded model.
        """
        info = self.get_model(model_id)
        if info is None:
            raise ValueError(f"Unknown model: {model_id}")

        local = self.local_path(model_id)
        if self.is_downloaded(model_id):
            logger.info(f"Already downloaded: {model_id}")
            if progress_callback:
                progress_callback(1.0, f"Already downloaded: {info.name}")
            return local

        from huggingface_hub import snapshot_download

        if progress_callback:
            progress_callback(0.0, f"Downloading {info.name} ({info.size_gb}GB)...")

        logger.info(f"Downloading {info.repo_id} → {local}")
        snapshot_download(
            repo_id=info.repo_id,
            local_dir=local,
            endpoint=hf_endpoint,
        )

        if progress_callback:
            progress_callback(1.0, f"Downloaded: {info.name}")

        return local

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete(self, model_id: str):
        """Delete a downloaded model from disk."""
        local = self.local_path(model_id)
        if os.path.isdir(local):
            shutil.rmtree(local)
            logger.info(f"Deleted: {model_id}")
        cfg = self._load_config()
        changed = False
        for key in ("asr_model", "aligner_model"):
            if cfg.get(key) == model_id:
                del cfg[key]
                changed = True
        if changed:
            self._save_config(cfg)

    # ── Selection ─────────────────────────────────────────────────────────────

    def select_asr_model(self, model_id: str):
        info = self.get_model(model_id)
        if info is None or info.role != "asr":
            raise ValueError(f"Not an ASR model: {model_id}")
        if not self.is_downloaded(model_id):
            raise RuntimeError(f"Model not downloaded: {model_id}")
        cfg = self._load_config()
        cfg["asr_model"] = model_id
        self._save_config(cfg)

    def select_aligner_model(self, model_id: str):
        info = self.get_model(model_id)
        if info is None or info.role != "aligner":
            raise ValueError(f"Not an aligner model: {model_id}")
        if not self.is_downloaded(model_id):
            raise RuntimeError(f"Model not downloaded: {model_id}")
        cfg = self._load_config()
        cfg["aligner_model"] = model_id
        self._save_config(cfg)

    def get_selected_asr(self) -> Optional[str]:
        """Return selected ASR model ID, or first downloaded recommended one."""
        cfg = self._load_config()
        if "asr_model" in cfg and self.is_downloaded(cfg["asr_model"]):
            return cfg["asr_model"]
        for m in CATALOG:
            if m.role == "asr" and m.recommended and self.is_downloaded(m.id):
                return m.id
        for m in CATALOG:
            if m.role == "asr" and self.is_downloaded(m.id):
                return m.id
        return None

    def get_selected_aligner(self) -> Optional[str]:
        cfg = self._load_config()
        if "aligner_model" in cfg and self.is_downloaded(cfg["aligner_model"]):
            return cfg["aligner_model"]
        for m in CATALOG:
            if m.role == "aligner" and self.is_downloaded(m.id):
                return m.id
        return None

    # ── Config I/O ────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                return json.load(f)
        return {}

    def _save_config(self, cfg: dict):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
