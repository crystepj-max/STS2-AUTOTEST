# 自托管 Mac Runner 运维手册（issue-24 T5）

本手册面向**非原排障人员**：按本手册可独立完成 runner 状态核查、停止/启动、
代理验证与常见故障处置。所有命令均以本机真实安装为准（2026-08-14 基线，
详见 `.agent-runs/issue-24-mac-runner-maintainability/evidence/`）。

## 1. 真实安装是什么（先读这节）

| 项 | 值 |
|---|---|
| 安装目录 | `~/actions-runner`（v2.336.0，架构 osx-arm64） |
| Runner 名称 | `Chris-Mac-mini-STS2-AUTOTEST`（agentId 21） |
| 服务形态 | svc.sh 安装的 launchd 服务（label `actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST`） |
| 代理 | ClashX（`HTTP_PROXY=http://127.0.0.1:7890`），写入 runner `.env` |
| 仓库 | `crystepj-max/STS2-AUTOTEST` |

> ⚠️ 不要按旧文档操作 `~/actions-runner-autotest` 或 `com.sts2.autotest-runner`
> ——它们在本机不存在（历史漂移，issue-24 F1）。

## 2. 查看状态

```bash
# 统一入口（推荐）：0=RUNNING 1=STOPPED 2=NOT_INSTALLED
scripts/runner-ctl.sh status

# 直接核查（svc.sh 必须从 runner 根目录运行）
cd ~/actions-runner && ./svc.sh status

# launchd 侧
launchctl list | grep actions.runner

# 进程
pgrep -fl "Runner.Listener"

# GitHub 侧（需要 gh 已登录）
gh api repos/crystepj-max/STS2-AUTOTEST/actions/runners \
  --jq '.runners[] | "\(.name) id=\(.id) status=\(.status) busy=\(.busy)"'
```

健康检查（不依赖游戏环境，返回 0=可用；三路核验——服务状态、
真实 Runner.Listener 进程、GitHub 侧 online 状态一致才报 HEALTHY）：

```bash
scripts/check-runner-health.sh          # 人类可读
scripts/check-runner-health.sh --json   # 结构化输出
```

> 反例：服务标记 started 但进程缺失，或 GitHub 侧 offline 时，
> 健康检查报 UNHEALTHY（避免「服务假启动仍误报可接任务」）。

## 3. 停止 / 启动

```bash
scripts/runner-ctl.sh stop    # 停止服务（launchctl unload）
scripts/runner-ctl.sh start   # 启动服务（launchctl load -w）
```

- `stop` 后 `runner-ctl.sh status` 应显示 `Stopped`（退出码 1）；
  此时 GitHub 侧不会领取新 job。
- `start` 后 `runner-ctl.sh status` 应显示 `Started:`（退出码 0）；
  等待约 30 秒后 GitHub 侧应恢复 `online`。
- 服务重启可能打断 BrokerServer 长轮询的**自愈过程**——先按第 5 节归因，
  确认是持久故障再重启（间歇抖动自愈中勿重启）。

## 4. 代理验证

```bash
# 代理端口是否在监听
nc -z 127.0.0.1 7890 && echo "ClashX 端口可达"

# 经代理访问 GitHub（应输出 200）
curl -s --max-time 5 -o /dev/null -w '%{http_code}' -x http://127.0.0.1:7890 https://api.github.com/zen

# 直连对比（issue-13 已实证：直连不可用属预期）
curl -s --max-time 5 -o /dev/null -w '%{http_code}' https://api.github.com/zen

# 出口 IP（经代理）
curl -s --max-time 5 -x http://127.0.0.1:7890 https://api.ipify.org
```

## 5. 签名 → 处置表

