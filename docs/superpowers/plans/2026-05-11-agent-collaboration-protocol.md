# Agent 协作协议实施计划

> 归档说明：本文件原为英文计划稿。为统一中文规范，现保留中文摘要；原始英文版本请通过 Git 历史查看。

## 目标

为仓库引入一套可复用的 agent collaboration protocol，使 Codex、Claude Code 和其他 coding agent 可以围绕开发、评审、验证与架构决策进行协作。

## 核心思路

- 协议本体放在 `.agent-collab/`。
- 项目专属交付规则由 `.agent-collab/WORKFLOW_ADAPTER.md` 承载。
- 当前仓库以 BMAD 作为 adapter，不把 BMAD 假设写死到通用协议中。

## 主要任务

### 任务 1：建立协议骨架

- 创建 `.agent-collab/AGENT_PROTOCOL.md`
- 创建 `.agent-collab/WORKFLOW_ADAPTER.md`
- 创建 `.agent-collab/README.md`

### 任务 2：建立角色与消息模板

- 创建角色文件，如 developer、architect、reviewer、verifier。
- 创建 dev-done、review、decision-request、decision、verify-result 等消息模板。

### 任务 3：建立 inbox 与日志目录

- 创建 `.agent-collab/inbox/*`
- 创建 `.agent-collab/log/*`
- 创建 `.agent-collab/state/board.md`

## 预期结果

- 所有 agent 都能明确何时开始实现、何时暂停、何时请求决策、何时进入审批。
- 仓库内形成 append-only handoff 与 review 轨迹。
- BMAD 仅作为仓库专属 adapter 存在。
