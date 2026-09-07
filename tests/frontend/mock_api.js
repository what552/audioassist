/** Configurable PyWebView API double used by browser-level frontend tests. */
(function () {
  'use strict';

  var defaults = {
    setup: { asr_ready: true, diarizer_ready: true },
    history: [
      {
        job_id: 'job-aaa', filename: 'meeting.mp3',
        date: '2026-03-01T10:00:00', duration: 180,
        language: 'zh', type: 'file', status: 'done', audio_path: '/audio/meeting.mp3',
      },
      {
        job_id: 'job-bbb', filename: 'lecture.mp4',
        date: '2026-03-02T14:00:00', duration: 90,
        language: 'en', type: 'file', status: 'done', audio_path: '/audio/lecture.mp4',
      },
    ],
    models: [
      {
        id: 'qwen3-asr-1.7b', name: 'Qwen3-ASR 1.7B', role: 'asr',
        size_gb: 3.5, description: 'Local speech recognition',
        downloaded: true, incomplete: false, recommended: true,
      },
      {
        id: 'qwen3-forced-aligner-0.6b', name: 'Qwen3 Forced Aligner', role: 'aligner',
        size_gb: 1.2, description: 'Word timestamps',
        downloaded: false, incomplete: false, recommended: false,
      },
      {
        id: 'pyannote-diarization-community-1', name: 'Pyannote Community-1', role: 'diarizer',
        size_gb: 0.034, description: 'Speaker diarization',
        downloaded: true, incomplete: false, recommended: true,
      },
    ],
    selectedFiles: [],
    transcribeIds: ['upload-job-1', 'upload-job-2', 'upload-job-3'],
    agentTurns: [],
    summaries: {},
    preflight: { supported: true },
    microphonePermission: { status: 'authorized', authorized: true },
    microphonePermissionDelayMs: 0,
    screenPermission: { status: 'authorized', authorized: true },
    screenPermissionDelayMs: 0,
    realtimeStart: { status: 'started' },
  };

  var supplied = window.__AUDIOASSIST_TEST_CONFIG || {};
  var state = Object.assign({}, defaults, supplied);
  state.setup = Object.assign({}, defaults.setup, supplied.setup || {});
  state.history = (supplied.history || defaults.history).slice();
  state.models = (supplied.models || defaults.models).map(function (model) {
    return Object.assign({}, model);
  });
  state.selectedFiles = (supplied.selectedFiles || defaults.selectedFiles).slice();
  state.transcribeIds = (supplied.transcribeIds || defaults.transcribeIds).slice();
  state.agentTurns = (supplied.agentTurns || defaults.agentTurns).slice();
  state.preflight = Object.assign({}, defaults.preflight, supplied.preflight || {});
  state.microphonePermission = Object.assign(
    {}, defaults.microphonePermission, supplied.microphonePermission || {}
  );
  state.microphonePermissionDelayMs = (
    supplied.microphonePermissionDelayMs || defaults.microphonePermissionDelayMs
  );
  state.screenPermission = Object.assign(
    {}, defaults.screenPermission, supplied.screenPermission || {}
  );
  state.screenPermissionDelayMs = (
    supplied.screenPermissionDelayMs || defaults.screenPermissionDelayMs
  );
  state.realtimeStart = Object.assign({}, defaults.realtimeStart, supplied.realtimeStart || {});
  state.calls = [];

  function record(name, args) {
    state.calls.push({ name: name, args: Array.from(args || []) });
  }

  function transcript(jobId) {
    return {
      job_id: jobId,
      audio: '/audio/' + jobId + '.mp3',
      filename: jobId + '.mp3',
      language: 'zh',
      segments: [
        { speaker: 'SPEAKER_00', start: 0.0, end: 3.5, text: 'Hello world.', words: [] },
      ],
    };
  }

  var api = {
    get_setup_status: async function () { record('get_setup_status', arguments); return Object.assign({}, state.setup); },
    get_history: async function () { record('get_history', arguments); return state.history.slice(); },
    get_models: async function () { record('get_models', arguments); return state.models.map(function (m) { return Object.assign({}, m); }); },
    get_transcript: async function (jobId) { record('get_transcript', arguments); return transcript(jobId); },
    select_files: async function () { record('select_files', arguments); return state.selectedFiles.slice(); },
    transcribe: async function () {
      record('transcribe', arguments);
      return { job_id: state.transcribeIds.shift() || ('upload-job-' + state.calls.length) };
    },
    cancel_transcription: async function () { record('cancel_transcription', arguments); return true; },
    resume_transcription: async function (jobId) { record('resume_transcription', arguments); return { job_id: jobId }; },
    refine: async function (jobId) { record('refine', arguments); return { job_id: jobId }; },
    rename_session: async function () { record('rename_session', arguments); return true; },
    delete_session: async function () { record('delete_session', arguments); return true; },
    download_model: async function () { record('download_model', arguments); return { status: 'started' }; },
    delete_model: async function (modelId) {
      record('delete_model', arguments);
      var model = state.models.find(function (item) { return item.id === modelId; });
      if (model) model.downloaded = false;
      return { downloaded: false };
    },
    get_api_config: async function () { return { base_url: '', api_key: '', model: '' }; },
    get_runtime_identity: async function () {
      return {version: '2.0.2', mode: 'packaged-app', app_path: '/mock/AudioAssist.app',
        source_root: null, entrypoint: '/mock/AudioAssist.app/Contents/MacOS/AudioAssist',
        python_executable: '/mock/AudioAssist.app/Contents/MacOS/AudioAssist'};
    },
    get_qwen_backend_settings: async function () {
      return {saved: localStorage.getItem('testQwenBackend') || 'auto', effective: 'auto', environment_override: false,
        options: ['auto', 'pytorch-cpu', 'pytorch-mps', 'mlx'].map(value => ({value, label: value, available: true, reason: ''}))};
    },
    save_qwen_backend_settings: async function (value) {
      record('save_qwen_backend_settings', arguments);
      localStorage.setItem('testQwenBackend', value);
      return {ok: true};
    },
    save_api_config: async function () { record('save_api_config', arguments); return true; },
    get_summary_templates: async function () { return [{ name: 'General', prompt: 'Summarize' }]; },
    save_summary_templates: async function () { record('save_summary_templates', arguments); return true; },
    get_summary_versions: async function (jobId) { return (state.summaries[jobId] || []).slice(); },
    save_summary_version: async function () { record('save_summary_version', arguments); return true; },
    summarize: async function () { record('summarize', arguments); return true; },
    get_agent_session: async function () { record('get_agent_session', arguments); return { turns: state.agentTurns.slice() }; },
    start_agent_turn: async function () { record('start_agent_turn', arguments); return { status: 'started' }; },
    clear_agent_session: async function () { record('clear_agent_session', arguments); state.agentTurns = []; return true; },
    get_storage_config: async function () { return { output_dir: '/mock/output' }; },
    select_output_folder: async function () { return '/mock/new-output'; },
    set_output_dir: async function () { record('set_output_dir', arguments); return { ok: true }; },
    get_obsidian_config: async function () { return { enabled: false, folder: '' }; },
    select_obsidian_folder: async function () { return '/mock/vault'; },
    set_obsidian_config: async function () { record('set_obsidian_config', arguments); return true; },
    export_transcript: async function () { record('export_transcript', arguments); return { status: 'saved', path: '/mock/transcript.txt' }; },
    export_summary: async function () { record('export_summary', arguments); return { status: 'saved', path: '/mock/summary.txt' }; },
    save_transcript: async function () { record('save_transcript', arguments); return true; },
    rename_speaker: async function () { record('rename_speaker', arguments); return { ok: true, segments: [] }; },
    rename_segment_speaker: async function () { record('rename_segment_speaker', arguments); return { ok: true }; },
    preflight_capture: async function () { record('preflight_capture', arguments); return Object.assign({}, state.preflight); },
    request_microphone_permission: async function () {
      record('request_microphone_permission', arguments);
      if (state.microphonePermissionDelayMs) {
        await new Promise(resolve => setTimeout(resolve, state.microphonePermissionDelayMs));
      }
      return Object.assign({}, state.microphonePermission);
    },
    request_screen_recording_permission: async function () {
      record('request_screen_recording_permission', arguments);
      if (state.screenPermissionDelayMs) {
        await new Promise(resolve => setTimeout(resolve, state.screenPermissionDelayMs));
      }
      return Object.assign({}, state.screenPermission);
    },
    start_realtime: async function () { record('start_realtime', arguments); return Object.assign({}, state.realtimeStart); },
    stop_realtime: async function () { record('stop_realtime', arguments); return { status: 'stopped' }; },
    pause_realtime: async function () { record('pause_realtime', arguments); return { status: 'pausing' }; },
    resume_realtime: async function () { record('resume_realtime', arguments); return { status: 'resuming' }; },
    open_privacy_settings: async function () { record('open_privacy_settings', arguments); return true; },
  };

  window.pywebview = { api: api };
  window._mockApi = {
    state: state,
    calls: function (name) { return state.calls.filter(function (call) { return call.name === name; }); },
    setSetup: function (patch) { Object.assign(state.setup, patch); },
    setModels: function (models) { state.models = models.map(function (model) { return Object.assign({}, model); }); },
    setPreflight: function (preflight) { state.preflight = Object.assign({}, preflight); },
    dispatchReady: function () { window.dispatchEvent(new Event('pywebviewready')); },
  };
})();
