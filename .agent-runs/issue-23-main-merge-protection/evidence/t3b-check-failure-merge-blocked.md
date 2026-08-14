# T3b 失败样例证据：PR Check Summary 失败 → 合并被禁（2026-08-14）

## 探针 PR

- PR: https://github.com/crystepj-max/STS2-AUTOTEST/pull/26 （验证后关闭，探针测试文件未进入 main）
- 内容: `tests/unit/test_t3_probe_ci_failure.py`（刻意失败的单元测试，验证后随分支删除）

## CI 结果

- PR Check Summary: **failure**
- 失败 run: https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31768184810/job/94668324465

## 合并尝试（`gh api -X PUT /pulls/26/merge`）

```
HTTP 405
{"message":"Repository rule violations found

Required status check \"PR Check Summary\" is failing.

","documentation_url":"https://docs.github.com/rest/pulls/pulls#merge-a-pull-request","status":"405"}
```

## PR 合并状态（GitHub 判定）

```
{"mergeStateStatus":"BLOCKED","mergeable":"MERGEABLE"}
```

（MERGEABLE 表示 diff 无冲突，BLOCKED 表示被规则阻止——证明是门禁而非冲突导致）

## 结论

自动验收失败时，合并入口被制度性禁用（HTTP 405 + 状态 BLOCKED），符合 Issue 完成标准第一条。
