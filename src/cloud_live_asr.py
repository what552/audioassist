"""Bounded, ordered Token Plan transcription alongside local audio capture."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import io
import logging
import threading
import time
import wave

from .token_plan_asr import TokenPlanClient, TokenPlanError

SAMPLE_RATE = 16_000
BATCH_SECONDS = 8.0
MAX_BATCH_WAIT = 6.0
MAX_BATCH_SECONDS = 15.032  # includes at most one VAD frame
MAX_GAP_SECONDS = 1.0
MAX_PENDING_SECONDS = 60.0
MAX_PENDING_ITEMS = 128
MAX_AGE_SECONDS = 45.0
REQUEST_TIMEOUT = 15.0
STOP_TIMEOUT = 10.0
MAX_MISSING_RANGES = 64
logger = logging.getLogger(__name__)


@dataclass
class Utterance:
    frames: list
    start: float
    end: float
    queued_at: float
    group: int


def _encode_wav(utterances: list[Utterance]) -> bytes:
    import numpy as np

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        previous_end = round(utterances[0].start * SAMPLE_RATE)
        for item in utterances:
            gap = round(item.start * SAMPLE_RATE) - previous_end
            if gap > 0:
                wav.writeframesraw(bytes(gap * 2))
            samples = np.concatenate(item.frames)
            wav.writeframesraw((samples * 32767).clip(-32768, 32767).astype("<i2").tobytes())
            previous_end = round(item.end * SAMPLE_RATE)
    return output.getvalue()


class CloudLiveASR:
    def __init__(self, key: str, on_result, on_warning) -> None:
        self._key = key
        self._on_result = on_result
        self._on_warning = on_warning
        self._condition = threading.Condition()
        self._queue: deque[Utterance] = deque()
        self._pending_seconds = 0.0
        self._group = 0
        self._stop_deadline: float | None = None
        self._cooldown_until = 0.0
        self._terminal_code: str | None = None
        self._error: str | None = None
        self._processed_through = 0.0
        self._missing: list[tuple[float, float]] = []
        self._missing_truncated = False
        self._missed_items = 0
        self._done = False
        self._client: TokenPlanClient | None = None

    def submit(self, frames: list, start: float, end: float) -> None:
        """Accept a bounded VAD utterance without waiting for the network."""
        with self._condition:
            duration = end - start
            if self._stop_deadline is not None:
                return
            if self._terminal_code or time.monotonic() < self._cooldown_until:
                self._miss_locked(start, end, self._terminal_code or "rate_limited")
            elif len(self._queue) >= MAX_PENDING_ITEMS or self._pending_seconds + duration > MAX_PENDING_SECONDS:
                self._miss_locked(start, end, "backlog_full")
            else:
                self._queue.append(Utterance(frames, start, end, time.monotonic(), self._group))
                self._pending_seconds += duration
                self._condition.notify()

    def flush(self) -> None:
        with self._condition:
            self._group += 1
            self._condition.notify()

    def finish(self) -> None:
        with self._condition:
            if self._stop_deadline is not None:
                return
            self._group += 1
            deadline = self._stop_deadline = time.monotonic() + STOP_TIMEOUT
            client = self._client
            self._condition.notify()
        if client is not None:
            client.limit_deadline(deadline)

    def snapshot(self) -> dict:
        with self._condition:
            return {
                "provider": "token-plan", "closed": self._done and self._stop_deadline is not None,
                "error": self._error, "processed_through": round(self._processed_through, 3),
                "missing_count": self._missed_items, "missing_ranges_truncated": self._missing_truncated,
                "missing_ranges": [[round(start, 3), round(end, 3)] for start, end in self._missing],
            }

    def _miss_locked(self, start: float, end: float, code: str) -> None:
        self._missed_items += 1
        self._error = code
        self._missing.append((start, end))
        merged: list[tuple[float, float]] = []
        for first, last in sorted(self._missing):
            if merged and first <= merged[-1][1] + 0.001:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        if len(merged) > MAX_MISSING_RANGES:
            self._missing_truncated = True
        self._missing = merged[-MAX_MISSING_RANGES:]

    def _dequeue_locked(self) -> Utterance:
        item = self._queue.popleft()
        self._pending_seconds = max(0, self._pending_seconds - (item.end - item.start))
        return item

    def _next_batch(self) -> list[Utterance]:
        with self._condition:
            while True:
                now = time.monotonic()
                if self._terminal_code or (self._stop_deadline is not None and now >= self._stop_deadline):
                    while self._queue:
                        item = self._dequeue_locked()
                        self._miss_locked(item.start, item.end, self._terminal_code or "stop_timeout")
                    return []
                if not self._queue:
                    if self._stop_deadline is not None:
                        return []
                    self._condition.wait()
                    continue
                head = self._queue[0]
                if now - head.queued_at > MAX_AGE_SECONDS or now < self._cooldown_until:
                    item = self._dequeue_locked()
                    self._miss_locked(item.start, item.end, "rate_limited" if now < self._cooldown_until else "backlog_expired")
                    continue
                count = 1
                end = head.end
                for item in list(self._queue)[1:]:
                    if (item.group != head.group or item.start - end > MAX_GAP_SECONDS
                            or item.end - head.start > MAX_BATCH_SECONDS):
                        break
                    count += 1
                    end = item.end
                wait = head.queued_at + MAX_BATCH_WAIT - now
                send = count < len(self._queue) or end - head.start >= BATCH_SECONDS or head.group != self._group
                if not send and wait > 0:
                    self._condition.wait(wait)
                    continue
                return [self._dequeue_locked() for _ in range(count)]

    def _request(self, client: TokenPlanClient, wav: bytes) -> str:
        deadline = time.monotonic() + REQUEST_TIMEOUT
        with self._condition:
            if self._stop_deadline is not None:
                deadline = min(deadline, self._stop_deadline)
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                raise TokenPlanError("request_timeout")
            try:
                return client.transcribe(wav, timeout=remaining)
            except TokenPlanError as exc:
                if exc.code != "connect_failed" or attempt:
                    raise
        raise AssertionError("unreachable")

    def _process(self, client: TokenPlanClient, batch: list[Utterance]) -> None:
        start, end = batch[0].start, batch[-1].end
        started = time.monotonic()
        try:
            text = self._request(client, _encode_wav(batch))
            if not text:
                raise TokenPlanError("empty_text")
            self._on_result({"text": text, "start": round(start, 3), "end": round(end, 3)})
        except Exception as exc:
            error = exc if isinstance(exc, TokenPlanError) else TokenPlanError("worker_failed")
            with self._condition:
                if error.code in {"quota_exhausted", "access_denied", "worker_failed"}:
                    self._terminal_code = error.code
                elif error.code == "rate_limited":
                    self._cooldown_until = time.monotonic() + error.retry_after
                self._miss_locked(start, end, error.code)
            self._on_warning(start, end, error.code)
        finally:
            with self._condition:
                self._processed_through = end
            logger.info(
                "Token Plan batch audio_s=%.3f queued_s=%.3f request_s=%.3f",
                end - start, started - batch[0].queued_at, time.monotonic() - started,
            )

    def run(self) -> None:
        """Called once by the capture adapter's ASR owner thread."""
        client = None
        try:
            client = TokenPlanClient(self._key)
            with self._condition:
                self._client = client
            while batch := self._next_batch():
                self._process(client, batch)
        except Exception:
            # Avoid logging transport exceptions that could contain remote response data.
            logger.error("Token Plan recording worker stopped unexpectedly")
            with self._condition:
                self._terminal_code = "worker_failed"
                self._error = self._terminal_code
                while self._queue:
                    item = self._dequeue_locked()
                    self._miss_locked(item.start, item.end, self._terminal_code)
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                logger.error("Unable to close the Token Plan HTTP client")
            finally:
                with self._condition:
                    self._client = None
                    self._done = True
