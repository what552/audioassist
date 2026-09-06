"""Canonical transcript-language handling shared by persistence and services."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
import unicodedata


_LANGUAGE_ALIASES = {
    "chinese": "zh",
    "mandarin": "zh",
    "cmn": "zh",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zho": "zh",
    "cantonese": "yue",
    "yue": "yue",
    "english": "en",
    "en": "en",
    "eng": "en",
    "french": "fr",
    "fr": "fr",
    "fra": "fr",
    "german": "de",
    "de": "de",
    "deu": "de",
    "spanish": "es",
    "es": "es",
    "spa": "es",
    "portuguese": "pt",
    "pt": "pt",
    "por": "pt",
    "russian": "ru",
    "ru": "ru",
    "rus": "ru",
    "arabic": "ar",
    "ar": "ar",
    "ara": "ar",
    "hindi": "hi",
    "hi": "hi",
    "hin": "hi",
    "japanese": "ja",
    "ja": "ja",
    "jpn": "ja",
    "korean": "ko",
    "ko": "ko",
    "kor": "ko",
    "mixed": "mixed",
    "multilingual": "mixed",
}
_UNKNOWN_LANGUAGE_VALUES = {"", "unknown", "und", "undefined", "none", "null"}


def canonical_language_code(language: str | None) -> str:
    """Map backend names and common ISO variants to stable application codes."""
    raw = (language or "").strip().lower().replace("_", "-")
    if raw in _UNKNOWN_LANGUAGE_VALUES:
        return ""
    return _LANGUAGE_ALIASES.get(raw, raw)


def _is_han(character: str) -> bool:
    value = ord(character)
    return (
        0x3400 <= value <= 0x4DBF
        or 0x4E00 <= value <= 0x9FFF
        or 0xF900 <= value <= 0xFAFF
    )


def _is_kana(character: str) -> bool:
    value = ord(character)
    return 0x3040 <= value <= 0x30FF or 0x31F0 <= value <= 0x31FF


def _is_hangul(character: str) -> bool:
    value = ord(character)
    return 0x1100 <= value <= 0x11FF or 0x3130 <= value <= 0x318F or 0xAC00 <= value <= 0xD7AF


def _script_counts(text: str) -> tuple[int, int, int, int]:
    han = kana = hangul = latin = 0
    for character in text or "":
        if _is_han(character):
            han += 1
        elif _is_kana(character):
            kana += 1
        elif _is_hangul(character):
            hangul += 1
        elif character.isalpha() and "LATIN" in unicodedata.name(character, ""):
            latin += 1
    return han, kana, hangul, latin


def _language_from_text(text: str) -> str:
    han, kana, hangul, latin = _script_counts(text)
    if kana >= 2:
        return "ja"
    if hangul >= 2:
        return "ko"

    total = han + latin
    if total == 0:
        return ""
    han_share = han / total
    latin_share = latin / total
    # Treat isolated product/person names as part of the surrounding language;
    # require a meaningful phrase in both scripts before declaring code-switching.
    if han >= 12 and latin >= 12 and han_share >= 0.15 and latin_share >= 0.15:
        return "mixed"
    if han >= 2 and han_share >= 0.35:
        return "zh"
    if latin >= 2 and latin_share >= 0.80:
        return "en"
    return ""


def canonical_transcript_language(language: str | None, text: str = "") -> str:
    """Resolve backend metadata against a conservative transcript-script signal.

    Qwen backends return full language names and can occasionally report English
    for strongly Chinese text.  Script inference only overrides that metadata
    when the transcript provides a strong, unambiguous signal.
    """
    reported = canonical_language_code(language)
    inferred = _language_from_text(text)
    han, kana, hangul, latin = _script_counts(text)
    strong_inference = (
        inferred == "zh" and han >= 6
        or inferred == "ja" and kana >= 2
        or inferred == "ko" and hangul >= 2
        or inferred == "en" and latin >= 12
    )

    if inferred == "mixed":
        return "mixed"
    if not reported:
        return inferred
    if reported == "mixed":
        return "mixed"
    if (
        reported in {"en", "zh", "yue", "ja", "ko"}
        and inferred
        and strong_inference
    ):
        if reported == "yue" and inferred == "zh":
            return "yue"
        return inferred
    return reported


def resolve_transcript_language(languages: Iterable[str], text: str = "") -> str:
    """Resolve multiple chunk reports using text first, then a stable majority."""
    canonical = [canonical_language_code(language) for language in languages]
    canonical = [language for language in canonical if language]
    if not canonical:
        return canonical_transcript_language("", text)
    majority = Counter(canonical).most_common(1)[0][0]
    return canonical_transcript_language(majority, text)
