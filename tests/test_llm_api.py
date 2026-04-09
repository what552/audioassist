"""Tests for src.llm_api — config normalization and remote model loading."""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


def _fake_openai_module(mock_client):
    openai_mod = types.ModuleType("openai")
    openai_mod.OpenAI = MagicMock(return_value=mock_client)
    return openai_mod


def test_normalize_openrouter_defaults_base_url():
    from src.llm_api import DEFAULT_OPENROUTER_BASE_URL, normalize_api_config

    cfg = normalize_api_config({
        "base_url": "",
        "api_key": "sk-or-v1-test",
        "model": "anthropic/claude-3.5-haiku",
    })

    assert cfg["base_url"] == DEFAULT_OPENROUTER_BASE_URL
    assert cfg["provider"] == "openrouter"


def test_normalize_openrouter_clears_placeholder_model():
    from src.llm_api import normalize_api_config

    cfg = normalize_api_config({
        "base_url": "",
        "api_key": "sk-or-v1-test",
        "model": "openrouter",
    })

    assert cfg["model"] == ""


def test_validate_chat_config_requires_real_openrouter_model():
    from src.llm_api import validate_chat_config

    with pytest.raises(ValueError, match="Select an OpenRouter model"):
        validate_chat_config("", "sk-or-v1-test", "openrouter")


def test_list_available_models_uses_normalized_openrouter_url():
    mock_client = MagicMock()
    mock_client.models.list.return_value = types.SimpleNamespace(data=[
        types.SimpleNamespace(id="z-model", name="Zed"),
        types.SimpleNamespace(id="a-model", name=""),
    ])

    openai_mod = _fake_openai_module(mock_client)
    with patch.dict(sys.modules, {"openai": openai_mod}):
        import importlib
        import src.llm_api as llm_api

        importlib.reload(llm_api)
        models = llm_api.list_available_models("", "sk-or-v1-test")

    openai_mod.OpenAI.assert_called_once_with(
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-v1-test",
    )
    assert models == [
        {"id": "a-model", "name": "a-model"},
        {"id": "z-model", "name": "Zed"},
    ]
