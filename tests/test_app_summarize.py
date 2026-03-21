"""Tests for app.API.summarize — background thread + JS event push."""
import json
import os
import time
import pytest
from unittest.mock import patch, MagicMock

import app as app_module
from app import API

TEMPLATE = {"name": "Meeting", "prompt": "Summarize this meeting."}

TRANSCRIPT_DATA = {
    "audio": "/path/to/audio.mp3",
    "language": "en",
    "segments": [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0,
         "text": "Hello everyone.", "words": []},
        {"speaker": "SPEAKER_01", "start": 2.5, "end": 5.0,
         "text": "Thanks for joining.", "words": []},
    ],
}

API_CONFIG = {
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-test",
    "model": "gpt-test",
}


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def job_env(tmp_path):
    """Patch OUTPUT_DIR + CONFIG_PATH to tmp; pre-create transcript + api config."""
    job_id = "summarize-test-001"

    # Transcript file
    json_path = tmp_path / f"{job_id}.json"
    json_path.write_text(json.dumps(TRANSCRIPT_DATA), encoding="utf-8")

    # Config file with api config
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"api": API_CONFIG}), encoding="utf-8")

    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)), \
         patch.object(app_module, "CONFIG_PATH", str(config_path)):
        yield API(), job_id


def _wait_thread(timeout=2.0):
    """Give the background thread time to finish."""
    time.sleep(timeout)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSummarizeReturn:
    def test_returns_started_status(self, job_env):
        api, job_id = job_env
        with patch("src.summary.summarize", return_value=iter([])):
            result = api.summarize(job_id, TEMPLATE)
        assert result["status"] == "started"
        assert result["job_id"] == job_id


class TestSummarizeJSEvents:
    def _collect_js(self, api, job_id, text_chunks):
        """Run summarize and collect all JS calls pushed to the window."""
        js_calls = []
        with patch("src.summary.summarize", return_value=iter(text_chunks)), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            api.summarize(job_id, TEMPLATE)
            _wait_thread(0.5)
        return js_calls

    def test_pushes_chunk_events(self, job_env):
        api, job_id = job_env
        js_calls = self._collect_js(api, job_id, ["Hello", " world"])
        chunk_calls = [c for c in js_calls if "onSummaryChunk" in c]
        assert len(chunk_calls) == 2

    def test_pushes_complete_event(self, job_env):
        api, job_id = job_env
        js_calls = self._collect_js(api, job_id, ["Done."])
        assert any("onSummaryComplete" in c for c in js_calls)

    def test_complete_contains_full_text(self, job_env):
        api, job_id = job_env
        js_calls = self._collect_js(api, job_id, ["Part1", "Part2"])
        complete_call = next(c for c in js_calls if "onSummaryComplete" in c)
        assert "Part1Part2" in complete_call


class TestSummarizeErrors:
    def test_missing_transcript_pushes_error(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"api": API_CONFIG}), encoding="utf-8")

        js_calls = []
        with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)), \
             patch.object(app_module, "CONFIG_PATH", str(config_path)), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            API().summarize("no-such-job", TEMPLATE)
            _wait_thread(0.3)

        assert any("onSummaryError" in c for c in js_calls)
        assert any("not found" in c.lower() for c in js_calls)

    def test_api_exception_pushes_error(self, job_env):
        api, job_id = job_env
        js_calls = []

        with patch("src.summary.summarize", side_effect=RuntimeError("API down")), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            api.summarize(job_id, TEMPLATE)
            _wait_thread(0.5)

        assert any("onSummaryError" in c for c in js_calls)


class TestSummarizeTranscriptText:
    def test_transcript_text_passed_to_summary(self, job_env):
        """Segment texts should be joined and forwarded to the LLM."""
        api, job_id = job_env

        captured = {}

        def fake_summarize(text, prompt, base_url, api_key, model, stream):
            captured["text"] = text
            captured["prompt"] = prompt
            return iter([])

        with patch("src.summary.summarize", fake_summarize), \
             patch.object(app_module, "_push"):
            api.summarize(job_id, TEMPLATE)
            _wait_thread(0.5)

        assert "Hello everyone." in captured.get("text", "")
        assert "Thanks for joining." in captured.get("text", "")
        assert captured.get("prompt") == TEMPLATE["prompt"]
