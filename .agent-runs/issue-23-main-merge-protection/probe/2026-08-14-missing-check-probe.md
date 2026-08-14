# 探针记录：缺失 `PR Check Summary` 的 PR 合并阻断样例（T8）

- 日期：2026-08-14
- 任务：issue-23 复审修复（T8）
- 目的：本 PR **只包含 markdown 文件**，命中 ci-pr.yml 的 `paths-ignore`（`docs/**`、`**.md`、`.gitignore`），
  因此不会触发任何 CI 工作流——即不会产生 `PR Check Summary` 检查。
  用于验证「缺失必填检查的 PR 无法合并」，补齐 T3b（失败样例）之外的缺失（missing）样例。

## 观察记录（创建后回读）

- 检查运行数：0（无 `PR Check Summary`）
- 合并状态：`BLOCKED`（必填检查 `PR Check Summary` 处于 expected 状态，未报告）
- 合并尝试：HTTP 405 拒绝

详细回读证据见 `../evidence/t8-missing-check-probe.md`。

## 结论

「失败或缺失 `PR Check Summary` 的 PR 无法合并」的缺失分支已实证：

| 场景 | 证据 |
|---|---|
| 检查失败（failure） | `evidence/t3b-check-failure-merge-blocked.md`（PR #26，HTTP 405 + BLOCKED） |
| 检查缺失（missing，无检查运行） | 本探针（HTTP 405 + BLOCKED） |

## 后续

- 本探针 PR 由 T7 紧急绕过演练作为「操作」对象：演练期间通过临时授予的绕过权限合并，
  以证明紧急流程真实可执行；演练细节见 `evidence/t7-emergency-bypass-drill.md`。
- 日常流程中，此类纯文档变更应附带非忽略文件（如 JSON 证据）以触发 CI，
  保证 `PR Check Summary` 存在并成功后合并。
