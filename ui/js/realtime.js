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
 *   onRealtimeSegmentWarning(sessionId, start, end) — one skipped utterance
 *   onRealtimeError(message)     — error
 */
const Realtime = (() => {
  let _recording = false;
  let _sessionId = null;
  let _loading = false;
  let _captureMode = 'mic';      // 'mic' | 'system' | 'mix'
  let _activeProvider = 'local';
  let _cloudPollTimer = null;
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
      segmentWarning: document.getElementById('realtime-segment-warning'),
      list:         document.getElementById('realtime-list'),
      modeButtons:  Array.from(document.querySelectorAll('.btn-mode')),
      provider:     document.getElementById('recording-asr-provider'),
      cloudNotice:  document.getElementById('recording-cloud-notice'),
      permNotice:   document.getElementById('capture-perm-notice'),
      permText:     document.getElementById('capture-perm-text'),
      btnOpenMic:   document.getElementById('btn-open-microphone-privacy'),
      btnOpenScreen: document.getElementById('btn-open-screen-privacy'),
    };
    dom.btnToggle.addEventListener('click', _onToggle);
    dom.modeButtons.forEach(btn => btn.addEventListener('click', _onModeClick));
    dom.provider.addEventListener('change', () => {
      dom.cloudNotice.hidden = dom.provider.value !== 'token-plan';
    });
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
    dom.provider.disabled = disabled;
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
        _showPermNotice({ ...refreshed, request_failure: permissionFailure });
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
        live_asr_provider: dom.provider.value,
      });
      if (startResult && startResult.status === 'permission_required') {
        _setLoading(false);
        _disableModeButtons(false);
        const missing = _missingPermissions(startResult);
        const kind = missing.length === 1 ? missing[0] : null;
        _setStatus(_permissionFailureStatus(kind, null));
        _showPermNotice(startResult);
      } else if (startResult?.status === 'configuration_required') {
        _setLoading(false);
        _disableModeButtons(false);
        _setStatus('请在设置中保存有效的 Token Plan Key');
      }
    } catch (e) {
      console.error('[Realtime] start error:', e);
      _setLoading(false);
      _disableModeButtons(false);
      _setStatus('Error');
    }
  }

  // ── Python → JS callbacks ─────────────────────────────────────────────────

  function onStarted(sessionId, wavPath, provider = 'local') {
    _recording = true;
    _sessionId = sessionId;
    _activeProvider = provider;
    _loading = false;
    _setLoading(false);
    _disableModeButtons(true);
    _setStartButtonLabel('Stop Recording', true);
    dom.btnToggle.classList.add('recording');
    dom.statusDot.classList.add('active');
    _setStatus(_recordingStatus());
    dom.segmentWarning.hidden = true;
    dom.segmentWarning.textContent = '';
    dom.list.innerHTML = '';
    if (_onStateChange) _onStateChange('started', sessionId, wavPath);
    if (_activeProvider === 'token-plan') void _pollCloudDraft(sessionId, 0);
  }

  function onStopped(cloudCoverage) {
    clearTimeout(_cloudPollTimer);
    _recording = false;
    _sessionId = null;
    _loading = false;
    _setLoading(false);
    _disableModeButtons(false);
    _setStartButtonLabel('Start Recording');
    dom.btnToggle.classList.remove('recording');
    dom.statusDot.classList.remove('active');
    _setStatus('Stopped');
    if (_onStateChange) _onStateChange('stopped', null, null, cloudCoverage);
  }

  function formatTime(time) {
    const minutes = Math.floor(time / 60);
    const seconds = Math.floor(time % 60);
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  function createTranscriptRow(seg) {
    // seg may be a plain string (backward compat) or {text, start, end} dict
    const isObj = seg && typeof seg === 'object';
    const text  = isObj ? (seg.text || '') : String(seg);
    const el = document.createElement('div');
    el.className = 'realtime-row';
    if (isObj && typeof seg.start === 'number') {
      const ts = document.createElement('span');
      ts.className = 'realtime-ts';
      ts.textContent = `[${formatTime(seg.start)}]`;
      el.appendChild(ts);
      el.appendChild(document.createTextNode(' ' + text));
    } else {
      el.textContent = text;
    }
    return el;
  }

  function onResult(seg) {
    dom.list.appendChild(createTranscriptRow(seg));
    dom.list.scrollTop = dom.list.scrollHeight;
  }

  function onSegmentWarning(sessionId, start, end) {
    if (!_recording || sessionId !== _sessionId) return;
    dom.segmentWarning.textContent = `${formatTime(start)}–${formatTime(end)} 的音频片段未能识别，录音继续。`;
    dom.segmentWarning.hidden = false;
  }

  function _showCloudWarning(code) {
    const messages = {
      quota_exhausted: 'Token Plan 额度用尽',
      access_denied: 'Token Plan 鉴权或模型权限失败',
      rate_limited: 'Token Plan 请求受到限流',
    };
    dom.segmentWarning.textContent = `${messages[code] || '部分云端音频未能转写'}。本地录音继续。`;
    dom.segmentWarning.hidden = false;
  }

  async function _pollCloudDraft(sessionId, cursor) {
    const stillRecording = () => _recording && _sessionId === sessionId;
    let next = cursor;
    let delay = 1000;
    try {
      const page = await window.pywebview.api.get_recording_draft(sessionId, cursor);
      if (!stillRecording()) return;
      if (page) {
        page.segments.forEach(onResult);
        if (page.live_asr?.error) _showCloudWarning(page.live_asr.error);
        next = Math.max(cursor, page.next_cursor);
        if (page.has_more && next > cursor) delay = 0;
      }
    } catch (_) {
      // The next poll can recover a transient IPC failure from the durable draft.
    }
    if (stillRecording()) _cloudPollTimer = setTimeout(() => _pollCloudDraft(sessionId, next), delay);
  }

  function onError(message) {
    // Non-fatal: mic degradation in mix mode — show an inline notice but keep
    // recording; the session continues in system-audio-only mode.
    if (typeof message === 'string' && message.startsWith('mic_degraded:')) {
      _showPermNotice({ reason: 'mic_capture_failed' });
      return;
    }

    // Fatal errors: reset the recording state
    clearTimeout(_cloudPollTimer);
    _recording = false;
    _sessionId = null;
    _loading = false;
    _setLoading(false);
    _disableModeButtons(false);
    _setStartButtonLabel('Start Recording');
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
    _setStatus(_recordingStatus());
    dom.statusDot.classList.add('active');
    if (_onStateChange) _onStateChange('resumed');
  }

  function _recordingStatus() {
    const mode = MODE_LABELS[_captureMode] || _captureMode;
    return `Recording · ${mode}${_activeProvider === 'token-plan' ? ' · Token Plan 云端' : ''}`;
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
    if (status === 'in_progress') return 'Permission request in progress';
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
        if (info.request_failure && info.request_failure.kind === 'microphone' &&
            info.request_failure.status === 'unknown') {
          messages.push('无法发起麦克风授权请求。请重试；若持续失败，请提供应用诊断日志。');
        } else if (info.microphone_permission === 'not_determined') {
          messages.push('尚未申请麦克风权限。点击 Start Recording 后，请在系统弹窗中允许访问。');
        } else {
          messages.push('麦克风权限：Mic 和 Mix 模式需要；System 模式不需要。');
        }
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
    if (dom.btnOpenMic) dom.btnOpenMic.hidden = !missing.has('microphone') ||
      info.microphone_permission === 'not_determined';
    if (dom.btnOpenScreen) dom.btnOpenScreen.hidden = !missing.has('screen_recording');
    if (dom.permNotice) dom.permNotice.removeAttribute('hidden');
  }

  function _hidePermNotice() {
    if (dom.permNotice) dom.permNotice.setAttribute('hidden', '');
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _setStartButtonLabel(label, stop = false) {
    const ns = 'http://www.w3.org/2000/svg';
    const icon = document.createElementNS(ns, 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    if (stop) {
      const rect = document.createElementNS(ns, 'rect');
      rect.setAttribute('x', '7');
      rect.setAttribute('y', '7');
      rect.setAttribute('width', '10');
      rect.setAttribute('height', '10');
      rect.setAttribute('rx', '1');
      icon.appendChild(rect);
    } else {
      const rect = document.createElementNS(ns, 'rect');
      rect.setAttribute('x', '9');
      rect.setAttribute('y', '3');
      rect.setAttribute('width', '6');
      rect.setAttribute('height', '11');
      rect.setAttribute('rx', '3');
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', 'M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M9 22h6');
      icon.append(rect, path);
    }
    const text = document.createElement('span');
    text.className = 'btn-label';
    text.textContent = label;
    dom.btnToggle.replaceChildren(icon, text);
  }

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
      _setStartButtonLabel('Stop Recording', true);
      return;
    }
    _setStartButtonLabel('Start Recording');
  }

  function _setStatus(text) {
    dom.statusText.textContent = text;
  }

  function isBusy() {
    return _loading || _recording || _awaitingPermission;
  }

  return { init, isBusy, createTranscriptRow, onStarted, onStopped, onResult, onError,
           onSegmentWarning, onPaused, onResumed };
})();

// ── Global callbacks (invoked by Python via evaluate_js) ──────────────────────

function onRealtimeStarted(sessionId, wavPath, provider) { Realtime.onStarted(sessionId, wavPath, provider); }
function onRealtimeStopped(coverage)  { Realtime.onStopped(coverage); }
function onRealtimeResult(text)       { Realtime.onResult(text); }
function onRealtimeSegmentWarning(sessionId, start, end) { Realtime.onSegmentWarning(sessionId, start, end); }
function onRealtimeError(message)     { Realtime.onError(message); }
function onRealtimePaused()           { Realtime.onPaused(); }
function onRealtimeResumed()          { Realtime.onResumed(); }
