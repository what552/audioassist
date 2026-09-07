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
    speaker_for_interval,
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


class TestSpeakerForInterval:
    def test_uses_overlap_not_only_word_midpoint(self):
        segments = [
            SpeakerSegment("SPEAKER_00", 0.0, 1.49),
            SpeakerSegment("SPEAKER_01", 1.49, 3.0),
        ]
        # The midpoint is after the boundary, but most of the word belongs to speaker 00.
        assert speaker_for_interval(1.0, 1.8, segments) == "SPEAKER_00"

    def test_near_even_cross_speaker_interval_stays_unknown(self):
        segments = [
            SpeakerSegment("SPEAKER_00", 0.0, 1.0),
            SpeakerSegment("SPEAKER_01", 1.0, 2.0),
        ]
        assert speaker_for_interval(0.5, 1.5, segments) == "UNKNOWN"

    def test_short_gap_between_same_speaker_turns_is_filled(self):
        segments = [
            SpeakerSegment("SPEAKER_00", 0.0, 1.0),
            SpeakerSegment("SPEAKER_00", 1.2, 2.0),
        ]
        assert speaker_for_interval(1.05, 1.15, segments) == "SPEAKER_00"

    def test_gap_between_different_speakers_stays_unknown(self):
        segments = [
            SpeakerSegment("SPEAKER_00", 0.0, 1.0),
            SpeakerSegment("SPEAKER_01", 1.2, 2.0),
        ]
        assert speaker_for_interval(1.05, 1.15, segments) == "UNKNOWN"

    def test_tiny_overlap_does_not_label_mostly_uncovered_interval(self):
        segments = [SpeakerSegment("SPEAKER_00", 0.0, 1.0)]
        assert speaker_for_interval(0.95, 2.0, segments) == "UNKNOWN"

    def test_small_leading_edge_jitter_is_snapped(self):
        segments = [SpeakerSegment("SPEAKER_00", 0.1, 2.0)]
        assert speaker_for_interval(0.0, 0.08, segments) == "SPEAKER_00"


# ── merge ─────────────────────────────────────────────────────────────────────

