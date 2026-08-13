# 外部阻塞记录：GitHub 账户计费失败导致托管 runner 无法分配（2026-08-13）

- 记录时间：2026-08-13 22:55 CST
- 任务：issue-13-restore-main-ci（开发阶段 attempt-002）

## 现象

Draft PR #22（`fix/issue-13-restore-main-ci`）创建后，PR CI（ci-pr.yml，pull_request run
[31712107063](https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31712107063)）全部 job
在 3–6 秒内失败，**runner 字段为空、0 步骤**；`gh run rerun --failed` 重跑（attempt 2）同样失败。

## 根因（job 注解原文）

> failure | The job was not started because recent account payments have failed or your
> spending limit needs to be increased. Please check the 'Billing & plans' section in your settings

- 来源：`check-runs/94487437703/annotations`（Import Layer Check ubuntu-latest 等全部托管 job 同款注解）。
- 性质：**账户级计费/支付失败**，GitHub 拒绝分配一切 GitHub 托管 runner（ubuntu/windows/macos）。
- 与本次改动无关：ci-pr.yml 未在本 PR 中修改；改动前最后一次正常运行的托管 job 为
  06:54 UTC 的 run 31675595593（runner 正常分配、步骤正常执行）→ 计费失败发生在 06:54–14:49 UTC 之间。

## 影响

- 所有依赖 GitHub 托管 runner 的 PR/主分支 job 当前都无法启动（仓库级，不只本 PR）。
- **自托管 runner 不受计费影响**（自托管不计入账单），仍在线可接活；但 ci-pr.yml 中唯一
  自托管 job（CLI Integration Tests）依赖托管 job（unit-test），故被跳过。

## 解除条件（用户侧）

1. 用户登录 GitHub → Settings → Billing & plans → 修复支付/上调消费上限。
2. 解除后对 PR #22 重跑失败 job（`gh run rerun 31712107063 --failed`），或合入 main 触发主分支运行。
3. 若无需等待计费解除，可考虑临时用自托管 runner 兜底运行非跨平台检查（设计变更，需单独评审，本任务不做）。

## 对 T4–T6 的影响

- T4 主分支重跑：需计费解除 + PR 合入后触发（`workflow_dispatch` 已就绪）。
- T5 CLI 集成 / T6 Gawain 部署：依赖 T4 通过后的自托管 job——自托管侧无阻塞（runner online、F1 修复已生效）。
