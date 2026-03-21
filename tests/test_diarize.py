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
    def _mock_mm(self, requires_token: bool, local_path: str):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock(requires_token=requires_token)
        mock_mm.local_path.return_value = local_path
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
    def _mock_mm(self, requires_token: bool, local_path: str):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock(requires_token=requires_token)
        mock_mm.local_path.return_value = local_path
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
