#!/usr/bin/env python3
"""
AudioAssist startup entry point.
"""
import logging
import os
import subprocess
import sys
import threading

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


import webview
import app as _app_module
from app import API
from src.local_api import (
    DesktopRecordingController,
    LocalAPIError,
    LocalAPIServer,
)
from src.runtime_logging import configure_runtime_file_logging


def _start_desktop_local_api(api: API) -> LocalAPIServer | None:
    """Start the app-owned loopback adapter without preventing GUI startup."""
    server = LocalAPIServer(
        _app_module._service(),
        mode="desktop",
        app_data_dir=_app_module.APP_DATA_DIR,
        recording=DesktopRecordingController(api),
    )
    try:
        server.start()
    except (LocalAPIError, OSError):
        logging.getLogger(__name__).warning(
            "Local API unavailable; another service may already be active",
            exc_info=True,
        )
        return None
    return server


def main(*, smoke_report=None):
    if smoke_report:
        from pathlib import Path
        smoke_target = Path(smoke_report).resolve()
        smoke_target.parent.mkdir(parents=True, exist_ok=True)
        smoke_target.write_text('{"ok": false, "stage": "starting"}', encoding="utf-8")
    log_path = configure_runtime_file_logging(_app_module.APP_DATA_DIR)
    logging.getLogger(__name__).info(
        "Desktop runtime log initialized",
        extra={"audioassist_diagnostic": True},
    )
    api = API()
    # Resolve UI path for both development and PyInstaller frozen builds.
    # In frozen mode, bundled resources land in sys._MEIPASS (onefile) or
    # next to sys.executable (onedir).
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    ui_path = os.path.join(base, "ui", "index.html")

    window_title = "AudioAssist" if getattr(sys, "frozen", False) else "AudioAssist · Dev"
    window = webview.create_window(
        window_title,
        url=f"file://{ui_path}",
        js_api=api,
        width=1280,
        height=800,
        min_size=(900, 600),
        text_select=True,
    )
    _app_module._window = window
    local_api = _start_desktop_local_api(api)
    cleanup_lock = threading.Lock()
    cleaned_up = False

    def cleanup(*_args):
        nonlocal cleaned_up
        with cleanup_lock:
            if cleaned_up:
                return
            cleaned_up = True
        if local_api is not None:
            local_api.stop()
        api.shutdown()

    window.events.closed += cleanup
    if smoke_report:
        def check_rendered_window():
            import json
            from pathlib import Path
            import time
            from src.runtime_identity import get_runtime_identity
            report = {"identity": get_runtime_identity(), "ok": False}
            try:
                # Exercise the packaged frontend's real Settings handler. Read
                # only the runtime identity label, never config/key input values.
                window.evaluate_js("document.getElementById('btn-summary-config').click(); true")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    label = window.evaluate_js("document.getElementById('cfg-runtime-identity').textContent") or ""
                    if report["identity"]["version"] in label:
                        break
                    time.sleep(0.1)
                report["rendered_identity"] = label
                report["identity_visible"] = window.evaluate_js(
                    "document.getElementById('cfg-runtime-identity').getBoundingClientRect().width > 0"
                )
                report["ok"] = bool(report["identity_visible"] and
                    report["identity"]["version"] in label and
                    str(report["identity"]["app_path"] or report["identity"]["source_root"]) in label)
            except Exception as error:
                report["error"] = type(error).__name__ + ": " + str(error)
            finally:
                try:
                    Path(smoke_report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    window.destroy()
        window.events.loaded += check_rendered_window
    try:
        webview.start(debug="--debug" in sys.argv)
    finally:
        cleanup()


if __name__ == "__main__":
    _maybe_reexec_with_pythonw()
    # Required for PyInstaller + multiprocessing on macOS (spawn mode).
    # Without this, spawned subprocesses re-run main() and open extra windows.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
