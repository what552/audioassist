/** AudioAssist appearance preference: system, light, or dark. */
const Theme = (() => {
  const STORAGE_KEY = 'audioassist.theme';
  const VALID = new Set(['system', 'light', 'dark']);
  let setting = 'system';
  let media = null;

  function _storedSetting() {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return VALID.has(value) ? value : 'system';
    } catch (_) {
      return 'system';
    }
  }

  function _resolvedTheme() {
    if (setting !== 'system') return setting;
    return media && media.matches ? 'dark' : 'light';
  }

  function _apply() {
    const resolved = _resolvedTheme();
    document.documentElement.dataset.themeSetting = setting;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }

  function set(next) {
    setting = VALID.has(next) ? next : 'system';
    try { localStorage.setItem(STORAGE_KEY, setting); } catch (_) {}
    _apply();
    const select = document.getElementById('cfg-theme');
    if (select) select.value = setting;
  }

  function init() {
    media = window.matchMedia('(prefers-color-scheme: dark)');
    setting = _storedSetting();
    _apply();
    const select = document.getElementById('cfg-theme');
    if (select) {
      select.value = setting;
      select.addEventListener('change', () => set(select.value));
    }
    const onSystemChange = () => { if (setting === 'system') _apply(); };
    if (media.addEventListener) media.addEventListener('change', onSystemChange);
    else if (media.addListener) media.addListener(onSystemChange);
  }

  return { init, set, get setting() { return setting; } };
})();
