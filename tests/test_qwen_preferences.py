import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock
import threading

import pytest

from src import preferences as prefs


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.delenv(prefs.QWEN_BACKEND_ENV, raising=False)


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(prefs, "backend_options", lambda: [
        {"value": value, "available": True, "reason": "", "label": value}
        for value in prefs.BACKENDS])


@pytest.mark.parametrize("backend", prefs.BACKENDS)
def test_persist_across_instances_preserves_other_settings(tmp_path, available, backend):
    path = str(tmp_path / "config.json")
    prefs.update_config(path, lambda data: data.update(api={"api_key": "private"}, storage={"output_dir": "/data"}))
    prefs.save_backend_settings(path, backend)
    assert prefs.default_backend(path) == backend
    assert prefs.get_backend_settings(path)["saved"] == backend
    assert prefs.read_config(path)["api"] == {"api_key": "private"}
    assert prefs.read_config(path)["storage"] == {"output_dir": "/data"}


def test_environment_precedence_is_visible_and_does_not_overwrite_saved(tmp_path, available, monkeypatch):
    path = str(tmp_path / "config.json")
    prefs.save_backend_settings(path, "mlx")
    monkeypatch.setenv(prefs.QWEN_BACKEND_ENV, "pytorch-mps")
    result = prefs.get_backend_settings(path)
    assert result["saved"] == "mlx" and result["effective"] == "pytorch-mps"
    assert result["environment_override"]


@pytest.mark.parametrize("value", [None, 1, "", "bad", "CPU"])
def test_invalid_preference_never_saved(tmp_path, value):
    path = str(tmp_path / "config.json")
    with pytest.raises(ValueError):
        prefs.save_backend_settings(path, value)
    assert not (tmp_path / "config.json").exists()


def test_unavailable_does_not_fallback_or_replace_preference(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    prefs.update_config(path, lambda data: data.update(qwen_backend="pytorch-cpu"))
    monkeypatch.setattr(prefs, "backend_options", lambda: [{"value": "mlx", "available": False, "reason": "unsupported hardware"}])
    with pytest.raises(ValueError, match="unsupported hardware"):
        prefs.save_backend_settings(path, "mlx")
    assert prefs.default_backend(path) == "pytorch-cpu"


def test_failed_atomic_write_retains_previous_config(tmp_path, available, monkeypatch):
    path = str(tmp_path / "config.json")
    prefs.save_backend_settings(path, "pytorch-cpu")
    monkeypatch.setattr(prefs.os, "replace", MagicMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        prefs.save_backend_settings(path, "mlx")
    assert prefs.default_backend(path) == "pytorch-cpu"
    assert list(tmp_path.glob(".config-*")) == []


def test_corrupt_config_not_overwritten(tmp_path, available):
    path = tmp_path / "config.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        prefs.save_backend_settings(str(path), "mlx")
    assert path.read_text() == "broken"


def test_invalid_environment_never_silently_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv(prefs.QWEN_BACKEND_ENV, "bad")
    with pytest.raises(ValueError, match="Unknown Qwen backend"):
        prefs.default_backend(str(tmp_path / "config.json"))


def test_default_auto_without_settings(tmp_path):
    assert prefs.default_backend(str(tmp_path / "absent.json")) == "auto"


def test_unavailable_options_include_failure_reason(monkeypatch):
    def select(value):
        if value in {"mlx", "pytorch-mps"}:
            raise RuntimeError("Apple Silicon required")
    monkeypatch.setattr(prefs, "select_qwen_backend", select)
    options = {item["value"]: item for item in prefs.backend_options()}
    assert options["pytorch-cpu"]["available"]
    assert not options["mlx"]["available"]
    assert options["pytorch-mps"]["reason"] == "Apple Silicon required"


def test_concurrent_preference_updates_preserve_all_sections(tmp_path):
    path = str(tmp_path / "config.json")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: prefs.update_config(path, lambda data: data.update({str(i): i})), range(32)))
    assert len(prefs.read_config(path)) == 32


