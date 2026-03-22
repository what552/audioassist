"""Tests for app.API agent methods: start_agent_turn, get_agent_session, clear_agent_session."""
import json
import time
import pytest
from unittest.mock import patch, MagicMock

import app as app_module
from app import API

TRANSCRIPT_DATA = {
    "audio": "/path/to/audio.mp3",
    "language": "en",
    "segments": [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0,
         "text": "Hello.", "words": []},
    ],
}

API_CONFIG = {
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-test",
    "model": "gpt-test",
}


@pytest.fixture
def job_env(tmp_path):
    """Patch OUTPUT_DIR + CONFIG_PATH; pre-create transcript + config."""
    job_id = "agent-test-001"
    (tmp_path / f"{job_id}.json").write_text(
        json.dumps(TRANSCRIPT_DATA), encoding="utf-8"
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"api": API_CONFIG}), encoding="utf-8")

    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)), \
         patch.object(app_module, "CONFIG_PATH", str(config_path)):
        yield API(), job_id, tmp_path


def _wait(secs=0.5):
    time.sleep(secs)


# ── start_agent_turn ──────────────────────────────────────────────────────────

class TestStartAgentTurn:
    def test_returns_started_status(self, job_env):
        api, job_id, _ = job_env
        js_calls = []
        with patch("src.agent.MeetingAgent.run", return_value="ok"), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            result = api.start_agent_turn(job_id, "Hello agent")
            _wait(0.4)  # wait for background thread
        assert result["status"] == "started"
        assert result["job_id"] == job_id

    def test_onAgentComplete_pushed(self, job_env):
        """app.py pushes onAgentComplete after agent.run() returns."""
        api, job_id, _ = job_env
        js_calls = []
        with patch("src.agent.MeetingAgent.run", return_value="Agent reply."), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            api.start_agent_turn(job_id, "hi")
            _wait(0.5)
        assert any("onAgentComplete" in c for c in js_calls)
        complete = next(c for c in js_calls if "onAgentComplete" in c)
        assert "Agent reply." in complete

    def test_onAgentError_pushed_on_exception(self, job_env):
        api, job_id, _ = job_env
        js_calls = []
        with patch("src.agent.MeetingAgent.run", side_effect=RuntimeError("boom")), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            api.start_agent_turn(job_id, "hi")
            _wait(0.5)
        assert any("onAgentError" in c for c in js_calls)

    def test_session_saved_after_complete(self, job_env):
        api, job_id, tmp_path = job_env
        with patch("src.agent.MeetingAgent.run", return_value="Saved reply."), \
             patch.object(app_module, "_push"):
            api.start_agent_turn(job_id, "user message")
            _wait(0.5)
        chat_path = tmp_path / f"{job_id}_agent_chat.json"
        assert chat_path.exists()
        data = json.loads(chat_path.read_text())
        turns = data["turns"]
        assert any(t["role"] == "user" and t["content"] == "user message" for t in turns)
        assert any(t["role"] == "assistant" and t["content"] == "Saved reply." for t in turns)

    def test_onAgentDraftUpdated_relayed(self, job_env):
        """When MeetingAgent pushes onAgentDraftUpdated, app.py should relay it."""
        api, job_id, _ = job_env
        js_calls = []

        def fake_run(self_agent, job_id_arg, user_input, history):
            self_agent._push("onAgentDraftUpdated", {"job_id": job_id_arg, "text": "New draft."})
            self_agent._push("onAgentComplete", {"job_id": job_id_arg, "text": "Done."})
            return "Done."

        with patch.object(app_module, "_push", side_effect=js_calls.append), \
             patch("src.agent.MeetingAgent.run", fake_run):
            api.start_agent_turn(job_id, "rewrite it")
            _wait(0.5)

        assert any("onAgentDraftUpdated" in c for c in js_calls)
        draft_call = next(c for c in js_calls if "onAgentDraftUpdated" in c)
        assert "New draft." in draft_call


# ── get_agent_session ─────────────────────────────────────────────────────────

class TestGetAgentSession:
    def test_returns_empty_turns_when_no_file(self, job_env):
        api, job_id, _ = job_env
        result = api.get_agent_session(job_id)
        assert result["turns"] == []

    def test_returns_saved_session(self, job_env):
        api, job_id, tmp_path = job_env
        session_data = {
            "job_id": job_id,
            "updated_at": "2026-03-22T10:00:00",
            "turns": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        }
        (tmp_path / f"{job_id}_agent_chat.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        result = api.get_agent_session(job_id)
        assert len(result["turns"]) == 2
        assert result["turns"][0]["content"] == "hi"


# ── clear_agent_session ───────────────────────────────────────────────────────

class TestClearAgentSession:
    def test_clear_removes_file(self, job_env):
        api, job_id, tmp_path = job_env
        path = tmp_path / f"{job_id}_agent_chat.json"
        path.write_text(json.dumps({"job_id": job_id, "turns": []}), encoding="utf-8")
        assert path.exists()
        result = api.clear_agent_session(job_id)
        assert result is True
        assert not path.exists()

    def test_clear_returns_false_when_no_file(self, job_env):
        api, job_id, _ = job_env
        result = api.clear_agent_session(job_id)
        assert result is False

    def test_get_returns_empty_after_clear(self, job_env):
        api, job_id, tmp_path = job_env
        path = tmp_path / f"{job_id}_agent_chat.json"
        path.write_text(
            json.dumps({"job_id": job_id, "turns": [{"role": "user", "content": "x"}]}),
            encoding="utf-8",
        )
        api.clear_agent_session(job_id)
        result = api.get_agent_session(job_id)
        assert result["turns"] == []
