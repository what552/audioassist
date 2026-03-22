# r02-b1 Development Summary

- Builder 分支：`feat/r01-builder`
- Baseline SHA：`e13e575`（r01 合并点，main HEAD）
- Target SHA（Gate Commit）：`9ca9d89`
- 开发日期：2026-03-22
- 测试结果：279 passed（含前端 Playwright 3 tests）

---

## 变更批次

| SHA | 内容 |
|-----|------|
| `679fd05` | fix(bootstrap): 移除 DOMContentLoaded fallback，仅保留 pywebviewready 监听，修复历史记录首次加载失败 |
| `f982925` | feat(r02-b1): Player.stop on switch + 纪要配置移至 header + 历史 rename/delete + realtime Finish 自动全量转写 |
| `917e76c` | feat(test): Playwright 前端测试框架（mock_api.js + 3 tests） |
| `c75bbe6` | fix: 原始音频复制到 output/ 保障绝对路径播放 + realtime WAV 显示在历史侧栏（_meta.json） |
| `4eaefd0` | fix(r02-b1-nogo): Escape 取消重命名不触发保存 + WAV-only session 删除完整 |
| `84afc19` | fix: templates.json 损坏时自动重置为默认值（app.py 防崩溃） |
| `9ca9d89` | fix(r02-b2): 纪要版本切换内容空白 + 纪要语言与音频不匹配 + 转写后孤立 WAV session |

---

## 主要变更说明

### UI 功能（r02-b1 核心）

- **三栏布局完善**：左栏历史侧栏 + 中栏转写区 + 右栏纪要区
- **历史 session 管理**：hover 显示重命名/删除图标；行内编辑（Enter 保存，Escape 取消，blur 保存）；删除需确认；WAV-only session 完整删除（.wav + _meta.json）
- **切换 session 停止播放**：`_onHistorySelect` 调用 `Player.stop()`
- **纪要配置入口移至 header**：API Key + 模板选择合并为顶部弹窗，移除右栏原设置入口
- **纪要展开/收起**：header 右上角小按钮
- **realtime Finish 自动全量转写**：WAV 保存后自动触发 pipeline，结果写入 JSON，历史侧栏刷新

### Bug 修复

- **pywebviewready 时序**：删除 DOMContentLoaded fallback，彻底修复首次加载历史为空
- **音频播放路径**：transcribe() 时将源文件 `shutil.copy2` 到 `output/{job_id}_audio{ext}`，JSON 存绝对路径
- **realtime WAV 历史**：扫描 `output/*.wav`，读取 `_meta.json` 获取 display_name，跳过已有 JSON 的同名 WAV
- **Escape 重命名**：history.js commit/cancel 均先 removeEventListener('blur') 再操作 DOM
- **纪要版本空白**：summary.js 版本按钮点击时加载对应版本文本更新内容区
- **纪要语言**：app.py `_lang_instruction()` 从 transcript JSON language 字段自动映射，fallback CJK 比例检测
- **孤立 WAV session**：transcribe() 完成后向原始 WAV 的 `_meta.json` 写入 `transcribed_job_id`；get_history() WAV 扫描检测到该字段则跳过

### 测试覆盖

- 新增 Playwright 前端测试：`tests/frontend/`（mock_api.js + test_frontend.py，3 用例）
- 新增单元测试：test_app_history.py、test_app_session.py、test_app_transcribe.py、test_lang_instruction.py
- 全量：**279 passed**

---

## 变更文件范围

| 文件 | 变更类型 |
|------|---------|
| `app.py` | 核心逻辑：历史扫描、音频路径、realtime 自动转写、语言检测、templates 防崩溃 |
| `ui/js/app.js` | bootstrap 修复、session 状态机、纪要配置弹窗 |
| `ui/js/history.js` | rename/delete UI 逻辑 |
| `ui/js/summary.js` | 版本切换、语言显示 |
| `ui/js/player.js` | stop() 暴露 |
| `ui/css/main.css` | 历史 hover 按钮、header 按钮样式 |
| `ui/index.html` | 结构调整（纪要 header 按钮、配置弹窗） |
| `requirements.txt` | 新增 langdetect（语言检测）、playwright（前端测试） |
| `tests/frontend/` | Playwright 前端测试 |
| `tests/test_*.py` | 新增后端单元测试 |

---

## 验证命令与结果

```
cd /Users/feifei/programing/audioassist/audioassist-builder
/Users/feifei/programing/local\ asr/.venv/bin/python -m pytest -q
# 结果：279 passed
```

---

## 未完成 / 遗留项

- drag-and-drop 绕过并发守卫 — P3，backlog
- alert() 弹窗未统一为 toast — P3，backlog
- Playwright 测试未覆盖：modal 弹窗、WAV 历史显示、auto-transcribe、rename/delete 全流程
- resume() 未检查 worker 是否存活 — P3，backlog

---

## Gate 候选 SHA

`9ca9d89`
