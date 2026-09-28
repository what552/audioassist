"""Live recording draft persistence and independent-reader tests."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time

import pytest

from src.live_transcript import FILENAME, LiveTranscriptWriter, read_page
from src.service import AudioAssistService, LiveTranscriptError, SessionNotFoundError


@pytest.fixture
def make_writer():
    writers = []

    def create(session_dir):
        writer = LiveTranscriptWriter(str(session_dir))
        writers.append(writer)
        return writer

    yield create
    for writer in writers:
        writer.close()


def _wait_persisted(writer, seq):
    deadline = time.monotonic() + 2
    while writer.snapshot()["persisted_seq"] < seq and time.monotonic() < deadline:
        time.sleep(0.005)
    assert writer.snapshot()["persisted_seq"] >= seq


def test_live_draft_is_immediately_readable_and_paged(tmp_path, make_writer):
    session = tmp_path / "meetings" / "meeting-1"
    writer = make_writer(session)
    writer.append({"text": "大家好", "start": 0.5, "end": 1.5})
    writer.append({"text": "Let's begin", "start": 2.0, "end": 3.5})
    _wait_persisted(writer, 2)

    service = AudioAssistService(str(tmp_path))
    first = service.get_live_transcript("meeting-1", limit=1)
    assert first == {
        "session_id": "meeting-1", "provisional": True,
        "segments": [{"seq": 1, "start": 0.5, "end": 1.5, "text": "大家好"}],
        "next_cursor": 1, "has_more": True,
        "draft_status": "incomplete", "draft_complete": False,
        "draft_warning": "Draft is not confirmed complete; only persisted utterances are shown",
    }
    second = service.get_live_transcript("meeting-1", after_seq=first["next_cursor"])
    assert [segment["text"] for segment in second["segments"]] == ["Let's begin"]
    assert second["next_cursor"] == 2
    assert second["has_more"] is False
    assert (session / FILENAME).stat().st_mode & 0o777 == 0o600


def test_empty_draft_before_first_utterance_and_missing_session(tmp_path):
    session = tmp_path / "meetings" / "meeting-1"
    session.mkdir(parents=True)
    service = AudioAssistService(str(tmp_path))
    assert service.get_live_transcript("meeting-1")["segments"] == []
    with pytest.raises(SessionNotFoundError):
        service.get_live_transcript("missing")


def test_second_writer_cannot_take_over_a_recording(tmp_path, make_writer):
    make_writer(tmp_path)
    with pytest.raises(FileExistsError):
        LiveTranscriptWriter(str(tmp_path))


def test_reader_ignores_only_incomplete_final_line(tmp_path):
    with (tmp_path / FILENAME).open("ab") as file:
        file.write(b'{"seq":1,"start":0,"end":1,"text":"complete"}\n')
        file.write(b'{"seq":2,"text":"truncated"')
    page = read_page(str(tmp_path))
    assert [item["text"] for item in page["segments"]] == ["complete"]
    assert page["draft_status"] == "incomplete"


def test_reader_rejects_corrupt_complete_line(tmp_path):
    (tmp_path / FILENAME).write_text("bad json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt"):
        read_page(str(tmp_path))


@pytest.mark.parametrize("after_seq,limit", [(-1, 1), (0, 0), (0, 501), (True, 1)])
def test_service_rejects_invalid_pagination(tmp_path, after_seq, limit):
    (tmp_path / "meetings" / "meeting-1").mkdir(parents=True)
    with pytest.raises(LiveTranscriptError):
        AudioAssistService(str(tmp_path)).get_live_transcript(
            "meeting-1", after_seq=after_seq, limit=limit
        )


def test_concurrent_reader_never_sees_partial_json(tmp_path, make_writer):
    writer = make_writer(tmp_path)
    errors = []

    def write_many():
        for index in range(200):
            writer.append({"text": f"utterance {index}", "start": index, "end": index + 1})

    thread = threading.Thread(target=write_many)
    thread.start()
    while thread.is_alive():
        try:
            page = read_page(str(tmp_path), limit=500)
            assert [item["seq"] for item in page["segments"]] == list(
                range(1, page["next_cursor"] + 1)
            )
        except Exception as error:
            errors.append(error)
    thread.join()
    _wait_persisted(writer, 200)
    assert not errors
    assert len(read_page(str(tmp_path), limit=500)["segments"]) == 200


def test_same_cursor_does_not_repeat_utterances(tmp_path, make_writer):
    writer = make_writer(tmp_path)
    writer.append("hello")
    _wait_persisted(writer, 1)
    first = read_page(str(tmp_path))
    assert first["segments"][0]["start"] is None
    assert read_page(str(tmp_path), after_seq=first["next_cursor"])["segments"] == []


def test_hour_long_draft_can_be_consumed_without_gaps(tmp_path, make_writer):
    writer = make_writer(tmp_path)
    for index in range(720):
        writer.append({"text": f"utterance {index}", "start": index * 5, "end": index * 5 + 2})
    _wait_persisted(writer, 720)
    cursor = 0
    seen = []
    while True:
        page = read_page(str(tmp_path), after_seq=cursor, limit=80)
        seen.extend(item["seq"] for item in page["segments"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert seen == list(range(1, 721))


def test_separate_cli_process_reads_new_utterances_by_cursor(tmp_path, make_writer):
    writer = make_writer(tmp_path / "meetings" / "live-1")
    writer.append({"text": "first", "start": 1, "end": 2})
    _wait_persisted(writer, 1)

    def read_cli(after_seq):
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "--json", "--output-dir", str(tmp_path),
             "recording", "transcript", "live-1", "--after-seq", str(after_seq)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)["data"]

    first = read_cli(0)
    writer.append({"text": "second", "start": 3, "end": 4})
    _wait_persisted(writer, 2)
    second = read_cli(first["next_cursor"])
    assert [item["text"] for item in first["segments"]] == ["first"]
    assert [item["text"] for item in second["segments"]] == ["second"]


def test_blocked_disk_write_never_blocks_asr_callback_or_stop(tmp_path, make_writer):
    writer = make_writer(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_write = writer._write_line

    def slow_write(entry):
        if entry.get("seq") == 1:
            entered.set()
            assert release.wait(timeout=2)
        original_write(entry)

    writer._write_line = slow_write
    started = time.monotonic()
    writer.append({"text": "ongoing", "start": 1, "end": 2})
    assert time.monotonic() - started < 0.1
    assert entered.wait(timeout=1)
    started = time.monotonic()
    assert writer.close(timeout=0.02) is False
    assert time.monotonic() - started < 0.2
    assert read_page(str(tmp_path))["draft_complete"] is False
    release.set()
    assert writer.close()
    assert read_page(str(tmp_path))["draft_status"] == "closed"


def test_failed_second_write_cannot_appear_complete(tmp_path, make_writer):
    writer = make_writer(tmp_path)
    original_write = writer._write_line

    def fail_second(entry):
        if entry.get("seq") == 2:
            raise OSError("disk full")
        original_write(entry)

    writer._write_line = fail_second
    writer.append({"text": "saved", "start": 1, "end": 2})
    _wait_persisted(writer, 1)
    writer.append({"text": "lost", "start": 3, "end": 4})
    deadline = time.monotonic() + 2
    while writer.snapshot()["state"] != "failed" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert writer.close() is False
    page = read_page(str(tmp_path))
    assert [item["text"] for item in page["segments"]] == ["saved"]
    assert page["draft_status"] == "incomplete"
    assert page["draft_complete"] is False


def test_full_queue_marks_journal_failed_without_blocking(tmp_path, make_writer):
    writer = make_writer(tmp_path)
    writer._queue = queue.Queue(maxsize=1)
    entered = threading.Event()
    release = threading.Event()
    original_write = writer._write_line

    def slow_write(entry):
        if entry.get("seq") == 1:
            entered.set()
            assert release.wait(timeout=2)
        original_write(entry)

    writer._write_line = slow_write
    writer.append("one")
    assert entered.wait(timeout=1)
    writer.append("two")
    with pytest.raises(OSError, match="queue is full"):
        writer.append("three")
    release.set()
    assert writer.close() is False
    page = read_page(str(tmp_path))
    assert [item["text"] for item in page["segments"]] == ["one", "two"]
    assert page["draft_status"] == "failed"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True])
def test_writer_rejects_invalid_timestamps(tmp_path, invalid, make_writer):
    with pytest.raises(ValueError):
        make_writer(tmp_path).append({
            "text": "hello", "start": invalid, "end": 2
        })


def test_writer_thread_start_failure_does_not_interrupt_cleanup(tmp_path, monkeypatch):
    writer = LiveTranscriptWriter(str(tmp_path))
    def fail_start():
        raise OSError("thread unavailable")

    monkeypatch.setattr(writer, "_start_thread", fail_start)
    assert writer.close() is False
    writer.abort()
    assert writer.snapshot()["state"] == "failed"


def test_unexpected_writer_exception_keeps_journal_incomplete(tmp_path, make_writer):
    writer = make_writer(tmp_path)

    def fail_write(_entry):
        raise UnicodeError("invalid text encoding")

    writer._write_line = fail_write
    writer.append("unpersisted")
    assert writer.close() is False
    assert writer.snapshot()["state"] == "failed"
    assert read_page(str(tmp_path))["draft_complete"] is False
