/**
 * History — left sidebar listing sessions.
 *
 * Pure view module: no backend calls, no internal state management.
 * App drives all data via render().
 *
 * Public API:
 *   History.init(onSelect, onRename, onDelete, onRetranscribe) — wire up DOM; callbacks on user actions
 *   History.render(sessions, selectedId)        — redraw list from session array
 */
const History = (() => {
  let dom = {};
  let _onSelect = null;
  let _onRename = null;
  let _onDelete = null;
  let _onRetranscribe = null;

  // ── Init ───────────────────────────────────────────────────────────────────

  function init(onSelect, onRename, onDelete, onRetranscribe) {
    _onSelect = onSelect;
    _onRename = onRename;
    _onDelete = onDelete;
    _onRetranscribe = onRetranscribe || null;
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
    if (session.status === 'queued') el.classList.add('history-item-queued');
    if (session.status === 'interrupted') el.classList.add('history-item-interrupted');

    // Clickable body (select)
    const body = document.createElement('div');
    body.className = 'history-item-body';

    const nameWrap = document.createElement('div');
    nameWrap.className = 'history-item-name';
    nameWrap.textContent = (isLive ? '🔴 ' : '') + (session.filename || session.id);
    nameWrap.title = session.filename || session.id;

    const meta = document.createElement('div');
    meta.className = 'history-item-meta';
    const dateStr = session.created_at ? session.created_at.slice(0, 16).replace('T', ' ') : '';
    const dur = session.duration ? _fmtDuration(session.duration) : '';
    meta.textContent = [dateStr, dur].filter(Boolean).join(' · ');

    body.append(nameWrap, meta);
    body.addEventListener('click', () => {
      if (_onSelect) _onSelect(session.id);
    });

    el.appendChild(body);

    // Action buttons (shown on hover; hidden for live sessions)
    if (!isLive) {
      const actions = document.createElement('div');
      actions.className = 'history-item-actions';

      const btnRename = document.createElement('button');
      btnRename.className = 'btn-history-action';
      btnRename.title = 'Rename';
      btnRename.textContent = '✏';
      btnRename.addEventListener('click', (e) => {
        e.stopPropagation();
        _startInlineRename(session, nameWrap);
      });

      // Re-transcribe button (F4): shown for file sessions in terminal states
      const retranscribeStates = ['done', 'error', 'interrupted', 'cancelled'];
      if (session.type === 'file' && retranscribeStates.includes(session.status) && _onRetranscribe) {
        const btnRetranscribe = document.createElement('button');
        btnRetranscribe.className = 'btn-history-action';
        btnRetranscribe.title = 'Re-transcribe';
        btnRetranscribe.textContent = '🔄';
        btnRetranscribe.addEventListener('click', (e) => {
          e.stopPropagation();
          _onRetranscribe(session.id);
        });
        actions.appendChild(btnRetranscribe);
      }

      const btnDelete = document.createElement('button');
      btnDelete.className = 'btn-history-action btn-history-delete';
      btnDelete.title = 'Delete';
      btnDelete.textContent = '🗑';
      btnDelete.addEventListener('click', (e) => {
        e.stopPropagation();
        if (_onDelete) _onDelete(session.id);
      });

      actions.append(btnRename, btnDelete);
      el.appendChild(actions);
    }

    return el;
  }

  // ── Inline rename ──────────────────────────────────────────────────────────

  function _startInlineRename(session, nameEl) {
    const original = session.filename || session.id;
    const input = document.createElement('input');
    input.className = 'history-item-rename-input';
    input.value = original;
    nameEl.replaceWith(input);
    input.focus();
    input.select();

    function _commit() {
      input.removeEventListener('blur', _commit);
      const newName = input.value.trim();
      // Restore the name element
      const restored = document.createElement('div');
      restored.className = 'history-item-name';
      restored.textContent = newName || original;
      restored.title = newName || original;
      input.replaceWith(restored);
      if (newName && newName !== original && _onRename) {
        _onRename(session.id, newName);
      }
    }

    function _cancel() {
      input.removeEventListener('blur', _commit);
      const restored = document.createElement('div');
      restored.className = 'history-item-name';
      restored.textContent = original;
      restored.title = original;
      input.replaceWith(restored);
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _commit(); }
      if (e.key === 'Escape') { e.preventDefault(); _cancel(); }
    });
    input.addEventListener('blur', _commit);
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _fmtDuration(secs) {
    const m = Math.floor(secs / 60), s = secs % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  return { init, render };
})();
