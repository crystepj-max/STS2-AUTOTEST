# 跨 Agent 通用接入与验收协议

本文把 STS2-AUTOTEST 的能力固定成“客户端无关”的公共协议。ChatGPT、WorkBuddy、Claude Code、OpenClaw、Hermes 可以使用不同的调用方式，但必须得到同一种任务记录、状态、结果归属和凭证目录。

## 1. 统一原则

Agent 只负责三件事：提交目标、保存 `run_id`、根据状态决定继续等待或停止。以下事项不能由 Agent 自己重写：

- 游戏会话独占和排队；
- 新局、旧局、过场、选牌、奖励和首战准备；
- 断线、崩溃、phantom combat 和旅行无进展的恢复；
- 项目失败、平台失败和环境阻塞的区分；
- 截图、状态、日志、JUnit、HTML 和压缩凭证的归档。

## 2. 两个公开传输方式

### MCP over HTTP

适用于能调用 MCP 的 Agent。服务端地址由项目环境提供，JSON-RPC 方法保持稳定：

```text
initialize
tools/list
tools/call
```

首选工具只有六个：

```text
capabilities
submit_run
get_run
cancel_run
resume_run
get_report
```

### CLI JSON

适用于不能直接接入 MCP 的 Agent：

```bash
autotest capabilities --json
autotest run --project <project> --suite <suite> --detach --idempotency-key <key>
autotest status <run_id> --json
autotest cancel <run_id>
autotest resume <run_id>
autotest report <run_id>
```

CLI 的机器可读输出与 MCP 的 `structuredContent` 必须表达同一份任务记录；Agent 不应解析人类提示语、日志文本或 HTML 来判断最终状态。

## 3. Agent 的最小调用流程

```text
capabilities
  ↓
submit_run（保留 run_id）
  ↓
循环 get_run
  ├─ 非终态：继续等待
  ├─ BLOCKED_ENVIRONMENT / FAILED_PLATFORM：停止并交给环境或平台处理
  └─ PASSED / FAILED_PRODUCT / CANCELLED：调用 get_report
```

重试 `submit_run` 时必须复用相同的 `idempotency_key`。网络断开不代表测试失败；先查询原 `run_id`，只有确认任务不存在时才重新提交。

## 4. 互操作验收矩阵

| 客户端 | 推荐入口 | 平台级验证 | 仍需外部环境实测 |
|---|---|---|---|
| ChatGPT | MCP | `tests/integration/test_cross_agent_contract.py` | 在 ChatGPT 的 MCP 配置中完成一次真实提交—查询—取报告 |
| WorkBuddy | MCP；不能使用时 CLI JSON | 公共协议测试；WorkBuddy 共享回归页面已确认复用 `AgentAdapter` 和公共导航入口 | 用新 `submit_run/get_run/get_report` 契约完成一次真实任务；现有页面主要证明了 Gawain 驱动脚本复用公共导航，不能替代新任务服务验收 |
| Claude Code | MCP 或 CLI JSON | 与其他客户端共用同一契约 | 在 Claude Code 会话中完成一次真实任务 |
| OpenClaw | MCP 或 CLI JSON | 与其他客户端共用同一契约 | 在 OpenClaw 环境中完成一次真实任务 |
| Hermes | MCP 或 CLI JSON | 与其他客户端共用同一契约 | 在 Hermes 环境中完成一次真实任务 |

“平台级验证”证明的是协议和服务行为一致；“外部环境实测”还要证明对应 Agent 能正确配置服务地址、保存 `run_id`、轮询任务并读取报告，不能用平台单测冒充。

## 5. 当前证据

截至 2026-07-15：

- `tests/unit`：`1500 passed`，仅有 2 个既有的测试收集警告；
- `tests/integration`：`30 passed, 5 skipped`，跳过项需要真实游戏或外部控制服务；
- `tests/integration/test_cross_agent_contract.py`：验证 MCP 幂等提交、同一 `run_id` 查询、终态报告读取，以及 CLI 能力发现；
- `compileall`、`git diff --check` 和 import 边界检查通过；
- 在忽略本机未安装的第三方类型文件后，项目自身类型检查通过；完整类型检查仍需安装 `types-PyYAML`、`types-psutil` 以及可选视觉库类型依赖；
- WorkBuddy 共享回归页面显示，实际 Gawain 驱动已调用 `AgentAdapter` 和公共 `progress_until`，并用公共卡牌奖励入口完成连续 7 轮回地图；但页面同时说明崩溃重启、phantom combat、旅行挂起看门狗仍在任务专用脚本中，因此这部分不能算作平台已经完成的跨 Agent 生命周期验收。

## 6. 进入稳定支持的门槛

在五类 Agent 都完成一次外部真实验收前，平台对外应使用“协议已兼容、具体客户端待实测”的表述。完成外部验收后，才可以把对应客户端标记为“已验证”。

每个客户端的验收凭证至少包含：

1. `capabilities` 返回的契约版本；
2. 一次带幂等键的 `submit_run`；
3. 任务从 `QUEUED` 到终态的状态记录；
4. `get_report` 返回与 `get_run` 一致的终态；
5. 任务证据目录和报告路径；
6. 至少一次取消或断线后的查询/恢复验证。
