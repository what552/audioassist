"""Runtime logging for the desktop application.

The packaged macOS application has no console, so stderr-only logging disappears
when it is launched from Finder.  Keep a small rotating log in application data
for release-only failures such as audio-device and permission initialization.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


LOG_DIRNAME = "logs"
LOG_FILENAME = "audioassist.log"
_HANDLER_MARKER = "_audioassist_runtime_file_handler"


def configure_runtime_file_logging(
    app_data_dir: str,
    *,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 2,
) -> str:
    """Attach one bounded UTF-8 file handler to the root logger.

    The function is idempotent so tests, embedded launchers, or a future startup
    retry cannot accidentally duplicate every log line.
    """
    log_dir = os.path.join(app_data_dir, LOG_DIRNAME)
    log_path = os.path.join(log_dir, LOG_FILENAME)
    root = logging.getLogger()

    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return os.path.abspath(handler.baseFilename)

    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
    return log_path
