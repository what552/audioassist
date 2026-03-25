"""
Agent session store — read/write conversation history for MeetingAgent.

Data file: {output_dir}/{job_id}_agent_chat.json
Format:
    {
        "job_id": "...",
        "updated_at": "2026-03-22T...",
        "turns": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "...", "tool_events": [...]}
        ]
    }

Stores at most MAX_TURNS pairs (user + assistant).
build_context_messages() returns only the last CONTEXT_TURNS pairs for the LLM.
"""
from __future__ import annotations

import json
import os
import time

MAX_TURNS = 20    # pairs to persist
CONTEXT_TURNS = 8  # pairs to send to LLM


def load_session(output_dir: str, job_id: str) -> dict:
    """Load session from disk. Returns empty session if not found or corrupted."""
    path = _session_path(output_dir, job_id)
    if not os.path.exists(path):
        return {"job_id": job_id, "turns": []}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"job_id": job_id, "turns": []}


def save_turn(
    output_dir: str,
    job_id: str,
    user_content: str,
    assistant_content: str,
    tool_events: list[dict] | None = None,
) -> None:
    """Append a user+assistant turn and persist, trimming to MAX_TURNS pairs."""
    path = _session_path(output_dir, job_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    session = load_session(output_dir, job_id)
    turns = session.get("turns", [])

    turns.append({"role": "user", "content": user_content})
    entry: dict = {"role": "assistant", "content": assistant_content}
    if tool_events:
        entry["tool_events"] = tool_events
    turns.append(entry)

    # Keep at most MAX_TURNS * 2 messages (each pair = 2 messages)
    turns = turns[-(MAX_TURNS * 2):]

    data = {
        "job_id": job_id,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "turns": turns,
    }
    path = _session_path(output_dir, job_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def clear_session(output_dir: str, job_id: str) -> bool:
    """Delete the session file. Returns True if the file was deleted."""
    path = _session_path(output_dir, job_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def build_context_messages(session: dict) -> list[dict]:
    """Return the last CONTEXT_TURNS pairs as simple role/content dicts."""
    turns = session.get("turns", [])
    recent = turns[-(CONTEXT_TURNS * 2):]
    return [{"role": t["role"], "content": t["content"]} for t in recent]


def _session_path(output_dir: str, job_id: str) -> str:
    session_dir = os.path.join(output_dir, "meetings", job_id)
    if os.path.isdir(session_dir):
        return os.path.join(session_dir, "agent_chat.json")
    return os.path.join(output_dir, f"{job_id}_agent_chat.json")
