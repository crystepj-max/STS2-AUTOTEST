# main 分支合并保护制度

- 任务：issue-23（[强制 main 合并前通过最终自动验收](https://github.com/crystepj-max/STS2-AUTOTEST/issues/23)）
- 生效时间：2026-08-14
- 适用范围：本仓库 `main` 分支，**所有协作者（含仓库所有者）**一视同仁

## 规则现状（配置回读证据）

### 1. 仓库可见性

仓库于 2026-08-14 从私有转为**公开**（用户授权 + 敏感信息扫描通过）。
这是启用原生分支保护的前置条件（私有免费档位对分支保护/rulesets 返回 403，已实测）。

- 证据：[`t0-visibility-change.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t0-visibility-change.md)

### 2. 分支保护（branch protection，API 回读）

对 `main` 生效：

| 项 | 值 |
|---|---|
| 必填状态检查 | `PR Check Summary`（ci-pr.yml 的 validation job，串行执行 lint / lint-imports / 单元测试 / mypy / CLI 集成测试） |
| 要求分支最新（strict） | `true` —— 成功结果必须对应准备合并的最终提交 |
| 管理员约束（enforce_admins） | `true` —— 仓库所有者同样受约束 |
| PR 审批 | 不要求（solo 维护者无法自审；PR 形态 + 必填检查已构成门禁） |
| Force push / 删除分支 | 禁止 |

- 证据：[`t1-protection-config.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t1-protection-config.md)、[`t1-protection-readback.json`](../../.agent-runs/issue-23-main-merge-protection/evidence/t1-protection-readback.json)

### 3. 仓库规则集（ruleset「Autotest protect」）

2026-07-29 创建，2026-08-14 修正以对齐治理决策（审批数 1→0、必填检查名 `Unit Tests`→`PR Check Summary`，修正旧 CI 重构后的悬空检查）：

| 规则 | 值 |
|---|---|
| 变更必须通过 PR | 启用（禁止直接写入 main） |
| 必填状态检查 | `PR Check Summary`（strict，要求分支最新） |
| PR 审批数 | 0 |
| 删除分支 / 非快进推送 | 禁止 |
| 绕过者（bypass actors） | 无，`current_user_can_bypass: never` |

- 证据：[`t1-ruleset-readback.json`](../../.agent-runs/issue-23-main-merge-protection/evidence/t1-ruleset-readback.json)

## 常规合并流程

1. 所有日常变更必须通过 PR 进入 `main`（直接 push 会被远端拒绝，见验证证据）。
2. 准备合并的**最终提交**必须通过 `PR Check Summary`（strict=true，分支需与 main 保持最新）。
3. `PR Check Summary` 失败或未运行时，PR 无法合并（见验证证据）。
4. 合并后若需要更新文档，同样走 PR（需包含会触发 CI 的变更，保证 `PR Check Summary` 存在并成功）。

## 紧急绕过流程

出现必须绕过的紧急情况时（如 CI 基础设施故障、紧急安全修复），按以下流程：

1. **授权人**：仅限仓库所有者（crystepj-max）。
2. **原因记录**：在 Issue 或 PR 上明确记录绕过原因，并附对应 Issue 链接。
3. **事后补验**：绕过后 **24 小时内**必须补齐验收证据（等价的 `PR Check Summary` 成功 run 或等价全量检查记录），并在此文档的「绕过记录」追加一行。
4. **审计**：绕过行为必须可被后续 Reviewer 复查；连续绕过将触发治理复盘。

> 绕过不改变规则本身；规则只允许「被记录、被补验的例外」，不允许无记录的常规化。

### 绕过记录

| 日期 | 授权人 | 原因 | 补验证据 |
|---|---|---|---|
| （暂无） | | | |

## 验证证据（双向）

### 失败样例

- 直接向 `main` 推送提交 → 远端拒绝：`GH013: Repository rule violations found ... push declined`
  - 证据：[`t3a-direct-push-rejected.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t3a-direct-push-rejected.md)
- `PR Check Summary` 失败的 PR → 合并被禁：HTTP 405 `Required status check "PR Check Summary" is failing`，状态 `BLOCKED`
  - 证据：[`t3b-check-failure-merge-blocked.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t3b-check-failure-merge-blocked.md)、失败 run：https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31768184810

### 成功样例

- 本 PR（治理文档 + 证据归档）通过 `PR Check Summary` 后正常合并 → 证明「满足规则的 PR 可以正常合并」。
  - 合并提交与成功 run 链接见本 PR 合并记录。

## 与 Issue 完成标准对照

| 完成标准 | 状态 | 证据 |
|---|---|---|
| 失败或缺失 `PR Check Summary` 的 PR 无法合并 | ✅ | T3b（HTTP 405 + BLOCKED） |
| 成功结果必须对应准备合并的最终提交 | ✅ | branch protection strict=true + ruleset strict 策略 |
| 日常直接写入 `main` 被禁止 | ✅ | T3a（GH013 拒绝） |
| 成功 PR 在满足人工授权后可以正常合并 | ✅ | T4 成功样例（本 PR 合并） |
| 紧急绕过有明确权限、原因记录和事后补验要求 | ✅ | 本文档「紧急绕过流程」 |
| 规则生效状态和验证证据写入正式文档 | ✅ | 本文档 |
