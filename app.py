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
import weakref
import datetime as dt
from typing import Optional

from platformdirs import user_data_dir
from src.service import AudioAssistService, language_instruction
from src.session_paths import get_session_paths, resolve_transcript_path

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
_SCREEN_PERMISSION_REQUEST_LOCK = threading.Lock()
_SCREEN_PERMISSION_REQUEST: Optional[dict] = None


def _macos_microphone_permission_status() -> str:
    """Return the current TCC microphone status without prompting.

    PyWebView already bundles PyObjC, but the optional generated AVFoundation
    wrapper is not part of the application environment. Load the system
    framework dynamically and use the stable AVCaptureDevice class API.
    """
    if sys.platform != "darwin":
        return "not_applicable"
    try:
        import objc

        objc.loadBundle(
            "AVFoundation",
            globals(),
            bundle_path="/System/Library/Frameworks/AVFoundation.framework",
        )
        capture_device = objc.lookUpClass("AVCaptureDevice")
        status = int(capture_device.authorizationStatusForMediaType_("soun"))
    except Exception:
        logger.warning("Unable to query macOS microphone permission", exc_info=True)
        return "unknown"
    return {
        0: "not_determined",
        1: "restricted",
        2: "denied",
        3: "authorized",
    }.get(status, "unknown")


def _microphone_permission_request_method():
    """Load the native request with the block ABI missing from loadBundle alone."""
    import objc

    objc.loadBundle(
        "AVFoundation", globals(),
        bundle_path="/System/Library/Frameworks/AVFoundation.framework",
    )
    # Objective-C encodes the parameter as @? but not its void (^)(BOOL)
    # signature. Without framework metadata PyObjC rejects a Python callback
    # before sending any request to TCC. Index 0 is the implicit block pointer.
    objc.registerMetaDataForSelector(
        b"AVCaptureDevice", b"requestAccessForMediaType:completionHandler:",
        {"arguments": {3: {"callable": {
            "retval": {"type": b"v"},
            "arguments": {0: {"type": b"^v"}, 1: {"type": objc._C_NSBOOL}},
        }}}},
    )
    return objc.lookUpClass("AVCaptureDevice").requestAccessForMediaType_completionHandler_


def _request_macos_microphone_permission(timeout_seconds: float = 60.0) -> str:
    """Request microphone access before PortAudio can block inside CoreAudio."""
    status = _macos_microphone_permission_status()
    if status != "not_determined":
        return status
    try:
        request = _microphone_permission_request_method()
        completed = threading.Event()
        result = {"status": "denied"}

        def _on_permission(granted: bool) -> None:
            result["status"] = "authorized" if granted else "denied"
            completed.set()

        request("soun", _on_permission)
        if not completed.wait(timeout_seconds):
            return "timeout"
        return result["status"]
    except Exception:
        logger.warning(
            "Unable to request macOS microphone permission", exc_info=True,
            extra={"audioassist_diagnostic": True},
        )
        return "unknown"


def _load_core_graphics():
    """Load the system CoreGraphics framework without a PyObjC package dependency."""
    import ctypes
    import ctypes.util

    path = ctypes.util.find_library("CoreGraphics")
    if not path:
        raise RuntimeError("CoreGraphics framework not found")
    return ctypes.CDLL(path)


def _macos_screen_recording_permission_status() -> str:
    """Return whether Screen & System Audio Recording access is available.

    CoreGraphics exposes only a boolean preflight, so a false result cannot
    distinguish first-use from a previous denial. ``required`` deliberately
    means that the GUI should make one bounded request and then offer Settings.
    """
    if sys.platform != "darwin":
        return "not_applicable"
    try:
        import ctypes

        preflight = _load_core_graphics().CGPreflightScreenCaptureAccess
        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        return "authorized" if preflight() else "required"
    except Exception:
        logger.warning("Unable to query macOS screen recording permission", exc_info=True)
        return "unknown"


