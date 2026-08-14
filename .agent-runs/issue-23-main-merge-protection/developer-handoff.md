# Developer Handoff: issue-23-main-merge-protection

- 开发阶段：2026-08-14（codex + GPT-5.6）
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/23
- 状态：开发完成（T0–T4 已验证），T5 回填收尾由本阶段与后续节点完成

## 实现摘要

按 task.yaml 的 T0–T4 完成 main 合并保护制度化：

- **T0**：敏感信息扫描（工作树/历史均无凭据）→ 用户再次授权 → 仓库私有转公开（不可逆操作已获双重授权）。
- **T1**：配置 main 分支保护（必填 `PR Check Summary`、strict=true、enforce_admins=true、无审批要求、禁 force push/删除），并修正既有 ruleset「Autotest protect」的悬空配置（旧检查名 `Unit Tests` 已不存在于重构后的 ci-pr.yml；审批数 1→0 对齐用户决策）。修正后 ruleset 与 branch protection 一致，无绕过者。
- **T2**：治理文档 `docs/process/main-merge-protection.md`（规则现状、常规流程、紧急绕过流程、双向验证证据、完成标准对照）。
- **T3**：失败样例双向验证——(a) 直接 push main 被远端拒绝（GH013）；(b) 构造 CI 必失败的 PR，PR Check Summary 失败后合并被 HTTP 405 拒绝、状态 BLOCKED。
- **T4**：本 PR（治理文档 + 证据归档）作为成功样例，通过 PR Check Summary 后正常合并（进行中）。

## 修改文件

- `docs/process/main-merge-protection.md`（新增，治理文档本体）
- `.agent-runs/issue-23-main-merge-protection/task.yaml`（新增，任务规格）
- `.agent-runs/issue-23-main-merge-protection/STATE.md`（新增，任务状态）
- `.agent-runs/issue-23-main-merge-protection/stage-handoff-s1.md`（新增，需求阶段交接）
- `.agent-runs/issue-23-main-merge-protection/stage-handoff-s2.md`（新增，本阶段交接）
- `.agent-runs/issue-23-main-merge-protection/developer-handoff.md`（新增，本文档）
- `.agent-runs/issue-23-main-merge-protection/evidence/`（新增 8 个证据文件：t0 可见性变更、t1 保护配置与回读 JSON、t1 ruleset 回读 JSON、t3a 直接推送被拒、t3b 失败合并被禁 + check run URL）

仓库外变更（不可逆，需 Reviewer 关注）：仓库可见性 PRIVATE → PUBLIC；main 分支保护与 ruleset「Autotest protect」已生效。

## 使用到的 BaseLib / STS2 API

不适用（本任务为仓库治理任务，无游戏 API 调用；未改动 `src/` 与 CI job 逻辑）。

## Localization 变更

无（未涉及 Card/Relic/Power/UI 等对象）。

## 自测命令

```bash
# 保护配置回读（远程状态）
gh api repos/crystepj-max/STS2-AUTOTEST/branches/main/protection
gh api repos/crystepj-max/STS2-AUTOTEST/rulesets/19962718

# 失败样例复验（探针已清理，复验方式见 evidence）
# T3a：空提交直接 push main → GH013 拒绝
# T3b：CI 必失败 PR → 合并 405 + BLOCKED

# 仓库常规检查（本次仅新增 docs/ 与 .agent-runs/ 下文档与证据，不触及 src/tests/CI）
python -m pytest tests/unit/ -q        # NOT_RUN：本次无代码改动，PR CI 的 PR Check Summary 即权威门禁
```

## 自测结果

- 保护配置回读：**PASSED**（`t1-protection-readback.json`、`t1-ruleset-readback.json`）
- T3a 失败样例：**PASSED**（直接 push main 被拒，`t3a-direct-push-rejected.md`）
- T3b 失败样例：**PASSED**（失败 CI 的 PR 合并被 405 拒绝 + BLOCKED，`t3b-check-failure-merge-blocked.md`）
- T4 成功样例：**进行中**（本 PR 的 PR Check Summary 通过后合并）
- 单元测试 / mypy / lint-imports：**NOT_RUN**（本次零代码改动；PR CI 会全量执行，以 PR Check Summary 为准）
- Localization：**NOT_APPLICABLE**
- Smoke Test：**NOT_APPLICABLE**（无游戏内变更）

## 已知风险

1. **转公开**为不可逆外溢操作：全历史对全网可见。已做敏感扫描（无凭据/密钥），但仍建议 Reviewer 抽检。
2. **ruleset 与 branch protection 双保护层**：本任务已对齐两者（同一检查名、同一 strict 语义）；未来若只改其一，可能再次出现配置漂移（本次即修复了 2026-07-29 的漂移）。
3. **md-only PR 无法合并**：paths-ignore 忽略 `docs/**` 与 `**.md`，纯文档 PR 无 PR Check Summary → 按规则会被"Expected"阻塞。治理文档更新需附带非忽略文件（如证据 JSON）触发 CI。已在治理文档「常规合并流程」第 4 条写明。
4. **issue-13 未合并**：`fix/issue-13-restore-main-ci`（PR #22）仍在 open；其修复的 ci-main.yml（push main 验收链）与本任务正交。main 的 push 检查（Quick Checks lint）当前仍失败，属 issue-13 范畴，不影响本任务已完成的 PR 门禁。

## 建议 Reviewer 重点检查

1. 分支保护与 ruleset 回读 JSON 与治理文档描述是否一致（检查名、strict、enforce_admins、审批数、绕过者）。
2. T3 双向验证证据是否完整可信（拒绝原因与 BLOCKED 状态）。
3. 紧急绕过流程是否可执行（授权人/原因/24h 补验/审计），是否与本仓库 solo 维护者现实匹配。
4. 治理文档完成标准对照表是否有夸大。
5. 确认本任务未改动 `src/` 与 `.github/workflows/`（Issue 明确不做项）。
