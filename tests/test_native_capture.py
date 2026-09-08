"""
Tests for src.native_capture.NativeCaptureHelper.

All tests mock the helper subprocess, FIFO, and model loading so they run
without the actual Swift binary or audio hardware.
"""
from __future__ import annotations

import io
import os
import queue
import signal
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch, call

import pytest

from src.native_capture import NativeCaptureHelper, _default_helper_path


@pytest.fixture(autouse=True)
def _authorized_app_capture_permissions(request):
    # Permission implementation tests must exercise the real helpers rather than
    # the authorized defaults used by the capture-routing tests in this module.
    if request.cls in {
        TestPreflightCapture,
        TestMicrophonePermissionRequest,
        TestScreenRecordingPermission,
    }:
        yield
        return
    with patch("app._macos_microphone_permission_status", return_value="authorized"), \
         patch("app._macos_screen_recording_permission_status", return_value="authorized"):
        yield


# ── Test fixtures / helpers ────────────────────────────────────────────────────

class _FakeProcess:
    """Minimal Popen-compatible fake that controls stdout content."""

    def __init__(self, stdout_lines=None, exit_code=None):
        self.pid = 42000
        self._exit_code = exit_code
        lines = "\n".join(stdout_lines or []) + "\n"
        self.stdout = io.StringIO(lines)
        self.stderr = io.StringIO("")
        self._terminated = False

    def poll(self):
        return self._exit_code

    def terminate(self):
        self._terminated = True
        self._exit_code = -15

    def kill(self):
        self._exit_code = -9

    def wait(self, timeout=None):
        return self._exit_code or 0


def _helper(on_result=None, on_error=None, output_path="/tmp/cap_test.wav"):
    return NativeCaptureHelper(
        mode="system",
        engine="qwen",
        on_result=on_result,
        on_error=on_error,
        output_path=output_path,
        helper_path="/fake/AudioAssistCaptureHelper",
    )


def _start_mocked(helper, proc, *, kill_fn=None):
    """
    Start helper under a comprehensive mock context.
    Returns the context-manager stack so callers can inspect side-effects.
    """
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("subprocess.Popen", return_value=proc))
    stack.enter_context(patch("os.mkfifo"))
    stack.enter_context(patch("os.open", return_value=5))
    stack.enter_context(patch("os.read", return_value=b""))  # PCM EOF immediately
    stack.enter_context(patch("os.close"))
    stack.enter_context(patch("fcntl.fcntl", return_value=0))
    # Report fd as immediately ready so _pcm_reader's select() doesn't block on the fake fd
    stack.enter_context(patch("select.select",
                              side_effect=lambda rlist, *a, **k: (rlist, [], [])))
    if kill_fn:
        stack.enter_context(patch("os.kill", side_effect=kill_fn))
    else:
        stack.enter_context(patch("os.kill"))
    stack.enter_context(patch.object(NativeCaptureHelper, "_load_models"))
    stack.__enter__()
    helper.start()
    return stack


# ── Default helper path ────────────────────────────────────────────────────────

class TestDefaultHelperPath:
    def test_contains_native_dir(self):
        p = _default_helper_path()
        assert "native" in p
        assert "AudioAssistCaptureHelper" in p

    def test_ends_with_binary_name(self):
        assert _default_helper_path().endswith("AudioAssistCaptureHelper")


# ── start() behaviour ─────────────────────────────────────────────────────────

class TestStart:
    def test_creates_fifo(self):
        h = _helper()
        proc = _FakeProcess()
        created_paths = []
        real_mkfifo = patch("os.mkfifo", side_effect=created_paths.append)

        with real_mkfifo, \
             patch("subprocess.Popen", return_value=proc), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"):
            h.start()

        assert len(created_paths) == 1
        assert "audioassist" in created_paths[0]
        assert created_paths[0].endswith(".fifo")

    def test_launches_helper_with_required_args(self):
        h = _helper()
        proc = _FakeProcess()
        captured_cmd = []

        def capture_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return proc

        with patch("subprocess.Popen", side_effect=capture_popen), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"):
            h.start()

        assert "/fake/AudioAssistCaptureHelper" in captured_cmd
        assert "stream"            in captured_cmd
        assert "--mode"            in captured_cmd
        assert "system"            in captured_cmd
        assert "--pcm-fifo"        in captured_cmd
        assert "--wav-out"         in captured_cmd
        assert "/tmp/cap_test.wav" in captured_cmd
        assert "--sample-rate"     in captured_cmd

    def test_sets_running_true(self):
        h = _helper()
        proc = _FakeProcess()
        stack = _start_mocked(h, proc)
        stack.__exit__(None, None, None)
        assert h._running is True

    def test_raises_if_output_path_missing(self):
        h = NativeCaptureHelper(
            mode="system",
            helper_path="/fake/helper",
        )
        with patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch.object(NativeCaptureHelper, "_load_models"):
            with pytest.raises(ValueError, match="output_path"):
                h.start()


# ── stop() behaviour ──────────────────────────────────────────────────────────

class TestStop:
    def test_terminates_process(self):
        h = _helper()
        proc = _FakeProcess()
        stack = _start_mocked(h, proc)
        time.sleep(0.05)
        h.stop()
        stack.__exit__(None, None, None)
        assert proc._terminated

    def test_sets_running_false(self):
        h = _helper()
        proc = _FakeProcess()
        stack = _start_mocked(h, proc)
        h.stop()
        stack.__exit__(None, None, None)
        assert h._running is False

    def test_cleans_up_fifo_path(self):
        h = _helper()
        proc = _FakeProcess()
        unlinked = []

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink", side_effect=lambda p: unlinked.append(p)), \
             patch.object(NativeCaptureHelper, "_load_models"):
            h.start()
            fifo_path = h._fifo_path  # capture before stop clears it
            h.stop()

        assert fifo_path in unlinked

    def test_stop_before_start_is_safe(self):
        h = _helper()
        h.stop()  # must not raise

    def test_terminate_error_still_cleans_fifo_and_model(self):
        h = _helper()
        backend = MagicMock()
        h._asr = backend
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = RuntimeError("terminate raced")
        h._process = proc
        h._fifo_path = "/tmp/audioassist-stop-error.fifo"

        with patch("os.path.exists", return_value=True), patch("os.unlink") as unlink:
            with pytest.raises(RuntimeError, match="terminate raced"):
                h.stop()

        proc.wait.assert_called()
        unlink.assert_called_once_with("/tmp/audioassist-stop-error.fifo")
        backend.close.assert_called_once()
        assert h._asr is None


