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
from playwright.sync_api import Page, expect

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
_UI_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "ui"))
_MOCK   = os.path.join(_HERE, "mock_api.js")


def test_ui_sources_do_not_use_blocking_alerts() -> None:
    for filename in ("app.js", "summary.js"):
        source = open(os.path.join(_UI_DIR, "js", filename), encoding="utf-8").read()
        assert "alert(" not in source

# ── HTTP server fixture ────────────────────────────────────────────────────────

class _UIHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the ui/ directory without printing access logs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=_UI_DIR, **kwargs)

    def log_message(self, *args):  # noqa: D401
        pass  # suppress output during tests


class _ReuseServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


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
    """Inject the mock API, load all scripts, then initialize the app directly.

    The production bootstrap waits for PyWebView's asynchronous ready event. In
    browser tests the API is already injected before navigation, so calling the
    documented test entry point directly avoids losing a synthetic event while
    Chromium is still finishing script evaluation on a busy host.
    """
    if config is not None:
        page.add_init_script(
            script=f"window.__AUDIOASSIST_TEST_CONFIG = {json.dumps(config)};"
        )
    page.add_init_script(path=_MOCK)
    page.goto(f"{base_url}/index.html", wait_until="networkidle")
    page.wait_for_function(
        "typeof App !== 'undefined' && typeof App.init === 'function' && "
        "typeof History !== 'undefined' && typeof History.init === 'function'"
    )
    page.evaluate("App.init()")


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


def test_reorganized_controls_keep_their_existing_functions(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url)

    assert page.locator("#history-footer #btn-start-recording").count() == 1
    assert page.locator("#history-footer #btn-upload").count() == 1
    assert page.locator(".history-manage #btn-models").count() == 1
    assert page.locator(".history-manage #btn-summary-config").count() == 1
    assert page.locator("#toolbar #sel-engine").count() == 1
    assert page.locator("#toolbar #btn-summary-toggle").count() == 1

    page.locator(".history-item", has_text="meeting.mp3").locator(
        ".history-item-body"
    ).click()
    expect(page.locator("#transcript-title")).to_have_text("meeting.mp3")
    expect(page.locator("#transcript-header #btn-export-transcript")).to_be_visible()
    expect(page.locator("#center-panel > #player-bar")).to_be_visible()


def test_high_accuracy_action_restores_from_history_and_remains_retryable(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [
            {
                "job_id": "quick-draft", "filename": "Realtime draft", "type": "file",
                "status": "done", "date": "2026-09-09T12:00:00",
                "audio_path": "/audio/realtime.wav", "refine_available": True,
            },
            {
                "job_id": "full-file", "filename": "Full transcript", "type": "file",
                "status": "done", "date": "2026-09-09T11:00:00",
                "audio_path": "/audio/file.m4a", "refine_available": False,
            },
        ],
    })

    page.locator(".history-item", has_text="Realtime draft").locator(
        ".history-item-body"
    ).click()
    expect(page.locator("#btn-high-accuracy")).to_be_visible()
    page.locator("#btn-high-accuracy").click()
    page.wait_for_function("_mockApi.calls('refine').length === 1")
    assert page.evaluate("_mockApi.calls('refine')")[0]["args"] == [
        "quick-draft", {"model_id": "qwen3-asr-1.7b"},
    ]
    page.evaluate("onTranscribeProgress('quick-draft', 0.42, 'Running ASR')")
    expect(page.locator("#refine-hint")).to_contain_text("42% · Running ASR")
    page.evaluate("onTranscribeRefineError('quick-draft', 'engine crashed')")
    expect(page.locator("#btn-high-accuracy")).to_be_visible()
    expect(page.locator("#toast")).to_contain_text("engine crashed")

    page.locator(".history-item", has_text="Full transcript").locator(
        ".history-item-body"
    ).click()
    expect(page.locator("#btn-high-accuracy")).to_be_hidden()


def test_recovered_raw_recording_can_start_full_transcription_by_session_id(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "recovered-job", "filename": "Recovered recording",
            "type": "realtime", "date": "2026-09-22T10:00:00",
            "audio_path": "/audio/realtime_recording_recovered-job.wav",
        }],
    })

    page.locator(".history-item-body").click()
    expect(page.locator("#btn-transcribe-recording")).to_be_visible()
    page.locator("#btn-transcribe-recording").click()
    page.wait_for_function("_mockApi.calls('transcribe_recording').length === 1")

    assert page.evaluate("_mockApi.calls('transcribe_recording')")[0]["args"] == [
        "recovered-job", {"model_id": "qwen3-asr-1.7b", "hf_token": None, "num_speakers": None},
    ]
    expect(page.locator("#progress-panel")).to_be_visible()
    expect(page.locator("#progress-filename")).to_have_text("Recovered recording")


