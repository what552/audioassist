/**
 * Realtime — microphone transcription control and live results display.
 *
 * Public API:
 *   Realtime.init(onStateChange)  — wire up DOM; onStateChange(state, sessionId?) on transitions
 *
 * Python → JS callbacks (called via evaluate_js):
 *   onRealtimeStarted(sessionId) — models loaded, mic open
 *   onRealtimeStopped()          — mic closed
 *   onRealtimeResult(text)       — one transcribed utterance
 *   onRealtimeError(message)     — error
 */
const Realtime = (() => {
  let _recording = false;
  let _onStateChange = null;
  let dom = {};

  // ── Init ──────────────────────────────────────────────────────────────────

  function init(onStateChange) {
    _onStateChange = onStateChange;
    dom = {
      btnToggle:  document.getElementById('btn-start-recording'),
      statusDot:  document.getElementById('realtime-dot'),
      statusText: document.getElementById('realtime-status'),
      list:       document.getElementById('realtime-list'),
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
      _setStatus('Loading…');
      try {
        await window.pywebview.api.start_realtime({ engine });
      } catch (e) {
        console.error('[Realtime] start error:', e);
        _setLoading(false);
        _setStatus('Error');
      }
    }
  }

  // ── Python → JS callbacks ─────────────────────────────────────────────────

  function onStarted(sessionId) {
    _recording = true;
    _setLoading(false);
    dom.btnToggle.textContent = '⏹ Stop Recording';
    dom.btnToggle.classList.add('recording');
    dom.statusDot.classList.add('active');
    _setStatus('Recording…');
    dom.list.innerHTML = '';
    if (_onStateChange) _onStateChange('started', sessionId);
  }

  function onStopped() {
    _recording = false;
    _setLoading(false);
    dom.btnToggle.textContent = '🎙 Start Recording';
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('Stopped');
    if (_onStateChange) _onStateChange('stopped');
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
    dom.btnToggle.textContent = '🎙 Start Recording';
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('⚠ ' + message);
    if (_onStateChange) _onStateChange('error');
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
function onRealtimeStopped()          { Realtime.onStopped(); }
function onRealtimeResult(text)       { Realtime.onResult(text); }
function onRealtimeError(message)     { Realtime.onError(message); }
