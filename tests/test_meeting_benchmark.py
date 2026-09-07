"""Speaker-aware public meeting benchmark tests."""
from __future__ import annotations

import json

import pytest

from src.diarize import SpeakerSegment
from src.meeting_benchmark import (
    concatenated_permutation_error_rate,
    diarization_error,
    load_reference_turns,
    speaker_texts_from_reference,
    speaker_texts_from_words,
)


def test_reference_turns_are_validated_sorted_and_grouped(tmp_path):
    path = tmp_path / "turns.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"speaker_id": "B", "start_s": 2, "end_s": 3, "text": "later"},
                {"speaker_id": "A", "start_s": 0, "end_s": 1, "text": "first"},
                {"speaker_id": "A", "start_s": 1, "end_s": 2, "text": "second"},
            )
        ),
        encoding="utf-8",
    )

    turns = load_reference_turns(path)

    assert [turn.start for turn in turns] == [0, 1, 2]
    assert speaker_texts_from_reference(turns) == {
        "A": "first second",
        "B": "later",
    }


def test_cpwer_is_permutation_invariant_and_penalizes_extra_words():
    reference = {"A": "one two", "B": "three"}
    swapped = {"X": "three", "Y": "one two"}
    extra = {"X": "three extra", "Y": "one two"}

    assert concatenated_permutation_error_rate(
        reference, swapped, metric="cpwer"
    ) == {"errors": 0, "reference_units": 3, "rate": 0}
    assert concatenated_permutation_error_rate(
        reference, extra, metric="cpwer"
    )["rate"] == pytest.approx(1 / 3)


def test_cpcer_normalizes_cjk_spacing_and_speaker_labels():
    score = concatenated_permutation_error_rate(
        {"A": "你好", "B": "世界"},
        {"speaker-1": "世 界", "speaker-2": "你号"},
        metric="cpcer",
    )

    assert score == {"errors": 1, "reference_units": 4, "rate": 0.25}


def test_words_are_assigned_to_detected_speakers_by_timestamps():
    words = [
        {"text": "one", "start": 0.1, "end": 0.4},
        {"text": "two", "start": 1.1, "end": 1.4},
    ]
    turns = [
        SpeakerSegment("X", 0, 1),
        SpeakerSegment("Y", 1, 2),
    ]

    assert speaker_texts_from_words(words, turns) == {"X": "one", "Y": "two"}


def test_der_uses_optimal_speaker_mapping_and_scores_missed_speech():
    reference_path_turns = [
        type("Turn", (), {"speaker": "A", "start": 0.0, "end": 1.0})(),
        type("Turn", (), {"speaker": "B", "start": 1.0, "end": 2.0})(),
    ]
    perfect_with_swapped_names = [
        SpeakerSegment("Y", 0, 1),
        SpeakerSegment("X", 1, 2),
    ]
    missed_second = [SpeakerSegment("Y", 0, 1)]

    assert diarization_error(reference_path_turns, perfect_with_swapped_names)[
        "diarization error rate"
    ] == 0
    assert diarization_error(reference_path_turns, missed_second)[
        "diarization error rate"
    ] == pytest.approx(0.5)
