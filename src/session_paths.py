"""
Shared helpers for resolving transcript/summary storage paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SessionPaths:
    session_dir: str
    transcript_new: str
    transcript_legacy: str
    summary_new: str
    summary_legacy: str


def get_session_paths(output_dir: str, job_id: str) -> SessionPaths:
    session_dir = os.path.join(output_dir, "meetings", job_id)
    return SessionPaths(
        session_dir=session_dir,
        transcript_new=os.path.join(session_dir, "transcript.json"),
        transcript_legacy=os.path.join(output_dir, f"{job_id}.json"),
        summary_new=os.path.join(session_dir, "summary.json"),
        summary_legacy=os.path.join(output_dir, f"{job_id}_summary.json"),
    )


def resolve_transcript_path(output_dir: str, job_id: str) -> Optional[str]:
    """Return transcript path preferring the canonical session layout."""
    paths = get_session_paths(output_dir, job_id)
    if os.path.exists(paths.transcript_new):
        return paths.transcript_new
    if os.path.exists(paths.transcript_legacy):
        return paths.transcript_legacy
    return None


def resolve_summary_path(output_dir: str, job_id: str) -> str:
    """
    Return summary path preferring the canonical session layout for both reads and writes.

    If the session directory exists, always use the new path to prevent agent/UI divergence.
    Otherwise fall back to the legacy flat layout.
    """
    paths = get_session_paths(output_dir, job_id)
    if os.path.isdir(paths.session_dir) or os.path.exists(paths.summary_new):
        return paths.summary_new
    return paths.summary_legacy
