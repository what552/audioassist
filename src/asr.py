"""
ASR module: transcription + word/char-level timestamps via Qwen3-ASR + ForcedAligner.

Actual API (qwen-asr 0.0.6):
  - Qwen3ASRModel.from_pretrained(model, forced_aligner=aligner_path)
  - model.transcribe(audio, return_time_stamps=True) -> List[ASRTranscription]
  - ASRTranscription: .text, .language, .time_stamps
  - time_stamps.items: each has .text, .start_time, .end_time
"""
from __future__ import annotations
import gc
import logging
import multiprocessing

from .asr_factory import QwenBackendConfig
from .types import WordSegment, TranscriptResult

logger = logging.getLogger(__name__)

class ASREngine:
    """Official qwen-asr PyTorch adapter using local model directories."""

    def __init__(
        self,
        with_timestamps: bool = True,
        model_path: str = "",
        aligner_path: str | None = None,
        backend: QwenBackendConfig | None = None,
    ):
        if not model_path:
            raise ValueError("ASREngine requires an explicit local model_path")
        if with_timestamps and not aligner_path:
            raise ValueError("ASREngine timestamps require an explicit aligner_path")
        self.with_timestamps = with_timestamps
        self.model_path = model_path
        self.aligner_path = aligner_path
        self.backend = backend or QwenBackendConfig(
            name="pytorch-cpu", device="cpu", dtype_name="float32"
        )
        self.backend_name = self.backend.name
        self._model = None

    def load(self) -> None:
        """Lazy-load already validated local models."""
        if self._model is not None:
            return

        from qwen_asr import Qwen3ASRModel
        import torch

        device = self.backend.device
        dtype = getattr(torch, self.backend.dtype_name)
        if device == "cpu":
            torch.set_num_threads(multiprocessing.cpu_count())

        logger.info("Loading Qwen backend %s on %s (%s)", self.backend_name, device, dtype)

        aligner = self.aligner_path if self.with_timestamps else None
        aligner_kwargs = None
        if aligner is not None:
            aligner_kwargs = {"device_map": device, "dtype": dtype}
        self._model = Qwen3ASRModel.from_pretrained(
            self.model_path,
            forced_aligner=aligner,
            forced_aligner_kwargs=aligner_kwargs,
            device_map=device,
            dtype=dtype,
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

    def close(self) -> None:
        """Release Qwen/aligner references and the selected device cache."""
        model = self._model
        self._model = None
        if model is not None:
            # qwen-asr retains the ASR model, processor, and Forced Aligner on
            # one wrapper. Clear those references before collecting.
            for attribute in ("forced_aligner", "model", "processor"):
                if hasattr(model, attribute):
                    try:
                        setattr(model, attribute, None)
                    except Exception:
                        pass
            del model

        gc.collect()

        try:
            import torch
            if self.backend.device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif self.backend.device == "mps":
                mps = getattr(torch, "mps", None)
                if mps is not None and hasattr(mps, "empty_cache"):
                    mps.empty_cache()
        except Exception:
            logger.debug("Qwen backend cache cleanup failed", exc_info=True)
