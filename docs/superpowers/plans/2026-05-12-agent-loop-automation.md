# Agent 循环自动化实施计划

> 归档说明：本文件原为英文计划稿。为统一中文规范，现保留中文摘要；原始英文版本请通过 Git 历史查看。

## 目标

增加本地自动化循环，让 Claude Code 负责实现，Codex 负责评审，直到 Codex 给出 `APPROVED`。

## 架构摘要

- ACP / BMAD 继续作为事实来源。
- 在 `.agent-collab/tools/` 下新增 PowerShell runner。
- runner 调用可配置的本地 CLI 命令，等待 append-only ACP 文件，并将状态与最终摘要写入 `.agent-collab/state/`。

## 主要任务

### 任务 1：新增 orchestrator smoke test

- 创建 `.agent-collab/tools/test-run-agent-loop.ps1`
- 覆盖 dry-run 与 mock loop 行为

### 任务 2：实现本地循环 runner

- 创建 `.agent-collab/tools/run-agent-loop.ps1`
- 支持 `-Task`、`-FromNextAction`、`-MaxRounds`、`-DryRun` 等参数
- 处理 `DEV_DONE`、`FIX_DONE`、`CHANGES_REQUESTED`、`BLOCKED`、`APPROVED`

### 任务 3：补充文档入口

- 更新工具 README
- 更新协议与 adapter 文档
- 补充 operator-facing bootstrap 说明

## 验收口径

- dry-run 状态可见
- mock Claude / Codex 循环可完整跑通
- 最终能生成 loop state、prompt、command log 与 summary