# ── pause() / resume() ────────────────────────────────────────────────────────

class TestPauseResume:
    def test_pause_sends_sigusr1(self):
        h = _helper()
        proc = _FakeProcess()
        kill_calls = []
        stack = _start_mocked(h, proc, kill_fn=lambda pid, sig: kill_calls.append((pid, sig)))

        h.pause()
        stack.__exit__(None, None, None)

        assert (42000, signal.SIGUSR1) in kill_calls

    def test_resume_sends_sigusr2(self):
        h = _helper()
        proc = _FakeProcess()
        kill_calls = []
        stack = _start_mocked(h, proc, kill_fn=lambda pid, sig: kill_calls.append((pid, sig)))

        h.resume()
        stack.__exit__(None, None, None)

        assert (42000, signal.SIGUSR2) in kill_calls

    def test_pause_sets_paused_flag(self):
        h = _helper()
        proc = _FakeProcess()
        stack = _start_mocked(h, proc)
        h.pause()
        stack.__exit__(None, None, None)
        assert h._paused is True

    def test_resume_clears_paused_flag(self):
        h = _helper()
        proc = _FakeProcess()
        stack = _start_mocked(h, proc)
        h._paused = True
        h.resume()
        stack.__exit__(None, None, None)
        assert h._paused is False

    def test_pause_without_start_does_not_raise(self):
        h = _helper()
        h.pause()  # must not raise

    def test_resume_without_start_does_not_raise(self):
        h = _helper()
        h.resume()  # must not raise

    def test_pause_dead_process_does_not_raise(self):
        h = _helper()
        proc = _FakeProcess(exit_code=1)
        stack = _start_mocked(h, proc)
        with patch("os.kill", side_effect=ProcessLookupError):
            h.pause()  # must not raise
        stack.__exit__(None, None, None)


# ── NDJSON event parsing ───────────────────────────────────────────────────────

class TestEventParsing:
    def _run_and_wait(self, stdout_lines, on_error=None, on_result=None):
        h = _helper(on_error=on_error, on_result=on_result)
        proc = _FakeProcess(stdout_lines=stdout_lines)
        stack = _start_mocked(h, proc)
        time.sleep(0.15)  # let event thread drain
        stack.__exit__(None, None, None)
        return h

    def test_error_event_fires_on_error(self):
        errors = []
        self._run_and_wait(
            ['{"event":"error","reason":"stream_start_failed"}'],
            on_error=errors.append,
        )
        assert len(errors) == 1
        assert "stream_start_failed" in errors[0]

    def test_permission_required_fires_on_error(self):
        errors = []
        self._run_and_wait(
            ['{"event":"permission_required","permission":"screen_recording"}'],
            on_error=errors.append,
        )
        assert any("permission_required" in e for e in errors)

    def test_started_event_does_not_fire_on_error(self):
        errors = []
        self._run_and_wait(
            ['{"event":"started","sample_rate":16000,"channels":1}'],
            on_error=errors.append,
        )
        assert errors == []

    def test_invalid_json_does_not_crash(self):
        errors = []
        # Mixed valid and invalid JSON — should not raise
        self._run_and_wait(
            ["not valid json", '{"event":"started"}'],
            on_error=errors.append,
        )
        # No crash, error list unchanged by invalid JSON
        assert errors == []

    def test_error_message_field_used(self):
        errors = []
        self._run_and_wait(
            ['{"event":"error","message":"explicit message"}'],
            on_error=errors.append,
        )
        assert "explicit message" in errors[0]


# ── Abnormal subprocess exit ───────────────────────────────────────────────────

class TestAbnormalExit:
    def test_error_event_before_exit_fires_on_error(self):
        errors = []
        h = _helper(on_error=errors.append)
        proc = _FakeProcess(
            stdout_lines=['{"event":"error","reason":"no_display_found"}'],
            exit_code=1,
        )
        stack = _start_mocked(h, proc)
        time.sleep(0.15)
        stack.__exit__(None, None, None)
        assert any("no_display_found" in e for e in errors)


# ── get_segments() ────────────────────────────────────────────────────────────

class TestGetSegments:
    def test_returns_empty_list_initially(self):
        h = _helper()
        assert h.get_segments() == []

    def test_returns_copy_not_reference(self):
        h = _helper()
        h._segments = [{"text": "hi", "start": 0.0, "end": 1.0}]
        segs = h.get_segments()
        segs.append({"text": "bye", "start": 1.0, "end": 2.0})
        assert len(h._segments) == 1  # original unchanged


