"""
Transcription checkpoint — persist and resume long transcription jobs.

Data file: {session_dir}/transcription_task.json
Format:
    {
        "job_id": "...", "source_audio": "...", "engine": "qwen", "model_id": "...",
        "status": "running",
        "chunks": [
            {"index": 0, "offset": 0.0, "status": "done", "text": "...", "words": [...], "language": ""},
            {"index": 1, "offset": 300.0, "status": "pending"}
        ],
        "completed_chunks": 1, "total_chunks": 5, "updated_at": "..."
    }
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

FILENAME = "transcription_task.json"


def write(session_dir: str, data: dict) -> None:
    """Atomically write checkpoint data to session_dir."""
    os.makedirs(session_dir, exist_ok=True)
    path = os.path.join(session_dir, FILENAME)
    data = dict(data)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read(session_dir: str) -> Optional[dict]:
    """Read checkpoint from session_dir. Returns None if not found or invalid."""
    path = os.path.join(session_dir, FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def delete(session_dir: str) -> None:
    """Delete checkpoint file if it exists."""
    path = os.path.join(session_dir, FILENAME)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def find_interrupted(output_dir: str) -> list[dict]:
    """
    Scan meetings/*/ for checkpoints with status 'interrupted' or 'running'.
    Returns a list of checkpoint dicts.
    """
    results: list[dict] = []
    meetings_dir = os.path.join(output_dir, "meetings")
    if not os.path.isdir(meetings_dir):
        return results
    for job_id in os.listdir(meetings_dir):
        session_dir = os.path.join(meetings_dir, job_id)
        if not os.path.isdir(session_dir):
            continue
        ckpt = read(session_dir)
        if ckpt and ckpt.get("status") in ("interrupted", "running"):
            results.append(ckpt)
    return results
