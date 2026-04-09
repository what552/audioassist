"""
Helpers for OpenAI-compatible API config normalization and model discovery.
"""
from __future__ import annotations

from typing import Any


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL_PLACEHOLDERS = {"openrouter"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def is_openrouter_config(base_url: str = "", api_key: str = "") -> bool:
    base = _clean(base_url).lower()
    key = _clean(api_key).lower()
    return "openrouter.ai" in base or key.startswith("sk-or-")


def normalize_api_config(config: dict | None) -> dict:
    raw = dict(config or {})
    api_key = _clean(raw.get("api_key"))
    base_url = _clean(raw.get("base_url")).rstrip("/")
    model = _clean(raw.get("model"))

    if not base_url and is_openrouter_config("", api_key):
        base_url = DEFAULT_OPENROUTER_BASE_URL

    provider = "openrouter" if is_openrouter_config(base_url, api_key) else "openai-compatible"
    if provider == "openrouter" and model.lower() in _OPENROUTER_MODEL_PLACEHOLDERS:
        model = ""

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "provider": provider,
    }


def validate_chat_config(base_url: str, api_key: str, model: str) -> dict:
    cfg = normalize_api_config({
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    })

    if not cfg["base_url"]:
        raise ValueError("Base URL is required before generating a summary.")
    if cfg["provider"] == "openrouter" and not cfg["api_key"]:
        raise ValueError("OpenRouter API key is required before generating a summary.")
    if not cfg["model"]:
        if cfg["provider"] == "openrouter":
            raise ValueError("Select an OpenRouter model in Settings before generating a summary.")
        raise ValueError("Model is required before generating a summary.")
    return cfg


def list_available_models(base_url: str, api_key: str) -> list[dict]:
    cfg = normalize_api_config({
        "base_url": base_url,
        "api_key": api_key,
    })

    if not cfg["base_url"]:
        raise ValueError("Base URL is required before loading models.")
    if cfg["provider"] == "openrouter" and not cfg["api_key"]:
        raise ValueError("OpenRouter API key is required before loading models.")

    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "openai package required for model loading. Run: pip install openai"
        )

    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    response = client.models.list()
    items = getattr(response, "data", response) or []

    models: list[dict] = []
    seen: set[str] = set()
    for item in items:
        model_id = getattr(item, "id", None)
        if model_id is None and isinstance(item, dict):
            model_id = item.get("id")
        model_id = _clean(model_id)
        if not model_id or model_id in seen:
            continue

        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            name = item.get("name")
        extra = getattr(item, "model_extra", None)
        if not name and isinstance(extra, dict):
            name = extra.get("name")

        models.append({
            "id": model_id,
            "name": _clean(name) or model_id,
        })
        seen.add(model_id)

    models.sort(key=lambda entry: entry["id"].lower())
    return models