class TestLocalModelLoading:
    @pytest.mark.parametrize("backend", [None, "mlx", "pytorch-mps"])
    def test_qwen_uses_validated_local_path_without_aligner(self, backend):
        asr_class = MagicMock()
        silero = types.ModuleType("silero_vad")
        silero.load_silero_vad = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {"silero_vad": silero}), \
             patch(
                 "src.native_capture.resolve_realtime_model",
                 return_value=types.SimpleNamespace(
                     engine="qwen",
                     model_id="qwen3-asr-1.7b",
                     local_path="/models/qwen",
                 ),
             ), patch(
                 "src.native_capture.create_qwen_engine", asr_class,
             ):
            helper = NativeCaptureHelper(qwen_backend=backend)
            helper._load_models()

        asr_class.assert_called_once_with(
            with_timestamps=False,
            model_path="/models/qwen",
            aligner_path=None,
            backend_override=backend,
        )
        asr_class.return_value.load.assert_called_once()

    def test_stop_closes_loaded_backend(self):
        helper = _helper()
        backend = MagicMock()
        helper._asr = backend

        helper.stop()

        backend.close.assert_called_once()
        assert helper._asr is None

    def test_whisper_uses_validated_local_path(self):
        whisper_class = MagicMock()
        whisper_module = types.ModuleType("src.asr_whisper")
        whisper_module.WhisperASREngine = whisper_class
        silero = types.ModuleType("silero_vad")
        silero.load_silero_vad = MagicMock(return_value=MagicMock())

        with patch.dict(
            sys.modules,
            {"src.asr_whisper": whisper_module, "silero_vad": silero},
        ), patch(
            "src.native_capture.resolve_realtime_model",
            return_value=types.SimpleNamespace(
                engine="whisper",
                model_id="whisper-medium",
                local_path="/models/whisper-medium",
            ),
        ):
            helper = NativeCaptureHelper(
                mode="system",
                engine="whisper",
                model_id="whisper-medium",
                output_path="/tmp/cap.wav",
                helper_path="/fake/helper",
            )
            helper._load_models()

        whisper_class.assert_called_once_with(
            model_path="/models/whisper-medium"
        )
        whisper_class.return_value.load.assert_called_once()


# ── app.py integration: capture_mode routing ──────────────────────────────────

class TestAppCaptureMode:
    """Verify app.py routes correctly based on capture_mode option."""

    def test_mic_mode_uses_realtime_transcriber(self):
        import app as app_module
        from app import API

        api = API()
        captured = {}

        def capture_rt_init(*args, **kwargs):
            captured["backend"] = "RealtimeTranscriber"
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_rt_init), \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "_macos_microphone_permission_status", return_value="authorized"), \
             patch.object(app_module, "OUTPUT_DIR", "/tmp"):
            api.start_realtime({"capture_mode": "mic"})
            time.sleep(0.3)

        assert captured.get("backend") == "RealtimeTranscriber"

    def test_default_mode_uses_realtime_transcriber(self):
        import app as app_module
        from app import API

        api = API()
        captured = {}

        def capture_rt_init(*args, **kwargs):
            captured["backend"] = "RealtimeTranscriber"
            inst = MagicMock()
            inst.start.return_value = None
            return inst

        with patch("src.realtime.RealtimeTranscriber", side_effect=capture_rt_init), \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "_macos_microphone_permission_status", return_value="authorized"), \
             patch.object(app_module, "OUTPUT_DIR", "/tmp"):
            api.start_realtime({})  # no capture_mode → defaults to mic
            time.sleep(0.3)

        assert captured.get("backend") == "RealtimeTranscriber"

    def test_system_mode_uses_native_capture_helper(self):
        import app as app_module
        from app import API

        api = API()
        captured = {}

        def capture_nch_init(*args, **kwargs):
            captured["backend"] = "NativeCaptureHelper"
            inst = MagicMock()
            inst.start.return_value = None
            inst._output_path = "/tmp/test.wav"
            return inst

        with patch("src.native_capture.NativeCaptureHelper", side_effect=capture_nch_init), \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "OUTPUT_DIR", "/tmp"):
            api.start_realtime({"capture_mode": "system"})
            time.sleep(0.3)

        assert captured.get("backend") == "NativeCaptureHelper"

    def test_system_mode_forwards_selected_model_id(self):
        import app as app_module
        from app import API

        api = API()
        captured = {}

        def capture_nch_init(*args, **kwargs):
            captured.update(kwargs)
            inst = MagicMock()
            inst.start.return_value = None
            inst._output_path = "/tmp/test.wav"
            return inst

        with patch("src.native_capture.NativeCaptureHelper", side_effect=capture_nch_init), \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "OUTPUT_DIR", "/tmp"):
            api.start_realtime({
                "capture_mode": "system",
                "model_id": "whisper-medium",
            })
            time.sleep(0.3)

        assert captured["engine"] == "whisper"
        assert captured["model_id"] == "whisper-medium"


# ── app.py: preflight_capture ─────────────────────────────────────────────────

