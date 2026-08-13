# STATE — issue-13-restore-main-ci

- 更新时间：2026-08-14 00:0x（开发 attempt-003 完成）
- 阶段：开发完成（T1–T3 交付 + attempt-003 实机加固：代理注入、tool cache 预置、部署路径修正、summary 迁移自托管）；Draft PR #22 已更新（3 个 commit）；**外部阻塞仍在：GitHub 账户计费失败**（详见 `evidence/billing-blocker-20260813.md`），托管 runner 不可用；自托管 runner 环境链已实测打通
- 状态机位置：`DEV_ASSIGNED` →（本地验证通过 + PR 已建 + 外部阻塞已记录）→ `TEST_ASSIGNED`

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/13 ，标签 `bug` + `sized-m`。
- 失败运行（修复前基线）：
  - 2026-08-13：https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31672864997 （sha f68d6f2）
  - 2026-08-12：https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31561378957
  - 完整日志已归档：`evidence/run-*.log`；签名：`evidence/failure-signatures.md`（T1 完成）
- 根因（非代码回归）：F1 setup-python tool cache 写 `/Users/runner` 无权限（持久）；F2 ClashX 代理 GitHub 域名 TLS 抖动（间歇）；F3 ci-main.yml 全量 ruff/mypy 与 main 存量债务冲突（静默）。
- 修复进度：
  - ✅ T2 runner tool cache：`RUNNER_TOOL_CACHE=/Users/chris/actions-runner/_work/_tool` 已写入 runner `.env` 与 launchd plist（均有备份），服务 21:47:58 重启，`ps eww` 确认进程内环境变量生效；runner online。
  - ✅ T3 代理策略：探针证据 `evidence/network-probe-20260813.md`——必须走 ClashX 代理（3/3 成功），直连不可用（2/2 超时）；不加 NO_PROXY。
  - ✅ 仓库改动：ci-main.yml baseline 机制 + `workflow_dispatch` + `[dev,visual]`；setup-mac-runner.sh 同步；测试/类型债务修正；`.gitignore` 补 `/.env`。
  - ⏳ T4 主分支重跑四项快速验收、T5 CLI 集成、T6 Gawain 部署：需合入 main 后在自托管 runner 上执行（本地无法替代，本机缺 Godot/Steam 游戏目录）。
- Draft PR：https://github.com/crystepj-max/STS2-AUTOTEST/pull/22（Draft，MERGEABLE；含 attempt-003 增量 commit 6849298 等）。
- 授权记录：runner 侧改动（工作目录外）已按 S1 要求先备份后实施（`.env.bak-20260813-before-fix`、`.env.bak-20260813-with-noproxy`、plist `.bak-20260813`、plist `.bak-20260813-before-proxy`）。
- **外部阻塞（2026-08-13 22:55 记录，08-14 仍存在）**：PR CI run 31712107063 全部托管 job 注解
  「recent account payments have failed or your spending limit needs to be increased」——
  GitHub 账户计费失败，托管 runner 无法分配；08-14 15:13 UTC 重跑同败（run 31712546623 rerun）。
  （自托管 runner 不受影响，仍在线。）

## attempt-003 增量（2026-08-14，开发节点）

- **runner 环境链已实测打通**（探针证据 `evidence/runner-probe-20260814.md`）：
  代理（plist 注入，F2 实机化）✅ / setup-python 3.11.9 缓存命中（tool cache 预置于
  `/Users/runner/hostedtoolcache/Python/3.11.9/arm64`，F1 实机化）✅ / pip install ✅ /
  sts2 CLI ✅ / doctor 正常执行（环境健康失败：steam/disk，属环境限制）。
- **关键新认知**：job 级 tool cache 路径由 runner 内部机制确定为
  `/Users/runner/hostedtoolcache`（plist 注入非权威值）；setup-python 缓存未命中时
  macOS 流程需要免密 sudo（`sudo installer -pkg`），本机无 → 必须以缓存预置规避。
- **仓库改动**：ci-main.yml 部署路径修正（"Slay the Spire 2"）+ summary 迁移自托管；
  setup-mac-runner.sh 模板同步（路径 + 代理变量）。
- 本地门禁复跑：单测 1757 passed / lint-imports / ruff+mypy baseline 全绿。

## 下一步

1. **用户侧**：修复 GitHub Billing & plans（支付失败 / 消费上限）。
2. **评审侧**：Reviewer 审阅 PR #22（含 attempt-003 增量），通过后合入 main。
3. 测试阶段（S3）：合入 main 后触发主分支 workflow（自然 push 或 `workflow_dispatch`）→
   记录四项快速验收 / CLI 集成 / Gawain 部署结果（runner 侧前置已就绪）→ 更新 Issue 完成状态。
4. 若 F2 再现：单独记录网络失败，不把后续跳过误判为代码失败。
5. 可选（用户侧，长期）：`echo 'chris ALL=(ALL) NOPASSWD: /usr/sbin/installer' | sudo tee /etc/sudoers.d/sts2-runner-installer`
   使 setup-python 免预置也能工作（与托管机 runner 用户行为一致）。