def test_history_reads_all_saved_draft_pages_without_mixing_recordings(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [
            {"job_id": "old", "filename": "Old recording", "type": "realtime",
             "audio_path": "/audio/old.wav"},
            {"job_id": "other", "filename": "Other recording", "type": "realtime",
             "audio_path": "/audio/other.wav"},
        ],
        "recordingDrafts": {
            "old": [
                {"seq": i, "start": i, "end": i + 1, "text": f"旧录音第 {i} 条"}
                for i in range(1, 503)
            ],
            "other": [{"seq": 1, "start": 0, "end": 1, "text": "另一场"}],
        },
    })
    page.get_by_text("Old recording", exact=True).click()
    rows = page.locator("#recording-draft-list .realtime-row")
    expect(rows).to_have_count(502)
    expect(rows.last).to_have_text("[08:22] 旧录音第 502 条")
    assert [call["args"] for call in page.evaluate("_mockApi.calls('get_recording_draft')")] == [
        ["old", 0], ["old", 500],
    ]
    page.get_by_text("Other recording", exact=True).click()
    expect(rows).to_have_count(1)
    expect(rows.first).to_have_text("[00:00] 另一场")
    page.get_by_text("Old recording", exact=True).click()
    expect(rows).to_have_count(502)


@pytest.mark.parametrize("outcome", ["cancel", "error"])
def test_stopped_recording_keeps_saved_draft_after_transcription_stops(
    page: Page, ui_base_url: str, outcome: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [],
        "recordingDrafts": {
            "same-recording": [{"seq": 1, "start": 1, "end": 2, "text": "录音原稿"}],
        },
    })
    page.evaluate("onRealtimeStarted('same-recording', '/audio/realtime_recording_same-recording.wav')")
    page.evaluate("onRealtimeStopped()")
    expect(page.locator("#progress-panel")).to_be_visible()
    expect(page.locator("#recording-draft-list")).to_contain_text("录音原稿")
    assert page.evaluate("_mockApi.calls('transcribe_recording')[0].args[0]") == "same-recording"

    if outcome == "cancel":
        page.locator("#btn-cancel-transcribe").click()
        assert page.evaluate("_mockApi.calls('cancel_transcription')[0].args[0]") == "same-recording"
        page.evaluate("onTranscribeCancel('same-recording')")
        expect(page.locator("#recording-task-note")).to_have_text("转写已取消")
    else:
        page.evaluate("onTranscribeError('same-recording', 'worker failed', 'mlx')")
        expect(page.locator("#recording-task-note")).to_contain_text("worker failed")

    expect(page.locator("#progress-panel")).to_be_hidden()
    expect(page.locator(".history-item")).to_have_count(1)
    expect(page.locator("#recording-draft-list")).to_be_visible()
    expect(page.locator("#recording-draft-list")).to_contain_text("录音原稿")
    page.locator("#btn-transcribe-recording").click()
    page.wait_for_function("_mockApi.calls('transcribe_recording').length === 2")
    page.evaluate("onTranscribeComplete('same-recording', '/mock/transcript.json', false, false)")
    expect(page.locator("#recording-draft-panel")).to_be_hidden()
    expect(page.locator("#transcript-list")).to_contain_text("Hello world.")
    expect(page.locator("#btn-high-accuracy")).to_be_hidden()


def test_interrupted_history_offers_resume_alongside_saved_draft(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{"job_id": "saved-draft", "filename": "Interrupted recording",
                     "type": "file", "status": "interrupted", "audio_path": "/audio/raw.wav"}],
        "recordingDrafts": {"saved-draft": [
            {"seq": 1, "start": 0, "end": 1, "text": "继续显示草稿"},
        ]},
    })
    page.get_by_text("Interrupted recording", exact=True).click()
    expect(page.locator("#recording-draft-list")).to_be_visible()
    page.locator("#btn-retry").click()
    page.wait_for_function("_mockApi.calls('resume_transcription').length === 1")
    page.evaluate("onTranscribeError('saved-draft', 'worker failed')")
    expect(page.locator("#error-msg")).to_contain_text("worker failed")
    expect(page.locator("#recording-draft-list")).to_be_visible()
    expect(page.locator("#recording-draft-list")).to_contain_text("继续显示草稿")


