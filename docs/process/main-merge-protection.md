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
| review 线程解决要求 | **是**（bot/人工意见线程须处理并标记解决后方可合并；处理约定见 [`t6-ruleset-thread-restored.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t6-ruleset-thread-restored.md)） |
| 删除分支 / 非快进推送 | 禁止 |
| 绕过者（bypass actors） | 无，`current_user_can_bypass: never`（紧急情况下临时授予并立即恢复，见「紧急绕过流程」） |

- 证据：[`t1-ruleset-readback.json`](../../.agent-runs/issue-23-main-merge-protection/evidence/t1-ruleset-readback.json)、复审修复后回读：[`t6-ruleset-readback.json`](../../.agent-runs/issue-23-main-merge-protection/evidence/t6-ruleset-readback.json)

### 4. 本地配置防护（复审新增）

仓库公开后，本地环境配置存在误提交风险。规则与门禁：

| 项 | 值 |
|---|---|
| 忽略规则 | `.gitignore` 忽略 `.env` 与 `.env.*`，仅放行 `.env.example` 入库 |
| 自动检查 | `bash scripts/check-env-gitignore.sh`（退出码门禁）：① `.env` 必须被忽略；② `.env` 不得被跟踪；③ 已跟踪环境文件只允许 `.env.example` |
| 使用约定 | 密钥与本地配置只放 `.env`；模板与文档用 `.env.example`；仓库不得出现真实凭据 |

- 证据：`.gitignore`、[`scripts/check-env-gitignore.sh`](../../../scripts/check-env-gitignore.sh)、复审终版 `t5-final-evidence.json` 的 `env_guard` 字段

## 常规合并流程

1. 所有日常变更必须通过 PR 进入 `main`（直接 push 会被远端拒绝，见验证证据）。
2. 准备合并的**最终提交**必须通过 `PR Check Summary`（strict=true，分支需与 main 保持最新）。
3. `PR Check Summary` **失败或未运行**（缺失）时，PR 都无法合并（失败样例与缺失样例见验证证据）。
4. 合并前需满足 review 线程解决要求：bot/人工意见线程逐条处理并标记解决。
5. 合并后若需要更新文档，同样走 PR（需包含会触发 CI 的变更，保证 `PR Check Summary` 存在并成功）。

## 紧急绕过流程

出现必须绕过的紧急情况时（如 CI 基础设施故障、紧急安全修复），采用
**「临时改规则 → 操作 → 立即恢复 → 回读 → 审计」**机制（2026-08-14 已真实演练，
见 [`t7-emergency-bypass-drill.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t7-emergency-bypass-drill.md)）。

> 本机制不保留任何静态绕过权限（日常 `bypass_actors: []`、`current_user_can_bypass: never`），
> 紧急窗口只存在于「改规则」到「恢复」之间，且全程留痕。

### 执行步骤（授权人：仅限仓库所有者 crystepj-max）

1. **原因记录（先于操作）**：在 Issue 或 PR 上说明紧急原因，附对应 Issue 链接。
2. **临时改规则（两层，缺一不可）**：
   - ruleset「Autotest protect」：`bypass_actors=[{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "pull_request"}]`
     （授予 Admin 角色绕过，仅开放 PR 合并路径；直接 push main 仍被禁）。
   - branch protection：`enforce_admins: false`（分支保护无「绕过者」概念，须临时解除管理员豁免；
     演练实证：仅授予 ruleset 绕过时 `gh pr merge --admin` 仍被 405 拒绝）。
3. **操作**：紧急合并目标 PR（`gh pr merge <n> --merge --admin`）。
4. **立即恢复（最迟 24 小时内，演练为分钟级）**：恢复 ruleset `bypass_actors=[]`；
   恢复 branch protection `enforce_admins: true`。
5. **事后回读**：确认 `current_user_can_bypass: never`、`bypass_actors: []`、
   `enforce_admins: true`，无残留。
6. **审计与补验**：在「绕过记录」追加一行；被绕过 PR 的变更在 **24 小时内**补齐等价验收证据
   （等价的 `PR Check Summary` 成功 run 或等价全量检查记录）。

### 绕过记录

| 日期 | 授权人 | 原因 | 补验证据 |
|---|---|---|---|
| 2026-08-14 | crystepj-max | 紧急绕过流程演练（S4 复审要求）：临时授予绕过 → 合并被阻断的探针 PR #31 → 立即恢复 | [`t7-emergency-bypass-drill.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t7-emergency-bypass-drill.md)（合并 `750ba976`；恢复后回读无残留） |

## 验证证据（双向）

### 失败样例

- 直接向 `main` 推送提交 → 远端拒绝：`GH013: Repository rule violations found ... push declined`
  - 证据：[`t3a-direct-push-rejected.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t3a-direct-push-rejected.md)
- `PR Check Summary` 失败的 PR → 合并被禁：HTTP 405 `Required status check "PR Check Summary" is failing`，状态 `BLOCKED`
  - 证据：[`t3b-check-failure-merge-blocked.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t3b-check-failure-merge-blocked.md)、失败 run：https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31768184810

### 缺失样例（复审补证）

- 仅含 markdown 文件的 PR（命中 ci-pr.yml `paths-ignore`，0 个检查运行）→ 合并被禁：
  HTTP 405 `Required status check "PR Check Summary" is expected.`
  - 证据：[`t8-missing-check-probe.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t8-missing-check-probe.md)（探针 PR #31，`check-runs = 0`）

### 成功样例

- 治理文档 PR 通过 `PR Check Summary` 后正常合并 → 证明「满足规则的 PR 可以正常合并」：
  - PR: https://github.com/crystepj-max/STS2-AUTOTEST/pull/27
  - 成功 run: https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31768914035
  - 合并提交: `a0673525aa32fe3845efdbea47c3b023dd442856`（2026-08-14）
  - 证据：[`t4-success-sample.md`](../../.agent-runs/issue-23-main-merge-protection/evidence/t4-success-sample.md)

## 与 Issue 完成标准对照

| 完成标准 | 状态 | 证据 |
|---|---|---|
| `PR Check Summary` **失败**的 PR 无法合并 | ✅ | T3b（HTTP 405 + BLOCKED，PR #26） |
| `PR Check Summary` **缺失**（未运行）的 PR 无法合并 | ✅ | T8 探针（HTTP 405 `is expected`，PR #31，0 check-runs） |
| 成功结果必须对应准备合并的最终提交 | ✅ | branch protection strict=true + ruleset strict 策略 |
| 日常直接写入 `main` 被禁止 | ✅ | T3a（GH013 拒绝） |
| 成功 PR 在满足人工授权后可以正常合并 | ✅ | T4 成功样例（PR #27 合并）+ 复审修复 PR 合并 |
| 紧急绕过有明确权限、原因记录和事后补验要求 | ✅ | T7 真实演练（临时改规则→操作→立即恢复→回读→审计） |
| 本地配置（.env）不得入库 | ✅ | `.gitignore` + `scripts/check-env-gitignore.sh` 门禁（见「本地配置防护」） |
| 规则生效状态和验证证据写入正式文档 | ✅ | 本文档 |
