# 发布前手跑 Smoke Test Checklist

**分支：** feat/r01-builder
**版本：** v0.8 (r02-b1)
**日期：** 2026-03-22

---

## 场景 1 — 文件上传 + 转写 + 播放

### 操作步骤

1. 启动应用：`python run.py`
2. 左侧历史栏为空时，中间显示空引导语
3. 点击 📁 **Upload File**，选择一个本地音频/视频文件（建议 ≤ 5 分钟）
4. 观察中间面板出现进度条，左侧历史出现"处理中"条目（排列在顶部）
5. 等待转写完成；进度条消失，中间切换为 Player + 逐段文本
6. 点击任意行跳转到对应时间点并播放
7. 点击 **Save** 按钮，确认底部无"unsaved"提示

### 预期结果

- 进度条百分比从 0 → 100
- 转写完成后 Player 可播放，段落与时间戳对应
- Save 操作无报错；`output/<job_id>.json` 和 `.md` 文件均更新

### 已知风险点

- 首次运行需下载 ASR + 对齐模型，进度显示取决于网络速度
- Qwen3-ASR 在 MPS (Apple M 系列) 上强制使用 CPU，首次较慢

---

## 场景 2 — 实时录音 → 自动转写

### 操作步骤

1. 点击 🎙 **Start Recording**（若有转写任务进行中，应被拦截提示）
2. 大声说话约 30 秒，观察 Realtime Panel 逐句出现文字，计时器增加
3. 点击 **Finish**；录音控制栏消失，左侧历史刷新为该 session
4. 等待自动转写管道完成（同场景 1 的进度条）
5. 转写完成后：Player 加载录音 WAV，逐段文字带 speaker 标签
6. 点击播放验证音频与文字对应

### 预期结果

- 实时面板每个语音段落约 0.5 s 后出现一句
- Finish 后 session 无缝切换到 `file/transcribing` 状态
- 最终 `output/<session_id>.wav` 和 `<new_job_id>.json` 均存在

### 已知风险点

- 首次加载 Silero VAD 模型约 2–5 s 延迟
- 若 WAV 路径未正确传递（`onRealtimeStarted` 两参数），Player 静音

---

## 场景 3 — 实时录音暂停 / 继续

### 操作步骤

1. 开始录音（同场景 2 步骤 1–2）
2. 点击控制栏 **⏸ Pause**；计时器停止，按钮变为 ▶（Resume）
3. 点击 **▶ Play**（仅暂停态可用）；Player 应播放已录音频
4. 点击 **▶ Resume**；计时器继续计时（不归零），录音恢复
5. 继续说话，确认新语音仍出现在 Realtime Panel
6. 点击 **Finish** 完成

### 预期结果

- 暂停后计时器静止；Resume 后计时器从暂停处继续
- 暂停期间 Player 播放键可用，Resume 后禁用（直到再次暂停）
- 完整 WAV 包含暂停前后的音频，无跳断

### 已知风险点

- Metal GPU 并发崩溃（已通过 worker queue 修复）；如遇 `A command encoder is already encoding to this command buffer` 说明 r02-b1 patch 未生效

---

## 场景 4 — Summary 生成 + 版本切换

### 操作步骤

1. 转写完成后（文件或实时 → done），工具栏显示 **Summary** 和 **⚙** 按钮
2. 点击 **⚙**，打开 Settings 模态框，填写 Base URL / API Key / Model，Save
3. 点击背景或 ✕ 关闭模态框；再次点击 **⚙** 确认数据已持久化
4. 点击 **Summary** 按钮展开摘要面板
5. 从模板下拉选择一个模板，点击 **Generate**
6. 观察流式文字逐步出现；完成后自动保存为 v1
7. 再次 Generate → v2 按钮出现；切换两个版本确认内容正确

### 预期结果

- 模态框 Save 后数据写入 `config.json`；关闭再打开数据不丢失
- Generate 期间 Generate 按钮禁用
- 最多 3 个版本；第 4 次 Generate 替换最旧版本

### 已知风险点

- 若 LLM 端点不可用，`onSummaryError` 弹出错误信息
- Summary Generating… spinner 在 `onSummaryComplete` 后可能未消失（已知遗留问题）

---

## 场景 5 — 历史列表：重命名 + 删除

### 操作步骤

1. 确保历史列表中有 ≥ 2 条 done 状态记录
2. 悬停在任一条目上，确认右侧出现 ✏ 和 🗑 图标
3. **重命名**：点击 ✏，内联输入框出现；输入新名称按 Enter；确认历史条目和顶部文件名均更新
4. **重命名取消**：点击 ✏，按 Esc；名称应恢复原值
5. **删除**：点击 🗑，确认弹框选 OK；条目从列表消失，中间面板回到 idle 状态
6. 重启应用，确认重命名持久化，已删除条目不再出现

### 预期结果

- 重命名写入 JSON（`filename` 字段）；重启后保持
- 删除移除 `.json` 和 `_summary.json`；History 列表无残留
- 删除后若该 session 为当前选中，中间面板切换到 idle

### 已知风险点

- 删除操作不可撤销；弹框为唯一防线

---

## 场景 6 — 并发互斥 + 切换音频停止

### 操作步骤

1. **上传时拦截录音**：开始 Upload 转写后（进行中），点击 🎙 Start Recording → 应弹出"A transcription is in progress"提示，录音不开始
2. **录音时拦截上传**：开始录音后，点击 📁 Upload File → 应弹出"A recording is in progress"提示，文件选择不触发
3. **切换历史停止播放**：在 file-done 状态点击 Player 播放音频，然后点击另一条历史记录 → 音频立即停止
4. 切换回原记录，确认 Player 重新加载，时间归零

### 预期结果

- 两个互斥方向均有明确弹框提示
- 切换历史调用 `Player.stop()`；新记录的 Player 从头加载

### 已知风险点

- 空格键控制 Player 播放/暂停未实现（已知遗留问题，player.js 未加 keydown 监听）
- `_setView('file-done')` 下 `#empty-hint` 隐藏逻辑待确认
