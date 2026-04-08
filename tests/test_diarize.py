"""Tests for src/diarize.py — DiarizationEngine local-path loading."""
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

from src.diarize import DiarizationEngine, DEFAULT_DIARIZER_MODEL


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_modules():
    """Return fake pyannote + torch modules and the Pipeline mock class."""
    pyannote_pkg = types.ModuleType("pyannote")
    pyannote_audio = types.ModuleType("pyannote.audio")
    mock_pipeline_cls = MagicMock()
    mock_pipeline_cls.from_pretrained.return_value = MagicMock()
    pyannote_audio.Pipeline = mock_pipeline_cls
    pyannote_pkg.audio = pyannote_audio

    # Fake torch: device() returns a MagicMock, backends.mps/cuda report unavailable
    torch_mod = MagicMock()
    torch_mod.device.return_value = MagicMock()
    torch_mod.backends.mps.is_available.return_value = False
    torch_mod.cuda.is_available.return_value = False

    modules = {
        "pyannote": pyannote_pkg,
        "pyannote.audio": pyannote_audio,
        "torch": torch_mod,
    }
    return modules, mock_pipeline_cls


# ── default model ─────────────────────────────────────────────────────────────

class TestDefaultModel:
    def test_default_model_id(self):
        engine = DiarizationEngine()
        assert engine.model_id == DEFAULT_DIARIZER_MODEL

    def test_none_model_id_becomes_default(self):
        engine = DiarizationEngine(model_id=None)
        assert engine.model_id == DEFAULT_DIARIZER_MODEL

    def test_explicit_model_id_used(self):
        engine = DiarizationEngine(model_id="pyannote-diarization-3.1")
        assert engine.model_id == "pyannote-diarization-3.1"


# ── token handling ────────────────────────────────────────────────────────────