def _request_macos_screen_recording_permission(timeout_seconds: float = 60.0) -> str:
    """Request Screen & System Audio Recording access before model loading.

    Core Graphics exposes a synchronous, non-cancelable request. Keep it
    process-wide single-flight so a UI cancel/timeout followed by retry cannot
    leak concurrent native permission calls.
    """
    global _SCREEN_PERMISSION_REQUEST

    status = _macos_screen_recording_permission_status()
    if status in {"authorized", "not_applicable"}:
        return status
    try:
        import ctypes

        with _SCREEN_PERMISSION_REQUEST_LOCK:
            active = _SCREEN_PERMISSION_REQUEST
            if active is not None and not active["completed"].is_set():
                return "in_progress"

            request = _load_core_graphics().CGRequestScreenCaptureAccess
            request.argtypes = []
            request.restype = ctypes.c_bool
            state = {
                "completed": threading.Event(),
                "status": "unknown",
            }
            _SCREEN_PERMISSION_REQUEST = state

            def _request() -> None:
                try:
                    state["status"] = "authorized" if request() else "required"
                except Exception:
                    logger.warning(
                        "Unable to request macOS screen recording permission",
                        exc_info=True,
                    )
                finally:
                    state["completed"].set()

            threading.Thread(
                target=_request,
                daemon=True,
                name="screen-recording-permission",
            ).start()
        if not state["completed"].wait(timeout_seconds):
            return "timeout"
        return state["status"]
    except Exception:
        logger.warning("Unable to request macOS screen recording permission", exc_info=True)
        return "unknown"


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


def _service() -> AudioAssistService:
    """Build a service using the currently configured (and test-patchable) paths."""
    return AudioAssistService(OUTPUT_DIR, CONFIG_PATH, TEMPLATES_PATH)


# ── Session-per-directory helpers (F6) ─────────────────────────────────────────

def _session_dir(job_id: str) -> str:
    """Return the per-session directory path under OUTPUT_DIR/meetings/."""
    return get_session_paths(OUTPUT_DIR, job_id).session_dir


def _transcript_path(job_id: str) -> Optional[str]:
    """Return transcript.json path for job_id using new or legacy layout."""
    return resolve_transcript_path(OUTPUT_DIR, job_id)


def _realtime_wav_name(session_id: str) -> str:
    """Return a collision-resistant realtime WAV filename for this session."""
    return f"{_REALTIME_WAV_PREFIX}_{session_id[:8]}.wav"


def _lang_instruction(language: str, text: str) -> str:
    """Backward-compatible adapter for the shared summary-language helper."""
    return language_instruction(language, text)


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
        lock = _transcript_locks.get(job_id)
        if lock is None:
            # Keep the new lock strongly referenced in this stack frame until
            # it has been returned to the caller; the registry itself is weak.
            lock = threading.Lock()
            _transcript_locks[job_id] = lock
        return lock


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


def _register_transcription(job_id: str) -> Optional[threading.Event]:
    """Register one active operation per job, returning None on conflict."""
    with _cancel_flags_mutex:
        if job_id in _cancel_flags:
            return None
        event = threading.Event()
        _cancel_flags[job_id] = event
        return event


def _unregister_transcription(job_id: str, event: threading.Event) -> None:
    """Remove an operation only when the caller still owns its registration."""
    with _cancel_flags_mutex:
        if _cancel_flags.get(job_id) is event:
            _cancel_flags.pop(job_id, None)

# Guard so the Obsidian startup scan runs only once per process
_obsidian_startup_done = False


class _TranscriptionCancelled(Exception):
    """Raised inside transcribe() thread when the job is cancelled."""


# Per-job lock to prevent concurrent read-modify-write on transcript files.
# Weak values retain a lock while any owner/waiter holds a strong reference and
# evict it automatically once the operation finishes, bounding long-lived apps.
_transcript_locks: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
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


