/**
 * Summary — API config, template management, summary generation, version history.
 *
 * Public API:
 *   Summary.init()               — wire up DOM; call once after DOMContentLoaded
 *   Summary.showForJob(jobId)    — reveal the summary panel and load versions for a job
 *
 * Python → JS callbacks (called via evaluate_js):
 *   onSummaryChunk(jobId, chunk)        — streaming token/chunk
 *   onSummaryComplete(jobId, fullText)  — generation finished
 *   onSummaryError(jobId, message)      — error
 */
const Summary = (() => {
  let _jobId = null;
  let _generating = false;
  let dom = {};

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

    dom.btnToggle.addEventListener('click', _onTogglePanel);
    dom.btnSettings.addEventListener('click', _toggleConfig);
    dom.btnModalClose.addEventListener('click', _closeModal);
    // Close modal on backdrop click
    dom.modal.addEventListener('click', (e) => { if (e.target === dom.modal) _closeModal(); });
    dom.btnSummarize.addEventListener('click', _onSummarize);
    dom.btnSaveConfig.addEventListener('click', _onSaveConfig);
    dom.btnAddTemplate.addEventListener('click', _onAddTemplate);

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
      dom.summaryText.textContent = '';
      dom.summaryText.hidden = false;
      dom.placeholder.hidden = true;
    }
    dom.summaryText.textContent += chunk;
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

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _setGenerating(active) {
    _generating = active;
    dom.btnSummarize.disabled = active;
    if (active) {
      dom.summaryText.textContent = '';
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
    if (state === 'placeholder') {
      dom.summaryText.textContent = '';
      dom.summaryText.style.color = '';
    } else if (state === 'text') {
      dom.summaryText.textContent = text;
      dom.summaryText.style.color = '';
    } else if (state === 'error') {
      dom.summaryText.textContent = '⚠ ' + text;
      dom.summaryText.style.color = 'var(--warn)';
    }
  }

  return { init, showForJob, onChunk, onComplete, onError };
})();

// ── Global callbacks (invoked by Python via evaluate_js) ──────────────────────

function onSummaryChunk(jobId, chunk)       { Summary.onChunk(jobId, chunk); }
function onSummaryComplete(jobId, fullText) { Summary.onComplete(jobId, fullText); }
function onSummaryError(jobId, message)     { Summary.onError(jobId, message); }
