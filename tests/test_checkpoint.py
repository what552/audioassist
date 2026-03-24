"""Tests for src/checkpoint.py"""
import json
import os
import pytest
from src.checkpoint import write, read, delete, find_interrupted, FILENAME


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "meetings" / "test-job-123"
    d.mkdir(parents=True)
    return str(d)


def test_write_creates_file(session_dir):
    write(session_dir, {"job_id": "abc", "status": "running"})
    assert os.path.exists(os.path.join(session_dir, FILENAME))


def test_read_returns_none_if_missing(tmp_path):
    result = read(str(tmp_path / "nonexistent"))
    assert result is None


def test_write_then_read_roundtrip(session_dir):
    data = {"job_id": "abc", "status": "running", "total_chunks": 3}
    write(session_dir, data)
    result = read(session_dir)
    assert result is not None
    assert result["job_id"] == "abc"
    assert result["status"] == "running"
    assert result["total_chunks"] == 3
    assert "updated_at" in result


def test_atomic_write_no_tmp_left(session_dir):
    write(session_dir, {"job_id": "x"})
    tmp = os.path.join(session_dir, FILENAME + ".tmp")
    assert not os.path.exists(tmp)


def test_delete_removes_file(session_dir):
    write(session_dir, {"job_id": "x"})
    assert os.path.exists(os.path.join(session_dir, FILENAME))
    delete(session_dir)
    assert not os.path.exists(os.path.join(session_dir, FILENAME))


def test_delete_no_error_if_missing(tmp_path):
    # Should not raise
    delete(str(tmp_path / "no-such-dir"))


def test_find_interrupted_scans_meetings_dir(tmp_path):
    meetings = tmp_path / "meetings"
    meetings.mkdir()

    # Create an interrupted session
    s1 = meetings / "job-1"
    s1.mkdir()
    write(str(s1), {"job_id": "job-1", "status": "interrupted"})

    # Create a running session
    s2 = meetings / "job-2"
    s2.mkdir()
    write(str(s2), {"job_id": "job-2", "status": "running"})

    # Create a done session (should be excluded)
    s3 = meetings / "job-3"
    s3.mkdir()
    write(str(s3), {"job_id": "job-3", "status": "done"})

    result = find_interrupted(str(tmp_path))
    job_ids = {r["job_id"] for r in result}
    assert "job-1" in job_ids
    assert "job-2" in job_ids
    assert "job-3" not in job_ids


def test_find_interrupted_empty_when_no_meetings_dir(tmp_path):
    result = find_interrupted(str(tmp_path))
    assert result == []


def test_write_sets_updated_at(session_dir):
    write(session_dir, {"job_id": "x", "status": "running"})
    result = read(session_dir)
    assert result["updated_at"] is not None
    # Should look like ISO datetime
    assert "T" in result["updated_at"]


def test_write_does_not_mutate_input_dict(session_dir):
    data = {"job_id": "x", "status": "running"}
    write(session_dir, data)
    # updated_at should not be added to original dict
    assert "updated_at" not in data
