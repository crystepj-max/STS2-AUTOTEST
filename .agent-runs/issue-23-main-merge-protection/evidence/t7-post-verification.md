# T7 补验记录：PR #31 紧急绕过的成功补验（S4 复审要求）

- 记录时间：2026-08-14（S4 REQUEST_CHANGES 复审修复轮）
- 制度依据：`docs/process/main-merge-protection.md`「紧急绕过流程」第 6 步——
  被绕过 PR 的变更须在操作完成后 **24 小时内**补齐等价验收证据。
- 原则：恢复证据 ≠ 补验证据。「规则已恢复」只证明保护复原，不能替代成功 run 的等价验收。

## 被绕过操作（T7 演练，2026-08-14）

| 字段 | 值 |
|---|---|
| 被绕过 PR | [#31](https://github.com/crystepj-max/STS2-AUTOTEST/pull/31)（缺失检查探针，纯 markdown，0 检查运行） |
| 被绕过 SHA（merge commit） | `750ba9768159c3e310bf906abf84a1207f292cbe` |
| 操作完成时间（merged_at） | 2026-08-14T05:50:11Z |
| 授权人 | crystepj-max（仓库所有者；授权依据 = issue #23「紧急绕过」条款 + S4 复审演练要求） |
| 恢复 | 演练第 4/5 步立即恢复（分钟级），回读 `bypass_actors: []`、`current_user_can_bypass: never`、`enforce_admins: true` 无残留（`t7-ruleset-after.json`、`t7-branch-protection-after.json`） |

## 补验证据（24h 内成功 run）

| 字段 | 值 | 核验方式 |
|---|---|---|
| 补验 run | [31774866515](https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31774866515)（`PR Check Summary`） | `gh api repos/crystepj-max/STS2-AUTOTEST/actions/runs/31774866515` |
| conclusion | **success** | 同上（实测返回 `"conclusion":"success"`） |
| run head | `64ed09fcd1f0174ca211da7170ce61da2a1b6b50` | 同上（实测返回 `"head_sha":"64ed09fcd1f0174ca211da7170ce61da2a1b6b50"`） |
| 完成时间 | 2026-08-14T07:47:43Z | 同上（`updated_at`）；**合并后约 2 小时（05:50:11Z → 07:47:43Z），在 24 小时期限内** ✓ |
| 覆盖关系 | 补验 run 的 head `64ed09fc` 的祖先链**包含**被绕过合并 `750ba976` | 实测 `git merge-base --is-ancestor 750ba976 64ed09fc` 返回 0（是祖先）——等价验收覆盖了被绕过内容 ✓ |

## 结论

被绕过 PR #31 的变更（缺失检查探针记录）在合并后约 2 小时由成功 run 31774866515
（head `64ed09fc`，`PR Check Summary` success）完成等价验收，满足「24 小时内补验」要求。
绕过审计闭环成立：授权 → 原因 → 操作 → 立即恢复 → 回读 → 补验，全链路留痕。
