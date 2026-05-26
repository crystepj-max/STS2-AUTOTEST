# B19 CliModAdapter Real CLI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 B19 从“已有但混杂的集成测试草稿”收口为可重复运行、分层清晰、能验证真实 `sts2` CLI 和真实游戏链路的测试资产。

**Architecture:** 集成测试拆成两层：CLI-only 层只要求 `sts2.exe` 可发现，Game-required 层要求 Slay the Spire 2 与 STS2-Cli-Mod 正在运行。纯函数映射、mock 超时、响应解析单元契约保留在 unit 测试，integration 只验证外部进程和游戏边界。

**Tech Stack:** Python 3.11+、pytest、STS2-Cli-Mod `sts2` CLI、现有 `CliModAdapter`、现有 `discover_sts2_cli()`。

---

## 文件结构

- Modify: `tests/conftest.py`
  - 负责在测试启动时确认 pytest 导入的是当前仓库 `src/sts2_autotest`，避免 editable install 指向旧 worktree 后产生假失败。
- Modify: `tests/integration/conftest.py`
  - 负责集成测试公共 fixture：发现真实 CLI、创建短超时 adapter、判断真实游戏是否可用。
- Create: `tests/integration/test_cli_mod_cli_only.py`
  - 负责真实 `sts2.exe` 进程层 smoke：`--version`、`ping`、`state`、错误分类、`act()` 降级返回。
- Create: `tests/integration/test_cli_mod_game_smoke.py`
  - 负责真实游戏链路 smoke：`health_check()`、`get_state()`、screen mapping、available actions、bug snapshot。
- Modify: `tests/integration/test_cli_mod_smoke.py`
  - 删除或瘦身旧混合文件，避免重复执行和职责混杂。
- Modify: `tests/unit/test_cli_command_mapping.py`
  - 保留 `_build_cli_args()`、`_SCREEN_MAP`、`_screen_to_actions()` 的纯函数契约。
- Modify: `tests/unit/test_cli_mod_adapter.py`
  - 保留 subprocess mock、timeout、non-zero exit、response envelope 的 adapter 单元契约。
- Modify: `pyproject.toml`
  - 修复 marker 中文说明编码，明确 `integration` 与 `requires_game`。
- Modify: `README.md`
  - 写清 B19 测试命令、环境变量、游戏前置条件、期望结果。
- Modify: `docs/beta-roadmap.md`
  - 将 B19 状态从“未完成技术债”更新为“测试分层完成，真实游戏验证需按环境执行”。

---

### Task 1: 测试导入路径保护

**Files:**
- Modify: `tests/conftest.py`
- Test: `python -m pytest tests/unit/test_cli_mod_adapter.py::TestCliModAdapterInit::test_defaults -q`

- [ ] **Step 1: 写入导入路径保护**

将 `tests/conftest.py` 替换为以下内容：

```python
"""pytest 全局配置。"""

from pathlib import Path

import pytest

import sts2_autotest


def pytest_sessionstart(session: pytest.Session) -> None:
    """确保测试导入当前仓库源码，而不是旧 editable worktree。"""
    repo_root = Path(__file__).resolve().parents[1]
    expected_pkg = repo_root / "src" / "sts2_autotest"
    actual_pkg = Path(sts2_autotest.__file__).resolve().parent

    if actual_pkg != expected_pkg:
        raise RuntimeError(
            "pytest 导入了错误的 sts2_autotest 包："
            f"{actual_pkg}；期望：{expected_pkg}。"
            '请在当前仓库运行 pip install -e ".[dev]"，'
            "或临时设置 PYTHONPATH=src 后再运行测试。"
        )
```

- [ ] **Step 2: 验证错误 worktree 能被暴露**

在未修复 editable install 的当前环境中运行：

```bash
python -m pytest tests/unit/test_cli_mod_adapter.py::TestCliModAdapterInit::test_defaults -q
```

Expected: 如果 `pip show sts2-autotest` 仍指向 `.claude/worktrees/feat+agent-adapter`，pytest 在 session start 阶段失败，错误消息包含 `pytest 导入了错误的 sts2_autotest 包`。

- [ ] **Step 3: 修复 editable install**

运行：

```bash
pip install -e ".[dev]"
```

Expected: 输出包含 `Successfully installed sts2-autotest-0.1.0` 或 `Successfully built sts2-autotest`。

- [ ] **Step 4: 验证导入路径已修复**

运行：

```bash
python -c "import sts2_autotest; print(sts2_autotest.__file__)"
```

