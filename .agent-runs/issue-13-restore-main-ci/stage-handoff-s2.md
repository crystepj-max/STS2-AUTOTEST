# Stage Handoff — S2 开发 → S3 测试

- task_id: issue-13-restore-main-ci
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/13
- 交接时间：2026-08-13（attempt-003 增量：2026-08-14；**attempt-004 增量：2026-08-14**）
- 下一阶段：测试（local-test-runner）——**第一件事读本文件与 `developer-handoff.md`、`STATE.md`、`task.yaml`**

## 结论

本地开发完成：T1–T3 已交付；attempt-003 完成 runner 环境链实机加固；
**attempt-004 实测主分支验收链——T4（四项快速检查）/ T5（CLI 集成）在自托管
runner 上真实执行并全绿（dispatch 两次：31720176886、31721182515）；T6（Gawain
部署）首次实跑暴露私有仓库跨仓库 token 缺口并已修复（e1ac64d，GH_PAT 预检 +
token 传递），剩余前置为用户配置 `GH_PAT` secret**。本地门禁全绿；Draft PR #22
已更新至 5 个 commit。最终 same-commit 主分支验收待用户配置 GH_PAT + 修复计费
+ Reviewer 合并后执行。

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

## attempt-004 增量（2026-08-14，本轮开发节点）

1. **T4/T5 已在真实 runner 上实机执行并全绿**（dispatch ci-main.yml @fix 分支）：
   - run 31720176886（提交 c30dcfd）：Quick Checks（lint/mypy/lint-imports/unit-test）
     全绿 + CLI Integration Tests ✅ + Deploy ❌（`Repository not found`）。
   - run 31721182515（提交 e1ac64d）：同上全绿 + Deploy 按预期快速失败（GH_PAT 诊断）+ Push Summary ✅。
2. **T6 暴露并修复真实缺口（commit e1ac64d）**：STS2-GAWAIN 为**私有仓库**，
   STS2-AUTOTEST 无跨仓库凭据（secrets 为空），默认 GITHUB_TOKEN 不可访问。
   Deploy job 已加 `Check cross-repo token (GH_PAT)` 预检 + checkout 传
   `token: ${{ secrets.GH_PAT }}`。**用户配置 GH_PAT 后部署链无需再改代码。**
3. 本地门禁复跑：单测 1757 passed / lint-imports / ruff+mypy baseline 全绿 / ci-main.yml 结构校验通过。

## S3 注意（相对上一版变化）

- **计费仍是硬前置之一**（08-14 15:13 UTC 重跑验证仍阻断托管 job）；自托管 job 不受影响，
  **主分支验收链（ci-main.yml）已全部自托管**，与计费无关。
- **新增硬前置（T6 最后一步）**：用户配置 `GH_PAT` secret（对 crystepj-max/STS2-GAWAIN
  有 `contents:read`）。配置前 Deploy 会快速失败并输出诊断——属预期行为，不算代码失败。
- 合入 main 后主分支 workflow 的 quick-checks / cli-integration / deploy 均走自托管，
  前置已就绪（GH_PAT 除外）；deploy 将部署到真实 mods 目录（路径已修正）。
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

## 本地门禁证据（开发复跑，attempt-004 提交 e1ac64d）

| 检查项 | 结果 | 命令 |
|---|---|---|
| 单元测试 | 1757 passed, 2 warnings（200.46s） | `.venv/bin/python -m pytest tests/unit/ -q` |
| 导入边界 | 1 kept, 0 broken | `.venv/bin/lint-imports` |
| Ruff 基线门禁 | 新增 0（存量 F401×2+F811×1 均在未修改文件） | `.github/scripts/check_ruff_baseline.py`（基线=origin/main f68d6f2） |
| mypy 基线门禁 | 新增 0，另解决 2 项 | `.github/scripts/check_mypy_baseline.py` |
| 脚本 / 工作流语法 | `bash -n` OK；4 个 YAML 解析 OK；ci-main.yml 结构断言通过 | — |
| 远程验收链（自托管） | run 31720176886 / 31721182515：Quick Checks + CLI 集成全绿 | `gh run view` |

## PR

- 分支：`fix/issue-13-restore-main-ci`（基于 origin/main f68d6f2，Draft，MERGEABLE）
- Commits（5）：`3b0d8da`（runner 修复）→ `f0af26a`（计费阻塞记录）→ `6849298`（attempt-003 修正）→ `c30dcfd`（探针证据与交接）→ `e1ac64d`（GH_PAT 预检，attempt-004）
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
2. **请用户配置 `GH_PAT` secret**（对 crystepj-max/STS2-GAWAIN 有 `contents:read`）——
   T6 最后一步前置，配置前 Deploy 按设计快速失败。
3. **请用户修复 GitHub Billing & plans**（支付失败 / 消费上限）——影响 PR 托管 job。
4. 计费解除后，对 PR #22 重跑失败 job 验证 PR CI。
5. **评审通过后合并 Draft PR 到 main**，然后触发主分支 workflow：
   - 合入后自然 push 触发；或 `gh workflow run "CI — Push to Main"`（已支持 dispatch，对同一提交可重复重跑）。
   - **注意**：T4/T5 已在 dispatch 预演中全绿（31720176886 / 31721182515），
     本轮须在**同一主分支提交**上取证；T6 在 GH_PAT 配置后应全绿。
6. 确认四项快速验收全绿（lint/mypy/lint-imports/unit-test）。
7. 确认 CLI Integration Tests 实际执行（`-m "not requires_game"`）并通过。
8. 确认 Deploy Gawain Mod 实际执行；独立失败须附证据（不能以跳过冒充）。
9. 若 F2 TLS 再现：单独记录网络失败，重试后继续，不把跳过误判为代码失败。
10. 更新 Issue #13 完成状态，回填运行链接。

## 禁止

- 以本地单次通过替代远程主分支验证。
- 顺手清理历史质量债务（存量 ruff F401×2/F811×1、mypy 15 项为已知债务，不在本任务范围）。
