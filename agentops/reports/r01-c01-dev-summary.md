# r01-c01 Dev Summary

**Branch:** feat/r01-builder
**Target SHA:** 6a712bc
**Date:** 2026-03-21

---

## Commits

| SHA | Message |
|-----|---------|
| `edc43c2` | feat(r01-c01): project skeleton + migrate/clean all src modules |
| `6a712bc` | feat(r01-c01): add unit tests for src modules |

---

## 变更文件范围

### 新增 — src/

| 文件 | 来源 | 操作 |
|------|------|------|
| `src/__init__.py` | — | 新建（空） |
| `src/types.py` | — | 新建：提取共享类型 `WordSegment`、`TranscriptResult` |
| `src/diarize.py` | `local asr/src/diarize.py` | 直接迁移，无修改 |
| `src/merge.py` | `local asr/src/merge.py` | import 改为 `.types` |
| `src/asr.py` | `local asr/src/asr.py` | 清理硬编码模型路径；`model_path`/`aligner_path` 参数化；类型定义移至 `.types` |
| `src/asr_whisper.py` | `local asr/src/asr_whisper.py` | 去除重复 `WordSegment`/`TranscriptResult` 定义；从 `.types` 导入 |
| `src/model_manager.py` | `local asr/src/model_manager.py` | 存储目录 `local-asr` → `TranscribeApp`；`progress_callback` 签名改为 `Callable[[float, str], None]` |
| `src/audio_utils.py` | `local asr/src/audio_utils.py` | `to_wav` 增加 WAV 采样率/声道检查；`get_duration` 增加 ffprobe returncode 校验；`SUPPORTED_NATIVE` 集合移除（改为按需检查） |
| `src/pipeline.py` | `local asr/src/pipeline.py` | 重构：加 `engine`（qwen/whisper）、`job_id`（UUID 命名输出）、`progress_callback(float, str)` 参数 |

### 新增 — 项目骨架

| 文件 | 说明 |
|------|------|
| `app.py` | PyWebView API 骨架：所有方法 stub，`transcribe`/`download_model` 线程模型完整 |
| `run.py` | 启动入口，`webview.create_window` + `_window` 引用注入 |
| `requirements.txt` | 核心依赖声明（pywebview、qwen-asr、pyannote.audio、huggingface-hub 等） |
| `.python-version` | 3.12.2 |
| `ui/index.html` | 空壳 HTML |
| `ui/css/main.css` | 基础样式重置 + 暗色背景 |
| `ui/js/app.js` | PyWebView bridge helper + progress event handler 骨架 |

### 新增 — tests/

| 文件 | 测试数 | 关键覆盖 |
|------|--------|---------|
| `tests/test_types.py` | 6 | 字段访问、相等性、`words` 默认列表实例隔离 |
| `tests/test_audio_utils.py` | 11 | `_wav_needs_conversion`：5 种采样率/声道/异常情况；`to_wav`：跳过/触发/失败；`get_duration`：正常返回和 returncode 失败 |
| `tests/test_merge.py` | 20 | `merge()`：说话人分配、文本拼接、时间戳、无词汇 fallback、连续同说话人合并；`to_json`/`to_markdown` 结构校验；`_fmt_time` 边界 |
| `tests/test_pipeline.py` | 11 | job_id 自动生成/自定义/唯一性；progress_callback float+str 合约、0.0→1.0 范围、None 安全；qwen/whisper 引擎选择；临时文件清理 |

---

## pytest 验证结果

```
$ python -m pytest tests/ -v
48 passed in 0.03s
```

全部 48 项通过，无警告，无依赖真实模型（全部 mock subprocess 和 ASR/diarize）。

### 完整测试列表

