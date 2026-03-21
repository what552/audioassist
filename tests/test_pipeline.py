"""Tests for src/pipeline.py — run() with mocked ASR, diarize, audio_utils."""
import os
import uuid
from unittest.mock import MagicMock, patch, call
import pytest

from src.types import WordSegment, TranscriptResult
from src.diarize import SpeakerSegment
from src.merge import SpeakerBlock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_asr_result():
    return TranscriptResult(
        text="你好世界",
        language="zh",
        words=[
            WordSegment("你好", 0.1, 0.5),
            WordSegment("世界", 0.6, 1.0),
        ],
    )


def _make_speaker_segments():
    return [SpeakerSegment("SPEAKER_00", 0.0, 5.0)]


def _make_blocks():
    return [SpeakerBlock(
        speaker="SPEAKER_00",
        start=0.1,
        end=1.0,
        text="你好世界",
        words=[],
    )]


# ── Fixtures / patch targets ──────────────────────────────────────────────────

PIPELINE = "src.pipeline"


@pytest.fixture
def mock_pipeline_deps(tmp_path):
    """Patch all heavy dependencies in the pipeline module."""
    wav = str(tmp_path / "audio.wav")

    mock_asr_instance = MagicMock()
    mock_asr_instance.transcribe.return_value = _make_asr_result()

    mock_asr_cls = MagicMock(return_value=mock_asr_instance)

    mock_diarize_instance = MagicMock()
    mock_diarize_instance.diarize.return_value = _make_speaker_segments()
    mock_diarize_cls = MagicMock(return_value=mock_diarize_instance)

    mock_mm_instance = MagicMock()
    mock_mm_instance.is_downloaded.return_value = True
    mock_mm_instance.local_path.return_value = "/fake/model/path"
    mock_mm_cls = MagicMock(return_value=mock_mm_instance)

    with patch("os.path.isfile", return_value=True), \
         patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
         patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
         patch(f"{PIPELINE}.ASREngine", mock_asr_cls), \
         patch(f"{PIPELINE}.WhisperASREngine", MagicMock(return_value=mock_asr_instance)), \
         patch(f"{PIPELINE}.DiarizationEngine", mock_diarize_cls), \
         patch(f"{PIPELINE}.ModelManager", mock_mm_cls), \
         patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
         patch(f"{PIPELINE}.to_json"), \
         patch(f"{PIPELINE}.to_markdown"):
        yield {
            "wav": wav,
            "asr_cls": mock_asr_cls,
            "asr_instance": mock_asr_instance,
            "diarize_cls": mock_diarize_cls,
            "diarize_instance": mock_diarize_instance,
            "mm_cls": mock_mm_cls,
            "mm_instance": mock_mm_instance,
        }


# ── job_id handling ───────────────────────────────────────────────────────────

