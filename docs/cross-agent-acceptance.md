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

| 客户端 | 推荐入口 | 平台级验证 | 外部环境实测 | 当前状态 |
|---|---|---|---|---|
| ChatGPT/Codex | MCP | `tests/integration/test_cross_agent_contract.py` | `p2-codex-short-20260720-01` / `p2-codex-cancel-20260720-01`：提交、防重、局内取消 CANCELLED、恢复 PASSED、报告可读 | **已验证**（2026-07-20） |
| WorkBuddy | MCP；CLI JSON 备选 | 公共协议测试 + M1-M7 共享回归 | `workbuddy-v11-replication-20260720-145438`：三柱全绿 | **已验证**（2026-07-20，P1 基线） |
| Claude Code | MCP 或 CLI JSON | 与公共契约一致 | `p2-claudecode-short-20260720-01`：CLI JSON 通道，提交、防重、取消 CANCELLED、恢复 PASSED、报告可读 | **已验证**（2026-07-20） |
| OpenClaw | MCP | 软件包定义验证；CLI 能力发现 | `p2-openclaw-short-20260720-01` / `p2-openclaw-cancel-20260720-01`：两轮全通 | **已验证**（2026-07-20） |
| Hermes | MCP | 公共契约 + 运行时兼容性 | `p2-hermes-short-20260720-01`：提交、防重、取消 CANCELLED；resume 因 FAILED_PLATFORM 证据未封存平台限制不可用，新提交等效恢复 PASSED | **已验证**（2026-07-20，附说明） |

“平台级验证”证明的是协议和服务行为一致；“外部环境实测”还要证明对应 Agent 能正确配置服务地址、保存 `run_id`、轮询任务并读取报告，不能用平台单测冒充。

## 5. 当前证据

截至 2026-07-20（P2 收口）：

- `tests/unit`：**1698 passed**（P2-1 迁移后全量回归）
- `tests/integration`：30 passed, 5 skipped（与 P1 基线一致的跳过项）
- 五类 Agent 均留下独立原始调用记录与证据包（见 `docs/p2/2026-07-20-p2-final-report.md` 验收矩阵）
- P2-1 Gawain 专属迁移清单与承接验证：`docs/p2/2026-07-20-p2-1-gawain-migration-inventory.md`
- 三角色短目标（IRONCLAD、SILENT、GAWAINMOD-GAWAIN）经同一公共入口通过：`tests/output/cross-agent-p2/short-goals-20260720-p2a*/`
- 整章遍历 PASSED（258.6s 第一章到第二章稳定地图，win_combat 快速结束 7 场战斗）：`run-20260720-142730-23590575`
- P1 三柱回归 PASSED（ORIG CANCELLED → RESUME PASSED → SECOND CANCELLED）：`tests/output/cross-agent-p1/p2-regression-v12-20260720/`
- 迁移后 Gawain 冒烟回归 PASSED：`STS2-GAWAIN/automation/autotest/output/gawain-smoke-v3/`

## 6. 进入稳定支持的门槛

**五类 Agent 全部完成外部真实验收后，平台对外使用“所有已验证客户端均已通过真实接入验收”的表述。**

每个客户端的验收凭证至少包含：

1. `capabilities` 返回的契约版本；
2. 一次带幂等键的 `submit_run`；
3. 任务从 `QUEUED` 到终态的状态记录；
4. `get_report` 返回与 `get_run` 一致的终态；
5. 任务证据目录和报告路径；
6. 至少一次取消或断线后的查询/恢复验证。

## 7. 目标场景与整章真实验收

公共 MCP 的 `capabilities` 必须公开目标场景、整章能力、路线规则、战斗模式、证据级别和提交参数。`submit_run` 支持 `journey`、`character_id`、`target_scene`、`route_policy`、`combat_mode`、`timeout`、`evidence` 和 `idempotency_key`。

整章旗舰任务固定为：

```text
journey=act_traversal
character_id=<合法角色标识>
target_scene=NEXT_ACT
route_policy=leftmost
combat_mode=traversal
evidence=full
```

只有报告同时证明第一章新局、角色选择、开图事件、地图路线、实际房间、战斗奖励、Boss、Boss 奖励、章节从第一章变为第二章、第二章开图事件和稳定地图，最终状态才可标记为 `PASSED`。单目标场景验收必须分别通过同一个 `submit_run → get_run → get_report` 入口完成。
