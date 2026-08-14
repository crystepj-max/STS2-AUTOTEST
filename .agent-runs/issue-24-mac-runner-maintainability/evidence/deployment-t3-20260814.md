# T3 部署记录：探针定时采集（2026-08-14）

- 授权：松哥 2026-08-14 授权（收口节点人工门禁）
- 目的：部署连续 ≥7 天探针采集，JSONL 落盘，支持四类归因（T7）

## 部署

- 调度：launchd `com.sts2.autotest.runner-probe`（StartInterval=600s，每 10 分钟）
- 命令：`bash scripts/runner-probe.sh >> ~/.sts2-runner-probe/probe-$(date +%Y%m%d).jsonl`
  （bash -c 内动态求值日期，文件按天轮转）
- RunAtLoad=true（加载即首采）
- 落盘目录：`~/.sts2-runner-probe/`（probe-YYYYMMDD.jsonl + .probe-state.json + launchd.log/err）

## 验证（2026-08-14 11:54 UTC 首采）

```json
{"ts":"2026-08-14T11:54:07Z","service_state":"running","runner_pids":"1989",
 "github_online":"online","github_busy":true,"transition":"steady","op":"",
 "proxy_local_reachable":true,"direct_github_reachable":true,
 "proxy_github_reachable":true,"exit_ip_direct":"...","exit_ip_proxy":"..."}
```

- launchctl list：`com.sts2.autotest.runner-probe` 已加载
- 字段含四类归因所需：service/进程/GitHub 侧/busy/transition/op/direct/proxy/出口 IP

## 采集周期

- 2026-08-14 11:54 起，每 10 分钟一条，**2026-08-21 满 7 天**
- 每 10 分钟 144 条/天 ≈ 1008 条/7 天

## 后续

- T7 归因：满 7 天后对 JSONL 做四类归因 → 代理决策记录 → 回填 Issue #24
- 探针脚本本身只读，不触碰 runner 服务；launchd 任务可随时 `launchctl unload` 停用
