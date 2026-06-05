# 首场战斗烟雾测试 — 使用 STS2-Agent API 驱动

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 改造 `TestAgentRunner._step_game_smoke()`，从裸 `sts2` CLI 命令切换到通过 `AgentAdapter` 调用 sts2-agent 的 HTTP API，完成首场战斗导航、截图、打牌验证，并将产出保存到 `automation/autotest/output/`。

**架构：** 最小侵入方案。不改 Orchestrator / Config / CaseRegistry。只在 `test_agent_runner.py` 中：
1. 修改 artifact 目录计算路径
2. 添加 `_capture_screenshot()` 和 `_save_state_snapshot()` 辅助方法
3. 重写 `_step_game_smoke()` 使用 `AgentAdapter` 驱动游戏
4. 对每张手牌截图 + 打牌 + 验证 damage/block 数值 + 截图

**技术栈：** Python 3.11+ / asyncio / httpx / mss / sts2-autotest AgentAdapter / pydantic

---

## 文件结构

| 角色 | 文件 | 说明 |
|------|------|------|
| 修改 | `src/sts2_autotest/core/test_agent_runner.py` | artifact 目录 + 重写 smoke test + 截图 |
| 修改 | `test-plans/gawain-localization-smoke.yaml` | 更新 evidence 路径 |
| 新建 | `tests/unit/test_smoke_card_validation.py` | mock AgentAdapter 验证截图 + 打牌逻辑 |

---

### 任务 1：修改 artifact 目录路径

**文件：** `src/sts2_autotest/core/test_agent_runner.py:375`

将 artifact 目录从 `.agent-runs/{task_id}` 改为 `automation/autotest/output/{task_id}`。

- [ ] **步骤 1：修改 `_artifact_dir` 计算**

将：
```python
self._artifact_dir = self._mod_project_path / ".agent-runs" / task_id
```
改为：
```python
self._artifact_dir = self._mod_project_path / "automation" / "autotest" / "output" / task_id
```

- [ ] **步骤 2：运行 unit test 验证**

运行：`python -m pytest tests/unit/ -k "test_agent" -v --co -q`
预期：现有测试通过

- [ ] **步骤 3：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py
git commit -m "fix: change artifact directory from .agent-runs to automation/autotest/output"
```

---

### 任务 2：添加截图和状态快照辅助方法

**文件：** `src/sts2_autotest/core/test_agent_runner.py`

添加两个简单辅助方法，用于在 smoke test 中收集证据。

- [ ] **步骤 1：确认 mss 可用**

运行：`python -c "import mss; print(mss.__version__)"`
预期：输出 mss 版本号

- [ ] **步骤 2：添加 `_capture_screenshot()` 方法**

在 `_ensure_dirs` 方法之后添加：

```python
def _capture_screenshot(self, name: str) -> str:
    """Capture full-screen screenshot and save to screenshot dir.

    Uses mss to grab the primary monitor. Saves as PNG.
    Returns the relative evidence path (for the report).
    Returns empty string on failure (non-blocking).
    """
    try:
        import mss
        path = self._screenshot_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            sct.shot(mon=1, output=str(path))
        return str(path.relative_to(self._mod_project_path))
    except Exception as exc:
        logger.warning("Screenshot failed (%s): %s", name, exc)
        return ""
```

- [ ] **步骤 3：添加 `_save_state_snapshot()` 方法**

```python
def _save_state_snapshot(self, step_name: str, state_dict: dict) -> str:
    """Save a state JSON snapshot to state dir.

    Returns the relative evidence path (for the report).
    """
    import json
    path = self._state_dir / f"{step_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path.relative_to(self._mod_project_path))
```

- [ ] **步骤 4：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py
git commit -m "feat: add _capture_screenshot and _save_state_snapshot helpers"
```

---

### 任务 3：重写 `_step_game_smoke()` - AgentAdapter 集成

**文件：** `src/sts2_autotest/core/test_agent_runner.py`

核心改动：用 `AgentAdapter` 替换裸 `sts2` CLI 命令。

- [ ] **步骤 1：在文件顶部 import AgentAdapter**

文件头部添加：
```python
from sts2_autotest.adapters.agent import AgentAdapter
```

- [ ] **步骤 2：重写 `_step_game_smoke()` 的 6a 部分（等待 API）**

将原来的 `sts2 ping` 循环替换为轮询 AgentAdapter health：