class TestJobId:
    def test_auto_generates_job_id(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        json_path, md_path = run("/audio/test.mp3", str(tmp_path))

        # Output files named after the auto job_id
        name = os.path.splitext(os.path.basename(json_path))[0]
        assert len(name) == 36  # UUID format
        assert name == os.path.splitext(os.path.basename(md_path))[0]

    def test_uses_provided_job_id(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        job_id = "my-custom-job-id"
        json_path, md_path = run("/audio/test.mp3", str(tmp_path), job_id=job_id)

        assert os.path.basename(json_path) == f"{job_id}.json"
        assert os.path.basename(md_path) == f"{job_id}.md"

    def test_two_runs_produce_different_ids(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        json1, _ = run("/audio/test.mp3", str(tmp_path))
        json2, _ = run("/audio/test.mp3", str(tmp_path))
        assert json1 != json2


# ── progress_callback ─────────────────────────────────────────────────────────

class TestProgressCallback:
    def test_callback_called_multiple_times(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        calls = []
        run("/audio/test.mp3", str(tmp_path), progress_callback=lambda p, m: calls.append((p, m)))
        assert len(calls) >= 3

    def test_callback_receives_float_and_str(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        calls = []
        run("/audio/test.mp3", str(tmp_path), progress_callback=lambda p, m: calls.append((p, m)))
        for pct, msg in calls:
            assert isinstance(pct, float)
            assert isinstance(msg, str)

    def test_callback_starts_at_zero_ends_at_one(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        calls = []
        run("/audio/test.mp3", str(tmp_path), progress_callback=lambda p, m: calls.append((p, m)))
        assert calls[0][0] == pytest.approx(0.0)
        assert calls[-1][0] == pytest.approx(1.0)

    def test_no_callback_still_runs(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        json_path, md_path = run("/audio/test.mp3", str(tmp_path))
        assert json_path.endswith(".json")
        assert md_path.endswith(".md")


# ── diarizer model selection ──────────────────────────────────────────────────

class TestDiarizerModelSelection:
    def test_diarizer_model_id_passed_to_engine(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        run("/audio/test.mp3", str(tmp_path), diarizer_model_id="pyannote-diarization-3.1")
        call_kwargs = mock_pipeline_deps["diarize_cls"].call_args[1]
        assert call_kwargs.get("model_id") == "pyannote-diarization-3.1"

    def test_default_diarizer_model_id_is_none(self, tmp_path, mock_pipeline_deps):
        """When diarizer_model_id not provided, None is passed (DiarizationEngine uses its default)."""
        from src.pipeline import run
        run("/audio/test.mp3", str(tmp_path))
        call_kwargs = mock_pipeline_deps["diarize_cls"].call_args[1]
        assert call_kwargs.get("model_id") is None

    def test_hf_token_passed_to_diarizer(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        run("/audio/test.mp3", str(tmp_path), hf_token="hf_abc123")
        call_kwargs = mock_pipeline_deps["diarize_cls"].call_args[1]
        assert call_kwargs.get("hf_token") == "hf_abc123"


# ── engine selection ──────────────────────────────────────────────────────────

class TestEngineSelection:
    def test_qwen_engine_uses_asr_engine(self, tmp_path, mock_pipeline_deps):
        from src.pipeline import run
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        mock_pipeline_deps["asr_cls"].assert_called_once()

    def test_whisper_engine_uses_whisper_asr_engine(self, tmp_path):
        wav = str(tmp_path / "audio.wav")
        mock_asr_instance = MagicMock()
        mock_asr_instance.transcribe.return_value = _make_asr_result()
        mock_whisper_cls = MagicMock(return_value=mock_asr_instance)
        mock_asr_cls = MagicMock()
        mock_diarize_instance = MagicMock()
        mock_diarize_instance.diarize.return_value = _make_speaker_segments()
        mock_mm_instance = MagicMock()
        mock_mm_instance.is_downloaded.return_value = True
        mock_mm_instance.local_path.return_value = "/fake/model/path"

        with patch("os.path.isfile", return_value=True), \
             patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
             patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
             patch(f"{PIPELINE}.ASREngine", mock_asr_cls), \
             patch(f"{PIPELINE}.WhisperASREngine", mock_whisper_cls), \
             patch(f"{PIPELINE}.DiarizationEngine", MagicMock(return_value=mock_diarize_instance)), \
             patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mock_mm_instance)), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path), engine="whisper")

        mock_whisper_cls.assert_called_once()
        mock_asr_cls.assert_not_called()


# ── temp file cleanup ─────────────────────────────────────────────────────────

class TestTempFileCleanup:
    def test_temp_wav_cleaned_up(self, tmp_path):
        import tempfile

        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_wav.close()
        tmp_wav_path = tmp_wav.name

        mock_asr_instance = MagicMock()
        mock_asr_instance.transcribe.return_value = _make_asr_result()
        mock_diarize_instance = MagicMock()
        mock_diarize_instance.diarize.return_value = _make_speaker_segments()

        mock_mm_instance = MagicMock()
        mock_mm_instance.is_downloaded.return_value = True
        mock_mm_instance.local_path.return_value = "/fake/model/path"
        with patch("os.path.isfile", return_value=True), \
             patch(f"{PIPELINE}.to_wav", return_value=(tmp_wav_path, True)), \
             patch(f"{PIPELINE}.split_to_chunks", return_value=[(tmp_wav_path, 0.0)]), \
             patch(f"{PIPELINE}.ASREngine", MagicMock(return_value=mock_asr_instance)), \
             patch(f"{PIPELINE}.DiarizationEngine", MagicMock(return_value=mock_diarize_instance)), \
             patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mock_mm_instance)), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path))

        assert not os.path.exists(tmp_wav_path)


# ── auto-download ─────────────────────────────────────────────────────────────

class TestAutoDownload:
    """pipeline.run() must auto-download missing ASR / diarizer models."""

    def _make_deps(self, tmp_path, mm_instance):
        """Shared patch context for auto-download tests."""
        wav = str(tmp_path / "audio.wav")
        mock_asr_instance = MagicMock()
        mock_asr_instance.transcribe.return_value = _make_asr_result()
        mock_diarize_instance = MagicMock()
        mock_diarize_instance.diarize.return_value = _make_speaker_segments()
        return (
            wav,
            patch("os.path.isfile", return_value=True),
            patch(f"{PIPELINE}.to_wav", return_value=(wav, False)),
            patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]),
            patch(f"{PIPELINE}.ASREngine", MagicMock(return_value=mock_asr_instance)),
            patch(f"{PIPELINE}.WhisperASREngine", MagicMock(return_value=mock_asr_instance)),
            patch(f"{PIPELINE}.DiarizationEngine", MagicMock(return_value=mock_diarize_instance)),
            patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mm_instance)),
            patch(f"{PIPELINE}.merge", return_value=_make_blocks()),
            patch(f"{PIPELINE}.to_json"),
            patch(f"{PIPELINE}.to_markdown"),
        )

    def test_asr_model_downloaded_when_missing(self, tmp_path):
        mm = MagicMock()
        mm.is_downloaded.return_value = False
        mm.local_path.return_value = "/fake/model/path"
        _, *patches = self._make_deps(tmp_path, mm)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path), engine="qwen")
        mm.download.assert_called()

    def test_asr_model_not_downloaded_when_present(self, tmp_path):
        mm = MagicMock()
        mm.is_downloaded.return_value = True
        mm.local_path.return_value = "/fake/model/path"
        _, *patches = self._make_deps(tmp_path, mm)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path), engine="qwen")
        mm.download.assert_not_called()


