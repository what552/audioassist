"""Tests for app.API history/summary-version methods."""
import json
import os
import time
import pytest
from unittest.mock import patch

import app as app_module
from app import API


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path):
    """Patch OUTPUT_DIR to tmp_path; yield (api, tmp_path)."""
    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        yield API(), tmp_path


def _write_job(tmp_path, job_id, data):
    p = tmp_path / f"{job_id}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ── get_history ────────────────────────────────────────────────────────────────

class TestGetHistory:
    def test_empty_when_no_output_dir(self, tmp_path):
        missing = str(tmp_path / "nonexistent")
        with patch.object(app_module, "OUTPUT_DIR", missing):
            result = API().get_history()
        assert result == []

    def test_returns_job_entries(self, env):
        api, tmp = env
        _write_job(tmp, "job1", {
            "filename": "audio.mp3",
            "audio": "audio.mp3",
            "language": "zh",
            "created_at": "2026-03-22 10:00",
            "segments": [{"start": 0.0, "end": 5.0, "text": "hi", "words": []}],
        })
        result = api.get_history()
        assert len(result) == 1
        assert result[0]["job_id"] == "job1"
        assert result[0]["filename"] == "audio.mp3"
        assert result[0]["language"] == "zh"
        assert result[0]["date"] == "2026-03-22 10:00"
        assert result[0]["duration"] == 5

    def test_skips_summary_json_files(self, env):
        api, tmp = env
        _write_job(tmp, "job1", {
            "filename": "f.mp3", "language": "en", "created_at": "",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x", "words": []}],
        })
        # Write a _summary.json sidecar — must be ignored
        (tmp / "job1_summary.json").write_text(
            json.dumps([{"text": "summary", "created_at": "2026-03-22 10:00"}]),
            encoding="utf-8",
        )
        result = api.get_history()
        assert all(not r["job_id"].endswith("_summary") for r in result)
        assert len(result) == 1

    def test_tolerates_malformed_json(self, env):
        api, tmp = env
        (tmp / "bad.json").write_text("not json", encoding="utf-8")
        _write_job(tmp, "good", {
            "filename": "g.mp3", "language": "en", "created_at": "",
            "segments": [{"start": 0.0, "end": 2.0, "text": "x", "words": []}],
        })
        result = api.get_history()
        assert len(result) == 1
        assert result[0]["job_id"] == "good"

    def test_duration_zero_for_empty_segments(self, env):
        api, tmp = env
        _write_job(tmp, "empty", {
            "filename": "e.mp3", "language": "en", "created_at": "", "segments": [],
        })
        result = api.get_history()
        assert result[0]["duration"] == 0

    def test_falls_back_to_audio_field(self, env):
        api, tmp = env
        _write_job(tmp, "j2", {
            "audio": "fallback.mp3",
            "language": "en", "created_at": "",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x", "words": []}],
        })
        result = api.get_history()
        assert result[0]["filename"] == "fallback.mp3"

    def test_json_entry_returns_type_file(self, env):
        api, tmp = env
        _write_job(tmp, "j3", {
            "filename": "f.mp3", "audio": "/abs/path/f.mp3",
            "language": "en", "created_at": "2026-03-22 10:00",
            "segments": [],
        })
        result = api.get_history()
        assert result[0]["type"] == "file"

    def test_json_entry_returns_audio_path(self, env):
        api, tmp = env
        _write_job(tmp, "j4", {
            "filename": "f.mp3", "audio": "/abs/path/j4_audio.mp3",
            "language": "en", "created_at": "2026-03-22 10:00",
            "segments": [],
        })
        result = api.get_history()
        assert result[0]["audio_path"] == "/abs/path/j4_audio.mp3"

    def test_completed_session_reports_persisted_high_accuracy_availability(self, env):
        api, tmp = env
        job_id = "refine-ready"
        session_dir = tmp / "meetings" / job_id
        session_dir.mkdir(parents=True)
        source = session_dir / "source_audio.wav"
        source.write_bytes(b"RIFF")
        (session_dir / "transcript.json").write_text(json.dumps({
            "filename": "Recording", "audio": str(source), "language": "zh",
            "created_at": "2026-09-09 12:00", "segments": [{
                "speaker": "SPEAKER_00", "start": 0, "end": 1,
                "text": "draft", "words": [],
            }],
        }), encoding="utf-8")
        (session_dir / "meta.json").write_text(json.dumps({
            "high_accuracy_refine": {
                "available": True,
                "source_audio": str(source),
                "engine": "qwen",
                "model_id": "qwen3-asr-1.7b",
                "num_speakers": None,
                "qwen_backend": "mlx",
            },
        }), encoding="utf-8")

        result = api.get_history()

        assert result[0]["refine_available"] is True

    def test_missing_refine_audio_hides_high_accuracy_action(self, env):
        api, tmp = env
        job_id = "refine-missing-audio"
        session_dir = tmp / "meetings" / job_id
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.json").write_text(json.dumps({
            "filename": "Recording", "audio": "missing.wav", "language": "zh",
            "created_at": "2026-09-09 12:00", "segments": [],
        }), encoding="utf-8")
        (session_dir / "meta.json").write_text(json.dumps({
            "high_accuracy_refine": {
                "available": True,
                "source_audio": str(session_dir / "missing.wav"),
                "engine": "qwen",
            },
        }), encoding="utf-8")

        result = api.get_history()

        assert result[0]["refine_available"] is False

    def test_missing_refine_copy_uses_linked_recording(self, env):
        api, tmp = env
        job_id = "refine-linked-audio"
        recording_dir = tmp / "meetings" / "recording-source"
        recording_dir.mkdir(parents=True)
        recording = recording_dir / "realtime_recording_source.wav"
        recording.write_bytes(b"RIFF")
        (recording_dir / "meta.json").write_text(json.dumps({
            "transcribed_job_id": job_id,
        }), encoding="utf-8")

        session_dir = tmp / "meetings" / job_id
        session_dir.mkdir()
        missing_copy = session_dir / "source_audio.wav"
        (session_dir / "transcript.json").write_text(json.dumps({
            "filename": "Recording", "audio": str(missing_copy), "language": "zh",
            "created_at": "2026-09-09 12:00", "segments": [{
                "speaker": "SPEAKER_00", "start": 0, "end": 1,
                "text": "draft", "words": [],
            }],
        }), encoding="utf-8")
        (session_dir / "meta.json").write_text(json.dumps({
            "high_accuracy_refine": {
                "available": True,
                "source_audio": str(missing_copy),
                "engine": "qwen",
                "model_id": "qwen3-asr-1.7b",
                "num_speakers": None,
                "qwen_backend": "mlx",
            },
        }), encoding="utf-8")

        history = api.get_history()
        restored = app_module._service().get_high_accuracy_refine(job_id)

        assert history[0]["audio_path"] == str(recording)
        assert history[0]["refine_available"] is True
        assert restored is not None
        assert restored["path"] == str(recording)

    def test_legacy_realtime_quick_draft_recovers_high_accuracy_action(self, env):
        api, tmp = env
        recording_id = "legacy-recording"
        recording_dir = tmp / "meetings" / recording_id
        recording_dir.mkdir(parents=True)
        (recording_dir / "realtime_recording_legacy.wav").write_bytes(b"RIFF")
        target_id = "legacy-quick-draft"
        (recording_dir / "meta.json").write_text(json.dumps({
            "transcribed_job_id": target_id,
        }), encoding="utf-8")
        target_dir = tmp / "meetings" / target_id
        target_dir.mkdir()
        source = target_dir / "source_audio.wav"
        source.write_bytes(b"RIFF")
        (target_dir / "transcript.json").write_text(json.dumps({
            "filename": "Old recording", "audio": str(source), "language": "zh",
            "created_at": "2026-09-08 12:00", "segments": [{
                "speaker": "SPEAKER_00", "start": 0, "end": 1,
                "text": "draft", "words": [],
            }],
        }), encoding="utf-8")

        history = api.get_history()
        restored = app_module._service().get_high_accuracy_refine(target_id)
        without_model = api.refine(target_id)

        assert history[0]["refine_available"] is True
        assert restored is not None
        assert restored["path"] == str(source)
        assert restored["engine"] is None
        assert restored["legacy_inferred"] is True
        assert without_model == {"error": "legacy_refine_model_required"}

    def test_legacy_realtime_full_qwen_output_is_not_treated_as_quick_draft(self, env):
        api, tmp = env
        recording_dir = tmp / "meetings" / "legacy-recording"
        recording_dir.mkdir(parents=True)
        (recording_dir / "realtime_recording_legacy.wav").write_bytes(b"RIFF")
        target_id = "legacy-full-output"
        (recording_dir / "meta.json").write_text(json.dumps({
            "transcribed_job_id": target_id,
        }), encoding="utf-8")
        target_dir = tmp / "meetings" / target_id
        target_dir.mkdir()
        source = target_dir / "source_audio.wav"
        source.write_bytes(b"RIFF")
        (target_dir / "transcript.json").write_text(json.dumps({
            "filename": "Already refined", "audio": str(source), "language": "zh",
            "qwen_backend": "mlx", "created_at": "2026-09-08 12:00", "segments": [],
        }), encoding="utf-8")

        history = api.get_history()

        assert history[0]["refine_available"] is False

    def test_legacy_aligned_output_without_backend_is_not_treated_as_quick_draft(self, env):
        api, tmp = env
        recording_dir = tmp / "meetings" / "legacy-recording"
        recording_dir.mkdir(parents=True)
        (recording_dir / "realtime_recording_legacy.wav").write_bytes(b"RIFF")
        target_id = "legacy-whisper-output"
        (recording_dir / "meta.json").write_text(json.dumps({
            "transcribed_job_id": target_id,
        }), encoding="utf-8")
        target_dir = tmp / "meetings" / target_id
        target_dir.mkdir()
        source = target_dir / "source_audio.wav"
        source.write_bytes(b"RIFF")
        (target_dir / "transcript.json").write_text(json.dumps({
            "filename": "Aligned output", "audio": str(source), "language": "en",
            "created_at": "2026-09-08 12:00", "segments": [{
                "speaker": "SPEAKER_00", "start": 0, "end": 1, "text": "done",
                "words": [{"word": "done", "start": 0, "end": 1}],
            }],
        }), encoding="utf-8")

        history = api.get_history()

        assert history[0]["refine_available"] is False


