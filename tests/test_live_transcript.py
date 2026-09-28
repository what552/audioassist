"""Live recording draft persistence and independent-reader tests."""
from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest

from src.live_transcript import FILENAME, LiveTranscriptWriter, read_page
from src.service import AudioAssistService, LiveTranscriptError, SessionNotFoundError


def test_live_draft_is_immediately_readable_and_paged(tmp_path):
    session = tmp_path / "meetings" / "meeting-1"
    writer = LiveTranscriptWriter(str(session))
    writer.append({"text": "大家好", "start": 0.5, "end": 1.5})
    writer.append({"text": "Let's begin", "start": 2.0, "end": 3.5})

    service = AudioAssistService(str(tmp_path))
    first = service.get_live_transcript("meeting-1", limit=1)
    assert first == {
        "session_id": "meeting-1", "provisional": True,
        "segments": [{"seq": 1, "start": 0.5, "end": 1.5, "text": "大家好"}],
        "next_cursor": 1, "has_more": True,
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


def test_second_writer_cannot_take_over_a_recording(tmp_path):
    LiveTranscriptWriter(str(tmp_path))
    with pytest.raises(FileExistsError):
        LiveTranscriptWriter(str(tmp_path))


def test_reader_ignores_only_incomplete_final_line(tmp_path):
    writer = LiveTranscriptWriter(str(tmp_path))
    writer.append({"text": "complete", "start": 0, "end": 1})
    with (tmp_path / FILENAME).open("ab") as file:
        file.write(b'{"seq":2,"text":"truncated"')
    assert [item["text"] for item in read_page(str(tmp_path))["segments"]] == ["complete"]


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


def test_concurrent_reader_never_sees_partial_json(tmp_path):
    writer = LiveTranscriptWriter(str(tmp_path))
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
    assert not errors
    assert len(read_page(str(tmp_path), limit=500)["segments"]) == 200


def test_same_cursor_does_not_repeat_utterances(tmp_path):
    writer = LiveTranscriptWriter(str(tmp_path))
    writer.append("hello")
    first = read_page(str(tmp_path))
    assert first["segments"][0]["start"] is None
    assert read_page(str(tmp_path), after_seq=first["next_cursor"])["segments"] == []


def test_hour_long_draft_can_be_consumed_without_gaps(tmp_path):
    writer = LiveTranscriptWriter(str(tmp_path))
    for index in range(720):
        writer.append({"text": f"utterance {index}", "start": index * 5, "end": index * 5 + 2})
    cursor = 0
    seen = []
    while True:
        page = read_page(str(tmp_path), after_seq=cursor, limit=80)
        seen.extend(item["seq"] for item in page["segments"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert seen == list(range(1, 721))


def test_separate_cli_process_reads_new_utterances_by_cursor(tmp_path):
    writer = LiveTranscriptWriter(str(tmp_path / "meetings" / "live-1"))
    writer.append({"text": "first", "start": 1, "end": 2})

    def read_cli(after_seq):
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "--json", "--output-dir", str(tmp_path),
             "recording", "transcript", "live-1", "--after-seq", str(after_seq)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)["data"]

    first = read_cli(0)
    writer.append({"text": "second", "start": 3, "end": 4})
    second = read_cli(first["next_cursor"])
    assert [item["text"] for item in first["segments"]] == ["first"]
    assert [item["text"] for item in second["segments"]] == ["second"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True])
def test_writer_rejects_invalid_timestamps(tmp_path, invalid):
    with pytest.raises(ValueError):
        LiveTranscriptWriter(str(tmp_path)).append({
            "text": "hello", "start": invalid, "end": 2
        })
