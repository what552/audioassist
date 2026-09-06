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
from .qwen_audio import (SAMPLE_RATE, MAX_CHUNK_SECONDS, MAX_NEW_TOKENS,
                         load_local_audio, split_audio, merged_language,
                         validate_words, chunk_diagnostic)
from .qwen_compat import GenerationObserver, install_audio_window_masks
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
        self._mask_handles = []
        self._observer = None
        self.last_truncated = False
        self.last_chunk_diagnostics = []
        self.inference_config = {
            "dtype": self.backend.dtype_name, "max_chunk_seconds": MAX_CHUNK_SECONDS,
            "max_new_tokens": MAX_NEW_TOKENS, "batch_size": 1,
            "audio_window_mask": True,
        }

    def load(self) -> None:
        """Lazy-load already validated local models."""
        if self._model is not None:
            return

        from qwen_asr import Qwen3ASRModel
        from importlib.metadata import version
        import torch

        if version("qwen-asr") != "0.0.6":
            raise RuntimeError("Review Qwen compatibility shims before changing qwen-asr 0.0.6")

        device = self.backend.device
        dtype = getattr(torch, self.backend.dtype_name)
        if device == "cpu":
            torch.set_num_threads(multiprocessing.cpu_count())

        logger.info("Loading Qwen backend %s on %s (%s)", self.backend_name, device, dtype)

        aligner = self.aligner_path if self.with_timestamps else None
        aligner_kwargs = None
        if aligner is not None:
            aligner_kwargs = {"device_map": device, "dtype": dtype}
        try:
            self._model = Qwen3ASRModel.from_pretrained(
                self.model_path,
                forced_aligner=aligner,
                forced_aligner_kwargs=aligner_kwargs,
                device_map=device,
                dtype=dtype,
                max_inference_batch_size=1,
                max_new_tokens=MAX_NEW_TOKENS,
            )
            self._mask_handles.extend(install_audio_window_masks(self._model.model))
            if aligner is not None:
                self._mask_handles.extend(install_audio_window_masks(self._model.forced_aligner.model))
            self._observer = GenerationObserver(self._model.model)
        except Exception:
            self.close()
            raise
        logger.info("Models loaded.")

    def transcribe(self, audio_path: str) -> TranscriptResult:
        """Transcribe a single audio file."""
        if self._model is None:
            self.load()

        self.last_truncated = False
        self.last_chunk_diagnostics = []
        audio = load_local_audio(audio_path)
        texts, languages = [], []
        words: list[WordSegment] = []
        for chunk, offset in split_audio(audio):
            self._observer.records.clear()
            result = self._model.transcribe(
                (chunk, SAMPLE_RATE), return_time_stamps=self.with_timestamps,
            )[0]
            if len(self._observer.records) != 1:
                raise RuntimeError("Qwen generation diagnostics missing for a serial audio chunk")
            generation = self._observer.records[0]
            self.last_truncated |= generation["truncated"]
            local_words = []
            if self.with_timestamps and result.time_stamps is not None:
                local_words = [WordSegment(word=item.text, start=float(item.start_time), end=float(item.end_time))
                               for item in result.time_stamps.items]
            duration = len(chunk) / SAMPLE_RATE
            self.last_chunk_diagnostics.append(chunk_diagnostic(
                offset, duration, local_words, with_timestamps=self.with_timestamps,
                text_present=bool(result.text.strip()), **generation,
            ))
            if self.last_truncated:
                raise RuntimeError("Qwen reached its per-chunk generation limit; transcription rejected")
            if generation["finish_reason"] != "eos":
                raise RuntimeError("Qwen generation stopped without EOS; transcription rejected")
            validate_words(local_words, duration)
            texts.append(result.text.strip())
            languages.append(result.language)
            words.extend(WordSegment(word=w.word, start=w.start + offset, end=w.end + offset)
                         for w in local_words)

        return TranscriptResult(
            text=" ".join(text for text in texts if text),
            language=merged_language(languages),
            words=words,
        )

    def close(self) -> None:
        """Release Qwen/aligner references and the selected device cache."""
        model = self._model
        self._model = None
        if self._observer is not None:
            try:
                self._observer.close()
            except Exception:
                logger.debug("Qwen generation observer cleanup failed", exc_info=True)
            self._observer = None
        for handle in self._mask_handles:
            try:
                handle.remove()
            except Exception:
                logger.debug("Qwen audio mask hook cleanup failed", exc_info=True)
        self._mask_handles = []
        self.last_truncated = False
        self.last_chunk_diagnostics = []
        if model is not None:
            # qwen-asr retains the ASR model, processor, and Forced Aligner on
            # one wrapper. Clear those references before collecting.
            for attribute in ("forced_aligner", "model", "processor"):
                if hasattr(model, attribute):
                    try:
                        setattr(model, attribute, None)
                    except Exception:
                        logger.debug(
                            "Failed to clear Qwen model attribute %s",
                            attribute,
                            exc_info=True,
                        )
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
