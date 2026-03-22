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
import tempfile
import threading
import wave
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SAMPLE_RATE    = 16_000   # Hz — required by Silero VAD and most ASR engines
CHUNK_SIZE     = 512      # samples per VAD frame (~32 ms at 16 kHz)
VAD_THRESHOLD  = 0.5      # speech probability cutoff
SILENCE_CHUNKS = 15       # consecutive silence frames to end utterance (~480 ms)
MIN_SPEECH_CHUNKS = 5     # minimum speech frames to bother transcribing (~160 ms)


# ── Main class ────────────────────────────────────────────────────────────────

class RealtimeTranscriber:
    """Microphone → Silero VAD → ASR pipeline."""

    def __init__(
        self,
        engine: str = "qwen",
        on_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        output_path: Optional[str] = None,
    ):
        self._engine      = engine
        self._on_result   = on_result or (lambda text: None)
        self._on_error    = on_error  or (lambda msg: None)
        self._output_path = output_path

        self._running    = False
        self._stream     = None
        self._asr        = None
        self._vad        = None
        self._wav_writer = None  # wave.Wave_write for session recording

        # VAD state — accessed only from the audio callback thread
        self._speech_buffer: list = []
        self._silence_count  = 0
        self._in_speech      = False

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Load models then open the microphone stream."""
        self._load_models()
        self._running = True

        # Open session WAV writer before the stream captures audio
        if self._output_path is not None:
            parent = os.path.dirname(self._output_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._wav_writer = wave.open(self._output_path, "wb")
            self._wav_writer.setnchannels(1)
            self._wav_writer.setsampwidth(2)
            self._wav_writer.setframerate(SAMPLE_RATE)

        import sounddevice as sd
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info("Realtime transcription started (engine=%s)", self._engine)

    def stop(self) -> None:
        """Stop the microphone stream and flush any pending speech."""
        self._running = False
        if self._stream is not None:
            self._stream.stop()   # blocks until audio callback finishes
            self._stream.close()
            self._stream = None
        # Stream is fully stopped — safe to close WAV writer (no more callbacks)
        if self._wav_writer is not None:
            self._wav_writer.close()
            self._wav_writer = None
        if self._speech_buffer:
            self._flush_speech()
        logger.info("Realtime transcription stopped")

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

        if self._engine == "whisper":
            from .asr_whisper import WhisperASREngine
            self._asr = WhisperASREngine(with_timestamps=False)
        else:
            from .asr import ASREngine
            self._asr = ASREngine(with_timestamps=False)
        self._asr.load()
        logger.info("ASR engine loaded: %s", self._engine)

    # ── Audio callback (runs in sounddevice audio thread) ─────────────────────

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if not self._running:
            return
        import torch

        chunk = indata[:, 0].copy()  # mono, shape (CHUNK_SIZE,)
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
        self._speech_buffer.clear()
        self._silence_count = 0
        self._in_speech = False
        if len(buf) < MIN_SPEECH_CHUNKS:
            return  # too short — likely noise, skip
        threading.Thread(
            target=self._transcribe_segment,
            args=(buf,),
            daemon=True,
        ).start()

    def _transcribe_segment(self, audio_chunks: list) -> None:
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
                self._on_result(text)
        except Exception as e:
            logger.exception("Realtime: transcribe segment failed")
            self._on_error(str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


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
