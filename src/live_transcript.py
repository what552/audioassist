"""Append-only, per-recording draft transcript shared with local readers.

The ASR worker is the sole writer for a recording. A complete JSON line becomes
visible to another process immediately; a truncated final line after a crash is
ignored. This is a draft, not the diarized/final transcript.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import threading
from typing import Any


FILENAME = "realtime_draft.jsonl"
MAX_TEXT_LENGTH = 100_000
MAX_LINE_BYTES = MAX_TEXT_LENGTH * 6 + 256
logger = logging.getLogger(__name__)


def _valid_time(value: Any) -> bool:
    if value is None:
        return True
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class LiveTranscriptWriter:
    def __init__(self, session_dir: str) -> None:
        self.path = os.path.join(session_dir, FILENAME)
        self._lock = threading.Lock()
        self._next_seq = 1
        self._persisted_seq = 0
        self._closed = False
        self._failed = False
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1024)
        os.makedirs(session_dir, exist_ok=True)
        # A recording has a unique session id and exactly one journal owner.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self._thread: threading.Thread | None = None

    def _start_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="realtime-draft-writer", daemon=True
        )
        try:
            self._thread.start()
        except RuntimeError as error:
            self._thread = None
            self._failed = True
            raise OSError("Unable to start realtime transcript writer") from error

    def append(self, segment: dict[str, Any] | str) -> dict[str, Any]:
        """Append one recognized utterance without touching the audio callback."""
        if isinstance(segment, str):
            segment = {"text": segment, "start": None, "end": None}
        text = segment.get("text")
        start = segment.get("start")
        end = segment.get("end")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise ValueError("Invalid realtime transcript text")
        if not _valid_time(start):
            raise ValueError("Invalid realtime transcript start")
        if not _valid_time(end):
            raise ValueError("Invalid realtime transcript end")
        if start is not None and end is not None and end < start:
            raise ValueError("Realtime transcript end precedes start")

        with self._lock:
            if self._failed or self._closed:
                raise OSError("Realtime transcript journal is unavailable")
            entry = {"seq": self._next_seq, "start": start, "end": end, "text": text}
            try:
                self._queue.put_nowait(entry)
            except queue.Full as error:
                self._failed = True
                raise OSError("Realtime transcript writer queue is full") from error
            self._next_seq += 1
            self._start_thread()
            return entry

    def snapshot(self) -> dict[str, Any]:
        """Report in-memory writer health to the visible app's recording status."""
        with self._lock:
            thread = self._thread
            return {
                "state": "failed" if self._failed else "closed" if self._closed and thread is not None and not thread.is_alive() else "active",
                "accepted_seq": self._next_seq - 1,
                "persisted_seq": self._persisted_seq,
            }

    def close(self, timeout: float = 1.0) -> bool:
        """Request a bounded flush after ASR stops; never hang recording shutdown."""
        with self._lock:
            self._closed = True
            self._start_thread()
            thread = self._thread
        assert thread is not None
        thread.join(timeout=timeout)
        with self._lock:
            return not thread.is_alive() and not self._failed

    def abort(self, timeout: float = 1.0) -> None:
        """End a recording that never started cleanly without a success marker."""
        with self._lock:
            self._failed = True
            self._closed = True
            self._start_thread()
            thread = self._thread
        assert thread is not None
        thread.join(timeout=timeout)

    def _write_line(self, entry: dict[str, Any]) -> None:
        raw = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
        try:
            if os.write(fd, raw) != len(raw):
                raise OSError("Short realtime transcript journal write")
        finally:
            os.close(fd)

    def _run(self) -> None:
        while True:
            try:
                entry = self._queue.get(timeout=0.05)
            except queue.Empty:
                with self._lock:
                    if self._closed:
                        break
                continue
            try:
                self._write_line(entry)
                with self._lock:
                    self._persisted_seq = entry["seq"]
            except OSError:
                with self._lock:
                    self._failed = True
                logger.exception("Realtime transcript journal write failed")
                return
            finally:
                self._queue.task_done()

        with self._lock:
            failed = self._failed
            persisted_seq = self._persisted_seq
        try:
            self._write_line({
                "type": "failed" if failed else "closed", "last_seq": persisted_seq
            })
        except OSError:
            with self._lock:
                self._failed = True
            logger.exception("Realtime transcript journal final marker failed")


def read_page(session_dir: str, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read only complete, validated entries after a stable sequence cursor."""
    if after_seq < 0 or not 1 <= limit <= 500:
        raise ValueError("after_seq must be nonnegative and limit must be 1..500")
    path = os.path.join(session_dir, FILENAME)
    entries: list[dict[str, Any]] = []
    cursor = after_seq
    has_more = False
    draft_status = "incomplete"
    if not os.path.isfile(path):
        return {
            "segments": entries, "next_cursor": cursor, "has_more": False,
            "draft_status": "missing", "draft_complete": False,
        }

    previous_seq = 0
    marker_seen = False
    with open(path, "rb") as file:
        while raw := file.readline(MAX_LINE_BYTES + 1):
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError("Corrupt realtime transcript journal")
            if not raw.endswith(b"\n"):
                break  # A process may have exited during its final append.
            try:
                entry = json.loads(raw)
                if not isinstance(entry, dict) or marker_seen:
                    raise ValueError("Invalid realtime draft entry")
                if entry.get("type") in {"closed", "failed"}:
                    if entry.get("last_seq") != previous_seq:
                        raise ValueError("Invalid realtime draft marker")
                    draft_status = entry["type"]
                    marker_seen = True
                    continue
                seq = entry["seq"]
                text = entry["text"]
                start = entry["start"]
                end = entry["end"]
                if (
                    not isinstance(seq, int) or isinstance(seq, bool)
                    or seq != previous_seq + 1
                    or not isinstance(text, str) or not text.strip()
                    or len(text) > MAX_TEXT_LENGTH
                    or not _valid_time(start)
                    or not _valid_time(end)
                    or start is not None and end is not None and end < start
                ):
                    raise ValueError("Invalid realtime draft entry")
            except (UnicodeError, ValueError, TypeError, KeyError) as error:
                raise ValueError("Corrupt realtime transcript journal") from error
            previous_seq = seq
            if seq <= after_seq:
                continue
            if len(entries) == limit:
                has_more = True
                continue
            entries.append({"seq": seq, "start": start, "end": end, "text": text})
            cursor = seq
    return {
        "segments": entries, "next_cursor": cursor, "has_more": has_more,
        "draft_status": draft_status, "draft_complete": draft_status == "closed",
    }
