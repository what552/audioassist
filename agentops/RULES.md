# Agent Team Branch & Review Rules

> 适用于 `leader + builder + reviewer-1 + reviewer-2 + researcher` 的多 Agent 协作项目。
> 目标：`main` 稳定、职责隔离、轮次可追溯、评审可执行、可跨项目复制。

## 1) 核心原则（必须遵守）

1. `main` 只做集成，不做日常功能开发。
2. Leader 负责任务编排与合并控制；Builder 负责主开发；Reviewer-1 / Reviewer-2 负责评审；Researcher 负责研究与补充分析。
3. 一轮（Round）一个里程碑，先过 Gate 再合并。
4. 合并后保持角色分离，不把所有窗口切回 `main`。
5. 每个开发批次（`rXX-cNN`）完成后，必须先完成对应评审批次（`rXX-bY`）并形成 Gate 结论，再允许进入下一个开发批次（`rXX-cNN+1`）。

## 2) 角色与工作区映射

- **Leader / Orchestrator**
  - worktree：主仓库
  - 分支：`main`
  - 职责：任务编排、汇总报告、控制 Merge Gate、执行合并、执行归档
- **Builder**
  - worktree：`../<repo>-claude`
  - 分支：`feat/rXX-builder`
  - 职责：架构落地、核心实现、测试、更新开发总结
- **Reviewer-1**
  - worktree：`../<repo>-codex`
  - 分支：`review/rXX-reviewer-1`
  - 职责：工程质量评审（build/lint/test/边界/异常）
- **Reviewer-2**
  - worktree：`../<repo>-gemini`
  - 分支：`review/rXX-reviewer-2`
  - 职责：交付质量评审（README/env/部署/一致性）
- **Researcher**
  - worktree：`../<repo>-codex-research`
  - 分支：`research/rXX-researcher`
  - 职责：前瞻研究、设计补充、风险补充、静态分析

## 3) 分支命名规范

- 集成分支：`main`
- Builder 开发分支：`feat/rXX-builder`
- Reviewer-1 评审分支：`review/rXX-reviewer-1`
- Reviewer-2 评审分支：`review/rXX-reviewer-2`
- Researcher 研究分支：`research/rXX-researcher`

说明：`rXX` 必须两位，如 `r01`、`r02`、`r10`。每轮新建，不复用旧分支。

## 4) Round 生命周期

1. **Round Start**
   - 四个子分支均从最新 `main` 创建。
2. **Round Execution**
   - Builder 在 `feat/*` 实现里程碑。
   - Reviewer-1 / Reviewer-2 默认输出评审报告，不直接改核心业务。
   - Researcher 默认输出研究/分析报告，不直接改核心业务。
   - Builder 每完成一批（`cNN`）即冻结评审基线，先评审后继续开发下一批。
3. **Round Merge**
   - 仅在 Gate 通过后，将 Builder 分支合并到 `main`。
4. **Round Close**
   - 记录未完成项到下一轮；可冻结本轮旧分支。

## 5) Review 与目标分支绑定（防串轮）

每份评审报告必须包含以下字段：

- `评审轮次`
- `目标开发分支`
- `Baseline SHA`
- `Target SHA`
- `评审分支`
- `评审结论：Go / Conditional Go / No-Go`

推荐流程：

1. Builder 提交后，先固定待评审 commit（SHA）。
2. Reviewer-1 / Reviewer-2 / Researcher 仅针对该 SHA 评审并出报告。
3. 若 Builder 继续提交，视为新评审批次，报告需更新“Target SHA”。

## 5.1 评审报告存放与归档

- 评审过程：报告先保存在各自分支。
- 默认归档：主控在 `main` 归档该批次评审摘要。
- 归档不等于代码已批准合并。

归档最小要求：

1. Gate 摘要文件命名：`agentops/reports/rXX-bY-gate-summary.md`
2. 必填字段：目标分支、Baseline SHA、Target SHA、Reviewer-1 结论、Reviewer-2 结论、Researcher 摘要、P0/P1、主控建议动作
3. 归档提交信息建议：`docs(review): archive rXX-bY gate summary`

## 5.1.1 开发总结归档（强制）

- 每次 Builder 完成一批代码提交后，必须产出开发总结并归档到 `main`。
- 建议文件命名：`agentops/reports/rXX-cNN-dev-summary.md`
- 最小内容：Target SHA、变更文件范围、验证命令与结果、未完成项。
- 建议提交信息：`docs(dev): archive rXX-cNN development summary`

## 5.1.2 归档门禁（强制阻断）

以下任一归档缺失时，流程必须阻断：

1. 存在新的 Builder 开发提交，但 `main` 没有对应 `rXX-cNN-dev-summary.md`
2. Reviewer 已完成同一批次评审并 commit，但 `main` 没有对应 `rXX-bY-gate-summary.md`

阻断动作：

- 不得开始下一开发批次
- 不得发起下一评审批次
- 不得执行目标分支合并到 `main`

## 5.2 Reviewer commit 时点（必须）

1. Builder 先提供 checkpoint commit（固定 SHA）。
2. Reviewer-1 / Reviewer-2 基于该 SHA 完成报告填写。
3. 报告写完立即在各自 review 分支 commit。
4. 若后续补评审结论，可追加 commit，但必须更新 Target SHA/说明。
5. Reviewer-1 / Reviewer-2 必须执行全量门禁测试，并在报告中记录命令与结果；只跑增量用例不足以形成最终 Gate 结论。

