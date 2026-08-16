# Developer Handoff: task-008

## 实现摘要

为 CI 中 Ruff / mypy / 单元验证三类外部检查建立独立时间边界（issue #18）：
新增共享工具 `.github/scripts/runner_utils.py`（跨平台带超时子进程执行 + 进程组清理 +
输出保留 + GITHUB_OUTPUT 超时标记），三个 baseline 脚本接入后超时返回退出码 124 并明确
报告 `TIMEOUT`；`ci-pr.yml` 增加 step 级兜底超时、受控超时验证步骤与 summary/enforce 的
Timeout 区分呈现、check 日志 artifact 上传。正常通过路径判定逻辑与退出码语义不变。

## 修改文件

- `.github/scripts/runner_utils.py`（新增）：`run_timed` / `TimedResult` / `timeout_from_env`
- `.github/scripts/check_ruff_baseline.py`：ruff 调用接入 `run_timed`，超时返回 124，日志 `ruff-check.log`，上限 `RUFF_TIMEOUT_SECONDS`（默认 600s）
- `.github/scripts/check_mypy_baseline.py`：mypy 调用接入 `run_timed`，超时返回 124，日志 `mypy-check.log`，上限 `MYPY_TIMEOUT_SECONDS`（默认 900s）；补 `sys` 导入
- `.github/scripts/check_pytest_baseline.py`：`_run_pytest` 改用 `run_timed(echo=True)`，超时返回 124 且不进入 baseline 比较，日志 `pytest-check.log`，上限 `PYTEST_TIMEOUT_SECONDS`（默认 1800s）；移除原 win32 KeyboardInterrupt 特判
- `.github/workflows/ci-pr.yml`：新增「受控超时验证」步骤 + ruff/mypy/unit step 兜底 `timeout-minutes` + summary/enforce Timeout 区分 + check 日志上传
- `.gitignore`：忽略 4 个脚本生成日志
- `tests/unit/test_runner_utils.py`（新增）：9 个测试（正常/超时/无残留/进程组/缺失命令/输出分离/GITHUB_OUTPUT/echo/env）
- `tests/unit/test_ci_pytest_baseline.py`：重写为 mock `run_timed` 的行为测试，新增超时 main 测试

## 使用到的 BaseLib / STS2 API

| API | 用途 | 来源 |
|---|---|---|
| （无） | 本改动仅使用 Python 标准库（subprocess/os/signal/threading/time）+ 项目既有依赖 psutil（测试用） | 项目现有同类实现：`scripts/verify.sh` 的 `run_with_timeout`（先例） |

## Localization 变更

- 无（不涉及 Card / Relic / Power / UI / Tooltip / Event 等游戏内容）
- 检查命令：不适用
- 检查结果：NOT_RUN（不适用）

## 自测命令

```bash
.venv/bin/python -m pytest tests/unit/ -q                        # 全量单元测试
.venv/bin/mypy .github/scripts/runner_utils.py                   # 类型检查（脚本目录非 scope，单独检查）
.venv/bin/ruff check tests/unit/test_runner_utils.py tests/unit/test_ci_pytest_baseline.py .github/scripts/runner_utils.py
.venv/bin/python .github/scripts/check_ruff_baseline.py --baseline-dir /tmp/ci-baseline --current-dir .   # 正常路径
.venv/bin/python .github/scripts/check_mypy_baseline.py --baseline-dir /tmp/ci-baseline --current-dir .   # 正常路径
.venv/bin/python .github/scripts/check_pytest_baseline.py        # 正常路径（JUnit 生成 + baseline 比较）
.venv/bin/lint-imports
BASELINE_DIR=/tmp/ci-baseline ./scripts/verify.sh                # 全量本地验证门禁
# 受控超时验证（脚本级）：
RUFF_TIMEOUT_SECONDS=0.000001 .venv/bin/python .github/scripts/check_ruff_baseline.py --baseline-dir /tmp/ci-baseline --current-dir .   # exit 124
MYPY_TIMEOUT_SECONDS=0.05 .venv/bin/python .github/scripts/check_mypy_baseline.py --baseline-dir /tmp/ci-baseline --current-dir .      # exit 124
PYTEST_TIMEOUT_SECONDS=1 .venv/bin/python .github/scripts/check_pytest_baseline.py                                                     # exit 124
```

