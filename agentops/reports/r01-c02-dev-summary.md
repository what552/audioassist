# r01-c02 Dev Summary

**Branch:** feat/r01-builder
**Target SHA:** 80ed0e3
**Date:** 2026-03-21

---

## Commits

| SHA | Message |
|-----|---------|
| `80ed0e3` | feat(r01-c02): transcript view + audio player |

---

## 变更文件范围

6 files changed, 851 insertions(+), 31 deletions(-)

### 新增 — ui/js/

| 文件 | 说明 |
|------|------|
| `ui/js/player.js` | HTML5 `<audio>` 封装；path → `file://` URL 转换（POSIX + Windows）；`seekTo(seconds)` seek + play；`onTimeUpdate(cb)` 用于高亮同步；暴露 `currentTime` / `duration` getter |
| `ui/js/transcript.js` | `render(data, jobId, container)`：从 `{speaker, start, end, text, words}` 构建行；点击行 → `Player.seekTo(start)`；双击文字 → `contenteditable` 内联编辑（Enter/Blur 保存，Escape 取消）；未保存行添加 `.unsaved` class 并触发 `transcript:unsaved` 自定义事件；`highlightAt(seconds)` 切换 `.active` 并 smooth scroll；`saveAll()` 收集编辑内容（保留原始 `words[]`）并调用 Python `save_transcript` |

### 重写 — ui/js/

| 文件 | 说明 |
|------|------|
| `ui/js/app.js` | 完整重写：状态机 `idle → transcribing → done`；拖放处理（PyWebView 使用 `file.path` 获取原生路径）；连接 `Player.onTimeUpdate → Transcript.highlightAt` + 时间显示；`onTranscribeProgress` / `onTranscribeComplete` / `onTranscribeError` 全局 handler 供 Python `evaluate_js` 回调 |

### 修改 — ui/

| 文件 | 变更内容 |
|------|---------|
| `ui/index.html` | 工具栏：应用标题、引擎选择器、打开文件按钮；左面板：拖放区 / 进度条 / transcript 列表；右面板：文件名、HTML5 audio 元素、时间显示 |
| `ui/css/main.css` | 暗色主题 + 设计 token（`--bg`、`--accent`、`--warn` 等）；transcript 行状态样式：hover、`.active`（蓝色左边框）、`.unsaved`（橙色）、`.editing`；进度条 CSS transition；可滚动 transcript 区域 + 自定义滚动条 |

### 修改 — app.py

| 方法 | 变更内容 |
|------|---------|
| `save_transcript` | 原子 JSON 写入后，自动调用 `to_markdown()` 重新生成 `.md` sidecar；MD 生成失败仅 warning，不中断保存 |

---

## pytest 验证结果

```
$ python -m pytest tests/ -v
78 passed in 0.21s
```

全部 78 项通过，无新增测试（UI 层无法在无浏览器环境下做单元测试）。

---

## 未完成项

c02 范围内全部完成。以下为 c03+ 待做项：

- `app.py`：`summarize`、`save_api_config`、`get_api_config`、`save_summary_templates`、`get_summary_templates` 实现（c03）
- `app.py`：`start_realtime`、`stop_realtime` 实现（c04）
- `src/summary.py`：LLM 摘要模块（c03）
- `src/realtime.py`：Silero VAD + sounddevice 实时转写（c04）
- `model_manager.py`：`snapshot_download` 细粒度进度（目前仅 0% 和 100%）
- 集成测试（需真实模型，留后续 cycle 联调时补充）
