"""Tests for src/obsidian.py"""
import json
import os
import pytest
import time

from src.obsidian import (
    _sanitize_name,
    obsidian_filename,
    build_md,
    sync_job,
    scan_and_sync,
)


# ── _sanitize_name ────────────────────────────────────────────────────────────

def test_sanitize_name_strips_slashes():
    assert "/" not in _sanitize_name("a/b/c")

def test_sanitize_name_strips_colon():
    assert ":" not in _sanitize_name("doc: title")

def test_sanitize_name_strips_control_chars():
    result = _sanitize_name("hello\x00world")
    assert "\x00" not in result

def test_sanitize_name_strips_trailing_dot():
    assert not _sanitize_name("file.").endswith(".")

def test_sanitize_name_normal_unchanged():
    assert _sanitize_name("个税副本") == "个税副本"


# ── obsidian_filename ─────────────────────────────────────────────────────────

def test_obsidian_filename_format():
    fname = obsidian_filename("2026-03-23", "个税副本")
    assert fname == "2026-03-23 个税副本.md"

def test_obsidian_filename_empty_name():
    fname = obsidian_filename("2026-03-23", "")
    assert fname == "2026-03-23 Untitled.md"

def test_obsidian_filename_sanitizes_unsafe_chars():
    fname = obsidian_filename("2026-03-23", "doc: final/v2")
    assert "/" not in fname
    assert ":" not in fname


# ── build_md ──────────────────────────────────────────────────────────────────

SEGMENTS = [
    {"speaker": "SPEAKER_00", "start": 0.0,  "end": 60.0, "text": "Hello"},
    {"speaker": "SPEAKER_01", "start": 60.0, "end": 125.0, "text": "World"},
]

DATA = {
    "job_id":     "job-001",
    "filename":   "meeting.mp3",
    "audio":      "/tmp/meeting.mp3",
    "created_at": "2026-03-23T10:00:00",
    "segments":   SEGMENTS,
}

def test_build_md_has_frontmatter():
    md = build_md(DATA)
    assert md.startswith("---\n")
    assert "date: 2026-03-23" in md
    assert "job_id: job-001" in md

def test_build_md_lists_speakers():
    md = build_md(DATA)
    assert "SPEAKER_00" in md
    assert "SPEAKER_01" in md

def test_build_md_duration_computed():
    md = build_md(DATA)
    # max end is 125s → 02:05
    assert "duration: 02:05" in md

def test_build_md_transcript_section():
    md = build_md(DATA)
    assert "## 转写原文" in md
    assert "Hello" in md
    assert "World" in md

def test_build_md_no_summary_omits_section():
    md = build_md(DATA)
    assert "## 纪要" not in md

def test_build_md_with_summary():
    md = build_md(DATA, summary_text="这是会议纪要。")
    assert "## 纪要" in md
    assert "这是会议纪要。" in md

def test_build_md_empty_summary_omits_section():
    md = build_md(DATA, summary_text="   ")
    assert "## 纪要" not in md

def test_build_md_empty_segments():
    data = dict(DATA, segments=[])
    md = build_md(data)
    assert "duration: 00:00" in md

def test_build_md_duration_with_hours():
    segs = [{"speaker": "A", "start": 0.0, "end": 3665.0, "text": "x"}]
    md = build_md(dict(DATA, segments=segs))
    # 3665s = 1:01:05
    assert "duration: 1:01:05" in md


# ── sync_job ──────────────────────────────────────────────────────────────────

