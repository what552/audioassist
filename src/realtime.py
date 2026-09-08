"""
Realtime transcription — Silero VAD + sounddevice microphone input.

Architecture:
  sounddevice.InputStream (audio callback, runs in audio thread)
      → VAD probability check per chunk (Silero VAD)
      → accumulate speech frames in buffer
      → on end-of-utterance (silence threshold reached): flush to ASR
      → ASR runs in worker thread; result delivered via on_result callback

All heavy imports (sounddevice, silero_vad, torch, numpy) are lazy
(inside methods) so this module is importable without those packages
installed — important for the test environment.
"""
from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import time
import wave
from typing import Callable, Optional

from .asr_factory import create_qwen_engine
from .realtime_models import resolve_realtime_model

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SAMPLE_RATE    = 16_000   # Hz — required by Silero VAD and most ASR engines
CHUNK_SIZE     = 512      # samples per VAD frame (~32 ms at 16 kHz)
VAD_THRESHOLD  = 0.5      # speech probability cutoff
SILENCE_CHUNKS = 15       # consecutive silence frames to end utterance (~480 ms)
MIN_SPEECH_CHUNKS = 5     # minimum speech frames to bother transcribing (~160 ms)
MODEL_READY_TIMEOUT_SECONDS = 120.0
MODEL_READY_POLL_SECONDS = 0.05


# ── Main class ────────────────────────────────────────────────────────────────

