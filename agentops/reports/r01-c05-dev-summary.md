# r01-c05 Dev Summary

**Branch:** feat/r01-builder
**Target SHA:** 869b78d
**Date:** 2026-03-21

---

## Commits

| SHA | Message |
|-----|---------|
| `869b78d` | feat(r01-c05): realtime transcription — Silero VAD + sounddevice pipeline |

---

## 变更文件范围

10 files changed, 950 insertions(+), 6 deletions(-)

### 新增 — src/realtime.py

麦克风实时转写管道，全部重型 import 均为懒加载（模块可在无 sounddevice / silero-vad / torch 的测试环境中直接 import）。

**常量：**

| 常量 | 值 | 说明 |
|------|----|------|
| `SAMPLE_RATE` | 16 000 Hz | Silero VAD 和主流 ASR 引擎要求 |
| `CHUNK_SIZE` | 512 samples | ~32 ms / 帧（Silero VAD 推荐） |
| `VAD_THRESHOLD` | 0.5 | 判定为语音的概率下限 |
| `SILENCE_CHUNKS` | 15 | 连续静默帧数触发段落结束（~480 ms） |
| `MIN_SPEECH_CHUNKS` | 5 | 最短有效语音帧数（~160 ms，短于此视为噪声丢弃） |

**`RealtimeTranscriber` 类：**

| 方法 / 属性 | 说明 |
|-------------|------|
| `__init__(engine, on_result, on_error)` | 构造；callback 默认为空函数；初始化 VAD 状态变量 |
| `start()` | 调用 `_load_models()` → 打开 `sounddevice.InputStream`；返回后麦克风已开始录音 |
| `stop()` | 设 `_running=False`；关闭并清理 stream；若 `_speech_buffer` 非空则 flush 剩余语音 |
| `_load_models()` | `silero_vad.load_silero_vad()` 加载 VAD；按 `engine` 参数加载 `ASREngine`（qwen）或 `WhisperEngine`（whisper）并调用 `.load()` |
| `_audio_callback(indata, ...)` | sounddevice 音频线程回调；取 mono 切片 → `torch.from_numpy` → VAD 推理；语音帧追加到 `_speech_buffer`；静默帧计数，达到 `SILENCE_CHUNKS` 时调用 `_flush_speech()` |
| `_flush_speech()` | 复制并清空 buffer；帧数不足 `MIN_SPEECH_CHUNKS` 直接丢弃；否则在 daemon 线程中运行 `_transcribe_segment()` |
| `_transcribe_segment(chunks)` | `numpy.concatenate` → `_write_wav(tmp.wav)` → `self._asr.transcribe(path)` → 非空结果调用 `on_result`；异常调用 `on_error`；finally 清理临时文件 |

**`_write_wav(path, audio, sample_rate)`：**

仅用 stdlib `wave` 模块。float32 数组 × 32767 截断为 int16，写入单声道 16-bit WAV，无需 `soundfile` 或 `scipy` 额外依赖。

### 修改 — app.py

| 变更 | 说明 |
|------|------|
| `API.__init__()` | 新增构造函数，初始化 `self._realtime = None` |
| `start_realtime(options)` | 若 `self._realtime` 非 None 返回 `{"status": "already_running"}`；否则先写入 sentinel（`object()`）防止竞态，后台线程中构造 `RealtimeTranscriber`、调用 `.start()`、push `onRealtimeStarted()`；异常时清空 `self._realtime` 并 push `onRealtimeError(msg)`；返回 `{"status": "started"}` |
| `stop_realtime()` | 立即清空 `self._realtime`；若原值为 None 返回 `{"status": "not_running"}`；后台线程调用 `.stop()` 并 push `onRealtimeStopped()`；返回 `{"status": "stopped"}` |
| JS 事件 | `onRealtimeStarted()` / `onRealtimeStopped()` / `onRealtimeResult(text)` / `onRealtimeError(message)` |

**竞态设计说明：** `start_realtime()` 将 `self._realtime` 设为非 None sentinel 再启线程，保证第二次调用在线程完成前仍能正确返回 `already_running`。

### 新增 — ui/js/realtime.js

IIFE 模块，风格与 `summary.js` / `player.js` 一致。

| 功能 | 说明 |
|------|------|
| `Realtime.init()` | 绑定 DOM 引用及按钮事件 |
| `_onToggle()` | 录音中 → 调用 `stop_realtime()`；否则 → 读取 `sel-engine` 值，调用 `start_realtime({engine})` |
| `_setLoading(active)` | 切换按钮禁用状态（等待 Python 回应期间防止重复点击） |
| `onStarted()` | 按钮改为 "⏹ Stop"，加 `.recording` 类；状态点激活（红色 pulse）；清空列表 |
| `onStopped()` | 按钮恢复 "🎙 Realtime"；状态点熄灭 |
| `onResult(text)` | 追加 `.realtime-row` 到列表并自动滚动到底部 |
| `onError(message)` | 重置按钮和状态点；显示 ⚠ 前缀错误文本 |
| 全局回调 | `onRealtimeStarted` / `onRealtimeStopped` / `onRealtimeResult` / `onRealtimeError`（供 Python `evaluate_js` 调用） |

### 修改 — ui/index.html

