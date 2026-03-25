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

    # F1: mock _validate_model_local so tests don't need real model dirs
    with patch("os.path.isfile", return_value=True), \
         patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
         patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
         patch(f"{PIPELINE}.ASREngine", mock_asr_cls), \
         patch(f"{PIPELINE}.WhisperASREngine", MagicMock(return_value=mock_asr_instance)), \
         patch(f"{PIPELINE}.DiarizationEngine", mock_diarize_cls), \
         patch(f"{PIPELINE}.ModelManager", mock_mm_cls), \
         patch(f"{PIPELINE}._validate_model_local", return_value="/fake/model/path"), \
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


class TestMergeChunkTexts:
    def test_english_chunks_join_with_space(self):
        from src.pipeline import _merge_chunk_texts
        text = _merge_chunk_texts(["May I see", "your passport"], "en")
        assert text == "May I see your passport"

    def test_cjk_chunks_join_without_ascii_space(self):
        from src.pipeline import _merge_chunk_texts
        text = _merge_chunk_texts(["你好", "世界"], "zh")
        assert text == "你好世界"


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
             patch(f"{PIPELINE}._validate_model_local", return_value="/fake/model/path"), \
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
             patch(f"{PIPELINE}._validate_model_local", return_value="/fake/model/path"), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path))

        assert not os.path.exists(tmp_wav_path)


# ── local-model-only (F1) ─────────────────────────────────────────────────────
# Note: auto-download was removed in r02-b5 (F1). Models must be pre-downloaded.
# Detailed ModelNotReadyError tests live in test_pipeline_local_only.py.

class TestLocalModelOnly:
    """pipeline.run() raises ModelNotReadyError when models are not downloaded (F1)."""

    def test_raises_when_asr_not_downloaded(self, tmp_path):
        """run() raises ModelNotReadyError if ASR model is not present."""
        from src.pipeline import run, ModelNotReadyError
        wav = str(tmp_path / "audio.wav")
        mm = MagicMock()
        mm.is_downloaded.return_value = False
        mm.local_path.return_value = str(tmp_path)

        mock_asr = MagicMock()
        mock_asr.transcribe.return_value = _make_asr_result()
        mock_diarize = MagicMock()
        mock_diarize.diarize.return_value = _make_speaker_segments()

        with patch("os.path.isfile", return_value=True), \
             patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
             patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
             patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mm)), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            with pytest.raises(ModelNotReadyError):
                run("/audio/test.mp3", str(tmp_path), engine="qwen")

    def test_no_download_called_even_when_present(self, tmp_path, mock_pipeline_deps):
        """mm.download() is never called regardless of model presence (F1)."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        mm.is_downloaded.return_value = True
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        mm.download.assert_not_called()


# ── aligner optional use ──────────────────────────────────────────────────────

class TestAlignerOptional:
    """Aligner is used if present (local_path), ignored if not (no auto-download in F1)."""

    def test_aligner_not_downloaded_automatically(self, tmp_path, mock_pipeline_deps):
        """mm.download() is never called for aligner (F1: no auto-download)."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        mm.is_downloaded.return_value = True
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        mm.download.assert_not_called()

    def test_aligner_path_passed_to_asr_when_present(self, tmp_path, mock_pipeline_deps):
        """When aligner is_downloaded, its local_path is passed to ASREngine."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        mm.is_downloaded.return_value = True
        mm.local_path.return_value = "/fake/aligner/path"
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        call_kwargs = mock_pipeline_deps["asr_cls"].call_args[1]
        # aligner_path should be set (not None)
        assert call_kwargs.get("aligner_path") is not None

    def test_aligner_not_passed_when_not_present(self, tmp_path, mock_pipeline_deps):
        """When aligner is not is_downloaded, aligner_path=None is passed to ASREngine."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        # asr and diarizer present; aligner not present
        mm.is_downloaded.side_effect = lambda mid: mid != "qwen3-forced-aligner"
        run("/audio/test.mp3", str(tmp_path), engine="qwen")
        call_kwargs = mock_pipeline_deps["asr_cls"].call_args[1]
        assert call_kwargs.get("aligner_path") is None


# ── asr_model_id / _WHISPER_SIZE_MAP (P3) ─────────────────────────────────────

