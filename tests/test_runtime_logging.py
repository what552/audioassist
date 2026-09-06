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
        test_logger = logging.getLogger("audioassist.test")
        test_logger.info("ordinary transcript-like text must stay out")
        try:
            raise RuntimeError("/private/audio/secret.wav 私密转写原文")
        except RuntimeError:
            test_logger.exception("ordinary exception with sensitive details")
        test_logger.info(
            "diagnostic marker",
            extra={runtime_logging.DIAGNOSTIC_RECORD_ATTRIBUTE: True},
        )
        for handler in root.handlers:
            if getattr(handler, runtime_logging._HANDLER_MARKER, False):
                handler.flush()

        assert log_path == os.path.join(
            str(tmp_path), "logs", runtime_logging.LOG_FILENAME
        )
        content = Path(log_path).read_text(encoding="utf-8")
        assert "diagnostic marker" in content
        assert "ordinary transcript-like text must stay out" not in content
        assert "secret.wav" not in content
        assert "私密转写原文" not in content
    finally:
        root.setLevel(old_level)
        _remove_audioassist_handlers()


def test_runtime_file_logging_rotates_at_configured_bound(tmp_path):
    _remove_audioassist_handlers()
    root = logging.getLogger()
    old_level = root.level
    try:
        root.setLevel(logging.INFO)
        log_path = runtime_logging.configure_runtime_file_logging(
            str(tmp_path), max_bytes=120, backup_count=2
        )
        diagnostic = logging.getLogger("audioassist.rotate")
        for index in range(8):
            diagnostic.info(
                "marker-%s-%s", index, "x" * 30,
                extra={runtime_logging.DIAGNOSTIC_RECORD_ATTRIBUTE: True},
            )

        assert Path(log_path).exists()
        assert Path(log_path + ".1").exists()
        assert not Path(log_path + ".3").exists()
    finally:
        root.setLevel(old_level)
        _remove_audioassist_handlers()


def test_two_handler_instances_share_rotation_without_stale_file_descriptors(tmp_path):
    log_path = str(tmp_path / "logs" / runtime_logging.LOG_FILENAME)
    Path(log_path).parent.mkdir(parents=True)
    handlers = [
        runtime_logging._CrossProcessRotatingFileHandler(
            log_path, max_bytes=90, backup_count=2
        )
        for _ in range(2)
    ]
    formatter = logging.Formatter("%(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)

    try:
        for index in range(10):
            record = logging.LogRecord(
                "audioassist.multi", logging.INFO, __file__, 1,
                f"process-marker-{index}-" + ("y" * 20), (), None,
            )
            handlers[index % 2].emit(record)

        combined = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(Path(log_path).parent.glob("audioassist.log*"))
            if path.name != "audioassist.log.lock"
        )
        assert "process-marker-9" in combined
        assert len(list(Path(log_path).parent.glob("audioassist.log.[0-9]*"))) <= 2
    finally:
        for handler in handlers:
            handler.close()
