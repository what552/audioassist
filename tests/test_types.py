"""Tests for src/types.py — WordSegment and TranscriptResult dataclasses."""
from src.types import WordSegment, TranscriptResult


class TestWordSegment:
    def test_fields(self):
        w = WordSegment(word="hello", start=1.0, end=1.5)
        assert w.word == "hello"
        assert w.start == 1.0
        assert w.end == 1.5

    def test_equality(self):
        a = WordSegment(word="hi", start=0.0, end=0.3)
        b = WordSegment(word="hi", start=0.0, end=0.3)
        assert a == b

    def test_inequality(self):
        a = WordSegment(word="hi", start=0.0, end=0.3)
        b = WordSegment(word="hi", start=0.0, end=0.4)
        assert a != b


class TestTranscriptResult:
    def test_defaults(self):
        t = TranscriptResult(text="hello", language="zh")
        assert t.text == "hello"
        assert t.language == "zh"
        assert t.words == []

    def test_words_not_shared(self):
        # Each instance should have its own list (not sharing default)
        a = TranscriptResult(text="a", language="en")
        b = TranscriptResult(text="b", language="en")
        a.words.append(WordSegment("x", 0.0, 1.0))
        assert b.words == []

    def test_with_words(self):
        words = [WordSegment("你", 0.0, 0.3), WordSegment("好", 0.3, 0.6)]
        t = TranscriptResult(text="你好", language="zh", words=words)
        assert len(t.words) == 2
        assert t.words[0].word == "你"