```python
# --- 6a: Wait for sts2-agent HTTP API ---
import asyncio

agent = AgentAdapter(endpoint="http://127.0.0.1:8080", timeout=10)
health_ok = False
deadline = time.time() + self._ping_timeout
while time.time() < deadline:
    try:
        health = asyncio.run(agent.health_check())
        if health.healthy:
            health_ok = True
            break
    except Exception:
        pass
    time.sleep(3)

if not health_ok:
    raise _Blocked(
        f"STS2-Agent HTTP API did not respond within {self._ping_timeout}s. "
        "Ensure the game is running with STS2AIAgent mod loaded."
    )
self._add("STS2-Agent Health", "PASSED", "http://127.0.0.1:8080/health")
```

- [ ] **步骤 3：添加导航到首场战斗的辅助方法**

```python
def _navigate_to_first_combat(self, agent: AgentAdapter) -> dict:
    """Navigate from MAIN_MENU to the first combat encounter.

    Returns the combat state dict after entering combat.
    Raises _Failed on any navigation failure.
    """
    import asyncio

    nav_steps = [
        ("open_character_select", {}),
        ("select_character", {"option_index": 0}),  # Gawain
        ("embark", {}),
        ("choose_map_node", {"option_index": 0}),  # first node = combat
    ]

    for action_name, params in nav_steps:
        result = asyncio.run(agent.act(action_name, params))
        if result.status != "success":
            raise _Failed(
                f"Navigation failed at '{action_name}': {result.detail}"
            )
        asyncio.run(agent.wait_until_actionable(timeout=15))

    state = asyncio.run(agent.get_state())
    state_dict = dict(state) if hasattr(state, "__dict__") else {}
    if state_dict.get("screen") != "COMBAT":
        raise _Failed(
            f"Expected COMBAT screen, got {state_dict.get('screen')}"
        )
    return state_dict
```

- [ ] **步骤 4：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py
git commit -m "feat: add _navigate_to_first_combat using AgentAdapter"
```

---

### 任务 4：打牌验证逻辑

**文件：** `src/sts2_autotest/core/test_agent_runner.py`

对首战初始手牌逐张截图、打出、验证数值。

- [ ] **步骤 1：添加 `_verify_card_and_screenshot()` 方法**

```python
def _verify_card_and_screenshot(
    self, agent: AgentAdapter, card: dict, card_index: int, target_index: int,
) -> dict:
    """Play one card: screenshot before, play, verify, screenshot after.

    card is a dict from combat.hand[] with card_id, name, index, energy_cost,
    playable, dynamic_values.

    Returns a dict with verification results for the report.
    """
    import asyncio

    card_id = card.get("card_id", f"card_{card_index}")
    card_name = card.get("name", card_id)
    result = {
        "card_id": card_id,
        "name": card_name,
        "index": card_index,
        "status": "UNKNOWN",
        "expected_damage": 0,
        "actual_damage": 0,
        "expected_block": 0,
        "actual_block": 0,
        "screenshot_before": "",
        "screenshot_after": "",
        "error": "",
    }

    for dv in card.get("dynamic_values", []):
        name = dv.get("name", "")
        if name == "damage":
            result["expected_damage"] = dv.get("current_value", dv.get("base_value", 0))
        elif name == "block":
            result["expected_block"] = dv.get("current_value", dv.get("base_value", 0))

    result["screenshot_before"] = self._capture_screenshot(f"card-{card_id}-before.png")

    # Before state
    before = asyncio.run(agent.get_state())
    before_dict = dict(before) if hasattr(before, "__dict__") else {}
    combat_before = before_dict.get("combat", {}) or {}
    enemies_before = combat_before.get("enemies", [])
    enemy_hp_before = enemies_before[0].get("current_hp", 0) if enemies_before else 0
    player_block_before = combat_before.get("player", {}).get("block", 0)
    self._save_state_snapshot(f"card-{card_id}-before", before_dict)

    # Play card
    try:
        play_result = asyncio.run(
            agent.act("play_card", {"card_id": card_id, "target_index": target_index})
        )
    except Exception as exc:
        result["status"] = "FAIL"
        result["error"] = f"play_card failed: {exc}"
        return result

    if play_result.status != "success":
        result["status"] = "FAIL"
        result["error"] = f"play_card: {play_result.status}: {play_result.detail}"
        return result

    asyncio.run(agent.wait_until_actionable(timeout=10))

    # After state
    after = asyncio.run(agent.get_state())
    after_dict = dict(after) if hasattr(after, "__dict__") else {}
    combat_after = after_dict.get("combat", {}) or {}
    enemies_after = combat_after.get("enemies", [])
    enemy_hp_after = enemies_after[0].get("current_hp", 0) if enemies_after else 0
    player_block_after = combat_after.get("player", {}).get("block", 0)
    self._save_state_snapshot(f"card-{card_id}-after", after_dict)
    result["screenshot_after"] = self._capture_screenshot(f"card-{card_id}-after.png")

    # Verify
    errors = []
    if result["expected_damage"] > 0:
        hp_diff = enemy_hp_before - enemy_hp_after
        result["actual_damage"] = hp_diff
        if hp_diff != result["expected_damage"]:
            errors.append(f"damage: expected {result['expected_damage']}, got {hp_diff}")
    if result["expected_block"] > 0:
        block_gained = player_block_after - player_block_before
        result["actual_block"] = block_gained
        if block_gained != result["expected_block"]:
            errors.append(f"block: expected {result['expected_block']}, got {block_gained}")

    result["status"] = "OK" if not errors else "FAIL"
    if errors:
        result["error"] = "; ".join(errors)
    return result