# ── aligner auto-download ─────────────────────────────────────────────────────

class TestAlignerAutoDownload:
    """Aligner is optional — downloaded when missing, silently skipped on failure."""

    def test_aligner_downloaded_when_missing(self, tmp_path, mock_pipeline_deps):
        """mm.download() called for aligner when not present."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        # ASR and diarizer present; aligner missing
        mm.is_downloaded.side_effect = lambda mid: mid != "qwen3-forced-aligner"
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        downloaded_ids = [c.args[0] for c in mm.download.call_args_list]
        assert "qwen3-forced-aligner" in downloaded_ids

    def test_aligner_not_downloaded_when_present(self, tmp_path, mock_pipeline_deps):
        """mm.download() not called when aligner already present."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        mm.is_downloaded.return_value = True
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        downloaded_ids = [c.args[0] for c in mm.download.call_args_list]
        assert "qwen3-forced-aligner" not in downloaded_ids

    def test_aligner_download_failure_does_not_raise(self, tmp_path, mock_pipeline_deps):
        """Pipeline continues (aligner_path=None) if download fails."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        # ASR and diarizer present; aligner never present
        mm.is_downloaded.side_effect = (
            lambda mid: mid in ("qwen3-asr-1.7b", "pyannote-diarization-community-1")
        )
        def _download(mid, **kwargs):
            if mid == "qwen3-forced-aligner":
                raise RuntimeError("network error")
        mm.download.side_effect = _download
        # Must not raise
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
