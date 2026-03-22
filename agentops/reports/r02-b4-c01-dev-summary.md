# r02-b4 Development Summary

- Builder 分支：`feat/r01-builder`
- Baseline SHA：`c3f8d8b`（r02-b3 合并点，main HEAD）
- Target SHA（Gate Commit）：`10352fd`
- 开发日期：2026-03-23
- 测试结果：358 passed（336 → 358，+22 新测试）

---

## 变更批次

| SHA | 内容 |
|-----|------|
| `e1f1eac` | feat(r02-b4): Summary Agent — 多轮对话 + tool-calling loop + session 持久化 |
| `10352fd` | fix(agent): inject job_id into system prompt，agent 不再向用户询问 job_id |

---

## 主要变更说明

### Summary Agent 核心（`e1f1eac`）

**后端：**
- `src/agent.py`：MeetingAgent，ReAct 风格 tool-calling loop（最多 5 次迭代）
  - 工具：`get_transcript`、`get_current_summary`、`get_summary_versions`、`update_summary`
  - 兼容所有 OpenAI-compatible provider；不支持 function calling 时自动降级为 no-tool one-shot 模式
- `src/agent_store.py`：session 持久化（每个 job 存 `{job_id}_chat.json`，最多 20 轮，发送给 LLM 最近 8 轮）
- `app.py`：新增 3 个 API 方法
  - `start_agent_turn(job_id, user_input)` — 启动后台 agent 推理，流式推送事件
  - `get_agent_session(job_id)` — 读取历史对话
  - `clear_agent_session(job_id)` — 清空对话历史

**前端（summary panel 扩展）：**
- `ui/js/summary.js`：chat bubble 区域，支持 `onAgentChunk`（流式文字）、`onAgentToolStart/End`（工具调用指示）、`onAgentDraftUpdated`（纪要更新）、`onAgentComplete/Error`
- `ui/index.html`：chat 输入框 + Send/Clear 按钮
- `ui/css/main.css`：chat bubble 样式

### 修复（`10352fd`）

- `src/agent.py` `run()` 方法：system message 追加 `当前会议 job_id：{job_id}`，agent 直接用当前 job_id 调用工具，不再询问用户

---

## 变更文件范围

| 文件 | 变更类型 |
|------|---------|
| `src/agent.py` | 新增（385 行） |
| `src/agent_store.py` | 新增（90 行） |
| `app.py` | +76 行（3 个 API 方法）|
| `ui/js/summary.js` | +170 行（chat UI）|
| `ui/index.html` | +13 行 |
| `ui/css/main.css` | +95 行 |
| `README.md` | Summary Agent 章节 |
| `tests/test_agent.py` | 新增（258 行）|
| `tests/test_app_agent.py` | 新增（164 行）|

---

## 验证命令与结果

```
cd /Users/feifei/programing/audioassist/audioassist-builder
/Users/feifei/programing/local\ asr/.venv/bin/python -m pytest -q
# 结果：358 passed
```

---

## 未完成 / 遗留项

- Agent tool 调用无超时机制 — P3
- get_transcript max_chars 固定 6000，长会议可能截断 — P3
- Playwright 未覆盖 agent chat UI — P3

---

## Gate 候选 SHA

`10352fd`
