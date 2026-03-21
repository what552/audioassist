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
import threading
from typing import Optional

from platformdirs import user_data_dir

logger = logging.getLogger(__name__)

# Resolved at runtime after webview window is created
_window = None

APP_DATA_DIR = user_data_dir("TranscribeApp", appauthor=False)
OUTPUT_DIR = os.path.join(APP_DATA_DIR, "output")
CONFIG_PATH = os.path.join(APP_DATA_DIR, "config.json")
TEMPLATES_PATH = os.path.join(APP_DATA_DIR, "templates.json")

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


class API:
    def __init__(self):
        self._realtime = None  # RealtimeTranscriber instance when active

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

    # ── Transcription ──────────────────────────────────────────────────────────

    def transcribe(self, path: str, options: dict) -> dict:
        """
        Start transcription in background thread.
        Pushes onTranscribeProgress(job_id, progress, message) events to JS.

        Returns: {"job_id": str}
        """
        import uuid
        job_id = str(uuid.uuid4())

        def _run():
            try:
                from src.pipeline import run as pipeline_run

                def _progress(pct: float, msg: str):
                    js = f"onTranscribeProgress({json.dumps(job_id)}, {pct:.4f}, {json.dumps(msg)})"
                    _push(js)

                hf_token = options.get("hf_token") or os.environ.get("HF_TOKEN")
                engine = options.get("engine", "qwen")
                num_speakers = options.get("num_speakers")

                json_path, md_path = pipeline_run(
                    audio_path=path,
                    output_dir=OUTPUT_DIR,
                    hf_token=hf_token,
                    engine=engine,
                    num_speakers=num_speakers,
                    job_id=job_id,
                    progress_callback=_progress,
                )
                js = f"onTranscribeComplete({json.dumps(job_id)}, {json.dumps(json_path)})"
                _push(js)
            except Exception as e:
                logger.exception("Transcription failed")
                js = f"onTranscribeError({json.dumps(job_id)}, {json.dumps(str(e))})"
                _push(js)

        threading.Thread(target=_run, daemon=True).start()
        return {"job_id": job_id}

    def get_transcript(self, job_id: str) -> Optional[dict]:
        """Load transcript JSON for a given job_id."""
        path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
        if not os.path.exists(path):
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
        path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
        with _transcript_locks_mutex:
            if job_id not in _transcript_locks:
                _transcript_locks[job_id] = threading.Lock()
            lock = _transcript_locks[job_id]

        with lock:
            if not os.path.exists(path):
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

        return True

    # ── Realtime ───────────────────────────────────────────────────────────────

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

        options = options or {}
        engine = options.get("engine", "qwen")

        def _run():
            try:
                import uuid
                session_id = str(uuid.uuid4())
                output_path = os.path.join(OUTPUT_DIR, f"{session_id}.wav")
                os.makedirs(OUTPUT_DIR, exist_ok=True)

                from src.realtime import RealtimeTranscriber
                rt = RealtimeTranscriber(
                    engine=engine,
                    output_path=output_path,
                    on_result=lambda text: _push(f"onRealtimeResult({json.dumps(text)})"),
                    on_error=lambda msg:  _push(f"onRealtimeError({json.dumps(msg)})"),
                )
                rt.start()
                # Race: stop_realtime() may have cleared self._realtime while
                # models were loading. If so, shut down immediately and return.
                if self._realtime is None:
                    rt.stop()
                    return
                self._realtime = rt
                _push(f"onRealtimeStarted({json.dumps(session_id)})")
            except Exception as e:
                logger.exception("Realtime start failed")
                self._realtime = None
                _push(f"onRealtimeError({json.dumps(str(e))})")

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "started"}

    def stop_realtime(self) -> dict:
        """
        Stop realtime transcription.
        Pushes onRealtimeStopped() after the stream is closed.

        Returns: {"status": "stopped"} or {"status": "not_running"}
        """
        rt = self._realtime
        self._realtime = None
        if rt is None:
            return {"status": "not_running"}

        def _run():
            try:
                if hasattr(rt, "stop"):
                    rt.stop()
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
                path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
                if not os.path.exists(path):
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

    # ── History ────────────────────────────────────────────────────────────────

    def get_history(self) -> list[dict]:
        """
        Return past transcription jobs, newest first.
        Each entry: {job_id, filename, date, duration, language}.
        """
        import glob
        result = []
        if not os.path.isdir(OUTPUT_DIR):
            return result
        for path in sorted(
            glob.glob(os.path.join(OUTPUT_DIR, "*.json")),
            key=os.path.getmtime,
            reverse=True,
        ):
            if os.path.basename(path).endswith("_summary.json"):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                job_id = os.path.splitext(os.path.basename(path))[0]
                segs = data.get("segments", [])
                duration = round(segs[-1]["end"]) if segs else 0
                result.append({
                    "job_id": job_id,
                    "filename": data.get("filename") or data.get("audio") or job_id,
                    "date": data.get("created_at", ""),
                    "duration": duration,
                    "language": data.get("language", ""),
                })
            except Exception:
                pass
        return result

    def get_summary_versions(self, job_id: str) -> list[dict]:
        """Return saved summary versions for a job (newest last)."""
        path = os.path.join(OUTPUT_DIR, f"{job_id}_summary.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save_summary_version(self, job_id: str, text: str) -> bool:
        """Append a new summary version; keep at most 3 per job."""
        import time as _time
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, f"{job_id}_summary.json")
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
        return True

    # ── Model management ───────────────────────────────────────────────────────

    def get_models(self) -> list[dict]:
        """Return model catalog with download status."""
        from src.model_manager import ModelManager
        return ModelManager().list_models()

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

    def save_summary_templates(self, templates: list[dict]) -> bool:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        return True

    def get_summary_templates(self) -> list[dict]:
        if not os.path.exists(TEMPLATES_PATH):
            return []
        with open(TEMPLATES_PATH, encoding="utf-8") as f:
            return json.load(f)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_config(self, cfg: dict):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