## 自测结果

- 单元测试：PASSED（1839 passed, 596.79s；2 个既有 PytestCollectionWarning，非本次引入）
- ruff/mypy 增量基线：PASSED（脚本实测无新增债务，`--baseline-dir /tmp/ci-baseline`）
- lint-imports：PASSED（Contracts: 1 kept, 0 broken）
- mypy（新模块单独检查）：PASSED（Success: no issues found）
- verify.sh：PASSED（全绿后建 Draft PR）
- 超时路径实测：ruff/mypy/pytest 均 `TIMEOUT: ...` + exit=124；子进程终止且无残留（psutil 断言）；部分输出保留于日志
- Build：PASSED（项目无构建步骤，editable 安装；以单元测试 + lint-imports + verify.sh 为等价门禁）
- Localization Check：NOT_RUN（不适用，无游戏内容改动）
- Smoke Test：NOT_RUN（不适用，CI 脚本改动；受控超时验证在 CI 中随 PR 运行）

## 已知风险

- Windows 进程组终止为尽力而为（CTRL_BREAK_EVENT 需子进程配合）；CI 主平台 macOS，POSIX 路径已实测。
- pytest 超时中断时 `junit-unit.xml` 可能缺失/部分：脚本不解析、明确报 Timeout，部分 JUnit 随 artifact 保留。
- ruff/mypy 脚本「工具运行失败」异常由 `CalledProcessError` 改为 `RuntimeError`（同为基础设施错误，信息更完整）。
- `check_pytest_baseline.py` 移除 win32 KeyboardInterrupt 特判：交互中断时由 `run_timed` 清理子进程后传播（行为更干净；CI 无交互中断场景）。
- step `timeout-minutes` 取值（ruff 25 / mypy 35 / unit 35 分钟）按脚本最坏耗时（baseline+current 各一次）留余量，保证脚本先报 Timeout。

## 建议 Reviewer 重点检查

- `runner_utils.run_timed` 终止时序：deadline 轮询 → SIGTERM 进程组 → 5s 宽限 → SIGKILL 进程组 → 再 5s 宽限；KeyboardInterrupt 清理路径。
- GITHUB_OUTPUT `timeout=true` 标记与 workflow summary/enforce 的联动（`steps.<id>.outputs.timeout`）。
- 正常路径零回归：baseline 比较逻辑与退出码语义（ruff 0/1、mypy 0/1/2、pytest 0/1/2）未变。
- 受控超时验证步骤的断言强度（timed_out + psutil 无残留）。
- `tests/unit/test_ci_pytest_baseline.py` 的重写是否完整覆盖原测试意图（原 5 个测试中 3 个 KeyboardInterrupt 场景随 win32 特判移除）。

---

## 复审修复轮（2026-08-16，提交 75168b1，已推送）

S4 审核 REQUEST_CHANGES 修复：全部审核项已处理并本地验证全绿，交接见
`stage-handoff-s2-fix.md`，开发报告见 Gold Band round-001/开发/attempt-002/attachments/dev-report.md。

| 项 | 修复 |
|----|------|
| HIGH：check-logs 上传早于 mypy | `Upload check logs` 移至 mypy 步骤之后、Generate summary 之前 |
| MEDIUM：job 45min < step 兜底之和 | `timeout-minutes: 45 → 120` |
| MEDIUM：KeyboardInterrupt 分支零覆盖 | 新增 spy 测试；**测试首跑抓到真实缺陷**（中断路径未 `proc.wait()` 回收 → 子进程僵尸残留），已补 wait |
| LOW ×3 | `{timeout:g}` 亚秒格式；join 超时 truncation 留痕；sys.modules 约束注释 |
| 流程 | S3 遗留的 ruff I001 修复随本轮提交 |

修复轮自测：全量单元测试 **1840 passed**（= 1839 + 新增测试），ruff/mypy 增量门禁
New=0，lint-imports 通过，workflow 结构校验通过。PR #39 已推送，CI run 即集成验证载体。
