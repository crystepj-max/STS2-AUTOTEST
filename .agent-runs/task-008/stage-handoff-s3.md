# Stage Handoff — S3 测试

**任务**：task-008 / issue #18 为自动验收中的外部任务建立明确时间边界  
**时间**：2026-08-16  
**来源节点**：S3 测试（attempt-002，针对 S4 审核 REQUEST_CHANGES 修复轮）  
**目标节点**：S4 验收  

## 输入

- S2 开发修复交接：`dev-report.md`（attempt-002，commit `75168b1`）
- S4 审核报告：`review-report.md`（attempt-001）
- 调度产物：`dispatch-result.json`（`need_integration_test: true`）
- GitHub Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/18
- Draft PR：https://github.com/crystepj-max/STS2-AUTOTEST/pull/39

## 测试结论

- **单元测试**：PASSED（targeted 14/14；全量 1840 passed，0 failed）
- **单元测试基线门禁**：PASSED（`check_pytest_baseline.py` exit 0，无新增最终失败）
- **Ruff 增量基线**：PASSED（clean-tree HEAD vs main：`New in this PR: 0`）
- **mypy 增量基线**：PASSED（clean-tree HEAD vs main：`New in this PR: 0`）
- **导入层级隔离**：PASSED（`lint-imports` 1 kept, 0 broken）
- **mypy 新模块**：PASSED（`.github/scripts/runner_utils.py` no issues）
- **CI workflow 结构**：PASSED（job timeout 120min；`Upload check logs` 位于 mypy 之后、summary 之前）
- **受控超时验证**：PASSED（2s 杀 300s sleep → exit 124，无残留；亚秒 `1e-06s` 显示正确）
- **KeyboardInterrupt 清理测试**：PASSED，且已复现旧版无 `proc.wait` 时子进程残留，证明非假测试

## 修复验证（本轮针对 S4 审核项）

| 审核项 | 严重程度 | 修复文件 | 验证结果 |
|--------|----------|----------|----------|
| `Upload check logs` 步骤在 mypy 之前 | HIGH | `.github/workflows/ci-pr.yml` | 已移至 mypy 之后、summary 之前 |
| job 级 timeout 小于 step 兜底之和 | MEDIUM | `.github/workflows/ci-pr.yml` | `timeout-minutes: 120` |
| KeyboardInterrupt 分支零覆盖 | MEDIUM | `tests/unit/test_runner_utils.py` | 新增测试通过且抓到旧版僵尸缺陷 |
| 超时消息亚秒显示 `0s` | LOW | `.github/scripts/runner_utils.py` | `exceeded 1e-06s` |
| 输出截断无提示 | LOW | `.github/scripts/runner_utils.py` | 日志写入 `output truncated` |
| ruff I001 未提交 | 流程 | `tests/unit/test_runner_utils.py` | 已提交，ruff changed 文件检查通过 |

## 已执行验证命令摘要

```bash
# 单元测试
.venv/bin/python -m pytest tests/unit/test_runner_utils.py tests/unit/test_ci_pytest_baseline.py -v
.venv/bin/python -m pytest tests/unit/ -q
.venv/bin/python .github/scripts/check_pytest_baseline.py

# 架构 / 类型门禁
.venv/bin/lint-imports
.venv/bin/mypy .github/scripts/runner_utils.py
.venv/bin/mypy src/sts2_autotest --strict   # 5 个历史错误，非本次引入

# CI baseline 脚本（clean-tree HEAD vs main）
export PATH="/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST/.venv/bin:$PATH"
git worktree add --detach /tmp/ci-head-issue18 HEAD
.venv/bin/python .github/scripts/check_ruff_baseline.py --baseline-dir /private/tmp/ci-baseline --current-dir /tmp/ci-head-issue18
.venv/bin/python .github/scripts/check_mypy_baseline.py --baseline-dir /private/tmp/ci-baseline --current-dir /tmp/ci-head-issue18

# workflow 结构校验
.venv/bin/python - <<'PY'
import yaml
wf = yaml.safe_load(Path('.github/workflows/ci-pr.yml').read_text())
job = wf['jobs']['validation']
steps = [s['name'] for s in job['steps']]
assert job['timeout-minutes'] == 120
assert steps.index('Upload check logs (issue-18)') > steps.index('Check no new mypy debt')
assert steps.index('Upload check logs (issue-18)') < steps.index('Generate summary')
print('workflow structure OK')
PY

# 受控超时路径
.venv/bin/python -c "... run_timed timeout=2 杀 300s sleep ..."
RUFF_TIMEOUT_SECONDS=0.000001 .venv/bin/python .github/scripts/check_ruff_baseline.py ...
MYPY_TIMEOUT_SECONDS=0.000001 .venv/bin/python .github/scripts/check_mypy_baseline.py ...

# KeyboardInterrupt 非假测试验证
.venv/bin/python attachments/reproduce_keyboard_interrupt_bug.py
```

## 风险与注意事项

1. **工作区污染**：`tests/generated/*.py` 存在其他任务遗留的未提交改动， dirty-tree 下 `check_ruff_baseline.py` 会报 6 条新增 I001；已用干净 HEAD worktree 复验，PR 范围无新增债务。
2. **既有 mypy 债务**：`mypy src/sts2_autotest --strict` 仍有 5 个历史错误（cv2 / Quartz / AppKit / mypy.ini 未用 section），均非本次引入，增量基线门禁通过。
3. **既有 ruff 债务**：`ruff check src tests` 仍有约 298 条历史债务，本次未新增。
4. **pytest 历史基线**：`darwin` 平台登记的 5 条历史失败当前全部通过，`check_pytest_baseline.py` 将其列为已解决，不影响门禁。
5. **Windows 进程组清理**：仍为尽力而为；CI 主平台 macOS 已覆盖 POSIX 路径（含孙子进程）。

## 下一步动作（S4/开发者）

1. 确认 clean-tree HEAD（`75168b1`）已推送至 PR #39。
2. 重新触发 PR #39 CI，预期全部通过。
3. 若 CI 仍失败，回传 S3 复现日志（证据保存在本节点 attachments）。

## 附件索引

完整证据位于：

```
/Users/chris/.gold-band/projects/Users-chris-STS2-WORKSPACE-STS2-AUTOTEST/tasks/task-008/runs/run-001/rounds/round-001/nodes/测试/attempt-002/attachments/
```

关键文件：

- `test-report.md`：本阶段正式测试报告
- `pytest-unit.log` / `pytest-targeted.log` / `pytest-baseline.log`：单元测试结果
- `ruff-baseline-clean.log` / `ruff-changed.log` / `ruff-timeout.log`：Ruff 增量基线与超时路径
- `mypy-baseline.log` / `mypy-runner-utils.log` / `mypy-timeout.log`：mypy 增量基线与超时路径
- `lint-imports.log`：导入层级隔离
- `workflow-validate.log`：ci-pr.yml 步骤顺序与 job 超时
- `controlled-timeout.log` / `timeout-message.log`：受控超时验证
- `reproduce-keyboard-interrupt.log` / `reproduce_keyboard_interrupt_bug.py`：KeyboardInterrupt 分支非假测试证据
- `git-status.log`：工作区状态说明
