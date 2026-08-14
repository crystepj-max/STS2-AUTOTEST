# 本机 runner 日志 F2 签名（T1 基线）

日志：`~/actions-runner/_diag/Runner_20260813-151834-utc.log`（2026-08-13 15:18Z 启动），
2026-08-14 取证。`_diag/` 目录另有同批多个大日志（单文件 8–25MB，未全文归档）。

## BrokerServer 长轮询中断（反复发生，可自愈）

`grep -nE "BrokerServer|SocketException|Back off"` 命中 30+ 处，典型片段：

```
[15:22:34Z ERR  BrokerServer] Catch exception during request
[15:22:34Z ERR  BrokerServer] System.Threading.Tasks.TaskCanceledException: The operation was canceled.
 ---> System.Threading.Tasks.TaskCanceledException: The operation was canceled.
 ---> System.Net.Sockets.SocketException (89): Operation canceled
  at GitHub.Runner.Common.BrokerServer.<>c__DisplayClass7_0.<<GetRunnerMessageAsync>b__0>d.MoveNext()
[15:22:34Z ERR  BrokerServer] System.IO.IOException: Unable to read data from the transport connection: Operation canceled.
[15:22:34Z WARN BrokerServer] Back off 12.96 seconds before next retry. 4 attempt left.
```

发生频率：15:22–16:37Z 之间 ≥20 轮 Back off，间隔约 2–25 分钟不等。
特征：每轮 4 次退避重试（"4 attempt left"），随后自行恢复继续长轮询。

## AAD token 获取慢

```
[15:21:48Z WARN GitHubActionsService] Retrieving an AAD auth token took a long time (3.1421485 seconds)
[15:39:59Z WARN GitHubActionsService] Retrieving an AAD auth token took a long time (2.0146787 seconds)
```

本日志内 2 次 >2s；分诊引用日志另有 6.1s / 14.6s 两例。阈值 >5s 时记为慢。

## 与症状的对应

- 症状 1/2「连接中断后自行恢复、无需重新注册」← BrokerServer 退避自愈（本签名）
- 症状「间歇性不可用窗口」← 中断时段可能叠加在 job 领取窗口（Run #40 排队 3h16m）

## 备注

- 不完整恢复的证据链（cancel → backoff → 恢复）完整保留在原始日志，
  本文件只提取签名；如需全文归档需按天滚动压缩，避免 12GB+ `_diag/` 增长问题
  （建议后续纳入 T5 手册的磁盘维护章节）。
