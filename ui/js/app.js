/**
 * AudioAssist — main JS entry point (c02+)
 *
 * PyWebView bridge: window.pywebview.api.<method>()
 * Python → JS events:
 *   onTranscribeProgress(jobId, progress, message)
 *   onTranscribeComplete(jobId, jsonPath)
 *   onTranscribeError(jobId, message)
 *   onModelDownloadProgress(name, percent)
 *   onModelDownloadError(name, message)
 */

// ── PyWebView bridge helpers ──────────────────────────────────────────────────

async function api(method, ...args) {
  await window.pywebview.api_ready;
  return window.pywebview.api[method](...args);
}

// ── Progress event handlers (called by Python via evaluate_js) ────────────────

function onTranscribeProgress(jobId, progress, message) {
  console.log(`[transcribe] ${jobId} ${(progress * 100).toFixed(0)}% — ${message}`);
}

function onTranscribeComplete(jobId, jsonPath) {
  console.log(`[transcribe] complete: ${jobId}`);
}

function onTranscribeError(jobId, message) {
  console.error(`[transcribe] error: ${jobId} — ${message}`);
}

function onModelDownloadProgress(name, percent) {
  console.log(`[model] ${name} ${(percent * 100).toFixed(0)}%`);
}

function onModelDownloadError(name, message) {
  console.error(`[model] error: ${name} — ${message}`);
}
