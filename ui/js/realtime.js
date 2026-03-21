/**
 * Realtime — microphone transcription control and live results display.
 *
 * Public API:
 *   Realtime.init()       — wire up DOM; call once after DOMContentLoaded
 *
 * Python → JS callbacks (called via evaluate_js):
 *   onRealtimeStarted(sessionId) — models loaded, mic open; sessionId = WAV filename stem
 *   onRealtimeStopped()          — mic closed
 *   onRealtimeResult(text)       — one transcribed utterance
 *   onRealtimeError(message)     — error
 */
const Realtime = (() => {
  let _recording = false;
  let dom = {};

  // ── Init ──────────────────────────────────────────────────────────────────

  function init() {
    dom = {
      btnToggle:    document.getElementById('btn-realtime'),
      statusDot:    document.getElementById('realtime-dot'),
      statusText:   document.getElementById('realtime-status'),
      panel:        document.getElementById('realtime-panel'),
      list:         document.getElementById('realtime-list'),
    };

    dom.btnToggle.addEventListener('click', _onToggle);
  }

  // ── Toggle ────────────────────────────────────────────────────────────────

  async function _onToggle() {
    if (_recording) {
      _setLoading(true);
      try {
        await window.pywebview.api.stop_realtime();
      } catch (e) {
        console.error('[Realtime] stop error:', e);
        _setLoading(false);
      }
    } else {
      const engine = document.getElementById('sel-engine').value;
      _setLoading(true);
      _setStatus('loading…');
      try {
        await window.pywebview.api.start_realtime({ engine });
      } catch (e) {
        console.error('[Realtime] start error:', e);
        _setLoading(false);
        _setStatus('error');
      }
    }
  }

  // ── Python → JS callbacks ─────────────────────────────────────────────────

  function onStarted(sessionId) {
    _recording = true;
    _setLoading(false);
    dom.btnToggle.textContent = '⏹ Stop';
    dom.btnToggle.classList.add('recording');
    dom.statusDot.classList.add('active');
    _setStatus('Recording…');
    dom.list.innerHTML = '';
    dom.panel.hidden = false;
  }

  function onStopped() {
    _recording = false;
    _setLoading(false);
    dom.btnToggle.textContent = '🎙 Realtime';
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('Stopped');
  }

  function onResult(text) {
    const el = document.createElement('div');
    el.className = 'realtime-row';
    el.textContent = text;
    dom.list.appendChild(el);
    dom.list.scrollTop = dom.list.scrollHeight;
  }

  function onError(message) {
    _recording = false;
    _setLoading(false);
    dom.btnToggle.textContent = '🎙 Realtime';
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('⚠ ' + message);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _setLoading(active) {
    dom.btnToggle.disabled = active;
  }

  function _setStatus(text) {
    dom.statusText.textContent = text;
  }

  return { init, onStarted, onStopped, onResult, onError };
})();

// ── Global callbacks (invoked by Python via evaluate_js) ──────────────────────

function onRealtimeStarted(sessionId) { Realtime.onStarted(sessionId); }
function onRealtimeStopped()        { Realtime.onStopped(); }
function onRealtimeResult(text)     { Realtime.onResult(text); }
function onRealtimeError(message)   { Realtime.onError(message); }
