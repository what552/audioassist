"""
Whisper-based ASR engine using mlx-whisper (Apple Silicon, MLX framework).
Falls back to faster-whisper on non-Apple platforms.

Same interface as src/asr.py: transcribe(audio_path) -> TranscriptResult
"""
from __future__ import annotations
from typing import Optional
import logging
import platform
import sys

from .types import WordSegment, TranscriptResult

logger = logging.getLogger(__name__)


def _is_apple_silicon() -> bool:
    """True on Apple Silicon (M1/M2/M3/M4) Macs."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


# mlx-whisper model IDs (auto-downloaded from HuggingFace or local path)
MLX_MODELS = {
    "tiny":   "mlx-community/whisper-tiny-mlx",
    "small":  "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large":  "mlx-community/whisper-large-v3-mlx",
    "turbo":  "mlx-community/whisper-large-v3-turbo",  # recommended
}

# faster-whisper model IDs (for non-Apple platforms)
FW_MODELS = {
    "tiny":   "tiny",
    "small":  "small",
    "medium": "medium",
    "large":  "large-v3",
    "turbo":  "large-v3",
}


class WhisperASREngine:
    """
    Whisper ASR engine with automatic backend selection:
      - Apple Silicon: mlx-whisper (fastest)
      - Other:         faster-whisper (CPU/CUDA)
    """

    def __init__(
        self,
        size: str = "turbo",
        language: Optional[str] = None,
        hf_endpoint: Optional[str] = None,
    ):
        self.size = size
        self.language = language
        self.hf_endpoint = hf_endpoint
        self._model = None
        self._backend: str = "mlx" if _is_apple_silicon() else "faster-whisper"

    def load(self):
        if self._backend == "mlx":
            self._load_mlx()
        else:
            self._load_faster_whisper()

    def _load_mlx(self):
        try:
            import mlx_whisper
        except ImportError:
            raise ImportError("Run: pip install mlx-whisper")

        import os
        if self.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", self.hf_endpoint)
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

        model_id = MLX_MODELS.get(self.size, MLX_MODELS["turbo"])
        logger.info(f"Loading mlx-whisper: {model_id}")
        self._model = model_id
        self._mlx_whisper = mlx_whisper
        logger.info("mlx-whisper ready.")

    def _load_faster_whisper(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError("Run: pip install faster-whisper")

        model_id = FW_MODELS.get(self.size, "large-v3")
        logger.info(f"Loading faster-whisper: {model_id}")
        self._model = WhisperModel(model_id, device="cpu", compute_type="int8")
        logger.info("faster-whisper ready.")

    def transcribe(self, audio_path: str) -> TranscriptResult:
        if self._model is None:
            self.load()

        logger.info(f"Transcribing [{self._backend}]: {audio_path}")

        if self._backend == "mlx":
            return self._transcribe_mlx(audio_path)
        return self._transcribe_faster_whisper(audio_path)

    def _transcribe_mlx(self, audio_path: str) -> TranscriptResult:
        result = self._mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=self._model,
            word_timestamps=True,
            language=self.language,
            fp16=True,
            no_speech_threshold=0.5,
            hallucination_silence_threshold=2.0,
            condition_on_previous_text=False,
        )
        text = result["text"].strip()
        language = result.get("language", "")

        words: list[WordSegment] = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                words.append(WordSegment(word=w["word"], start=w["start"], end=w["end"]))
        return TranscriptResult(text=text, language=language, words=words)

    def _transcribe_faster_whisper(self, audio_path: str) -> TranscriptResult:
        segments, info = self._model.transcribe(
            audio_path,
            word_timestamps=True,
            language=self.language,
            beam_size=5,
            vad_filter=True,
        )
        words: list[WordSegment] = []
        text_parts: list[str] = []
        for seg in segments:
            text_parts.append(seg.text)
            if seg.words:
                for w in seg.words:
                    words.append(WordSegment(word=w.word, start=w.start, end=w.end))
        return TranscriptResult(
            text="".join(text_parts).strip(),
            language=info.language,
            words=words,
        )
