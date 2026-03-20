# ARCHITECTURE

## 项目结构

```
audioassist-main/               # Leader 主仓库
├── agentops/                   # 多 Agent 协作框架
│   ├── RULES.md
│   ├── TASK.md
│   ├── CONTEXT.md
│   ├── ARCHITECTURE.md
│   ├── DECISIONS.md
│   ├── TODO.md
│   └── reports/
├── src/                        # 后端核心逻辑
│   ├── asr.py                  # Qwen3-ASR 引擎
│   ├── asr_whisper.py          # Whisper 引擎（mlx / faster-whisper）
│   ├── diarize.py              # 说话人分离（pyannote）
│   ├── merge.py                # 时间戳 + 说话人合并，输出 MD/JSON
│   ├── pipeline.py             # 完整文件转写流程
│   ├── realtime.py             # 实时转写（VAD + 麦克风）【待开发】
│   ├── model_manager.py        # 模型下载/管理
│   ├── audio_utils.py          # ffmpeg 格式转换 / 长音频分块
│   └── summary.py              # LLM 摘要（调用本地/云端 API）【待开发】
├── ui/
│   ├── index.html              # 主界面入口
│   ├── css/
│   │   └── main.css
│   └── js/
│       ├── app.js              # 主逻辑、PyWebView bridge
│       ├── player.js           # 音频播放器（seek to timestamp）
│       ├── transcript.js       # 转写结果展示、行内编辑、保存
│       ├── realtime.js         # 实时字幕
│       └── summary.js          # 摘要面板
├── app.py                      # PyWebView 入口，暴露 Python API
├── run.py                      # 启动入口
├── requirements.txt
├── .python-version             # 3.12.2
└── scripts/
    └── agentops_bootstrap.sh
```

## 核心模块职责

### app.py — PyWebView API 桥

暴露给 JS 的 Python API：

```python
class API:
    def select_file(self)               # 文件选择对话框
    def transcribe(self, path, options) # 触发文件转写 pipeline
    def get_transcript(self, job_id)    # 获取转写结果
    def save_transcript(self, job_id, edits) # 保存编辑后的转写
    def start_realtime(self)            # 开始实时转写
    def stop_realtime(self)             # 停止实时转写
    def summarize(self, job_id, template) # 触发摘要生成
    def get_models(self)                # 列出模型及状态
    def download_model(self, name)      # 下载模型（带进度推送）
    def save_api_config(self, config)   # 保存 LLM API 配置
    def get_api_config(self)            # 读取 LLM API 配置
    def save_summary_templates(self, templates) # 保存摘要模板
    def get_summary_templates(self)     # 读取摘要模板
```

Python 推送给 JS 的事件（通过 `window.evaluate_js()`）：

```
onTranscribeProgress(job_id, progress, partial_block)  # 转写进度 + 实时分段
onRealtimeSegment(block)                                # 实时转写新句子
onModelDownloadProgress(name, percent)                  # 模型下载进度
```

## UI 功能模块

### 转写视图（transcript.js）

- 每个 SpeakerBlock 渲染为一行：`[时间戳] SPEAKER_XX  文本内容`
- 点击任意一行 → 调用 `player.seekTo(start)` 跳转音频
- 双击文本 → 进入行内编辑模式（contenteditable）
- 编辑完成 → `save_transcript()` 写回 JSON/MD 文件
- 编辑状态有"未保存"标记

### 音频播放器（player.js）

- HTML5 `<audio>` 元素，加载完整音频文件
- `seekTo(seconds)` 接口，供转写行点击调用
- 播放时高亮当前正在播放的转写行（按时间戳匹配）
- 支持拖拽进度条

### 实时字幕（realtime.js）

- 接收 `onRealtimeSegment` 事件，逐句追加显示
- 每 30s 积累音频后 pyannote 回填 Speaker 标签（异步更新）
- 提供开始/停止按钮

### 摘要面板（summary.js）

- **API 配置**：填写 base_url、api_key、model_name（兼容 OpenAI 接口）
- **模板管理**：用户可创建/编辑/删除多个摘要风格模板（名称 + prompt 文本）
- **生成摘要**：选模板 → 调用 `summarize()` → 流式或一次性显示结果
- **保存**：摘要可导出为 MD

## 数据流

### 文件转写

```
用户拖拽/选择文件
    → app.py: transcribe(path, options)
    → pipeline.py: run()
        → audio_utils: 格式转换 + 分块
        → asr.py / asr_whisper.py: 转写（带进度回调）
        → diarize.py: 说话人分离
        → merge.py: 合并 → SpeakerBlock[]
    → evaluate_js("onTranscribeProgress(...)")  # 实时推进度
    → 写 output/{job_id}.json + .md
    → JS 渲染转写结果
```

### 实时转写

```
麦克风 → sounddevice
    → Silero VAD（0.8s 静音触发分句）
    → ASREngine.transcribe(chunk) + offset → 绝对时间戳
    → evaluate_js("onRealtimeSegment(block)")
    → 每 30s → pyannote 回填 Speaker → evaluate_js 更新
```

### 摘要生成

```
转写文本（已编辑版）
    → summary.py: summarize(text, template, api_config)
    → 调用 LLM API（OpenAI 兼容接口）
    → 流式返回 → evaluate_js 逐 token 推给 UI
```

## 配置文件存储

```
~/.local/share/TranscribeApp/
├── models/          # 模型文件
├── config.json      # API 配置、模型选择
├── templates.json   # 摘要模板列表
└── output/          # 转写历史（job_id/）
```