class TestPreflightCapture:
    @pytest.mark.parametrize("raw, expected", [
        (0, "not_determined"),
        (1, "restricted"),
        (2, "denied"),
        (3, "authorized"),
        (99, "unknown"),
    ])
    def test_macos_microphone_status_mapping(self, raw, expected):
        import sys
        import types
        import app as app_module

        capture_device = MagicMock()
        capture_device.authorizationStatusForMediaType_.return_value = raw
        objc = types.ModuleType("objc")
        objc.loadBundle = MagicMock()
        objc.lookUpClass = MagicMock(return_value=capture_device)

        with patch.object(app_module.sys, "platform", "darwin"), \
             patch.dict(sys.modules, {"objc": objc}):
            assert app_module._macos_microphone_permission_status() == expected

        capture_device.authorizationStatusForMediaType_.assert_called_once_with("soun")

    def test_mic_mode_supported_when_authorized(self):
        from app import API
        api = API()
        with patch("app._macos_microphone_permission_status", return_value="authorized"):
            result = api.preflight_capture("mic")
        assert result["supported"] is True
        assert result["microphone_permission"] == "authorized"
        assert result["missing_permissions"] == []

    @pytest.mark.parametrize("status", ["denied", "restricted"])
    def test_mic_mode_reports_blocked_permission(self, status):
        from app import API
        api = API()
        with patch("app._macos_microphone_permission_status", return_value=status):
            result = api.preflight_capture("mic")
        assert result["supported"] is False
        assert result["missing_permissions"] == ["microphone"]
        assert result["reason"] == f"microphone_permission_{status}"

    def test_mic_mode_allows_system_prompt_when_not_determined(self):
        from app import API
        api = API()
        with patch("app._macos_microphone_permission_status", return_value="not_determined"):
            result = api.preflight_capture("mic")
        assert result["supported"] is True
        assert result["microphone_permission"] == "not_determined"
        assert result["missing_permissions"] == ["microphone"]

    def test_system_mode_unsupported_on_old_macos(self):
        from app import API
        api = API()
        with patch("platform.mac_ver", return_value=("12.6.0", ("", "", ""), "")):
            result = api.preflight_capture("system")
        assert result["supported"] is False
        assert result["reason"] == "screencapturekit_requires_macos_13_0"

    def test_system_mode_supported_on_macos_13_when_helper_exists(self):
        from app import API
        api = API()
        fake_helper = "/fake/helper/binary"
        with patch("platform.mac_ver", return_value=("13.0.0", ("", "", ""), "")), \
             patch("src.native_capture._default_helper_path", return_value=fake_helper), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch("app._macos_screen_recording_permission_status", return_value="authorized"):
            result = api.preflight_capture("system")
        assert result["supported"] is True
        assert result["missing_permissions"] == []

    def test_capture_mode_permission_matrix_is_explicit(self):
        from app import API

        api = API()
        fake_helper = "/fake/helper/binary"
        common = [
            patch("platform.mac_ver", return_value=("26.0.0", ("", "", ""), "")),
            patch("src.native_capture._default_helper_path", return_value=fake_helper),
            patch("os.path.isfile", return_value=True),
            patch("os.access", return_value=True),
        ]
        with common[0], common[1], common[2], common[3], \
             patch("app._macos_microphone_permission_status", return_value="denied"), \
             patch("app._macos_screen_recording_permission_status", return_value="required"):
            mic = api.preflight_capture("mic")
            system = api.preflight_capture("system")
            mix = api.preflight_capture("mix")

        assert mic["missing_permissions"] == ["microphone"]
        assert "screen_recording_permission" not in mic
        assert system["missing_permissions"] == ["screen_recording"]
        assert "microphone_permission" not in system
        assert mix["missing_permissions"] == ["microphone", "screen_recording"]
        assert mix["reason"] == "capture_permissions_required"

    def test_mic_preflight_never_queries_screen_recording_permission(self):
        from app import API

        with patch(
            "app._macos_microphone_permission_status", return_value="authorized"
        ) as microphone, patch(
            "app._macos_screen_recording_permission_status"
        ) as screen_recording:
            result = API().preflight_capture("mic")

        assert result["supported"] is True
        microphone.assert_called_once_with()
        screen_recording.assert_not_called()

    def test_system_preflight_never_queries_microphone_permission(self):
        from app import API

        fake_helper = "/fake/helper/binary"
        with patch("platform.mac_ver", return_value=("26.0.0", ("", "", ""), "")), \
             patch("src.native_capture._default_helper_path", return_value=fake_helper), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch("app._macos_microphone_permission_status") as microphone, \
             patch(
                 "app._macos_screen_recording_permission_status", return_value="authorized"
             ) as screen_recording:
            result = API().preflight_capture("system")

        assert result["supported"] is True
        microphone.assert_not_called()
        screen_recording.assert_called_once_with()

    def test_system_mode_unsupported_when_helper_missing(self):
        from app import API
        api = API()
        fake_helper = "/nonexistent/helper"
        with patch("platform.mac_ver", return_value=("13.5.0", ("", "", ""), "")), \
             patch("src.native_capture._default_helper_path", return_value=fake_helper), \
             patch("os.path.isfile", return_value=False):
            result = api.preflight_capture("system")
        assert result["supported"] is False
        assert result["reason"] == "helper_not_found"

    def test_returns_os_version(self):
        from app import API
        api = API()
        with patch("platform.mac_ver", return_value=("14.2.1", ("", "", ""), "")), \
             patch("app._macos_microphone_permission_status", return_value="authorized"):
            result = api.preflight_capture("mic")
        assert result["os_version"] == "14.2.1"


