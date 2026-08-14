# STATE — issue-24-mac-runner-maintainability

- 更新时间：2026-08-14（返工完成 + 远端 CI 全绿，待 Reviewer 重审）
- 阶段：T1 取证已归档；T2/T3(脚本)/T4/T5 开发完成；
  S4 审核 REQUEST_CHANGES → S2 返工完成（提交 e603b75 → 723089f 等 10 提交）；
  远端 CI run 31792626355 SUCCESS（健康检查前置 + 45 脚本测试 + 单测 + 门禁全绿）。
- 状态机位置：`DEV_ASSIGNED` →（返工完成 + CI 全绿，待 Reviewer 重审）→ `REVIEW`

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

## 待办（授权/时间门禁，不在返工范围）

1. **T2 实证**（需授权）：真实机器 `runner-ctl.sh stop` → 确认 GitHub 侧
   不再领取 job；`start` → 确认进程更新且可接收。记录到 evidence/。
2. **T3 部署**（需授权）：定时探针部署（cron/launchd，建议 10 分钟间隔），
   JSONL 落盘 `~/.sts2-runner-probe/`，连续采集 ≥7 天（最快 2026-08-21）。
3. **T6 演练**（需授权）：按 docs/runner-runbook.md 第 7 节做恢复演练，
   记录到 evidence/drill-YYYYMMDD.md。
4. **T7 收口**（依赖 T3 满 7 天）：四类归因 → 代理决策记录 → 回填 Issue
   完成标准 → 关闭任务。

## 下一步

1. 全量验证（verify.sh）→ push PR #30 → 远端 CI（含脚本测试 step）绿灯。
2. Reviewer 重审（gate：reviewer_approved）→ 通过后进入人工门禁
   （用户验收 + 合并授权）。
3. 人工确认授权项（T2 实证 / T3 部署 / T6 演练）后逐项执行。
4. T3 满 7 天（2026-08-21 后）执行 T7 收口。