def test_sync_job_creates_md_file(tmp_path):
    job_id   = "job-sync-001"
    obs_dir  = tmp_path / "obsidian"
    obs_dir.mkdir()
    (tmp_path / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")

    dest = sync_job(job_id, str(tmp_path), str(obs_dir))
    assert os.path.isfile(dest)
    assert dest.endswith(".md")

def test_sync_job_content_correct(tmp_path):
    job_id   = "job-sync-002"
    obs_dir  = tmp_path / "obsidian"
    obs_dir.mkdir()
    # Omit job_id from file so setdefault assigns the correct job_id
    data = {k: v for k, v in DATA.items() if k != "job_id"}
    (tmp_path / f"{job_id}.json").write_text(json.dumps(data), encoding="utf-8")

    dest = sync_job(job_id, str(tmp_path), str(obs_dir))
    content = open(dest, encoding="utf-8").read()
    assert "SPEAKER_00" in content
    assert "job_id: job-sync-002" in content

def test_sync_job_includes_summary(tmp_path):
    job_id    = "job-sync-003"
    obs_dir   = tmp_path / "obsidian"
    obs_dir.mkdir()
    (tmp_path / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")
    versions = [{"text": "会议纪要内容", "created_at": "2026-03-23T10:00:00"}]
    (tmp_path / f"{job_id}_summary.json").write_text(json.dumps(versions), encoding="utf-8")

    dest = sync_job(job_id, str(tmp_path), str(obs_dir))
    content = open(dest, encoding="utf-8").read()
    assert "会议纪要内容" in content

def test_sync_job_raises_if_no_json(tmp_path):
    with pytest.raises(FileNotFoundError):
        sync_job("nonexistent", str(tmp_path), str(tmp_path / "obs"))

def test_sync_job_creates_obs_dir(tmp_path):
    job_id  = "job-sync-004"
    obs_dir = tmp_path / "new_obs_dir"
    (tmp_path / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")

    dest = sync_job(job_id, str(tmp_path), str(obs_dir))
    assert os.path.isdir(obs_dir)
    assert os.path.isfile(dest)

def test_sync_job_atomic_write(tmp_path):
    """No .tmp file should remain after sync."""
    job_id  = "job-sync-005"
    obs_dir = tmp_path / "obsidian"
    obs_dir.mkdir()
    (tmp_path / f"{job_id}.json").write_text(json.dumps(DATA), encoding="utf-8")

    sync_job(job_id, str(tmp_path), str(obs_dir))
    tmp_files = [f for f in os.listdir(obs_dir) if f.endswith(".tmp")]
    assert tmp_files == []


# ── scan_and_sync ─────────────────────────────────────────────────────────────

def test_scan_and_sync_processes_all_jobs(tmp_path):
    obs_dir = tmp_path / "obsidian"
    for jid in ("job-a", "job-b"):
        (tmp_path / f"{jid}.json").write_text(json.dumps(DATA), encoding="utf-8")

    synced = scan_and_sync(str(tmp_path), str(obs_dir))
    assert len(synced) == 2

def test_scan_and_sync_skips_sidecar_files(tmp_path):
    obs_dir = tmp_path / "obsidian"
    (tmp_path / "job-main.json").write_text(json.dumps(DATA), encoding="utf-8")
    (tmp_path / "job-main_summary.json").write_text("[]", encoding="utf-8")
    (tmp_path / "job-main_agent_chat.json").write_text("{}", encoding="utf-8")
    (tmp_path / "job-main_meta.json").write_text("{}", encoding="utf-8")

    synced = scan_and_sync(str(tmp_path), str(obs_dir))
    assert len(synced) == 1

def test_scan_and_sync_empty_dir(tmp_path):
    obs_dir = tmp_path / "obsidian"
    synced  = scan_and_sync(str(tmp_path), str(obs_dir))
    assert synced == []

def test_scan_and_sync_missing_dir(tmp_path):
    synced = scan_and_sync(str(tmp_path / "nonexistent"), str(tmp_path / "obs"))
    assert synced == []

def test_scan_and_sync_skips_invalid_json(tmp_path):
    obs_dir = tmp_path / "obsidian"
    (tmp_path / "good-job.json").write_text(json.dumps(DATA), encoding="utf-8")
    (tmp_path / "bad-job.json").write_text("NOT JSON", encoding="utf-8")

    # Should not raise; bad-job silently skipped
    synced = scan_and_sync(str(tmp_path), str(obs_dir))
    assert len(synced) == 1
