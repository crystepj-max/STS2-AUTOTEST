# Stage Handoff — S2 开发 → S3 测试

- task_id: issue-13-restore-main-ci
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/13
- 交接时间：2026-08-13
- 下一阶段：测试（local-test-runner）——**第一件事读本文件与 `developer-handoff.md`、`STATE.md`、`task.yaml`**

## 结论

本地开发完成：T1–T3 已交付且 runner 环境修复已实机生效；本地门禁全绿；Draft PR 已建。T4–T6 远程验收待执行。

## 上一轮（测试 attempt-001）BLOCKED 的解除情况

| 上轮阻塞点 | 本轮状态 |
|---|---|
| Developer Handoff 缺失 | ✅ 已补齐：`.agent-runs/issue-13-restore-main-ci/developer-handoff.md` |
| runner 环境未验证 | ✅ T2/T3 已实机应用：`.env` + plist 双写 `RUNNER_TOOL_CACHE`（有备份），服务已重启，`ps eww` 确认进程内生效；runner online；代理策略经探针确认（必须走 ClashX，不加 NO_PROXY） |
| 无法触发主分支运行 | ✅ ci-main.yml 新增 `workflow_dispatch`——合入后可对同一主分支提交手动重跑（dispatch 时基线=当前提交，0 diff 通过） |
| 无本轮提交运行链接 | ⏳ 仍需合入 main 后触发（push 或 dispatch） |

## 必读（按序）

1. `.agent-runs/issue-13-restore-main-ci/developer-handoff.md`（实现摘要、修改文件、风险、Reviewer 检查项）
2. `.agent-runs/issue-13-restore-main-ci/STATE.md`（进度与授权记录）
3. `.agent-runs/issue-13-restore-main-ci/task.yaml`（T1–T7、quality_gates）
4. Draft PR：见下方「PR」

## 本地门禁证据（开发复跑）

| 检查项 | 结果 | 命令 |
|---|---|---|
| 单元测试 | 1757 passed, 2 warnings（225.84s） | `.venv/bin/python -m pytest tests/unit/ -q` |
| 导入边界 | 1 kept, 0 broken | `.venv/bin/lint-imports` |
| Ruff 基线门禁 | 新增 0（存量 F401×2+F811×1 均在未修改文件） | `.github/scripts/check_ruff_baseline.py`（基线=origin/main f68d6f2） |
| mypy 基线门禁 | 新增 0，另修复 2 项 | `.github/scripts/check_mypy_baseline.py` |
| 脚本 / 工作流语法 | `bash -n` OK；4 个 YAML 解析 OK | — |
| runner 环境 | RUNNER_TOOL_CACHE 生效，runner online | `ps eww` + gh api runners |

## PR

- 分支：`fix/issue-13-restore-main-ci`（基于 origin/main f68d6f2，Draft）
- 变更内容：见 `developer-handoff.md`「修改文件」

## S3 必做（远程验收，本地不可替代）

1. 复核本地门禁（单测/mypy/ruff 基线/lint-imports）。
2. **先合并 Draft PR（或经评审后合并）到 main**，然后在自托管 runner 上触发主分支 workflow：
   - 合入后自然 push 触发；或 `gh workflow run "CI — Push to Main"`（已支持 dispatch，对同一提交可重复重跑）。
3. 确认四项快速验收全绿（lint/mypy/lint-imports/unit-test）。
4. 确认 CLI Integration Tests 实际执行（`-m "not requires_game"`）并通过。
5. 确认 Deploy Gawain Mod 实际执行；独立失败须附证据（不能以跳过冒充）。
6. 若 F2 TLS 再现：单独记录网络失败，重试后继续，不把跳过误判为代码失败。
7. 更新 Issue #13 完成状态，回填运行链接。

## 禁止

- 以本地单次通过替代远程主分支验证。
- 顺手清理历史质量债务（存量 ruff F401×2/F811×1、mypy 15 项为已知债务，不在本任务范围）。