class TestMicrophonePermissionRequest:
    @pytest.mark.skipif(sys.platform != "darwin", reason="real macOS bridge required")
    def test_native_request_has_boolean_block_metadata_in_fresh_process(self):
        # This must not mock objc: mock-only tests missed the missing block ABI.
        # A fresh process also prevents another framework import masking the bug.
        import subprocess

        result = subprocess.run(
            [sys.executable, "-c", """
import app
import objc
request = app._microphone_permission_request_method()
block = request.__metadata__()['arguments'][3]['callable']
assert block['retval']['type'] == b'v'
assert block['arguments'][0]['type'] == b'^v'
assert block['arguments'][1]['type'] == objc._C_NSBOOL
"""], capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("status", [
        "authorized", "denied", "restricted", "unknown", "not_applicable",
    ])
    def test_existing_status_returns_without_prompt(self, status):
        import app as app_module

        with patch.object(
            app_module, "_macos_microphone_permission_status", return_value=status
        ), patch.dict(sys.modules, {"objc": MagicMock()}) as modules:
            assert app_module._request_macos_microphone_permission() == status
            modules["objc"].lookUpClass.assert_not_called()

    @pytest.mark.parametrize("granted, expected", [
        (True, "authorized"),
        (False, "denied"),
    ])
    def test_not_determined_requests_avfoundation_permission(self, granted, expected):
        import app as app_module

        capture_device = MagicMock()
        capture_device.requestAccessForMediaType_completionHandler_.side_effect = (
            lambda media_type, callback: callback(granted)
        )
        objc = types.ModuleType("objc")
        objc.registerMetaDataForSelector = MagicMock()
        objc._C_NSBOOL = b"Z"
        objc.loadBundle = MagicMock()
        objc.lookUpClass = MagicMock(return_value=capture_device)

        with patch.object(
            app_module, "_macos_microphone_permission_status", return_value="not_determined"
        ), patch.dict(sys.modules, {"objc": objc}):
            status = app_module._request_macos_microphone_permission(timeout_seconds=0.1)

        assert status == expected
        capture_device.requestAccessForMediaType_completionHandler_.assert_called_once()
        assert capture_device.requestAccessForMediaType_completionHandler_.call_args.args[0] == "soun"

    def test_not_determined_permission_request_is_bounded(self):
        import app as app_module

        capture_device = MagicMock()
        objc = types.ModuleType("objc")
        objc.registerMetaDataForSelector = MagicMock()
        objc._C_NSBOOL = b"Z"
        objc.loadBundle = MagicMock()
        objc.lookUpClass = MagicMock(return_value=capture_device)

        with patch.object(
            app_module, "_macos_microphone_permission_status", return_value="not_determined"
        ), patch.dict(sys.modules, {"objc": objc}):
            status = app_module._request_macos_microphone_permission(timeout_seconds=0)

        assert status == "timeout"

    def test_api_returns_json_serializable_permission_result(self):
        from app import API

        with patch("app._request_macos_microphone_permission", return_value="authorized"):
            result = API().request_microphone_permission()

        assert result == {"status": "authorized", "authorized": True}


class TestScreenRecordingPermission:
    @pytest.mark.parametrize("granted, expected", [
        (True, "authorized"),
        (False, "required"),
    ])
    def test_core_graphics_preflight_mapping(self, granted, expected):
        import app as app_module

        library = MagicMock()
        library.CGPreflightScreenCaptureAccess.return_value = granted
        with patch.object(app_module.sys, "platform", "darwin"), \
             patch.object(app_module, "_load_core_graphics", return_value=library):
            assert app_module._macos_screen_recording_permission_status() == expected

    @pytest.mark.parametrize("granted, expected", [
        (True, "authorized"),
        (False, "required"),
    ])
    def test_core_graphics_request_mapping(self, granted, expected):
        import app as app_module

        library = MagicMock()
        library.CGRequestScreenCaptureAccess.return_value = granted
        with patch.object(
            app_module, "_macos_screen_recording_permission_status", return_value="required"
        ), patch.object(app_module, "_load_core_graphics", return_value=library):
            status = app_module._request_macos_screen_recording_permission(0.2)

        assert status == expected

    def test_screen_permission_request_is_bounded(self):
        import app as app_module

        library = MagicMock()
        release = threading.Event()
        started = threading.Event()

        def blocking_request():
            started.set()
            release.wait(0.5)
            return True

        library.CGRequestScreenCaptureAccess.side_effect = blocking_request
        with patch.object(
            app_module, "_macos_screen_recording_permission_status", return_value="required"
        ), patch.object(
            app_module, "_load_core_graphics", return_value=library
        ), patch.object(app_module, "_SCREEN_PERMISSION_REQUEST", None):
            first_status = app_module._request_macos_screen_recording_permission(0)
            assert started.wait(0.1)
            retry_status = app_module._request_macos_screen_recording_permission(0)
            release.set()

        assert first_status == "timeout"
        assert retry_status == "in_progress"
        library.CGRequestScreenCaptureAccess.assert_called_once_with()

    def test_api_returns_json_serializable_screen_permission_result(self):
        from app import API

        with patch("app._request_macos_screen_recording_permission", return_value="authorized"):
            result = API().request_screen_recording_permission()

        assert result == {"status": "authorized", "authorized": True}


# ── mix 模式路由（P1 修复验证）────────────────────────────────────────────────

class TestMixModeRouting:
    def test_mix_mode_uses_native_capture_helper(self):
        """mix 模式必须走 NativeCaptureHelper，不能退化到 mic-only。"""
        import app as app_module
        from app import API

        api = API()
        captured = {}

        def capture_nch_init(*args, **kwargs):
            captured["backend"] = "NativeCaptureHelper"
            captured["mode"]    = kwargs.get("mode")
            inst = MagicMock()
            inst.start.return_value = None
            inst._output_path = "/tmp/mix_test.wav"
            return inst

        with patch("src.native_capture.NativeCaptureHelper", side_effect=capture_nch_init), \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "OUTPUT_DIR", "/tmp"):
            api.start_realtime({"capture_mode": "mix"})
            time.sleep(0.3)

        assert captured.get("backend") == "NativeCaptureHelper"
        assert captured.get("mode") == "mix"

    def test_mix_mode_does_not_use_realtime_transcriber(self):
        """mix 模式下 RealtimeTranscriber 不应被实例化。"""
        import app as app_module
        from app import API

        api = API()
        rt_called = []

        def capture_nch_init(*args, **kwargs):
            inst = MagicMock()
            inst.start.return_value = None
            inst._output_path = "/tmp/mix_test2.wav"
            return inst

        with patch("src.native_capture.NativeCaptureHelper", side_effect=capture_nch_init), \
             patch("src.realtime.RealtimeTranscriber",
                   side_effect=lambda *a, **k: rt_called.append(True)) as mock_rt, \
             patch.object(app_module, "_push"), \
             patch.object(app_module, "OUTPUT_DIR", "/tmp"):
            api.start_realtime({"capture_mode": "mix"})
            time.sleep(0.3)

        assert rt_called == [], "RealtimeTranscriber should not be called in mix mode"

    def test_mix_mode_preflight_check(self):
        """preflight_capture('mix') 同样检查 macOS 版本和 helper。"""
        from app import API
        api = API()
        with patch("platform.mac_ver", return_value=("12.5.0", ("", "", ""), "")):
            result = api.preflight_capture("mix")
        assert result["supported"] is False
        assert result["reason"] == "screencapturekit_requires_macos_13_0"


# ── start() 异常路径清理（P2 修复验证）────────────────────────────────────────

