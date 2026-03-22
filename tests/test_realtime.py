"""Tests for src/realtime.py — VAD pipeline, audio callback, WAV writer."""
import sys
import types
import wave
import threading
import tempfile
import os
import pytest
from unittest.mock import MagicMock, patch, call

import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_torch():
    """Minimal torch stub: from_numpy returns a MagicMock with .float() → self."""
    mod = types.ModuleType("torch")
    def from_numpy(arr):
        t = MagicMock()
        t.float.return_value = t
        return t
    mod.from_numpy = from_numpy
    return mod


def _import_realtime():
    import importlib, src.realtime as m
    importlib.reload(m)
    return m


def _make_rt(**kwargs):
    """Create a RealtimeTranscriber with mocked internals (no model loading)."""
    with patch.dict(sys.modules, {"torch": _fake_torch(),
                                  "silero_vad": MagicMock(),
                                  "sounddevice": MagicMock()}):
        m = _import_realtime()
    rt = m.RealtimeTranscriber(**kwargs)
    rt._vad = MagicMock()
    rt._asr = MagicMock()
    rt._running = True
    return rt, m


# ── Constructor ───────────────────────────────────────────────────────────────

class TestDefaults:
    def test_default_engine(self):
        rt, _ = _make_rt()
        assert rt._engine == "qwen"

    def test_default_output_path_is_none(self):
        rt, _ = _make_rt()
        assert rt._output_path is None
        assert rt._wav_writer is None

    def test_initial_state(self):
        rt, _ = _make_rt()
        assert rt._speech_buffer == []
        assert rt._silence_count == 0
        assert rt._in_speech is False

    def test_callbacks_default_to_noop(self):
        rt, _ = _make_rt()
        rt._on_result("hello")  # should not raise
        rt._on_error("oops")

    def test_custom_callbacks(self):
        results, errors = [], []
        rt, _ = _make_rt(on_result=results.append, on_error=errors.append)
        rt._on_result("text")
        rt._on_error("msg")
        assert results == ["text"]
        assert errors == ["msg"]


# ── Audio callback — VAD branching ────────────────────────────────────────────

def _chunk(value=0.0):
    """Return a (512, 1) float32 indata array as sounddevice provides."""
    return np.full((512, 1), value, dtype=np.float32)


def _set_vad_prob(rt, prob):
    result = MagicMock()
    result.item.return_value = prob
    rt._vad.return_value = result


class TestAudioCallback:
    def test_speech_accumulated(self):
        rt, m = _make_rt()
        _set_vad_prob(rt, 0.9)
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)
        assert len(rt._speech_buffer) == 1
        assert rt._in_speech is True

    def test_silence_before_speech_ignored(self):
        rt, m = _make_rt()
        _set_vad_prob(rt, 0.1)
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)
        assert rt._speech_buffer == []
        assert rt._in_speech is False

    def test_silence_after_speech_counts_up(self):
        rt, m = _make_rt()
        rt._in_speech = True
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)]
        _set_vad_prob(rt, 0.1)
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)
        assert rt._silence_count == 1

    def test_silence_threshold_triggers_flush(self):
        rt, m = _make_rt()
        rt._in_speech = True
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * 10
        rt._silence_count = m.SILENCE_CHUNKS - 1
        _set_vad_prob(rt, 0.1)
        with patch.object(rt, "_flush_speech") as mock_flush, \
             patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)
        mock_flush.assert_called_once()

    def test_no_flush_before_threshold(self):
        rt, m = _make_rt()
        rt._in_speech = True
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * 5
        rt._silence_count = m.SILENCE_CHUNKS - 2
        _set_vad_prob(rt, 0.1)
        with patch.object(rt, "_flush_speech") as mock_flush, \
             patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)
        mock_flush.assert_not_called()

    def test_not_running_exits_early(self):
        rt, m = _make_rt()
        rt._running = False
        _set_vad_prob(rt, 0.9)
        with patch.object(rt, "_flush_speech") as mock_flush, \
             patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)
        assert rt._speech_buffer == []
        mock_flush.assert_not_called()

    def test_vad_exception_treated_as_silence(self):
        rt, m = _make_rt()
        rt._vad.side_effect = RuntimeError("vad boom")
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            # Should not raise; simply treated as silence
            rt._audio_callback(_chunk(), 512, None, None)
        assert rt._speech_buffer == []


# ── Flush speech ──────────────────────────────────────────────────────────────

class TestFlushSpeech:
    def test_clears_buffer_and_resets_state(self):
        rt, m = _make_rt()
        rt._in_speech = True
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * 10
        rt._silence_count = 5
        with patch.object(rt, "_transcribe_segment"):
            rt._flush_speech()
        assert rt._speech_buffer == []
        assert rt._silence_count == 0
        assert rt._in_speech is False

    def test_short_segment_skipped(self):
        rt, m = _make_rt()
        # Only MIN_SPEECH_CHUNKS - 1 frames — below minimum
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * (m.MIN_SPEECH_CHUNKS - 1)
        with patch.object(rt, "_transcribe_segment") as mock_tx:
            rt._flush_speech()
        mock_tx.assert_not_called()

    def test_long_enough_segment_spawns_thread(self):
        rt, m = _make_rt()
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * m.MIN_SPEECH_CHUNKS
        spawned = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            spawned.append(t)
            return t

        with patch("threading.Thread", side_effect=capture_thread):
            rt._flush_speech()
        assert len(spawned) == 1


