"""Local, backend-independent Qwen audio boundaries and quality diagnostics."""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import subprocess

import numpy as np

from .audio_utils import _find_bin
from .types import WordSegment

SAMPLE_RATE = 16000
MAX_CHUNK_SECONDS = 30.0
MAX_NEW_TOKENS = 1024
MIN_RETRY_CHUNK_SECONDS = 5.0
MAX_TIMESTAMP_END_OVERSHOOT_SECONDS = 0.05


def load_local_audio(path: str) -> np.ndarray:
    """Decode once, identically for CPU/MPS/MLX. Never accept remote URLs."""
    if not Path(path).is_file():
        raise ValueError("Qwen transcription requires an existing local audio file")
    import soundfile as sf

    try:
        # Inspect the header first; do not hold an entire high-rate source
        # in RAM while also asking ffmpeg to decode its resampled copy.
        rate = sf.info(path).samplerate
        audio = sf.read(path, dtype="float32", always_2d=True)[0] if rate == SAMPLE_RATE else None
    except (RuntimeError, sf.LibsndfileError):
        audio, rate = None, None
    if rate == SAMPLE_RATE:
        audio = audio.mean(axis=1)
    else:
        result = subprocess.run(
            [_find_bin("ffmpeg"), "-nostdin", "-v", "error", "-i", str(Path(path).resolve()),
             "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("Qwen audio decoding failed: " + result.stderr.decode(errors="replace"))
        audio = np.frombuffer(result.stdout, dtype="<f4").copy()
    if not audio.size or not np.isfinite(audio).all():
        raise ValueError("Qwen audio must contain finite, non-empty samples")
    peak = float(np.max(np.abs(audio)))
    if peak > 1.0:
        audio = audio / peak
    return np.asarray(audio, dtype=np.float32)


def split_audio(audio: np.ndarray, sample_rate: int = SAMPLE_RATE,
                max_seconds: float = MAX_CHUNK_SECONDS) -> list[tuple[np.ndarray, float]]:
    """Lossless low-energy subdivision, matching MLX 0.3.5's boundary policy.

    Search the middle 60% with 0.5 s RMS windows at 0.25 s intervals; recurse
    iteratively so pathological quiet inputs cannot exhaust Python's stack.
    All boundaries are integer samples; returned arrays are views, not copies.
    """
    if sample_rate <= 0 or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("Qwen chunk rate and duration must be positive")
    if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
        raise ValueError("Qwen chunks require finite, non-empty mono audio")
    limit = max(1, int(max_seconds * sample_rate))
    window = max(1, int(0.5 * sample_rate))
    step = max(1, window // 2)
    pending = [(0, len(audio))]
    parts = []
    while pending:
        start, end = pending.pop()
        length = end - start
        if length <= limit:
            parts.append((audio[start:end], start / sample_rate))
            continue
        split = length // 2
        lowest = float("inf")
        for pos in range(int(length * 0.2), int(length * 0.8) - window, step):
            energy = float(np.sqrt(np.mean(audio[start + pos:start + pos + window] ** 2)))
            if energy < lowest:
                lowest, split = energy, pos + window // 2
        if not 0 < split < length:
            split = max(1, length // 2)
        boundary = start + split
        pending.extend([(boundary, end), (start, boundary)])
    return parts


def bisect_failed_chunk(
    audio: np.ndarray,
    offset: float,
    sample_rate: int = SAMPLE_RATE,
    min_seconds: float = MIN_RETRY_CHUNK_SECONDS,
) -> list[tuple[np.ndarray, float]] | None:
    """Bisect a failed generation chunk without creating tiny retry loops."""
    if sample_rate <= 0 or min_seconds <= 0:
        raise ValueError("Qwen retry rate and minimum duration must be positive")
    minimum = max(1, int(min_seconds * sample_rate))
    if audio.ndim != 1 or len(audio) < minimum * 2:
        return None
    midpoint = len(audio) // 2
    return [
        (audio[:midpoint], offset),
        (audio[midpoint:], offset + midpoint / sample_rate),
    ]


def merged_language(languages: list[str]) -> str:
    known = [str(item).strip() for item in languages
             if item and str(item).strip().lower() not in ("", "unknown")]
    return Counter(known).most_common(1)[0][0] if known else ""


def validate_words(words: list[WordSegment], duration: float, *, required: bool = False) -> None:
    if required and not words:
        raise RuntimeError("Qwen returned text without requested word timestamps; transcription rejected")
    for index, word in enumerate(words):
        if not (
            math.isfinite(word.start)
            and math.isfinite(word.end)
            and 0 <= word.start <= word.end
        ):
            raise RuntimeError(
                "Qwen returned a timestamp outside its audio chunk; transcription rejected "
                f"(word_index={index}, start={word.start:.6f}, end={word.end:.6f}, "
                f"chunk_duration={duration:.6f})"
            )
        if word.start > duration:
            if word.end - duration > MAX_TIMESTAMP_END_OVERSHOOT_SECONDS:
                raise RuntimeError(
                    "Qwen returned a timestamp outside its audio chunk; transcription rejected "
                    f"(word_index={index}, start={word.start:.6f}, end={word.end:.6f}, "
                    f"chunk_duration={duration:.6f})"
                )
            # The aligner can quantize a zero-duration final token so both its
            # start and end land in the first timestamp bin beyond the WAV.
            # Preserve the token at the exact boundary when the overshoot is
            # bounded, just as for an end-only quantization overshoot.
            word.start = duration
            word.end = duration
            continue
        if word.end > duration:
            if word.end - duration > MAX_TIMESTAMP_END_OVERSHOOT_SECONDS:
                raise RuntimeError(
                    "Qwen returned a timestamp outside its audio chunk; transcription rejected "
                    f"(word_index={index}, start={word.start:.6f}, end={word.end:.6f}, "
                    f"chunk_duration={duration:.6f})"
                )
            # Forced aligners quantize boundaries. Keep a final boundary that is
            # within one 50 ms timestamp bin, but never leak it beyond the WAV.
            word.end = duration


def chunk_diagnostic(offset: float, duration: float, words: list[WordSegment],
                     *, with_timestamps: bool, text_present: bool, **generation) -> dict:
    tail = max(0.0, duration - max((word.end for word in words), default=0.0))
    return {
        "start": offset, "end": offset + duration, "word_count": len(words),
        "timestamp_tail_gap_seconds": tail if with_timestamps else None,
        # A long tail can be ordinary meeting silence because chunk boundaries
        # are selected near low-energy regions. Generation termination is the
        # authoritative truncation signal for the pinned adapters; keep the tail
        # as diagnostic data without turning silence into a failure.
        "possible_truncation": bool(generation.get("truncated")),
        **generation,
    }
