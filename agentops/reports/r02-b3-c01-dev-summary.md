# r02-b3 Development Summary

- Builder 分支：`feat/r01-builder`
- Baseline SHA：`9a3c620`（r02-b2 合并点，main HEAD）
- Target SHA（Gate Commit）：`44d3830`
- 开发日期：2026-03-22
- 测试结果：318 passed（294 → 318，+24 新测试）

---

## 变更批次

| SHA | 内容 |
|-----|------|
| `44d3830` | feat(r02-b3): model library modal + realtime timestamps + diarize-only finish |

---

## 主要变更说明

### P1 — 模型管理 UI

- Header 新增「Models」按钮，点击打开模型管理弹窗
- 弹窗列出所有 catalog 模型（ASR、Aligner、Diarizer），每项显示：名称、大小、已下载状态 badge、Download 按钮（含实时进度条）、Delete 按钮
- `app.py`：新增 `delete_model(model_id)` 方法，删除本地模型目录
- `onModelDownloadProgress` 同时更新 setup panel 和 model modal

### P2 — 实时录音时间戳

- `realtime.py`：`RealtimeTranscriber` 跟踪 `_total_samples` + `_segment_start_samples`
- `_flush_speech()` 入队 `(buf, start_sec, end_sec)` tuple，不再只传 buf
- `_transcribe_segment()` 调用 `on_result({text, start, end})`，时间戳为相对录音开始的绝对秒数
- `onRealtimeResult` JS 处理同时支持 string（向后兼容）和 dict

### P3 — Finish 后只跑 Diarization

- `RealtimeTranscriber` 累积 `self._segments`，`get_segments()` 返回副本
- `stop_realtime()` worker drain 完成后收集 segments，存入 `API._rt_segments[wav_path]`
- `transcribe()` 检测到预存 segments 时调用 `pipeline.run_realtime_segments()` 代替完整 ASR pipeline
- `pipeline.run_realtime_segments()` 只跑 diarization，按时间重叠分配 speaker，输出与完整 pipeline 相同格式的 JSON + MD

---

## 变更文件范围

| 文件 | 变更类型 |
|------|---------|
| `src/realtime.py` | 时间戳跟踪 + segments 累积 |
| `src/pipeline.py` | `run_realtime_segments()` 新增 |
| `app.py` | `delete_model()` + `stop_realtime()` segments 收集 + `transcribe()` 路径分支 |
| `ui/js/app.js` | model modal + `onRealtimeResult` dict 支持 |
| `ui/index.html` | model modal 结构 |
| `ui/css/main.css` | model modal 样式 |
| `tests/` | +24 新测试（realtime timestamps、diarize-only pipeline、model delete） |

---

## 验证命令与结果

```
cd /Users/feifei/programing/audioassist/audioassist-builder
/Users/feifei/programing/local\ asr/.venv/bin/python -m pytest -q
# 结果：318 passed
```

---

## 未完成 / 遗留项

- Playwright 测试未覆盖 model modal 交互 — P3
- `run_realtime_segments()` diarization 对齐精度可进一步优化（词级 vs 段级）— P3
- README 未更新 r02-b3 新功能 — 待 gate 后更新

---

## Gate 候选 SHA

`44d3830`
