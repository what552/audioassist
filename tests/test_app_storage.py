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