```
tests/test_audio_utils.py::TestWavNeedsConversion::test_already_16k_mono PASSED
tests/test_audio_utils.py::TestWavNeedsConversion::test_wrong_sample_rate PASSED
tests/test_audio_utils.py::TestWavNeedsConversion::test_wrong_channels PASSED
tests/test_audio_utils.py::TestWavNeedsConversion::test_ffprobe_failure PASSED
tests/test_audio_utils.py::TestWavNeedsConversion::test_malformed_output PASSED
tests/test_audio_utils.py::TestToWav::test_wav_already_correct_skips_conversion PASSED
tests/test_audio_utils.py::TestToWav::test_wav_wrong_rate_triggers_conversion PASSED
tests/test_audio_utils.py::TestToWav::test_mp3_triggers_conversion PASSED
tests/test_audio_utils.py::TestToWav::test_ffmpeg_failure_raises PASSED
tests/test_audio_utils.py::TestGetDuration::test_returns_float PASSED
tests/test_audio_utils.py::TestGetDuration::test_ffprobe_failure_raises PASSED
tests/test_merge.py::TestGetSpeakerAt::test_finds_correct_speaker PASSED
tests/test_merge.py::TestGetSpeakerAt::test_unknown_when_no_match PASSED
tests/test_merge.py::TestGetSpeakerAt::test_empty_segments PASSED
tests/test_merge.py::TestGetSpeakerAt::test_boundary_inclusive PASSED
tests/test_merge.py::TestMerge::test_splits_by_speaker PASSED
tests/test_merge.py::TestMerge::test_block_text PASSED
tests/test_merge.py::TestMerge::test_block_timestamps PASSED
tests/test_merge.py::TestMerge::test_block_words PASSED
tests/test_merge.py::TestMerge::test_no_words_falls_back_to_single_block PASSED
tests/test_merge.py::TestMerge::test_single_speaker_all_unknown PASSED
tests/test_merge.py::TestMerge::test_consecutive_same_speaker_merged PASSED
tests/test_merge.py::TestToJson::test_output_structure PASSED
tests/test_merge.py::TestToJson::test_segment_fields PASSED
tests/test_merge.py::TestToJson::test_timestamps_rounded_to_3dp PASSED
tests/test_merge.py::TestToMarkdown::test_contains_filename PASSED
tests/test_merge.py::TestToMarkdown::test_contains_speakers PASSED
tests/test_merge.py::TestToMarkdown::test_contains_text PASSED
tests/test_merge.py::TestFmtTime::test_seconds_only PASSED
tests/test_merge.py::TestFmtTime::test_minutes_and_seconds PASSED
tests/test_merge.py::TestFmtTime::test_hours PASSED
tests/test_merge.py::TestFmtTime::test_zero PASSED
tests/test_pipeline.py::TestJobId::test_auto_generates_job_id PASSED
tests/test_pipeline.py::TestJobId::test_uses_provided_job_id PASSED
tests/test_pipeline.py::TestJobId::test_two_runs_produce_different_ids PASSED
tests/test_pipeline.py::TestProgressCallback::test_callback_called_multiple_times PASSED
tests/test_pipeline.py::TestProgressCallback::test_callback_receives_float_and_str PASSED
tests/test_pipeline.py::TestProgressCallback::test_callback_starts_at_zero_ends_at_one PASSED
tests/test_pipeline.py::TestProgressCallback::test_no_callback_still_runs PASSED
tests/test_pipeline.py::TestEngineSelection::test_qwen_engine_uses_asr_engine PASSED
tests/test_pipeline.py::TestEngineSelection::test_whisper_engine_uses_whisper_asr_engine PASSED
tests/test_pipeline.py::TestTempFileCleanup::test_temp_wav_cleaned_up PASSED
tests/test_types.py::TestWordSegment::test_fields PASSED
tests/test_types.py::TestWordSegment::test_equality PASSED
tests/test_types.py::TestWordSegment::test_inequality PASSED
tests/test_types.py::TestTranscriptResult::test_defaults PASSED
tests/test_types.py::TestTranscriptResult::test_words_not_shared PASSED
tests/test_types.py::TestTranscriptResult::test_with_words PASSED
```

---

## 未完成项

c01 范围内全部完成。以下为 c02+ 待做项：

- `app.py`：`select_file`、`transcribe`、`save_transcript` 完整实现（c02）
- `app.py`：`summarize`、`save_api_config`、`get_api_config`、`save_summary_templates`、`get_summary_templates` 实现（c03）
- `app.py`：`start_realtime`、`stop_realtime` 实现（c04）
- `src/summary.py`：LLM 摘要模块（c03）
- `src/realtime.py`：Silero VAD + sounddevice 实时转写（c04）
- `ui/`：所有 JS 功能模块（c02–c04）
- `model_manager.py`：`snapshot_download` 细粒度进度（目前仅 0% 和 100%）
- 集成测试（需真实模型，留 c02 联调时补充）
