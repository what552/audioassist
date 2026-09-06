"""Runtime logging for the desktop application.

The packaged macOS application has no console, so stderr-only logging disappears
when it is launched from Finder.  Keep a small rotating log in application data
for release-only failures such as audio-device and permission initialization.
"""
from __future__ import annotations

import logging
import os


LOG_DIRNAME = "logs"
LOG_FILENAME = "audioassist.log"
_HANDLER_MARKER = "_audioassist_runtime_file_handler"
DIAGNOSTIC_RECORD_ATTRIBUTE = "audioassist_diagnostic"


class _DiagnosticOnlyFilter(logging.Filter):
    """Keep general application logs out of the packaged diagnostic file."""

    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE, False))


class _CrossProcessRotatingFileHandler(logging.Handler):
    """A small cross-process-safe handler for infrequent diagnostic markers.

    ``RotatingFileHandler`` keeps an open descriptor. If another AudioAssist
    process rotates that path, the first process continues writing into the
    renamed file. Diagnostic markers are deliberately low volume, so opening
    the file for each record is a worthwhile tradeoff for predictable rotation
    when two GUI instances overlap.
    """

    def __init__(self, filename: str, *, max_bytes: int, backup_count: int):
        super().__init__()
        self.baseFilename = os.path.abspath(filename)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock_path = self.baseFilename + ".lock"
        self._lock_file = open(self._lock_path, "a+b")
        if os.name == "nt" and os.path.getsize(self._lock_path) == 0:
            self._lock_file.write(b"\0")
            self._lock_file.flush()

    def _acquire_process_lock(self) -> None:
        self._lock_file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)

    def _release_process_lock(self) -> None:
        self._lock_file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        current_size = (
            os.path.getsize(self.baseFilename)
            if os.path.exists(self.baseFilename)
            else 0
        )
        if not self.max_bytes or current_size + incoming_bytes <= self.max_bytes:
            return
        if self.backup_count <= 0:
            with open(self.baseFilename, "wb"):
                pass
            return
        for index in range(self.backup_count - 1, 0, -1):
            source = f"{self.baseFilename}.{index}"
            destination = f"{self.baseFilename}.{index + 1}"
            if os.path.exists(source):
                os.replace(source, destination)
        if os.path.exists(self.baseFilename):
            os.replace(self.baseFilename, f"{self.baseFilename}.1")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = (self.format(record) + "\n").encode("utf-8")
            self._acquire_process_lock()
            try:
                self._rotate_if_needed(len(payload))
                with open(self.baseFilename, "ab") as output:
                    output.write(payload)
                    output.flush()
            finally:
                self._release_process_lock()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        lock_file = getattr(self, "_lock_file", None)
        self._lock_file = None
        if lock_file is not None:
            lock_file.close()
        super().close()


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
    handler = _CrossProcessRotatingFileHandler(
        log_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    handler.addFilter(_DiagnosticOnlyFilter())
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
    return log_path