class RealtimeTranscriber:
    """Microphone → Silero VAD → ASR pipeline."""

    def __init__(
        self,
        engine: str = "qwen",
        on_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        output_path: Optional[str] = None,
        model_id: Optional[str] = None,
        diagnostic_id: Optional[str] = None,
        qwen_backend: Optional[str] = None,
    ):
        self._engine      = engine
        self._on_result   = on_result or (lambda text: None)
        self._on_error    = on_error  or (lambda msg: None)
        self._output_path = output_path
        self._model_id    = model_id
        self._qwen_backend = qwen_backend
        self._diagnostic_id = diagnostic_id or "unassigned"

        self._running    = False
        self._stream     = None
        self._asr        = None
        self._asr_lock   = threading.Lock()
        self._vad        = None
        self._wav_writer = None  # wave.Wave_write for session recording

        # VAD state — accessed only from the audio callback thread
        self._speech_buffer: list = []
        self._silence_count  = 0
        self._in_speech      = False

        # Timestamp tracking — samples elapsed since stream start
        self._total_samples: int = 0
        self._segment_start_samples: int = 0

        # Accumulated realtime segments [{text, start, end}] — for P3 skip-ASR
        self._segments: list = []

        # Serial transcription worker — prevents concurrent Metal GPU usage
        self._transcribe_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_ready = threading.Event()
        self._worker_load_error: BaseException | None = None
        self._startup_cancel = threading.Event()
        self._worker_control_lock = threading.Lock()
        self._worker_stop_sent = False
        self._capture_lifecycle_lock = threading.Lock()
        self._first_audio_frame_event = threading.Event()
        self._first_audio_frame_info: Optional[tuple[int, str]] = None
        self._diagnostic_thread: Optional[threading.Thread] = None

    def _log_start_stage(self, stage: str, started: Optional[float] = None, **fields) -> None:
        """Emit a correlation-friendly marker for packaged-app diagnostics."""
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        elapsed = ""
        if started is not None:
            elapsed = f" elapsed_ms={(time.monotonic() - started) * 1000:.1f}"
        suffix = f" {details}" if details else ""
        logger.info(
            "Realtime mic start attempt=%s stage=%s%s%s",
            self._diagnostic_id,
            stage,
            elapsed,
            suffix,
            extra={"audioassist_diagnostic": True},
        )

    def _wait_for_first_audio_frame(self) -> None:
        """Move first-frame file logging off the realtime audio callback."""
        if not self._first_audio_frame_event.wait(timeout=10.0):
            return
        info = self._first_audio_frame_info
        if info is not None:
            frames, status = info
            self._log_start_stage(
                "first_audio_frame",
                frames=frames,
                status=status,
            )

    def _stop_diagnostic_waiter(self) -> None:
        self._first_audio_frame_event.set()
        thread = self._diagnostic_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)
        self._diagnostic_thread = None

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Load models then open the microphone stream."""
        if self._startup_cancel.is_set():
            raise RuntimeError("Realtime startup was cancelled")
        if self._worker_thread is not None:
            if self._worker_thread.is_alive():
                raise RuntimeError(
                    "Previous realtime ASR worker is still stopping; wait before restarting"
                )
            self._worker_thread = None
        started = time.monotonic()
        self._log_start_stage("model_load.begin")
        self._worker_ready.clear()
        self._worker_load_error = None
        self._worker_thread = threading.Thread(
            target=self._transcription_worker,
            daemon=True,
            name="realtime-transcribe-worker",
        )
        try:
            self._worker_thread.start()
        except BaseException:
            self._worker_thread = None
            raise
        deadline = time.monotonic() + MODEL_READY_TIMEOUT_SECONDS
        while not self._worker_ready.wait(timeout=MODEL_READY_POLL_SECONDS):
            if self._startup_cancel.is_set():
                raise RuntimeError("Realtime startup was cancelled")
            if time.monotonic() >= deadline:
                self._startup_cancel.set()
                self._request_worker_stop()
                raise TimeoutError("Realtime ASR model loading timed out")
        if self._startup_cancel.is_set():
            raise RuntimeError("Realtime startup was cancelled")
        if self._worker_load_error is not None:
            error = self._worker_load_error
            self._worker_thread.join()
            self._worker_thread = None
            raise error
        self._log_start_stage("model_load.done", started)
        try:
            with self._capture_lifecycle_lock:
                if self._startup_cancel.is_set():
                    raise RuntimeError("Realtime startup was cancelled")
                self._running = True

                # Open session WAV writer before the stream captures audio
                if self._output_path is not None:
                    wav_started = time.monotonic()
                    self._log_start_stage("wav_open.begin")
                    parent = os.path.dirname(self._output_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    self._wav_writer = wave.open(self._output_path, "wb")
                    self._wav_writer.setnchannels(1)
                    self._wav_writer.setsampwidth(2)
                    self._wav_writer.setframerate(SAMPLE_RATE)
                    self._log_start_stage("wav_open.done", wav_started)

                import_started = time.monotonic()
                self._log_start_stage("sounddevice_import.begin")
                import sounddevice as sd
                self._log_start_stage(
                    "sounddevice_import.done",
                    import_started,
                    sounddevice=getattr(sd, "__version__", "unknown"),
                )
                stream_started = time.monotonic()
                self._log_start_stage(
                    "stream_open.begin",
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    blocksize=CHUNK_SIZE,
                )
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=CHUNK_SIZE,
                    callback=self._audio_callback,
                )
                self._log_start_stage("stream_open.done", stream_started)
                activate_started = time.monotonic()
                self._log_start_stage("stream_start.begin")
                self._first_audio_frame_info = None
                self._first_audio_frame_event.clear()
                self._diagnostic_thread = threading.Thread(
                    target=self._wait_for_first_audio_frame,
                    daemon=True,
                    name="realtime-first-frame-diagnostic",
                )
                self._diagnostic_thread.start()
                self._stream.start()
                self._log_start_stage("stream_start.done", activate_started)
        except Exception:
            if self._startup_cancel.is_set():
                raise
            self._running = False
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._wav_writer is not None:
                try:
                    self._wav_writer.close()
                except Exception:
                    pass
                self._wav_writer = None
            if self._worker_thread is not None and self._worker_thread.is_alive():
                self._request_worker_stop()
                self._worker_thread.join()
            self._worker_thread = None
            self._stop_diagnostic_waiter()
            raise
        logger.info("Realtime transcription started (engine=%s)", self._engine)

    def stop(self) -> None:
        """Stop the microphone stream and flush any pending speech."""
        self._startup_cancel.set()
        errors: list[Exception] = []
        with self._capture_lifecycle_lock:
            self._running = False
            stream = self._stream
            self._stream = None
            if stream is not None:
                try:
                    stream.stop()   # blocks until audio callback finishes
                except Exception as exc:
                    errors.append(exc)
                try:
                    stream.close()
                except Exception as exc:
                    errors.append(exc)
            # Stream is fully stopped — safe to close WAV writer (no more callbacks)
            writer = self._wav_writer
            self._wav_writer = None
            if writer is not None:
                try:
                    writer.close()
                except Exception as exc:
                    errors.append(exc)
            if self._speech_buffer:
                try:
                    self._flush_speech()
                except Exception as exc:
                    errors.append(exc)
            # Signal worker to stop after it drains remaining items, then wait.
            if self._worker_thread is not None and self._worker_thread.is_alive():
                try:
                    self._request_worker_stop()
                    self._worker_thread.join()
                except Exception as exc:
                    errors.append(exc)
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = None
                self._close_asr()
            self._stop_diagnostic_waiter()
        logger.info("Realtime transcription stopped")
        if errors:
            raise errors[0]

    def _request_worker_stop(self) -> None:
        """Queue exactly one owner-worker sentinel, including during model load."""
        with self._worker_control_lock:
            worker = self._worker_thread
            if worker is None or not worker.is_alive() or self._worker_stop_sent:
                return
            self._worker_stop_sent = True
            self._transcribe_queue.put(None)

    def pause(self) -> None:
        """Pause microphone capture; WAV writer stays open."""
        self._running = False
        if self._stream is not None:
            self._stream.stop()   # blocks until callback finishes
        if self._speech_buffer:
            self._flush_speech()
        logger.info("Realtime transcription paused")

    def resume(self) -> None:
        """Resume capture into the same WAV file."""
        if self._stream is None:
            return
        self._running = True
        self._speech_buffer.clear()
        self._silence_count = 0
        self._in_speech = False
        self._stream.start()      # re-start stopped (not closed) stream
        logger.info("Realtime transcription resumed")

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_models(self) -> None:
        from silero_vad import load_silero_vad
        self._vad = load_silero_vad()
        logger.info("Silero VAD loaded")

        resolved = resolve_realtime_model(
            engine=self._engine,
            model_id=self._model_id,
        )
        self._engine = resolved.engine
        self._model_id = resolved.model_id

        if resolved.engine == "whisper":
            from .asr_whisper import WhisperASREngine
            self._asr = WhisperASREngine(model_path=resolved.local_path)
        else:
            self._asr = create_qwen_engine(
                with_timestamps=False,
                model_path=resolved.local_path,
                aligner_path=None,
                backend_override=self._qwen_backend,
            )
        try:
            self._asr.load()
        except Exception:
            self._close_asr()
            raise
        logger.info(
            "ASR engine loaded: %s backend=%s (%s)",
            self._engine,
            getattr(self._asr, "backend_name", "default"),
            resolved.local_path,
            extra={"audioassist_diagnostic": True},
        )

    def _close_asr(self, expected: object | None = None) -> None:
        with self._asr_lock:
            if expected is not None and self._asr is not expected:
                return
            asr = self._asr
            self._asr = None
        close = getattr(asr, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.warning("Realtime ASR cleanup failed", exc_info=True)

    # ── Audio callback (runs in sounddevice audio thread) ─────────────────────

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if not self._running:
            return
        if not self._first_audio_frame_event.is_set():
            self._first_audio_frame_info = (frames, str(status or "ok"))
            self._first_audio_frame_event.set()
        import torch

        chunk = indata[:, 0].copy()  # mono, shape (CHUNK_SIZE,)
        self._total_samples += CHUNK_SIZE   # track elapsed samples for timestamps
        chunk_t = torch.from_numpy(chunk).float()
        try:
            speech_prob = self._vad(chunk_t, SAMPLE_RATE).item()
        except Exception:
            speech_prob = 0.0

        # Write every chunk to the session WAV file (continuous recording)
        if self._wav_writer is not None:
            import numpy as np
            pcm = (chunk * 32767.0).clip(-32768, 32767).astype(np.int16)
            self._wav_writer.writeframes(pcm.tobytes())

        is_speech = speech_prob >= VAD_THRESHOLD

        if is_speech:
            if not self._in_speech:
                # Record the start of this utterance (beginning of current chunk)
                self._segment_start_samples = self._total_samples - CHUNK_SIZE
            self._silence_count = 0
            self._in_speech = True
            self._speech_buffer.append(chunk)
        elif self._in_speech:
            self._silence_count += 1
            self._speech_buffer.append(chunk)  # keep trailing silence for natural audio
            if self._silence_count >= SILENCE_CHUNKS:
                self._flush_speech()

    # ── Utterance flush ───────────────────────────────────────────────────────

    def _flush_speech(self) -> None:
        buf = self._speech_buffer.copy()
        start_sec = self._segment_start_samples / SAMPLE_RATE
        end_sec   = start_sec + len(buf) * CHUNK_SIZE / SAMPLE_RATE
        self._speech_buffer.clear()
        self._silence_count = 0
        self._in_speech = False
        if len(buf) < MIN_SPEECH_CHUNKS:
            return  # too short — likely noise, skip
        # Enqueue for serial processing — never spawn concurrent ASR threads
        self._transcribe_queue.put((buf, start_sec, end_sec))

    def _transcription_worker(self) -> None:
        """Long-running worker that serially drains the transcription queue.

        The worker owns backend load, inference, and close. Besides preventing
        concurrent Metal use, this preserves MLX's thread-local stream identity.

        The worker exits when it dequeues the ``None`` sentinel value (sent by
        ``stop()`` after the audio stream is closed).
        """
        backend = None
        try:
            if self._asr is None:
                self._load_models()
            backend = self._asr
        except BaseException as exc:
            self._worker_load_error = exc
            return
        finally:
            self._worker_ready.set()

        try:
            while True:
                item = self._transcribe_queue.get()
                if item is None:  # sentinel — time to exit
                    self._transcribe_queue.task_done()
                    break
                try:
                    buf, start_sec, end_sec = item
                    self._transcribe_segment(buf, start_sec, end_sec)
                finally:
                    self._transcribe_queue.task_done()
        finally:
            self._close_asr(expected=backend)

    def _transcribe_segment(
        self, audio_chunks: list, start_sec: float = 0.0, end_sec: float = 0.0
    ) -> None:
        """Concatenate audio chunks, write temp WAV, run ASR, fire on_result."""
        import numpy as np
        audio = np.concatenate(audio_chunks)
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            _write_wav(tmp_path, audio, SAMPLE_RATE)
            result = self._asr.transcribe(tmp_path)
            text = result.text.strip()
            if text:
                seg = {
                    "text":  text,
                    "start": round(start_sec, 3),
                    "end":   round(end_sec,   3),
                }
                self._segments.append(seg)
                self._on_result(seg)
        except Exception as e:
            logger.exception(
                "Realtime: transcribe segment failed",
                extra={"audioassist_diagnostic": True},
            )
            self._on_error(str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def get_segments(self) -> list:
        """Return accumulated realtime segments [{text, start, end}]."""
        return list(self._segments)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_wav(path: str, audio: "np.ndarray", sample_rate: int) -> None:
    """Write float32 mono numpy array to a 16-bit WAV file (stdlib only)."""
    import numpy as np
    pcm = (audio * 32767.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
