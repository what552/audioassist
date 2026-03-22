# TODO

## P0（r01 阻塞）

- [x] 代码迁移：评估 local asr/src/ 各模块适配性
- [x] PyWebView evaluate_js 线程安全方案确认
- [x] Silero VAD Python 接入方案

## P1（r01 本轮）

- [x] 文件转写 + 点击播放联调
- [x] 行内编辑 + 保存
- [x] 摘要面板 + LLM API 配置
- [x] 实时转写基础框架
- [x] ModelManager.is_downloaded() 完整性校验
- [x] pipeline.py 接入 ModelManager 取 ASR 本地路径
- [x] DEFAULT_DIARIZER_MODEL 切回 community-1（自动下载修复后）
- [x] c06 No-Go 修复：aligner 自动下载链路补全 + local_path() _has_key_files 一致性 + README 模型自动下载说明 + select_file 改用 FileDialog.OPEN（OPEN_DIALOG 已废弃）
- [x] realtime.py whisper 引擎导入名称错误：WhisperEngine 应为 WhisperASREngine（r02-a 修复）
- [x] 纪要生成完成后 "Generating..." loading 状态未清除：onSummaryComplete 触发后 UI 仍显示转圈（r02-a 修复）

## P2（r02-a 已完成）

### UI 重设计（三列布局）✅
- [x] 三列布局重构：左列历史侧栏 + 中列转写卡片 + 右列纪要卡片（可隐藏，收起时中列自动拉宽）
- [x] 左列历史侧栏：扫描 output/*.json 列出历史记录（名称/日期/时长），新增转写时写入原始文件名到 json metadata；底部固定"上传文件"和"开始录音"两个入口按钮
- [x] 中列转写区域扩大：顶部音频播放键 + 进度条，主体按时间行展示转写内容
- [x] 右列纪要卡片：顶部设置按钮，纪要内容区，保留最近 3 个版本供切换（{job_id}_summary.json）
- [x] Session 状态机重构：_sessions Map + _render() 单一入口，6 状态互斥
- [x] Realtime 控制栏：计时器 + pause/resume + Finish 按钮
- [x] Realtime WAV 路径传递给 JS（session.audioPath），pause 时可回听

## P2（r02-b 计划）

### UI 体验修复（r02-b1，用户测试反馈）
- [ ] Session 管理：历史侧栏每条 session 增加重命名和删除操作（hover 显示图标，点击重命名可行内编辑，删除需确认）
- [ ] 切换 session 时停止当前播放：_onHistorySelect 时调用 Player.stop()，避免切换后音频继续播放
- [ ] 纪要配置入口移至 header 右侧：将 API 配置 + 模板选择合并为"纪要配置"按钮，放在顶部 header 右侧，移除右栏顶部现有的设置入口
- [ ] 纪要展开/收起按钮移至 header 最右侧：替换现有的中间长条 toggle，改为 header 右上角小按钮
- [ ] 实时录音结束后自动全量转写：Finish 后对保存的 WAV 自动跑完整 pipeline（ASR + 说话人分离 + merge），结果同文件上传转写一致（有时间轴 + speaker），保存 JSON，历史侧栏显示

### 功能
- [ ] 首次启动引导：App 启动时检测 ASR + Diarizer 是否已下载，未下载时显示引导页（必选：下载 ASR 模型、Diarizer 模型；可选：配置 LLM API），完成后进入主界面；主转写流程不再触发下载
- [ ] 转写取消：转写进行中显示取消按钮，chunk 间检查 cancel flag 中止，推送 onTranscribeCancel 事件
- [ ] 转写失败重试：onTranscribeError 时显示重试入口，记住上次文件路径和参数，点击直接重新发起 transcribe()
- [ ] 模型管理 UI（下载进度、选择、删除）
- [ ] pyannote-community-1 repo_id 切换为 pyannote-community/speaker-diarization-community-1（无 HF token，CC-BY-4.0，~33MB）

## Backlog

- [ ] 端到端冒烟测试：用真实短音频（5-10秒）跑完整转写链路，不放 CI，每个 r 版本发布前手跑
- [ ] Qwen3-ASR MPS 修复（等待上游 PyTorch）
- [ ] UNKNOWN speaker 后处理合并
- [ ] 打包 DMG / EXE
- [ ] 输出标点优化
- [ ] ModelManager 落地：download() 已实现但无 UI 入口，需配合模型管理 UI 真正启用 App 自管目录
- [ ] aligner 下载无提示静默降级字级时间戳 — 补日志或 UI 提示（P3）
- [ ] local_path() HF cache 路径未经 _has_key_files 校验，与 is_downloaded() 不对称（P3）
