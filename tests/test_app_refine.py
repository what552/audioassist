"""
Tests for high-accuracy background re-transcription (refine) feature.

Flow:
  1. stop_realtime() stores pre-collected segments in _rt_segments.
  2. transcribe(wav_path, {}) pops segments → runs diarize-only (Phase 1).
  3. Pushes onTranscribeComplete(jobId, jsonPath, True)  ← hasRefine=True
  4. Spawns a background refine thread → runs full pipeline_run().
  5. Refine thread overwrites JSON, pushes onTranscribeRefined(jobId).
"""
from __future__ import annotations
import json
import os
import tempfile
import time
import pytest
from unittest.mock import MagicMock, patch

import app as app_module
from app import API


def _wait(secs: float = 0.5):
    time.sleep(secs)


def _make_fake_diarize_only(out_dir: str, segs: list[dict] | None = None):
    """Return a fake run_realtime_segments that writes a minimal JSON."""
    def _fn(segments, wav_path, output_dir, **kw):
        job_id = kw.get("job_id", "test-job")
        jp = os.path.join(output_dir, f"{job_id}.json")
        mp = os.path.join(output_dir, f"{job_id}.md")
        os.makedirs(output_dir, exist_ok=True)
        data = {
            "audio": wav_path,
            "filename": os.path.basename(wav_path),
            "language": "zh",
            "created_at": "",
            "segments": segs or [{"speaker": "SPEAKER_00", "start": 0.0,
                                   "end": 1.0, "text": "draft", "words": []}],
        }
        with open(jp, "w") as f:
            json.dump(data, f)
        open(mp, "w").close()
        return jp, mp
    return _fn


def _make_fake_pipeline_run(out_dir: str, refined_text: str = "refined"):
    """Return a fake pipeline.run that writes a refined JSON."""
    def _fn(audio_path, output_dir, *, job_id="test-job", **kw):
        jp = os.path.join(output_dir, f"{job_id}.json")
        mp = os.path.join(output_dir, f"{job_id}.md")
        os.makedirs(output_dir, exist_ok=True)
        data = {
            "audio": audio_path,
            "filename": os.path.basename(audio_path),
            "language": "zh",
            "created_at": "",
            "segments": [{"speaker": "SPEAKER_00", "start": 0.0,
                           "end": 1.0, "text": refined_text, "words": []}],
        }
        with open(jp, "w") as f:
            json.dump(data, f)
        open(mp, "w").close()
        return jp, mp
    return _fn


# ── onTranscribeComplete hasRefine flag ───────────────────────────────────────

class TestHasRefineFlag:
    def test_complete_includes_true_for_rt_segments(self):
        """onTranscribeComplete(..., True) pushed when rt_segments present."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            js_calls: list[str] = []
            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch("src.pipeline.run",
                       side_effect=_make_fake_pipeline_run(td)), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.8)

        complete_calls = [c for c in js_calls if "onTranscribeComplete" in c]
        assert len(complete_calls) == 1
        # Third arg must be true (JSON-serialised)
        assert "true" in complete_calls[0].lower()

    def test_complete_includes_false_for_file_transcription(self):
        """onTranscribeComplete(..., False) pushed for regular file jobs."""
        api = API()
        with tempfile.TemporaryDirectory() as td:
            mp3 = os.path.join(td, "audio.mp3")
            open(mp3, "wb").close()

            js_calls: list[str] = []
            mock_run = MagicMock(side_effect=_make_fake_pipeline_run(td))
            with patch("src.pipeline.run", mock_run), \
                 patch("os.path.isfile", return_value=True), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(mp3, {})
                _wait(0.5)

        complete_calls = [c for c in js_calls if "onTranscribeComplete" in c]
        assert len(complete_calls) == 1
        assert "false" in complete_calls[0].lower()


# ── onTranscribeRefined pushed ────────────────────────────────────────────────

class TestRefineTriggered:
    def test_onTranscribeRefined_pushed_after_rt_segments(self):
        """onTranscribeRefined is pushed after refine pipeline completes."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            js_calls: list[str] = []
            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch("src.pipeline.run",
                       side_effect=_make_fake_pipeline_run(td)), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.8)

        assert any("onTranscribeRefined" in c for c in js_calls)

    def test_onTranscribeRefined_not_pushed_for_file_transcription(self):
        """Regular file transcription must NOT push onTranscribeRefined."""
        api = API()
        with tempfile.TemporaryDirectory() as td:
            mp3 = os.path.join(td, "audio.mp3")
            open(mp3, "wb").close()

            js_calls: list[str] = []
            mock_run = MagicMock(side_effect=_make_fake_pipeline_run(td))
            with patch("src.pipeline.run", mock_run), \
                 patch("os.path.isfile", return_value=True), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(mp3, {})
                _wait(0.5)

        assert not any("onTranscribeRefined" in c for c in js_calls)

    def test_complete_pushed_before_refined(self):
        """onTranscribeComplete must arrive before onTranscribeRefined."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            js_calls: list[str] = []
            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch("src.pipeline.run",
                       side_effect=_make_fake_pipeline_run(td)), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.8)

        filtered = [c for c in js_calls
                    if "onTranscribeComplete" in c or "onTranscribeRefined" in c]
        assert len(filtered) >= 2
        assert "onTranscribeComplete" in filtered[0]
        assert "onTranscribeRefined" in filtered[-1]


# ── JSON overwritten with refined content ─────────────────────────────────────

class TestRefineOverwritesJson:
    def test_json_contains_refined_text_after_refine(self):
        """After refine completes, JSON on disk has the full-ASR content."""
        api = API()
        sample_segs = [{"text": "draft text", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            captured_job: list[str] = []

            def _fake_complete_cb(js: str):
                if "onTranscribeComplete" in js:
                    # extract job_id from first JSON string in the call
                    import re
                    m = re.search(r'"([0-9a-f-]{36})"', js)
                    if m:
                        captured_job.append(m.group(1))

            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch("src.pipeline.run",
                       side_effect=_make_fake_pipeline_run(td, refined_text="high quality")), \
                 patch.object(app_module, "_push",
                              side_effect=_fake_complete_cb), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.8)

            assert captured_job, "onTranscribeComplete not received"
            job_id = captured_job[0]
            json_path = os.path.join(td, f"{job_id}.json")
            assert os.path.exists(json_path)
            with open(json_path) as f:
                data = json.load(f)
            # Refined content should have overwritten the draft
            texts = [s["text"] for s in data.get("segments", [])]
            assert "high quality" in texts

    def test_refine_fail_does_not_crash_or_error_js(self):
        """If refine pipeline raises, no onTranscribeError is pushed."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            js_calls: list[str] = []

            def _boom(*args, **kwargs):
                raise RuntimeError("ASR engine crashed")

            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch("src.pipeline.run", side_effect=_boom), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.5)

        # onTranscribeComplete is pushed (draft is still good)
        assert any("onTranscribeComplete" in c for c in js_calls)
        # No error event from the refine failure
        assert not any("onTranscribeError" in c for c in js_calls)
