"""Tests for the framework-independent AudioAssist service layer."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from src.service import (
    AudioAssistService,
    ConfigurationError,
    InvalidJobIdError,
    SessionNotFoundError,
    language_instruction,
)


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


def test_list_sessions_returns_empty_for_missing_output_dir(tmp_path):
    assert AudioAssistService(str(tmp_path / "missing")).list_sessions() == []


def test_list_sessions_ignores_corrupt_and_non_transcript_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"api": {"model": "example"}}), encoding="utf-8"
    )
    (tmp_path / "job_agent_chat.json").write_text(
        json.dumps({"job_id": "job", "turns": []}), encoding="utf-8"
    )

    assert AudioAssistService(str(tmp_path)).list_sessions() == []


def test_canonical_session_wins_over_legacy_duplicate(tmp_path):
    _write_transcript(tmp_path, "same-job", new_layout=False)
    _write_transcript(tmp_path, "same-job", new_layout=True)

    sessions = AudioAssistService(str(tmp_path)).list_sessions()

    assert [item["job_id"] for item in sessions] == ["same-job"]


def test_sessions_are_sorted_newest_first(tmp_path):
    _write_transcript(tmp_path, "older")
    _write_transcript(tmp_path, "newer")
    older_path = tmp_path / "meetings" / "older" / "transcript.json"
    newer_path = tmp_path / "meetings" / "newer" / "transcript.json"
    older = json.loads(older_path.read_text(encoding="utf-8"))
    newer = json.loads(newer_path.read_text(encoding="utf-8"))
    older["created_at"] = "2026-01-01T00:00:00"
    newer["created_at"] = "2026-09-05T00:00:00"
    older_path.write_text(json.dumps(older), encoding="utf-8")
    newer_path.write_text(json.dumps(newer), encoding="utf-8")

    sessions = AudioAssistService(str(tmp_path)).list_sessions()

    assert [item["job_id"] for item in sessions] == ["newer", "older"]


def test_new_layout_realtime_uses_meta_display_name(tmp_path):
    session_dir = tmp_path / "meetings" / "realtime-job"
    session_dir.mkdir(parents=True)
    (session_dir / "realtime_recording_realtime.wav").write_bytes(b"RIFF")
    (session_dir / "meta.json").write_text(
        json.dumps({"filename": "现场录音"}, ensure_ascii=False), encoding="utf-8"
    )

    sessions = AudioAssistService(str(tmp_path)).list_sessions()

    assert sessions[0]["job_id"] == "realtime-job"
    assert sessions[0]["filename"] == "现场录音"
    assert sessions[0]["type"] == "realtime"


def test_transcribed_realtime_wav_is_hidden(tmp_path):
    session_dir = tmp_path / "meetings" / "realtime-job"
    session_dir.mkdir(parents=True)
    (session_dir / "realtime_recording.wav").write_bytes(b"RIFF")
    (session_dir / "meta.json").write_text(
        json.dumps({"transcribed_job_id": "new-job"}), encoding="utf-8"
    )

    assert AudioAssistService(str(tmp_path)).list_sessions() == []


def test_legacy_realtime_wav_is_discovered(tmp_path):
    wav = tmp_path / "legacy-recording.wav"
    wav.write_bytes(b"RIFF")

    sessions = AudioAssistService(str(tmp_path)).list_sessions()

    assert sessions[0]["job_id"] == "legacy-recording"
    assert sessions[0]["type"] == "realtime"
    assert sessions[0]["audio_path"] == str(wav)


def test_completed_transcript_takes_priority_over_checkpoint(tmp_path):
    _write_transcript(tmp_path, "job-1")
    checkpoint = tmp_path / "meetings" / "job-1" / "transcription_task.json"
    checkpoint.write_text(
        json.dumps({"job_id": "job-1", "status": "interrupted"}), encoding="utf-8"
    )

    status = AudioAssistService(str(tmp_path)).get_status("job-1")

    assert status == {
        "job_id": "job-1",
        "status": "completed",
        "progress": 1.0,
        "resumable": False,
    }


def test_running_checkpoint_state_is_preserved(tmp_path):
    session_dir = tmp_path / "meetings" / "job-1"
    session_dir.mkdir(parents=True)
    (session_dir / "transcription_task.json").write_text(
        json.dumps({
            "job_id": "job-1",
            "status": "running",
            "completed_chunks": 0,
            "total_chunks": 0,
            "updated_at": "2026-09-05T12:00:00",
        }),
        encoding="utf-8",
    )

    status = AudioAssistService(str(tmp_path)).get_status("job-1")

    assert status["status"] == "running"
    assert status["progress"] == 0.0
    assert status["resumable"] is True


def test_recorded_status_for_realtime_session(tmp_path):
    session_dir = tmp_path / "meetings" / "recording-job"
    session_dir.mkdir(parents=True)
    (session_dir / "realtime_recording.wav").write_bytes(b"RIFF")

    status = AudioAssistService(str(tmp_path)).get_status("recording-job")

    assert status["status"] == "recorded"
    assert status["progress"] == 0.0


def test_get_session_combines_resources(tmp_path):
    _write_transcript(tmp_path, "job-1")
    service = AudioAssistService(str(tmp_path))
    service.save_summary_version("job-1", "纪要")

    session = service.get_session("job-1")

    assert session["session"]["job_id"] == "job-1"
    assert session["state"]["status"] == "completed"
    assert session["transcript"]["segments"][0]["text"] == "测试内容"
    assert session["summary_versions"][0]["text"] == "纪要"


@pytest.mark.parametrize("job_id", ["", ".", "..", "../secret", "a/b", "含中文"])
def test_invalid_job_ids_are_rejected(tmp_path, job_id):
    with pytest.raises(InvalidJobIdError):
        AudioAssistService(str(tmp_path)).get_status(job_id)


def test_summarize_requires_existing_transcript(tmp_path):
    with pytest.raises(SessionNotFoundError):
        AudioAssistService(str(tmp_path)).summarize("missing", "prompt")


def test_summarize_requires_api_configuration(tmp_path):
    _write_transcript(tmp_path, "job-1")

    with pytest.raises(ConfigurationError):
        AudioAssistService(str(tmp_path), str(tmp_path / "missing-config.json")).summarize(
            "job-1", "prompt"
        )


def test_summarize_passes_config_text_and_language_instruction(tmp_path):
    _write_transcript(tmp_path, "job-1")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "api": {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "local-model",
        }
    }), encoding="utf-8")
    captured = {}

    def fake_summary(text, prompt, base_url, api_key, model, stream):
        captured.update(locals())
        return "summary"

    with patch("src.summary.summarize", side_effect=fake_summary):
        result = AudioAssistService(str(tmp_path), str(config)).summarize(
            "job-1", "Make notes", stream=False
        )

    assert result == "summary"
    assert captured["text"] == "测试内容"
    assert captured["prompt"].startswith("请用中文生成纪要。")
    assert captured["base_url"] == "http://localhost:1234/v1"
    assert captured["api_key"] == "test-key"
    assert captured["model"] == "local-model"
    assert captured["stream"] is False


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        ("en", "hello", "English"),
        ("zh", "hello", "中文"),
        ("", "这是中文内容" * 5, "中文"),
        ("unknown", "plain latin text", ""),
    ],
)
def test_language_instruction(language, text, expected):
    instruction = language_instruction(language, text)
    assert expected in instruction


def test_language_instruction_accepts_full_backend_language_name():
    assert "English" in language_instruction("English", "plain English text")


def test_language_instruction_for_mixed_text_preserves_both_languages():
    instruction = language_instruction(
        "mixed", "今天介绍 CoWoS. This section explains advanced packaging."
    )
    assert "主要语言" in instruction


def test_history_normalizes_legacy_language_without_rewriting_file(tmp_path):
    _write_transcript(tmp_path, "legacy-language")
    path = tmp_path / "meetings" / "legacy-language" / "transcript.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["language"] = "English"
    data["segments"][0]["text"] = "这是一段中文历史转写"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    sessions = AudioAssistService(str(tmp_path)).list_sessions()

    assert sessions[0]["language"] == "zh"
    assert json.loads(path.read_text(encoding="utf-8"))["language"] == "English"


def test_transcribe_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        AudioAssistService(str(tmp_path)).transcribe(str(tmp_path / "missing.wav"))


def test_transcribe_resolves_whisper_engine_from_model_id(tmp_path):
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"RIFF")
    captured = {}

    def fake_pipeline(audio_path, output_dir, **kwargs):
        captured.update(kwargs)
        json_path = os.path.join(output_dir, "transcript.json")
        markdown_path = os.path.join(output_dir, "transcript.md")
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump({"audio": audio_path, "segments": []}, file)
        with open(markdown_path, "w", encoding="utf-8") as file:
            file.write("# Transcript")
        return json_path, markdown_path

    with patch("src.pipeline.run", side_effect=fake_pipeline):
        result = AudioAssistService(str(tmp_path / "output")).transcribe(
            str(source), model_id="whisper-large-v3-turbo"
        )

    assert result["engine"] == "whisper"
    assert captured["engine"] == "whisper"


def test_transcribe_preserves_existing_display_name(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF")
    output = tmp_path / "output"
    session_dir = output / "meetings" / "job-1"
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"filename": "用户重命名"}, ensure_ascii=False), encoding="utf-8"
    )

    def fake_pipeline(audio_path, output_dir, **_kwargs):
        json_path = os.path.join(output_dir, "transcript.json")
        markdown_path = os.path.join(output_dir, "transcript.md")
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump({"audio": audio_path, "segments": []}, file)
        with open(markdown_path, "w", encoding="utf-8") as file:
            file.write("# Transcript")
        return json_path, markdown_path

    with patch("src.pipeline.run", side_effect=fake_pipeline):
        AudioAssistService(str(output)).transcribe(str(source), job_id="job-1")

    transcript = json.loads(
        (session_dir / "transcript.json").read_text(encoding="utf-8")
    )
    assert transcript["filename"] == "用户重命名"


def test_corrupt_summary_file_recovers_on_save(tmp_path):
    session_dir = tmp_path / "meetings" / "job-1"
    session_dir.mkdir(parents=True)
    summary_path = session_dir / "summary.json"
    summary_path.write_text("invalid", encoding="utf-8")

    AudioAssistService(str(tmp_path)).save_summary_version("job-1", "recovered")

    assert json.loads(summary_path.read_text(encoding="utf-8"))[0]["text"] == "recovered"


def test_concurrent_summary_writes_remain_valid_and_capped(tmp_path):
    session_dir = tmp_path / "meetings" / "job-1"
    session_dir.mkdir(parents=True)
    service = AudioAssistService(str(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(service.save_summary_version, "job-1", f"summary-{index}")
            for index in range(20)
        ]
        for future in futures:
            future.result()

    versions = service.get_summary_versions("job-1")
    assert len(versions) == 3
    assert all(version["text"].startswith("summary-") for version in versions)
