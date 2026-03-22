"""Tests for app.API.start_realtime / stop_realtime."""
import time
import pytest
from unittest.mock import MagicMock, patch

import app as app_module
from app import API


def _wait(timeout=0.5):
    time.sleep(timeout)


# ── Return values ─────────────────────────────────────────────────────────────

class TestReturnValues:
    def test_start_returns_started(self):
        api = API()
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push"):
            MockRT.return_value.start.return_value = None
            result = api.start_realtime()
        assert result["status"] == "started"

    def test_start_returns_already_running(self):
        api = API()
        api._realtime = MagicMock()  # simulate active session
        result = api.start_realtime()
        assert result["status"] == "already_running"

    def test_stop_returns_not_running(self):
        api = API()
        result = api.stop_realtime()
        assert result["status"] == "not_running"

    def test_stop_returns_stopped(self):
        api = API()
        api._realtime = MagicMock()
        with patch.object(app_module, "_push"):
            result = api.stop_realtime()
        assert result["status"] == "stopped"


# ── JS event push ─────────────────────────────────────────────────────────────

class TestJSEvents:
    def test_start_pushes_onRealtimeStarted(self):
        api = API()
        js_calls = []
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            MockRT.return_value.start.return_value = None
            api.start_realtime()
            _wait(0.3)
        assert any("onRealtimeStarted" in c for c in js_calls)

    def test_start_pushes_onRealtimeStarted_with_session_id_and_wav_path(self):
        """onRealtimeStarted must be called with a UUID session_id AND a wav path."""
        import re
        api = API()
        js_calls = []
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            MockRT.return_value.start.return_value = None
            api.start_realtime()
            _wait(0.3)
        started = [c for c in js_calls if "onRealtimeStarted" in c]
        assert len(started) == 1
        # Must contain UUID session_id and a wav path ending in .wav
        # e.g. onRealtimeStarted("uuid", "/path/to/uuid.wav")
        assert re.search(
            r'onRealtimeStarted\("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"',
            started[0],
        )
        assert '.wav"' in started[0]

    def test_start_exception_pushes_onRealtimeError(self):
        api = API()
        js_calls = []
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            MockRT.return_value.start.side_effect = RuntimeError("mic not found")
            api.start_realtime()
            _wait(0.3)
        assert any("onRealtimeError" in c for c in js_calls)
        assert any("mic not found" in c for c in js_calls)

    def test_start_exception_clears_realtime(self):
        api = API()
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push"):
            MockRT.return_value.start.side_effect = RuntimeError("boom")
            api.start_realtime()
            _wait(0.3)
        assert api._realtime is None

    def test_stop_pushes_onRealtimeStopped(self):
        api = API()
        api._realtime = MagicMock()
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.stop_realtime()
            _wait(0.3)
        assert any("onRealtimeStopped" in c for c in js_calls)

    def test_on_result_callback_pushes_onRealtimeResult(self):
        """Verify that the on_result callback passed to RealtimeTranscriber pushes JS."""
        api = API()
        js_calls = []
        captured_callbacks = {}

        def capture_init(*args, **kwargs):
            captured_callbacks["on_result"] = kwargs.get("on_result")
            captured_callbacks["on_error"]  = kwargs.get("on_error")
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            api.start_realtime()
            _wait(0.3)
            # Simulate a transcription result arriving — _push mock still active here
            if captured_callbacks.get("on_result"):
                captured_callbacks["on_result"]("Hello world")

        assert any("onRealtimeResult" in c for c in js_calls)
        assert any("Hello world" in c for c in js_calls)

    def test_on_error_callback_pushes_onRealtimeError(self):
        api = API()
        js_calls = []
        captured_callbacks = {}

        def capture_init(*args, **kwargs):
            captured_callbacks["on_error"] = kwargs.get("on_error")
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push", side_effect=js_calls.append):
            api.start_realtime()
            _wait(0.3)
            if captured_callbacks.get("on_error"):
                captured_callbacks["on_error"]("asr crashed")

        assert any("onRealtimeError" in c for c in js_calls)


# ── Race condition: stop_realtime() during model load ─────────────────────────

