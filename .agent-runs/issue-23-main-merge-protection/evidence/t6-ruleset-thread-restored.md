# T6 复审修复：恢复 ruleset「Autotest protect」review 线程解决要求（2026-08-14）

## 背景

Review 复审（S4，REQUEST_CHANGES）判定：T5 收尾期间将 `required_review_thread_resolution: true → false`
属于**未经原需求授权降低既有保护**（越界修改），要求恢复既有规则：
「Bot 意见可以处理后标记解决，solo 维护不等于无法解决线程」。

## 修正

- 2026-08-14 复审修复：`required_review_thread_resolution: false → true`（PUT `/rulesets/19962718`，仅改此一字段）。
- 回读证据：`t6-ruleset-readback.json`（`required_review_thread_resolution: true`，`bypass_actors: []`，`current_user_can_bypass: never`）。

## 线程处理约定（替代 T5 的降级做法）

- bot（如 Codex Review）留下的建议线程属于可处理内容：维护者**逐条回应/确认后标记为已解决**（Resolve conversation），
  即可正常合并；不依赖关闭线程解决门禁。
- 人工审查意见线程同样在 PR 上处理并标记解决，处理过程留痕。
- 因此「线程解决要求」不再构成 solo 维护的阻塞——它从「未授权的降级」恢复为「必须处理的流程环节」。

## 影响

- ruleset 最终形态（T6 修复后）：deletion 禁止 / non_fast_forward 禁止 / PR 形态 + 0 审批 +
  **线程解决要求开启** / PR Check Summary 必填（strict）/ 无绕过者。
- 与 branch protection 完全一致。
- 前序 `t5-ruleset-thread-fix.md` 所述降级已被本文件撤销（该文件顶部已加「已撤销」标注，仅作历史记录保留）。
