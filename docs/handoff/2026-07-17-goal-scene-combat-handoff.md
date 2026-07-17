# 2026-07-17 目标场景与战斗处理交接

## 结论

当前阶段结论：**PARTIAL，可继续交接**。

- 公共 MCP 的 10 个目标场景均已有真实运行记录；其中 `NEXT_ACT` 已完成第一章到第二章的完整通路验收。
- 普通通路战斗已经按用户规则使用控制台快速结束，并验证了战斗离开、奖励处理和地图继续推进。
- 卡牌专测分支和角色死亡分支尚未完成真实验收，不能把当前结果写成“所有目标类型都已完成”。
- 证据压缩包、运行终态和服务重启后的历史查询均已验证。

## 用户要求的战斗决策

后续实现和验收必须保持下面的优先级：

1. **卡牌测试**：通过控制台把指定卡牌加入手牌，再验证该卡牌对应功能。
2. **角色死亡测试**：每个回合自动结束玩家回合，直到角色被怪物击杀。
3. **普通通路**：不属于前两类时，使用控制台结束战斗，然后继续下一个节点。

本轮真实运行只覆盖第 3 类；没有添加 Gawain 角色或卡牌专用分支。

## 已完成的公共平台改动

### 导航和战斗

- 真实战斗判定增加了屏幕、战斗数据、存活敌人和可用战斗动作的联合检查，避免把伪战斗当作已进入战斗。
- `combat_mode=traversal` 且公共环境公开开发能力时，优先使用 `win_combat`；报告能力中明确标记为开发专用通路验证。
- 基础战斗策略会跳过运行时明确标记为不可出牌的卡牌，避免误打出当前不可用牌。
- 地图会等待旅行结束和本地投票结束后再选择节点。
- 事件会选择第一个未锁定选项；卡包选择页会选择第一个卡包并处理确认。
- 卡牌选择过场交由上层按不同索引逐张推进，避免事件收尾阶段重复点击同一张牌。
- 休息房间会过滤 `is_enabled=false` 的选项，不再选择页面上已禁用的锻造选项。

### 新局、章节和证据

- 调试模式下，主菜单清理存档使用真实菜单动作；运行中的地图、战斗等页面才使用调试控制台复位，避免把主菜单动作误映射成 `die`。
- `NEXT_ACT` 已要求真实观察到第二章事件、处理事件并到达稳定第二章地图，不能只根据章节数字或伪状态判定通过。
- 证据包文件名稳定，当前运行不会在保留策略中把自己删除；终态报告会重新封存最终清单。
- `get_report` 会读取本地证据包或压缩包，并返回真实 artifact 状态、截图/日志计数和归档校验结果。

主要改动文件：

- `src/sts2_autotest/adapters/agent.py`
- `src/sts2_autotest/core/navigation.py`
- `src/sts2_autotest/core/journeys.py`
- `src/sts2_autotest/evidence/packager.py`
- `src/sts2_autotest/core/evidence_hooks.py`
- `src/sts2_autotest/cli/main.py`
- `src/sts2_autotest/cli/mcp_tools.py`
- `tests/unit/test_agent_adapter.py`
- `tests/unit/test_goal_scene_executor.py`
- `tests/unit/test_journey_foreground.py`
- `tests/unit/test_journeys.py`
- `tests/unit/test_navigation_flow.py`
- `tests/unit/test_p0_evidence_persistence.py`

## 公共 MCP 真实验收矩阵

每条成功记录均按 `capabilities → submit_run → get_run → get_report` 完成，使用 `evidence=full`。成功压缩包均位于 `tests/output/artifacts/`。

| 目标 | 角色 | 运行 ID | 结果 | 备注 |
|---|---|---|---|---|
| `MAIN_MENU` | IRONCLAD | `run-20260717-001842-3ea783fc` | PASSED | 入口状态 |
| `CHARACTER_SELECT` | IRONCLAD | `run-20260717-001928-efdb0c86` | PASSED | 角色选择页 |
| `EVENT` | IRONCLAD | `run-20260717-002004-fe3e8d9f` | PASSED | 开局事件 |
| `MAP` | IRONCLAD | `run-20260717-002728-81b2e91e` | PASSED | 稳定地图 |
| `COMBAT` | IRONCLAD | `run-20260717-003124-b29596f9` | PASSED | 真实战斗入口；该次不是快速结束验收 |
| `REST` | IRONCLAD | `run-20260717-010731-6a5ec830` | PASSED | 经过战斗和奖励后到达休息房间 |
| `SHOP` | IRONCLAD | `run-20260717-010454-83669bb0` | PASSED | 多场 `win_combat` 后到达商店 |
| `CHEST` | IRONCLAD | `run-20260717-012024-b3424ade` | PASSED | 左侧路线；经过商店和多场战斗 |
| `CARD_REWARD` | IRONCLAD | `run-20260717-012735-57bf0b37` | PASSED | 真实卡牌奖励页 |
| `NEXT_ACT` | IRONCLAD | `run-20260717-014540-ad028d62` | PASSED | 第一章完整通路 → 第二章事件 → 稳定地图 |

