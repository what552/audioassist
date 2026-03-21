# r01-b4 Gate Summary

- 目标分支：`feat/r01-builder`
- Baseline SHA：`679d2a3`（r01-b3 合并点）
- Target SHA（Gate Commit）：`0237632`
- 覆盖批次：c06
- 评审日期：2026-03-21

---

## 覆盖范围

| 批次 | SHA | 内容 |
|------|-----|------|
| c06 | `9f9227e` → `0237632` | 模型自愈（is_downloaded 完整性校验 + 自动下载）+ community-1 默认 diarizer + realtime WAV 保存 + stop_realtime race condition 修复 |

---

## Reviewer-1 结论：Go ✅

- 全量测试：198/198 通过

**确认项：**
- aligner 自动下载链路完整（失败降级，不 fatal）✅
- local_path() HF cache 分支加 _has_key_files() 校验 ✅
- README v0.5 + 首次运行自动下载说明 + WAV 保存说明 ✅
- realtime 录音保存到 output/{session_id}.wav ✅
- select_file() 改为 webview.FileDialog.OPEN ✅

**P3 遗留：** aligner except 缺 exc_info=True，stack trace 丢失，不阻塞合并

---

## Reviewer-2 结论：Go ✅

- 全量测试：198/198 通过

**确认项：**
- aligner 自动下载 + 失败降级 ✅
- local_path() _has_key_files 语义一致 ✅
- README v0.5 更新 ✅
- realtime WAV 保存 ✅
- select_file() FileDialog.OPEN ✅

**补充：** README Known fixes 段落仍保留旧 OPEN_DIALOG 表述，不影响运行，P3 遗留

---

## P1/P2 处理汇总

| 批次 | 级别 | 问题 | 状态 |
|------|------|------|------|
| c06 | P1 | aligner 自动下载链路缺失 | ✅ 已修复 |
| c06 | P1 | local_path() HF cache 未经 _has_key_files 校验 | ✅ 已修复 |
| c06 | P1 | README 未更新模型自动下载说明 | ✅ 已修复 |

---

## 遗留项

- aligner except 缺 exc_info=True — P3，backlog
- README Known fixes 段落旧表述 — P3，下轮清理

---

## Gate 决定：通过 ✅

Builder 分支 `feat/r01-builder` @ `0237632` 覆盖 c06，允许合并到 `main`。
已合并：`main`
