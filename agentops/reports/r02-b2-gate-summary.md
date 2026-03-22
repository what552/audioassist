# r02-b2 Gate Summary

- 目标分支：`feat/r01-builder`
- Baseline SHA：`afae469`（r02-a 合并点）
- Target SHA（Gate Commit）：`1b90328`
- 覆盖批次：r02-b1（1 commit）
- 评审日期：2026-03-22

---

## 覆盖范围

| 批次 | SHA | 内容 |
|------|-----|------|
| r02-b1 | `1b90328` | fix(realtime): serialize MLX transcription via worker queue — prevent Metal GPU concurrent crash |

---

## 问题背景

用户实测发现 realtime 录音在 pause/resume 操作后崩溃：
```
-[AGXG17XFamilyCommandBuffer tryCoalescingPreviousComputeCommandEncoderWithConfig:nextEncoderClass:]:1094:
failed assertion 'A command encoder is already encoding to this command buffer'
zsh: abort      python run.py
```
根因：`_flush_speech()` 每次起新线程跑 MLX 推理，多线程并发访问 Metal GPU 触发断言。

---

## Reviewer-1 结论：Go ✅

- 全量测试：237/237 通过（7.91s）

**确认项：**
- `_transcription_worker()` 串行逻辑正确（sentinel、task_done、try/finally 完整）✅
- `stop()` 顺序：停 stream → 关 WAV writer → flush → sentinel → join ✅
- `pause()` 不停 worker，已入队任务继续消化 ✅
- `_flush_speech()` 改为 `queue.put()`，不再起并发线程 ✅
- 测试覆盖：TestWorkerQueue 4 用例 + TestFlushSpeech 无线程生成验证 ✅
- README 新增 MLX serial execution 说明 ✅

**P3 遗留：** `stop()` 在 `start()` 前调用时 sentinel 静默跳过，调用者保证顺序，实际无风险

---

## Reviewer-2 结论：Go ✅

- 全量测试：237/237 通过

**确认项：**
- `_transcription_worker()` 串行循环逻辑正确 ✅
- `stop()` flush → sentinel → join 顺序严格正确，末段语音不丢失 ✅
- `pause()` 完全不碰 worker ✅
- `_flush_speech()` 干净替换为 `queue.put()` ✅

**P3 遗留：**
- P3-1：并发测试 `threading.local` 检测机制可补注释
- P3-2：`resume()` 未检查 worker 是否存活，极端场景队列可能堆积

---

## P1/P2 处理汇总

| 批次 | 级别 | 问题 | 状态 |
|------|------|------|------|
| r02-b1 | P0 | MLX Metal GPU 并发崩溃，abort | ✅ 1b90328 修复 |

---

## 遗留项

- stop() 在 start() 前调用时 sentinel 静默跳过 — P3，backlog
- resume() 未检查 worker 存活 — P3，backlog

---

## Gate 决定：通过 ✅

Builder 分支 `feat/r01-builder` @ `1b90328` 覆盖 r02-b1，允许合并到 `main`。
