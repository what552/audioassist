"""Windows packaging boundaries, using synthetic paths without audio/network."""
from pathlib import Path

from src import audio_utils, runtime_identity


def test_windows_bundle_prefers_bundled_executable_over_path(tmp_path, monkeypatch):
    tool = tmp_path / "ffprobe.exe"
    tool.write_bytes(b"synthetic")
    monkeypatch.setattr(audio_utils.sys, "frozen", True, raising=False)
    monkeypatch.setattr(audio_utils.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(audio_utils.sys, "platform", "win32")
    assert audio_utils._find_bin("ffprobe") == str(tool)


def test_windows_packaged_identity_is_the_executable(tmp_path, monkeypatch):
    executable = tmp_path / "AudioAssist.exe"
    monkeypatch.setattr(runtime_identity.sys, "executable", str(executable))
    monkeypatch.setattr(runtime_identity.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_identity, "_source_root", lambda: None)
    monkeypatch.setattr(runtime_identity.metadata, "version", lambda name: "3.0.0")
    data = runtime_identity.get_runtime_identity()
    assert data["version"] == "3.0.0"
    assert data["mode"] == "packaged-executable"
    assert data["app_path"] == str(executable)
    assert data["source_root"] is None


def test_windows_system_and_mix_have_actionable_preflight(monkeypatch):
    import app
    monkeypatch.setattr(app.sys, "platform", "win32")
    for mode in ("system", "mix"):
        result = app.API().preflight_capture(mode)
        assert result["supported"] is False
        assert "not implemented" in result["reason"]


def test_windows_whisper_requires_complete_ctranslate2_weights(tmp_path, monkeypatch):
    import pytest
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows model catalog")
    from src.model_manager import ModelManager
    from src.realtime_models import resolve_realtime_model
    manager = ModelManager(models_dir=str(tmp_path))
    monkeypatch.setattr(manager, "_hf_cache_path", lambda model_id: None)
    path = tmp_path / "whisper-tiny-cpu"
    path.mkdir()
    (path / "config.json").write_text("{}")
    assert manager.is_downloaded("whisper-tiny-cpu") is False
    for name in ("model.bin", "tokenizer.json", "vocabulary.txt"):
        (path / name).write_bytes(b"synthetic fixture")
    result = resolve_realtime_model(model_id="whisper-tiny-cpu", manager=manager)
    assert result.engine == "whisper"
    assert result.local_path == str(path)


def test_windows_pid_probe_never_signals_a_live_process(monkeypatch):
    import os
    import pytest
    from src.local_api import _pid_is_alive
    if os.name != "nt":
        pytest.skip("Windows process probe")
    def forbidden(*args):
        pytest.fail("A Windows liveness check must never call os.kill")
    monkeypatch.setattr(os, "kill", forbidden)
    assert _pid_is_alive(os.getpid()) is True
    assert _pid_is_alive(-1) is False
    assert _pid_is_alive(0) is False


def test_windows_discovery_acl_is_protected_and_owner_only(tmp_path):
    import ctypes
    import os
    import pytest
    from ctypes import wintypes
    from src.local_api import _protect_local_file
    if os.name != "nt":
        pytest.skip("Windows DACL")
    path = tmp_path / "credential.json"
    with path.open("wb") as file:
        _protect_local_file(str(path), file.fileno())
        file.write(b"synthetic")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileSecurityW.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    api.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(needed))
    descriptor = ctypes.create_string_buffer(needed.value)
    assert api.GetFileSecurityW(str(path), 4, descriptor, needed.value, ctypes.byref(needed))
    text = wintypes.LPWSTR()
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    assert api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None)
    try:
        value = text.value
        assert value.startswith("D:P")
        assert value.count("(A;") == 3
        for principal in ("SY", "BA", "OW"):
            assert f";;;{principal})" in value
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
