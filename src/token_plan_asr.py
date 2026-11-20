"""Explicitly selected Token Plan speech recognition for short recording batches."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time

import httpx

ENDPOINT = (
    "https://token-plan.cn-beijing.maas.aliyuncs.com"
    "/api/v1/services/aigc/multimodal-generation/generation"
)
MODEL = "qwen-audio-3.0-asr-flash"
KEY_ENV = "QWEN_TOKEN_PLAN_API_KEY"
KEYCHAIN_SERVICE = "com.audioassist.app.token-plan-asr"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_AUDIO_BYTES = 16_000 * 2 * 300 + 44  # 5 min, mono PCM16 WAV
logger = logging.getLogger(__name__)


class TokenPlanError(RuntimeError):
    def __init__(self, code: str, *, retry_after: float = 0):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def validate_key(value: str) -> str:
    # The service documents the prefix, but the opaque suffix can contain punctuation.
    if not isinstance(value, str) or not re.fullmatch(r"sk-sp-[\x21-\x7e]{8,512}", value.strip()):
        raise TokenPlanError("invalid_key_format")
    return value.strip()


def _keychain_query(security, profile_dir: str | None) -> dict:
    service = KEYCHAIN_SERVICE
    if profile_dir is not None:
        profile_id = hashlib.sha256(os.fsencode(profile_dir)).hexdigest()[:16]
        service = f"{service}.dev.{profile_id}"
    return {
        security.kSecClass: security.kSecClassGenericPassword,
        security.kSecAttrService: service,
        security.kSecAttrAccount: "audioassist",
    }


def load_key(profile_dir: str | None = None) -> str | None:
    if value := os.environ.get(KEY_ENV):
        return validate_key(value)
    if sys.platform != "darwin":
        return None
    import Security

    query = _keychain_query(Security, profile_dir)
    query[Security.kSecReturnData] = True
    query[Security.kSecUseAuthenticationUI] = Security.kSecUseAuthenticationUIFail
    status, data = Security.SecItemCopyMatching(query, None)
    if status == Security.errSecItemNotFound:
        return None
    if status != 0 or data is None:
        raise TokenPlanError("keychain_unavailable")
    try:
        return validate_key(bytes(data).decode("ascii"))
    except UnicodeDecodeError:
        raise TokenPlanError("invalid_key_format") from None


def save_key(value: str, profile_dir: str | None = None) -> None:
    key = validate_key(value)
    if sys.platform != "darwin":
        raise TokenPlanError("keychain_unavailable")
    import Security
    from Foundation import NSData

    data = key.encode("ascii")
    query = _keychain_query(Security, profile_dir)
    attributes = {Security.kSecValueData: NSData.dataWithBytes_length_(data, len(data))}
    status = Security.SecItemUpdate(query, attributes)
    if status == Security.errSecItemNotFound:
        status, _ = Security.SecItemAdd({**query, **attributes}, None)
        if status == Security.errSecDuplicateItem:
            status = Security.SecItemUpdate(query, attributes)
    if status != 0:
        raise TokenPlanError("keychain_unavailable")


class TokenPlanClient:
    def __init__(self, key: str) -> None:
        self._key = validate_key(key)
        self._loop = asyncio.new_event_loop()
        self._client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self._active_task: asyncio.Task | None = None

    def limit_deadline(self, deadline: float) -> None:
        """Shorten the in-flight request lifetime when capture stops."""
        def cancel_request() -> None:
            if self._active_task is not None:
                self._active_task.cancel()

        def schedule() -> None:
            self._loop.call_later(max(0, deadline - time.monotonic()), cancel_request)

        try:
            self._loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            pass  # The owner thread already closed the client.

    def close(self) -> None:
        try:
            self._loop.run_until_complete(self._client.aclose())
        finally:
            self._loop.close()

    def transcribe(self, wav: bytes, timeout: float) -> str:
        if not 44 < len(wav) <= MAX_AUDIO_BYTES:
            raise TokenPlanError("invalid_audio")
        return self._loop.run_until_complete(self._transcribe(wav, timeout))

    async def _transcribe(self, wav: bytes, timeout: float) -> str:
        payload = {
            "model": MODEL,
            "input": {"messages": [{"role": "user", "content": [{
                "type": "input_audio",
                "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")},
            }]}]},
            "parameters": {"format": "wav", "sample_rate": 16_000},
        }
        self._active_task = asyncio.current_task()
        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream(
                    "POST", ENDPOINT, json=payload,
                    headers={"Authorization": f"Bearer {self._key}"},
                    timeout=httpx.Timeout(timeout, connect=min(timeout, 3.0)),
                ) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise TokenPlanError("invalid_response")
                        raw.extend(chunk)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise TokenPlanError("connect_failed") from None
        except (TimeoutError, httpx.TimeoutException):
            raise TokenPlanError("request_timeout") from None
        except httpx.HTTPError:
            raise TokenPlanError("network_error") from None
        except asyncio.CancelledError:
            raise TokenPlanError("stop_timeout") from None
        finally:
            self._active_task = None

        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        status = response.status_code
        code = str(body.get("code", "")).lower() if isinstance(body, dict) else ""
        if code in {"insufficient_quota", "allocationquota.freequotaexhausted"}:
            raise TokenPlanError("quota_exhausted")
        if status in {401, 403, 404} or code in {"invalidapikey", "accessdenied", "modelnotfound"}:
            raise TokenPlanError("access_denied")
        if status == 429 or code in {"throttling", "throttling.ratelimit", "rate_limit_exceeded"}:
            try:
                retry_after = min(86400, max(1, int(response.headers["Retry-After"])))
            except (KeyError, ValueError):
                retry_after = 60
            raise TokenPlanError("rate_limited", retry_after=retry_after)
        if status >= 500:
            raise TokenPlanError("server_error")
        if status != 200 or code or not isinstance(body, dict):
            raise TokenPlanError("invalid_response")
        output = body.get("output")
        text = output.get("text") if isinstance(output, dict) else None
        if not isinstance(text, str) or len(text) > 100_000:
            raise TokenPlanError("invalid_response")
        return text.strip()