额外角色短目标：

- `SILENT` → `CHARACTER_SELECT`：`run-20260717-014957-67ae856a`，PASSED。

最终 `NEXT_ACT` 关键证据：

- `act_id` 从 `0` 变为 `1`（报告中的 `current_chapter=2`）。
- 第二章事件 `OROBAS` 被观察并处理。
- 最终状态为第二章 `MAP`，floor 0，地图稳定。
- 62 个操作、17 次地图路线记录、43 张截图、1 份日志。
- 压缩包校验：`counts_match=true`，artifact 可读。

## 已保留的失败/诊断记录

不要删除或覆盖这些运行；它们解释了为什么后续做了对应修复：

- `run-20260717-010946-26e69078`：CHEST 目标因普通事件未处理“未结束但可选”的下一项，在 EVENT 失败。
- `run-20260717-011313-db4bfa41`：事件触发 `CARD_SELECTION`，游戏返回 `max_select=0`，旧处理反复尝试并失败。
- `run-20260717-012904-ee1941cb`：NEXT_ACT 卡在未处理的 `BUNDLE_SELECTION`，随后取消并保留证据。
- `run-20260717-013708-6c78f27f`：NEXT_ACT 在 REST 选择了页面禁用的锻造选项。

这些失败均有可读压缩包和 `FAILED_PLATFORM`/`CANCELLED` 终态，不应改写成成功。

## 当前环境

- 工作目录：`/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST`
- 游戏 API：`http://127.0.0.1:8080`
- 公共 MCP：`http://127.0.0.1:8090/mcp`
- 当前公共 MCP 已重启，调试能力实际生效；重启后再次查询 `run-20260717-014540-ad028d62` 仍为 `PASSED`，`get_report` 仍返回 43 张截图、1 份日志和可读压缩包。
- 当前游戏在 SILENT 短目标后位于 `CHARACTER_SELECT`；不要假设仍处于 NEXT_ACT 的第二章地图。
- 启动公共 MCP 的命令：

```bash
STS2_ADAPTER__AGENT__DEBUG_ACTIONS=true \
PYTHONPATH=src python3 -m sts2_autotest.cli.mcp_server \
  --host 127.0.0.1 --port 8090
```

启动前先检查 8090 是否已有服务，避免重复启动。游戏调试能力是一次性启动环境变量，不要写入持久系统环境：

```bash
env STS2_ENABLE_DEBUG_ACTIONS=1 open -b com.megacrit.SlayTheSpire2
```

## 验证结果

已通过：

```bash
PYTHONPATH=src pytest -q tests/unit tests/integration
# 1581 passed, 6 skipped, 2 warnings

python3 -m compileall -q src
git diff --check
```

已知警告：

- 两个既有 `TestAgentRunner` 类收集警告。
- 一条截图测试因当时游戏窗口不可用而跳过截图；不影响本轮公共 MCP 证据包。
- 不要把历史上默认全量收集时由生成文件重复导致的收集错误误判成此次业务失败；如下一个 Agent 需要重新跑全量，先区分收集层问题和测试断言问题。

## 下一 Agent 必须继续的工作

### 1. 完成卡牌专测

- 明确一个实际卡牌目标和成功断言。
- 通过公共任务入口调用调试 `give_card`，不要直接改牌组文件；运行时卡牌 ID 需要转换成游戏控制台格式。
- 验证卡牌确实进入手牌、执行对应功能，并在报告中保留状态 JSON、截图和日志。

### 2. 完成角色死亡测试

- 新增或明确一个“死亡测试”任务模式/目标，不要复用普通 `traversal` 的 `win_combat`。
- 战斗中每回合只执行 `end_turn`，直到游戏真实进入 `GAME_OVER`；报告必须证明每回合动作和最终死亡状态。
- 如果公共契约还没有死亡模式，先补契约和 capabilities，再做真实 MCP 验收。

### 3. 修复截图过渡滞后

- 本轮最终 `NEXT_ACT` API 终态是第二章 MAP，但视觉检查到的 `MAP` 命名截图仍显示卡牌选择界面。
- 这不是黑屏或桌面误采集，而是截图时机早于画面稳定；下一 Agent 应在截图前等待稳定状态，重新生成一张与最终状态一致的视觉凭证。

### 4. 最终收尾

- 重新检查 `git status`，保留用户已有修改，不做 reset/checkout/清理无关文件。
- 不要提交或推送，除非用户另行授权。
- 完成卡牌和死亡分支后，再把总体结论从 `PARTIAL` 改为真实对应的状态。

## 工作区注意事项

当前工作区已有用户原有修改，包括 `AGENT.md`、`CLAUDE.md`、`README.md`、若干 `docs/` 文件、`.env`、`.serena/`、`.workbuddy/` 和 `reports/`；本轮源码和测试改动也尚未提交。本次交接整理只新增本文件；下一个 Agent 不得用 destructive git 命令清理这些变更。
