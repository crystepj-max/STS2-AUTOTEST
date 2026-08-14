# STATE — issue-23-main-merge-protection

- 更新时间：2026-08-14（开发阶段，T4 合并进行中）
- 阶段：开发完成（T0–T3 已验证，T4 成功样例 PR 提交中，T5 回填待办）
- 状态机位置：`DEV_ASSIGNED` → `DEV_REVIEW`（T4 合并后由 Review 节点接续）

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/23 ，标签 `enhancement` + `sized-s`。
- **仓库已转公开**（2026-08-14，用户双重授权 + 敏感扫描通过）——不可逆操作，见 `evidence/t0-visibility-change.md`。
- **main 分支保护已生效**（回读证据 `evidence/t1-protection-readback.json`）：
  - 必填检查 `PR Check Summary`，strict=true，enforce_admins=true，无审批要求，禁 force push/删除。
- **ruleset「Autotest protect」已修正**（`evidence/t1-ruleset-readback.json`）：
  - 审批数 1→0（solo 维护者决策）；必填检查 `Unit Tests`→`PR Check Summary`（修复 2026-07-29 旧 job 名悬空）；无绕过者（bypass=never）。
- **失败样例已验证**：
  - T3a 直接 push main → GH013 拒绝（`evidence/t3a-direct-push-rejected.md`）。
  - T3b 失败 CI 的 PR（#26）→ 合并 405 拒绝 + BLOCKED（`evidence/t3b-check-failure-merge-blocked.md`，失败 run https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31768184810）；探针已清理。
- **成功样例进行中**：T4 文档 PR（治理文档 + 证据归档）通过 PR Check Summary 后合并。
- 治理文档：`docs/process/main-merge-protection.md`（规则现状、常规流程、紧急绕过、双向证据、完成标准对照）。

## 风险与注意

- **转公开不可逆**：全历史公开；Review 阶段若发现泄露风险须立即报告。
- **双保护层**（branch protection + ruleset）已对齐；后续改动任一层需同步另一层，防漂移。
- **md-only PR 无法合并**：paths-ignore 忽略 docs/**.md，纯文档 PR 无 PR Check Summary；文档更新须附带非忽略文件（治理文档已写明）。
- issue-13（PR #22）未合并，main push 检查（ci-main.yml lint）仍失败，与本任务正交，不阻塞本任务门禁。
- 本任务未改动 `src/`、`tests/` 与 `.github/workflows/`（Issue 明确不做项）。

## 下一步

1. T4 成功样例 PR 合并（本 PR）。
2. T5：回填 Issue 完成标准（勾选 + 证据链接 + 关闭）、补 `ready`/`done` 标签、更新本文件终版。
3. Review 节点接续（读 stage-handoff-s2.md）。
