"""Tests for app.API.transcribe() audio-copy and JSON-patching behaviour."""
from __future__ import annotations

import json
import os
import time

import pytest
from unittest.mock import patch

import app as app_module
from app import API


def _wait(timeout: float = 0.4) -> None:
    time.sleep(timeout)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path):
    """Patch OUTPUT_DIR; yield (api, tmp_path)."""
    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        yield API(), tmp_path


def _fake_pipeline(audio_path, output_dir, job_id, **_kwargs):
    """Minimal pipeline stub: writes a JSON with basename 'audio' field."""
    json_path = os.path.join(output_dir, f"{job_id}.json")
    md_path   = os.path.join(output_dir, f"{job_id}.md")
    data = {
        "audio":      os.path.basename(audio_path),
        "filename":   os.path.basename(audio_path),
        "language":   "zh",
        "created_at": "2026-03-22 10:00",
        "segments":   [],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    open(md_path, "w").close()
    return json_path, md_path


# ── Audio copy ────────────────────────────────────────────────────────────────

class TestAudioCopy:
    def test_copy_created_in_output_dir(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "speech.mp3"
        src.write_bytes(b"fake-audio")

        with patch("src.pipeline.run", side_effect=_fake_pipeline), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        copies = list(out_dir.glob("*_audio.mp3"))
        assert len(copies) == 1, "Expected exactly one _audio.mp3 copy"

    def test_copy_has_same_content_as_source(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "rec.mp3"
        content = b"binary-audio-content-\x00\xff"
        src.write_bytes(content)

        with patch("src.pipeline.run", side_effect=_fake_pipeline), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        copy = next(out_dir.glob("*_audio.mp3"))
        assert copy.read_bytes() == content

    def test_copy_extension_matches_source(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "meeting.m4a"
        src.write_bytes(b"data")

        with patch("src.pipeline.run", side_effect=_fake_pipeline), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        assert list(out_dir.glob("*_audio.m4a")), "Expected *_audio.m4a copy"


# ── JSON audio field ──────────────────────────────────────────────────────────

class TestJsonAudioField:
    def _run_and_get_json(self, api, out_dir, src):
        job_ids = []

        def _capture_pipeline(audio_path, output_dir, job_id, **kw):
            job_ids.append(job_id)
            return _fake_pipeline(audio_path, output_dir, job_id, **kw)

        with patch("src.pipeline.run", side_effect=_capture_pipeline), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        json_path = out_dir / f"{job_ids[0]}.json"
        return json.loads(json_path.read_text(encoding="utf-8"))

    def test_audio_field_is_absolute_path(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "talk.mp3"
        src.write_bytes(b"x")
        data = self._run_and_get_json(api, out_dir, src)
        assert os.path.isabs(data["audio"]), (
            f"Expected absolute path in 'audio', got: {data['audio']!r}"
        )

    def test_audio_field_points_to_copy_file(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "talk.mp3"
        src.write_bytes(b"x")
        data = self._run_and_get_json(api, out_dir, src)
        assert data["audio"].endswith("_audio.mp3")
        assert os.path.isfile(data["audio"])

    def test_filename_field_keeps_original_basename(self, env, tmp_path):
        api, out_dir = env
        src = tmp_path / "interview.mp3"
        src.write_bytes(b"x")
        data = self._run_and_get_json(api, out_dir, src)
        # filename should remain the display name, not the internal copy name
        assert data["filename"] == "interview.mp3"


# ── Resilience ────────────────────────────────────────────────────────────────

class TestCopyResilience:
    def test_transcription_completes_if_copy_fails(self, env, tmp_path):
        """A copy failure must not prevent onTranscribeComplete from firing."""
        api, out_dir = env
        src = tmp_path / "file.mp3"
        src.write_bytes(b"x")
        pushes = []

        with patch("src.pipeline.run", side_effect=_fake_pipeline), \
             patch.object(app_module, "shutil") as mock_shutil, \
             patch.object(app_module, "_push", side_effect=pushes.append):
            mock_shutil.copy2.side_effect = OSError("disk full")
            api.transcribe(str(src), {})
            _wait()

        assert any("onTranscribeComplete" in p for p in pushes), (
            "onTranscribeComplete must be pushed even when audio copy fails"
        )
        assert not any("onTranscribeError" in p for p in pushes)


# ── WAV meta (transcribed_job_id) ─────────────────────────────────────────────

class TestWavTranscribedJobId:
    def test_meta_written_when_source_is_wav_in_output_dir(self, env, tmp_path):
        """transcribe() writes transcribed_job_id to _meta.json for realtime WAVs."""
        api, out_dir = env
        # Source WAV lives inside out_dir (simulates a realtime recording)
        src = out_dir / "abcd1234-0000-0000-0000-000000000000.wav"
        src.write_bytes(b"RIFF")

        job_ids = []

        def _capture(audio_path, output_dir, job_id, **kw):
            job_ids.append(job_id)
            return _fake_pipeline(audio_path, output_dir, job_id, **kw)

        with patch("src.pipeline.run", side_effect=_capture), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        meta_path = out_dir / "abcd1234-0000-0000-0000-000000000000_meta.json"
        assert meta_path.exists(), "_meta.json should be created for realtime WAV"
        import json as _json
        data = _json.loads(meta_path.read_text(encoding="utf-8"))
        assert data.get("transcribed_job_id") == job_ids[0]

    def test_meta_not_written_for_external_source(self, env, tmp_path):
        """transcribe() must NOT write meta when source is outside output dir."""
        import tempfile, pathlib
        api, out_dir = env
        # Use a completely separate temp directory so it's clearly not out_dir
        ext_dir = pathlib.Path(tempfile.mkdtemp())
        src = ext_dir / "external.wav"
        src.write_bytes(b"RIFF")

        with patch("src.pipeline.run", side_effect=_fake_pipeline), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        # No _meta.json for external sources
        metas = list(out_dir.glob("*_meta.json"))
        assert len(metas) == 0

    def test_meta_preserves_existing_fields(self, env, tmp_path):
        """transcribed_job_id is merged into existing _meta.json, not overwritten."""
        api, out_dir = env
        stem = "abcd1234-0000-0000-0000-000000000001"
        src = out_dir / f"{stem}.wav"
        src.write_bytes(b"RIFF")
        # Pre-existing meta with a custom filename
        meta_path = out_dir / f"{stem}_meta.json"
        import json as _json
        meta_path.write_text(_json.dumps({"filename": "My Recording"}), encoding="utf-8")

        with patch("src.pipeline.run", side_effect=_fake_pipeline), \
             patch.object(app_module, "_push"):
            api.transcribe(str(src), {})
            _wait()

        data = _json.loads(meta_path.read_text(encoding="utf-8"))
        assert data["filename"] == "My Recording", "existing fields must be preserved"
        assert "transcribed_job_id" in data
