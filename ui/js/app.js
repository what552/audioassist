/**
 * AudioAssist — main coordinator (session-state-machine).
 *
 * Session store: _sessions: Map<id, Session>
 * Single _render() derives all UI from the selected session's type + status.
 *
 * View states:
 *   idle           — #empty-hint
 *   transcribing   — #progress-panel
 *   file-done      — #player-bar + #transcript-*
 *   realtime-rec   — #realtime-panel + #realtime-control-bar
 *   realtime-paused — #realtime-panel + #realtime-control-bar
 *   realtime-done  — #player-bar + #realtime-panel
 *
 * Python → JS event handlers (called via evaluate_js):
 *   onTranscribeProgress(jobId, pct, message)
 *   onTranscribeComplete(jobId, jsonPath)
 *   onTranscribeError(jobId, message)
 *   onModelDownloadProgress(name, percent)
 *   onModelDownloadError(name, message)
 *   onRealtimePaused()
 *   onRealtimeResumed()
 *
 * Concurrency rules:
 *   Upload is blocked while a realtime session is recording/paused.
 *   Start Recording is blocked while any file transcription is in progress.
 */
const App = (() => {
  // ── Session store ──────────────────────────────────────────────────────────
  const _sessions = new Map();  // insertion-order preserved
  let _selectedId        = null;
  let _activeRealtimeId  = null;
  let dom = {};

  // ── Timer ──────────────────────────────────────────────────────────────────
  let _timerInterval = null;
  let _timerSeconds  = 0;

  function _startTimer() {
    _timerInterval = setInterval(() => { _timerSeconds++; _updateTimer(); }, 1000);
  }

  function _pauseTimer() {
    clearInterval(_timerInterval);
    _timerInterval = null;
  }

  function _resetTimer() {
    _pauseTimer();
    _timerSeconds = 0;
    _updateTimer();
  }

  function _updateTimer() {
    const m = Math.floor(_timerSeconds / 60), s = _timerSeconds % 60;
    if (dom.rcTimer) {
      dom.rcTimer.textContent =
        `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }
  }

  // ── Session helpers ────────────────────────────────────────────────────────

  function _now() { return new Date().toISOString(); }

  function _updateSession(id, patch) {
    const s = _sessions.get(id);
    if (!s) return;
    Object.assign(s, patch);
    _render();
  }

  function _sortedSessions() {
    const active = ['recording', 'transcribing', 'paused'];
    return [..._sessions.values()].sort((a, b) => {
      const aActive = active.includes(a.status);
      const bActive = active.includes(b.status);
      if (aActive !== bActive) return aActive ? -1 : 1;
      return (b.created_at || '').localeCompare(a.created_at || '');
    });
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  function _render() {
    History.render(_sortedSessions(), _selectedId);

    const session = _selectedId ? _sessions.get(_selectedId) : null;
    if (!session) { _setView('idle'); return; }

    if (session.type === 'file') {
      if (session.status === 'transcribing') {
        _setView('transcribing');
        dom.progressFilename.textContent = session.filename;
      } else { // done
        _setView('file-done');
        if (session._data) {
          _showFileDone(session);
        } else {
          _loadTranscriptData(session);
        }
      }
    } else { // realtime
      if (session.status === 'recording') {
        _setView('realtime-rec');
        _syncControlBar(session);
      } else if (session.status === 'paused') {
        _setView('realtime-paused');
        _syncControlBar(session);
      } else { // done
        _setView('realtime-done');
        Player.load(session.audioPath || '');
        dom.playerFilename.textContent = session.filename;
        Summary.showForJob(session.id);
      }
    }
  }

  async function _loadTranscriptData(session) {
    try {
      const data = await window.pywebview.api.get_transcript(session.id);
      if (!data) return;
      session._data = data;
      if (_selectedId === session.id) _render();
    } catch (err) {
      console.error('[App] _loadTranscriptData error:', err);
    }
  }

  function _showFileDone(session) {
    const data = session._data;
    dom.playerFilename.textContent = data.audio || data.filename || session.filename;
    dom.transcriptMeta.textContent =
      `${(data.segments || []).length} segments · ${data.language || ''}`;
    Player.load(session.audioPath || '');
    Transcript.render(data, session.id, dom.transcriptList);
    Summary.showForJob(session.id);
  }

  // ── View ───────────────────────────────────────────────────────────────────

  function _setView(state) {
    dom.emptyHint.hidden          = state !== 'idle';
    dom.progressPanel.hidden      = state !== 'transcribing';
    dom.transcriptHeader.hidden   = state !== 'file-done';
    dom.transcriptList.hidden     = state !== 'file-done';
    dom.realtimePanel.hidden      =
      !['realtime-rec','realtime-paused','realtime-done'].includes(state);
    dom.playerBar.hidden          =
      !['file-done','realtime-done'].includes(state);
    dom.saveBtn.hidden            = state !== 'file-done';
    dom.realtimeControlBar.hidden =
      !['realtime-rec','realtime-paused'].includes(state);
  }

  function _syncControlBar(session) {
    dom.rcSessionName.textContent = session.filename;
    const isPaused = session.status === 'paused';
    dom.rcBtnPause.textContent = isPaused ? '▶' : '⏸';
    dom.rcBtnPause.title       = isPaused ? 'Resume' : 'Pause';
    dom.rcBtnPause.disabled    = false;
    dom.rcBtnPlay.disabled     = !isPaused;
  }

  // ── Control bar actions ────────────────────────────────────────────────────

  async function _onPauseResume() {
    const s = _sessions.get(_activeRealtimeId);
    if (!s) return;
    if (s.status === 'recording') {
      dom.rcBtnPause.disabled = true;
      await window.pywebview.api.pause_realtime();
    } else if (s.status === 'paused') {
      dom.rcBtnPause.disabled = true;
      await window.pywebview.api.resume_realtime();
    }
  }

  function _onPlayRecording() {
    const s = _sessions.get(_activeRealtimeId);
    if (!s || s.status !== 'paused') return;
    Player.load(s.audioPath || '');
  }

  // ── Realtime state ─────────────────────────────────────────────────────────

  function _onRealtimeState(state, sessionId, wavPath) {
    if (state === 'started') {
      _activeRealtimeId = sessionId;
      _sessions.set(sessionId, {
        id: sessionId, type: 'realtime', status: 'recording',
        filename: 'Recording ' + sessionId.slice(0, 8), created_at: _now(),
        audioPath: wavPath || null,
      });
      _selectedId = sessionId;
      _startTimer();
      _render();
    } else if (state === 'paused') {
      _updateSession(_activeRealtimeId, { status: 'paused' });
      _pauseTimer();
    } else if (state === 'resumed') {
      _updateSession(_activeRealtimeId, { status: 'recording' });
      _startTimer();
    } else if (state === 'stopped') {
      const rtSession = _sessions.get(_activeRealtimeId);
      const wavPath   = rtSession?.audioPath;
      const rtName    = rtSession?.filename;
      _sessions.delete(_activeRealtimeId);
      if (_selectedId === _activeRealtimeId) _selectedId = null;
      _activeRealtimeId = null;
      _resetTimer();
      if (wavPath) {
        (async () => { await _startTranscription(wavPath, rtName); })();
      } else {
        _render();
      }
    } else if (state === 'error') {
      if (_selectedId === _activeRealtimeId) _selectedId = null;
      _sessions.delete(_activeRealtimeId);
      _activeRealtimeId = null;
      _resetTimer();
      _render();
    }
  }

  // ── History ────────────────────────────────────────────────────────────────

  function _onHistorySelect(id) {
    Player.stop();
    _selectedId = id;
    _render();
  }

  function _onHistoryRename(id, newName) {
    if (!newName || !newName.trim()) return;
    window.pywebview.api.rename_session(id, newName.trim())
      .then(() => _updateSession(id, { filename: newName.trim() }))
      .catch(e => console.error('[App] rename_session error:', e));
  }

  function _onHistoryDelete(id) {
    if (!confirm('Delete this recording? This cannot be undone.')) return;
    window.pywebview.api.delete_session(id)
      .then(ok => {
        if (!ok) return;
        if (_selectedId === id) _selectedId = null;
        _sessions.delete(id);
        _render();
      })
      .catch(e => console.error('[App] delete_session error:', e));
  }

  async function _loadHistory() {
    try {
      const items = await window.pywebview.api.get_history();
      for (const item of items) {
        if (!_sessions.has(item.job_id)) {
          _sessions.set(item.job_id, {
            id:         item.job_id,
            type:       item.type || 'file',
            status:     'done',
            filename:   item.filename,
            created_at: item.date,
            duration:   item.duration,
            language:   item.language,
            audioPath:  item.audio_path || null,
          });
        }
      }
      _render();
    } catch (e) {
      console.warn('[App] _loadHistory error:', e);
    }
  }

  // ── Concurrency guards ────────────────────────────────────────────────────

  function _canStartRecording() {
    for (const s of _sessions.values()) {
      if (s.status === 'transcribing') {
        alert('A transcription is in progress. Please wait for it to finish before recording.');
        return false;
      }
    }
    return true;
  }

  // ── Upload / Transcription ─────────────────────────────────────────────────

  async function _onUpload() {
    if (_activeRealtimeId !== null) {
      alert('A recording is in progress. Please finish it before uploading a file.');
      return;
    }
    try {
      const path = await window.pywebview.api.select_file();
      if (path) _startTranscription(path);
    } catch (err) {
      console.error('[App] select_file error:', err);
    }
  }

  async function _startTranscription(filePath, displayName) {
    const filename = displayName || filePath.split('/').pop().split('\\').pop();
    try {
      const { job_id } = await window.pywebview.api.transcribe(filePath, {
        engine:       dom.selEngine.value,
        hf_token:     null,
        num_speakers: null,
      });
      _sessions.set(job_id, {
        id: job_id, type: 'file', status: 'transcribing',
        filename, created_at: _now(), audioPath: filePath,
      });
      _selectedId = job_id;
      _setProgress(0, 'Starting…');
      _render();
    } catch (err) {
      alert('Transcription failed: ' + err);
    }
  }

  // ── Progress / helpers ─────────────────────────────────────────────────────

  function _setProgress(pct, msg) {
    dom.progressBar.style.width = (pct * 100).toFixed(1) + '%';
    dom.progressMsg.textContent = msg;
  }

  function _updateTimeDisplay(t) {
    const fmt = (s) => {
      const m = Math.floor(s / 60), sec = Math.floor(s % 60);
      return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    };
    dom.playerTime.textContent = `${fmt(t)} / ${fmt(Player.duration)}`;
  }

  function _updateSaveStatus(count) {
    if (count === 0) {
      dom.saveStatus.textContent = '';
      dom.saveStatus.className   = 'save-status';
    } else {
      dom.saveStatus.textContent = `${count} unsaved`;
      dom.saveStatus.className   = 'save-status has-unsaved';
    }
  }

  // ── Python → JS event handlers ────────────────────────────────────────────

  function onTranscribeProgress(jobId, pct, message) {
    if (_selectedId !== jobId) return;
    const s = _sessions.get(jobId);
    if (!s || s.status !== 'transcribing') return;
    _setProgress(pct, message);
  }

  function onTranscribeComplete(jobId) {
    if (!_sessions.has(jobId)) return;
    _updateSession(jobId, { status: 'done' });
  }

  function onTranscribeError(jobId, message) {
    if (!_sessions.has(jobId)) return;
    if (_selectedId === jobId) _selectedId = null;
    _sessions.delete(jobId);
    _render();
    alert('Transcription error: ' + message);
  }

  // ── Init ───────────────────────────────────────────────────────────────────

  function init() {
    dom = {
      centerPanel:        document.getElementById('center-panel'),
      emptyHint:          document.getElementById('empty-hint'),
      progressPanel:      document.getElementById('progress-panel'),
      progressFilename:   document.getElementById('progress-filename'),
      progressBar:        document.getElementById('progress-bar'),
      progressMsg:        document.getElementById('progress-msg'),
      transcriptHeader:   document.getElementById('transcript-header'),
      transcriptMeta:     document.getElementById('transcript-meta'),
      transcriptList:     document.getElementById('transcript-list'),
      realtimePanel:      document.getElementById('realtime-panel'),
      realtimeControlBar: document.getElementById('realtime-control-bar'),
      playerBar:          document.getElementById('player-bar'),
      playerFilename:     document.getElementById('player-filename'),
      audioEl:            document.getElementById('audio-el'),
      playerTime:         document.getElementById('player-time'),
      saveBtn:            document.getElementById('btn-save'),
      saveStatus:         document.getElementById('save-status'),
      btnUpload:          document.getElementById('btn-upload'),
      selEngine:          document.getElementById('sel-engine'),
      rcSessionName:      document.getElementById('rc-session-name'),
      rcTimer:            document.getElementById('rc-timer'),
      rcBtnPause:         document.getElementById('rc-btn-pause'),
      rcBtnPlay:          document.getElementById('rc-btn-play'),
      rcBtnFinish:        document.getElementById('rc-btn-finish'),
    };

    Player.init(dom.audioEl);
    Summary.init();
    Realtime.init(_onRealtimeState, _canStartRecording);
    History.init(_onHistorySelect, _onHistoryRename, _onHistoryDelete);

    Player.onTimeUpdate((t) => {
      Transcript.highlightAt(t);
      _updateTimeDisplay(t);
    });

    dom.saveBtn.addEventListener('click', () => Transcript.saveAll());
    document.addEventListener('transcript:unsaved', (e) => {
      _updateSaveStatus(e.detail.count);
    });

    dom.btnUpload.addEventListener('click', _onUpload);

    dom.rcBtnPause.addEventListener('click', _onPauseResume);
    dom.rcBtnPlay.addEventListener('click', _onPlayRecording);
    dom.rcBtnFinish.addEventListener('click', () => window.pywebview.api.stop_realtime());

    // Drag-and-drop on center panel
    dom.centerPanel.addEventListener('dragover', (e) => {
      e.preventDefault();
      dom.centerPanel.classList.add('drag-over');
    });
    dom.centerPanel.addEventListener('dragleave', () =>
      dom.centerPanel.classList.remove('drag-over'));
    dom.centerPanel.addEventListener('dragend', () =>
      dom.centerPanel.classList.remove('drag-over'));
    dom.centerPanel.addEventListener('drop', (e) => {
      e.preventDefault();
      dom.centerPanel.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file) _startTranscription(file.path || file.name);
    });

    _loadHistory();
  }

  return { init, onTranscribeProgress, onTranscribeComplete, onTranscribeError };
})();

// ── Global event handlers (called by Python via evaluate_js) ──────────────────

function onTranscribeProgress(jobId, pct, message) { App.onTranscribeProgress(jobId, pct, message); }
function onTranscribeComplete(jobId, jsonPath)      { App.onTranscribeComplete(jobId, jsonPath); }
function onTranscribeError(jobId, message)          { App.onTranscribeError(jobId, message); }
function onModelDownloadProgress(name, percent)     { console.log(`[model] ${name} ${(percent * 100).toFixed(0)}%`); }
function onModelDownloadError(name, message)        { console.error(`[model] error: ${name} — ${message}`); }

// ── Bootstrap ─────────────────────────────────────────────────────────────────
// pywebview injects window.pywebview.api asynchronously after DOM is ready.
// Wait for 'pywebviewready' so _loadHistory() (and all other API calls in
// init) can actually reach the backend.
// Tests call App.init() directly — no bootstrap needed in that environment.

window.addEventListener('pywebviewready', () => App.init());
