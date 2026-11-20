"""Framework-independent application services shared by the GUI and CLI."""
from __future__ import annotations

import datetime as dt
import glob
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from .session_paths import get_session_paths, resolve_summary_path, resolve_transcript_path
from .language import canonical_language_code, canonical_transcript_language


ProgressCallback = Callable[[float, str], None]
logger = logging.getLogger(__name__)

_LANG_INSTRUCTIONS: dict[str, str] = {
    "zh": "请用中文生成纪要。",
    "yue": "請用粵語（繁體中文）生成紀要。",
    "ja": "日本語で要約してください。",
    "ko": "한국어로 요약해 주세요。",
    "fr": "Veuillez répondre en français.",
    "de": "Bitte auf Deutsch antworten.",
    "es": "Por favor, responde en español.",
    "pt": "Por favor, responda em português.",
    "ru": "Пожалуйста, отвечайте на русском языке.",
    "ar": "يرجى الرد باللغة العربية.",
    "en": "Please respond in English.",
    "mixed": "请使用转写中的主要语言生成纪要，并保留必要的中英文术语。",
}


class AudioAssistError(RuntimeError):
    """Base class for errors safe to expose through command-line interfaces."""

    code = "audioassist_error"


class InvalidJobIdError(AudioAssistError):
    code = "invalid_job_id"


class SessionNotFoundError(AudioAssistError):
    code = "session_not_found"


class ConfigurationError(AudioAssistError):
    code = "configuration_error"


class LiveTranscriptError(AudioAssistError):
    code = "live_transcript_invalid"


class LiveTranscriptReadError(AudioAssistError):
    code = "live_transcript_read_failed"


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REALTIME_WAV_PREFIX = "realtime_recording"
_HIGH_ACCURACY_REFINE_KEY = "high_accuracy_refine"
_summary_locks: dict[str, threading.Lock] = {}
_summary_locks_guard = threading.Lock()


def language_instruction(language: str, text: str) -> str:
    """Return a summary-language instruction, with a conservative CJK fallback."""
    reported = canonical_language_code(language)
    normalized = canonical_transcript_language(language, text) if reported else ""
    if normalized in _LANG_INSTRUCTIONS:
        return _LANG_INSTRUCTIONS[normalized]
    if text:
        cjk = sum(1 for character in text if "\u4e00" <= character <= "\u9fff")
        if cjk / len(text) > 0.10:
            return _LANG_INSTRUCTIONS["zh"]
    return ""


def validate_job_id(job_id: str) -> str:
    """Reject identifiers that could escape the meetings directory."""
    value = (job_id or "").strip()
    if value in {"", ".", ".."} or not _JOB_ID_RE.fullmatch(value):
        raise InvalidJobIdError(
            "job_id must contain only letters, numbers, '.', '_' or '-' and be at most 128 characters"
        )
    return value


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError, TypeError):
        return default


