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

## 采集周期（2026-08-15 修正：从最终四类探针部署后重新计时）

- 首采：2026-08-14 11:54 UTC（launchd 部署）。
- 修正 1：维护操作归因链路（runner-ctl 写 ops.jsonl + 探针消费）在
  2026-08-14 14:11 UTC 演练后完整生效。
- **修正 2（bot 审查 3787 系列）**：独立联网探针（`internet_reachable`/
  `dns_resolvable`，区分本机网络 vs GitHub 上游）于 2026-08-14 21:05 UTC
  合并进 main 后生效；此前采样无该字段，不构成完整四类可归因数据。
- **七天采集期最终计时：自 2026-08-14 21:05 UTC（完整四类探针部署）起，
  2026-08-21 21:05 UTC 满 7 天**。
- 每 10 分钟 144 条/天 ≈ 1008 条/7 天。

## 维护操作归因链路（2026-08-14 补充）

- `runner-ctl.sh stop/start` 自动追加 `manual-stop`/`manual-start` 到
  `~/.sts2-runner-probe/ops.jsonl`（含 UTC 时间戳；原子追加：临时文件 + 锁 + mv）。
- 探针每次采样读取 ops.jsonl 最新记录，最近 15 分钟内的操作且**与 transition
  匹配**（manual-stop↔service-stopped、manual-start↔service-started）才填入
  `op` 字段——`service-stopped/service-started` 因此可区分「人工维护」与
  「意外中断」，满足 T3 四类归因的维护操作类（bot P1 意见 3784471373/3784471376 已修）。
- 实证：T2/T6 演练的 stop/start 已写入 ops.jsonl，探针识别 op=manual-stop/start。
- 样例数据：`evidence/probe-sample-20260814.jsonl`（注意：该样例为链路修正前
  采样，op 为空属预期）。

## 后续

- T7 归因：满 7 天后（2026-08-21 14:11 UTC）对 JSONL 做四类归因 → 代理决策
  记录 → 回填 Issue #24
- 探针脚本本身只读，不触碰 runner 服务；launchd 任务可随时 `launchctl unload` 停用
