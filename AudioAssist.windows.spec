# -*- mode: python ; coding: utf-8 -*-
"""Windows x64 onedir bundle; the macOS/Swift/MLX spec stays independent."""
import os
import shutil
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

ROOT = Path(SPEC).resolve().parent
APP_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
if sys.platform != "win32" or sys.maxsize <= 2**32:
    raise SystemExit("Build with the repository Windows x64 Python 3.12 environment")
if version("audioassist-local") != APP_VERSION:
    raise SystemExit("Refresh the repository editable install before packaging")
TOOLS = Path(os.environ.get("AUDIOASSIST_FFMPEG_DIR", "."))
ffmpeg = str(TOOLS / "ffmpeg.exe") if (TOOLS / "ffmpeg.exe").is_file() else shutil.which("ffmpeg")
ffprobe = str(TOOLS / "ffprobe.exe") if (TOOLS / "ffprobe.exe").is_file() else shutil.which("ffprobe")
if not ffmpeg or not ffprobe:
    raise SystemExit("Set AUDIOASSIST_FFMPEG_DIR to the directory containing ffmpeg.exe and ffprobe.exe")

datas = [(str(ROOT / "ui"), "ui"), (str(ROOT / "LICENSE"), "licenses/AudioAssist")]
for package in ("silero_vad", "qwen_asr", "pyannote.audio", "pyannote.database", "faster_whisper"):
    datas += collect_data_files(package)
# nagisa's training module uses bare imports (model/prepro/tagger). The shared
# runtime hook adds this real directory to sys.path; bytecode-only is insufficient.
datas += collect_data_files("nagisa", include_py_files=True)
for distribution in ("audioassist-local", "qwen-asr", "transformers", "faster-whisper"):
    datas += copy_metadata(distribution)
# Keep the upstream FFmpeg license/source information with the shipped tools.
for name in ("LICENSE", "README.txt"):
    path = Path(ffmpeg).parent.parent / name
    if path.is_file():
        datas.append((str(path), "licenses/ffmpeg"))

a = Analysis(
    [str(ROOT / "scripts/windows_entry.py")], pathex=[str(ROOT)],
    binaries=[(ffmpeg, "."), (ffprobe, ".")], datas=datas,
    hiddenimports=[
        "run", "app", "src.cli", "webview.platforms.winforms", "webview.platforms.edgechromium",
        "silero_vad.data", "sounddevice", "scipy.special._special_ufuncs",
        *collect_submodules("qwen_asr", filter=lambda name: ".cli" not in name),
        *collect_submodules("pyannote.audio"),
        *collect_submodules("pyannote.core"),
        *collect_submodules("pyannote.database"),
        *collect_submodules("pyannote.pipeline"),
        *collect_submodules("nagisa"),
    ],
    excludes=["tests", "agentops", "mlx", "mlx_whisper", "mlx_qwen3_asr",
              "webview.platforms.cocoa", "torchcodec", "tkinter", "IPython", "notebook"],
    runtime_hooks=[str(ROOT / "scripts/hook_runtime_metadata.py")],
    noarchive=False,
)
pyz = PYZ(a.pure)
number = tuple(map(int, APP_VERSION.split("."))) + (0,)
pe_version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=number, prodvers=number, mask=0x3f, flags=0,
                      OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("FileDescription", "AudioAssist"),
        StringStruct("FileVersion", APP_VERSION),
        StringStruct("ProductName", "AudioAssist"),
        StringStruct("ProductVersion", APP_VERSION),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AudioAssist",
          console=False, debug=False, strip=False, upx=False, version=pe_version)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AudioAssist-CLI",
          console=True, debug=False, strip=False, upx=False, version=pe_version)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False,
               name=f"AudioAssist-{APP_VERSION}-windows-x64")
