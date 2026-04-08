#!/usr/bin/env python3
"""
AudioAssist startup entry point.
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import webview
import app as _app_module
from app import API


def main():
    api = API()
    # Resolve UI path for both development and PyInstaller frozen builds.
    # In frozen mode, bundled resources land in sys._MEIPASS (onefile) or
    # next to sys.executable (onedir).
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    ui_path = os.path.join(base, "ui", "index.html")

    window = webview.create_window(
        "AudioAssist",
        url=f"file://{ui_path}",
        js_api=api,
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    _app_module._window = window
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    # Required for PyInstaller + multiprocessing on macOS (spawn mode).
    # Without this, spawned subprocesses re-run main() and open extra windows.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
