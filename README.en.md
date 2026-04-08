# AudioAssist

[中文](README.md) | [English](README.en.md)

AudioAssist is a local transcription app built for AI agent workflows.

Unlike traditional transcription tools that upload audio to the cloud and leave the result inside their own platform, AudioAssist turns transcripts into local Markdown and structured assets that can be reused. This makes it easier for Claude Code, Codex, and other agents to read transcripts together with background materials, project documents, and history, and generate more accurate meeting notes, action items, and long-term memory.

## Why AudioAssist

Many transcription products still follow the old pattern: upload audio, get a cloud document, and stop there. That model has several limits:

- transcripts stay inside the vendor's cloud
- they do not fit naturally into personal AI workflows
- names, terms, and project context are often handled poorly
- they do not build long-term memory around the user's own materials
- note structure is usually generic and hard to adapt

AudioAssist is built to make transcription a first-class input for AI agents.

## Current Features

- Local audio / video transcription
- Speaker diarization
- Realtime recording
- Manual high-accuracy transcription
- System audio / microphone capture
- Summary and meeting-note generation
- Markdown / text export
- Local transcript storage for agent workflows
- Direct sync to Obsidian Vault for Claude Code + Obsidian workflows

## Platform Support

- macOS: local transcription, microphone recording, and system audio capture are supported
- Windows: local transcription and microphone recording are supported
- Local system audio capture on Windows is not finished yet

## Who It's For

- People who attend many meetings and need better notes
- Users who want agents to process transcripts further
- Users who prefer local ownership of their data
- Individuals and small teams building long-term knowledge workflows

## Download

- macOS DMG: [Download Latest Release](https://github.com/what552/audioassist/releases/latest)
- Release Notes: [GitHub Releases](https://github.com/what552/audioassist/releases)

## Roadmap

- Complete local system audio capture support on Windows
- Cloud ASR API support for lower-performance devices
- Better echo cancellation in mix mode
- A more stable end-user distribution
- Deeper Obsidian integration
- CLI / HTTP interfaces so agents can call transcription directly without the full GUI
- Explore hybrid local + cloud workflows for audio captured from phones, microphones, and recording devices

## Developer and Detailed Docs

- Detailed developer and runtime notes: [README.dev.md](README.dev.md)