class _RealtimeStartupLease:
    """Cancelable placeholder published before a realtime backend is constructed."""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.backend_ready = threading.Event()
        self._lock = threading.Lock()
        self._backend = None

    def publish_backend(self, backend) -> None:
        with self._lock:
            self._backend = backend
        self.backend_ready.set()

    def publish_failure(self) -> None:
        self.backend_ready.set()

    def cancel_and_stop(self) -> None:
        self.cancelled.set()
        self.backend_ready.wait()
        with self._lock:
            backend = self._backend
        if backend is not None and hasattr(backend, "stop"):
            backend.stop()

    def wait_for_worker_exit(self, timeout: float | None = None) -> None:
        with self._lock:
            backend = self._backend
        if backend is not None and hasattr(backend, "wait_for_worker_exit"):
            backend.wait_for_worker_exit(timeout=timeout)


class API:
    def __init__(self):
        self._realtime = None  # RealtimeTranscriber instance when active
        self._recording_lock = threading.RLock()
        self._recording_generation = 0
        self._recording_status = {
            "state": "idle",
            "session_id": None,
            "capture_mode": None,
            "started_at": None,
            "error": None,
        }
        # Pre-collected realtime segments keyed by wav_path — consumed by transcribe()
        self._rt_segments: dict[str, list] = {}
        # Options stored after Phase 1 (realtime), consumed by manual refine()
        self._refine_options: dict = {}
        self._caffeinate_proc = None  # caffeinate subprocess (macOS only)

    def _set_recording_status(self, state: str, **changes) -> None:
        with self._recording_lock:
            self._recording_status.update(changes)
            self._recording_status["state"] = state

    def get_recording_status(self) -> dict:
        """Return a stable snapshot for the local HTTP and MCP adapters."""
        with self._recording_lock:
            return dict(self._recording_status)

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
            "qwen_backend": ckpt.get("qwen_backend"),
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

        # Resolve ASR engine/model before spawning thread so both the main
        # transcription and the optional background refine use the same choice.
        model_id = options.get("model_id")
        engine   = options.get("engine", "qwen")
        if model_id:
            from src.model_manager import CATALOG as _CATALOG
            _info = next((m for m in _CATALOG if m.id == model_id), None)
            if _info:
                engine = "whisper" if _info.engine == "mlx-whisper" else _info.engine

        # Register before starting the thread so an immediate cancel cannot
        # miss the job. A job id owns at most one transcription/refine worker.
        from src.preferences import default_backend
        qwen_backend = (
            options.get("qwen_backend") or default_backend(CONFIG_PATH)
            if engine == "qwen"
            else None
        )
        cancel_ev = _register_transcription(job_id)
        if cancel_ev is None:
            return {"error": "job_in_progress", "job_id": job_id}

        # Pop realtime segments only after registration succeeds. A rejected
        # duplicate must not consume the pending realtime result.
        rt_segments = self._rt_segments.pop(path, None)

        def _run():
            try:
                def _progress(pct: float, msg: str):
                    if cancel_ev.is_set():
                        raise _TranscriptionCancelled()
                    js = f"onTranscribeProgress({json.dumps(job_id)}, {pct:.4f}, {json.dumps(msg)})"
                    _push(js)

                hf_token = options.get("hf_token") or os.environ.get("HF_TOKEN")
                num_speakers = options.get("num_speakers")
                lock = _get_transcript_lock(job_id)
                with lock:
                    if cancel_ev.is_set():
                        raise _TranscriptionCancelled()
                    result = _service().transcribe(
                        path,
                        engine=engine,
                        model_id=model_id,
                        hf_token=hf_token,
                        num_speakers=num_speakers,
                        job_id=job_id,
                        realtime_segments=rt_segments,
                        progress_callback=_progress,
                        qwen_backend=qwen_backend,
                    )
                    s_dir = _session_dir(job_id)
                    json_path = result["json_path"]
                    audio_copy = result.get("audio_copy") or None

                    # Store options for optional manual high-accuracy refine (realtime only).
                    if rt_segments:
                        self._refine_options[job_id] = {
                            "path": path, "s_dir": s_dir, "engine": engine,
                            "model_id": model_id, "hf_token": hf_token,
                            "num_speakers": num_speakers, "audio_copy": audio_copy,
                            "json_path": json_path,
                        }

                # Stop accepting cancellation once the transactional service
                # has committed the completed session.
                _unregister_transcription(job_id, cancel_ev)

                # Notify frontend — hasRefine is always False; manual refine via refine().
                has_refine = False
                _push(
                    f"onTranscribeComplete({json.dumps(job_id)}, "
                    f"{json.dumps(json_path)}, {json.dumps(has_refine)})"
                )

            except _TranscriptionCancelled:
                logger.info("Transcription cancelled: %s", job_id)
                _push(f"onTranscribeCancel({json.dumps(job_id)})")
            except Exception as e:
                logger.exception(
                    "Transcription failed (engine=%s, qwen_backend=%s)",
                    engine,
                    qwen_backend,
                )
                # Import here to avoid circular import issues at module load
                try:
                    from src.pipeline import ModelNotReadyError as _MNR
                    if isinstance(e, _MNR):
                        pass  # fall through to generic error push below
                except ImportError:
                    pass
                actual_backend = qwen_backend
                if qwen_backend:
                    try:
                        from src.checkpoint import read as _read_checkpoint

                        failed_checkpoint = _read_checkpoint(_session_dir(job_id)) or {}
                        actual_backend = failed_checkpoint.get("qwen_backend") or qwen_backend
                    except Exception:
                        logger.warning(
                            "Failed to read backend from interrupted checkpoint",
                            exc_info=True,
                        )
                js = (
                    f"onTranscribeError({json.dumps(job_id)}, {json.dumps(str(e))}, "
                    f"{json.dumps(actual_backend)})"
                )
                _push(js)
            finally:
                _unregister_transcription(job_id, cancel_ev)

        threading.Thread(target=_run, daemon=True).start()
        return {"job_id": job_id, "qwen_backend": qwen_backend}

    def refine(self, job_id: str) -> dict:
        """
        Trigger manual high-accuracy re-transcription for a realtime session (Phase 2).
        Called from the UI when the user clicks "高精度转写".

        Pops the stored options from _refine_options (so duplicate calls are no-ops).
        Returns {"job_id": str} immediately; progress is pushed via onTranscribeProgress.
        Pushes onTranscribeRefined(jobId) on completion (success or error).
        """
        with _cancel_flags_mutex:
            if job_id in _cancel_flags:
                return {"error": "job_in_progress", "job_id": job_id}

        opts = self._refine_options.get(job_id)
        if opts is None:
            return {"error": "no_refine_options"}

        from src.preferences import default_backend
        qwen_backend = default_backend(CONFIG_PATH) if opts["engine"] == "qwen" else None

        cancel_ev = _register_transcription(job_id)
        if cancel_ev is None:
            return {"error": "job_in_progress", "job_id": job_id}
        self._refine_options.pop(job_id, None)

        path         = opts["path"]
        engine       = opts["engine"]
        model_id     = opts["model_id"]
        hf_token     = opts["hf_token"]
        num_speakers = opts["num_speakers"]

        def _run():
            try:
                def _progress(pct: float, msg: str):
                    if cancel_ev.is_set():
                        raise _TranscriptionCancelled()
                    js = f"onTranscribeProgress({json.dumps(job_id)}, {pct:.4f}, {json.dumps(msg)})"
                    _push(js)

                logger.info("Refine ASR started for job %s", job_id)
                lock = _get_transcript_lock(job_id)
                with lock:
                    if cancel_ev.is_set():
                        raise _TranscriptionCancelled()
                    _service().transcribe(
                        path,
                        engine=engine,
                        model_id=model_id,
                        hf_token=hf_token,
                        num_speakers=num_speakers,
                        job_id=job_id,
                        progress_callback=_progress,
                        qwen_backend=qwen_backend,
                    )

                _unregister_transcription(job_id, cancel_ev)
                _push(f"onTranscribeRefined({json.dumps(job_id)})")
                logger.info("Refine ASR complete for job %s", job_id)
            except _TranscriptionCancelled:
                logger.info("Refine ASR cancelled for job %s", job_id)
                self._refine_options[job_id] = opts
                _push(f"onTranscribeCancel({json.dumps(job_id)})")
            except Exception:
                logger.exception("Refine ASR failed for job %s", job_id)
                _push(f"onTranscribeRefined({json.dumps(job_id)})")
            finally:
                _unregister_transcription(job_id, cancel_ev)

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
        return _service().get_transcript(job_id)

    def save_transcript(self, job_id: str, edits: list[dict]) -> bool:
        """
        Persist edited transcript segments.

        edits: list of {"speaker", "start", "end", "text", "words"} dicts.
        Uses a per-job lock and atomic rename to prevent data corruption from
        concurrent saves or a mid-write crash.
        """
        path = _transcript_path(job_id)
        lock = _get_transcript_lock(job_id)

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
        lock = _get_transcript_lock(job_id)

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
        lock = _get_transcript_lock(job_id)

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

        Permission requirements are mode-specific: Mic needs microphone only,
        System needs Screen & System Audio Recording only, and Mix needs both.
        This method never triggers a system prompt.
        """
        import platform

        os_version = platform.mac_ver()[0]
        result: dict = {
            "supported":           True,
            "missing_permissions": [],
            "os_version":          os_version,
            "reason":              None,
        }

        if mode in ("mic", "mix"):
            permission_status = _macos_microphone_permission_status()
            result["microphone_permission"] = permission_status
            if permission_status not in {"authorized", "not_applicable"}:
                result["missing_permissions"].append("microphone")
            if mode == "mic" and permission_status in {"denied", "restricted", "unknown"}:
                result["supported"] = False
                result["reason"] = f"microphone_permission_{permission_status}"

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

            screen_status = _macos_screen_recording_permission_status()
            result["screen_recording_permission"] = screen_status
            if screen_status not in {"authorized", "not_applicable"}:
                result["missing_permissions"].append("screen_recording")

        if result["missing_permissions"] and result["reason"] is None:
            if len(result["missing_permissions"]) > 1:
                result["reason"] = "capture_permissions_required"
            elif result["missing_permissions"][0] == "microphone":
                result["reason"] = (
                    f"microphone_permission_{result.get('microphone_permission', 'unknown')}"
                )
            else:
                result["reason"] = (
                    f"screen_recording_permission_"
                    f"{result.get('screen_recording_permission', 'unknown')}"
                )

        return result

    def request_microphone_permission(self) -> dict:
        """Prompt for microphone access with a bounded wait before model loading."""
        status = _request_macos_microphone_permission()
        return {"status": status, "authorized": status == "authorized"}

    def request_screen_recording_permission(self) -> dict:
        """Prompt for Screen & System Audio Recording before model loading."""
        status = _request_macos_screen_recording_permission()
        return {"status": status, "authorized": status == "authorized"}

    def open_privacy_settings(self, permission: str = "screen") -> dict:
        """Open the requested bounded macOS Privacy & Security pane."""
        pane = "Privacy_Microphone" if permission == "microphone" else "Privacy_ScreenCapture"
        try:
            subprocess.Popen([
                "open",
                f"x-apple.systempreferences:com.apple.preference.security?{pane}",
            ])
            return {"status": "ok"}
        except Exception as e:
            logger.warning("open_privacy_settings failed: %s", e)
            return {"status": "error", "message": str(e)}

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
        options = options or {}
        capture_mode = options.get("capture_mode", "mic")
        model_id = options.get("model_id")
        engine = options.get("engine", "qwen")
        if model_id:
            from src.model_manager import CATALOG
            info = next((m for m in CATALOG if m.id == model_id), None)
            if info:
                engine = "whisper" if info.engine == "mlx-whisper" else info.engine
        from src.preferences import default_backend
        qwen_backend = default_backend(CONFIG_PATH) if engine == "qwen" else None
        with self._recording_lock:
            if self._realtime is not None:
                return {"status": "already_running"}
            missing_permissions: list[str] = []
            permission_result: dict = {}
            if capture_mode in {"mic", "mix"}:
                permission_status = _macos_microphone_permission_status()
                permission_result["microphone_permission"] = permission_status
                if permission_status not in {"authorized", "not_applicable"}:
                    missing_permissions.append("microphone")
            if capture_mode in {"system", "mix"}:
                screen_status = _macos_screen_recording_permission_status()
                permission_result["screen_recording_permission"] = screen_status
                if screen_status not in {"authorized", "not_applicable"}:
                    missing_permissions.append("screen_recording")
            if missing_permissions:
                return {
                    "status": "permission_required",
                    "missing_permissions": missing_permissions,
                    **permission_result,
                }
            # Keep a concrete, cancelable lease from construction through model load.
            lease = _RealtimeStartupLease()
            self._realtime = lease
            self._recording_generation += 1
            generation = self._recording_generation
        self._caffeinate_start()

        self._set_recording_status(
            "starting",
            session_id=None,
            capture_mode=capture_mode,
            started_at=None,
            error=None,
        )

        def _run():
            try:
                import uuid
                session_id = str(uuid.uuid4())
                # F6: realtime WAV goes into session dir
                rt_session_dir = _session_dir(session_id)
                os.makedirs(rt_session_dir, exist_ok=True)
                output_path = os.path.join(rt_session_dir, _realtime_wav_name(session_id))
                logger.info(
                    "Realtime start attempt=%s stage=backend_create.begin mode=%s engine=%s model=%s qwen_backend=%s",
                    session_id,
                    capture_mode,
                    engine,
                    model_id or "default",
                    qwen_backend or "not-applicable",
                    extra={"audioassist_diagnostic": True},
                )

                if capture_mode in ("system", "mix"):
                    from src.native_capture import NativeCaptureHelper
                    rt = NativeCaptureHelper(
                        mode=capture_mode,
                        engine=engine,
                        qwen_backend=qwen_backend,
                        model_id=model_id,
                        output_path=output_path,
                        on_result=lambda seg: _push(f"onRealtimeResult({json.dumps(seg)})"),
                        on_error=lambda msg:  _push(f"onRealtimeError({json.dumps(msg)})"),
                    )
                else:
                    # Default: mic mode — existing sounddevice path unchanged
                    from src.realtime import RealtimeTranscriber
                    rt = RealtimeTranscriber(
                        engine=engine,
                        qwen_backend=qwen_backend,
                        model_id=model_id,
                        output_path=output_path,
                        diagnostic_id=session_id,
                        on_result=lambda seg: _push(f"onRealtimeResult({json.dumps(seg)})"),
                        on_error=lambda msg:  _push(f"onRealtimeError({json.dumps(msg)})"),
                    )
                lease.publish_backend(rt)
                logger.info(
                    "Realtime start attempt=%s stage=backend_create.done backend=%s",
                    session_id,
                    type(rt).__name__,
                    extra={"audioassist_diagnostic": True},
                )
                if lease.cancelled.is_set():
                    return
                rt.start()
                with self._recording_lock:
                    cancelled = (
                        lease.cancelled.is_set()
                        or self._realtime is not lease
                        or generation != self._recording_generation
                    )
                    if not cancelled:
                        self._realtime = rt
                if cancelled:
                    return
                self._set_recording_status(
                    "recording",
                    session_id=session_id,
                    capture_mode=capture_mode,
                    started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    error=None,
                )
                logger.info(
                    "Realtime start attempt=%s stage=recording.confirmed",
                    session_id,
                    extra={"audioassist_diagnostic": True},
                )
                _push(f"onRealtimeStarted({json.dumps(session_id)}, {json.dumps(output_path)})")
            except Exception as e:
                logger.info(
                    "Realtime start attempt=%s stage=failed error_type=%s",
                    locals().get("session_id", "unassigned"),
                    type(e).__name__,
                    extra={"audioassist_diagnostic": True},
                )
                logger.exception("Realtime start failed")
                lease.publish_failure()
                with self._recording_lock:
                    cancelled = (
                        lease.cancelled.is_set()
                        or generation != self._recording_generation
                    )
                    if cancelled:
                        return
                # A timed-out load can leave the owner worker finishing late.
                # Hold the lease until cleanup closes that backend.
                try:
                    rt = locals().get("rt")
                    if rt is not None and hasattr(rt, "stop"):
                        rt.stop()
                except Exception as cleanup_error:
                    if (
                        type(cleanup_error).__name__ == "RealtimeWorkerStillStopping"
                        and hasattr(rt, "wait_for_worker_exit")
                    ):
                        rt.wait_for_worker_exit()
                    else:
                        logger.exception("Realtime failed-start cleanup failed")
                with self._recording_lock:
                    if self._realtime is lease:
                        self._realtime = None
                self._set_recording_status("error", error=str(e))
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
        self._set_recording_status("pausing", error=None)

        def _run():
            try:
                rt.pause()
            except Exception as error:
                logger.exception("Realtime pause failed")
                self._set_recording_status("error", error=str(error))
                _push(f"onRealtimeError({json.dumps(str(error))})")
            else:
                self._set_recording_status("paused", error=None)
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
        self._set_recording_status("resuming", error=None)

        def _run():
            try:
                rt.resume()
            except Exception as error:
                logger.exception("Realtime resume failed")
                self._set_recording_status("error", error=str(error))
                _push(f"onRealtimeError({json.dumps(str(error))})")
            else:
                self._set_recording_status("recording", error=None)
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
        with self._recording_lock:
            rt = self._realtime
            if rt is None:
                return {"status": "not_running"}
            self._recording_generation += 1
        self._caffeinate_stop()
        self._set_recording_status("stopping", error=None)

        def _run():
            try:
                if isinstance(rt, _RealtimeStartupLease):
                    rt.cancel_and_stop()
                elif hasattr(rt, "stop"):
                    rt.stop()
                # After stop(), the worker queue is fully drained — segments complete.
                wav_path = getattr(rt, "_output_path", None)
                if wav_path and hasattr(rt, "get_segments"):
                    segs = rt.get_segments()
                    if segs:
                        self._rt_segments[wav_path] = segs
            except Exception as error:
                if (
                    type(error).__name__ == "RealtimeWorkerStillStopping"
                    and hasattr(rt, "wait_for_worker_exit")
                ):
                    logger.warning(
                        "Realtime backend still draining; retaining session lease"
                    )
                    try:
                        rt.wait_for_worker_exit()
                    except Exception as wait_error:
                        logger.exception("Realtime worker shutdown failed")
                        self._set_recording_status("error", error=str(wait_error))
                        _push(f"onRealtimeError({json.dumps(str(wait_error))})")
                        return
                    else:
                        self._collect_realtime_segments(rt)
                        self._finish_realtime_stop(rt)
                else:
                    logger.exception("Realtime stop failed")
                    with self._recording_lock:
                        if self._realtime is rt:
                            self._realtime = None
                    self._set_recording_status("error", error=str(error))
                    _push(f"onRealtimeError({json.dumps(str(error))})")
            else:
                self._finish_realtime_stop(rt)

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "stopped"}

    def _collect_realtime_segments(self, rt) -> None:
        wav_path = getattr(rt, "_output_path", None)
        if wav_path and hasattr(rt, "get_segments"):
            segs = rt.get_segments()
            if segs:
                self._rt_segments[wav_path] = segs

    def _finish_realtime_stop(self, rt) -> None:
        """Release the global lease only after its owner worker has exited."""
        with self._recording_lock:
            if self._realtime is rt:
                self._realtime = None
        self._set_recording_status(
            "idle",
            session_id=None,
            capture_mode=None,
            started_at=None,
            error=None,
        )
        _push("onRealtimeStopped()")

    def shutdown(self) -> None:
        """Synchronously release capture and sleep-prevention resources on app exit."""
        with self._recording_lock:
            rt = self._realtime
            self._realtime = None
            self._recording_generation += 1
        self._caffeinate_stop()
        if rt is not None and hasattr(rt, "stop"):
            try:
                rt.stop()
            except Exception:
                logger.exception("Realtime shutdown failed")
        self._set_recording_status(
            "idle",
            session_id=None,
            capture_mode=None,
            started_at=None,
            error=None,
        )

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
                data = _service().get_transcript(job_id)
                if data is None:
                    _push(f"onSummaryError({json.dumps(job_id)}, {json.dumps('Transcript not found')})")
                    return
                prompt   = template.get("prompt", "Summarize the following transcript.")
                chunks = _service().summarize(job_id, prompt, stream=True)

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
        result = _service().list_sessions()

        # One-time background Obsidian startup scan
        global _obsidian_startup_done
        if not _obsidian_startup_done:
            _obsidian_startup_done = True
            threading.Thread(target=self._obsidian_startup_scan, daemon=True).start()

        return result

    def get_summary_versions(self, job_id: str) -> list[dict]:
        """Return saved summary versions for a job (newest last)."""
        return _service().get_summary_versions(job_id)

    def save_summary_version(self, job_id: str, text: str) -> bool:
        """Append a new summary version; keep at most 3 per job."""
        _service().save_summary_version(job_id, text)
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
        s_dir = _session_dir(job_id)
        if (json_path and os.path.exists(json_path)) or os.path.isdir(s_dir):
            # A new transcription creates its session directory before the
            # transcript exists. Use the same per-session lock as the worker,
            # then re-check the transcript path after acquiring it. Otherwise
            # a rename made during the pipeline can be written only to
            # meta.json and then hidden by the completed transcript.
            lock = _get_transcript_lock(job_id)
            with lock:
                json_path = _transcript_path(job_id)
                if not json_path or not os.path.exists(json_path):
                    if not os.path.isdir(s_dir):
                        return False
                    meta_path = os.path.join(s_dir, "meta.json")
                    meta = _read_json_file(meta_path, {})
                    if not isinstance(meta, dict):
                        meta = {}
                    meta["filename"] = name
                    _write_json_atomic(meta_path, meta)
                    return True
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
        # WAV-only legacy flat layout.
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
        lock = _get_transcript_lock(job_id)
        with lock:
            # Resolve targets only after any active transcribe/refine/save/rename
            # finishes so deletion cannot race a transactional commit.
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
        from src.preferences import update_config
        update_config(CONFIG_PATH, lambda cfg: cfg.update(api=config))
        return True

    def get_api_config(self) -> dict:
        return self._load_config().get("api", {})

    def get_runtime_identity(self) -> dict:
        from src.runtime_identity import get_runtime_identity
        return get_runtime_identity()

    def get_qwen_backend_settings(self) -> dict:
        from src.preferences import get_backend_settings
        return get_backend_settings(CONFIG_PATH)

    def save_qwen_backend_settings(self, backend: str) -> dict:
        from src.preferences import save_backend_settings
        try:
            save_backend_settings(CONFIG_PATH, backend)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
        from src.preferences import update_config
        update_config(CONFIG_PATH, lambda cfg: cfg.setdefault("storage", {}).update(output_dir=path))
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
        from src.preferences import update_config
        update_config(CONFIG_PATH, lambda cfg: cfg.update(obsidian={"folder": (folder or "").strip(), "enabled": bool(enabled)}))
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
