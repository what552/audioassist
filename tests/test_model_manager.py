"""Tests for src/model_manager.py — list_models, download, select, config I/O."""
import json
import os
from unittest.mock import MagicMock, patch, call
import pytest

# Override APP_DATA_DIR before importing ModelManager so tests never touch
# the real ~/.local/share/TranscribeApp directory.
import src.model_manager as _mm_module


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Redirect all data-dir paths to a tmp directory for each test."""
    data_dir = str(tmp_path / "TranscribeApp")
    models_dir = os.path.join(data_dir, "models")
    config_path = os.path.join(data_dir, "config.json")
    os.makedirs(models_dir, exist_ok=True)
    monkeypatch.setattr(_mm_module, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(_mm_module, "DEFAULT_MODELS_DIR", models_dir)
    monkeypatch.setattr(_mm_module, "CONFIG_PATH", config_path)
    return {"data_dir": data_dir, "models_dir": models_dir, "config_path": config_path}


def _make_manager(isolated_data_dir):
    from src.model_manager import ModelManager
    return ModelManager(models_dir=isolated_data_dir["models_dir"])


# ── list_models ───────────────────────────────────────────────────────────────

class TestListModels:
    def test_returns_all_catalog_entries(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        result = mm.list_models()
        from src.model_manager import CATALOG
        assert len(result) == len(CATALOG)

    def test_each_entry_has_required_fields(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        for entry in mm.list_models():
            for field in ("id", "name", "description", "size_gb", "engine",
                          "role", "languages", "recommended", "requires_token",
                          "downloaded", "local_path"):
                assert field in entry, f"Missing field: {field}"

    def test_not_downloaded_when_dir_empty(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        for entry in mm.list_models():
            assert entry["downloaded"] is False

    def test_downloaded_when_dir_has_files(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        # Simulate a downloaded model by creating its directory with a file
        model_id = "qwen3-asr-1.7b"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()

        entries = {e["id"]: e for e in mm.list_models()}
        assert entries[model_id]["downloaded"] is True
        assert entries["qwen3-forced-aligner"]["downloaded"] is False


# ── download ──────────────────────────────────────────────────────────────────

class TestDownload:
    def test_calls_snapshot_download(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        with patch("huggingface_hub.snapshot_download") as mock_snap:
            mm.download("qwen3-asr-1.7b")
        mock_snap.assert_called_once()
        kwargs = mock_snap.call_args
        assert kwargs[1]["repo_id"] == "Qwen/Qwen3-ASR-1.7B"

    def test_progress_callback_receives_zero_then_one(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        calls = []
        with patch("huggingface_hub.snapshot_download"):
            mm.download("qwen3-asr-1.7b", progress_callback=lambda p, m: calls.append((p, m)))

        pcts = [p for p, _ in calls]
        assert pcts[0] == pytest.approx(0.0)
        assert pcts[-1] == pytest.approx(1.0)

    def test_progress_callback_messages_are_strings(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        calls = []
        with patch("huggingface_hub.snapshot_download"):
            mm.download("qwen3-asr-1.7b", progress_callback=lambda p, m: calls.append((p, m)))

        for _, msg in calls:
            assert isinstance(msg, str)

    def test_already_downloaded_skips_snapshot(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        # Pre-create the model directory
        model_dir = os.path.join(isolated_data_dir["models_dir"], "qwen3-asr-1.7b")
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()

        with patch("huggingface_hub.snapshot_download") as mock_snap:
            result = mm.download("qwen3-asr-1.7b")

        mock_snap.assert_not_called()
        assert result == model_dir

    def test_already_downloaded_calls_callback_with_one(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        model_dir = os.path.join(isolated_data_dir["models_dir"], "qwen3-asr-1.7b")
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()

        calls = []
        with patch("huggingface_hub.snapshot_download"):
            mm.download("qwen3-asr-1.7b", progress_callback=lambda p, m: calls.append(p))

        assert calls == [pytest.approx(1.0)]

    def test_unknown_model_raises(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        with pytest.raises(ValueError, match="Unknown model"):
            mm.download("nonexistent-model")


# ── select / get_selected ─────────────────────────────────────────────────────

class TestSelectAndGetSelected:
    def _install_model(self, isolated_data_dir, model_id: str):
        """Simulate a downloaded model."""
        d = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "config.json"), "w").close()

    def test_select_asr_model_saves_to_config(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "qwen3-asr-1.7b")
        mm.select_asr_model("qwen3-asr-1.7b")

        with open(isolated_data_dir["config_path"]) as f:
            cfg = json.load(f)
        assert cfg["asr_model"] == "qwen3-asr-1.7b"

    def test_select_not_downloaded_raises(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        with pytest.raises(RuntimeError, match="not downloaded"):
            mm.select_asr_model("qwen3-asr-1.7b")

    def test_select_wrong_role_raises(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "qwen3-forced-aligner")
        with pytest.raises(ValueError, match="Not an ASR model"):
            mm.select_asr_model("qwen3-forced-aligner")

    def test_get_selected_asr_returns_config_value(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "qwen3-asr-1.7b")
        mm.select_asr_model("qwen3-asr-1.7b")
        assert mm.get_selected_asr() == "qwen3-asr-1.7b"

    def test_get_selected_asr_auto_selects_recommended(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        # Install only a recommended model without calling select
        self._install_model(isolated_data_dir, "qwen3-asr-1.7b")
        result = mm.get_selected_asr()
        assert result == "qwen3-asr-1.7b"

    def test_get_selected_asr_returns_none_when_none_downloaded(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        assert mm.get_selected_asr() is None

    def test_get_selected_aligner_returns_downloaded(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "qwen3-forced-aligner")
        assert mm.get_selected_aligner() == "qwen3-forced-aligner"


# ── delete ────────────────────────────────────────────────────────────────────

class TestDelete:
    def _install_model(self, isolated_data_dir, model_id: str):
        d = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "config.json"), "w").close()
        return d

    def test_delete_removes_directory(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        d = self._install_model(isolated_data_dir, "qwen3-asr-1.7b")
        mm.delete("qwen3-asr-1.7b")
        assert not os.path.exists(d)

    def test_delete_clears_config_entry(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "qwen3-asr-1.7b")
        mm.select_asr_model("qwen3-asr-1.7b")
        mm.delete("qwen3-asr-1.7b")

        with open(isolated_data_dir["config_path"]) as f:
            cfg = json.load(f)
        assert "asr_model" not in cfg

    def test_delete_nonexistent_is_noop(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        mm.delete("nonexistent-model")  # should not raise


# ── diarizer catalog ──────────────────────────────────────────────────────────

class TestDiarizerCatalog:
    def test_community_1_in_catalog(self, isolated_data_dir):
        from src.model_manager import CATALOG
        ids = [m.id for m in CATALOG]
        assert "pyannote-diarization-community-1" in ids

    def test_diarization_3_1_in_catalog(self, isolated_data_dir):
        from src.model_manager import CATALOG
        ids = [m.id for m in CATALOG]
        assert "pyannote-diarization-3.1" in ids

    def test_community_1_requires_no_token(self, isolated_data_dir):
        from src.model_manager import CATALOG
        m = next(m for m in CATALOG if m.id == "pyannote-diarization-community-1")
        assert m.requires_token is False

    def test_diarization_3_1_requires_token(self, isolated_data_dir):
        from src.model_manager import CATALOG
        m = next(m for m in CATALOG if m.id == "pyannote-diarization-3.1")
        assert m.requires_token is True

    def test_community_1_is_recommended(self, isolated_data_dir):
        from src.model_manager import CATALOG
        m = next(m for m in CATALOG if m.id == "pyannote-diarization-community-1")
        assert m.recommended is True

    def test_diarizer_role(self, isolated_data_dir):
        from src.model_manager import CATALOG
        for m in CATALOG:
            if m.id.startswith("pyannote-diarization"):
                assert m.role == "diarizer"


# ── select_diarizer / get_selected_diarizer ───────────────────────────────────

class TestSelectAndGetSelectedDiarizer:
    def _install_model(self, isolated_data_dir, model_id: str):
        d = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "config.json"), "w").close()

    def test_select_diarizer_saves_to_config(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "pyannote-diarization-community-1")
        mm.select_diarizer_model("pyannote-diarization-community-1")

        with open(isolated_data_dir["config_path"]) as f:
            cfg = json.load(f)
        assert cfg["diarizer_model"] == "pyannote-diarization-community-1"

    def test_select_wrong_role_raises(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "qwen3-asr-1.7b")
        with pytest.raises(ValueError, match="Not a diarizer model"):
            mm.select_diarizer_model("qwen3-asr-1.7b")

    def test_select_not_downloaded_raises(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        with pytest.raises(RuntimeError, match="not downloaded"):
            mm.select_diarizer_model("pyannote-diarization-community-1")

    def test_get_selected_diarizer_returns_config_value(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "pyannote-diarization-community-1")
        mm.select_diarizer_model("pyannote-diarization-community-1")
        assert mm.get_selected_diarizer() == "pyannote-diarization-community-1"

    def test_get_selected_diarizer_auto_selects_recommended(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "pyannote-diarization-community-1")
        assert mm.get_selected_diarizer() == "pyannote-diarization-community-1"

    def test_get_selected_diarizer_returns_none_when_none_downloaded(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        assert mm.get_selected_diarizer() is None

    def test_delete_clears_diarizer_config(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._install_model(isolated_data_dir, "pyannote-diarization-community-1")
        mm.select_diarizer_model("pyannote-diarization-community-1")
        mm.delete("pyannote-diarization-community-1")

        with open(isolated_data_dir["config_path"]) as f:
            cfg = json.load(f)
        assert "diarizer_model" not in cfg


# ── config I/O ────────────────────────────────────────────────────────────────

class TestConfigIO:
    def test_load_config_returns_empty_when_no_file(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        assert mm._load_config() == {}

    def test_save_and_load_config_roundtrip(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        mm._save_config({"key": "value", "num": 42})
        assert mm._load_config() == {"key": "value", "num": 42}

    def test_config_file_is_valid_json(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        mm._save_config({"a": 1})
        with open(isolated_data_dir["config_path"]) as f:
            data = json.load(f)
        assert data == {"a": 1}
