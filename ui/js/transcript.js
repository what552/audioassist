/**
 * Transcript — speaker block list renderer + inline editor.
 *
 * Public API:
 *   Transcript.render(data, jobId, containerEl)
 *     data: {audio, language, segments: [{speaker, start, end, text, words}]}
 *   Transcript.highlightAt(seconds)   — called by Player.onTimeUpdate
 *   Transcript.saveAll()              — flush all unsaved edits via Python API
 *   Transcript.unsavedCount           — number of modified rows
 */
const Transcript = (() => {
  let _jobId = null;
  let _container = null;
  let _segments = [];   // original segment objects (includes words)
  let _unsavedCount = 0;

  // ── Render ───────────────────────────────────────────────────────────────────

  function render(data, jobId, container) {
    _jobId = jobId;
    _container = container;
    _segments = data.segments.map(s => Object.assign({}, s)); // shallow clone
    _unsavedCount = 0;
    container.innerHTML = '';

    _segments.forEach((seg, i) => {
      container.appendChild(_makeRow(seg, i));
    });

    _notifyUnsaved();
  }

  function _makeRow(seg, index) {
    const row = document.createElement('div');
    row.className = 'transcript-row';
    row.dataset.index = String(index);
    row.dataset.start = String(seg.start);
    row.dataset.end   = String(seg.end);

    const time = document.createElement('span');
    time.className = 'row-time';
    time.textContent = _fmt(seg.start);

    const speaker = document.createElement('span');
    speaker.className = 'row-speaker';
    speaker.textContent = seg.speaker;

    const text = document.createElement('span');
    text.className = 'row-text';
    text.textContent = seg.text;

    row.append(time, speaker, text);

    // Click → seek (ignore if clicking inside an active contenteditable)
    row.addEventListener('click', (e) => {
      if (text.contentEditable === 'true') return;
      Player.seekTo(parseFloat(row.dataset.start));
    });

    // Double-click on text → inline edit
    text.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      _startEdit(text, row);
    });

    return row;
  }

  // ── Inline editing ────────────────────────────────────────────────────────────

  function _startEdit(textEl, row) {
    if (textEl.contentEditable === 'true') return;

    const original = textEl.textContent;
    textEl.contentEditable = 'true';
    textEl.classList.add('editing');
    textEl.focus();

    // Select all text for easy replacement
    const range = document.createRange();
    range.selectNodeContents(textEl);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);

    function finish(save) {
      textEl.contentEditable = 'false';
      textEl.classList.remove('editing');
      if (save && textEl.textContent.trim() !== original.trim()) {
        _markUnsaved(row);
      } else if (!save) {
        textEl.textContent = original;
      }
      cleanup();
    }

    function onBlur()    { finish(true); }
    function onKeydown(e) {
      if (e.key === 'Enter')  { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { finish(false); }
    }
    function cleanup() {
      textEl.removeEventListener('blur',    onBlur);
      textEl.removeEventListener('keydown', onKeydown);
    }

    textEl.addEventListener('blur',    onBlur);
    textEl.addEventListener('keydown', onKeydown);
  }

  function _markUnsaved(row) {
    if (!row.classList.contains('unsaved')) {
      row.classList.add('unsaved');
      _unsavedCount++;
      _notifyUnsaved();
    }
  }

  function _notifyUnsaved() {
    document.dispatchEvent(
      new CustomEvent('transcript:unsaved', { detail: { count: _unsavedCount } })
    );
  }

  // ── Playback highlight ────────────────────────────────────────────────────────

  function highlightAt(seconds) {
    if (!_container) return;
    let activeRow = null;

    for (const row of _container.querySelectorAll('.transcript-row')) {
      const start = parseFloat(row.dataset.start);
      const end   = parseFloat(row.dataset.end);
      const active = seconds >= start && seconds < end;
      row.classList.toggle('active', active);
      if (active) activeRow = row;
    }

    // Scroll active row into view (only if not currently editing)
    if (activeRow && !activeRow.querySelector('[contenteditable="true"]')) {
      activeRow.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  // ── Save ──────────────────────────────────────────────────────────────────────

  function _collectEdits() {
    if (!_container) return [];
    return Array.from(_container.querySelectorAll('.transcript-row')).map(row => {
      const i   = parseInt(row.dataset.index, 10);
      const seg = _segments[i];            // original (preserves words[])
      return Object.assign({}, seg, {
        text:    row.querySelector('.row-text').textContent,
        speaker: row.querySelector('.row-speaker').textContent,
      });
    });
  }

  async function saveAll() {
    if (!_jobId || _unsavedCount === 0) return;
    try {
      const edits = _collectEdits();
      const ok = await window.pywebview.api.save_transcript(_jobId, edits);
      if (ok) {
        _container.querySelectorAll('.transcript-row.unsaved')
          .forEach(r => r.classList.remove('unsaved'));
        _unsavedCount = 0;
        _notifyUnsaved();
      }
    } catch (err) {
      console.error('[Transcript] saveAll failed:', err);
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function _fmt(s) {
    const m   = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  }

  return {
    render,
    highlightAt,
    saveAll,
    get unsavedCount() { return _unsavedCount; },
  };
})();
