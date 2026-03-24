"""
Obsidian vault sync for AudioAssist sessions.

Generates one Markdown file per session with YAML frontmatter, a summary
section, and a transcript section.  Files are written atomically.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional


# ── Filename helpers ───────────────────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    """Strip characters that are unsafe in filenames across all major OSes."""
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', '-', name).strip().strip('.')


def obsidian_filename(date_str: str, display_name: str) -> str:
    """Return the canonical Obsidian MD filename, e.g. '2026-03-23 个税副本.md'."""
    safe = _sanitize_name(display_name) or "Untitled"
    return f"{date_str} {safe}.md"


# ── Content builders ───────────────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _speakers_from_segments(segments: list) -> list[str]:
    seen: list[str] = []
    for seg in segments:
        sp = seg.get("speaker", "")
        if sp and sp not in seen:
            seen.append(sp)
    return seen


def _transcript_md_body(segments: list) -> str:
    lines: list[str] = []
    for seg in segments:
        start = seg.get("start", 0)
        m, s = divmod(int(start), 60)
        lines.append(f"**{m:02d}:{s:02d} {seg.get('speaker', '')}**  ")
        lines.append(seg.get("text", "").strip())
        lines.append("")
    return "\n".join(lines)


def build_md(data: dict, summary_text: Optional[str] = None) -> str:
    """
    Build the full Obsidian Markdown content for a session.

    data        — transcript JSON dict (segments, filename, audio, created_at, job_id…)
    summary_text — latest summary text (None → omit summary section)
    """
    segments  = data.get("segments", [])
    job_id    = data.get("job_id", "")
    filename  = data.get("filename") or os.path.basename(data.get("audio", ""))
    created   = data.get("created_at") or ""
    date_str  = created[:10] if created else time.strftime("%Y-%m-%d")
    duration  = _fmt_duration(
        max((seg.get("end", 0) for seg in segments), default=0)
    )
    speakers  = _speakers_from_segments(segments)
    source    = os.path.basename(data.get("audio", ""))

    # ── YAML frontmatter ──────────────────────────────────────────────────────
    lines = [
        "---",
        f"date: {date_str}",
        f"duration: {duration}",
        "speakers:",
    ]
    for sp in speakers:
        lines.append(f"  - {sp}")
    lines += [
        f"source: {source}",
        f"job_id: {job_id}",
        "---",
        "",
    ]

    # ── Summary section (optional) ────────────────────────────────────────────
    if summary_text and summary_text.strip():
        lines += ["## 纪要", "", summary_text.strip(), "", "---", ""]

    # ── Transcript section ────────────────────────────────────────────────────
    lines += ["## 转写原文", "", _transcript_md_body(segments)]

    return "\n".join(lines)


# ── Sync functions ─────────────────────────────────────────────────────────────

def sync_job(job_id: str, output_dir: str, obsidian_dir: str) -> str:
    """
    Write / overwrite the Obsidian MD file for *job_id*.

    Returns the absolute path of the written file.
    Raises FileNotFoundError if the transcript JSON does not exist.
    """
    json_path = os.path.join(output_dir, f"{job_id}.json")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Transcript not found: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("job_id", job_id)

    # Load latest summary (optional)
    summary_text: Optional[str] = None
    summary_path = os.path.join(output_dir, f"{job_id}_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                versions = json.load(f)
            if versions:
                summary_text = versions[-1].get("text", "")
        except Exception:
            pass

    content = build_md(data, summary_text)

    # Determine destination filename
    display_name = data.get("filename") or os.path.basename(data.get("audio", "Unknown"))
    created      = data.get("created_at") or ""
    date_str     = created[:10] if created else time.strftime("%Y-%m-%d")
    md_name      = obsidian_filename(date_str, display_name)

    os.makedirs(obsidian_dir, exist_ok=True)
    dest = os.path.join(obsidian_dir, md_name)
    tmp  = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)
    return dest


def scan_and_sync(output_dir: str, obsidian_dir: str) -> list[str]:
    """
    Scan *output_dir* for all transcript JSONs and sync each to *obsidian_dir*.
    Skips non-job files (_summary, _agent_chat, _meta suffixes).
    Returns the list of written paths (errors are silently skipped).
    """
    synced: list[str] = []
    if not os.path.isdir(output_dir):
        return synced
    for fname in os.listdir(output_dir):
        if not fname.endswith(".json"):
            continue
        stem = fname[:-5]
        # Skip metadata / sidecar files
        if any(stem.endswith(s) for s in ("_summary", "_agent_chat", "_meta")):
            continue
        try:
            path = sync_job(stem, output_dir, obsidian_dir)
            synced.append(path)
        except Exception:
            pass
    return synced
