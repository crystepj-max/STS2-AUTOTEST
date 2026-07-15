# STS2-AUTOTEST 统一运行契约

本文档定义所有 Agent 共用的测试任务入口。平台只接收测试目标，不要求 Agent 自己处理游戏启动、页面等待、崩溃恢复或证据收集。

## 1. 任务请求

请求至少包含一个项目或测试套件；未指定具体用例时，平台执行项目默认全量套件。

```json
{
  "project": "gawain",
  "suite": "m1-m7",
  "cases": [],
  "mode": "new",
  "timeout": 3600,
  "adapter": "agent",
  "evidence": "full",
  "idempotency_key": "gawain-m1-m7-20260715"
}
```

字段含义：

- `project`：项目标识或项目目录。
- `suite` / `cases`：测试套件或指定用例；二者都提供时，以指定用例为准。
- `mode`：`new` 表示从新局开始，`resume` 表示使用平台保存的进度继续。
- `timeout`：整次任务允许的最长等待时间。
- `adapter`：游戏控制通道；为空时使用项目默认值。
- `evidence`：`none`、`minimal` 或 `full`，默认使用 `full`。
- `idempotency_key`：Agent 重试提交时保持相同值，平台返回原任务，不重复启动游戏。

## 2. 任务状态

提交后立即返回唯一 `run_id`。Agent 应通过 `run_id` 查询，不应依赖长时间保持连接。

阶段状态：

`QUEUED` → `PRECHECK` → `PREPARING` → `STARTING` → `RUNNING` → `RECOVERING` → `COLLECTING` → `COMPLETED`

最终结果：

- `PASSED`：项目功能和测试流程均通过。
- `FAILED_PRODUCT`：项目功能或预期结果失败。
- `FAILED_PLATFORM`：测试平台未能执行或留证。
- `BLOCKED_ENVIRONMENT`：游戏、登录、文件或机器环境阻塞。
- `CANCELLED`：用户或 Agent 主动取消。

状态响应示例：

```json
{
  "run_id": "run-20260715-120000-a1b2c3d4",
  "status": "RUNNING",
  "phase": "RUNNING",
  "request": {"project": "gawain", "suite": "m1-m7"},
  "cancel_requested": false,
  "result": {},
  "evidence_dir": "tests/output/run-20260715-120000-a1b2c3d4"
}
```

## 3. 命令行入口

```text
autotest run --project gawain --suite m1-m7 --detach
autotest status <run_id> --json
autotest cancel <run_id>
autotest resume <run_id>
autotest report <run_id>
autotest run --journey first_battle --character-id IRONCLAD --detach
```

`--detach` 会把任务交给独立工作进程，控制端退出不会影响任务。所有任务共享一个游戏会话位置，后提交的任务自动排队。

平台内部还提供通用旅程：创建新局、恢复旧局、推进开局事件、处理卡包/选牌/战后奖励、进入第一场真实战斗，以及回到稳定地图。项目用例只补充角色专属预期，不再重复编写这些流程。

## 4. MCP 入口

稳定工具名称：

- `capabilities`
- `submit_run`
- `get_run`
- `cancel_run`
- `resume_run`
- `get_report`

`submit_run` 可以额外指定 `journey`：`new_run`、`resume_run`、`first_battle` 或 `finish_interstitials`。这些旅程只负责通用游戏流程；角色、卡牌、遗物和数值预期仍由项目用例负责。

旧的 `run_test`、`run_pipeline`、`review_spec` 和 `compile_spec` 继续保留一个兼容周期，但新的 Agent 接入应优先使用上述任务接口。当前 MCP 入口为 JSON-RPC over HTTP；不要求 Agent 保持长连接。

## 5. 凭证要求

每个任务目录必须能够定位到：

- 最终状态和失败归属；
- 机器可读的任务记录；
- 测试报告；
- 失败用例的截图、游戏日志和状态快照；
- 恢复尝试及恢复结果；
- 可继续执行的进度记录。

“操作已接受”但目标状态没有改变，不得写入 `PASSED`。
