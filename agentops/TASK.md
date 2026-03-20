# TASK

## 当前轮次：r01

### 里程碑目标

**文件转写 + 基础 UI 可用版本**

用户能够：
1. 打开 app，拖入音频/视频文件
2. 看到转写进度，完成后显示带说话人 + 时间戳的转写结果
3. 点击任意一句 → 音频跳转到对应时间戳播放
4. 双击文本 → 行内编辑，保存修改
5. 打开摘要面板，配置 LLM API，选择/创建模板，生成摘要

实时转写在 r01 开始搭框架（VAD + 麦克风录音），但不要求完整可用。

---

## r01 任务拆分

### Builder 任务（feat/r01-builder）

**c01：代码迁移 + 项目骨架**
- 从 `/Users/feifei/programing/local asr/src/` 迁移以下模块到 `src/`：
  - `asr.py`、`asr_whisper.py`、`diarize.py`、`merge.py`
  - `pipeline.py`、`model_manager.py`、`audio_utils.py`
- 迁移时评估各模块，去除探索阶段的冗余代码和调试路径
- 创建 `app.py`（PyWebView 入口 + API 骨架）
- 创建 `run.py`（启动入口）
- 创建 `requirements.txt`、`.python-version`
- 创建 `ui/index.html`、`ui/css/main.css`、`ui/js/app.js`（空壳）

**c02：核心 UI —— 转写视图 + 音频播放器**
- `ui/js/player.js`：HTML5 audio，`seekTo(seconds)` 接口
- `ui/js/transcript.js`：SpeakerBlock 列表渲染、点击跳转、双击编辑、保存
- `app.py` 实现：`select_file`、`transcribe`、`save_transcript`
- 转写进度通过 `evaluate_js("onTranscribeProgress(...)")` 推送给 UI
- 联调：文件拖入 → 转写 → 渲染 → 点击播放

**c03：摘要面板**
- `src/summary.py`：封装 OpenAI 兼容接口调用（支持 base_url + api_key + model）
- `ui/js/summary.js`：API 配置表单 + 模板增删改 + 摘要生成展示
- `app.py` 实现：`summarize`、`save_api_config`、`get_api_config`、`save_summary_templates`、`get_summary_templates`
- 配置持久化到 `~/.local/share/TranscribeApp/config.json` 和 `templates.json`

**c04：实时转写框架（r01 阶段目标：可录音 + 出字幕，说话人回填可选）**
- `src/realtime.py`：Silero VAD + sounddevice + ASR，逐句推送
- `ui/js/realtime.js`：开始/停止按钮，逐句追加显示
- `app.py` 实现：`start_realtime`、`stop_realtime`

---

### Researcher 任务（research/r01-researcher）

配合 c01 迁移前，先做代码审查分析：
- 分析 `local asr/src/` 各模块，标出可直接用、需清理、需重构的部分
- 评估 PyWebView 的 Python ↔ JS 通信模式，给出线程安全建议
- 调研 Silero VAD Python 接入方案（sounddevice 配合）
- 输出 `research/r01-c01-analysis.md`

---

### Reviewer 任务

- **Reviewer-1**：每个 cNN checkpoint 后执行工程质量评审（import 链、lint、边界处理）
- **Reviewer-2**：每个 cNN checkpoint 后执行交付质量评审（README、配置文档、跨平台兼容性）

---

## 任务状态

- [ ] Researcher: r01 代码分析
- [ ] Builder c01: 代码迁移 + 项目骨架
- [ ] Builder c02: 转写视图 + 音频播放器
- [ ] Builder c03: 摘要面板
- [ ] Builder c04: 实时转写框架
- [ ] Reviewer-1/2: r01-b1 评审
- [ ] Gate: r01 通过
- [ ] 合并到 main

---

## 后续轮次预览

- **r02**：实时转写完善（说话人回填、字幕 overlay）+ 模型管理 UI + 首次启动引导
- **r03**：打包 DMG / EXE + 安装包测试
