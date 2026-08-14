# T7 复审修复：紧急绕过流程真实演练记录（2026-08-14）

## 背景

Review 复审（S4，REQUEST_CHANGES）判定：原「紧急绕过流程」不可执行——ruleset 无绕过者
（`bypass_actors: []`、`current_user_can_bypass: never`），文档所述授权人没有实际通道，
且「绕过不改变规则」与实际能力不符。要求：选择一种真实机制并**演练留证**。

本演练采用「临时改规则 → 操作 → 立即恢复 → 回读 → 审计」机制（Review 给出的两个选项之一）。

## 演练对象

探针 PR [#31](https://github.com/crystepj-max/STS2-AUTOTEST/pull/31)（纯 markdown，无 `PR Check Summary`，
合并被阻，详见 `t8-missing-check-probe.md`）。

## 演练步骤与回读证据

| 步骤 | 操作 | 回读结果 | 原始证据 |
|---|---|---|---|
| 1. 事前 | 无（记录当前状态） | ruleset `bypass_actors: []`、`current_user_can_bypass: never`；branch protection `enforce_admins: true`、strict、PR Check Summary | `t7-ruleset-before.json`、`t7-branch-protection-before.json` |
| 2a. 临时改规则（ruleset） | PUT ruleset：`bypass_actors=[{RepositoryRole, actor_id=5(Admin), bypass_mode=pull_request}]` | `current_user_can_bypass: pull_requests_only`（绕过已授予，仅 PR 操作可绕过，直接 push 仍被禁） | `t7-ruleset-during.json` |
| 2b. 临时改规则（分支保护） | PUT branch protection：`enforce_admins: false`（分支保护无绕过者概念，须临时解除管理员豁免） | `enforce_admins: false`、strict 不变 | `t7-branch-protection-during.json` |
| 3. 操作 | `gh pr merge 31 --merge --admin` | **合并成功**（merge_commit_sha `750ba9768159c3e310bf906abf84a1207f292cbe`） | PR #31 merged 状态 |
| 4. 立即恢复 | PUT 回 ruleset `bypass_actors: []`；PUT 回 branch protection `enforce_admins: true` | ruleset `current_user_can_bypass: never`；branch protection `enforce_admins: true` | `t7-ruleset-after.json`、`t7-branch-protection-after.json` |
| 5. 事后回读 | 与「恢复」同步骤回读 | 双层保护完全复原（绕过者无、线程解决开启、enforce_admins 开启） | 同上 |

## 关键实证结论

1. **仅授予 ruleset 绕过不足以完成紧急合并**：`gh pr merge --admin` 在 ruleset 绕过已生效
   （`current_user_can_bypass: pull_requests_only`）但 branch protection `enforce_admins: true` 时仍被拒
   （HTTP 405 `Required status check "PR Check Summary" is expected.`）。
   → 紧急流程必须**同时**临时调整两层：ruleset 授予绕过 + branch protection 解除管理员豁免。
2. **紧急绕过只开放 PR 合并路径**（bypass_mode=pull_request），直接推送 main 仍被禁止，
   日常 T3a 保护不受影响。
3. **恢复是流程硬性步骤**：演练完成后回读确认 `bypass_actors: []`、`current_user_can_bypass: never`、
   `enforce_admins: true`，无残留。

## 审计

- 演练全程可复现：本文件 + 7 个原始 JSON 回读证据；操作时间 2026-08-14。
- 演练期间唯一合并：探针 PR #31（`750ba976`，内容为治理探针记录本身，无业务代码）。
- 未发生未记录的保护变更；演练前后的规则状态均可回读比对。
