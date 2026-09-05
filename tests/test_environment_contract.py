"""Repository Python environment and build entrypoints stay unified."""
from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_pyproject_is_the_static_dependency_source():
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]

    assert "dynamic" not in project
    assert "qwen-asr==0.0.6" in project["dependencies"]
    assert "torch==2.10.0" in project["dependencies"]
    assert "pyannote.audio==4.0.4" in project["dependencies"]
    assert set(project["optional-dependencies"]) == {"mac", "other", "dev", "build"}
    assert not (ROOT / "requirements.txt").exists()


def test_lock_file_exists():
    assert (ROOT / "uv.lock").is_file()


def test_bootstrap_uses_locked_uv_sync_and_repository_venv():
    script = _text("scripts/bootstrap.sh")

    assert "uv sync --frozen" in script
    assert 'PLATFORM_EXTRA="mac"' in script
    assert 'PLATFORM_EXTRA="other"' in script
    assert '.venv/bin/python' in script


def test_dmg_build_uses_repository_python():
    script = _text("scripts/build_dmg.sh")

    assert 'PYTHON_BIN="${ROOT}/.venv/bin/python"' in script
    assert '"$PYTHON_BIN" -m PyInstaller' in script
    assert "\n  python -m PyInstaller" not in script


def test_pyinstaller_spec_uses_active_interpreter_site_packages():
    spec = _text("AudioAssist.spec")

    assert 'sysconfig.get_path("purelib")' in spec
    assert "/Users/" not in spec
    assert "local asr/.venv" not in spec