@pytest.mark.parametrize("operation,role,key", [
    ("select_asr_model", "asr", "asr_model"),
    ("select_aligner_model", "aligner", "aligner_model"),
    ("select_diarizer_model", "diarizer", "diarizer_model"),
    ("delete", "asr", "asr_model"),
])
def test_model_update_interleaved_with_backend_save(tmp_path, monkeypatch, available, operation, role, key):
    from src import model_manager as mm
    path = str(tmp_path / "config.json")
    model_id = next(item.id for item in mm.CATALOG if item.role == role)
    prefs.update_config(path, lambda data: data.update(qwen_backend="pytorch-cpu", **{key: model_id}))
    monkeypatch.setattr(mm, "CONFIG_PATH", path)
    manager = mm.ModelManager(models_dir=str(tmp_path / "models"))
    monkeypatch.setattr(manager, "is_downloaded", lambda _: True)
    read_started, release_read, save_started = threading.Event(), threading.Event(), threading.Event()
    original_read = prefs.read_config

    def delayed_read(filename):
        data = original_read(filename)
        if threading.current_thread().name.startswith("model-edit"):
            read_started.set()
            assert release_read.wait(3)
        return data

    monkeypatch.setattr(prefs, "read_config", delayed_read)
    def save_backend():
        save_started.set()
        prefs.save_backend_settings(path, "mlx")

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-edit") as models, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="backend-edit") as backends:
        model_future = models.submit(getattr(manager, operation), model_id)
        assert read_started.wait(3)
        save_future = backends.submit(save_backend)
        assert save_started.wait(3)
        release_read.set()
        model_future.result(timeout=3)
        save_future.result(timeout=3)
    result = prefs.read_config(path)
    assert result["qwen_backend"] == "mlx"
    assert (key not in result) if operation == "delete" else result[key] == model_id


def test_api_save_and_reopen_and_other_settings_preserve_backend(tmp_path, available, monkeypatch):
    import app
    monkeypatch.setattr(app, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(app, "APP_DATA_DIR", str(tmp_path))
    assert app.API().save_qwen_backend_settings("mlx") == {"ok": True}
    api = app.API()
    api.save_api_config({"model": "existing"})
    api.set_obsidian_config("/vault", True)
    assert api.get_qwen_backend_settings()["saved"] == "mlx"
    assert not api.save_qwen_backend_settings("bad")["ok"]


@pytest.mark.parametrize("mode", ["mic", "system", "mix"])
def test_recording_snapshots_default_before_worker_starts(tmp_path, monkeypatch, mode):
    import app
    queued = []
    path = str(tmp_path / "config.json")
    prefs.update_config(path, lambda data: data.update(qwen_backend="mlx"))
    monkeypatch.setattr(app, "CONFIG_PATH", path)
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_macos_microphone_permission_status", lambda: "authorized")
    monkeypatch.setattr(app, "_macos_screen_recording_permission_status", lambda: "authorized")
    monkeypatch.setattr(app.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: queued.append(target)))
    monkeypatch.setattr(app, "_push", lambda _: None)
    factory = MagicMock()
    monkeypatch.setattr("src.realtime.RealtimeTranscriber", factory)
    monkeypatch.setattr("src.native_capture.NativeCaptureHelper", factory)
    api = app.API()
    monkeypatch.setattr(api, "_caffeinate_start", lambda: None)
    assert api.start_realtime({"capture_mode": mode})["status"] == "started"
    prefs.update_config(path, lambda data: data.update(qwen_backend="pytorch-cpu"))
    queued[0]()
    assert factory.call_args.kwargs["qwen_backend"] == "mlx"


def test_file_transcription_snapshots_default_before_worker_starts(tmp_path, monkeypatch):
    import app
    queued = []
    audio_path = tmp_path / "local.wav"
    audio_path.write_bytes(b"RIFF")
    path = str(tmp_path / "config.json")
    prefs.update_config(path, lambda data: data.update(qwen_backend="pytorch-mps"))
    monkeypatch.setattr(app, "CONFIG_PATH", path)
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: queued.append(target)))
    monkeypatch.setattr(app, "_push", lambda _: None)
    service = MagicMock()
    service.transcribe.side_effect = RuntimeError("stop after observing call")
    monkeypatch.setattr(app, "_service", lambda: service)
    app.API().transcribe(str(audio_path), {}, job_id="snapshot-test")
    prefs.update_config(path, lambda data: data.update(qwen_backend="mlx"))
    queued[0]()
    assert service.transcribe.call_args.kwargs["qwen_backend"] == "pytorch-mps"


@pytest.mark.parametrize("explicit,expected", [(None, "mlx"), ("pytorch-cpu", "pytorch-cpu")])
def test_shared_service_uses_saved_default_and_explicit_snapshot(tmp_path, monkeypatch, explicit, expected):
    from src.service import AudioAssistService
    path = str(tmp_path / "config.json")
    prefs.update_config(path, lambda data: data.update(qwen_backend="mlx"))
    source = tmp_path / "audio.wav"
    source.write_bytes(b"fixture")
    pipeline = MagicMock(side_effect=RuntimeError("stop after observing call"))
    monkeypatch.setattr("src.pipeline.run", pipeline)
    with pytest.raises(RuntimeError):
        AudioAssistService(str(tmp_path), path).transcribe(str(source), qwen_backend=explicit)
    assert pipeline.call_args.kwargs["qwen_backend"] == expected