def test_segment_warning_keeps_recording_controls_and_later_text(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={"history": []})
    page.evaluate("onRealtimeStarted('active', '/audio/active.wav')")
    page.evaluate("onRealtimeSegmentWarning('old', 0, 1)")
    expect(page.locator("#realtime-segment-warning")).to_be_hidden()

    page.evaluate("onRealtimeResult({text: '识别失败前', start: 0, end: 1})")
    page.evaluate("onRealtimeSegmentWarning('active', 1, 3)")
    page.evaluate("onRealtimeResult({text: '识别失败后', start: 3, end: 4})")
    expect(page.locator("#realtime-segment-warning")).to_contain_text("00:01–00:03")
    expect(page.locator("#realtime-status")).to_contain_text("Recording")
    expect(page.locator("#rc-timer")).not_to_have_text("00:00")
    expect(page.locator("#realtime-list .realtime-row")).to_have_count(2)
    expect(page.locator("#rc-btn-pause")).to_be_enabled()
    expect(page.locator(".history-item")).to_have_count(1)
    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('stop_realtime').length === 1")


def test_recovered_recording_start_failure_remains_retryable(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "recovered-job", "filename": "Recovered recording",
            "type": "realtime", "date": "2026-09-22T10:00:00",
            "audio_path": "/audio/realtime_recording_recovered-job.wav",
        }],
    })
    page.evaluate(
        "window.pywebview.api.transcribe_recording = async () => "
        "({error: 'Recording audio not found', code: 'session_not_found'})"
    )

    page.locator(".history-item-body").click()
    page.locator("#btn-transcribe-recording").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('not found')")

    expect(page.locator("#btn-transcribe-recording")).to_be_visible()
    expect(page.locator(".history-item")).to_have_count(1)


def test_recovered_recording_does_not_start_during_another_transcription(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [
            {
                "job_id": "busy-job", "filename": "Busy file", "type": "file",
                "status": "transcribing", "date": "2026-09-22T10:01:00",
                "audio_path": "/audio/busy.wav",
            },
            {
                "job_id": "recovered-job", "filename": "Recovered recording",
                "type": "realtime", "date": "2026-09-22T10:00:00",
                "audio_path": "/audio/realtime_recording_recovered-job.wav",
            },
        ],
    })

    page.locator(".history-item", has_text="Recovered recording").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-transcribe-recording").click()

    expect(page.locator("#toast")).to_contain_text("transcription is already in progress")
    assert page.evaluate("_mockApi.calls('transcribe_recording').length") == 0
    expect(page.locator("#btn-transcribe-recording")).to_be_visible()


def test_recovered_recording_does_not_start_during_active_recording(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "recovered-job", "filename": "Recovered recording",
            "type": "realtime", "date": "2026-09-22T10:00:00",
            "audio_path": "/audio/realtime_recording_recovered-job.wav",
        }],
    })
    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")
    page.evaluate("onRealtimeStarted('live-job', '/audio/live.wav')")

    page.locator(".history-item", has_text="Recovered recording").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-transcribe-recording").click()

    expect(page.locator("#toast")).to_contain_text("recording is in progress")
    assert page.evaluate("_mockApi.calls('transcribe_recording').length") == 0


def test_recovered_recording_does_not_start_while_recording_is_loading(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "recovered-job", "filename": "Recovered recording",
            "type": "realtime", "date": "2026-09-22T10:00:00",
            "audio_path": "/audio/realtime_recording_recovered-job.wav",
        }],
    })
    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('start_realtime').length === 1")

    page.locator(".history-item", has_text="Recovered recording").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-transcribe-recording").click()

    expect(page.locator("#toast")).to_contain_text("recording is in progress")
    assert page.evaluate("_mockApi.calls('transcribe_recording').length") == 0


def test_recovered_recording_does_not_start_during_high_accuracy_refine(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [
            {
                "job_id": "refine-job", "filename": "Refining file", "type": "file",
                "status": "refining", "date": "2026-09-22T10:01:00",
                "audio_path": "/audio/refining.wav",
            },
            {
                "job_id": "recovered-job", "filename": "Recovered recording",
                "type": "realtime", "date": "2026-09-22T10:00:00",
                "audio_path": "/audio/realtime_recording_recovered-job.wav",
            },
        ],
    })

    page.locator(".history-item", has_text="Recovered recording").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-transcribe-recording").click()

    expect(page.locator("#toast")).to_contain_text("transcription is already in progress")
    assert page.evaluate("_mockApi.calls('transcribe_recording').length") == 0


