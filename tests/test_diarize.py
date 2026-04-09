"""Tests for src/diarize.py — DiarizationEngine local-path loading."""
import sys
import types
import numpy as np
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
            hf_token="test-token",
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
        monkeypatch.setenv("HF_TOKEN", "from-env")
        engine = DiarizationEngine(model_id="pyannote-diarization-3.1")
        assert engine.hf_token == "from-env"


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


class TestInMemoryAudioLoading:
    def test_prepare_audio_input_returns_pyannote_mapping(self, tmp_path):
        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"fake")

        fake_tensor = object()
        fake_torch = MagicMock()
        fake_torch.from_numpy.return_value = fake_tensor
        waveform = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)

        with patch("src.diarize.sf.read", return_value=(waveform, 16000)) as mock_read, \
             patch.dict(sys.modules, {"torch": fake_torch}):
            engine = DiarizationEngine()
            result = engine._prepare_audio_input(str(audio_path))

        mock_read.assert_called_once_with(
            str(audio_path),
            always_2d=True,
            dtype="float32",
        )
        fake_torch.from_numpy.assert_called_once()
        tensor_arg = fake_torch.from_numpy.call_args[0][0]
        assert tensor_arg.shape == (1, 3)
        assert result == {
            "waveform": fake_tensor,
            "sample_rate": 16000,
            "uri": "audio.wav",
        }

    def test_diarize_uses_in_memory_audio_mapping(self, tmp_path):
        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"fake")

        class FakeAnnotation:
            def itertracks(self, yield_label=True):
                return [
                    (types.SimpleNamespace(start=0.0, end=1.5), None, "SPEAKER_00")
                ]

        annotation = FakeAnnotation()
        pipeline = MagicMock(return_value=annotation)

        fake_torch = MagicMock()
        fake_torch.from_numpy.return_value = MagicMock()
        waveform = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)

        engine = DiarizationEngine()
        engine._pipeline = pipeline

        with patch("src.diarize.sf.read", return_value=(waveform, 16000)), \
             patch.dict(sys.modules, {"torch": fake_torch}):
            segments = engine.diarize(str(audio_path))

        pipeline_arg = pipeline.call_args[0][0]
        assert set(pipeline_arg.keys()) == {"waveform", "sample_rate", "uri"}
        assert pipeline_arg["sample_rate"] == 16000
        assert pipeline_arg["uri"] == "audio.wav"
        assert segments[0].speaker == "SPEAKER_00"
        assert segments[0].start == 0.0
        assert segments[0].end == 1.5

    def test_prepare_audio_input_rejects_empty_audio(self, tmp_path):
        audio_path = tmp_path / "empty.wav"
        audio_path.write_bytes(b"fake")

        with patch(
            "src.diarize.sf.read",
            return_value=(np.empty((0, 1), dtype=np.float32), 16000),
        ), pytest.raises(ValueError, match="Audio file is empty"):
            DiarizationEngine()._prepare_audio_input(str(audio_path))


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
