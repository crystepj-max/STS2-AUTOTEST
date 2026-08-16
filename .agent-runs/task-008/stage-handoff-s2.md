# Stage Handoff — S2 开发

**任务**：task-008 / issue #18 为自动验收中的外部任务建立明确时间边界  
**时间**：2026-08-16  
**来源节点**：S2 开发  
**目标节点**：S3 测试

## 输入

- S1 调度产出：`task.yaml`、`STATE.md`、`stage-handoff-s1.md`、`dispatch-result.json`
- GitHub Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/18

## 开发结论

- **分支**：`bugfix/issue-18-timeout-boundaries`（基线 main），所有改动仅在该分支提交。
- **T1–T5 全部完成**（TDD：先写失败测试再实现）。
- 正常路径零回归：baseline 比较逻辑与退出码语义未变（ruff 0/1、mypy 0/1/2、pytest 0/1/2）。

## 人工门禁「需求确认」三项落定（S2 决策）

1. **时间上限**：Ruff 600s / mypy 900s / pytest 1800s；环境变量
   `RUFF/MYPY/PYTEST_TIMEOUT_SECONDS` 可覆盖（受控验证用，CI 不设置时用默认值）。
2. **受控超时验证方式**：三管齐下——① `tests/unit/test_runner_utils.py` 超时场景；
   ② CI 固定「Controlled timeout verification」步骤（2s 上限杀 300s sleep，
   psutil 断言无残留，每 PR 必跑）；③ 脚本级环境变量注入实测（开发时完成）。
3. **工具模块位置与命名**：`.github/scripts/runner_utils.py`（与 baseline 脚本同目录，
   测试经 importlib 加载，sys.path 注入同目录）。

## 实现内容摘要

- 新增 `runner_utils.py`：`run_timed`（独立进程组 + 超时 terminate→grace→kill 双级降级 +
  增量日志 + 可选回显 + GITHUB_OUTPUT `timeout=true` 标记）、`TimedResult`、
  `timeout_from_env`、`TIMEOUT_EXIT_CODE=124`。
- 三个 baseline 脚本接入：超时 → stderr `TIMEOUT: ...` + exit 124 + 日志保留
  （ruff-check.log / mypy-check.log / pytest-check.log）。
- `ci-pr.yml`：ruff/mypy/unit step 兜底 `timeout-minutes`（25/35/35，≥ 脚本最坏耗时）；
  受控超时验证步骤；summary/enforce 区分 `Timeout (124)`；check 日志 artifact 上传。
- `.gitignore` 忽略 4 个日志文件。

## 自测结果（本地）

| 项 | 结果 |
|----|------|
| 单元测试全量 | PASSED（1839 passed, 596.79s） |
| ruff/mypy 增量基线 | PASSED（脚本实测无新增债务） |
| lint-imports | PASSED（1 kept, 0 broken） |
| mypy（新模块单独） | PASSED |
| pytest baseline 正常路径 | PASSED（exit 0，JUnit 229KB，New failures 0） |
| 超时路径实测 | PASSED（ruff/mypy/pytest 均 TIMEOUT + exit 124 + 无残留） |
| verify.sh | PASSED（全部 5 步通过，exit 0） |

## 风险与注意事项（转交 S3/S4）

- Windows 进程组清理为尽力而为（CI 主平台 macOS，POSIX 已实测含孙子进程）。
- pytest 超时中断时 junit-unit.xml 可能缺失/部分：脚本明确报 Timeout 不崩溃，部分产物随 artifact 保留。
- `check_pytest_baseline.py` 移除 win32 KeyboardInterrupt 特判（中断时由 run_timed 清理后传播）。
- 本地单元运行 5 个历史失败被解析（mcp_tools/smoke_card_validation），非本次引入。

## 下一步动作（S3 测试）

1. 推送分支并建 **Draft PR**（PR CI run 即集成验证载体）。
2. 核对 CI run：受控超时验证步骤 OK、ruff/mypy/unit 正常路径、summary 呈现无 Timeout 误报。
3. 按验收标准 6 条核对并产出测试报告；必要时在 CI 上做一次真实受控超时演练。
