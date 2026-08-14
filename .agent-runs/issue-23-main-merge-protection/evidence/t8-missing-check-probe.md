# T8 复审修复：缺失 `PR Check Summary` 的 PR 合并阻断证据（2026-08-14）

## 背景

Review 复审（S4，REQUEST_CHANGES）判定：原 T3b 只验证了「检查**失败**阻断合并」，
「检查**缺失**（无检查运行）阻断合并」仅有文档断言、无行为证据。要求补一个
**不会产生 `PR Check Summary`** 的 PR 探针并保存无法合并的回读证据，同时把
failure / missing 拆成两个独立验收字段。

## 探针构造

- PR: [#31](https://github.com/crystepj-max/STS2-AUTOTEST/pull/31)（`probe/issue-23-missing-check`，提交 `5e476b9`）
- 变更内容：**仅 1 个 markdown 文件**（探针记录本身），命中 ci-pr.yml `paths-ignore`（`docs/**`、`**.md`、`.gitignore`）
- 预期效果：不触发任何 CI 工作流 → 无 `PR Check Summary` 检查运行

## 回读证据（创建后实读）

1. **检查运行数 = 0**：`gh api repos/crystepj-max/STS2-AUTOTEST/commits/5e476b9/check-runs`
   返回 `check_runs | length = 0`（无任何检查，包括 `PR Check Summary`）。
2. **合并尝试被拒**：`PUT /pulls/31/merge` → HTTP 405：

   ```
   Repository rule violations found
   Required status check "PR Check Summary" is expected.
   ```

## 结论

「失败或缺失 `PR Check Summary` 的 PR 无法合并」的两条分支均已有行为证据：

| 场景 | PR | 证据 |
|---|---|---|
| 检查失败（failure） | #26（CI 必失败构造） | `t3b-check-failure-merge-blocked.md`（HTTP 405 + BLOCKED） |
| 检查缺失（missing，0 个检查运行） | #31（纯 md 探针，本文件） | HTTP 405 `Required status check ... is expected.` + 0 check-runs |

## 后续

- 探针 PR #31 在 T7 紧急绕过演练中作为「操作」对象经授权通道合并入 main
  （`750ba976`），证明紧急流程真实可执行（见 `t7-emergency-bypass-drill.md`）。
- 日常流程提醒：纯文档变更不会触发 CI，需附带非忽略文件（如 JSON 证据）才能通过
  `PR Check Summary` 合并——`docs/process/main-merge-protection.md` 已写明。
