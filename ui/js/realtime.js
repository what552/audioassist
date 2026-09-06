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
  let _loading = false;
  let _captureMode = 'mic';      // 'mic' | 'system' | 'mix'
  let _onStateChange = null;
  let _canStart = null;
  let _awaitingPermission = false;
  let _permissionRequestGeneration = 0;
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
      btnOpenMic:   document.getElementById('btn-open-microphone-privacy'),
      btnOpenScreen: document.getElementById('btn-open-screen-privacy'),
    };
    dom.btnToggle.addEventListener('click', _onToggle);
    dom.modeButtons.forEach(btn => btn.addEventListener('click', _onModeClick));
    if (dom.btnOpenMic) {
      dom.btnOpenMic.addEventListener('click', () => {
        window.pywebview.api.open_privacy_settings('microphone').catch(() => {});
      });
    }
    if (dom.btnOpenScreen) {
      dom.btnOpenScreen.addEventListener('click', () => {
        window.pywebview.api.open_privacy_settings('screen').catch(() => {});
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
    if (_awaitingPermission) {
      _permissionRequestGeneration += 1;
      _awaitingPermission = false;
      _setLoading(false);
      _disableModeButtons(false);
      _setStatus('Permission request canceled');
      return;
    }
    if (_recording) {
      _setLoading(true, 'Stopping…');
      try {
        await window.pywebview.api.stop_realtime();
      } catch (e) {
        console.error('[Realtime] stop error:', e);
        _setLoading(false);
      }
      return;
    }

    if (_canStart && !_canStart()) return;
    _setLoading(true, 'Loading…');
    _disableModeButtons(true);

    _setStatus('Checking…');
    let pre;
    try {
      pre = await window.pywebview.api.preflight_capture(_captureMode);
    } catch (e) {
      console.error('[Realtime] preflight error:', e);
      _setLoading(false);
      _disableModeButtons(false);
      _setStatus('Error');
      return;
    }
    if (pre && pre.supported === false) {
      _setLoading(false);
      _disableModeButtons(false);
      _setStatus('Unavailable');
      _showPermNotice(pre);
      return;
    }
    const missingPermissions = _missingPermissions(pre);
    if (missingPermissions.length) {
      const requestGeneration = ++_permissionRequestGeneration;
      _awaitingPermission = true;
      _setLoading(true, 'Cancel Permission Wait');
      dom.btnToggle.disabled = false;
      let permissionFailure = null;

      if (missingPermissions.includes('microphone')) {
        _setStatus('Waiting for microphone permission…');
        if (pre.microphone_permission !== 'not_determined') {
          permissionFailure = { kind: 'microphone', status: pre.microphone_permission };
        } else {
          try {
            const permission = await window.pywebview.api.request_microphone_permission();
            if (!permission || !permission.authorized) {
              permissionFailure = {
                kind: 'microphone',
                status: (permission && permission.status) || 'unknown',
              };
            }
          } catch (e) {
            console.error('[Realtime] microphone permission request error:', e);
            permissionFailure = { kind: 'microphone', status: 'unknown' };
          }
        }
      }
      if (requestGeneration !== _permissionRequestGeneration) return;

      if (!permissionFailure && missingPermissions.includes('screen_recording')) {
        _setStatus('Waiting for Screen & System Audio permission…');
        try {
          const permission = await window.pywebview.api.request_screen_recording_permission();
          if (!permission || !permission.authorized) {
            permissionFailure = {
              kind: 'screen_recording',
              status: (permission && permission.status) || 'unknown',
            };
          }
        } catch (e) {
          console.error('[Realtime] screen recording permission request error:', e);
          permissionFailure = { kind: 'screen_recording', status: 'unknown' };
        }
      }
      if (requestGeneration !== _permissionRequestGeneration) return;
      _awaitingPermission = false;
      if (permissionFailure) {
        let refreshed = pre;
        try {
          refreshed = await window.pywebview.api.preflight_capture(_captureMode);
        } catch (_) {}
        _setLoading(false);
        _disableModeButtons(false);
        _setStatus(_permissionFailureStatus(
          permissionFailure.kind,
          permissionFailure.status,
        ));
        _showPermNotice(refreshed);
        return;
      }
      _setLoading(true, 'Loading…');
      _setStatus('Loading…');
    } else {
      _setStatus('Loading…');
    }
    _hidePermNotice();

    const model_id = document.getElementById('sel-engine').value;
    try {
      const startResult = await window.pywebview.api.start_realtime({
        model_id,
        capture_mode: _captureMode,
      });
      if (startResult && startResult.status === 'permission_required') {
        _setLoading(false);
        _disableModeButtons(false);
        const missing = _missingPermissions(startResult);
        const kind = missing.length === 1 ? missing[0] : null;
        _setStatus(_permissionFailureStatus(kind, null));
        _showPermNotice(startResult);
      }
    } catch (e) {
      console.error('[Realtime] start error:', e);
      _setLoading(false);
      _disableModeButtons(false);
      _setStatus('Error');
    }
  }

  // ── Python → JS callbacks ─────────────────────────────────────────────────

  function onStarted(sessionId, wavPath) {
    _recording = true;
    _loading = false;
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
    _loading = false;
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
    _loading = false;
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

  function _missingPermissions(info) {
    info = info || {};
    const missing = new Set(info.missing_permissions || []);
    if (info.microphone_permission &&
        !['authorized', 'not_applicable'].includes(info.microphone_permission)) {
      missing.add('microphone');
    }
    if (info.screen_recording_permission &&
        !['authorized', 'not_applicable'].includes(info.screen_recording_permission)) {
      missing.add('screen_recording');
    }
    const reason = info.reason || '';
    if (reason.startsWith('microphone_permission_')) missing.add('microphone');
    if (reason.includes('screen_recording')) {
      missing.add('screen_recording');
    }
    if (reason.startsWith('permission_required:')) {
      const requested = reason.slice('permission_required:'.length).split(',');
      if (requested.some(item => ['mic', 'microphone'].includes(item.trim()))) {
        missing.add('microphone');
      }
      if (requested.some(item => ['screen', 'system', 'screen_recording'].includes(item.trim()))) {
        missing.add('screen_recording');
      }
    }
    return Array.from(missing);
  }

  function _permissionFailureStatus(kind, status) {
    if (status === 'timeout') return 'Permission timed out';
    if (kind === 'microphone') return 'Microphone unavailable';
    if (kind === 'screen_recording') return 'Screen & System Audio unavailable';
    return 'Permissions required';
  }

  function _showPermNotice(info) {
    const reason = info.reason || '';
    const missing = new Set(_missingPermissions(info));
    let msg = '';

    if (reason === 'screencapturekit_requires_macos_13_0') {
      msg = '需要 macOS 13.0 或更高版本才能使用系统音频捕获。';
    } else if (reason === 'helper_not_found') {
      msg = '系统音频助手未构建。请先运行: swift build -c release';
    } else if (missing.size) {
      const messages = [];
      if (missing.has('microphone')) {
        messages.push('麦克风权限：Mic 和 Mix 模式需要；System 模式不需要。');
      }
      if (missing.has('screen_recording')) {
        messages.push('屏幕与系统音频录制权限：System 和 Mix 模式需要；Mic 模式不需要。');
      }
      msg = messages.join('\n');
    } else if (reason === 'mic_capture_failed' || reason === 'mic_unavailable') {
      msg = '无法访问麦克风。混合模式将仅录制系统音频。';
    } else if (reason) {
      msg = '无法使用该模式: ' + reason;
    } else {
      msg = '该捕获模式当前不可用。';
    }

    if (dom.permText)  dom.permText.textContent = msg;
    if (dom.btnOpenMic) dom.btnOpenMic.hidden = !missing.has('microphone');
    if (dom.btnOpenScreen) dom.btnOpenScreen.hidden = !missing.has('screen_recording');
    if (dom.permNotice) dom.permNotice.removeAttribute('hidden');
  }

  function _hidePermNotice() {
    if (dom.permNotice) dom.permNotice.setAttribute('hidden', '');
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _setLoading(active) {
    let label = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : 'Loading…';
    _loading = active;
    dom.btnToggle.disabled = active;
    dom.btnToggle.classList.toggle('loading', active);
    if (active) {
      dom.btnToggle.innerHTML = `<span class="btn-spinner" aria-hidden="true"></span>${label}`;
      return;
    }

    if (_recording) {
      dom.btnToggle.textContent = '⏹ Stop Recording';
      return;
    }
    dom.btnToggle.textContent = '🎙 Start Recording';
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