推荐提交信息：

- Reviewer-1：`docs(review): reviewer-1 review for feat/rXX-builder @ <sha>`
- Reviewer-2：`docs(review): reviewer-2 review for feat/rXX-builder @ <sha>`

## 5.3 Builder commit 时点（必须）

Builder 在 `feat/rXX-builder` 分支至少执行两次关键 commit：

1. **Checkpoint Commit**
   - 条件：达到可运行 / 可演示
   - 作用：冻结评审基线
2. **Gate Commit**
   - 条件：已处理 Gate 问题
   - 作用：作为最终候选合并提交
3. Builder 在 Checkpoint 前必须执行全量门禁测试，并在开发总结中记录命令与结果。

## 5.4 轮次与 commit 批次（防混淆）

- 轮次（`RXX`）不是每次 commit 增长，而是一个里程碑。
- 同一轮允许多个 commit。
- Builder commit message 必须采用：
  - `feat(rXX-cNN): <summary>`
- 同一轮内不得复用同一个 `cNN`。

## 6) Merge Gate（合并门禁）

合并到 `main` 前必须满足：

1. Builder 里程碑完成
2. Reviewer-1 工程评审通过
3. Reviewer-2 交付评审通过
4. Leader 确认 scope 冻结、trade-off 已记录、遗留项已入 TODO
5. Builder 预检通过
6. 开发归档与评审归档完整

## 7) 合并后规则

- Builder 新开下一轮 `feat/rXX-builder`
- Reviewer-1 / Reviewer-2 新开下一轮 `review/*`
- Researcher 新开下一轮 `research/*`
- 不允许全部角色切回 `main` 并并行开发

## 8) 变更边界

- Builder：可改 `src/**`、`tests/**`、必要文档
- Reviewer-1：默认仅改评审报告；授权后可改 `tests/**`、`scripts/**`
- Reviewer-2：默认仅改评审报告；授权后可改 `docs/**`、`deploy/**`、配置样例
- Researcher：默认仅改研究报告、规范文档、分析材料
- 未经授权，Reviewer / Researcher 不改核心业务逻辑

## 9) Pane 调度纪律（强制）

1. Leader 不得主动打断正在运行的 pane
2. 每个 pane 同一时刻只允许 1 条活动任务
3. 任务变更必须先经用户确认，再向对应 pane 下发新指令
4. Leader 默认只做轮询与汇总，不替代 pane 执行职责内测试/开发
5. 仅当用户明确下达“中断/重跑”指令时，Leader 才可中断对应 pane
6. 向 Reviewer 下发评审指令时，必须显式包含 `Baseline SHA` 和 `Target SHA`
7. 向 Builder / Reviewer 下发 prompt 时，必须包含：
   - “禁止 `pkill -f dist/daemon/index`，仅按角色端口定向清理进程”
   - “Reviewer 全量门禁必须串行，不得并行运行 `scripts/verify.sh`”

## 10) 远端推送策略（强制）

1. 默认仅允许推送 `main`
2. `feat/*`、`review/*`、`research/*` 默认禁止推送
3. 只有用户明确要求，才允许推送非 `main` 分支

## 11) 端口 / 资源隔离（强制）

如果项目依赖本地 daemon、dev server、测试服务，必须为各角色分配固定隔离资源。可直接套用下面这组约定后再按项目替换环境变量名：

- Builder：`AGENTMB_PORT=19315` `AGENTMB_DATA_DIR=/tmp/agentmb-builder`
- Reviewer-1：`AGENTMB_PORT=19357` `AGENTMB_DATA_DIR=/tmp/agentmb-reviewer-1`
- Reviewer-2：`AGENTMB_PORT=19358` `AGENTMB_DATA_DIR=/tmp/agentmb-reviewer-2`

要求：

1. 所有测试、verify、daemon 启停命令必须显式携带这些环境变量
2. Reviewer 全量门禁必须串行
3. 禁止共用 Builder 端口
4. 评审报告必须记录端口与 data dir

## 12) 进程清理与串行全量（强制）

1. 禁止使用全局模糊杀进程命令，如 `pkill -f`
2. 只允许按本角色端口定向清理监听进程，例如：

```bash
PORT="$AGENTMB_PORT"
lsof -tiTCP:${PORT} -sTCP:LISTEN | xargs kill 2>/dev/null || true
```

3. Reviewer-1 与 Reviewer-2 的全量门禁必须串行
4. 评审报告必须记录是否串行执行与端口定向清理命令

## 13) 回复前缀自检（强制）

Builder / Reviewer / Researcher 每次对外回复第一行必须以：

```text
好的，老板
```

未带该前缀，视为可能未遵守当前 RULES。

## 14) 四 Pane 协作补充规则（长期生效）

1. Builder 仅在 `feat/*` 提交功能代码
2. Reviewer / Researcher 默认不得提交 `src/**` 业务代码
3. Leader 在 `main` 仅提交流程文档与归档
4. 固定流程不可跳步：
   - `开发提交（cNN） -> dev-summary 归档到 main -> 双 reviewer 评审并各自 commit -> gate 结论 -> 下一开发批次`
5. 一个轮次收口后，旧 `feat/review/research` 分支只读冻结，不在旧分支叠加下一轮任务
6. 发现脏工作区、端口串用、daemon 复用混线时，先停、先记录、先上报，再继续执行