def test_canceling_recovered_transcription_for_recording_restores_raw_action(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "recovered-job", "filename": "Recovered recording",
            "type": "realtime", "date": "2026-09-22T10:00:00",
            "audio_path": "/audio/realtime_recording_recovered-job.wav",
        }],
    })
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(".history-item-body").click()
    page.locator("#btn-transcribe-recording").click()
    page.wait_for_function("_mockApi.calls('transcribe_recording').length === 1")

    page.locator("#btn-start-recording").click()
    page.wait_for_function("_mockApi.calls('cancel_transcription').length === 1")
    page.evaluate("onTranscribeCancel('recovered-job')")
    page.locator(".history-item", has_text="Recovered recording").locator(
        ".history-item-body"
    ).click()

    expect(page.locator("#btn-transcribe-recording")).to_be_visible()
    assert page.evaluate("_mockApi.calls('resume_transcription').length") == 0


def test_high_accuracy_cancel_keeps_draft_visible_and_retryable(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "quick-draft", "filename": "Realtime draft", "type": "file",
            "status": "done", "date": "2026-09-09T12:00:00",
            "audio_path": "/audio/realtime.wav", "refine_available": True,
        }],
    })
    page.locator(".history-item-body").click()
    page.locator("#btn-high-accuracy").click()
    page.locator("#btn-cancel-refine").click()
    assert page.evaluate("_mockApi.calls('cancel_transcription')[0].args[0]") == "quick-draft"
    page.evaluate("onTranscribeCancel('quick-draft')")

    expect(page.locator(".history-item")).to_have_count(1)
    expect(page.locator("#btn-high-accuracy")).to_be_visible()
    expect(page.locator("#transcript-list")).to_contain_text("Hello world.")


def test_high_accuracy_start_failure_restores_the_clicked_session_after_switch(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [
            {
                "job_id": "quick-draft", "filename": "Realtime draft", "type": "file",
                "status": "done", "date": "2026-09-09T12:00:00",
                "audio_path": "/audio/realtime.wav", "refine_available": True,
            },
            {
                "job_id": "full-file", "filename": "Full transcript", "type": "file",
                "status": "done", "date": "2026-09-09T11:00:00",
                "audio_path": "/audio/file.m4a", "refine_available": False,
            },
        ],
    })
    page.evaluate(
        "() => { window.pywebview.api.refine = async () => { "
        "await new Promise(resolve => setTimeout(resolve, 100)); "
        "throw new Error('start failed'); }; }"
    )
    page.locator(".history-item", has_text="Realtime draft").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-high-accuracy").click()
    page.locator(".history-item", has_text="Full transcript").locator(
        ".history-item-body"
    ).click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('start failed')")
    expect(page.locator("#btn-high-accuracy")).to_be_hidden()

    page.locator(".history-item", has_text="Realtime draft").locator(
        ".history-item-body"
    ).click()
    expect(page.locator("#btn-high-accuracy")).to_be_visible()


def test_minimum_window_keeps_transcript_and_summary_usable(
    page: Page, ui_base_url: str
) -> None:
    page.set_viewport_size({"width": 900, "height": 600})
    _inject_and_navigate(page, ui_base_url)
    page.locator(".history-item", has_text="meeting.mp3").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-summary-toggle").click()

    center = page.locator("#center-panel").bounding_box()
    assert center is not None and center["width"] >= 300
    expect(page.locator("#transcript-title")).to_be_visible()
    expect(page.locator("#btn-export-transcript")).to_be_visible()
    expect(page.locator("#summary-panel")).to_be_visible()


def test_summary_defaults_closed_and_session_switch_preserves_visibility(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url)
    first = page.locator(".history-item", has_text="meeting.mp3").locator(
        ".history-item-body"
    )
    second = page.locator(".history-item", has_text="lecture.mp4").locator(
        ".history-item-body"
    )

    first.click()
    expect(page.locator("#summary-panel")).to_be_hidden()
    expect(page.locator("#btn-summary-toggle")).to_have_attribute("aria-expanded", "false")

    page.locator("#btn-summary-toggle").click()
    expect(page.locator("#summary-panel")).to_be_visible()
    expect(page.locator("#btn-summary-toggle")).to_have_attribute("aria-expanded", "true")
    second.click()
    expect(page.locator("#summary-panel")).to_be_visible()

    page.locator("#btn-summary-toggle").click()
    first.click()
    expect(page.locator("#summary-panel")).to_be_hidden()