class TestGetHistoryWavEntries:
    def test_wav_without_json_appears_as_realtime(self, env):
        api, tmp = env
        (tmp / "abcd1234-0000-0000-0000-000000000000.wav").write_bytes(b"RIFF")
        result = api.get_history()
        assert len(result) == 1
        assert result[0]["type"] == "realtime"
        assert result[0]["filename"] == "abcd1234"  # first 8 chars of stem

    def test_wav_entry_audio_path_is_absolute(self, env):
        api, tmp = env
        (tmp / "deadbeef-0000-0000-0000-000000000000.wav").write_bytes(b"RIFF")
        result = api.get_history()
        assert os.path.isabs(result[0]["audio_path"])
        assert result[0]["audio_path"].endswith(".wav")

    def test_wav_with_existing_json_not_doubled(self, env):
        api, tmp = env
        job_id = "12345678-1234-1234-1234-123456789012"
        _write_job(tmp, job_id, {
            "filename": "rec.mp3", "language": "zh",
            "created_at": "2026-03-22 10:00",
            "segments": [{"start": 0.0, "end": 5.0, "text": "hi", "words": []}],
        })
        (tmp / f"{job_id}.wav").write_bytes(b"RIFF")
        result = api.get_history()
        assert len(result) == 1  # JSON entry only; WAV not double-counted
        assert result[0]["type"] == "file"

    def test_audio_copy_wavs_excluded(self, env):
        api, tmp = env
        (tmp / "somejob_audio.wav").write_bytes(b"RIFF")
        result = api.get_history()
        assert len(result) == 0  # _audio suffix → internal copy, skipped

    def test_wav_with_transcribed_job_id_excluded(self, env):
        """WAV with transcribed_job_id in _meta.json must not appear in history."""
        api, tmp = env
        stem = "realtime-0000-0000-0000-000000000000"
        (tmp / f"{stem}.wav").write_bytes(b"RIFF")
        (tmp / f"{stem}_meta.json").write_text(
            json.dumps({"filename": "My Rec", "transcribed_job_id": "some-new-uuid"}),
            encoding="utf-8",
        )
        result = api.get_history()
        assert len(result) == 0, "WAV with transcribed_job_id should be hidden"

    def test_wav_and_json_entries_combined_and_sorted(self, env):
        api, tmp = env
        # JSON job with an older date
        _write_job(tmp, "job-old", {
            "filename": "old.mp3", "audio": "/p/old.mp3",
            "language": "en", "created_at": "2026-03-01T08:00:00",
            "segments": [],
        })
        # Realtime WAV — mtime will be "now" (newer)
        (tmp / "wav-new-0-0000-0000-0000-000000000000.wav").write_bytes(b"RIFF")
        result = api.get_history()
        assert len(result) == 2
        # Newer (WAV) should come first
        assert result[0]["type"] == "realtime"
        assert result[1]["type"] == "file"

    def test_new_layout_realtime_unique_wav_name_appears(self, env):
        api, tmp = env
        job_id = "abcd1234-0000-0000-0000-000000000000"
        session_dir = tmp / "meetings" / job_id
        session_dir.mkdir(parents=True)
        wav = session_dir / "realtime_recording_abcd1234.wav"
        wav.write_bytes(b"RIFF")

        result = api.get_history()
        assert len(result) == 1
        assert result[0]["type"] == "realtime"
        assert result[0]["filename"] == "abcd1234"
        assert result[0]["audio_path"].endswith("realtime_recording_abcd1234.wav")

    def test_new_layout_realtime_legacy_wav_name_still_appears(self, env):
        api, tmp = env
        job_id = "deadbeef-0000-0000-0000-000000000000"
        session_dir = tmp / "meetings" / job_id
        session_dir.mkdir(parents=True)
        wav = session_dir / "realtime_recording.wav"
        wav.write_bytes(b"RIFF")

        result = api.get_history()
        assert len(result) == 1
        assert result[0]["type"] == "realtime"
        assert result[0]["filename"] == "deadbeef"
        assert result[0]["audio_path"].endswith("realtime_recording.wav")

    def test_interrupted_session_uses_persisted_rename(self, env):
        api, tmp = env
        from src.checkpoint import write as write_checkpoint

        job_id = "interrupted-renamed-job"
        session_dir = tmp / "meetings" / job_id
        write_checkpoint(str(session_dir), {
            "job_id": job_id,
            "source_audio": "/audio/original-name.wav",
            "status": "interrupted",
            "chunks": [],
            "completed_chunks": 0,
            "total_chunks": 1,
        })
        (session_dir / "meta.json").write_text(
            json.dumps({"filename": "讨论后的标题"}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = api.get_history()

        assert len(result) == 1
        assert result[0]["status"] == "interrupted"
        assert result[0]["filename"] == "讨论后的标题"


# ── get_summary_versions ───────────────────────────────────────────────────────

class TestGetSummaryVersions:
    def test_returns_empty_when_no_file(self, env):
        api, _ = env
        assert api.get_summary_versions("nonexistent") == []

    def test_returns_versions(self, env):
        api, tmp = env
        versions = [
            {"text": "v1 text", "created_at": "2026-03-22 09:00"},
            {"text": "v2 text", "created_at": "2026-03-22 10:00"},
        ]
        (tmp / "myjob_summary.json").write_text(
            json.dumps(versions), encoding="utf-8"
        )
        result = api.get_summary_versions("myjob")
        assert len(result) == 2
        assert result[0]["text"] == "v1 text"
        assert result[1]["text"] == "v2 text"


# ── save_summary_version ───────────────────────────────────────────────────────

class TestSaveSummaryVersion:
    def test_creates_file_with_first_version(self, env):
        api, tmp = env
        api.save_summary_version("job1", "First summary")

        path = tmp / "job1_summary.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["text"] == "First summary"
        assert "created_at" in data[0]

    def test_appends_new_version(self, env):
        api, tmp = env
        api.save_summary_version("job1", "v1")
        api.save_summary_version("job1", "v2")

        data = json.loads((tmp / "job1_summary.json").read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[1]["text"] == "v2"

    def test_keeps_at_most_3_versions(self, env):
        api, tmp = env
        for i in range(5):
            api.save_summary_version("job1", f"v{i}")

        data = json.loads((tmp / "job1_summary.json").read_text(encoding="utf-8"))
        assert len(data) == 3
        # Most recent 3
        assert data[0]["text"] == "v2"
        assert data[2]["text"] == "v4"

    def test_returns_true(self, env):
        api, _ = env
        assert api.save_summary_version("job1", "text") is True
