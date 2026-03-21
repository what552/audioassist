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
