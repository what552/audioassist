"""
Full transcription pipeline: ASR + diarization + merge → JSON + MD.
Handles long audio by splitting into chunks.
"""
from __future__ import annotations
import os
import uuid
import logging
from typing import Callable, Optional

from .types import WordSegment, TranscriptResult
from .asr import ASREngine
from .asr_whisper import WhisperASREngine
from .diarize import DiarizationEngine, SpeakerSegment, DEFAULT_DIARIZER_MODEL
from .merge import SpeakerBlock, merge, to_json, to_markdown
from .audio_utils import to_wav, split_to_chunks
from .model_manager import ModelManager
from . import checkpoint

logger = logging.getLogger(__name__)


class ModelNotReadyError(RuntimeError):
    def __init__(self, model_id: str, reason: str):
        self.model_id = model_id
        self.reason = reason  # "model_not_downloaded" | "model_incomplete"
        super().__init__(
            f"Model '{model_id}' is not ready: {reason.replace('_', ' ')}. "
            "Please download it from the Model Library."
        )


def _validate_model_local(mm: ModelManager, model_id: str) -> str:
    """Validate that a model is downloaded and complete. Returns local path."""
    if not mm.is_downloaded(model_id):
        raise ModelNotReadyError(model_id, "model_not_downloaded")
    path = mm.local_path(model_id)
    if not path or not os.path.isdir(path):
        raise ModelNotReadyError(model_id, "model_incomplete")
    return path


_WHISPER_SIZE_MAP: dict[str, str] = {
    "whisper-large-v3-turbo": "turbo",
    "whisper-large-v3":       "large",
    "whisper-medium":         "medium",
}


def _merge_chunk_texts(chunks: list[str], language: str) -> str:
    """Join per-chunk ASR text conservatively for fallback/no-word scenarios."""
    parts = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if not parts:
        return ""
    if (language or "").lower() in {"zh", "yue", "ja"}:
        return "".join(parts)
    return " ".join(parts)


