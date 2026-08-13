# 网络探针记录（2026-08-13）

目的：确定 runner 与 GitHub 通信应走代理还是直连（F2 修复决策依据）。

## 环境

- 本机 macOS（Darwin 25.5.0），ClashX Pro 运行中（PID 19892），HTTP/HTTPS 代理 127.0.0.1:7890。
- macOS 系统代理已启用（`scutil --proxy`：HTTPProxy/HTTPSProxy=127.0.0.1:7890），
  绕过列表不含 GitHub 域名。
- GitHub Actions runner（2.336.0，launchd 服务）的 .NET HttpClient 实测连接 127.0.0.1:7890。

## 探针命令与结果

目标 URL：`https://codeload.github.com/actions/setup-python/tar.gz/refs/tags/v5.5.0`

| 路径 | 尝试 | 结果 | 耗时 |
|------|------|------|------|
| 走代理 `-x http://127.0.0.1:7890` | 1 | HTTP 200 | 1.86s |
| 走代理 | 2 | HTTP 200 | 1.96s |
| 走代理 | 3 | HTTP 200 | 3.33s |
| 直连 `--noproxy '*'` | 1 | 超时（收到 152927 字节后中断） | 12.01s |
| 直连 | 2 | 超时（收到 283999 字节后中断） | 12.00s |

## 结论

- **必须走 ClashX 代理**：3/3 稳定成功。
- **直连不可用**：本机直连 codeload.github.com 下载超时中断（2/2 失败）。
- F2（8-13 的 TLS 断连）为走代理时的间歇性节点抖动；GitHub Actions 下载重试可自愈
  （8-13 运行中重试后下载成功，致命失败为 F1 的 mkdir 权限）。
- **不添加 GitHub 域名 NO_PROXY 直连**。曾临时添加后导致 job 卡在 Set up job
  （下载超时），已撤销恢复（备份：`.env.bak-20260813-with-noproxy`）。
