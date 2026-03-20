"""
Shared data types used across ASR, merge, and pipeline modules.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class WordSegment:
    word: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class TranscriptResult:
    text: str
    language: str
    words: list[WordSegment] = field(default_factory=list)
