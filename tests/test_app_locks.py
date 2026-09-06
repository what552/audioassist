"""Concurrency and lifecycle tests for per-session transcript locks."""
from __future__ import annotations

import gc
import threading

import pytest
from unittest.mock import patch

import app as app_module
from app import API


@pytest.fixture(autouse=True)
def clean_lock_registry():
    with app_module._transcript_locks_mutex:
        app_module._transcript_locks.clear()
    yield
    with app_module._transcript_locks_mutex:
        app_module._transcript_locks.clear()


def test_same_live_job_lock_is_reused() -> None:
    first = app_module._get_transcript_lock("job-one")
    second = app_module._get_transcript_lock("job-one")

    assert second is first
    assert app_module._transcript_locks["job-one"] is first


def test_finished_job_locks_are_evicted() -> None:
    for index in range(1_000):
        lock = app_module._get_transcript_lock(f"job-{index}")
    del lock
    gc.collect()

    assert len(app_module._transcript_locks) == 0


def test_waiter_keeps_shared_lock_alive_until_contention_finishes() -> None:
    owner_lock = app_module._get_transcript_lock("contended-job")
    owner_lock.acquire()
    waiter_has_reference = threading.Event()
    waiter_entered = threading.Event()

    def wait_for_lock() -> None:
        waiter_lock = app_module._get_transcript_lock("contended-job")
        assert waiter_lock is owner_lock
        waiter_has_reference.set()
        with waiter_lock:
            waiter_entered.set()

    waiter = threading.Thread(target=wait_for_lock)
    waiter.start()
    assert waiter_has_reference.wait(1)
    assert not waiter_entered.wait(0.05)
    assert app_module._transcript_locks["contended-job"] is owner_lock

    owner_lock.release()
    assert waiter_entered.wait(1)
    waiter.join(timeout=1)
    assert not waiter.is_alive()

    del owner_lock
    gc.collect()
    assert "contended-job" not in app_module._transcript_locks


def test_delete_waits_for_active_session_mutation(tmp_path) -> None:
    job_id = "delete-contended-job"
    session_dir = tmp_path / "meetings" / job_id
    session_dir.mkdir(parents=True)
    transcript = session_dir / "transcript.json"
    transcript.write_text("{}", encoding="utf-8")
    owner_lock = app_module._get_transcript_lock(job_id)
    owner_lock.acquire()
    delete_finished = threading.Event()
    result = []

    def delete_session() -> None:
        result.append(API().delete_session(job_id))
        delete_finished.set()

    with patch.object(app_module, "OUTPUT_DIR", str(tmp_path)):
        worker = threading.Thread(target=delete_session)
        worker.start()
        assert not delete_finished.wait(0.05)
        assert transcript.exists()
        owner_lock.release()
        assert delete_finished.wait(1)
        worker.join(timeout=1)

    assert result == [True]
    assert not session_dir.exists()
