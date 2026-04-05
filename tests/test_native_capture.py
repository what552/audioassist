"""
Tests for src.native_capture.NativeCaptureHelper.

All tests mock the helper subprocess, FIFO, and model loading so they run
without the actual Swift binary or audio hardware.
"""
from __future__ import annotations

import io
import os
import signal
import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.native_capture import NativeCaptureHelper, _default_helper_path


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


# ── app.py: preflight_capture ─────────────────────────────────────────────────

class TestPreflightCapture:
    def test_mic_mode_always_supported(self):
        from app import API
        api = API()
        result = api.preflight_capture("mic")
        assert result["supported"] is True

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
             patch("os.access", return_value=True):
            result = api.preflight_capture("system")
        assert result["supported"] is True

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
        with patch("platform.mac_ver", return_value=("14.2.1", ("", "", ""), "")):
            result = api.preflight_capture("mic")
        assert result["os_version"] == "14.2.1"


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

    def test_returns_error_dict_on_failure(self):
        from app import API
        api = API()
        with patch("subprocess.Popen", side_effect=OSError("no open binary")):
            result = api.open_privacy_settings()

        assert result["status"] == "error"
        assert "message" in result
