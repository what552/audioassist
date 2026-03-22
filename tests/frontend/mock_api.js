/**
 * mock_api.js — simulates window.pywebview.api for frontend tests.
 *
 * Loaded via Playwright page.add_init_script() BEFORE page scripts run.
 * Provides the complete set of API methods called during App.init() and
 * normal session interactions.
 *
 * Deliberately does NOT dispatch 'pywebviewready' — each test controls
 * the timing of that event via window._mockApi.dispatchReady().
 */
(function () {
  'use strict';

  /* ── Fixed mock data ──────────────────────────────────────────────────── */

  var HISTORY_ITEMS = [
    {
      job_id: 'job-aaa',
      filename: 'meeting.mp3',
      date: '2026-03-01T10:00:00',
      duration: 180,
      language: 'zh',
    },
    {
      job_id: 'job-bbb',
      filename: 'lecture.mp4',
      date: '2026-03-02T14:00:00',
      duration: 90,
      language: 'en',
    },
  ];

  var TRANSCRIPT_TEMPLATE = {
    language: 'zh',
    segments: [
      { speaker: 'SPEAKER_00', start: 0.0, end: 3.5, text: 'Hello world.', words: [] },
    ],
  };

  /* ── Mock API ─────────────────────────────────────────────────────────── */

  window.pywebview = {
    api: {
      get_history:           async function () { return HISTORY_ITEMS.slice(); },
      get_api_config:        async function () { return { base_url: '', api_key: '', model: '' }; },
      get_summary_templates: async function () { return []; },
      get_summary_versions:  async function () { return []; },
      get_transcript:        async function (jobId) {
        return Object.assign({}, TRANSCRIPT_TEMPLATE, {
          audio: jobId + '.mp3',
          filename: jobId + '.mp3',
        });
      },
      rename_session:        async function () { return true; },
      delete_session:        async function () { return true; },
      /* Realtime stubs (not called during tests, but prevent errors) */
      start_realtime:        async function () { return { status: 'started' }; },
      stop_realtime:         async function () { return { status: 'stopped' }; },
      pause_realtime:        async function () { return { status: 'pausing' }; },
      resume_realtime:       async function () { return { status: 'resuming' }; },
    },
  };

  /* ── Test helper ──────────────────────────────────────────────────────── */

  window._mockApi = {
    HISTORY_ITEMS: HISTORY_ITEMS,
    /** Fire pywebviewready, triggering App.init(). */
    dispatchReady: function () {
      window.dispatchEvent(new Event('pywebviewready'));
    },
  };
})();