Expected: 输出以 `D:\workspace\STS2\STS2-AUTOTEST\src\sts2_autotest\__init__.py` 结尾。

- [ ] **Step 5: 运行最小单测**

运行：

```bash
python -m pytest tests/unit/test_cli_mod_adapter.py::TestCliModAdapterInit::test_defaults -q
```

Expected: `1 passed`。

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py
git commit -m "test: guard pytest source import path"
```

---

### Task 2: 集成测试公共 fixture 分层

**Files:**
- Modify: `tests/integration/conftest.py`
- Test: `python -m pytest tests/integration --collect-only -q`

- [ ] **Step 1: 替换 integration conftest**

将 `tests/integration/conftest.py` 替换为以下内容：

```python
"""STS2-AUTOTEST 集成测试公共 fixture。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.adapters.discovery import discover_sts2_cli


def _run(coro: Any) -> Any:
    """在同步 pytest 测试中执行 async adapter API。"""
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def real_cli_path() -> str:
    """返回真实 sts2 CLI 路径；不存在时跳过 CLI 集成测试。"""
    cli_path = discover_sts2_cli()
    if cli_path is None:
        pytest.skip("未找到 STS2-Cli-Mod CLI，请设置 STS2_CLI_PATH 或安装 sts2.exe。")
    return cli_path


@pytest.fixture
def real_cli_adapter(real_cli_path: str) -> Generator[CliModAdapter, None, None]:
    """短超时真实 CLI adapter，用于不要求游戏运行的测试。"""
    adapter = CliModAdapter(cli_path=real_cli_path, timeout=2.0)
    yield adapter
    _run(adapter.cleanup())


@pytest.fixture
def game_adapter(real_cli_path: str) -> Generator[CliModAdapter, None, None]:
    """要求真实游戏和 Mod 可通信的 adapter。"""
    adapter = CliModAdapter(cli_path=real_cli_path, timeout=5.0)
    health = _run(adapter.health_check())
    if not health.healthy:
        pytest.skip(f"游戏或 STS2-Cli-Mod 未就绪：{health.message}")
    yield adapter
    _run(adapter.cleanup())
```

- [ ] **Step 2: 收集测试确认 fixture 可加载**

运行：

```bash
python -m pytest tests/integration --collect-only -q
```

Expected: 输出已收集的 integration 测试节点，且没有 `ImportError`。

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: add real cli integration fixtures"
```

---

### Task 3: 新建 CLI-only 真实进程集成测试

**Files:**
- Create: `tests/integration/test_cli_mod_cli_only.py`
- Test: `python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short`

- [ ] **Step 1: 创建失败测试文件**

创建 `tests/integration/test_cli_mod_cli_only.py`：

```python
"""真实 STS2-Cli-Mod CLI 进程集成测试。