class TestMerge:
    def test_splits_by_speaker(self, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        assert len(blocks) == 2
        assert blocks[0].speaker == "SPEAKER_00"
        assert blocks[1].speaker == "SPEAKER_01"

    def test_block_text(self, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        assert blocks[0].text == "你好世界。"
        assert blocks[1].text == "再见朋友。"

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
        assert blocks[0].speaker == "UNKNOWN"

    def test_no_words_uses_speaker_when_diarization_has_only_one_identity(self):
        asr = TranscriptResult(text="hello world", language="en", words=[])
        blocks = merge(asr, [
            SpeakerSegment("SPEAKER_00", 0.0, 1.0),
            SpeakerSegment("SPEAKER_00", 2.0, 3.0),
        ])
        assert blocks[0].speaker == "SPEAKER_00"

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
        assert blocks[0].text == "a b c"

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
        assert blocks[0].text == "Hello world."
        assert blocks[1].text == "Next topic."

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
        assert " ".join(" ".join(block.text.split()) for block in blocks) == "a b c d e f."

    def test_english_words_keep_spaces(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 10.0)]
        words = [
            WordSegment("May", 0.0, 0.2),
            WordSegment("I", 0.3, 0.4),
            WordSegment("see", 0.5, 0.7),
            WordSegment("your", 0.8, 1.0),
            WordSegment("passport", 1.1, 1.4),
        ]
        asr = TranscriptResult(text="May I see your passport", language="en", words=words)
        blocks = merge(asr, segs)
        assert blocks[0].text == "May I see your passport."

    def test_cjk_words_stay_compact(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 10.0)]
        words = [
            WordSegment("你好", 0.0, 0.2),
            WordSegment("世界", 0.3, 0.5),
        ]
        asr = TranscriptResult(text="你好世界", language="zh", words=words)
        blocks = merge(asr, segs)
        assert blocks[0].text == "你好世界。"

    def test_english_pause_without_existing_punctuation_splits_and_adds_periods(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 20.0)]
        words = [
            WordSegment("Please", 0.0, 0.2),
            WordSegment("show", 0.2, 0.4),
            WordSegment("me", 0.4, 0.5),
            WordSegment("your", 0.5, 0.7),
            WordSegment("passport", 0.7, 1.0),
            WordSegment("thank", 1.7, 1.9),
            WordSegment("you", 1.9, 2.1),
            WordSegment("have", 2.1, 2.3),
            WordSegment("a", 2.3, 2.4),
            WordSegment("seat", 2.4, 2.6),
        ]
        asr = TranscriptResult(
            text="Please show me your passport thank you have a seat",
            language="en",
            words=words,
        )
        blocks = merge(asr, segs)
        assert len(blocks) == 2
        assert blocks[0].text == "Please show me your passport."
        assert blocks[1].text == "thank you have a seat."

    def test_cjk_length_split_does_not_invent_internal_sentence_boundary(self):
        segs = [SpeakerSegment("SPEAKER_00", 0.0, 40.0)]
        words = [
            WordSegment("这是", 0.0, 0.2),
            WordSegment("一个", 0.2, 0.4),
            WordSegment("没有", 0.4, 0.6),
            WordSegment("标点", 0.6, 0.8),
            WordSegment("也没", 0.8, 1.0),
            WordSegment("明显", 1.0, 1.2),
            WordSegment("停顿", 1.2, 1.4),
            WordSegment("但是", 1.4, 1.6),
            WordSegment("需要", 1.6, 1.8),
            WordSegment("基础", 1.8, 2.0),
            WordSegment("断句", 2.0, 2.2),
            WordSegment("的长", 2.2, 2.4),
            WordSegment("中文", 2.4, 2.6),
            WordSegment("片段", 2.6, 2.8),
        ]
        asr = TranscriptResult(
            text="这是一个没有标点也没明显停顿但是需要基础断句的长中文片段",
            language="zh",
            words=words,
        )
        blocks = merge(asr, segs)
        assert len(blocks) >= 2
        assert not blocks[0].text.endswith("。")
        assert blocks[-1].text.endswith("。")

    def test_speaker_switch_without_pause_does_not_invent_period(self):
        segs = [
            SpeakerSegment("SPEAKER_00", 0.0, 0.8),
            SpeakerSegment("SPEAKER_01", 0.8, 2.0),
        ]
        words = [
            WordSegment("we", 0.0, 0.2),
            WordSegment("need", 0.2, 0.4),
            WordSegment("to", 0.4, 0.5),
            WordSegment("consider", 0.5, 0.8),
            WordSegment("the", 0.8, 1.0),
            WordSegment("next", 1.0, 1.2),
            WordSegment("step", 1.2, 1.4),
            WordSegment("carefully", 1.4, 1.7),
        ]
        blocks = merge(TranscriptResult("", "en", words), segs)
        assert blocks[0].text == "we need to consider"
        assert blocks[1].text == "the next step carefully."

    def test_speaker_switch_after_sentence_pause_adds_period(self):
        segs = [
            SpeakerSegment("SPEAKER_00", 0.0, 1.0),
            SpeakerSegment("SPEAKER_01", 1.5, 3.0),
        ]
        words = [
            WordSegment("please", 0.0, 0.2),
            WordSegment("send", 0.2, 0.4),
            WordSegment("the", 0.4, 0.5),
            WordSegment("report", 0.5, 0.8),
            WordSegment("yes", 1.5, 1.7),
            WordSegment("I", 1.7, 1.8),
            WordSegment("will", 1.8, 2.0),
            WordSegment("do", 2.0, 2.2),
        ]
        blocks = merge(TranscriptResult("", "en", words), segs)
        assert blocks[0].text == "please send the report."

    @pytest.mark.parametrize("ending", [",", ":", "，", "：", "、", "…"])
    def test_existing_nonterminal_punctuation_is_not_doubled(self, ending):
        words = [
            WordSegment("this", 0.0, 0.1),
            WordSegment("is", 0.1, 0.2),
            WordSegment("already", 0.2, 0.3),
            WordSegment("marked" + ending, 0.3, 0.5),
        ]
        blocks = merge(
            TranscriptResult("", "en", words),
            [SpeakerSegment("SPEAKER_00", 0.0, 1.0)],
        )
        assert blocks[0].text.endswith(ending)

    def test_chinese_sentence_punctuation_inside_closing_quote_is_preserved(self):
        words = [
            WordSegment("他说", 0.0, 0.2),
            WordSegment("“我们现在出发。”", 0.2, 0.8),
        ]
        blocks = merge(
            TranscriptResult("", "zh", words),
            [SpeakerSegment("SPEAKER_00", 0.0, 1.0)],
        )
        assert blocks[0].text == "他说“我们现在出发。”"

    def test_japanese_language_uses_full_width_stop_and_no_ascii_spaces(self):
        words = [
            WordSegment("これ", 0.0, 0.2),
            WordSegment("から", 0.2, 0.4),
            WordSegment("始め", 0.4, 0.6),
            WordSegment("ます", 0.6, 0.8),
        ]
        blocks = merge(
            TranscriptResult("", "ja", words),
            [SpeakerSegment("SPEAKER_00", 0.0, 1.0)],
        )
        assert blocks[0].text == "これから始めます。"

    def test_mixed_text_uses_dominant_script_for_generated_terminal(self):
        words = [
            WordSegment("今天", 0.0, 0.2),
            WordSegment("讨论", 0.2, 0.4),
            WordSegment("MLX", 0.4, 0.6),
            WordSegment("方案", 0.6, 0.8),
        ]
        blocks = merge(
            TranscriptResult("", "mixed", words),
            [SpeakerSegment("SPEAKER_00", 0.0, 1.0)],
        )
        assert blocks[0].text == "今天讨论MLX方案。"

    @pytest.mark.parametrize(
        ("language", "tokens", "expected"),
        [
            ("ru", ["это", "важный", "новый", "результат"], "это важный новый результат."),
            ("ar", ["هذا", "اختبار", "صوتي", "واضح"], "هذا اختبار صوتي واضح."),
            ("ko", ["이것은", "명확한", "음성", "테스트"], "이것은 명확한 음성 테스트."),
        ],
    )
    def test_non_ascii_spaced_languages_receive_conservative_terminal(
        self, language, tokens, expected
    ):
        words = [
            WordSegment(token, index * 0.2, (index + 1) * 0.2)
            for index, token in enumerate(tokens)
        ]
        blocks = merge(
            TranscriptResult("", language, words),
            [SpeakerSegment("SPEAKER_00", 0.0, 2.0)],
        )
        assert blocks[0].text == expected

    def test_earlier_punctuation_does_not_disable_later_length_split(self):
        tokens = ["Intro."] + [f"word{index}" for index in range(1, 25)]
        words = [
            WordSegment(token, index * 0.2, (index + 1) * 0.2)
            for index, token in enumerate(tokens)
        ]
        blocks = merge(
            TranscriptResult("", "en", words),
            [SpeakerSegment("SPEAKER_00", 0.0, 10.0)],
        )
        assert len(blocks) >= 2
        assert "Intro." in blocks[0].text


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

    def test_optional_qwen_backend_metadata(self, tmp_path, asr_result, two_speakers):
        blocks = merge(asr_result, two_speakers)
        out = str(tmp_path / "result.json")

        to_json(
            blocks,
            "/audio/test.mp3",
            "zh",
            out,
            qwen_backend="pytorch-mps",
        )

        with open(out) as file:
            data = json.load(file)

        assert data["qwen_backend"] == "pytorch-mps"


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