def test_theme_setting_applies_immediately_and_persists(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.evaluate("Theme.set('system')")
    page.locator("#btn-summary-config").click()
    page.locator("#cfg-theme").select_option("dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.locator("html")).to_have_attribute("data-theme-setting", "dark")

    page.reload()
    page.evaluate("window._mockApi.dispatchReady()")
    page.locator("#btn-summary-config").click()
    expect(page.locator("#cfg-theme")).to_have_value("dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.evaluate("Theme.set('system')")


def test_speaker_menu_distinguishes_single_correction_and_bulk_rename(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.evaluate(
        """
        window.pywebview.api.get_transcript = async jobId => ({
          job_id: jobId, audio: '/audio/test.mp3', language: 'zh', segments: [
            {speaker:'SPEAKER_00',start:0,end:2,text:'第一段',words:[]},
            {speaker:'SPEAKER_00',start:2,end:4,text:'第二段',words:[]},
            {speaker:'SPEAKER_01',start:4,end:6,text:'第三段',words:[]},
          ]
        });
        """
    )
    page.locator(".history-item", has_text="meeting.mp3").locator(
        ".history-item-body"
    ).click()
    expect(page.locator(".transcript-row")).to_have_count(3)

    page.locator(".row-speaker").first.click()
    expect(page.locator(".speaker-menu")).to_contain_text("仅修改这一段")
    expect(page.locator(".speaker-menu")).to_contain_text("统一重命名 “SPEAKER_00”（2 段）")
    page.locator(".speaker-menu-item", has_text="统一重命名").click()
    page.locator(".row-speaker-input").fill("张总")
    page.locator(".row-speaker-input").press("Enter")
    page.wait_for_function("_mockApi.calls('rename_speaker').length === 1")
    assert page.evaluate("_mockApi.calls('rename_speaker')")[0]["args"] == [
        "job-aaa", "SPEAKER_00", "张总",
    ]
    expect(page.locator(".row-speaker", has_text="张总")).to_have_count(2)

    page.locator(".row-speaker").last.click()
    page.locator(".speaker-menu-item", has_text="仅修改这一段").click()
    page.locator(".row-speaker-input").fill("李总")
    page.locator(".row-speaker-input").press("Enter")
    page.wait_for_function("_mockApi.calls('rename_segment_speaker').length === 1")
    assert page.evaluate("_mockApi.calls('rename_segment_speaker')")[0]["args"] == [
        "job-aaa", 2, "李总",
    ]
    expect(page.locator(".row-speaker", has_text="李总")).to_have_count(1)
    expect(page.locator(".row-speaker", has_text="张总")).to_have_count(2)


def test_qwen_default_persists_across_page_reload(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.locator('#btn-summary-config').click()
    expect(page.locator('#cfg-qwen-backend')).to_be_enabled()
    page.locator('#cfg-qwen-backend').select_option('mlx')
    page.locator('#btn-save-qwen-backend').click()
    expect(page.locator('#cfg-qwen-status')).to_contain_text('已保存：mlx')
    page.reload()
    page.evaluate('window._mockApi.dispatchReady()')
    page.locator('#btn-summary-config').click()
    expect(page.locator('#cfg-qwen-backend')).to_have_value('mlx')
    expect(page.locator('#cfg-qwen-help')).to_contain_text('不自动回退')


def test_settings_show_runtime_version_and_app_path(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.locator('#btn-summary-config').click()

    identity = page.locator('#cfg-runtime-identity')
    expect(identity).to_contain_text('Version 2.2.0')
    expect(identity).to_contain_text('packaged-app')
    expect(identity).to_contain_text('/mock/AudioAssist.app')


def test_qwen_save_failure_visible_and_retry_possible(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.evaluate("window.pywebview.api.save_qwen_backend_settings = async () => ({ok:false,error:'disk full'})")
    page.locator('#btn-summary-config').click()
    expect(page.locator('#cfg-qwen-backend')).to_be_enabled()
    page.locator('#cfg-qwen-backend').select_option('pytorch-mps')
    page.locator('#btn-save-qwen-backend').click()
    expect(page.locator('#cfg-qwen-status')).to_contain_text('disk full')
    expect(page.locator('#btn-save-qwen-backend')).to_be_enabled()


def test_qwen_unavailable_option_and_environment_override_visible(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.evaluate("""window.pywebview.api.get_qwen_backend_settings = async () => ({
      saved:'mlx',effective:'pytorch-cpu',environment_override:true,options:[
        {value:'mlx',label:'MLX',available:false,reason:'unsupported hardware'},
        {value:'pytorch-cpu',label:'CPU',available:true,reason:''}]})""")
    page.locator('#btn-summary-config').click()
    expect(page.locator('#cfg-qwen-status')).to_contain_text('环境变量覆盖为 pytorch-cpu')
    expect(page.locator('#cfg-qwen-backend option[value="mlx"]')).to_be_disabled()
    expect(page.locator('#btn-save-qwen-backend')).to_be_disabled()
    page.locator('#cfg-qwen-backend').select_option('pytorch-cpu')
    expect(page.locator('#btn-save-qwen-backend')).to_be_enabled()


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


def test_seek_playback_failure_shows_actionable_toast(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.locator(".history-item", has_text="meeting.mp3").locator(
        ".history-item-body"
    ).click()
    page.locator("#transcript-list .transcript-row").wait_for(state="visible")
    page.evaluate(
        """
        document.querySelector('#audio-el').play = () =>
          Promise.reject(new DOMException('media missing', 'NotSupportedError'));
        void 0;
        """
    )

    page.locator("#transcript-list .transcript-row").first.click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('still exists and is readable')"
    )

    assert page.locator("#toast").is_visible()


def test_selected_session_views_never_leak_player_or_empty_state(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, config={
        "history": [
            {
                "job_id": "job-done", "filename": "Done", "type": "file",
                "status": "done", "date": "2026-09-07T01:00:00",
                "audio_path": "/audio/done.wav",
            },
            {
                "job_id": "job-interrupted", "filename": "Interrupted",
                "type": "file", "status": "interrupted",
                "date": "2026-09-07T00:00:00", "audio_path": "/audio/interrupted.wav",
            },
        ],
    })

    assert page.locator("#empty-hint").is_visible()
    assert page.locator("#player-bar").is_hidden()

    page.locator(".history-item", has_text="Done").locator(".history-item-body").click()
    page.locator("#player-bar").wait_for(state="visible")
    assert page.locator("#empty-hint").is_hidden()
    assert page.locator("#error-panel").is_hidden()

    page.locator(".history-item", has_text="Interrupted").locator(
        ".history-item-body"
    ).click()
    page.locator("#error-panel").wait_for(state="visible")
    assert page.locator("#player-bar").is_hidden()
    assert page.locator("#empty-hint").is_hidden()

    page.on("dialog", lambda dialog: dialog.accept())
    interrupted = page.locator(".history-item", has_text="Interrupted")
    interrupted.hover()
    interrupted.locator('button[title="Delete"]').click()
    done = page.locator(".history-item", has_text="Done")
    done.hover()
    done.locator('button[title="Delete"]').click()
    page.locator("#empty-hint").wait_for(state="visible")
    assert page.locator("#player-bar").is_hidden()
    assert page.locator("#error-panel").is_hidden()


def test_pending_rename_survives_status_rerender(page: Page, ui_base_url: str) -> None:
    """A rename queued behind transcription locking stays visible immediately."""
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-work",
            "filename": "Before",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "transcribing",
            "audio_path": "/audio/work.wav",
        }],
    })
    page.evaluate(
        """
        window.pywebview.api.rename_session = async () => {
          await new Promise(resolve => setTimeout(resolve, 250));
          return true;
        };
        """
    )
    item = page.locator(".history-item", has_text="Before")
    item.locator(".history-item-body").click()
    item = page.locator(".history-item", has_text="Before")
    item.hover()
    item.locator('button[title="Rename"]').click()
    page.locator(".history-item-rename-input").fill("During transcription")
    page.keyboard.press("Enter")
    page.evaluate("onTranscribeComplete('job-work', '', false)")

    assert page.locator(".history-item", has_text="During transcription").count() == 1
    assert page.locator("#player-filename").inner_text() == "During transcription"
    page.wait_for_timeout(300)


def test_failed_rename_rolls_back_optimistic_title(page: Page, ui_base_url: str) -> None:
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-rename",
            "filename": "Before",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "done",
            "audio_path": "/audio/work.wav",
        }],
    })
    page.evaluate(
        "window.pywebview.api.rename_session = async () => false;"
    )

    item = page.locator(".history-item", has_text="Before")
    item.hover()
    item.locator('button[title="Rename"]').click()
    page.locator(".history-item-rename-input").fill("Rejected")
    page.keyboard.press("Enter")

    page.wait_for_function(
        "document.querySelector('.history-item-name').textContent === 'Before'"
    )
    assert page.locator(".history-item", has_text="Rejected").count() == 0


