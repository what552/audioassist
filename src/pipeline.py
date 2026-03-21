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
from .diarize import DiarizationEngine, SpeakerSegment
from .merge import merge, to_json, to_markdown
from .audio_utils import to_wav, split_to_chunks

logger = logging.getLogger(__name__)


def run(
    audio_path: str,
    output_dir: str,
    hf_token: Optional[str] = None,
    with_timestamps: bool = True,
    num_speakers: Optional[int] = None,
    engine: str = "qwen",
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

        # 2. ASR
        _progress(0.05, f"Loading {engine} ASR model...")
        if engine == "whisper":
            asr: ASREngine | WhisperASREngine = WhisperASREngine()
        else:
            asr = ASREngine(with_timestamps=with_timestamps)
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

        # 3. Diarization
        _progress(0.6, "Running speaker diarization...")
        diarizer = DiarizationEngine(
            model_id=diarizer_model_id,
            hf_token=hf_token,
            num_speakers=num_speakers,
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
