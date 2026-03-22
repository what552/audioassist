/**
 * Resizer — drag handles for column widths and summary-panel internal split.
 *
 * Column resizers (horizontal drag):
 *   #resizer-left   between #history-panel and #center-panel
 *   #resizer-right  between #center-panel and #summary-panel
 *
 * Vertical resizer (inside summary panel):
 *   #summary-v-resizer  between #summary-output and #agent-chat
 *
 * Minimum sizes:
 *   history  ≥ 160px,  center ≥ 300px,  summary ≥ 280px
 *   summary-output ≥ 120px,  agent-chat ≥ 150px
 */
const Resizer = (() => {
  const MIN_HISTORY = 160;
  const MIN_CENTER  = 300;
  const MIN_SUMMARY = 280;
  const MIN_OUTPUT  = 120;
  const MIN_CHAT    = 150;

  function init() {
    _initColLeft();
    _initColRight();
    _initVertical();
    _watchSummaryPanel();
  }

  // ── Column: history | center ────────────────────────────────────────────

  function _initColLeft() {
    const handle  = document.getElementById('resizer-left');
    const histEl  = document.getElementById('history-panel');
    const mainEl  = document.getElementById('main');
    const summEl  = document.getElementById('summary-panel');
    const rRight  = document.getElementById('resizer-right');

    _makeDraggable(handle, 'col', () => {
      const startW = histEl.offsetWidth;
      return (dx) => {
        const summW  = summEl.hidden ? 0 : summEl.offsetWidth;
        const rW     = handle.offsetWidth + (rRight.hidden ? 0 : rRight.offsetWidth);
        const maxW   = mainEl.offsetWidth - summW - MIN_CENTER - rW;
        histEl.style.width = clamp(startW + dx, MIN_HISTORY, maxW) + 'px';
      };
    });
  }

  // ── Column: center | summary ────────────────────────────────────────────

  function _initColRight() {
    const handle  = document.getElementById('resizer-right');
    const summEl  = document.getElementById('summary-panel');
    const histEl  = document.getElementById('history-panel');
    const mainEl  = document.getElementById('main');
    const rLeft   = document.getElementById('resizer-left');

    _makeDraggable(handle, 'col', () => {
      const startW = summEl.offsetWidth;
      return (dx) => {
        // Moving handle left (dx < 0) → summary grows
        const histW  = histEl.offsetWidth;
        const rW     = rLeft.offsetWidth + handle.offsetWidth;
        const maxW   = mainEl.offsetWidth - histW - MIN_CENTER - rW;
        summEl.style.width = clamp(startW - dx, MIN_SUMMARY, maxW) + 'px';
      };
    });
  }

  // ── Vertical: summary-output | agent-chat ──────────────────────────────

  function _initVertical() {
    const handle  = document.getElementById('summary-v-resizer');
    const topEl   = document.getElementById('summary-output');
    const botEl   = document.getElementById('agent-chat');

    _makeDraggable(handle, 'row', () => {
      const startTopH = topEl.offsetHeight;
      const startBotH = botEl.offsetHeight;
      const totalH    = startTopH + startBotH;
      return (dy) => {
        let newTopH = clamp(startTopH + dy, MIN_OUTPUT, totalH - MIN_CHAT);
        let newBotH = totalH - newTopH;
        topEl.style.flex   = 'none';
        topEl.style.height = newTopH + 'px';
        botEl.style.height = newBotH + 'px';
      };
    });
  }

  // ── Sync resizer-right visibility with summary panel ───────────────────

  function _watchSummaryPanel() {
    const panel  = document.getElementById('summary-panel');
    const rRight = document.getElementById('resizer-right');
    new MutationObserver(() => {
      rRight.hidden = panel.hidden;
    }).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
  }

  // ── Generic drag helper ─────────────────────────────────────────────────

  function _makeDraggable(handle, axis, onStart) {
    if (!handle) return;
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const startPos = axis === 'col' ? e.clientX : e.clientY;
      const move     = onStart();
      const cursor   = axis === 'col' ? 'col-resize' : 'row-resize';

      handle.classList.add('resizing');
      document.body.style.cursor     = cursor;
      document.body.style.userSelect = 'none';

      const onMove = (e) => {
        const delta = (axis === 'col' ? e.clientX : e.clientY) - startPos;
        move(delta);
      };
      const onUp = () => {
        handle.classList.remove('resizing');
        document.body.style.cursor     = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup',   onUp);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup',   onUp);
    });
  }

  function clamp(val, min, max) {
    return Math.max(min, Math.min(val, max));
  }

  return { init };
})();

window.addEventListener('pywebviewready', () => Resizer.init());
// Also init on DOMContentLoaded for non-pywebview environments (tests / browser preview)
document.addEventListener('DOMContentLoaded', () => {
  if (typeof window.pywebview === 'undefined') Resizer.init();
});