这些测试只要求 `sts2` CLI 可执行文件存在，不要求游戏正在运行。
"""

from __future__ import annotations

import re
import subprocess

import pytest

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.errors import ErrorCategory, STS2Error

from .conftest import _run


pytestmark = pytest.mark.integration


class TestRealCliExecutable:
    """真实 sts2 可执行文件基础契约。"""

    def test_version_exits_zero(self, real_cli_path: str) -> None:
        proc = subprocess.run(
            [real_cli_path, "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        assert proc.returncode == 0

    def test_version_matches_semver(self, real_cli_path: str) -> None:
        proc = subprocess.run(
            [real_cli_path, "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        output = proc.stdout.strip()
        assert re.match(r"^\d+\.\d+\.\d+", output), output

    def test_adapter_accepts_real_version(self, real_cli_path: str) -> None:
        proc = subprocess.run(
            [real_cli_path, "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        adapter = CliModAdapter(cli_path=real_cli_path, timeout=2.0, version_output=proc.stdout)
        assert adapter._version_checked is True


class TestRealCliWithoutGame:
    """游戏未运行时的真实 CLI 降级契约。"""

    def test_health_check_returns_status(self, real_cli_adapter: CliModAdapter) -> None:
        result = _run(real_cli_adapter.health_check())
        assert isinstance(result, HealthStatus)
        assert isinstance(result.healthy, bool)

    def test_health_check_completes_under_adapter_timeout(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        import time

        started_at = time.monotonic()
        _run(real_cli_adapter.health_check())
        elapsed = time.monotonic() - started_at
        assert elapsed < 3.0

    def test_state_returns_or_raises_classified_adapter_error(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        try:
            state = _run(real_cli_adapter.get_state())
        except STS2Error as exc:
            assert exc.category == ErrorCategory.ADAPTER_ERROR
            assert exc.detail.get("subtype") is not None
        else:
            assert state.screen is not None

    def test_available_actions_empty_when_unhealthy(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        health = _run(real_cli_adapter.health_check())
        actions = _run(real_cli_adapter.get_available_actions())
        assert isinstance(actions, list)
        if not health.healthy:
            assert actions == []

    def test_act_returns_action_result_not_raw_exception(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        result = _run(real_cli_adapter.act("play_card", {"card_id": "Strike"}))
        assert isinstance(result, ActionResult)
        assert result.status in {"success", "failure", "timeout"}
        assert isinstance(result.state_changed, bool)
        if result.status in {"failure", "timeout"}:
            assert result.detail is not None
```

- [ ] **Step 2: 运行测试确认当前失败原因**

运行：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

Expected: 如果真实 CLI 存在，测试执行；如果不存在，整文件跳过并显示 `未找到 STS2-Cli-Mod CLI`。若因旧混合测试仍存在导致重复失败，继续 Task 5 清理。

- [ ] **Step 3: 确认 CLI-only 测试通过或跳过**

运行：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

Expected: 在当前机器上真实 CLI 存在时输出 `10 passed`；在无 CLI 环境输出 skipped。

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_cli_mod_cli_only.py
git commit -m "test: add real cli-only integration smoke tests"
```

---

### Task 4: 新建真实游戏链路 smoke 测试

**Files:**
- Create: `tests/integration/test_cli_mod_game_smoke.py`
- Test: `python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short`

- [ ] **Step 1: 创建 game-required 测试文件**

创建 `tests/integration/test_cli_mod_game_smoke.py`：

```python
"""真实游戏链路集成测试。

这些测试要求 Slay the Spire 2 正在运行，且 STS2-Cli-Mod 已加载并能通过
`sts2` CLI 通信。
"""

from __future__ import annotations

import pytest

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.state import GameScreen, GameState

from .conftest import _run


pytestmark = [pytest.mark.integration, pytest.mark.requires_game]


class TestRealGameState:
    """真实游戏状态读取。"""

    def test_health_check_is_healthy(self, game_adapter: CliModAdapter) -> None:
        health = _run(game_adapter.health_check())
        assert health.healthy is True

    def test_state_returns_game_state(self, game_adapter: CliModAdapter) -> None:
        state = _run(game_adapter.get_state())
        assert isinstance(state, GameState)
        assert isinstance(state.screen, GameScreen)

    def test_real_screen_maps_to_known_or_loading_state(
        self, game_adapter: CliModAdapter
    ) -> None:
        state = _run(game_adapter.get_state())
        assert state.screen in set(GameScreen)
        assert state.screen != GameScreen.CRASHED

    def test_state_model_is_frozen(self, game_adapter: CliModAdapter) -> None:
        state = _run(game_adapter.get_state())
        with pytest.raises(Exception):
            state.screen = GameScreen.COMBAT  # type: ignore[misc]

    def test_available_actions_follow_current_screen(
        self, game_adapter: CliModAdapter
    ) -> None:
        state = _run(game_adapter.get_state())
        actions = _run(game_adapter.get_available_actions())
        assert isinstance(actions, list)
        if state.screen not in {
            GameScreen.UNKNOWN,
            GameScreen.CRASHED,
        }:
            assert actions


class TestRealGameSnapshot:
    """真实游戏 bug snapshot 契约。"""

    def test_bug_snapshot_has_state_actions_and_timestamp(
        self, game_adapter: CliModAdapter
    ) -> None:
        snapshot = _run(game_adapter.capture_bug_snapshot())
        assert set(snapshot) == {"game_state", "available_actions", "timestamp"}
        assert isinstance(snapshot["game_state"], GameState)
        assert isinstance(snapshot["available_actions"], list)
        assert snapshot["timestamp"].tzinfo is not None
```

- [ ] **Step 2: 未启动游戏时验证跳过**

关闭游戏或保持当前无游戏状态，运行：

```bash
python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short
```

Expected: 输出 skipped，跳过原因包含 `游戏或 STS2-Cli-Mod 未就绪`。

- [ ] **Step 3: 启动真实游戏与 Mod**

手动启动 Slay the Spire 2，确认 STS2-Cli-Mod 已加载，然后运行：

```bash
python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short
```

Expected: 输出 `6 passed`。如果游戏正在 loading，`test_available_actions_follow_current_screen` 允许 `UNKNOWN` 无 actions，但不允许 `CRASHED`。

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_cli_mod_game_smoke.py
git commit -m "test: add real game cli mod smoke tests"
```

---

### Task 5: 清理旧混合集成测试

**Files:**
- Modify: `tests/integration/test_cli_mod_smoke.py`
- Test: `python -m pytest tests/integration --collect-only -q`

- [ ] **Step 1: 删除旧混合测试文件内容并保留迁移说明**

将 `tests/integration/test_cli_mod_smoke.py` 替换为以下内容：

```python
"""已拆分的 CliModAdapter 集成测试入口。

