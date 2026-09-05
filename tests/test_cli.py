"""Tests for stable CLI output, commands, and exit codes."""
from __future__ import annotations

import json

from src import cli


def _write_transcript(output_dir, job_id: str = "job-1") -> None:
    session_dir = output_dir / "meetings" / job_id
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.json").write_text(
        json.dumps({
            "job_id": job_id,
            "filename": "meeting.wav",
            "audio": "/audio/meeting.wav",
            "language": "zh",
            "created_at": "2026-09-05T12:00:00",
            "segments": [
                {
                    "speaker": "SPEAKER_00",
                    "start": 0,
                    "end": 3,
                    "text": "你好",
                    "words": [],
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_sessions_list_json_envelope(tmp_path, capsys):
    _write_transcript(tmp_path)

    exit_code = cli.main(["--json", "--output-dir", str(tmp_path), "sessions", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["data"][0]["job_id"] == "job-1"


def test_sessions_show_missing_has_stable_error_and_exit_code(tmp_path, capsys):
    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "sessions", "show", "missing"
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_NOT_FOUND
    assert payload["ok"] is False
    assert payload["error"]["code"] == "session_not_found"


def test_status_human_output(tmp_path, capsys):
    _write_transcript(tmp_path)

    exit_code = cli.main(["--output-dir", str(tmp_path), "status", "job-1"])

    output = capsys.readouterr().out
    assert exit_code == cli.EXIT_OK
    assert "job-1\tcompleted\t100%" in output


def test_transcribe_json_keeps_progress_on_stderr(tmp_path, capsys, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    def fake_transcribe(self, path, **kwargs):
        kwargs["progress_callback"](0.5, "Working")
        return {
            "job_id": "job-new",
            "json_path": str(tmp_path / "transcript.json"),
            "markdown_path": str(tmp_path / "transcript.md"),
            "audio_path": path,
            "engine": "qwen",
            "model_id": None,
        }

    monkeypatch.setattr(cli.AudioAssistService, "transcribe", fake_transcribe)
    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "transcribe", str(audio)
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_OK
    assert payload["data"]["job_id"] == "job-new"
    assert "Working" in captured.err


def test_summarize_saves_version(tmp_path, capsys, monkeypatch):
    _write_transcript(tmp_path)
    monkeypatch.setattr(
        cli.AudioAssistService,
        "summarize",
        lambda self, job_id, prompt, stream=False: "结构化纪要",
    )

    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "summarize", "job-1"
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload["data"]["summary"] == "结构化纪要"
    versions = json.loads(
        (tmp_path / "meetings" / "job-1" / "summary.json").read_text(encoding="utf-8")
    )
    assert versions[-1]["text"] == "结构化纪要"