# ── Transcribe segment ────────────────────────────────────────────────────────

class TestTranscribeSegment:
    def _run(self, rt, chunks, asr_text):
        from src.types import TranscriptResult
        rt._asr.transcribe.return_value = TranscriptResult(
            text=asr_text, language="en"
        )
        rt._transcribe_segment(chunks)

    def test_calls_on_result_with_text(self):
        results = []
        rt, _ = _make_rt(on_result=results.append)
        chunks = [np.zeros(512, dtype=np.float32)] * 5
        self._run(rt, chunks, "Hello world")
        assert results == ["Hello world"]

    def test_empty_text_not_delivered(self):
        results = []
        rt, _ = _make_rt(on_result=results.append)
        chunks = [np.zeros(512, dtype=np.float32)] * 5
        self._run(rt, chunks, "   ")  # whitespace-only
        assert results == []

    def test_asr_exception_calls_on_error(self):
        errors = []
        rt, _ = _make_rt(on_error=errors.append)
        rt._asr.transcribe.side_effect = RuntimeError("asr failed")
        chunks = [np.zeros(512, dtype=np.float32)] * 5
        rt._transcribe_segment(chunks)
        assert len(errors) == 1
        assert "asr failed" in errors[0]

    def test_temp_file_cleaned_up(self):
        rt, _ = _make_rt()
        from src.types import TranscriptResult
        rt._asr.transcribe.return_value = TranscriptResult(text="ok", language="en")
        created_paths = []
        original_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_paths.append(path)
            return fd, path

        with patch("tempfile.mkstemp", side_effect=tracking_mkstemp):
            rt._transcribe_segment([np.zeros(512, dtype=np.float32)] * 5)

        for p in created_paths:
            assert not os.path.exists(p), f"temp file not cleaned up: {p}"


# ── stop() flushes remaining buffer ──────────────────────────────────────────

class TestStop:
    def test_stop_flushes_pending_speech(self):
        rt, _ = _make_rt()
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * 10
        with patch.object(rt, "_flush_speech") as mock_flush:
            rt.stop()
        mock_flush.assert_called_once()

    def test_stop_skips_flush_when_buffer_empty(self):
        rt, _ = _make_rt()
        rt._stream = MagicMock()
        with patch.object(rt, "_flush_speech") as mock_flush:
            rt.stop()
        mock_flush.assert_not_called()

    def test_stop_sets_running_false(self):
        rt, _ = _make_rt()
        rt._stream = MagicMock()
        rt.stop()
        assert rt._running is False

    def test_stop_closes_stream(self):
        rt, _ = _make_rt()
        mock_stream = MagicMock()
        rt._stream = mock_stream
        rt.stop()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()
        assert rt._stream is None


# ── pause / resume ────────────────────────────────────────────────────────────

class TestPauseResume:
    def test_pause_sets_running_false(self):
        rt, _ = _make_rt()
        mock_stream = MagicMock()
        rt._stream = mock_stream
        rt.pause()
        assert rt._running is False

    def test_pause_stops_stream_without_closing(self):
        rt, _ = _make_rt()
        mock_stream = MagicMock()
        rt._stream = mock_stream
        rt.pause()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_not_called()
        assert rt._stream is mock_stream  # stream kept open

    def test_pause_flushes_speech_buffer(self):
        rt, _ = _make_rt()
        rt._stream = MagicMock()
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * 5
        with patch.object(rt, "_flush_speech") as mock_flush:
            rt.pause()
        mock_flush.assert_called_once()

    def test_pause_skips_flush_when_buffer_empty(self):
        rt, _ = _make_rt()
        rt._stream = MagicMock()
        rt._speech_buffer = []
        with patch.object(rt, "_flush_speech") as mock_flush:
            rt.pause()
        mock_flush.assert_not_called()

    def test_pause_without_stream_does_not_raise(self):
        rt, _ = _make_rt()
        assert rt._stream is None
        rt.pause()  # must not raise

    def test_resume_sets_running_true(self):
        rt, _ = _make_rt()
        rt._running = False
        rt._stream = MagicMock()
        rt.resume()
        assert rt._running is True

    def test_resume_starts_stream(self):
        rt, _ = _make_rt()
        mock_stream = MagicMock()
        rt._stream = mock_stream
        rt._running = False
        rt.resume()
        mock_stream.start.assert_called_once()

    def test_resume_resets_vad_state(self):
        rt, _ = _make_rt()
        rt._stream = MagicMock()
        rt._running = False
        rt._in_speech = True
        rt._silence_count = 7
        rt._speech_buffer = [np.zeros(512, dtype=np.float32)] * 3
        rt.resume()
        assert rt._in_speech is False
        assert rt._silence_count == 0
        assert rt._speech_buffer == []

    def test_resume_without_stream_does_nothing(self):
        rt, _ = _make_rt()
        rt._stream = None
        rt._running = False
        rt.resume()
        assert rt._running is False  # unchanged


