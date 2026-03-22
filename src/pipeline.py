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

logger = logging.getLogger(__name__)


_WHISPER_SIZE_MAP: dict[str, str] = {
    "whisper-large-v3-turbo": "turbo",
    "whisper-large-v3":       "large",
    "whisper-medium":         "medium",
}


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
        progress_callback: Called with (percent 0.0-1.0, message) at each step.

    Returns:
        (json_path, md_path)
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path!r}")

    os.makedirs(output_dir, exist_ok=True)

    if job_id is None:
        job_id = str(uuid.uuid4())

    json_path = os.path.join(output_dir, f"{job_id}.json")
    md_path = os.path.join(output_dir, f"{job_id}.md")

    def _progress(pct: float, msg: str):
        logger.info(f"[{pct:.0%}] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    # 0. Convert to WAV
    _progress(0.0, "Converting audio...")
    wav_path, is_temp_wav = to_wav(audio_path)
    temp_files = [wav_path] if is_temp_wav else []

    try:
        # 1. Split into chunks
        chunks = split_to_chunks(wav_path)
        temp_files += [p for p, _ in chunks if p != wav_path]

        # 2. ASR — auto-download and load from ModelManager-managed local path
        mm = ModelManager()
        if engine == "whisper":
            _progress(0.05, "Loading Whisper ASR model...")
            size = _WHISPER_SIZE_MAP.get(asr_model_id or "", "turbo")
            asr: ASREngine | WhisperASREngine = WhisperASREngine(size=size)
        else:
            asr_id     = asr_model_id or "qwen3-asr-1.7b"
            aligner_id = "qwen3-forced-aligner"
            if not mm.is_downloaded(asr_id):
                _progress(0.03, f"Downloading ASR model ({asr_id})…")
                mm.download(
                    asr_id,
                    progress_callback=lambda p, m: _progress(0.03 + p * 0.02, m),
                )
            if not mm.is_downloaded(aligner_id):
                try:
                    _progress(0.04, f"Downloading aligner model ({aligner_id})…")
                    mm.download(
                        aligner_id,
                        progress_callback=lambda p, m: _progress(0.04 + p * 0.01, m),
                    )
                except Exception:
                    logger.warning(
                        "Aligner download failed — word-level timestamps will be unavailable"
                    )
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

        for i, (chunk_path, offset) in enumerate(chunks):
            chunk_pct = 0.1 + 0.5 * (i / len(chunks))
            _progress(chunk_pct, f"Transcribing chunk {i + 1}/{len(chunks)}...")
            result = asr.transcribe(chunk_path)
            all_text.append(result.text)
            language = result.language or language
            for w in result.words:
                all_words.append(WordSegment(
                    word=w.word,
                    start=round(w.start + offset, 3),
                    end=round(w.end + offset, 3),
                ))

        merged_asr = TranscriptResult(
            text="".join(all_text),
            language=language,
            words=all_words,
        )
        logger.info(
            f"Transcript ({len(merged_asr.text)} chars): {merged_asr.text[:80]}..."
        )

        # 3. Diarization — auto-download if needed, then load
        actual_diarizer_id = diarizer_model_id or DEFAULT_DIARIZER_MODEL
        if not mm.is_downloaded(actual_diarizer_id):
            _progress(0.58, f"Downloading diarizer model ({actual_diarizer_id})…")
            mm.download(
                actual_diarizer_id,
                progress_callback=lambda p, m: _progress(0.58 + p * 0.02, m),
            )
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
        progress_callback: Called with (percent 0.0-1.0, message) at each step.

    Returns:
        (json_path, md_path)
    """
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"WAV file not found: {wav_path!r}")

    os.makedirs(output_dir, exist_ok=True)

    if job_id is None:
        job_id = str(uuid.uuid4())

    json_path = os.path.join(output_dir, f"{job_id}.json")
    md_path   = os.path.join(output_dir, f"{job_id}.md")

    def _progress(pct: float, msg: str):
        logger.info(f"[{pct:.0%}] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    # 1. Diarize
    mm = ModelManager()
    actual_diarizer_id = diarizer_model_id or DEFAULT_DIARIZER_MODEL
    if not mm.is_downloaded(actual_diarizer_id):
        _progress(0.1, f"Downloading diarizer model ({actual_diarizer_id})…")
        mm.download(
            actual_diarizer_id,
            progress_callback=lambda p, m: _progress(0.1 + p * 0.4, m),
        )
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
