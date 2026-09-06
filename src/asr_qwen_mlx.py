"""MLX Qwen3-ASR adapter for Apple Silicon.

This module is imported only when the internal ``mlx`` Qwen backend is
explicitly selected. Model directories are validated by the application
factory before construction; the adapter never supplies repository IDs.
"""
from __future__ import annotations

import gc
import logging

from .types import TranscriptResult, WordSegment
from .qwen_audio import (SAMPLE_RATE, MAX_CHUNK_SECONDS, MAX_NEW_TOKENS,
                         load_local_audio, split_audio, merged_language,
                         validate_words, chunk_diagnostic)

logger = logging.getLogger(__name__)


class MLXQwenASREngine:
    """Full-precision MLX adapter backed by mlx-qwen3-asr 0.3.5."""

    backend_name = "mlx"

    def __init__(
        self,
        *,
        model_path: str,
        aligner_path: str | None,
        with_timestamps: bool,
    ) -> None:
        if not model_path:
            raise ValueError("MLXQwenASREngine requires an explicit local model_path")
        if with_timestamps and not aligner_path:
            raise ValueError(
                "MLXQwenASREngine timestamps require an explicit aligner_path"
            )
        self.model_path = model_path
        self.aligner_path = aligner_path
        self.with_timestamps = with_timestamps
        self._session = None
        self._aligner = None
        self._mx = None
        self.last_truncated = False
        self.last_chunk_diagnostics = []
        self.inference_config = {
            "dtype": "float16", "max_chunk_seconds": MAX_CHUNK_SECONDS,
            "max_new_tokens": MAX_NEW_TOKENS, "batch_size": 1,
            "audio_window_mask": True,
        }

    def load(self) -> None:
        if self._session is not None:
            return

        import mlx.core as mx
        from mlx_qwen3_asr import ForcedAligner, Session

        logger.info("Loading Qwen backend mlx from %s", self.model_path)
        self._mx = mx
        try:
            self._session = Session(model=self.model_path, dtype=mx.float16)
            if self.with_timestamps:
                self._aligner = ForcedAligner(
                    self.aligner_path,
                    dtype=mx.float16,
                    backend="mlx",
                )
        except Exception:
            self.close()
            raise
        logger.info("MLX Qwen backend loaded")

    def transcribe(self, audio_path: str) -> TranscriptResult:
        if self._session is None:
            self.load()

        self.last_truncated = False
        self.last_chunk_diagnostics = []
        audio = load_local_audio(audio_path)
        texts, languages, words = [], [], []
        for chunk, offset in split_audio(audio):
            result = self._session.transcribe(
                chunk,
                return_timestamps=self.with_timestamps,
                forced_aligner=self._aligner if self.with_timestamps else None,
                return_chunks=True,
                max_new_tokens=MAX_NEW_TOKENS,
            )
            chunks = getattr(result, "chunks", None) or []
            if len(chunks) != 1:
                raise RuntimeError("MLX generation diagnostics missing for a serial audio chunk")
            native = chunks[0]
            if not all(key in native for key in ("generated_tokens", "max_new_tokens", "finish_reason", "truncated")):
                raise RuntimeError("MLX generation diagnostics incomplete; review pinned runtime")
            truncated = bool(getattr(result, "truncated", False) or native["truncated"]
                             or native["finish_reason"] == "length")
            self.last_truncated |= truncated
            local_words = [WordSegment(word=str(seg["text"]), start=float(seg["start"]), end=float(seg["end"]))
                           for seg in (getattr(result, "segments", None) or [])]
            duration = len(chunk) / SAMPLE_RATE
            self.last_chunk_diagnostics.append(chunk_diagnostic(
                offset, duration, local_words, with_timestamps=self.with_timestamps,
                text_present=bool(result.text.strip()), truncated=truncated,
                generated_tokens=native.get("generated_tokens"),
                max_new_tokens=native.get("max_new_tokens"),
                finish_reason=native.get("finish_reason", "unknown"),
            ))
            if truncated:
                raise RuntimeError("MLX Qwen reached its per-chunk generation limit; transcription rejected")
            if native["finish_reason"] != "eos":
                raise RuntimeError("MLX Qwen generation stopped without EOS; transcription rejected")
            validate_words(local_words, duration)
            texts.append(str(result.text).strip())
            languages.append(result.language or merged_language([item.get("language", "") for item in chunks]))
            words.extend(WordSegment(word=w.word, start=w.start + offset, end=w.end + offset)
                         for w in local_words)
        return TranscriptResult(
            text=" ".join(text for text in texts if text),
            language=merged_language(languages),
            words=words,
        )

    def close(self) -> None:
        """Release session, aligner holders, and MLX allocations.

        Version 0.3.5 has no public close API and its forced aligner uses
        private process-local holders. The dependency is pinned so these
        explicit cleanup calls are reviewed and covered by tests.
        """
        session = self._session
        aligner = self._aligner
        mx = self._mx
        self._session = None
        self._aligner = None
        self._mx = None
        self.last_truncated = False
        self.last_chunk_diagnostics = []

        if session is not None:
            for attribute in ("model", "tokenizer", "config"):
                if hasattr(session, attribute):
                    try:
                        setattr(session, attribute, None)
                    except Exception:
                        logger.debug(
                            "Failed to clear MLX Qwen session attribute %s",
                            attribute,
                            exc_info=True,
                        )

        if aligner is not None and hasattr(aligner, "_backend"):
            backend = getattr(aligner, "_backend", None)
            if backend is not None:
                for attribute in ("model", "tokenizer", "config"):
                    if hasattr(backend, attribute):
                        try:
                            setattr(backend, attribute, None)
                        except Exception:
                            logger.debug(
                                "Failed to clear MLX aligner backend attribute %s",
                                attribute,
                                exc_info=True,
                            )
            try:
                aligner._backend = None
            except Exception:
                logger.debug(
                    "Failed to release MLX aligner backend",
                    exc_info=True,
                )

        try:
            from mlx_qwen3_asr.load_models import _ModelHolder
            from mlx_qwen3_asr.tokenizer import _TokenizerHolder

            _ModelHolder.clear()
            _TokenizerHolder.clear()
        except Exception:
            logger.debug("MLX Qwen holder cleanup failed", exc_info=True)

        del session, aligner
        gc.collect()

        if mx is not None:
            try:
                clear_cache = getattr(mx, "clear_cache", None)
                if callable(clear_cache):
                    clear_cache()
                else:
                    metal = getattr(mx, "metal", None)
                    metal_clear_cache = getattr(metal, "clear_cache", None)
                    if callable(metal_clear_cache):
                        metal_clear_cache()
            except Exception:
                logger.debug("MLX cache cleanup failed", exc_info=True)
