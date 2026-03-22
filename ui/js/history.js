/**
 * History — left sidebar listing past jobs.
 *
 * Public API:
 *   History.init(onSelect)         — wire up DOM; onSelect(jobId, filename) on click
 *   History.reload()               — refresh the list from the backend
 *   History.setRecording(sessionId) — show a live "Recording…" placeholder at top
 *   History.clearRecording()       — remove the placeholder (called automatically by reload)
 */
const History = (() => {
  let dom = {};
  let _onSelect = null;
  let _recordingEl = null;

  // ── Init ───────────────────────────────────────────────────────────────────

  function init(onSelect) {
    _onSelect = onSelect;
    dom = {
      list: document.getElementById('history-list'),
    };
    reload();
  }

  // ── Recording placeholder ──────────────────────────────────────────────────

  function setRecording(sessionId) {
    clearRecording();

    const el = document.createElement('div');
    el.className = 'history-item history-item-recording';

    const name = document.createElement('div');
    name.className = 'history-item-name';
    name.textContent = '🔴 Recording…';

    const meta = document.createElement('div');
    meta.className = 'history-item-meta';
    meta.textContent = sessionId ? sessionId.slice(0, 8) + '…' : '';

    el.append(name, meta);
    _recordingEl = el;

    // Remove empty-hint if present, then prepend
    const emptyEl = dom.list.querySelector('.history-empty');
    if (emptyEl) emptyEl.remove();
    dom.list.prepend(el);
  }

  function clearRecording() {
    if (_recordingEl) {
      _recordingEl.remove();
      _recordingEl = null;
    }
  }

  // ── Reload ─────────────────────────────────────────────────────────────────

  async function reload() {
    clearRecording();
    try {
      const items = await window.pywebview.api.get_history();
      _render(items);
    } catch (e) {
      console.warn('[History] reload error:', e);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  function _render(items) {
    dom.list.innerHTML = '';
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = 'No recordings yet';
      dom.list.appendChild(empty);
      return;
    }
    items.forEach(item => dom.list.appendChild(_makeItem(item)));
  }

  function _makeItem(item) {
    const el = document.createElement('div');
    el.className = 'history-item';
    el.dataset.jobId = item.job_id;

    const name = document.createElement('div');
    name.className = 'history-item-name';
    name.textContent = item.filename || item.job_id;
    name.title = item.filename || item.job_id;

    const meta = document.createElement('div');
    meta.className = 'history-item-meta';
    const dur = item.duration ? _fmtDuration(item.duration) : '';
    meta.textContent = [item.date, dur].filter(Boolean).join(' · ');

    el.append(name, meta);
    el.addEventListener('click', () => {
      document.querySelectorAll('.history-item.active')
        .forEach(x => x.classList.remove('active'));
      el.classList.add('active');
      if (_onSelect) _onSelect(item.job_id, item.filename || item.job_id);
    });
    return el;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _fmtDuration(secs) {
    const m = Math.floor(secs / 60), s = secs % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  return { init, reload, setRecording, clearRecording };
})();
