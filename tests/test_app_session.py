"""Tests for rename_session and delete_session API methods."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestRenameSession(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.job_id = "test-rename-job"
        self.path = os.path.join(self.tmpdir, f"{self.job_id}.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"filename": "original.mp3", "segments": []}, f)

        from app import API, OUTPUT_DIR
        import app as app_module
        self._orig_output_dir = app_module.OUTPUT_DIR
        app_module.OUTPUT_DIR = self.tmpdir
        self.api = API()

    def tearDown(self):
        import app as app_module
        app_module.OUTPUT_DIR = self._orig_output_dir

    def test_rename_updates_filename_in_json(self):
        ok = self.api.rename_session(self.job_id, "new name")
        self.assertTrue(ok)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["filename"], "new name")

    def test_rename_preserves_other_fields(self):
        # Write extra data
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"filename": "orig", "language": "zh", "segments": [1, 2]}, f)
        self.api.rename_session(self.job_id, "updated")
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["language"], "zh")
        self.assertEqual(data["segments"], [1, 2])

    def test_rename_returns_false_for_missing_job(self):
        ok = self.api.rename_session("nonexistent-id", "name")
        self.assertFalse(ok)

    def test_rename_writes_atomically(self):
        """Ensure a .tmp file is not left behind after rename."""
        self.api.rename_session(self.job_id, "atomic")
        tmp = self.path + ".tmp"
        self.assertFalse(os.path.exists(tmp))


class TestDeleteSession(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.job_id = "test-delete-job"
        self.path = os.path.join(self.tmpdir, f"{self.job_id}.json")
        self.summary_path = os.path.join(self.tmpdir, f"{self.job_id}_summary.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"filename": "some.mp3"}, f)

        from app import API
        import app as app_module
        self._orig_output_dir = app_module.OUTPUT_DIR
        app_module.OUTPUT_DIR = self.tmpdir
        self.api = API()

    def tearDown(self):
        import app as app_module
        app_module.OUTPUT_DIR = self._orig_output_dir

    def test_delete_removes_transcript_file(self):
        ok = self.api.delete_session(self.job_id)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.path))

    def test_delete_also_removes_summary_file_when_present(self):
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(self.summary_path))

    def test_delete_succeeds_without_summary_file(self):
        # No summary file — should not raise
        ok = self.api.delete_session(self.job_id)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.path))

    def test_delete_returns_false_for_missing_job(self):
        ok = self.api.delete_session("nonexistent-id")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
