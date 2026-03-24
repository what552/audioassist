"""Tests for src/merge.py — merge(), to_json(), to_markdown()."""
import json
import os
import pytest

from src.types import WordSegment, TranscriptResult
from src.diarize import SpeakerSegment
from src.merge import (
    SpeakerBlock,
    get_speaker_at,
    merge,
    split_long_blocks,
    to_json,
    to_markdown,
    _fmt_time,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def two_speakers():
    return [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=3.0),
        SpeakerSegment(speaker="SPEAKER_01", start=3.0, end=6.0),
    ]


@pytest.fixture
def words_two_speakers():
    return [
        WordSegment(word="你好", start=0.1, end=0.5),
        WordSegment(word="世界", start=0.6, end=1.0),
        WordSegment(word="再见", start=3.2, end=3.7),
        WordSegment(word="朋友", start=3.8, end=4.2),
    ]


@pytest.fixture
def asr_result(words_two_speakers):
    return TranscriptResult(
        text="你好世界再见朋友",
        language="zh",
        words=words_two_speakers,
    )


# ── get_speaker_at ────────────────────────────────────────────────────────────

class TestGetSpeakerAt:
    def test_finds_correct_speaker(self, two_speakers):
        assert get_speaker_at(1.0, two_speakers) == "SPEAKER_00"
        assert get_speaker_at(4.0, two_speakers) == "SPEAKER_01"

    def test_unknown_when_no_match(self, two_speakers):
        assert get_speaker_at(10.0, two_speakers) == "UNKNOWN"

    def test_empty_segments(self):
        assert get_speaker_at(1.0, []) == "UNKNOWN"

    def test_boundary_inclusive(self, two_speakers):
        # start and end are inclusive
        assert get_speaker_at(0.0, two_speakers) == "SPEAKER_00"
        assert get_speaker_at(3.0, two_speakers) == "SPEAKER_00"


# ── merge ─────────────────────────────────────────────────────────────────────

class TestMerge:
    def test_splits_by_speaker(self, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        assert len(blocks) == 2
        assert blocks[0].speaker == "SPEAKER_00"
        assert blocks[1].speaker == "SPEAKER_01"

    def test_block_text(self, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        assert blocks[0].text == "你好世界"
        assert blocks[1].text == "再见朋友"

    def test_block_timestamps(self, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        assert blocks[0].start == pytest.approx(0.1)
        assert blocks[0].end == pytest.approx(1.0)
        assert blocks[1].start == pytest.approx(3.2)
        assert blocks[1].end == pytest.approx(4.2)

    def test_block_words(self, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        assert len(blocks[0].words) == 2
        assert blocks[0].words[0]["word"] == "你好"

    def test_no_words_falls_back_to_single_block(self, two_speakers):
        asr = TranscriptResult(text="hello world", language="en", words=[])
        blocks = merge(asr, two_speakers)
        assert len(blocks) == 1
        assert blocks[0].text == "hello world"
        assert blocks[0].words == []

    def test_single_speaker_all_unknown(self):
        asr = TranscriptResult(
            text="test",
            language="en",
            words=[WordSegment("test", 0.0, 1.0)],
        )
        blocks = merge(asr, [])
        assert len(blocks) == 1
        assert blocks[0].speaker == "UNKNOWN"

    def test_consecutive_same_speaker_merged(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 10.0)]
        words = [
            WordSegment("a", 1.0, 1.5),
            WordSegment("b", 2.0, 2.5),
            WordSegment("c", 3.0, 3.5),
        ]
        asr = TranscriptResult(text="abc", language="en", words=words)
        blocks = merge(asr, segs)
        assert len(blocks) == 1
        assert blocks[0].text == "abc"

    def test_splits_single_speaker_block_on_long_pause(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 20.0)]
        words = [
            WordSegment("Hello", 0.0, 0.4),
            WordSegment("world.", 0.4, 0.8),
            WordSegment("Next", 2.4, 2.8),
            WordSegment("topic", 2.8, 3.1),
        ]
        asr = TranscriptResult(text="Hello world. Next topic", language="en", words=words)
        blocks = merge(asr, segs)
        assert len(blocks) == 2
        assert blocks[0].text == "Helloworld."
        assert blocks[1].text == "Nexttopic"

    def test_splits_single_speaker_block_when_duration_grows_too_long(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 80.0)]
        words = [
            WordSegment("a", 0.0, 0.2),
            WordSegment("b", 10.0, 10.2),
            WordSegment("c", 20.0, 20.2),
            WordSegment("d", 30.0, 30.2),
            WordSegment("e", 40.0, 40.2),
            WordSegment("f", 50.0, 50.2),
        ]
        asr = TranscriptResult(text="abcdef", language="en", words=words)
        blocks = merge(asr, segs)
        assert len(blocks) >= 2
        assert "".join(block.text for block in blocks) == "abcdef"


# ── to_json ───────────────────────────────────────────────────────────────────

class TestToJson:
    def test_output_structure(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.json")
        to_json(blocks, "/audio/test.mp3", "zh", out)

        with open(out) as f:
            data = json.load(f)

        assert data["audio"] == "test.mp3"
        assert data["language"] == "zh"
        assert len(data["segments"]) == 2

    def test_segment_fields(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.json")
        to_json(blocks, "/audio/test.mp3", "zh", out)

        with open(out) as f:
            data = json.load(f)

        seg = data["segments"][0]
        assert "speaker" in seg
        assert "start" in seg
        assert "end" in seg
        assert "text" in seg
        assert "words" in seg

    def test_timestamps_rounded_to_3dp(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.json")
        to_json(blocks, "/audio/test.mp3", "zh", out)

        with open(out) as f:
            data = json.load(f)

        start = data["segments"][0]["start"]
        # Should be at most 3 decimal places
        assert round(start, 3) == start

    def test_has_filename_field(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.json")
        to_json(blocks, "/audio/test.mp3", "zh", out)

        with open(out) as f:
            data = json.load(f)

        assert data["filename"] == "test.mp3"

    def test_has_created_at_field(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.json")
        to_json(blocks, "/audio/test.mp3", "zh", out)

        with open(out) as f:
            data = json.load(f)

        assert "created_at" in data
        assert len(data["created_at"]) > 0


# ── to_markdown ───────────────────────────────────────────────────────────────

class TestToMarkdown:
    def test_contains_filename(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.md")
        to_markdown(blocks, "/audio/meeting.mp3", "zh", out)

        content = open(out).read()
        assert "meeting.mp3" in content

    def test_contains_speakers(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.md")
        to_markdown(blocks, "/audio/meeting.mp3", "zh", out)

        content = open(out).read()
        assert "SPEAKER_00" in content
        assert "SPEAKER_01" in content

    def test_contains_text(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.md")
        to_markdown(blocks, "/audio/meeting.mp3", "zh", out)

        content = open(out).read()
        assert "你好世界" in content
        assert "再见朋友" in content


# ── _fmt_time ─────────────────────────────────────────────────────────────────

class TestFmtTime:
    def test_seconds_only(self):
        assert _fmt_time(45.0) == "00:45"

    def test_minutes_and_seconds(self):
        assert _fmt_time(125.0) == "02:05"

    def test_hours(self):
        assert _fmt_time(3661.0) == "01:01:01"

    def test_zero(self):
        assert _fmt_time(0.0) == "00:00"