B19 的真实 CLI 测试已拆分为：
- tests/integration/test_cli_mod_cli_only.py
- tests/integration/test_cli_mod_game_smoke.py

本文件保留到一个开发周期后删除，避免旧命令路径静默失效。
"""
```

- [ ] **Step 2: 收集测试确认旧文件不再贡献测试节点**

运行：

```bash
python -m pytest tests/integration --collect-only -q
```

Expected: 输出只包含 `test_cli_mod_cli_only.py`、`test_cli_mod_game_smoke.py` 和既有 `test_spec_pipeline_e2e.py` 的测试节点，不包含 `test_cli_mod_smoke.py::Test...`。

- [ ] **Step 3: 运行 CLI-only 集成测试确认无重复失败**

运行：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

Expected: 当前机器有真实 CLI 时 `10 passed`。

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_cli_mod_smoke.py
git commit -m "test: split cli mod integration smoke tests"
```

---

### Task 6: 补齐单元测试归属并修复编码说明

**Files:**
- Modify: `tests/unit/test_cli_command_mapping.py`
- Modify: `tests/unit/test_cli_mod_adapter.py`
- Test: `python -m pytest tests/unit/test_cli_command_mapping.py tests/unit/test_cli_mod_adapter.py -q --tb=short`

- [ ] **Step 1: 修复 `test_cli_command_mapping.py` 文件头说明**

将文件开头 docstring 替换为：

```python
"""CliModAdapter 纯函数契约测试。

验证 `_build_cli_args`、`_screen_to_actions` 和 `_SCREEN_MAP`
是否符合 `docs/sts2-cli-mod-reference.md` 中记录的 STS2-Cli-Mod CLI 命令格式。
这些测试不启动真实 CLI 进程。
"""
```

- [ ] **Step 2: 确认 positional 参数测试留在 unit**

确认 `tests/unit/test_cli_command_mapping.py` 中包含以下测试：

```python
def test_action_with_string_arg(self) -> None:
    result = _build_cli_args("play_card", {"card_id": "VoidSlash"})
    assert result == ["play_card", "VoidSlash"]


def test_grid_select_card_uses_positional_card_id(self) -> None:
    result = _build_cli_args("grid_select_card", {"card_id": "Strike"})
    assert result == ["grid_select_card", "Strike"]
```

- [ ] **Step 3: 修复 `test_cli_mod_adapter.py` 文件头说明**

将文件开头 docstring 替换为：

```python
"""CliModAdapter 单元测试。

