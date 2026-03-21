"""Tests for app.API.save_transcript — JSON atomic write + .md sidecar."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

import app as app_module
from app import API


# ── Fixtures ──────────────────────────────────────────────────────────────────

EDITS = [
    {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.5, "text": "Hello world", "words": []},
    {"speaker": "SPEAKER_01", "start": 3.0, "end": 5.0, "text": "Hi there",    "words": []},
]

INITIAL_JSON = {
    "audio":    "/path/to/audio.mp3",
    "language": "en",
    "segments": [],
}


@pytest.fixture
def job_dir(tmp_path):
    """Patch OUTPUT_DIR to a temp directory and yield (api, job_id, json_path)."""
    job_id = "test-job-001"
    json_path = tmp_path / f"{job_id}.json"
    json_path.write_text(json.dumps(INITIAL_JSON), encoding="utf-8")

    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        yield API(), job_id, str(json_path), tmp_path


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSaveTranscriptReturnsValue:
    def test_returns_true_on_success(self, job_dir):
        api, job_id, _, _ = job_dir
        assert api.save_transcript(job_id, EDITS) is True

    def test_returns_false_when_file_missing(self, tmp_path):
        with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
            assert API().save_transcript("no-such-job", EDITS) is False


class TestSaveTranscriptJsonWrite:
    def test_segments_updated_in_file(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.save_transcript(job_id, EDITS)
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["segments"] == EDITS

    def test_existing_metadata_preserved(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.save_transcript(job_id, EDITS)
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["audio"] == "/path/to/audio.mp3"
        assert saved["language"] == "en"

    def test_no_tmp_file_left_behind(self, job_dir):
        api, job_id, json_path, tmp_path = job_dir
        api.save_transcript(job_id, EDITS)
        assert not os.path.exists(json_path + ".tmp")


class TestSaveTranscriptMarkdownSidecar:
    def test_md_file_created(self, job_dir):
        api, job_id, json_path, _ = job_dir
        api.save_transcript(job_id, EDITS)
        md_path = json_path[:-5] + ".md"
        assert os.path.exists(md_path)

    def test_md_failure_does_not_raise(self, job_dir):
        """to_markdown raising must be swallowed — save still returns True."""
        api, job_id, _, _ = job_dir
        with patch("src.merge.to_markdown", side_effect=RuntimeError("disk full")):
            result = api.save_transcript(job_id, EDITS)
        assert result is True

    def test_md_failure_json_still_written(self, job_dir):
        """Even when .md generation fails, JSON is persisted correctly."""
        api, job_id, json_path, _ = job_dir
        with patch("src.merge.to_markdown", side_effect=RuntimeError("disk full")):
            api.save_transcript(job_id, EDITS)
        saved = json.loads(open(json_path, encoding="utf-8").read())
        assert saved["segments"] == EDITS
