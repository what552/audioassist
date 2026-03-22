# r02-b1 Dev Summary

**Branch:** feat/r01-builder
**Target SHA:** 1b90328
**Baseline SHA:** afae469
**Date:** 2026-03-22

---

## Commits

| SHA | Message |
|-----|---------|
| `1b90328` | fix(realtime): serialize MLX transcription via worker queue — prevent Metal GPU concurrent crash |

---

## 变更文件范围

3 files changed

| 文件 | 改动 |
|------|------|
| `src/realtime.py` | 新增 `queue.Queue` + worker thread；`_flush_speech()` 改为入队；新增 `_transcription_worker()` |
| `tests/test_realtime.py` | 更新 `TestFlushSpeech`；新增 `TestWorkerQueue`（4 用例） |
| `README.md` | Realtime Notes 补充 MLX serial execution 说明 |

---

## 问题

**MLX Metal GPU 并发崩溃（Apple Silicon）**

原实现在 `_flush_speech()` 中每次检测到完整语音段时直接 `threading.Thread(target=self._transcribe_segment, ...).start()`。若短时间内连续检测到多个语音段（例如 pause/resume 时一次性 flush 多段），会同时有多个线程调用 `mlx-whisper`，触发 Metal 断言失败：

```
A command encoder is already encoding to this command buffer
```

进程崩溃，realtime 会话终止。

---

## 修复

**串行 worker queue 替代直接起线程**

### `src/realtime.py` 变更

**新增属性（`__init__`）：**

```python
self._transcribe_queue: queue.Queue = queue.Queue()
self._worker_thread: Optional[threading.Thread] = None
```

**`start()` 新增 worker 启动：**

```python
self._worker_thread = threading.Thread(
    target=self._transcription_worker,
    daemon=True,
    name="realtime-transcribe-worker",
)
self._worker_thread.start()
```

**`stop()` 新增 sentinel + join：**

```python
if self._worker_thread is not None and self._worker_thread.is_alive():
    self._transcribe_queue.put(None)   # sentinel
    self._worker_thread.join()
    self._worker_thread = None
```

**`_flush_speech()` 改为入队（不再 spawn 线程）：**

```python
# 原实现
threading.Thread(target=self._transcribe_segment, args=(buf,), daemon=True).start()

# 新实现
self._transcribe_queue.put(buf)
```

**新增 `_transcription_worker()`：**

```python
def _transcription_worker(self) -> None:
    while True:
        item = self._transcribe_queue.get()
        if item is None:          # sentinel — exit
            self._transcribe_queue.task_done()
            break
        try:
            self._transcribe_segment(item)
        finally:
            self._transcribe_queue.task_done()
```

**`pause()` 行为不变：** 仅停止 stream，不发 sentinel；worker 保持存活以消费 pause 前入队的剩余段落。

---

## 测试

### 更新 — `TestFlushSpeech`

| 用例 | 变更 |
|------|------|
| `test_long_enough_segment_enqueues_for_transcription` | 原：检查 `threading.Thread` 被调用；改为：检查 `rt._transcribe_queue.qsize() == 1` |
| `test_flush_does_not_spawn_extra_threads` | 新增：连续 flush 3 次，断言未产生额外线程 |

### 新增 — `TestWorkerQueue`（4 用例）

| 用例 | 覆盖 |
|------|------|
| `test_worker_processes_queued_items` | 3 段落入队 + sentinel，验证全部按顺序处理 |
| `test_worker_never_runs_two_transcriptions_concurrently` | threading.local 标记验证同一时刻只有一个 `_transcribe_segment` 在执行 |
| `test_stop_sends_sentinel_and_joins_worker` | stop() 后 `_worker_thread` 置 None |
| `test_pause_does_not_stop_worker` | pause() 后 worker 仍存活 |

### 结果

```
$ python -m pytest -q
237 passed in 7.40s
```

---

## 未完成项

无。r02-b1 范围内全部完成。

以下为已知遗留 UI 问题（r02-a review 时发现，用户决定下轮处理）：

- 空格键控制 Player 播放/暂停（player.js 未加 keydown space 监听）
- `_setView('file-done')` 未隐藏 `#empty-hint`，导致 idle 引导语残留
- 左侧历史时间仅显示日期，同天多次转写无法区分
- Summary Generating… spinner 在 `onSummaryComplete` 后可能未消失
