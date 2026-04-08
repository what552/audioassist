# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AudioAssist.

Build with:
    pyinstaller AudioAssist.spec

Outputs:
    dist/AudioAssist.app   (macOS application bundle)
"""

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(SPEC))
HELPER_BIN = os.path.join(
    ROOT, "native", "AudioAssistCaptureHelper",
    ".build", "release", "AudioAssistCaptureHelper",
)

# Virtual-env site-packages used at build time.
# PyInstaller resolves imports from the active Python env, but data files
# (model weights, assets) that are loaded via open() / pkg_resources /
# importlib.resources must be declared explicitly.
VENV = "/Users/feifei/programing/local asr/.venv"
SITE = os.path.join(VENV, "lib", "python3.12", "site-packages")

# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[
        # Bundle the native Swift helper next to the executable so
        # _default_helper_path() can find it via sys._MEIPASS / sys.executable.
        (HELPER_BIN, "."),
        # Bundle ffmpeg and ffprobe so audio_utils.py works without system install.
        ("/opt/homebrew/bin/ffmpeg",  "."),
        ("/opt/homebrew/bin/ffprobe", "."),
        # torchcodec dylibs intentionally NOT bundled — torchcodec embeds its own
        # libpython3.12.dylib which conflicts with the app's libpython and causes
        # SIGSEGV on startup.  diarize.py bypasses torchcodec by passing a waveform
        # dict directly to pyannote, so none of these dylibs are needed at runtime.
    ],
    datas=[
        # UI assets (HTML / JS / CSS / images)
        (os.path.join(ROOT, "ui"), "ui"),

        # ── silero_vad ────────────────────────────────────────────────────────
        # silero_vad loads model weights from its own data/ directory at runtime
        # (silero_vad.data sub-package with .jit / .onnx / .safetensors files).
        # PyInstaller does not collect these automatically because they are not
        # imported — they are opened via importlib.resources / __file__ paths.
        (os.path.join(SITE, "silero_vad"), "silero_vad"),

        # ── mlx_whisper ───────────────────────────────────────────────────────
        # Include the full mlx_whisper package (Python files + assets).
        (os.path.join(SITE, "mlx_whisper"), "mlx_whisper"),

        # ── mlx ──────────────────────────────────────────────────────────────
        # MLX Apple-Silicon framework: contains compiled .so / Metal shaders.
        # PyInstaller collects the .so binaries but misses data files.
        (os.path.join(SITE, "mlx"), "mlx"),

        # ── pyannote ──────────────────────────────────────────────────────────
        # pyannote is a namespace package; include the full pyannote/ tree so
        # that pyannote.audio, pyannote.core, pyannote.database, etc. are all
        # available at runtime (importlib.resources paths, config files, etc.).
        (os.path.join(SITE, "pyannote"), "pyannote"),

        # ── nagisa ────────────────────────────────────────────────────────────
        # nagisa loads its dict/model files from nagisa/data/ at import time
        # via __file__-relative paths; include the entire nagisa package dir.
        (os.path.join(SITE, "nagisa"), "nagisa"),

        # ── qwen_asr ─────────────────────────────────────────────────────────
        # qwen_asr loads inference/assets/korean_dict_jieba.dict at runtime
        # via __file__-relative paths.
        (os.path.join(SITE, "qwen_asr"), "qwen_asr"),

    ],
    hiddenimports=[
        # pywebview backend on macOS
        "webview.platforms.cocoa",
        # common transitive imports that PyInstaller may miss
        "platformdirs",
        "huggingface_hub",
        # silero_vad — the data sub-package is accessed via importlib.resources
        # and is not discovered by static analysis
        "silero_vad",
        "silero_vad.data",
        # scienceplots — required by torchmetrics/utilities/plot.py at import time
        "scienceplots",
        # torchcodec — pyannote.audio/core/io.py imports AudioDecoder at module level
        # Use hiddenimports (not datas) to avoid bundling .dylibs/libpython3.12.dylib
        # which conflicts with the app's own libpython and causes SIGSEGV.
        *collect_submodules("torchcodec"),
        # pyannote — namespace package, collect all submodules explicitly
        *collect_submodules("pyannote.audio"),
        *collect_submodules("pyannote.core"),
        *collect_submodules("pyannote.database"),
        *collect_submodules("pyannote.pipeline"),
        # mlx_whisper — collect all submodules
        *collect_submodules("mlx_whisper"),
        # nagisa — bare imports in train.py need submodules collected
        *collect_submodules("nagisa"),
    ],
    excludes=[
        "tests",
        "agentops",
        # torchcodec is intentionally excluded: its custom_ops dylib embeds a
        # hard link to libpython3.12.dylib that causes SIGSEGV when loaded
        # inside a PyInstaller bundle.  diarize.py passes {"waveform": tensor,
        # "sample_rate": int} to the pyannote pipeline instead of a raw file
        # path, bypassing torchcodec entirely.
        "torchcodec",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, "scripts", "hook_runtime_metadata.py")],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── PYZ (bytecode archive) ─────────────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ───────────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,         # native arch; use lipo for universal if needed
    codesign_identity=None,   # sign separately with codesign(1)
    entitlements_file=os.path.join(ROOT, "entitlements.plist"),
)

# ── COLLECT (onedir layout) ────────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AudioAssist",
)

# ── BUNDLE (.app) ─────────────────────────────────────────────────────────────
app = BUNDLE(
    coll,
    name="AudioAssist.app",
    icon=None,                # set to .icns path when available
    bundle_identifier="com.audioassist.app",
    info_plist={
        "CFBundleName":                "AudioAssist",
        "CFBundleDisplayName":         "AudioAssist",
        "CFBundleVersion":             "1.0.0",
        "CFBundleShortVersionString":  "1.0.0",
        "NSMicrophoneUsageDescription":
            "AudioAssist needs microphone access for live transcription.",
        "NSAppleMusicUsageDescription":
            "AudioAssist uses audio input for transcription.",
        # ScreenCaptureKit (system / mix capture modes)
        "NSScreenCaptureUsageDescription":
            "AudioAssist needs screen recording permission to capture system audio.",
        "LSUIElement": False,
    },
)