class TestStartCleanupOnError:
    def test_model_closed_when_mkfifo_raises(self):
        h = _helper()
        backend = MagicMock()

        def load_models():
            h._asr = backend

        with patch.object(h, "_load_models", side_effect=load_models), patch(
            "os.path.exists", return_value=False
        ), patch("os.mkfifo", side_effect=OSError("fifo failed")):
            with pytest.raises(OSError, match="fifo failed"):
                h.start()

        backend.close.assert_called_once()
        assert h._fifo_path is None

    def test_post_popen_thread_failure_terminates_helper_and_closes_model(self):
        h = _helper()
        backend = MagicMock()
        proc = _FakeProcess()
        original_start = threading.Thread.start

        def load_models():
            h._asr = backend

        def fail_event_start(thread):
            if thread.name == "native-event-reader":
                raise RuntimeError("event thread failed")
            return original_start(thread)

        with patch.object(h, "_load_models", side_effect=load_models), patch(
            "os.mkfifo"
        ), patch("os.path.exists", return_value=False), patch(
            "os.open", return_value=7
        ), patch("os.close"), patch("fcntl.fcntl", return_value=0), patch(
            "subprocess.Popen", return_value=proc
        ), patch.object(threading.Thread, "start", fail_event_start):
            with pytest.raises(RuntimeError, match="event thread failed"):
                h.start()

        assert proc._terminated is True
        backend.close.assert_called_once()
        assert h._worker_thread is None

    def test_fifo_cleaned_up_when_popen_raises(self):
        """Popen 失败时，已创建的 FIFO 文件和 fd 不应泄漏。"""
        unlinked = []
        closed_fds = []

        h = _helper()

        with patch.object(NativeCaptureHelper, "_load_models"), \
             patch("os.mkfifo"), \
             patch("os.path.exists", return_value=True), \
             patch("os.open", return_value=7), \
             patch("fcntl.fcntl", return_value=0), \
             patch("os.close", side_effect=lambda fd: closed_fds.append(fd)), \
             patch("os.unlink", side_effect=lambda p: unlinked.append(p)), \
             patch("subprocess.Popen", side_effect=OSError("binary not found")):
            with pytest.raises(OSError, match="binary not found"):
                h.start()

        # FIFO fd must have been closed
        assert 7 in closed_fds
        # FIFO path must have been unlinked
        assert len(unlinked) >= 1

    def test_worker_thread_not_left_running_when_popen_raises(self):
        """Popen 失败后 worker thread 应被清理，不留在 _worker_thread。"""
        h = _helper()

        with patch.object(NativeCaptureHelper, "_load_models"), \
             patch("os.mkfifo"), \
             patch("os.path.exists", return_value=False), \
             patch("os.open", return_value=7), \
             patch("fcntl.fcntl", return_value=0), \
             patch("os.close"), \
             patch("subprocess.Popen", side_effect=OSError("fail")):
            with pytest.raises(OSError):
                h.start()

        assert h._worker_thread is None


# ── mix 模式：--mode 标志传递 ─────────────────────────────────────────────────

class TestMixModeHelperCommand:
    """NativeCaptureHelper 以 mix/system mode 启动时，正确将 --mode 传给子进程。"""

    def _start_and_capture_cmd(self, mode: str) -> list:
        h = NativeCaptureHelper(
            mode=mode,
            output_path="/tmp/capture_test.wav",
            helper_path="/fake/helper",
        )
        captured_cmd: list = []
        proc = _FakeProcess()

        def fake_popen(cmd, **kw):
            captured_cmd.extend(cmd)
            return proc

        with patch.object(NativeCaptureHelper, "_load_models"), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch("subprocess.Popen", side_effect=fake_popen):
            h.start()
            h.stop()

        return captured_cmd

    def test_mix_mode_passes_mix_flag(self):
        cmd = self._start_and_capture_cmd("mix")
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "mix"

    def test_system_mode_passes_system_flag(self):
        cmd = self._start_and_capture_cmd("system")
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "system"


# ── app.py: open_privacy_settings ────────────────────────────────────────────

class TestWarningEventHandling:
    """warning イベントが正しく処理されることを確認する。"""

    def _make_helper_with_cb(self):
        errors = []
        h = _helper(on_error=lambda m: errors.append(m))
        return h, errors

    def _fire_event(self, helper, event: dict):
        """_handle_event を直接呼んでイベントを注入する。"""
        helper._handle_event(event)

    def test_mic_unavailable_fires_mic_degraded(self):
        h, errors = self._make_helper_with_cb()
        self._fire_event(h, {"event": "warning", "reason": "mic_unavailable"})
        assert len(errors) == 1
        assert errors[0].startswith("mic_degraded:")

    def test_mic_converter_failed_fires_mic_degraded(self):
        h, errors = self._make_helper_with_cb()
        self._fire_event(h, {"event": "warning", "reason": "mic_converter_failed"})
        assert errors[0].startswith("mic_degraded:")

    def test_mic_capture_failed_fires_mic_degraded(self):
        h, errors = self._make_helper_with_cb()
        self._fire_event(h, {"event": "warning", "reason": "mic_capture_failed"})
        assert errors[0].startswith("mic_degraded:")

    def test_unknown_warning_does_not_fire_on_error(self):
        h, errors = self._make_helper_with_cb()
        self._fire_event(h, {"event": "warning", "reason": "some_other_warning"})
        assert errors == []

    def test_mic_degraded_message_contains_reason(self):
        h, errors = self._make_helper_with_cb()
        self._fire_event(h, {"event": "warning", "reason": "mic_unavailable"})
        assert "mic_unavailable" in errors[0]


# ── stop() drain 时序 ─────────────────────────────────────────────────────────

