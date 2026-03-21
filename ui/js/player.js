/**
 * Player — HTML5 audio wrapper.
 *
 * Public API:
 *   Player.init(audioElement)
 *   Player.load(filePath)        — accepts a native FS path, converts to file:// URL
 *   Player.seekTo(seconds)       — seek and play
 *   Player.play() / pause()
 *   Player.onTimeUpdate(cb)      — cb(currentTime) fires on every timeupdate
 *   Player.get currentTime / duration
 */
const Player = (() => {
  let _el = null;
  let _timeUpdateCb = null;

  function init(audioElement) {
    _el = audioElement;

    _el.addEventListener('timeupdate', () => {
      if (_timeUpdateCb) _timeUpdateCb(_el.currentTime);
    });

    _el.addEventListener('error', (e) => {
      console.error('[Player] audio error:', _el.error);
    });
  }

  /** Convert a native file system path to a file:// URL. */
  function _toFileUrl(p) {
    if (!p) return '';
    // Windows: C:\path → file:///C:/path
    if (/^[A-Za-z]:/.test(p)) return 'file:///' + p.replace(/\\/g, '/');
    // POSIX: /path → file:///path
    return 'file://' + p;
  }

  function load(filePath) {
    if (!_el) return;
    _el.src = _toFileUrl(filePath);
    _el.load();
  }

  function seekTo(seconds) {
    if (!_el) return;
    _el.currentTime = seconds;
    _el.play().catch(() => {}); // ignore autoplay policy errors
  }

  function play() {
    _el && _el.play().catch(() => {});
  }

  function pause() {
    _el && _el.pause();
  }

  function onTimeUpdate(cb) {
    _timeUpdateCb = cb;
  }

  return {
    init,
    load,
    seekTo,
    play,
    pause,
    onTimeUpdate,
    get currentTime() { return _el ? _el.currentTime : 0; },
    get duration()    { return _el ? _el.duration || 0 : 0; },
  };
})();
