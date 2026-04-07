"""
Tests for high-accuracy re-transcription (refine) feature.

Flow:
  1. stop_realtime() stores pre-collected segments in _rt_segments.
  2. transcribe(wav_path, {}) pops segments → runs diarize-only (Phase 1).
  3. Pushes onTranscribeComplete(jobId, jsonPath, False)  ← hasRefine always False now
  4. Stores options in api._refine_options[job_id] for optional manual refine.
  5. User clicks "高精度转写" → JS calls api.refine(job_id).
  6. refine() pops options, runs full pipeline in background.
  7. Overwrites JSON, pushes onTranscribeProgress events + onTranscribeRefined(jobId).
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
        stem = kw.get("output_stem", kw.get("job_id", "test-job"))
        jp = os.path.join(output_dir, f"{stem}.json")
        mp = os.path.join(output_dir, f"{stem}.md")
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
    def _fn(audio_path, output_dir, *, job_id="test-job", output_stem=None, **kw):
        stem = output_stem or job_id
        jp = os.path.join(output_dir, f"{stem}.json")
        mp = os.path.join(output_dir, f"{stem}.md")
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
    def test_complete_false_for_rt_segments(self):
        """Phase 1 always pushes onTranscribeComplete(..., False) — no auto-refine."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            js_calls: list[str] = []
            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch.object(app_module, "_push",
                              side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.5)

        complete_calls = [c for c in js_calls if "onTranscribeComplete" in c]
        assert len(complete_calls) == 1
        assert "false" in complete_calls[0].lower()

    def test_complete_false_for_file_transcription(self):
        """Regular file transcription also pushes onTranscribeComplete(..., False)."""
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

    def test_refine_options_stored_after_rt_transcribe(self):
        """_refine_options[job_id] populated after realtime Phase 1 completes."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav")
            open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            captured_job: list[str] = []
            def _spy(js: str):
                if "onTranscribeComplete" in js:
                    import re
                    m = re.search(r'"([0-9a-f-]{36})"', js)
                    if m:
                        captured_job.append(m.group(1))

            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch.object(app_module, "_push", side_effect=_spy), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.5)

        assert captured_job, "onTranscribeComplete not received"
        job_id = captured_job[0]
        assert job_id in api._refine_options
        opts = api._refine_options[job_id]
        assert opts["path"] == wav
        assert "json_path" in opts
        assert "s_dir" in opts

    def test_refine_options_not_stored_for_file_transcription(self):
        """_refine_options must NOT be populated for regular file jobs."""
        api = API()
        with tempfile.TemporaryDirectory() as td:
            mp3 = os.path.join(td, "audio.mp3")
            open(mp3, "wb").close()

            with patch("src.pipeline.run", side_effect=_make_fake_pipeline_run(td)), \
                 patch("os.path.isfile", return_value=True), \
                 patch.object(app_module, "_push"), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                result = api.transcribe(mp3, {})
                _wait(0.5)

        assert not api._refine_options


# ── Manual refine() API ───────────────────────────────────────────────────────

class TestManualRefine:
    def _setup_after_phase1(self, td: str, wav: str) -> tuple[API, str]:
        """Run Phase 1 transcription and return (api, job_id)."""
        api = API()
        api._rt_segments[wav] = [{"text": "draft", "start": 0.0, "end": 1.0}]

        captured_job: list[str] = []
        def _spy(js: str):
            if "onTranscribeComplete" in js:
                import re
                m = re.search(r'"([0-9a-f-]{36})"', js)
                if m:
                    captured_job.append(m.group(1))

        with patch("src.pipeline.run_realtime_segments",
                   side_effect=_make_fake_diarize_only(td)), \
             patch.object(app_module, "_push", side_effect=_spy), \
             patch.object(app_module, "OUTPUT_DIR", td):
            api.transcribe(wav, {})
            _wait(0.5)

        assert captured_job, "Phase 1 did not complete"
        return api, captured_job[0]

    def test_refine_returns_job_id(self):
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api, job_id = self._setup_after_phase1(td, wav)

            with patch("src.pipeline.run", side_effect=_make_fake_pipeline_run(td)), \
                 patch.object(app_module, "_push"), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                result = api.refine(job_id)
                _wait(0.5)

        assert result == {"job_id": job_id}

    def test_refine_returns_error_if_no_options(self):
        """Duplicate refine call (options already consumed) returns error."""
        api = API()
        result = api.refine("nonexistent-job-id")
        assert result == {"error": "no_refine_options"}

    def test_refine_options_cleared_after_call(self):
        """_refine_options entry is consumed (popped) by refine()."""
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api, job_id = self._setup_after_phase1(td, wav)

            with patch("src.pipeline.run", side_effect=_make_fake_pipeline_run(td)), \
                 patch.object(app_module, "_push"), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.refine(job_id)
                _wait(0.5)

        assert job_id not in api._refine_options

    def test_refine_pushes_onTranscribeRefined(self):
        """refine() must push onTranscribeRefined after pipeline completes."""
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api, job_id = self._setup_after_phase1(td, wav)

            js_calls: list[str] = []
            with patch("src.pipeline.run", side_effect=_make_fake_pipeline_run(td)), \
                 patch.object(app_module, "_push", side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.refine(job_id)
                _wait(0.5)

        assert any("onTranscribeRefined" in c for c in js_calls)

    def test_refine_pushes_progress_events(self):
        """refine() pushes onTranscribeProgress events from pipeline callback."""
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api, job_id = self._setup_after_phase1(td, wav)

            progress_calls: list[str] = []

            def _fake_pipeline(audio_path, output_dir, *, job_id="j", output_stem=None,
                               progress_callback=None, **kw):
                if progress_callback:
                    progress_callback(0.5, "halfway")
                    progress_callback(1.0, "done")
                return _make_fake_pipeline_run(td)(
                    audio_path, output_dir,
                    job_id=job_id, output_stem=output_stem, **kw
                )

            js_calls: list[str] = []
            with patch("src.pipeline.run", side_effect=_fake_pipeline), \
                 patch.object(app_module, "_push", side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.refine(job_id)
                _wait(0.5)

        progress = [c for c in js_calls if "onTranscribeProgress" in c]
        assert len(progress) >= 1

    def test_refine_overwrites_json_with_refined_content(self):
        """After refine(), on-disk JSON contains full-ASR text."""
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api, job_id = self._setup_after_phase1(td, wav)

            with patch("src.pipeline.run",
                       side_effect=_make_fake_pipeline_run(td, refined_text="high quality")), \
                 patch.object(app_module, "_push"), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.refine(job_id)
                _wait(0.5)

            json_path = os.path.join(td, "meetings", job_id, "transcript.json")
            assert os.path.exists(json_path)
            with open(json_path) as f:
                data = json.load(f)
        texts = [s["text"] for s in data.get("segments", [])]
        assert "high quality" in texts

    def test_refine_fail_pushes_onTranscribeRefined(self):
        """If refine pipeline raises, onTranscribeRefined is still pushed (UI unblocks)."""
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api, job_id = self._setup_after_phase1(td, wav)

            js_calls: list[str] = []

            def _boom(*args, **kwargs):
                raise RuntimeError("ASR engine crashed")

            with patch("src.pipeline.run", side_effect=_boom), \
                 patch.object(app_module, "_push", side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.refine(job_id)
                _wait(0.5)

        assert any("onTranscribeRefined" in c for c in js_calls)
        assert not any("onTranscribeError" in c for c in js_calls)

    def test_refine_no_auto_trigger_after_transcribe(self):
        """After Phase 1, onTranscribeRefined must NOT be pushed automatically."""
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "session.wav"); open(wav, "wb").close()
            api._rt_segments[wav] = sample_segs

            js_calls: list[str] = []
            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch.object(app_module, "_push", side_effect=js_calls.append), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.transcribe(wav, {})
                _wait(0.5)

        assert not any("onTranscribeRefined" in c for c in js_calls)


# ── Metadata durability across refine ────────────────────────────────────────

class TestRealtimeRenameDurability:
    def test_filename_and_job_id_survive_refine(self):
        """After refine(), transcript JSON still has the original filename and job_id."""
        api = API()
        sample_segs = [{"text": "draft text", "start": 0.0, "end": 1.0}]

        with tempfile.TemporaryDirectory() as td:
            rt_job = "076bf942-0000-0000-0000-000000000000"
            rt_dir = os.path.join(td, "meetings", rt_job)
            os.makedirs(rt_dir, exist_ok=True)
            wav = os.path.join(rt_dir, "realtime_recording_076bf942.wav")
            open(wav, "wb").close()
            with open(os.path.join(rt_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"filename": "Custom Title"}, f)

            api._rt_segments[wav] = sample_segs

            captured_job: list[str] = []
            def _spy(js: str):
                if "onTranscribeComplete" in js:
                    import re
                    m = re.search(r'"([0-9a-f-]{36})"', js)
                    if m:
                        captured_job.append(m.group(1))

            with patch("src.pipeline.run_realtime_segments",
                       side_effect=_make_fake_diarize_only(td)), \
                 patch.object(app_module, "_push", side_effect=_spy), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                result = api.transcribe(wav, {})
                job_id = result["job_id"]
                _wait(0.5)

            # Now trigger manual refine
            with patch("src.pipeline.run",
                       side_effect=_make_fake_pipeline_run(td, refined_text="high quality")), \
                 patch.object(app_module, "_push"), \
                 patch.object(app_module, "OUTPUT_DIR", td):
                api.refine(job_id)
                _wait(0.5)

            json_path = os.path.join(td, "meetings", job_id, "transcript.json")
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            with open(os.path.join(rt_dir, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)

            assert data["filename"] == "Custom Title"
            assert data["job_id"] == job_id
            assert data["segments"][0]["text"] == "high quality"
            assert meta["filename"] == "Custom Title"
            assert meta["transcribed_job_id"] == job_id
