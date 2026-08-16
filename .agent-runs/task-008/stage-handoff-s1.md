# Stage Handoff — S1 需求/调度

**任务**：task-008 / issue #18 为自动验收中的外部任务建立明确时间边界  
**时间**：2026-08-16  
**来源节点**：S1 调度  
**目标节点**：S2 开发（需先通过人工门禁「需求确认」）

## 输入

- GitHub Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/18
- 分诊评论：https://github.com/crystepj-max/STS2-AUTOTEST/issues/18#issuecomment-5306427427

## 调度结论

- **三要素**：齐全（complete=true）
  - 任务目标：三类外部检查独立时间边界，超时失败、终止残留、保留输出
  - 涉及范围：3 个 baseline 脚本 + runner_utils.py + 测试 + ci-pr.yml；明确不做 3 项
  - 验收标准：6 条可操作可验证标准
- **集成测试**：需要（跨脚本/新增共享模块/CI workflow/受控超时验证）
- **规模**：sized-m（非 L 型，无需 wayfinder/OpenSpec）

## 已落盘产物

- `.agent-runs/task-008/task.yaml`
- `.agent-runs/task-008/STATE.md`
- 本文件 `.agent-runs/task-008/stage-handoff-s1.md`

## 任务拆分（依赖顺序）

1. **T1** `runner_utils.py` + `tests/unit/test_runner_utils.py`（前置）
2. **T2** `check_ruff_baseline.py` 接入超时（可并行）
3. **T3** `check_mypy_baseline.py` 接入超时（可并行）
4. **T4** `check_pytest_baseline.py` 接入超时（可并行）
5. **T5** `ci-pr.yml` 呈现与受控超时验证（依赖 T2/T3/T4）

## 人工门禁「需求确认」待确认项

- [ ] 三类检查（ruff/mypy/pytest）的时间上限数值
- [ ] 受控超时验证方式（如临时注入 sleep 的测试分支或 CI matrix 参数）
- [ ] 新增 `.github/scripts/runner_utils.py` 的模块位置与命名

## 风险与注意事项

- 正常通过路径必须与 Run #47 保持一致，不得引入新的判定变化。
- 超时路径需确保子进程与孙子进程都被终止，避免残留。
- 已产生的 stdout/stderr、日志与 JUnit 输出需保留用于诊断。
- 不改既存 ruff/mypy 债务，也不改动 test-agent 运行器内部超时。

## 下一步动作

人工门禁通过后，创建分支 `bugfix/issue-18-timeout-boundaries` 并从 T1 开始开发。
