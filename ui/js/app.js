/**
 * AudioAssist — main coordinator.
 *
 * State machine:
 *   idle         — drop zone visible, no file loaded
 *   transcribing — progress bar visible, pipeline running
 *   done         — transcript + player visible
 *
 * Python → JS event handlers (called via evaluate_js):
 *   onTranscribeProgress(jobId, pct, message)
 *   onTranscribeComplete(jobId, jsonPath)
 *   onTranscribeError(jobId, message)
 *   onModelDownloadProgress(name, percent)
 *   onModelDownloadError(name, message)
 */

// ── State ─────────────────────────────────────────────────────────────────────

const App = (() => {
  let _currentJobId   = null;
  let _currentAudioPath = null;

  // DOM refs (populated in init)
  let dom = {};

  // ── Init ───────────────────────────────────────────────────────────────────

  function init() {
    dom = {
      dropZone:           document.getElementById('drop-zone'),
      progressPanel:      document.getElementById('progress-panel'),
      progressFilename:   document.getElementById('progress-filename'),
      progressBar:        document.getElementById('progress-bar'),
      progressMsg:        document.getElementById('progress-msg'),
      transcriptHeader:   document.getElementById('transcript-header'),
      transcriptMeta:     document.getElementById('transcript-meta'),
      transcriptList:     document.getElementById('transcript-list'),
      realtimePanel:      document.getElementById('realtime-panel'),
      playerPanel:        document.getElementById('player-panel'),
      playerFilename:     document.getElementById('player-filename'),
      audioEl:            document.getElementById('audio-el'),
      playerTime:         document.getElementById('player-time'),
      saveBtn:            document.getElementById('btn-save'),
      saveStatus:         document.getElementById('save-status'),
      btnOpen:            document.getElementById('btn-open'),
      btnSelect:          document.getElementById('btn-select'),
      selEngine:          document.getElementById('sel-engine'),
    };

    Player.init(dom.audioEl);
    Summary.init();
    Realtime.init();

    // Player → transcript highlight sync
    Player.onTimeUpdate((t) => {
      Transcript.highlightAt(t);
      _updateTimeDisplay(t);
    });

    // Unsaved indicator
    document.addEventListener('transcript:unsaved', (e) => {
      _updateSaveStatus(e.detail.count);
    });

    // Toolbar buttons
    dom.btnOpen.addEventListener('click', _onOpenFile);
    if (dom.btnSelect) dom.btnSelect.addEventListener('click', _onOpenFile);
    dom.saveBtn.addEventListener('click', () => Transcript.saveAll());

    // Drag-and-drop on drop zone
    _initDragDrop();
  }

  // ── Drag-drop ──────────────────────────────────────────────────────────────

  function _initDragDrop() {
    const dz = dom.dropZone;

    dz.addEventListener('dragover',  (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
    dz.addEventListener('dragleave', ()  => dz.classList.remove('drag-over'));
    dz.addEventListener('dragend',   ()  => dz.classList.remove('drag-over'));

    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      dz.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (!file) return;
      // file.path is available in PyWebView (non-standard but supported)
      const path = file.path || file.name;
      _startTranscription(path);
    });
  }

  // ── File open ──────────────────────────────────────────────────────────────

  async function _onOpenFile() {
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

  // ── View helpers ───────────────────────────────────────────────────────────

  function _setView(state) {
    dom.dropZone.hidden         = state !== 'idle';
    dom.progressPanel.hidden    = state !== 'transcribing';
    dom.transcriptHeader.hidden = state !== 'done';
    dom.transcriptList.hidden   = state !== 'done';
    dom.saveBtn.hidden          = state !== 'done';
    dom.realtimePanel.hidden    = state !== 'realtime';
  }

  function _setProgress(pct, msg) {
    dom.progressBar.style.width = (pct * 100).toFixed(1) + '%';
    dom.progressMsg.textContent  = msg;
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
        `${data.segments.length} segments · ${data.language || ''}`;
      Player.load(_currentAudioPath);
      Transcript.render(data, jobId, dom.transcriptList);
      Summary.showForJob(jobId);
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

// ── Global event handlers (called by Python via evaluate_js) ─────────────────

function onTranscribeProgress(jobId, pct, message) {
  App.onTranscribeProgress(jobId, pct, message);
}

function onTranscribeComplete(jobId, jsonPath) {
  App.onTranscribeComplete(jobId, jsonPath);
}

function onTranscribeError(jobId, message) {
  App.onTranscribeError(jobId, message);
}

function onModelDownloadProgress(name, percent) {
  console.log(`[model] ${name} ${(percent * 100).toFixed(0)}%`);
}

function onModelDownloadError(name, message) {
  console.error(`[model] error: ${name} — ${message}`);
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', App.init);
} else {
  App.init();
}
