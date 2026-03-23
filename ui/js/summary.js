/**
 * Summary — API config, template management, summary generation, version history,
 * and interactive Summary Agent chat.
 *
 * Public API:
 *   Summary.init()               — wire up DOM; call once after DOMContentLoaded
 *   Summary.showForJob(jobId)    — reveal the summary panel and load versions for a job
 *
 * Python → JS callbacks (called via evaluate_js):
 *   onSummaryChunk(jobId, chunk)        — streaming token/chunk
 *   onSummaryComplete(jobId, fullText)  — generation finished
 *   onSummaryError(jobId, message)      — error
 *   onAgentChunk(jobId, chunk)          — agent streaming text delta
 *   onAgentToolStart(jobId, toolName)   — agent tool call beginning
 *   onAgentToolEnd(jobId, toolName)     — agent tool call finished
 *   onAgentDraftUpdated(jobId, newText) — agent saved a new summary version
 *   onAgentComplete(jobId, fullText)    — agent turn finished
 *   onAgentError(jobId, message)        — agent error
 */
const Summary = (() => {
  let _jobId = null;
  let _generating = false;
  let _agentBusy = false;
  let _agentCurrentBubble = null;
  let _streamBuffer      = '';  // summary chunk accumulator
  let _agentStreamBuffer = '';  // agent chunk accumulator
  let dom = {};
  let agentDom = {};

  // ── Markdown rendering ──────────────────────────────────────────────────────

  // Configure marked once: GitHub-flavoured, line breaks as <br>
  if (typeof marked !== 'undefined') {
    marked.use({ gfm: true, breaks: true });
  }

  function _md(text) {
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
      // Fallback: escape HTML only
      return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    return DOMPurify.sanitize(marked.parse(text));
  }

  // ── Init ───────────────────────────────────────────────────────────────────

  function init() {
    dom = {
      panel:          document.getElementById('summary-panel'),
      btnToggle:      document.getElementById('btn-summary-toggle'),
      btnSettings:    document.getElementById('btn-summary-config'),
      modal:          document.getElementById('config-modal'),
      btnModalClose:  document.getElementById('btn-modal-close'),
      inner:          document.getElementById('summary-inner'),
      selTemplate:    document.getElementById('sel-template'),
      btnSummarize:   document.getElementById('btn-summarize'),
      versions:       document.getElementById('summary-versions'),
      output:         document.getElementById('summary-output'),
      placeholder:    document.getElementById('summary-placeholder'),
      summaryText:    document.getElementById('summary-text'),
      loading:        document.getElementById('summary-loading'),
      cfgBaseUrl:     document.getElementById('cfg-base-url'),
      cfgApiKey:      document.getElementById('cfg-api-key'),
      cfgModel:       document.getElementById('cfg-model'),
      btnSaveConfig:  document.getElementById('btn-save-config'),
      btnAddTemplate: document.getElementById('btn-add-template'),
      templateList:   document.getElementById('template-list'),
    };

    agentDom = {
      chat:       document.getElementById('agent-chat'),
      messages:   document.getElementById('agent-messages'),
      toolStatus: document.getElementById('agent-tool-status'),
      input:      document.getElementById('agent-input'),
      btnSend:    document.getElementById('btn-agent-send'),
      btnClear:   document.getElementById('btn-agent-clear'),
    };

    dom.btnToggle.addEventListener('click', _onTogglePanel);
    dom.btnSettings.addEventListener('click', _toggleConfig);
    dom.btnModalClose.addEventListener('click', _closeModal);
    // Close modal on backdrop click
    dom.modal.addEventListener('click', (e) => { if (e.target === dom.modal) _closeModal(); });
    dom.btnSummarize.addEventListener('click', _onSummarize);
    dom.btnSaveConfig.addEventListener('click', _onSaveConfig);
    dom.btnAddTemplate.addEventListener('click', _onAddTemplate);

    agentDom.btnSend.addEventListener('click', _sendAgentTurn);
    agentDom.btnClear.addEventListener('click', _clearAgentChat);
    agentDom.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendAgentTurn(); }
    });

    const btnExportSummary = document.getElementById('btn-export-summary');
    if (btnExportSummary) {
      btnExportSummary.addEventListener('click', (e) => {
        App.openExportMenu(e, (fmt) => _exportSummary(fmt));
      });
    }

    _loadConfig();
    _loadTemplates();
  }

  // ── Panel toggle ───────────────────────────────────────────────────────────

  function _onTogglePanel() {
    dom.panel.hidden = !dom.panel.hidden;
  }

  // ── Public: show for a job ─────────────────────────────────────────────────

  async function showForJob(jobId) {
    _jobId = jobId;
    dom.panel.hidden = false;
    _setOutput('placeholder');
    await _loadVersions(jobId);
    await _initChat(jobId);
  }

  // ── Public: reset panel (no active job — recording / transcribing / idle) ──

  function reset() {
    _jobId = null;
    _setOutput('placeholder');
    _renderVersions([]);
    agentDom.messages.innerHTML = '';
    _agentCurrentBubble = null;
  }

  // ── Version management ─────────────────────────────────────────────────────

  async function _loadVersions(jobId) {
    try {
      const versions = await window.pywebview.api.get_summary_versions(jobId);
      _renderVersions(versions);
    } catch (e) {
      console.warn('[Summary] _loadVersions error:', e);
    }
  }

  function _renderVersions(versions) {
    dom.versions.innerHTML = '';
    if (!versions.length) {
      dom.versions.hidden = true;
      return;
    }
    dom.versions.hidden = false;
    versions.forEach((v, i) => {
      const btn = document.createElement('button');
      btn.className = 'version-btn';
      btn.textContent = `v${i + 1} · ${v.created_at}`;
      btn.addEventListener('click', () => {
        document.querySelectorAll('.version-btn.active')
          .forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _setOutput('text', v.text);
      });
      dom.versions.appendChild(btn);
    });
  }

  // ── Config ─────────────────────────────────────────────────────────────────

  async function _loadConfig() {
    try {
      const cfg = await window.pywebview.api.get_api_config();
      dom.cfgBaseUrl.value = cfg.base_url || '';
      dom.cfgApiKey.value  = cfg.api_key  || '';
      dom.cfgModel.value   = cfg.model    || '';
    } catch (e) {
      console.warn('[Summary] _loadConfig error:', e);
    }
  }

  async function _onSaveConfig() {
    const cfg = {
      base_url: dom.cfgBaseUrl.value.trim(),
      api_key:  dom.cfgApiKey.value.trim(),
      model:    dom.cfgModel.value.trim(),
    };
    try {
      await window.pywebview.api.save_api_config(cfg);
      _closeModal();
    } catch (e) {
      console.error('[Summary] _onSaveConfig error:', e);
    }
  }

  function _closeModal() {
    dom.modal.hidden = true;
    dom.btnSettings.classList.remove('active');
  }

  function _toggleConfig() {
    const open = !dom.modal.hidden;
    dom.modal.hidden = open;
    dom.btnSettings.classList.toggle('active', !open);
  }

  // ── Templates ──────────────────────────────────────────────────────────────

  async function _loadTemplates() {
    try {
      const templates = await window.pywebview.api.get_summary_templates();
      _renderTemplates(templates);
      _refreshTemplateSelect(templates);
    } catch (e) {
      console.warn('[Summary] _loadTemplates error:', e);
    }
  }

  function _renderTemplates(templates) {
    dom.templateList.innerHTML = '';
    templates.forEach((t, i) => {
      dom.templateList.appendChild(_makeTemplateRow(t, i, templates));
    });
  }

  function _refreshTemplateSelect(templates) {
    const prev = dom.selTemplate.value;
    dom.selTemplate.innerHTML = '';
    templates.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.name;
      opt.textContent = t.name;
      dom.selTemplate.appendChild(opt);
    });
    if (prev && templates.some(t => t.name === prev)) {
      dom.selTemplate.value = prev;
    }
  }

  function _makeTemplateRow(t, index, templates) {
    const row = document.createElement('div');
    row.className = 'template-item';

    const nameEl = document.createElement('span');
    nameEl.className = 'template-name';
    nameEl.textContent = t.name;
    nameEl.title = t.prompt;

    const btnEdit = document.createElement('button');
    btnEdit.className = 'btn-template-action';
    btnEdit.textContent = 'Edit';
    btnEdit.addEventListener('click', () => _onEditTemplate(index, templates));

    const btnDel = document.createElement('button');
    btnDel.className = 'btn-template-action danger';
    btnDel.textContent = '✕';
    btnDel.addEventListener('click', () => _onDeleteTemplate(index, templates));

    row.append(nameEl, btnEdit, btnDel);
    return row;
  }

  async function _onAddTemplate() {
    const name = prompt('Template name:');
    if (!name || !name.trim()) return;
    const prompt_ = prompt('Prompt (the transcript will be appended as context):');
    if (prompt_ === null) return;

    const templates = await window.pywebview.api.get_summary_templates();
    if (templates.some(t => t.name === name.trim())) {
      alert(`A template named "${name.trim()}" already exists.`);
      return;
    }
    templates.push({ name: name.trim(), prompt: prompt_.trim() });
    await window.pywebview.api.save_summary_templates(templates);
    _renderTemplates(templates);
    _refreshTemplateSelect(templates);
  }

  async function _onEditTemplate(index, templates) {
    const t = templates[index];
    const name = prompt('Template name:', t.name);
    if (!name || !name.trim()) return;
    const prompt_ = prompt('Prompt:', t.prompt);
    if (prompt_ === null) return;

    if (name.trim() !== t.name && templates.some((u, i) => i !== index && u.name === name.trim())) {
      alert(`A template named "${name.trim()}" already exists.`);
      return;
    }
    const updated = [...templates];
    updated[index] = { name: name.trim(), prompt: prompt_.trim() };
    await window.pywebview.api.save_summary_templates(updated);
    _renderTemplates(updated);
    _refreshTemplateSelect(updated);
  }

  async function _onDeleteTemplate(index, templates) {
    const updated = templates.filter((_, i) => i !== index);
    await window.pywebview.api.save_summary_templates(updated);
    _renderTemplates(updated);
    _refreshTemplateSelect(updated);
  }

  // ── Summarize ──────────────────────────────────────────────────────────────

  async function _onSummarize() {
    if (!_jobId || _generating) return;

    const selectedName = dom.selTemplate.value;
    const templates = await window.pywebview.api.get_summary_templates();
    const template = templates.find(t => t.name === selectedName);
    if (!template) {
      alert('No template selected. Please add a template in ⚙ settings.');
      return;
    }

    _setGenerating(true);
    try {
      await window.pywebview.api.summarize(_jobId, template);
      // Result delivered via onSummaryChunk / onSummaryComplete / onSummaryError
    } catch (err) {
      _setGenerating(false);
      _setOutput('error', String(err));
    }
  }

  // ── Python → JS callbacks ──────────────────────────────────────────────────

  function onChunk(jobId, chunk) {
    if (jobId !== _jobId) return;
    if (dom.summaryText.hidden) {
      _streamBuffer = '';
      dom.summaryText.innerHTML = '';
      dom.summaryText.hidden = false;
      dom.placeholder.hidden = true;
    }
    _streamBuffer += chunk;
    dom.summaryText.innerHTML = _md(_streamBuffer);
    dom.output.scrollTop = dom.output.scrollHeight;
  }

  async function onComplete(jobId, fullText) {
    if (jobId !== _jobId) return;
    _setGenerating(false);
    // Save version and refresh version switcher
    try {
      await window.pywebview.api.save_summary_version(jobId, fullText);
      await _loadVersions(jobId);
    } catch (e) {
      console.warn('[Summary] save_summary_version error:', e);
    }
  }

  function onError(jobId, message) {
    if (jobId !== _jobId) return;
    _setGenerating(false);
    _setOutput('error', message);
  }

  // ── Agent chat ─────────────────────────────────────────────────────────────

  async function _initChat(jobId) {
    agentDom.messages.innerHTML = '';
    _agentCurrentBubble = null;
    try {
      const session = await window.pywebview.api.get_agent_session(jobId);
      const turns = session.turns || [];
      for (const t of turns) {
        if (t.role === 'user' || t.role === 'assistant') {
          _appendChatBubble(t.role, t.content);
        }
      }
    } catch (e) {
      console.warn('[Summary] _initChat error:', e);
    }
  }

  function _appendChatBubble(role, text) {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble chat-bubble-${role}`;
    if (role === 'assistant' && text) {
      bubble.innerHTML = _md(text);
    } else {
      bubble.textContent = text;
    }
    agentDom.messages.appendChild(bubble);
    agentDom.messages.scrollTop = agentDom.messages.scrollHeight;
    return bubble;
  }

  async function _sendAgentTurn() {
    if (!_jobId || _agentBusy) return;
    const text = agentDom.input.value.trim();
    if (!text) return;

    agentDom.input.value = '';
    agentDom.btnSend.disabled = true;
    _agentBusy = true;

    _agentStreamBuffer = '';
    _appendChatBubble('user', text);
    _agentCurrentBubble = _appendChatBubble('assistant', '');
    _agentCurrentBubble.classList.add('chat-bubble-loading');

    try {
      await window.pywebview.api.start_agent_turn(_jobId, text);
      // Result delivered via onAgentChunk / onAgentComplete / onAgentError
    } catch (err) {
      _agentBusy = false;
      agentDom.btnSend.disabled = false;
      if (_agentCurrentBubble) {
        _agentCurrentBubble.textContent = '⚠ ' + String(err);
        _agentCurrentBubble.classList.remove('chat-bubble-loading');
        _agentCurrentBubble.classList.add('chat-bubble-error');
        _agentCurrentBubble = null;
      }
    }
  }

  async function _clearAgentChat() {
    if (!_jobId) return;
    agentDom.messages.innerHTML = '';
    _agentCurrentBubble = null;
    try {
      await window.pywebview.api.clear_agent_session(_jobId);
    } catch (e) {
      console.warn('[Summary] _clearAgentChat error:', e);
    }
  }

  // ── Agent Python → JS callbacks ────────────────────────────────────────────

  function onAgentChunk(jobId, chunk) {
    if (jobId !== _jobId) return;
    if (_agentCurrentBubble) {
      _agentCurrentBubble.classList.remove('chat-bubble-loading');
      _agentStreamBuffer += chunk;
      _agentCurrentBubble.innerHTML = _md(_agentStreamBuffer);
    }
    agentDom.messages.scrollTop = agentDom.messages.scrollHeight;
  }

  function onAgentToolStart(jobId, toolName) {
    if (jobId !== _jobId) return;
    agentDom.toolStatus.textContent = `⚙ ${toolName}…`;
    agentDom.toolStatus.hidden = false;
  }

  function onAgentToolEnd(jobId, toolName) {
    if (jobId !== _jobId) return;
    agentDom.toolStatus.hidden = true;
  }

  function onAgentDraftUpdated(jobId, newText) {
    if (jobId !== _jobId) return;
    _setOutput('text', newText);
    _loadVersions(jobId);
  }

  function onAgentComplete(jobId, fullText) {
    if (jobId !== _jobId) return;
    _agentBusy = false;
    agentDom.btnSend.disabled = false;
    agentDom.toolStatus.hidden = true;
    if (_agentCurrentBubble) {
      _agentCurrentBubble.classList.remove('chat-bubble-loading');
      // Re-render with the authoritative full text to fix any mid-chunk MD artifacts
      const finalText = fullText || _agentStreamBuffer;
      if (finalText) _agentCurrentBubble.innerHTML = _md(finalText);
      _agentCurrentBubble = null;
      _agentStreamBuffer = '';
    }
    agentDom.messages.scrollTop = agentDom.messages.scrollHeight;
  }

  function onAgentError(jobId, message) {
    if (jobId !== _jobId) return;
    _agentBusy = false;
    agentDom.btnSend.disabled = false;
    agentDom.toolStatus.hidden = true;
    if (_agentCurrentBubble) {
      _agentCurrentBubble.textContent = '⚠ ' + message;
      _agentCurrentBubble.classList.remove('chat-bubble-loading');
      _agentCurrentBubble.classList.add('chat-bubble-error');
      _agentCurrentBubble = null;
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _setGenerating(active) {
    _generating = active;
    dom.btnSummarize.disabled = active;
    if (active) {
      _streamBuffer = '';
      dom.summaryText.innerHTML = '';
      dom.summaryText.hidden = false;
      dom.placeholder.hidden = true;
      dom.loading.hidden = false;
    } else {
      dom.loading.hidden = true;
    }
  }

  function _setOutput(state, text = '') {
    dom.placeholder.hidden = state !== 'placeholder';
    dom.summaryText.hidden = state !== 'text' && state !== 'error';
    dom.loading.hidden = true;
    const btnExport = document.getElementById('btn-export-summary');
    if (btnExport) btnExport.hidden = (state !== 'text');
    if (state === 'placeholder') {
      dom.summaryText.innerHTML = '';
      dom.summaryText.style.color = '';
    } else if (state === 'text') {
      dom.summaryText.innerHTML = _md(text);
      dom.summaryText.style.color = '';
    } else if (state === 'error') {
      dom.summaryText.textContent = '⚠ ' + text;  // error messages are plain text
      dom.summaryText.style.color = 'var(--warn)';
    }
  }

  async function _exportSummary(fmt) {
    if (!_jobId || !window.pywebview) return;
    try {
      const res = await window.pywebview.api.export_summary(_jobId, fmt);
      if (res && res.status === 'saved') {
        const name = res.path.replace(/\\/g, '/').split('/').pop();
        App.showToast(`已保存到 ${name}`);
      }
    } catch (err) {
      console.error('[Summary] export_summary error:', err);
    }
  }

  return {
    init, showForJob, reset,
    onChunk, onComplete, onError,
    onAgentChunk, onAgentToolStart, onAgentToolEnd,
    onAgentDraftUpdated, onAgentComplete, onAgentError,
  };
})();

// ── Global callbacks (invoked by Python via evaluate_js) ──────────────────────

function onSummaryChunk(jobId, chunk)         { Summary.onChunk(jobId, chunk); }
function onSummaryComplete(jobId, fullText)   { Summary.onComplete(jobId, fullText); }
function onSummaryError(jobId, message)       { Summary.onError(jobId, message); }

function onAgentChunk(jobId, chunk)           { Summary.onAgentChunk(jobId, chunk); }
function onAgentToolStart(jobId, toolName)    { Summary.onAgentToolStart(jobId, toolName); }
function onAgentToolEnd(jobId, toolName)      { Summary.onAgentToolEnd(jobId, toolName); }
function onAgentDraftUpdated(jobId, newText)  { Summary.onAgentDraftUpdated(jobId, newText); }
function onAgentComplete(jobId, fullText)     { Summary.onAgentComplete(jobId, fullText); }
function onAgentError(jobId, message)         { Summary.onAgentError(jobId, message); }
