# STATE — issue-13-restore-main-ci

- 更新时间：2026-08-14（开发 attempt-004 完成）
- 阶段：开发完成（T1–T3 交付 + attempt-003 runner 实机加固 + **attempt-004 实测主分支
  验收链：T4/T5 远程实机全绿，T6 暴露并修复跨仓库 token 缺口**）；Draft PR #22 已更新
  （5 个 commit，最新 e1ac64d）；外部阻塞收窄为：用户配置 GH_PAT（新）+ GitHub 计费（既有）+ 评审合并
- 状态机位置：`DEV_ASSIGNED` →（本地验证通过 + 远程验收链实测 + PR 已更新 + 阻塞已记录）→ `TEST_ASSIGNED`

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/13 ，标签 `bug` + `sized-m` + `ready`。
- 失败运行（修复前基线）：
  - 2026-08-13：https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31672864997 （sha f68d6f2）
  - 2026-08-12：https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31561378957
  - 完整日志已归档：`evidence/run-*.log`；签名：`evidence/failure-signatures.md`（T1 完成）
- 根因（非代码回归）：F1 setup-python tool cache 写 `/Users/runner` 无权限（持久）；F2 ClashX 代理 GitHub 域名 TLS 抖动（间歇）；F3 ci-main.yml 全量 ruff/mypy 与 main 存量债务冲突（静默）。
- 修复进度：
  - ✅ T2 runner tool cache：`RUNNER_TOOL_CACHE` 已写入 runner `.env` 与 launchd plist（均有备份），服务已重启，进程内环境变量生效；runner online。
  - ✅ T3 代理策略：探针证据 `evidence/network-probe-20260813.md`——必须走 ClashX 代理，不加 NO_PROXY。
  - ✅ 仓库改动：ci-main.yml baseline 机制 + `workflow_dispatch` + `[dev,visual]` + summary 自托管化 + **deploy 私有仓库 GH_PAT 预检（e1ac64d）**；setup-mac-runner.sh 同步；测试/类型债务修正；`.gitignore` 补 `/.env`。
  - ✅ **T4 远程实机实证（attempt-004，dispatch 两次全绿）**：lint/mypy/lint-imports/unit-test 在自托管 runner 真实执行并全绿——run 31720176886（c30dcfd）、31721182515（e1ac64d）。
  - ✅ **T5 远程实机实证**：CLI Integration Tests（`-m "not requires_game"`）真实执行并全绿（同上两 run）。
  - ⏳ **T6 实证中，最后一步前置为 GH_PAT**：Deploy 首次实跑暴露私有仓库 checkout 缺口（`Repository not found`）——STS2-GAWAIN 为私有、STS2-AUTOTEST 无跨仓库凭据。已修复：GH_PAT 预检 + checkout 传 token（复跑已验证诊断路径）。**用户配置 GH_PAT 后部署链无需再改代码。**
  - ⏳ 最终主分支 same-commit 验收：需合入 main 后自然 push 触发（届时 T4/T5/T6 应全绿）。
- Draft PR：https://github.com/crystepj-max/STS2-AUTOTEST/pull/22（Draft，MERGEABLE，5 commits：3b0d8da/f0af26a/6849298/c30dcfd/e1ac64d）。
- 授权记录：runner 侧改动（工作目录外）已按 S1 要求先备份后实施（`.env.bak-*`、plist `.bak-*` 系列）。
- **外部阻塞（用户侧，两项）**：
  1. **GH_PAT secret 未配置（attempt-004 新发现）**：Deploy 需要跨仓库访问私有 STS2-GAWAIN；
     需用户在 STS2-AUTOTEST 仓库 secrets 配置 `GH_PAT`（contents:read 权限即可）。
  2. **GitHub 账户计费失败（2026-08-13 22:55 记录，仍存在）**：托管 runner 无法分配
     （注解「recent account payments have failed or your spending limit needs to be increased」）。
     主分支验收链（ci-main.yml）已全部自托管，不受影响；仅影响 PR 检查的托管矩阵 job。

## attempt-004 增量（2026-08-14，开发节点）

- **实测主分支验收链**（此前仅本地证据；本轮 dispatch 两次实跑）：
  - run 31720176886（提交 c30dcfd）：Quick Checks 全绿（lint/mypy/lint-imports/unit-test）+ CLI 集成 ✅ + Deploy ❌（`Repository not found`）→ 暴露跨仓库 token 缺口。
  - run 31721182515（提交 e1ac64d）：Quick Checks 全绿 + CLI 集成 ✅ + Deploy 快速失败（GH_PAT 诊断，符合预期）+ Push Summary ✅。
- **修复（commit e1ac64d）**：ci-main.yml deploy job 新增 `Check cross-repo token (GH_PAT)` 预检步骤 + `Checkout Gawain` 传 `token: ${{ secrets.GH_PAT }}`。本地结构校验通过（YAML 解析 + 断言）。
- 本地门禁复跑：单测 1757 passed / lint-imports 1 kept 0 broken / ruff baseline 0 新增 / mypy baseline 0 新增（另解决 2 项）。

## 下一步

1. **用户侧（T6 最后一步）**：创建对 `crystepj-max/STS2-GAWAIN` 有 `contents:read` 的 PAT，
   配置为 STS2-AUTOTEST 仓库 secret `GH_PAT`。
2. **用户侧（既有）**：修复 GitHub Billing & plans（支付失败 / 消费上限）。
3. **评审侧**：Reviewer 审阅 PR #22（含 attempt-004 增量 e1ac64d），通过后合入 main。
4. 测试阶段（S3）：合入 main 后主分支 workflow 自然触发（或 dispatch）→
   记录四项快速验收 / CLI 集成 / Gawain 部署结果（GH_PAT 配置后预计全绿）→ 更新 Issue 完成状态。
5. 若 F2 再现：单独记录网络失败，不把后续跳过误判为代码失败。
6. 可选（用户侧，长期）：`echo 'chris ALL=(ALL) NOPASSWD: /usr/sbin/installer' | sudo tee /etc/sudoers.d/sts2-runner-installer`
   使 setup-python 免预置也能工作。
