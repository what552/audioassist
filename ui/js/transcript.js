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
  // Shallow clone of each segment (see render() below) — words[] arrays are
  // shared references to the original objects from the Python API response.
  // Treat words[] as READ-ONLY; never mutate individual word objects in place.
  let _segments = [];
  let _unsavedCount = 0;
  let _speakerMenu = null;   // currently open popup, or null

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

    // Click on speaker label → rename menu
    speaker.addEventListener('click', (e) => {
      e.stopPropagation();
      _openSpeakerMenu(e, speaker, row);
    });

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

  // ── Speaker rename ───────────────────────────────────────────────────────────

  function _closeMenu() {
    if (_speakerMenu) {
      _speakerMenu.remove();
      _speakerMenu = null;
    }
  }

  function _openSpeakerMenu(e, speakerEl, row) {
    _closeMenu();
    const oldName = speakerEl.textContent;

    const menu = document.createElement('div');
    menu.className = 'speaker-menu';
    _speakerMenu = menu;

    const btnSingle = document.createElement('button');
    btnSingle.className = 'speaker-menu-item';
    btnSingle.textContent = 'Rename this';
    btnSingle.addEventListener('click', (ev) => {
      ev.stopPropagation();
      _closeMenu();
      _startSpeakerInput(speakerEl, row, false);
    });

    const btnAll = document.createElement('button');
    btnAll.className = 'speaker-menu-item';
    btnAll.textContent = `Rename all "${oldName}"`;
    btnAll.addEventListener('click', (ev) => {
      ev.stopPropagation();
      _closeMenu();
      _startSpeakerInput(speakerEl, row, true);
    });

    menu.append(btnSingle, btnAll);
    document.body.appendChild(menu);

    // Position below the clicked element
    const rect = speakerEl.getBoundingClientRect();
    menu.style.left = rect.left + 'px';
    menu.style.top  = (rect.bottom + 4) + 'px';

    // Dismiss on outside click
    const dismiss = (ev) => {
      if (!menu.contains(ev.target)) {
        _closeMenu();
        document.removeEventListener('mousedown', dismiss);
      }
    };
    setTimeout(() => document.addEventListener('mousedown', dismiss), 0);
  }

  function _startSpeakerInput(speakerEl, row, bulk) {
    const oldName = speakerEl.textContent;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'row-speaker-input';
    input.value = oldName;
    speakerEl.parentNode.insertBefore(input, speakerEl);
    speakerEl.classList.add('renaming');
    input.focus();
    input.select();

    async function confirm() {
      const newName = input.value.trim();
      cleanup();
      if (!newName || newName === oldName) return;
      await _applySpeakerRename(speakerEl, row, oldName, newName, bulk);
    }

    function cancel() { cleanup(); }

    function cleanup() {
      speakerEl.classList.remove('renaming');
      input.remove();
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter')  { e.preventDefault(); confirm(); }
      if (e.key === 'Escape') { cancel(); }
    });
    input.addEventListener('blur', confirm);
  }

  async function _applySpeakerRename(speakerEl, row, oldName, newName, bulk) {
    if (!window.pywebview) return;
    try {
      if (bulk) {
        const res = await window.pywebview.api.rename_speaker(_jobId, oldName, newName);
        if (!res || !res.ok) {
          console.error('[Transcript] rename_speaker failed:', res && res.error);
          return;
        }
        // Update all speaker spans and in-memory segments
        _segments.forEach((seg, i) => {
          if (seg.speaker === oldName) {
            seg.speaker = newName;
            const r = _container.querySelector(`.transcript-row[data-index="${i}"] .row-speaker`);
            if (r) r.textContent = newName;
          }
        });
      } else {
        const idx = parseInt(row.dataset.index, 10);
        const res = await window.pywebview.api.rename_segment_speaker(_jobId, idx, newName);
        if (!res || !res.ok) {
          console.error('[Transcript] rename_segment_speaker failed:', res && res.error);
          return;
        }
        _segments[idx].speaker = newName;
        speakerEl.textContent = newName;
      }
    } catch (err) {
      console.error('[Transcript] speaker rename error:', err);
    }
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
