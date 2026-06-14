# STS2-AUTOTEST 用户使用手册

本文档面向准备实际使用 STS2-AUTOTEST 的测试人员、Mod 作者和自动化脚本维护者。它覆盖 MVP 版本的安装、配置、命令行、pytest 集成、Fluent DSL、证据产物、恢复机制和常见问题处理。

## 1. 项目概览

STS2-AUTOTEST 是《Slay the Spire 2》Mod 的端到端自动化测试框架。MVP 的核心链路是：

```text
测试用例 / autotest CLI / pytest
  -> STS2-AUTOTEST 编排器
  -> STS2-Cli-Mod adapter
  -> sts2 CLI
  -> 游戏内 STS2-Cli-Mod
  -> Slay the Spire 2
```

框架负责：

- 检查 Python、Steam、游戏、STS2-Cli-Mod、磁盘空间和会话锁状态。
- 通过 `sts2` CLI 读取游戏状态并执行游戏动作。
- 管理测试会话生命周期、状态转换、失败恢复、进度保存和中断恢复。
- 为 pytest 提供 fixture，并提供 Fluent DSL 编写游戏语义测试。
- 收集截图、Godot/游戏日志、运行摘要、Markdown 报告、JUnit XML 和 ZIP 归档。

## 2. 安装前准备

### 2.1 必备软件

- Python 3.11 或更高版本。
- Steam。
- 《Slay the Spire 2》。
- STS2-Cli-Mod 及其 `sts2` CLI 可执行文件。

### 2.2 推荐目录约定

建议在一个独立工作区中放置本项目，并使用虚拟环境：