def _write_json_atomic(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _summary_lock(path: str) -> threading.Lock:
    with _summary_locks_guard:
        return _summary_locks.setdefault(path, threading.Lock())


class AudioAssistService:
    """Synchronous use cases with no GUI or web-framework dependencies."""

    def __init__(
        self,
        output_dir: str,
        config_path: str | None = None,
        templates_path: str | None = None,
    ) -> None:
        self.output_dir = os.path.abspath(output_dir)
        self.config_path = config_path
        self.templates_path = templates_path

    # -- Session resources -------------------------------------------------

    def get_transcript(self, job_id: str) -> dict | None:
        job_id = validate_job_id(job_id)
        path = resolve_transcript_path(self.output_dir, job_id)
        if not path:
            return None
        data = _read_json(path, None)
        if not isinstance(data, dict):
            return None
        result = dict(data)
        text = " ".join(
            str(segment.get("text", ""))
            for segment in result.get("segments", [])
            if isinstance(segment, dict)
        )
        result["language"] = canonical_transcript_language(
            result.get("language", ""), text
        )
        # Older realtime recordings can use separate recording and transcript
        # directories. Externally-cleaned sessions can lose the copied source_audio
        # while the original recording remains linked through transcribed_job_id.
        # Normalize the returned view without rewriting the historical JSON.
        resolved_audio = self._resolve_audio_path(job_id, result)
        if resolved_audio or "audio" in result:
            result["audio"] = resolved_audio
        return result

    def get_summary_versions(self, job_id: str) -> list[dict]:
        job_id = validate_job_id(job_id)
        path = resolve_summary_path(self.output_dir, job_id)
        data = _read_json(path, [])
        return data if isinstance(data, list) else []

    def save_summary_version(self, job_id: str, text: str) -> dict:
        job_id = validate_job_id(job_id)
        path = resolve_summary_path(self.output_dir, job_id)
        version = {
            "text": text,
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
        }
        with _summary_lock(path):
            data = _read_json(path, [])
            versions = data if isinstance(data, list) else []
            versions.append(version)
            _write_json_atomic(path, versions[-3:])
        return version

    def list_sessions(self) -> list[dict]:
        """Return canonical and legacy sessions, newest first."""
        result: list[dict] = []
        if not os.path.isdir(self.output_dir):
            return result

        seen_job_ids: set[str] = set()
        meetings_dir = os.path.join(self.output_dir, "meetings")
        linked_realtime_audio = self._linked_realtime_audio_paths(meetings_dir)
        legacy_refine_jobs = set(linked_realtime_audio)
        from .checkpoint import find_interrupted

        interrupted_checkpoints = find_interrupted(self.output_dir)
        interrupted_job_ids = {
            checkpoint.get("job_id", "") for checkpoint in interrupted_checkpoints
        }

        if os.path.isdir(meetings_dir):
            for job_id in os.listdir(meetings_dir):
                session_dir = os.path.join(meetings_dir, job_id)
                if not os.path.isdir(session_dir):
                    continue
                transcript_path = os.path.join(session_dir, "transcript.json")
                if os.path.exists(transcript_path):
                    data = _read_json(transcript_path, None)
                    if self._is_transcript(data):
                        seen_job_ids.add(job_id)
                        data = dict(data)
                        data["audio"] = self._resolve_audio_path(
                            job_id,
                            data,
                            linked_realtime_audio,
                        )
                        meta = _read_json(os.path.join(session_dir, "meta.json"), {})
                        refine_available = self._refine_available(meta, data)
                        if not refine_available:
                            refine_available = self._legacy_refine_available(
                                job_id, data, meta, legacy_refine_jobs
                            )
                        result.append(self._history_entry(
                            job_id,
                            data,
                            refine_available=refine_available,
                        ))
                    continue

                wav_path = self._find_realtime_wav(session_dir)
                if not wav_path:
                    continue
                # A full transcription started from this recovered recording and
                # left a checkpoint. Expose the resumable job, not the raw WAV,
                # otherwise the UI has no way to continue completed chunks.
                if job_id in interrupted_job_ids:
                    continue
                seen_job_ids.add(job_id)
                meta = _read_json(os.path.join(session_dir, "meta.json"), {})
                if isinstance(meta, dict) and meta.get("transcribed_job_id"):
                    continue
                date = dt.datetime.fromtimestamp(os.path.getmtime(wav_path)).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                result.append({
                    "job_id": job_id,
                    "filename": meta.get("filename", job_id[:8]) if isinstance(meta, dict) else job_id[:8],
                    "date": date,
                    "duration": 0,
                    "language": "",
                    "audio_path": os.path.abspath(wav_path),
                    "type": "realtime",
                })

        for path in glob.glob(os.path.join(self.output_dir, "*.json")):
            basename = os.path.basename(path)
            if basename.endswith(("_summary.json", "_meta.json", "_agent_chat.json")):
                continue
            data = _read_json(path, None)
            if not self._is_transcript(data):
                continue
            job_id = os.path.splitext(basename)[0]
            if job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            data = dict(data)
            data["audio"] = self._resolve_audio_path(
                job_id,
                data,
                linked_realtime_audio,
            )
            result.append(self._history_entry(job_id, data))

        for wav_path in glob.glob(os.path.join(self.output_dir, "*.wav")):
            job_id = os.path.splitext(os.path.basename(wav_path))[0]
            if job_id.endswith("_audio") or job_id in seen_job_ids:
                continue
            if job_id in interrupted_job_ids:
                continue
            meta = _read_json(os.path.join(self.output_dir, f"{job_id}_meta.json"), {})
            if isinstance(meta, dict) and meta.get("transcribed_job_id"):
                continue
            seen_job_ids.add(job_id)
            date = dt.datetime.fromtimestamp(os.path.getmtime(wav_path)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            result.append({
                "job_id": job_id,
                "filename": meta.get("filename", job_id[:8]) if isinstance(meta, dict) else job_id[:8],
                "date": date,
                "duration": 0,
                "language": "",
                "audio_path": os.path.abspath(wav_path),
                "type": "realtime",
            })

        for checkpoint in interrupted_checkpoints:
            job_id = checkpoint.get("job_id", "")
            if not job_id or job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            meta = _read_json(
                os.path.join(meetings_dir, job_id, "meta.json"), {}
            )
            display_name = (
                meta.get("filename") if isinstance(meta, dict) else None
            ) or os.path.basename(checkpoint.get("source_audio", job_id))
            result.append({
                "job_id": job_id,
                "filename": display_name,
                "date": checkpoint.get("updated_at", ""),
                "duration": 0,
                "language": "",
                "audio_path": checkpoint.get("source_audio", ""),
                "type": "file",
                "status": "interrupted",
                "qwen_backend": checkpoint.get("qwen_backend"),
            })

        result.sort(key=lambda item: item.get("date", ""), reverse=True)
        return result

    def get_recording_audio_path(self, job_id: str) -> str:
        """Resolve a saved raw recording by session id.

        This is the trusted boundary used by the desktop recovery action. The
        frontend supplies only a validated session id and cannot redirect a
        transcription worker to an arbitrary or stale client-side path.
        """
        job_id = validate_job_id(job_id)
        session_dir = os.path.join(self.output_dir, "meetings", job_id)
        wav_path = self._find_realtime_wav(session_dir)
        if wav_path and os.path.isfile(wav_path):
            return os.path.abspath(wav_path)

        legacy_path = os.path.join(self.output_dir, f"{job_id}.wav")
        if os.path.isfile(legacy_path):
            return os.path.abspath(legacy_path)

        raise SessionNotFoundError(f"Recording audio not found: {job_id}")

    def get_status(self, job_id: str) -> dict:
        job_id = validate_job_id(job_id)
        transcript = self.get_transcript(job_id)
        paths = get_session_paths(self.output_dir, job_id)
        if transcript is not None:
            return {
                "job_id": job_id,
                "status": "completed",
                "progress": 1.0,
                "resumable": False,
            }

        from .checkpoint import read as read_checkpoint

        checkpoint = read_checkpoint(paths.session_dir)
        if checkpoint:
            completed = int(checkpoint.get("completed_chunks", 0) or 0)
            total = int(checkpoint.get("total_chunks", 0) or 0)
            return {
                "job_id": job_id,
                "status": checkpoint.get("status", "interrupted"),
                "progress": completed / total if total else 0.0,
                "resumable": True,
                "completed_chunks": completed,
                "total_chunks": total,
                "updated_at": checkpoint.get("updated_at", ""),
                "qwen_backend": checkpoint.get("qwen_backend"),
            }

        entry = next((item for item in self.list_sessions() if item["job_id"] == job_id), None)
        if entry and entry.get("type") == "realtime":
            return {
                "job_id": job_id,
                "status": "recorded",
                "progress": 0.0,
                "resumable": False,
            }
        raise SessionNotFoundError(f"Session not found: {job_id}")

    def get_session(self, job_id: str) -> dict:
        job_id = validate_job_id(job_id)
        entry = next((item for item in self.list_sessions() if item["job_id"] == job_id), None)
        if entry is None:
            raise SessionNotFoundError(f"Session not found: {job_id}")
        return {
            "session": entry,
            "state": self.get_status(job_id),
            "transcript": self.get_transcript(job_id),
            "summary_versions": self.get_summary_versions(job_id),
        }

    def get_live_transcript(
        self, job_id: str, *, after_seq: int = 0, limit: int = 100
    ) -> dict:
        """Return a paged, provisional realtime ASR draft for one recording."""
        job_id = validate_job_id(job_id)
        if not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0:
            raise LiveTranscriptError("after_seq must be a nonnegative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise LiveTranscriptError("limit must be an integer from 1 to 500")
        paths = get_session_paths(self.output_dir, job_id)
        if not os.path.isdir(paths.session_dir):
            raise SessionNotFoundError(f"Session not found: {job_id}")
        from .live_transcript import CLOUD_STATUS_FILENAME, annotate_cloud_coverage, read_page

        try:
            page = read_page(paths.session_dir, after_seq=after_seq, limit=limit)
        except (OSError, ValueError) as error:
            raise LiveTranscriptReadError(f"Unable to read realtime draft for {job_id}") from error
        if not page["draft_complete"]:
            page["draft_warning"] = (
                "Draft is not confirmed complete; only persisted utterances are shown"
            )
        coverage = _read_json(os.path.join(paths.session_dir, CLOUD_STATUS_FILENAME), None)
        return annotate_cloud_coverage({"session_id": job_id, "provisional": True, **page}, coverage)

    # -- Processing use cases ---------------------------------------------

    def transcribe(
        self,
        audio_path: str,
        *,
        engine: str = "qwen",
        model_id: str | None = None,
        hf_token: str | None = None,
        num_speakers: int | None = None,
        job_id: str | None = None,
        realtime_segments: list[dict] | None = None,
        progress_callback: ProgressCallback | None = None,
        qwen_backend: str | None = None,
    ) -> dict:
        """Run one transcription synchronously and persist a complete session."""
        source = os.path.abspath(os.path.expanduser(audio_path))
        if not os.path.isfile(source) and not realtime_segments:
            raise FileNotFoundError(f"Audio file not found: {source}")
        job_id = validate_job_id(job_id or str(uuid.uuid4()))
        engine = self._engine_for_model(model_id, engine)
        if engine == "qwen":
            from .preferences import default_backend, validate_backend
            try:
                qwen_backend = validate_backend(qwen_backend) if qwen_backend is not None else default_backend(self.config_path)
            except (ValueError, OSError) as exc:
                raise ConfigurationError(f"Invalid Qwen backend preference: {exc}") from exc
        paths = get_session_paths(self.output_dir, job_id)
        os.makedirs(paths.session_dir, exist_ok=True)
        staging_dir = tempfile.mkdtemp(prefix=".transcribe-", dir=paths.session_dir)

        preferred_filename = self._preferred_filename(job_id, source)
        try:
            if realtime_segments:
                from .pipeline import run_realtime_segments

                staged_json, staged_markdown = run_realtime_segments(
                    segments=realtime_segments,
                    wav_path=source,
                    output_dir=staging_dir,
                    hf_token=hf_token,
                    num_speakers=num_speakers,
                    job_id=job_id,
                    output_stem="transcript",
                    progress_callback=progress_callback,
                )
            else:
                from .pipeline import run

                staged_json, staged_markdown = run(
                    audio_path=source,
                    output_dir=staging_dir,
                    hf_token=hf_token,
                    engine=engine,
                    asr_model_id=model_id,
                    num_speakers=num_speakers,
                    job_id=job_id,
                    output_stem="transcript",
                    session_dir=paths.session_dir,
                    progress_callback=progress_callback,
                    qwen_backend=qwen_backend,
                )

            audio_copy: str | None = None
            staged_audio: str | None = None
            try:
                extension = os.path.splitext(source)[1]
                audio_copy = os.path.join(paths.session_dir, f"source_audio{extension}")
                staged_audio = os.path.join(staging_dir, f"source_audio{extension}")
                shutil.copy2(source, staged_audio)
            except OSError:
                logger.warning("Failed to copy source audio for job %s", job_id, exc_info=True)
                audio_copy = None
                staged_audio = None

            data = _read_json(staged_json, {})
            if not isinstance(data, dict):
                data = {}
            if audio_copy:
                data["audio"] = audio_copy
            data["job_id"] = job_id
            if preferred_filename:
                data["filename"] = preferred_filename
            _write_json_atomic(staged_json, data)

            # One final cooperative cancellation point after every staged
            # artifact is ready, but before any canonical file is replaced.
            if progress_callback:
                progress_callback(1.0, "Committing output...")

            json_path = paths.transcript_new
            markdown_path = os.path.join(paths.session_dir, "transcript.md")
            if staged_audio and audio_copy:
                os.replace(staged_audio, audio_copy)
            os.replace(staged_markdown, markdown_path)
            # Commit the JSON last: its presence defines a completed session.
            os.replace(staged_json, json_path)

            if source.lower().endswith(".wav"):
                meta_path = self._realtime_meta_path(source)
                if meta_path:
                    meta = _read_json(meta_path, {})
                    if not isinstance(meta, dict):
                        meta = {}
                    meta["transcribed_job_id"] = job_id
                    _write_json_atomic(meta_path, meta)

            return {
                "job_id": job_id,
                "json_path": json_path,
                "markdown_path": markdown_path,
                "audio_path": audio_copy or data.get("audio", ""),
                "audio_copy": audio_copy,
                "engine": engine,
                "model_id": model_id,
                "qwen_backend": data.get("qwen_backend") if engine == "qwen" else None,
            }
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def summarize(
        self,
        job_id: str,
        prompt: str,
        *,
        stream: bool = False,
    ) -> str | Iterator[str]:
        """Generate a summary from configured OpenAI-compatible API settings."""
        job_id = validate_job_id(job_id)
        transcript = self.get_transcript(job_id)
        if transcript is None:
            raise SessionNotFoundError(f"Transcript not found: {job_id}")
        text = " ".join(
            segment.get("text", "") for segment in transcript.get("segments", [])
        )
        summary_language_instruction = language_instruction(
            transcript.get("language", ""), text
        )
        if summary_language_instruction:
            prompt = summary_language_instruction + "\n\n" + prompt
        config = self._load_config().get("api", {})
        base_url = (config.get("base_url") or "").strip()
        model = (config.get("model") or "").strip()
        if not base_url or not model:
            raise ConfigurationError(
                "Summary API is not configured; set api.base_url and api.model in config.json"
            )

        from .summary import summarize

        return summarize(
            text,
            prompt,
            base_url,
            config.get("api_key", ""),
            model,
            stream=stream,
        )

    # -- Internal helpers --------------------------------------------------

    def _load_config(self) -> dict:
        if not self.config_path:
            return {}
        data = _read_json(self.config_path, {})
        return data if isinstance(data, dict) else {}

    def get_high_accuracy_refine(self, job_id: str) -> dict | None:
        """Return a durable manual-refine descriptor for a completed session."""
        job_id = validate_job_id(job_id)
        paths = get_session_paths(self.output_dir, job_id)
        meta = _read_json(os.path.join(paths.session_dir, "meta.json"), {})
        if not isinstance(meta, dict):
            return None
        transcript = _read_json(paths.transcript_new, {})
        resolved_audio = self._resolve_audio_path(job_id, transcript)
        if isinstance(transcript, dict):
            transcript = dict(transcript)
            transcript["audio"] = resolved_audio
        descriptor = meta.get(_HIGH_ACCURACY_REFINE_KEY)
        if not isinstance(descriptor, dict) or not descriptor.get("available"):
            legacy_jobs = self._legacy_realtime_transcript_ids(
                os.path.join(self.output_dir, "meetings")
            )
            if not self._legacy_refine_available(job_id, transcript, meta, legacy_jobs):
                return None
            descriptor = {
                "available": True,
                "source_audio": transcript.get("audio"),
                "engine": None,
                "model_id": None,
                "num_speakers": None,
                "qwen_backend": None,
                "legacy_inferred": True,
            }
        elif not self._looks_like_realtime_quick_draft(transcript):
            return None

        source_audio = descriptor.get("source_audio")
        if not isinstance(source_audio, str) or not os.path.isfile(source_audio):
            source_audio = resolved_audio
        if not isinstance(source_audio, str) or not os.path.isfile(source_audio):
            return None
        engine = descriptor.get("engine")
        is_legacy = descriptor.get("legacy_inferred") is True
        if engine not in {"qwen", "whisper"} and not is_legacy:
            return None
        return {
            "path": os.path.abspath(source_audio),
            "s_dir": paths.session_dir,
            "engine": engine,
            "model_id": descriptor.get("model_id"),
            "hf_token": None,
            "num_speakers": descriptor.get("num_speakers"),
            "qwen_backend": descriptor.get("qwen_backend"),
            "legacy_inferred": is_legacy,
            "audio_copy": os.path.abspath(source_audio),
            "json_path": paths.transcript_new,
        }

    def set_high_accuracy_refine(
        self,
        job_id: str,
        *,
        source_audio: str,
        engine: str,
        model_id: str | None,
        num_speakers: int | None,
        qwen_backend: str | None,
    ) -> None:
        """Persist eligibility and non-secret options for manual high accuracy."""
        job_id = validate_job_id(job_id)
        paths = get_session_paths(self.output_dir, job_id)
        os.makedirs(paths.session_dir, exist_ok=True)
        meta_path = os.path.join(paths.session_dir, "meta.json")
        meta = _read_json(meta_path, {})
        if not isinstance(meta, dict):
            meta = {}
        meta[_HIGH_ACCURACY_REFINE_KEY] = {
            "available": True,
            "source_audio": os.path.abspath(source_audio),
            "engine": engine,
            "model_id": model_id,
            "num_speakers": num_speakers,
            "qwen_backend": qwen_backend,
        }
        _write_json_atomic(meta_path, meta)

    def clear_high_accuracy_refine(self, job_id: str) -> None:
        """Mark a manual high-accuracy pass as consumed after it succeeds."""
        job_id = validate_job_id(job_id)
        paths = get_session_paths(self.output_dir, job_id)
        meta_path = os.path.join(paths.session_dir, "meta.json")
        meta = _read_json(meta_path, {})
        if not isinstance(meta, dict) or _HIGH_ACCURACY_REFINE_KEY not in meta:
            return
        descriptor = meta.get(_HIGH_ACCURACY_REFINE_KEY)
        if isinstance(descriptor, dict):
            descriptor["available"] = False
        else:
            meta.pop(_HIGH_ACCURACY_REFINE_KEY, None)
        _write_json_atomic(meta_path, meta)

    @staticmethod
    def _refine_available(meta: Any, transcript: Any) -> bool:
        if not isinstance(meta, dict):
            return False
        descriptor = meta.get(_HIGH_ACCURACY_REFINE_KEY)
        if not isinstance(descriptor, dict) or not descriptor.get("available"):
            return False
        source_audio = descriptor.get("source_audio")
        if not isinstance(source_audio, str) or not os.path.isfile(source_audio):
            source_audio = (
                transcript.get("audio") if isinstance(transcript, dict) else None
            )
        return (
            isinstance(source_audio, str)
            and os.path.isfile(source_audio)
            and AudioAssistService._looks_like_realtime_quick_draft(transcript)
        )

    @classmethod
    def _linked_realtime_audio_paths(cls, meetings_dir: str) -> dict[str, str]:
        """Map completed transcript IDs to their still-present source recordings."""
        result: dict[str, str] = {}
        if os.path.isdir(meetings_dir):
            try:
                session_ids = sorted(os.listdir(meetings_dir))
            except OSError:
                session_ids = []
            for session_id in session_ids:
                session_dir = os.path.join(meetings_dir, session_id)
                if not os.path.isdir(session_dir):
                    continue
                meta = _read_json(os.path.join(session_dir, "meta.json"), {})
                target = (
                    meta.get("transcribed_job_id") if isinstance(meta, dict) else None
                )
                if not isinstance(target, str) or not target or target in result:
                    continue
                wav_path = cls._find_realtime_wav(session_dir)
                if wav_path and os.path.isfile(wav_path):
                    result[target] = os.path.abspath(wav_path)

        # Pre-session-directory recordings live directly under output_dir and
        # carry their link in <recording_id>_meta.json. They remain supported
        # read inputs, so they must participate in the same recovery index.
        output_dir = os.path.dirname(os.path.abspath(meetings_dir))
        for wav_path in sorted(glob.glob(os.path.join(output_dir, "*.wav"))):
            stem = os.path.splitext(os.path.basename(wav_path))[0]
            meta = _read_json(os.path.join(output_dir, f"{stem}_meta.json"), {})
            target = meta.get("transcribed_job_id") if isinstance(meta, dict) else None
            if isinstance(target, str) and target and target not in result:
                result[target] = os.path.abspath(wav_path)
        return result

    def _resolve_audio_path(
        self,
        job_id: str,
        transcript: Any,
        linked_realtime_audio: dict[str, str] | None = None,
    ) -> str:
        """Return a usable session audio path, falling back to its linked recording."""
        recorded = transcript.get("audio") if isinstance(transcript, dict) else None
        if isinstance(recorded, str) and os.path.isfile(recorded):
            return os.path.abspath(recorded)
        if linked_realtime_audio is None:
            linked_realtime_audio = self._linked_realtime_audio_paths(
                os.path.join(self.output_dir, "meetings")
            )
        fallback = linked_realtime_audio.get(job_id)
        if fallback and os.path.isfile(fallback):
            return os.path.abspath(fallback)
        return recorded if isinstance(recorded, str) else ""

    @classmethod
    def _legacy_realtime_transcript_ids(cls, meetings_dir: str) -> set[str]:
        """Find pre-metadata realtime drafts through their source-session link."""
        return set(cls._linked_realtime_audio_paths(meetings_dir))

    @staticmethod
    def _legacy_refine_available(
        job_id: str,
        transcript: Any,
        meta: Any,
        legacy_refine_jobs: set[str],
    ) -> bool:
        """Conservatively recognize realtime quick drafts written before this field."""
        if job_id not in legacy_refine_jobs or not isinstance(transcript, dict):
            return False
        if isinstance(meta, dict) and _HIGH_ACCURACY_REFINE_KEY in meta:
            return False
        # Full Qwen output records the backend; the realtime diarize-only draft does not.
        if transcript.get("qwen_backend"):
            return False
        source_audio = transcript.get("audio")
        return (
            isinstance(source_audio, str)
            and os.path.isfile(source_audio)
            and AudioAssistService._looks_like_realtime_quick_draft(transcript)
        )

    @staticmethod
    def _looks_like_realtime_quick_draft(transcript: Any) -> bool:
        """Realtime Phase 1 has segments but no word-level ASR alignment."""
        if not isinstance(transcript, dict):
            return False
        segments = transcript.get("segments")
        if not isinstance(segments, list) or not segments:
            return False
        return all(
            isinstance(segment, dict) and not segment.get("words")
            for segment in segments
        )

    @staticmethod
    def _history_entry(
        job_id: str,
        data: dict,
        *,
        refine_available: bool = False,
    ) -> dict:
        segments = data.get("segments", [])
        duration = round(segments[-1].get("end", 0)) if segments else 0
        text = " ".join(
            str(segment.get("text", ""))
            for segment in segments
            if isinstance(segment, dict)
        )
        return {
            "job_id": job_id,
            "filename": data.get("filename") or data.get("audio") or job_id,
            "date": data.get("created_at", ""),
            "duration": duration,
            "language": canonical_transcript_language(
                data.get("language", ""), text
            ),
            "audio_path": data.get("audio", ""),
            "type": "file",
            "refine_available": refine_available,
        }

    @staticmethod
    def _is_transcript(data: Any) -> bool:
        return isinstance(data, dict) and isinstance(data.get("segments"), list)

    @staticmethod
    def _find_realtime_wav(session_dir: str) -> str | None:
        legacy = os.path.join(session_dir, "realtime_recording.wav")
        if os.path.exists(legacy):
            return legacy
        try:
            for name in sorted(os.listdir(session_dir)):
                if name.startswith(_REALTIME_WAV_PREFIX + "_") and name.endswith(".wav"):
                    return os.path.join(session_dir, name)
        except OSError:
            return None
        return None

    def _realtime_meta_path(self, wav_path: str) -> str | None:
        source = os.path.abspath(wav_path)
        meetings_dir = os.path.abspath(os.path.join(self.output_dir, "meetings"))
        session_dir = os.path.dirname(source)
        if os.path.dirname(session_dir) == meetings_dir:
            return os.path.join(session_dir, "meta.json")
        if os.path.dirname(source) == self.output_dir:
            stem = os.path.splitext(os.path.basename(source))[0]
            return os.path.join(self.output_dir, f"{stem}_meta.json")
        return None

    def _preferred_filename(self, job_id: str, source: str) -> str:
        paths = get_session_paths(self.output_dir, job_id)
        existing = _read_json(paths.transcript_new, {})
        if isinstance(existing, dict) and existing.get("filename"):
            return str(existing["filename"]).strip()
        meta = _read_json(os.path.join(paths.session_dir, "meta.json"), {})
        if isinstance(meta, dict) and meta.get("filename"):
            return str(meta["filename"]).strip()
        realtime_meta_path = self._realtime_meta_path(source) if source.lower().endswith(".wav") else None
        realtime_meta = _read_json(realtime_meta_path, {}) if realtime_meta_path else {}
        if isinstance(realtime_meta, dict) and realtime_meta.get("filename"):
            return str(realtime_meta["filename"]).strip()
        return os.path.basename(source)

    @staticmethod
    def _engine_for_model(model_id: str | None, fallback: str) -> str:
        if not model_id:
            return fallback
        from .model_manager import CATALOG

        info = next((model for model in CATALOG if model.id == model_id), None)
        if not info:
            return fallback
        return "whisper" if info.engine == "mlx-whisper" else info.engine
