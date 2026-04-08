# AudioAssist

[中文](README.md) | [English](README.en.md)

AudioAssist 是一个面向 AI Agent 工作流的本地转写应用。

和传统语音转写产品不同，AudioAssist 不只是把音频转成一篇保存在云端的文档，而是把转写结果沉淀为用户本地可管理、可检索、可继续加工的 Markdown 与结构化资产。这样，Claude Code、Codex 或其他 Agent 就可以直接读取转写内容，结合背景资料、项目文档和历史记录，生成更准确的会议纪要、行动项和长期记忆。

## 为什么做这个产品

今天很多语音转写软件仍然停留在“上传音频 -> 云端转写 -> 得到一篇文档”的模式。这类方案的问题是：

- 转写结果停留在平台云端，难以进入用户自己的 AI 工作流
- 很难和 Claude Code、Codex 等 Agent 直接协作
- 对人名、项目名、术语和上下文的理解不稳定
- 很难结合用户已有资料持续优化，无法形成长期记忆
- 输出结构通常固定，不够贴近不同用户的记录习惯和关注重点

AudioAssist 的目标，是把“语音转写”升级成“Agent 可用的个人知识入口”。

## 当前能力

- 本地音频 / 视频转写
- 说话人分离
- 实时录音
- 手动高精度转写
- 系统音频 / 麦克风录制
- 摘要与纪要生成
- Markdown / 文本导出
- 转写结果本地保存，便于后续 Agent 处理
- 直接同步到 Obsidian Vault，进入 Claude Code + Obsidian 工作流

## 平台支持

- macOS: 支持本地转写、麦克风录音、系统音频录制
- Windows: 支持本地转写和麦克风录音
- Windows 上的本地系统音频录制当前仍未完成

## 性能建议

- Qwen3-ASR 相比 Whisper 更吃性能，更适合 Apple Silicon 高内存机型或带独立显卡的 PC
- macOS 建议使用 Apple Silicon 芯片；16GB 统一内存可用，32GB 及以上在长音频和高精度场景下更稳
- Windows/Linux 如果使用本地 Qwen3-ASR，建议配备 NVIDIA GPU；8GB 显存可作为起点，12GB 及以上体验更稳定
- 8GB 内存设备仍可运行应用，但更建议优先使用 Whisper，或后续接入云端 ASR API

## 适合谁

- 频繁开会、需要高质量纪要的人
- 希望把转写结果交给 AI Agent 深加工的用户
- 重视本地数据控制的人
- 有长期知识管理需求的个人和小团队

## 下载

- macOS DMG: [Download Latest Release](https://github.com/what552/audioassist/releases/latest)
- Release Notes: [GitHub Releases](https://github.com/what552/audioassist/releases)

## Roadmap

- 补齐 Windows 本地系统音频录制能力
- 接入合适的云端 ASR API，为本地性能不足的设备提供替代方案
- 改进 mix 模式下的系统音频回声消除
- 提供更稳定的面向普通用户的系统发行版
- 与 Obsidian 更深度整合
- 提供 CLI / HTTP 接口，让 Agent 可以直接调用转写能力，而不必依赖完整 GUI
- 探索本地 + 云端协同形态，让手机、麦克风、录音笔等设备产生的音频进入统一转写工作流，并最终沉淀为可继续被 Agent 使用的 Markdown 资产

## 开发与详细说明

- 详细开发说明与运行文档： [README.dev.md](README.dev.md)
