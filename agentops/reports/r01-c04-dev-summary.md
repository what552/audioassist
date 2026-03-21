# r01-c04 Dev Summary

**Branch:** feat/r01-builder
**Target SHA:** 1678943
**Date:** 2026-03-21

---

## Commits

| SHA | Message |
|-----|---------|
| `1678943` | feat(r01-c04): summary panel — LLM wrapper, streaming UI, template CRUD |

---

## 变更文件范围

9 files changed, 905 insertions(+), 3 deletions(-)

### 新增 — src/summary.py

OpenAI-compatible LLM 摘要包装层。

| 变更 | 说明 |
|------|------|
| `summarize()` 函数签名 | `(text, prompt, base_url, api_key, model, stream=False) -> str \| Iterator[str]` |
| `from openai import OpenAI` | 延迟导入（在函数内），避免 `openai` 未安装时模块级报错 |
| 非流式模式 | 调用 `create(stream=False)`，返回 `choices[0].message.content` 字符串 |
| 流式模式 | 返回生成器 `_stream_gen()`，逐 chunk yield `delta.content`（跳过 `None`） |
| `ImportError` 守卫 | 无 `openai` 包时抛出带提示信息的 `ImportError` |
| 兼容范围 | 任意 OpenAI-compatible endpoint（OpenAI、DeepSeek、Qwen、Ollama 等），通过 `base_url` 切换 |

### 修改 — app.py

| 变更 | 说明 |
|------|------|
| `API.summarize(job_id, template)` | 原 stub 实现补全。启动后台线程，返回 `{"job_id": ..., "status": "started"}` |
| `_run()` 内部流程 | 读取 `OUTPUT_DIR/{job_id}.json`，拼接 segment 文本，从 config 读取 api 参数，调用 `src.summary.summarize(..., stream=True)` |
| JS 事件推送 | 流式 chunk → `onSummaryChunk(jobId, chunk)`；结束 → `onSummaryComplete(jobId, fullText)`；异常 → `onSummaryError(jobId, message)` |
| `get_api_config()` / `save_api_config(cfg)` | 读写 `config.json` 中的 `api` 字段（`base_url` / `api_key` / `model`） |
| `get_summary_templates()` / `save_summary_templates(templates)` | 读写 `APP_DATA_DIR/templates.json`（独立文件，`[{name, prompt}]` 列表；与 `config.json` 分离） |

### 新增 — ui/js/summary.js

IIFE 模块，与 `player.js` / `transcript.js` 风格一致。

| 功能 | 说明 |
|------|------|
| `Summary.init()` | 绑定 DOM、事件监听；加载 config 和 templates |
| `Summary.showForJob(jobId)` | 显示摘要区块，重置输出为 placeholder |
| Config 表单 | `_loadConfig()` 初始化填充；`_onSaveConfig()` 写回并关闭面板 |
| Template CRUD | Add / Edit / Delete 均调用 `get/save_summary_templates` API；`_refreshTemplateSelect()` 同步下拉列表 |
| `_onSummarize()` | 读取选中 template，调用 `pywebview.api.summarize(jobId, template)` |
| 流式输出 | `onChunk` 追加文本 + auto-scroll；`onComplete` 关闭 spinner；`onError` 显示警告文本 |
| 全局回调 | `onSummaryChunk` / `onSummaryComplete` / `onSummaryError`（供 Python `evaluate_js` 调用） |

### 修改 — ui/index.html

| 变更 | 说明 |
|------|------|
| `#summary-section` | 新增于 `#player-panel` 内，默认 `hidden`；包含控制栏、输出区、config 面板 |
| `#summary-controls` | template `<select>`（`#sel-template`）+ Summarize 按钮 + ⚙ 设置按钮 |
| `#summary-output` | 含 `#summary-placeholder`、`#summary-text`、`#summary-loading`（spinner） |
| `#summary-config` | API 配置输入行（base_url / api_key / model）+ template 列表（`#template-list`）+ 操作按钮 |
| `<script>` 引用 | 在 `app.js` 之前新增 `<script src="js/summary.js">` |

