"""Tests for src/summary.py — OpenAI-compatible LLM summary wrapper."""
import sys
import types
import pytest
from unittest.mock import MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────────

CALL_ARGS = dict(
    text="Hello world transcript.",
    prompt="Summarize this.",
    base_url="https://api.example.com/v1",
    api_key="sk-test",
    model="gpt-test",
)


def _make_non_stream_response(content: str):
    msg = MagicMock(); msg.content = content
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    return resp


def _make_stream_chunks(parts: list):
    """parts may contain str or None (to simulate empty delta)."""
    chunks = []
    for part in parts:
        delta = MagicMock(); delta.content = part
        choice = MagicMock(); choice.delta = delta
        chunk = MagicMock(); chunk.choices = [choice]
        chunks.append(chunk)
    return iter(chunks)


def _fake_openai_module(mock_client):
    """Return a fake openai module with OpenAI class that returns mock_client."""
    openai_mod = types.ModuleType("openai")
    openai_mod.OpenAI = MagicMock(return_value=mock_client)
    return openai_mod


# ── non-streaming ─────────────────────────────────────────────────────────────

class TestNonStreaming:
    def _run(self, mock_client, **kwargs):
        openai_mod = _fake_openai_module(mock_client)
        with patch.dict(sys.modules, {"openai": openai_mod}):
            import importlib, src.summary as _mod
            importlib.reload(_mod)
            return _mod.summarize(**{**CALL_ARGS, **kwargs}, stream=False)

    def test_returns_string(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_non_stream_response("Summary here.")
        result = self._run(client)
        assert result == "Summary here."

    def test_empty_content_returns_empty_string(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_non_stream_response("")
        result = self._run(client)
        assert result == ""

    def test_api_called_with_correct_params(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_non_stream_response("ok")
        self._run(client)
        kw = client.chat.completions.create.call_args[1]
        assert kw["model"] == "gpt-test"
        assert kw["stream"] is False
        msgs = kw["messages"]
        assert msgs[0] == {"role": "system", "content": "Summarize this."}
        assert msgs[1] == {"role": "user",   "content": "Hello world transcript."}

    def test_client_constructed_with_base_url_and_api_key(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_non_stream_response("x")
        openai_mod = _fake_openai_module(client)
        with patch.dict(sys.modules, {"openai": openai_mod}):
            import importlib, src.summary as _mod
            importlib.reload(_mod)
            _mod.summarize(**CALL_ARGS, stream=False)
        openai_mod.OpenAI.assert_called_once_with(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )


# ── streaming ─────────────────────────────────────────────────────────────────

class TestStreaming:
    def _run_stream(self, parts):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_stream_chunks(parts)
        openai_mod = _fake_openai_module(client)
        with patch.dict(sys.modules, {"openai": openai_mod}):
            import importlib, src.summary as _mod
            importlib.reload(_mod)
            gen = _mod.summarize(**CALL_ARGS, stream=True)
            return list(gen), client

    def test_returns_iterator(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_stream_chunks(["Hello", " world"])
        openai_mod = _fake_openai_module(client)
        with patch.dict(sys.modules, {"openai": openai_mod}):
            import importlib, src.summary as _mod
            importlib.reload(_mod)
            result = _mod.summarize(**CALL_ARGS, stream=True)
        assert hasattr(result, "__iter__")

    def test_yields_chunks(self):
        chunks, _ = self._run_stream(["A", "B", "C"])
        assert chunks == ["A", "B", "C"]

    def test_none_delta_content_skipped(self):
        chunks, _ = self._run_stream([None, "real", None])
        assert chunks == ["real"]

    def test_api_called_with_stream_true(self):
        _, client = self._run_stream(["ok"])
        kw = client.chat.completions.create.call_args[1]
        assert kw["stream"] is True


# ── ImportError ───────────────────────────────────────────────────────────────

class TestImportError:
    def test_missing_openai_raises_import_error(self):
        with patch.dict(sys.modules, {"openai": None}):
            import importlib, src.summary as _mod
            importlib.reload(_mod)
            with pytest.raises(ImportError, match="openai package required"):
                _mod.summarize(**CALL_ARGS, stream=False)
