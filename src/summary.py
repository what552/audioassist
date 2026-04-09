"""
LLM summary module — OpenAI-compatible API wrapper.

Supports streaming and non-streaming modes.
Any OpenAI-compatible endpoint (OpenAI, DeepSeek, Qwen, Ollama, etc.)
can be used by supplying base_url + api_key + model.
"""
from __future__ import annotations
from typing import Iterator

from .llm_api import validate_chat_config


def summarize(
    text: str,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    stream: bool = False,
) -> str | Iterator[str]:
    """
    Call an OpenAI-compatible chat endpoint to summarize transcript text.

    Args:
        text:     Transcript text to summarize (sent as user message).
        prompt:   System prompt / instruction (e.g. "Summarize this meeting.").
        base_url: API endpoint base URL (e.g. "https://api.openai.com/v1").
        api_key:  API key for authentication.
        model:    Model ID (e.g. "gpt-4o-mini", "deepseek-chat").
        stream:   If True return a generator yielding text chunks;
                  if False return the complete summary string.

    Returns:
        Full summary string (stream=False) or Iterator[str] (stream=True).

    Raises:
        ImportError: if the openai package is not installed.
        openai.APIError: on API-level errors.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "openai package required for summary. Run: pip install openai"
        )

    cfg = validate_chat_config(base_url, api_key, model)
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user",   "content": text},
    ]

    if stream:
        def _stream_gen() -> Iterator[str]:
            response = client.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                stream=True,
            )
            for chunk in response:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        return _stream_gen()

    response = client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        stream=False,
    )
    return response.choices[0].message.content or ""
