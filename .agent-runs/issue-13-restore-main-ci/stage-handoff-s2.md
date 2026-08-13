# Stage Handoff — S2 开发 → S3 测试

- task_id: issue-13-restore-main-ci
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/13
- 交接时间：2026-08-13（attempt-003 增量：2026-08-14）
- 下一阶段：测试（local-test-runner）——**第一件事读本文件与 `developer-handoff.md`、`STATE.md`、`task.yaml`**

## 结论

本地开发完成：T1–T3 已交付；attempt-003 完成 runner 环境链实机加固与端到端验证
（代理、tool cache、部署路径、summary 自托管化）；本地门禁全绿；Draft PR #22 已更新。
T4–T6 远程验收待**用户修复 GitHub 计费 + Reviewer 合并后**执行。

## attempt-003 增量要点（2026-08-14）

1. **runner 环境链已实测打通**（探针 run 31718147872，证据 `evidence/runner-probe-20260814.md`）：
   代理（F2）✅ / setup-python 3.11.9 缓存命中（F1 实机化，tool cache 预置于
   `/Users/runner/hostedtoolcache/Python/3.11.9/arm64`）✅ / pip install ✅ / sts2 CLI ✅。
   **T4/T5/T6 的 runner 侧前置条件全部满足。**
2. **关键认知**：job 级 tool cache 路径由 runner 内部机制确定为 `/Users/runner/hostedtoolcache`
   （plist 注入非权威值）；setup-python 缓存未命中时 macOS 流程需要免密 sudo
   （`sudo installer -pkg`）——本机无，已通过缓存预置规避（幂等，无需 sudo）。
3. **仓库改动（commit 6849298）**：ci-main.yml 部署 GAME_DIR 回退路径修正为
   "Slay the Spire 2"（旧路径不存在，会静默部署到幻影目录）；summary job 迁移自托管
   （托管计费故障下主分支验收不再被信息性 job 拖红）；setup-mac-runner.sh 模板同步。
4. **runner 侧**：plist 补代理 env（备份 `*.plist.bak-20260813-before-proxy`，服务已重启生效）。
5. 本地门禁：单测 1757 passed / lint-imports / ruff+mypy baseline 全绿。

## S3 注意（相对上一版变化）

- **计费仍是唯一硬前置**（08-14 15:13 UTC 重跑验证仍阻断托管 job）；自托管 job 不受影响。
- 合入 main 后主分支 workflow 的 quick-checks / cli-integration / deploy 均走自托管，
  前置已就绪；deploy 将部署到真实 mods 目录（路径已修正）。
- `autotest doctor` 在本机报告 steam/disk 不健康——requires_game 类验收仍属环境 BLOCKED，
  但 T4（四项快速验收）/ T5（CLI 集成）/ T6（部署执行）不依赖游戏健康。

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

## ⚠️ 新增外部阻塞（22:55 记录，详见 `evidence/billing-blocker-20260813.md`）

- PR CI run 31712107063（两次 attempt）全部托管 job 3–6 秒即失败、runner 未分配；注解原文：
  「The job was not started because recent account payments have failed or your spending
  limit needs to be increased」。
- 定性：**GitHub 账户计费/支付失败**（用户侧，需在 Settings → Billing & plans 修复），仓库级影响
  所有托管 runner job；非本 PR 改动引起（ci-pr.yml 未改，06:54 UTC 前的运行分配正常）。
- 自托管 runner 不受影响（online）；T5/T6 的自托管 job 无技术阻塞，只等托管前置 job 能跑。

## S3 必做（远程验收，本地不可替代）

1. 复核本地门禁（单测/mypy/ruff 基线/lint-imports）。
2. **先请用户修复 GitHub Billing & plans**（支付失败 / 消费上限）。
3. 计费解除后，对 PR #22 重跑失败 job（`gh run rerun 31712107063 --failed`）验证 PR CI。
4. **评审通过后合并 Draft PR 到 main**，然后触发主分支 workflow：
   - 合入后自然 push 触发；或 `gh workflow run "CI — Push to Main"`（已支持 dispatch，对同一提交可重复重跑）。
5. 确认四项快速验收全绿（lint/mypy/lint-imports/unit-test）。
6. 确认 CLI Integration Tests 实际执行（`-m "not requires_game"`）并通过。
7. 确认 Deploy Gawain Mod 实际执行；独立失败须附证据（不能以跳过冒充）。
8. 若 F2 TLS 再现：单独记录网络失败，重试后继续，不把跳过误判为代码失败。
9. 更新 Issue #13 完成状态，回填运行链接。

## 禁止

- 以本地单次通过替代远程主分支验证。
- 顺手清理历史质量债务（存量 ruff F401×2/F811×1、mypy 15 项为已知债务，不在本任务范围）。
