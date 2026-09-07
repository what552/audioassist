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
    def test_immediate_cancel_is_registered_before_worker_starts(self, env, tmp_path):
        api, _ = env
        src = tmp_path / "immediate.mp3"
        src.write_bytes(b"x")
        worker = {}
        pushes = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                worker["target"] = target

            def start(self):
                return None

        with patch.object(app_module.threading, "Thread", DeferredThread), \
             patch.object(app_module, "_push", side_effect=pushes.append):
            result = api.transcribe(str(src), {})
            assert api.cancel_transcription(result["job_id"]) is True
            worker["target"]()

        assert any("onTranscribeCancel" in call for call in pushes)
        assert api.cancel_transcription(result["job_id"]) is False

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

    def test_duplicate_job_id_is_rejected_while_running(self, env, tmp_path):
        api, _ = env
        src = tmp_path / "duplicate.mp3"
        src.write_bytes(b"x")
        started = threading.Event()
        release = threading.Event()

        def _pipeline(audio_path, output_dir, job_id, progress_callback=None, **kw):
            started.set()
            assert release.wait(2)
            if progress_callback:
                progress_callback(0.5, "working")
            return _fake_pipeline_slow(audio_path, output_dir, job_id, progress_callback, **kw)

        with patch("src.pipeline.run", side_effect=_pipeline), \
             patch("src.preferences.default_backend", return_value="pytorch-cpu"), \
             patch.object(app_module, "_push"):
            first = api.transcribe(str(src), {}, job_id="same-job")
            assert started.wait(2)
            second = api.transcribe(str(src), {}, job_id="same-job")
            assert second == {"error": "job_in_progress", "job_id": "same-job"}
            assert first == {
                "job_id": "same-job",
                "qwen_backend": "pytorch-cpu",
            }
            assert api.cancel_transcription("same-job") is True
            release.set()
            deadline = time.monotonic() + 2
            while api.cancel_transcription("same-job") and time.monotonic() < deadline:
                time.sleep(0.01)

        assert api.cancel_transcription("same-job") is False

    def test_cancel_after_pipeline_output_preserves_previous_transcript(self, env, tmp_path):
        api, out_dir = env
        job_id = "existing-job"
        src = tmp_path / "rerun.mp3"
        src.write_bytes(b"x")
        session_dir = out_dir / "meetings" / job_id
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "transcript.json"
        original = {
            "job_id": job_id,
            "filename": "Original",
            "audio": str(src),
            "language": "en",
            "created_at": "",
            "segments": [{"speaker": "A", "start": 0, "end": 1, "text": "old", "words": []}],
        }
        transcript_path.write_text(json.dumps(original), encoding="utf-8")
        written = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()

        def _pipeline(audio_path, output_dir, job_id, progress_callback=None, **kw):
            json_path = os.path.join(output_dir, "transcript.json")
            md_path = os.path.join(output_dir, "transcript.md")
            with open(json_path, "w", encoding="utf-8") as file:
                json.dump({**original, "segments": [{**original["segments"][0], "text": "new"}]}, file)
            open(md_path, "w").close()
            written.set()
            assert release.wait(2)
            return json_path, md_path

        def _push(call):
            if "onTranscribeCancel" in call:
                cancelled.set()

        with patch("src.pipeline.run", side_effect=_pipeline), \
             patch.object(app_module, "_push", side_effect=_push):
            api.transcribe(str(src), {}, job_id=job_id)
            assert written.wait(2)
            assert api.cancel_transcription(job_id) is True
            release.set()
            assert cancelled.wait(2)

        assert json.loads(transcript_path.read_text(encoding="utf-8")) == original
        assert not list(session_dir.glob(".transcribe-*"))
