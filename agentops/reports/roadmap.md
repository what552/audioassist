# AudioAssist Development Roadmap

确认日期：2026-03-22

## r02-b2（进行中）— 首次使用门槛
- pyannote community-1 无 token 化
- 首次启动引导页
- 转写取消
- 转写失败重试

## r02-b3 — 模型管理 UI
- 下载进度可视化
- 模型选择
- 模型删除
- 打包 DMG 前的硬性条件

## r02-b4 — Summary Agent
- 本地 Agent 升级纪要生成（OpenAI Agents SDK）
- 参考 PRD：agentops/reports/summary-agent-prd.md（researcher 分支）

## r02-b5 — ScreenCaptureKit 系统音频录制
- macOS native helper（Swift）捕获 Zoom / 腾讯会议系统音频
- 支持：系统音频 / 麦克风 / 混合三种模式
- 参考 PRD：agentops/reports/screencapturekit-quickrecorder-integration-prd.md（researcher 分支）

## r03 — Service Mode + OpenClaw 集成
- HTTP Resource API（会议列表、转写、纪要）
- HTTP Agent API（搜索、Q&A、纪要重写）
- 参考 PRD：agentops/reports/audioassist-service-prd.md（researcher 分支）
