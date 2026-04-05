/**
 * Realtime — microphone / system-audio transcription control and live results.
 *
 * Public API:
 *   Realtime.init(onStateChange)  — wire up DOM; onStateChange(state, sessionId?) on transitions
 *
 * Python → JS callbacks (called via evaluate_js):
 *   onRealtimeStarted(sessionId) — models loaded, capture open
 *   onRealtimeStopped()          — capture closed
 *   onRealtimeResult(text)       — one transcribed utterance
 *   onRealtimeError(message)     — error
 */
const Realtime = (() => {
  let _recording = false;
  let _captureMode = 'mic';      // 'mic' | 'system' | 'mix'
  let _onStateChange = null;
  let _canStart = null;
  let dom = {};

  const MODE_LABELS = { mic: '麦克风', system: '系统音频', mix: '混合' };

  // ── Init ──────────────────────────────────────────────────────────────────

  function init(onStateChange, canStart) {
    _onStateChange = onStateChange;
    _canStart = canStart || null;
    dom = {
      btnToggle:    document.getElementById('btn-start-recording'),
      statusDot:    document.getElementById('realtime-dot'),
      statusText:   document.getElementById('realtime-status'),
      list:         document.getElementById('realtime-list'),
      modeButtons:  Array.from(document.querySelectorAll('.btn-mode')),
      permNotice:   document.getElementById('capture-perm-notice'),
      permText:     document.getElementById('capture-perm-text'),
      btnOpenPriv:  document.getElementById('btn-open-privacy'),
    };
    dom.btnToggle.addEventListener('click', _onToggle);
    dom.modeButtons.forEach(btn => btn.addEventListener('click', _onModeClick));
    if (dom.btnOpenPriv) {
      dom.btnOpenPriv.addEventListener('click', () => {
        window.pywebview.api.open_privacy_settings().catch(() => {});
      });
    }
  }

  // ── Mode selection ────────────────────────────────────────────────────────

  function _onModeClick(e) {
    if (_recording) return;
    const mode = e.currentTarget.dataset.mode;
    _setMode(mode);
  }

  function _setMode(mode) {
    _captureMode = mode;
    dom.modeButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    _hidePermNotice();
  }

  function _disableModeButtons(disabled) {
    dom.modeButtons.forEach(btn => { btn.disabled = disabled; });
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
      return;
    }

    if (_canStart && !_canStart()) return;

    // Preflight check for system / mix modes
    if (_captureMode !== 'mic') {
      _setLoading(true);
      _setStatus('Checking…');
      let pre;
      try {
        pre = await window.pywebview.api.preflight_capture(_captureMode);
      } catch (e) {
        console.error('[Realtime] preflight error:', e);
        _setLoading(false);
        _setStatus('Error');
        return;
      }
      if (!pre.supported) {
        _setLoading(false);
        _setStatus('Unavailable');
        _showPermNotice(pre);
        return;
      }
      _hidePermNotice();
    }

    const model_id = document.getElementById('sel-engine').value;
    _setLoading(true);
    _setStatus('Loading…');
    try {
      await window.pywebview.api.start_realtime({
        model_id,
        capture_mode: _captureMode,
      });
    } catch (e) {
      console.error('[Realtime] start error:', e);
      _setLoading(false);
      _setStatus('Error');
    }
  }

  // ── Python → JS callbacks ─────────────────────────────────────────────────

  function onStarted(sessionId, wavPath) {
    _recording = true;
    _setLoading(false);
    _disableModeButtons(true);
    dom.btnToggle.textContent = '⏹ Stop Recording';
    dom.btnToggle.classList.add('recording');
    dom.statusDot.classList.add('active');
    _setStatus('Recording · ' + (MODE_LABELS[_captureMode] || _captureMode));
    dom.list.innerHTML = '';
    if (_onStateChange) _onStateChange('started', sessionId, wavPath);
  }

  function onStopped() {
    _recording = false;
    _setLoading(false);
    _disableModeButtons(false);
    dom.btnToggle.textContent = '🎙 Start Recording';
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('Stopped');
    if (_onStateChange) _onStateChange('stopped');
  }

  function onResult(seg) {
    // seg may be a plain string (backward compat) or {text, start, end} dict
    const isObj = seg && typeof seg === 'object';
    const text  = isObj ? (seg.text || '') : String(seg);
    const el = document.createElement('div');
    el.className = 'realtime-row';
    if (isObj && typeof seg.start === 'number') {
      const ts = document.createElement('span');
      ts.className = 'realtime-ts';
      const m = Math.floor(seg.start / 60), s = Math.floor(seg.start % 60);
      ts.textContent = `[${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}]`;
      el.appendChild(ts);
      el.appendChild(document.createTextNode(' ' + text));
    } else {
      el.textContent = text;
    }
    dom.list.appendChild(el);
    dom.list.scrollTop = dom.list.scrollHeight;
  }

  function onError(message) {
    // Non-fatal: mic degradation in mix mode — show an inline notice but keep
    // recording; the session continues in system-audio-only mode.
    if (typeof message === 'string' && message.startsWith('mic_degraded:')) {
      _showPermNotice({ reason: 'mic_capture_failed' });
      return;
    }

    // Fatal errors: reset the recording state
    _recording = false;
    _setLoading(false);
    _disableModeButtons(false);
    dom.btnToggle.textContent = '🎙 Start Recording';
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('⚠ ' + message);

    // Show permission guidance if the helper emitted a permission event
    if (typeof message === 'string' && message.startsWith('permission_required:')) {
      _showPermNotice({ reason: message });
    }

    if (_onStateChange) _onStateChange('error');
  }

  function onPaused() {
    _setStatus('Paused · ' + (MODE_LABELS[_captureMode] || _captureMode));
    dom.statusDot.classList.remove('active');
    if (_onStateChange) _onStateChange('paused');
  }

  function onResumed() {
    _setStatus('Recording · ' + (MODE_LABELS[_captureMode] || _captureMode));
    dom.statusDot.classList.add('active');
    if (_onStateChange) _onStateChange('resumed');
  }

  // ── Permission notice ─────────────────────────────────────────────────────

  function _showPermNotice(info) {
    const reason = info.reason || '';
    let msg = '';
    let showOpenBtn = false;

    if (reason === 'screencapturekit_requires_macos_13_0') {
      msg = '需要 macOS 13.0 或更高版本才能使用系统音频捕获。';
    } else if (reason === 'helper_not_found') {
      msg = '系统音频助手未构建。请先运行: swift build -c release';
    } else if (reason.includes('screen_recording') || reason.includes('permission_required')) {
      msg = '需要「屏幕录制」权限才能捕获系统音频。请在系统设置中授权。';
      showOpenBtn = true;
    } else if (reason === 'mic_capture_failed' || reason === 'mic_unavailable') {
      msg = '无法访问麦克风。混合模式将仅录制系统音频。';
    } else if (reason) {
      msg = '无法使用该模式: ' + reason;
    } else {
      msg = '该捕获模式当前不可用。';
    }

    if (dom.permText)  dom.permText.textContent = msg;
    if (dom.btnOpenPriv) dom.btnOpenPriv.hidden = !showOpenBtn;
    if (dom.permNotice) dom.permNotice.removeAttribute('hidden');
  }

  function _hidePermNotice() {
    if (dom.permNotice) dom.permNotice.setAttribute('hidden', '');
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _setLoading(active) {
    dom.btnToggle.disabled = active;
  }

  function _setStatus(text) {
    dom.statusText.textContent = text;
  }

  return { init, onStarted, onStopped, onResult, onError, onPaused, onResumed };
})();

// ── Global callbacks (invoked by Python via evaluate_js) ──────────────────────

function onRealtimeStarted(sessionId, wavPath) { Realtime.onStarted(sessionId, wavPath); }
function onRealtimeStopped()          { Realtime.onStopped(); }
function onRealtimeResult(text)       { Realtime.onResult(text); }
function onRealtimeError(message)     { Realtime.onError(message); }
function onRealtimePaused()           { Realtime.onPaused(); }
function onRealtimeResumed()          { Realtime.onResumed(); }