def test_rapid_renames_are_serialized_last_write_wins(page: Page, ui_base_url: str) -> None:
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-rename",
            "filename": "Before",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "done",
            "audio_path": "/audio/work.wav",
        }],
    })
    page.evaluate(
        """
        window.renameCalls = [];
        window.pywebview.api.rename_session = async function (_id, name) {
          window.renameCalls.push(Array.from(arguments));
          if (name === 'A') {
            return await new Promise(resolve => {
              window.releaseFirstRename = () => resolve(true);
            });
          }
          return true;
        };
        void 0;
        """
    )

    item = page.locator(".history-item", has_text="Before")
    item.hover()
    item.locator('button[title="Rename"]').click()
    page.locator(".history-item-rename-input").fill("A")
    page.keyboard.press("Enter")
    assert page.evaluate("window.renameCalls") == [["job-rename", "A"]]

    item = page.locator(".history-item", has_text="A")
    item.hover()
    item.locator('button[title="Rename"]').click()
    page.locator(".history-item-rename-input").fill("B")
    page.keyboard.press("Enter")

    assert page.evaluate("window.renameCalls") == [["job-rename", "A"]]
    page.evaluate("window.releaseFirstRename()")
    page.wait_for_function("window.renameCalls.length === 2")
    assert page.evaluate("window.renameCalls") == [
        ["job-rename", "A"], ["job-rename", "B"]
    ]
    assert page.locator(".history-item", has_text="B").count() == 1


