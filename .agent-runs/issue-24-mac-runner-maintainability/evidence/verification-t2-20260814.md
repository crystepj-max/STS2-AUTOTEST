# T2 实证记录：真实 stop/start 与任务接收（2026-08-14）

- 授权：松哥 2026-08-14 授权（收口节点人工门禁）
- 目的：验证「状态显示与实际后台进程一致」「停止后不可接收任务」「启动后进程更新且可接收」

## 基线

- `runner-ctl.sh status` → state: running（EXIT 0），svc.sh Started
- GitHub 侧：status=online busy=true（CI 执行中）
- 进程：Runner.Listener PID 62928

## stop 实证

```bash
bash scripts/runner-ctl.sh stop
```

| 检查 | 结果 |
|---|---|
| 本地状态 | state: stopped（EXIT 1），svc.sh 输出 Stopped |
| 进程 | Runner.Listener 消失 |
| GitHub 侧（stop 后立即） | online busy=true（刷新延迟） |
| GitHub 侧（stop 90s 后） | **status=offline** ✅ |
| CI job 行为 | 排队中的 run 不再被领取（queued 停滞）✅ |

**结论：停止后确实不可接收任务。**

## start 实证

```bash
bash scripts/runner-ctl.sh start
```

| 检查 | 结果 |
|---|---|
| 本地状态 | state: running（EXIT 0），svc.sh Started |
| 进程 | **新 PID 1989**（launchd PID 1981）✅ 后台进程已更新 |
| GitHub 侧（start 60s 后） | **status=online** ✅ |
| 健康检查 | healthy=true（service/process/github 三路一致）✅ |

**结论：启动后后台进程已更新且可接收任务。**

## 额外发现

- GitHub 侧 online/offline 状态刷新有约 60–90 秒延迟（心跳超时），属预期行为。
- runner 停止期间排队的 job 消息可能失效（GitHub 侧 `job assignment is invalid`），
  取消重触发即可；探针/健康检查不依赖该状态。

## 原始证据（实证时命令输出，2026-08-14 11:40–11:55 UTC）

### stop 后（11:44 UTC 附近，GitHub API 原始输出）

```
$ gh api repos/crystepj-max/STS2-AUTOTEST/actions/runners --jq '.runners[] | "\(.name) status=\(.status) busy=\(.busy)"'
Chris-Mac-mini-STS2-AUTOTEST status=offline busy=true
```

- `status=offline`：stop 后 GitHub 侧不再标记在线（不可接收任务）。
- `busy=true` 为 stop 前已领取的 CI job 残留（runner 停止后该 job 排队停滞，
  证据：排队 run 保持 queued 不被领取）。

### stop 后本地（原始输出）

```
$ bash scripts/runner-ctl.sh status
---
status actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST:
...
Stopped
---
state: stopped
$ pgrep -fl "Runner.Listener"
（空——进程已停止）
```

### start 后（11:50 UTC 附近，GitHub API 原始输出）

```
$ gh api repos/crystepj-max/STS2-AUTOTEST/actions/runners --jq '.runners[] | "\(.name) status=\(.status) busy=\(.busy)"'
Chris-Mac-mini-STS2-AUTOTEST status=online busy=false
```

### start 后本地（原始输出）

```
$ bash scripts/runner-ctl.sh status
---
status actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST:
...
Started:
1981 0 actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST
---
state: running
$ pgrep -fl "Runner.Listener"
1989 /Users/chris/actions-runner/bin/Runner.Listener run --startuptype service
```

### 排队 run 停滞证据（stop 期间）

- 排队 run 31797270282（chore/issue-23-review-fixes）在 runner offline 期间保持
  queued 不被领取；runner 恢复 online 后由新 Listener 领取执行。

## 记录

- 时间：2026-08-14 11:40–11:55 UTC
- 未重新注册 runner，未改动凭证（符合 task.yaml 授权边界）
