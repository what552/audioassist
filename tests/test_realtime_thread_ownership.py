"""Realtime ASR backends must stay on their owner worker thread."""
from __future__ import annotations

import io
import threading
import types
from unittest.mock import MagicMock, patch

import numpy as np

from src.native_capture import NativeCaptureHelper
from src.realtime import RealtimeTranscriber
from src.types import TranscriptResult


class _ThreadBoundBackend:
    backend_name = "thread-bound"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def load(self) -> None:
        self.calls.append(("load", threading.get_ident()))

    def transcribe(self, _path: str) -> TranscriptResult:
        self.calls.append(("transcribe", threading.get_ident()))
        return TranscriptResult(text="owner thread", language="en")

    def close(self) -> None:
        self.calls.append(("close", threading.get_ident()))


class _FailingThreadBoundBackend(_ThreadBoundBackend):
    def load(self) -> None:
        super().load()
        raise RuntimeError("model load failed")


def _silero_module() -> types.ModuleType:
    module = types.ModuleType("silero_vad")
    module.load_silero_vad = MagicMock(return_value=MagicMock())
    return module


def _resolved_model() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        engine="qwen",
        model_id="qwen3-asr-1.7b",
        local_path="/models/qwen",
    )


def _assert_single_owner(backend: _ThreadBoundBackend) -> None:
    stages = [stage for stage, _ in backend.calls]
    assert stages == ["load", "transcribe", "close"]
    assert len({thread_id for _, thread_id in backend.calls}) == 1


def test_microphone_backend_load_inference_and_close_share_worker_thread() -> None:
    backend = _ThreadBoundBackend()
    results = []
    stream = MagicMock()
    sounddevice = types.ModuleType("sounddevice")
    sounddevice.__version__ = "test"
    sounddevice.InputStream = MagicMock(return_value=stream)

    with patch.dict(
        "sys.modules",
        {"silero_vad": _silero_module(), "sounddevice": sounddevice},
    ), patch("src.realtime.resolve_realtime_model", return_value=_resolved_model()), patch(
        "src.realtime.create_qwen_engine", return_value=backend
    ):
        realtime = RealtimeTranscriber(on_result=results.append, qwen_backend="mlx")
        realtime.start()
        realtime._transcribe_queue.put(
            ([np.zeros(512, dtype=np.float32)] * 5, 0.0, 0.16)
        )
        realtime._transcribe_queue.join()
        realtime.stop()

    assert results and results[0]["text"] == "owner thread"
    _assert_single_owner(backend)


def test_microphone_load_failure_closes_on_worker_before_capture() -> None:
    backend = _FailingThreadBoundBackend()
    sounddevice = types.ModuleType("sounddevice")
    sounddevice.InputStream = MagicMock()

    with patch.dict(
        "sys.modules",
        {"silero_vad": _silero_module(), "sounddevice": sounddevice},
    ), patch("src.realtime.resolve_realtime_model", return_value=_resolved_model()), patch(
        "src.realtime.create_qwen_engine", return_value=backend
    ):
        realtime = RealtimeTranscriber(qwen_backend="mlx")
        try:
            realtime.start()
        except RuntimeError as exc:
            assert str(exc) == "model load failed"
        else:
            raise AssertionError("model load error was not propagated")

    assert [stage for stage, _ in backend.calls] == ["load", "close"]
    assert len({thread_id for _, thread_id in backend.calls}) == 1
    assert realtime._worker_thread is None
    sounddevice.InputStream.assert_not_called()


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 42000
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self._exit_code = None

    def poll(self):
        return self._exit_code

    def terminate(self) -> None:
        self._exit_code = -15

    def kill(self) -> None:
        self._exit_code = -9

    def wait(self, timeout=None):
        return self._exit_code or 0


def test_native_backend_load_inference_and_close_share_worker_thread() -> None:
    backend = _ThreadBoundBackend()
    results = []
    helper = NativeCaptureHelper(
        mode="mix",
        output_path="/tmp/thread-owner.wav",
        helper_path="/fake/AudioAssistCaptureHelper",
        on_result=results.append,
        qwen_backend="mlx",
    )

    with patch.dict("sys.modules", {"silero_vad": _silero_module()}), patch(
        "src.native_capture.resolve_realtime_model", return_value=_resolved_model()
    ), patch("src.native_capture.create_qwen_engine", return_value=backend), patch(
        "subprocess.Popen", return_value=_FakeProcess()
    ), patch("os.path.exists", return_value=False), patch("os.mkfifo"), patch(
        "os.open", return_value=5
    ), patch("os.read", return_value=b""), patch("os.close"), patch(
        "fcntl.fcntl", return_value=0
    ), patch(
        "select.select", side_effect=lambda rlist, *args, **kwargs: (rlist, [], [])
    ), patch("os.kill"):
        helper.start()
        helper._transcribe_queue.put(
            ([np.zeros(512, dtype=np.float32)] * 5, 0.0, 0.16)
        )
        helper._transcribe_queue.join()
        helper.stop()

    assert results and results[0]["text"] == "owner thread"
    _assert_single_owner(backend)


def test_native_load_failure_closes_on_worker_before_helper_launch() -> None:
    backend = _FailingThreadBoundBackend()
    helper = NativeCaptureHelper(
        mode="system",
        output_path="/tmp/thread-owner-error.wav",
        helper_path="/fake/AudioAssistCaptureHelper",
        qwen_backend="mlx",
    )

    with patch.dict("sys.modules", {"silero_vad": _silero_module()}), patch(
        "src.native_capture.resolve_realtime_model", return_value=_resolved_model()
    ), patch("src.native_capture.create_qwen_engine", return_value=backend), patch(
        "subprocess.Popen"
    ) as popen:
        try:
            helper.start()
        except RuntimeError as exc:
            assert str(exc) == "model load failed"
        else:
            raise AssertionError("model load error was not propagated")

    assert [stage for stage, _ in backend.calls] == ["load", "close"]
    assert len({thread_id for _, thread_id in backend.calls}) == 1
    assert helper._worker_thread is None
    popen.assert_not_called()
