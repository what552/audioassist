"""MCP protocol and AudioAssist tool integration tests."""
from __future__ import annotations

import io
import json
import subprocess

from src import mcp_server
from src.local_api import LocalAPIError
from src.live_transcript import LiveTranscriptWriter
from src.service import AudioAssistService


def _write_transcript(output_dir, job_id="job-1"):
    session = output_dir / "meetings" / job_id
    session.mkdir(parents=True)
    (session / "transcript.json").write_text(json.dumps({
        "job_id": job_id,
        "filename": "meeting.wav",
        "audio": "/audio/meeting.wav",
        "language": "zh",
        "created_at": "2026-09-06T12:00:00",
        "segments": [],
    }), encoding="utf-8")


def _initialized_server(tmp_path):
    server = mcp_server.MCPServer(AudioAssistService(str(tmp_path)), app_data_dir=str(tmp_path))
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    })
    assert response["result"]["protocolVersion"] == "2025-11-25"
    assert server.handle({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
    }) is None
    return server


def test_requires_initialize_before_tools(tmp_path):
    server = mcp_server.MCPServer(AudioAssistService(str(tmp_path)), app_data_dir=str(tmp_path))
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response["error"]["code"] == -32002


def test_lists_recording_draft_read_tool(tmp_path):
    server = _initialized_server(tmp_path)
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [
        "audioassist_list_sessions", "audioassist_get_session", "audioassist_get_status",
        "audioassist_recording_status", "audioassist_get_live_transcript",
        "audioassist_start_recording",
        "audioassist_pause_recording", "audioassist_resume_recording",
        "audioassist_stop_recording",
    ]


def test_read_tools_work_with_desktop_closed(tmp_path):
    _write_transcript(tmp_path)
    server = _initialized_server(tmp_path)
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "audioassist_get_session", "arguments": {"job_id": "job-1"}},
    })
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["session"]["job_id"] == "job-1"


def test_live_draft_tool_reads_paged_utterances(tmp_path):
    writer = LiveTranscriptWriter(str(tmp_path / "meetings" / "live-1"))
    writer.append({"text": "会议进行中", "start": 12, "end": 14})
    assert writer.close()
    server = _initialized_server(tmp_path)
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "audioassist_get_live_transcript",
            "arguments": {"session_id": "live-1", "after_seq": 0, "limit": 10},
        },
    })
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["segments"][0]["text"] == "会议进行中"


def test_missing_session_is_tool_error_not_protocol_error(tmp_path):
    server = _initialized_server(tmp_path)
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "audioassist_get_status", "arguments": {"job_id": "missing"}},
    })
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "session_not_found"


def test_recording_unavailable_is_stable_tool_error(tmp_path, monkeypatch):
    server = _initialized_server(tmp_path)
    monkeypatch.setattr(
        mcp_server, "request_desktop",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LocalAPIError("app_unavailable", "Visible app required", 503)
        ),
    )
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "audioassist_recording_status", "arguments": {}},
    })
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "app_unavailable"


def test_start_recording_forwards_only_bounded_arguments(tmp_path, monkeypatch):
    server = _initialized_server(tmp_path)
    calls = []
    monkeypatch.setattr(
        mcp_server, "request_desktop",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"accepted": "start"},
    )
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "audioassist_start_recording",
            "arguments": {"capture_mode": "mix", "model_id": None},
        },
    })
    assert response["result"]["isError"] is False
    assert calls == [(('POST', '/v1/recording/start', {
        'capture_mode': 'mix', 'model_id': None,
    }), {'app_data_dir': str(tmp_path)})]


def test_invalid_tool_arguments_return_is_error(tmp_path):
    server = _initialized_server(tmp_path)
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "audioassist_start_recording",
            "arguments": {"capture_mode": "lan", "shell": "echo bad"},
        },
    })
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "invalid_request"


def test_stream_parse_error_and_notification_stdout_purity(tmp_path):
    server = mcp_server.MCPServer(AudioAssistService(str(tmp_path)), app_data_dir=str(tmp_path))
    source = io.StringIO(
        "not-json\n"
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
    )
    output = io.StringIO()
    mcp_server.run_stream(server, source, output)
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [message.get("id") for message in messages] == [None, 1, 2]
    assert messages[0]["error"]["code"] == -32700


def test_real_stdio_process_outputs_only_jsonrpc(tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25"
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    process = subprocess.run(
        [
            "/Users/feifei/programing/audioassist/audioassist-main/.venv/bin/python",
            "-m", "src.mcp_server", "--output-dir", str(tmp_path),
            "--app-data-dir", str(tmp_path),
        ],
        input="".join(json.dumps(message) + "\n" for message in messages),
        text=True,
        capture_output=True,
        check=False,
    )
    output = [json.loads(line) for line in process.stdout.splitlines()]
    assert process.returncode == 0
    assert [message["id"] for message in output] == [1, 2]
    assert all(message["jsonrpc"] == "2.0" for message in output)
