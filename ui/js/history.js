/**
 * History — left sidebar listing sessions.
 *
 * Pure view module: no backend calls, no internal state management.
 * App drives all data via render().
 *
 * Public API:
 *   History.init(onSelect)              — wire up DOM; onSelect(sessionId) on click
 *   History.render(sessions, selectedId) — redraw list from session array
 */
const History = (() => {
  let dom = {};
  let _onSelect = null;

  // ── Init ───────────────────────────────────────────────────────────────────

  function init(onSelect) {
    _onSelect = onSelect;
    dom = {
      list: document.getElementById('history-list'),
    };
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  function render(sessions, selectedId) {
    dom.list.innerHTML = '';
    if (!sessions.length) {
      const empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = 'No recordings yet';
      dom.list.appendChild(empty);
      return;
    }
    sessions.forEach(session => dom.list.appendChild(_makeItem(session, selectedId)));
  }

  function _makeItem(session, selectedId) {
    const el = document.createElement('div');
    el.className = 'history-item';
    if (session.id === selectedId) el.classList.add('active');

    const isLive = session.status === 'recording' || session.status === 'paused';
    if (isLive) el.classList.add('history-item-recording');

    const name = document.createElement('div');
    name.className = 'history-item-name';
    name.textContent = (isLive ? '🔴 ' : '') + (session.filename || session.id);
    name.title = session.filename || session.id;

    const meta = document.createElement('div');
    meta.className = 'history-item-meta';
    const dateStr = session.created_at ? session.created_at.slice(0, 10) : '';
    const dur = session.duration ? _fmtDuration(session.duration) : '';
    meta.textContent = [dateStr, dur].filter(Boolean).join(' · ');

    el.append(name, meta);
    el.addEventListener('click', () => {
      if (_onSelect) _onSelect(session.id);
    });
    return el;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _fmtDuration(secs) {
    const m = Math.floor(secs / 60), s = secs % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  return { init, render };
})();