class TestAsrModelId:
    def test_whisper_size_map_contains_expected_keys(self):
        from src.pipeline import _WHISPER_SIZE_MAP
        assert _WHISPER_SIZE_MAP["whisper-large-v3-turbo"] == "turbo"
        assert _WHISPER_SIZE_MAP["whisper-large-v3"] == "large"
        assert _WHISPER_SIZE_MAP["whisper-medium"] == "medium"

    def test_asr_model_id_overrides_qwen_default(self, tmp_path, mock_pipeline_deps):
        """asr_model_id replaces the hardcoded 'qwen3-asr-1.7b' for qwen engine."""
        from src.pipeline import run
        mm = mock_pipeline_deps["mm_instance"]
        run("/audio/test.mp3", str(tmp_path), engine="qwen",
            asr_model_id="qwen3-asr-1.7b")
        # ASREngine should be called with model_path from mm.local_path("qwen3-asr-1.7b")
        mock_pipeline_deps["asr_cls"].assert_called_once()

    def test_asr_model_id_whisper_selects_turbo(self, tmp_path):
        """asr_model_id='whisper-large-v3-turbo' → WhisperASREngine(size='turbo')."""
        wav = str(tmp_path / "audio.wav")
        mock_asr_instance = MagicMock()
        mock_asr_instance.transcribe.return_value = _make_asr_result()
        mock_whisper_cls = MagicMock(return_value=mock_asr_instance)
        mock_diarize_instance = MagicMock()
        mock_diarize_instance.diarize.return_value = _make_speaker_segments()
        mock_mm_instance = MagicMock()
        mock_mm_instance.is_downloaded.return_value = True

        with patch("os.path.isfile", return_value=True), \
             patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
             patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
             patch(f"{PIPELINE}.ASREngine", MagicMock()), \
             patch(f"{PIPELINE}.WhisperASREngine", mock_whisper_cls), \
             patch(f"{PIPELINE}.DiarizationEngine", MagicMock(return_value=mock_diarize_instance)), \
             patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mock_mm_instance)), \
             patch(f"{PIPELINE}._validate_model_local", return_value="/fake/model/path"), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path), engine="whisper",
                asr_model_id="whisper-large-v3-turbo")

        mock_whisper_cls.assert_called_once_with(size="turbo")

    def test_asr_model_id_whisper_large(self, tmp_path):
        """asr_model_id='whisper-large-v3' → WhisperASREngine(size='large')."""
        wav = str(tmp_path / "audio.wav")
        mock_asr_instance = MagicMock()
        mock_asr_instance.transcribe.return_value = _make_asr_result()
        mock_whisper_cls = MagicMock(return_value=mock_asr_instance)
        mock_diarize_instance = MagicMock()
        mock_diarize_instance.diarize.return_value = _make_speaker_segments()
        mock_mm_instance = MagicMock()
        mock_mm_instance.is_downloaded.return_value = True

        with patch("os.path.isfile", return_value=True), \
             patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
             patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
             patch(f"{PIPELINE}.ASREngine", MagicMock()), \
             patch(f"{PIPELINE}.WhisperASREngine", mock_whisper_cls), \
             patch(f"{PIPELINE}.DiarizationEngine", MagicMock(return_value=mock_diarize_instance)), \
             patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mock_mm_instance)), \
             patch(f"{PIPELINE}._validate_model_local", return_value="/fake/model/path"), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path), engine="whisper",
                asr_model_id="whisper-large-v3")

        mock_whisper_cls.assert_called_once_with(size="large")

    def test_no_asr_model_id_whisper_defaults_to_turbo(self, tmp_path):
        """When asr_model_id=None, WhisperASREngine defaults to size='turbo'."""
        wav = str(tmp_path / "audio.wav")
        mock_asr_instance = MagicMock()
        mock_asr_instance.transcribe.return_value = _make_asr_result()
        mock_whisper_cls = MagicMock(return_value=mock_asr_instance)
        mock_diarize_instance = MagicMock()
        mock_diarize_instance.diarize.return_value = _make_speaker_segments()
        mock_mm_instance = MagicMock()
        mock_mm_instance.is_downloaded.return_value = True

        with patch("os.path.isfile", return_value=True), \
             patch(f"{PIPELINE}.to_wav", return_value=(wav, False)), \
             patch(f"{PIPELINE}.split_to_chunks", return_value=[(wav, 0.0)]), \
             patch(f"{PIPELINE}.ASREngine", MagicMock()), \
             patch(f"{PIPELINE}.WhisperASREngine", mock_whisper_cls), \
             patch(f"{PIPELINE}.DiarizationEngine", MagicMock(return_value=mock_diarize_instance)), \
             patch(f"{PIPELINE}.ModelManager", MagicMock(return_value=mock_mm_instance)), \
             patch(f"{PIPELINE}._validate_model_local", return_value="/fake/model/path"), \
             patch(f"{PIPELINE}.merge", return_value=_make_blocks()), \
             patch(f"{PIPELINE}.to_json"), \
             patch(f"{PIPELINE}.to_markdown"):
            from src.pipeline import run
            run("/audio/test.mp3", str(tmp_path), engine="whisper")

        mock_whisper_cls.assert_called_once_with(size="turbo")