| 变更 | 说明 |
|------|------|
| `#btn-realtime` | 工具栏新增 "🎙 Realtime" 按钮（`.btn-realtime` 样式类） |
| `#realtime-panel` | transcript-panel 内新增（默认 `hidden`）；含 `#realtime-header`（状态点 + 文字）和 `#realtime-list`（逐句结果列表） |
| `<script>` 引用 | `realtime.js` 插入 `summary.js` 与 `app.js` 之间 |

### 修改 — ui/js/app.js

| 变更 | 说明 |
|------|------|
| `dom.realtimePanel` | 新增 DOM 引用 `#realtime-panel` |
| `Realtime.init()` | 在 `init()` 中 `Summary.init()` 之后调用 |
| `_setView('realtime')` | `dom.realtimePanel.hidden = state !== 'realtime'` 加入视图状态机 |

### 修改 — ui/css/main.css

新增约 77 行实时转写相关样式：

| 选择器 | 说明 |
|--------|------|
| `.btn-realtime` | 默认边框风格；`.recording` 态红色边框 + 淡红背景；`:disabled` 半透明 |
| `#realtime-panel` | flex 列布局，占满剩余高度 |
| `#realtime-header` | 状态点 + 文字横排，底部分隔线 |
| `.realtime-dot` | 8px 圆点；`.active` 态红色 + `box-shadow` + `@keyframes pulse` 呼吸动画 |
| `.realtime-status-text` | 12px 静音色文字 |
| `#realtime-list` | 可滚动列表，webkit 滚动条美化 |
| `.realtime-row` | 单句行，最新行左侧 `var(--accent)` 强调线 |

### 修改 — requirements.txt

| 新增依赖 | 说明 |
|----------|------|
| `sounddevice>=0.4` | 跨平台麦克风输入（PortAudio 绑定） |
| `silero-vad>=4.0` | 语音活动检测（PyTorch，通过 PyPI 安装） |
| `numpy>=1.24` | 音频数组处理（torch 的传递依赖，此处显式声明） |

### 修改 — README.md

- Features 版本号更新为 v0.4 — r01-c05
- 新增特性列表条目：实时转写
- 新增 `## Realtime transcription` 章节：工作原理（VAD → 段落检测 → ASR）、使用步骤、注意事项、附加依赖表 + 安装命令

### 新增 — tests/test_realtime.py

| 测试类 | 用例数 | 覆盖 |
|--------|--------|------|
| `TestDefaults` | 4 | 默认 engine、初始状态、默认 noop callback、自定义 callback |
| `TestAudioCallback` | 7 | 语音帧累积、静默前语音忽略、静默计数递增、达阈值触发 flush、未达阈值不 flush、`_running=False` 提前退出、VAD 异常降级为静默 |
| `TestFlushSpeech` | 3 | 清空 buffer 并重置状态、短段落丢弃、足够长度启动 thread |
| `TestTranscribeSegment` | 4 | 正常回调 text、空白文本不回调、ASR 异常触发 on_error、临时文件清理 |
| `TestStop` | 4 | flush 剩余 buffer、空 buffer 跳过 flush、`_running` 置 False、stream stop+close |
| `TestWriteWav` | 2 | 合法 WAV 头信息、float32 截断为 int16 范围 |

**Mock 策略：** `torch`、`silero_vad`、`sounddevice` 均通过 `patch.dict(sys.modules, ...)` 注入 fake module；`_import_realtime()` 辅助函数每次 `importlib.reload(src.realtime)` 确保 fake 生效。`_make_rt()` 创建实例后直接替换 `_vad` / `_asr` 属性，绕过 `_load_models()` 调用。

### 新增 — tests/test_app_realtime.py

| 测试类 | 用例数 | 覆盖 |
|--------|--------|------|
| `TestReturnValues` | 4 | started、already_running、not_running、stopped |
| `TestJSEvents` | 5 | onRealtimeStarted、异常推送 onRealtimeError、异常后 `_realtime` 清空、onRealtimeStopped、on_result/on_error callback 到 JS 的全链路 |
| `TestEngineOption` | 2 | engine 参数透传、默认 engine 为 qwen |

**Mock 策略：** `src.realtime` 模块所有外部依赖均为懒加载，直接 `patch("src.realtime.RealtimeTranscriber")` 即可；`app_module._push` 用 `side_effect=js_calls.append` 捕获所有 JS 调用；callback 测试在 `with` 块内调用（确保 `_push` mock 仍有效）。

---

## pytest 验证结果

```
$ python -m pytest tests/ -q
172 passed in 5.51s
```

---

## 未完成项

c05 范围内全部完成。以下为 c06+ 待做项：

- `app.js`：实时录音进行中时，拖入文件 / Open File 应提示或阻止（两模式互斥）
- `app.js`：转写进行中拖入新文件无中止机制（需 `cancel_transcribe` 接口）
- `realtime.js`：实时结果支持复制到剪贴板
- `model_manager.py`：`snapshot_download` 细粒度进度（目前仅 0% 和 100%）
- JS 单元测试（Jest）：`player.js`、`transcript.js`、`app.js`、`summary.js`、`realtime.js` 待补
- 集成测试（需真实模型和麦克风，留后续 cycle 联调时补充）
