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
- [ ] c06 No-Go 修复：aligner 自动下载链路补全 + local_path() _has_key_files 一致性 + README 模型自动下载说明 + select_file 改用 FileDialog.OPEN（OPEN_DIALOG 已废弃）

## P2（r02 计划）

- [ ] 首次启动引导：App 启动时检测 ASR + Diarizer 是否已下载，未下载时显示引导页（必选：下载 ASR 模型、Diarizer 模型；可选：配置 LLM API），完成后进入主界面；主转写流程不再触发下载
- [ ] 转写取消：转写进行中显示取消按钮，chunk 间检查 cancel flag 中止，推送 onTranscribeCancel 事件
- [ ] 转写失败重试：onTranscribeError 时显示重试入口，记住上次文件路径和参数，点击直接重新发起 transcribe()
- [ ] 实时转写说话人回填
- [ ] 实时转写音频保存（整场录音写入 output/ 与转写结果同名，支持回听）
- [ ] 模型管理 UI（下载进度、选择、删除）

## Backlog

- [ ] 端到端冒烟测试：用真实短音频（5-10秒）跑完整转写链路，不放 CI，每个 r 版本发布前手跑
- [ ] Qwen3-ASR MPS 修复（等待上游 PyTorch）
- [ ] UNKNOWN speaker 后处理合并
- [ ] 打包 DMG / EXE
- [ ] 输出标点优化
- [ ] ModelManager 落地：download() 已实现但无 UI 入口，需配合模型管理 UI 真正启用 App 自管目录
- [ ] aligner 下载无提示静默降级字级时间戳 — 补日志或 UI 提示（P3）
- [ ] local_path() HF cache 路径未经 _has_key_files 校验，与 is_downloaded() 不对称（P3）
