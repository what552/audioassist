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
    ],
    hiddenimports=[
        # pywebview backend on macOS
        "webview.platforms.cocoa",
        # common transitive imports that PyInstaller may miss
        "platformdirs",
        "huggingface_hub",
    ],
    excludes=[
        "tests",
        "agentops",
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
