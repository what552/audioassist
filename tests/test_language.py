"""Canonical transcript-language detection and normalization tests."""

import pytest

from src.language import canonical_transcript_language, resolve_transcript_language


@pytest.mark.parametrize(
    ("reported", "text", "expected"),
    [
        ("Chinese", "这是中文转写内容。", "zh"),
        ("zho", "这是中文转写内容。", "zh"),
        ("English", "This is an English transcript.", "en"),
        ("eng", "This is an English transcript.", "en"),
        ("Cantonese", "呢段係廣東話內容。", "yue"),
        ("Japanese", "これは日本語の文章です。", "ja"),
        ("Korean", "이것은 한국어 문장입니다.", "ko"),
    ],
)
def test_known_language_names_and_codes_are_canonical(reported, text, expected):
    assert canonical_transcript_language(reported, text) == expected


def test_strong_chinese_text_corrects_incorrect_english_report():
    text = (
        "大家好，我是 Robert。这是一段跨平台声音克隆测试。"
        "我们将比较音色相似度、自然度、清晰度，以及中英文发音的稳定性。"
    )

    assert canonical_transcript_language("English", text) == "zh"


def test_substantial_chinese_and_english_is_mixed():
    text = (
        "各位好，今天介绍 CoWoS 和 HBM。"
        "The closer compute and memory are, the faster they communicate."
        "先进封装让芯片之间的数据交换速度进一步提升。"
    )

    assert canonical_transcript_language("English", text) == "mixed"


def test_english_name_inside_chinese_does_not_make_text_mixed():
    text = "大家好，我是 Robert。今天我们讨论产品计划和用户反馈。"

    assert canonical_transcript_language("English", text) == "zh"


def test_unknown_language_is_preserved_when_text_has_no_strong_script_signal():
    assert canonical_transcript_language("Esperanto", "1234") == "esperanto"


def test_empty_language_and_text_remain_empty():
    assert canonical_transcript_language("", "") == ""


def test_multiple_ambiguous_chunks_use_majority_language():
    assert resolve_transcript_language(
        ["Chinese", "English", "English"], "1234"
    ) == "en"


def test_latin_script_does_not_turn_reported_french_into_english():
    assert resolve_transcript_language(
        ["French", "French"], "Bonjour, ceci est une transcription française."
    ) == "fr"
