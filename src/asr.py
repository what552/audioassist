"""
ASR module: transcription + word/char-level timestamps via Qwen3-ASR + ForcedAligner.

Actual API (qwen-asr 0.0.6):
  - Qwen3ASRModel.from_pretrained(model, forced_aligner=aligner_path)
  - model.transcribe(audio, return_time_stamps=True) -> List[ASRTranscription]
  - ASRTranscription: .text, .language, .time_stamps
  - time_stamps.items: each has .text, .start_time, .end_time
"""
from __future__ import annotations
from typing import Optional
import logging

from .types import WordSegment, TranscriptResult

logger = logging.getLogger(__name__)

# Default HuggingFace model IDs (used when local path not provided)
_DEFAULT_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
_DEFAULT_ALIGNER_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"


class ASREngine:
    """Wraps Qwen3ASRModel with optional ForcedAligner for timestamps."""

    def __init__(
        self,
        with_timestamps: bool = True,
        model_path: Optional[str] = None,
        aligner_path: Optional[str] = None,
    ):
        self.with_timestamps = with_timestamps
        self.model_path = model_path or _DEFAULT_ASR_MODEL
        self.aligner_path = aligner_path or _DEFAULT_ALIGNER_MODEL
        self._model = None

    def load(self):
        """Lazy-load models (first call downloads ~3GB to HF cache)."""
        from qwen_asr import Qwen3ASRModel
        import torch

        # Device selection: CUDA > CPU (MPS causes SIGBUS on Qwen3-ASR)
        if torch.cuda.is_available():
            device = "cuda"
            dtype = torch.float16
        else:
            device = "cpu"
            dtype = torch.float32
            import multiprocessing
            torch.set_num_threads(multiprocessing.cpu_count())

        logger.info(f"Loading ASR model on {device} ({dtype})")

        aligner = self.aligner_path if self.with_timestamps else None
        self._model = Qwen3ASRModel.from_pretrained(
            self.model_path,
            forced_aligner=aligner,
            device_map=device,
            torch_dtype=dtype,
        )
        logger.info("Models loaded.")

    def transcribe(self, audio_path: str) -> TranscriptResult:
        """Transcribe a single audio file."""
        if self._model is None:
            self.load()

        logger.info(f"Transcribing: {audio_path}")
        results = self._model.transcribe(
            audio_path,
            return_time_stamps=self.with_timestamps,
        )
        result = results[0]

        words: list[WordSegment] = []
        if self.with_timestamps and result.time_stamps is not None:
            for item in result.time_stamps.items:
                words.append(WordSegment(
                    word=item.text,
                    start=item.start_time,
                    end=item.end_time,
                ))

        return TranscriptResult(
            text=result.text,
            language=result.language,
            words=words,
        )
