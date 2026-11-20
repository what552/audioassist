"""Append-only, per-recording draft transcript shared with local readers.

The ASR worker only enqueues utterances; a dedicated worker writes the journal.
Complete JSON lines are visible across processes, while a truncated crash tail is
ignored. This draft is not the diarized/final transcript.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import tempfile
import threading
from typing import Any


FILENAME = "realtime_draft.jsonl"
CLOUD_STATUS_FILENAME = "realtime_asr.json"
MAX_TEXT_LENGTH = 100_000
MAX_LINE_BYTES = MAX_TEXT_LENGTH * 6 + 256
logger = logging.getLogger(__name__)


def write_cloud_coverage(session_dir: str, coverage: dict) -> None:
    path = os.path.join(session_dir, CLOUD_STATUS_FILENAME)
    fd, temporary = tempfile.mkstemp(prefix=".realtime-asr-", dir=session_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(coverage, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_cloud_coverage(session_dir: str, *, cloud_journal: bool = False) -> dict | None:
    unknown = {"provider": "token-plan", "closed": False, "error": "coverage_unconfirmed",
               "processed_through": None, "missing_count": 0, "missing_ranges": [],
               "missing_ranges_truncated": False}
    try:
        with open(os.path.join(session_dir, CLOUD_STATUS_FILENAME), "rb") as file:
            coverage = json.loads(file.read(MAX_LINE_BYTES))
    except FileNotFoundError:
        return unknown if cloud_journal else None
    except (OSError, ValueError):
        return unknown
    if (not isinstance(coverage, dict) or coverage.get("provider") != "token-plan"
            or type(coverage.get("closed")) is not bool or type(coverage.get("missing_count")) is not int
            or coverage["missing_count"] < 0):
        return unknown
    return coverage


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
    def __init__(self, session_dir: str, *, cloud: bool = False) -> None:
        self.path = os.path.join(session_dir, FILENAME)
        self._lock = threading.Lock()
        self._next_seq = 1
        self._persisted_seq = 0
        self._closed = False
        self._cloud = cloud
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
            try:
                self._start_thread()
            except OSError:
                return False
            thread = self._thread
        assert thread is not None
        thread.join(timeout=timeout)
        with self._lock:
            return not thread.is_alive() and not self._failed

    def abort(self, timeout: float = 1.0) -> None:
        """End a recording that never started cleanly without a success marker."""
        self.mark_failed()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def mark_failed(self) -> None:
        """Preserve queued lines but never claim a partially lost draft is complete."""
        with self._lock:
            self._failed = True
            self._closed = True
            try:
                self._start_thread()
            except OSError:
                return

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
            except Exception:
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
            marker = {
                "type": "failed" if failed else "closed", "last_seq": persisted_seq
            }
            if self._cloud:
                marker["asr_provider"] = "token-plan"
            self._write_line(marker)
        except Exception:
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
        page = {
            "segments": entries, "next_cursor": cursor, "has_more": False,
            "draft_status": "missing", "draft_complete": False,
        }
        return annotate_cloud_coverage(page, read_cloud_coverage(session_dir))

    previous_seq = 0
    marker_seen = False
    cloud_journal = False
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
                    cloud_journal = entry.get("asr_provider") == "token-plan"
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
    page = {
        "segments": entries, "next_cursor": cursor, "has_more": has_more,
        "draft_status": draft_status, "draft_complete": draft_status == "closed",
    }
    return annotate_cloud_coverage(page, read_cloud_coverage(session_dir, cloud_journal=cloud_journal))


def merge_live_writer_state(page: dict[str, Any], state: Any) -> dict[str, Any]:
    """Annotate a durable page with current writer health for the same session."""
    if not isinstance(state, dict) or state.get("session_id") != page.get("session_id"):
        return page
    result = dict(page)
    writer = state.get("live_transcript")
    if isinstance(writer, dict):
        result["live_writer"] = writer
    if isinstance(state.get("live_asr"), dict):
        result = annotate_cloud_coverage(result, state["live_asr"])
    if state.get("live_transcript_warning") or (
        isinstance(writer, dict) and writer.get("state") == "failed"
    ):
        result["draft_status"] = "failed"
        result["draft_complete"] = False
        result["draft_warning"] = (
            "Live transcript saving failed; utterances after the last persisted sequence may be missing"
        )
    elif isinstance(writer, dict) and not result.get("live_asr", {}).get("missing_count"):
        accepted = writer.get("accepted_seq")
        persisted = writer.get("persisted_seq")
        if type(accepted) is int and type(persisted) is int and accepted > persisted:
            result["draft_warning"] = (
                f"{accepted - persisted} recognized utterance(s) are not yet persisted; retry shortly"
            )
    return result


def annotate_cloud_coverage(page: dict, coverage: Any) -> dict:
    """Keep journal health distinct from coverage of Token Plan recognition."""
    if not isinstance(coverage, dict) or coverage.get("provider") != "token-plan":
        return page
    result = {**page, "live_asr": coverage}
    if coverage.get("missing_count") or not coverage.get("closed") or coverage.get("error"):
        result["draft_complete"] = False
    if coverage.get("missing_count") and page.get("draft_status") != "failed":
        result["draft_warning"] = "Cloud recognition missed audio ranges; persisted text is still readable"
    elif not result["draft_complete"] and page.get("draft_status") == "closed":
        result["draft_warning"] = "Cloud recognition coverage is unconfirmed; persisted text is still readable"
    return result
