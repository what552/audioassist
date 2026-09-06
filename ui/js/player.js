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
  let _errorCb = null;
  let _loadedPath = '';

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
    const nextPath = filePath || '';
    if (nextPath === _loadedPath) return;

    // Stop the previous session before replacing its source. WebKit may keep
    // playing the old resource until the new one has loaded otherwise.
    _el.pause();
    _loadedPath = nextPath;
    _el.src = _toFileUrl(nextPath);
    _el.load();
    _el.currentTime = 0;
    if (_timeUpdateCb) _timeUpdateCb(0);
  }

  function seekTo(seconds) {
    const message = 'Unable to play this recording. Check that the audio file still exists and is readable.';
    if (!_el || !_loadedPath || !Number.isFinite(seconds) || seconds < 0) {
      if (_errorCb) _errorCb(message);
      return Promise.resolve(false);
    }
    try {
      _el.currentTime = seconds;
      return Promise.resolve(_el.play())
        .then(() => true)
        .catch((error) => {
          console.error('[Player] seek playback failed:', error);
          if (_errorCb) _errorCb(message);
          return false;
        });
    } catch (error) {
      console.error('[Player] seek failed:', error);
      if (_errorCb) _errorCb(message);
      return Promise.resolve(false);
    }
  }

  function play() {
    _el && _el.play().catch(() => {});
  }

  function pause() {
    _el && _el.pause();
  }

  function stop() {
    if (!_el) return;
    _el.pause();
    _el.currentTime = 0;
    if (_timeUpdateCb) _timeUpdateCb(0);
  }

  function onTimeUpdate(cb) {
    _timeUpdateCb = cb;
  }

  function onError(cb) {
    _errorCb = cb;
  }

  return {
    init,
    load,
    seekTo,
    play,
    pause,
    stop,
    onTimeUpdate,
    onError,
    get currentTime() { return _el ? _el.currentTime : 0; },
    get duration()    { return _el ? _el.duration || 0 : 0; },
    get loadedPath()  { return _loadedPath; },
  };
})();