# ── _load_models engine dispatch ─────────────────────────────────────────────

class TestLoadModels:
    def test_whisper_engine_uses_WhisperASREngine(self):
        """engine='whisper' path imports WhisperASREngine (not WhisperEngine)."""
        mock_whisper_cls = MagicMock()
        mock_asr_whisper_mod = types.ModuleType("src.asr_whisper")
        mock_asr_whisper_mod.WhisperASREngine = mock_whisper_cls

        mock_silero = types.ModuleType("silero_vad")
        mock_silero.load_silero_vad = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {
            "silero_vad": mock_silero,
            "src.asr_whisper": mock_asr_whisper_mod,
        }):
            import src.realtime as m
            rt = m.RealtimeTranscriber(engine="whisper")
            rt._load_models()

        mock_whisper_cls.assert_called_once_with()  # WhisperASREngine() — no with_timestamps
        mock_whisper_cls.return_value.load.assert_called_once()

    def test_qwen_engine_uses_ASREngine(self):
        """engine='qwen' (default) path imports ASREngine."""
        mock_asr_cls = MagicMock()
        mock_asr_mod = types.ModuleType("src.asr")
        mock_asr_mod.ASREngine = mock_asr_cls

        mock_silero = types.ModuleType("silero_vad")
        mock_silero.load_silero_vad = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {
            "silero_vad": mock_silero,
            "src.asr": mock_asr_mod,
        }):
            import src.realtime as m
            rt = m.RealtimeTranscriber(engine="qwen")
            rt._load_models()

        mock_asr_cls.assert_called_once_with(with_timestamps=False)
        mock_asr_cls.return_value.load.assert_called_once()


# ── _write_wav ────────────────────────────────────────────────────────────────

class TestWriteWav:
    def test_produces_valid_wav(self, tmp_path):
        import importlib
        m = _import_realtime()
        audio = np.zeros(1600, dtype=np.float32)
        path = str(tmp_path / "test.wav")
        m._write_wav(path, audio, 16000)
        with wave.open(path) as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 1600

    def test_clips_values_to_int16_range(self, tmp_path):
        m = _import_realtime()
        audio = np.array([2.0, -2.0, 0.5], dtype=np.float32)
        path = str(tmp_path / "clip.wav")
        m._write_wav(path, audio, 16000)
        with wave.open(path) as wf:
            import struct
            raw = wf.readframes(3)
            samples = struct.unpack("<3h", raw)
        assert samples[0] == 32767
        assert samples[1] == -32768
        assert samples[2] == pytest.approx(0.5 * 32767, abs=1)


# ── output WAV writer ─────────────────────────────────────────────────────────

class TestOutputWav:
    def test_audio_callback_writes_to_wav_writer(self):
        """Each audio chunk is written to _wav_writer when set."""
        rt, m = _make_rt()
        mock_wav = MagicMock()
        rt._wav_writer = mock_wav
        _set_vad_prob(rt, 0.1)  # silence — no VAD state change
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(0.5), 512, None, None)
        mock_wav.writeframes.assert_called_once()

    def test_audio_callback_no_wav_writer_does_not_raise(self):
        """_wav_writer=None by default — callback must not raise."""
        rt, m = _make_rt()
        assert rt._wav_writer is None
        _set_vad_prob(rt, 0.1)
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(), 512, None, None)

    def test_stop_closes_wav_writer(self):
        """stop() must close and clear _wav_writer."""
        rt, _ = _make_rt()
        mock_wav = MagicMock()
        rt._wav_writer = mock_wav
        rt._stream = MagicMock()
        rt.stop()
        mock_wav.close.assert_called_once()
        assert rt._wav_writer is None

    def test_stop_with_no_wav_writer_does_not_raise(self):
        """stop() without a wav writer must not raise."""
        rt, _ = _make_rt()
        rt._stream = MagicMock()
        assert rt._wav_writer is None
        rt.stop()  # must not raise

    def test_output_path_creates_file(self, tmp_path):
        """With output_path set, start() opens a valid WAV file."""
        wav_path = str(tmp_path / "session.wav")
        rt, m = _make_rt(output_path=wav_path)

        # Simulate what start() does for the WAV writer (models/stream already mocked)
        import wave as _wave
        parent = os.path.dirname(wav_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        rt._wav_writer = _wave.open(wav_path, "wb")
        rt._wav_writer.setnchannels(1)
        rt._wav_writer.setsampwidth(2)
        rt._wav_writer.setframerate(m.SAMPLE_RATE)

        # Write two chunks via callback
        _set_vad_prob(rt, 0.1)
        with patch.dict(sys.modules, {"torch": _fake_torch()}):
            rt._audio_callback(_chunk(0.0), 512, None, None)
            rt._audio_callback(_chunk(0.5), 512, None, None)

        # Close (as stop() would do)
        rt._wav_writer.close()
        rt._wav_writer = None

        with _wave.open(wav_path) as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == m.SAMPLE_RATE
            assert wf.getnframes() == 512 * 2  # two chunks
