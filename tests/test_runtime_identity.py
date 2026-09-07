"""Runtime version/path identity for source and packaged applications."""
from __future__ import annotations

from pathlib import Path
import plistlib

from src import runtime_identity


def test_source_identity_reads_pyproject_instead_of_stale_metadata():
    identity = runtime_identity.get_runtime_identity()

    assert identity["version"] == "2.0.3"
    assert identity["mode"] == "source"
    assert identity["source_root"] == str(Path(__file__).parents[1])
    assert identity["app_path"] is None
    assert identity["python_executable"] == str(Path(runtime_identity.sys.executable).absolute())


def test_packaged_identity_prefers_bundle_version_and_path(tmp_path, monkeypatch):
    app = tmp_path / "AudioAssist.app"
    executable = app / "Contents/MacOS/AudioAssist"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    with (app / "Contents/Info.plist").open("wb") as info_file:
        plistlib.dump({"CFBundleShortVersionString": "9.8.7"}, info_file)
    monkeypatch.setattr(runtime_identity.sys, "executable", str(executable))

    identity = runtime_identity.get_runtime_identity()

    assert identity["version"] == "9.8.7"
    assert identity["mode"] == "packaged-app"
    assert identity["app_path"] == str(app)