def run(
    audio_path: str,
    output_dir: str,
    hf_token: Optional[str] = None,
    with_timestamps: bool = True,
    num_speakers: Optional[int] = None,
    engine: str = "qwen",
    asr_model_id: Optional[str] = None,
    diarizer_model_id: Optional[str] = None,
    job_id: Optional[str] = None,
    output_stem: Optional[str] = None,
    session_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> tuple[str, str]:
    """
    Run full transcription pipeline on an audio file.
    Automatically splits long audio into 5-minute chunks.

    Args:
        audio_path: Path to input audio/video file.
        output_dir: Directory for JSON and MD output.
        hf_token: HuggingFace token for pyannote diarization (only required for
            pyannote-diarization-3.1; community-1 default needs no token).
        with_timestamps: Whether to include word-level timestamps.
        num_speakers: Known number of speakers (optional hint for diarization).
        engine: ASR engine to use — "qwen" or "whisper".
        diarizer_model_id: Diarization model catalog ID; defaults to
            "pyannote-diarization-community-1".
        job_id: Unique job identifier; auto-generated if None.
        output_stem: Override filename stem for output files (default: job_id).
        session_dir: If provided, read/write checkpoint to this directory (F2).
        progress_callback: Called with (percent 0.0-1.0, message) at each step.

    Returns:
        (json_path, md_path)
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path!r}")

    os.makedirs(output_dir, exist_ok=True)

    if job_id is None:
        job_id = str(uuid.uuid4())

    stem = output_stem or job_id
    json_path = os.path.join(output_dir, f"{stem}.json")
    md_path   = os.path.join(output_dir, f"{stem}.md")

    def _progress(pct: float, msg: str):
        logger.info(f"[{pct:.0%}] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    # 0. Convert to WAV
    _progress(0.0, "Converting audio...")
    wav_path, is_temp_wav = to_wav(audio_path)
    temp_files = [wav_path] if is_temp_wav else []

    # Load checkpoint if session_dir provided
    ckpt_data: Optional[dict] = None
    if session_dir:
        ckpt_data = checkpoint.read(session_dir)

    try:
        # 1. Split into chunks
        chunks = split_to_chunks(wav_path)
        temp_files += [p for p, _ in chunks if p != wav_path]

        # 2. ASR — validate local models (F1: no auto-download)
        mm = ModelManager()
        if engine == "whisper":
            _progress(0.05, "Loading Whisper ASR model...")
            size = _WHISPER_SIZE_MAP.get(asr_model_id or "", "turbo")
            asr: ASREngine | WhisperASREngine = WhisperASREngine(size=size)
        else:
            asr_id     = asr_model_id or "qwen3-asr-1.7b"
            aligner_id = "qwen3-forced-aligner"
            _validate_model_local(mm, asr_id)
            _progress(0.05, "Loading Qwen3-ASR model...")
            aligner_path = mm.local_path(aligner_id) if mm.is_downloaded(aligner_id) else None
            asr = ASREngine(
                with_timestamps=with_timestamps,
                model_path=mm.local_path(asr_id),
                aligner_path=aligner_path,
            )
        asr.load()

        all_words: list[WordSegment] = []
        all_text: list[str] = []
        language = ""

        # Build initial chunk list from checkpoint if available
        if ckpt_data and session_dir:
            ckpt_chunks = {c["index"]: c for c in ckpt_data.get("chunks", [])}
        else:
            ckpt_chunks = {}

        # Write initial checkpoint
        if session_dir:
            ckpt_payload: dict = {
                "job_id": job_id,
                "source_audio": audio_path,
                "engine": engine,
                "model_id": asr_model_id or ("qwen3-asr-1.7b" if engine != "whisper" else asr_model_id),
                "status": "running",
                "chunks": [
                    ckpt_chunks.get(i, {"index": i, "offset": offset, "status": "pending"})
                    for i, (_, offset) in enumerate(chunks)
                ],
                "completed_chunks": sum(1 for c in ckpt_chunks.values() if c.get("status") == "done"),
                "total_chunks": len(chunks),
            }
            checkpoint.write(session_dir, ckpt_payload)

        try:
            for i, (chunk_path, offset) in enumerate(chunks):
                # Skip already-done chunks from checkpoint
                if ckpt_chunks.get(i, {}).get("status") == "done":
                    done_chunk = ckpt_chunks[i]
                    all_text.append(done_chunk.get("text", ""))
                    language = done_chunk.get("language", "") or language
                    for w in done_chunk.get("words", []):
                        all_words.append(WordSegment(
                            word=w["word"],
                            start=round(w["start"] + offset, 3),
                            end=round(w["end"] + offset, 3),
                        ))
                    continue

                chunk_pct = 0.1 + 0.5 * (i / len(chunks))
                _progress(chunk_pct, f"Transcribing chunk {i + 1}/{len(chunks)}...")
                result = asr.transcribe(chunk_path)
                all_text.append(result.text)
                language = result.language or language
                chunk_words = []
                for w in result.words:
                    all_words.append(WordSegment(
                        word=w.word,
                        start=round(w.start + offset, 3),
                        end=round(w.end + offset, 3),
                    ))
                    chunk_words.append({"word": w.word, "start": w.start, "end": w.end})

                # Update checkpoint after each chunk
                if session_dir and ckpt_payload is not None:
                    ckpt_payload["chunks"][i] = {
                        "index": i, "offset": offset, "status": "done",
                        "text": result.text, "words": chunk_words,
                        "language": result.language or "",
                    }
                    ckpt_payload["completed_chunks"] = sum(
                        1 for c in ckpt_payload["chunks"] if c.get("status") == "done"
                    )
                    checkpoint.write(session_dir, ckpt_payload)

        except Exception:
            if session_dir and ckpt_payload is not None:
                ckpt_payload["status"] = "interrupted"
                checkpoint.write(session_dir, ckpt_payload)
            raise

        merged_asr = TranscriptResult(
            text=_merge_chunk_texts(all_text, language),
            language=language,
            words=all_words,
        )
        logger.info(
            f"Transcript ({len(merged_asr.text)} chars): {merged_asr.text[:80]}..."
        )

        # 3. Diarization — validate local model (F1: no auto-download)
        actual_diarizer_id = diarizer_model_id or DEFAULT_DIARIZER_MODEL
        _validate_model_local(mm, actual_diarizer_id)
        _progress(0.6, "Running speaker diarization...")
        diarizer = DiarizationEngine(
            model_id=diarizer_model_id,
            hf_token=hf_token,
            num_speakers=num_speakers,
            progress_callback=lambda p, m: _progress(0.6 + p * 0.02, m),
        )
        speaker_segments = diarizer.diarize(wav_path)
        logger.info(
            f"Found {len(set(s.speaker for s in speaker_segments))} speaker(s)"
        )

        # 4. Merge
        _progress(0.9, "Merging transcription and speakers...")
        blocks = merge(merged_asr, speaker_segments)

        # 5. Output
        to_json(blocks, audio_path, merged_asr.language, json_path)
        to_markdown(blocks, audio_path, merged_asr.language, md_path)

        # Delete checkpoint on success
        if session_dir:
            checkpoint.delete(session_dir)

        _progress(1.0, "Done.")
        logger.info(f"JSON → {json_path}")
        logger.info(f"MD   → {md_path}")

    finally:
        for f in temp_files:
            try:
                os.unlink(f)
            except Exception:
                pass

    return json_path, md_path


def run_realtime_segments(
    segments: list[dict],
    wav_path: str,
    output_dir: str,
    hf_token: Optional[str] = None,
    num_speakers: Optional[int] = None,
    diarizer_model_id: Optional[str] = None,
    job_id: Optional[str] = None,
    output_stem: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> tuple[str, str]:
    """
    Diarize-only pipeline for realtime sessions.

    Takes pre-transcribed segments (from RealtimeTranscriber) and runs only
    speaker diarization on the WAV, skipping the ASR step entirely.

    Args:
        segments: List of {text, start, end} dicts from RealtimeTranscriber.
        wav_path: Path to the full session WAV file.
        output_dir: Directory for JSON and MD output.
        hf_token: HuggingFace token (only for pyannote-diarization-3.1).
        num_speakers: Hint for diarizer.
        diarizer_model_id: Diarizer catalog ID; defaults to community-1.
        job_id: Unique job identifier; auto-generated if None.
        output_stem: Base name for output files (without extension). Defaults to job_id.
        progress_callback: Called with (percent 0.0-1.0, message) at each step.

    Returns:
        (json_path, md_path)
    """
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"WAV file not found: {wav_path!r}")

    os.makedirs(output_dir, exist_ok=True)

    if job_id is None:
        job_id = str(uuid.uuid4())

    stem = output_stem or job_id
    json_path = os.path.join(output_dir, f"{stem}.json")
    md_path   = os.path.join(output_dir, f"{stem}.md")

    def _progress(pct: float, msg: str):
        logger.info(f"[{pct:.0%}] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    # 1. Diarize — validate local model (F1: no auto-download)
    mm = ModelManager()
    actual_diarizer_id = diarizer_model_id or DEFAULT_DIARIZER_MODEL
    _validate_model_local(mm, actual_diarizer_id)
    _progress(0.5, "Running speaker diarization…")
    diarizer = DiarizationEngine(
        model_id=diarizer_model_id,
        hf_token=hf_token,
        num_speakers=num_speakers,
        progress_callback=lambda p, m: _progress(0.5 + p * 0.3, m),
    )
    speaker_segs = diarizer.diarize(wav_path)
    logger.info(
        f"Found {len(set(s.speaker for s in speaker_segs))} speaker(s)"
    )

    # 2. Assign speakers to realtime segments
    _progress(0.8, "Assigning speakers…")
    blocks = [
        SpeakerBlock(
            speaker=_dominant_speaker(seg["start"], seg["end"], speaker_segs),
            start=seg["start"],
            end=seg["end"],
            text=seg["text"],
            words=[],
        )
        for seg in segments
    ]

    # 3. Output
    _progress(0.9, "Writing output…")
    to_json(blocks, wav_path, "", json_path)
    to_markdown(blocks, wav_path, "", md_path)

    _progress(1.0, "Done.")
    logger.info(f"JSON → {json_path}")
    logger.info(f"MD   → {md_path}")

    return json_path, md_path


def _dominant_speaker(
    start: float, end: float, speaker_segs: list[SpeakerSegment]
) -> str:
    """Return the speaker label with the most overlap in [start, end]."""
    overlaps: dict[str, float] = {}
    for ss in speaker_segs:
        overlap = max(0.0, min(end, ss.end) - max(start, ss.start))
        if overlap > 0:
            overlaps[ss.speaker] = overlaps.get(ss.speaker, 0.0) + overlap
    if not overlaps:
        return "UNKNOWN"
    return max(overlaps, key=lambda k: overlaps[k])
