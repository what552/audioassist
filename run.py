#!/usr/bin/env python3
"""
AudioAssist startup entry point.
"""
import logging
import os
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _maybe_reexec_with_pythonw() -> None:
    """
    On Windows, opening a .py file with python.exe creates a transient console
    window tied to the GUI process. Re-launch under pythonw.exe unless the user
    explicitly asked to keep the console for debugging.
    """
    if sys.platform != "win32":
        return
    if os.environ.get("AUDIOASSIST_GUI_REEXEC") == "1":
        return
    if os.path.basename(sys.executable).lower() != "python.exe":
        return
    if "--console" in sys.argv:
        sys.argv.remove("--console")
        return
    if "--debug" in sys.argv:
        return

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        return

    env = os.environ.copy()
    env["AUDIOASSIST_GUI_REEXEC"] = "1"
    subprocess.Popen(
        [pythonw, os.path.abspath(__file__), *sys.argv[1:]],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        close_fds=True,
    )
    raise SystemExit(0)


_maybe_reexec_with_pythonw()

import webview
import app as _app_module
from app import API


def main():
    api = API()
    ui_path = os.path.join(os.path.dirname(__file__), "ui", "index.html")

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
    main()
