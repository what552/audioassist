"""Tests for src/model_manager.py — list_models, download, select, config I/O."""
import json
import os
from unittest.mock import MagicMock, patch, call
import pytest

# Override APP_DATA_DIR before importing ModelManager so tests never touch
# the real ~/.local/share/TranscribeApp directory.
import src.model_manager as _mm_module
from src.model_manager import _DIARIZER_REQUIRED_FILES


def _create_diarizer_files(directory: str) -> None:
    """Create all required files for a fully-populated diarizer model directory."""
    for rel_path in _DIARIZER_REQUIRED_FILES:
        full_path = os.path.join(directory, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        open(full_path, "w").close()

# Capture the real _hf_cache_path before any patching so TestHFCacheDetection
# can restore it after the autouse fixture stubs it out.
_REAL_HF_CACHE_PATH = _mm_module.ModelManager._hf_cache_path


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """
    Redirect all data-dir paths to a tmp directory for each test,
    and stub out _hf_cache_path so the developer's real HF cache is
    invisible unless a test explicitly overrides _hf_cache_path.
    """
    data_dir = str(tmp_path / "TranscribeApp")
    models_dir = os.path.join(data_dir, "models")
    config_path = os.path.join(data_dir, "config.json")
    os.makedirs(models_dir, exist_ok=True)
    monkeypatch.setattr(_mm_module, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(_mm_module, "DEFAULT_MODELS_DIR", models_dir)
    monkeypatch.setattr(_mm_module, "CONFIG_PATH", config_path)
    # Isolate from real HF cache on the developer's machine
    monkeypatch.setattr(
        _mm_module.ModelManager, "_hf_cache_path", lambda self, model_id: None
    )
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
                          "downloaded", "incomplete", "local_path"):
                assert field in entry, f"Missing field: {field}"

    def test_not_downloaded_when_dir_empty(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        for entry in mm.list_models():
            assert entry["downloaded"] is False

    def test_downloaded_when_dir_has_key_file(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        model_id = "qwen3-asr-1.7b"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()

        entries = {e["id"]: e for e in mm.list_models()}
        assert entries[model_id]["downloaded"] is True
        assert entries["qwen3-forced-aligner"]["downloaded"] is False

    def test_not_downloaded_when_dir_has_no_key_file(self, isolated_data_dir):
        """Directory exists but missing config — treated as incomplete download."""
        mm = _make_manager(isolated_data_dir)
        model_id = "qwen3-asr-1.7b"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "some_weights.bin"), "w").close()

        assert mm.is_downloaded(model_id) is False

    def test_not_downloaded_when_diarizer_has_config_json_not_yaml(self, isolated_data_dir):
        """Diarizer model needs config.yaml; config.json alone is insufficient."""
        mm = _make_manager(isolated_data_dir)
        model_id = "pyannote-diarization-community-1"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()  # wrong file

        assert mm.is_downloaded(model_id) is False

    def test_not_downloaded_when_diarizer_has_config_yaml_only(self, isolated_data_dir):
        """config.yaml alone is no longer sufficient — all 5 files required."""
        mm = _make_manager(isolated_data_dir)
        model_id = "pyannote-diarization-community-1"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.yaml"), "w").close()

        assert mm.is_downloaded(model_id) is False

    def test_downloaded_when_diarizer_has_all_required_files(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        model_id = "pyannote-diarization-community-1"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        _create_diarizer_files(model_dir)

        assert mm.is_downloaded(model_id) is True


# ── _has_key_files ─────────────────────────────────────────────────────────────

class TestHasKeyFiles:
    def _dir(self, isolated_data_dir, model_id, files):
        d = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(d, exist_ok=True)
        for f in files:
            full = os.path.join(d, f)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
        return d

    def test_asr_config_json_ok(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        d = self._dir(isolated_data_dir, "qwen3-asr-1.7b", ["config.json"])
        assert mm._has_key_files("qwen3-asr-1.7b", d) is True

    def test_asr_config_yaml_ok(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        d = self._dir(isolated_data_dir, "qwen3-asr-1.7b", ["config.yaml"])
        assert mm._has_key_files("qwen3-asr-1.7b", d) is True

    def test_asr_no_config_returns_false(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        d = self._dir(isolated_data_dir, "qwen3-asr-1.7b", ["weights.bin"])
        assert mm._has_key_files("qwen3-asr-1.7b", d) is False

    def test_diarizer_all_files_ok(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        d = os.path.join(isolated_data_dir["models_dir"], "pyannote-diarization-community-1")
        os.makedirs(d)
        _create_diarizer_files(d)
        assert mm._has_key_files("pyannote-diarization-community-1", d) is True

    def test_diarizer_config_yaml_only_not_ok(self, isolated_data_dir):
        """All 5 files required; config.yaml alone is insufficient."""
        mm = _make_manager(isolated_data_dir)
        d = self._dir(isolated_data_dir, "pyannote-diarization-community-1", ["config.yaml"])
        assert mm._has_key_files("pyannote-diarization-community-1", d) is False

    def test_diarizer_config_json_not_ok(self, isolated_data_dir):
        """Diarizer requires config.yaml (+ 4 others), not config.json."""
        mm = _make_manager(isolated_data_dir)
        d = self._dir(isolated_data_dir, "pyannote-diarization-community-1", ["config.json"])
        assert mm._has_key_files("pyannote-diarization-community-1", d) is False

    def test_diarizer_missing_one_file_not_ok(self, isolated_data_dir):
        """All 5 files must be present; missing any one returns False."""
        mm = _make_manager(isolated_data_dir)
        d = os.path.join(isolated_data_dir["models_dir"], "pyannote-diarization-community-1")
        os.makedirs(d)
        _create_diarizer_files(d)
        # Remove one required file
        os.remove(os.path.join(d, "config.yaml"))
        assert mm._has_key_files("pyannote-diarization-community-1", d) is False

    def test_unknown_model_returns_false(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        d = self._dir(isolated_data_dir, "qwen3-asr-1.7b", ["config.json"])
        assert mm._has_key_files("no-such-model", d) is False


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

    def test_community_1_download_uses_no_token(self, isolated_data_dir):
        """snapshot_download must be called with token=False for community-1 (no auth)."""
        mm = _make_manager(isolated_data_dir)
        with patch("huggingface_hub.snapshot_download") as mock_snap:
            mm.download("pyannote-diarization-community-1")
        call_kwargs = mock_snap.call_args[1]
        assert call_kwargs.get("token") is False, (
            "Community-1 download should use token=False (anonymous) "
            "to prevent a stale HF credential from blocking the download"
        )

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

    def test_community_1_uses_community_repo(self, isolated_data_dir):
        from src.model_manager import CATALOG
        m = next(m for m in CATALOG if m.id == "pyannote-diarization-community-1")
        assert m.repo_id == "pyannote-community/speaker-diarization-community-1"

    def test_community_1_size_is_small(self, isolated_data_dir):
        from src.model_manager import CATALOG
        m = next(m for m in CATALOG if m.id == "pyannote-diarization-community-1")
        assert m.size_gb == pytest.approx(0.034)

    def test_diarizer_role(self, isolated_data_dir):
        from src.model_manager import CATALOG
        for m in CATALOG:
            if m.id.startswith("pyannote-diarization"):
                assert m.role == "diarizer"


# ── select_diarizer / get_selected_diarizer ───────────────────────────────────

class TestSelectAndGetSelectedDiarizer:
    def _install_model(self, isolated_data_dir, model_id: str):
        from src.model_manager import CATALOG
        d = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(d, exist_ok=True)
        info = next((m for m in CATALOG if m.id == model_id), None)
        if info and info.role == "diarizer":
            _create_diarizer_files(d)
        else:
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


# ── HF cache detection ────────────────────────────────────────────────────────

_COMMUNITY1_REPO = "pyannote-community/speaker-diarization-community-1"


def _build_hf_cache(
    tmp_path,
    repo_id: str,
    snapshot_hash: str = "abc123",
    key_files: tuple = ("config.yaml",),
) -> str:
    """
    Create a minimal HF hub cache structure and return the snapshot path.

    Layout:
      tmp_path/
        models--{org}--{name}/
          refs/main                  ← contains snapshot_hash
          snapshots/{hash}/
            {key_files[0]}, ...      ← marker files
    """
    cache_name = "models--" + repo_id.replace("/", "--")
    repo_cache = tmp_path / cache_name
    refs_dir = repo_cache / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "main").write_text(snapshot_hash)

    snapshot_dir = repo_cache / "snapshots" / snapshot_hash
    snapshot_dir.mkdir(parents=True)
    for rel in key_files:
        target = snapshot_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
    return str(snapshot_dir)


class TestHFCacheDetection:
    """
    Tests for _hf_cache_path / is_downloaded / local_path HF cache fallback.
    These tests bypass the autouse _hf_cache_path stub by calling the real
    implementation via monkeypatch on HF_HUB_CACHE.
    """

    def _real_manager(self, isolated_data_dir, monkeypatch, hf_cache_dir: str):
        """Return a ModelManager whose _hf_cache_path reads from hf_cache_dir."""
        import huggingface_hub.constants as _hf_const
        monkeypatch.setattr(_hf_const, "HF_HUB_CACHE", hf_cache_dir)
        # Restore real implementation (autouse fixture replaced it with a stub)
        monkeypatch.setattr(
            _mm_module.ModelManager,
            "_hf_cache_path",
            _REAL_HF_CACHE_PATH,
        )
        from src.model_manager import ModelManager
        return ModelManager(models_dir=isolated_data_dir["models_dir"])

    def test_hf_cache_path_returns_snapshot_when_present(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "hf-cache")
        snap = _build_hf_cache(
            tmp_path / "hf-cache",
            _COMMUNITY1_REPO,
            key_files=_DIARIZER_REQUIRED_FILES,
        )
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        result = mm._hf_cache_path("pyannote-diarization-community-1")
        assert result == snap

    def test_hf_cache_path_returns_none_when_no_cache(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "empty-hf-cache")
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        assert mm._hf_cache_path("pyannote-diarization-community-1") is None

    def test_hf_cache_path_returns_none_for_unknown_model(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "hf-cache")
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        assert mm._hf_cache_path("no-such-model") is None

    def test_is_downloaded_true_when_only_in_hf_cache(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "hf-cache")
        _build_hf_cache(
            tmp_path / "hf-cache",
            _COMMUNITY1_REPO,
            key_files=_DIARIZER_REQUIRED_FILES,
        )
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        assert mm.is_downloaded("pyannote-diarization-community-1") is True

    def test_local_path_returns_hf_cache_when_app_dir_empty(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "hf-cache")
        snap = _build_hf_cache(
            tmp_path / "hf-cache",
            _COMMUNITY1_REPO,
            key_files=_DIARIZER_REQUIRED_FILES,
        )
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        assert mm.local_path("pyannote-diarization-community-1") == snap

    def test_local_path_prefers_app_dir_over_hf_cache(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "hf-cache")
        _build_hf_cache(
            tmp_path / "hf-cache",
            _COMMUNITY1_REPO,
            key_files=_DIARIZER_REQUIRED_FILES,
        )
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        # Populate the App dir with all required diarizer files
        app_dir = os.path.join(isolated_data_dir["models_dir"], "pyannote-diarization-community-1")
        os.makedirs(app_dir)
        _create_diarizer_files(app_dir)

        result = mm.local_path("pyannote-diarization-community-1")
        assert result == app_dir

    def test_download_skipped_when_in_hf_cache(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        hf_root = str(tmp_path / "hf-cache")
        snap = _build_hf_cache(
            tmp_path / "hf-cache",
            _COMMUNITY1_REPO,
            key_files=_DIARIZER_REQUIRED_FILES,
        )
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)

        with patch("huggingface_hub.snapshot_download") as mock_snap:
            result = mm.download("pyannote-diarization-community-1")

        mock_snap.assert_not_called()
        assert result == snap

    def test_local_path_falls_through_when_hf_cache_incomplete(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        """HF cache dir exists but missing key files → fall through to app dir default."""
        hf_root = str(tmp_path / "hf-cache")
        # Only config.yaml, missing the other 4 required files
        _build_hf_cache(
            tmp_path / "hf-cache",
            _COMMUNITY1_REPO,
            key_files=("config.yaml",),
        )
        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        result = mm.local_path("pyannote-diarization-community-1")
        app_dir = os.path.join(
            isolated_data_dir["models_dir"], "pyannote-diarization-community-1"
        )
        assert result == app_dir  # fell through to app dir default

    def test_hf_cache_path_returns_none_when_refs_main_is_empty(
        self, tmp_path, isolated_data_dir, monkeypatch
    ):
        """refs/main exists but is empty — should return None, not crash."""
        hf_root = str(tmp_path / "hf-cache")
        cache_name = "models--" + _COMMUNITY1_REPO.replace("/", "--")
        refs_dir = tmp_path / "hf-cache" / cache_name / "refs"
        refs_dir.mkdir(parents=True)
        (refs_dir / "main").write_text("")   # empty hash

        mm = self._real_manager(isolated_data_dir, monkeypatch, hf_root)
        assert mm._hf_cache_path("pyannote-diarization-community-1") is None

# ── incomplete file detection (P5) ────────────────────────────────────────────

class TestIncompleteFiles:
    """Tests for _has_incomplete_files and is_downloaded's .incomplete guard."""

    def _make_incomplete(self, isolated_data_dir, model_id: str) -> str:
        """Create a .incomplete file in the model's HF download cache dir."""
        dl_cache = os.path.join(
            isolated_data_dir["models_dir"], model_id,
            ".cache", "huggingface", "download",
        )
        os.makedirs(dl_cache, exist_ok=True)
        inc_path = os.path.join(dl_cache, "some_blob.incomplete")
        open(inc_path, "w").close()
        return inc_path

    def test_no_incomplete_returns_false(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        assert mm._has_incomplete_files("qwen3-asr-1.7b") is False

    def test_incomplete_file_detected(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._make_incomplete(isolated_data_dir, "qwen3-asr-1.7b")
        assert mm._has_incomplete_files("qwen3-asr-1.7b") is True

    def test_is_downloaded_false_when_incomplete(self, isolated_data_dir):
        """is_downloaded() returns False when .incomplete files are present,
        even if config.json exists (partial download scenario)."""
        mm = _make_manager(isolated_data_dir)
        model_id = "qwen3-asr-1.7b"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()
        self._make_incomplete(isolated_data_dir, model_id)
        assert mm.is_downloaded(model_id) is False

    def test_list_models_incomplete_field_true(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        self._make_incomplete(isolated_data_dir, "qwen3-asr-1.7b")
        entries = {e["id"]: e for e in mm.list_models()}
        assert entries["qwen3-asr-1.7b"]["incomplete"] is True
        assert entries["qwen3-asr-1.7b"]["downloaded"] is False

    def test_list_models_incomplete_false_when_clean(self, isolated_data_dir):
        mm = _make_manager(isolated_data_dir)
        model_id = "qwen3-asr-1.7b"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        open(os.path.join(model_dir, "config.json"), "w").close()
        entries = {e["id"]: e for e in mm.list_models()}
        assert entries[model_id]["incomplete"] is False
        assert entries[model_id]["downloaded"] is True

    def test_no_dl_cache_dir_returns_false(self, isolated_data_dir):
        """If the .cache/huggingface/download dir doesn't exist, no incomplete."""
        mm = _make_manager(isolated_data_dir)
        model_id = "qwen3-asr-1.7b"
        model_dir = os.path.join(isolated_data_dir["models_dir"], model_id)
        os.makedirs(model_dir)
        assert mm._has_incomplete_files(model_id) is False
