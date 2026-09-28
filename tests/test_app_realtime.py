"""Tests for app.API.start_realtime / stop_realtime / delete_model."""
import os
import threading
import time
import pytest
from unittest.mock import MagicMock, patch

import app as app_module
from app import API


def _wait(timeout=0.5):
    time.sleep(timeout)


@pytest.fixture(autouse=True)
def _output_dir(tmp_path):
    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)), \
         patch.object(
             app_module, "_macos_microphone_permission_status", return_value="authorized"
         ), patch.object(
             app_module, "_macos_screen_recording_permission_status", return_value="authorized"
         ):
        yield


# ── Return values ─────────────────────────────────────────────────────────────

class TestReturnValues:
    def test_start_returns_started(self):
        api = API()
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push"):
            MockRT.return_value.start.return_value = None
            result = api.start_realtime()
            _wait(0.1)
        assert result["status"] == "started"

    def test_start_returns_already_running(self):
        api = API()
        api._realtime = MagicMock()  # simulate active session
        result = api.start_realtime()
        assert result["status"] == "already_running"

    @pytest.mark.parametrize("status", [
        "not_determined", "denied", "restricted", "unknown",
    ])
    def test_mic_start_requires_permission_before_session_or_backend(self, status):
        api = API()
        with patch.object(
            app_module, "_macos_microphone_permission_status", return_value=status
        ), patch("src.realtime.RealtimeTranscriber") as realtime, \
             patch.object(app_module.os, "makedirs") as makedirs, \
             patch.object(api, "_caffeinate_start") as caffeinate:
            result = api.start_realtime({"capture_mode": "mic"})

        assert result == {
            "status": "permission_required",
            "missing_permissions": ["microphone"],
            "microphone_permission": status,
        }
        assert api._realtime is None
        assert api.get_recording_status()["state"] == "idle"
        realtime.assert_not_called()
        makedirs.assert_not_called()
        caffeinate.assert_not_called()

    @pytest.mark.parametrize(("capture_mode", "mic_status", "screen_status", "missing"), [
        ("system", "authorized", "required", ["screen_recording"]),
        ("mix", "denied", "authorized", ["microphone"]),
        ("mix", "authorized", "required", ["screen_recording"]),
        ("mix", "denied", "required", ["microphone", "screen_recording"]),
    ])
    def test_system_and_mix_require_their_exact_permission_set(
        self, capture_mode, mic_status, screen_status, missing
    ):
        api = API()
        with patch.object(
            app_module, "_macos_microphone_permission_status", return_value=mic_status
        ), patch.object(
            app_module, "_macos_screen_recording_permission_status", return_value=screen_status
        ), patch("src.native_capture.NativeCaptureHelper") as native, \
             patch.object(app_module.os, "makedirs") as makedirs, \
             patch.object(api, "_caffeinate_start") as caffeinate:
            result = api.start_realtime({"capture_mode": capture_mode})

        assert result["status"] == "permission_required"
        assert result["missing_permissions"] == missing
        assert api._realtime is None
        native.assert_not_called()
        makedirs.assert_not_called()
        caffeinate.assert_not_called()

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

    def test_start_uses_unique_realtime_wav_name(self):
        import re
        api = API()
        captured = {}

        def capture_init(*args, **kwargs):
            captured["output_path"] = kwargs.get("output_path")
            captured["diagnostic_id"] = kwargs.get("diagnostic_id")
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push"):
            api.start_realtime()
            _wait(0.3)

        output_path = captured["output_path"]
        basename = os.path.basename(output_path)
        parent = os.path.basename(os.path.dirname(output_path))
        match = re.fullmatch(r"realtime_recording_([0-9a-f]{8})\.wav", basename)
        assert match, basename
        assert parent.startswith(match.group(1))
        assert captured["diagnostic_id"] == parent

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
            api.stop_realtime()
            _wait(0.2)

        assert any("onRealtimeResult" in c for c in js_calls)
        assert any("Hello world" in c for c in js_calls)

    @pytest.mark.parametrize("capture_mode", ["mic", "system", "mix"])
    def test_live_utterance_is_saved_while_recording(self, capture_mode, tmp_path):
        from src.service import AudioAssistService

        api = API()
        callbacks = {}

        def create_backend(*args, **kwargs):
            callbacks["on_result"] = kwargs["on_result"]
            backend = MagicMock()
            backend.start.return_value = None
            return backend

        with patch("src.realtime.RealtimeTranscriber", side_effect=create_backend), \
             patch("src.native_capture.NativeCaptureHelper", side_effect=create_backend), \
             patch.object(app_module, "_push"):
            assert api.start_realtime({"capture_mode": capture_mode})["status"] == "started"
            deadline = time.monotonic() + 2
            while api.get_recording_status()["state"] != "recording" and time.monotonic() < deadline:
                time.sleep(0.01)
            session_id = api.get_recording_status()["session_id"]
            assert session_id
            callbacks["on_result"]({"text": "第 40 分钟的讨论", "start": 2400, "end": 2403})
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                draft = AudioAssistService(str(tmp_path)).get_live_transcript(session_id)
                if draft["segments"]:
                    break
                time.sleep(0.005)

        assert draft["segments"] == [
            {"seq": 1, "start": 2400, "end": 2403, "text": "第 40 分钟的讨论"}
        ]
        assert api.get_recording_status()["state"] == "recording"
        api.stop_realtime()
        deadline = time.monotonic() + 2
        while api.get_recording_status()["state"] != "idle" and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_live_draft_write_error_warns_agent_but_keeps_recording(self):
        api = API()
        callbacks = {}

        def create_backend(*args, **kwargs):
            callbacks["on_result"] = kwargs["on_result"]
            backend = MagicMock()
            backend.start.return_value = None
            return backend

        pushed = []
        with patch("src.realtime.RealtimeTranscriber", side_effect=create_backend), \
             patch.object(app_module, "_push", side_effect=pushed.append), \
             patch.object(app_module.LiveTranscriptWriter, "append", side_effect=OSError("disk full")) as append:
            api.start_realtime({"capture_mode": "mic"})
            deadline = time.monotonic() + 2
            while api.get_recording_status()["state"] != "recording" and time.monotonic() < deadline:
                time.sleep(0.01)
            callbacks["on_result"]({"text": "still recording", "start": 1, "end": 2})
            callbacks["on_result"]({"text": "still recording", "start": 3, "end": 4})

        assert api.get_recording_status()["state"] == "recording"
        assert api.get_recording_status()["live_transcript_warning"]
        assert append.call_count == 1
        assert sum("onRealtimeResult" in call for call in pushed) == 2

    def test_live_draft_init_failure_does_not_block_audio_capture(self):
        api = API()
        backend = MagicMock()
        backend.start.return_value = None
        with patch("src.realtime.RealtimeTranscriber", return_value=backend), \
             patch.object(app_module.LiveTranscriptWriter, "__init__", side_effect=OSError("disk full")), \
             patch.object(app_module, "_push"):
            assert api.start_realtime({"capture_mode": "mic"})["status"] == "started"
            deadline = time.monotonic() + 2
            while api.get_recording_status()["state"] != "recording" and time.monotonic() < deadline:
                time.sleep(0.01)
        assert api.get_recording_status()["state"] == "recording"
        assert api.get_recording_status()["live_transcript_warning"]

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
    def test_stop_during_load_holds_lease_until_backend_exits(self):
        """
        A second session must not start while a cancelled startup still owns
        the process-global MLX backend.
        """
        api = API()
        start_entered = threading.Event()
        stop_entered = threading.Event()
        release = threading.Event()
        js_calls = []

        class SlowBackend:
            def start(self):
                start_entered.set()
                release.wait(timeout=5)

            def stop(self):
                stop_entered.set()
                release.wait(timeout=5)

        immediate = MagicMock()
        immediate.start.return_value = None

        with patch(
            "src.realtime.RealtimeTranscriber",
            side_effect=[SlowBackend(), immediate],
        ), patch.object(app_module, "_push", side_effect=js_calls.append):
            assert api.start_realtime()["status"] == "started"
            assert start_entered.wait(timeout=2)
            assert api.stop_realtime()["status"] == "stopped"
            assert stop_entered.wait(timeout=2)
            assert api.get_recording_status()["state"] == "stopping"
            assert api.start_realtime()["status"] == "already_running"
            assert not any("onRealtimeStopped" in call for call in js_calls)

            release.set()
            deadline = time.monotonic() + 2
            while api._realtime is not None and time.monotonic() < deadline:
                time.sleep(0.01)

            assert api._realtime is None
            assert any("onRealtimeStopped" in call for call in js_calls)
            assert not any("onRealtimeStarted" in call for call in js_calls)
            assert api.start_realtime()["status"] == "started"
            deadline = time.monotonic() + 2
            while not immediate.start.called and time.monotonic() < deadline:
                time.sleep(0.01)
            assert immediate.start.called

    def test_native_timeout_retains_lease_until_worker_exit(self):
        from src.native_capture import RealtimeWorkerStillStopping

        api = API()
        wait_entered = threading.Event()
        release = threading.Event()
        js_calls = []
        rt = MagicMock()
        rt.stop.side_effect = RealtimeWorkerStillStopping("still stopping")

        def wait_for_worker_exit():
            wait_entered.set()
            release.wait(timeout=5)

        rt.wait_for_worker_exit.side_effect = wait_for_worker_exit
        api._realtime = rt

        with patch.object(app_module, "_push", side_effect=js_calls.append):
            assert api.stop_realtime()["status"] == "stopped"
            assert wait_entered.wait(timeout=2)
            assert api.get_recording_status()["state"] == "stopping"
            assert api.start_realtime()["status"] == "already_running"
            assert not any("onRealtimeStopped" in call for call in js_calls)
            release.set()
            deadline = time.monotonic() + 2
            while api._realtime is not None and time.monotonic() < deadline:
                time.sleep(0.01)

        assert api._realtime is None
        assert any("onRealtimeStopped" in call for call in js_calls)

    def test_shutdown_during_load_cancels_and_waits_for_startup_lease(self):
        api = API()
        start_entered = threading.Event()
        stop_entered = threading.Event()
        release = threading.Event()
        js_calls = []

        class SlowBackend:
            def start(self):
                start_entered.set()
                release.wait(timeout=5)

            def stop(self):
                stop_entered.set()
                release.wait(timeout=5)

        with patch(
            "src.realtime.RealtimeTranscriber", return_value=SlowBackend()
        ), patch.object(app_module, "_push", side_effect=js_calls.append):
            assert api.start_realtime()["status"] == "started"
            assert start_entered.wait(timeout=2)
            shutdown = threading.Thread(target=api.shutdown, daemon=True)
            shutdown.start()
            assert stop_entered.wait(timeout=2)
            assert api._realtime is not None
            assert api.start_realtime()["status"] == "already_running"
            release.set()
            shutdown.join(timeout=2)

        assert not shutdown.is_alive()
        assert api._realtime is None
        assert api.get_recording_status()["state"] == "idle"
        assert not any("onRealtimeStarted" in call for call in js_calls)

    def test_shutdown_waits_for_late_native_worker_before_clearing_lease(self):
        from src.native_capture import RealtimeWorkerStillStopping

        api = API()
        wait_entered = threading.Event()
        release = threading.Event()
        rt = MagicMock()
        rt.stop.side_effect = RealtimeWorkerStillStopping("still stopping")

        def wait_for_worker_exit():
            wait_entered.set()
            release.wait(timeout=5)

        rt.wait_for_worker_exit.side_effect = wait_for_worker_exit
        api._realtime = rt
        shutdown = threading.Thread(target=api.shutdown, daemon=True)
        shutdown.start()
        assert wait_entered.wait(timeout=2)
        assert api._realtime is rt
        assert api.start_realtime()["status"] == "already_running"
        release.set()
        shutdown.join(timeout=2)

        assert not shutdown.is_alive()
        assert api._realtime is None
        assert api.get_recording_status()["state"] == "idle"


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

    def test_pause_exception_reports_error_without_false_paused_event(self):
        api = API()
        mock_rt = MagicMock(spec=["pause", "resume"])
        mock_rt.pause.side_effect = RuntimeError("pause failed")
        api._realtime = mock_rt
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.pause_realtime()
            _wait(0.3)
        assert not any("onRealtimePaused" in c for c in js_calls)
        assert any("onRealtimeError" in c and "pause failed" in c for c in js_calls)
        assert api.get_recording_status()["state"] == "error"

    def test_resume_exception_reports_error_without_false_resumed_event(self):
        api = API()
        mock_rt = MagicMock(spec=["pause", "resume"])
        mock_rt.resume.side_effect = RuntimeError("resume failed")
        api._realtime = mock_rt
        js_calls = []
        with patch.object(app_module, "_push", side_effect=js_calls.append):
            api.resume_realtime()
            _wait(0.3)
        assert not any("onRealtimeResumed" in c for c in js_calls)
        assert any("onRealtimeError" in c and "resume failed" in c for c in js_calls)
        assert api.get_recording_status()["state"] == "error"

    def test_pause_returns_not_running_when_rt_has_no_pause_method(self):
        api = API()
        api._realtime = object()  # no pause/resume methods
        result = api.pause_realtime()
        assert result["status"] == "not_running"

    def test_status_tracks_record_pause_resume_stop_lifecycle(self):
        api = API()
        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(app_module, "_push"):
            api.start_realtime({"capture_mode": "mic"})
            _wait(0.3)
            running = api.get_recording_status()
            assert running["state"] == "recording"
            assert running["capture_mode"] == "mic"
            assert running["session_id"]
            assert running["started_at"]

            api.pause_realtime()
            _wait(0.3)
            assert api.get_recording_status()["state"] == "paused"

            api.resume_realtime()
            _wait(0.3)
            assert api.get_recording_status()["state"] == "recording"

            api.stop_realtime()
            _wait(0.3)
            assert api.get_recording_status() == {
                "state": "idle",
                "session_id": None,
                "capture_mode": None,
                "started_at": None,
                "error": None,
            }

    def test_shutdown_stops_backend_and_resets_status(self):
        api = API()
        backend = MagicMock()
        api._realtime = backend
        api._set_recording_status("recording", session_id="s", capture_mode="mic")
        api.shutdown()
        backend.stop.assert_called_once()
        assert api.get_recording_status()["state"] == "idle"


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

    def test_model_id_is_forwarded_to_mic_transcriber(self):
        api = API()
        captured = {}

        def capture_init(*args, **kwargs):
            captured.update(kwargs)
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_init), \
             patch.object(app_module, "_push"):
            api.start_realtime({"model_id": "whisper-medium"})
            _wait(0.3)

        assert captured["engine"] == "whisper"
        assert captured["model_id"] == "whisper-medium"


