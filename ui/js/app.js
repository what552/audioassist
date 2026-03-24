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

  // ── Export menu (shared by transcript + summary export buttons) ────────────
  let _exportMenuEl  = null;
  let _toastTimer    = null;

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
    if (!session) { _setView('idle'); Summary.reset(); return; }

    if (session.type === 'file') {
      if (session.status === 'transcribing') {
        _setView('transcribing');
        dom.progressFilename.textContent = session.filename;
        Summary.reset();
      } else if (session.status === 'error') {
        _setView('error');
        dom.errorFilename.textContent = session.filename;
        dom.errorMsg.textContent = session.errorMsg || 'Unknown error';
        Summary.reset();
      } else if (session.status === 'refining') {
        _setView('file-done');
        dom.refineHint.hidden = false;
        dom.refineHint.textContent = '正在进行高精度转写…';
        if (session._data) {
          _showFileDone(session);
        } else {
          _loadTranscriptData(session);
        }
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
        Summary.reset();
      } else if (session.status === 'paused') {
        _setView('realtime-paused');
        _syncControlBar(session);
        Summary.reset();
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
    dom.setupPanel.hidden         = state !== 'setup';
    dom.emptyHint.hidden          = state !== 'idle';
    dom.progressPanel.hidden      = state !== 'transcribing';
    dom.errorPanel.hidden         = state !== 'error';
    dom.transcriptHeader.hidden   = state !== 'file-done';
    dom.refineHint.hidden         = true;  // controlled separately per render
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

  async function _onFinishRecording() {
    const secs = _timerSeconds;
    if (secs < 5) {
      const keep = confirm('录音时长不足 5 秒，是否保留？');
      if (!keep) {
        const s = _sessions.get(_activeRealtimeId);
        if (s) s.discarding = true;
      }
    }
    await window.pywebview.api.stop_realtime();
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
      const discard   = rtSession?.discarding;
      const rtId      = _activeRealtimeId;
      _sessions.delete(_activeRealtimeId);
      if (_selectedId === _activeRealtimeId) _selectedId = null;
      _activeRealtimeId = null;
      _resetTimer();
      if (discard) {
        if (rtId) window.pywebview.api.delete_session(rtId).catch(() => {});
        _render();
      } else if (wavPath) {
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
    if (_activeRealtimeId !== null) {
      alert('A recording is in progress. Please finish it before uploading a file.');
      return;
    }
    const filename = displayName || filePath.split('/').pop().split('\\').pop();
    try {
      const { job_id } = await window.pywebview.api.transcribe(filePath, {
        model_id:     dom.selEngine.value || null,
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

  function onTranscribeComplete(jobId, _jsonPath, hasRefine) {
    if (!_sessions.has(jobId)) return;
    _updateSession(jobId, { status: hasRefine ? 'refining' : 'done' });
  }

  function onTranscribeRefined(jobId) {
    if (!_sessions.has(jobId)) return;
    const s = _sessions.get(jobId);
    s._data = null;  // force JSON reload
    _updateSession(jobId, { status: 'done' });
  }

  function onTranscribeError(jobId, message) {
    if (!_sessions.has(jobId)) return;
    _updateSession(jobId, { status: 'error', errorMsg: message });
  }

  function onTranscribeCancel(jobId) {
    if (!_sessions.has(jobId)) return;
    if (_selectedId === jobId) _selectedId = null;
    _sessions.delete(jobId);
    _render();
  }

  // ── Init ───────────────────────────────────────────────────────────────────

  // ── Model IDs used by the setup panel ──────────────────────────────────────
  const _SETUP_ASR_ID      = 'qwen3-asr-1.7b';
  const _SETUP_DIARIZER_ID = 'pyannote-diarization-community-1';

  function init() {
    dom = {
      centerPanel:        document.getElementById('center-panel'),
      setupPanel:         document.getElementById('setup-panel'),
      setupAsrBadge:      document.getElementById('setup-asr-badge'),
      setupAsrProgress:   document.getElementById('setup-asr-progress'),
      setupAsrBar:        document.getElementById('setup-asr-bar'),
      setupDiarizerBadge: document.getElementById('setup-diarizer-badge'),
      setupDiarizerProgress: document.getElementById('setup-diarizer-progress'),
      setupDiarizerBar:   document.getElementById('setup-diarizer-bar'),
      btnSetupAsr:        document.getElementById('btn-setup-asr'),
      btnSetupDiarizer:   document.getElementById('btn-setup-diarizer'),
      btnModels:          document.getElementById('btn-models'),
      modelsModal:        document.getElementById('models-modal'),
      btnModelsClose:     document.getElementById('btn-models-close'),
      modelsList:         document.getElementById('models-list'),
      emptyHint:          document.getElementById('empty-hint'),
      progressPanel:      document.getElementById('progress-panel'),
      progressFilename:   document.getElementById('progress-filename'),
      progressBar:        document.getElementById('progress-bar'),
      progressMsg:        document.getElementById('progress-msg'),
      btnCancelTranscribe: document.getElementById('btn-cancel-transcribe'),
      errorPanel:         document.getElementById('error-panel'),
      errorFilename:      document.getElementById('error-filename'),
      errorMsg:           document.getElementById('error-msg'),
      btnRetry:           document.getElementById('btn-retry'),
      transcriptHeader:   document.getElementById('transcript-header'),
      transcriptMeta:     document.getElementById('transcript-meta'),
      refineHint:         document.getElementById('refine-hint'),
      transcriptList:     document.getElementById('transcript-list'),
      realtimePanel:      document.getElementById('realtime-panel'),
      realtimeControlBar: document.getElementById('realtime-control-bar'),
      playerBar:          document.getElementById('player-bar'),
      playerFilename:     document.getElementById('player-filename'),
      audioEl:            document.getElementById('audio-el'),
      playerTime:         document.getElementById('player-time'),
      saveBtn:              document.getElementById('btn-save'),
      saveStatus:           document.getElementById('save-status'),
      btnExportTranscript:  document.getElementById('btn-export-transcript'),
      toast:                document.getElementById('toast'),
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

    // Spacebar: toggle play/pause; never activate buttons (prevents accidental recording)
    document.addEventListener('keydown', (e) => {
      if (e.code !== 'Space') return;
      const active = document.activeElement;
      const tag = active && active.tagName;
      // Let space type in text fields
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (active && active.isContentEditable) return;
      // Blur any focused button so space can't click it (e.g. Start Recording)
      if (tag === 'BUTTON') active.blur();
      e.preventDefault();
      if (!dom.playerBar.hidden) {
        if (dom.audioEl.paused) Player.play(); else Player.pause();
      }
    });

    dom.saveBtn.addEventListener('click', () => Transcript.saveAll());
    document.addEventListener('transcript:unsaved', (e) => {
      _updateSaveStatus(e.detail.count);
    });

    dom.btnExportTranscript.addEventListener('click', (e) => {
      openExportMenu(e, (fmt) => _exportTranscript(fmt));
    });

    dom.btnUpload.addEventListener('click', _onUpload);

    dom.rcBtnPause.addEventListener('click', _onPauseResume);
    dom.rcBtnPlay.addEventListener('click', _onPlayRecording);
    dom.rcBtnFinish.addEventListener('click', _onFinishRecording);

    // Models modal
    dom.btnModels.addEventListener('click', _openModelsModal);
    dom.btnModelsClose.addEventListener('click', _closeModelsModal);
    dom.modelsModal.addEventListener('click', (e) => {
      if (e.target === dom.modelsModal) _closeModelsModal();
    });

    // Setup panel — download buttons
    dom.btnSetupAsr.addEventListener('click', () => _setupDownload('asr'));
    dom.btnSetupDiarizer.addEventListener('click', () => _setupDownload('diarizer'));

    // Cancel transcription
    dom.btnCancelTranscribe.addEventListener('click', () => {
      if (_selectedId) window.pywebview.api.cancel_transcription(_selectedId);
    });

    // Retry failed transcription
    dom.btnRetry.addEventListener('click', () => {
      const s = _selectedId ? _sessions.get(_selectedId) : null;
      if (!s || s.status !== 'error' || !s.audioPath) return;
      const filePath    = s.audioPath;
      const displayName = s.filename;
      _sessions.delete(_selectedId);
      _selectedId = null;
      _startTranscription(filePath, displayName);
    });

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

    _checkSetup();
  }

  // ── Setup panel logic ───────────────────────────────────────────────────────

  async function _checkSetup() {
    try {
      const status = await window.pywebview.api.get_setup_status();
      _applySetupStatus(status);
      if (status.asr_ready && status.diarizer_ready) {
        _setView('idle');
        _populateEngineSelector();
        _loadHistory();
      } else {
        _setView('setup');
      }
    } catch (e) {
      console.warn('[App] _checkSetup error:', e);
      _loadHistory();  // fail-open: if API unavailable, proceed normally
    }
  }

  function _applySetupStatus(status) {
    _setSetupItem('asr', status.asr_ready);
    _setSetupItem('diarizer', status.diarizer_ready);
  }

  function _setSetupItem(key, ready) {
    const badge = key === 'asr' ? dom.setupAsrBadge : dom.setupDiarizerBadge;
    const btn   = key === 'asr' ? dom.btnSetupAsr  : dom.btnSetupDiarizer;
    if (ready) {
      badge.textContent = '✓ Ready';
      badge.className   = 'setup-badge setup-badge-ok';
      btn.disabled      = true;
    } else {
      badge.textContent = 'Not downloaded';
      badge.className   = 'setup-badge setup-badge-pending';
      btn.disabled      = false;
    }
  }

  async function _setupDownload(key) {
    const modelId = key === 'asr' ? _SETUP_ASR_ID : _SETUP_DIARIZER_ID;
    const badge   = key === 'asr' ? dom.setupAsrBadge       : dom.setupDiarizerBadge;
    const bar     = key === 'asr' ? dom.setupAsrBar         : dom.setupDiarizerBar;
    const prog    = key === 'asr' ? dom.setupAsrProgress    : dom.setupDiarizerProgress;
    const btn     = key === 'asr' ? dom.btnSetupAsr         : dom.btnSetupDiarizer;

    btn.disabled      = true;
    badge.textContent = 'Downloading…';
    badge.className   = 'setup-badge setup-badge-pending';
    prog.hidden       = false;
    prog.classList.add('indeterminate');

    try {
      await window.pywebview.api.download_model(modelId);
    } catch (e) {
      badge.textContent = 'Error';
      badge.className   = 'setup-badge setup-badge-error';
      btn.disabled      = false;
    }
    // Progress arrives via onModelDownloadProgress; completion via pct === 1.0
  }

  // ── Models modal ────────────────────────────────────────────────────────────

  function _openModelsModal() {
    dom.modelsModal.hidden = false;
    _loadModelsModal();
  }

  function _closeModelsModal() {
    dom.modelsModal.hidden = true;
  }

  async function _loadModelsModal() {
    dom.modelsList.innerHTML = '<p style="padding:12px;color:var(--text-muted)">Loading…</p>';
    try {
      const models = await window.pywebview.api.get_models();
      _renderModels(models);
    } catch (e) {
      dom.modelsList.innerHTML =
        `<p style="padding:12px;color:var(--text-muted)">Error: ${e}</p>`;
    }
  }

  const _ROLE_ORDER = ['asr', 'aligner', 'diarizer'];
  const _ROLE_LABEL = { asr: 'ASR Models', aligner: 'Aligner Models', diarizer: 'Diarizer Models' };

  function _renderModels(models) {
    const grouped = {};
    for (const m of models) {
      if (!grouped[m.role]) grouped[m.role] = [];
      grouped[m.role].push(m);
    }
    dom.modelsList.innerHTML = '';
    for (const role of _ROLE_ORDER) {
      if (!grouped[role]) continue;
      const header = document.createElement('div');
      header.className = 'model-role-header';
      header.textContent = _ROLE_LABEL[role] || role;
      dom.modelsList.appendChild(header);
      for (const m of grouped[role]) {
        dom.modelsList.appendChild(_makeModelItem(m));
      }
    }
  }

  function _makeModelItem(m) {
    const div = document.createElement('div');
    div.className = 'model-item';
    div.dataset.modelId = m.id;

    const starMark = m.recommended ? ' <span class="model-recommended">★</span>' : '';
    const badgeClass = m.downloaded ? 'model-badge-ok'
                     : m.incomplete ? 'model-badge-incomplete'
                     : 'model-badge-pending';
    const badgeText  = m.downloaded ? '✓ Downloaded'
                     : m.incomplete ? '⚠ Incomplete'
                     : 'Not downloaded';

    div.innerHTML = `
      <div class="model-item-info">
        <span class="model-item-name">${m.name}${starMark}</span>
        <span class="model-item-size">${m.size_gb} GB</span>
        <span class="model-item-badge ${badgeClass}">${badgeText}</span>
      </div>
      <div class="model-item-desc">${m.description}</div>
      <div class="model-item-progress" hidden>
        <div class="model-item-bar" style="width:0%"></div>
      </div>
      <div class="model-item-actions">
        <button class="btn-primary btn-sm model-btn-download"
          ${m.downloaded ? 'disabled' : ''}>Download</button>
        <button class="btn-link btn-sm model-btn-delete"
          ${!m.downloaded ? 'disabled' : ''}>Delete</button>
      </div>
    `;

    div.querySelector('.model-btn-download').addEventListener('click',
      () => _downloadModelItem(m.id, div));
    div.querySelector('.model-btn-delete').addEventListener('click',
      () => _deleteModelItem(m.id, div));

    return div;
  }

  async function _downloadModelItem(modelId, itemEl) {
    const btn   = itemEl.querySelector('.model-btn-download');
    const badge = itemEl.querySelector('.model-item-badge');
    const prog  = itemEl.querySelector('.model-item-progress');
    btn.disabled      = true;
    badge.textContent = 'Downloading…';
    badge.className   = 'model-item-badge model-badge-pending';
    prog.hidden       = false;
    prog.classList.add('indeterminate');
    try {
      await window.pywebview.api.download_model(modelId);
    } catch (e) {
      badge.textContent = 'Error';
      btn.disabled = false;
    }
    // Progress updates arrive via onModelDownloadProgress; completion at pct >= 1.0
  }

  // ── Engine selector ─────────────────────────────────────────────────────────

  async function _populateEngineSelector() {
    try {
      const models = await window.pywebview.api.get_models();
      const asrModels = models.filter(m => m.role === 'asr' && m.downloaded);
      const prev = dom.selEngine.value;
      dom.selEngine.innerHTML = '';
      if (asrModels.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '— No ASR model downloaded —';
        opt.disabled = true;
        opt.selected = true;
        dom.selEngine.appendChild(opt);
      } else {
        for (const m of asrModels) {
          const opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.name + (m.recommended ? ' ★' : '');
          if (m.id === prev) opt.selected = true;
          dom.selEngine.appendChild(opt);
        }
        // If previous selection is no longer available, first option is selected by default
      }
    } catch (e) {
      console.warn('[App] _populateEngineSelector error:', e);
    }
  }

  async function _deleteModelItem(modelId, itemEl) {
    if (!confirm(`Delete model "${modelId}"?\nThis frees disk space but requires re-download to use.`)) return;
    const deleteBtn = itemEl.querySelector('.model-btn-delete');
    deleteBtn.disabled = true;
    try {
      const result      = await window.pywebview.api.delete_model(modelId);
      const badge       = itemEl.querySelector('.model-item-badge');
      const downloadBtn = itemEl.querySelector('.model-btn-download');
      // result.downloaded may be true when HF cache still has the model
      if (result && result.downloaded) {
        badge.textContent = '✓ Downloaded';
        badge.className   = 'model-item-badge model-badge-ok';
        downloadBtn.disabled = true;
        deleteBtn.disabled   = false;
      } else {
        badge.textContent = 'Not downloaded';
        badge.className   = 'model-item-badge model-badge-pending';
        downloadBtn.disabled = false;
        deleteBtn.disabled   = true;
      }
    } catch (e) {
      console.error('[Models] delete_model error:', e);
      deleteBtn.disabled = false;
    }
    _populateEngineSelector();
  }

  // ── Export dropdown (shared with summary.js via App.openExportMenu) ─────────

  function openExportMenu(e, onSelect) {
    if (_exportMenuEl) { _exportMenuEl.remove(); _exportMenuEl = null; }
    const menu = document.createElement('div');
    menu.className = 'export-menu';
    _exportMenuEl = menu;
    ['txt', 'md'].forEach(fmt => {
      const btn = document.createElement('button');
      btn.className = 'export-item';
      btn.textContent = `Export as ${fmt.toUpperCase()}`;
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        menu.remove(); _exportMenuEl = null;
        onSelect(fmt);
      });
      menu.appendChild(btn);
    });
    document.body.appendChild(menu);
    const rect = e.currentTarget.getBoundingClientRect();
    menu.style.left = rect.left + 'px';
    menu.style.top  = (rect.bottom + 4) + 'px';
    const dismiss = (ev) => {
      if (!menu.contains(ev.target)) {
        menu.remove(); _exportMenuEl = null;
        document.removeEventListener('mousedown', dismiss);
      }
    };
    setTimeout(() => document.addEventListener('mousedown', dismiss), 0);
  }

  function showToast(msg, duration = 2500) {
    if (!dom.toast) return;
    dom.toast.textContent = msg;
    dom.toast.hidden = false;
    dom.toast.classList.remove('toast-fade');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => {
      dom.toast.classList.add('toast-fade');
      setTimeout(() => { dom.toast.hidden = true; }, 300);
    }, duration);
  }

  async function _exportTranscript(fmt) {
    const jobId = _selectedId;
    if (!jobId || !window.pywebview) return;
    try {
      const res = await window.pywebview.api.export_transcript(jobId, fmt);
      if (res && res.status === 'saved') {
        const name = res.path.replace(/\\/g, '/').split('/').pop();
        showToast(`已保存到 ${name}`);
      }
    } catch (err) {
      console.error('[App] export_transcript error:', err);
    }
  }

  return { init, onTranscribeProgress, onTranscribeComplete, onTranscribeError,
           onTranscribeCancel, onTranscribeRefined,
           refreshEngineSelector: _populateEngineSelector,
           openExportMenu, showToast };
})();

// ── Global event handlers (called by Python via evaluate_js) ──────────────────

function onTranscribeProgress(jobId, pct, message) { App.onTranscribeProgress(jobId, pct, message); }
function onTranscribeComplete(jobId, jsonPath, hasRefine) { App.onTranscribeComplete(jobId, jsonPath, hasRefine); }
function onTranscribeError(jobId, message)          { App.onTranscribeError(jobId, message); }
function onTranscribeCancel(jobId)                  { App.onTranscribeCancel(jobId); }
function onTranscribeRefined(jobId)                 { App.onTranscribeRefined(jobId); }

function onModelDownloadProgress(name, percent) {
  console.log(`[model] ${name} ${(percent * 100).toFixed(0)}%`);

  // ── Update setup panel bars (only for the two required models) ──────────────
  const isSetupAsr  = name === 'qwen3-asr-1.7b';
  const isSetupDiar = name === 'pyannote-diarization-community-1';
  if (isSetupAsr || isSetupDiar) {
    const prog = isSetupAsr
      ? document.getElementById('setup-asr-progress')
      : document.getElementById('setup-diarizer-progress');
    const bar = isSetupAsr
      ? document.getElementById('setup-asr-bar')
      : document.getElementById('setup-diarizer-bar');
    if (prog) prog.classList.remove('indeterminate');
    if (bar) bar.style.width = (percent * 100).toFixed(1) + '%';

    if (percent >= 1.0) {
      if (window.pywebview?.api?.get_setup_status) {
        window.pywebview.api.get_setup_status().then(status => {
          const badge = isSetupAsr
            ? document.getElementById('setup-asr-badge')
            : document.getElementById('setup-diarizer-badge');
          if (badge) {
            badge.textContent = '✓ Ready';
            badge.className   = 'setup-badge setup-badge-ok';
          }
          if (status.asr_ready && status.diarizer_ready) {
            App.init._checkSetup?.() || location.reload();
          }
        }).catch(() => {});
      }
    }
  }

  // ── Update models modal item (if the modal is open) ─────────────────────────
  const modalItem = document.querySelector(
    `.model-item[data-model-id="${CSS.escape(name)}"]`
  );
  if (modalItem) {
    const prog = modalItem.querySelector('.model-item-progress');
    const bar  = modalItem.querySelector('.model-item-bar');
    if (prog) prog.classList.remove('indeterminate');
    if (bar) bar.style.width = (percent * 100).toFixed(1) + '%';

    if (percent >= 1.0) {
      const badge       = modalItem.querySelector('.model-item-badge');
      const downloadBtn = modalItem.querySelector('.model-btn-download');
      const deleteBtn   = modalItem.querySelector('.model-btn-delete');
      if (badge)       { badge.textContent = '✓ Downloaded'; badge.className = 'model-item-badge model-badge-ok'; }
      if (prog)        prog.hidden = true;
      if (downloadBtn) downloadBtn.disabled = true;
      if (deleteBtn)   deleteBtn.disabled   = false;
      // Refresh engine selector in case an ASR model just became available
      App.refreshEngineSelector();
    }
  }
}

function onModelDownloadError(name, message) {
  console.error(`[model] error: ${name} — ${message}`);
  const isAsr = name === 'qwen3-asr-1.7b';
  const isDiar = name === 'pyannote-diarization-community-1';
  if (!isAsr && !isDiar) return;
  const badge = isAsr
    ? document.getElementById('setup-asr-badge')
    : document.getElementById('setup-diarizer-badge');
  const btn = isAsr
    ? document.getElementById('btn-setup-asr')
    : document.getElementById('btn-setup-diarizer');
  if (badge) { badge.textContent = 'Error'; badge.className = 'setup-badge setup-badge-error'; }
  if (btn)   btn.disabled = false;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
// pywebview injects window.pywebview.api asynchronously after DOM is ready.
// Wait for 'pywebviewready' so _loadHistory() (and all other API calls in
// init) can actually reach the backend.
// Tests call App.init() directly — no bootstrap needed in that environment.

window.addEventListener('pywebviewready', () => App.init());