class TestTokenHandling:
    def _mock_mm(self, requires_token: bool, local_path: str, is_downloaded: bool = True):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock(requires_token=requires_token)
        mock_mm.local_path.return_value = local_path
        mock_mm.is_downloaded.return_value = is_downloaded
        return mock_mm

    def test_community_1_loads_without_token(self, monkeypatch):
        """community-1 (requires_token=False) must load with no token."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        engine = DiarizationEngine(model_id="pyannote-diarization-community-1")

        fake_mods, mock_pipeline_cls = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm_cls.return_value = self._mock_mm(
                requires_token=False,
                local_path="/models/pyannote-diarization-community-1",
            )
            engine.load()  # must not raise

        mock_pipeline_cls.from_pretrained.assert_called_once_with(
            "/models/pyannote-diarization-community-1"
        )

    def test_3_1_without_token_raises(self, monkeypatch):
        """3.1 (requires_token=True) must raise ValueError when no token."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        engine = DiarizationEngine(model_id="pyannote-diarization-3.1", hf_token=None)

        fake_mods, _ = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm_cls.return_value = self._mock_mm(
                requires_token=True,
                local_path="/models/pyannote-diarization-3.1",
            )
            with pytest.raises(ValueError, match="HF_TOKEN required"):
                engine.load()

    def test_3_1_with_token_loads_without_error(self, monkeypatch):
        """3.1 with token provided must reach from_pretrained."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        engine = DiarizationEngine(
            model_id="pyannote-diarization-3.1",
            hf_token="hf_fake_token",
        )

        fake_mods, mock_pipeline_cls = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm_cls.return_value = self._mock_mm(
                requires_token=True,
                local_path="/models/pyannote-diarization-3.1",
            )
            engine.load()  # must not raise

        mock_pipeline_cls.from_pretrained.assert_called_once_with(
            "/models/pyannote-diarization-3.1"
        )

    def test_hf_token_env_var_used(self, monkeypatch):
        """HF_TOKEN env var is picked up when hf_token= not passed."""
        monkeypatch.setenv("HF_TOKEN", "hf_from_env")
        engine = DiarizationEngine(model_id="pyannote-diarization-3.1")
        assert engine.hf_token == "hf_from_env"


# ── local path loading ────────────────────────────────────────────────────────

class TestLocalPathLoading:
    def _mock_mm(self, requires_token: bool, local_path: str, is_downloaded: bool = True):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock(requires_token=requires_token)
        mock_mm.local_path.return_value = local_path
        mock_mm.is_downloaded.return_value = is_downloaded
        return mock_mm

    def test_load_uses_model_manager_local_path(self):
        """Pipeline.from_pretrained must receive the local path, not the Hub ID."""
        engine = DiarizationEngine(model_id="pyannote-diarization-community-1")

        fake_mods, mock_pipeline_cls = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm_cls.return_value = self._mock_mm(
                requires_token=False,
                local_path="/data/models/pyannote-diarization-community-1",
            )
            engine.load()

        path_arg = mock_pipeline_cls.from_pretrained.call_args[0][0]
        assert "pyannote/speaker-diarization" not in path_arg, (
            "from_pretrained should receive a local path, not a Hub repo ID"
        )
        assert path_arg == "/data/models/pyannote-diarization-community-1"

    def test_pipeline_not_loaded_twice(self):
        """Calling load() twice should not call from_pretrained again."""
        engine = DiarizationEngine()

        fake_mods, mock_pipeline_cls = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm_cls.return_value = self._mock_mm(
                requires_token=False,
                local_path="/data/models/community-1",
            )
            engine.load()
            engine.load()  # second call — pipeline already set

        assert mock_pipeline_cls.from_pretrained.call_count == 1


# ── unknown model_id ──────────────────────────────────────────────────────────

class TestUnknownModel:
    def test_unknown_model_id_raises_on_load(self):
        """load() must raise ValueError for a model_id not in CATALOG."""
        engine = DiarizationEngine(model_id="totally-unknown-model")

        fake_mods, _ = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm = MagicMock()
            mock_mm.get_model.return_value = None  # not in catalog
            mock_mm_cls.return_value = mock_mm

            with pytest.raises(ValueError, match="Unknown diarizer model"):
                engine.load()


# ── auto-download ─────────────────────────────────────────────────────────────

class TestAutoDownload:
    """load() should trigger mm.download() when model is not yet present."""

    def _mock_mm(self, is_downloaded: bool, local_path: str = "/fake/path"):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock(requires_token=False)
        mock_mm.is_downloaded.return_value = is_downloaded
        mock_mm.local_path.return_value = local_path
        return mock_mm

    def test_download_called_when_not_downloaded(self):
        engine = DiarizationEngine(model_id="pyannote-diarization-community-1")
        fake_mods, _ = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm = self._mock_mm(is_downloaded=False)
            mock_mm_cls.return_value = mock_mm
            engine.load()
        mock_mm.download.assert_called_once_with(
            "pyannote-diarization-community-1",
            progress_callback=None,
        )

    def test_download_not_called_when_already_downloaded(self):
        engine = DiarizationEngine(model_id="pyannote-diarization-community-1")
        fake_mods, _ = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm = self._mock_mm(is_downloaded=True)
            mock_mm_cls.return_value = mock_mm
            engine.load()
        mock_mm.download.assert_not_called()

    def test_progress_callback_forwarded_to_download(self):
        cb = MagicMock()
        engine = DiarizationEngine(
            model_id="pyannote-diarization-community-1",
            progress_callback=cb,
        )
        fake_mods, _ = _fake_modules()
        with patch("src.diarize.ModelManager") as mock_mm_cls, \
             patch.dict(sys.modules, fake_mods):
            mock_mm = self._mock_mm(is_downloaded=False)
            mock_mm_cls.return_value = mock_mm
            engine.load()
        mock_mm.download.assert_called_once_with(
            "pyannote-diarization-community-1",
            progress_callback=cb,
        )


# ── diarize() waveform-dict input ─────────────────────────────────────────────

class TestDiarizeWaveformInput:
    """diarize() must load audio via torchaudio and pass a waveform dict to the
    pipeline instead of a raw file path, so torchcodec is never invoked."""

    def _make_fake_mods_with_torchaudio(self):
        """Extend _fake_modules() with a torchaudio mock."""
        fake_mods, mock_pipeline_cls = _fake_modules()
        mock_tensor = MagicMock(name="waveform_tensor")
        torchaudio_mod = MagicMock(name="torchaudio")
        torchaudio_mod.load.return_value = (mock_tensor, 16000)
        fake_mods["torchaudio"] = torchaudio_mod
        return fake_mods, mock_pipeline_cls, mock_tensor, torchaudio_mod

    def _loaded_engine(self, pipeline_instance, num_speakers=None):
        """Return an engine with _pipeline already set (bypasses load())."""
        engine = DiarizationEngine(num_speakers=num_speakers)
        engine._pipeline = pipeline_instance
        return engine

    def _mock_annotation(self, tracks=()):
        """Build a minimal pyannote Annotation-like mock.

        spec=["itertracks"] prevents MagicMock from auto-creating a
        speaker_diarization attribute, so the hasattr() check in diarize()
        falls through to the else-branch and uses the annotation directly.
        """
        ann = MagicMock(spec=["itertracks"])
        ann.itertracks.return_value = list(tracks)
        return ann

    def test_passes_waveform_dict_to_pipeline(self):
        """Pipeline must receive {"waveform": tensor, "sample_rate": int}."""
        fake_mods, mock_pipeline_cls, mock_tensor, torchaudio_mod = \
            self._make_fake_mods_with_torchaudio()

        pipeline_instance = mock_pipeline_cls.from_pretrained.return_value
        pipeline_instance.return_value = self._mock_annotation()

        engine = self._loaded_engine(pipeline_instance)
        with patch.dict(sys.modules, fake_mods):
            engine.diarize("/audio/test.wav")

        # torchaudio.load called with the original path
        torchaudio_mod.load.assert_called_once_with("/audio/test.wav")

        # Pipeline called with waveform dict, not the raw string path
        audio_input = pipeline_instance.call_args[0][0]
        assert isinstance(audio_input, dict), "pipeline must receive a dict"
        assert "waveform" in audio_input
        assert "sample_rate" in audio_input
        assert audio_input["waveform"] is mock_tensor
        assert audio_input["sample_rate"] == 16000

    def test_does_not_pass_raw_path_to_pipeline(self):
        """The string audio path must NOT be passed directly to the pipeline."""
        fake_mods, mock_pipeline_cls, _, _ = self._make_fake_mods_with_torchaudio()
        pipeline_instance = mock_pipeline_cls.from_pretrained.return_value
        pipeline_instance.return_value = self._mock_annotation()

        engine = self._loaded_engine(pipeline_instance)
        with patch.dict(sys.modules, fake_mods):
            engine.diarize("/audio/test.wav")

        first_arg = pipeline_instance.call_args[0][0]
        assert first_arg != "/audio/test.wav", \
            "raw file path must not be passed to pipeline (torchcodec bypass)"

    def test_returns_speaker_segments(self):
        """diarize() must parse itertracks and return correct SpeakerSegment list."""
        from src.diarize import SpeakerSegment
        fake_mods, mock_pipeline_cls, _, _ = self._make_fake_mods_with_torchaudio()
        pipeline_instance = mock_pipeline_cls.from_pretrained.return_value

        turn1 = MagicMock(); turn1.start = 0.0; turn1.end = 2.5
        turn2 = MagicMock(); turn2.start = 3.0; turn2.end = 5.0
        pipeline_instance.return_value = self._mock_annotation([
            (turn1, None, "SPEAKER_00"),
            (turn2, None, "SPEAKER_01"),
        ])

        engine = self._loaded_engine(pipeline_instance)
        with patch.dict(sys.modules, fake_mods):
            segments = engine.diarize("/audio/test.wav")

        assert len(segments) == 2
        assert segments[0] == SpeakerSegment("SPEAKER_00", 0.0, 2.5)
        assert segments[1] == SpeakerSegment("SPEAKER_01", 3.0, 5.0)

    def test_num_speakers_forwarded_to_pipeline(self):
        """num_speakers kwarg must still be forwarded to pipeline call."""
        fake_mods, mock_pipeline_cls, _, _ = self._make_fake_mods_with_torchaudio()
        pipeline_instance = mock_pipeline_cls.from_pretrained.return_value
        pipeline_instance.return_value = self._mock_annotation()

        engine = self._loaded_engine(pipeline_instance, num_speakers=2)
        with patch.dict(sys.modules, fake_mods):
            engine.diarize("/audio/test.wav")

        call_kwargs = pipeline_instance.call_args[1]
        assert call_kwargs.get("num_speakers") == 2
