# Agent 最短接入说明（客户端无关）

任何 Agent 只需完成本文六个公共操作，即可在 STS2-AUTOTEST 上提交、跟踪、中断、恢复并取回一次测试任务。Agent **不得**自己实现新局、事件、奖励、地图、战斗、恢复或证据采集逻辑——这些全部由平台承担。

## 一、两个等价通道

| 通道 | 适用 Agent | 地址/命令 |
| --- | --- | --- |
| MCP over HTTP | 能调用 MCP 工具的 Agent | `http://127.0.0.1:8090/mcp`（JSON-RPC `tools/call`） |
| CLI JSON | 不能调用 MCP 的 Agent | `autotest` 命令行，全部输出机器可读 JSON |

两个通道操作同名、参数同名、返回同一份任务记录；状态名称与报告结构完全一致。Agent 只解析 JSON，不解析人类提示语。

## 二、六个公共操作

### 1. capabilities — 查看平台支持什么

```text
MCP: tools/call { "name": "capabilities", "arguments": {} }
CLI: autotest capabilities --json
```

返回：六操作清单、支持的 journey、目标场景、路线规则、战斗模式、证据级别和提交参数。

### 2. submit_run — 提交任务

```text
MCP: tools/call { "name": "submit_run", "arguments": {
       "journey": "new_run", "character_id": "IRONCLAD",
       "timeout": 600, "evidence": "full",
       "idempotency_key": "<每次任务唯一的键>" } }
CLI: autotest run --journey new_run --character-id IRONCLAD \
       --timeout 600 --evidence full --detach \
       --idempotency-key <每次任务唯一的键>
```

返回：任务编号 `run_id`（必须保存）与初始状态 `QUEUED`。

**防重规则**：重试提交必须复用同一个 `idempotency_key`。相同键返回同一个 `run_id`（`created_at` 不变），平台不会启动第二局游戏；不同键才会创建新任务。

### 3. get_run — 查询进度

```text
MCP: tools/call { "name": "get_run", "arguments": { "run_id": "<run_id>" } }
CLI: autotest status <run_id> --json
```

返回：状态、阶段、进度（当前章节/楼层/页面/房间数/最近动作）。

### 4. cancel_run — 取消任务

```text
MCP: tools/call { "name": "cancel_run", "arguments": { "run_id": "<run_id>" } }
CLI: autotest cancel <run_id>
```

取消执行中的任务：平台清理旧局、回到干净主菜单并封存证据，终态为 `CANCELLED`。

### 5. resume_run — 恢复任务

```text
MCP: tools/call { "name": "resume_run", "arguments": { "run_id": "<原 run_id>" } }
CLI: autotest resume <run_id>
```

为已取消/失败的任务创建新任务（新 `run_id`，`resumed_from` 指向原任务），从干净起点继续执行。

### 6. get_report — 获取结果和证据

```text
MCP: tools/call { "name": "get_report", "arguments": { "run_id": "<run_id>" } }
CLI: autotest report <run_id>
```

返回：终态、状态轨迹、最后成功位置、失败归类（如适用）、截图/日志清单和证据压缩包路径。

## 三、状态模型（两个通道完全一致）

```text
QUEUED → RUNNING → PASSED            （目标达成）
                 → FAILED_PRODUCT    （被测对象不符合预期）
                 → FAILED_PLATFORM   （平台自身问题）
                 → BLOCKED_ENVIRONMENT（游戏/Steam/控制入口不可用）
                 → CANCELLED         （被取消，已清理并封存证据）
```

最小调用循环：

```text
capabilities → submit_run（保存 run_id 与幂等键）
  ↓
循环 get_run
  ├─ 非终态：继续等待
  ├─ BLOCKED_ENVIRONMENT / FAILED_PLATFORM：停止，交环境或平台处理
  └─ PASSED / FAILED_PRODUCT / CANCELLED：调用 get_report
```

断线不代表测试失败：重新连接后先用原 `run_id` 查询；任务仍在就继续等，已结束就直接取报告。Agent 退出或网络中断不会丢失任务。

## 四、统一短目标（多 Agent 验收用）

```text
journey       = new_run        （新局到稳定地图）
character_id  = IRONCLAD
route_policy  = leftmost（默认）
combat_mode   = traversal（默认）
evidence      = full
timeout       = 600
idempotency_key = <每个 Agent 自己唯一的键>
```

每个 Agent 必须使用自己的幂等键，不允许复用其他 Agent 的任务编号。
