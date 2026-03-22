"""Tests for src.pipeline.run_realtime_segments() and _dominant_speaker()."""
from __future__ import annotations
import json
import os
import wave

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from src.diarize import SpeakerSegment
from src.pipeline import _dominant_speaker


# ── _dominant_speaker ─────────────────────────────────────────────────────────

class TestDominantSpeaker:
    def _segs(self, *triples):
        return [SpeakerSegment(sp, s, e) for sp, s, e in triples]

    def test_full_overlap_single_speaker(self):
        segs = self._segs(("SPEAKER_00", 0.0, 5.0))
        assert _dominant_speaker(1.0, 3.0, segs) == "SPEAKER_00"

    def test_no_overlap_returns_unknown(self):
        segs = self._segs(("SPEAKER_00", 5.0, 10.0))
        assert _dominant_speaker(0.0, 2.0, segs) == "UNKNOWN"

    def test_picks_speaker_with_most_overlap(self):
        segs = self._segs(
            ("SPEAKER_00", 0.0, 2.0),   # 2 s overlap
            ("SPEAKER_01", 2.0, 4.5),   # 2.5 s overlap → wins
        )
        assert _dominant_speaker(0.0, 4.5, segs) == "SPEAKER_01"

    def test_partial_overlap_from_left(self):
        segs = self._segs(("SPEAKER_00", 0.0, 1.5))
        # Segment [1.0, 3.0] overlaps [0, 1.5] by 0.5 s
        result = _dominant_speaker(1.0, 3.0, segs)
        assert result == "SPEAKER_00"

    def test_empty_speaker_segs_returns_unknown(self):
        assert _dominant_speaker(0.0, 1.0, []) == "UNKNOWN"


# ── run_realtime_segments ─────────────────────────────────────────────────────

def _make_wav(path: str, duration_s: float = 1.0, sr: int = 16000):
    """Write a minimal silent WAV file."""
    n_frames = int(duration_s * sr)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n_frames)


@pytest.fixture
def wav_and_segs(tmp_path):
    wav = str(tmp_path / "session.wav")
    _make_wav(wav, duration_s=3.0)
    segs = [
        {"text": "Hello", "start": 0.1, "end": 1.0},
        {"text": "World", "start": 1.5, "end": 2.8},
    ]
    return wav, segs, tmp_path


class TestRunRealtimeSegments:
    def test_produces_json_and_md(self, wav_and_segs):
        from src.pipeline import run_realtime_segments

        wav, segs, out_dir = wav_and_segs
        mock_diarizer = MagicMock()
        mock_diarizer.diarize.return_value = [SpeakerSegment("SPEAKER_00", 0.0, 3.0)]

        mock_mm = MagicMock()
        mock_mm.is_downloaded.return_value = True

        with patch("src.pipeline.DiarizationEngine", return_value=mock_diarizer), \
             patch("src.pipeline.ModelManager",      return_value=mock_mm):
            json_path, md_path = run_realtime_segments(
                segments=segs,
                wav_path=wav,
                output_dir=str(out_dir),
                job_id="test-job",
            )

        assert os.path.exists(json_path)
        assert os.path.exists(md_path)

    def test_output_json_contains_segments(self, wav_and_segs):
        from src.pipeline import run_realtime_segments

        wav, segs, out_dir = wav_and_segs
        mock_diarizer = MagicMock()
        mock_diarizer.diarize.return_value = [SpeakerSegment("SPEAKER_00", 0.0, 3.0)]
        mock_mm = MagicMock()
        mock_mm.is_downloaded.return_value = True

        with patch("src.pipeline.DiarizationEngine", return_value=mock_diarizer), \
             patch("src.pipeline.ModelManager",      return_value=mock_mm):
            json_path, _ = run_realtime_segments(
                segments=segs,
                wav_path=wav,
                output_dir=str(out_dir),
                job_id="test-job2",
            )

        with open(json_path) as f:
            data = json.load(f)

        assert len(data["segments"]) == 2
        assert data["segments"][0]["text"] == "Hello"
        assert data["segments"][1]["text"] == "World"

    def test_speaker_assigned_from_diarizer(self, wav_and_segs):
        from src.pipeline import run_realtime_segments

        wav, segs, out_dir = wav_and_segs
        # SPEAKER_00 covers 0-1 s, SPEAKER_01 covers 1-3 s
        mock_diarizer = MagicMock()
        mock_diarizer.diarize.return_value = [
            SpeakerSegment("SPEAKER_00", 0.0, 1.0),
            SpeakerSegment("SPEAKER_01", 1.0, 3.0),
        ]
        mock_mm = MagicMock()
        mock_mm.is_downloaded.return_value = True

        with patch("src.pipeline.DiarizationEngine", return_value=mock_diarizer), \
             patch("src.pipeline.ModelManager",      return_value=mock_mm):
            json_path, _ = run_realtime_segments(
                segments=segs,
                wav_path=wav,
                output_dir=str(out_dir),
                job_id="test-job3",
            )

        with open(json_path) as f:
            data = json.load(f)

        # "Hello" at [0.1, 1.0] → mostly SPEAKER_00
        assert data["segments"][0]["speaker"] == "SPEAKER_00"
        # "World" at [1.5, 2.8] → SPEAKER_01
        assert data["segments"][1]["speaker"] == "SPEAKER_01"

    def test_missing_wav_raises(self, tmp_path):
        from src.pipeline import run_realtime_segments
        with pytest.raises(FileNotFoundError):
            run_realtime_segments(
                segments=[{"text": "hi", "start": 0.0, "end": 1.0}],
                wav_path=str(tmp_path / "nonexistent.wav"),
                output_dir=str(tmp_path),
            )

    def test_progress_callback_called(self, wav_and_segs):
        from src.pipeline import run_realtime_segments

        wav, segs, out_dir = wav_and_segs
        mock_diarizer = MagicMock()
        mock_diarizer.diarize.return_value = []
        mock_mm = MagicMock()
        mock_mm.is_downloaded.return_value = True

        calls = []
        with patch("src.pipeline.DiarizationEngine", return_value=mock_diarizer), \
             patch("src.pipeline.ModelManager",      return_value=mock_mm):
            run_realtime_segments(
                segments=segs,
                wav_path=wav,
                output_dir=str(out_dir),
                job_id="test-job4",
                progress_callback=lambda p, m: calls.append(p),
            )

        assert calls, "progress_callback was never called"
        assert calls[-1] == pytest.approx(1.0)  # final progress
