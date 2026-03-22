# r02-b2 Development Summary

- Builder 分支：`feat/r01-builder`
- Baseline SHA：`e13e575`（main HEAD）
- Target SHA（Gate Commit）：`57168e1`
- 开发日期：2026-03-22
- 测试结果：294 passed

---

## 变更批次（main..HEAD 全量，含 r02-b1）

| SHA | 内容 |
|-----|------|
| `679fd05` | fix(bootstrap): 移除 DOMContentLoaded fallback |
| `f982925` | feat(r02-b1): 历史管理 + 纪要配置 + 自动转写 |
| `917e76c` | feat(test): Playwright 前端测试 |
| `c75bbe6` | fix: 音频绝对路径 + WAV 历史 |
| `4eaefd0` | fix(nogo): Escape 重命名 + WAV 删除 |
| `84afc19` | fix: templates.json 损坏自动重置 |
| `9ca9d89` | fix: 纪要版本空白 + 语言匹配 + 孤立 WAV |
| `fb69752` | feat(r02-b2): P1-P4 全量实现（见下） |
| `57168e1` | fix(review): 拖拽互斥守卫 + README 更新 |

---

## r02-b2 核心变更（`fb69752`）

### P1 — pyannote community-1 无 token 化
- `src/model_manager.py`：`repo_id` 改为 `pyannote-community/speaker-diarization-community-1`
- `_has_key_files()` diarizer 分支：单检 `config.yaml` → 5 文件全检（config.yaml、embedding/pytorch_model.bin、plda/plda.npz、plda/xvec_transform.npz、segmentation/pytorch_model.bin）
- `size_gb` 从 0.5 更正为 0.034

### P2 — 首次启动引导页
- `app.py`：新增 `get_setup_status()` 检查 ASR + Diarizer 是否已下载
- `ui/index.html`：新增 `#setup-panel`，内嵌在中间栏，显示两个模型下载状态 + 进度条 + 下载按钮
- `ui/js/app.js`：`_init()` 先调 `_checkSetup()`，两模型就绪才进入主流程；`onModelDownloadProgress` 驱动进度条，完成后自动重检

### P3 — 转写取消
- `app.py`：`_cancel_flags` dict + `_TranscriptionCancelled` 异常；`cancel_transcription(job_id)` API
- progress callback 每 chunk 检查 cancel flag
- 取消后推送 `onTranscribeCancel` 事件，UI 回 Idle

### P4 — 转写失败重试
- `onTranscribeError` 改为将 session 置 `status:'error'`（不再 alert+删除）
- `#error-panel` 显示文件名 + 错误信息 + 重试按钮
- 重试：用 `session.audioPath` 重新发起 `transcribe()`

### No-Go 修复（`57168e1`）
- `_startTranscription()` 入口加 `_activeRealtimeId !== null` 互斥检查（与 `_onUpload()` 一致）
- README `## Summary panel` 段落更新：移除旧 ‹/› strip 描述，改为 toolbar 按钮说明

---

## 变更文件范围

| 文件 | 变更类型 |
|------|---------|
| `src/model_manager.py` | repo_id + _has_key_files + size_gb |
| `app.py` | get_setup_status + cancel_transcription + onTranscribeError 改造 |
| `ui/js/app.js` | setup 检查 + 取消 + 重试 + 拖拽守卫 |
| `ui/index.html` | #setup-panel 结构 |
| `ui/css/main.css` | setup panel + error panel 样式 |
| `README.md` | Summary panel 段落更新 |
| `tests/test_app_cancel.py` | 转写取消测试（新增） |
| `tests/test_app_setup.py` | get_setup_status 测试（新增） |
| `tests/test_model_manager.py` | _has_key_files 5 文件校验测试 |

---

## 验证命令与结果

```
cd /Users/feifei/programing/audioassist/audioassist-builder
/Users/feifei/programing/local\ asr/.venv/bin/python -m pytest -q
# 结果：294 passed
```

---

## 未完成 / 遗留项

- 模型管理 UI（下载进度可视化、已下载列表、删除）— r02-b3
- Playwright 测试覆盖 setup-panel 和 cancel/retry 流程 — P3
- drag-and-drop 完整测试 — P3

---

## Gate 候选 SHA

`57168e1`
