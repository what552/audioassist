"""
Audio preprocessing: convert any format to 16kHz mono WAV via ffmpeg.
"""
from __future__ import annotations
import os
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

CHUNK_SECONDS = 300  # 5 minutes per chunk


def to_wav(audio_path: str) -> tuple[str, bool]:
    """
    Convert audio to 16kHz mono WAV if needed.

    Checks WAV files for correct sample rate and channel count before skipping
    conversion — native WAV at wrong rate/channels will still be re-encoded.

    Returns:
        (wav_path, is_temp) — if is_temp=True, caller should delete after use.
    """
    ext = os.path.splitext(audio_path)[1].lower()
    if ext == ".wav" and not _wav_needs_conversion(audio_path):
        return audio_path, False

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    wav_path = tmp.name

    logger.info(f"Converting {ext} → WAV: {os.path.basename(audio_path)}")
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        os.unlink(wav_path)
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr.decode()}")

    logger.info(f"Converted to: {wav_path}")
    return wav_path, True


def _wav_needs_conversion(wav_path: str) -> bool:
    """Return True if WAV is not 16kHz mono."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "csv=p=0", wav_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return True
    parts = result.stdout.strip().split(",")
    if len(parts) < 2:
        return True
    return parts[0] != "16000" or parts[1] != "1"


def get_duration(audio_path: str) -> float:
    """Return audio duration in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", audio_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {audio_path!r}:\n{result.stderr}"
        )
    return float(result.stdout.strip())


def split_to_chunks(wav_path: str, chunk_sec: int = CHUNK_SECONDS) -> list[tuple[str, float]]:
    """
    Split a WAV file into chunks of chunk_sec seconds.

    Returns:
        List of (chunk_wav_path, start_offset_sec)
    """
    duration = get_duration(wav_path)
    if duration <= chunk_sec:
        return [(wav_path, 0.0)]

    chunks = []
    start = 0.0
    while start < duration:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        cmd = [
            "ffmpeg", "-y", "-i", wav_path,
            "-ss", str(start), "-t", str(chunk_sec),
            "-ar", "16000", "-ac", "1",
            tmp.name,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        chunks.append((tmp.name, start))
        start += chunk_sec

    logger.info(f"Split into {len(chunks)} chunks ({chunk_sec}s each)")
    return chunks
