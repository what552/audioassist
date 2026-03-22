"""
Frontend Playwright tests for AudioAssist.

Spins up a local HTTP server serving ui/, injects mock_api.js via
Playwright's add_init_script() so window.pywebview.api is available
before any page script runs, then controls pywebviewready timing manually.

Three scenarios:
  1. history_loads       — _loadHistory() populates sidebar after pywebviewready
  2. switch_stops_audio  — _onHistorySelect() calls Player.stop() on session switch
  3. pywebviewready_timing — App.init() is NOT called before pywebviewready fires
"""
from __future__ import annotations

import http.server
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

def _inject_and_navigate(page: Page, base_url: str) -> None:
    """Inject mock API, navigate to index.html, then fire pywebviewready."""
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
