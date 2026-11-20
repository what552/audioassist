import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
import time
import wave

import httpx
import numpy as np
import pytest

from src import cloud_live_asr, token_plan_asr
from src.live_transcript import CLOUD_STATUS_FILENAME, LiveTranscriptWriter, merge_live_writer_state
from src.service import AudioAssistService


KEY = "sk-sp-fixture.with.punctuation"


def create_client(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(token_plan_asr.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    return token_plan_asr.TokenPlanClient(KEY)


def transcribe_on_owner_thread(monkeypatch, handler):
    def run():
        client = create_client(monkeypatch, handler)
        try:
            return client.transcribe(b"0" * 48, timeout=1)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result(timeout=3)


def test_posts_to_the_documented_endpoint_and_returns_input_text(monkeypatch):
    def handle(request):
        assert str(request.url) == token_plan_asr.ENDPOINT
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["model"] == token_plan_asr.MODEL
        assert payload["parameters"] == {"format": "wav", "sample_rate": 16000}
        data = payload["input"]["messages"][0]["content"][0]["input_audio"]["data"]
        assert data.startswith("data:audio/wav;base64,")
        return httpx.Response(200, json={"output": {"text": "hello"}})

    assert transcribe_on_owner_thread(monkeypatch, handle) == "hello"


@pytest.mark.parametrize(("status", "body", "headers", "expected"), [
    (302, {}, {"Location": "https://example.invalid/"}, "invalid_response"),
    (403, {"message": "credentials are never in the error"}, {}, "access_denied"),
    (429, {"code": "insufficient_quota"}, {}, "quota_exhausted"),
    (429, {}, {"Retry-After": "2"}, "rate_limited"),
    (502, {}, {}, "server_error"),
    (200, {"output": {"text": None}}, {}, "invalid_response"),
])
def test_classifies_provider_errors_without_following_redirects(monkeypatch, status, body, headers, expected):
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(status, json=body, headers=headers)

    with pytest.raises(token_plan_asr.TokenPlanError, match=f"^{expected}$") as error:
        transcribe_on_owner_thread(monkeypatch, handle)
    if expected == "rate_limited":
        assert error.value.retry_after == 2
    assert seen == [token_plan_asr.ENDPOINT]


def test_can_shorten_an_inflight_request_on_stop(monkeypatch):
    entered = threading.Event()

    async def handle(request):
        entered.set()
        await asyncio.sleep(5)
        pytest.fail("waited for the network instead of stopping")

    client = create_client(monkeypatch, handle)
    errors = []

    def run():
        try:
            client.transcribe(b"0" * 48, timeout=2)
        except token_plan_asr.TokenPlanError as error:
            errors.append(error.code)
        finally:
            client.close()

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(1)
    client.limit_deadline(time.monotonic() + 0.01)
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert errors == ["stop_timeout"]


@pytest.mark.parametrize("value", ["sk-ordinary-1234", "sk-sp-12345678\nAuthorization: X"])
def test_rejects_other_credentials_and_header_injection(value):
    with pytest.raises(token_plan_asr.TokenPlanError, match="invalid_key_format"):
        token_plan_asr.validate_key(value)


def make_scheduler(monkeypatch, respond):
    sent = []
    segments = []
    warnings = []

    class FakeClient:
        def __init__(self, key):
            assert key == KEY

        def transcribe(self, data, timeout):
            with wave.open(io.BytesIO(data), "rb") as wav:
                assert wav.getframerate() == 16000
                sent.append(wav.getnframes() / wav.getframerate())
            return respond(len(sent))

        def close(self):
            pass

        def limit_deadline(self, deadline):
            pass

    monkeypatch.setattr(cloud_live_asr, "TokenPlanClient", FakeClient)
    scheduler = cloud_live_asr.CloudLiveASR(KEY, segments.append, lambda *warning: warnings.append(warning))
    return scheduler, sent, segments, warnings


def submit(scheduler, start, end):
    scheduler.submit([np.zeros(round((end - start) * 16000), dtype=np.float32)], start, end)


def test_preserves_short_silence_and_does_not_batch_across_pause(monkeypatch):
    scheduler, sent, segments, warnings = make_scheduler(monkeypatch, lambda _: "words")
    submit(scheduler, 0, 1)
    submit(scheduler, 1.25, 2.25)
    scheduler.flush()
    submit(scheduler, 2.5, 3.5)
    scheduler.finish()
    scheduler.run()
    assert sent == [2.25, 1]
    assert [(seg["start"], seg["end"]) for seg in segments] == [(0, 2.25), (2.5, 3.5)]
    assert not warnings
    assert scheduler.snapshot()["closed"]


def test_unsent_audio_becomes_a_gap_instead_of_blocking_capture(monkeypatch):
    monkeypatch.setattr(cloud_live_asr, "MAX_PENDING_ITEMS", 1)
    scheduler, sent, segments, _ = make_scheduler(monkeypatch, lambda _: "first")
    submit(scheduler, 0, 1)
    submit(scheduler, 1, 2)
    scheduler.finish()
    scheduler.run()
    assert sent == [1]
    assert [seg["text"] for seg in segments] == ["first"]
    assert scheduler.snapshot()["missing_ranges"] == [[1, 2]]


def test_permanent_quota_failure_does_not_upload_later_audio(monkeypatch):
    def quota_failure(_):
        raise token_plan_asr.TokenPlanError("quota_exhausted")

    scheduler, sent, segments, warnings = make_scheduler(monkeypatch, quota_failure)
    submit(scheduler, 0, 1)
    scheduler.flush()
    submit(scheduler, 1, 2)
    scheduler.run()
    submit(scheduler, 2, 3)
    scheduler.finish()
    assert sent == [1]
    assert not segments
    assert warnings == [(0, 1, "quota_exhausted")]
    assert scheduler.snapshot()["missing_ranges"] == [[0, 3]]


def test_failed_cloud_audio_does_not_hide_saved_draft(tmp_path):
    session = tmp_path / "meetings" / "token-session"
    writer = LiveTranscriptWriter(str(session))
    writer.append({"text": "First sentence", "start": 0, "end": 1})
    assert writer.close()
    coverage = {"provider": "token-plan", "closed": True, "missing_count": 1, "missing_ranges": [[1, 2]]}
    (session / CLOUD_STATUS_FILENAME).write_text(json.dumps(coverage))
    page = AudioAssistService(str(tmp_path)).get_live_transcript("token-session")
    assert page["segments"][0]["text"] == "First sentence"
    assert page["draft_status"] == "closed"
    assert page["draft_complete"] is False
    assert page["live_asr"] == coverage
    assert merge_live_writer_state(page, {"session_id": "another-session", "live_asr": {}}) == page


def test_local_default_does_not_read_cloud_key(monkeypatch):
    import app

    def unexpected_key_lookup():
        pytest.fail("local recording looked up cloud credentials")

    monkeypatch.setattr(token_plan_asr, "load_key", unexpected_key_lookup)
    monkeypatch.setattr(app, "_macos_microphone_permission_status", lambda: "denied")
    assert app.API().start_realtime()["status"] == "permission_required"


@pytest.mark.parametrize("capture_mode", ["mic", "system", "mix"])
def test_desktop_cloud_recording_closes_with_a_readable_wav_and_live_draft(monkeypatch, tmp_path, capture_mode):
    import app
    from src import native_capture, realtime

    class FakeClient:
        def __init__(self, key):
            assert key == KEY

        def transcribe(self, _wav, timeout):
            return "Synthetic passage"

        def limit_deadline(self, deadline):
            pass

        def close(self):
            pass

    class FakeCapture:
        def __init__(self, output_path, cloud_asr, **kwargs):
            self._output_path = output_path
            self.cloud_asr = cloud_asr
            self.worker = threading.Thread(target=cloud_asr.run)

        def start(self):
            with wave.open(self._output_path, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(bytes(32000))
            submit(self.cloud_asr, 0, 1)
            self.worker.start()

        def stop(self):
            self.cloud_asr.finish()
            self.worker.join(timeout=1)
            assert not self.worker.is_alive()

    started = threading.Event()
    stopped = threading.Event()

    def desktop_event(js):
        if js.startswith("onRealtimeStarted("):
            started.set()
        if js.startswith("onRealtimeStopped("):
            stopped.set()

    monkeypatch.setenv(token_plan_asr.KEY_ENV, KEY)
    monkeypatch.setattr(cloud_live_asr, "TokenPlanClient", FakeClient)
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(app, "_push", desktop_event)
    monkeypatch.setattr(app, "_macos_microphone_permission_status", lambda: "authorized")
    monkeypatch.setattr(app, "_macos_screen_recording_permission_status", lambda: "authorized")
    monkeypatch.setattr(realtime, "RealtimeTranscriber", FakeCapture)
    monkeypatch.setattr(native_capture, "NativeCaptureHelper", FakeCapture)
    api = app.API()
    monkeypatch.setattr(api, "_caffeinate_start", lambda: None)
    assert api.start_realtime({"capture_mode": capture_mode, "live_asr_provider": "token-plan"})["status"] == "started"
    assert started.wait(2)
    status = api.get_recording_status()
    assert status["state"] == "recording"
    assert status["live_asr"]["provider"] == "token-plan"
    assert api.stop_realtime()["status"] == "stopped"
    assert stopped.wait(2)
    session_id = status["session_id"]
    draft = api.get_recording_draft(session_id)
    assert draft["segments"][0]["text"] == "Synthetic passage"
    assert draft["draft_complete"]
    assert api.get_recording_status()["state"] == "idle"
    with wave.open(AudioAssistService(str(tmp_path)).get_recording_audio_path(session_id), "rb") as recording:
        assert recording.getnframes() == 16000
