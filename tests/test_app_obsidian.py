"""Tests for app.API Obsidian sync methods."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

import app as app_module
from app import API


# ── Fixtures ───────────────────────────────────────────────────────────────────

DATA = {
    "audio":      "/tmp/meeting.mp3",
    "filename":   "meeting.mp3",
    "created_at": "2026-03-23T10:00:00",
    "segments":   [{"speaker": "A", "start": 0.0, "end": 5.0, "text": "Hi"}],
}


@pytest.fixture
def api(tmp_path):
    """API instance with CONFIG_PATH + OUTPUT_DIR inside tmp_path."""
    config_path = str(tmp_path / "config.json")
    output_dir  = str(tmp_path / "output")
    os.makedirs(output_dir)
    with (
        patch.object(app_module, "APP_DATA_DIR", str(tmp_path)),
        patch.object(app_module, "CONFIG_PATH", config_path),
        patch.object(app_module, "OUTPUT_DIR", output_dir),
    ):
        yield API(), tmp_path, output_dir


@pytest.fixture
def api_with_obs(tmp_path):
    """API instance pre-configured with an enabled Obsidian folder."""
    config_path = str(tmp_path / "config.json")
    output_dir  = str(tmp_path / "output")
    obs_dir     = tmp_path / "obsidian"
    obs_dir.mkdir()
    os.makedirs(output_dir)

    cfg = {"obsidian": {"enabled": True, "folder": str(obs_dir)}}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    with (
        patch.object(app_module, "APP_DATA_DIR", str(tmp_path)),
        patch.object(app_module, "CONFIG_PATH", config_path),
        patch.object(app_module, "OUTPUT_DIR", output_dir),
    ):
        yield API(), tmp_path, output_dir, str(obs_dir)


# ── get_obsidian_config ────────────────────────────────────────────────────────

def test_get_obsidian_config_defaults(api):
    instance, tmp_path, _ = api
    result = instance.get_obsidian_config()
    assert result == {"folder": "", "enabled": False}


def test_get_obsidian_config_returns_saved(api):
    instance, tmp_path, _ = api
    cfg = {"obsidian": {"folder": "/vault", "enabled": True}}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    result = instance.get_obsidian_config()
    assert result["folder"] == "/vault"
    assert result["enabled"] is True


# ── set_obsidian_config ────────────────────────────────────────────────────────

def test_set_obsidian_config_saves(api):
    instance, tmp_path, _ = api
    ret = instance.set_obsidian_config("/vault/folder", True)
    assert ret is True
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["obsidian"]["folder"] == "/vault/folder"
    assert saved["obsidian"]["enabled"] is True


def test_set_obsidian_config_strips_whitespace(api):
    instance, tmp_path, _ = api
    instance.set_obsidian_config("  /vault  ", True)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["obsidian"]["folder"] == "/vault"


def test_set_obsidian_config_empty_folder(api):
    instance, tmp_path, _ = api
    instance.set_obsidian_config("", False)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["obsidian"]["folder"] == ""
    assert saved["obsidian"]["enabled"] is False


# ── select_obsidian_folder ─────────────────────────────────────────────────────

def test_select_obsidian_folder_no_window(api):
    instance, _, _ = api
    with patch.object(app_module, "_window", None):
        result = instance.select_obsidian_folder()
    assert result is None


def test_select_obsidian_folder_returns_path(api):
    instance, _, _ = api
    mock_win = MagicMock()
    mock_win.create_file_dialog.return_value = ["/chosen/folder"]
    import webview
    with (
        patch.object(app_module, "_window", mock_win),
        patch.dict("sys.modules", {"webview": MagicMock(FileDialog=MagicMock(FOLDER=3))}),
    ):
        result = instance.select_obsidian_folder()
    assert result == "/chosen/folder"


def test_select_obsidian_folder_cancelled(api):
    instance, _, _ = api
    mock_win = MagicMock()
    mock_win.create_file_dialog.return_value = None
    with (
        patch.object(app_module, "_window", mock_win),
        patch.dict("sys.modules", {"webview": MagicMock(FileDialog=MagicMock(FOLDER=3))}),
    ):
        result = instance.select_obsidian_folder()
    assert result is None


# ── sync_to_obsidian ──────────────────────────────────────────────────────────

def test_sync_to_obsidian_disabled(api):
    instance, _, _ = api
    result = instance.sync_to_obsidian("job-001")
    assert result["status"] == "disabled"


def test_sync_to_obsidian_missing_folder(api):
    instance, tmp_path, _ = api
    cfg = {"obsidian": {"enabled": True, "folder": "/nonexistent/path"}}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    result = instance.sync_to_obsidian("job-001")
    assert result["status"] == "error"
    assert "Folder not found" in result["error"]


def test_sync_to_obsidian_ok(api_with_obs):
    instance, tmp_path, output_dir, obs_dir = api_with_obs
    job_id = "job-obs-001"
    (tmp_path / "output" / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")

    result = instance.sync_to_obsidian(job_id)
    assert result["status"] == "ok"
    assert os.path.isfile(result["path"])


def test_sync_to_obsidian_missing_transcript(api_with_obs):
    instance, *_ = api_with_obs
    result = instance.sync_to_obsidian("nonexistent-job")
    assert result["status"] == "error"


# ── _obsidian_auto_sync ───────────────────────────────────────────────────────

def test_obsidian_auto_sync_disabled_noop(api):
    instance, _, _ = api
    # Should not raise even when disabled
    instance._obsidian_auto_sync("job-001")


def test_obsidian_auto_sync_writes_file(api_with_obs):
    instance, tmp_path, output_dir, obs_dir = api_with_obs
    job_id = "job-auto-001"
    (tmp_path / "output" / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")

    instance._obsidian_auto_sync(job_id)
    md_files = [f for f in os.listdir(obs_dir) if f.endswith(".md")]
    assert len(md_files) == 1


# ── _obsidian_rename ──────────────────────────────────────────────────────────

def test_obsidian_rename_renames_file(api_with_obs):
    instance, tmp_path, output_dir, obs_dir = api_with_obs
    job_id = "job-rename-001"

    # Create the old MD file manually
    old_name = "2026-03-23 OldTitle.md"
    old_path = os.path.join(obs_dir, old_name)
    with open(old_path, "w") as f:
        f.write("# old")

    (tmp_path / "output" / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")
    instance._obsidian_rename(job_id, "OldTitle", "2026-03-23", "NewTitle")

    assert not os.path.exists(old_path)
    new_path = os.path.join(obs_dir, "2026-03-23 NewTitle.md")
    assert os.path.exists(new_path)


def test_obsidian_rename_creates_if_old_missing(api_with_obs):
    instance, tmp_path, output_dir, obs_dir = api_with_obs
    job_id = "job-rename-002"
    (tmp_path / "output" / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")

    # No old file exists — should fall back to sync_job
    instance._obsidian_rename(job_id, "OldTitle", "2026-03-23", "NewTitle")
    md_files = [f for f in os.listdir(obs_dir) if f.endswith(".md")]
    assert len(md_files) == 1


# ── _obsidian_startup_scan ────────────────────────────────────────────────────

def test_obsidian_startup_scan_syncs_all(api_with_obs):
    instance, tmp_path, output_dir, obs_dir = api_with_obs
    for jid in ("job-scan-a", "job-scan-b"):
        data = dict(DATA, filename=f"{jid}.mp3")  # unique filename → unique MD file
        (tmp_path / "output" / f"{jid}.json").write_text(json.dumps(data), encoding="utf-8")

    instance._obsidian_startup_scan()
    md_files = [f for f in os.listdir(obs_dir) if f.endswith(".md")]
    assert len(md_files) == 2


def test_obsidian_startup_scan_disabled_noop(api):
    instance, _, _ = api
    # Should not raise when disabled
    instance._obsidian_startup_scan()