```

- [ ] **步骤 2：在 `_step_game_smoke()` 中集成打牌验证循环**

替换原来的 6b/6c 部分：

```python
# --- 6b: Navigate to first combat ---
state = self._navigate_to_first_combat(agent)
self._add("First Combat Reached", "PASSED",
    self._save_state_snapshot("combat-start", state))

# --- 6c: Read hand ---
combat = state.get("combat", {}) or {}
hand = combat.get("hand", [])
if not hand:
    raise _Failed("No cards in hand at combat start")

# --- 6d: Verify each card ---
self._card_results = []
for card in hand:
    card_index = card.get("index", 0)
    result = self._verify_card_and_screenshot(agent, card, card_index, 0)
    self._card_results.append(result)

passed_count = sum(1 for r in self._card_results if r["status"] == "OK")
failed = [r for r in self._card_results if r["status"] != "OK"]

if failed:
    detail = "; ".join(f"{r['name']}({r['card_id']}): {r['error']}" for r in failed)
    raise _Failed(f"Card verification: {len(failed)} failed ({detail})")

self._add("Card Smoke Test", "PASSED",
    f"Verified {passed_count} cards; "
    f"screenshots in automation/autotest/output/{self._task_id}/screenshots/")

# --- 6e: Clean up ---
asyncio.run(agent.act("abandon_run"))
self._add("Abandon Run", "PASSED", "")

# --- 6f: Scan for raw keys in final state ---
final_state = asyncio.run(agent.get_state())
final_dict = dict(final_state) if hasattr(final_state, "__dict__") else {}
import json
final_json = json.dumps(final_dict)
raw_patterns = ["GAWAIN_", "MISSING", "missing localization", "KeyNotFound"]
for pattern in raw_patterns:
    if pattern.lower() in final_json.lower():
        raise _Failed(f"Raw key found after combat: {pattern}")

self._add("No Raw Key", "PASSED",
    self._save_state_snapshot("final-state", final_dict))
```

- [ ] **步骤 3：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py
git commit -m "feat: add card-by-card combat verification in smoke test"
```

---

### 任务 5：更新测试报告

**文件：** `src/sts2_autotest/core/test_agent_runner.py`

在 `_write_report()` 中补充打牌验证表格。

- [ ] **步骤 1：添加 `_build_card_detail_table()` 辅助方法**

```python
def _build_card_detail_table(self) -> str:
    """Build card verification table from _card_results."""
    if not hasattr(self, "_card_results") or not self._card_results:
        return ""
    rows = "| 卡牌 | ID | 预期伤害 | 实际伤害 | 预期格挡 | 实际格挡 | 状态 | 截图 |\n"
    rows += "|------|-----|---------|---------|---------|---------|------|------|\n"
    for r in self._card_results:
        rows += (
            f"| {r['name']} | {r['card_id']} "
            f"| {r['expected_damage']} | {r['actual_damage']} "
            f"| {r['expected_block']} | {r['actual_block']} "
            f"| {r['status']} | before: {r['screenshot_before']}<br>after: {r['screenshot_after']} |\n"
        )
    return rows
```

- [ ] **步骤 2：更新报告模板**

