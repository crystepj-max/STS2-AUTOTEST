# 开发阶段真实环境实证 — 2026-08-14（T2 只读 / T3 首条采集 / T4 真实检查）

时间：2026-08-14 13:22 北京时间（05:22Z）。
本文件记录**只读**操作实证；写操作（stop/start、定时部署、演练）待逐项授权后
补充到本目录（drill-*.md / deploy-*.md）。

## T2 实证（只读）：runner-ctl.sh status 反映真实状态

```bash
$ bash scripts/runner-ctl.sh status
---
status actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST:
/Users/chris/Library/LaunchAgents/actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST.plist

Started:
40231 0 actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST
---
state: running
$ echo $?
0
```

对照（三者一致）：
- `launchctl list | grep actions.runner` → `40231 0 actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST`
- `pgrep -fl "Runner.Listener"` → `40235 /Users/chris/actions-runner/bin/Runner.Listener run --startuptype service`
- GitHub 侧：`gh api .../actions/runners` → `Chris-Mac-mini-STS2-AUTOTEST id=21 status=online busy=true`

✅ status 与真实进程/launchd/GitHub 侧一致（Issue 完成标准 1 只读部分）。
stop/start 写操作实证待授权（Issue 完成标准 2）。

## T3 首条采集（真实数据，第 1 条）

```json
{
  "ts": "2026-08-14T05:22:37Z",
  "service_state": "running",
  "runner_pids": "40235",
  "github_online": "online",
  "proxy_local_reachable": true,
  "direct_github_reachable": true,
  "proxy_github_reachable": true,
  "exit_ip_direct": "",
  "exit_ip_proxy": "142.249.36.27"
}
```

观察点：
- 本次直连 api.github.com 可达（issue-13 曾实证直连不可用）——直连/代理可达性
  是动态的，正说明需要 ≥7 天连续采集归因（T3/T7）。
- exit_ip_direct 为空（直连 ipify 5s 超时），proxy 出口 IP 142.249.36.27。

采集频率建议 10 分钟；部署（cron/launchd）待授权。

## T4 真实健康检查

```bash
$ bash scripts/check-runner-health.sh
HEALTHY: runner 可接收任务（service=running, direct=true, proxy=true）
$ echo $?
0
```

✅ 不依赖游戏环境，业务验收前可用/不可用判定可用（Issue 完成标准 5 脚本部分）。
workflow 前置 step 已在 ci-pr.yml 接入，**已在真实 CI 首次执行成功**：

```
run 31772830402（PR #30）：
  step: Runner health precheck (issue-24 T4) | completed | success
```

（run 31772426892 被并发组 cancel——第二次 push 触发新 run，符合 cancel-in-progress
设计，非故障。）

## CI 最终结果（PR #30 run 31772830402）

**conclusion: success**，19/19 step 全绿：

- Runner health precheck (issue-24 T4)：success
- Check no new Ruff debt / Check no new mypy debt：success（与本地 verify.sh 一致）
- Check no new unit-test failures / CLI-only integration：success
- Enforce validation result：success

✅ T4 完成标准「健康检查在验收 workflow 前置实际执行」实证闭环。

## 现场观察：正常排队 vs 异常排队

PR #30 的 run 排队约 10 分钟未领取，初看像 issue-24 症状；核查 GitHub 侧
`busy=true` + Worker 日志 `Worker_20260814-051533-utc.log` → runner 正在执行
main 分支 push 的 job（正常队列行为，非故障）。对应手册第 5 节：
「job 排队但 runner online/busy → 先查 busy，busy=true 属正常排队」。
