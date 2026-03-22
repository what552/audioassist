"""Tests for the _lang_instruction helper used by summarize()."""
import app as app_module
from app import _lang_instruction


class TestLangInstruction:
    def test_zh_returns_chinese_instruction(self):
        result = _lang_instruction("zh", "")
        assert "中文" in result

    def test_en_returns_english_instruction(self):
        result = _lang_instruction("en", "")
        assert result  # non-empty
        assert "English" in result or "english" in result.lower()

    def test_unknown_language_with_cjk_text_returns_chinese(self):
        # >10% CJK characters → auto-detect as Chinese
        cjk_text = "今天的会议主要讨论了产品发布计划和用户反馈情况。" * 5
        result = _lang_instruction("", cjk_text)
        assert "中文" in result

    def test_unknown_language_latin_text_returns_empty(self):
        latin_text = "The quick brown fox jumps over the lazy dog."
        result = _lang_instruction("", latin_text)
        assert result == ""

    def test_missing_language_empty_text_returns_empty(self):
        assert _lang_instruction("", "") == ""

    def test_unknown_language_code_falls_back_to_cjk_detection(self):
        # If language is some unknown code but text is mostly CJK, still detect
        cjk_text = "这是一段中文文字" * 10
        result = _lang_instruction("xx", cjk_text)
        assert "中文" in result
