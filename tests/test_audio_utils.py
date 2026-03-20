"""Tests for src/audio_utils.py — to_wav, get_duration, _wav_needs_conversion, split_to_chunks."""
import os
import subprocess
from unittest.mock import MagicMock, patch, call
import pytest

from src.audio_utils import to_wav, get_duration, split_to_chunks, _wav_needs_conversion


# ── _wav_needs_conversion ─────────────────────────────────────────────────────

class TestWavNeedsConversion:
    def _mock_run(self, stdout="", returncode=0):
        m = MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        return m

    def test_already_16k_mono(self):
        with patch("subprocess.run", return_value=self._mock_run("16000,1")):
            assert _wav_needs_conversion("dummy.wav") is False

    def test_wrong_sample_rate(self):
        with patch("subprocess.run", return_value=self._mock_run("44100,1")):
            assert _wav_needs_conversion("dummy.wav") is True

    def test_wrong_channels(self):
        with patch("subprocess.run", return_value=self._mock_run("16000,2")):
            assert _wav_needs_conversion("dummy.wav") is True

    def test_ffprobe_failure(self):
        with patch("subprocess.run", return_value=self._mock_run("", returncode=1)):
            assert _wav_needs_conversion("dummy.wav") is True

    def test_malformed_output(self):
        # Only one field returned
        with patch("subprocess.run", return_value=self._mock_run("16000")):
            assert _wav_needs_conversion("dummy.wav") is True


# ── to_wav ────────────────────────────────────────────────────────────────────

class TestToWav:
    def test_wav_already_correct_skips_conversion(self, tmp_path):
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"RIFF fake wav")

        with patch("src.audio_utils._wav_needs_conversion", return_value=False):
            result_path, is_temp = to_wav(str(wav))

        assert result_path == str(wav)
        assert is_temp is False

    def test_wav_wrong_rate_triggers_conversion(self, tmp_path):
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"RIFF fake wav")

        ok = MagicMock()
        ok.returncode = 0

        with patch("src.audio_utils._wav_needs_conversion", return_value=True), \
             patch("subprocess.run", return_value=ok):
            result_path, is_temp = to_wav(str(wav))

        assert is_temp is True
        assert result_path.endswith(".wav")
        assert result_path != str(wav)

    def test_mp3_triggers_conversion(self, tmp_path):
        mp3 = tmp_path / "audio.mp3"
        mp3.write_bytes(b"fake mp3 data")

        ok = MagicMock()
        ok.returncode = 0

        with patch("subprocess.run", return_value=ok):
            result_path, is_temp = to_wav(str(mp3))

        assert is_temp is True
        assert result_path.endswith(".wav")

    def test_ffmpeg_failure_raises(self, tmp_path):
        mp4 = tmp_path / "audio.mp4"
        mp4.write_bytes(b"fake mp4 data")

        fail = MagicMock()
        fail.returncode = 1
        fail.stderr = b"some ffmpeg error"

        with patch("subprocess.run", return_value=fail):
            with pytest.raises(RuntimeError, match="ffmpeg failed"):
                to_wav(str(mp4))


# ── get_duration ──────────────────────────────────────────────────────────────

class TestGetDuration:
    def test_returns_float(self):
        ok = MagicMock()
        ok.returncode = 0
        ok.stdout = "123.456\n"

        with patch("subprocess.run", return_value=ok):
            assert get_duration("dummy.mp3") == pytest.approx(123.456)

    def test_ffprobe_failure_raises(self):
        fail = MagicMock()
        fail.returncode = 1
        fail.stderr = "no such file"

        with patch("subprocess.run", return_value=fail):
            with pytest.raises(RuntimeError, match="ffprobe failed"):
                get_duration("missing.mp3")

    def test_empty_stdout_raises(self):
        empty = MagicMock()
        empty.returncode = 0
        empty.stdout = "   \n"

        with patch("subprocess.run", return_value=empty):
            with pytest.raises(RuntimeError, match="empty output"):
                get_duration("silent.mp3")


# ── split_to_chunks ───────────────────────────────────────────────────────────

class TestSplitToChunks:
    def _ok(self):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        return m

    def test_short_file_returns_single_entry(self):
        with patch("src.audio_utils.get_duration", return_value=60.0):
            result = split_to_chunks("audio.wav", chunk_sec=300)
        assert result == [("audio.wav", 0.0)]

    def test_exact_boundary_returns_single_entry(self):
        with patch("src.audio_utils.get_duration", return_value=300.0):
            result = split_to_chunks("audio.wav", chunk_sec=300)
        assert result == [("audio.wav", 0.0)]

    def test_long_file_creates_multiple_chunks(self, tmp_path):
        with patch("src.audio_utils.get_duration", return_value=700.0), \
             patch("subprocess.run", return_value=self._ok()):
            result = split_to_chunks("audio.wav", chunk_sec=300)

        assert len(result) == 3
        offsets = [off for _, off in result]
        assert offsets == [0.0, 300.0, 600.0]

    def test_chunk_files_are_distinct_temp_paths(self, tmp_path):
        with patch("src.audio_utils.get_duration", return_value=700.0), \
             patch("subprocess.run", return_value=self._ok()):
            result = split_to_chunks("audio.wav", chunk_sec=300)

        paths = [p for p, _ in result]
        assert len(paths) == len(set(paths))  # all unique

    def test_ffmpeg_failure_on_first_chunk_cleans_up(self):
        import subprocess as sp

        with patch("src.audio_utils.get_duration", return_value=700.0), \
             patch("subprocess.run", side_effect=sp.CalledProcessError(1, "ffmpeg")):
            with pytest.raises(sp.CalledProcessError):
                split_to_chunks("audio.wav", chunk_sec=300)

    def test_ffmpeg_failure_on_second_chunk_cleans_up_first(self, tmp_path):
        import subprocess as sp

        call_count = 0
        first_chunk_path = None

        def _side_effect(cmd, **kwargs):
            nonlocal call_count, first_chunk_path
            call_count += 1
            if call_count == 1:
                # First chunk succeeds — record its path so we can check deletion
                first_chunk_path = cmd[-1]
                open(first_chunk_path, "w").close()  # create the file
                return self._ok()
            raise sp.CalledProcessError(1, "ffmpeg")

        with patch("src.audio_utils.get_duration", return_value=700.0), \
             patch("subprocess.run", side_effect=_side_effect):
            with pytest.raises(sp.CalledProcessError):
                split_to_chunks("audio.wav", chunk_sec=300)

        # First chunk should have been cleaned up
        assert first_chunk_path is not None
        assert not os.path.exists(first_chunk_path)
