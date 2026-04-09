"""Tests for app.API API-config helpers and remote model lookup."""
import json
from unittest.mock import patch

import app as app_module
from app import API


def test_get_api_config_normalizes_legacy_openrouter_values(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "api": {
            "base_url": "",
            "api_key": "sk-or-v1-test",
            "model": "openrouter",
        }
    }), encoding="utf-8")

    with patch.object(app_module, "CONFIG_PATH", str(config_path)):
        cfg = API().get_api_config()

    assert cfg["base_url"] == "https://openrouter.ai/api/v1"
    assert cfg["model"] == ""
    assert cfg["provider"] == "openrouter"


def test_save_api_config_persists_normalized_values(tmp_path):
    config_path = tmp_path / "config.json"

    with patch.object(app_module, "CONFIG_PATH", str(config_path)):
        API().save_api_config({
            "base_url": "",
            "api_key": "sk-or-v1-test",
            "model": "openrouter",
        })

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["api"] == {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-or-v1-test",
        "model": "",
    }


def test_get_remote_models_returns_provider_and_models(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "api": {
            "base_url": "",
            "api_key": "sk-or-v1-test",
            "model": "",
        }
    }), encoding="utf-8")

    with patch.object(app_module, "CONFIG_PATH", str(config_path)), \
         patch("src.llm_api.list_available_models", return_value=[
             {"id": "anthropic/claude-3.5-haiku", "name": "Claude 3.5 Haiku"},
         ]):
        result = API().get_remote_models()

    assert result["provider"] == "openrouter"
    assert result["base_url"] == "https://openrouter.ai/api/v1"
    assert result["models"][0]["id"] == "anthropic/claude-3.5-haiku"
