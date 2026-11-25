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
