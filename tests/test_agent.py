"""Tests for MeetingAgent — tool execution and tool-calling loop."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch, call

from src.agent import MeetingAgent


# ── Helpers ──────────────────────────────────────────────────────────────────

TRANSCRIPT_DATA = {
    "segments": [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0, "text": "Hello everyone.", "words": []},
        {"speaker": "SPEAKER_01", "start": 65.0, "end": 68.0, "text": "Thanks.", "words": []},
    ]
}

SUMMARY_DATA = [
    {"text": "First summary.", "created_at": "2026-03-22 10:00"},
    {"text": "Second summary.", "created_at": "2026-03-22 11:00"},
]


def _make_agent(tmp_path) -> tuple[MeetingAgent, list]:
    events = []
    agent = MeetingAgent(
        output_dir=str(tmp_path),
        base_url="http://fake",
        api_key="sk-fake",
        model="fake-model",
        push_event=lambda name, payload: events.append((name, payload)),
    )
    return agent, events


def _write_transcript(tmp_path, job_id="job-001", data=None):
    (tmp_path / f"{job_id}.json").write_text(
        json.dumps(data or TRANSCRIPT_DATA), encoding="utf-8"
    )


def _write_transcript_new_layout(tmp_path, job_id="job-001", data=None):
    session_dir = tmp_path / "meetings" / job_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "transcript.json").write_text(
        json.dumps(data or TRANSCRIPT_DATA), encoding="utf-8"
    )
    return session_dir


def _write_summary(tmp_path, job_id="job-001"):
    (tmp_path / f"{job_id}_summary.json").write_text(
        json.dumps(SUMMARY_DATA), encoding="utf-8"
    )


def _write_summary_new_layout(tmp_path, job_id="job-001", data=None):
    session_dir = tmp_path / "meetings" / job_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(
        json.dumps(data or SUMMARY_DATA), encoding="utf-8"
    )
    return session_dir


# ── Tool unit tests ──────────────────────────────────────────────────────────

class TestToolGetTranscript:
    def test_prefers_new_layout_when_session_dir_exists(self, tmp_path):
        legacy_data = {
            "segments": [{"speaker": "LEGACY", "start": 0.0, "end": 1.0, "text": "old", "words": []}]
        }
        _write_transcript(tmp_path, data=legacy_data)
        _write_transcript_new_layout(tmp_path, "job-001")
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_transcript("job-001")
        assert "LEGACY" not in result["transcript"]
        assert "Hello everyone." in result["transcript"]

    def test_formats_with_timestamps_and_speakers(self, tmp_path):
        _write_transcript(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_transcript("job-001")
        assert "error" not in result
        t = result["transcript"]
        assert "[00:00] SPEAKER_00: Hello everyone." in t
        assert "[01:05] SPEAKER_01: Thanks." in t
        assert result["segment_count"] == 2

    def test_truncates_to_max_chars(self, tmp_path):
        _write_transcript(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_transcript("job-001", max_chars=10)
        assert len(result["transcript"]) <= 10 + len("\n... [truncated]")
        assert "[truncated]" in result["transcript"]

    def test_missing_file_returns_error(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_transcript("no-such-job")
        assert "error" in result


class TestToolGetCurrentSummary:
    def test_reads_new_layout_summary(self, tmp_path):
        _write_summary_new_layout(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_current_summary("job-001")
        assert result["summary"] == "Second summary."
        assert result["version_count"] == 2

    def test_falls_back_to_legacy_summary(self, tmp_path):
        _write_summary(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_current_summary("job-001")
        assert result["summary"] == "Second summary."

    def test_returns_latest_version(self, tmp_path):
        _write_summary(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_current_summary("job-001")
        assert result["summary"] == "Second summary."
        assert result["version_count"] == 2

    def test_empty_when_no_file(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_current_summary("job-001")
        assert result["summary"] == ""
        assert result["version_count"] == 0


class TestToolGetSummaryVersions:
    def test_prefers_new_layout_versions(self, tmp_path):
        legacy = [{"text": "Legacy summary.", "created_at": "2026-03-22 09:00"}]
        _write_summary_new_layout(tmp_path)
        (tmp_path / "job-001_summary.json").write_text(json.dumps(legacy), encoding="utf-8")
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_summary_versions("job-001")
        assert result["versions"][-1]["preview"] == "Second summary."

    def test_lists_versions_with_preview(self, tmp_path):
        _write_summary(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_summary_versions("job-001")
        versions = result["versions"]
        assert len(versions) == 2
        assert versions[0]["index"] == 1
        assert versions[1]["preview"] == "Second summary."

    def test_empty_list_when_no_file(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_get_summary_versions("job-001")
        assert result["versions"] == []


class TestToolUpdateSummary:
    def test_writes_new_layout_when_session_dir_exists(self, tmp_path):
        _write_transcript_new_layout(tmp_path)
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_update_summary("job-001", "New summary text.", "agent edit")
        assert result["saved"] is True
        assert (tmp_path / "meetings" / "job-001" / "summary.json").exists()
        assert not (tmp_path / "job-001_summary.json").exists()

    def test_falls_back_to_legacy_when_no_session_dir(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_update_summary("job-001", "Legacy summary text.", "agent edit")
        assert result["saved"] is True
        assert (tmp_path / "job-001_summary.json").exists()

    def test_saves_new_version(self, tmp_path):
        agent, events = _make_agent(tmp_path)
        result = agent._tool_update_summary("job-001", "New summary text.", "agent edit")
        assert result["saved"] is True
        path = tmp_path / "job-001_summary.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data[-1]["text"] == "New summary text."

    def test_fires_onAgentDraftUpdated(self, tmp_path):
        agent, events = _make_agent(tmp_path)
        agent._tool_update_summary("job-001", "Updated.", "test")
        draft_events = [e for e in events if e[0] == "onAgentDraftUpdated"]
        assert len(draft_events) == 1
        assert draft_events[0][1]["text"] == "Updated."

    def test_keeps_max_3_versions(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        for i in range(4):
            agent._tool_update_summary("job-001", f"v{i}", "test")
        data = json.loads((tmp_path / "job-001_summary.json").read_text())
        assert len(data) == 3

    def test_empty_text_returns_error(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = agent._tool_update_summary("job-001", "   ", "test")
        assert "error" in result


# ── Tool-calling loop tests ───────────────────────────────────────────────────

def _make_tool_call_response(tool_name, args_dict, call_id="call-1"):
    """Build a mock chat completion response that requests a tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(args_dict)

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message.content = ""
    choice.message.tool_calls = [tc]

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_stop_response(content="Final answer."):
    """Build a mock chat completion response that stops."""
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = content
    choice.message.tool_calls = None

    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestToolLoop:
    def test_stops_after_max_iterations_without_stop(self, tmp_path):
        """If provider always returns tool_calls, loop stops at MAX_ITERATIONS."""
        _write_transcript(tmp_path)
        agent, _ = _make_agent(tmp_path)

        # Always return a tool_call response — never "stop"
        always_tool = _make_tool_call_response(
            "get_transcript", {"job_id": "job-001"}
        )
        # After max iterations, the loop calls without tools for final answer
        final = _make_stop_response("Done.")

        call_count = {"n": 0}
        def fake_create(**kwargs):
            call_count["n"] += 1
            # First MAX_ITERATIONS calls return tool_calls; final call returns stop
            if call_count["n"] <= agent.MAX_ITERATIONS:
                return always_tool
            return final

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("src.agent._OpenAI", return_value=mock_client):
            text = agent.run("job-001", "test", [])

        # Should not exceed MAX_ITERATIONS + 1 (final no-tool call)
        assert call_count["n"] <= agent.MAX_ITERATIONS + 1
        assert isinstance(text, str)

    def test_tool_start_end_events_pushed(self, tmp_path):
        """onAgentToolStart/End should be pushed for each tool call."""
        _write_transcript(tmp_path)
        agent, events = _make_agent(tmp_path)

        responses = iter([
            _make_tool_call_response("get_transcript", {"job_id": "job-001"}),
            _make_stop_response("Answer."),
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda **kw: next(responses)

        with patch("src.agent._OpenAI", return_value=mock_client):
            agent.run("job-001", "hi", [])

        tool_start = [e for e in events if e[0] == "onAgentToolStart"]
        tool_end = [e for e in events if e[0] == "onAgentToolEnd"]
        assert len(tool_start) == 1
        assert len(tool_end) == 1
        assert tool_start[0][1]["tool"] == "get_transcript"

    def test_chunk_pushed_on_stop(self, tmp_path):
        """onAgentChunk should be pushed when finish_reason is stop."""
        agent, events = _make_agent(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_stop_response("Hi.")

        with patch("src.agent._OpenAI", return_value=mock_client):
            result = agent.run("job-001", "hello", [])

        chunk_events = [e for e in events if e[0] == "onAgentChunk"]
        assert len(chunk_events) == 1
        assert chunk_events[0][1]["chunk"] == "Hi."
        assert result == "Hi."


class TestProviderFallback:
    def test_falls_back_when_tool_choice_raises(self, tmp_path):
        """If tool_choice causes an error, agent falls back to one-shot mode."""
        _write_transcript(tmp_path)
        agent, events = _make_agent(tmp_path)

        # Simulate a provider that rejects tool_choice
        def fake_create(**kwargs):
            if kwargs.get("tools"):
                raise RuntimeError("tool_choice not supported by this provider")
            # Fallback one-shot streaming call
            chunk = MagicMock()
            chunk.choices[0].delta.content = "Fallback answer."
            return iter([chunk])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("src.agent._OpenAI", return_value=mock_client):
            result = agent.run("job-001", "hi", [])

        # Should not raise; should return some text
        assert isinstance(result, str)
        chunk_events = [e for e in events if e[0] == "onAgentChunk"]
        assert len(chunk_events) >= 1


# ── Security: job_id isolation ────────────────────────────────────────────────

class TestJobIdIsolation:
    def test_execute_tool_ignores_model_supplied_job_id(self, tmp_path):
        """Model-supplied job_id must be overridden by default_job_id."""
        # Write transcript for the legitimate session
        _write_transcript(tmp_path, job_id="legit-job")
        # Write a *different* session that an adversarial prompt might try to access
        other_data = {"segments": [{"speaker": "X", "start": 0.0, "end": 1.0, "text": "secret"}]}
        (tmp_path / "other-job.json").write_text(json.dumps(other_data), encoding="utf-8")

        agent, _ = _make_agent(tmp_path)
        # Model passes a different job_id in args — must be ignored
        result = agent._execute_tool(
            "get_transcript",
            {"job_id": "other-job", "max_chars": 6000},
            default_job_id="legit-job",
        )
        assert "error" not in result
        assert "secret" not in result.get("transcript", "")
        assert "Hello everyone." in result.get("transcript", "")

    def test_tool_loop_uses_session_job_id_not_model_value(self, tmp_path):
        """Even if the model returns a different job_id in tool args, the session job_id is used."""
        _write_transcript(tmp_path, job_id="session-job")
        agent, _ = _make_agent(tmp_path)

        # Model call requests get_transcript with a *different* job_id
        responses = iter([
            _make_tool_call_response("get_transcript", {"job_id": "attacker-job"}),
            _make_stop_response("Ok."),
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda **kw: next(responses)

        executed_job_ids = []
        original_execute = agent._execute_tool

        def spy_execute(name, args, default_job_id):
            # Record which job_id the tool actually runs with
            executed_job_ids.append(default_job_id)
            return original_execute(name, args, default_job_id)

        agent._execute_tool = spy_execute

        with patch("src.agent._OpenAI", return_value=mock_client):
            agent.run("session-job", "read transcript", [])

        # The tool must have been called with the session job_id, not the attacker's
        assert all(jid == "session-job" for jid in executed_job_ids)
