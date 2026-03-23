"""Tests for app.API.export_transcript and app.API.export_summary."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

import app as app_module
from app import API, _transcript_to_txt, _transcript_to_md, _fmt_time


# ── Fixtures ──────────────────────────────────────────────────────────────────

SEGMENTS = [
    {"speaker": "SPEAKER_00", "start": 0.0,  "end": 2.5, "text": "Hello world", "words": []},
    {"speaker": "SPEAKER_01", "start": 65.0, "end": 68.0, "text": "Hi there",   "words": []},
]

TRANSCRIPT_JSON = {
    "audio":    "/path/to/audio.mp3",
    "language": "en",
    "segments": SEGMENTS,
}

SUMMARY_TEXT = "## Summary\n\nThis is the meeting summary."


@pytest.fixture
def job_dir(tmp_path):
    job_id   = "test-export-001"
    json_path = tmp_path / f"{job_id}.json"
    json_path.write_text(json.dumps(TRANSCRIPT_JSON), encoding="utf-8")

    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        yield API(), job_id, str(json_path), tmp_path


@pytest.fixture
def mock_window(tmp_path):
    """Mock _window with a SAVE dialog that returns a path under tmp_path."""
    w = MagicMock()
    def _dialog(**kwargs):
        name = kwargs.get("save_filename", "out.txt")
        return [str(tmp_path / name)]
    w.create_file_dialog.side_effect = _dialog
    return w


# ── _fmt_time helper ──────────────────────────────────────────────────────────


class TestFmtTime:
    def test_zero(self):      assert _fmt_time(0) == "00:00"
    def test_seconds(self):   assert _fmt_time(65) == "01:05"
    def test_exact_min(self): assert _fmt_time(120) == "02:00"
    def test_large(self):     assert _fmt_time(3661) == "61:01"


# ── _transcript_to_txt ────────────────────────────────────────────────────────


class TestTranscriptToTxt:
    def test_format(self):
        lines = _transcript_to_txt(SEGMENTS).splitlines()
        assert lines[0] == "[00:00] SPEAKER_00: Hello world"
        assert lines[1] == "[01:05] SPEAKER_01: Hi there"

    def test_strips_text_whitespace(self):
        segs = [{"speaker": "A", "start": 0, "text": "  hi  ", "words": []}]
        line = _transcript_to_txt(segs)
        assert line.endswith(": hi")

    def test_empty_segments(self):
        assert _transcript_to_txt([]) == ""


# ── _transcript_to_md ─────────────────────────────────────────────────────────


class TestTranscriptToMd:
    def test_starts_with_title(self):
        md = _transcript_to_md(SEGMENTS)
        assert md.startswith("# Transcript")

    def test_speaker_bold(self):
        md = _transcript_to_md(SEGMENTS)
        assert "**00:00 SPEAKER_00**" in md
        assert "**01:05 SPEAKER_01**" in md

    def test_text_present(self):
        md = _transcript_to_md(SEGMENTS)
        assert "Hello world" in md
        assert "Hi there" in md

    def test_empty_segments(self):
        md = _transcript_to_md([])
        assert "# Transcript" in md


# ── export_transcript ─────────────────────────────────────────────────────────


class TestExportTranscript:
    def test_saves_txt(self, job_dir, tmp_path, mock_window):
        api, job_id, _, _ = job_dir
        with patch.object(app_module, "_window", mock_window):
            res = api.export_transcript(job_id, "txt")
        assert res["status"] == "saved"
        content = open(res["path"], encoding="utf-8").read()
        assert "[00:00] SPEAKER_00: Hello world" in content

    def test_saves_md(self, job_dir, tmp_path, mock_window):
        api, job_id, _, _ = job_dir
        with patch.object(app_module, "_window", mock_window):
            res = api.export_transcript(job_id, "md")
        assert res["status"] == "saved"
        content = open(res["path"], encoding="utf-8").read()
        assert "# Transcript" in content
        assert "**00:00 SPEAKER_00**" in content

    def test_cancelled_when_dialog_returns_none(self, job_dir, mock_window):
        api, job_id, _, _ = job_dir
        mock_window.create_file_dialog.side_effect = None
        mock_window.create_file_dialog.return_value = None
        with patch.object(app_module, "_window", mock_window):
            res = api.export_transcript(job_id, "txt")
        assert res["status"] == "cancelled"

    def test_cancelled_when_dialog_returns_empty(self, job_dir, mock_window):
        api, job_id, _, _ = job_dir
        mock_window.create_file_dialog.side_effect = None
        mock_window.create_file_dialog.return_value = []
        with patch.object(app_module, "_window", mock_window):
            res = api.export_transcript(job_id, "txt")
        assert res["status"] == "cancelled"

    def test_error_when_no_window(self, job_dir):
        api, job_id, _, _ = job_dir
        with patch.object(app_module, "_window", None):
            res = api.export_transcript(job_id, "txt")
        assert res["status"] == "error"

    def test_error_when_file_missing(self, tmp_path, mock_window):
        with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)), \
             patch.object(app_module, "_window", mock_window):
            res = API().export_transcript("no-such-job", "txt")
        assert res["status"] == "error"
        assert "not found" in res["error"].lower()

    def test_default_format_is_txt(self, job_dir, mock_window):
        api, job_id, _, _ = job_dir
        with patch.object(app_module, "_window", mock_window):
            res = api.export_transcript(job_id, "")
        assert res["status"] == "saved"
        assert res["path"].endswith("transcript.txt")

    def test_dialog_called_with_save_type(self, job_dir, mock_window):
        api, job_id, _, _ = job_dir
        import webview
        with patch.object(app_module, "_window", mock_window):
            api.export_transcript(job_id, "txt")
        call_kwargs = mock_window.create_file_dialog.call_args[1]
        assert call_kwargs["dialog_type"] == webview.FileDialog.SAVE

    def test_path_returned_in_response(self, job_dir, mock_window):
        api, job_id, _, _ = job_dir
        with patch.object(app_module, "_window", mock_window):
            res = api.export_transcript(job_id, "txt")
        assert "path" in res
        assert os.path.exists(res["path"])


# ── export_summary ────────────────────────────────────────────────────────────


class TestExportSummary:
    @pytest.fixture(autouse=True)
    def _with_summary(self, job_dir):
        api, job_id, _, tmp_path = job_dir
        self.api = api
        self.job_id = job_id
        self.tmp_path = tmp_path
        # seed a summary version
        summary_path = tmp_path / f"{job_id}_summary.json"
        summary_path.write_text(
            json.dumps([{"text": SUMMARY_TEXT, "created_at": "2026-03-23 10:00"}]),
            encoding="utf-8",
        )

    def test_saves_txt(self, mock_window):
        with patch.object(app_module, "_window", mock_window):
            res = self.api.export_summary(self.job_id, "txt")
        assert res["status"] == "saved"
        content = open(res["path"], encoding="utf-8").read()
        assert "Summary" in content

    def test_saves_md(self, mock_window):
        with patch.object(app_module, "_window", mock_window):
            res = self.api.export_summary(self.job_id, "md")
        assert res["status"] == "saved"
        content = open(res["path"], encoding="utf-8").read()
        assert content == SUMMARY_TEXT

    def test_returns_latest_version(self, mock_window, job_dir):
        api, job_id, _, tmp_path = job_dir
        # write two versions
        sp = tmp_path / f"{job_id}_summary.json"
        sp.write_text(json.dumps([
            {"text": "old version", "created_at": "2026-03-23 09:00"},
            {"text": "new version", "created_at": "2026-03-23 10:00"},
        ]), encoding="utf-8")
        with patch.object(app_module, "_window", mock_window):
            res = api.export_summary(job_id, "txt")
        content = open(res["path"], encoding="utf-8").read()
        assert content == "new version"

    def test_cancelled_when_dialog_returns_none(self, mock_window):
        mock_window.create_file_dialog.side_effect = None
        mock_window.create_file_dialog.return_value = None
        with patch.object(app_module, "_window", mock_window):
            res = self.api.export_summary(self.job_id, "txt")
        assert res["status"] == "cancelled"

    def test_error_when_no_window(self):
        with patch.object(app_module, "_window", None):
            res = self.api.export_summary(self.job_id, "txt")
        assert res["status"] == "error"

    def test_error_when_no_summary(self, tmp_path, mock_window):
        # job_id has no summary file
        with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)), \
             patch.object(app_module, "_window", mock_window):
            res = API().export_summary("empty-job", "txt")
        assert res["status"] == "error"
        assert "No summary" in res["error"]

    def test_default_format_is_txt(self, mock_window):
        with patch.object(app_module, "_window", mock_window):
            res = self.api.export_summary(self.job_id, "")
        assert res["status"] == "saved"
        assert res["path"].endswith("summary.txt")
