# 使用 STS2-Agent 驱动首场战斗烟雾测试

## 问题

autotest 的 `TestAgentRunner._step_game_smoke()` 当前只做了两件事：

1. `sts2 ping` — 确认 STS2-Cli-Mod 可用
2. `sts2 state -p` — 读取初始状态并扫原始 Key

测试计划 `gawain-localization-smoke.yaml` 中列出的 `first_combat_smoke`
（进入首场战斗并实际打出一张 Gawain 卡牌）从未真正实现。

同时，游戏中已安装 **STS2-Agent Mod (v0.7.2)**，它提供了比 STS2-Cli-Mod
更丰富的 HTTP API，包括 `play_card`、`choose_map_node`、`collect_rewards_and_proceed`
等高层动作，以及稳定的状态读取和事件机制。

## 目标

让 autotest 能稳定执行以下流程并产出测试报告：

1. 启动游戏 → 等待 Mod API 就绪
2. 导航到首场战斗（主菜单 → 选角色 → 选地图节点 → 战斗加载）
3. 对每张初始手牌：
   - 截图（卡牌打出前）
   - 打出卡牌
   - 验证数值：攻击牌扣血准确 / 防御牌加格挡准确
   - 截图（卡牌打出后）
4. 收集证据：截图、状态 JSON、日志
5. 输出测试报告

## 方案：改造 `TestAgentRunner._step_game_smoke()`

最小侵入方案。不改 Orchestrator，不改 Config，不改 `CaseRegistry`。
只在 smoke test 这一步用 `AgentAdapter` 驱动 sts2-agent 的 HTTP API。

### 改动范围

| 文件 | 改动 |
|------|------|
| `test_agent_runner.py` | 重写 `_step_game_smoke()`，使用 `AgentAdapter` |
| `test_agent_runner.py` | 新加 `_capture_screenshot()` 方法 |
| `test_agent_runner.py` | 新加 `_verify_card()` 方法 |
| `evidence/capture.py` | 确认截图 API 可用（`mss` 已依赖） |
| `adapters/agent.py` | 可选：补充 `get_combat_detail()` 或确认 `play_card` 后的 state 字段 |

### 执行流程（伪代码）

```
async def _step_game_smoke(self):
    # 1. 等待 sts2-agent HTTP API 就绪
    adapter = AgentAdapter(endpoint="http://127.0.0.1:8080")
    await wait_for_health(adapter, timeout=90)

    # 2. 导航到首场战斗
    await act("open_character_select")
    await act("select_character", {"character": "GAWAIN"})
    await act("embark")
    await act("choose_map_node", {"option_index": 0})
    # choose_map_node 0 必定进入第一场战斗
    # 等战斗加载完成
    await wait_for_screen("COMBAT", timeout=30)

    # 3. 读手牌信息
    state = await adapter.get_state()
    hand = state.combat.hand  # [{card_id, name, damage, block, cost}]

    # 4. 对每张手牌截图+打出+验证
    for card in hand:
        # 截图 before
        cap = await self._capture_screenshot(f"card-{card.card_id}-before.png")

        # 获取当前敌人/玩家状态（用于验证）
        before = await adapter.get_state()
        enemy_hp_before = before.combat.enemies[0].hp
        player_block_before = before.combat.player.block

        # 打出卡牌
        result = await act("play_card", {
            "card_id": card.card_id,
            "target_index": 0,
        })

        # 等待动作生效
        await wait_until_actionable(timeout=10)

        # 获取动作后状态
        after = await adapter.get_state()
        enemy_hp_after = after.combat.enemies[0].hp
        player_block_after = after.combat.player.block

        # 验证
        if card.damage > 0:
            hp_diff = enemy_hp_before - enemy_hp_after
            assert hp_diff == card.damage, \
                f"{card.card_id}: expected {card.damage} damage, got {hp_diff}"
        if card.block > 0:
            block_gained = player_block_after - player_block_before
            assert block_gained == card.block, \
                f"{card.card_id}: expected {card.block} block, got {block_gained}"

        # 截图 after
        cap2 = await self._capture_screenshot(f"card-{card.card_id}-after.png")

        results.append(CardResult(card.card_id, "OK", card.damage, card.block))

    # 5. 收尾：放弃当前 run
    await act("abandon_run")
```

### 截图机制

已有依赖 `mss`（`pyproject.toml` 中的 `mss>=9.0`），可以在 `evidence/capture.py`
中调用全屏截图。smoke test 中直接调用：

```python
from sts2_autotest.evidence.capture import capture_screenshot
capture_screenshot(path)
```

截图保存到 `automation/autotest/output/{task_id}/screenshots/` 目录。

### 状态 JSON 快照

每步保存状态 JSON 到 `state/step-{n}-{action}.json`，作为断言证据。

### 数值验证策略

手牌卡片的 damage/block 信息通过 `combat.hand[].dynamic_values` 取得，
其中 `name` 为 `"damage"` / `"block"` 的条目包含 `base_value` 和 `current_value`。

简单精确匹配：

- **攻击牌**：`enemy_hp_before - enemy_hp_after == card.current_value("damage")`
  （首战敌人无格挡、无易伤等修正，精确匹配即可）
- **防御牌**：`player_block_after - player_block_before == card.current_value("block")`
  （首战时无 buff/debuff 干扰）

> sts2-agent 的 `play_card` 接受 `card_index`（手牌位置）作为参数，
> `AgentAdapter._resolve_agent_action_args` 会自动将 `card_id` 转换为 `card_index`。
> `choose_map_node` 和 `select_character` 使用 `option_index` 参数。

### 测试计划更新

更新 `test-plans/gawain-localization-smoke.yaml`，在 `first_combat_smoke` 的
evidence 列表中补充：

```
screenshots/card-*.png
state/step-*.json
```

### 错误处理

| 场景 | 行为 |
|------|------|
| sts2-agent API 在超时内连接不上 | BLOCKED，提示检查 Mod 安装 |
| `play_card` 失败（如费用不足） | FAILED，记录卡牌 + 错误 |
| 截图失败 | WARNING 但继续 |
| 数值不符 | FAILED，记录预期 vs 实际 |
| 战斗加载超时 | FAILED |

### 证据结构

所有测试产出遵守 `sts2-workspace.yaml` 的约定，保存在 mod 项目下的 `automation/autotest/`：

```
STS2-GAWAIN/automation/autotest/output/{task_id}/
  screenshots/
    card-STRIKE-before.png
    card-STRIKE-after.png
    card-DEFEND-before.png
    card-DEFEND-after.png
    ...
  state/
    step-0-after_launch.json
    step-1-character_select.json
    step-2-embark.json
    step-3-combat_start.json
    step-4-card-{card_id}-before.json
    step-4-card-{card_id}-after.json
  test-report.md
  game-smoke.log
```

对应 `test_agent_runner.py` 中目录计算从：
```python
self._artifact_dir = self._mod_project_path / ".agent-runs" / task_id
```
改为：
```python
self._artifact_dir = self._mod_project_path / "automation" / "autotest" / "output" / task_id
```

### 测试策略

- Unit test：mock `AgentAdapter`，验证截图路径、证据收集、报告生成
- Integration test：需要游戏运行 + STS2-Agent Mod 加载，手动触发
- 首次集成测试：在用户机器上实际运行确认
