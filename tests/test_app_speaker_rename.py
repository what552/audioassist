"""Tests for app.API.rename_speaker and app.API.rename_segment_speaker."""
import json
import os
import pytest
from unittest.mock import patch

import app as app_module
from app import API


# ── Fixtures ──────────────────────────────────────────────────────────────────

SEGMENTS = [
    {"speaker": "SPEAKER_00", "start": 0.0,  "end": 2.5, "text": "Hello",   "words": []},
    {"speaker": "SPEAKER_01", "start": 3.0,  "end": 5.0, "text": "Hi",      "words": []},
    {"speaker": "SPEAKER_00", "start": 6.0,  "end": 8.0, "text": "Goodbye", "words": []},
]

INITIAL_JSON = {
    "audio":    "/path/to/audio.mp3",
    "language": "en",
    "segments": SEGMENTS,
}


@pytest.fixture
def job_dir(tmp_path):
    """Patch OUTPUT_DIR to a temp dir; yield (api, job_id, json_path, tmp_path)."""
    job_id = "test-rename-001"
    json_path = tmp_path / f"{job_id}.json"
    json_path.write_text(json.dumps(INITIAL_JSON), encoding="utf-8")

    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        yield API(), job_id, str(json_path), tmp_path


# ── rename_speaker (bulk) ─────────────────────────────────────────────────────


class TestRenameSpeakerBulk:
    def test_returns_ok_true(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_speaker(job_id, "SPEAKER_00", "Alice")
        assert res["ok"] is True

    def test_all_matching_segments_renamed(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_speaker(job_id, "SPEAKER_00", "Alice")
        saved = json.loads(open(json_path, encoding="utf-8").read())
        speakers = [s["speaker"] for s in saved["segments"]]
        assert speakers == ["Alice", "SPEAKER_01", "Alice"]

    def test_non_matching_segment_unchanged(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_speaker(job_id, "SPEAKER_00", "Alice")
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["segments"][1]["speaker"] == "SPEAKER_01"

    def test_segments_returned_in_response(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_speaker(job_id, "SPEAKER_00", "Alice")
        assert len(res["segments"]) == 3
        assert res["segments"][0]["speaker"] == "Alice"

    def test_metadata_preserved(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_speaker(job_id, "SPEAKER_00", "Alice")
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["audio"] == "/path/to/audio.mp3"
        assert saved["language"] == "en"

    def test_no_tmp_file_left_behind(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_speaker(job_id, "SPEAKER_00", "Alice")
        assert not os.path.exists(json_path + ".tmp")

    def test_returns_error_when_file_missing(self, tmp_path):
        with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
            res = API().rename_speaker("no-such-job", "SPEAKER_00", "Alice")
        assert res["ok"] is False
        assert "not found" in res["error"].lower()

    def test_returns_error_for_empty_new_speaker(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_speaker(job_id, "SPEAKER_00", "")
        assert res["ok"] is False
        assert "empty" in res["error"].lower()

    def test_returns_error_for_whitespace_only_name(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_speaker(job_id, "SPEAKER_00", "   ")
        assert res["ok"] is False

    def test_noop_when_old_speaker_not_found(self, job_dir):
        api, job_id, json_path, _ = job_dir
        res = api.rename_speaker(job_id, "GHOST", "Alice")
        assert res["ok"] is True
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert all(s["speaker"] != "Alice" for s in saved["segments"])


# ── rename_segment_speaker (single) ──────────────────────────────────────────


class TestRenameSegmentSpeaker:
    def test_returns_ok_true(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_segment_speaker(job_id, 1, "Bob")
        assert res["ok"] is True

    def test_only_target_segment_renamed(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_segment_speaker(job_id, 1, "Bob")
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["segments"][0]["speaker"] == "SPEAKER_00"
        assert saved["segments"][1]["speaker"] == "Bob"
        assert saved["segments"][2]["speaker"] == "SPEAKER_00"

    def test_segment_returned_in_response(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_segment_speaker(job_id, 1, "Bob")
        assert res["segment"]["speaker"] == "Bob"
        assert res["segment"]["text"] == "Hi"

    def test_first_segment(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_segment_speaker(job_id, 0, "Alice")
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["segments"][0]["speaker"] == "Alice"

    def test_last_segment(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_segment_speaker(job_id, 2, "Charlie")
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["segments"][2]["speaker"] == "Charlie"

    def test_no_tmp_file_left_behind(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.rename_segment_speaker(job_id, 0, "Alice")
        assert not os.path.exists(json_path + ".tmp")

    def test_returns_error_when_file_missing(self, tmp_path):
        with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
            res = API().rename_segment_speaker("no-such-job", 0, "Alice")
        assert res["ok"] is False
        assert "not found" in res["error"].lower()

    def test_returns_error_for_empty_new_speaker(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_segment_speaker(job_id, 0, "")
        assert res["ok"] is False
        assert "empty" in res["error"].lower()

    def test_returns_error_for_out_of_range_index(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_segment_speaker(job_id, 99, "Alice")
        assert res["ok"] is False
        assert "range" in res["error"].lower()

    def test_returns_error_for_negative_index(self, job_dir):
        api, job_id, _, _ = job_dir
        res = api.rename_segment_speaker(job_id, -1, "Alice")
        assert res["ok"] is False
