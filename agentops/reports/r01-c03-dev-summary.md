# r01-c03 Dev Summary

**Branch:** feat/r01-builder
**Target SHA:** 0a33ce2
**Date:** 2026-03-21

---

## Commits

| SHA | Message |
|-----|---------|
| `b238aee` | feat(r01-c03): diarization model management + community-1 parallel support |
| `0a33ce2` | feat(r01-c03): ModelManager HF cache compatibility |

---

## 变更文件范围

8 files changed, 562 insertions(+), 33 deletions(-)

### 修改 — src/model_manager.py

| 变更 | 说明 |
|------|------|
| `ModelInfo.requires_token: bool` | 新字段（默认 `False`）；标记需要 HF token 的 gated 模型 |
| CATALOG 新增 diarizer 条目 | `pyannote-diarization-community-1`（无需 token，`recommended=True`）和 `pyannote-diarization-3.1`（`requires_token=True`） |
| `list_models()` | 输出中新增 `requires_token` 字段 |
| `_app_path(model_id)` | 原 `local_path` 重命名，专指 App 目录 `{models_dir}/{model_id}/` |
| `_hf_cache_path(model_id)` | 新增。读取 HF hub cache 的 `refs/main` 定位最新 snapshot；不存在返回 `None` |
| `local_path(model_id)` | 优先返回 App 目录（已有内容）→ fallback HF cache → App 目录（新下载默认） |
| `is_downloaded(model_id)` | App 目录 OR HF cache 任一存在即返回 `True` |
| `download()` | 已在 HF cache 中直接跳过网络下载；新下载始终写入 App 目录；`delete()` 只删 App 目录 |
| `select_diarizer_model()` / `get_selected_diarizer()` | 新增；与 `select_asr_model` / `get_selected_asr` 对称 |
| `delete()` | 新增清理 `diarizer_model` config key |

### 修改 — src/diarize.py

| 变更 | 说明 |
|------|------|
| `ModelManager` import | 移到模块顶层（原在 `load()` 内延迟导入） |
| `DiarizationEngine.__init__` | 新增 `model_id` 参数（默认 `'pyannote-diarization-community-1'`）；移除 `hf_endpoint` 参数；`hf_token` 保留用于 3.1 向后兼容 |
| `DiarizationEngine.load()` | 加 already-loaded 短路守卫；从 `ModelManager.local_path()` 取本地路径，用 `Pipeline.from_pretrained(local_path)` 加载（不传 token）；仅在 `model.requires_token=True` 且无 token 时抛 `ValueError` |

### 修改 — src/pipeline.py

| 变更 | 说明 |
|------|------|
| `run()` | 新增 `diarizer_model_id: Optional[str] = None` 参数，透传给 `DiarizationEngine(model_id=...)` |

### 新增 — tests/test_diarize.py

| 分组 | 用例数 | 覆盖 |
|------|--------|------|
| `TestDefaultModel` | 3 | 默认 model_id、`None` 转默认、显式指定 |
| `TestTokenHandling` | 4 | community-1 无 token 加载成功；3.1 无 token 抛 ValueError；3.1 有 token 加载成功；`HF_TOKEN` 环境变量读取 |
| `TestLocalPathLoading` | 2 | `from_pretrained` 收到本地路径（非 Hub ID）；double-load 守卫 |

### 修改 — tests/test_model_manager.py

| 变更 | 说明 |
|------|------|
| `isolated_data_dir` fixture | 新增 `_hf_cache_path` stub（返回 `None`），隔离开发机真实 HF cache |
| `_REAL_HF_CACHE_PATH` | 模块顶层保存真实方法引用，供 HF cache 专项测试恢复 |
| `TestListModels` | `test_each_entry_has_required_fields` 增加 `requires_token` 字段检查 |
| `TestDiarizerCatalog` | 6 个新用例：catalog 条目存在、requires_token 值、recommended 标记、role 字段 |
| `TestSelectAndGetSelectedDiarizer` | 7 个新用例：select 写 config、错误 role 报错、未下载报错、get_selected 读 config / auto-select / 返回 None / delete 清 config |
| `TestHFCacheDetection` | 7 个新用例：snapshot 检测、无 cache 返回 None、未知模型返回 None、`is_downloaded` via cache、`local_path` fallback/优先级、download 跳过 |

### 修改 — tests/test_pipeline.py

| 新增用例 | 覆盖 |
|---------|------|
| `test_diarizer_model_id_passed_to_engine` | `diarizer_model_id` 透传到 `DiarizationEngine` |
| `test_default_diarizer_model_id_is_none` | 未传时为 `None`（由 `DiarizationEngine` 使用默认值） |
| `test_hf_token_passed_to_diarizer` | `hf_token` 透传 |

---

## pytest 验证结果

```
$ python -m pytest tests/ -q
118 passed in 0.16s
```

---

## 未完成项

c03 范围内全部完成。以下为 c04+ 待做项：

- `app.py`：`summarize`、`save_api_config`、`get_api_config`、`save_summary_templates`、`get_summary_templates` 实现（c04）
- `app.py`：`start_realtime`、`stop_realtime` 实现（c05）
- `src/summary.py`：LLM 摘要模块（c04）
- `src/realtime.py`：Silero VAD + sounddevice 实时转写（c05）
- `model_manager.py`：`snapshot_download` 细粒度进度（目前仅 0% 和 100%）
- `app.js`：转写进行中拖入新文件无中止机制（需 `cancel_transcribe` 接口）
- JS 单元测试（Jest）：`player.js`、`transcript.js`、`app.js` 待补
- 集成测试（需真实模型，留后续 cycle 联调时补充）
