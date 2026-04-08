"""
NativeCaptureHelper — manages AudioAssistCaptureHelper Swift subprocess.

Responsibilities:
  - Create / destroy the named pipe (FIFO) for PCM streaming
  - Launch and control the helper process lifecycle
  - Parse NDJSON events from helper stdout
  - Read float32 PCM from the FIFO and run Silero VAD + ASR
  - Expose start / pause / resume / stop interface matching RealtimeTranscriber

Public interface is intentionally compatible with RealtimeTranscriber so
app.py can swap backends without changing its realtime control flow.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import queue
import select
import signal
import subprocess
import sys
import tempfile
import threading
import wave
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Audio constants — must match RealtimeTranscriber and the Swift helper
SAMPLE_RATE        = 16_000
CHUNK_SIZE         = 512       # samples per VAD frame
VAD_THRESHOLD      = 0.5
SILENCE_CHUNKS     = 15
MIN_SPEECH_CHUNKS  = 5
MAX_SEGMENT_SECONDS = 10       # force-flush when a single utterance exceeds this


def _default_helper_path() -> str:
    """Return the expected path to the compiled AudioAssistCaptureHelper binary.

    Supports two environments:
    - Frozen (PyInstaller): binary is bundled alongside the executable in the
      _MEIPASS temp tree or next to sys.executable (onedir builds).
    - Development: binary lives at native/AudioAssistCaptureHelper/.build/release/
      relative to the project root (inferred from this source file's location).
    """
    binary_name = "AudioAssistCaptureHelper"

    # ── Frozen (PyInstaller) ──────────────────────────────────────────────────
    if getattr(sys, "frozen", False):
        # _MEIPASS is the unpacked temp dir for onefile builds; for onedir
        # builds sys.executable's directory works for both.
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, binary_name)

    # ── Development ───────────────────────────────────────────────────────────
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(
        project_root,
        "native", "AudioAssistCaptureHelper",
        ".build", "release", binary_name,
    )


class NativeCaptureHelper:
    """
    System-audio capture backend backed by the Swift helper process.

    Interface mirrors RealtimeTranscriber so app.py can use either backend
    through a uniform `start / pause / resume / stop / get_segments` contract.
    """

    def __init__(
        self,
        mode: str = "system",
        engine: str = "qwen",
        on_result: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        output_path: Optional[str] = None,
        helper_path: Optional[str] = None,
    ) -> None:
        self._mode        = mode
        self._engine      = engine
        self._on_result   = on_result or (lambda seg: None)
        self._on_error    = on_error  or (lambda msg: None)
        self._output_path = output_path        # WAV path written by the helper
        self._helper_path = helper_path or _default_helper_path()

        # Subprocess / IPC handles
        self._process: Optional[subprocess.Popen] = None
        self._fifo_path: Optional[str] = None
        self._fifo_fd: Optional[int]   = None

        # Background threads
        self._event_thread:  Optional[threading.Thread] = None
        self._pcm_thread:    Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None

        # Runtime state
        self._running = False
        self._paused  = False
        self._stop_event = threading.Event()

        # VAD accumulation state (PCM thread only — no locking needed)
        self._speech_buffer: list       = []
        self._silence_count: int        = 0
        self._in_speech:     bool       = False
        self._total_samples: int        = 0
        self._segment_start_samples: int = 0

        # Accumulated transcription segments
        self._segments: list = []

        # Serial ASR worker — prevents concurrent Metal/GPU calls
        self._transcribe_queue: queue.Queue = queue.Queue()

        # Telemetry counters (populated during a session)
        self._flush_count:  int = 0
        self._chunks_read:  int = 0

        # Lazy-loaded models
        self._vad = None
        self._asr = None

    # ── Public interface ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Load models, create FIFO, launch helper, start reader threads."""
        self._stop_event.clear()
        self._load_models()

        if not self._output_path:
            raise ValueError("output_path must be set before calling start()")

        # Create FIFO
        self._fifo_path = os.path.join(
            tempfile.gettempdir(),
            f"audioassist-{os.getpid()}.fifo",
        )
        if os.path.exists(self._fifo_path):
            os.unlink(self._fifo_path)
        os.mkfifo(self._fifo_path)

        try:
            # Open FIFO read-end BEFORE launching the helper so the helper's
            # blocking O_WRONLY open completes immediately.
            self._fifo_fd = os.open(self._fifo_path, os.O_RDONLY | os.O_NONBLOCK)
            # Switch to blocking reads for the PCM reader thread.
            fl = fcntl.fcntl(self._fifo_fd, fcntl.F_GETFL)
            fcntl.fcntl(self._fifo_fd, fcntl.F_SETFL, fl & ~os.O_NONBLOCK)

            # Start serial ASR worker
            self._worker_thread = threading.Thread(
                target=self._transcription_worker,
                daemon=True,
                name="native-transcribe-worker",
            )
            self._worker_thread.start()

            # Launch helper subprocess
            cmd = [
                self._helper_path, "stream",
                "--mode",        self._mode,
                "--pcm-fifo",    self._fifo_path,
                "--wav-out",     self._output_path,
                "--sample-rate", str(SAMPLE_RATE),
                "--channels",    "1",
            ]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception:
            # Clean up FIFO fd and worker thread on any startup failure
            if self._fifo_fd is not None:
                try:
                    os.close(self._fifo_fd)
                except OSError:
                    pass
                self._fifo_fd = None
            if self._fifo_path and os.path.exists(self._fifo_path):
                try:
                    os.unlink(self._fifo_path)
                except OSError:
                    pass
                self._fifo_path = None
            if self._worker_thread is not None and self._worker_thread.is_alive():
                self._transcribe_queue.put(None)
            self._worker_thread = None
            raise

        self._running = True

        # Event reader thread (parses stdout NDJSON)
        self._event_thread = threading.Thread(
            target=self._event_reader,
            daemon=True,
            name="native-event-reader",
        )
        self._event_thread.start()

        # Stderr drain thread — prevents helper from blocking when stderr pipe fills.
        threading.Thread(
            target=self._stderr_reader,
            daemon=True,
            name="native-stderr-reader",
        ).start()

        # PCM reader thread (reads FIFO, runs VAD + ASR)
        self._pcm_thread = threading.Thread(
            target=self._pcm_reader,
            daemon=True,
            name="native-pcm-reader",
        )
        self._pcm_thread.start()

        logger.info("NativeCaptureHelper started (mode=%s)", self._mode)

    def pause(self) -> None:
        """Pause capture: send SIGUSR1 to helper, flush pending speech."""
        self._paused = True
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGUSR1)
            except ProcessLookupError:
                pass
        if self._speech_buffer:
            self._flush_speech()
        logger.info("NativeCaptureHelper paused")

    def resume(self) -> None:
        """Resume capture: send SIGUSR2 to helper, reset VAD state."""
        self._paused = False
        self._speech_buffer.clear()
        self._silence_count = 0
        self._in_speech     = False
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGUSR2)
            except ProcessLookupError:
                pass
        logger.info("NativeCaptureHelper resumed")

    def stop(self) -> None:
        """Stop capture: terminate helper, drain FIFO, flush ASR queue, clean up."""
        # Step 1: Terminate helper — closes the FIFO write end so _pcm_reader
        # gets EOF and exits naturally.
        proc = self._process
        self._process = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        # Step 2: Belt-and-suspenders signal — wakes the reader out of its
        # select() poll if the write-end EOF hasn't arrived yet.
        self._stop_event.set()

        # Step 3: Wait for the PCM reader to finish draining and exit.
        pcm_drained = True
        if self._pcm_thread is not None:
            self._pcm_thread.join(timeout=10)
            if self._pcm_thread.is_alive():
                logger.critical(
                    "NativeCaptureHelper: pcm_thread still alive after 10 s — "
                    "skipping flush and ASR drain to avoid deadlock"
                )
                pcm_drained = False
                # Best-effort: send sentinel so worker can still drain whatever
                # was already queued before the reader got stuck.
                if self._worker_thread is not None and self._worker_thread.is_alive():
                    try:
                        self._transcribe_queue.put(None)
                    except Exception:
                        pass
            self._pcm_thread = None

        if pcm_drained:
            # Step 4: Flush any speech remaining in the buffer (reader has fully exited).
            if self._speech_buffer:
                self._flush_speech()

            # Step 5: Drain the ASR worker.
            if self._worker_thread is not None and self._worker_thread.is_alive():
                self._transcribe_queue.put(None)  # sentinel
                self._worker_thread.join(timeout=30)
                if self._worker_thread.is_alive():
                    logger.error(
                        "NativeCaptureHelper: worker thread did not exit within 30 s timeout"
                    )
            self._worker_thread = None

        # Step 6: Close FIFO read-end.
        fd = self._fifo_fd
        self._fifo_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        # Step 7: Remove FIFO from filesystem.
        if self._fifo_path and os.path.exists(self._fifo_path):
            try:
                os.unlink(self._fifo_path)
            except OSError:
                pass
            self._fifo_path = None

        self._running = False

        logger.info(
            "NativeCaptureHelper stopped: segments=%d flush=%d chunks_read=%d",
            len(self._segments),
            self._flush_count,
            self._chunks_read,
        )

    def get_segments(self) -> list:
        """Return a copy of accumulated realtime transcription segments."""
        return list(self._segments)

    # ── Model loading ──────────────────────────────────────────────────────────

    def _load_models(self) -> None:
        from silero_vad import load_silero_vad
        self._vad = load_silero_vad()
        logger.info("Silero VAD loaded (native capture)")

        if self._engine == "whisper":
            from .asr_whisper import WhisperASREngine
            self._asr = WhisperASREngine()
        else:
            from .asr import ASREngine
            self._asr = ASREngine(with_timestamps=False)
        self._asr.load()
        logger.info("ASR engine loaded for native capture: %s", self._engine)

    # ── Event reader (helper stdout → NDJSON) ──────────────────────────────────

    def _event_reader(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("helper stdout non-JSON: %s", line)
                    continue
                self._handle_event(event)
        except Exception:
            logger.exception("NativeCaptureHelper event reader error")

    def _stderr_reader(self) -> None:
        """Drain helper stderr so the pipe never fills and blocks the helper."""
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    logger.debug("helper stderr: %s", line)
        except Exception:
            pass

    def _handle_event(self, event: dict) -> None:
        ev = event.get("event", "")
        if ev == "error":
            msg = event.get("message") or event.get("reason") or "unknown error"
            logger.error("Helper error event: %s", msg)
            self._on_error(msg)
        elif ev == "permission_required":
            perm = event.get("permission", "unknown")
            self._on_error(f"permission_required:{perm}")
        elif ev == "warning":
            reason = event.get("reason", "")
            logger.warning("Helper warning: reason=%s", reason)
            # Mic-related warnings are non-fatal; notify the frontend so it can
            # show a degradation notice while the session continues in system-only mode.
            if reason in ("mic_unavailable", "mic_converter_failed", "mic_capture_failed"):
                self._on_error(f"mic_degraded:{reason}")
        elif ev == "started":
            logger.info("Helper stream started: sr=%s ch=%s",
                        event.get("sample_rate"), event.get("channels"))
        elif ev == "stopped":
            logger.info("Helper stream stopped")
        elif ev == "paused":
            logger.debug("Helper paused")
        elif ev == "resumed":
            logger.debug("Helper resumed")
        elif ev == "stats":
            logger.debug("Helper stats: dropped_frames=%s", event.get("dropped_frames"))
        else:
            logger.debug("Helper event: %s", event)

    # ── PCM reader (FIFO → VAD → ASR) ─────────────────────────────────────────

    def _pcm_reader(self) -> None:
        """Read float32 PCM chunks from FIFO, run VAD + ASR.

        Uses select() with a 50 ms timeout so _stop_event can interrupt a
        stalled read. Exits on EOF (write end closed), OSError, or when
        _stop_event is set while the FIFO is idle.
        """
        import numpy as np

        fd = self._fifo_fd
        if fd is None:
            return

        chunk_bytes = CHUNK_SIZE * 4  # float32 = 4 bytes/sample
        chunks_read = 0
        buf = b""

        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 0.05)
            except (OSError, ValueError):
                break  # fd is invalid

            if not ready:
                # No data in this 50 ms window — check for stop signal.
                if self._stop_event.is_set():
                    break
                continue

            try:
                data = os.read(fd, chunk_bytes - len(buf))
            except OSError:
                break
            if not data:
                break  # EOF — write end closed

            buf += data
            if len(buf) >= chunk_bytes:
                chunk = np.frombuffer(buf[:chunk_bytes], dtype=np.float32).copy()
                buf = buf[chunk_bytes:]
                chunks_read += 1
                if not self._paused:
                    self._process_audio_chunk(chunk)

        # Zero-pad and process any partial tail left in buf after EOF/stop.
        # The Swift helper's drainRemaining() may produce a non-chunk-aligned tail.
        if buf:
            tail = buf + b"\x00" * (chunk_bytes - len(buf))
            chunk = np.frombuffer(tail, dtype=np.float32).copy()
            chunks_read += 1
            if not self._paused:
                self._process_audio_chunk(chunk)

        self._chunks_read = chunks_read
        logger.debug("NativeCaptureHelper PCM reader exited (chunks_read=%d)", chunks_read)

    def _process_audio_chunk(self, chunk) -> None:
        """Run Silero VAD on one chunk; flush utterance to ASR when done."""
        import torch

        self._total_samples += CHUNK_SIZE
        chunk_t = torch.from_numpy(chunk).float()
        try:
            speech_prob = self._vad(chunk_t, SAMPLE_RATE).item()
        except Exception:
            speech_prob = 0.0

        is_speech = speech_prob >= VAD_THRESHOLD

        if is_speech:
            if not self._in_speech:
                self._segment_start_samples = self._total_samples - CHUNK_SIZE
            self._silence_count = 0
            self._in_speech     = True
            self._speech_buffer.append(chunk)
            # Force-flush if the current utterance has grown too long
            speech_samples = len(self._speech_buffer) * CHUNK_SIZE
            if speech_samples >= MAX_SEGMENT_SECONDS * SAMPLE_RATE:
                self._flush_speech()
        elif self._in_speech:
            self._silence_count += 1
            self._speech_buffer.append(chunk)
            if self._silence_count >= SILENCE_CHUNKS:
                self._flush_speech()

    # ── Utterance flush ────────────────────────────────────────────────────────

    def _flush_speech(self) -> None:
        buf       = self._speech_buffer.copy()
        start_sec = self._segment_start_samples / SAMPLE_RATE
        end_sec   = start_sec + len(buf) * CHUNK_SIZE / SAMPLE_RATE
        self._speech_buffer.clear()
        self._silence_count = 0
        self._in_speech     = False
        if len(buf) < MIN_SPEECH_CHUNKS:
            return
        self._flush_count += 1
        self._transcribe_queue.put((buf, start_sec, end_sec))

    def _transcription_worker(self) -> None:
        """Serial ASR worker — drains the transcription queue."""
        while True:
            item = self._transcribe_queue.get()
            if item is None:  # sentinel
                self._transcribe_queue.task_done()
                break
            try:
                buf, start_sec, end_sec = item
                self._transcribe_segment(buf, start_sec, end_sec)
            finally:
                self._transcribe_queue.task_done()

    def _transcribe_segment(
        self, audio_chunks: list, start_sec: float, end_sec: float
    ) -> None:
        import numpy as np

        audio    = np.concatenate(audio_chunks)
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            _write_wav(tmp_path, audio, SAMPLE_RATE)
            result = self._asr.transcribe(tmp_path)
            text   = result.text.strip()
            if text:
                seg = {
                    "text":  text,
                    "start": round(start_sec, 3),
                    "end":   round(end_sec,   3),
                }
                self._segments.append(seg)
                self._on_result(seg)
        except Exception as e:
            logger.exception("NativeCaptureHelper: transcribe segment failed")
            self._on_error(str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


# ── Module helpers ─────────────────────────────────────────────────────────────

def _write_wav(path: str, audio, sample_rate: int) -> None:
    """Write float32 mono numpy array to a 16-bit WAV file."""
    import numpy as np
    pcm = (audio * 32767.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
