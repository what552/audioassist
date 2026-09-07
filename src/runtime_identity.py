"""Authoritative runtime version and location information for CLI and desktop UI."""
from __future__ import annotations

from importlib import metadata
from pathlib import Path
import plistlib
import sys
import tomllib


PROJECT_NAME = "audioassist-local"


def _source_root() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    return root if (root / "pyproject.toml").is_file() else None


def _app_bundle() -> Path | None:
    executable = Path(sys.executable)
    for executable_path in (executable, executable.resolve()):
        for candidate in (executable_path, *executable_path.parents):
            if candidate.suffix == ".app" and (candidate / "Contents/Info.plist").is_file():
                return candidate
    return None


def _source_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as project_file:
        return str(tomllib.load(project_file)["project"]["version"])


def _bundle_version(app: Path) -> str:
    with (app / "Contents/Info.plist").open("rb") as info_file:
        return str(plistlib.load(info_file)["CFBundleShortVersionString"])


def get_runtime_identity() -> dict[str, str | None]:
    """Return JSON-safe identity data without trusting stale editable metadata."""
    app = _app_bundle()
    source = _source_root()
    if app is not None:
        mode = "packaged-app"
        version = _bundle_version(app)
    elif source is not None:
        mode = "source"
        version = _source_version(source)
    else:
        mode = "installed-package"
        try:
            version = metadata.version(PROJECT_NAME)
        except metadata.PackageNotFoundError:
            version = "unknown"

    return {
        "version": version,
        "mode": mode,
        "app_path": str(app) if app is not None else None,
        "source_root": str(source) if source is not None else None,
        "entrypoint": str(Path(sys.argv[0]).resolve()),
        "python_executable": str(Path(sys.executable).absolute()),
    }


def format_runtime_identity(identity: dict[str, str | None]) -> str:
    """Human-readable CLI representation of :func:`get_runtime_identity`."""
    location = identity.get("app_path") or identity.get("source_root") or identity.get("entrypoint")
    return "\n".join(
        (
            f"AudioAssist {identity['version']}",
            f"Mode: {identity['mode']}",
            f"Location: {location}",
            f"Python: {identity['python_executable']}",
        )
    )