在 `_write_report` 的测试结果表后插入：
```python
card_table = self._build_card_detail_table()
if card_table:
    report += "\n## 卡牌验证详情\n\n" + card_table
```

也在附件区域加入 screenshots 和 state 目录引用：
```python
# Update attachments section
report += "- screenshots: screenshots/\n"
report += "- state snapshots: state/\n"
```

- [ ] **步骤 3：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py
git commit -m "feat: add card verification details to test report"
```

---

### 任务 6：更新测试计划 YAML

**文件：** `test-plans/gawain-localization-smoke.yaml`

- [ ] **步骤 1：更新 paths 和 evidence**

将 `report_output` 改为：
```yaml
  report_output: ../STS2-GAWAIN/automation/autotest/output/gawain-localization-key-fix/test-report.md
```

在 `first_combat_smoke` 中添加 evidence 项：
```yaml
    evidence:
      - state_json
      - cli_log
      - game_log
      - screenshots/card-*.png
      - state/step-*.json
```

在 `artifacts` 中添加 screenshot 和 state：
```yaml
artifacts:
  - screenshots
  - state_snapshots
```

- [ ] **步骤 2：Commit**

```bash
git add test-plans/gawain-localization-smoke.yaml
git commit -m "docs: update evidence paths in smoke test plan"
```

---

### 任务 7：编写单元测试

**文件：** 新建 `tests/unit/test_smoke_card_validation.py`

- [ ] **步骤 1：编写 mock 测试**

```python
"""Test card smoke validation logic with mocked AgentAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.core.test_agent_runner import TestAgentRunner


class TestSmokeCardValidation:

    def test_screenshot_saves_file(self, tmp_path):
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = tmp_path / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._screenshot_dir.mkdir(parents=True)

        with patch("sts2_autotest.core.test_agent_runner.mss") as mock_mss:
            mock_sct = MagicMock()
            mock_mss.mss.return_value.__enter__.return_value = mock_sct
            mock_sct.monitors = [{}, {"width": 1920, "height": 1080}]
            result = runner._capture_screenshot("test-card.png")

        assert "screenshots/test-card.png" in result

    def test_verify_card_attack_ok(self, tmp_path):
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = tmp_path / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._state_dir = runner._artifact_dir / "state"

        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(
            status="success", state_changed=True
        )

        from sts2_autotest.common.state import GameScreen, GameState
        before = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        after = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 44, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        mock_agent.get_state.side_effect = [before, after]

        card = {
            "card_id": "STRIKE",
            "name": "Strike",
            "index": 0,
            "energy_cost": 1,
            "playable": True,
            "dynamic_values": [
                {"name": "damage", "base_value": 6, "current_value": 6}
            ],
        }

        with patch.object(runner, "_capture_screenshot", return_value="screenshots/test.png"):
            with patch.object(runner, "_save_state_snapshot", return_value="state/test.json"):
                result = runner._verify_card_and_screenshot(
                    mock_agent, card, card_index=0, target_index=0
                )

        assert result["status"] == "OK"
        assert result["expected_damage"] == 6
        assert result["actual_damage"] == 6

    def test_verify_card_block_ok(self, tmp_path):
        """Block card test: verify block gained matches expected."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = tmp_path / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._state_dir = runner._artifact_dir / "state"

        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(
            status="success", state_changed=True
        )

        from sts2_autotest.common.state import GameScreen, GameState
        before = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        after = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 5, "current_hp": 80},
                "hand": [],
            },
        )
        mock_agent.get_state.side_effect = [before, after]

        card = {
            "card_id": "DEFEND",
            "name": "Defend",
            "index": 1,
            "energy_cost": 1,
            "playable": True,
            "dynamic_values": [
                {"name": "block", "base_value": 5, "current_value": 5}
            ],
        }

        with patch.object(runner, "_capture_screenshot", return_value="screenshots/test.png"):
            with patch.object(runner, "_save_state_snapshot", return_value="state/test.json"):
                result = runner._verify_card_and_screenshot(
                    mock_agent, card, card_index=1, target_index=0
                )

        assert result["status"] == "OK"
        assert result["expected_block"] == 5
        assert result["actual_block"] == 5
```

- [ ] **步骤 2：运行测试验证通过**

运行：`python -m pytest tests/unit/test_smoke_card_validation.py -v`
预期：所有测试 PASS

- [ ] **步骤 3：Commit**

```bash
git add tests/unit/test_smoke_card_validation.py
git commit -m "test: add unit tests for card smoke validation"
```
