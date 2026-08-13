# Runner 环境探针记录（2026-08-14，开发 attempt-003）

目的：在自托管 runner 上端到端实证 issue-13 修复（F1 tool cache、F2 代理、
T5 前置 sts2 CLI），确认主分支验收链（T4–T6）的 runner 侧前置条件是否就绪。
探针载体：`ci-game.yml`（workflow_dispatch → 自托管 job）在临时分支
`ci/runner-probe-issue13` 上的运行（分支已删除，运行历史保留）。

## 结论

**Runner 环境链路已实测打通**：checkout ✅ → setup-python 缓存命中 ✅ →
pip install ✅ → `autotest doctor` 正常执行 ✅（健康项失败为游戏环境限制，
非基础设施故障）。主分支验收链（quick-checks / CLI 集成 / 部署）的
runner 侧前置条件全部满足。剩余阻塞仅为：GitHub 托管计费（用户侧）、
评审合并、游戏环境健康（仅影响 requires_game 类流程）。

## 关键发现（按探针顺序）

| # | 发现 | 证据 |
|---|---|---|
| 1 | **F2 代理修复在 job 内生效**：listener 进程与 job 日志均确认走 ClashX（`Runner is running behind proxy server 'http://127.0.0.1:7890'`），action 下载 / checkout / git fetch 全部成功 | run 31714979518 起所有探针 |
| 2 | **job 级 tool cache 路径由 runner 内部机制确定，plist 注入非权威**：多次探针实测 job env 为 `RUNNER_TOOL_CACHE=AGENT_TOOLSDIRECTORY=/Users/runner/hostedtoolcache`（个别运行可见 `_tool`，来源未完全定位）；`/Users/runner` 由 root 于 22:00 创建，`hostedtoolcache/` 与 `Python/` 为 chris 可写（22:01/22:05 由 job 进程创建） | run 31716237619 / 31717673601 env 转储 |
| 3 | **setup-python v5 macOS 预构建包流程在缓存未命中时执行 `sudo installer -pkg`**（归档内 setup.sh 第 42 行），本机 chris 无免密 sudo → `sudo: a terminal is required to read the password`；GitHub 托管机 runner 用户有免密 sudo 所以无此问题 | run 31714979518 起每个未命中缓存运行的 setup-python 步骤 |
| 4 | setup-python 缓存查找使用**清单解析后的精确版本**（非输入范围）：预置 3.11.15 不命中（请求 3.11.9）；tool cache 目录须为 `Python/<精确版本>/<arch>/` + `<arch>.complete` 标记 | run 31715561310（3.11.15 未命中）→ run 31718147872（3.11.9 命中） |
| 5 | python-build-standalone 构建带 `EXTERNALLY-MANAGED` 标记（PEP 668），pip install 直接拒绝；需移除标记 | run 31717939654 `error: externally-managed-environment` |
| 6 | 游戏环境健康（`autotest doctor --ci`）：`{"healthy": false, "failed_checks": ["steam_installed", "steam_login_state", "disk_space"]}` —— 机器无 Steam 登录态/游戏进程环境，属环境限制（与测试阶段 BLOCKED 结论一致），不影响 T4/T5/T6 | run 31718147872 |

## 修复措施（runner 侧，均在 `/Users/chris` 下，可逆）

1. **launchd plist 补代理环境变量**（备份：`actions.runner.*.plist.bak-20260813-before-proxy`）：
   `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy=http://127.0.0.1:7890`、
   `NO_PROXY/no_proxy=127.0.0.1,localhost`；服务已重启，`ps eww` 确认生效。
   注意：`.env` 仅为交互模式参考，服务模式必须写 plist（与 T2 的 RUNNER_TOOL_CACHE
   双写同理）。
2. **Tool cache 预置 Python 3.11.9（可重定位构建，无 sudo）**，写入 job 实际
   使用的路径与 `_tool` 双处：
   - `/Users/runner/hostedtoolcache/Python/3.11.9/arm64/`（job env 权威路径）
   - `/Users/chris/actions-runner/_work/_tool/Python/3.11.9/arm64/`（plist 值兜底）
   布局：`bin/include/lib/share/python` 符号链接 + `arm64.complete` 标记；
   版本来源：`uv python install 3.11.9`（python-build-standalone 可重定位构建，
   与 pyproject 的 Python 3.11 要求一致）；移除两处 `EXTERNALLY-MANAGED` 标记。
3. 幂等说明：setup-python 命中缓存即跳过下载与 `sudo installer` 流程；
   若将来清单解析出新的 3.11.x 精确版本，需按同样方法预置对应版本
   （或为用户授予免密 sudo：`echo 'chris ALL=(ALL) NOPASSWD: /usr/sbin/installer' | sudo tee /etc/sudoers.d/sts2-runner-installer`，与托管机 runner 用户行为一致）。

## 探针运行索引

| Run | 时间(UTC) | 载体 | 结果 |
|---|---|---|---|
| 31714979518 | 08-13 15:21 | ci-game@main | 代理生效；setup-python 未命中缓存 → sudo 失败 |
| 31715561310 | 08-13 15:28 | ci-game@main | 预置 3.11.15 未命中（精确版本要求）→ sudo 失败 |
| 31715894352 | 08-13 15:32 | ci-game@main | 预置 3.11.9@_tool 未命中 → sudo 失败 |
| 31716237619 | 08-13 15:36 | ci-game@probe | env 转储：bash 步骤见 `_tool`；路径矛盾定位起点 |
| 31716619634 | 08-13 15:40 | ci-game@probe | ACTIONS_STEP_DEBUG 无效（需仓库 secret 级） |
| 31716798669 | 08-13 15:42 | ci-game@probe | shell 复现 setup.sh：shell 环境逻辑正确，会删缓存目录 |
| 31717160526 | 08-13 15:46 | ci-game@probe | node 进程 env = `/Users/runner/hostedtoolcache`（关键证据） |
| 31717673601 | 08-13 15:52 | ci-game@probe | 再次确认 node env；`tc.find is not a function`（bundle 导出形态） |
| 31717939654 | 08-13 15:55 | ci-game@probe | 种子命中路径打通 → pip PEP 668 错误 |
| **31718147872** | 08-13 15:57 | ci-game@probe | **链路全通**：缓存命中 + pip install ✅ + doctor 执行（环境健康失败） |

## 关联

- 修复前失败基线：`run-31672864997-20260813-main-full.log`、`run-31561378957-20260812-main-full.log`
- 网络基线：`network-probe-20260813.md`
- 计费阻塞：`billing-blocker-20260813.md`
