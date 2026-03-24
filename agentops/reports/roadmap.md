# AudioAssist Development Roadmap

确认日期：2026-03-24

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

## r02-b4-fix — 遗留修复批次（r02-b4 merge 后）
- Session 删除完整清理：delete_session() 补删 {job_id}_audio.* 和 {job_id}.md（researcher PRD）
- 存储路径配置：设置界面可修改音频/转写文件存储路径

## r02-b5 — 转写能力升级
- Runtime local-model-only：推理路径只传本地路径，模型缺失 fail-fast + 引导 setup（researcher design note）
- 长音频断点续传：chunk 级 checkpoint，中断后从上次位置继续（researcher PRD）
- 批量导入 + 顺序转写队列：多文件拖放/选择，排队顺序执行（researcher PRD）
- 一键重新转写：已有 session 直接重跑，无需重新上传（researcher PRD）
- 录音中断确认：文件转写进行中想录音时弹确认框，不再硬拦截（researcher PRD）
- 参考 PRD：runtime-local-model-only-design.md、transcription-checkpoint-resume-prd.md、transcription-queue-retranscribe-and-recording-interrupt-prd.md（researcher 分支）

## r02-b6 — ScreenCaptureKit 系统音频录制
- macOS native helper（Swift）捕获 Zoom / 腾讯会议系统音频
- 支持：系统音频 / 麦克风 / 混合三种模式
- 参考 PRD：agentops/reports/screencapturekit-quickrecorder-integration-prd.md（researcher 分支）

## r03 — Service Mode + OpenClaw 集成
- HTTP Resource API（会议列表、转写、纪要）
- HTTP Agent API（搜索、Q&A、纪要重写）
- 参考 PRD：agentops/reports/audioassist-service-prd.md（researcher 分支）
