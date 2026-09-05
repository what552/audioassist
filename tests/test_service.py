"""Tests for the framework-independent AudioAssist service layer."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from src.service import AudioAssistService, InvalidJobIdError, SessionNotFoundError


def _write_transcript(output_dir, job_id: str, *, new_layout: bool = True) -> None:
    data = {
        "job_id": job_id,
        "filename": f"{job_id}.wav",
        "audio": f"/audio/{job_id}.wav",
        "language": "zh",
        "created_at": "2026-09-05T12:00:00",
        "segments": [
            {
                "speaker": "SPEAKER_00",
                "start": 0.0,
                "end": 12.4,
                "text": "测试内容",
                "words": [],
            }
        ],
    }
    if new_layout:
        path = output_dir / "meetings" / job_id / "transcript.json"
        path.parent.mkdir(parents=True)
    else:
        path = output_dir / f"{job_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_list_sessions_supports_new_and_legacy_layouts(tmp_path):
    _write_transcript(tmp_path, "new-job")
    _write_transcript(tmp_path, "legacy-job", new_layout=False)

    sessions = AudioAssistService(str(tmp_path)).list_sessions()

    assert {item["job_id"] for item in sessions} == {"new-job", "legacy-job"}
    assert all(item["duration"] == 12 for item in sessions)


def test_status_reports_checkpoint_progress(tmp_path):
    session_dir = tmp_path / "meetings" / "job-1"
    session_dir.mkdir(parents=True)
    (session_dir / "transcription_task.json").write_text(
        json.dumps({
            "job_id": "job-1",
            "status": "interrupted",
            "completed_chunks": 2,
            "total_chunks": 5,
        }),
        encoding="utf-8",
    )

    status = AudioAssistService(str(tmp_path)).get_status("job-1")

    assert status["status"] == "interrupted"
    assert status["progress"] == pytest.approx(0.4)
    assert status["resumable"] is True


def test_get_session_rejects_path_traversal(tmp_path):
    with pytest.raises(InvalidJobIdError):
        AudioAssistService(str(tmp_path)).get_session("../../config")


def test_get_status_raises_for_missing_session(tmp_path):
    with pytest.raises(SessionNotFoundError):
        AudioAssistService(str(tmp_path)).get_status("missing")


def test_transcribe_persists_gui_compatible_session(tmp_path):
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"RIFF")
    output_dir = tmp_path / "output"

    def fake_pipeline(audio_path, output_dir, **_kwargs):
        json_path = os.path.join(output_dir, "transcript.json")
        markdown_path = os.path.join(output_dir, "transcript.md")
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump({
                "audio": audio_path,
                "language": "zh",
                "segments": [],
            }, file)
        with open(markdown_path, "w", encoding="utf-8") as file:
            file.write("# Transcript\n")
        return json_path, markdown_path

    with patch("src.pipeline.run", side_effect=fake_pipeline):
        result = AudioAssistService(str(output_dir)).transcribe(
            str(source), job_id="job-123"
        )

    transcript = json.loads(
        (output_dir / "meetings" / "job-123" / "transcript.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["job_id"] == "job-123"
    assert transcript["filename"] == "meeting.wav"
    assert transcript["audio"].endswith("source_audio.wav")
    assert os.path.exists(transcript["audio"])


def test_summary_versions_are_capped_at_three(tmp_path):
    service = AudioAssistService(str(tmp_path))
    session_dir = tmp_path / "meetings" / "job-1"
    session_dir.mkdir(parents=True)

    for index in range(5):
        service.save_summary_version("job-1", f"summary {index}")

    versions = service.get_summary_versions("job-1")
    assert [item["text"] for item in versions] == ["summary 2", "summary 3", "summary 4"]
