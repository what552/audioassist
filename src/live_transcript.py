"""Append-only, per-recording draft transcript shared with local readers.

The ASR worker is the sole writer for a recording. A complete JSON line becomes
visible to another process immediately; a truncated final line after a crash is
ignored. This is a draft, not the diarized/final transcript.
"""
from __future__ import annotations

import json
import math
import os
import threading
from typing import Any


FILENAME = "realtime_draft.jsonl"
MAX_TEXT_LENGTH = 100_000
MAX_LINE_BYTES = MAX_TEXT_LENGTH * 6 + 256


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
        self._failed = False
        os.makedirs(session_dir, exist_ok=True)
        # A recording has a unique session id and exactly one journal owner.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

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
            if self._failed:
                raise OSError("Realtime transcript journal is unavailable")
            entry = {"seq": self._next_seq, "start": start, "end": end, "text": text}
            raw = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
                try:
                    written = os.write(fd, raw)
                    if written != len(raw):
                        raise OSError("Short realtime transcript journal write")
                finally:
                    os.close(fd)
            except OSError:
                self._failed = True
                raise
            self._next_seq += 1
            return entry


def read_page(session_dir: str, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read only complete, validated entries after a stable sequence cursor."""
    if after_seq < 0 or not 1 <= limit <= 500:
        raise ValueError("after_seq must be nonnegative and limit must be 1..500")
    path = os.path.join(session_dir, FILENAME)
    entries: list[dict[str, Any]] = []
    cursor = after_seq
    has_more = False
    if not os.path.isfile(path):
        return {"segments": entries, "next_cursor": cursor, "has_more": False}

    previous_seq = 0
    with open(path, "rb") as file:
        while raw := file.readline(MAX_LINE_BYTES + 1):
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError("Corrupt realtime transcript journal")
            if not raw.endswith(b"\n"):
                break  # A process may have exited during its final append.
            try:
                entry = json.loads(raw)
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
                break
            entries.append({"seq": seq, "start": start, "end": end, "text": text})
            cursor = seq
    return {"segments": entries, "next_cursor": cursor, "has_more": has_more}
