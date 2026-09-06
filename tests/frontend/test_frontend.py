"""
Frontend Playwright tests for AudioAssist.

Spins up a local HTTP server serving ui/, injects mock_api.js via
Playwright's add_init_script() so window.pywebview.api is available
before any page script runs, then controls pywebviewready timing manually.

Coverage includes startup timing/history, first-run setup, upload cancellation and
retry, model management, drag/drop queueing, and Summary Agent interactions.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import threading

import pytest
from playwright.sync_api import Page

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
_UI_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "ui"))
_MOCK   = os.path.join(_HERE, "mock_api.js")

# ── HTTP server fixture ────────────────────────────────────────────────────────

class _UIHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the ui/ directory without printing access logs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=_UI_DIR, **kwargs)

    def log_message(self, *args):  # noqa: D401
        pass  # suppress output during tests


class _ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True


@pytest.fixture(scope="module")
def ui_base_url():
    """Start a local HTTP server for ui/ and return its base URL."""
    with _ReuseServer(("127.0.0.1", 0), _UIHandler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}"
        httpd.shutdown()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inject_and_navigate(page: Page, base_url: str, config: dict | None = None) -> None:
    """Inject mock API, navigate to index.html, then fire pywebviewready."""
    if config is not None:
        page.add_init_script(
            script=f"window.__AUDIOASSIST_TEST_CONFIG = {json.dumps(config)};"
        )
    page.add_init_script(path=_MOCK)
    page.goto(f"{base_url}/index.html", wait_until="domcontentloaded")
    page.evaluate("window._mockApi.dispatchReady()")


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_history_loads(page: Page, ui_base_url: str) -> None:
    """
    Scenario 1 — History sidebar is populated after pywebviewready fires.

    mock_api.js returns 2 history items. After App.init() runs via the
    pywebviewready event, History.render() must create 2 .history-item nodes.
    """
    _inject_and_navigate(page, ui_base_url)

    page.wait_for_selector(".history-item", timeout=5000)
    items = page.locator(".history-item").all()

    assert len(items) == 2, f"Expected 2 history items, got {len(items)}"

    # Verify the filenames surfaced by the mock are present in the sidebar
    sidebar_text = page.locator("#history-list").inner_text()
    assert "meeting.mp3" in sidebar_text
    assert "lecture.mp4" in sidebar_text


def test_switch_stops_audio(page: Page, ui_base_url: str) -> None:
    """
    Scenario 2 — Switching session calls Player.stop() on each selection.

    Install a spy on Player.stop before any interaction. Click item 1,
    verify stop was called; reset counter; click item 2, verify stop was
    called again. This confirms _onHistorySelect() calls Player.stop()
    on every session switch regardless of async _render() cascades.
    """
    _inject_and_navigate(page, ui_base_url)
    page.wait_for_selector(".history-item-body", timeout=5000)

    # Install spy before any interaction
    page.evaluate(
        """
        window._stopCallCount = 0;
        var _orig = Player.stop;
        Player.stop = function () {
            window._stopCallCount += 1;
            return _orig.apply(this, arguments);
        };
        """
    )

    # Click item 1 — _onHistorySelect → Player.stop()
    page.locator(".history-item-body").first.click()
    page.wait_for_selector("#player-bar:not([hidden])", timeout=5000)

    count_after_first = page.evaluate("window._stopCallCount")
    assert count_after_first >= 1, (
        f"Expected Player.stop() when selecting item 1, got {count_after_first} calls"
    )

    # Reset and switch to item 2 — must call Player.stop() again
    page.evaluate("window._stopCallCount = 0")
    page.locator(".history-item-body").nth(1).click()

    count_after_switch = page.evaluate("window._stopCallCount")
    assert count_after_switch >= 1, (
        f"Expected Player.stop() on session switch to item 2, got {count_after_switch} calls"
    )


def test_player_load_preserves_same_session_and_resets_on_source_change(
    page: Page, ui_base_url: str
) -> None:
    """A rerender must not restart one session; a new source must stop the old one."""
    _inject_and_navigate(page, ui_base_url)
    result = page.evaluate(
        """
        () => {
          const audio = document.getElementById('audio-el');
          let pauses = 0;
          let loads = 0;
          audio.pause = () => { pauses += 1; };
          audio.load = () => { loads += 1; };

          Player.load('/audio/session-a.wav');
          audio.currentTime = 17;
          Player.load('/audio/session-a.wav');
          const afterSame = {
            pauses,
            loads,
            currentTime: audio.currentTime,
            path: Player.loadedPath,
          };

          Player.load('/audio/session-b.wav');
          return {
            afterSame,
            afterSwitch: {
              pauses,
              loads,
              currentTime: audio.currentTime,
              path: Player.loadedPath,
              display: document.getElementById('player-time').textContent,
            },
          };
        }
        """
    )

    assert result["afterSame"] == {
        "pauses": 1,
        "loads": 1,
        "currentTime": 17,
        "path": "/audio/session-a.wav",
    }
    assert result["afterSwitch"]["pauses"] == 2
    assert result["afterSwitch"]["loads"] == 2
    assert result["afterSwitch"]["currentTime"] == 0
    assert result["afterSwitch"]["path"] == "/audio/session-b.wav"
    assert result["afterSwitch"]["display"].startswith("00:00 / ")


def test_pywebviewready_timing(page: Page, ui_base_url: str) -> None:
    """
    Scenario 3 — App.init() is NOT triggered before pywebviewready.

    Navigate to the page WITHOUT dispatching pywebviewready, wait briefly,
    and confirm the history list is still empty. Then fire the event and
    confirm the history loads.
    """
    # Navigate with the mock in place but do NOT fire pywebviewready yet
    page.add_init_script(path=_MOCK)
    page.goto(f"{ui_base_url}/index.html", wait_until="domcontentloaded")

    # Brief wait — enough time for any erroneous init to run
    page.wait_for_timeout(300)

    # History list must be empty: no .history-item, no known filename
    list_html = page.locator("#history-list").inner_html()
    assert "meeting.mp3" not in list_html, (
        "App.init() appears to have run before pywebviewready; "
        "history loaded prematurely"
    )
    assert page.locator(".history-item").count() == 0, (
        "History items appeared before pywebviewready was dispatched"
    )

    # Now fire the event — App.init() should run and load history
    page.evaluate("window._mockApi.dispatchReady()")
    page.wait_for_selector(".history-item", timeout=5000)

    assert page.locator(".history-item").count() == 2
    assert "meeting.mp3" in page.locator("#history-list").inner_text()


def test_microphone_permission_denied_blocks_start_and_opens_correct_settings(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": False,
            "microphone_permission": "denied",
            "missing_permissions": ["microphone"],
            "reason": "microphone_permission_denied",
        },
    })

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('preflight_capture').length === 1")

    assert page.evaluate("_mockApi.calls('preflight_capture')[0].args") == ["mic"]
    assert page.evaluate("_mockApi.calls('start_realtime').length") == 0
    notice = page.locator("#capture-perm-notice")
    assert notice.is_visible()
    assert "麦克风" in page.locator("#capture-perm-text").inner_text()

    assert page.locator("#btn-open-microphone-privacy").is_visible()
    assert page.locator("#btn-open-screen-privacy").is_hidden()
    page.locator("#btn-open-microphone-privacy").click()
    page.wait_for_function("_mockApi.calls('open_privacy_settings').length === 1")
    assert page.evaluate("_mockApi.calls('open_privacy_settings')[0].args") == ["microphone"]


def test_undetermined_microphone_permission_can_cancel_without_starting_session(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "microphone_permission": "not_determined",
        },
        "microphonePermissionDelayMs": 500,
    })

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('request_microphone_permission').length === 1")

    assert "Cancel Permission Wait" in page.locator("#btn-start-recording").inner_text()
    assert page.locator("#btn-start-recording").is_enabled()
    assert page.locator("#realtime-status").inner_text() == "Waiting for microphone permission…"

    page.locator("#btn-start-recording").click()
    assert page.locator("#realtime-status").inner_text() == "Permission request canceled"
    page.wait_for_timeout(600)
    assert page.evaluate("_mockApi.calls('start_realtime').length") == 0


def test_microphone_permission_authorized_before_realtime_start(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "microphone_permission": "not_determined",
        },
        "microphonePermission": {"status": "authorized", "authorized": True},
    })

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")

    assert page.evaluate("_mockApi.calls('request_microphone_permission').length") == 1
    calls = page.evaluate("_mockApi.state.calls.map(call => call.name)")
    assert calls.index("request_microphone_permission") < calls.index("start_realtime")


def test_mic_permission_race_from_shared_backend_restores_ui(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "microphone_permission": "authorized",
        },
        "realtimeStart": {
            "status": "permission_required",
            "microphone_permission": "denied",
        },
    })

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")
    page.wait_for_function("!document.querySelector('#btn-start-recording').disabled")

    assert page.locator("#realtime-status").inner_text() == "Microphone unavailable"
    assert page.locator("#capture-perm-notice").is_visible()


def test_mic_mode_never_requests_screen_recording_permission(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "missing_permissions": [],
            "microphone_permission": "authorized",
        },
    })

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")

    assert page.evaluate("_mockApi.calls('request_microphone_permission').length") == 0
    assert page.evaluate("_mockApi.calls('request_screen_recording_permission').length") == 0
    assert page.evaluate("_mockApi.calls('start_realtime')[0].args[0].capture_mode") == "mic"


def test_system_mode_requests_only_screen_and_system_audio_permission(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "missing_permissions": ["screen_recording"],
            "screen_recording_permission": "required",
        },
        "screenPermission": {"status": "authorized", "authorized": True},
    })

    page.locator(".btn-mode[data-mode='system']").click()
    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")

    assert page.evaluate("_mockApi.calls('request_microphone_permission').length") == 0
    assert page.evaluate("_mockApi.calls('request_screen_recording_permission').length") == 1
    assert page.evaluate("_mockApi.calls('start_realtime')[0].args[0].capture_mode") == "system"


def test_system_permission_wait_can_cancel_without_starting_session(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "missing_permissions": ["screen_recording"],
            "screen_recording_permission": "required",
        },
        "screenPermissionDelayMs": 500,
    })

    page.locator(".btn-mode[data-mode='system']").click()
    page.locator("#btn-start-recording").click()
    page.wait_for_function(
        "_mockApi.calls('request_screen_recording_permission').length === 1"
    )
    assert page.locator("#realtime-status").inner_text() == (
        "Waiting for Screen & System Audio permission…"
    )

    page.locator("#btn-start-recording").click()
    assert page.locator("#realtime-status").inner_text() == "Permission request canceled"
    page.wait_for_timeout(600)
    assert page.evaluate("_mockApi.calls('start_realtime').length") == 0


def test_system_permission_in_progress_restores_controls(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "missing_permissions": ["screen_recording"],
            "screen_recording_permission": "required",
        },
        "screenPermission": {"status": "in_progress", "authorized": False},
    })

    page.locator(".btn-mode[data-mode='system']").click()
    page.locator("#btn-start-recording").click()
    page.wait_for_function("!document.querySelector('#btn-start-recording').disabled")

    assert page.locator("#realtime-status").inner_text() == "Permission request in progress"
    assert page.locator("#btn-open-screen-privacy").is_visible()
    assert page.evaluate("_mockApi.calls('start_realtime').length") == 0


def test_mix_mode_requests_microphone_then_screen_permission(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "missing_permissions": ["microphone", "screen_recording"],
            "microphone_permission": "not_determined",
            "screen_recording_permission": "required",
        },
        "microphonePermission": {"status": "authorized", "authorized": True},
        "screenPermission": {"status": "authorized", "authorized": True},
    })

    page.locator(".btn-mode[data-mode='mix']").click()
    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")

    calls = page.evaluate("_mockApi.state.calls.map(call => call.name)")
    assert calls.index("request_microphone_permission") < calls.index("request_screen_recording_permission")
    assert calls.index("request_screen_recording_permission") < calls.index("start_realtime")
    assert page.evaluate("_mockApi.calls('start_realtime')[0].args[0].capture_mode") == "mix"


def test_mix_mode_lists_both_missing_permissions_with_separate_settings_actions(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "reason": "capture_permissions_required",
            "missing_permissions": ["microphone", "screen_recording"],
            "microphone_permission": "denied",
            "screen_recording_permission": "required",
        },
    })

    page.locator(".btn-mode[data-mode='mix']").click()
    page.locator("#btn-start-recording").click()
    page.wait_for_function("!document.querySelector('#btn-start-recording').disabled")

    text = page.locator("#capture-perm-text").inner_text()
    assert "麦克风权限" in text
    assert "屏幕与系统音频录制权限" in text
    assert page.locator("#btn-open-microphone-privacy").is_visible()
    assert page.locator("#btn-open-screen-privacy").is_visible()
    assert page.evaluate("_mockApi.calls('start_realtime').length") == 0


@pytest.mark.parametrize("status", ["denied", "timeout", "unknown"])
def test_microphone_permission_failure_resets_ui_without_starting(
    page: Page, ui_base_url: str, status: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "preflight": {
            "supported": True,
            "microphone_permission": "not_determined",
        },
        "microphonePermission": {"status": status, "authorized": False},
    })

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('request_microphone_permission').length === 1")
    page.wait_for_function("!document.querySelector('#btn-start-recording').disabled")

    assert page.evaluate("_mockApi.calls('start_realtime').length") == 0
    assert page.locator("#capture-perm-notice").is_visible()
    assert page.locator("#btn-start-recording").inner_text() == "🎙 Start Recording"
    expected = "Permission timed out" if status == "timeout" else "Microphone unavailable"
    assert page.locator("#realtime-status").inner_text() == expected
    assert not page.locator("#btn-open-microphone-privacy").is_visible()
    if status == "unknown":
        assert "无法发起麦克风授权请求" in page.locator("#capture-perm-notice").inner_text()


def test_first_run_setup_download_progress_and_error(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "setup": {"asr_ready": False, "diarizer_ready": False},
        "history": [],
    })

    page.locator("#setup-panel").wait_for(state="visible")
    assert page.locator("#setup-asr-badge").inner_text() == "Not downloaded"
    assert page.locator("#setup-diarizer-badge").inner_text() == "Not downloaded"

    page.locator("#btn-setup-asr").click()
    assert page.evaluate("_mockApi.calls('download_model')")[-1]["args"] == ["qwen3-asr-1.7b"]
    assert page.locator("#setup-asr-progress").is_visible()

    page.evaluate("_mockApi.setSetup({asr_ready: true})")
    page.evaluate("onModelDownloadProgress('qwen3-asr-1.7b', 1.0)")
    page.wait_for_function("document.querySelector('#setup-asr-badge').textContent.includes('Ready')")
    assert page.locator("#btn-setup-asr").is_disabled()

    page.locator("#btn-setup-diarizer").click()
    page.evaluate("onModelDownloadError('pyannote-diarization-community-1', 'network unavailable')")
    assert page.locator("#setup-diarizer-badge").inner_text() == "Error"
    assert page.locator("#btn-setup-diarizer").is_enabled()


def test_upload_error_retry_and_cancel(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "history": [],
        "selectedFiles": ["/fixtures/interview.wav"],
        "transcribeIds": ["attempt-1", "attempt-2"],
    })

    page.locator("#btn-upload").click()
    page.locator("#progress-panel").wait_for(state="visible")
    first = page.evaluate("_mockApi.calls('transcribe')")[0]
    assert first["args"][0] == "/fixtures/interview.wav"
    assert page.locator("#progress-filename").inner_text() == "interview.wav"

    page.evaluate("onTranscribeError('attempt-1', 'decoder failed')")
    page.locator("#error-panel").wait_for(state="visible")
    assert page.locator("#error-msg").inner_text() == "decoder failed"

    page.locator("#btn-retry").click()
    page.wait_for_function("_mockApi.calls('transcribe').length === 2")
    second = page.evaluate("_mockApi.calls('transcribe')")[1]
    assert second["args"][0] == "/fixtures/interview.wav"
    assert page.locator("#progress-filename").inner_text() == "interview.wav"

    page.locator("#btn-cancel-transcribe").click()
    page.wait_for_function("_mockApi.calls('cancel_transcription').length === 1")
    assert page.evaluate("_mockApi.calls('cancel_transcription')")[0]["args"] == ["attempt-2"]
    page.evaluate("onTranscribeCancel('attempt-2')")
    page.locator("#empty-hint").wait_for(state="visible")
    assert page.locator(".history-empty").inner_text() == "No recordings yet"


def test_model_library_download_delete_and_selector_refresh(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.on("dialog", lambda dialog: dialog.accept())

    page.locator("#btn-models").click()
    page.locator(".model-item").first.wait_for(state="visible")
    assert page.locator(".model-item").count() == 3
    assert [text.lower() for text in page.locator(".model-role-header").all_inner_texts()] == [
        "asr models", "aligner models", "diarizer models",
    ]

    aligner = page.locator('[data-model-id="qwen3-forced-aligner-0.6b"]')
    aligner.locator(".model-btn-download").click()
    assert page.evaluate("_mockApi.calls('download_model')")[-1]["args"] == [
        "qwen3-forced-aligner-0.6b"
    ]
    page.evaluate("onModelDownloadProgress('qwen3-forced-aligner-0.6b', 1.0)")
    assert "Downloaded" in aligner.locator(".model-item-badge").inner_text()
    assert aligner.locator(".model-btn-download").is_disabled()

    asr = page.locator('[data-model-id="qwen3-asr-1.7b"]')
    asr.locator(".model-btn-delete").click()
    page.wait_for_function("_mockApi.calls('delete_model').length === 1")
    assert asr.locator(".model-item-badge").inner_text() == "Not downloaded"
    page.wait_for_function("document.querySelector('#sel-engine').value === ''")


def test_drag_drop_queue_runs_one_file_at_a_time(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "history": [],
        "transcribeIds": ["drop-1", "drop-2"],
    })

    page.evaluate(
        "document.querySelector('#center-panel').dispatchEvent("
        "new DragEvent('dragover', {bubbles: true, cancelable: true}))"
    )
    assert page.locator("#center-panel").evaluate("el => el.classList.contains('drag-over')")
    page.evaluate(
        """
        const transfer = new DataTransfer();
        transfer.items.add(new File(['one'], 'first.wav', {type: 'audio/wav'}));
        transfer.items.add(new File(['two'], 'second.mp3', {type: 'audio/mpeg'}));
        document.querySelector('#center-panel').dispatchEvent(
          new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer})
        );
        """
    )
    page.wait_for_function("_mockApi.calls('transcribe').length === 1")
    assert page.evaluate("_mockApi.calls('transcribe')")[0]["args"][0] == "first.wav"
    assert not page.locator("#center-panel").evaluate("el => el.classList.contains('drag-over')")

    page.evaluate("onTranscribeComplete('drop-1', '/mock/transcript.json', false)")
    page.wait_for_function("_mockApi.calls('transcribe').length === 2")
    assert page.evaluate("_mockApi.calls('transcribe')")[1]["args"][0] == "second.mp3"
    assert page.locator(".history-item").count() == 2


def test_summary_agent_stream_tool_error_recovery_and_clear(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "agentTurns": [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
        ],
    })
    page.locator(".history-item", has_text="meeting.mp3").locator(".history-item-body").click()
    page.wait_for_function("_mockApi.calls('get_agent_session').length >= 1")
    assert page.locator("#agent-messages .chat-bubble").count() == 2

    page.locator("#agent-input").fill("列出行动项")
    page.locator("#btn-agent-send").click()
    page.wait_for_function("_mockApi.calls('start_agent_turn').length === 1")
    call = page.evaluate("_mockApi.calls('start_agent_turn')")[0]
    assert call["args"] == ["job-aaa", "列出行动项"]
    assert page.locator("#btn-agent-send").is_disabled()

    page.evaluate("onAgentToolStart('job-aaa', 'save_summary')")
    assert page.locator("#agent-tool-status").is_visible()
    page.evaluate("onAgentChunk('job-aaa', '**完成** <script>window.bad = true</script>')")
    assert page.locator("#agent-messages strong").last.inner_text() == "完成"
    assert page.locator("#agent-messages script").count() == 0
    assert page.evaluate("window.bad") is None
    page.evaluate("onAgentToolEnd('job-aaa', 'save_summary')")
    page.evaluate("onAgentComplete('job-aaa', '**完成**')")
    assert page.locator("#btn-agent-send").is_enabled()
    assert page.locator("#agent-tool-status").is_hidden()

    page.locator("#agent-input").fill("再试一次")
    page.locator("#btn-agent-send").click()
    page.evaluate("onAgentError('job-aaa', 'tool timeout')")
    assert "tool timeout" in page.locator(".chat-bubble-error").inner_text()
    assert page.locator("#btn-agent-send").is_enabled()

    page.locator("#btn-agent-clear").click()
    page.wait_for_function("_mockApi.calls('clear_agent_session').length === 1")
    assert page.locator("#agent-messages .chat-bubble").count() == 0
