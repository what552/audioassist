"""
Merge ASR word timestamps with speaker diarization segments.
Outputs: JSON (full data) + MD (human-readable).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional
import json
import os

from .types import WordSegment, TranscriptResult
from .diarize import SpeakerSegment


@dataclass
class SpeakerBlock:
    speaker: str
    start: float
    end: float
    text: str
    words: list[dict]  # [{word, start, end}]


def get_speaker_at(time: float, segments: list[SpeakerSegment]) -> str:
    for seg in segments:
        if seg.start <= time <= seg.end:
            return seg.speaker
    return "UNKNOWN"


def merge(
    asr_result: TranscriptResult,
    speaker_segments: list[SpeakerSegment],
) -> list[SpeakerBlock]:
    """
    Assign each word to a speaker, group into blocks.
    Falls back to single block if no word timestamps.
    """
    words = asr_result.words

    # No word timestamps: return single block with full text
    if not words:
        speaker = get_speaker_at(
            speaker_segments[0].start if speaker_segments else 0,
            speaker_segments,
        )
        return [SpeakerBlock(
            speaker=speaker,
            start=speaker_segments[0].start if speaker_segments else 0.0,
            end=speaker_segments[-1].end if speaker_segments else 0.0,
            text=asr_result.text,
            words=[],
        )]

    blocks: list[SpeakerBlock] = []
    current_speaker: Optional[str] = None
    current_words: list[WordSegment] = []

    for w in words:
        mid = (w.start + w.end) / 2
        speaker = get_speaker_at(mid, speaker_segments)

        if speaker != current_speaker:
            if current_words:
                blocks.append(_make_block(current_speaker, current_words))
            current_speaker = speaker
            current_words = [w]
        else:
            current_words.append(w)

    if current_words:
        blocks.append(_make_block(current_speaker, current_words))

    return blocks


def _make_block(speaker: str, words: list[WordSegment]) -> SpeakerBlock:
    return SpeakerBlock(
        speaker=speaker,
        start=words[0].start,
        end=words[-1].end,
        text="".join(w.word for w in words),
        words=[{"word": w.word, "start": w.start, "end": w.end} for w in words],
    )


# ── Output formats ────────────────────────────────────────────────────────────

def to_json(
    blocks: list[SpeakerBlock],
    audio_path: str,
    language: str,
    output_path: str,
):
    data = {
        "audio": os.path.basename(audio_path),
        "language": language,
        "segments": [
            {
                "speaker": b.speaker,
                "start": round(b.start, 3),
                "end": round(b.end, 3),
                "text": b.text,
                "words": b.words,
            }
            for b in blocks
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def to_markdown(
    blocks: list[SpeakerBlock],
    audio_path: str,
    language: str,
    output_path: str,
):
    lines = [f"# {os.path.basename(audio_path)}\n"]
    lines.append(f"**语言:** {language}\n")

    for b in blocks:
        start = _fmt_time(b.start)
        end = _fmt_time(b.end)
        lines.append(f"\n**[{start} → {end}] {b.speaker}**\n")
        lines.append(f"{b.text}\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
