# CONTEXT

## 项目背景

TranscribeApp —— 本地运行的语音转文字桌面应用。
前身是 `/Users/feifei/programing/local asr/`，核心 pipeline 已验证，现在做成完整产品。

代码库文件夹名：`audioassist`
应用名：**TranscribeApp**

## 技术栈

### 后端（Python 3.12.2）
- **ASR 引擎**：Qwen3-ASR 1.7B（中文优先，CPU）/ mlx-whisper（Apple Silicon）/ faster-whisper（Windows/Linux）
- **说话人分离**：pyannote/speaker-diarization-3.1（pyannote.audio 4.0.4）
- **音频处理**：ffmpeg（格式转换、长音频分块 300s/块）
- **实时转写**：Silero VAD + sounddevice + ASR（待开发）
- **UI 框架**：PyWebView（原生 WKWebView / WebView2，Python 直接调用）
- **打包**：PyInstaller → macOS DMG / Windows EXE（模型不打包，首次启动引导下载）

### 前端（HTML/CSS/JS，运行在 PyWebView 内）
- 原生 JS 为主，无需 Node 生态
- Python ↔ JS 通信：`window.pywebview.api.xxx()` 调 Python；Python 用 `window.evaluate_js()` 推数据给 UI

## 平台目标

- macOS Apple Silicon (M5 Pro) —— 主要开发平台
- Windows —— 次要，需兼容

## 环境变量

```
HF_ENDPOINT=https://hf-mirror.com   # 国内必须
HF_HUB_OFFLINE=1                    # 模型下载后离线
HF_TOKEN=hf_xxx                     # pyannote 首次下载
```

## 已知问题与约束

| 问题 | 影响 | 处理方式 |
|------|------|----------|
| Qwen3-ASR 在 MPS 上 SIGBUS | 强制用 CPU | 等待上游修复 |
| mlx-whisper 静音段幻觉 | 重复词 | `hallucination_silence_threshold=2.0` + VAD 预处理 |
| pyannote 短片段 UNKNOWN | 说话人边界不准 | 后处理合并 |
| HuggingFace 国内访问 | 下载失败 | hf-mirror.com |

## 模型路径约定

```
~/.local/share/TranscribeApp/models/
├── qwen3-asr-1.7b/
├── qwen3-forced-aligner/
├── whisper-large-v3-turbo/
└── whisper-large-v3/
```

## 迁移来源

`/Users/feifei/programing/local asr/src/` 中以下模块已验证，可直接迁移：
- `asr.py`（Qwen3-ASR）
- `asr_whisper.py`（Whisper）
- `diarize.py`（pyannote）
- `merge.py`（合并输出）
- `pipeline.py`（完整流程）
- `model_manager.py`（模型管理）
- `audio_utils.py`（格式转换/分块）

`realtime.py` 未开发，需新建。
