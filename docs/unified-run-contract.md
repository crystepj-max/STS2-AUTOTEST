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

失败报告必须同时返回结构化原因。通用导航至少使用：
`TARGET_UNREACHABLE`（目标房间不可达）、`NO_PROGRESS`（操作后无可观察变化）、
`ACTION_SURFACE_INCOMPLETE`（游戏已出现可处理状态，但控制入口未公开所需操作）、
`COMBAT_FAILED`（角色死亡）、`GAME_CRASHED`（游戏崩溃）和 `TIMEOUT`（在有持续进展或无法进一步归类时超时）。
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

`submit_run` 可以额外指定 `journey`：`new_run`、`resume_run`、`first_battle`、`finish_interstitials`、`goal_scene`、`act_traversal` 或 `card_test`。这些旅程只负责通用游戏流程；角色、卡牌、遗物和数值预期仍由项目用例负责。

## 4.1 目标场景执行

目标场景请求与旧项目套件请求共用同一个任务服务。请求可以包含：

```json
{
  "journey": "act_traversal",
  "character_id": "IRONCLAD",
  "target_scene": "NEXT_ACT",
  "route_policy": "leftmost",
  "combat_mode": "traversal",
  "timeout": 3600,
  "evidence": "full",
  "idempotency_key": "unique-per-attempt"
}
```

支持的目标场景为 `MAIN_MENU`、`CHARACTER_SELECT`、`MAP`、`EVENT`、`COMBAT`、`REST`、`SHOP`、`CHEST`、`CARD_REWARD` 和 `NEXT_ACT`。`act_traversal` 只是把统一目标执行器的目标设为 `NEXT_ACT`，不会拥有另一套房间处理逻辑。

任务执行遵循“读取状态 → 执行一个操作 → 重新读取 → 验证可观察变化”的循环。地图路线、战斗、事件、奖励、营火、商店和宝箱均按独立公共规则处理。`combat_mode=traversal` 在当前环境公开 `win_combat` 时优先使用平台级快速结束命令；该命令仅用于通路验证，且仍必须确认战斗页面离开、敌人清空并进入奖励或地图。未公开该能力时回退到角色无关的基础战斗。操作返回成功但状态没有变化时，平台最多进行短时观察和一次连接恢复，然后以 `FAILED_PLATFORM` 留证终止。

## 4.2 死亡测试与卡牌专测

- 死亡测试：`target_scene=COMBAT` 且 `combat_mode=death`。进入真实战斗后每回合只执行 `end_turn`，绝不出牌或使用 `win_combat`，直到游戏真实进入 `GAME_OVER` 才算 `PASSED`；报告中的操作序列和血量变化证明角色被怪物击杀。
- 卡牌专测：`journey=card_test`，必须携带非空 `card_id`（运行时控制台格式）。平台通过调试控制台把该牌加入手牌，验证卡牌确实入手、真实打出且产生可观察状态变化。平台只断言这些通用事实；具体卡牌效果由项目用例基于报告中的前后状态 JSON 断言。当前环境未开启调试能力时以 `FAILED_PLATFORM` 留证终止。

运行中的 `get_run` 至少返回 `current_chapter`、`current_floor`、`current_screen`、`target_scene`、`rooms_processed`、`room_types`、`last_action`、`last_updated_at`、`steps`、`recovering` 和 `last_observed_change`。目标完成必须由场景、地图稳定性或章节变化等状态证据确认，不能由操作返回值单独确认。

旧的 `run_test`、`run_pipeline`、`review_spec` 和 `compile_spec` 继续保留一个兼容周期，但新的 Agent 接入应优先使用上述任务接口。当前 MCP 入口为 JSON-RPC over HTTP；不要求 Agent 保持长连接。

## 5. 凭证要求

每个任务目录必须能够定位到：

- 最终状态和失败归属；
- 机器可读的任务记录；
- 测试报告；
- 失败用例的截图、游戏日志和状态快照；
- 恢复尝试及恢复结果；
- 可继续执行的进度记录。

“操作已接受”但目标状态没有改变，不得写入 `PASSED`。目标任务目录还应包含 `reports/journey-trace.json`、`reports/evidence-manifest.json` 和 `reports/run-result.json`；截图或日志不可用时，清单必须明确记录实际缺失情况。