| 签名 | 含义 | 处置 |
|---|---|---|
| `svc.sh status` 显示 Stopped 或 exit 1 | 服务已停止 | 检查是否有人主动 stop；如需运行则 `runner-ctl.sh start` |
| `svc.sh status` 报 `Must run from runner root` | 从错误目录调用了 svc.sh | 用 `runner-ctl.sh` 或先 `cd ~/actions-runner` |
| runner 日志 `BrokerServer ... SocketException (89)` + `Back off N attempt left` | 长轮询中断，正在退避自愈（F2） | **观察，勿重启**；多数在 4 次重试内恢复 |
| `Retrieving an AAD auth token took a long time (x s)` | AAD token 获取慢（>5s） | 记录时间戳，纳入 7 天数据归因（T7），不必处置 |
| GitHub 侧 `status=offline` | runner 与 GitHub 断链 | 先看探针数据区分网络/代理；确认代理正常后重启服务 |
| GitHub 侧 `busy=true` 持续数小时 | 任务执行中或卡死 | 查看 `_diag` 日志；按 run 取消/超时策略处理 |
| job 排队 >15min 但 runner online | runner 领取异常 | 查 `_diag` 最新日志 + 探针数据归因（本机网络/代理/GitHub 上游） |
| checkout 报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` | IDE/代理会话变量触发 safe-delete 钩子 | 确认 `CODEBUDDY_SESSION_ID` / `CLAUDE_SESSION_ID` 未注入 runner 服务环境；workflow 已置空。手动：`unset` 后重跑 `check-runner-health.sh` |

> 探针：`scripts/runner-probe.sh` 输出 JSONL，记录服务状态、真实进程、
> GitHub 侧状态与忙闲（`github_online`/`github_busy`）、直连/代理可达性
> 与出口 IP、事件（`transition`: disconnect/recover/service-stopped/
> service-started）与维护操作标记（`op`，由 `PROBE_OP` 注入），用于区分
> 四类归因（本机网络 / 代理出口 / GitHub 上游 / 维护操作）。
> 直连探测强制绕过环境代理（`--noproxy '*'`），确保两条路径真实可区分。
> 连续采集 ≥7 天后做归因（issue-24 T3/T7）。

## 6. 连续数据采集（issue-24 T3）

探针单次执行输出一行 JSON：

```bash
scripts/runner-probe.sh
```

部署定时采集（建议每 10 分钟一次，落盘追加；需部署到 launchd/cron 后生效）：

⚠️ **计划任务的入口脚本不要放在 `/Volumes/...` 下。** launchd 派生的 `/bin/bash`
没有"访问其他卷"的隐私授权，读该卷一律 `Operation not permitted`（家目录不受影响，
文件权限位与 ACL 也都正常）。所以先把探针复制到家目录，再让计划任务指向这份副本；
改过 `scripts/runner-probe.sh` 后必须重跑 `cp` 刷新副本，否则排期跑的仍是旧版。

```bash
mkdir -p ~/.sts2-runner-probe
cp scripts/runner-probe.sh ~/.sts2-runner-probe/runner-probe.sh
# cron 示例（每 10 分钟）：
# */10 * * * * /bin/bash $HOME/.sts2-runner-probe/runner-probe.sh \
#   >> $HOME/.sts2-runner-probe/probe-$(date +%Y%m%d).jsonl 2>&1
```

launchd 版（本机实际采用）：`~/Library/LaunchAgents/com.sts2.autotest.runner-probe.plist`，
`StartInterval=600`，`ProgramArguments` 指向 `~/.sts2-runner-probe/runner-probe.sh`。
改完 plist 后重载并验证（跑完立刻出第一行 JSON，不必等 10 分钟）：

```bash
launchctl bootout gui/$(id -u)/com.sts2.autotest.runner-probe 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sts2.autotest.runner-probe.plist
tail -1 ~/.sts2-runner-probe/probe-$(date +%Y%m%d).jsonl   # 应为 JSON，不是 bash 报错
```

> 历史中断：2026-09-06 起采集断过——工作区从 `~/STS2-WORKSPACE` 迁到
> `/Volumes/4mac/STS2-WORKSPACE` 后先报 `No such file or directory`，把 plist 路径
> 改对之后又因上述卷授权报 `Operation not permitted`，一直到本次修复。issue-24 T3
> 要求的"连续 ≥7 天"要从修复日重新起算，9-06 之后的数据不可用。

采集满 7 天后的归因规则：

| 观测 | 归因 |
|---|---|
| `direct=false` 且 `proxy=false` | 本机网络（出口/局域网）故障 |
| `direct=false`、`proxy=false`、但代理端口可达 | 代理出口路径故障 |
| 直连/代理可达但 `github_online=unknown` 或响应慢 | GitHub 上游波动 |
| `service_state` 突变与操作时间吻合 | 维护操作（对照操作记录） |

## 7. 恢复演练（issue-24 T6，需授权）

演练步骤（模拟中断 → 按手册恢复 → 确认可接收任务）：

1. 记录演练开始时间与当前状态（`runner-ctl.sh status`）。
2. `scripts/runner-ctl.sh stop` → 确认 `status` 为 Stopped、GitHub 侧不再领取 job。
3. `scripts/runner-ctl.sh start` → 确认 `status` 为 Started。
4. 等待 30–60 秒，确认 GitHub 侧 `online`。
5. 触发一个真实 job（如重跑最近一次 PR CI），确认能被本机 runner 领取并完成。
6. 将命令记录与结果写入 `.agent-runs/issue-24-mac-runner-maintainability/evidence/drill-YYYYMMDD.md`。

> 对 `~/actions-runner`、launchd 服务做任何改动前：先备份、留命令记录、
> 逐项征得授权（issue-24 授权边界）。

## 8. 常见问题

- **`runner-ctl.sh` 报 `not found`/`not installed`**：检查 `RUNNER_DIR`
  是否被覆盖（环境变量），或安装目录是否真的存在。
- **改了 `.env` 不生效**：`.env` 在服务启动时读取，改后需 `stop` + `start`。
- **磁盘膨胀**：`~/actions-runner/_diag/` 单文件可达 8–25MB，建议按天滚动归档。
- **checkout 随机失败 / SAFE_DELETE**：不要把 `CODEBUDDY_SESSION_ID`、
  `CLAUDE_SESSION_ID` 写进 `~/actions-runner/.env` 或 launchd 环境。
  CI workflow 已显式置空；`check-runner-health.sh --json` 的
  `safe_delete_session_env` 非空即表示仍有风险。
- **需要全新安装/修复**：`scripts/setup-mac-runner.sh`（幂等：
  已配置安装跳过下载与注册）。
  - 新安装必须显式提供机器身份：`RUNNER_NAME=<机器名> ./scripts/setup-mac-runner.sh`
    （不再默认固定机器名，防止误覆盖其他机器身份）。
  - 同名已注册默认拒绝覆盖，需显式 `ALLOW_REPLACE=1` 才允许。
  - 装后自动写入运行环境（HTTP_PROXY/HTTPS_PROXY 到 runner `.env`）、
    `svc.sh install` → `start` 并验证 status 为 Started。
