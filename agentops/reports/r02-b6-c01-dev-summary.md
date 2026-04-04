# r02-b6 c01 Dev Summary — ScreenCaptureKit System Audio Capture (Phase 1)

## 交付物

| 文件 | 说明 |
|------|------|
| `native/AudioAssistCaptureHelper/Package.swift` | Swift Package，最低 macOS 13.0 |
| `native/AudioAssistCaptureHelper/Sources/AudioAssistCaptureHelper/main.swift` | Swift helper 完整实现 |
| `src/native_capture.py` | Python 封装层 |
| `app.py` | 新增 `preflight_capture` + `start_realtime` capture_mode 路由 |
| `tests/test_native_capture.py` | 33 个单元测试 |
| `README.md` | 更新至 v0.14，新增 system audio capture 功能说明 |

---

## 架构决策

### 1. macOS 最低版本：13.0（非 PRD 的 12.3）

PRD 写的是 12.3，但 `SCStreamOutputType.audio` 和 `SCStreamConfiguration.sampleRate` / `channelCount` 实际上都是 macOS 13.0 API。Swift 编译器在 Package.swift 指定 12.3 时直接报 error。实际结论：

- `SCStream` 本体：12.3
- **音频捕获 (`SCStreamOutputType.audio`)**：13.0

已将 Package.swift、Swift guard、Python preflight 全部更新为 13.0，`reason` 字段改为 `screencapturekit_requires_macos_13_0`。

### 2. `NativeCaptureHelper` 兼具子进程管理 + VAD/ASR

PRD 允许 "保守实现 HelperRealtimeTranscriber"（提高维护成本，但 Phase 1 可接受）。`NativeCaptureHelper` 直接实现了与 `RealtimeTranscriber` 相同的 public interface：
- `start / pause / resume / stop / get_segments`
- `on_result / on_error` 回调
- `_output_path` 属性

这样 `app.py` 的 `pause_realtime / resume_realtime / stop_realtime` 完全不需要修改，按 mode 分支只在 `start_realtime._run()` 内完成。

### 3. FIFO 生命周期

- Python 先用 `O_RDONLY | O_NONBLOCK` 打开 FIFO read 端（保证 helper `O_WRONLY` 不阻塞）
- 切换为阻塞读，PCM 线程用 `os.read(fd, chunk_bytes)` 累积满 512 sample 再处理
- helper 退出时 write 端关闭 → `os.read` 返回空 bytes → PCM 线程自然退出
- `stop()` 在 terminate 后也 close fd（belt-and-suspenders）

### 4. Swift helper 信号处理

使用 `DispatchSource.makeSignalSource(queue: backgroundQueue)` 而非 `signal()` 函数，主线程 `stopSemaphore.wait()` 可安全阻塞。pause/resume 分别由 SIGUSR1/SIGUSR2 驱动。

### 5. 音频格式

在 macOS 13.0+ 直接设置 `SCStreamConfiguration.sampleRate = 16000` 和 `channelCount = 1`，SCStream 输出已是 16 kHz mono，`AVAudioConverter` 退化为直通。仍保留 converter 逻辑以应对不同 macOS 版本的格式差异。

FIFO 输出：float32 little-endian，16 kHz，mono
WAV 输出：PCM16，16 kHz，mono，session 目录下

---

## 测试结果

```
tests/test_native_capture.py  33 passed
全量门禁（不含 webview 缺失的 export/obsidian 测试）  557 passed（Gate fix 后）
预存失败（webview 未安装）  13 failed  ← 与本 c01 无关，无回归
```

Swift build: `Build complete!` (macOS 13.0, release, arm64)

---

## Gate No-Go 修复（Gate Commit: ca346dd）

| 问题 | 级别 | 修复 |
|------|------|------|
| mix 模式退化到 mic-only | P1 | `app.py` capture_mode `in ("system", "mix")` 均走 NativeCaptureHelper |
| README 缺 macOS 13.0+/Xcode CLT/swift build 步骤 | P1 | 新增"Build the Swift helper"章节 |
| start() 异常路径 FIFO fd + worker thread 泄漏 | P2 | try/except 包裹，异常时 close fd、unlink FIFO、清零 worker_thread |
| Swift `didStopWithError` 不触发 stopSemaphore | P2 | 注入 `onFatalError` 回调，异常退出时 signal stopSemaphore |

新增测试 +5：mix 模式路由 ×3、start() 清理 ×2，共 38 个测试全通过。

---

## 当前未覆盖（留 c02）

- mix 模式麦克风采集（NativeCaptureHelper mode="mix" 目前仅路由，helper 侧 mix 实现留 c02）
- UI 模式选择控件（前端 capture_mode 下拉/分段按钮）
- 权限引导弹窗（preflight 结果反馈到前端）
- meta.json 写入 capture_mode / capture_backend 字段

---

## Commits

| SHA | 说明 |
|-----|------|
| `adbfa9a` | `feat(r02-b6-c01)`: 主交付物 |
| `bd04d0f` | `chore`: gitignore Swift .build 产物 |
| `7744f20` | `docs(dev)`: dev summary 初版 |
| `ca346dd` | `fix(r02-b6-c01)`: Gate No-Go 修复（mix 路由、README、泄漏、异常退出）|