# ── delete_model ──────────────────────────────────────────────────────────────

class TestDeleteModel:
    def test_delete_unknown_model_returns_not_found(self):
        api = API()
        result = api.delete_model("nonexistent-model-xyz")
        assert result["status"] == "not_found"

    def test_delete_known_model_calls_mm_delete(self):
        api = API()
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock()  # model exists
        mock_mm.is_downloaded.return_value = False
        with patch("src.model_manager.ModelManager", return_value=mock_mm):
            result = api.delete_model("qwen3-asr-1.7b")
        mock_mm.delete.assert_called_once_with("qwen3-asr-1.7b")
        assert result["status"] == "ok"

    def test_delete_returns_downloaded_false_after_delete(self):
        api = API()
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = MagicMock()
        mock_mm.is_downloaded.return_value = False
        with patch("src.model_manager.ModelManager", return_value=mock_mm):
            result = api.delete_model("qwen3-asr-1.7b")
        assert result["downloaded"] is False


# ── Realtime segments collection on stop ──────────────────────────────────────

class TestRtSegmentsCollection:
    def test_stop_collects_segments_from_rt(self):
        """stop_realtime() stores segments from rt.get_segments() in _rt_segments."""
        api = API()
        sample_segs = [
            {"text": "Hello", "start": 0.0, "end": 1.0},
            {"text": "World", "start": 1.5, "end": 2.5},
        ]

        mock_rt = MagicMock()
        mock_rt._output_path = "/tmp/session.wav"
        mock_rt.get_segments.return_value = sample_segs

        api._realtime = mock_rt
        with patch.object(app_module, "_push"):
            api.stop_realtime()
            _wait(0.4)

        assert "/tmp/session.wav" in api._rt_segments
        assert api._rt_segments["/tmp/session.wav"] == sample_segs

    def test_stop_no_segments_does_not_add_entry(self):
        """If rt.get_segments() is empty, nothing is added to _rt_segments."""
        api = API()
        mock_rt = MagicMock()
        mock_rt._output_path = "/tmp/session2.wav"
        mock_rt.get_segments.return_value = []
        api._realtime = mock_rt
        with patch.object(app_module, "_push"):
            api.stop_realtime()
            _wait(0.4)
        assert "/tmp/session2.wav" not in api._rt_segments

    def test_transcribe_pops_rt_segments_and_uses_run_realtime_segments(self):
        """transcribe() uses run_realtime_segments when _rt_segments has path."""
        import tempfile
        api = API()
        sample_segs = [{"text": "Hi", "start": 0.0, "end": 1.0}]
        wav_path = os.path.join(tempfile.gettempdir(), "rt_test.wav")
        # Pre-seed rt_segments
        api._rt_segments[wav_path] = sample_segs

        called_with = {}

        def fake_run_realtime_segments(segments, wav_path, output_dir, **kw):
            called_with["segments"] = segments
            called_with["wav_path"] = wav_path
            json_path = os.path.join(output_dir, f"{kw['job_id']}.json")
            md_path   = os.path.join(output_dir, f"{kw['job_id']}.md")
            import json
            os.makedirs(output_dir, exist_ok=True)
            with open(json_path, "w") as f:
                json.dump({"audio": wav_path, "filename": "rt_test.wav",
                           "language": "", "created_at": "", "segments": []}, f)
            open(md_path, "w").close()
            return json_path, md_path

        with patch("src.pipeline.run_realtime_segments",
                   side_effect=fake_run_realtime_segments), \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "OUTPUT_DIR", tempfile.gettempdir()):
            api.transcribe(wav_path, {})
            _wait(0.5)

        assert called_with.get("segments") == sample_segs
        assert called_with.get("wav_path") == wav_path
        # Segments should be consumed (popped)
        assert wav_path not in api._rt_segments


# ── Caffeinate leak guard ─────────────────────────────────────────────────────

class TestCaffeinateOnStartFailure:
    def test_caffeinate_released_when_start_raises(self):
        """
        If RealtimeTranscriber.start() raises, _caffeinate_stop() must be called
        so the system-sleep-prevention lock is not held indefinitely.
        """
        api = API()
        caffeinate_stopped = []

        def boom():
            raise RuntimeError("model load failed")

        with patch("src.realtime.RealtimeTranscriber") as MockRT, \
             patch.object(api, "_caffeinate_stop",
                          side_effect=lambda: caffeinate_stopped.append(True)), \
             patch.object(app_module, "_push"):
            MockRT.return_value.start.side_effect = boom
            api.start_realtime()
            _wait(0.3)

        assert caffeinate_stopped, "_caffeinate_stop() must be called after start() failure"
