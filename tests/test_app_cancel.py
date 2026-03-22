"""Tests for app.API.cancel_transcription()."""
from __future__ import annotations

import json
import os
import time
import threading

import pytest
from unittest.mock import patch

import app as app_module
from app import API


def _wait(timeout: float = 0.6) -> None:
    time.sleep(timeout)


@pytest.fixture
def env(tmp_path):
    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        yield API(), tmp_path


def _fake_pipeline_slow(audio_path, output_dir, job_id, progress_callback=None, **_kw):
    """Pipeline that calls progress_callback 3 times with 0.1 s gaps."""
    for pct in (0.1, 0.3, 0.6):
        time.sleep(0.1)
        if progress_callback:
            progress_callback(pct, f"{int(pct * 100)}%")
    # Write output files if we get here (not cancelled)
    json_path = os.path.join(output_dir, f"{job_id}.json")
    md_path   = os.path.join(output_dir, f"{job_id}.md")
    data = {
        "audio": os.path.basename(audio_path),
        "filename": os.path.basename(audio_path),
        "language": "en", "created_at": "", "segments": [],
    }
    with open(json_path, "w") as f:
        json.dump(data, f)
    open(md_path, "w").close()
    return json_path, md_path


class TestCancelTranscription:
    def test_cancel_returns_false_when_no_job_running(self, env):
        api, _ = env
        assert api.cancel_transcription("nonexistent-job-id") is False

    def test_cancel_returns_true_when_job_is_running(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "audio.mp3"
        src.write_bytes(b"x")

        # Use slow pipeline so the job is still running when we cancel
        started = threading.Event()

        def _pipeline(audio_path, output_dir, job_id, progress_callback=None, **kw):
            started.set()
            return _fake_pipeline_slow(audio_path, output_dir, job_id, progress_callback, **kw)

        with patch("src.pipeline.run", side_effect=_pipeline), \
             patch.object(app_module, "_push"):
            result = api.transcribe(str(src), {})
            job_id = result["job_id"]
            started.wait(timeout=2)
            ok = api.cancel_transcription(job_id)

        assert ok is True

    def test_cancel_pushes_onTranscribeCancel(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "speech.mp3"
        src.write_bytes(b"x")

        pushes = []
        started = threading.Event()

        def _pipeline(audio_path, output_dir, job_id, progress_callback=None, **kw):
            started.set()
            return _fake_pipeline_slow(audio_path, output_dir, job_id, progress_callback, **kw)

        with patch("src.pipeline.run", side_effect=_pipeline), \
             patch.object(app_module, "_push", side_effect=pushes.append):
            result = api.transcribe(str(src), {})
            job_id = result["job_id"]
            started.wait(timeout=2)
            api.cancel_transcription(job_id)
            _wait(0.8)

        assert any("onTranscribeCancel" in p for p in pushes), (
            f"Expected onTranscribeCancel in pushes, got: {pushes}"
        )

    def test_cancel_does_not_push_error(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "talk.mp3"
        src.write_bytes(b"x")

        pushes = []
        started = threading.Event()

        def _pipeline(audio_path, output_dir, job_id, progress_callback=None, **kw):
            started.set()
            return _fake_pipeline_slow(audio_path, output_dir, job_id, progress_callback, **kw)

        with patch("src.pipeline.run", side_effect=_pipeline), \
             patch.object(app_module, "_push", side_effect=pushes.append):
            result = api.transcribe(str(src), {})
            job_id = result["job_id"]
            started.wait(timeout=2)
            api.cancel_transcription(job_id)
            _wait(0.8)

        assert not any("onTranscribeError" in p for p in pushes), (
            "Cancel should not push onTranscribeError"
        )

    def test_flag_removed_after_cancellation(self, env, tmp_path):
        """cancel_transcription() should return False once the job has finished."""
        api, out_dir = env
        src = tmp_path / "f.mp3"
        src.write_bytes(b"x")

        started = threading.Event()

        def _pipeline(audio_path, output_dir, job_id, progress_callback=None, **kw):
            started.set()
            return _fake_pipeline_slow(audio_path, output_dir, job_id, progress_callback, **kw)

        with patch("src.pipeline.run", side_effect=_pipeline), \
             patch.object(app_module, "_push"):
            result = api.transcribe(str(src), {})
            job_id = result["job_id"]
            started.wait(timeout=2)
            api.cancel_transcription(job_id)
            _wait(0.8)

        # After cancellation completes the flag is removed
        assert api.cancel_transcription(job_id) is False
