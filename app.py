"""
AudioAssist — PyWebView entry point.
Exposes Python API to the JavaScript frontend via js_api.

All public methods return JSON-serializable values.
Long-running operations (transcribe, download_model) run in background threads
and push progress via window.evaluate_js().
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

from platformdirs import user_data_dir
from src.session_paths import get_session_paths, resolve_summary_path, resolve_transcript_path

logger = logging.getLogger(__name__)

# Resolved at runtime after webview window is created
_window = None

APP_DATA_DIR = user_data_dir("TranscribeApp", appauthor=False)
_DEFAULT_OUTPUT_DIR = os.path.join(APP_DATA_DIR, "output")
CONFIG_PATH = os.path.join(APP_DATA_DIR, "config.json")
TEMPLATES_PATH = os.path.join(APP_DATA_DIR, "templates.json")

# Known audio extensions used when copying source files into the output dir
_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".mov", ".mkv")
_REALTIME_WAV_PREFIX = "realtime_recording"


def _resolve_output_dir() -> str:
    """Return OUTPUT_DIR: persisted path from config if valid, else default."""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as _f:
                _cfg = json.load(_f)
            stored = _cfg.get("storage", {}).get("output_dir", "")
            if stored and os.path.isdir(stored):
                return stored
    except Exception:
        pass
    return _DEFAULT_OUTPUT_DIR


OUTPUT_DIR = _resolve_output_dir()


# ── Session-per-directory helpers (F6) ─────────────────────────────────────────

def _session_dir(job_id: str) -> str:
    """Return the per-session directory path under OUTPUT_DIR/meetings/."""
    return get_session_paths(OUTPUT_DIR, job_id).session_dir


def _transcript_path(job_id: str) -> Optional[str]:
    """Return transcript.json path for job_id using new or legacy layout."""
    return resolve_transcript_path(OUTPUT_DIR, job_id)


def _summary_path(job_id: str) -> str:
    """Return summary.json path for job_id using new or legacy layout."""
    return resolve_summary_path(OUTPUT_DIR, job_id)


def _realtime_wav_name(session_id: str) -> str:
    """Return a collision-resistant realtime WAV filename for this session."""
    return f"{_REALTIME_WAV_PREFIX}_{session_id[:8]}.wav"


def _read_json_file(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json_atomic(path: str, data: dict) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _get_transcript_lock(job_id: str) -> threading.Lock:
    with _transcript_locks_mutex:
        if job_id not in _transcript_locks:
            _transcript_locks[job_id] = threading.Lock()
        return _transcript_locks[job_id]


def _realtime_meta_path_for_wav(wav_path: str) -> Optional[str]:
    """Return meta.json path for a realtime WAV in new or legacy layout."""
    src_abs = os.path.abspath(wav_path)
    meetings_dir = os.path.abspath(os.path.join(OUTPUT_DIR, "meetings"))
    rt_session_dir = os.path.dirname(src_abs)
    if os.path.dirname(rt_session_dir) == meetings_dir:
        return os.path.join(rt_session_dir, "meta.json")

    out_abs = os.path.abspath(OUTPUT_DIR)
    if os.path.dirname(src_abs) == out_abs:
        stem = os.path.splitext(os.path.basename(src_abs))[0]
        return os.path.join(OUTPUT_DIR, f"{stem}_meta.json")
    return None


def _find_realtime_wav(session_dir: str) -> Optional[str]:
    """Find the realtime WAV for a session, supporting old and new basenames."""
    legacy = os.path.join(session_dir, "realtime_recording.wav")
    if os.path.exists(legacy):
        return legacy

    try:
        for name in sorted(os.listdir(session_dir)):
            if name.startswith(_REALTIME_WAV_PREFIX + "_") and name.endswith(".wav"):
                return os.path.join(session_dir, name)
    except Exception:
        return None
    return None


_LANG_INSTRUCTIONS: dict[str, str] = {
    "zh":    "请用中文生成纪要。",
    "yue":   "請用粵語（繁體中文）生成紀要。",
    "ja":    "日本語で要約してください。",
    "ko":    "한국어로 요약해 주세요。",
    "fr":    "Veuillez répondre en français.",
    "de":    "Bitte auf Deutsch antworten.",
    "es":    "Por favor, responde en español.",
    "pt":    "Por favor, responda em português.",
    "ru":    "Пожалуйста, отвечайте на русском языке.",
    "ar":    "يرجى الرد باللغة العربية.",
    "en":    "Please respond in English.",
}


def _lang_instruction(language: str, text: str) -> str:
    """Return a language instruction to prepend to the LLM prompt."""
    lang = (language or "").strip().lower()
    if lang in _LANG_INSTRUCTIONS:
        return _LANG_INSTRUCTIONS[lang]
    # Auto-detect: if >10 % of chars are CJK, assume Chinese
    if text:
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if cjk / len(text) > 0.10:
            return _LANG_INSTRUCTIONS["zh"]
    return ""


_DEFAULT_TEMPLATES = [
    {
        "name": "General Summary",
        "prompt": (
            "Please summarize the following transcript concisely. "
            "Highlight the main topics discussed and any key decisions or action items."
        ),
    },
    {
        "name": "Meeting Notes",
        "prompt": (
            "Convert the following meeting transcript into structured meeting notes. "
            "Include: attendees (if mentioned), agenda items, decisions made, and action items with owners."
        ),
    },
    {
        "name": "Key Points",
        "prompt": (
            "Extract the key points from the following transcript as a bulleted list. "
            "Be concise and focus on the most important information."
        ),
    },
]

# Per-job cancel events for in-flight transcriptions
_cancel_flags: dict[str, threading.Event] = {}
_cancel_flags_mutex = threading.Lock()

# Guard so the Obsidian startup scan runs only once per process
_obsidian_startup_done = False


class _TranscriptionCancelled(Exception):
    """Raised inside transcribe() thread when the job is cancelled."""


# Per-job lock to prevent concurrent read-modify-write on transcript files
# TODO: _transcript_locks grows unbounded (one entry per job for the lifetime of
#   the process).  Fine for a single-session desktop app, but should be cleaned
#   up (e.g. evict locks after save_transcript returns, or use a WeakValueDictionary)
#   if the app ever supports long-running multi-session use.
_transcript_locks: dict[str, threading.Lock] = {}
_transcript_locks_mutex = threading.Lock()


def _push(js: str):
    """Evaluate JS in the webview window (thread-safe)."""
    if _window is not None:
        _window.evaluate_js(js)


# ── Export helpers ─────────────────────────────────────────────────────────────

def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _transcript_to_txt(segments: list) -> str:
    return "\n".join(
        f"[{_fmt_time(seg.get('start', 0))}] {seg.get('speaker', '')}: {seg.get('text', '').strip()}"
        for seg in segments
    )


def _transcript_to_md(segments: list) -> str:
    lines = ["# Transcript", ""]
    for seg in segments:
        lines.append(f"**{_fmt_time(seg.get('start', 0))} {seg.get('speaker', '')}**  ")
        lines.append(seg.get("text", "").strip())
        lines.append("")
    return "\n".join(lines)


# Maximum wall-clock seconds allowed for the background refine thread.
# If exceeded, onTranscribeRefined is pushed immediately so the UI unblocks
# and the user sees the realtime draft instead of spinning forever.
REFINE_TIMEOUT = 1800  # 30 minutes


class API:
    def __init__(self):
        self._realtime = None  # RealtimeTranscriber instance when active
        # Pre-collected realtime segments keyed by wav_path — consumed by transcribe()
        self._rt_segments: dict[str, list] = {}
        self._caffeinate_proc = None  # caffeinate subprocess (macOS only)

    # ── File selection ─────────────────────────────────────────────────────────

    def select_file(self) -> Optional[str]:
        """Open a native file picker. Returns selected path or None."""
        import webview
        result = _window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("Audio Video (*.mp3;*.mp4;*.m4a;*.wav;*.flac;*.ogg;*.aac;*.mov;*.mkv)",),
        )
        return result[0] if result else None

    def select_files(self) -> list[str]:
        """Open a native multi-file picker. Returns list of selected paths."""
        import webview
        result = _window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("Audio Video (*.mp3;*.mp4;*.m4a;*.wav;*.flac;*.ogg;*.aac;*.mov;*.mkv)",),
        )
        return list(result) if result else []

    def resume_transcription(self, job_id: str) -> dict:
        """
        Resume an interrupted transcription job (F2).
        Reads checkpoint to find source audio and options, then calls transcribe().

        Returns: {"job_id": str} on success, {"error": "..."} if no checkpoint.
        """
        from src.checkpoint import read as ckpt_read
        ckpt = ckpt_read(_session_dir(job_id))
        if not ckpt:
            return {"error": "no_checkpoint"}
        opts = {
            "engine": ckpt.get("engine", "qwen"),
            "model_id": ckpt.get("model_id"),
        }
        return self.transcribe(ckpt["source_audio"], opts, job_id=job_id)

    def discard_checkpoint(self, job_id: str) -> bool:
        """Delete the checkpoint for a job without resuming (F2)."""
        from src.checkpoint import delete as ckpt_delete
        ckpt_delete(_session_dir(job_id))
        return True

    # ── Transcription ──────────────────────────────────────────────────────────

    def transcribe(self, path: str, options: dict, job_id: Optional[str] = None) -> dict:
        """
        Start transcription in background thread.
        Pushes onTranscribeProgress(job_id, progress, message) events to JS.

        If pre-collected realtime segments exist for this path (stored by
        stop_realtime()), skips ASR and runs diarization only.

        job_id: optional, used when resuming a checkpoint (F2).

        Returns: {"job_id": str}
        """
        import uuid
        if job_id is None:
            job_id = str(uuid.uuid4())

        # Pop realtime segments if this is a just-finished realtime session.
        # Must be popped here (before the thread starts) to avoid a TOCTOU race.
        rt_segments = self._rt_segments.pop(path, None)

        # Resolve ASR engine/model before spawning thread so both the main
        # transcription and the optional background refine use the same choice.
        model_id = options.get("model_id")
        engine   = options.get("engine", "qwen")
        if model_id:
            from src.model_manager import CATALOG as _CATALOG
            _info = next((m for m in _CATALOG if m.id == model_id), None)
            if _info:
                engine = "whisper" if _info.engine == "mlx-whisper" else _info.engine

        def _run():
            cancel_ev = threading.Event()
            with _cancel_flags_mutex:
                _cancel_flags[job_id] = cancel_ev
            try:
                def _progress(pct: float, msg: str):
                    if cancel_ev.is_set():
                        raise _TranscriptionCancelled()
                    js = f"onTranscribeProgress({json.dumps(job_id)}, {pct:.4f}, {json.dumps(msg)})"
                    _push(js)

                hf_token     = options.get("hf_token") or os.environ.get("HF_TOKEN")
                num_speakers = options.get("num_speakers")
                # F6: create session dir
                s_dir = _session_dir(job_id)
                os.makedirs(s_dir, exist_ok=True)
                preferred_filename: Optional[str] = None
                transcript_path = os.path.join(s_dir, "transcript.json")
                if os.path.exists(transcript_path):
                    existing = _read_json_file(transcript_path, {})
                    if isinstance(existing, dict):
                        value = (existing.get("filename") or "").strip()
                        preferred_filename = value or None
                session_meta_path = os.path.join(s_dir, "meta.json")
                if preferred_filename is None and os.path.exists(session_meta_path):
                    meta = _read_json_file(session_meta_path, {})
                    if isinstance(meta, dict):
                        value = (meta.get("filename") or "").strip()
                        preferred_filename = value or None
                if preferred_filename is None and path.lower().endswith(".wav"):
                    meta_path = _realtime_meta_path_for_wav(path)
                    if meta_path and os.path.exists(meta_path):
                        meta = _read_json_file(meta_path, {})
                        if isinstance(meta, dict):
                            value = (meta.get("filename") or "").strip()
                            preferred_filename = value or None

                if rt_segments:
                    # Phase 1 — diarize-only: fast initial draft from live chunks
                    from src.pipeline import run_realtime_segments
                    json_path, md_path = run_realtime_segments(
                        segments=rt_segments,
                        wav_path=path,
                        output_dir=s_dir,
                        hf_token=hf_token,
                        num_speakers=num_speakers,
                        job_id=job_id,
                        output_stem="transcript",
                        progress_callback=_progress,
                    )
                else:
                    from src.pipeline import run as pipeline_run, ModelNotReadyError
                    json_path, md_path = pipeline_run(
                        audio_path=path,
                        output_dir=s_dir,
                        hf_token=hf_token,
                        engine=engine,
                        asr_model_id=model_id,
                        num_speakers=num_speakers,
                        job_id=job_id,
                        output_stem="transcript",
                        session_dir=s_dir,
                        progress_callback=_progress,
                    )

                # Copy original audio into output dir for persistent playback.
                # The copy is kept alongside the transcript so history playback
                # works regardless of whether the source file has moved or been
                # deleted.  If the copy fails we log a warning and continue.
                audio_copy: Optional[str] = None
                try:
                    _, ext = os.path.splitext(path)
                    # F6: copy audio into session dir as source_audio.{ext}
                    audio_copy = os.path.join(s_dir, f"source_audio{ext}")
                    shutil.copy2(path, audio_copy)
                except Exception:
                    logger.warning("transcribe: failed to copy audio file", exc_info=True)
                try:
                    # Patch durable metadata even if audio copy failed.
                    lock = _get_transcript_lock(job_id)
                    with lock:
                        data = _read_json_file(json_path, {})
                        if audio_copy:
                            data["audio"] = audio_copy
                        data["job_id"] = job_id
                        if preferred_filename:
                            data["filename"] = preferred_filename
                        _write_json_atomic(json_path, data)
                except Exception:
                    logger.warning("transcribe: failed to patch transcript metadata", exc_info=True)

                # If source was a realtime WAV from our output dir, record
                # transcribed_job_id in its meta.json so get_history() can
                # skip the orphaned WAV entry on next launch.
                try:
                    if path.lower().endswith(".wav"):
                        meta_path_rt = _realtime_meta_path_for_wav(path)
                        if meta_path_rt:
                            rt_meta = _read_json_file(meta_path_rt, {})
                            if not isinstance(rt_meta, dict):
                                rt_meta = {}
                            rt_meta["transcribed_job_id"] = job_id
                            _write_json_atomic(meta_path_rt, rt_meta)
                except Exception:
                    logger.warning("transcribe: failed to write WAV meta", exc_info=True)

                # Notify frontend — include hasRefine flag so JS can enter
                # the 'refining' status while the background re-transcription runs.
                has_refine = bool(rt_segments)
                _push(
                    f"onTranscribeComplete({json.dumps(job_id)}, "
                    f"{json.dumps(json_path)}, {json.dumps(has_refine)})"
                )

                # Phase 2 (realtime only) — full ASR re-transcription in background.
                # Runs silently; on success overwrites JSON and notifies JS.
                if rt_segments:
                    _ac = audio_copy  # capture for closure

                    def _refine():
                        # Guard events — ensure onTranscribeRefined is pushed
                        # exactly once regardless of which path (success/timeout) wins.
                        _succeeded = threading.Event()
                        _timed_out = threading.Event()

                        def _on_timeout():
                            if not _succeeded.is_set():
                                _timed_out.set()
                                logger.warning(
                                    "Refine ASR timed out after %ds for job %s; "
                                    "falling back to realtime draft",
                                    REFINE_TIMEOUT, job_id,
                                )
                                _push(f"onTranscribeRefined({json.dumps(job_id)})")

                        timer = threading.Timer(REFINE_TIMEOUT, _on_timeout)
                        timer.daemon = True
                        timer.start()
                        try:
                            from src.pipeline import run as pipeline_run
                            logger.info("Refine ASR started for job %s", job_id)
                            lock = _get_transcript_lock(job_id)
                            preserved_filename = None
                            preserved_job_id = job_id
                            if os.path.exists(json_path):
                                with lock:
                                    current = _read_json_file(json_path, {})
                                if isinstance(current, dict):
                                    preserved_filename = current.get("filename") or None
                                    preserved_job_id = current.get("job_id") or job_id
                            refined_json, _ = pipeline_run(
                                audio_path=path,
                                output_dir=s_dir,
                                hf_token=hf_token,
                                engine=engine,
                                asr_model_id=model_id,
                                num_speakers=num_speakers,
                                job_id=job_id,  # same id → overwrites draft
                                output_stem="transcript",
                                session_dir=s_dir,
                                progress_callback=lambda p, m: logger.debug(
                                    "[refine %d%%] %s", int(p * 100), m
                                ),
                            )
                            # Re-patch durable metadata after pipeline overwrite.
                            with lock:
                                try:
                                    rdata = _read_json_file(refined_json, {})
                                    if _ac:
                                        rdata["audio"] = _ac
                                    rdata["job_id"] = preserved_job_id
                                    if preserved_filename:
                                        rdata["filename"] = preserved_filename
                                    _write_json_atomic(refined_json, rdata)
                                except Exception:
                                    logger.warning(
                                        "refine: failed to patch audio/write JSON", exc_info=True
                                    )
                            _succeeded.set()
                            timer.cancel()
                            if not _timed_out.is_set():
                                _push(f"onTranscribeRefined({json.dumps(job_id)})")
                                logger.info("Refine ASR complete for job %s", job_id)
                            else:
                                logger.info(
                                    "Refine ASR finished after timeout for job %s "
                                    "(result on disk; UI already unblocked)",
                                    job_id,
                                )
                        except Exception:
                            logger.exception("Refine ASR failed for job %s", job_id)
                            _succeeded.set()
                            timer.cancel()

                    threading.Thread(target=_refine, daemon=True).start()

            except _TranscriptionCancelled:
                logger.info("Transcription cancelled: %s", job_id)
                _push(f"onTranscribeCancel({json.dumps(job_id)})")
            except Exception as e:
                logger.exception("Transcription failed")
                # Import here to avoid circular import issues at module load
                try:
                    from src.pipeline import ModelNotReadyError as _MNR
                    if isinstance(e, _MNR):
                        pass  # fall through to generic error push below
                except ImportError:
                    pass
                js = f"onTranscribeError({json.dumps(job_id)}, {json.dumps(str(e))})"
                _push(js)
            finally:
                with _cancel_flags_mutex:
                    _cancel_flags.pop(job_id, None)

        threading.Thread(target=_run, daemon=True).start()
        return {"job_id": job_id}

    def cancel_transcription(self, job_id: str) -> bool:
        """
        Request cancellation of an in-flight transcription job.

        Returns True if the job was found and flagged for cancellation,
        False if no matching job is running.
        """
        with _cancel_flags_mutex:
            ev = _cancel_flags.get(job_id)
        if ev is None:
            return False
        ev.set()
        return True

    def get_transcript(self, job_id: str) -> Optional[dict]:
        """Load transcript JSON for a given job_id."""
        path = _transcript_path(job_id)
        if not path:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save_transcript(self, job_id: str, edits: list[dict]) -> bool:
        """
        Persist edited transcript segments.

        edits: list of {"speaker", "start", "end", "text", "words"} dicts.
        Uses a per-job lock and atomic rename to prevent data corruption from
        concurrent saves or a mid-write crash.
        """
        path = _transcript_path(job_id)
        with _transcript_locks_mutex:
            if job_id not in _transcript_locks:
                _transcript_locks[job_id] = threading.Lock()
            lock = _transcript_locks[job_id]

        with lock:
            if not path or not os.path.exists(path):
                return False
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["segments"] = edits

            # Atomic JSON write
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)

            # Regenerate markdown sidecar
            try:
                from src.merge import SpeakerBlock, to_markdown
                blocks = [SpeakerBlock(**{k: seg[k] for k in ("speaker", "start", "end", "text", "words")}) for seg in edits]
                md_path = path[:-5] + ".md"  # replace .json with .md
                to_markdown(blocks, data.get("audio", ""), data.get("language", ""), md_path)
            except Exception:
                logger.warning("save_transcript: failed to regenerate .md", exc_info=True)

        threading.Thread(target=lambda: self._obsidian_auto_sync(job_id), daemon=True).start()
        return True

    def rename_speaker(self, job_id: str, old_speaker: str, new_speaker: str) -> dict:
        """
        Rename ALL segments where speaker == old_speaker to new_speaker.

        Returns {"ok": True, "segments": [...]} on success,
                {"ok": False, "error": "..."} on failure.
        """
        new_speaker = (new_speaker or "").strip()
        if not new_speaker:
            return {"ok": False, "error": "Speaker name cannot be empty"}

        path = _transcript_path(job_id)
        with _transcript_locks_mutex:
            if job_id not in _transcript_locks:
                _transcript_locks[job_id] = threading.Lock()
            lock = _transcript_locks[job_id]

        with lock:
            if not path or not os.path.exists(path):
                return {"ok": False, "error": "File not found"}
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            for seg in data.get("segments", []):
                if seg.get("speaker") == old_speaker:
                    seg["speaker"] = new_speaker

            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)

            try:
                from src.merge import SpeakerBlock, to_markdown
                segs = data.get("segments", [])
                blocks = [SpeakerBlock(**{k: s[k] for k in ("speaker", "start", "end", "text", "words")}) for s in segs]
                md_path = path[:-5] + ".md"
                to_markdown(blocks, data.get("audio", ""), data.get("language", ""), md_path)
            except Exception:
                logger.warning("rename_speaker: failed to regenerate .md", exc_info=True)

        return {"ok": True, "segments": data.get("segments", [])}

    def rename_segment_speaker(self, job_id: str, segment_index: int, new_speaker: str) -> dict:
        """
        Rename the speaker of the single segment at segment_index.

        Returns {"ok": True, "segment": {...}} on success,
                {"ok": False, "error": "..."} on failure.
        """
        new_speaker = (new_speaker or "").strip()
        if not new_speaker:
            return {"ok": False, "error": "Speaker name cannot be empty"}

        path = _transcript_path(job_id)
        with _transcript_locks_mutex:
            if job_id not in _transcript_locks:
                _transcript_locks[job_id] = threading.Lock()
            lock = _transcript_locks[job_id]

        with lock:
            if not path or not os.path.exists(path):
                return {"ok": False, "error": "File not found"}
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            segs = data.get("segments", [])
            if segment_index < 0 or segment_index >= len(segs):
                return {"ok": False, "error": f"Index {segment_index} out of range"}

            segs[segment_index]["speaker"] = new_speaker

            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)

            try:
                from src.merge import SpeakerBlock, to_markdown
                blocks = [SpeakerBlock(**{k: s[k] for k in ("speaker", "start", "end", "text", "words")}) for s in segs]
                md_path = path[:-5] + ".md"
                to_markdown(blocks, data.get("audio", ""), data.get("language", ""), md_path)
            except Exception:
                logger.warning("rename_segment_speaker: failed to regenerate .md", exc_info=True)

        return {"ok": True, "segment": segs[segment_index]}

    # ── Realtime ───────────────────────────────────────────────────────────────

    def preflight_capture(self, mode: str) -> dict:
        """
        Check capture prerequisites for the given mode.

        Returns a structured dict:
          {
            "supported": bool,
            "missing_permissions": [...],
            "os_version": str,
            "reason": str | None,
          }

        For "mic" mode this always returns supported=True (existing path).
        For "system" / "mix" modes it checks macOS version and helper binary.
        """
        import platform

        os_version = platform.mac_ver()[0]
        result: dict = {
            "supported":           True,
            "missing_permissions": [],
            "os_version":          os_version,
            "reason":              None,
        }

        if mode in ("system", "mix"):
            # macOS version gate
            try:
                parts = [int(x) for x in os_version.split(".")]
                if (parts[0], parts[1] if len(parts) > 1 else 0) < (13, 0):
                    result["supported"] = False
                    result["reason"]    = "screencapturekit_requires_macos_13_0"
                    return result
            except (ValueError, IndexError):
                pass  # cannot parse version — proceed

            # Helper binary check
            from src.native_capture import _default_helper_path
            helper = _default_helper_path()
            if not os.path.isfile(helper) or not os.access(helper, os.X_OK):
                result["supported"]   = False
                result["reason"]      = "helper_not_found"
                result["helper_path"] = helper
                return result

        return result

    # ── Screen-sleep prevention (macOS caffeinate) ─────────────────────────────

    def _caffeinate_start(self) -> None:
        """Hold a caffeinate lock to prevent display and idle sleep (macOS only)."""
        if sys.platform != "darwin":
            return
        if self._caffeinate_proc is not None:
            return  # already running
        try:
            self._caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-d", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            logger.warning("caffeinate: failed to start", exc_info=True)

    def _caffeinate_stop(self) -> None:
        """Release the caffeinate lock."""
        proc = self._caffeinate_proc
        self._caffeinate_proc = None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                logger.warning("caffeinate: failed to terminate", exc_info=True)

    def start_realtime(self, options: Optional[dict] = None) -> dict:
        """
        Start realtime microphone transcription in a background thread.
        Model loading happens asynchronously; pushes JS events when ready:
          onRealtimeStarted()            — models loaded, microphone open
          onRealtimeResult(text)         — each transcribed utterance
          onRealtimeError(message)       — error during load or transcription

        Returns: {"status": "started"} or {"status": "already_running"}
        """
        if self._realtime is not None:
            return {"status": "already_running"}

        # Sentinel prevents a second call racing before _run() sets self._realtime
        self._realtime = object()
        self._caffeinate_start()

        options = options or {}
        model_id = options.get("model_id")
        engine   = options.get("engine", "qwen")
        if model_id:
            from src.model_manager import CATALOG
            _info = next((m for m in CATALOG if m.id == model_id), None)
            if _info:
                engine = "whisper" if _info.engine == "mlx-whisper" else _info.engine

        capture_mode = options.get("capture_mode", "mic")

        def _run():
            try:
                import uuid
                session_id = str(uuid.uuid4())
                # F6: realtime WAV goes into session dir
                rt_session_dir = _session_dir(session_id)
                os.makedirs(rt_session_dir, exist_ok=True)
                output_path = os.path.join(rt_session_dir, _realtime_wav_name(session_id))

                if capture_mode == "system":
                    from src.native_capture import NativeCaptureHelper
                    rt = NativeCaptureHelper(
                        mode="system",
                        engine=engine,
                        output_path=output_path,
                        on_result=lambda seg: _push(f"onRealtimeResult({json.dumps(seg)})"),
                        on_error=lambda msg:  _push(f"onRealtimeError({json.dumps(msg)})"),
                    )
                else:
                    # Default: mic mode — existing sounddevice path unchanged
                    from src.realtime import RealtimeTranscriber
                    rt = RealtimeTranscriber(
                        engine=engine,
                        output_path=output_path,
                        on_result=lambda seg: _push(f"onRealtimeResult({json.dumps(seg)})"),
                        on_error=lambda msg:  _push(f"onRealtimeError({json.dumps(msg)})"),
                    )
                rt.start()
                # Race: stop_realtime() may have cleared self._realtime while
                # models were loading. If so, shut down immediately and return.
                if self._realtime is None:
                    rt.stop()
                    return
                self._realtime = rt
                _push(f"onRealtimeStarted({json.dumps(session_id)}, {json.dumps(output_path)})")
            except Exception as e:
                logger.exception("Realtime start failed")
                self._realtime = None
                self._caffeinate_stop()
                _push(f"onRealtimeError({json.dumps(str(e))})")

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "started"}

    def pause_realtime(self) -> dict:
        """
        Pause realtime transcription.
        Pushes onRealtimePaused() after the stream is paused.

        Returns: {"status": "pausing"} or {"status": "not_running"}
        """
        rt = self._realtime
        if rt is None or not hasattr(rt, 'pause'):
            return {"status": "not_running"}

        def _run():
            try:
                rt.pause()
            except Exception:
                logger.exception("Realtime pause failed")
            _push("onRealtimePaused()")

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "pausing"}

    def resume_realtime(self) -> dict:
        """
        Resume realtime transcription.
        Pushes onRealtimeResumed() after the stream is resumed.

        Returns: {"status": "resuming"} or {"status": "not_running"}
        """
        rt = self._realtime
        if rt is None or not hasattr(rt, 'resume'):
            return {"status": "not_running"}

        def _run():
            try:
                rt.resume()
            except Exception:
                logger.exception("Realtime resume failed")
            _push("onRealtimeResumed()")

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "resuming"}

    def stop_realtime(self) -> dict:
        """
        Stop realtime transcription.
        Pushes onRealtimeStopped() after the stream is closed.

        After stop(), collects accumulated segments from the transcriber so that
        the subsequent transcribe() call (triggered by JS) can skip ASR and run
        diarization only.

        Returns: {"status": "stopped"} or {"status": "not_running"}
        """
        rt = self._realtime
        self._realtime = None
        self._caffeinate_stop()
        if rt is None:
            return {"status": "not_running"}

        def _run():
            try:
                if hasattr(rt, "stop"):
                    rt.stop()
                # After stop(), the worker queue is fully drained — segments complete.
                wav_path = getattr(rt, "_output_path", None)
                if wav_path and hasattr(rt, "get_segments"):
                    segs = rt.get_segments()
                    if segs:
                        self._rt_segments[wav_path] = segs
            except Exception:
                logger.exception("Realtime stop failed")
            _push("onRealtimeStopped()")

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "stopped"}

    # ── Summary ────────────────────────────────────────────────────────────────

    def summarize(self, job_id: str, template: dict) -> dict:
        """
        Generate a summary for the given transcript in a background thread.

        Pushes JS events:
          onSummaryChunk(jobId, chunk)       — streaming chunk
          onSummaryComplete(jobId, fullText) — generation finished
          onSummaryError(jobId, message)     — error

        template: {"name": str, "prompt": str}
        Returns: {"job_id": str, "status": "started"}
        """
        def _run():
            try:
                path = _transcript_path(job_id)
                if not path or not os.path.exists(path):
                    _push(f"onSummaryError({json.dumps(job_id)}, {json.dumps('Transcript not found')})")
                    return

                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                text = " ".join(seg.get("text", "") for seg in data.get("segments", []))

                cfg = self._load_config().get("api", {})
                base_url = cfg.get("base_url", "")
                api_key  = cfg.get("api_key", "")
                model    = cfg.get("model", "")
                prompt   = template.get("prompt", "Summarize the following transcript.")
                lang_inst = _lang_instruction(data.get("language", ""), text)
                if lang_inst:
                    prompt = lang_inst + "\n\n" + prompt

                from src.summary import summarize as _summarize
                chunks = _summarize(text, prompt, base_url, api_key, model, stream=True)

                full_text = ""
                for chunk in chunks:
                    full_text += chunk
                    _push(f"onSummaryChunk({json.dumps(job_id)}, {json.dumps(chunk)})")

                _push(f"onSummaryComplete({json.dumps(job_id)}, {json.dumps(full_text)})")

            except Exception as e:
                logger.exception("Summary failed")
                _push(f"onSummaryError({json.dumps(job_id)}, {json.dumps(str(e))})")

        threading.Thread(target=_run, daemon=True).start()
        return {"job_id": job_id, "status": "started"}

    # ── Summary Agent ──────────────────────────────────────────────────────────

    def start_agent_turn(self, job_id: str, user_input: str) -> dict:
        """
        Start one agent turn in a background thread.

        Pushes JS events:
          onAgentChunk(jobId, chunk)           — streaming text delta
          onAgentToolStart(jobId, toolName)    — tool call beginning
          onAgentToolEnd(jobId, toolName)      — tool call finished
          onAgentDraftUpdated(jobId, newText)  — update_summary tool saved a new version
          onAgentComplete(jobId, fullText)     — turn finished
          onAgentError(jobId, message)         — error

        Returns: {"job_id": str, "status": "started"}
        """
        def _run():
            try:
                cfg = self._load_config().get("api", {})
                from src.agent_store import (
                    build_context_messages,
                    load_session,
                    save_turn,
                )

                session = load_session(OUTPUT_DIR, job_id)
                history = build_context_messages(session)

                tool_events: list[dict] = []

                def _push_event(event_name: str, payload: dict):
                    if event_name == "onAgentChunk":
                        _push(f"onAgentChunk({json.dumps(job_id)}, {json.dumps(payload['chunk'])})")
                    elif event_name == "onAgentToolStart":
                        _push(f"onAgentToolStart({json.dumps(job_id)}, {json.dumps(payload['tool'])})")
                        tool_events.append({"tool": payload["tool"], "status": "started"})
                    elif event_name == "onAgentToolEnd":
                        _push(f"onAgentToolEnd({json.dumps(job_id)}, {json.dumps(payload['tool'])})")
                        # Update last matching started entry to ok
                        for ev in reversed(tool_events):
                            if ev["tool"] == payload["tool"] and ev["status"] == "started":
                                ev["status"] = "ok"
                                break
                    elif event_name == "onAgentDraftUpdated":
                        _push(f"onAgentDraftUpdated({json.dumps(job_id)}, {json.dumps(payload['text'])})")

                from src.agent import MeetingAgent

                agent = MeetingAgent(
                    output_dir=OUTPUT_DIR,
                    base_url=cfg.get("base_url", ""),
                    api_key=cfg.get("api_key", ""),
                    model=cfg.get("model", ""),
                    push_event=_push_event,
                )
                full_text = agent.run(job_id, user_input, history)
                _push(f"onAgentComplete({json.dumps(job_id)}, {json.dumps(full_text)})")
                completed_tool_events = [e for e in tool_events if e["status"] == "ok"]
                save_turn(OUTPUT_DIR, job_id, user_input, full_text, completed_tool_events or None)
            except Exception as e:
                logger.exception("Agent turn failed for job %s", job_id)
                _push(f"onAgentError({json.dumps(job_id)}, {json.dumps(str(e))})")

        threading.Thread(target=_run, daemon=True).start()
        return {"job_id": job_id, "status": "started"}

    def get_agent_session(self, job_id: str) -> dict:
        """Return agent chat session history. Returns {"job_id": ..., "turns": []} if none."""
        from src.agent_store import load_session
        return load_session(OUTPUT_DIR, job_id)

    def clear_agent_session(self, job_id: str) -> bool:
        """Delete agent chat session file. Returns True if deleted."""
        from src.agent_store import clear_session
        return clear_session(OUTPUT_DIR, job_id)

    # ── History ────────────────────────────────────────────────────────────────

    def get_history(self) -> list[dict]:
        """
        Return past transcription jobs and realtime recordings, newest first.

        Each entry: {job_id, filename, date, duration, language, audio_path, type}
          type = "file"     — transcription job (has a JSON transcript)
          type = "realtime" — realtime WAV recording without a transcript JSON
        """
        import glob
        import datetime as _dt

        result: list[dict] = []
        if not os.path.isdir(OUTPUT_DIR):
            return result

        seen_job_ids: set[str] = set()

        # ── New layout: meetings/*/transcript.json ─────────────────────────────
        meetings_dir = os.path.join(OUTPUT_DIR, "meetings")
        if os.path.isdir(meetings_dir):
            for job_id in os.listdir(meetings_dir):
                session_dir_path = os.path.join(meetings_dir, job_id)
                if not os.path.isdir(session_dir_path):
                    continue
                transcript_file = os.path.join(session_dir_path, "transcript.json")
                if os.path.exists(transcript_file):
                    try:
                        with open(transcript_file, encoding="utf-8") as f:
                            data = json.load(f)
                        seen_job_ids.add(job_id)
                        segs = data.get("segments", [])
                        duration = round(segs[-1]["end"]) if segs else 0
                        result.append({
                            "job_id": job_id,
                            "filename": data.get("filename") or data.get("audio") or job_id,
                            "date": data.get("created_at", ""),
                            "duration": duration,
                            "language": data.get("language", ""),
                            "audio_path": data.get("audio", ""),
                            "type": "file",
                        })
                    except Exception:
                        pass
                else:
                    # WAV-only realtime session dir
                    wav_file = _find_realtime_wav(session_dir_path)
                    if wav_file and os.path.exists(wav_file):
                        seen_job_ids.add(job_id)
                        meta_file = os.path.join(session_dir_path, "meta.json")
                        mtime = os.path.getmtime(wav_file)
                        date_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")
                        display_name = job_id[:8]
                        if os.path.exists(meta_file):
                            try:
                                with open(meta_file, encoding="utf-8") as f:
                                    meta = json.load(f)
                                if meta.get("transcribed_job_id"):
                                    continue  # already transcribed
                                display_name = meta.get("filename", display_name)
                            except Exception:
                                pass
                        result.append({
                            "job_id": job_id,
                            "filename": display_name,
                            "date": date_str,
                            "duration": 0,
                            "language": "",
                            "audio_path": os.path.abspath(wav_file),
                            "type": "realtime",
                        })

        # ── Legacy flat layout: *.json ─────────────────────────────────────────
        json_stems: set[str] = set()
        for path in glob.glob(os.path.join(OUTPUT_DIR, "*.json")):
            basename = os.path.basename(path)
            if basename.endswith("_summary.json") or basename.endswith("_meta.json"):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                stem = os.path.splitext(os.path.basename(path))[0]
                if stem in seen_job_ids:
                    continue
                json_stems.add(stem)
                seen_job_ids.add(stem)
                segs = data.get("segments", [])
                duration = round(segs[-1]["end"]) if segs else 0
                result.append({
                    "job_id": stem,
                    "filename": data.get("filename") or data.get("audio") or stem,
                    "date": data.get("created_at", ""),
                    "duration": duration,
                    "language": data.get("language", ""),
                    "audio_path": data.get("audio", ""),
                    "type": "file",
                })
            except Exception:
                pass

        # ── Legacy WAV-only realtime recordings ────────────────────────────────
        for wav_path in glob.glob(os.path.join(OUTPUT_DIR, "*.wav")):
            stem = os.path.splitext(os.path.basename(wav_path))[0]
            if stem.endswith("_audio"):
                continue  # internal audio copy
            if stem in seen_job_ids:
                continue
            mtime = os.path.getmtime(wav_path)
            date_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")
            display_name = stem[:8]
            meta_path = os.path.join(OUTPUT_DIR, f"{stem}_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    if meta.get("transcribed_job_id"):
                        continue  # already transcribed
                    display_name = meta.get("filename", display_name)
                except Exception:
                    pass
            seen_job_ids.add(stem)
            result.append({
                "job_id": stem,
                "filename": display_name,
                "date": date_str,
                "duration": 0,
                "language": "",
                "audio_path": os.path.abspath(wav_path),
                "type": "realtime",
            })

        # ── Interrupted sessions from checkpoints (F2) ─────────────────────────
        try:
            from src.checkpoint import find_interrupted as _find_interrupted
            interrupted = _find_interrupted(OUTPUT_DIR)
            for ckpt in interrupted:
                ckpt_job_id = ckpt.get("job_id", "")
                if not ckpt_job_id or ckpt_job_id in seen_job_ids:
                    continue
                seen_job_ids.add(ckpt_job_id)
                result.append({
                    "job_id": ckpt_job_id,
                    "filename": os.path.basename(ckpt.get("source_audio", ckpt_job_id)),
                    "date": ckpt.get("updated_at", ""),
                    "duration": 0,
                    "language": "",
                    "audio_path": ckpt.get("source_audio", ""),
                    "type": "file",
                    "status": "interrupted",
                })
        except Exception:
            pass

        result.sort(key=lambda x: x["date"], reverse=True)

        # One-time background Obsidian startup scan
        global _obsidian_startup_done
        if not _obsidian_startup_done:
            _obsidian_startup_done = True
            threading.Thread(target=self._obsidian_startup_scan, daemon=True).start()

        return result

    def get_summary_versions(self, job_id: str) -> list[dict]:
        """Return saved summary versions for a job (newest last)."""
        path = _summary_path(job_id)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save_summary_version(self, job_id: str, text: str) -> bool:
        """Append a new summary version; keep at most 3 per job."""
        import time as _time
        path = _summary_path(job_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        versions: list[dict] = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    versions = json.load(f)
            except Exception:
                versions = []
        versions.append({"text": text, "created_at": _time.strftime("%Y-%m-%d %H:%M")})
        versions = versions[-3:]  # keep max 3
        with open(path, "w", encoding="utf-8") as f:
            json.dump(versions, f, ensure_ascii=False, indent=2)
        threading.Thread(target=lambda: self._obsidian_auto_sync(job_id), daemon=True).start()
        return True

    def export_transcript(self, job_id: str, format: str) -> dict:
        """
        Export transcript to a user-chosen file via a native Save dialog.

        format: 'txt' or 'md'
        Returns {"status": "saved", "path": "..."} or {"status": "cancelled"}.
        """
        if _window is None:
            return {"status": "error", "error": "No window"}
        path = _transcript_path(job_id)
        if not path or not os.path.exists(path):
            return {"status": "error", "error": "Transcript not found"}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        fmt = (format or "txt").lower().strip()
        if fmt == "md":
            content      = _transcript_to_md(data.get("segments", []))
            default_name = "transcript.md"
            file_types   = ("Markdown (*.md)", "All files (*.*)")
        else:
            content      = _transcript_to_txt(data.get("segments", []))
            default_name = "transcript.txt"
            file_types   = ("Text Files (*.txt)", "All files (*.*)")

        import webview
        result = _window.create_file_dialog(
            dialog_type=webview.FileDialog.SAVE,
            save_filename=default_name,
            file_types=file_types,
        )
        if not result:
            return {"status": "cancelled"}

        save_path = result[0] if isinstance(result, (list, tuple)) else result
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "saved", "path": save_path}

    def export_summary(self, job_id: str, format: str) -> dict:
        """
        Export the latest summary version to a user-chosen file via a native Save dialog.

        format: 'txt' or 'md'
        Returns {"status": "saved", "path": "..."} or {"status": "cancelled"}.
        """
        if _window is None:
            return {"status": "error", "error": "No window"}
        versions = self.get_summary_versions(job_id)
        if not versions:
            return {"status": "error", "error": "No summary available"}

        text = versions[-1].get("text", "")
        fmt  = (format or "txt").lower().strip()
        if fmt == "md":
            default_name = "summary.md"
            file_types   = ("Markdown (*.md)", "All files (*.*)")
        else:
            default_name = "summary.txt"
            file_types   = ("Text Files (*.txt)", "All files (*.*)")

        import webview
        result = _window.create_file_dialog(
            dialog_type=webview.FileDialog.SAVE,
            save_filename=default_name,
            file_types=file_types,
        )
        if not result:
            return {"status": "cancelled"}

        save_path = result[0] if isinstance(result, (list, tuple)) else result
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(text)
        return {"status": "saved", "path": save_path}

    def rename_session(self, job_id: str, name: str) -> bool:
        """Rename a transcript session.

        For JSON-backed sessions: updates the filename field in the JSON.
        For WAV-only realtime sessions: writes meta.json inside session dir (new)
          or sidecar {job_id}_meta.json (legacy).
        """
        json_path = _transcript_path(job_id)
        if json_path and os.path.exists(json_path):
            lock = _get_transcript_lock(job_id)
            with lock:
                data = _read_json_file(json_path, {})
                # Capture old display name and date for Obsidian rename BEFORE write
                old_display = data.get("filename") or os.path.basename(data.get("audio", "")) or job_id
                old_date    = (data.get("created_at") or "")[:10]
                data["filename"] = name
                _write_json_atomic(json_path, data)
            threading.Thread(
                target=lambda: self._obsidian_rename(job_id, old_display, old_date, name),
                daemon=True,
            ).start()
            return True
        # WAV-only: new layout session dir or legacy flat WAV
        s_dir = _session_dir(job_id)
        if os.path.isdir(s_dir):
            meta_path = os.path.join(s_dir, "meta.json")
            meta = _read_json_file(meta_path, {})
            if not isinstance(meta, dict):
                meta = {}
            meta["filename"] = name
            _write_json_atomic(meta_path, meta)
            return True
        wav_path = os.path.join(OUTPUT_DIR, f"{job_id}.wav")
        if os.path.exists(wav_path):
            meta_path = os.path.join(OUTPUT_DIR, f"{job_id}_meta.json")
            meta = _read_json_file(meta_path, {})
            if not isinstance(meta, dict):
                meta = {}
            meta["filename"] = name
            _write_json_atomic(meta_path, meta)
            return True
        return False

    def delete_session(self, job_id: str) -> bool:
        """Delete a transcript session and all associated files."""
        # F6: new layout — rmtree the session directory
        s_dir = _session_dir(job_id)
        if os.path.isdir(s_dir):
            shutil.rmtree(s_dir)
            return True
        # Legacy: per-file deletion
        candidates = [
            os.path.join(OUTPUT_DIR, f"{job_id}.json"),
            os.path.join(OUTPUT_DIR, f"{job_id}.wav"),
            os.path.join(OUTPUT_DIR, f"{job_id}.md"),
            os.path.join(OUTPUT_DIR, f"{job_id}_summary.json"),
            os.path.join(OUTPUT_DIR, f"{job_id}_meta.json"),
            os.path.join(OUTPUT_DIR, f"{job_id}_agent_chat.json"),
        ] + [os.path.join(OUTPUT_DIR, f"{job_id}_audio{ext}") for ext in _AUDIO_EXTS]
        deleted = False
        for p in candidates:
            if os.path.exists(p):
                os.remove(p)
                deleted = True
        return deleted

    # ── Model management ───────────────────────────────────────────────────────

    def get_setup_status(self) -> dict:
        """
        Return download readiness for the two required model families.

        Returns: {
            "asr_ready":      bool,  # any recommended ASR model downloaded
            "diarizer_ready": bool,  # any recommended diarizer downloaded
        }
        """
        from src.model_manager import ModelManager, CATALOG
        mm = ModelManager()
        asr_ready = any(
            mm.is_downloaded(m.id) for m in CATALOG if m.role == "asr" and m.recommended
        )
        diarizer_ready = any(
            mm.is_downloaded(m.id) for m in CATALOG if m.role == "diarizer" and m.recommended
        )
        return {"asr_ready": asr_ready, "diarizer_ready": diarizer_ready}

    def get_models(self) -> list[dict]:
        """Return model catalog with download status."""
        from src.model_manager import ModelManager
        return ModelManager().list_models()

    def delete_model(self, model_id: str) -> dict:
        """
        Delete the app-managed copy of a model from disk (HF cache untouched).

        Returns: {"status": "ok", "downloaded": bool} or {"status": "not_found"}
        """
        from src.model_manager import ModelManager
        mm = ModelManager()
        if mm.get_model(model_id) is None:
            return {"status": "not_found"}
        mm.delete(model_id)
        return {"status": "ok", "downloaded": mm.is_downloaded(model_id)}

    def download_model(self, name: str) -> dict:
        """
        Download a model in background.
        Pushes onModelDownloadProgress(name, percent) events to JS.

        Returns: {"status": "started"}
        """
        def _run():
            try:
                from src.model_manager import ModelManager

                def _progress(pct: float, msg: str):
                    js = f"onModelDownloadProgress({json.dumps(name)}, {pct:.4f})"
                    _push(js)

                ModelManager().download(name, progress_callback=_progress)
            except Exception as e:
                logger.exception(f"Model download failed: {name}")
                js = f"onModelDownloadError({json.dumps(name)}, {json.dumps(str(e))})"
                _push(js)

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "started"}

    # ── Config ─────────────────────────────────────────────────────────────────

    def save_api_config(self, config: dict) -> bool:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        cfg = self._load_config()
        cfg["api"] = config
        self._save_config(cfg)
        return True

    def get_api_config(self) -> dict:
        return self._load_config().get("api", {})

    # ── Storage config ──────────────────────────────────────────────────────────

    def get_storage_config(self) -> dict:
        """Return current storage paths."""
        return {"output_dir": OUTPUT_DIR, "default_output_dir": _DEFAULT_OUTPUT_DIR}

    def set_output_dir(self, path: str) -> dict:
        """
        Persist a new OUTPUT_DIR.  Effective immediately for new operations.

        Returns {"ok": True, "path": path} on success,
                {"ok": False, "error": "..."} on failure.
        """
        global OUTPUT_DIR
        path = (path or "").strip()
        if not path:
            return {"ok": False, "error": "Path is empty"}
        if not os.path.isdir(path):
            return {"ok": False, "error": f"Directory not found: {path}"}
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        cfg = self._load_config()
        cfg.setdefault("storage", {})["output_dir"] = path
        self._save_config(cfg)
        OUTPUT_DIR = path
        return {"ok": True, "path": path}

    def select_output_folder(self) -> Optional[str]:
        """Open a native folder picker and return the chosen path (or None)."""
        if _window is None:
            return None
        import webview
        result = _window.create_file_dialog(dialog_type=webview.FileDialog.FOLDER)
        return result[0] if result else None

    # ── Obsidian sync ───────────────────────────────────────────────────────────

    def get_obsidian_config(self) -> dict:
        """Return current Obsidian sync config: {folder, enabled}."""
        obs = self._load_config().get("obsidian", {})
        return {"folder": obs.get("folder", ""), "enabled": bool(obs.get("enabled", False))}

    def set_obsidian_config(self, folder: str, enabled: bool) -> bool:
        """Save Obsidian sync settings to config.json."""
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        cfg = self._load_config()
        cfg["obsidian"] = {"folder": (folder or "").strip(), "enabled": bool(enabled)}
        self._save_config(cfg)
        return True

    def select_obsidian_folder(self) -> Optional[str]:
        """Open a native folder picker and return the chosen path (or None)."""
        if _window is None:
            return None
        import webview
        result = _window.create_file_dialog(dialog_type=webview.FileDialog.FOLDER)
        return result[0] if result else None

    def sync_to_obsidian(self, job_id: str) -> dict:
        """
        Sync a single session to the configured Obsidian folder.

        Returns {"status": "ok", "path": "..."}, {"status": "disabled"},
                or {"status": "error", "error": "..."}.
        """
        obs = self._load_config().get("obsidian", {})
        if not obs.get("enabled") or not obs.get("folder"):
            return {"status": "disabled"}
        folder = obs["folder"]
        if not os.path.isdir(folder):
            return {"status": "error", "error": f"Folder not found: {folder}"}
        from src.obsidian import sync_job
        try:
            path = sync_job(job_id, OUTPUT_DIR, folder)
            return {"status": "ok", "path": path}
        except Exception as e:
            logger.warning("sync_to_obsidian failed for job %s: %s", job_id, e)
            return {"status": "error", "error": str(e)}

    # ── Obsidian internal helpers ───────────────────────────────────────────────

    def _obsidian_auto_sync(self, job_id: str) -> None:
        """Silently sync a job after transcript/summary save."""
        obs = self._load_config().get("obsidian", {})
        if not obs.get("enabled") or not obs.get("folder"):
            return
        folder = obs["folder"]
        if not os.path.isdir(folder):
            return
        from src.obsidian import sync_job
        try:
            sync_job(job_id, OUTPUT_DIR, folder)
        except Exception:
            logger.warning("Obsidian auto-sync failed for job %s", job_id, exc_info=True)

    def _obsidian_rename(
        self, job_id: str, old_display: str, old_date: str, new_display: str
    ) -> None:
        """Rename the Obsidian MD file when a session is renamed."""
        obs = self._load_config().get("obsidian", {})
        if not obs.get("enabled") or not obs.get("folder"):
            return
        folder = obs["folder"]
        if not os.path.isdir(folder):
            return
        from src.obsidian import obsidian_filename, sync_job
        import time as _t
        date = old_date or _t.strftime("%Y-%m-%d")
        old_name = obsidian_filename(date, old_display)
        new_name = obsidian_filename(date, new_display)
        old_path = os.path.join(folder, old_name)
        new_path = os.path.join(folder, new_name)
        try:
            if os.path.exists(old_path) and old_path != new_path:
                os.rename(old_path, new_path)
            elif not os.path.exists(new_path):
                sync_job(job_id, OUTPUT_DIR, folder)
        except Exception:
            logger.warning("Obsidian rename failed for job %s", job_id, exc_info=True)

    def _obsidian_startup_scan(self) -> None:
        """Background startup scan — sync all sessions missing an Obsidian file."""
        obs = self._load_config().get("obsidian", {})
        if not obs.get("enabled") or not obs.get("folder"):
            return
        folder = obs["folder"]
        if not os.path.isdir(folder):
            return
        from src.obsidian import scan_and_sync
        try:
            synced = scan_and_sync(OUTPUT_DIR, folder)
            if synced:
                logger.info("Obsidian startup sync: wrote %d file(s)", len(synced))
        except Exception:
            logger.warning("Obsidian startup scan failed", exc_info=True)

    def save_summary_templates(self, templates: list[dict]) -> bool:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        return True

    def get_summary_templates(self) -> list[dict]:
        if not os.path.exists(TEMPLATES_PATH):
            return list(_DEFAULT_TEMPLATES)
        try:
            with open(TEMPLATES_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.warning("templates.json is corrupted; resetting to defaults")
            self.save_summary_templates(list(_DEFAULT_TEMPLATES))
            return list(_DEFAULT_TEMPLATES)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_config(self, cfg: dict):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
