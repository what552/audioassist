"""
Merge ASR word timestamps with speaker diarization segments.
Outputs: JSON (full data) + MD (human-readable).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json
import os

from .types import WordSegment, TranscriptResult
from .diarize import SpeakerSegment

MAX_BLOCK_DURATION = 45.0
MAX_BLOCK_CHARS = 220
STRONG_PAUSE_SECONDS = 1.2
SOFT_PAUSE_SECONDS = 0.6
SENTENCE_ENDINGS = ".!?。！？；;"
NO_SPACE_BEFORE = ".,!?;:)]}%。，！？；：、"
NO_SPACE_AFTER = "([{"


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

    return split_long_blocks(blocks)


def _make_block(speaker: str, words: list[WordSegment]) -> SpeakerBlock:
    return SpeakerBlock(
        speaker=speaker,
        start=words[0].start,
        end=words[-1].end,
        text=_join_words(words),
        words=[{"word": w.word, "start": w.start, "end": w.end} for w in words],
    )


def _is_cjk_char(ch: str) -> bool:
    return (
        "\u3400" <= ch <= "\u4dbf"
        or "\u4e00" <= ch <= "\u9fff"
        or "\uf900" <= ch <= "\ufaff"
    )


def _is_wordish_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "'")


def _needs_space_between(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left[-1].isspace() or right[0].isspace():
        return False
    if right[0] in NO_SPACE_BEFORE:
        return False
    if left[-1] in NO_SPACE_AFTER:
        return False
    if _is_cjk_char(left[-1]) or _is_cjk_char(right[0]):
        return False
    if _is_wordish_char(left[-1]) and _is_wordish_char(right[0]):
        return True
    if left[-1] in ".!?;:)]}\"'" and _is_wordish_char(right[0]):
        return True
    return False


def _join_words(words: list[WordSegment]) -> str:
    if not words:
        return ""

    parts = [words[0].word]
    for word in words[1:]:
        token = word.word
        if _needs_space_between(parts[-1], token):
            parts.append(" ")
        parts.append(token)
    return "".join(parts)


def _block_char_len(words: list[WordSegment]) -> int:
    return sum(len(w.word) for w in words)


def _ends_sentence(word: str) -> bool:
    return bool(word) and word.rstrip()[-1:] in SENTENCE_ENDINGS


def _should_split(words: list[WordSegment], next_word: WordSegment) -> bool:
    if not words:
        return False

    prev = words[-1]
    gap = max(0.0, next_word.start - prev.end)
    duration = next_word.end - words[0].start
    char_len = _block_char_len(words) + len(next_word.word)

    if gap >= STRONG_PAUSE_SECONDS:
        return True
    if _ends_sentence(prev.word) and gap >= SOFT_PAUSE_SECONDS:
        return True
    if duration >= MAX_BLOCK_DURATION:
        return True
    if char_len >= MAX_BLOCK_CHARS and gap >= SOFT_PAUSE_SECONDS / 2:
        return True
    return False


def split_long_blocks(blocks: list[SpeakerBlock]) -> list[SpeakerBlock]:
    """Split overly long single-speaker blocks using pauses and sentence boundaries."""
    split_blocks: list[SpeakerBlock] = []

    for block in blocks:
        if len(block.words) <= 1:
            split_blocks.append(block)
            continue

        current_words: list[WordSegment] = []
        for raw_word in block.words:
            word = WordSegment(
                word=raw_word["word"],
                start=raw_word["start"],
                end=raw_word["end"],
            )
            if current_words and _should_split(current_words, word):
                split_blocks.append(_make_block(block.speaker, current_words))
                current_words = [word]
            else:
                current_words.append(word)

        if current_words:
            split_blocks.append(_make_block(block.speaker, current_words))

    return split_blocks


# ── Output formats ────────────────────────────────────────────────────────────

def to_json(
    blocks: list[SpeakerBlock],
    audio_path: str,
    language: str,
    output_path: str,
):
    import time as _time
    data = {
        "audio": os.path.basename(audio_path),
        "filename": os.path.basename(audio_path),
        "language": language,
        "created_at": _time.strftime("%Y-%m-%d %H:%M"),
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
