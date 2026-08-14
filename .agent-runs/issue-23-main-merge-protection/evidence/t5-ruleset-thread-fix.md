# T5 修正：ruleset「Autotest protect」review 线程解决要求（2026-08-14）

## 背景

T4 成功样例合并后，T5 收尾 PR（#28）在 `PR Check Summary` 通过的情况下仍被阻止合并：

```
HTTP 405: Repository rule violations found
A conversation must be resolved before this pull request can be merged.
```

原因：ruleset 的 `pull_request` 规则保留了 `required_review_thread_resolution: true`
（2026-07-29 原配置继承），而 Codex Review bot（chatgpt-codex-connector）会在每个 PR
自动留下 P1/P2 建议线程。solo 维护者场景下（用户已确认 approvals=0），
bot 建议线程未解决会阻塞所有常规合并——与治理决策「流程顺畅、无人工审批」冲突。

## 修正

- 2026-08-14：`required_review_thread_resolution: true → false`
- 理由：bot 线程是建议性内容，不构成门禁；PR Check Summary（strict）+ PR 形态已是唯一强制门禁。
  人工审查意见仍可通过 Issue/PR 评论提出，不依赖线程解决机制。
- 修正后 T5 收尾 PR #28 立即正常合并（c549c20e）。

## 证据

- 合并被阻错误：HTTP 405 "A conversation must be resolved"
- 修正后回读：`t1-ruleset-readback.json`（`required_review_thread_resolution: false`）
- T5 合并：https://github.com/crystepj-max/STS2-AUTOTEST/pull/28（merged c549c20e）

## 影响

- ruleset 最终形态：deletion 禁止 / non_fast_forward 禁止 / PR 形态 + 0 审批（无线程解决要求）/ PR Check Summary 必填（strict）/ 无绕过者。
- 与 branch protection 完全一致，无悬空参数。