### 修改 — ui/js/app.js

| 变更 | 说明 |
|------|------|
| `Summary.init()` | 在 `init()` 中 `Player.init()` 之后调用，完成 Summary 模块初始化 |
| `Summary.showForJob(jobId)` | 在 `onTranscribeComplete()` 渲染 transcript 后调用，显示摘要区块 |

### 修改 — ui/css/main.css

新增约 190 行摘要相关样式（追加至文件末尾）：

| 选择器 | 说明 |
|--------|------|
| `#summary-section` | flex 列布局，`border-top` 分隔，`min-height: 0` 防 flex 溢出 |
| `#summary-controls` | 横排弹性布局，间距 6px |
| `#sel-template` | `flex: 1` 占满剩余宽度，主题配色 |
| `.btn-sm` / `.btn-icon` | 小尺寸按钮；icon 按钮含 active 态（边框 + 颜色高亮） |
| `#summary-output` | 可滚动输出区，`min-height: 80px` |
| `.summary-placeholder` | 居中斜体提示文本 |
| `#summary-text` | `white-space: pre-wrap`，错误态变 `--warn` 色 |
| `.summary-loading` / `.summary-spinner` | 居中加载提示 + CSS 旋转动画（`@keyframes spin`） |
| `#summary-config` | 配置面板，`flex-shrink: 0` |
| `.config-row` / `.config-section-title` | 表单行布局和分区标题 |
| `.template-item` / `.template-name` / `.btn-template-action` | template 列表行，危险操作悬停红色 |

### 修改 — requirements.txt

| 变更 | 说明 |
|------|------|
| `openai>=1.0` | 新增 LLM 摘要依赖 |

### 新增 — tests/test_summary.py

| 测试类 | 用例数 | 覆盖 |
|--------|--------|------|
| `TestNonStreaming` | 4 | 返回字符串、空内容、API 参数正确性、client 构造参数 |
| `TestStreaming` | 4 | 返回可迭代、yield chunks、None delta 跳过、`stream=True` 传参 |
| `TestImportError` | 1 | `openai` 不可用时抛出 `ImportError` |

**Mock 策略：** `openai` 未安装，import 为延迟调用，使用 `patch.dict(sys.modules, {"openai": fake_mod})` + `importlib.reload(src.summary)` 注入 mock。

### 新增 — tests/test_app_summarize.py

| 测试类 | 用例数 | 覆盖 |
|--------|--------|------|
| `TestSummarizeReturn` | 1 | 返回 `{"status": "started", "job_id": ...}` |
| `TestSummarizeJSEvents` | 3 | chunk 事件数量、complete 事件存在、complete 含完整文本 |
| `TestSummarizeErrors` | 2 | transcript 不存在推送 error；API 抛异常推送 error |
| `TestSummarizeTranscriptText` | 1 | segment 文本拼接后正确传入 summarize；prompt 透传 |

**Mock 策略：** `patch("src.summary.summarize", ...)` 直接替换函数（app.py 在 `_run()` 内 `from src.summary import summarize`，每次调用时查找 module 属性，patch 有效）。`_push` 用 `side_effect=js_calls.append` 捕获所有 JS 调用。后台线程通过 `time.sleep(0.5)` 等待。

---

## pytest 验证结果

```
$ python -m pytest tests/ -q
136 passed in 3.04s
```

---

## 未完成项

c04 范围内全部完成。以下为 c05+ 待做项：

- `app.py`：`start_realtime`、`stop_realtime` 实现（c05）
- `src/realtime.py`：Silero VAD + sounddevice 实时转写（c05）
- `model_manager.py`：`snapshot_download` 细粒度进度（目前仅 0% 和 100%）
- `app.js`：转写进行中拖入新文件无中止机制（需 `cancel_transcribe` 接口）
- JS 单元测试（Jest）：`player.js`、`transcript.js`、`app.js`、`summary.js` 待补
- 集成测试（需真实模型和真实 LLM endpoint，留后续 cycle 联调时补充）
