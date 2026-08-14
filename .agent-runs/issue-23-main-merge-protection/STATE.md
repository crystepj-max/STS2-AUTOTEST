# STATE — issue-23-main-merge-protection

- 更新时间：2026-08-14（开发阶段全部完成，T5 回填收尾）
- 阶段：开发完成（T0–T4 全部验证通过），进入 Review
- 状态机位置：`DEV_ASSIGNED` → `DEV_REVIEW`（Review 节点从 stage-handoff-s2.md 读起）

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/23 ，标签 `enhancement` + `sized-s`。
- **仓库已转公开**（2026-08-14，用户双重授权 + 敏感扫描通过）——不可逆操作，见 `evidence/t0-visibility-change.md`。
- **main 分支保护已生效**（回读证据 `evidence/t1-protection-readback.json`）：
  - 必填检查 `PR Check Summary`，strict=true，enforce_admins=true，无审批要求，禁 force push/删除。
- **ruleset「Autotest protect」已修正**（`evidence/t1-ruleset-readback.json`）：
  - 审批数 1→0；必填检查 `Unit Tests`→`PR Check Summary`（修复旧 job 名悬空）；无绕过者（bypass=never）。
- **失败样例已验证**（`evidence/t3a-direct-push-rejected.md`、`t3b-check-failure-merge-blocked.md`）：
  - T3a 直接 push main → GH013 拒绝；
  - T3b 失败 CI 的 PR（#26）→ 合并 405 拒绝 + BLOCKED（失败 run 31768184810）。
- **成功样例已验证**（`evidence/t4-success-sample.md`）：
  - 治理文档 PR #27 → PR Check Summary success（run 31768914035，重跑后全绿）→ 已合并（a0673525）。
- 治理文档：`docs/process/main-merge-protection.md`（规则现状、常规流程、紧急绕过、双向证据、完成标准对照）。

## 风险与注意

- **转公开不可逆**：全历史公开；Review 阶段若发现泄露风险须立即报告。
- **双保护层**（branch protection + ruleset）已对齐；后续改动任一层需同步另一层，防漂移。
- **md-only PR 无法合并**：paths-ignore 忽略 docs/**.md，纯文档 PR 无 PR Check Summary；文档更新须附带非忽略文件（治理文档已写明）。
- issue-13（PR #22）未合并，main push 检查（ci-main.yml lint）仍失败，与本任务正交，不阻塞本任务门禁。
- 本任务未改动 `src/`、`tests/` 与 `.github/workflows/`（Issue 明确不做项）。
- 自托管 runner 网络偶发中断（T4 首次运行 ECONNRESET），重跑即可恢复——已有先例记录。

## 下一步

1. Review 节点接续：读 `stage-handoff-s2.md` 与 `developer-handoff.md`，核对配置回读与双向证据。
2. Review 通过后关闭 Issue #23（完成标准已全部勾选，证据链完整）。
