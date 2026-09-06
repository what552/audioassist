"""Tests for bounded desktop runtime logging."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from src import runtime_logging


def _remove_audioassist_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, runtime_logging._HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()


def test_configure_runtime_file_logging_is_idempotent(tmp_path):
    _remove_audioassist_handlers()
    try:
        first = runtime_logging.configure_runtime_file_logging(str(tmp_path))
        second = runtime_logging.configure_runtime_file_logging(str(tmp_path))

        assert first == second
        handlers = [
            handler for handler in logging.getLogger().handlers
            if getattr(handler, runtime_logging._HANDLER_MARKER, False)
        ]
        assert len(handlers) == 1
    finally:
        _remove_audioassist_handlers()


def test_runtime_file_logging_writes_and_bounds_log_location(tmp_path):
    _remove_audioassist_handlers()
    root = logging.getLogger()
    old_level = root.level
    try:
        root.setLevel(logging.INFO)
        log_path = runtime_logging.configure_runtime_file_logging(str(tmp_path))
        logging.getLogger("audioassist.test").info("diagnostic marker")
        for handler in root.handlers:
            if getattr(handler, runtime_logging._HANDLER_MARKER, False):
                handler.flush()

        assert log_path == os.path.join(
            str(tmp_path), "logs", runtime_logging.LOG_FILENAME
        )
        assert "diagnostic marker" in Path(log_path).read_text(encoding="utf-8")
    finally:
        root.setLevel(old_level)
        _remove_audioassist_handlers()
