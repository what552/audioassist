# TODO

## P0（r01 阻塞）

- [ ] 代码迁移：评估 local asr/src/ 各模块适配性
- [ ] PyWebView evaluate_js 线程安全方案确认
- [ ] Silero VAD Python 接入方案

## P1（r01 本轮）

- [ ] 文件转写 + 点击播放联调
- [ ] 行内编辑 + 保存
- [ ] 摘要面板 + LLM API 配置
- [ ] 实时转写基础框架

## P2（r02 计划）

- [ ] 实时转写说话人回填
- [ ] 模型管理 UI（下载进度、选择）
- [ ] 首次启动模型引导下载
- [ ] 模型管理 UI：下载/删除/进度显示，用户通过 App 下载模型存到 App 目录（~/Library/Application Support/TranscribeApp/models/），而不依赖 HF cache
- [ ] 首次启动引导：检测模型是否已下载，未下载时引导用户在 App 内完成下载

## Backlog

- [ ] Qwen3-ASR MPS 修复（等待上游 PyTorch）
- [ ] UNKNOWN speaker 后处理合并
- [ ] 打包 DMG / EXE
- [ ] 输出标点优化
- [ ] ModelManager 落地：目前 download() 已实现但无 UI 入口，模型全在 HF cache，需配合模型管理 UI 真正启用 App 自管目录
