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


class API:
    def __init__(self):
        self._realtime = None  # RealtimeTranscriber instance when active
        # Pre-collected realtime segments keyed by wav_path — consumed by transcribe()
        self._rt_segments: dict[str, list] = {}

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

        If pre-collected realtime segments exist for this path (stored by
        stop_realtime()), skips ASR and runs diarization only.

        Returns: {"job_id": str}
        """
        import uuid
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

                if rt_segments:
                    # Phase 1 — diarize-only: fast initial draft from live chunks
                    from src.pipeline import run_realtime_segments
                    json_path, md_path = run_realtime_segments(
                        segments=rt_segments,
                        wav_path=path,
                        output_dir=OUTPUT_DIR,
                        hf_token=hf_token,
                        num_speakers=num_speakers,
                        job_id=job_id,
                        progress_callback=_progress,
                    )
                else:
                    from src.pipeline import run as pipeline_run
                    json_path, md_path = pipeline_run(
                        audio_path=path,
                        output_dir=OUTPUT_DIR,
                        hf_token=hf_token,
                        engine=engine,
                        asr_model_id=model_id,
                        num_speakers=num_speakers,
                        job_id=job_id,
                        progress_callback=_progress,
                    )

                # Copy original audio into output dir for persistent playback.
                # The copy is kept alongside the transcript so history playback
                # works regardless of whether the source file has moved or been
                # deleted.  If the copy fails we log a warning and continue.
                audio_copy: Optional[str] = None
                try:
                    _, ext = os.path.splitext(path)
                    audio_copy = os.path.join(OUTPUT_DIR, f"{job_id}_audio{ext}")
                    shutil.copy2(path, audio_copy)
                    # Patch the JSON: replace the basename "audio" field with
                    # the absolute path of the internal copy so Player.load()
                    # can resolve it via file:// URL.
                    with open(json_path, encoding="utf-8") as f:
                        data = json.load(f)
                    data["audio"] = audio_copy
                    tmp_json = json_path + ".tmp"
                    with open(tmp_json, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    os.replace(tmp_json, json_path)
                except Exception:
                    logger.warning("transcribe: failed to copy audio file", exc_info=True)

                # If source was a realtime WAV from our output dir, record
                # transcribed_job_id in its _meta.json so get_history() can
                # skip the orphaned WAV entry on next launch.
                try:
                    if path.lower().endswith(".wav"):
                        src_abs = os.path.abspath(path)
                        out_abs = os.path.abspath(OUTPUT_DIR)
                        if os.path.dirname(src_abs) == out_abs:
                            wav_stem = os.path.splitext(os.path.basename(path))[0]
                            meta_path_rt = os.path.join(OUTPUT_DIR, f"{wav_stem}_meta.json")
                            rt_meta: dict = {}
                            if os.path.exists(meta_path_rt):
                                try:
                                    with open(meta_path_rt, encoding="utf-8") as f:
                                        rt_meta = json.load(f)
                                except Exception:
                                    pass
                            rt_meta["transcribed_job_id"] = job_id
                            tmp_rt = meta_path_rt + ".tmp"
                            with open(tmp_rt, "w", encoding="utf-8") as f:
                                json.dump(rt_meta, f, ensure_ascii=False, indent=2)
                            os.replace(tmp_rt, meta_path_rt)
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
                        try:
                            from src.pipeline import run as pipeline_run
                            logger.info("Refine ASR started for job %s", job_id)
                            refined_json, _ = pipeline_run(
                                audio_path=path,
                                output_dir=OUTPUT_DIR,
                                hf_token=hf_token,
                                engine=engine,
                                asr_model_id=model_id,
                                num_speakers=num_speakers,
                                job_id=job_id,  # same id → overwrites draft
                                progress_callback=lambda p, m: logger.debug(
                                    "[refine %d%%] %s", int(p * 100), m
                                ),
                            )
                            # Re-patch audio field and atomically overwrite JSON.
                            # Acquire the same per-job lock used by save_transcript()
                            # so a concurrent user save cannot interleave with this write.
                            with _transcript_locks_mutex:
                                if job_id not in _transcript_locks:
                                    _transcript_locks[job_id] = threading.Lock()
                                _refine_lock = _transcript_locks[job_id]

                            with _refine_lock:
                                try:
                                    with open(refined_json, encoding="utf-8") as f:
                                        rdata = json.load(f)
                                    if _ac:
                                        rdata["audio"] = _ac
                                    tmp = refined_json + ".tmp"
                                    with open(tmp, "w", encoding="utf-8") as f:
                                        json.dump(rdata, f, ensure_ascii=False, indent=2)
                                    os.replace(tmp, refined_json)
                                except Exception:
                                    logger.warning(
                                        "refine: failed to patch audio/write JSON", exc_info=True
                                    )
                            _push(f"onTranscribeRefined({json.dumps(job_id)})")
                            logger.info("Refine ASR complete for job %s", job_id)
                        except Exception:
                            logger.exception("Refine ASR failed for job %s", job_id)

                    threading.Thread(target=_refine, daemon=True).start()

            except _TranscriptionCancelled:
                logger.info("Transcription cancelled: %s", job_id)
                _push(f"onTranscribeCancel({json.dumps(job_id)})")
            except Exception as e:
                logger.exception("Transcription failed")
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
        model_id = options.get("model_id")
        engine   = options.get("engine", "qwen")
        if model_id:
            from src.model_manager import CATALOG
            _info = next((m for m in CATALOG if m.id == model_id), None)
            if _info:
                engine = "whisper" if _info.engine == "mlx-whisper" else _info.engine

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

        json_stems: set[str] = set()

        # ── Transcription jobs (*.json) ────────────────────────────────────────
        for path in glob.glob(os.path.join(OUTPUT_DIR, "*.json")):
            basename = os.path.basename(path)
            if basename.endswith("_summary.json") or basename.endswith("_meta.json"):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                stem = os.path.splitext(os.path.basename(path))[0]
                json_stems.add(stem)
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

        # ── WAV-only realtime recordings (no transcript JSON) ──────────────────
        for wav_path in glob.glob(os.path.join(OUTPUT_DIR, "*.wav")):
            stem = os.path.splitext(os.path.basename(wav_path))[0]
            if stem.endswith("_audio"):
                continue  # internal audio copy from transcribe(); not a recording
            if stem in json_stems:
                continue  # already covered by a JSON entry
            mtime = os.path.getmtime(wav_path)
            date_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")
            display_name = stem[:8]
            meta_path = os.path.join(OUTPUT_DIR, f"{stem}_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    if meta.get("transcribed_job_id"):
                        continue  # already transcribed; shown as a file entry
                    display_name = meta.get("filename", display_name)
                except Exception:
                    pass
            result.append({
                "job_id": stem,
                "filename": display_name,
                "date": date_str,
                "duration": 0,
                "language": "",
                "audio_path": os.path.abspath(wav_path),
                "type": "realtime",
            })

        result.sort(key=lambda x: x["date"], reverse=True)
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

    def rename_session(self, job_id: str, name: str) -> bool:
        """Rename a transcript session.

        For JSON-backed sessions: updates the filename field in the JSON.
        For WAV-only realtime sessions: writes a sidecar {job_id}_meta.json.
        """
        json_path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
        if os.path.exists(json_path):
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            data["filename"] = name
            tmp_path = json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, json_path)
            return True
        wav_path = os.path.join(OUTPUT_DIR, f"{job_id}.wav")
        if os.path.exists(wav_path):
            meta_path = os.path.join(OUTPUT_DIR, f"{job_id}_meta.json")
            tmp_path = meta_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"filename": name}, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, meta_path)
            return True
        return False

    def delete_session(self, job_id: str) -> bool:
        """Delete a transcript session and all associated files."""
        json_path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
        wav_path = os.path.join(OUTPUT_DIR, f"{job_id}.wav")
        summary_path = os.path.join(OUTPUT_DIR, f"{job_id}_summary.json")
        meta_path = os.path.join(OUTPUT_DIR, f"{job_id}_meta.json")
        deleted = False
        if os.path.exists(json_path):
            os.remove(json_path)
            deleted = True
        if os.path.exists(wav_path):
            os.remove(wav_path)
            deleted = True
        if os.path.exists(summary_path):
            os.remove(summary_path)
        if os.path.exists(meta_path):
            os.remove(meta_path)
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