def test_two_failed_renames_restore_last_confirmed_title(page: Page, ui_base_url: str) -> None:
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-rename",
            "filename": "Before",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "done",
            "audio_path": "/audio/work.wav",
        }],
    })
    page.evaluate(
        """
        window.renameCalls = [];
        window.pywebview.api.rename_session = async function (_id, name) {
          window.renameCalls.push(Array.from(arguments));
          if (name === 'A') {
            return await new Promise(resolve => {
              window.releaseFirstRename = () => resolve(false);
            });
          }
          return false;
        };
        void 0;
        """
    )

    item = page.locator(".history-item", has_text="Before")
    item.hover()
    item.locator('button[title="Rename"]').click()
    page.locator(".history-item-rename-input").fill("A")
    page.keyboard.press("Enter")
    item = page.locator(".history-item", has_text="A")
    item.hover()
    item.locator('button[title="Rename"]').click()
    page.locator(".history-item-rename-input").fill("B")
    page.keyboard.press("Enter")

    page.evaluate("window.releaseFirstRename()")
    page.wait_for_function(
        "window.renameCalls.length === 2 && "
        "document.querySelector('.history-item-name').textContent === 'Before'"
    )
    assert page.evaluate("window.renameCalls") == [
        ["job-rename", "A"], ["job-rename", "B"]
    ]


def test_failed_resume_remains_visible_and_retryable(page: Page, ui_base_url: str) -> None:
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-interrupted",
            "filename": "Keep me",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "interrupted",
            "audio_path": "/audio/keep.wav",
        }],
    })
    page.evaluate(
        "window.pywebview.api.resume_transcription = async () => "
        "({error: 'no_checkpoint'});"
    )

    item = page.locator(".history-item", has_text="Keep me")
    item.hover()
    item.locator('button[title="Resume"]').click()
    page.wait_for_function(
        "document.querySelector('#error-msg').textContent.includes('no_checkpoint')"
    )

    item = page.locator(".history-item", has_text="Keep me")
    assert item.count() == 1
    assert "history-item-interrupted" in (item.get_attribute("class") or "")
    assert item.locator('button[title="Resume"]').count() == 1


def test_async_failed_resume_remains_visible_and_retryable(page: Page, ui_base_url: str) -> None:
    """A worker error after resume starts must retain the checkpoint UI."""
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-interrupted",
            "filename": "Keep me",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "interrupted",
            "audio_path": "/audio/keep.wav",
        }],
    })
    page.evaluate(
        """
        window.pywebview.api.resume_transcription = async jobId => {
          queueMicrotask(() => onTranscribeError(jobId, 'worker failed'));
          return {job_id: jobId};
        };
        """
    )

    item = page.locator(".history-item", has_text="Keep me")
    item.hover()
    item.locator('button[title="Resume"]').click()
    page.wait_for_function(
        "document.querySelector('#error-msg').textContent.includes('worker failed')"
    )

    item = page.locator(".history-item", has_text="Keep me")
    assert "history-item-interrupted" in (item.get_attribute("class") or "")
    assert item.locator('button[title="Resume"]').count() == 1


