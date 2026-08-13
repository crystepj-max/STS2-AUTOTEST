# STATE — issue-13-restore-main-ci

- 更新时间：2026-08-13 22:50（开发 S2 attempt-002）
- 阶段：开发完成（T1–T3 已交付），Draft PR 已创建；等待测试阶段（S3）远程验收 T4–T6
- 状态机位置：`DEV_ASSIGNED` →（本地验证通过 + PR 已建）→ `TEST_ASSIGNED`

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
- Draft PR：见开发阶段产物（链接在 `stage-handoff-s2.md`）。
- 授权记录：runner 侧改动（工作目录外）已按 S1 要求先备份后实施（`.env.bak-20260813-before-fix`、`.env.bak-20260813-with-noproxy`、plist `.bak-20260813`）。

## 下一步

1. 测试阶段（S3）：复核本地门禁 → 在真实 runner 上触发主分支 workflow（合入或 dispatch）→ 记录四项快速验收 / CLI 集成 / Gawain 部署结果 → 更新 Issue 完成状态。
2. 若 F2 再现：单独记录网络失败，不把后续跳过误判为代码失败。
