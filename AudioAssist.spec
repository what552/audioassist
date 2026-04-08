# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AudioAssist.

Build with:
    pyinstaller AudioAssist.spec

Outputs:
    dist/AudioAssist.app   (macOS application bundle)
"""

import os

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
        # mlx_whisper loads mel_filters.npz and tiktoken vocab files from its
        # assets/ directory at transcription start.
        (os.path.join(SITE, "mlx_whisper", "assets"), os.path.join("mlx_whisper", "assets")),

        # ── pyannote.audio ────────────────────────────────────────────────────
        # pyannote.audio loads telemetry/config.yaml at import time via
        # importlib.resources; include the whole telemetry directory.
        (os.path.join(SITE, "pyannote", "audio", "telemetry"),
         os.path.join("pyannote", "audio", "telemetry")),
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
        # pyannote namespace packages (implicit namespace, no __init__.py)
        "pyannote",
        "pyannote.audio",
        "pyannote.audio.pipelines",
        "pyannote.audio.pipelines.speaker_diarization",
        # mlx_whisper
        "mlx_whisper",
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
    runtime_hooks=[],
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