```powershell
cd D:\workspace\STS2\STS2-AUTOTEST
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2.3 STS2-Cli-Mod 可执行文件发现规则

框架会按以下顺序寻找 `sts2`：

1. `STS2_CLI_PATH` 环境变量。
2. 系统 `PATH` 中的 `sts2`。
3. 常见安装目录，例如用户目录、Steam 游戏目录、Mod 目录、Program Files 等。

如果自动发现失败，最稳妥的做法是在 `.env` 或系统环境变量中设置：

```dotenv
STS2_CLI_PATH=C:\path\to\sts2.exe
```

游戏目录可通过 `STS2_GAME_PATH` 手动指定：

```dotenv
STS2_GAME_PATH=C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2
```

## 3. 安装

开发模式安装：

```powershell
python -m pip install -e ".[dev]"
```

仅运行框架：

```powershell
python -m pip install -e .
```

安装完成后应能使用：

```powershell
autotest --help
```

命令入口由 `pyproject.toml` 注册：

```toml
[project.scripts]
autotest = "sts2_autotest.cli.main:cli"
```

## 4. 快速启动

1. 启动 Steam。
2. 启动《Slay the Spire 2》。
3. 确认游戏内 STS2-Cli-Mod 已加载。
4. 检查 `sts2` CLI：

```powershell
sts2 ping
```

5. 检查 STS2-AUTOTEST 环境：

```powershell
autotest doctor
```

6. 运行全部测试：

```powershell
autotest run --all
```

7. 查看报告：

```powershell
autotest report latest
```

如果 `latest` 不存在，命令会列出当前 evidence 目录下可用的 run ID。

## 自然语言测试工作流

1. 编写 `specs/cases/*.md` 或 `specs/suites/*.md`
2. 运行 `autotest review`
3. 查看审查结果与修订建议
4. 运行 `autotest compile`
5. 运行 `autotest run --all`

## 5. 配置系统

配置来源有四层，后面的层会覆盖前面的层：

1. 内置默认值。
2. 项目根目录 `sts2-autotest.yaml`。
3. `.env` 与系统环境变量。
4. CLI 参数覆盖。

环境变量必须以 `STS2_` 开头，嵌套字段使用双下划线 `__` 分隔。例如：

```dotenv
STS2_FRAMEWORK__LOG_LEVEL=DEBUG
STS2_FRAMEWORK__EVIDENCE_DIR=tests/output
STS2_ADAPTER__CLI__CLI_PATH=sts2
STS2_ADAPTER__CLI__TIMEOUT=30.0
STS2_EXECUTION__MAX_RETRIES=3
STS2_STATE_MACHINE__POLL_INTERVAL=0.5
```

### 5.1 YAML 配置示例

在项目根目录创建 `sts2-autotest.yaml`：

```yaml
framework:
  log_level: INFO
  screenshot_dir: tests/output/screenshots
  evidence_dir: tests/output
  evidence_retention: 20
  strict_validation: false

adapter:
  cli:
    enabled: true
    cli_path: sts2
    timeout: 30.0
  agent:
    enabled: false
    endpoint: http://localhost:8080
    timeout: 30.0

execution:
  game_timeout: 60.0
  game_startup_timeout: 60.0
  max_retries: 3
  max_consecutive_failures: 3
  heartbeat_timeout: 60.0
  parallel: false

state_machine:
  transition_timeout: 10.0
  poll_interval: 0.5
```

### 5.2 常用配置项

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `framework.log_level` | `INFO` | 日志级别，可选 `DEBUG/INFO/WARNING/ERROR/CRITICAL`。 |
| `framework.screenshot_dir` | `tests/output/screenshots` | 截图输出目录。 |
| `framework.evidence_dir` | `tests/output` | 证据包根目录。 |
| `framework.evidence_retention` | `20` | 保留的证据包数量。 |
| `framework.strict_validation` | `false` | 是否对游戏状态校验失败立即报错。 |
| `adapter.cli.cli_path` | `sts2` | STS2-Cli-Mod CLI 路径或命令名。 |
| `adapter.cli.timeout` | `30.0` | 单次 CLI 调用超时时间。 |
| `execution.game_timeout` | `60.0` | 游戏动作等待超时。 |
| `execution.max_retries` | `3` | 默认重试次数。 |
| `execution.max_consecutive_failures` | `3` | 连续相同失败达到阈值后标记为确定性失败。 |
| `state_machine.poll_interval` | `0.5` | 状态轮询间隔。 |

## 6. 命令行使用

### 6.1 `autotest doctor`

检查当前机器是否具备运行条件：

```powershell
autotest doctor
```

检查项包括：

- Python 版本。
- Steam 是否安装。
- 《Slay the Spire 2》是否安装。
- STS2-Cli-Mod CLI 是否可发现。
- C 盘可用空间。
- 截图目录是否可写。
- 当前是否已有测试会话锁。

JSON 输出：

```powershell
autotest doctor --json
```

CI 友好输出：

```powershell
autotest doctor --ci
```

`--ci` 输出紧凑 JSON，并通过退出码表达健康状态。所有检查为 `OK` 时退出码为 0，存在 `FAIL` 或 `NOT_FOUND` 时退出码为 1。

### 6.2 `autotest run`

运行全部用例：

```powershell
autotest run --all
```

运行指定用例：

```powershell
autotest run --cases TC-001 TC-002
```

运行指定套件：

```powershell
autotest run --suite smoke
```

重新运行失败用例：

```powershell
autotest run --failed
```

设置单用例超时：

```powershell
autotest run --all --timeout 60
```

MVP 中，`run` 会创建 `CliModAdapter` 与 `TestOrchestrator`，并将传入的 case ID 或 suite 名交给编排器执行。当前 orchestrator 的默认用例执行逻辑主要验证游戏可响应性与可用动作；更复杂的业务断言建议通过 pytest/DSL 编写。

### 6.3 `autotest report`

查看指定运行的 `summary.json`：

```powershell
autotest report run_20260514T120000
```

指定 evidence 目录：

```powershell
autotest report run_20260514T120000 --evidence-dir tests/output
```

不传 run ID 时默认使用 `latest`：

```powershell
autotest report
```

如果找不到对应运行，命令会列出 evidence 目录下可用的运行目录。

## 7. 进度保存与恢复

框架默认会在每个 case 完成后写入：

```text
tests/output/.progress/session-progress.json
```

如果上一次运行被 Ctrl+C 或异常中断，再次运行时若发现进度文件存在，会提示：

```text
使用 --resume 继续，或使用 --no-resume 重新开始。
```

继续未完成的用例：

```powershell
autotest run --resume
```

忽略旧进度并重新开始：

```powershell
autotest run --no-resume --all
```

如果进度文件损坏，`--resume` 会降级为完整运行，并打印 warning。

## 8. pytest 集成

项目通过 pytest entry point 自动注册插件：

```toml
[project.entry-points.pytest11]
sts2_autotest = "sts2_autotest.pytest_plugin.plugin"
```

### 8.1 可用 fixture

| fixture | 作用 |
|---|---|
| `autotest` | 提供会话级 `TestOrchestrator`。 |
| `game_state` | 返回当前 `GameState` 快照。 |

测试函数必须是同步函数。插件会在内部管理 asyncio event loop；如果写成 `async def`，会抛出用户配置错误。

示例：

```python
from sts2_autotest.common.state import GameScreen


def test_game_is_readable(game_state):
    assert game_state.screen != GameScreen.UNKNOWN
```

使用 orchestrator：

```python
from sts2_autotest.core.action_model import ActionDescriptor


def test_can_end_turn_when_in_combat(autotest):
    action = ActionDescriptor(action_type="end_turn")
    # 仅当当前 screen 的 available_actions 包含 end_turn 时才应执行。
    # 真实用例通常先导航到 COMBAT，再执行断言。
```

## 9. Fluent DSL

Fluent DSL 用于以游戏语义组织测试步骤：

```python
import asyncio

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.state import GameScreen
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.dsl import (
    capture_screenshot,
    define,
    enter_combat,
    game_reached_state,
    log_state,
    play_card,
    start_game,
)


def test_card_flow():
    loop = asyncio.new_event_loop()
    adapter = CliModAdapter()
    orch = TestOrchestrator(adapter=adapter)

    result = (
        define("card-flow", orch, loop)
        .setup(start_game(), enter_combat("JawWorm"))
        .execute(play_card("Strike", target=0))
        .on_error(log_state, capture_screenshot)
        .assert_that(game_reached_state(GameScreen.COMBAT))
    )

    loop.close()
    assert result.passed, result.failures
```

### 9.1 动作描述函数

| 函数 | 说明 |
|---|---|
| `start_game(save=None)` | 开始或载入游戏。 |
| `enter_combat(enemy="")` | 进入指定敌人的战斗。 |
| `play_card(card_id, target=0)` | 打出指定卡牌。 |
| `end_turn()` | 结束当前回合。 |
| `set_seed(seed)` | 设置随机种子。 |
| `give_card(card_id)` | 给玩家添加卡牌。 |
| `set_hp(hp)` | 设置玩家 HP。 |

### 9.2 断言函数

| 函数 | 说明 |
|---|---|
| `game_reached_state(expected)` | 断言当前游戏 screen 等于目标状态。 |
| `enemy_hp_decreased_by(amount)` | 断言敌人 HP 至少下降指定值。 |
| `player_energy_decreased_by(amount)` | 断言玩家能量下降指定值。 |
| `player_hp_changed_by(amount)` | 断言玩家 HP 变化指定值，正数表示治疗，负数表示伤害。 |

这些断言依赖当前 `GameState` 中存在对应字段。例如 HP 对比类断言需要 `previous_hp`、`hp` 等字段由状态源提供。

### 9.3 错误处理函数

| 函数 | 说明 |
|---|---|
| `log_state` | 失败时记录当前 screen，并在可用时收集失败日志。 |
| `capture_screenshot` | 失败时尝试截图。需要 orchestrator 使用包含 capture 的 evidence hooks。 |

## 10. 测试数据 fixture

`FixtureLoader` 支持从 JSON/YAML 读取测试数据：

```python
from sts2_autotest.dsl import FixtureLoader


def test_cards_fixture():
    loader = FixtureLoader("fixtures")
    cards = loader.load_cards()
    assert cards
```

查找顺序：

1. `<name>.json`
2. `<name>.yaml`
3. `<name>.yml`

支持的便捷方法：

- `load(name, scope="test")`
- `load_cards(scope="test")`
- `load_relics(scope="test")`
- `load_seeds(scope="test")`

`scope` 可为 `test`、`class`、`session`。每个 scope 有独立缓存，返回值会深拷贝，避免测试之间互相污染。

示例 `fixtures/cards.yaml`：

```yaml
cards:
  - id: Strike
    cost: 1
  - id: Defend
    cost: 1
```

## 11. STS2-Cli-Mod 动作映射

适配器会根据当前 screen 推导可用动作。MVP 中的静态映射如下：

| 游戏状态 | 可用动作 |
|---|---|
| `MAIN_MENU` | `new_run`, `continue_run`, `choose_game_mode` |
| `CHARACTER_SELECT` | `select_character`, `set_ascension`, `embark` |
| `MAP` | `choose_map_node`, `proceed` |
| `COMBAT` | `play_card`, `end_turn`, `use_potion` |
| `SHOP` | `shop_buy_card`, `shop_buy_relic`, `shop_buy_potion`, `shop_remove_card` |
| `REST` | `choose_rest_option` |
| `EVENT` | `choose_event`, `advance_dialogue` |
| `CHEST` | `open_chest`, `pick_relic` |
| `BOSS_REWARD` | `reward_claim`, `relic_select`, `relic_skip` |
| `CARD_REWARD` | `reward_choose_card`, `reward_skip_card`, `reward_claim` |
| `RELIC_REWARD` | `reward_claim`, `relic_select`, `relic_skip` |
| `GAME_OVER` | `return_to_menu` |
| `VICTORY` | `return_to_menu` |

部分动作使用位置参数传给 `sts2`：

- `choose_game_mode <mode>`
- `select_character <character_id>`
- `choose_map_node <col> <row>`
- `choose_event <index>`
- `grid_card_select <index>`

其他动作默认转换为 `--key value` 形式。例如：

```python
await adapter.act("play_card", {"card_id": "Strike", "target": 0})
```

会转换为类似：

```text
sts2 play_card --card_id Strike --target 0
```

更多底层 STS2-Cli-Mod 命令见 [sts2-cli-mod-reference.md](sts2-cli-mod-reference.md)。

## 12. 游戏状态模型

`GameState` 是不可变状态快照，核心字段是 `screen`。为了兼容不同游戏版本和 Mod 返回的新字段，模型允许额外字段。

支持的 screen：

- `MAIN_MENU`
- `CHARACTER_SELECT`
- `MAP`
- `COMBAT`
- `SHOP`
- `REST`
- `EVENT`
- `CHEST`
- `BOSS_REWARD`
- `CARD_REWARD`
- `RELIC_REWARD`
- `GAME_OVER`
- `VICTORY`
- `CRASHED`
- `UNKNOWN`

`UNKNOWN` 表示适配器无法识别当前游戏状态。会话启动时如果读取到 `UNKNOWN`，编排器会拒绝继续执行。

## 13. 证据收集与报告

默认 evidence 根目录：

```text
tests/output
```

证据包结构：

```text
tests/output/<pack_id>/
  summary.json
  summary.md
  screenshots/
  logs/
  reports/
```

导出 ZIP 时会写入：

```text
tests/output/artifacts/<pack_id>_<result>_<timestamp>.zip
```

### 13.1 截图

截图组件默认查找窗口标题：

```text
Slay the Spire 2
```

截图流程：

1. 尝试将游戏窗口恢复、置前并最大化。
2. 使用 `mss` 截取主显示器。
3. 校验 RGB 颜色数量、目标分辨率和文件大小。
4. 使用临时文件加原子替换方式写入 PNG。

如果窗口不可见、无法置前或系统不支持截图，会返回 `skipped`，不会阻塞整个测试。

### 13.2 日志

日志组件默认读取 Godot 日志目录：

```text
%APPDATA%\Godot\app_userdata\Slay the Spire 2\logs
```

也可通过 `STS2_GODOT_LOG_DIR` 覆盖。

失败时默认收集 `ERROR`、`WARN`、`WARNING` 级别日志。可用配置：

```dotenv
STS2_FRAMEWORK__LOG_LEVELS=ERROR,WARN,WARNING
STS2_FRAMEWORK__LOG_MAX_ENTRIES=10000
STS2_FRAMEWORK__LOG_CUSTOM_PATHS=C:\path\a.log,C:\path\b.log
STS2_FRAMEWORK__LOG_BACKUP_DIR=C:\path\backup
```

日志读取支持文件锁重试和备份目录回退。

### 13.3 报告文件

`summary.json` 是机器可读摘要，包含 schema version、run 信息、环境信息、失败信息和 artifact 路径。

`summary.md` 是人工可读报告，包含：

- 运行结果。
- 耗时。
- run ID。
- 框架、适配器、游戏、操作系统、Python 版本。
- 截图和日志列表。
- 失败类型、消息、expected/actual 对比和 stack trace。

导出 artifact 时会生成 `reports/junit.xml`，便于 CI 系统消费。

HTML 测试报告会在截图旁展示 OCR 辅助分析。该分析用于提示 localization 裸 key、missing localization 占位和未替换 token 风险，不改变测试结果。

当 OCR provider 未配置或不可用时，报告会显示未执行或跳过，不影响 `test-report.html` 生成。

### 13.4 版本可观测性

STS2-AUTOTEST 采用统一滚动升级策略：工作区内只维护一个当前生效版本，所有接入的 MOD 项目直接跟随。为保证升级问题可追溯，所有核心测试产物都会记录当前 `autotest version`（来源为 `src/sts2_autotest/__init__.py` 中的 `__version__`）：

- `summary.json` 的 `test_run.autotest_version` 字段。
- `summary.md` 的 `- **Autotest Version:**` 行。
- Test Agent 报告（`test-report.md`）环境段的 `- Autotest version:` 行。

任何一次测试结果都能据此回答：这是哪个 MOD 项目、哪次 run、由哪个 autotest 版本执行的。旧版本生成的证据包没有该字段，读取时按缺省 `null` 兼容处理，报告中不渲染版本行。

### 13.5 平台兼容性阻塞

若报告中出现 `autotest_compatibility_blocked`，表示该次运行被 STS2-AUTOTEST 平台升级的兼容性问题阻塞，而非被测 MOD 业务逻辑失败。此时：

- 上游检测到平台兼容性问题后，通过 `create_pack(compatibility_block_reason=...)` 将原因写入 `summary.json` 的 `compatibility_block_reason` 字段（例如 `"autotest_compatibility_blocked"`），并将运行结果归类为 `BLOCKED`。
- `summary.md` 会渲染 `- **Compatibility Block Reason:**` 行及解释文案。
- 处理顺序：优先交回 STS2-AUTOTEST 平台侧补兼容层，其次增加迁移适配逻辑，最后才考虑宣布废弃旧行为并提供迁移说明。MOD 项目侧无需为此锁版本或回退。

## 14. 会话锁、看门狗与失败恢复

### 14.1 会话锁

默认锁文件：

```text
tests/output/.sts2-autotest.lock
```

`autotest doctor` 会检查是否已有会话占用锁。配置项：

```dotenv
STS2_FRAMEWORK__LOCK_FILE=tests/output/.sts2-autotest.lock
```

### 14.2 看门狗

编排器会启动 watchdog 监控游戏/适配器心跳。如果检测到僵死会话，会将当前会话标记为 crashed，并触发 crash evidence 收集。

相关配置：

```dotenv
STS2_EXECUTION__HEARTBEAT_TIMEOUT=60.0
```

### 14.3 失败分类

常见结果状态：

| 状态 | 含义 |
|---|---|
| `pass` | 用例通过。 |
| `fail` | 用例失败，可继续运行后续用例。 |
| `crash` | 会话或游戏发生崩溃级错误。 |
| `skip` | 因前置崩溃、中断或不可运行而跳过。 |
| `deterministic_fail` | 连续相同失败达到阈值，判断为确定性失败。 |

连续失败阈值由 `execution.max_consecutive_failures` 控制。

## 15. 端到端示例

仓库中包含一个端到端探索脚本：

```text
tests/e2e_first_battle.py
```

它演示了从主菜单开始，进入新游戏、选择角色、处理事件、选择地图节点、进入战斗、出牌并保存截图的流程。

运行前请确认：

- 游戏已启动。
- STS2-Cli-Mod 已加载。
- `sts2` CLI 可用。
- 当前工作目录为项目根目录。

运行：

```powershell
python tests/e2e_first_battle.py
```

输出目录：

```text
tests/output/1sttest
```

该脚本更适合作为真实环境连通性验证和二次开发参考，不建议直接作为稳定回归用例模板。

## 16. CI 使用建议

CI 中至少执行：

```powershell
autotest doctor --ci
python -m pytest
```

如果 CI 机器没有真实游戏环境，可以将真实游戏集成测试与普通单元测试分开：

```powershell
python -m pytest tests/unit
```

真实游戏测试建议放在单独 job 中，并确保：

- Steam 可启动。
- 游戏和 Mod 已预装。
- `STS2_CLI_PATH` 已配置。
- 运行用户具备桌面会话和截图权限。
- evidence/artifact 目录会被 CI 收集。

## 17. 常见问题

### 17.1 `autotest doctor` 提示 `sts2_cli_mod: NOT_FOUND`

检查：

- `sts2.exe` 是否存在。
- 是否设置 `STS2_CLI_PATH`。
- 是否加入系统 `PATH`。
- 当前 shell 是否重新加载了环境变量。

### 17.2 `game_installed: NOT_FOUND`

检查：

- Steam 是否安装在默认路径。
- 游戏是否已经安装。
- 如果使用自定义 Steam 库，设置 `STS2_GAME_PATH`。

### 17.3 adapter health check failed

通常表示 `sts2 ping` 无法连通游戏内 Mod。检查：

- 游戏是否已启动。
- STS2-Cli-Mod 是否启用。
- 游戏是否卡在启动阶段。
- Mod 与 CLI 版本是否兼容。

### 17.4 `Game state is UNKNOWN`

表示 CLI 返回的 `screen` 无法映射到框架已知状态。可能原因：

- 游戏处于暂不支持的界面。
- STS2-Cli-Mod 返回了新的 screen 名称。
- CLI/Mod 与框架版本不匹配。

处理方式：

- 返回主菜单后重试。
- 更新 STS2-Cli-Mod 或框架映射。
- 查看 `sts2 state --pretty` 输出。

### 17.5 发现旧 progress 文件

继续：

```powershell
autotest run --resume
```

重新开始：

```powershell
autotest run --no-resume --all
```

### 17.6 截图被跳过

截图跳过不会直接导致测试失败。常见原因：

- 游戏窗口不可见。
- 游戏窗口标题不是 `Slay the Spire 2`。
- 当前运行环境没有桌面会话。
- 截图目录不可写。

### 17.7 报告找不到 run ID

先列出可用运行：

```powershell
autotest report missing-run --evidence-dir tests/output
```

命令会在找不到 `missing-run` 时打印可用目录列表。确认目录中存在 `summary.json`。

## 18. 推荐工作流

日常本地使用：

```powershell
.\.venv\Scripts\Activate.ps1
autotest doctor
autotest run --all
autotest report latest
```

开发新测试：

```powershell
python -m pytest tests/unit
python -m pytest tests/integration
autotest doctor
python tests/e2e_first_battle.py
```

中断后恢复：

```powershell
autotest run --resume
```

环境变更后重新检查：

```powershell
autotest doctor --json
```

## 19. MVP 边界

- 当前默认只启用 CLI adapter；Agent adapter 仍为 Beta/预留配置。
- 真实游戏测试依赖游戏窗口和 STS2-Cli-Mod 的运行状态，不适合在无桌面会话的普通 CI runner 中直接执行。
- `autotest run` 的套件/失败重跑入口已经存在，但完整测试发现、套件目录约定和历史失败索引仍需要后续版本增强。
- 证据系统能力已具备，但 CLI 默认 orchestrator 使用的是基础 evidence hooks；需要在自定义运行器中注入 `RealEvidenceHooks` 才能完整启用自动截图、日志和打包流程。
## 20. 桌面截图 Helper 回退

如果自动化进程所在会话看不到你的 Windows 桌面，`mss` 可能会抓不到游戏窗口，表现为截图一直 `skipped`，或者报 `BitBlt` 失败。此时可以启动桌面侧 helper，让截图请求在你的交互桌面里执行。

启动方式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/desktop_capture_helper.ps1
```

默认 helper 监听 `tests/output/1sttest/.capture_helper`。如果你要给别的输出目录用，可以传：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/desktop_capture_helper.ps1 -HelperRoot "tests/output/myrun/.capture_helper"
```

工作方式：

- helper 会持续写入 `heartbeat.json`，主测试进程据此判断 helper 是否在线。
- 当原生窗口抓图失败时，`ScreenCapture` 会自动把请求写入 `.capture_helper/requests/`。
- helper 在交互桌面里处理请求，把 PNG 写到目标输出目录，并将结果写回 `.capture_helper/responses/`。

建议：

- 先把游戏窗口切到前台，再启动 `python tests/e2e_first_battle.py`。
- helper 需要保持运行；关闭 helper 后，框架会退回原生 `mss` 抓图路径。