class TestStopDrainTiming:
    """Verify that stop() drains the FIFO tail before flushing speech buffer."""

    def test_worker_timeout_closes_backend_when_inference_eventually_finishes(self):
        h = _helper()
        backend = MagicMock()
        h._asr = backend
        entered = threading.Event()
        release = threading.Event()

        def slow_transcribe(*_args):
            entered.set()
            release.wait(timeout=5)

        h._transcribe_queue.put(([], 0.0, 1.0))
        with patch.object(h, "_transcribe_segment", side_effect=slow_transcribe):
            worker = threading.Thread(
                target=h._transcription_worker,
                daemon=True,
                name="native-transcribe-worker",
            )
            h._worker_thread = worker
            worker.start()
            assert entered.wait(timeout=2)
            real_join = worker.join

            # Simulate the production 30-second join timing out immediately.
            with patch.object(worker, "join", return_value=None), pytest.raises(
                RuntimeError, match="still stopping"
            ):
                h.stop()

            backend.close.assert_not_called()
            assert h.has_live_worker()

            release.set()
            real_join(timeout=2)
            h.wait_for_worker_exit(timeout=2)

        assert not worker.is_alive()
        backend.close.assert_called_once()
        assert h._asr is None

    def test_pcm_reader_exits_on_eof_not_running_flag(self):
        """_pcm_reader must exit when os.read returns b'' (EOF), not on _running."""
        import numpy as np

        h = _helper()
        # Simulate two full chunks then EOF
        chunk_bytes = 512 * 4
        fake_chunk = (np.zeros(512, dtype=np.float32)).tobytes()
        read_sequence = [fake_chunk, fake_chunk, b""]
        read_iter = iter(read_sequence)

        proc = _FakeProcess()

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", side_effect=lambda fd, n: next(read_iter)), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"), \
             patch.object(NativeCaptureHelper, "_process_audio_chunk") as mock_proc:
            h.start()
            # Give pcm thread time to drain
            time.sleep(0.2)
            h.stop()

        # Both non-empty chunks should have been processed
        assert mock_proc.call_count == 2

    def test_stop_joins_pcm_thread_before_flush(self):
        """stop() must join _pcm_thread before flushing speech buffer."""
        import numpy as np

        h = _helper()
        # Single shared timeline so we can compare relative ordering
        timeline = []

        original_flush = NativeCaptureHelper._flush_speech
        original_join  = threading.Thread.join

        def tracking_flush(self_inner):
            timeline.append("flush")
            original_flush(self_inner)

        def tracking_join(t_self, timeout=None):
            if "pcm" in (t_self.name or ""):
                timeline.append("pcm_join")
            original_join(t_self, timeout=timeout)

        proc = _FakeProcess()

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"), \
             patch.object(NativeCaptureHelper, "_flush_speech", tracking_flush), \
             patch.object(threading.Thread, "join", tracking_join):
            h.start()
            time.sleep(0.05)  # let pcm thread reach EOF and exit
            # Put enough chunks in speech buffer to survive MIN_SPEECH_CHUNKS check
            h._speech_buffer = [np.zeros(512, dtype=np.float32)] * 10
            h._in_speech = True
            h.stop()

        # pcm_join must appear in timeline before flush
        assert "pcm_join" in timeline, "pcm_thread.join() was never called"
        assert "flush" in timeline, "_flush_speech() was never called"
        assert timeline.index("pcm_join") < timeline.index("flush"), (
            f"Expected pcm_join before flush, got: {timeline}"
        )

    def test_sentinel_sent_after_flush(self):
        """Sentinel (None) must be queued after speech buffer is flushed."""
        import numpy as np

        h = _helper()
        queue_items = []
        original_put = queue.Queue.put

        def tracking_put(q_self, item):
            queue_items.append(item)
            original_put(q_self, item)

        proc = _FakeProcess()

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"), \
             patch.object(queue.Queue, "put", tracking_put):
            h.start()
            # Add enough chunks to pass MIN_SPEECH_CHUNKS check
            h._speech_buffer = [np.zeros(512, dtype=np.float32)] * 10
            h._in_speech = True
            h.stop()

        # A non-None item (the speech segment) must appear before the sentinel
        non_sentinel = [i for i, v in enumerate(queue_items) if v is not None]
        sentinel     = [i for i, v in enumerate(queue_items) if v is None]
        assert sentinel, "Sentinel was never sent"
        if non_sentinel:
            assert non_sentinel[-1] < sentinel[0], (
                "Sentinel must be sent after all speech segments"
            )

    def test_chunks_read_counter_updated(self):
        """_chunks_read must reflect the number of full chunks processed."""
        import numpy as np

        h = _helper()
        chunk_bytes = 512 * 4
        fake_chunk = (np.zeros(512, dtype=np.float32)).tobytes()
        read_sequence = [fake_chunk, fake_chunk, fake_chunk, b""]
        read_iter = iter(read_sequence)

        proc = _FakeProcess()

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", side_effect=lambda fd, n: next(read_iter, b"")), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"), \
             patch.object(NativeCaptureHelper, "_process_audio_chunk"):
            h.start()
            time.sleep(0.2)
            h.stop()

        assert h._chunks_read == 3

    def test_stop_running_false_after_complete(self):
        """_running must be False after stop() completes."""
        h = _helper()
        proc = _FakeProcess()
        stack = _start_mocked(h, proc)
        h.stop()
        stack.__exit__(None, None, None)
        assert h._running is False

    def test_pcm_thread_join_timeout_skips_flush_and_drain(self):
        """If pcm_thread is still alive after join timeout, stop() must NOT flush or send sentinel."""
        import numpy as np

        h = _helper()
        flush_called = []
        sentinel_sent = []

        original_flush = NativeCaptureHelper._flush_speech
        def tracking_flush(self_inner):
            flush_called.append(True)
            original_flush(self_inner)

        original_put = queue.Queue.put
        def tracking_put(q_self, item):
            if item is None:
                sentinel_sent.append(True)
            original_put(q_self, item)

        # Fake a pcm_thread that appears alive even after join (simulates timeout)
        class _StuckThread:
            name = "native-pcm-reader"
            def join(self, timeout=None): pass
            def is_alive(self): return True

        proc = _FakeProcess()

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"), \
             patch.object(NativeCaptureHelper, "_flush_speech", tracking_flush), \
             patch.object(queue.Queue, "put", tracking_put):
            h.start()
            h._speech_buffer = [np.zeros(512, dtype=np.float32)] * 10
            h._in_speech = True
            # Swap in the stuck thread so stop() sees is_alive() == True
            h._pcm_thread = _StuckThread()
            with pytest.raises(RuntimeError, match="still stopping"):
                h.stop()

        assert flush_called == [], "flush must not run when pcm_thread is still alive (fatal path)"
        # Best-effort sentinel IS sent so the worker can drain already-queued items
        assert sentinel_sent == [True], "best-effort sentinel must be sent in fatal path"

    def test_partial_tail_zero_padded(self):
        """EOF with a partial buffer should zero-pad to CHUNK_SIZE and call _process_audio_chunk."""
        import numpy as np

        h = _helper()
        # 100 bytes = 25 float32 samples (well under the 512-sample chunk)
        partial = (np.zeros(25, dtype=np.float32)).tobytes()
        read_iter = iter([partial, b""])  # partial bytes then EOF

        proc = _FakeProcess()

        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", side_effect=lambda fd, n: next(read_iter, b"")), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", side_effect=lambda rlist, *a, **k: (rlist, [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"), \
             patch.object(NativeCaptureHelper, "_process_audio_chunk") as mock_proc:
            h.start()
            time.sleep(0.2)
            h.stop()

        # Exactly one call for the zero-padded tail chunk
        assert mock_proc.call_count == 1
        # The chunk passed must be exactly CHUNK_SIZE samples
        chunk_arg = mock_proc.call_args[0][0]
        assert chunk_arg.shape == (512,), f"Expected (512,) got {chunk_arg.shape}"

    def test_stop_event_exits_reader(self):
        """Setting _stop_event causes _pcm_reader to exit within a few select timeouts."""
        h = _helper()
        proc = _FakeProcess()

        # select never returns data — reader will loop checking _stop_event
        with patch("subprocess.Popen", return_value=proc), \
             patch("os.mkfifo"), \
             patch("os.open", return_value=5), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("fcntl.fcntl", return_value=0), \
             patch("select.select", return_value=([], [], [])), \
             patch("os.kill"), \
             patch.object(NativeCaptureHelper, "_load_models"):
            h.start()
            pcm_thread = h._pcm_thread
            assert pcm_thread is not None

            h._stop_event.set()
            pcm_thread.join(timeout=1.0)

        assert not pcm_thread.is_alive(), "pcm_thread should have exited after _stop_event was set"


# ── MAX_SEGMENT_SECONDS 强制切段 ──────────────────────────────────────────────

class TestMaxSegmentForceFlush:
    """Tests for MAX_SEGMENT_SECONDS force-flush via real _process_audio_chunk.

    torch is not installed in this test environment, so we inject a mock via
    sys.modules so the `import torch` inside _process_audio_chunk resolves to
    a MagicMock that always returns speech_prob=1.0 from the VAD.
    """

    def _make_mock_torch(self):
        import sys
        mock_torch = MagicMock()
        mock_chunk_t = MagicMock()
        mock_torch.from_numpy.return_value = mock_chunk_t
        mock_chunk_t.float.return_value = mock_chunk_t
        return mock_torch

    def _run_chunks_until_flush(self, h, n_chunks):
        import sys
        import numpy as np
        mock_torch = self._make_mock_torch()
        h._transcribe_queue.put = MagicMock()
        h._vad = MagicMock(return_value=MagicMock(item=lambda: 1.0))  # always speech
        chunk = np.zeros(512, dtype=np.float32)
        with patch.dict(sys.modules, {"torch": mock_torch}):
            for _ in range(n_chunks):
                h._process_audio_chunk(chunk)
                if h._flush_count > 0:
                    break

    def test_force_flush_when_segment_exceeds_max(self):
        """Continuous speech beyond MAX_SEGMENT_SECONDS triggers a force-flush."""
        from src.native_capture import MAX_SEGMENT_SECONDS, SAMPLE_RATE, CHUNK_SIZE
        h = _helper()
        chunks_needed = (MAX_SEGMENT_SECONDS * SAMPLE_RATE) // CHUNK_SIZE + 2
        self._run_chunks_until_flush(h, chunks_needed)
        assert h._flush_count >= 1, "Force-flush did not trigger"

    def test_force_flush_resets_speech_buffer(self):
        """After a force-flush, speech buffer should be cleared."""
        from src.native_capture import MAX_SEGMENT_SECONDS, SAMPLE_RATE, CHUNK_SIZE
        h = _helper()
        chunks_needed = (MAX_SEGMENT_SECONDS * SAMPLE_RATE) // CHUNK_SIZE + 2
        self._run_chunks_until_flush(h, chunks_needed)
        assert h._speech_buffer == [], "Speech buffer was not cleared after force-flush"


class TestOpenPrivacySettings:
    def test_calls_open_with_screen_capture_url(self):
        from app import API
        api = API()
        called_cmds: list = []

        def fake_popen(cmd, **kw):
            called_cmds.append(cmd)
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen):
            result = api.open_privacy_settings()

        assert result["status"] == "ok"
        assert any("Privacy_ScreenCapture" in str(c) for c in called_cmds)

    def test_microphone_target_opens_microphone_pane(self):
        from app import API
        api = API()
        called_cmds: list = []

        with patch("subprocess.Popen", side_effect=lambda cmd, **kw: called_cmds.append(cmd)):
            result = api.open_privacy_settings("microphone")

        assert result["status"] == "ok"
        assert any("Privacy_Microphone" in str(c) for c in called_cmds)

    def test_returns_error_dict_on_failure(self):
        from app import API
        api = API()
        with patch("subprocess.Popen", side_effect=OSError("no open binary")):
            result = api.open_privacy_settings()

        assert result["status"] == "error"
        assert "message" in result
