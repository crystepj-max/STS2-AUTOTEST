# STATE — issue-24-mac-runner-maintainability

- 更新时间：2026-08-14（开发阶段 S2 进行中）
- 阶段：T1 取证已完成归档；T2/T3(脚本)/T4/T5 开发完成并自测；
  T3 部署 / T6 演练 / T7 收口待授权与 7 天数据
- 状态机位置：`DEV_ASSIGNED` →（开发完成，待 Reviewer）→ `REVIEW`

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/24 ，
  标签 `bug` + `sized-m`；`ready` 待人工门禁通过后补打。
- 三个独立失败源与真实安装基线：见 task.yaml `triage` 节与
  `evidence/BASELINE-20260814.md`（T1 已归档，提交 a271d70）。
- 授权边界（用户确认）：工作目录外改动（`~/actions-runner`、
  `~/Library/LaunchAgents`、服务重启）逐项二次确认，先备份、留命令记录。

## 交付物（S1 + 开发阶段）

- task.yaml / STATE.md / stage-handoff-s1.md（S1）
- `evidence/`（T1 基线 4 份，提交 a271d70）
- `scripts/runner-ctl.sh`（T2 统一状态/停止/启动入口）
- `scripts/setup-mac-runner.sh`（T2 重写，F1 漂移修复，幂等）
- `scripts/runner-probe.sh`（T3 采集探针，JSONL）
- `scripts/check-runner-health.sh`（T4 健康检查，0=可用 1=不可用）
- `.github/workflows/ci-pr.yml`（T4 前置 step：Runner health precheck）
- `docs/runner-runbook.md`（T5 手册）
- `scripts/verify.sh`（本地全量验证入口）
- `scripts/tests/`（4 套 shell 测试：runner-ctl 9 / setup 6 / probe 5 / health 5）
- `developer-handoff.md`（本文件同目录，开发交接）

## 自测状态（2026-08-14）

- shell 测试 4 套：**全绿**（`bash scripts/tests/run-all.sh`）
- lint-imports：**PASSED**（Contracts: 1 kept, 0 broken）
- unit 测试：运行中（首次后台运行被本机真实 CI job 并发干扰中断，
  待 CI job 结束后重跑，见 `scripts/verify.sh` 输出）
- mypy/ruff：未全量跑（仓库存在既有债务，归属 Issue #25；CI 有增量基线门禁）

## 待办（开发阶段未完成，交接给后续）

1. **T2 实证**（需授权）：真实机器 `runner-ctl.sh stop` → 确认 GitHub 侧
   不再领取 job；`start` → 确认进程更新且可接收。记录到 evidence/。
2. **T3 部署**（需授权）：定时探针部署（cron/launchd，建议 10 分钟间隔），
   JSONL 落盘 `~/.sts2-runner-probe/`，连续采集 ≥7 天（最快 2026-08-21）。
3. **T6 演练**（需授权）：按 docs/runner-runbook.md 第 7 节做恢复演练，
   记录到 evidence/drill-YYYYMMDD.md。
4. **T7 收口**（依赖 T3 满 7 天）：四类归因 → 代理决策记录 → 回填 Issue
   完成标准 → 关闭任务。

## 下一步

1. Reviewer 审查（gate：reviewer_approved）→ 修正或合并。
2. 人工确认授权项（T2 实证 / T3 部署 / T6 演练）后逐项执行。
3. T3 满 7 天（2026-08-21 后）执行 T7 收口。
