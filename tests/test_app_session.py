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


class TestRenameSessionWavOnly(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self._orig_output_dir = app_module.OUTPUT_DIR
        app_module.OUTPUT_DIR = self.tmpdir
        from app import API
        self.api = API()
        self.job_id = "wav-only-job"
        self.wav_path = os.path.join(self.tmpdir, f"{self.job_id}.wav")
        self.meta_path = os.path.join(self.tmpdir, f"{self.job_id}_meta.json")
        with open(self.wav_path, "wb") as f:
            f.write(b"RIFF")

    def tearDown(self):
        import app as app_module
        app_module.OUTPUT_DIR = self._orig_output_dir

    def test_rename_wav_only_returns_true(self):
        ok = self.api.rename_session(self.job_id, "My Recording")
        self.assertTrue(ok)

    def test_rename_wav_only_writes_meta_json(self):
        self.api.rename_session(self.job_id, "My Recording")
        self.assertTrue(os.path.exists(self.meta_path))
        with open(self.meta_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["filename"], "My Recording")

    def test_rename_returns_false_when_neither_json_nor_wav(self):
        ok = self.api.rename_session("no-such-id", "name")
        self.assertFalse(ok)

    def test_rename_wav_only_meta_appears_in_history(self):
        self.api.rename_session(self.job_id, "Renamed")
        history = self.api.get_history()
        entry = next((h for h in history if h["job_id"] == self.job_id), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["filename"], "Renamed")


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


class TestDeleteSessionWavOnly(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self._orig_output_dir = app_module.OUTPUT_DIR
        app_module.OUTPUT_DIR = self.tmpdir
        from app import API
        self.api = API()
        self.job_id = "wav-delete-job"
        self.wav_path = os.path.join(self.tmpdir, f"{self.job_id}.wav")
        self.meta_path = os.path.join(self.tmpdir, f"{self.job_id}_meta.json")
        with open(self.wav_path, "wb") as f:
            f.write(b"RIFF")

    def tearDown(self):
        import app as app_module
        app_module.OUTPUT_DIR = self._orig_output_dir

    def test_delete_removes_wav_file(self):
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(self.wav_path))

    def test_delete_wav_only_returns_true(self):
        ok = self.api.delete_session(self.job_id)
        self.assertTrue(ok)

    def test_delete_also_removes_meta_json(self):
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump({"filename": "My Rec"}, f)
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(self.meta_path))


class TestDeleteSessionComplete(unittest.TestCase):
    """Verify delete_session() removes every sidecar file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self._orig_output_dir = app_module.OUTPUT_DIR
        app_module.OUTPUT_DIR = self.tmpdir
        from app import API
        self.api = API()
        self.job_id = "test-complete-delete"

    def tearDown(self):
        import app as app_module
        app_module.OUTPUT_DIR = self._orig_output_dir

    def _touch(self, name):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            f.write("x")
        return path

    def test_deletes_md_sidecar(self):
        self._touch(f"{self.job_id}.json")
        md = self._touch(f"{self.job_id}.md")
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(md))

    def test_deletes_chat_json(self):
        self._touch(f"{self.job_id}.json")
        chat = self._touch(f"{self.job_id}_agent_chat.json")
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(chat))

    def test_deletes_audio_mp3(self):
        self._touch(f"{self.job_id}.json")
        audio = self._touch(f"{self.job_id}_audio.mp3")
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(audio))

    def test_deletes_audio_m4a(self):
        self._touch(f"{self.job_id}.json")
        audio = self._touch(f"{self.job_id}_audio.m4a")
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(audio))

    def test_deletes_audio_mp4(self):
        self._touch(f"{self.job_id}.json")
        audio = self._touch(f"{self.job_id}_audio.mp4")
        self.api.delete_session(self.job_id)
        self.assertFalse(os.path.exists(audio))

    def test_deletes_all_sidecars_together(self):
        files = [
            self._touch(f"{self.job_id}.json"),
            self._touch(f"{self.job_id}.md"),
            self._touch(f"{self.job_id}_summary.json"),
            self._touch(f"{self.job_id}_meta.json"),
            self._touch(f"{self.job_id}_agent_chat.json"),
            self._touch(f"{self.job_id}_audio.mp3"),
        ]
        self.api.delete_session(self.job_id)
        for f in files:
            self.assertFalse(os.path.exists(f), f"{f} should have been deleted")

    def test_does_not_delete_unrelated_files(self):
        self._touch(f"{self.job_id}.json")
        unrelated = self._touch("other-job.json")
        self.api.delete_session(self.job_id)
        self.assertTrue(os.path.exists(unrelated))


if __name__ == "__main__":
    unittest.main()
