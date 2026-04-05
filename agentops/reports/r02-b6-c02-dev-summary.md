# r02-b6 c02 Dev Summary — Mix 模式 + UI 模式选择 + 权限引导

## 交付物

| 文件 | 说明 |
|------|------|
| `native/AudioAssistCaptureHelper/Sources/AudioAssistCaptureHelper/main.swift` | 补全 mix 模式：AudioMixer + AVAudioEngine 麦克风采集 |
| `ui/index.html` | 新增捕获模式选择器（Mic / System / Mix）+ 权限通知区 |
| `ui/css/main.css` | 新增 `.btn-mode` 分段按钮 + `#capture-perm-notice` 样式 |
| `ui/js/realtime.js` | 模式选择逻辑、preflight 调用、权限通知显示 |
| `app.py` | 新增 `open_privacy_settings()` API |
| `tests/test_native_capture.py` | 新增 4 个测试，共 42 个 |

---

## 架构决策

### 1. AudioMixer（Swift）— 无锁环形缓冲混音

```swift
final class AudioMixer {
    private var systemBuf: [Float] = []
    private var micBuf:    [Float] = []
    
    func appendSystem(_ s: [Float]) { systemBuf += s }
    func appendMic(_ m: [Float])    { micBuf    += m }
    
    func drainAll() -> [Float] {
        // max(sys, mic) 长度，较短端补零；结果 clamp[-1, 1]
    }
}
```

关键设计：无锁。`appendSystem` 和 `appendMic` 均从 `writeQueue`（串行队列）调用，因此不需要任何 mutex。`drainAll()` 也在同一队列执行，天然线程安全。

### 2. Mix 模式刷新策略

- 每次 `appendSystem` 或 `appendMic` 后调用 `tryFlushMix()`
- 刷新阈值：`max(systemCount, micCount) >= 512`（32 ms @ 16 kHz）
- 较短流用零填充 → 单路静音时另一路仍正常输出（麦克风无声时系统音频不卡顿）
- 关闭前 `drainAll()` 清空残余

### 3. 麦克风采集（AVAudioEngine）

```swift
private func startMicCapture() {
    let engine = AVAudioEngine()
    let inputNode = engine.inputNode
    let inputFormat = inputNode.outputFormat(forBus: 0)
    // ... AVAudioConverter(from: inputFormat, to: dstFormat) ...
    inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { buffer, _ in
        // convert → 16kHz/mono/float32
        writeQueue.async { mixBuffer.appendMic(samples); tryFlushMix() }
    }
    try engine.start()
}
```

麦克风失败非致命：emit `warning` 事件，session 继续以系统音频单路进行。用户侧 JS 可决定是否提示。

### 4. writeSamples() 统一写路径

两种模式（system 直写、mix 混后写）最终都调用同一个 `writeSamples(_ samples: [Float])`：

```swift
private func writeSamples(_ samples: [Float]) {
    samples.withUnsafeBufferPointer { ptr in
        // FIFO non-blocking write (drop on EAGAIN)
        Darwin.write(fd, ptr.baseAddress!, byteCount)
        // WAV int16 write
        wavWriter?.write(floats: ptr.baseAddress!, count: samples.count)
    }
}
```

### 5. UI 模式选择器

位置：侧边栏历史面板底部，Start Recording 按钮上方。
三个 `.btn-mode` 分段按钮：`Mic / System / Mix`，默认 `Mic`。

行为：
- 录音中：全部 `disabled`，防止切换
- 停止后：恢复可点击
- 状态文案包含模式：`"Recording · 系统音频"`

### 6. Preflight 流程（realtime.js）

```js
async function _onToggle() {
    if (_captureMode !== 'mic') {
        const pre = await window.pywebview.api.preflight_capture(_captureMode);
        if (!pre.supported) {
            _showPermNotice(pre);
            return;  // 不开始录音
        }
    }
    // ... 正常启动，传 capture_mode
    await window.pywebview.api.start_realtime({ model_id, capture_mode: _captureMode });
}
```

权限通知四种情况：
| reason | 文案 | 显示"打开系统设置"按钮 |
|--------|------|----------------------|
| `screencapturekit_requires_macos_13_0` | 需要 macOS 13.0+ | 否 |
| `helper_not_found` | 未找到助手，请先构建 | 否 |
| `permission_required:*` | 需要屏幕录制权限 | **是** |
| `mic_capture_failed` | 麦克风不可用，混合模式降级 | 否 |

---

## 测试结果

```
tests/test_native_capture.py    42 passed  ← 新增 4（TestMixModeHelperCommand ×2, TestOpenPrivacySettings ×2）
全量门禁（不含 webview 缺失）   571 passed
预存失败（webview 未安装）        13 failed ← 与本 c02 无关，无回归
```

Swift build: `Build complete!` (macOS 13.0, release, arm64)

---

## 当前未覆盖（留 c03）

- `realtime.py` 采集源解耦（c02 不含）
- meta.json 写入 `capture_mode / capture_backend` 字段
- 混合模式麦克风失败时的前端降级提示（runtime warning 事件 → JS 回调）

---

---

## Gate No-Go 修复（Gate Commit: 7e16724）

| 问题 | 级别 | 修复 |
|------|------|------|
| mix 混音逻辑错误：`drainAll()` 强制清空双路，导致串行输出 | P1 | 改为 `drain(chunkSize: 512)` 分块截断，各路独立缓冲，多余样本保留到下次 flush |
| README 缺三模式说明 | P1 | 新增 "Capture modes" 章节：模式表、macOS 13.0+ 门槛、Screen Recording 权限步骤、麦克风降级说明 |
| 麦克风降级静默（mix 模式 mic 不可用时无前端提示） | P2 | `native_capture.py` 处理 `warning` 事件 → `_on_error("mic_degraded:*")`；`realtime.js` 中 `mic_degraded:*` 非致命，录音继续并展示 inline 提示 |

新增测试 +5：`TestWarningEventHandling`（mic_unavailable/mic_converter_failed/mic_capture_failed/unknown_warning/reason_in_message），共 47 个测试全通过。

---

## Commits

| SHA | 说明 |
|-----|------|
| `3d9acf5` | `feat(r02-b6-c02)`: mix mode, UI capture mode selector, permission guidance |
| `7e16724` | `fix(r02-b6-c02)`: Gate No-Go 修复（混音逻辑、README、mic 降级）|