def test_cancelled_resume_remains_visible_and_retryable(page: Page, ui_base_url: str) -> None:
    page.set_default_timeout(3000)
    _inject_and_navigate(page, ui_base_url, config={
        "history": [{
            "job_id": "job-interrupted",
            "filename": "Keep me",
            "date": "2026-09-06T23:00:00",
            "type": "file",
            "status": "interrupted",
            "audio_path": "/audio/keep.wav",
        }],
    })
    page.evaluate(
        """
        window.pywebview.api.resume_transcription = async jobId => {
          queueMicrotask(() => onTranscribeCancel(jobId));
          return {job_id: jobId};
        };
        """
    )

    item = page.locator(".history-item", has_text="Keep me")
    item.hover()
    item.locator('button[title="Resume"]').click()
    page.wait_for_function(
        "document.querySelector('.history-item')?.classList.contains('history-item-interrupted')"
    )

    item = page.locator(".history-item", has_text="Keep me")
    assert item.locator('button[title="Resume"]').count() == 1


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
    assert page.locator("#btn-start-recording").inner_text() == "Start Recording"
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
    assert page.locator("#error-msg").inner_text() == "Qwen backend mlx\ndecoder failed"

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


def test_transcription_start_failure_uses_non_blocking_toast(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "history": [],
        "selectedFiles": ["/fixtures/interview.wav"],
    })
    page.evaluate(
        """
        window.alertCalls = [];
        window.alert = message => window.alertCalls.push(message);
        window.pywebview.api.transcribe = async () => ({error: 'job_in_progress'});
        void 0;
        """
    )

    page.locator("#btn-upload").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('job_in_progress')"
    )

    assert page.locator("#toast").is_visible()
    assert page.evaluate("window.alertCalls") == []


def test_missing_summary_template_uses_non_blocking_toast(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url)
    page.evaluate(
        """
        window.alertCalls = [];
        window.alert = message => window.alertCalls.push(message);
        window.pywebview.api.get_summary_templates = async () => [];
        void 0;
        """
    )

    page.locator(".history-item", has_text="meeting.mp3").locator(
        ".history-item-body"
    ).click()
    page.locator("#btn-summary-toggle").click()
    page.locator("#btn-summarize").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('No template selected')"
    )

    assert page.locator("#toast").is_visible()
    assert page.evaluate("window.alertCalls") == []


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
        const first = new File(['one'], 'first.wav', {type: 'audio/wav'});
        const second = new File(['two'], 'second.mp3', {type: 'audio/mpeg'});
        Object.defineProperty(first, 'path', {value: '/tmp/first.wav'});
        Object.defineProperty(second, 'path', {value: '/tmp/second.mp3'});
        transfer.items.add(first);
        transfer.items.add(second);
        document.querySelector('#center-panel').dispatchEvent(
          new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer})
        );
        """
    )
    page.wait_for_function("_mockApi.calls('transcribe').length === 1")
    assert page.evaluate("_mockApi.calls('transcribe')")[0]["args"][0] == "/tmp/first.wav"
    assert not page.locator("#center-panel").evaluate("el => el.classList.contains('drag-over')")

    page.evaluate("onTranscribeComplete('drop-1', '/mock/transcript.json', false)")
    page.wait_for_function("_mockApi.calls('transcribe').length === 2")
    assert page.evaluate("_mockApi.calls('transcribe')")[1]["args"][0] == "/tmp/second.mp3"
    assert page.locator(".history-item").count() == 2


def test_drag_drop_without_absolute_paths_directs_user_to_upload_file(
    page: Page, ui_base_url: str
) -> None:
    _inject_and_navigate(page, ui_base_url, {"history": []})

    page.evaluate(
        """
        const transfer = new DataTransfer();
        transfer.items.add(new File(['one'], 'first.wav', {type: 'audio/wav'}));
        document.querySelector('#center-panel').dispatchEvent(
          new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer})
        );
        """
    )

    page.wait_for_function("document.querySelector('#toast').textContent.includes('Upload File')")
    assert page.evaluate("_mockApi.calls('transcribe').length") == 0


def test_summary_agent_stream_tool_error_recovery_and_clear(page: Page, ui_base_url: str) -> None:
    _inject_and_navigate(page, ui_base_url, {
        "agentTurns": [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
        ],
    })
    page.locator(".history-item", has_text="meeting.mp3").locator(".history-item-body").click()
    page.wait_for_function("_mockApi.calls('get_agent_session').length >= 1")
    page.locator("#btn-summary-toggle").click()
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