class TestRaceCondition:
    def test_stop_during_load_calls_rt_stop(self):
        """
        If stop_realtime() is called while start_realtime() is still loading
        models (before rt.start() returns), the background thread detects
        self._realtime is None and calls rt.stop() to abort cleanly.
        """
        api = API()
        rt_stopped = []

        def slow_start():
            # Simulate model loading time; stop_realtime clears _realtime meanwhile
            api._realtime = None  # mimic stop_realtime() called externally

        def capture_init(*args, **kwargs):
            inst = MagicMock()
            inst.start.side_effect = slow_start
            inst.stop.side_effect = lambda: rt_stopped.append(True)
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push"):
            api.start_realtime()
            _wait(0.3)

        assert rt_stopped, "rt.stop() must be called when stop_realtime() races with load"


# ── pause_realtime / resume_realtime ─────────────────────────────────────────

class TestPauseResumeAPI:
    def test_pause_returns_not_running_when_no_session(self):
        api = API()
        result = api.pause_realtime()
        assert result["status"] == "not_running"

    def test_resume_returns_not_running_when_no_session(self):
        api = API()
        result = api.resume_realtime()
        assert result["status"] == "not_running"

    def test_pause_returns_pausing(self):
        api = API()
        api._realtime = MagicMock(spec=["pause", "resume"])
        with patch.object(app_module, "_push"):
            result = api.pause_realtime()
        assert result["status"] == "pausing"

    def test_resume_returns_resuming(self):
        api = API()
        api._realtime = MagicMock(spec=["pause", "resume"])
        with patch.object(app_module, "_push"):
            result = api.resume_realtime()
        assert result["status"] == "resuming"

    def test_pause_calls_rt_pause_and_pushes_onRealtimePaused(self):
        api = API()
        mock_rt = MagicMock(spec=["pause", "resume"])
        api._realtime = mock_rt
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.pause_realtime()
            _wait(0.3)
        mock_rt.pause.assert_called_once()
        assert any("onRealtimePaused" in c for c in js_calls)

    def test_resume_calls_rt_resume_and_pushes_onRealtimeResumed(self):
        api = API()
        mock_rt = MagicMock(spec=["pause", "resume"])
        api._realtime = mock_rt
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.resume_realtime()
            _wait(0.3)
        mock_rt.resume.assert_called_once()
        assert any("onRealtimeResumed" in c for c in js_calls)

    def test_pause_exception_still_pushes_onRealtimePaused(self):
        api = API()
        mock_rt = MagicMock(spec=["pause", "resume"])
        mock_rt.pause.side_effect = RuntimeError("pause failed")
        api._realtime = mock_rt
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.pause_realtime()
            _wait(0.3)
        assert any("onRealtimePaused" in c for c in js_calls)

    def test_resume_exception_still_pushes_onRealtimeResumed(self):
        api = API()
        mock_rt = MagicMock(spec=["pause", "resume"])
        mock_rt.resume.side_effect = RuntimeError("resume failed")
        api._realtime = mock_rt
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.resume_realtime()
            _wait(0.3)
        assert any("onRealtimeResumed" in c for c in js_calls)

    def test_pause_returns_not_running_when_rt_has_no_pause_method(self):
        api = API()
        api._realtime = object()  # no pause/resume methods
        result = api.pause_realtime()
        assert result["status"] == "not_running"


# ── Engine option passed through ──────────────────────────────────────────────

class TestEngineOption:
    def test_engine_forwarded_to_transcriber(self):
        api = API()
        captured = {}

        def capture_init(*args, **kwargs):
            captured["engine"] = kwargs.get("engine") or (args[0] if args else None)
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push"):
            api.start_realtime({"engine": "whisper"})
            _wait(0.3)

        assert captured.get("engine") == "whisper"

    def test_default_engine_is_qwen(self):
        api = API()
        captured = {}

        def capture_init(*args, **kwargs):
            captured["engine"] = kwargs.get("engine") or (args[0] if args else None)
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push"):
            api.start_realtime()
            _wait(0.3)

        assert captured.get("engine") == "qwen"
