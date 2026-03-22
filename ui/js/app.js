/**
 * AudioAssist — main coordinator.
 *
 * State machine (center panel):
 *   idle         — empty hint visible
 *   transcribing — progress bar visible
 *   done         — player + transcript visible
 *   realtime     — realtime panel visible
 *
 * Python → JS event handlers (called via evaluate_js):
 *   onTranscribeProgress(jobId, pct, message)
 *   onTranscribeComplete(jobId, jsonPath)
 *   onTranscribeError(jobId, message)
 *   onModelDownloadProgress(name, percent)
 *   onModelDownloadError(name, message)
 */
const App = (() => {
  let _currentJobId     = null;
  let _currentAudioPath = null;
  let dom = {};

  // ── Init ───────────────────────────────────────────────────────────────────

  function init() {
    dom = {
      centerPanel:      document.getElementById('center-panel'),
      emptyHint:        document.getElementById('empty-hint'),
      progressPanel:    document.getElementById('progress-panel'),
      progressFilename: document.getElementById('progress-filename'),
      progressBar:      document.getElementById('progress-bar'),
      progressMsg:      document.getElementById('progress-msg'),
      transcriptHeader: document.getElementById('transcript-header'),
      transcriptMeta:   document.getElementById('transcript-meta'),
      transcriptList:   document.getElementById('transcript-list'),
      realtimePanel:    document.getElementById('realtime-panel'),
      playerBar:        document.getElementById('player-bar'),
      playerFilename:   document.getElementById('player-filename'),
      audioEl:          document.getElementById('audio-el'),
      playerTime:       document.getElementById('player-time'),
      saveBtn:          document.getElementById('btn-save'),
      saveStatus:       document.getElementById('save-status'),
      btnUpload:        document.getElementById('btn-upload'),
      selEngine:        document.getElementById('sel-engine'),
    };

    Player.init(dom.audioEl);
    Summary.init();
    Realtime.init(_onRealtimeState);
    History.init(_onHistorySelect);

    // Player → transcript highlight + time display
    Player.onTimeUpdate((t) => {
      Transcript.highlightAt(t);
      _updateTimeDisplay(t);
    });

    // Save button / unsaved indicator
    dom.saveBtn.addEventListener('click', () => Transcript.saveAll());
    document.addEventListener('transcript:unsaved', (e) => {
      _updateSaveStatus(e.detail.count);
    });

    // Upload button → file picker
    dom.btnUpload.addEventListener('click', _onUpload);

    // Drag-and-drop on center panel
    dom.centerPanel.addEventListener('dragover', (e) => {
      e.preventDefault();
      dom.centerPanel.classList.add('drag-over');
    });
    dom.centerPanel.addEventListener('dragleave', () => dom.centerPanel.classList.remove('drag-over'));
    dom.centerPanel.addEventListener('dragend',   () => dom.centerPanel.classList.remove('drag-over'));
    dom.centerPanel.addEventListener('drop', (e) => {
      e.preventDefault();
      dom.centerPanel.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file) _startTranscription(file.path || file.name);
    });
  }

  // ── History select ─────────────────────────────────────────────────────────

  async function _onHistorySelect(jobId, filename) {
    try {
      const data = await window.pywebview.api.get_transcript(jobId);
      if (!data) return;
      _currentJobId = jobId;
      _currentAudioPath = null;
      _setView('done');
      dom.playerFilename.textContent = data.audio || data.filename || filename;
      dom.transcriptMeta.textContent =
        `${(data.segments || []).length} segments · ${data.language || ''}`;
      Player.load('');  // no audio path for history items without original file
      Transcript.render(data, jobId, dom.transcriptList);
      Summary.showForJob(jobId);
    } catch (err) {
      console.error('[App] _onHistorySelect error:', err);
    }
  }

  // ── Upload ─────────────────────────────────────────────────────────────────

  async function _onUpload() {
    try {
      const path = await window.pywebview.api.select_file();
      if (path) _startTranscription(path);
    } catch (err) {
      console.error('[App] select_file error:', err);
    }
  }

  // ── Transcription ──────────────────────────────────────────────────────────

  async function _startTranscription(filePath) {
    _currentAudioPath = filePath;
    const filename = filePath.split('/').pop().split('\\').pop();
    _setView('transcribing');
    dom.progressFilename.textContent = filename;
    _setProgress(0, 'Starting…');

    const options = {
      engine:       dom.selEngine.value,
      hf_token:     null,
      num_speakers: null,
    };

    try {
      const { job_id } = await window.pywebview.api.transcribe(filePath, options);
      _currentJobId = job_id;
    } catch (err) {
      _setView('idle');
      alert('Transcription failed: ' + err);
    }
  }

  // ── Realtime state callback ────────────────────────────────────────────────

  function _onRealtimeState(state, sessionId) {
    if (state === 'started') {
      _setView('realtime');
    } else if (state === 'stopped') {
      History.reload();
      _setView('idle');
    } else if (state === 'error') {
      _setView('idle');
    }
  }

  // ── View helpers ───────────────────────────────────────────────────────────

  function _setView(state) {
    dom.emptyHint.hidden        = state !== 'idle';
    dom.progressPanel.hidden    = state !== 'transcribing';
    dom.transcriptHeader.hidden = state !== 'done';
    dom.transcriptList.hidden   = state !== 'done';
    dom.realtimePanel.hidden    = state !== 'realtime';
    dom.playerBar.hidden        = state !== 'done';
    dom.saveBtn.hidden          = state !== 'done';
  }

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
    if (jobId !== _currentJobId && _currentJobId !== null) return;
    _setProgress(pct, message);
  }

  async function onTranscribeComplete(jobId, jsonPath) {
    if (jobId !== _currentJobId) return;
    try {
      const data = await window.pywebview.api.get_transcript(jobId);
      if (!data) { _setView('idle'); return; }

      _setView('done');
      dom.playerFilename.textContent = data.audio || '';
      dom.transcriptMeta.textContent =
        `${(data.segments || []).length} segments · ${data.language || ''}`;
      Player.load(_currentAudioPath);
      Transcript.render(data, jobId, dom.transcriptList);
      Summary.showForJob(jobId);
      History.reload();
    } catch (err) {
      console.error('[App] onTranscribeComplete error:', err);
      _setView('idle');
    }
  }

  function onTranscribeError(jobId, message) {
    if (jobId !== _currentJobId) return;
    _setView('idle');
    alert('Transcription error: ' + message);
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
// Wait for 'pywebviewready' so History.reload() (and all other API calls in
// init) can actually reach the backend.  Fall back to DOMContentLoaded for
// non-pywebview environments (unit tests, browser dev-mode).

let _initDone = false;
function _initOnce() {
  if (_initDone) return;
  _initDone = true;
  App.init();
}

window.addEventListener('pywebviewready', _initOnce);

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    // Give pywebview a short grace period; if it fires first we're already done.
    if (!window.pywebview) setTimeout(_initOnce, 0);
  });
} else {
  if (!window.pywebview) setTimeout(_initOnce, 0);
}
