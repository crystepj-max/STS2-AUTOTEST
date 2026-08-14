# STATE — issue-24-mac-runner-maintainability

- 更新时间：2026-08-14（已合并；T2 完成；T3 采集中；T6 部分完成；T7 待 8/21）
- 阶段：T1 取证已归档；T2/T3(脚本)/T4/T5 开发完成；S4 返工完成；
  PR #30 已合并（f1c1ab3，2026-08-14）；T2 实证完成；
  T3 探针已部署、连续采集中（完整四类探针 2026-08-14 21:05 UTC 部署，2026-08-21 21:05 满 7 天，**满 7 天前不视为完成**）；
  T6 演练流程已执行（**但 task.yaml 门禁要求「非原排障人员」独立执行，
  当前由原排障者演练，门禁未满足**）；
  T7 归因待 8/21 定时检视。
- 状态机位置：`REVIEW` →（用户验收通过 + 合并授权 + 已合并）→ T3 采集中 → T6 待独立执行 → 待 T7 收口关闭

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/24 ，
  标签 `bug` + `sized-m`；`ready` 待人工门禁通过后补打。
- 三个独立失败源与真实安装基线：见 task.yaml `triage` 节与
  `evidence/BASELINE-20260814.md`（T1 已归档，提交 a271d70）。
- 授权边界（用户确认）：工作目录外改动（`~/actions-runner`、
  `~/Library/LaunchAgents`、服务重启）逐项二次确认，先备份、留命令记录。
- **表述更正**：此前本地验证结果与远端 PR 绿灯分开表述；「本地工作区通过」
  不等于「远端 PR 已通过」。远端 PR 绿灯是否覆盖脚本测试以 CI 配置为准。

## 交付物（S1 + 开发阶段）

- task.yaml / STATE.md / stage-handoff-s1.md（S1）
- `evidence/`（T1 基线 4 份，提交 a271d70）
- `scripts/runner-ctl.sh`（T2 统一状态/停止/启动入口）
- `scripts/setup-mac-runner.sh`（T2 重写，F1 漂移修复，幂等）
- `scripts/runner-probe.sh`（T3 采集探针，JSONL）
- `scripts/check-runner-health.sh`（T4 健康检查，0=可用 1=不可用）
- `.github/workflows/ci-pr.yml`（T4 前置 step + 脚本测试必跑 step）
- `docs/runner-runbook.md`（T5 手册）
- `scripts/verify.sh`（本地全量验证入口）
- `scripts/tests/`（4 套 shell 测试：runner-ctl / setup / probe / health）
- `developer-handoff.md`（本文件同目录，开发交接）

## 返工修正（S4 REQUEST_CHANGES → S2 返工，2026-08-14）

审核 7 项阻塞（详见 `review-report.md` 或 stage-handoff-s4.md），返工修正如下：

1. **R1 直连绕过代理**：`runner-probe.sh` 直连探测强制 `--noproxy '*'`，
   新增反例测试（代理环境存在时直连仍走代理 → 测试失败，修复后通过）。
2. **R2 事件归因字段**：探针新增 `github_busy`（任务领取/忙闲）、`op`
   （维护操作标记，PROBE_OP 注入）、`transition`（disconnect/recover/
   service-stopped/service-started，状态文件推导）。
3. **R3 真实状态核验**：`check-runner-health.sh` 与 `runner-ctl.sh status`
   核对 Runner.Listener 进程与 GitHub 侧 online 状态；新增反例测试
   （服务标记 started 但进程缺失 / GitHub offline → UNHEALTHY）。
4. **S1 外部操作超时**：`runner-probe.sh`、`check-runner-health.sh`、
   `runner-ctl.sh`、`setup-mac-runner.sh`、`verify.sh` 全部外部调用逐项
   带超时（挂起替身验证限时退出）。
5. **S2 进程/临时文件回收**：`run_with_timeout` 递归终止进程树
   （kill_tree），临时文件 EXIT trap 清理；超时后无残留子进程测试通过。
6. **R4/S3 新安装路径**：`setup-mac-runner.sh` 机器身份必须显式传入
   （RUNNER_NAME，不再固定机器名）；同名已注册默认拒绝覆盖
   （需 ALLOW_REPLACE=1）；装后写入运行环境（HTTP_PROXY/HTTPS_PROXY 到
   .env）并 svc.sh install → start → status 验证 Started。
7. **CI 门禁**：`ci-pr.yml` 新增 `scripts/tests/run-all.sh` 必跑 step，
   纳入 summary 与 enforce 检查。
8. **表述更正**：本 STATE.md 与 developer-handoff.md 不再把本地绿灯
   写成「远端 PR 已通过」。

返工后本地脚本测试 40 用例（probe 12 / ctl 12 / health 9 / setup 7）全过，
等待全量重验与 Reviewer 重审。

## 授权项执行情况（2026-08-14 松哥已授权）

1. **T2 实证：✅ 完成** → evidence/verification-t2-20260814.md
   （stop → GitHub 侧 offline 不可接收；start → 新进程 + online 可接收）。
2. **T3 部署：✅ 已部署，⏳ 采集中（未完成）** → evidence/deployment-t3-20260814.md
   （launchd `com.sts2.autotest.runner-probe` 每 10 分钟，JSONL 落盘
   `~/.sts2-runner-probe/`；**连续采集满 7 天（2026-08-21 21:05 UTC）才算 T3 完成**）。
3. **T6 演练：⚠️ 部分完成（流程已演练，门禁未满足）** → evidence/drill-20260814.md
   （stop→offline→start→online→真实 CI 领取执行成功；但 task.yaml 门禁
   `runbook_drill_passed` 要求**非原排障人员**按手册独立完成演练，
   当前由原排障者（收口 Agent）执行，**需另请独立执行者按手册第 7 节演练**）。
4. **T7 收口：⏳ 待 8/21**——定时任务已设（**2026-08-21 21:30，本地时区，
   晚于 21:05 UTC 满 7 天**，持久化 CronCreate），检视 T2/T3/T6/T7 结果后
   决策是否关闭 Issue。定时任务本身在满 7 天门禁之后触发，不会提前误判。

## 下一步（关闭前置条件）

1. T3 数据连续采集至 2026-08-21 满 7 天（未满不视为完成）。
2. **T6 独立演练**：另请**非原排障人员**按手册第 7 节完成演练并落盘证据
   （`runbook_drill_passed` 门禁；当前原排障者演练不满足）。
3. 8/21 定时任务触发：检视 T2（已完成）/T3（满 7 天）/T6（独立演练）/T7 结果。
4. **全部前置满足后**（T3 满 7 天 + T6 独立演练通过）：四类归因 → 代理决策记录
   → 回填 Issue #24 → 关闭任务。任一前置未满足不得关闭。
