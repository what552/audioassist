"""Tests for app.API storage config: get_storage_config, set_output_dir, select_output_folder."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

import app as app_module
from app import API, _DEFAULT_OUTPUT_DIR


@pytest.fixture
def api(tmp_path):
    config_path = str(tmp_path / "config.json")
    with (
        patch.object(app_module, "APP_DATA_DIR", str(tmp_path)),
        patch.object(app_module, "CONFIG_PATH", config_path),
        patch.object(app_module, "OUTPUT_DIR", str(tmp_path / "output")),
    ):
        (tmp_path / "output").mkdir()
        yield API(), tmp_path


# ── get_storage_config ────────────────────────────────────────────────────────

def test_get_storage_config_returns_output_dir(api):
    instance, tmp_path = api
    result = instance.get_storage_config()
    assert "output_dir" in result
    assert "default_output_dir" in result


def test_get_storage_config_reflects_current_OUTPUT_DIR(api):
    instance, tmp_path = api
    result = instance.get_storage_config()
    assert result["output_dir"] == str(tmp_path / "output")


# ── set_output_dir ────────────────────────────────────────────────────────────

def test_set_output_dir_accepts_existing_dir(api):
    instance, tmp_path = api
    new_dir = str(tmp_path / "new_output")
    os.makedirs(new_dir)
    result = instance.set_output_dir(new_dir)
    assert result["ok"] is True
    assert result["path"] == new_dir


def test_set_output_dir_updates_global(api):
    instance, tmp_path = api
    new_dir = str(tmp_path / "new_output")
    os.makedirs(new_dir)
    instance.set_output_dir(new_dir)
    assert app_module.OUTPUT_DIR == new_dir


def test_set_output_dir_persists_to_config(api):
    instance, tmp_path = api
    new_dir = str(tmp_path / "new_output")
    os.makedirs(new_dir)
    instance.set_output_dir(new_dir)
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["storage"]["output_dir"] == new_dir


def test_set_output_dir_rejects_nonexistent_path(api):
    instance, tmp_path = api
    result = instance.set_output_dir(str(tmp_path / "does_not_exist"))
    assert result["ok"] is False
    assert "error" in result


def test_set_output_dir_rejects_empty_string(api):
    instance, _ = api
    result = instance.set_output_dir("")
    assert result["ok"] is False


def test_set_output_dir_strips_whitespace(api):
    instance, tmp_path = api
    new_dir = str(tmp_path / "new_output")
    os.makedirs(new_dir)
    result = instance.set_output_dir(f"  {new_dir}  ")
    assert result["ok"] is True
    assert result["path"] == new_dir


# ── select_output_folder ──────────────────────────────────────────────────────

def test_select_output_folder_no_window(api):
    instance, _ = api
    with patch.object(app_module, "_window", None):
        result = instance.select_output_folder()
    assert result is None


def test_select_output_folder_returns_chosen_path(api):
    instance, tmp_path = api
    mock_win = MagicMock()
    mock_win.create_file_dialog.return_value = [str(tmp_path / "chosen")]
    with (
        patch.object(app_module, "_window", mock_win),
        patch.dict("sys.modules", {"webview": MagicMock(FileDialog=MagicMock(FOLDER=3))}),
    ):
        result = instance.select_output_folder()
    assert result == str(tmp_path / "chosen")


def test_select_output_folder_cancelled_returns_none(api):
    instance, _ = api
    mock_win = MagicMock()
    mock_win.create_file_dialog.return_value = None
    with (
        patch.object(app_module, "_window", mock_win),
        patch.dict("sys.modules", {"webview": MagicMock(FileDialog=MagicMock(FOLDER=3))}),
    ):
        result = instance.select_output_folder()
    assert result is None


# ── _resolve_output_dir ───────────────────────────────────────────────────────

def test_resolve_output_dir_falls_back_to_default_when_no_config(tmp_path):
    from app import _resolve_output_dir
    with patch.object(app_module, "CONFIG_PATH", str(tmp_path / "missing.json")):
        result = _resolve_output_dir()
    assert result == _DEFAULT_OUTPUT_DIR


def test_resolve_output_dir_reads_from_config(tmp_path):
    from app import _resolve_output_dir
    custom = str(tmp_path / "custom_out")
    os.makedirs(custom)
    cfg = {"storage": {"output_dir": custom}}
    config_path = str(tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    with patch.object(app_module, "CONFIG_PATH", config_path):
        result = _resolve_output_dir()
    assert result == custom


def test_resolve_output_dir_ignores_nonexistent_stored_path(tmp_path):
    from app import _resolve_output_dir
    cfg = {"storage": {"output_dir": str(tmp_path / "gone")}}
    config_path = str(tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    with patch.object(app_module, "CONFIG_PATH", config_path):
        result = _resolve_output_dir()
    assert result == _DEFAULT_OUTPUT_DIR


# ── New session-per-directory layout tests (F6) ───────────────────────────────

def _setup_new_layout_session(output_dir, job_id, extra=None):
    """Create a new-layout session dir with transcript.json."""
    session_dir = os.path.join(output_dir, "meetings", job_id)
    os.makedirs(session_dir, exist_ok=True)
    data = {
        "job_id": job_id,
        "filename": f"test_{job_id}.mp3",
        "segments": [{"speaker": "S1", "start": 0.0, "end": 10.0, "text": "hello", "words": []}],
        "language": "en",
        "created_at": "2026-03-24T10:00:00",
        "audio": os.path.join(session_dir, "source_audio.mp3"),
    }
    if extra:
        data.update(extra)
    with open(os.path.join(session_dir, "transcript.json"), "w") as f:
        json.dump(data, f)
    return session_dir


def test_get_history_finds_new_layout_sessions(api):
    instance, tmp_path = api
    output_dir = str(tmp_path / "output")
    with patch.object(app_module, "OUTPUT_DIR", output_dir):
        _setup_new_layout_session(output_dir, "new-job-001")
        history = instance.get_history()
    assert any(h["job_id"] == "new-job-001" for h in history)


def test_get_history_finds_legacy_sessions(api):
    instance, tmp_path = api
    output_dir = str(tmp_path / "output")
    # Create legacy flat JSON
    legacy_data = {
        "filename": "legacy.mp3",
        "segments": [{"speaker": "S1", "start": 0.0, "end": 5.0, "text": "hi", "words": []}],
        "language": "zh",
        "created_at": "2026-01-01T00:00:00",
    }
    with open(os.path.join(output_dir, "legacy-job.json"), "w") as f:
        json.dump(legacy_data, f)
    with patch.object(app_module, "OUTPUT_DIR", output_dir):
        history = instance.get_history()
    assert any(h["job_id"] == "legacy-job" for h in history)


def test_delete_session_new_uses_rmtree(api):
    instance, tmp_path = api
    output_dir = str(tmp_path / "output")
    session_dir = _setup_new_layout_session(output_dir, "del-new-job")
    with patch.object(app_module, "OUTPUT_DIR", output_dir):
        ok = instance.delete_session("del-new-job")
    assert ok is True
    assert not os.path.exists(session_dir)


def test_delete_session_legacy_per_file(api):
    instance, tmp_path = api
    output_dir = str(tmp_path / "output")
    job_id = "del-legacy-job"
    json_path = os.path.join(output_dir, f"{job_id}.json")
    summary_path = os.path.join(output_dir, f"{job_id}_summary.json")
    with open(json_path, "w") as f:
        json.dump({"filename": "x.mp3"}, f)
    with open(summary_path, "w") as f:
        json.dump([], f)
    with patch.object(app_module, "OUTPUT_DIR", output_dir):
        ok = instance.delete_session(job_id)
    assert ok is True
    assert not os.path.exists(json_path)
    assert not os.path.exists(summary_path)


def test_rename_session_new_layout(api):
    instance, tmp_path = api
    output_dir = str(tmp_path / "output")
    session_dir = _setup_new_layout_session(output_dir, "rename-new-job")
    with patch.object(app_module, "OUTPUT_DIR", output_dir):
        ok = instance.rename_session("rename-new-job", "My Meeting")
    assert ok is True
    transcript_path = os.path.join(session_dir, "transcript.json")
    with open(transcript_path) as f:
        data = json.load(f)
    assert data["filename"] == "My Meeting"


def test_summary_versions_new_layout(api):
    instance, tmp_path = api
    output_dir = str(tmp_path / "output")
    session_dir = _setup_new_layout_session(output_dir, "sum-new-job")
    with patch.object(app_module, "OUTPUT_DIR", output_dir):
        ok = instance.save_summary_version("sum-new-job", "My summary text")
        assert ok is True
        versions = instance.get_summary_versions("sum-new-job")
    assert len(versions) == 1
    assert versions[0]["text"] == "My summary text"
    # Summary file should be inside session dir
    assert os.path.exists(os.path.join(session_dir, "summary.json"))


def test_agent_chat_path_new_layout(tmp_path):
    """agent_store._session_path() should use new layout when session dir exists."""
    from src.agent_store import _session_path
    output_dir = str(tmp_path)
    job_id = "agent-new-job"
    session_dir = os.path.join(output_dir, "meetings", job_id)
    os.makedirs(session_dir, exist_ok=True)
    path = _session_path(output_dir, job_id)
    assert path == os.path.join(session_dir, "agent_chat.json")


def test_agent_chat_path_legacy_layout(tmp_path):
    """agent_store._session_path() should use legacy layout when no session dir."""
    from src.agent_store import _session_path
    output_dir = str(tmp_path)
    job_id = "agent-legacy-job"
    path = _session_path(output_dir, job_id)
    assert path == os.path.join(output_dir, f"{job_id}_agent_chat.json")
