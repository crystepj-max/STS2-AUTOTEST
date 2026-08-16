# Stage Handoff — S2 复审修复轮

**任务**：task-008 / issue #18 为自动验收中的外部任务建立明确时间边界  
**时间**：2026-08-16  
**来源节点**：S2 开发（修复轮，round-001/开发/attempt-002）  
**目标节点**：S4 审核（复审）

## 输入

- S4 审核结果：`stage-handoff-s4.md`（REQUEST_CHANGES，1 HIGH + 2 MEDIUM + 3 LOW + 1 流程项）
- 审核报告全文：Gold Band round-001/审核/attempt-001/attachments/review-report.md

## 修复结论

**全部审核项已修复并本地验证全绿**，提交 `75168b1` 已推送（edd4437..75168b1），
PR #39 已自动触发新一轮 CI run（即受控超时验证的集成载体）。

| # | 级别 | 问题 | 修复方式 | 验证 |
|---|------|------|----------|------|
| 1 | HIGH | `Upload check logs` 早于 mypy 步骤，mypy-check.log 永不进 artifact | 上传步骤移至 `Check no new mypy debt` 之后、`Generate summary` 之前 | YAML 结构校验：upload(16) > mypy(15) < summary(17) |
| 2 | MEDIUM | job 级 45min < step 兜底之和（25+35+35=95min） | `timeout-minutes: 45 → 120`（含注释） | YAML 校验 timeout=120 |
| 3 | MEDIUM | `run_timed` KeyboardInterrupt 分支零覆盖 | 新增 spy 测试：代理 time 模块模拟中断，断言 `_kill_tree` 被调用 + 异常传播 + psutil 无残留 | **测试首跑抓到真实缺陷**：中断分支未 `proc.wait()` 回收，子进程僵尸残留；补 wait 后通过 |
| 4 | LOW | 亚秒超时消息显示 `0s` | `{timeout:.0f}s → {timeout:g}s` | 实测 `exceeded 1e-06s` / `exceeded 1s` |
| 5 | LOW | join 超时静默截断输出 | 捕获线程 join 超时后日志写 `output truncated` 提示 | 单元测试全绿 |
| 6 | LOW | 测试文件 import 期改 `sys.modules` | 两测试文件补常驻约束注释 | 单元测试全绿 |
| 7 | 流程 | ruff I001 修复未提交（S3 遗留） | 随修复轮一并提交 | ruff 增量门禁 New=0 |

## 自测结果（本地，修复轮）

| 项 | 结果 |
|----|------|
| targeted 单元测试 | PASSED（14 passed） |
| 全量单元测试 | PASSED（**1840** passed, 576.50s, 2 个既有 warning；pytest 增量门禁 exit 0） |
| ruff 增量基线 | PASSED（`New in this PR: 0`，exit 0） |
| mypy（runner_utils） | PASSED（no issues） |
| lint-imports | PASSED（1 kept, 0 broken） |
| workflow 结构校验 | PASSED（步骤顺序 + job timeout 120） |
| 超时消息格式 | PASSED（1e-06s / 1s） |

注：1840 = S3 的 1839 + 新增 KeyboardInterrupt 测试。

## 修复过程中的新发现（重要）

- **KeyboardInterrupt 僵尸进程缺陷**（审核 MEDIUM #2 的门禁追问直接验证）：
  原实现中断路径 `_kill_tree` 后未 `proc.wait()`，被 SIGKILL 的子进程成为僵尸停留在
  进程表中（psutil.pid_exists 仍为 True）。已补 wait 回收（SIGKILL 不可忽略，
  wait 仅用于回收，5s 宽限）。
- EXE001（`.github/scripts` 脚本 shebang 非可执行）为 **main 既有债务**：ruff 门禁
  仅扫 `src tests`，不在门禁范围，main 上同样存在；按 task.yaml「不做」范围未处理。

## 风险与注意事项（转交 S4 复审）

- KeyboardInterrupt 路径 wait 超时极端情形仍可能留僵尸（SIGKILL 不可忽略，概率极低）。
- job 级 120min 兜底放大了无 step 超时步骤（install/lint-imports/script_tests/
  cli_tests）的最坏挂起时间——「step 先报 Timeout、日志可保留」优先级的合理代价。
- 其余 S2 原风险（Windows 进程组尽力而为、pytest 超时 JUnit 部分缺失）不变。

## 下一步动作（S4 复审）

1. 核对 PR #39 新一轮 CI run：受控超时验证 OK、ruff/mypy/unit 正常路径、summary 呈现。
2. 按验收标准 6 条复审，重点核对第 4 条（mypy 日志随 artifact 保留）。
3. 满意后合并或转人工验收。