这些测试 mock `subprocess.Popen`，不依赖真实 STS2-Cli-Mod 安装。
真实 CLI 和真实游戏链路由 `tests/integration/` 覆盖。
"""
```

- [ ] **Step 4: 确认 screen mapping mock 测试留在 unit**

确认 `tests/unit/test_cli_mod_adapter.py` 中包含以下测试：

```python
@patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
def test_singleplayer_submenu_maps_to_main_menu(
    self, mock_popen: MagicMock, adapter: CliModAdapter
) -> None:
    mock_popen.return_value = _mock_popen_ok({"screen": "SINGLEPLAYER_SUBMENU"})
    result = _run(adapter.get_state())
    assert result.screen == GameScreen.MAIN_MENU
```

- [ ] **Step 5: 运行归属测试**

运行：

```bash
python -m pytest tests/unit/test_cli_command_mapping.py tests/unit/test_cli_mod_adapter.py -q --tb=short
```

Expected: `85 passed`。

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cli_command_mapping.py tests/unit/test_cli_mod_adapter.py
git commit -m "test: keep cli mod pure contracts in unit tests"
```

---

### Task 7: 修复 pytest marker 与中文编码

**Files:**
- Modify: `pyproject.toml`
- Test: `python -m pytest tests/integration --collect-only -q`

- [ ] **Step 1: 替换 marker 配置**

将 `pyproject.toml` 中 `[tool.pytest.ini_options]` 的 `markers` 替换为：

```toml
markers = [
    "integration: 需要真实 STS2-Cli-Mod CLI 环境的集成测试",
    "requires_game: 需要 Slay the Spire 2 游戏已运行且 STS2-Cli-Mod 已加载的测试",
]
```

- [ ] **Step 2: 收集 integration 测试确认 marker 合法**

运行：

```bash
python -m pytest tests/integration --collect-only -q
```

Expected: 没有 `PytestUnknownMarkWarning`。

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "test: document integration pytest markers"
```

---

### Task 8: 文档化 B19 执行方式与完成标准

**Files:**
- Modify: `README.md`
- Modify: `docs/beta-roadmap.md`
- Test: `python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short`

- [ ] **Step 1: 在 README 添加 B19 测试说明**

在 README 的测试命令区域添加：

```markdown
### B19 CliModAdapter 真实 CLI 集成测试

CLI-only 测试只要求 `sts2` CLI 可发现：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

如果 CLI 不在 `PATH` 中，设置：

```powershell
$env:STS2_CLI_PATH="C:\path\to\sts2.exe"
```

真实游戏链路测试要求 Slay the Spire 2 正在运行，且 STS2-Cli-Mod 已加载：

```bash
python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short
```

B19 的关闭标准：

- CLI-only 测试在有 `sts2.exe` 的机器上通过。
- Game-required 测试在启动游戏和 Mod 后通过。
- 没有游戏环境时，Game-required 测试必须跳过，而不是失败。
- 单元测试继续覆盖 CLI 参数映射、screen mapping、subprocess mock 和错误分类。
```

- [ ] **Step 2: 更新 beta-roadmap B19 状态**

将 `docs/beta-roadmap.md` 中 B19 行替换为：

```markdown
| B19 | CliModAdapter 真实 CLI 集成测试 | 分层为 CLI-only 与 requires_game 两组；CLI-only 可在仅安装 sts2.exe 的环境运行，requires_game 用于验证真实游戏/Mod 链路 |
```

- [ ] **Step 3: 运行 CLI-only 测试记录结果**

运行：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

Expected: 当前机器有真实 CLI 时 `10 passed`。

- [ ] **Step 4: Commit**

```bash
git add README.md docs/beta-roadmap.md
git commit -m "docs: document b19 real cli integration tests"
```

---

### Task 9: 全量验证与 B19 收口

**Files:**
- No source edits
- Test: unit、integration、mypy、lint-imports

- [ ] **Step 1: 运行 B19 相关单元测试**

运行：

```bash
python -m pytest tests/unit/test_cli_mod_adapter.py tests/unit/test_cli_command_mapping.py -q --tb=short
```

Expected: `85 passed`。

- [ ] **Step 2: 运行 CLI-only 集成测试**

运行：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

Expected: 当前机器有真实 CLI 时 `10 passed`；无 CLI 环境时 skipped。

- [ ] **Step 3: 未启动游戏时运行 game-required 测试**

运行：

```bash
python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short
```

Expected: 未启动游戏时 skipped，跳过原因包含 `游戏或 STS2-Cli-Mod 未就绪`。

- [ ] **Step 4: 启动游戏后运行 game-required 测试**

手动启动 Slay the Spire 2 并确认 STS2-Cli-Mod 已加载，然后运行：

```bash
python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short
```

Expected: `6 passed`。

- [ ] **Step 5: 运行项目要求的验证命令**

运行：

```bash
python -m pytest tests/unit/ -v
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令全部通过。

- [ ] **Step 6: Commit 验证记录**

如果 Task 9 只产生测试缓存，不提交文件。若 README 或 roadmap 中补充了实际验证日期和结果，提交：

```bash
git add README.md docs/beta-roadmap.md
git commit -m "docs: record b19 verification results"
```

---

## 自检结果

- Spec coverage: 覆盖了真实 CLI 可执行文件、真实游戏链路、跳过策略、测试分层、环境导入错误、文档完成标准。
- Placeholder scan: 未留下占位词、延期实现标记、无代码步骤的“写测试”指令。
- Type consistency: fixture 名称统一为 `real_cli_path`、`real_cli_adapter`、`game_adapter`；测试导入统一使用现有 `CliModAdapter`、`HealthStatus`、`ActionResult`、`GameState`、`GameScreen`。
